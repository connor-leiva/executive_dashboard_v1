"""Turn raw rows into the DashboardResponse.

Handles the Phase-1 case where financials aren't connected yet: money fields
come back None and the frontend shows an "awaiting QuickBooks" state. Output
strings/labels are kept identical to the mockup so the frontend renders unchanged.

Note on portfolio totals: we sum each area's *full* revenue/NOI (matching the
mockup's combined figures). Spring's JV economics surface as the dedicated
"Spring's JV share" P&L row inside Sympli, not by discounting the portfolio.
"""
from __future__ import annotations

import calendar
import datetime as dt
import uuid
from collections import Counter

from sqlalchemy import select, func, distinct
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Business, Transaction, Agent, Lead, PLSnapshot, CashSnapshot, Integration, MetricRecord
from ..schemas import (
    DashboardResponse, Portfolio, CompositionSeg, AreaPayload, PLRow,
    OpTile, FunnelRow, Scorecard, Flywheel, FlywheelAgent, FlywheelLender, SourceStatus,
    LoanOfficer,
)

# Per-area source badges shown in the UI.
_SOURCES = {
    "ulrg": ["QuickBooks", "Sisu", "Follow Up Boss"],
    "springb": ["QuickBooks"],
    "sympli": ["QuickBooks", "Arive"],
}
# Per-area P&L row labels (match the mockup exactly).
_PL_LABELS = {
    "ulrg": ("Revenue (GCI)", "Cost of sale — agent commissions"),
    "springb": ("Revenue — membership + events", "Cost of sale — production, venue, speakers"),
    "sympli": ("Revenue — loan production", "Cost of sale — loan officer comp"),
}
_PERIOD_LABELS = {
    "mtd": "Month to date", "qtd": "Quarter to date",
    "ytd": "Year to date", "last_month": "Last month",
}
_APPOINTMENT_STAGES = ("Appointment", "Appointment Set", "Met")


# ── period / formatting ───────────────────────────────────────────
def _period_range(period: str):
    today = dt.date.today()
    if period == "ytd":
        return today.replace(month=1, day=1), today
    if period == "qtd":
        return today.replace(month=((today.month - 1) // 3) * 3 + 1, day=1), today
    if period == "last_month":
        first = today.replace(day=1)
        last_end = first - dt.timedelta(days=1)
        return last_end.replace(day=1), last_end
    return today.replace(day=1), today  # mtd


def _pl_period(period: str) -> tuple[dt.date, dt.date]:
    """(period_start, CALENDAR period-end) — the key a QBO snapshot is stored and
    read under. Unlike _period_range (whose end is 'today', for the Sisu "so far"
    queries), this end is the fixed month/quarter/year end — so a snapshot doesn't
    go missing when viewed a day after it was synced."""
    start, end = _period_range(period)
    if period == "qtd":
        m = ((start.month - 1) // 3) * 3 + 3
        return start, dt.date(start.year, m, calendar.monthrange(start.year, m)[1])
    if period == "ytd":
        return start, dt.date(start.year, 12, 31)
    if period == "last_month":
        return start, end                       # already a full calendar month
    return start, dt.date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])  # mtd


def _compact_usd(n: float | None) -> str:
    if n is None:
        return "—"
    n = float(n)
    sign, a = ("-" if n < 0 else ""), abs(n)
    if a >= 1_000_000:
        return f"{sign}${a / 1_000_000:.1f}M"
    if a >= 1_000:
        return f"{sign}${round(a / 1_000):,}K"
    return f"{sign}${round(a):,}"


def derive_status(business, margin: float | None) -> str:
    """Data-driven health. When a period margin and a watch threshold both exist,
    a margin below the threshold flags 'watch'; otherwise fall back to the stored
    status (editable on /settings/businesses)."""
    thresh = business.watch_margin_below
    if margin is not None and thresh is not None and margin < float(thresh):
        return "watch"
    return business.status or "healthy"


# ── small SQL aggregations ────────────────────────────────────────
async def _count(s, tenant_id, business_id, status, start, end, side=None, require_sale=False) -> int:
    q = select(func.count()).select_from(Transaction).where(
        Transaction.tenant_id == tenant_id,
        Transaction.business_id == business_id,
        Transaction.status == status,
    )
    if side:
        q = q.where(Transaction.side == side)
    if require_sale:  # real sales only (excludes $0 outbound referrals), matches Sisu
        q = q.where(Transaction.sale_price > 0)
    if start and end:
        q = q.where(Transaction.close_date >= start, Transaction.close_date <= end)
    return int((await s.execute(q)).scalar() or 0)


async def _sum(s, tenant_id, business_id, column, status, start, end, require_sale=False) -> float:
    q = select(func.coalesce(func.sum(column), 0)).where(
        Transaction.tenant_id == tenant_id,
        Transaction.business_id == business_id,
        Transaction.status == status,
    )
    if require_sale:
        q = q.where(Transaction.sale_price > 0)
    if start and end:
        q = q.where(Transaction.close_date >= start, Transaction.close_date <= end)
    return float((await s.execute(q)).scalar() or 0)


async def _current_pending(s, tenant_id, business_id, cutoff) -> tuple[int, float]:
    """Count + $ volume of CURRENT under-contract deals (uc_dt within the window),
    so 20 years of stale/never-closed contracts don't inflate the pipeline."""
    base = (Transaction.tenant_id == tenant_id, Transaction.business_id == business_id,
            Transaction.status == "pending", Transaction.contract_date >= cutoff)
    cnt = (await s.execute(select(func.count()).select_from(Transaction).where(*base))).scalar() or 0
    vol = (await s.execute(
        select(func.coalesce(func.sum(Transaction.sale_price), 0)).where(*base))).scalar() or 0
    return int(cnt), float(vol)


async def _forum_kpis(s, tenant_id, business_id, start, end) -> dict:
    """The Forum's Go High Level KPIs from metric_record — see reference audit:
    members (official tag union, segmented), Forum ARR + renewals due (renewals
    pipeline), new members (sales-funnel onboarded, period), event registrations,
    and MRR (active subscriptions)."""
    def _base(kind):
        return (MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
                MetricRecord.source == "ghl", MetricRecord.kind == kind)

    async def _count(*conds):
        return int((await s.execute(select(func.count()).select_from(MetricRecord).where(*conds))).scalar() or 0)

    members = await _count(*_base("member"), MetricRecord.status == "active")
    seg_rows = (await s.execute(select(MetricRecord.segment, func.count())
                .where(*_base("member"), MetricRecord.status == "active")
                .group_by(MetricRecord.segment))).all()
    segments = {(k or "member"): v for k, v in seg_rows}

    memberships = (await s.execute(select(MetricRecord).where(*_base("membership")))).scalars().all()
    arr = sum(float(m.amount or 0) for m in memberships)
    mon3 = dt.date.today().strftime("%b").lower()      # 'jul' — 3-char prefix tolerates "Febuary" typo
    renewals_due = sum(1 for m in memberships
                       if ((m.meta or {}).get("renewal_month", "")[:3].lower() == mon3))

    new_members = await _count(*_base("onboarded"),
                               MetricRecord.occurred_on >= start, MetricRecord.occurred_on <= end)
    registered = await _count(*_base("registration"))
    # True MRR = active PERPETUAL subscriptions only (installment plans excluded) —
    # one source, shared with the Cash & Billing block (billing.mrr_of).
    from .billing import mrr_of
    active_subs = (await s.execute(select(MetricRecord).where(
        *_base("subscription"), MetricRecord.status == "active"))).scalars().all()
    mrr = mrr_of(active_subs)

    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.business_id == business_id,
        Integration.provider == "ghl"))).scalar_one_or_none()
    event_name = (integ.config or {}).get("event_name") if integ else None

    return {"members": members, "segments": segments, "arr": arr, "memberships": len(memberships),
            "renewals_due": renewals_due, "new_members": new_members,
            "registered": registered, "mrr": mrr, "event_name": event_name}


