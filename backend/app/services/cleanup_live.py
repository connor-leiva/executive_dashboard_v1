"""Books Cleanup -- a company's months ahead, built from the app's own records instead of the audit file.

The file projects one company (beCollective, whose memberships are one-time contracts the audit's export works
out). The membership company -- the one the file keeps a dues schedule for, the Forum -- renews every year, and
its members are already here: each one's plan, renewal date and contract value from the CRM (services/sync.py),
and what they have paid and are set to pay from GHL Payments and the legacy Stripe account (services/billing.py).
This builds the same block the file's `forward` is (Books.forward reads both) from those records as they stand,
so the Forum's forecast moves when they do. The Edge's members and charges are not the Forum's and stay out.

A MEMBER'S TERM is the twelve months before their renewal date, and its contract value is earned day by day across
it -- the rule the corrected books use for dues. On the books' last day each member is part way through one: what
they had paid toward it, against what had been earned, is the paid-ahead (deferred) or earned-ahead the books
should carry. After that day:

  * the rest of the term is earned month by month, the part already paid first;
  * what is still owed comes in as it already has since the books' end, then as their subscription schedules it;
  * a member who joined since the books' end, or whose renewal the CRM already shows (the date moved on a year), is
    a contract in hand and counted in full;
  * every other renewal in the year ahead is counted at the renewal rate (a Forecast assumption, starting from the
    last twelve months' retention), paid the way that member pays: in full, monthly, quarterly or in installments.

What the records can't place is said rather than guessed: a member with no renewal date or contract value is left
out and named, and one with nothing paid toward the term and nothing scheduled is flagged.
"""
from __future__ import annotations

import datetime as dt
from calendar import monthrange
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import MetricRecord
from . import roles
from .billing import edge_offering, merge_payment_sources, project_charges

HORIZON = 12                       # months projected after the one the books end in
GRACE = dt.timedelta(days=45)      # a lump paid a few weeks before the term it pays for still pays for it
SLACK = dt.timedelta(days=3)       # a scheduled charge lands on the term's first day, give or take a time zone
_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _today() -> dt.date:
    return dt.date.today()


def _key(d: dt.date) -> str:
    return f"{d.year}-{d.month:02d}"


def _add(d: dt.date, n: int) -> dt.date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return dt.date(y, m, min(d.day, monthrange(y, m)[1]))


def _pdate(v) -> dt.date | None:
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _r(x: float) -> float:
    return round(x + 0.0, 2)


def _spread(value: float, start: dt.date, end: dt.date, after: dt.date) -> dict[str, float]:
    """`value` earned day by day across [start, end): each month's part of it, counting only the days after `after`."""
    days = (end - start).days
    out: dict[str, float] = {}
    d = max(start, after + dt.timedelta(days=1))
    while days > 0 and d < end:
        nxt = min(end, _add(d.replace(day=1), 1))
        out[_key(d)] = out.get(_key(d), 0.0) + value * (nxt - d).days / days
        d = nxt
    return out


def _earned_by(value: float, start: dt.date, end: dt.date, day: dt.date) -> float:
    days = (end - start).days
    return value * max(0, (min(end, day + dt.timedelta(days=1)) - start).days) / days if days > 0 else 0.0


def _plan(plan: str | None, start: dt.date, value: float, n: int | None) -> list[tuple[dt.date, float]]:
    """A term's payments the way this member pays: in full at the start (also when the plan is unknown), monthly,
    quarterly, or in N installments (the subscription's own count, else the CRM's usual three)."""
    if plan == "monthly":
        return [(_add(start, i), value / 12) for i in range(12)]
    if plan == "quarterly":
        return [(_add(start, 3 * i), value / 4) for i in range(4)]
    if plan == "installments":
        n = n or 3
        return [(_add(start, i), value / n) for i in range(n)]
    return [(start, value)]


def _counts(p) -> bool:
    """A succeeded charge that pays dues: not an event ticket or a sponsorship, and not The Edge's."""
    meta = p.meta or {}
    if (p.status or "") != "succeeded" or not p.occurred_on:
        return False
    if meta.get("stream") in ("event_tickets", "sponsorships") or meta.get("segment") == "edge":
        return False
    return not edge_offering(meta.get("entity_source_name"))[0]


