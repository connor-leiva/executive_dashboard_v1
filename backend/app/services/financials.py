"""Three-lens financials for a business (SPEC-financials).

Live (Sisu, closed) ≤ Projection (Live + pending set to close) come from
transactions; Booked (QuickBooks) comes from the period pl_snapshot. Net GCI =
gross GCI − agent commissions; est. expenses = a monthly run-rate. The gap
between Live and Booked is the booking lag (reconciliation).
"""
from __future__ import annotations

import calendar
import datetime as dt

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Transaction, PLSnapshot, Business, MetricRecord


def _period(period: str) -> tuple[dt.date, dt.date, bool]:
    t = dt.date.today()
    if period == "last_month":
        first = t.replace(day=1)
        end = first - dt.timedelta(days=1)
        return end.replace(day=1), end, False
    if period == "qtd":
        return t.replace(month=((t.month - 1) // 3) * 3 + 1, day=1), t, True
    if period == "ytd":
        return t.replace(month=1, day=1), t, True
    return t.replace(day=1), t, True   # mtd


def _projection_end(period: str, start: dt.date, end: dt.date) -> dt.date:
    """The calendar end of the period, for the pending/expected-close window.
    Closed deals + the Booked snapshot are "as of today" (end); the Projection
    counts everything pending expected to close anywhere in the *whole* period —
    matching Sisu's month view (Status End Date = the last of the month), not
    just up to today (early in a month that would exclude nearly all pending)."""
    if period == "qtd":
        m = ((start.month - 1) // 3) * 3 + 3
        return dt.date(start.year, m, calendar.monthrange(start.year, m)[1])
    if period == "ytd":
        return dt.date(start.year, 12, 31)
    if period == "last_month":
        return end
    return dt.date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])  # mtd


async def expense_run_rate(s: AsyncSession, tenant_id, business: Business, end: dt.date) -> tuple[float, str]:
    mode = business.expense_run_rate_mode or "trailing_3mo"
    if mode == "manual" and business.expense_run_rate_manual is not None:
        return float(business.expense_run_rate_manual), "manual"
    n = 1 if mode == "last_month" else 3
    rows = (await s.execute(
        select(PLSnapshot.opex).where(
            PLSnapshot.tenant_id == tenant_id,
            PLSnapshot.business_id == business.id,
            PLSnapshot.period_end < end.replace(day=1),      # prior full months only
        ).order_by(PLSnapshot.period_end.desc()).limit(n)
    )).scalars().all()
    if not rows:
        return float(business.expense_run_rate_manual or 0), "manual"
    return float(sum(rows) / len(rows)), mode


def _period_months(period: str, start: dt.date, end: dt.date) -> int:
    """Calendar months the period spans through today — so the monthly expense
    run-rate scales (YTD in July = 7 months), not a flat single month."""
    if period in ("mtd", "last_month"):
        return 1
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


async def _agg(s, tenant_id, bid, status, date_col, start, end) -> tuple[float, float, int]:
    # sale_price > 0 excludes rentals / $0 referrals, matching the operational
    # "Closed Units" KPI so the financials unit count reconciles with it.
    gci, comm, units = (await s.execute(
        select(func.coalesce(func.sum(Transaction.gci), 0),
               func.coalesce(func.sum(Transaction.agent_commission), 0),
               func.count(Transaction.id))
        .where(Transaction.tenant_id == tenant_id, Transaction.business_id == bid,
               Transaction.status == status, Transaction.sale_price > 0,
               date_col >= start, date_col <= end)
    )).one()
    return float(gci), float(comm), int(units)


async def _booked_lens(s, tenant_id, business_id, period):
    """The period's QuickBooks P&L snapshot as the Booked lens rows."""
    from .metrics import _pl_period
    pl_start, pl_end = _pl_period(period)
    snap = (await s.execute(select(PLSnapshot).where(
        PLSnapshot.tenant_id == tenant_id, PLSnapshot.business_id == business_id,
        PLSnapshot.period_start == pl_start, PLSnapshot.period_end == pl_end))).scalar_one_or_none()
    if snap:
        return dict(rev=float(snap.revenue), cost=float(snap.cogs), gross=float(snap.gross_profit),
                    opex=float(snap.opex), noi=float(snap.noi), closed=bool(snap.books_closed))
    return dict(rev=0.0, cost=0.0, gross=0.0, opex=0.0, noi=0.0, closed=False)


def _booked_rows(b):
    return {"profit": b["noi"], "units": None,
            "flag": None if b["closed"] else "close_in_progress", "rows": [
                {"l": "Revenue", "v": b["rev"], "kind": "rev", "key": "revenue"},
                {"l": "Cost of sale", "v": -b["cost"], "kind": "ded", "key": "cogs"},
                {"l": "Gross profit", "v": b["gross"], "kind": "sub", "key": "gross_profit"},
                {"l": "Operating expenses", "v": -b["opex"], "kind": "ded", "key": "opex"},
                {"l": "Net operating income", "v": b["noi"], "kind": "tot", "key": "noi"}]}


def _jv_label(jv_share: float) -> str:
    return f"Spring's JV share ({int(round(jv_share * 100))}%)"


def _sympli_calc_rows(rev, lo_rate, opex_rate, jv_share, rev_label, commission_key):
    """The reverse-engineered Sympli P&L on a commission-revenue base: LO comp (the
    configurable cost of sale) → net commission (true margin) → operating costs →
    NOI → Spring's JV share. Returns (rows, noi, spring_share)."""
    lo = round(rev * lo_rate, 2)
    net_comm = round(rev - lo, 2)
    opex = round(rev * opex_rate, 2)
    noi = round(net_comm - opex, 2)
    share = round(noi * jv_share, 2)
    rows = [{"l": rev_label, "v": round(rev, 2), "kind": "rev", "key": commission_key}]
    if lo_rate:
        rows.append({"l": "Loan officer comp", "v": -lo, "kind": "ded", "key": commission_key})
    rows.append({"l": "Net commission", "v": net_comm, "kind": "sub"})
    if opex_rate:
        rows.append({"l": "Operating costs", "v": -opex, "kind": "ded", "est": True})
    rows.append({"l": "Net operating income", "v": noi, "kind": "tot"})
    rows.append({"l": _jv_label(jv_share), "v": share, "kind": "share"})
    return rows, noi, share


def _sympli_booked_rows(b, jv_share):
    """Booked lens mirroring the calculated structure so Live (Arive) and Booked (QBO)
    line up row-for-row. The QBO cost-of-sale line IS the LO comp; opex is everything
    else; NOI × jv_share is Spring's cut — the same shape as the Live lens."""
    noi = b["noi"]
    return {"profit": noi, "units": None,
            "flag": None if b["closed"] else "close_in_progress", "rows": [
                {"l": "Commission revenue", "v": b["rev"], "kind": "rev", "key": "revenue"},
                {"l": "Loan officer comp", "v": -b["cost"], "kind": "ded", "key": "cogs"},
                {"l": "Net commission", "v": b["gross"], "kind": "sub", "key": "gross_profit"},
                {"l": "Operating costs", "v": -b["opex"], "kind": "ded", "key": "opex"},
                {"l": "Net operating income", "v": noi, "kind": "tot", "key": "noi"},
                {"l": _jv_label(jv_share), "v": round(noi * jv_share, 2), "kind": "share"}]}


async def _sympli_financials(s, tenant_id, business, period, start, end, is_current) -> dict:
    """Sympli's CALCULATED financials from Arive funded loans (Utah-scoped). The
    commission Sympli books (net of loan-level cures) → the loan-officer split
    (the configurable cost of sale, reverse-engineered from the QBO books at
    ~55%) → operating costs (~29%) → NOI → Spring's 50% JV share. Reconciles to
    the QuickBooks P&L (Booked lens), which mirrors the same structure."""
    from .metrics import _arive_states, _in_states
    states = await _arive_states(s, tenant_id)
    loans = [l for l in (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business.id,
        MetricRecord.source == "arive", MetricRecord.kind == "loan"))).scalars().all()
        if _in_states(l, states)]
    funded = [l for l in loans if l.segment == "funded" and l.occurred_on and start <= l.occurred_on <= end]
    # Projection counts loans NEAR funding (in underwriting → clear-to-close), not
    # early preapprovals — those are too speculative to forecast commission on.
    _NEAR = {"UNDERWRITING_SUBMITTED", "APPROVED_WITH_CONDITION", "RE_SUBMITTAL",
             "CLEAR_TO_CLOSE", "DOCS_OUT", "DOCS_SIGNED"}
    pipeline = [l for l in loans if l.segment == "pipeline" and (l.status or "").upper() in _NEAR]

    def em(l, k):
        try:
            return float((l.meta or {}).get(k) or 0)
        except (TypeError, ValueError):
            return 0.0

    # Commission revenue = net commission (gross less loan-level cures/reimbursements)
    # — the figure that actually posts to QBO (ties to Mortgage Revenue within ~1%).
    gross = sum(em(l, "gross_revenue") for l in funded)
    net = sum(em(l, "net_revenue") for l in funded)
    if gross and not net:
        net = gross
    rev = net
    n_funded = len(funded)

    lo_rate = float(business.lo_comp_rate) if business.lo_comp_rate is not None else 0.0
    opex_rate = float(business.opex_rate) if business.opex_rate is not None else 0.0
    jv_share = float(business.jv_share) if business.jv_share is not None else 1.0

    # Projection — if the current near-funding pipeline funds at today's average commission.
    avg = rev / n_funded if n_funded else 0.0
    n_pipe = len(pipeline)
    pending = round(n_pipe * avg, 2)
    p_rev = round(rev + pending, 2)

    live_rows, live_noi, _ = _sympli_calc_rows(
        rev, lo_rate, opex_rate, jv_share, "Commission revenue", "sympli_commission")
    proj_rows, proj_noi, _ = _sympli_calc_rows(
        p_rev, lo_rate, opex_rate, jv_share, "Projected commission", "sympli_commission")

    b = await _booked_lens(s, tenant_id, business.id, period)
    booked = _sympli_booked_rows(b, jv_share)
    return {
        "period": {"label": period.upper(), "start": start.isoformat(), "end": end.isoformat(),
                   "is_current": is_current},
        "expense_run_rate": 0.0, "expense_run_rate_source": "n/a",
        "expense_months": 1, "period_expenses": 0.0,
        "lenses": {
            "live": {"profit": live_noi, "units": n_funded, "tag": "Arive · funded loans",
                     "units_label": f"{n_funded} loans funded",
                     "desc": "Net operating income Sympli earned on funded loans, after the LO split.",
                     "rows": live_rows},
            "projection": {"profit": proj_noi, "units": n_funded + n_pipe, "closed_units": n_funded,
                     "pending_units": n_pipe, "closed_gci": round(rev, 2), "pending_gci": pending,
                     "gci": p_rev, "tag": "Arive · if the pipeline funds",
                     "units_label": f"{n_funded} funded · {n_pipe} in pipeline",
                     "desc": "If the current pipeline funds at today's average commission.",
                     "rows": proj_rows},
            "booked": {**booked, "tag": "QuickBooks", "desc": "Sympli's booked P&L for the period."},
        },
        "reconciliation": {"sisu_closed": round(rev, 2), "qbo_booked": b["rev"],
                           "gap_gci": round(rev - b["rev"], 2), "gap_profit": round(live_noi - b["noi"], 2),
                           "source": "Arive", "metric": "in commissions"},
    }


