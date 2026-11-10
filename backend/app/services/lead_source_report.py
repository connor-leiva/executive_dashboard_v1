"""The lead-source cohort report (docs/specs/ulrg-reporting-dashboard-port.md §4.2, §4.3, §4.7, phase 3).

PURE READS OVER POSTGRES. No call to Follow Up Boss, Sisu or QuickBooks happens on a request: the
leads, their activity and their linked deals were stored by the syncs (phase 2), and spend by Books.

`load_universe` builds, for one lead-source group, the same per-person shape the legacy app built
from live API calls -- created, stage, first outbound touch, the touch list, appointments, linked
Sisu transactions -- and every report (cohorts here, the pipeline and What's Working beside it)
reads that one shape, so a rule cannot drift between them.

The cohort rules are ported without changing them, with ONE deliberate difference the spec decided
(§Decisions 4): the cohort month is `lead.created_at_src` -- the workspace's calendar date -- not the
UTC month. A lead at 11pm Mountain on 31 January is a January lead here and a February lead in the
legacy report.

Spend and ROI follow §4.7, not the legacy flat fee: QuickBooks lines per month, the cost share,
both ROI multiples, and a month with spend and no leads still in the program total.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select

from ..models import Agent, CrmActivity, Integration, Lead, LeadSourceGroup, Transaction
from . import lead_source_spend as spend
from . import lead_sources as ls

# ── the legacy constants, verbatim (roi-conversion-dashboard/fub_live_dashboard/backend/app/cohorts.py) ──
STAGE_KEYWORDS_UNDER_CONTRACT = ("under contract", "pending", "escrow")
STAGE_KEYWORDS_CLOSED = ("closed", "sold")
STAGE_KEYWORDS_DEAD = ("dead", "trash", "archive", "junk", "do not contact", "lost", "unqualified")
STAGE_KEYWORDS_APPT_SET = ("appointment set", "appointment")
STAGE_KEYWORDS_APPT_HELD = ("met with", "showing", "tour", "listing agreement", "active listing",
                            "submitting offers", "offer")
APPT_HELD_OUTCOME_TOKENS = ("attended", "completed", "held", "showed", "kept")
MATURITY_CURVE = {1: 5, 2: 8, 3: 12, 4: 18, 5: 28, 6: 45, 7: 55, 8: 63, 9: 75, 10: 82, 11: 87, 12: 92}
SPEED_BUCKET_ORDER = ["u5min", "u30min", "u2hr", "u24hr", "u72hr", "o72hr", "never"]
SPEED_BUCKET_LABELS = {"u5min": "Under 5 min", "u30min": "5–30 min", "u2hr": "30 min–2 hr",
                       "u24hr": "2–24 hr", "u72hr": "24–72 hr", "o72hr": "Over 72 hr",
                       "never": "Never contacted"}
BENCHMARKS = {"industry_avg": [1.0, 3.0], "top_quartile": [5.0, 8.0], "elite_isa": [10.0, 14.0]}
LIVE_OUTCOMES = ("met", "kept", "completed")
UNMATCHED_CAP = 50


def safe_pct(num, den) -> float:
    if not den:
        return 0.0
    return round((num / den) * 100.0, 1)


def _stage_has(stage: str, tokens) -> bool:
    s = (stage or "").strip().lower()
    return any(t in s for t in tokens)


def speed_bucket(minutes: float | None) -> str:
    if minutes is None:
        return "never"
    if minutes < 5:
        return "u5min"
    if minutes < 30:
        return "u30min"
    if minutes < 120:
        return "u2hr"
    if minutes < 24 * 60:
        return "u24hr"
    if minutes < 72 * 60:
        return "u72hr"
    return "o72hr"


def cohort_label(key: str) -> str:
    try:
        return dt.datetime.strptime(key, "%Y-%m").strftime("%b %Y")
    except ValueError:
        return key


def cohort_age_months(key: str, today: dt.date) -> float:
    try:
        c = dt.datetime.strptime(key, "%Y-%m")
    except ValueError:
        return 0.0
    months = (today.year - c.year) * 12 + (today.month - c.month)
    months += (today.day - 1) / 30.0
    return round(max(months, 0.0), 1)


def _aware(d):
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


# ── the universe ─────────────────────────────────────────────────────────────────────────────

def classify_transaction(t: Transaction, today: dt.date) -> str:
    """The legacy order, on dates rather than the team's status strings:
    closed (close date has passed) > lost (lost reason or archived) > under contract > active.
    Status code LOSTT alone is NOT lost here, unlike `transaction.status` (open question §12.8)."""
    if t.close_date is not None and t.close_date <= today:
        return "closed"
    lost = t.lost_signal if t.lost_signal is not None else (t.status == "dead")
    if lost:
        return "lost"
    if t.contract_date is not None:
        return "under_contract"
    return "active"


def best_state(states: list[str]) -> str | None:
    for want in ("closed", "under_contract", "lost", "active"):
        if want in states:
            return want
    return None


async def _workspace_tz(s, tenant_id) -> dt.tzinfo:
    from .sync import _workspace_tz as tz_of
    return await tz_of(s, tenant_id)


async def fub_link_base(s, tenant_id, business_id) -> str | None:
    """`https://{account}.followupboss.com/2/people/view`, from the account domain the FUB sync
    learned (`fub_state.account_domain`) -- never a host written into code."""
    for integ in (await s.execute(select(Integration).where(
            Integration.tenant_id == tenant_id, Integration.provider == "fub",
            Integration.business_id == business_id))).scalars():
        domain = (((integ.config or {}).get("fub_state") or {}).get("account_domain") or "").strip()
        if domain:
            return f"https://{domain}.followupboss.com/2/people/view"
    return None


async def load_universe(s, tenant_id, business_id, group: LeadSourceGroup, *, now=None) -> dict:
    """Every lead in the group from its program-start month, with what each report needs.

    Returns {"people": [...], "tz", "today", "now", "origins"}. A person is a plain dict in the
    legacy shape, keyed so that the ported functions read it unchanged.
    """
    tz = await _workspace_tz(s, tenant_id)
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(tz).date()
    origins = await ls.matching_origins(s, tenant_id, business_id, group)
    out = {"people": [], "tz": tz, "today": today, "now": now, "origins": origins}
    if not origins:
        return out
    start_key = group.program_start.strftime("%Y-%m") if group.program_start else None
    leads = (await s.execute(select(Lead).where(
        Lead.tenant_id == tenant_id, Lead.business_id == business_id, Lead.origin.in_(origins),
        Lead.created_at_src.is_not(None)))).scalars().all()
    leads = [lead for lead in leads if not start_key or lead.created_at_src.strftime("%Y-%m") >= start_key]
    if not leads:
        return out
    agent_ids = {lead.agent_id for lead in leads if lead.agent_id}
    agents = {a.id: a.name for a in (await s.execute(select(Agent).where(
        Agent.tenant_id == tenant_id, Agent.id.in_(agent_ids or {None})))).scalars()} if agent_ids else {}

    ext_ids = [lead.external_id for lead in leads]
    acts: dict[str, list] = defaultdict(list)
    for i in range(0, len(ext_ids), 900):
        for a in (await s.execute(select(CrmActivity).where(
                CrmActivity.tenant_id == tenant_id, CrmActivity.source == "fub",
                CrmActivity.person_external_id.in_(ext_ids[i:i + 900])))).scalars():
            acts[a.person_external_id].append(a)
    lead_ids = [lead.id for lead in leads]
    txns: dict = defaultdict(list)
    for i in range(0, len(lead_ids), 900):
        for t in (await s.execute(select(Transaction).where(
                Transaction.tenant_id == tenant_id, Transaction.business_id == business_id,
                Transaction.lead_id.in_(lead_ids[i:i + 900])))).scalars():
            txns[t.lead_id].append(t)
    link_base = await fub_link_base(s, tenant_id, business_id)

    for lead in leads:
        created = _aware(lead.src_created_at)
        if created is None:                               # no timestamp: the workspace's midnight
            created = dt.datetime.combine(lead.created_at_src, dt.time.min, tzinfo=tz).astimezone(dt.timezone.utc)
        touches, appts = [], []
        for a in acts.get(lead.external_id, []):
            at = _aware(a.occurred_at)
            if a.kind == "appointment":
                appts.append({"start": at, "outcome": (a.outcome or "").lower()})
                if at is None:
                    continue
                kind = "live" if (a.outcome or "").lower() in LIVE_OUTCOMES else "try"
            elif a.kind in ("call", "text"):
                if a.direction != "outbound":
                    continue                                  # an inbound call is not the agent's touch
                kind = "live" if (a.kind == "call" and a.is_live) else "try"
            elif a.kind == "email":
                kind = "try"
            else:
                continue
            if at is None or at < created:
                continue
            touches.append({"kind": kind, "at": at, "channel": a.channel,
                            "duration_sec": float(a.duration_sec) if a.duration_sec is not None else None,
                            "label": f"{a.channel.title()} {at.astimezone(tz).strftime('%b %d')}"})
        touches.sort(key=lambda t: t["at"])
        deals = txns.get(lead.id, [])
        states = [classify_transaction(t, today) for t in deals]
        out["people"].append({
            "lead_id": lead.id, "person_id": lead.external_id, "full_name": lead.name or "",
            "email": lead.email, "source": lead.origin, "created": created,
            "cohort_key": lead.created_at_src.strftime("%Y-%m"), "created_date": lead.created_at_src,
            "stage": (lead.stage or ""),
            "agent_id": str(lead.agent_id) if lead.agent_id else None,
            "assigned_to": agents.get(lead.agent_id) or "Unassigned",
            "contacted_flag": bool(lead.contacted),
            "price": float(lead.price) if lead.price is not None else None,
            "first_outbound_at": touches[0]["at"] if touches else None,
            "last_activity_at": touches[-1]["at"] if touches else None,
            "touches": touches, "appointments": appts, "deals": [],
            "transactions": deals, "tx_states": states, "sisu_state": best_state(states),
            "sisu_matched": bool(lead.sisu_contact_id) or bool(deals),
            "sisu_checked": lead.sisu_checked_at is not None,
            "fub_url": f"{link_base}/{lead.external_id}" if link_base else None,
        })
    return out


# ── cohorts (§4.2) ───────────────────────────────────────────────────────────────────────────

def aggregate_cohorts(people: list[dict], *, now: dt.datetime, today: dt.date) -> dict:
    """Port of legacy `aggregate_cohorts`: one row per cohort month, the speed table, the
    uncontacted snapshot. Contacted means a first outbound touch exists -- not FUB's flag."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for p in people:
        buckets[p["cohort_key"]].append(p)
    rows, speed, uncontacted = [], [], []
    current_key = today.strftime("%Y-%m")
    for ckey in sorted(buckets):
        members = buckets[ckey]
        n = len(members)
        sb = {k: 0 for k in SPEED_BUCKET_ORDER}
        mins_list: list[float] = []
        n_contacted = n_u5 = n_u24 = 0
        u24 = u48 = u72 = 0
        n_set = n_held = n_uc = n_closed = n_dead = n_active = 0
        for p in members:
            created, stage = p["created"], p["stage"]
            fc = p["first_outbound_at"]
            if fc is not None:
                minutes = max((fc - created).total_seconds() / 60.0, 0.0)
                mins_list.append(minutes)
                n_contacted += 1
                if minutes < 5:
                    n_u5 += 1
                if minutes <= 24 * 60:
                    n_u24 += 1
                sb[speed_bucket(minutes)] += 1
            else:
                sb["never"] += 1
                age_h = (now - created).total_seconds() / 3600.0
                u24 += age_h >= 24
                u48 += age_h >= 48
                u72 += age_h >= 72
            appts = p["appointments"]
            held_record = any(any(tok in (a["outcome"] or "") for tok in APPT_HELD_OUTCOME_TOKENS) for a in appts)
            deals = p["deals"]
            deal_closed = any(("closed" in (d.get("status") or "")) or ("won" in (d.get("status") or "")) for d in deals)
            deal_uc = any(_stage_has(d.get("stage") or "", STAGE_KEYWORDS_UNDER_CONTRACT) for d in deals)
            is_dead = _stage_has(stage, STAGE_KEYWORDS_DEAD)
            is_closed = _stage_has(stage, STAGE_KEYWORDS_CLOSED) or deal_closed
            is_uc = (_stage_has(stage, STAGE_KEYWORDS_UNDER_CONTRACT) or deal_uc) and not is_closed
            held_by_stage = _stage_has(stage, STAGE_KEYWORDS_APPT_HELD) or is_uc or is_closed
            set_by_stage = _stage_has(stage, STAGE_KEYWORDS_APPT_SET) or held_by_stage
            if appts or set_by_stage:
                n_set += 1
            if held_record or held_by_stage:
                n_held += 1
            if is_closed:
                n_closed += 1
            elif is_uc:
                n_uc += 1
            elif is_dead:
                n_dead += 1
            else:
                n_active += 1
        avg = round(sum(mins_list) / len(mins_list), 1) if mins_list else None
        age = cohort_age_months(ckey, today)
        is_mtd = ckey == current_key
        floor = max(int(age), 0)
        maturity = 100 if floor >= 12 else MATURITY_CURVE.get(floor, 0)
        pct24 = safe_pct(n_u24, n)
        rows.append({
            "cohort_month": cohort_label(ckey), "cohort_key": ckey, "leads_received": n,
            "contacted_count": n_contacted, "contacted_pct": safe_pct(n_contacted, n),
            "avg_time_to_contact_minutes": avg, "pct_contacted_5min": safe_pct(n_u5, n),
            "pct_contacted_24hr": pct24, "appts_set_count": n_set, "appts_set_pct": safe_pct(n_set, n),
            "appts_held_count": n_held, "appts_held_pct": safe_pct(n_held, n_set),
            "under_contract_count": n_uc, "closed_count": n_closed, "dead_count": n_dead,
            "active_count": n_active, "conversion_to_date_pct": safe_pct(n_closed, n),
            "cohort_age_months": age, "is_mtd": is_mtd, "maturity_pct": maturity,
            "show_conversion": age >= 3 and not is_mtd, "muted_conversion": age < 6,
            "industry_comparable": age >= 12,
            "flag_slow_contact": avg is not None and avg > 30,
            "flag_low_24hr_contact": pct24 < 85.0 and n > 0,
        })
        speed.append({"cohort_month": cohort_label(ckey), "cohort_key": ckey, "total": n,
                      "buckets": dict(sb), "bucket_pcts": {k: safe_pct(sb[k], n) for k in SPEED_BUCKET_ORDER}})
        uncontacted.append({"cohort_month": cohort_label(ckey), "cohort_key": ckey, "total": n,
                            "uncontacted_total": sb["never"], "u24_count": u24, "u48_count": u48,
                            "u72_count": u72})
    return {"cohort_rows": rows, "speed_table": speed, "uncontacted_table": uncontacted}


