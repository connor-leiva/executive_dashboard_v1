"""Give a newly provisioned workspace a console it can actually open.

THE PROBLEM THIS SOLVES. `provision_tenant` created a tenant, its domain, its businesses and an
invited owner -- and not one intranet row. No roles, no capabilities, no `console_access` grant,
no member for the owner. `require_console_access` needs an active member whose role holds
console_access=Full, so a workspace that had just been sold could not open its own admin console
at all. The only thing that had ever created those rows was `scripts/seed_intranet.py`, which is
one customer's real staff list, their courses and their tool stack; running it against a paying
customer would have populated their workspace with another company's people.

So this is the generic half, extracted: the structure every workspace needs and none of the
content any particular workspace has. A new customer gets roles they can rename, capabilities
they can grant, an owner who can sign in and configure, and a setup checklist that starts empty
and honest. What they do NOT get is a pre-filled roster, courses they never wrote, or tiles
pointing at tools they may not use.

WHY THE ROLES ARE GENERIC. "Buyer Agent" and "Listing Agent" are real-estate titles. This product
is sold to teams, and a workspace names its own roles in the console, so the starting set is
Owner / Manager / Member -- three rungs that mean something in any organisation and that an admin
renames in a minute. Anything more specific would be guessing at the customer's org chart.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    IntranetCapability,
    IntranetMember,
    IntranetPermission,
    IntranetRole,
    IntranetSetupTask,
    IntranetWorkspace,
    User,
)

# key, name, sort, is_leadership
ROLES = [
    ("owner", "Owner", 1, True),
    ("manager", "Manager", 2, True),
    ("member", "Member", 3, False),
]

# The levels line up with ROLES above, in order.
# `console_access` is the one that gates the admin console, and only Owner gets Full on day one:
# a workspace should decide for itself who else administers it, and the safe starting point is
# the person who bought it.
CAPABILITIES = [
    ("training_library", "Training Library", "Courses, lessons and progress",
     ["Full", "Full", "Full"]),
    ("sop_library", "SOP Library", "All published SOPs", ["Full", "Full", "Full"]),
    ("wtd", "Win the Day", "The daily run and its lists", ["Full", "Full", "Full"]),
    ("own_numbers", "Own Numbers", "Their own production dashboard", ["Full", "Full", "Full"]),
    ("team_production", "Team Production", "Everyone else's numbers", ["Full", "Full", "None"]),
    ("team_calendar", "Team Calendar", "Shared calendars and events", ["Full", "Full", "View"]),
    ("marketing_requests", "Marketing Requests", "Submit and track requests",
     ["Full", "Full", "Full"]),
    ("console_access", "Console Access", "This admin console", ["Full", "None", "None"]),
]

# key, label, destination, sort. Nothing is pre-ticked: an empty checklist is the honest state
# for a workspace where nothing has been configured, and _setup_evidence() derives the rest.
SETUP_TASKS = [
    ("brand", "Brand and logo set", "brand", 1),
    ("roster", "People invited", "roster", 2),
    ("perms", "Roles and permissions reviewed", "perms", 3),
    ("launchpad", "Tools added", "launchpad", 4),
    ("training", "Training library started", "training", 5),
    ("sops", "SOPs uploaded", "sops", 6),
    ("wtd", "Win the Day lists set", "wtd", 7),
    ("calendar", "Calendars connected", "calendar", 8),
]


async def bootstrap_intranet(s: AsyncSession, tenant_id: uuid.UUID, *, workspace_name: str,
                             subdomain: str, owner_email: str | None = None) -> None:
    """Create the structure a workspace needs before anyone can configure it.

    Idempotent: it returns early if the workspace row already exists, so re-running against a
    tenant that has been configured cannot overwrite an admin's choices. That matters more than
    it sounds -- provisioning is retried, and a bootstrap that reset roles on the second attempt
    would silently undo real work.

    Everything is created PUBLISHED. A workspace whose roles existed only as drafts would show an
    agent no roles at all until somebody found and pressed Publish, which is a confusing first
    five minutes for something they have not had a chance to change yet.
    """
    existing = (await s.execute(select(IntranetWorkspace).where(
        IntranetWorkspace.tenant_id == tenant_id))).scalars().first()
    if existing is not None:
        return

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    published = {"published_at": now, "draft_dirty": False}

    s.add(IntranetWorkspace(tenant_id=tenant_id, portal_name=workspace_name,
                            subdomain=subdomain, **published))

    role_ids: dict[str, uuid.UUID] = {}
    for key, name, sort, leadership in ROLES:
        role = IntranetRole(tenant_id=tenant_id, key=key, name=name, sort=sort,
                            is_leadership=leadership, **published)
        s.add(role)
        await s.flush()
        role_ids[key] = role.id

    for sort, (key, name, description, levels) in enumerate(CAPABILITIES, start=1):
        cap = IntranetCapability(tenant_id=tenant_id, key=key, name=name,
                                 description=description, sort=sort, **published)
        s.add(cap)
        await s.flush()
        for (role_key, _n, _s, _l), level in zip(ROLES, levels):
            s.add(IntranetPermission(tenant_id=tenant_id, capability_id=cap.id,
                                     role_id=role_ids[role_key], level=level, **published))

    for key, label, destination, sort in SETUP_TASKS:
        s.add(IntranetSetupTask(tenant_id=tenant_id, key=key, label=label,
                                destination=destination, sort=sort))

    # The owner becomes the first member, with the Owner role, so the person who just bought the
    # product can sign in and open the console. Without this the workspace exists and nobody can
    # administer it -- which was the actual state before this module.
    if owner_email:
        owner = (await s.execute(select(User).where(
            User.tenant_id == tenant_id, User.email == owner_email.strip().lower(),
        ))).scalars().first()
        s.add(IntranetMember(
            tenant_id=tenant_id,
            user_id=owner.id if owner is not None else None,
            role_id=role_ids["owner"],
            full_name=(owner.name if owner is not None and owner.name else owner_email),
            email=owner_email.strip().lower(),
            status="Active",
            auth_source="SSO",
        ))