# ARIVE current-status → pipeline funnel stage (loans sit at one status at a time).
_ARIVE_FUNNEL = [
    ("Pre-approval", {"PREAPPROVED", "QUALIFICATION", "PRE-APPROVED"}),
    ("Application", {"APPLICATION_INTAKE", "LOAN_SETUP", "DISCLOSURE_SENT"}),
    ("Underwriting", {"UNDERWRITING_SUBMITTED", "APPROVED_WITH_CONDITION", "RE_SUBMITTAL", "RE_SUBMISSION"}),
    ("Clear to close", {"CLEAR_TO_CLOSE", "DOCS_OUT", "DOCS_SENT", "DOCS_SIGNED"}),
]
_ARIVE_UW = {"UNDERWRITING_SUBMITTED", "APPROVED_WITH_CONDITION", "RE_SUBMITTAL",
             "CLEAR_TO_CLOSE", "DOCS_OUT", "DOCS_SIGNED"}


async def _arive_states(s, tenant_id) -> set:
    """Which property states count toward the Sympli dashboard. The Arive instance is
    Sympli's FULL multi-state LOS; Spring's view is Utah (ULRG's market). Configurable
    on the Arive integration (`states`), default {"UT"}."""
    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.provider == "arive"))).scalars().first()
    st = (integ.config or {}).get("states") if integ else None
    return {str(x).upper() for x in st} if st else {"UT"}


def _in_states(loan, states) -> bool:
    return ((loan.meta or {}).get("property_state") or "").upper() in states


async def _loan_source_map(s, tenant_id, loans) -> dict:
    """Per-loan ULRG attribution → {loan.id: 'ULRG' | 'Other'}. A loan is ULRG-sourced
    when Utah Life referred it (Arive referral @liveutah.com) OR its borrower matches a
    ULRG closing by email/phone. Powers the loan-drawer source chip + pipeline-by-source
    view — the same three-signal logic as the flywheel, from the loan's point of view."""
    bmap = {b.key: b for b in (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id))).scalars().all()}
    ulrg = bmap.get("ulrg")
    sisu_integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.provider == "sisu"))).scalars().first()
    vcfg = (sisu_integ.config or {}) if sisu_integ else {}
    ref_domains = [d.lower().lstrip("@") for d in (vcfg.get("referral_domains") or ["liveutah.com"])]
    ulrg_emails, ulrg_phones = set(), set()
    if ulrg:
        # Any ULRG buy-side deal (closed OR still under contract) — a loan in
        # underwriting maps to a ULRG deal that hasn't closed yet, so don't restrict
        # to closed here (that's the flywheel's denominator, a different question).
        buys = (await s.execute(select(Transaction).where(
            Transaction.tenant_id == tenant_id, Transaction.business_id == ulrg.id,
            Transaction.side == "buy", Transaction.buyer_email.isnot(None)))).scalars().all()
        for t in buys:
            for em in ((t.buyer_email or "").lower(), (t.buyer_email2 or "").lower()):
                if em:
                    ulrg_emails.add(em)
            if t.buyer_phone:
                ulrg_phones.add(str(t.buyer_phone))

    def is_ulrg(l) -> bool:
        m = l.meta or {}
        for e in (m.get("referral_email"), m.get("buyer_agent_email")):
            if e and any(str(e).lower().endswith(d) for d in ref_domains):
                return True
        for em in ((l.email or "").lower(), (m.get("borrower_email") or "").lower()):
            if em and em in ulrg_emails:
                return True
        return bool(m.get("borrower_phone") and str(m["borrower_phone"]) in ulrg_phones)

    return {l.id: ("ULRG" if is_ulrg(l) else "Other") for l in loans}


