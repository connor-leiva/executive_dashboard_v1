"""The operator surface — creating, inspecting and suspending TENANTS.

Mounted at /api/v1/platform and gated by `current_platform_user`, a different realm from every
other router in this app. Nothing here takes a tenant from the request: the operator names the
tenant in the path, because the whole point is to act across tenants. That is precisely why the
identity is separate — see models.PlatformUser.

Deliberately not exposed: reading a tenant's DATA. This surface answers "does this customer
exist, are they suspended, is their sync healthy" — the operational questions — and never
"what are their numbers". An operator who needs that should be invited into the tenant as a
user, which leaves an audit row in that tenant rather than a silent read.
"""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import current_platform_user
from ..models import (AuditLog, Business, Domain, Integration, PlatformUser, SyncRun,
                      Tenant, User)
from ..security import hash_pw, make_platform_token, new_action_token, verify_pw
from ..services.audit import audit
from ..services.provisioning import INVITE_VALID_DAYS, invite_url, provision_tenant

router = APIRouter(prefix="/platform", tags=["platform"])

LOCK_THRESHOLD = 10
LOCK_MINUTES = 15


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _aware(d):
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


class OpLogin(BaseModel):
    email: str
    password: str


class NewTenant(BaseModel):
    slug: str
    name: str
    owner_email: str
    hostname: str | None = None
    businesses: list[dict] | None = None


@router.post("/login")
async def platform_login(body: OpLogin, s: AsyncSession = Depends(get_session)):
    """Sign in as an operator. Same neutral-error and lockout rules as tenant login, because
    this account is strictly more powerful than any tenant account."""
    op = (await s.execute(select(PlatformUser).where(
        PlatformUser.email == body.email.strip().lower()))).scalar_one_or_none()
    if not op or not op.password_hash or not op.is_active:
        raise HTTPException(401, "Invalid email or password")
    if op.locked_until and _aware(op.locked_until) > _now():
        raise HTTPException(423, "Account temporarily locked. Try again shortly.")
    if not verify_pw(body.password, op.password_hash):
        op.failed_logins = (op.failed_logins or 0) + 1
        if op.failed_logins >= LOCK_THRESHOLD:
            op.locked_until = _now() + dt.timedelta(minutes=LOCK_MINUTES)
        await s.commit()
        raise HTTPException(401, "Invalid email or password")
    op.failed_logins, op.locked_until, op.last_login_at = 0, None, _now()
    await s.commit()
    return {"token": make_platform_token(op.id, op.token_version or 0),
            "name": op.name, "email": op.email}


@router.get("/me")
async def platform_me(op: PlatformUser = Depends(current_platform_user)):
    return {"id": str(op.id), "email": op.email, "name": op.name}


async def _tenant_row(s: AsyncSession, t: Tenant) -> dict:
    """One tenant's operational health — never its data.

    The questions an operator actually has: is it live, who owns it, has anyone signed in, are
    its sources syncing. `last_error` is included because a customer whose sync has been broken
    for a week is the single thing most worth knowing and the thing nobody currently learns
    until they complain.
    """
    biz = (await s.execute(select(func.count()).select_from(Business)
                           .where(Business.tenant_id == t.id))).scalar_one()
    users = (await s.execute(select(func.count()).select_from(User)
                             .where(User.tenant_id == t.id))).scalar_one()
    pending = (await s.execute(select(func.count()).select_from(User).where(
        User.tenant_id == t.id, User.status == "invited"))).scalar_one()
    last_login = (await s.execute(select(func.max(User.last_login_at))
                                  .where(User.tenant_id == t.id))).scalar_one()
    hosts = (await s.execute(select(Domain.hostname).where(
        Domain.tenant_id == t.id).order_by(Domain.is_primary.desc()))).scalars().all()
    integs = (await s.execute(select(Integration).where(
        Integration.tenant_id == t.id))).scalars().all()
    broken = [{"provider": i.provider, "error": (i.last_error or "")[:200]}
              for i in integs if i.status == "error"]
    last_sync = max([i.last_synced_at for i in integs if i.last_synced_at], default=None)
    recent_fail = (await s.execute(select(func.count()).select_from(SyncRun).where(
        SyncRun.tenant_id == t.id, SyncRun.status == "error",
        SyncRun.started_at >= _now() - dt.timedelta(days=7)))).scalar_one()
    return {
        "slug": t.slug, "name": t.name, "status": t.status,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "hosts": list(hosts), "businesses": biz,
        "users": users, "users_pending_invite": pending,
        "last_login_at": last_login.isoformat() if last_login else None,
        "sources": len(integs), "sources_in_error": len(broken), "errors": broken,
        "last_synced_at": last_sync.isoformat() if last_sync else None,
        "sync_failures_7d": recent_fail,
    }