# ── Sisu rollup (§4.3) ───────────────────────────────────────────────────────────────────────

def _f(v) -> float:
    return float(v) if v is not None else 0.0


def sisu_rollup(people: list[dict], rows: list[dict], *, today: dt.date) -> dict:
    """Per cohort month: matched people, transaction counts by legacy state, and the closed deals'
    GCI, volume and company dollar. Counts are TRANSACTIONS, not people (a person can have two)."""
    agg: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    closed_detail, uc_detail, pending_lines = [], [], 0
    for p in people:
        a = agg[p["cohort_key"]]
        if not p["sisu_matched"]:
            continue
        a["matched"] += 1
        for t, state in zip(p["transactions"], p["tx_states"], strict=True):
            a["txns"] += 1
            a[state] += 1
            if state == "closed":
                gci = _f(t.gci)
                volume = _f(t.closed_volume) or _f(t.sale_price)
                a["gci"] += gci
                a["volume"] += volume
                if t.company_dollar is None:
                    pending_lines += 1                       # not read from Sisu yet: shown, not guessed
                    a["pending_lines"] += 1
                else:
                    a["company_dollar"] += _f(t.company_dollar)
                    a["team_income"] += _f(t.team_income)
                    a["adjusted_gci"] += _f(t.adjusted_gci) or gci
                    a["referral_out"] += _f(t.referral_fee_out)
                    a["trans_fees"] += _f(t.trans_fee)
                closed_detail.append({
                    "transaction_id": t.external_id, "cohort_month": p["cohort_key"],
                    "name": p["full_name"] or t.buyer_name or "", "address": t.address or "",
                    "close_date": t.close_date.isoformat() if t.close_date else None,
                    "agent": p["assigned_to"], "gci": round(gci, 2),
                    "trans_fee": round(_f(t.trans_fee), 2) if t.trans_fee is not None else None,
                    "exp_risk_team": round(_f(t.exp_risk_team), 2) if t.exp_risk_team is not None else None,
                    "exp_risk_agent": round(_f(t.exp_risk_agent), 2) if t.exp_risk_agent is not None else None,
                    "agent_payment": round(_f(t.agent_payment), 2) if t.agent_payment is not None else None,
                    "company_dollar": round(_f(t.company_dollar), 2) if t.company_dollar is not None else None,
                    "volume": round(volume, 2),
                    "exp_risk_source": (t.exp_risk_source or "").upper() or None,
                    "fub_person_id": p["person_id"], "fub_source": p["source"], "fub_url": p["fub_url"],
                })
            elif state == "under_contract":
                uc_detail.append({
                    "transaction_id": t.external_id, "cohort_month": p["cohort_key"],
                    "name": p["full_name"] or t.buyer_name or "",
                    "uc_date": t.contract_date.isoformat() if t.contract_date else None,
                    "expected_close_date": (t.expected_close_date or t.close_date).isoformat()
                    if (t.expected_close_date or t.close_date) else None,
                    "agent": p["assigned_to"], "gci": round(_f(t.gci), 2),
                    "volume": round(_f(t.closed_volume) or _f(t.sale_price), 2),
                    "fub_person_id": p["person_id"], "fub_source": p["source"],
                })
    for row in rows:
        a = agg.get(row["cohort_key"], {})
        leads = row["leads_received"]
        closes = int(a.get("closed", 0))
        gci, cd = a.get("gci", 0.0), a.get("company_dollar", 0.0)
        ti = a.get("team_income", 0.0)
        row.update({
            "sisu_matched_count": int(a.get("matched", 0)),
            "sisu_match_rate": safe_pct(a.get("matched", 0), leads),
            "sisu_closed_count": closes, "sisu_closed_pct": safe_pct(closes, leads),
            "sisu_under_contract_count": int(a.get("under_contract", 0)),
            "sisu_lost_count": int(a.get("lost", 0)), "sisu_active_count": int(a.get("active", 0)),
            "sisu_transactions_count": int(a.get("txns", 0)),
            "sisu_gci_dollars": round(gci, 2), "sisu_volume_dollars": round(a.get("volume", 0.0), 2),
            "sisu_company_dollar_dollars": round(cd, 2), "sisu_team_income_dollars": round(ti, 2),
            "sisu_adjusted_gci_dollars": round(a.get("adjusted_gci", 0.0), 2),
            "sisu_referral_fees_out_dollars": round(a.get("referral_out", 0.0), 2),
            "sisu_transaction_fees_in_dollars": round(a.get("trans_fees", 0.0), 2),
            "sisu_company_take_pct": round(cd / gci * 100.0, 1) if gci else 0.0,
            "sisu_team_take_pct": round(ti / gci * 100.0, 1) if gci else 0.0,
            "sisu_gci_per_lead": round(gci / leads, 2) if leads else 0.0,
            "sisu_company_dollar_per_lead": round(cd / leads, 2) if leads else 0.0,
            "sisu_company_dollar_per_close": round(cd / closes, 2) if closes else 0.0,
            "sisu_closes_awaiting_commission": int(a.get("pending_lines", 0)),
        })
    closed_detail.sort(key=lambda r: (r["close_date"] or "", r["transaction_id"]), reverse=True)
    uc_detail.sort(key=lambda r: (r["uc_date"] or "", r["transaction_id"]), reverse=True)
    return {"closed": closed_detail, "under_contract": uc_detail, "pending_commission": pending_lines}