async def _arive_kpis(s, tenant_id, business_id, start, end, states=None) -> dict:
    """Sympli's ARIVE loan pipeline from metric_record (kind='loan', source='arive'),
    scoped to the dashboard's states (default Utah). Funded is period-scoped
    (occurred_on in range); pipeline/pre-approvals/UW are the current-state snapshot.
    Segment ('funded'|'pipeline'|'dead') is set by the sync, so funded loans are
    counted once (post-funding statuses are the same loan)."""
    if states is None:
        states = await _arive_states(s, tenant_id)
    loans = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "arive", MetricRecord.kind == "loan"))).scalars().all()
    loans = [l for l in loans if _in_states(l, states)]
    funded = [l for l in loans if l.segment == "funded"]
    pipeline = [l for l in loans if l.segment == "pipeline"]
    dead = [l for l in loans if l.segment == "dead"]
    funded_p = [l for l in funded if l.occurred_on and start <= l.occurred_on <= end]
    funded_vol = sum(float(l.amount or 0) for l in funded_p)
    decided = len(funded) + len(dead)                    # pull-through = funded / decided
    stages = [{"label": lbl, "v": sum(1 for l in pipeline if (l.status or "").upper() in codes)}
              for lbl, codes in _ARIVE_FUNNEL]
    stages.append({"label": "Funded", "v": len(funded_p)})
    return {
        "loans": len(loans),
        "funded_count": len(funded_p), "funded_volume": funded_vol,
        "avg_loan": (funded_vol / len(funded_p)) if funded_p else 0,
        "pipeline_count": len(pipeline),
        "pipeline_volume": sum(float(l.amount or 0) for l in pipeline),
        "preapprovals": sum(1 for l in pipeline if (l.status or "").upper() in {"PREAPPROVED", "QUALIFICATION"}),
        "in_underwriting": sum(1 for l in pipeline if (l.status or "").upper() in _ARIVE_UW),
        "pull_through": round(len(funded) / decided * 100) if decided else 0,
        "funnel": stages,
    }


_FW_PERIOD_LABEL = {"mtd": "this month", "qtd": "this quarter",
                    "ytd": "this year", "last_month": "last month"}


def _prior_range(period, start, end):
    """The comparable prior period, for the attach delta (None if not derivable)."""
    try:
        if period == "mtd":
            return _period_range("last_month")
        if period == "last_month":
            pe = start - dt.timedelta(days=1)
            return (pe.replace(day=1), pe)
        if period == "qtd":
            pe = start - dt.timedelta(days=1)
            return (dt.date(pe.year, ((pe.month - 1) // 3) * 3 + 1, 1), pe)
        if period == "ytd":
            def _yb(d):
                try:
                    return d.replace(year=d.year - 1)
                except ValueError:
                    return d.replace(year=d.year - 1, day=28)   # Feb 29 → Feb 28
            return (dt.date(start.year - 1, 1, 1), _yb(end))
    except Exception:  # noqa: BLE001
        return None
    return None


async def _arive_loan_officers(s, tenant_id, business_id, start, end) -> list[LoanOfficer]:
    """Per-LO performance for Sympli (Utah): funded / volume / avg loan / gross
    commission this period + all-time pull-through. Loans carry lo_email + lo_name."""
    states = await _arive_states(s, tenant_id)
    loans = [l for l in (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "arive", MetricRecord.kind == "loan"))).scalars().all()
        if _in_states(l, states)]
    by: dict = {}
    for l in loans:
        m = l.meta or {}
        email = (m.get("lo_email") or "—").lower()
        r = by.setdefault(email, {"name": None, "funded": 0, "volume": 0.0, "revenue": 0.0,
                                  "funded_all": 0, "dead": 0})
        r["name"] = r["name"] or m.get("lo_name")
        if l.segment == "funded":
            r["funded_all"] += 1
            if l.occurred_on and start <= l.occurred_on <= end:
                r["funded"] += 1
                r["volume"] += float(l.amount or 0)
                r["revenue"] += float(m.get("gross_revenue") or 0)
        elif l.segment == "dead":
            r["dead"] += 1
    out = []
    for email, r in by.items():
        if r["funded"] <= 0:
            continue                                   # only LOs active this period
        decided = r["funded_all"] + r["dead"]
        out.append(LoanOfficer(
            email=email, name=(r["name"] or email.split("@")[0].title()),
            funded=r["funded"], volume=r["volume"],
            avg_loan=round(r["volume"] / r["funded"], 2) if r["funded"] else 0,
            revenue=r["revenue"],
            pull_through=round(r["funded_all"] / decided * 100) if decided else 0))
    out.sort(key=lambda x: x.revenue, reverse=True)
    return out


async def _arive_pipeline_cells(s, tenant_id, business_id, start, end) -> dict:
    """Pivot-ready loan pipeline (Utah-scoped): stage labels + aggregated cells
    {stage, lo, lo_name, source, n}. Stages 1-4 are the current pipeline status
    buckets, 'Funded' is period-funded — matching the funnel counts. The frontend
    groups the cells by LO, by source (ULRG/Other), or both, and each cell drills."""
    states = await _arive_states(s, tenant_id)
    loans = [l for l in (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "arive", MetricRecord.kind == "loan"))).scalars().all()
        if _in_states(l, states)]
    stage_labels = [lbl for lbl, _ in _ARIVE_FUNNEL] + ["Funded"]
    status_to_stage = {code: lbl for lbl, codes in _ARIVE_FUNNEL for code in codes}

    def stage_of(l):
        if l.segment == "funded":
            return "Funded" if (l.occurred_on and start <= l.occurred_on <= end) else None
        if l.segment == "pipeline":
            return status_to_stage.get((l.status or "").upper())
        return None                                    # dead loans aren't on the funnel

    smap = await _loan_source_map(s, tenant_id, loans)
    cells: dict = {}
    lo_names: dict = {}
    for l in loans:
        stg = stage_of(l)
        if not stg:
            continue
        m = l.meta or {}
        lo = (m.get("lo_email") or "").lower() or "—"
        lo_names.setdefault(lo, m.get("lo_name") or (lo.split("@")[0].replace(".", " ").title()
                                                     if lo != "—" else "Unassigned"))
        k = (stg, lo, smap.get(l.id, "Other"))
        cells[k] = cells.get(k, 0) + 1
    cell_list = [{"stage": stg, "lo": lo, "lo_name": lo_names.get(lo), "source": src, "n": n}
                 for (stg, lo, src), n in cells.items()]
    return {"stages": stage_labels, "cells": cell_list}


