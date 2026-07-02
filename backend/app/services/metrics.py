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

from sqlalchemy import select, func, distinct
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Business, Transaction, Agent, Lead, PLSnapshot, CashSnapshot, Integration, MetricRecord
from ..schemas import (
    DashboardResponse, Portfolio, CompositionSeg, AreaPayload, PLRow,
    OpTile, FunnelRow, Scorecard, Flywheel, SourceStatus,
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
    if abs(n) >= 1_000_000:
        return f"${n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"${round(n / 1_000):,}K"
    return f"${round(n):,}"


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
    mrr = float((await s.execute(select(func.coalesce(func.sum(MetricRecord.amount), 0))
                 .where(*_base("subscription"), MetricRecord.status == "active"))).scalar() or 0)

    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.business_id == business_id,
        Integration.provider == "ghl"))).scalar_one_or_none()
    event_name = (integ.config or {}).get("event_name") if integ else None

    return {"members": members, "segments": segments, "arr": arr, "memberships": len(memberships),
            "renewals_due": renewals_due, "new_members": new_members,
            "registered": registered, "mrr": mrr, "event_name": event_name}


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
    """Portfolio revenue % change vs the prior month, from PLSnapshots."""
    prior_start, prior_end = _period_range("last_month")
    prior = (await s.execute(
        select(func.coalesce(func.sum(PLSnapshot.revenue), 0)).where(
            PLSnapshot.tenant_id == tenant_id,
            PLSnapshot.period_start == prior_start,
            PLSnapshot.period_end == prior_end)
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
        Scorecard(label="Active Members", value=members or "—", sub="The Forum", business_key="forum", key="active_members"),
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
        "members": None,
    }

    for b in businesses:
        cfg = b.config or {}

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
        )

    # Spring B is one QBO entity but two views: split its area into The Forum
    # (the operational + shared-P&L view) and beCollective (its own GHL segment,
    # pending its focused view). The composition bar stays one "Spring B" segment.
    if "springb" in areas:
        sb = areas.pop("springb")
        sbiz = next((b for b in businesses if b.key == "springb"), None)
        members = arr = 0
        if sbiz:
            fk = await _forum_kpis(s, tenant_id, sbiz.id, start, end)
            members, arr = fk["members"], fk["arr"]
        forum_tag = f"Mastermind · {members} members" + (f" · {_compact_usd(arr)} ARR" if arr else "")
        areas["forum"] = sb.model_copy(update={
            "key": "forum", "name": "The Forum", "tag": forum_tag,
            "accent": "#FFDD1F", "ink": "#6D5336"})   # daffodil (Forum identity)
        areas["becollective"] = AreaPayload(
            id=sb.id, key="becollective", name="beCollective", tag="Community · GHL segment",
            status="opportunity", accent="#FFBA9F", ink="#6D5336",
            sources=["Go High Level"], revenue=None, noi=None, margin=None,
            trend=sb.trend, pl=[], ops=[], funnel=None)

    # Composition (only when financials are present).
    composition: list[CompositionSeg] = []
    if have_financials and portfolio_rev:
        order = [k for k in ("ulrg", "sympli", "forum") if k in areas]
        for k in order:
            a = areas[k]
            if a.revenue:
                composition.append(CompositionSeg(
                    key=k, name="Spring B" if k == "forum" else a.name, revenue=a.revenue,
                    pct=round(a.revenue / portfolio_rev * 100, 1), accent=a.accent))

    portfolio_margin = round(portfolio_noi / portfolio_rev * 100) if portfolio_rev else 0
    mom = await _mom(s, tenant_id, portfolio_rev) if (period == "mtd" and portfolio_rev) else None
    cash = await _cash(s, tenant_id, end)
    sources = await _source_statuses(s, tenant_id)
    scorecards = _scorecards(
        portfolio_noi=portfolio_noi, portfolio_margin=portfolio_margin,
        ulrg_gci=sc["ulrg_gci"], ulrg_closed=sc["ulrg_closed"], ulrg_pending=sc["ulrg_pending"],
        ulrg_pipeline=sc["ulrg_pipeline"], producing=sc["producing"], total_agents=sc["total_agents"],
        sympli_funded=sc["sympli_funded"], sympli_volume=sc["sympli_volume"],
        attach_rate=None, members=sc["members"], have_financials=have_financials,
    )

    return DashboardResponse(
        period={"label": _PERIOD_LABELS.get(period, period.upper()), "as_of": end.isoformat(),
                "start": start.isoformat(), "end": end.isoformat()},
        portfolio=Portfolio(
            revenue=portfolio_rev or None, noi=portfolio_noi or None,
            margin=portfolio_margin if portfolio_rev else None, mom=mom,
            cash=cash, composition=composition),
        scorecards=scorecards, areas=areas,
        flywheel=Flywheel(available=False),   # Phase 3
        sources=sources,
    )
