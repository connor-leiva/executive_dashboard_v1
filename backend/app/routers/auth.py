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
from ..services.tabs import tenant_tabs, effective_tabs
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


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, s: AsyncSession = Depends(get_session)):
    tid = current_tenant_id()
    user = (await s.execute(
        select(User).where(User.tenant_id == tid, User.email == body.email.lower())
    )).scalar_one_or_none()
    # Neutral error for missing user / invited (no password yet) / disabled — no enumeration.
    if not user or not user.password_hash or user.status != "active":
        raise HTTPException(401, "Invalid email or password")
    if user.locked_until and _aware(user.locked_until) > _now():
        raise HTTPException(423, "Account temporarily locked. Try again shortly.")
    if not verify_pw(body.password, user.password_hash):
        user.failed_logins = (user.failed_logins or 0) + 1
        if user.failed_logins >= LOCK_THRESHOLD:
            user.locked_until = _now() + dt.timedelta(minutes=LOCK_MINUTES)
        await s.commit()
        raise HTTPException(401, "Invalid email or password")
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = _now()
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
    tabs = effective_tabs(user, await tenant_tabs(s, user.tenant_id))
    return MeResponse(id=str(user.id), email=user.email, name=user.name, role=user.role,
                      status=user.status, tenant=tenant.slug, tabs=tabs)


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
