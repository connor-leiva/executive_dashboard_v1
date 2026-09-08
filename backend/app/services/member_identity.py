"""Which agent is this portal member.

Every per-person figure in the portal depends on this one question, and until now nothing asked it:
`numbers` in the config payload was a block of zeroes in `_default_config`, the same shape for every
workspace, never computed from anything.

MATCHED ON EMAIL, NOT STORED AS A FOREIGN KEY. Both tables already carry the address, agent rows are
rewritten by the sync on a schedule, and a stored FK would be a second copy of a fact the sync owns
-- one that goes stale the first time somebody is re-created upstream. `agent_email` on the member
is the override for the one case email cannot resolve: the CRM has their old address.

ONE PERSON, SEVERAL AGENT ROWS. `agent` is unique on (tenant, source, external_id), so the same
human appears once for Sisu and once for Follow Up Boss. That is not a duplicate to collapse -- the
Sisu row is where their appointments live and the FUB row is where their calls do -- so this returns
a map keyed by source rather than picking a winner.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Agent, IntranetMember


def addresses(member: IntranetMember | None) -> list[str]:
    """Every address this member might be known by, lowercased, most specific first."""
    if member is None:
        return []
    out = []
    for value in (member.agent_email, member.email):
        clean = (value or "").strip().lower()
        if clean and clean not in out:
            out.append(clean)
    return out


async def agents_for(s: AsyncSession, tenant_id, member: IntranetMember | None) -> dict[str, Agent]:
    """{"sisu": Agent, "fub": Agent} for this member, as far as email can tell.

    An empty map is the honest answer for somebody who is not in the CRM at all -- an ops admin, a
    JV partner -- and callers show "not connected" rather than zero, because those are different
    facts and only one of them is worth acting on.
    """
    wanted = addresses(member)
    if not wanted:
        return {}
    rows = (await s.execute(select(Agent).where(
        Agent.tenant_id == tenant_id,
        Agent.email.is_not(None)))).scalars().all()

    out: dict[str, Agent] = {}
    for row in rows:
        email = (row.email or "").strip().lower()
        if email not in wanted:
            continue
        # `agent_email` beats `email` when both match different rows of the same source, which is
        # exactly what an override is for.
        existing = out.get(row.source)
        if existing is None or wanted.index(email) < wanted.index(
                (existing.email or "").strip().lower()):
            out[row.source] = row
    return out