# ── spend and ROI (§4.7) ─────────────────────────────────────────────────────────────────────

def _money(v):
    return float(spend._cents(v)) if v is not None else None


def apply_spend(rows: list[dict], m: dict, today: dt.date) -> None:
    """Per cohort month: gross and net spend, cost per lead and per close on each, net profit and
    both ROI multiples. Every one is null when the group has no spend rule -- never $0 and an
    infinite ROI. A month before the stored QuickBooks history is null too: unknown, not free."""
    share = Decimal(m["share_pct"]) if m else Decimal(0)
    history = m.get("history_starts") if m else None
    hist_key = history.strftime("%Y-%m") if history else None
    for row in rows:
        known = bool(m and m["mapped"] and m["synced"]) and (hist_key is None or row["cohort_key"] >= hist_key)
        gross = m["gross_by_month"].get(row["cohort_key"], Decimal(0)) if known else None
        net = spend.net_of(gross, share) if gross is not None else None
        cd = Decimal(str(row.get("sisu_company_dollar_dollars", 0.0)))
        leads, closes = row["leads_received"], row.get("sisu_closed_count", 0)
        row.update({
            "spend_known": known, "gross_spend": _money(gross), "share_pct": float(share),
            "net_spend": _money(net),
            "cost_per_lead_gross": _money(gross / leads) if gross is not None and leads else None,
            "cost_per_lead_net": _money(net / leads) if net is not None and leads else None,
            "cost_per_close_gross": _money(gross / closes) if gross is not None and closes else None,
            "cost_per_close_net": _money(net / closes) if net is not None and closes else None,
            "net_profit_gross": _money(cd - gross) if gross is not None else None,
            "net_profit_net": _money(cd - net) if net is not None else None,
            "roi_gross": round(float(cd / gross), 2) if gross else None,
            "roi_net": round(float(cd / net), 2) if net else None,
        })