async def _build_flywheel(s, tenant_id, period, start, end) -> Flywheel:
    """The ULRG → Sympli attachment flywheel, triangulated from three signals:
      A  the ULRG agent picked a Sympli mortgage vendor in Sisu (mortgage_vid)
      C  the buyer's email/phone matches a funded Sympli loan
      B  the Sympli loan names Utah Life as the referral (Arive, @liveutah.com)
    Captured (on the financeable ULRG denominator) = A ∪ C. B is an independent
    Sympli-side count for reconciliation; A/B disagreements are data-quality gaps."""
    biz = {b.key: b for b in (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id))).scalars().all()}
    ulrg, sympli = biz.get("ulrg"), biz.get("sympli")
    if not (ulrg and sympli):
        return Flywheel(available=False)

    # Vendor directory config (resolved by the Sisu sync): which mortgage vids are
    # Sympli / cash, and the vid→lender-name map for the competitor breakdown.
    sisu_integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.provider == "sisu"))).scalars().first()
    vcfg = (sisu_integ.config or {}) if sisu_integ else {}
    sympli_vids = set(vcfg.get("sympli_mortgage_vids") or [])
    cash_vids = set(vcfg.get("cash_vids") or [])
    lender_names = vcfg.get("lender_names") or {}
    ref_domains = [d.lower().lstrip("@") for d in (vcfg.get("referral_domains") or ["liveutah.com"])]

    states = await _arive_states(s, tenant_id)          # Utah-only by default
    funded = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == sympli.id,
        MetricRecord.source == "arive", MetricRecord.kind == "loan",
        MetricRecord.segment == "funded"))).scalars().all()
    funded = [f for f in funded if _in_states(f, states)]
    if not funded and not sympli_vids:
        return Flywheel(available=False)               # nothing wired yet → stub

    # Index funded loans by borrower email + phone (signal C).
    loan_by_email, loan_by_phone = {}, {}
    for f in funded:
        m = f.meta or {}
        for em in {(f.email or "").lower(), (m.get("borrower_email") or "").lower()}:
            if em:
                loan_by_email.setdefault(em, f)
        if m.get("borrower_phone"):
            loan_by_phone.setdefault(str(m["borrower_phone"]), f)

    def ref_is_ulrg(f) -> bool:
        m = f.meta or {}
        for e in (m.get("referral_email"), m.get("buyer_agent_email")):
            if e and any(str(e).lower().endswith(d) for d in ref_domains):
                return True
        return False

    def linked_loan(t):
        for em in {(t.buyer_email or "").lower(), (t.buyer_email2 or "").lower()}:
            if em and em in loan_by_email:
                return loan_by_email[em]
        if t.buyer_phone and t.buyer_phone in loan_by_phone:
            return loan_by_phone[t.buyer_phone]
        return None

    # Capture for a date range: A (Sympli vid) ∪ C (email/phone link) over the
    # financeable (cash-excluded) buy-side closings. Called for the current period
    # (full detail) and again — lightweight — for the prior period's delta.
    async def capture_for(cs, ce):
        buys = (await s.execute(select(Transaction).where(
            Transaction.tenant_id == tenant_id, Transaction.business_id == ulrg.id,
            Transaction.status == "closed", Transaction.side == "buy",
            Transaction.sale_price > 0, Transaction.close_date >= cs,
            Transaction.close_date <= ce, Transaction.buyer_email.isnot(None)))).scalars().all()
        fin = [t for t in buys if t.mortgage_vid not in cash_vids]
        cap_ids, matched, lost_c, tally, vnl = set(), set(), Counter(), {}, 0
        for t in fin:
            by_vid = t.mortgage_vid in sympli_vids
            loan = linked_loan(t)
            d = tally.setdefault(t.agent_id, {"buys": 0, "caps": 0})
            d["buys"] += 1
            if by_vid or loan:
                cap_ids.add(t.id)
                d["caps"] += 1
                if loan:
                    matched.add(loan.external_id)
                elif by_vid:
                    vnl += 1
            else:
                nm = lender_names.get(str(t.mortgage_vid)) if t.mortgage_vid else None
                lost_c[nm or "Unknown / not recorded"] += 1
        return {"fin": fin, "cap_ids": cap_ids, "matched": matched, "lost": lost_c,
                "tally": tally, "vendor_no_loan": vnl}

    cur = await capture_for(start, end)
    n_fin, n_cap = len(cur["fin"]), len(cur["cap_ids"])
    capture_pct = round(n_cap / n_fin * 100) if n_fin else 0
    lost_n = n_fin - n_cap

    # Prior comparable period → the attach delta (null when the prior period is empty).
    attach_delta = None
    pr = _prior_range(period, start, end)
    if pr:
        prior = await capture_for(pr[0], pr[1])
        if prior["fin"]:
            attach_delta = capture_pct - round(len(prior["cap_ids"]) / len(prior["fin"]) * 100)

    # Money: per-loan JV share from the Business (unset when NULL or 0 → no dollars).
    share = float(sympli.per_loan_share) if sympli.per_loan_share else None
    target = float(sympli.capture_target or 60)
    gap_dollars = round(lost_n * share, 2) if share else None
    gap_at_target = round(round(n_fin * (1 - target / 100)) * share, 2) if share else None
    per_point = round(n_fin / 100 * share, 2) if share else None

    # Referrers (all, refs desc — sums to captured) + zero-referral producing agents.
    names = {a.id: a.name for a in (await s.execute(select(Agent).where(
        Agent.tenant_id == tenant_id, Agent.business_id == ulrg.id))).scalars().all()}

    def _agent(aid, refs):
        return FlywheelAgent(id=str(aid) if aid else "", name=names.get(aid) or "House account", refs=refs)

    referrers = sorted([_agent(aid, v["caps"]) for aid, v in cur["tally"].items() if v["caps"] > 0],
                       key=lambda a: a.refs, reverse=True)
    ref_ids = {aid for aid, v in cur["tally"].items() if v["caps"] > 0}
    producing = {a for a in (await s.execute(select(Transaction.agent_id).where(
        Transaction.tenant_id == tenant_id, Transaction.business_id == ulrg.id,
        Transaction.status == "closed", Transaction.sale_price > 0,
        Transaction.close_date >= start, Transaction.close_date <= end).distinct())).scalars().all() if a}
    zero_agents = [_agent(aid, 0) for aid in producing if aid not in ref_ids]

    # Audit extras (period-scoped): Utah-Life-credited loans + reconciliation gaps.
    in_period_funded = [f for f in funded if f.occurred_on and start <= f.occurred_on <= end]
    sympli_referred = [f for f in in_period_funded if ref_is_ulrg(f)]
    referral_no_deal = sum(1 for f in sympli_referred if f.external_id not in cur["matched"])

    return Flywheel(
        available=True, period_label=_FW_PERIOD_LABEL.get(period, "this period"),
        buyer_closings=n_fin, captured=n_cap, lost=lost_n, capture_pct=capture_pct,
        capture_target=target, attach_delta_pts=attach_delta, per_loan_share=share,
        gap_dollars=gap_dollars, gap_at_target=gap_at_target, per_point_value=per_point,
        monthly_gap=gap_dollars, annual_gap=None,
        zero_ref_agents=len(zero_agents), agents=referrers[:6],
        referrers=referrers, zero_agents=zero_agents,
        lost_to=[FlywheelLender(name=k, count=v) for k, v in cur["lost"].most_common(8)],
        sympli_referred=len(sympli_referred),
        sympli_referred_linked=len(sympli_referred) - referral_no_deal,
        vendor_no_loan=cur["vendor_no_loan"], referral_no_deal=referral_no_deal)


