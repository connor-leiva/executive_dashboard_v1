"""The Forum focused view — the /api/v1/forum payload, built from metric_record +
the GHL integration config. Shaped exactly on the mockup's data objects
(the-forum-view-v2.jsx) so wiring is a drop-in. Every section degrades to a
fallback (None / dimmed) when its GHL inputs are missing — it never errors.

Point-in-time vs period: only `new_members` respects `period`; ARR, MRR, the
funnel, the renewal book, event registration, and payment mix are current-state.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import MetricRecord, Business, Integration
from .metrics import _period_range, _forum_kpis


def _usd(n) -> str:
    n = float(n or 0)
    if abs(n) >= 1_000_000:
        return f"${n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"${round(n / 1000):,}K"
    return f"${round(n):,}"


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _mon3(s: str) -> str:
    return (s or "")[:3].lower()


async def build_forum(s: AsyncSession, tenant_id, period: str) -> dict:
    start, end = _period_range(period)
    biz = (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id, Business.key == "springb"))).scalar_one_or_none()
    if not biz:
        return {"status": "pending", "members_total": 0, "kpis": [], "deck": [],
                "funnel": None, "renewals": None, "event": None, "revq": None,
                "watch": {"count": 0, "items": []}, "pl": None}

    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.business_id == biz.id,
        Integration.provider == "ghl"))).scalar_one_or_none()
    cfg = (integ.config or {}) if integ else {}

    def base(kind):
        return (MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == biz.id,
                MetricRecord.source == "ghl", MetricRecord.kind == kind)

    async def records(kind, *extra):
        return (await s.execute(select(MetricRecord).where(*base(kind), *extra))).scalars().all()

    k = await _forum_kpis(s, tenant_id, biz.id, start, end)
    members_total = k["members"]
    seg = k["segments"]
    forum_n = seg.get("forum", 0) + seg.get("member", 0)
    ic_n = seg.get("inner_circle", 0)
    arr = k["arr"]

    # Registrations split members from guests (guests are additive prospect seats,
    # counted separately) so the "Registered" KPI and the event card agree.
    all_regs = await records("registration")
    guests = sum(1 for r in all_regs if (r.meta or {}).get("guest"))
    member_regs = len(all_regs) - guests

    # ── KPI tiles (mirror the operational panel) ──
    kpis = [
        {"key": "active_members", "label": "Active Members", "value": str(members_total),
         "sub": f"Forum {forum_n} · Inner Circle {ic_n}", "drill": "active_members"},
        {"key": "forum_arr", "label": "Forum ARR", "value": _usd(arr) if arr else "—",
         "sub": f"{k['memberships']} memberships", "drill": "forum_arr"},
        {"key": "new_members", "label": "New Members", "value": str(k["new_members"]),
         "sub": _period_label(period)},
        {"key": "renewals_due", "label": "Renewals Due", "value": str(k["renewals_due"]),
         "sub": _MONTHS[dt.date.today().month - 1], "drill": "renewal_book"},
        {"key": "registered", "label": "Registered", "value": str(member_regs),
         "sub": cfg.get("event_name") or "next event", "drill": "registered"},
        {"key": "mrr", "label": "MRR", "value": _usd(k["mrr"]) if k["mrr"] else "—",
         "sub": "monthly subscriptions", "drill": "monthly"},
    ]

    memberships = await records("membership")
    # Memberships live in the (Forum) renewals pipeline and carry no segment;
    # recover it by joining to the member roster on contact id.
    member_recs = await records("member", MetricRecord.status == "active")   # primary + add-on
    admin_recs = await records("member", MetricRecord.status == "admin")     # staff, not members
    seg_by_contact = {m.external_id: m.segment for m in member_recs}
    roster = _roster_summary(member_recs, admin_recs, forum_n, ic_n)
    funnel = await _funnel(s, base, cfg)
    renewals = _renewals(memberships, seg_by_contact)
    event = _event(cfg, members_total, member_regs, guests)

    # ── Cash & Billing (GHL Payments, Stripe-fed) — supersedes Revenue Quality ──
    from .billing import compute_billing, merge_payment_sources
    payments = await records("payment")
    subs_all = list(await records("subscription"))

    async def legacy_recs(kind):
        return (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == biz.id,
            MetricRecord.source == "stripe_legacy", MetricRecord.kind == kind))).scalars().all()

    # Legacy Stripe backfill: the original account's Forum charges still bill outside
    # the new sub-account. Merge charges (deduping the CSV-backfill copies already in
    # the GHL feed so nothing double-counts) and ADD the legacy subscriptions to the
    # recurring book so MRR + the forward projection stop understating legacy dues.
    # Legacy subscribers aren't in the new-GHL subs (that's the gap) → subs are additive.
    legacy = await legacy_recs("payment")
    if legacy:
        # The CSV-imported backfill copies are redundant with the authoritative, labeled,
        # classified legacy-Stripe pull (and were the source of the duplicates AND the
        # unclassified 'Other' leakage — an excluded charge's copy resurfaced with no twin
        # to dedupe against). Drop them from billing; they stay in GHL for contact history.
        # Native new-sub-account charges (not imported) are kept.
        native = [p for p in payments if not (p.meta or {}).get("imported")]
        dropped = len(payments) - len(native)
        payments, suppressed = merge_payment_sources(native, legacy)
        print(f"[forum] legacy merge: {len(legacy)} legacy · dropped {dropped} imported backfill "
              f"copies · {suppressed} deduped", flush=True)
    subs_all += list(await legacy_recs("subscription"))

    # PIF renewal projection: paid-in-full members have no monthly subscription, so
    # project their annual renewal lump from the GHL membership fields (renewal date +
    # total cost) — excluding anyone already covered by an active sub (no double-count).
    from .billing import project_renewals
    active_sub_emails = {(x.email or "").lower() for x in subs_all if x.status == "active" and x.email}
    renewal_members = [m for m in member_recs if (m.email or "").lower() not in active_sub_emails]
    today = dt.date.today()
    extra = project_renewals(renewal_members, today, dt.date(today.year, 12, 31))
    billing = compute_billing(payments, subs_all, arr, start, end, today, extra_projected=extra)

    # ── watch signals (only real ones: failed/past-due payments + event pace) ──
    watch_items = []
    if billing.get("available") and (billing.get("failed_count") or billing.get("past_due")):
        watch_items.append("payments")
    if event and event.get("behind_pace"):
        watch_items.append("behind_pace")

    # ── deep-dive deck (Recruiting · Renewals · Event readiness — revq retired) ──
    deck = _deck(funnel, renewals, event, members_total, k["registered"])

    return {
        "status": "watch" if watch_items else "healthy",
        "watch": {"count": len(watch_items), "items": watch_items},
        "members_total": members_total,
        "roster": roster,
        "pl": None,                       # shared springb P&L is rendered by the dashboard payload
        "kpis": kpis, "deck": deck, "funnel": funnel,
        "renewals": renewals, "event": event, "billing": billing,
    }


def _roster_summary(members, admins, forum_n, ic_n) -> dict:
    """Composition of the active roster for the Members & Growth panel. `members` are the
    real member seats (Member Type primary/add-on); `admins` are staff, counted separately
    and excluded from the member total. Program split + primary/add-on + the monthly/PIF/
    payment mix (monthly / quarterly / PIF / installments), all from the GHL Member Type /
    Payment Plan fields (`unspecified` only arises in the degraded tag-fallback path)."""
    comp = {"primary": 0, "add_on": 0, "unspecified": 0}
    mix = {"monthly": 0, "quarterly": 0, "pif": 0, "installments": 0}
    for m in members:
        mem = (m.meta or {}).get("membership") or {}
        kind = mem.get("member_kind")
        comp[kind if kind in ("primary", "add_on") else "unspecified"] += 1
        pay = mem.get("payment")
        if pay in mix:
            mix[pay] += 1
    return {
        "total": len(members),                 # members only — admins excluded
        "forum": forum_n, "inner_circle": ic_n,
        "primary": comp["primary"], "add_on": comp["add_on"],
        "admin": len(admins), "unspecified": comp["unspecified"],
        "payment_mix": mix,
    }


def _period_label(period: str) -> str:
    return {"mtd": "month to date", "qtd": "quarter to date",
            "ytd": "year to date", "last_month": "last month"}.get(period, period)


# The Forum Main Sales Funnel has ~22 raw stages; collapse them into a clean
# 4-step recruiting funnel. Overridable via cfg["recruiting_stage_groups"]
# (ordered [label, [stage-substrings]]). First matching group wins, so substrings
# are precise: e.g. "VIP Guest- application submitted" must land in VIP Guest, not
# Applied. Dead/nurture stages (Unresponsive, No Show, Attended in the Past) match
# no group and are counted only in the footer. Count-only — the sales funnel opps
# don't carry meaningful $ (real dollars live in the renewals pipeline + payments).
DEFAULT_RECRUITING_GROUPS = [
    ["Applied", ["qualif", "opt in", "did not schedule"]],
    ["Appointment", ["scheduled appointment", "appointment complete"]],
    ["VIP Guest", ["vip guest"]],
    ["Contract sent", ["sent contract"]],
    ["Onboarding", ["payment received", "fulfillment"]],
]


async def _funnel(s, base, cfg) -> dict | None:
    recs = (await s.execute(select(MetricRecord).where(*base("recruiting")))).scalars().all()
    if not recs:
        return None
    groups = cfg.get("recruiting_stage_groups") or DEFAULT_RECRUITING_GROUPS
    buckets = {label: {"label": label, "v": 0} for label, _ in groups}
    grouped = 0
    for r in recs:
        stage = ((r.meta or {}).get("stage") or "").lower()
        label = next((lbl for lbl, subs in groups if any(sub in stage for sub in subs)), None)
        if label is None:                        # dead/nurture stage → footer only
            continue
        buckets[label]["v"] += 1
        grouped += 1
    stages = [buckets[lbl] for lbl, _ in groups if buckets[lbl]["v"] > 0]
    if not stages:
        return None
    other = len(recs) - grouped
    footer = cfg.get("funnel_footer")
    if other > 0 and not footer:
        footer = f"{other} more in nurture / unresponsive stages (not active deals)"
    return {"stages": [{"label": g["label"], "v": g["v"]} for g in stages], "footer": footer}


def _renewals(memberships, seg_by_contact=None) -> dict | None:
    """Members due to renew in the next 90 days, by month + contract value.
    (Renewal *health* isn't tracked in GHL — the renewals pipeline is filed by
    month — so there are no committed/talking/risk statuses.)"""
    if not memberships:
        return None
    seg_by_contact = seg_by_contact or {}
    today = dt.date.today()
    window = {_MONTHS[(today.month - 1 + i) % 12] for i in range(3)}   # this + next 2 months
    rows = []
    segments = {"F": 0, "IC": 0}
    for m in memberships:
        meta = m.meta or {}
        mon = (meta.get("renewal_month") or "")[:3].title()
        if mon not in {w[:3] for w in window}:
            continue
        seg_raw = seg_by_contact.get(meta.get("contact_id")) or m.segment
        seg = "IC" if seg_raw == "inner_circle" else "F"
        segments[seg] += 1
        val = float(m.amount or 0)
        rows.append({"name": (m.name or "").title() or m.external_id,
                     "seg": seg, "month": mon, "value": _usd(val), "_val": val})
    if not rows:
        return None
    order = {"Jan": 0, "Feb": 1, "Mar": 2, "Apr": 3, "May": 4, "Jun": 5,
             "Jul": 6, "Aug": 7, "Sep": 8, "Oct": 9, "Nov": 10, "Dec": 11}
    rows.sort(key=lambda r: (order.get(r["month"], 99), -r["_val"]))
    total = sum(r["_val"] for r in rows)
    return {"rows": [{kk: vv for kk, vv in r.items() if kk != "_val"} for r in rows[:6]],
            "summary": {"count": len(rows), "value": _usd(total),
                        "segments": segments, "retention": None}}


def _event(cfg, members_total, member_regs, guests) -> dict | None:
    # Render whenever a next event is configured (name / tag / title / date).
    # The date only powers the countdown — without it days_out is None but the
    # registration progress + call list still show.
    if not (cfg.get("event_date") or cfg.get("event_name")
            or cfg.get("event_tag") or cfg.get("event_title")):
        return None
    days_out = None
    if cfg.get("event_date"):
        try:
            days_out = (dt.date.fromisoformat(str(cfg["event_date"])) - dt.date.today()).days
        except (ValueError, TypeError):
            days_out = None
    unregistered = max(0, members_total - member_regs)
    # pace: latest reg_count for the prior event vs config fallback
    prior = cfg.get("prior_event_pace")
    behind = bool(prior) and member_regs < int(prior)
    pace_note = (f"{prior} were registered at this point before the prior event"
                 if prior is not None else None)
    return {"title": cfg.get("event_title") or "Next event", "where": cfg.get("event_name") or "",
            "when": cfg.get("event_dates") or "", "days_out": days_out,
            "registered": member_regs, "members": members_total, "guests": guests,
            "unregistered": unregistered, "behind_pace": behind, "pace_note": pace_note}


async def _revq(s, base, memberships, arr, tenant_id, business_id) -> dict | None:
    subs = (await s.execute(select(MetricRecord).where(*base("subscription")))).scalars().all()
    if not subs and not memberships:
        return None
    mrr = sum(float(x.amount or 0) for x in subs if x.status == "active")
    past = [x for x in subs if x.status == "past_due"]
    # payment mix from membership.meta.payment
    monthly = [m for m in memberships if (m.meta or {}).get("payment") == "monthly"]
    monthly_val = sum(float(m.amount or 0) for m in monthly)
    pif = [m for m in memberships if (m.meta or {}).get("payment") != "monthly"]
    pif_val = arr - monthly_val
    out = {
        "pif": {"value": round(pif_val, 2), "count": len(pif)} if memberships else None,
        "monthly": ({"value": round(monthly_val, 2), "count": len(monthly),
                     "sub": f"{_usd(mrr)} MRR annualized"} if subs else None),
        "past_due": ({"count": len(past), "value": _usd(sum(float(x.amount or 0) for x in past))}
                     if subs else None),
    }
    # ARR bridge (YTD). Onboarded opps carry no $ (they live in the sales funnel),
    # so New = the membership contract value of contacts who onboarded this year.
    # Churn = lost renewals opps this year (those DO carry monetaryValue).
    year_start = dt.date(dt.date.today().year, 1, 1)
    onb = (await s.execute(select(MetricRecord).where(
        *base("onboarded"), MetricRecord.occurred_on >= year_start))).scalars().all()
    onb_contacts = {(o.meta or {}).get("contact_id") for o in onb if (o.meta or {}).get("contact_id")}
    new_ytd = sum(float(m.amount or 0) for m in memberships
                  if (m.meta or {}).get("contact_id") in onb_contacts)
    # Fallback to onboarded amounts if contacts don't join (e.g. no membership yet).
    if not new_ytd:
        new_ytd = sum(float(o.amount or 0) for o in onb)
    lost = (await s.execute(select(MetricRecord).where(*base("membership_lost")))).scalars().all()
    churn_ytd = sum(float(x.amount or 0) for x in lost
                    if x.occurred_on and x.occurred_on >= year_start)
    # Only render the bridge when there's real churn data to close it. Without
    # lost-membership records the line is a misleading flat bar (churn actually
    # lives in "offboarded" tags today) — omit it per the spec's fallback rule.
    if lost and (new_ytd or churn_ytd):
        start_arr = arr - new_ytd + churn_ytd
        out["bridge"] = [
            {"label": "Jan 1", "value": _usd(start_arr)},
            {"label": "New", "value": f"+{_usd(new_ytd)}"},
            {"label": "Churned", "value": f"−{_usd(churn_ytd)}", "soft": True},
            {"label": "Today", "value": _usd(arr), "tot": True},
        ]
    return out


def _deck(funnel, renewals, event, members_total, registered) -> list[dict]:
    deck = []
    if funnel:
        last = funnel["stages"][-1] if funnel["stages"] else {"v": 0, "label": "pipeline"}
        deck.append({"k": "pipeline", "label": "Recruiting pipeline",
                     "hero": str(sum(x["v"] for x in funnel["stages"])), "hero_sub": "in the pipeline",
                     "salient": f"{last['v']} in {last['label'].lower()}", "tone": "good"})
    if renewals:
        sm = renewals["summary"]
        seg = sm.get("segments", {})
        deck.append({"k": "renewals", "label": "Renewals · next 90 days",
                     "hero": sm["value"], "hero_sub": f"{sm['count']} renewals",
                     "salient": f"Forum {seg.get('F', 0)} · Inner Circle {seg.get('IC', 0)}",
                     "tone": "good"})
    if event:
        pct = round((event["registered"] / event["members"] * 100)) if event["members"] else 0
        deck.append({"k": "event", "label": f"Next event · {event['where']}",
                     "hero": str(event["days_out"]) if event["days_out"] is not None else "—",
                     "hero_sub": "days out",
                     "salient": f"{event['unregistered']} unregistered" + (" · behind pace" if event["behind_pace"] else ""),
                     "tone": "watch" if (pct < 50 and event["unregistered"] > 0) else "good"})
    return deck
