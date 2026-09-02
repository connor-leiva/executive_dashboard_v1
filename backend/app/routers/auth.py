import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import current_user
from ..models import User, Tenant
from ..schemas import (LoginRequest, LoginResponse, MeResponse, ChangePasswordRequest,
                       AcceptInviteRequest, ResetPasswordRequest)
from ..security import (verify_pw, make_token, hash_pw, hash_action_token, MIN_PASSWORD_LEN)
from ..services.audit import audit
from ..services import roles
from ..services.tabs import tenant_tabs, tenant_tab_descriptors, effective_tabs
from ..tenancy import current_tenant_id

router = APIRouter(tags=["auth"])

LOCK_THRESHOLD = 10
LOCK_MINUTES = 15


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _aware(d):
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


@router.get("/public/brand")
async def public_brand(s: AsyncSession = Depends(get_session)):
    """The workspace's identity, BEFORE anyone signs in. No auth, by necessity.

    The login screen has no session, so it cannot ask /me who it belongs to — which is why it
    used to render one customer's photograph and wordmark to everybody who ever reached it. This
    resolves the workspace from the host the browser is already sending and hands back only the
    chrome: name, marks, colours, type.

    A HOST THAT RESOLVES TO NOTHING GETS THE PLATFORM'S OWN IDENTITY, not a 404. That is
    deliberate on two counts. It is the honest answer — before you are signed in you are at
    Acumyn, not inside a workspace — and it means this endpoint cannot be used to ask "does a
    workspace exist at this address", which a 404 would answer for anyone who cared to iterate.

    Nothing here is private: it is the same branding painted on the page a moment later.
    """
    tid = None
    try:
        tid = current_tenant_id()
    except Exception:                       # unresolved host: fall through to the platform
        tid = None
    tenant = await s.get(Tenant, tid) if tid else None
    if tenant is not None and tenant.status != "active":
        tenant = None                       # a suspended workspace shows nothing of itself
    return roles.brand(tenant) if tenant is not None else roles.platform_brand()


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, s: AsyncSession = Depends(get_session)):
    tid = current_tenant_id()
    # Refuse before touching credentials: a suspended workspace should not be probeable for
    # which passwords are right, and the lockout counter should not tick for people who could
    # not sign in either way.
    tenant = (await s.execute(select(Tenant).where(Tenant.id == tid))).scalar_one_or_none()
    if tenant is not None and tenant.status == "suspended":
        audit(s, tid, None, "auth.login_blocked", "tenant", tid, {"reason": "suspended"})
        await s.commit()
        raise HTTPException(403, "This workspace is suspended. Contact your administrator.")
    user = (await s.execute(
        select(User).where(User.tenant_id == tid, User.email == body.email.lower())
    )).scalar_one_or_none()
    # Neutral error for missing user / invited (no password yet) / disabled — no enumeration.
    # Every branch below writes an audit row: a password spray leaves no other trace, and
    # `last_login_at` alone cannot tell a successful compromise from ordinary use. The email
    # is recorded (not the password) because for an unknown address there is no user id to
    # attribute the attempt to — which is exactly the case worth seeing.
    if not user or not user.password_hash or user.status != "active":
        audit(s, tid, None, "auth.login_failed", "user", None,
              {"email": body.email.lower()[:160], "reason": "no_such_login"})
        await s.commit()
        raise HTTPException(401, "Invalid email or password")
    if user.locked_until and _aware(user.locked_until) > _now():
        audit(s, tid, user.id, "auth.login_blocked", "user", user.id, {"reason": "locked"})
        await s.commit()
        raise HTTPException(423, "Account temporarily locked. Try again shortly.")
    if not verify_pw(body.password, user.password_hash):
        user.failed_logins = (user.failed_logins or 0) + 1
        locked = user.failed_logins >= LOCK_THRESHOLD
        if locked:
            user.locked_until = _now() + dt.timedelta(minutes=LOCK_MINUTES)
        audit(s, tid, user.id, "auth.login_failed", "user", user.id,
              {"reason": "bad_password", "failed_logins": user.failed_logins, "locked": locked})
        await s.commit()
        raise HTTPException(401, "Invalid email or password")
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = _now()
    audit(s, tid, user.id, "auth.login", "user", user.id)
    await s.commit()
    return LoginResponse(token=make_token(user.id, user.tenant_id, user.token_version or 0))