@router.get("/tenants")
async def list_tenants(op: PlatformUser = Depends(current_platform_user),
                       s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(Tenant).order_by(Tenant.created_at))).scalars().all()
    return {"tenants": [await _tenant_row(s, t) for t in rows]}


async def _get(s: AsyncSession, slug: str) -> Tenant:
    t = (await s.execute(select(Tenant).where(Tenant.slug == slug.lower()))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "Unknown tenant")
    return t


@router.get("/tenants/{slug}")
async def get_tenant(slug: str, op: PlatformUser = Depends(current_platform_user),
                     s: AsyncSession = Depends(get_session)):
    t = await _get(s, slug)
    row = await _tenant_row(s, t)
    row["owners"] = [
        {"email": u.email, "name": u.name, "role": u.role, "status": u.status,
         "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None}
        for u in (await s.execute(select(User).where(
            User.tenant_id == t.id, User.role.in_(("owner", "admin")))
            .order_by(User.created_at))).scalars().all()]
    return row


@router.post("/tenants", status_code=201)
async def create_tenant(body: NewTenant, op: PlatformUser = Depends(current_platform_user),
                        s: AsyncSession = Depends(get_session)):
    """Provision a tenant. Same path as the CLI — scripts.create_tenant and this route both
    call provision_tenant, so a tenant cannot be created two different ways."""
    try:
        r = await provision_tenant(s, slug=body.slug, name=body.name,
                                   owner_email=body.owner_email, hostname=body.hostname,
                                   businesses=body.businesses)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # Audited INSIDE the new tenant, so its own trail begins with its creation and names the
    # operator who did it. actor_user_id stays null — the actor is not a user of this tenant.
    audit(s, r.tenant_id, None, "tenant.created", "tenant", r.tenant_id,
          {"by": op.email, "slug": r.slug, "hostname": r.hostname})
    await s.commit()
    return {"slug": r.slug, "hostname": r.hostname, "owner_email": r.owner_email,
            "invite_url": r.invite_url, "catalogs": r.catalogs}


@router.post("/tenants/{slug}/suspend")
async def suspend_tenant(slug: str, op: PlatformUser = Depends(current_platform_user),
                         s: AsyncSession = Depends(get_session)):
    """Suspend a workspace: no new logins, and every live session in it stops working."""
    t = await _get(s, slug)
    t.status = "suspended"
    audit(s, t.id, None, "tenant.suspended", "tenant", t.id, {"by": op.email})
    await s.commit()
    return {"slug": t.slug, "status": t.status}


@router.post("/tenants/{slug}/resume")
async def resume_tenant(slug: str, op: PlatformUser = Depends(current_platform_user),
                        s: AsyncSession = Depends(get_session)):
    t = await _get(s, slug)
    t.status = "active"
    audit(s, t.id, None, "tenant.resumed", "tenant", t.id, {"by": op.email})
    await s.commit()
    return {"slug": t.slug, "status": t.status}


@router.post("/tenants/{slug}/resend-invite")
async def resend_owner_invite(slug: str, op: PlatformUser = Depends(current_platform_user),
                              s: AsyncSession = Depends(get_session)):
    """Mint a fresh owner invite. The commonest real failure of onboarding is a link that
    expired before the customer got to it, and there was no way to issue another."""
    t = await _get(s, slug)
    owner = (await s.execute(select(User).where(
        User.tenant_id == t.id, User.role == "owner").order_by(User.created_at))).scalars().first()
    if owner is None:
        raise HTTPException(404, "That tenant has no owner")
    if owner.status == "active":
        raise HTTPException(409, "That owner has already accepted; send a password reset instead.")
    raw, token_hash = new_action_token()
    owner.action_token_hash = token_hash
    owner.action_token_purpose = "invite"
    owner.action_token_expires = _now() + dt.timedelta(days=INVITE_VALID_DAYS)
    host = (await s.execute(select(Domain.hostname).where(
        Domain.tenant_id == t.id).order_by(Domain.is_primary.desc()))).scalars().first()
    audit(s, t.id, None, "tenant.invite_resent", "user", owner.id, {"by": op.email})
    await s.commit()
    return {"owner_email": owner.email, "invite_url": invite_url(host or t.slug, raw)}


@router.get("/tenants/{slug}/audit")
async def tenant_audit(slug: str, limit: int = 50,
                       op: PlatformUser = Depends(current_platform_user),
                       s: AsyncSession = Depends(get_session)):
    """The tenant's own audit trail — who was invited, what was connected, who signed in.
    Operational history, not business data."""
    t = await _get(s, slug)
    rows = (await s.execute(select(AuditLog).where(AuditLog.tenant_id == t.id)
                            .order_by(AuditLog.created_at.desc())
                            .limit(max(1, min(200, limit))))).scalars().all()
    return {"slug": t.slug, "events": [
        {"action": r.action, "target_type": r.target_type, "detail": r.detail,
         "at": r.created_at.isoformat() if r.created_at else None} for r in rows]}
