import uuid

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .security import read_token
from .models import User
from .tenancy import current_tenant_id
from .services.tabs import tenant_tabs, effective_tabs

bearer = HTTPBearer()


async def current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    s: AsyncSession = Depends(get_session),
) -> User:
    try:
        payload = read_token(creds.credentials)
    except Exception:
        raise HTTPException(401, "Invalid token")
    tid = current_tenant_id()
    # A token whose tenant doesn't match the resolved host isn't a permission
    # problem — it's an invalid session for this realm (a stale token left over
    # after a reseed, or a token minted for another tenant). Return 401, like the
    # other session failures below, so the client clears it and re-authenticates
    # instead of reading it as 403 "you lack access" and showing a cryptic error.
    if payload.get("tid") != str(tid):
        raise HTTPException(401, "Session invalid for this tenant")
    user = (await s.execute(
        select(User).where(User.id == uuid.UUID(payload["sub"]), User.tenant_id == tid)
    )).scalar_one_or_none()
    if not user:
        raise HTTPException(401, "User not found")
    if user.status != "active":
        raise HTTPException(401, "Account is not active")
    # token_version gate: a bump (disable / password change) kills outstanding tokens.
    # Legacy tokens minted before the deploy carry no "ver" → read as 0 → matches
    # every migrated user, so the deploy logs nobody out.
    if int(payload.get("ver", 0)) != int(user.token_version or 0):
        raise HTTPException(401, "Session expired")
    return user


def require_role(*roles: str):
    """Capability guard — role only (managing users, integrations, settings)."""
    async def dep(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "Insufficient role")
        return user
    return dep


async def assert_tab(user: User, s: AsyncSession, tab: str) -> None:
    """Raise 403 unless the user's effective tabs include `tab`. Used inline where the
    tab is dynamic (a path/query param); owners/admins pass implicitly."""
    tabs = effective_tabs(user, await tenant_tabs(s, user.tenant_id))
    if tab not in tabs:
        raise HTTPException(403, "No access to this view")


def require_tab(tab: str):
    """Visibility guard for a fixed-tab route (forum, becollective, flywheel)."""
    async def dep(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)) -> User:
        await assert_tab(user, s, tab)
        return user
    return dep