async def _active_listings(s, tenant_id, business_id, cutoff) -> int:
    """Current active listings: sell-side, active, listed within the window."""
    return int((await s.execute(select(func.count()).select_from(Transaction).where(
        Transaction.tenant_id == tenant_id, Transaction.business_id == business_id,
        Transaction.status == "active", Transaction.side == "sell",
        Transaction.listing_date >= cutoff))).scalar() or 0)


async def _producing_agents(s, tenant_id, business_id, start, end) -> tuple[int, int]:
    producing = (await s.execute(
        select(func.count(distinct(Transaction.agent_id))).where(
            Transaction.tenant_id == tenant_id,
            Transaction.business_id == business_id,
            Transaction.status == "closed",
            Transaction.agent_id.is_not(None),
            Transaction.close_date >= start, Transaction.close_date <= end,
        )
    )).scalar() or 0
    total = (await s.execute(
        select(func.count()).select_from(Agent).where(
            Agent.tenant_id == tenant_id,
            Agent.business_id == business_id,
            Agent.is_active.is_(True),
        )
    )).scalar() or 0
    return int(producing), int(total)


async def _funnel(s, tenant_id, business_id, start, end) -> list[FunnelRow]:
    # Top of funnel: prefer FUB leads when synced (Phase 1b); otherwise fall back
    # to Sisu deal dates (lead_date / appt_set_date).
    lead_ct = (await s.execute(
        select(func.count()).select_from(Lead).where(
            Lead.tenant_id == tenant_id, Lead.business_id == business_id)
    )).scalar() or 0
    if lead_ct:
        leads = lead_ct
        appts = (await s.execute(
            select(func.count()).select_from(Lead).where(
                Lead.tenant_id == tenant_id, Lead.business_id == business_id,
                Lead.stage.in_(_APPOINTMENT_STAGES))
        )).scalar() or 0
    else:
        leads = (await s.execute(
            select(func.count()).select_from(Transaction).where(
                Transaction.tenant_id == tenant_id, Transaction.business_id == business_id,
                Transaction.lead_date >= start, Transaction.lead_date <= end)
        )).scalar() or 0
        appts = (await s.execute(
            select(func.count()).select_from(Transaction).where(
                Transaction.tenant_id == tenant_id, Transaction.business_id == business_id,
                Transaction.appt_set_date >= start, Transaction.appt_set_date <= end)
        )).scalar() or 0
    under_contract = (await s.execute(
        select(func.count()).select_from(Transaction).where(
            Transaction.tenant_id == tenant_id, Transaction.business_id == business_id,
            Transaction.status.in_(("pending", "closed")),
            Transaction.contract_date >= start, Transaction.contract_date <= end)
    )).scalar() or 0
    closed = await _count(s, tenant_id, business_id, "closed", start, end, require_sale=True)
    return [
        FunnelRow(label="Leads", v=int(leads)),
        FunnelRow(label="Appointments", v=int(appts)),
        FunnelRow(label="Under contract", v=int(under_contract)),
        FunnelRow(label="Closed", v=int(closed)),
    ]


async def _mom(s, tenant_id, current_rev: float) -> float | None:
    """Portfolio revenue % change vs the prior month, from PLSnapshots (portfolio
    entities only, so it matches the gated current_rev)."""
    prior_start, prior_end = _period_range("last_month")
    prior = (await s.execute(
        select(func.coalesce(func.sum(PLSnapshot.revenue), 0))
        .join(Business, Business.id == PLSnapshot.business_id).where(
            PLSnapshot.tenant_id == tenant_id,
            PLSnapshot.period_start == prior_start,
            PLSnapshot.period_end == prior_end,
            Business.include_in_portfolio.is_(True))
    )).scalar() or 0
    prior = float(prior)
    if not prior:
        return None
    return round((current_rev - prior) / prior * 100, 1)


async def _cash(s, tenant_id, end) -> float | None:
    row = (await s.execute(
        select(CashSnapshot).where(
            CashSnapshot.tenant_id == tenant_id,
            CashSnapshot.business_id.is_(None),
            CashSnapshot.as_of <= end,
        ).order_by(CashSnapshot.as_of.desc())
    )).scalars().first()
    return float(row.amount) if row else None