def build(data: dict, members, payments, subs, lost, today: dt.date, business: str | None = None) -> dict | None:
    """The block Books.forward projects from, for the company the file keeps membership dues for. Pure: the
    records are passed in (MetricRecord-shaped: name, email, external_id, status, amount, occurred_on, meta), so
    the tests build it from a handful of invented members. None when the file has no such company, or projects it
    itself."""
    co = (data.get("dues") or {}).get("company")
    if not co or co not in data["companies"] or co == (data.get("forward") or {}).get("company"):
        return None
    end = dt.date.fromisoformat(data["period"]["end"])
    first = end.replace(day=1)
    keys = [_key(_add(first, i)) for i in range(HORIZON + 1)]
    last_day = _add(first, HORIZON + 1) - dt.timedelta(days=1)
    in_view = set(keys)
    days_in = monthrange(end.year, end.month)[1]

    # what each member paid, and what is set to bill them, matched on the CRM contact and then on the email
    paid_c, paid_e = defaultdict(list), defaultdict(list)
    for p in payments:
        if not _counts(p):
            continue
        amt = _num(p.amount) - _num((p.meta or {}).get("amount_refunded"))
        cid = str((p.meta or {}).get("contact_id") or "")
        if cid:
            paid_c[cid].append((p.occurred_on, amt, id(p)))
        if p.email:
            paid_e[p.email.strip().lower()].append((p.occurred_on, amt, id(p)))
    subs_c, subs_e = defaultdict(list), defaultdict(list)
    for x in subs:
        if x.status != "active" or (x.meta or {}).get("segment") == "edge":
            continue
        cid = str((x.meta or {}).get("contact_id") or "")
        if cid:
            subs_c[cid].append(x)
        if x.email:
            subs_e[x.email.strip().lower()].append(x)

    sched = {k: [0.0, 0.0] for k in keys}                 # earned, from money already paid / still to come
    coll = defaultdict(float)
    due = {k: {"earned": 0.0, "collected": 0.0, "count": 0, "value": 0.0} for k in keys}
    c = {"members": 0, "contracted": 0.0, "collected": 0.0, "still_to_collect": 0.0, "earned_so_far": 0.0}
    rows, left_out = [], []
    n_flag = {"nothing": 0, "nothing_value": 0.0, "joined": 0, "joined_value": 0.0, "renewed": 0,
              "renewed_value": 0.0, "due": 0, "due_value": 0.0, "add_ons": 0}
    values, plans = [], []

    for m in members:
        mem = (m.meta or {}).get("membership") or {}
        name = (m.name or "").title() or m.email or m.external_id
        v, rd, ed = _num(mem.get("total_cost")), _pdate(mem.get("renewal_date")), _pdate(mem.get("enrollment_date"))
        plan = mem.get("payment")
        early = GRACE if plan in (None, "pif") else SLACK
        if v <= 0 and mem.get("member_kind") == "add_on":
            n_flag["add_ons"] += 1                 # a seat on a primary member's contract: nothing of its own to earn
            continue
        if v <= 0 or not rd:
            left_out.append({"name": name, "why": "no contract value" if v <= 0 else "no renewal date",
                             "source_url": m.source_url})
            continue
        if mem.get("member_kind") in (None, "primary"):
            values.append(v)
            plans.append(plan)
        email = (m.email or "").strip().lower()
        seen, pays = set(), []
        for d, amt, ref in paid_c.get(str(m.external_id), []) + (paid_e.get(email, []) if email else []):
            if ref not in seen:
                seen.add(ref)
                pays.append((d, amt))
        pays.sort()
        mysubs = list({id(x): x for x in subs_c.get(str(m.external_id), []) + (subs_e.get(email, []) if email else [])}.values())
        n_inst = next((int((x.meta or {})["installments_total"]) for x in mysubs
                       if (x.meta or {}).get("installments_total")), None)

        # the terms this member has in the year ahead
        if ed and ed > end:
            terms = [("joined", ed, _add(ed, 12))]
        else:
            te = rd
            while te <= end:
                te = _add(te, 12)
            while _add(te, -12) > end:
                te = _add(te, -12)
            terms = [("current", _add(te, -12), te)]
            if te <= last_day:
                # renewed already: the CRM shows the next term, or they have paid since the renewal date
                done = rd > te or any(te <= d <= today for d, _ in pays)
                terms.append(("renewed" if done else "due", te, _add(te, 12)))

        row = {"name": name, "plan": plan, "value": _r(v), "source_url": m.source_url, "renewal": None, "flag": None}
        for kind, ts, te in terms:
            if kind == "due":
                for k, amt in _spread(v, ts, te, end).items():
                    if k in in_view:
                        due[k]["earned"] += amt
                for d, amt in _plan(plan, ts, v, n_inst):
                    if _key(d) in in_view:
                        due[_key(d)]["collected"] += amt
                due[_key(ts)]["count"] += 1
                due[_key(ts)]["value"] += v
                n_flag["due"] += 1
                n_flag["due_value"] += v
                row["renewal"] = {"date": ts.isoformat(), "state": "due"}
                continue
            current = kind == "current"
            raw = sum(a for d, a in pays if ts - early <= d <= end) if current else 0.0
            paid = min(v, max(0.0, raw))
            earned = _earned_by(v, ts, te, end) if current else 0.0
            owed = v - paid
            # since the books' end: what came in, then what is scheduled, never more than is owed
            # (a term that follows another takes only what came on or after its first day; one that opened the
            # membership takes an early payment too)
            lo = ts if kind == "renewed" else ts - early
            got = [(d, a) for d, a in pays if end < d <= today and lo <= d and (d < te if current else True)]
            ahead = [(dt.date.fromisoformat(x["date"]), x["amount"])
                     for x in project_charges(mysubs, today, te - dt.timedelta(days=1))]
            ahead = [(d, a) for d, a in ahead if d >= ts]
            if not mysubs and not current:
                ahead = [(d, a) for d, a in _plan(plan, ts, v, n_inst) if d > today]
            since = sched_total = 0.0
            for d, a in got + ahead:
                left = owed - since - sched_total
                if left <= 0.005:
                    break
                take = min(max(0.0, a), left)
                if _key(d) in in_view:
                    coll[_key(d)] += take
                if d <= today:
                    since += take
                else:
                    sched_total += take
            # the rest of the term, month by month: the part already paid first
            ahead_paid = max(0.0, paid - earned)
            for k, amt in sorted(_spread(v, ts, te, end).items()):
                if k not in in_view:
                    continue
                from_paid = min(amt, ahead_paid)
                ahead_paid -= from_paid
                sched[k][0] += from_paid
                sched[k][1] += amt - from_paid
            c["contracted"] += v
            c["collected"] += paid
            c["still_to_collect"] += owed
            c["earned_so_far"] += earned
            if current:
                row.update({"term": [ts.isoformat(), te.isoformat()], "paid_by_end": _r(paid), "earned_by_end": _r(earned),
                            "since": _r(since), "scheduled": _r(sched_total)})
                if raw > v * 1.05:
                    row["flag"] = f"Paid {_r(raw):,.2f} toward a {_r(v):,.2f} term: two terms' payments, or the value is out of date"
                elif paid <= 0 and since <= 0 and sched_total <= 0:
                    row["flag"] = "Nothing paid toward this term, and nothing scheduled"
                    n_flag["nothing"] += 1
                    n_flag["nothing_value"] += v
            elif kind == "joined":
                row.update({"term": [ts.isoformat(), te.isoformat()], "paid_by_end": 0.0, "earned_by_end": 0.0,
                            "since": _r(since), "scheduled": _r(sched_total), "joined": ts.isoformat()})
                n_flag["joined"] += 1
                n_flag["joined_value"] += v
            else:
                row["renewal"] = {"date": ts.isoformat(), "state": "renewed", "since": _r(since), "scheduled": _r(sched_total)}
                n_flag["renewed"] += 1
                n_flag["renewed_value"] += v
        c["members"] += 1
        rows.append(row)

    # the renewal rate starts from the last twelve months: of everyone who was a member, the share still here
    year_ago = today - dt.timedelta(days=365)
    lost12 = sum(1 for x in lost if x.occurred_on and year_ago < x.occurred_on <= today)
    pool = len(members) + lost12
    renew_pct = round(100 * (1 - lost12 / pool)) if pool else 100
    known = [p for p in plans if p]
    label = lambda k, i: (f"{_MON[end.month - 1]} {end.day + 1}-{days_in}" if i == 0 and end.day < days_in
                          else f"{_MON[int(k[5:7]) - 1]} {k[:4]}")
    return {
        "company": co, "source": "live", "business": business, "revenue_line": data["dues"]["line"],
        "as_of": end.isoformat(), "today": today.isoformat(), "horizon": keys[-1],
        "first_fraction": (days_in - end.day) / days_in,
        "contracts": {k: (v if k == "members" else _r(v)) for k, v in c.items()},
        "schedule": [{"key": k, "label": label(k, i), "from_paid": _r(sched[k][0]), "from_future": _r(sched[k][1])}
                     for i, k in enumerate(keys)],
        "collections": {k: _r(v) for k, v in coll.items() if k in in_view},
        "renewals": {k: {**r, "earned": _r(r["earned"]), "collected": _r(r["collected"]), "value": _r(r["value"])}
                     for k, r in due.items() if r["count"] or r["earned"] or r["collected"]},
        "mix": {"pif": round(sum(1 for p in known if p == "pif") / len(known), 4) if known else 0.0},
        "assumptions": {"renew_pct": renew_pct, "new_members": 0,
                        "new_value": float(round(sum(values) / len(values))) if values else 0.0},
        "retention": {"lost12": lost12, "members": len(members)},
        "cash": {"from_books": True},
        "launches": [], "selling": [], "plan_from": {}, "plan_defaults": {}, "notes": {},
        "labels": {"in": "Dues in", "still_to_collect": "dues still to come"},
        "detail": {"members": sorted(rows, key=lambda r: (r["flag"] is None, -r["value"])), "left_out": left_out,
                   "counts": {k: (_r(v) if k.endswith("value") else v) for k, v in n_flag.items()}},
    }