@router.post("/auth/logout")
async def logout():
    # JWTs are stateless; the client clearing its token is the logout. Hard revocation
    # is via token_version (bumped on disable / password change), checked in current_user.
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    tenant = (await s.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one()
    descriptors = await tenant_tab_descriptors(s, user.tenant_id)
    tabs = effective_tabs(user, [d["key"] for d in descriptors])
    granted = set(tabs)
    return MeResponse(id=str(user.id), email=user.email, name=user.name, role=user.role,
                      status=user.status, tenant=tenant.slug, tenant_name=tenant.name,
                      tabs=tabs, brand=roles.brand(tenant),
                      # Filtered to what this user may see, so the rail cannot render a tab
                      # the API would refuse — the nav and the grant come from one source.
                      nav=[d for d in descriptors if d["key"] in granted])


@router.post("/auth/change-password", response_model=LoginResponse)
async def change_password(body: ChangePasswordRequest, user: User = Depends(current_user),
                          s: AsyncSession = Depends(get_session)):
    if not user.password_hash or not verify_pw(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    if len(body.new_password) < MIN_PASSWORD_LEN:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LEN} characters")
    user.password_hash = hash_pw(body.new_password)
    user.token_version = (user.token_version or 0) + 1     # kill other sessions
    audit(s, user.tenant_id, user.id, "auth.password_changed", "user", user.id)
    await s.commit()
    return LoginResponse(token=make_token(user.id, user.tenant_id, user.token_version))


async def _consume_action_token(s, tid, token: str, purpose: str) -> User:
    """Resolve an invite/reset token within the current tenant, or 400."""
    th = hash_action_token(token)
    u = (await s.execute(select(User).where(
        User.tenant_id == tid, User.action_token_hash == th,
        User.action_token_purpose == purpose))).scalar_one_or_none()
    if not u or not u.action_token_expires or _aware(u.action_token_expires) < _now():
        raise HTTPException(400, "This link is invalid or expired. Ask your admin to resend it.")
    return u


@router.post("/auth/accept-invite", response_model=LoginResponse)
async def accept_invite(body: AcceptInviteRequest, s: AsyncSession = Depends(get_session)):
    tid = current_tenant_id()
    u = await _consume_action_token(s, tid, body.token, "invite")
    if u.status != "invited":
        raise HTTPException(400, "This invite has already been used.")
    if len(body.password) < MIN_PASSWORD_LEN:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LEN} characters")
    u.name = body.name.strip()[:200] or u.name
    u.password_hash = hash_pw(body.password)
    u.status = "active"
    u.action_token_hash = u.action_token_purpose = u.action_token_expires = None
    u.token_version = (u.token_version or 0)
    audit(s, tid, u.id, "user.accepted_invite", "user", u.id)
    await s.commit()
    return LoginResponse(token=make_token(u.id, u.tenant_id, u.token_version or 0))


@router.post("/auth/reset-password", response_model=LoginResponse)
async def reset_password(body: ResetPasswordRequest, s: AsyncSession = Depends(get_session)):
    tid = current_tenant_id()
    u = await _consume_action_token(s, tid, body.token, "reset")
    # A disabled account must never be resurrected by a link that predates the disable.
    # `u.status = "active"` below exists for the ordinary case (a reset completes an account
    # that was mid-invite); without this guard it also silently undid an admin's emergency
    # disable and handed the link holder a live session. accept_invite has always had the
    # equivalent guard; reset never did. The token is already consumed above, so a stale
    # link is spent either way.
    if u.status == "disabled":
        raise HTTPException(400, "This account is disabled. Ask an administrator.")
    if len(body.new_password) < MIN_PASSWORD_LEN:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LEN} characters")
    u.password_hash = hash_pw(body.new_password)
    u.status = "active"
    u.action_token_hash = u.action_token_purpose = u.action_token_expires = None
    u.token_version = (u.token_version or 0) + 1            # kill old sessions
    u.failed_logins = 0
    u.locked_until = None
    audit(s, tid, u.id, "auth.password_reset", "user", u.id)
    await s.commit()
    return LoginResponse(token=make_token(u.id, u.tenant_id, u.token_version))
