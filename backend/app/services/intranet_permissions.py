"""What a workspace's roles are actually allowed to see.

THE MATRIX WAS AUTHORED, PUBLISHED, AND READ ONCE. `IntranetPermission.level` spans every
capability a workspace defines, the console edits it, the publish cycle ships it -- and the only
place it had ever been read was `console_access == "Full"`, to decide who may open the console.
Every other capability was decorative: a workspace could set Training Library to None for JV
Partners, publish it, see it saved, and the JV Partner would still see every course.

A published permission that does nothing is worse than no permission at all, because an admin
reasonably believes they have restricted something.

CAPABILITIES ARE TENANT DATA, NOT A FIXED VOCABULARY. The generic bootstrap defines eight and the
Utah Life seed defines ten; a workspace may rename them or add its own. So the enforcement here is
keyed on the capability keys the PLATFORM has content for -- listed in CAPABILITY_CONTENT below --
and a workspace's own extra capability is authored, displayed and enforces nothing, which is the
honest behaviour rather than a silent failure.

ONLY "None" IS ENFORCED, deliberately. The levels are Full / View / Limited / None, and while
"None" has one unambiguous meaning, the difference between View, Limited and Full is not defined
anywhere in this product -- there is no read-only mode for a course list, and no specification of
what subset "Limited" is. Inventing those semantics here would be making up product behaviour and
then enforcing it. So access is denied for None and granted otherwise, and the level itself is
reported to the client so a richer distinction can be added later without changing this gate.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import IntranetCapability, IntranetPermission

# The capability keys the platform has member-facing content for. A key absent here may still be
# authored by a workspace -- it just has nothing to gate.
CAPABILITY_CONTENT = {
    "training_library": "courses",
    "sop_library": "sops",
    "wtd": "wtd_lists",
    "team_calendar": "calendar",
    "marketing_requests": "marketing",
    "own_numbers": "numbers",
    "team_production": "team_numbers",
}

DENIED = "None"


async def capability_levels(s: AsyncSession, tenant_id: uuid.UUID,
                            role_id: uuid.UUID | None) -> dict[str, str]:
    """Capability key -> level for this role, for every capability the workspace defines.

    A role of None (somebody with no roster entry) gets an empty map, which `allows` reads as
    permitted -- see there for why that is the right default.
    """
    if role_id is None:
        return {}
    rows = (await s.execute(
        select(IntranetCapability.key, IntranetPermission.level)
        .join(IntranetPermission, IntranetPermission.capability_id == IntranetCapability.id)
        .where(
            IntranetCapability.tenant_id == tenant_id,
            IntranetPermission.tenant_id == tenant_id,
            IntranetPermission.role_id == role_id,
        ))).all()
    return {key: level for key, level in rows}


def allows(levels: dict[str, str], capability_key: str) -> bool:
    """Whether this role may see the thing behind `capability_key`.

    ABSENCE MEANS PERMITTED, matching how launchpad tile audiences already behave here: a
    capability nobody has written a row for is one an admin has never considered, and the
    alternative -- denying everything until somebody ticks every box -- would black out a
    workspace the moment it defined a new capability. Denial has to be a decision somebody made.
    """
    return levels.get(capability_key) != DENIED
