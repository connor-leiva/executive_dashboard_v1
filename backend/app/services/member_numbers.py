"""A member's own production, and their team's, from the deals the Sisu sync already holds.

What My Numbers, the home cards and Sunburst's "what it already knows" panel read.

ONE QUERY DEFINES EVERY FIGURE, for an agent and for the team alike. The team's totals are the sum
of the same grouped rows one agent's card is read from, so an agent's page and their manager's
cannot come to disagree about what a figure means. "Closed" means what the dashboard means by it --
a closed SALE, with a price -- which leaves out the $0 referrals Sisu also marks closed (51 of 573
"closings" on the first live workspace, September 2026).

THREE DIFFERENT EMPTIES, reported separately, because only one of them is a quiet week:
  * the WORKSPACE has no Sisu connection, so there is nobody to match anybody against;
  * Sisu is connected but this MEMBER matched no agent -- an address to fix on the roster;
  * matched, and the figures really are zero.
The first two used to be one flag, and an owner whose portal had no Sisu connection at all was told
to have an admin fix their email address.

WINDOWS. The week is the one Sunburst's check-in names. Closings, volume and GCI are the calendar
year, because a goal is annual. Pending is contracts written inside the dashboard's recency window
(SISU_CURRENT_WINDOW_DAYS), so twenty years of contracts that never closed are not a pipeline.
Every window ends on `today`, which the caller supplies in the workspace's own timezone.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Agent, Integration, IntranetMember, Transaction
from . import member_identity

WINDOW_DAYS = 7

# Every figure, and exactly the keys of `own`, of `team` and of each `team.by_agent` row. The
# portal reads these names, and tests/test_member_numbers.py holds its reads to this payload.
FIGURES = ("appointments_set", "appointments_held", "appointments_held_mtd", "new_contracts",
           "closed_units_ytd", "closed_volume_ytd", "gci_ytd", "pending_units", "pending_volume")
MONEY = {"closed_volume_ytd", "gci_ytd", "pending_volume"}


def _within(column, start: dt.date, end: dt.date):
    return and_(column >= start, column <= end)


def _figure_sql(today: dt.date) -> dict:
    """The SQL for each figure as of `today`, keyed as FIGURES."""
    since = today - dt.timedelta(days=WINDOW_DAYS)
    closed = and_(Transaction.status == "closed", Transaction.sale_price > 0,
                  _within(Transaction.close_date, dt.date(today.year, 1, 1), today))
    pending = and_(Transaction.status == "pending",
                   Transaction.contract_date >= today - dt.timedelta(
                       days=settings.SISU_CURRENT_WINDOW_DAYS))

    def count(condition):
        return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)

    def total(condition, column):
        return func.coalesce(func.sum(case((condition, column), else_=0)), 0)

    return {
        # Every window ENDS today as well as starting somewhere: Sisu holds a few dates after
        # today, and none of them happened this week.
        "appointments_set": count(_within(Transaction.appt_set_date, since, today)),
        "appointments_held": count(_within(Transaction.appt_met_date, since, today)),
        "appointments_held_mtd": count(_within(Transaction.appt_met_date,
                                               today.replace(day=1), today)),
        "new_contracts": count(_within(Transaction.contract_date, since, today)),
        "closed_units_ytd": count(closed),
        "closed_volume_ytd": total(closed, Transaction.sale_price),
        "gci_ytd": total(closed, Transaction.gci),
        "pending_units": count(pending),
        "pending_volume": total(pending, Transaction.sale_price),
    }


async def _rollup(s: AsyncSession, tenant_id, today: dt.date, agent_id=None) -> dict:
    """{agent_id: {figure: value}} for every agent with deals, or for one.

    One grouped pass rather than a query per figure per agent: the team view would otherwise be
    nine queries times the size of the team, on every page load.
    """
    sql = _figure_sql(today)
    q = (select(Transaction.agent_id, *(expr.label(key) for key, expr in sql.items()))
         .where(Transaction.tenant_id == tenant_id)
         .group_by(Transaction.agent_id))
    if agent_id is not None:
        q = q.where(Transaction.agent_id == agent_id)
    return {row["agent_id"]: {key: (round(float(row[key] or 0), 2) if key in MONEY
                                    else int(row[key] or 0))
                              for key in FIGURES}
            for row in (await s.execute(q)).mappings()}


def _zeroes() -> dict:
    return {key: 0 for key in FIGURES}


async def sisu_state(s: AsyncSession, tenant_id) -> dict:
    """Whether this WORKSPACE has Sisu at all, and when it last synced.

    `error` counts as connected: the credentials are there and the next tick retries, which is why
    sync.run_all includes it too. Only disconnecting Sisu turns the numbers off.
    """
    rows = (await s.execute(select(Integration.status, Integration.last_synced_at).where(
        Integration.tenant_id == tenant_id, Integration.provider == "sisu"))).all()
    live = [synced for status, synced in rows if status in ("connected", "error")]
    synced = [at for at in live if at is not None]
    return {"connected": bool(live), "synced_at": max(synced) if synced else None}


async def own_figures(s: AsyncSession, tenant_id, agent: Agent, today: dt.date) -> dict:
    """One agent's figures. Zeroes are real here: nobody reaches this without a match."""
    figures = (await _rollup(s, tenant_id, today, agent.id)).get(agent.id) or _zeroes()
    # From the Follow Up Boss call sync, which does not exist yet. None renders as an em dash;
    # 0 would be a claim about their week rather than about our integrations.
    return {**figures, "conversations_logged": None}


