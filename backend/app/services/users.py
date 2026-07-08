"""User-management authorization: the actor↔target matrix and the tenant invariants,
in one place so every /users mutation enforces them identically (SPEC-platform §4.2).
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select, func

from ..models import User

RANK = {"member": 0, "admin": 1, "owner": 2}


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
