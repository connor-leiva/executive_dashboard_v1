"""One agent's own week, from the data the syncs already hold.

This is what Sunburst's "what it already knows" panel reads, and what My Numbers should have been
reading all along -- that block was five zeroes in `_default_config`, identical for every workspace
and computed from nothing, so every agent who opened their own numbers saw a page of noughts.

WINDOW IS SEVEN DAYS, and it is the window the panel names. Sunburst's whole pitch is "last week,
then next week", so a figure covering anything else would be answering a different question from the
one the button next to it asks.

`connected` IS NOT `has data`. An agent with a quiet week and an agent whose email does not match
anything in Sisu both produce zeroes, and only one of those is worth telling somebody about -- the
first is their week and the second is a configuration problem they cannot see. So the source is
reported separately from the counts.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Agent, IntranetMember, Transaction
from . import member_identity

WINDOW_DAYS = 7


async def _count(s: AsyncSession, tenant_id, agent: Agent, column, since: dt.date) -> int:
    """How many of one agent's deals have `column` inside the window.

    Joined on `Transaction.agent_id`, the real foreign key, rather than on the external id the
    sync happens to have written -- the FK is what the rest of this codebase counts by, and an
    external id is a string from somebody else's system.
    """
    return int((await s.execute(
        select(func.count()).select_from(Transaction)
        .where(Transaction.tenant_id == tenant_id,
               Transaction.agent_id == agent.id,
               column.is_not(None),
               column >= since))).scalar_one() or 0)


async def week_for(s: AsyncSession, tenant_id, member: IntranetMember | None,
                   today: dt.date | None = None) -> dict:
    """The member's last seven days. Never raises; an unmatched member gets `connected: False`."""
    today = today or dt.date.today()
    since = today - dt.timedelta(days=WINDOW_DAYS)
    agents = await member_identity.agents_for(s, tenant_id, member)
    sisu = agents.get("sisu")

    out = {
        "window_days": WINDOW_DAYS,
        "since": since.isoformat(),
        "sisu_connected": sisu is not None,
        "appointments_set": 0,
        "appointments_held": 0,
        "new_contracts": 0,
        # Filled by the Follow Up Boss call sync; None until that workspace has one, which the
        # panel renders as an em dash rather than as zero conversations.
        "conversations_logged": None,
        "closed_units_ytd": 0,
    }
    if sisu is None:
        return out

    out["appointments_set"] = await _count(s, tenant_id, sisu, Transaction.appt_set_date, since)
    out["appointments_held"] = await _count(s, tenant_id, sisu, Transaction.appt_met_date, since)
    out["new_contracts"] = await _count(s, tenant_id, sisu, Transaction.contract_date, since)
    # Year to date, not seven days: it is the numerator of pace-to-goal and a goal is annual.
    # `status == closed` as well as a close date, because the dashboard counts units that way and
    # two surfaces in one product disagreeing about "closed" is worse than either answer.
    out["closed_units_ytd"] = int((await s.execute(
        select(func.count()).select_from(Transaction)
        .where(Transaction.tenant_id == tenant_id,
               Transaction.agent_id == sisu.id,
               Transaction.status == "closed",
               Transaction.close_date.is_not(None),
               Transaction.close_date >= dt.date(today.year, 1, 1),
               Transaction.close_date <= today))).scalar_one() or 0)
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