async def compute_financials(s: AsyncSession, tenant_id, business: Business, period: str) -> dict:
    start, end, is_current = _period(period)
    # Sympli's Live/Projection come from Arive loan commissions, not Sisu deals.
    if business.key == "sympli":
        return await _sympli_financials(s, tenant_id, business, period, start, end, is_current)
    proj_end = _projection_end(period, start, end)
    run_rate, rr_src = await expense_run_rate(s, tenant_id, business, end)
    months = _period_months(period, start, end)
    period_expenses = run_rate * months          # monthly run-rate × months elapsed
    split = float(business.default_agent_split) if business.default_agent_split else None

    # LIVE — closed deals this period (as of today).
    g_gci, g_comm, c_units = await _agg(s, tenant_id, business.id, "closed", Transaction.close_date, start, end)
    if g_gci and not g_comm and split:          # Sisu didn't provide commission → split fallback
        g_comm = g_gci * split
    net = g_gci - g_comm
    live_profit = net - period_expenses

    # PROJECTION — pending expected to close anywhere in the period (current only).
    p_gci, p_comm, p_units = (await _agg(s, tenant_id, business.id, "pending",
                                         Transaction.expected_close_date, start, proj_end)) if is_current else (0.0, 0.0, 0)
    if p_gci and not p_comm and split:
        p_comm = p_gci * split
    proj_gci, proj_comm = g_gci + p_gci, g_comm + p_comm
    proj_net = proj_gci - proj_comm
    proj_profit = proj_net - period_expenses

    # BOOKED — the period's QuickBooks snapshot (keyed on the calendar period).
    from .metrics import _pl_period
    pl_start, pl_end = _pl_period(period)
    snap = (await s.execute(select(PLSnapshot).where(
        PLSnapshot.tenant_id == tenant_id, PLSnapshot.business_id == business.id,
        PLSnapshot.period_start == pl_start, PLSnapshot.period_end == pl_end))).scalar_one_or_none()
    if snap:
        b = dict(rev=float(snap.revenue), cost=float(snap.cogs), gross=float(snap.gross_profit),
                 opex=float(snap.opex), noi=float(snap.noi), closed=bool(snap.books_closed))
    else:
        b = dict(rev=0.0, cost=0.0, gross=0.0, opex=0.0, noi=0.0, closed=False)

    return {
        "period": {"label": period.upper(), "start": start.isoformat(), "end": end.isoformat(),
                   "is_current": is_current},
        "expense_run_rate": run_rate, "expense_run_rate_source": rr_src,
        "expense_months": months, "period_expenses": period_expenses,
        "lenses": {
            "live": {"profit": live_profit, "units": c_units, "rows": [
                {"l": "Gross GCI", "v": g_gci, "kind": "rev", "key": "fin_closed"},
                {"l": "Agent commissions", "v": -g_comm, "kind": "ded", "key": "fin_closed"},
                {"l": "Net GCI", "v": net, "kind": "sub", "key": "fin_closed"},
                {"l": ("Est. expenses" if months == 1 else f"Est. expenses · {months} mo"),
                 "v": -period_expenses, "kind": "ded", "est": True, "key": "fin_expenses"},
                {"l": "Net profit", "v": live_profit, "kind": "tot"}]},
            "projection": {"profit": proj_profit, "units": c_units + p_units,
                "closed_units": c_units, "pending_units": p_units,
                "closed_gci": g_gci, "pending_gci": p_gci, "gci": proj_gci, "rows": [
                {"l": "Projected GCI", "v": proj_gci, "kind": "rev", "key": "fin_projected"},
                {"l": "Commissions", "v": -proj_comm, "kind": "ded", "key": "fin_projected"},
                {"l": "Net GCI", "v": proj_net, "kind": "sub", "key": "fin_projected"},
                {"l": ("Est. expenses" if months == 1 else f"Est. expenses · {months} mo"),
                 "v": -period_expenses, "kind": "ded", "est": True, "key": "fin_expenses"},
                {"l": "Projected profit", "v": proj_profit, "kind": "tot"}]},
            "booked": {"profit": b["noi"], "units": None,
                "flag": None if b["closed"] else "close_in_progress", "rows": [
                {"l": "Revenue", "v": b["rev"], "kind": "rev", "key": "revenue"},
                {"l": "Cost of sale", "v": -b["cost"], "kind": "ded", "key": "cogs"},
                {"l": "Gross profit", "v": b["gross"], "kind": "sub", "key": "gross_profit"},
                {"l": "Operating expenses", "v": -b["opex"], "kind": "ded", "key": "opex"},
                {"l": "Net operating income", "v": b["noi"], "kind": "tot", "key": "noi"}]},
        },
        "reconciliation": {"sisu_closed": g_gci, "qbo_booked": b["rev"],
                           "gap_gci": g_gci - b["rev"], "gap_profit": live_profit - b["noi"]},
    }