async def team_figures(s: AsyncSession, tenant_id, today: dt.date) -> dict:
    """The whole workspace's figures, and who they came from.

    The totals count every deal in the workspace, attributed to an agent or not. The table lists
    only agents with something inside one of the windows: rows of zeroes for people who left years
    ago are not a view of this year.
    """
    rows = await _rollup(s, tenant_id, today)
    totals = _zeroes()
    for figures in rows.values():
        for key in FIGURES:
            totals[key] += figures[key]
    for key in MONEY:
        totals[key] = round(totals[key], 2)

    active = [agent_id for agent_id, figures in rows.items()
              if agent_id is not None and any(figures.values())]
    names = {}
    if active:
        names = dict((await s.execute(select(Agent.id, Agent.name).where(
            Agent.tenant_id == tenant_id, Agent.id.in_(active)))).all())
    by_agent = sorted(
        ({"id": str(agent_id), "name": names.get(agent_id) or "Unnamed agent", **rows[agent_id]}
         for agent_id in active),
        key=lambda row: (-row["closed_units_ytd"], -row["closed_volume_ytd"], row["name"].lower()))
    return {**totals, "conversations_logged": None,
            "producing_agents": sum(1 for row in by_agent if row["closed_units_ytd"]),
            "by_agent": by_agent}


async def numbers_for(s: AsyncSession, tenant_id, member: IntranetMember | None,
                      today: dt.date, *, team: bool) -> dict:
    """The `numbers` block of the portal payload, before the goal and pace are added.

    `team` is the caller's decision -- whether this person may see everyone else's numbers --
    because permissions belong to the router, and this module only counts.
    """
    state = await sisu_state(s, tenant_id)
    synced = state["synced_at"]
    if synced is not None and synced.tzinfo is None:
        synced = synced.replace(tzinfo=dt.timezone.utc)      # the sync stamps utcnow()
    out = {
        "connected": state["connected"],
        "synced_at": synced.isoformat() if synced else None,
        "as_of": today.isoformat(),
        "window_days": WINDOW_DAYS,
        "since": (today - dt.timedelta(days=WINDOW_DAYS)).isoformat(),
        "pending_window_days": settings.SISU_CURRENT_WINDOW_DAYS,
        "on_roster": member is not None,
        "matched": False,
        "own": None,
        "team": None,
    }
    # Nothing is counted until Sisu has synced at least once. Before that there are no agent rows
    # to match, and every member would be told their address was wrong on the afternoon somebody
    # connected it.
    if not state["connected"] or synced is None:
        return out
    agent = (await member_identity.agents_for(s, tenant_id, member)).get("sisu")
    if agent is not None:
        out["matched"] = True
        out["own"] = await own_figures(s, tenant_id, agent, today)
    if team:
        out["team"] = await team_figures(s, tenant_id, today)
    return out


def pace(closed_ytd: int, annual_goal: int | None, today: dt.date | None = None) -> int | None:
    """Percent of the year's goal they are ON PACE for, not percent achieved.

    Those differ, and the difference is the whole point of the number: four units closed by 31 March
    against a goal of twelve is 33% achieved and 135% of pace, and only the second one tells
    somebody whether to change anything this week. None when no goal is set -- inventing one would
    put a confident percentage against a target nobody agreed to.
    """
    if not annual_goal or annual_goal <= 0:
        return None
    today = today or dt.date.today()
    start = dt.date(today.year, 1, 1)
    days = (dt.date(today.year, 12, 31) - start).days + 1
    elapsed = (today - start).days + 1
    expected = annual_goal * (elapsed / days)
    if expected <= 0:
        return None
    return int(round((closed_ytd / expected) * 100))
