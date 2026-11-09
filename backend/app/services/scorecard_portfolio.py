"""The Portfolio scorecard's resolvers — the owners' Monday board across every company.

Built 2026-11-09 from the "Weekly Priorities Agenda: Connor + Spring" scorecard draft. It is a
WORKSPACE board (no business of its own), so a resolver here is handed business_id=None and finds the
company it reads by kind (or, for per-company rows, by the business key carried in the metric's
resolver_key as "name:<business key>").

Two kinds of figure:

  * AS-OF-NOW month figures (units closed + pending this month, projected GCI and profit, Forum members,
    month-to-date QuickBooks profit). They describe the pipeline or books AS THEY STAND TODAY, which
    cannot be rebuilt for a past date — so they answer only for the week containing today and return
    UNAVAILABLE for any other week. The 30-minute tick re-reads its trailing weeks; UNAVAILABLE leaves
    a closed week's value exactly as it was last written, so each week keeps its own end-of-week number.
  * Dated figures (agents on the team at week end, VIP guests registered in the week), which are
    computed for any week from the same resolvers the brokerage and Spring B boards use.

Null ≠ zero throughout: a source that isn't connected, or a month with no QuickBooks snapshot yet,
is UNAVAILABLE — never a $0 that reads as a real result.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from ..models import Business, PLSnapshot
from . import roles
from .scorecard_resolvers import (UNAVAILABLE, _ghl_live, _sisu_live, forum_vip_guests_added, resolver,
                                  roster_net)

SOURCE_LABELS = {
    "portfolio_ulrg_units": "Sisu", "portfolio_ulrg_gci": "Sisu", "portfolio_ulrg_profit": "Sisu",
    "portfolio_ulrg_agents": "Sisu", "portfolio_forum_members": "GHL", "portfolio_forum_vip": "GHL",
    "portfolio_entity_profit": "QuickBooks",
}


def is_portfolio_key(key: str | None) -> bool:
    return bool(key) and key.startswith("portfolio_")


def source_label(key: str) -> str:
    return SOURCE_LABELS.get(key.split(":", 1)[0], "Sisu")


def _today() -> dt.date:
    from .recruiting_rules import business_tz
    return dt.datetime.now(business_tz()).date()


def _is_this_week(week_start: dt.date, week_end: dt.date) -> bool:
    return week_start <= _today() <= week_end


async def _ulrg_projection(s, tenant_id, week_start, week_end):
    """The brokerage's month projection (closed + pending set to close this month) as it stands now —
    the same numbers as the Utah Life tab's Projection lens — or UNAVAILABLE."""
    if not _is_this_week(week_start, week_end):
        return UNAVAILABLE
    biz = await roles.real_estate(s, tenant_id)
    if biz is None or not await _sisu_live(s, tenant_id, biz.id):
        return UNAVAILABLE
    from .financials import compute_financials
    fin = await compute_financials(s, tenant_id, biz, "mtd")
    return fin["lenses"]["projection"]


@resolver("portfolio_ulrg_units")
async def ulrg_units(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Utah Life units closed + pending this month (Sisu)."""
    p = await _ulrg_projection(s, tenant_id, week_start, week_end)
    return p if p is UNAVAILABLE else float(p["units"] or 0)


@resolver("portfolio_ulrg_gci")
async def ulrg_gci(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Utah Life projected GCI this month (Sisu): closed GCI + pending GCI set to close this month."""
    p = await _ulrg_projection(s, tenant_id, week_start, week_end)
    return p if p is UNAVAILABLE else round(float(p["gci"] or 0), 2)


@resolver("portfolio_ulrg_profit")
async def ulrg_profit(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Utah Life projected profit this month — net GCI less the expense run-rate. BEFORE overrides, the
    5% team leader pay and the PLACE split (those deductions aren't defined yet; Connor 2026-11-09)."""
    p = await _ulrg_projection(s, tenant_id, week_start, week_end)
    return p if p is UNAVAILABLE else round(float(p["profit"] or 0), 2)


@resolver("portfolio_ulrg_agents")
async def ulrg_agents(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Utah Life agents on the Sisu team at week end — the whole team, out-of-office agents included
    (the brokerage board's Overall Net Agent Count)."""
    biz = await roles.real_estate(s, tenant_id)
    if biz is None:
        return UNAVAILABLE
    return await roster_net(s, tenant_id, biz.id, week_start, week_end, group={"sisu_group_id": None})


@resolver("portfolio_forum_members")
async def forum_members(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """The Forum's active members as they stand now — the Forum tab's Active Members (GHL)."""
    if not _is_this_week(week_start, week_end):
        return UNAVAILABLE
    biz = await roles.membership(s, tenant_id)
    if biz is None or not await _ghl_live(s, tenant_id, biz.id):
        return UNAVAILABLE
    from .metrics import _forum_kpis
    k = await _forum_kpis(s, tenant_id, biz.id, week_start, _today())
    return float(k["members"])


@resolver("portfolio_forum_vip")
async def forum_vip(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Forum VIP guests registered in the week — the Spring B board's VIP Guests Added (GHL)."""
    biz = await roles.membership(s, tenant_id)
    if biz is None:
        return UNAVAILABLE
    return await forum_vip_guests_added(s, tenant_id, biz.id, week_start, week_end)


@resolver("portfolio_entity_profit:*")
async def entity_profit(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None,
                        arg: str = ""):
    """One company's month-to-date profit (net operating income) from its QuickBooks snapshot — the
    company named by the business key in the resolver_key ("portfolio_entity_profit:<key>")."""
    if not _is_this_week(week_start, week_end):
        return UNAVAILABLE
    biz = (await s.execute(select(Business).where(Business.tenant_id == tenant_id,
                                                  Business.key == arg))).scalars().first()
    if biz is None:
        return UNAVAILABLE
    today = _today()
    month_start = today.replace(day=1)
    month_end = (month_start + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    snap = (await s.execute(select(PLSnapshot).where(
        PLSnapshot.tenant_id == tenant_id, PLSnapshot.business_id == biz.id,
        PLSnapshot.period_start == month_start, PLSnapshot.period_end == month_end))).scalars().first()
    if snap is None:                                   # QuickBooks hasn't snapshotted this month yet
        return UNAVAILABLE
    return round(float(snap.noi or 0), 2)