async def _records(s: AsyncSession, tenant_id, biz_id) -> dict:
    """The membership business's members, payments (GHL's own and the legacy Stripe account's, deduplicated the way
    the Forum tab does it), subscriptions, and the memberships lost."""
    async def recs(source, kind, *extra):
        return list((await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == biz_id,
            MetricRecord.source == source, MetricRecord.kind == kind, *extra))).scalars().all())
    payments = await recs("ghl", "payment")
    legacy = await recs("stripe_legacy", "payment")
    if legacy:
        payments, _ = merge_payment_sources([p for p in payments if not (p.meta or {}).get("imported")], legacy)
    return {"members": await recs("ghl", "member", MetricRecord.status == "active"),
            "payments": payments,
            "subs": await recs("ghl", "subscription") + await recs("stripe_legacy", "subscription"),
            "lost": await recs("ghl", "membership_lost")}


async def blocks(s: AsyncSession, tenant_id, data: dict, today: dt.date | None = None) -> list[dict]:
    """The blocks the app projects from its own records for this workspace's file (the Forum's, when it has one)."""
    biz = await roles.membership(s, tenant_id)
    if biz is None or not await companies(s, tenant_id, data):
        return []
    r = await _records(s, tenant_id, biz.id)
    block = build(data, r["members"], r["payments"], r["subs"], r["lost"], today or _today(), biz.name)
    return [block] if block else []


async def companies(s: AsyncSession, tenant_id, data: dict) -> list[str]:
    """Which of the file's companies the app projects from its own records: the dues company, when the workspace has
    a membership business with members on record and the file doesn't project that company itself. A cheap check,
    for the overview."""
    co = (data.get("dues") or {}).get("company")
    if not co or co not in data["companies"] or co == (data.get("forward") or {}).get("company"):
        return []
    biz = await roles.membership(s, tenant_id)
    if biz is None:
        return []
    found = (await s.execute(select(MetricRecord.id).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == biz.id, MetricRecord.source == "ghl",
        MetricRecord.kind == "member", MetricRecord.status == "active").limit(1))).first()
    return [co] if found else []