async def _source_statuses(s, tenant_id) -> list[SourceStatus]:
    integs = (await s.execute(
        select(Integration).where(Integration.tenant_id == tenant_id)
    )).scalars().all()
    display = {"qbo": "QuickBooks", "sisu": "Sisu", "fub": "Follow Up Boss", "arive": "Arive"}
    # Collapse multiple rows per provider (QBO is per-realm) into one status.
    agg: dict[str, dict] = {}
    for i in integs:
        cur = agg.setdefault(i.provider, {"status": "disconnected", "last": None})
        if i.status == "connected":
            cur["status"] = "connected"
        elif i.status == "error" and cur["status"] != "connected":
            cur["status"] = "error"
        if i.last_synced_at and (cur["last"] is None or i.last_synced_at > cur["last"]):
            cur["last"] = i.last_synced_at
    out = []
    for prov in ("qbo", "sisu", "fub", "arive"):
        a = agg.get(prov, {"status": "disconnected", "last": None})
        out.append(SourceStatus(
            name=display[prov], status=a["status"],
            last_synced=a["last"].isoformat() if a["last"] else None,
        ))
    return out


# ── mappers ───────────────────────────────────────────────────────
def _trend(b: Business) -> list[float]:
    cfg = b.config or {}
    return [float(x) for x in cfg.get("trend", [])]


def _pl_rows(pl: PLSnapshot, b: Business) -> list[PLRow]:
    rev = float(pl.revenue)
    cogs = float(pl.cogs)
    gross = float(pl.gross_profit)
    opex = float(pl.opex)
    noi = float(pl.noi)
    rev_label, cogs_label = _PL_LABELS.get(b.key, ("Revenue", "Cost of sale"))
    gp_pct = round(gross / rev * 100) if rev else 0
    margin = round(noi / rev * 100) if rev else 0
    rows = [
        PLRow(label=rev_label, value=rev, kind="rev"),
        PLRow(label=cogs_label, value=-cogs, kind="ded"),
        PLRow(label="Gross profit", value=gross, kind="sub", note=f"{gp_pct}%"),
        PLRow(label="Operating expenses", value=-opex, kind="ded"),
        PLRow(label="Net operating income", value=noi, kind="tot", note=f"{margin}% margin"),
    ]
    if b.is_jv:
        share_pct = int(round(float(b.jv_share) * 100))
        rows.append(PLRow(
            label=f"Spring's JV share ({share_pct}%)",
            value=noi * float(b.jv_share), kind="share",
        ))
    return rows


def _ops_ulrg(closed, volume, gci, pending, pipeline, active_listings, producing,
              total, period_label="") -> list[OpTile]:
    avg_price = volume / closed if closed else 0
    scope = period_label.lower() if period_label else "this period"
    return [
        OpTile(label="Units Closed", value=str(closed), sub=scope, key="units_closed"),
        OpTile(label="Volume", value=_compact_usd(volume), key="volume"),
        OpTile(label="GCI", value=_compact_usd(gci), sub=scope, key="gci"),
        OpTile(label="Avg Sale Price", value=_compact_usd(avg_price), key="avg_price"),
        OpTile(label="Pending Pipeline", value=str(pending), sub=_compact_usd(pipeline), key="pending"),
        OpTile(label="Active Listings", value=str(active_listings), key="active_listings"),
        OpTile(label="Agents Producing", value=str(producing), sub=f"of {total}", key="agents_producing"),
    ]


def _ops_from_config(b: Business) -> list[OpTile]:
    cfg = b.config or {}
    out = []
    for o in cfg.get("ops", []):
        o = dict(o)
        o["label"] = (o.get("label") or "").title()   # Title-case KPI labels
        out.append(OpTile(**o))
    return out


def _scorecards(
    *, portfolio_noi, portfolio_margin, ulrg_gci, ulrg_closed, ulrg_pending, ulrg_pipeline,
    producing, total_agents, sympli_funded, sympli_volume, attach_rate, members, have_financials,
    members_sub="The Forum",
) -> list[Scorecard]:
    return [
        Scorecard(
            label="Combined Profit",
            value=_compact_usd(portfolio_noi) if have_financials else "—",
            sub=f"{portfolio_margin}% margin" if have_financials else "awaiting QuickBooks",
            business_key="portfolio", key="combined_profit"),
        Scorecard(label="Total GCI", value=_compact_usd(ulrg_gci), sub="this period", business_key="ulrg", key="gci"),
        Scorecard(label="Closed Units", value=str(ulrg_closed), sub="this period", business_key="ulrg", key="units_closed"),
        Scorecard(label="Under Contract", value=str(ulrg_pending),
                  sub=f"{_compact_usd(ulrg_pipeline)} pipeline", business_key="ulrg", key="pending"),
        Scorecard(label="Agents Producing", value=str(producing), sub=f"of {total_agents}", business_key="ulrg", key="agents_producing"),
        Scorecard(label="Loans Funded", value=sympli_funded or "—",
                  sub=f"{sympli_volume} volume" if sympli_volume else None, business_key="sympli", key="funded_loans"),
        Scorecard(label="Attach Rate", value=attach_rate or "—", sub="ULRG → Sympli", business_key="sympli"),
        Scorecard(label="Active Members", value=members or "—", sub=members_sub, business_key="forum", key="active_members"),
    ]