def _empty_row(key: str, today: dt.date) -> dict:
    age = cohort_age_months(key, today)
    return {"cohort_month": cohort_label(key), "cohort_key": key, "leads_received": 0,
            "contacted_count": 0, "contacted_pct": 0.0, "avg_time_to_contact_minutes": None,
            "pct_contacted_5min": 0.0, "pct_contacted_24hr": 0.0, "appts_set_count": 0,
            "appts_set_pct": 0.0, "appts_held_count": 0, "appts_held_pct": 0.0,
            "under_contract_count": 0, "closed_count": 0, "dead_count": 0, "active_count": 0,
            "conversion_to_date_pct": 0.0, "cohort_age_months": age,
            "is_mtd": key == today.strftime("%Y-%m"),
            "maturity_pct": 100 if int(age) >= 12 else MATURITY_CURVE.get(int(age), 0),
            "show_conversion": False, "muted_conversion": True, "industry_comparable": False,
            "flag_slow_contact": False, "flag_low_24hr_contact": False, "spend_only": True}


def header(rows: list[dict], m: dict | None) -> dict:
    """The program figures, recomputed from the VISIBLE rows so a month filter can never leave an
    unfiltered total in the header."""
    leads = sum(r["leads_received"] for r in rows)
    cd = sum(r.get("sisu_company_dollar_dollars", 0.0) for r in rows)
    known = [r for r in rows if r.get("spend_known")]
    mapped = bool(m and m["mapped"])
    gross = sum(r["gross_spend"] for r in known) if mapped and known else None
    net = sum(r["net_spend"] for r in known) if mapped and known else None
    cd_known = sum(r.get("sisu_company_dollar_dollars", 0.0) for r in known)
    closes = sum(r.get("sisu_closed_count", 0) for r in rows)
    matched = sum(r.get("sisu_matched_count", 0) for r in rows)
    return {
        "leads": leads, "company_dollar": round(cd, 2),
        "gci": round(sum(r.get("sisu_gci_dollars", 0.0) for r in rows), 2),
        "volume": round(sum(r.get("sisu_volume_dollars", 0.0) for r in rows), 2),
        "sisu_closed": closes, "sisu_matched": matched, "match_rate": safe_pct(matched, leads),
        "closes_awaiting_commission": sum(r.get("sisu_closes_awaiting_commission", 0) for r in rows),
        "gross_spend": round(gross, 2) if gross is not None else None,
        "net_spend": round(net, 2) if net is not None else None,
        # ROI on the months whose spend is known, against those months' company dollar: a ROI that
        # divided all company dollar by part of the spend would flatter the channel.
        "net_profit_gross": round(cd_known - gross, 2) if gross is not None else None,
        "net_profit_net": round(cd_known - net, 2) if net is not None else None,
        "roi_gross": round(cd_known / gross, 2) if gross else None,
        "roi_net": round(cd_known / net, 2) if net else None,
        "spend_months_known": len(known), "spend_months": len(rows),
        "cost_per_lead_gross": round(gross / leads, 2) if gross is not None and leads else None,
        "cost_per_close_gross": round(gross / closes, 2) if gross is not None and closes else None,
    }


