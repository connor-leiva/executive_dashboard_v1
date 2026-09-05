"""User-management authorization: the actor↔target matrix and the tenant invariants,
in one place so every /users mutation enforces them identically (SPEC-platform §4.2).
"""
from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy import select, func

from ..models import Domain, Tenant, User

RANK = {"member": 0, "admin": 1, "owner": 2}

# How long an issued link stays usable. Here rather than in a router because THREE endpoints
# across two routers mint these, and the number is also what the email tells the recipient —
# a second copy is a copy that eventually disagrees with the link it describes.
INVITE_DAYS = 7
RESET_HOURS = 24


async def primary_host(s, tenant_id) -> str:
    # Pick the primary domain, but tolerate a tenant that has several domains flagged primary
    # (real setups often add both an app host and an api host): order primary-first and take
    # one, rather than scalar_one_or_none() which RAISES on >1 row and would 500 the whole
    # invite after the user is committed.
    d = (await s.execute(select(Domain).where(Domain.tenant_id == tenant_id)
                         .order_by(Domain.is_primary.desc()))).scalars().first()
    if d:
        return d.hostname
    t = (await s.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    return f"{t.slug}.acumyn.io"


async def link_base(request: Request, s, tenant_id) -> str:
    """Base URL for invite/reset links. Prefer the origin the caller is actually using — that
    host is, by definition, serving a working frontend — over the stored primary-domain row,
    which may be a custom domain that is not live yet (a dead link there just loads a broken
    page for the recipient)."""
    origin = (request.headers.get("origin") or "").rstrip("/")
    if origin:
        return origin
    return f"https://{await primary_host(s, tenant_id)}"


def can_manage(actor: User, target: User) -> bool:
    """owner → everyone; admin → members only; member → nobody (self-service elsewhere)."""
    if actor.role == "owner":
        return True
    if actor.role == "admin":
        return target.role == "member"
    return False


def assert_can_manage(actor: User, target: User) -> None:
    if not can_manage(actor, target):
        raise HTTPException(403, "You can't manage this user")


def assert_grantable_role(actor: User, role: str) -> None:
    """An admin can never create or elevate to a role above member."""
    if role not in ("owner", "admin", "member"):
        raise HTTPException(400, "Unknown role")
    if actor.role == "admin" and RANK[role] > RANK["member"]:
        raise HTTPException(403, "Admins can only grant the member role")
    if role == "owner" and actor.role != "owner":
        raise HTTPException(403, "Only an owner can grant the owner role")


async def _active_owner_count(s, tenant_id, exclude_id=None) -> int:
    q = select(func.count()).select_from(User).where(
        User.tenant_id == tenant_id, User.role == "owner", User.status == "active")
    if exclude_id is not None:
        q = q.where(User.id != exclude_id)
    return int((await s.execute(q)).scalar() or 0)


async def assert_not_last_owner(s, tenant_id, target: User) -> None:
    """Block the disable/demote that would leave a tenant with zero active owners."""
    if target.role == "owner" and target.status == "active":
        if await _active_owner_count(s, tenant_id, exclude_id=target.id) == 0:
            raise HTTPException(409, "A tenant must keep at least one active owner")