# ── main build ────────────────────────────────────────────────────
async def build_dashboard(s: AsyncSession, tenant_id: uuid.UUID, period: str) -> DashboardResponse:
    start, end = _period_range(period)
    businesses = (await s.execute(
        select(Business).where(Business.tenant_id == tenant_id).order_by(Business.sort_order)
    )).scalars().all()

    areas: dict[str, AreaPayload] = {}
    portfolio_rev = 0.0
    portfolio_noi = 0.0
    have_financials = False

    # raw values we need for scorecards
    sc: dict[str, object] = {
        "ulrg_gci": 0.0, "ulrg_closed": 0, "ulrg_pending": 0, "ulrg_pipeline": 0.0,
        "producing": 0, "total_agents": 0, "sympli_funded": None, "sympli_volume": None,
        "members": None, "members_sub": "The Forum",
    }

    for b in businesses:
        cfg = b.config or {}
        los = []                                       # per-LO performance (Sympli only)
        pipeline_cells = None                          # loan-pipeline pivot (Sympli only)

        # Operational (Phase 1) — only ULRG has live transaction data.
        if b.key == "ulrg":
            cutoff = dt.date.today() - dt.timedelta(days=settings.SISU_CURRENT_WINDOW_DAYS)
            closed = await _count(s, tenant_id, b.id, "closed", start, end, require_sale=True)
            volume = await _sum(s, tenant_id, b.id, Transaction.sale_price, "closed", start, end, require_sale=True)
            gci = await _sum(s, tenant_id, b.id, Transaction.gci, "closed", start, end, require_sale=True)
            # Current-state tiles are recency-scoped (not date-in-period).
            pending, pipeline = await _current_pending(s, tenant_id, b.id, cutoff)
            active_listings = await _active_listings(s, tenant_id, b.id, cutoff)
            producing, total = await _producing_agents(s, tenant_id, b.id, start, end)
            ops = _ops_ulrg(closed, volume, gci, pending, pipeline, active_listings,
                            producing, total, _PERIOD_LABELS.get(period, period.upper()))
            funnel = await _funnel(s, tenant_id, b.id, start, end)
            sc.update(ulrg_gci=gci, ulrg_closed=closed, ulrg_pending=pending,
                      ulrg_pipeline=pipeline, producing=producing, total_agents=total)
        else:
            ops = _ops_from_config(b)
            funnel = [FunnelRow(**f) for f in cfg.get("funnel", [])] if cfg.get("funnel") else None
            scc = cfg.get("scorecard", {})
            if b.key == "sympli":
                try:
                    ak = await _arive_kpis(s, tenant_id, b.id, start, end)
                except Exception:          # e.g. metric_record migration not yet applied
                    await s.rollback()
                    ak = None
                if ak and ak["loans"] > 0:                 # Arive is synced → live pipeline
                    plabel = _PERIOD_LABELS.get(period, period)
                    ops = [
                        OpTile(label="Funded Loans", value=str(ak["funded_count"]), sub=plabel, key="funded_loans"),
                        OpTile(label="Loan Volume", value=_compact_usd(ak["funded_volume"]), key="loan_volume"),
                        OpTile(label="Avg Loan Amount", value=_compact_usd(ak["avg_loan"]), key="avg_loan"),
                        OpTile(label="Pre-approvals", value=str(ak["preapprovals"]), sub="active", key="preapprovals"),
                        OpTile(label="In Underwriting", value=str(ak["in_underwriting"]), sub="active", key="in_underwriting"),
                        OpTile(label="Pull-through Rate", value=f"{ak['pull_through']}%", key="pull_through"),
                    ]
                    funnel = [FunnelRow(**f) for f in ak["funnel"]]
                    los = await _arive_loan_officers(s, tenant_id, b.id, start, end)
                    pipeline_cells = await _arive_pipeline_cells(s, tenant_id, b.id, start, end)
                    sc["sympli_funded"] = str(ak["funded_count"])
                    sc["sympli_volume"] = _compact_usd(ak["funded_volume"]) if ak["funded_volume"] else None
                else:                                      # not synced → seeded placeholders
                    sc["sympli_funded"] = scc.get("funded")
                    sc["sympli_volume"] = scc.get("volume")
            if b.key == "springb":
                try:
                    k = await _forum_kpis(s, tenant_id, b.id, start, end)
                except Exception:          # e.g. metric_record migration not yet applied
                    await s.rollback()
                    k = None
                if k and (k["members"] > 0 or k["memberships"] > 0):   # GHL is synced
                    seg = k["segments"]
                    ev_name = k["event_name"]
                    ops = [
                        OpTile(label="Active Members", value=str(k["members"]),
                               sub=f"Forum {seg.get('forum', 0)} · Inner Circle {seg.get('inner_circle', 0)}",
                               key="active_members"),
                        OpTile(label="Forum ARR", value=_compact_usd(k["arr"]),
                               sub=f"{k['memberships']} memberships", key="forum_arr"),
                        OpTile(label="New Members", value=str(k["new_members"]),
                               sub=_PERIOD_LABELS.get(period, period), key="new_members"),
                        OpTile(label="Renewals Due", value=str(k["renewals_due"]),
                               sub=dt.date.today().strftime("%B"), key="renewals_due"),
                        OpTile(label="Registered", value=str(k["registered"]),
                               sub=ev_name, key="registered"),
                        OpTile(label="MRR", value=_compact_usd(k["mrr"]) if k["mrr"] else "—",
                               sub="monthly subscriptions", key="mrr"),
                    ]
                    sc["members"] = str(k["members"])
                else:                       # not synced yet — keep seeded placeholders
                    for t in ops:
                        if t.label == "Active Members":
                            t.key = "active_members"
                    sc["members"] = scc.get("members")

        # Financial (Phase 2) — from the PLSnapshot, keyed on the calendar period.
        pl_start, pl_end = _pl_period(period)
        pl_row = (await s.execute(select(PLSnapshot).where(
            PLSnapshot.tenant_id == tenant_id, PLSnapshot.business_id == b.id,
            PLSnapshot.period_start == pl_start, PLSnapshot.period_end == pl_end))).scalar_one_or_none()

        if pl_row:
            have_financials = True
            rev, noi = float(pl_row.revenue), float(pl_row.noi)
            margin = round(noi / rev * 100) if rev else 0
            pl = _pl_rows(pl_row, b)
            if b.include_in_portfolio:      # operational-only holders don't roll up (avoids double-count)
                portfolio_rev += rev
                portfolio_noi += noi
        else:
            rev = noi = margin = None
            pl = []

        tag = "Real estate" if b.tag == "Brokerage" else b.tag   # ULRG is a team, not a brokerage
        areas[b.key] = AreaPayload(
            id=str(b.id), key=b.key, name=b.name, tag=tag, status=derive_status(b, margin),
            accent=b.accent, ink=b.ink, sources=_SOURCES.get(b.key, ["QuickBooks"]),
            revenue=rev, noi=noi, margin=margin, trend=_trend(b), pl=pl, ops=ops, funnel=funnel,
            loan_officers=los, loan_pipeline=pipeline_cells,
        )

    # Spring B is one QBO entity but two views: split its area into The Forum
    # (the operational + shared-P&L view) and beCollective (its own GHL segment,
    # pending its focused view). The composition bar stays one "Spring B" segment.
    if "springb" in areas:
        sb = areas.pop("springb")
        sbiz = next((b for b in businesses if b.key == "springb"), None)
        members = arr = bc_members = 0
        if sbiz:
            fk = await _forum_kpis(s, tenant_id, sbiz.id, start, end)
            members, arr = fk["members"], fk["arr"]
            bc_members = int((await s.execute(select(func.count()).select_from(MetricRecord).where(
                MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == sbiz.id,
                MetricRecord.source == "ghl", MetricRecord.kind == "bc_member",
                MetricRecord.status == "active"))).scalar() or 0)
            # "Active members" scorecard = distinct union across programs (no double count).
            union_ids = (await s.execute(select(MetricRecord.external_id).where(
                MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == sbiz.id,
                MetricRecord.source == "ghl", MetricRecord.kind.in_(["member", "bc_member"]),
                MetricRecord.status == "active"))).scalars().all()
            if union_ids:
                sc["members"] = str(len(set(union_ids)))
                if bc_members:
                    sc["members_sub"] = "Forum + beCollective"
        forum_tag = f"Mastermind · {members} members" + (f" · {_compact_usd(arr)} ARR" if arr else "")
        areas["forum"] = sb.model_copy(update={
            "key": "forum", "name": "The Forum", "tag": forum_tag,
            "accent": "#FFDD1F", "ink": "#6D5336"})   # daffodil (Forum identity)
        areas["becollective"] = AreaPayload(
            id=sb.id, key="becollective", name="beCollective",
            tag=(f"Community · {bc_members} members" if bc_members else "Community · GHL segment"),
            status="opportunity", accent="#FFBA9F", ink="#6D5336",
            sources=["Go High Level"], revenue=None, noi=None, margin=None,
            trend=sb.trend, pl=[], ops=[], funnel=None)

        # Springb's own P&L defaults to the forum view (above). If it's been re-routed
        # to another page, move its financial there and leave forum's financial empty
        # (the operational forum view stays). Default (forum) path is untouched.
        fin_tab = (sbiz.display_tab if sbiz else None) or "forum"
        if fin_tab != "forum" and fin_tab in areas and sb.revenue is not None:
            areas[fin_tab] = areas[fin_tab].model_copy(update={
                "revenue": sb.revenue, "noi": sb.noi, "margin": sb.margin, "pl": sb.pl})
            areas["forum"] = areas["forum"].model_copy(update={
                "revenue": None, "noi": None, "margin": None, "pl": []})

    # Route a financial entity (a QBO account connected to another page) onto its
    # display_tab area: merge its P&L into that page, or open the page if brand-new.
    # The operational holder (springb) is already split above — its area is popped, so
    # this loop skips it (src is None); own-key businesses stay where they are.
    for b in businesses:
        tab = b.display_tab or b.key
        if tab == b.key:
            continue
        src = areas.pop(b.key, None)
        if src is None:
            continue
        dst = areas.get(tab)
        if dst is None:                                 # routed to a brand-new page
            areas[tab] = src.model_copy(update={"key": tab})
        elif src.revenue is not None:                   # merge financials into the existing page
            rev = (dst.revenue or 0.0) + src.revenue
            noi = (dst.noi or 0.0) + (src.noi or 0.0)
            areas[tab] = dst.model_copy(update={
                "revenue": rev or None, "noi": noi or None,
                "margin": round(noi / rev * 100) if rev else None,
                "pl": (dst.pl or []) + (src.pl or [])})

    # Composition — one segment per portfolio page (largest first). Dedupe by page so
    # several entities routed to one page count once; the first (by sort_order) names it.
    composition: list[CompositionSeg] = []
    if have_financials and portfolio_rev:
        segs, seen = [], set()
        for b in businesses:
            tab = b.display_tab or b.key
            if not b.include_in_portfolio or tab in seen:
                continue
            a = areas.get(tab)
            if a and a.revenue:
                seen.add(tab)
                segs.append((b, a))
        segs.sort(key=lambda ba: ba[1].revenue, reverse=True)
        composition = [CompositionSeg(
            key=a.key, name=b.name, revenue=a.revenue,
            pct=round(a.revenue / portfolio_rev * 100, 1), accent=a.accent)
            for b, a in segs]

    portfolio_margin = round(portfolio_noi / portfolio_rev * 100) if portfolio_rev else 0
    mom = await _mom(s, tenant_id, portfolio_rev) if (period == "mtd" and portfolio_rev) else None
    cash = await _cash(s, tenant_id, end)
    sources = await _source_statuses(s, tenant_id)
    try:
        flywheel = await _build_flywheel(s, tenant_id, period, start, end)
    except Exception:              # e.g. metric_record migration not yet applied
        await s.rollback()
        flywheel = Flywheel(available=False)
    attach = f"{flywheel.capture_pct}%" if flywheel.available and flywheel.capture_pct is not None else None
    scorecards = _scorecards(
        portfolio_noi=portfolio_noi, portfolio_margin=portfolio_margin,
        ulrg_gci=sc["ulrg_gci"], ulrg_closed=sc["ulrg_closed"], ulrg_pending=sc["ulrg_pending"],
        ulrg_pipeline=sc["ulrg_pipeline"], producing=sc["producing"], total_agents=sc["total_agents"],
        sympli_funded=sc["sympli_funded"], sympli_volume=sc["sympli_volume"],
        attach_rate=attach, members=sc["members"], have_financials=have_financials,
        members_sub=sc["members_sub"],
    )

    return DashboardResponse(
        period={"label": _PERIOD_LABELS.get(period, period.upper()), "as_of": end.isoformat(),
                "start": start.isoformat(), "end": end.isoformat()},
        portfolio=Portfolio(
            revenue=portfolio_rev or None, noi=portfolio_noi or None,
            margin=portfolio_margin if portfolio_rev else None, mom=mom,
            cash=cash, composition=composition),
        scorecards=scorecards, areas=areas,
        flywheel=flywheel,
        sources=sources,
    )
