import uuid

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .security import read_token, read_capability
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


# ── step-up (second factor) for a sensitive section ───────────────────────────────────────
# Tab access says WHO may see a section; a step-up says they proved it again, recently. The
# grant is the existing short-lived capability token (security.make_capability), minted only
# by the section's /unlock route after a TOTP check, and sent back on the X-Step-Up header.
STEP_UP_REQUIRED = 428          # Precondition Required — the client shows the code prompt
# The sections that demand a second factor, and how long one unlock lasts. Defined HERE (not
# in the totp router) so both the guards and the router read the same list without a cycle.
STEP_UP_SCOPES = {"binder": 20}                 # scope -> grant minutes
STEP_UP_SCOPE_NAMES = tuple(STEP_UP_SCOPES)


def require_step_up(scope: str):
    """Guard a section behind a live step-up grant for `scope`. 428 (not 403) so the client
    can tell 'prove it again' apart from 'you don't have access'."""
    async def dep(user: User = Depends(current_user),
                  x_step_up: str | None = Header(default=None)) -> User:
        if not x_step_up:
            raise HTTPException(STEP_UP_REQUIRED, f"{scope} is locked — verification required")
        try:
            data = read_capability(x_step_up, f"stepup:{scope}")
        except Exception:
            raise HTTPException(STEP_UP_REQUIRED, "Verification expired — enter a new code")
        # The grant is bound to the user AND their token_version, so disabling the account or
        # rotating credentials kills an outstanding unlock too.
        if data.get("sub") != str(user.id) or int(data.get("ver", -1)) != int(user.token_version or 0):
            raise HTTPException(STEP_UP_REQUIRED, "Verification no longer valid")
        return user
    return dep


def verified_scopes(header: str | None, user: User) -> set[str]:
    """Which step-up scopes THIS request has proved — for routes that merely need to know
    (e.g. the assistant deciding whether it may load a locked section's data). Never raises:
    a missing/expired/foreign grant simply proves nothing."""
    if not header:
        return set()
    for scope in STEP_UP_SCOPE_NAMES:
        try:
            data = read_capability(header, f"stepup:{scope}")
        except Exception:
            continue
        if (data.get("sub") == str(user.id)
                and int(data.get("ver", -1)) == int(user.token_version or 0)):
            return {scope}
    return set()


def require_tab_with_step_up(tab: str, scope: str):
    """Both gates: the tab grant AND a live step-up. One symbol so every route in a section
    is covered by a single dependency (no route can be added that forgets the second factor)."""
    async def dep(user: User = Depends(require_step_up(scope)),
                  s: AsyncSession = Depends(get_session)) -> User:
        await assert_tab(user, s, tab)
        return user
    return dep