async def cohort_report(s, tenant_id, business, group: LeadSourceGroup, *, start: str | None = None,
                        end: str | None = None, now=None) -> dict:
    uni = await load_universe(s, tenant_id, business.id, group, now=now)
    people, today, now = uni["people"], uni["today"], uni["now"]
    tables = aggregate_cohorts(people, now=now, today=today)
    rows = tables["cohort_rows"]
    m = await spend.monthly_spend(s, tenant_id, group)
    # A month with spend and no leads is still a cost of the program (§4.7): it gets a row.
    start_key = group.program_start.strftime("%Y-%m") if group.program_start else None
    have = {r["cohort_key"] for r in rows}
    for key, gross in m["gross_by_month"].items():
        if key not in have and gross and (start_key is None or key >= start_key) and key <= today.strftime("%Y-%m"):
            rows.append(_empty_row(key, today))
    rows.sort(key=lambda r: r["cohort_key"])
    audit = sisu_rollup(people, rows, today=today)
    apply_spend(rows, m, today)

    def visible(key):
        return (start is None or key >= start) and (end is None or key <= end)
    rows_v = [r for r in rows if visible(r["cohort_key"])]
    speed_v = [r for r in tables["speed_table"] if visible(r["cohort_key"])]
    unc_v = [r for r in tables["uncontacted_table"] if visible(r["cohort_key"])]
    people_v = [p for p in people if visible(p["cohort_key"])]
    unmatched = [{"fub_person_id": p["person_id"], "full_name": p["full_name"], "email": p["email"],
                  "cohort_key": p["cohort_key"], "agent": p["assigned_to"], "fub_url": p["fub_url"],
                  "reason": ("No email on the Follow Up Boss record, so Sisu can't be searched"
                             if not p["email"] else "Not found in Sisu" if p["sisu_checked"]
                             else "Not looked up in Sisu yet")}
                 for p in people_v if not p["sisu_matched"]]
    bearer = m["bearers"][0] if m["bearers"] else None
    return {
        "group": {"key": group.key, "display_name": group.display_name,
                  "program_start": group.program_start.isoformat() if group.program_start else None,
                  "matches_nothing": not any(str(v).strip() for v in (group.variants or []))},
        "business": {"key": business.key, "name": business.name},
        "as_of": now.isoformat(timespec="seconds"), "today": today.isoformat(),
        "applied_filters": {"start_cohort": start, "end_cohort": end},
        "available_months": [r["cohort_key"] for r in rows],
        "source_variants_matched": uni["origins"],
        "spend": {"mapped": m["mapped"], "synced": m["synced"], "connected": m["connected"],
                  "history_starts": m["history_starts"].isoformat() if m["history_starts"] else None,
                  "share_pct": float(m["share_pct"]),
                  "bearer_name": bearer["bearer_name"] if bearer else None,
                  # "Sympli", not "Sympli Mortgage": the label reads "ROI after Sympli's share".
                  "bearer_short": bearer["bearer_short"] if bearer else None,
                  "bearers": [{"name": b["bearer_name"], "share_pct": float(b["share_pct"])} for b in m["bearers"]]},
        "header": header(rows_v, m),
        "cohort_rows": rows_v, "speed_table": speed_v, "uncontacted_table": unc_v,
        "speed_bucket_order": SPEED_BUCKET_ORDER, "speed_bucket_labels": SPEED_BUCKET_LABELS,
        "closed_transactions": [r for r in audit["closed"] if visible(r["cohort_month"])],
        "under_contract": [r for r in audit["under_contract"] if visible(r["cohort_month"])],
        "unmatched": unmatched[:UNMATCHED_CAP], "unmatched_total": len(unmatched),
        "benchmarks": BENCHMARKS, "maturity_curve": MATURITY_CURVE,
    }


async def cohort_leads(s, tenant_id, business, group, *, month: str | None, now=None) -> dict:
    """The people behind a cohort row, for the per-lead drawer."""
    uni = await load_universe(s, tenant_id, business.id, group, now=now)
    rows = []
    for p in uni["people"]:
        if month and p["cohort_key"] != month:
            continue
        fc = p["first_outbound_at"]
        rows.append({
            "fub_person_id": p["person_id"], "name": p["full_name"], "cohort_key": p["cohort_key"],
            "agent": p["assigned_to"], "stage": p["stage"] or None, "source": p["source"],
            "created": p["created"].astimezone(uni["tz"]).date().isoformat(),
            "minutes_to_first_touch": round(max((fc - p["created"]).total_seconds() / 60.0, 0.0), 1) if fc else None,
            "sisu_matched": p["sisu_matched"], "sisu_state": p["sisu_state"], "fub_url": p["fub_url"],
        })
    rows.sort(key=lambda r: (r["created"], r["fub_person_id"]), reverse=True)
    return {"month": month, "count": len(rows), "rows": rows}
