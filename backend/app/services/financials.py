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

from ..models import Transaction, PLSnapshot, Business


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


async def compute_financials(s: AsyncSession, tenant_id, business: Business, period: str) -> dict:
    start, end, is_current = _period(period)
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
