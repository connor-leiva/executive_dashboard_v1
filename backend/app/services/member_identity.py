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
    """{"sisu": Agent, "fub": Agent} for this member.

    An empty map is the honest answer for somebody who is not in the CRM at all -- an ops admin, a
    JV partner -- and callers show "not connected" rather than zero, because those are different
    facts and only one of them is worth acting on.
    """
    return {source: agent for source, (agent, _how) in
            (await matches_for(s, tenant_id, member)).items()}


async def matches_for(s: AsyncSession, tenant_id,
                      member: IntranetMember | None) -> dict[str, tuple[Agent, str]]:
    """{source: (Agent, how)} for one member. `how` is "link", "agent_email" or "email"."""
    if member is None:
        return {}
    return (await matches_for_many(s, tenant_id, [member])).get(member.id, {})


async def matches_for_many(s: AsyncSession, tenant_id,
                           members: list[IntranetMember]) -> dict:
    """{member id: {source: (Agent, how)}} for many members, in one read of the agents.

    THE ORDER, most deliberate first:
      1. `agent_links` -- an admin picked this user from the CRM's own list in the console. It
         wins because it is the only one somebody chose; everything below is inferred.
      2. `agent_email` -- the address the CRM knows them by, when it is not their portal one.
      3. their own `email`.
    Email could always pick wrongly or not at all -- the first live workspace's two roster members
    matched no Follow Up Boss user -- and until the link existed there was no screen to fix it on,
    though the portal told agents that an admin could.

    Two rows of one source with the same address (a re-created user) resolve to the active one.
    A link to a user the CRM no longer has falls back to email rather than to nothing.
    """
    members = [m for m in members if m is not None]
    if not members:
        return {}
    agents = (await s.execute(select(Agent).where(Agent.tenant_id == tenant_id))).scalars().all()
    by_ext = {(a.source, a.external_id): a for a in agents}
    by_email: dict[tuple[str, str], Agent] = {}
    for a in agents:
        email = (a.email or "").strip().lower()
        if not email:
            continue
        current = by_email.get((a.source, email))
        if current is None or (a.is_active and not current.is_active):
            by_email[(a.source, email)] = a
    sources = {a.source for a in agents}

    out: dict = {}
    for m in members:
        found: dict[str, tuple[Agent, str]] = {}
        for source, ext in (getattr(m, "agent_links", None) or {}).items():
            agent = by_ext.get((source, str(ext))) if ext not in (None, "") else None
            if agent is not None:
                found[source] = (agent, "link")
        override = (m.agent_email or "").strip().lower()
        own = (m.email or "").strip().lower()
        for source in sources - set(found):
            if override and (source, override) in by_email:
                found[source] = (by_email[(source, override)], "agent_email")
            elif own and (source, own) in by_email:
                found[source] = (by_email[(source, own)], "email")
        out[m.id] = found
    return out
