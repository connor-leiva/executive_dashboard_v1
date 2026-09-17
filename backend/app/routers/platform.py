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
import functools
import json
import os
import time
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import plans
from ..config import settings
from ..db import get_session
from ..deps import STEP_UP_SCOPES, current_platform_user
from ..models import (AuditLog, Business, Domain, Integration, JobHeartbeat, PlatformAudit,
                      PlatformBillingConfig, PlatformInvoice, PlatformSubscription, PlatformUser,
                      ShareLink, SyncRun, Tenant, User)
from ..security import dec, enc, hash_pw, make_platform_token, new_action_token, verify_pw
from ..services import (fleet_health, fleet_rollup, jobs, mail_templates, mailer, operator_audit,
                        platform_billing, sync_jobs)
from ..throttle import client_ip
from ..services.provisioning import INVITE_VALID_DAYS, invite_url, provision_tenant
from ..services.tabs import PLATFORM_TABS, tenant_tab_descriptors
from ..services.users import RESET_HOURS, primary_host
from ..tenancy import PLATFORM_HOSTS, WILDCARD_RESERVED, url_scheme

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
    # REQUIRED from the operator console. provision_tenant accepts no plan and applies the column
    # default, and that default is the bug it documents: a workspace that bought the portal came
    # up on `team`, without it, and it looked like a defect rather than a tier. Validated there,
    # so a typo is a 400 rather than a workspace on a plan that does not exist.
    plan: str


class SuspendBody(BaseModel):
    # Required and non-empty: the reason is written to both audit trails and shown on the fleet
    # list for as long as the workspace stays suspended.
    reason: str


class FreezeBody(BaseModel):
    # Required for the same reason as a suspension: the next operator to see a frozen workspace
    # needs to know what it is waiting on before they unfreeze it.
    reason: str


def _record(s: AsyncSession, op: PlatformUser, request: Request, t: Tenant, action: str,
            target_type: str | None = None, target_id=None, *, category: str = "Workspace",
            **detail) -> None:
    """Record an operator's change twice: in the workspace's own audit log, where the customer can
    see what Acumyn did, and in platform_audit, the operator's cross-tenant trail. Every mutation
    in this router goes through here (services/operator_audit.py)."""
    operator_audit.record(s, op, t, action, target_type, target_id, request=request,
                          category=category, **detail)


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


def _plan_out(key: str) -> dict:
    p = plans.PLANS[key]
    return {
        "key": key, "name": p["name"], "price_monthly": p["price_monthly"],
        "max_businesses": p["max_businesses"], "max_users": p["max_users"],
        "max_share_links": p["max_share_links"], "history_months": p["history_months"],
        "max_ai_employees": p["max_ai_employees"], "custom_branding": p["custom_branding"],
        "intranet": p["intranet"], "ai_assistant": p["ai_assistant"],
        "extra_tabs": sorted(p["extra_tabs"]), "sources": sorted(p["sources"]),
    }


@router.get("/plans")
async def list_plans(op: PlatformUser = Depends(current_platform_user)):
    """Every tier exactly as plans.py defines it.

    SERVED, NOT MIRRORED. The console's design carried its own copy of these limits, and a copy
    drifted during its own audit: one pane claimed 15 seats while another correctly read Team's 5.
    Reading the one table the product enforces removes the second copy rather than testing it.

    Prices appear here and nowhere tenant-facing. plans.describe() stays price-free, because a
    stale figure on a customer's dashboard is worse than none; an operator billing screen without
    the price is simply useless.
    """
    return {"plans": [_plan_out(k) for k in plans.ORDER],
            "gated_tabs": [{"key": t, "lowest_tier": plans.lowest_tier_with(t)}
                           for t in plans.gated_tabs()],
            "default_plan": plans.plan_of(None),
            "platform_domain": settings.PLATFORM_DOMAIN,
            # tenancy keeps two reserved sets with different meanings, and flattening them took
            # production down once: www was treated as unreachable while the dashboard lived there.
            "reserved": {"platform_hosts": sorted(PLATFORM_HOSTS),
                         "wildcard_reserved": sorted(WILDCARD_RESERVED)}}


async def _tenant_row(s: AsyncSession, t: Tenant, now: dt.datetime | None = None) -> dict:
    """One tenant's operational health — never its data.

    The questions an operator actually has: is it live, who owns it, has anyone signed in, are
    its sources syncing. `last_error` is included because a customer whose sync has been broken
    for a week is the single thing most worth knowing and the thing nobody currently learns
    until they complain.

    `health` is fleet_health.evaluate() over the same facts: the state, the reasons for it and
    how it was derived. It is computed here on every read and stored nowhere.
    """
    now = now or _now()
    facts = await fleet_health.gather(s, t, now)
    health = fleet_health.evaluate(facts, now)
    biz = (await s.execute(select(func.count()).select_from(Business)
                           .where(Business.tenant_id == t.id))).scalar_one()
    hosts = (await s.execute(select(Domain.hostname).where(
        Domain.tenant_id == t.id).order_by(Domain.is_primary.desc()))).scalars().all()
    people = facts.people
    broken = [{"provider": src.provider, "error": (src.last_error or "")[:200]}
              for src in facts.sources if src.status == "error"]
    last_sync = max((fleet_health._aware(src.last_synced_at) for src in facts.sources
                     if src.last_synced_at), default=None)
    last_login = max((fleet_health._aware(p.last_login_at) for p in people if p.last_login_at),
                     default=None)
    owner = next((p for p in people if p.role == "owner" and p.status == "active"),
                 next((p for p in people if p.role == "owner"), None))
    stale = sum(1 for sig in health["signals"] if sig["key"].split(":")[1] == "stale")
    return {
        "slug": t.slug, "name": t.name, "status": t.status,
        # The plan as stored, and as the product actually applies it. plan_of() defaults an unset
        # or unknown value to the most generous tier on purpose; `plan_set` is what lets the
        # console show a workspace silently receiving everything instead of letting it hide.
        "plan": plans.plan_of(t), "plan_stored": t.plan,
        "plan_set": (t.plan or "").strip().lower() in plans.PLANS,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "hosts": list(hosts), "businesses": biz,
        "owner_email": owner.email if owner else None,
        "users": len(people),
        "users_pending_invite": sum(1 for p in people if p.status == "invited"),
        # C17: the split that makes the People column answer something.
        "people": {
            "active": sum(1 for p in people if p.status == "active"),
            "invited": sum(1 for p in people if p.status == "invited"),
            "disabled": sum(1 for p in people if p.status == "disabled"),
            "locked": sum(1 for p in people if p.locked_until
                          and fleet_health._aware(p.locked_until) > now),
            "two_factor": sum(1 for p in people if p.two_factor and p.status == "active"),
        },
        "last_login_at": last_login.isoformat() if last_login else None,
        "sources": sum(1 for src in facts.sources if src.configured),
        "sources_in_error": len(broken), "sources_stale": stale, "errors": broken,
        "last_synced_at": last_sync.isoformat() if last_sync else None,
        "sync_failures_7d": sum(src.failed_runs_7d for src in facts.sources),
        "syncs_frozen": facts.syncs_frozen,
        "share_links_live": facts.share_links_live,
        "tokens": {"used": facts.tokens_used, "budget": facts.token_budget},
        "billing": {"billed": facts.subscription is not None,
                    "status": (facts.subscription or {}).get("status")},
        "health": {k: health[k] for k in ("state", "rank", "why", "derivation")},
        "signals": health["signals"],
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
async def create_tenant(request: Request, body: NewTenant, bg: BackgroundTasks,
                        op: PlatformUser = Depends(current_platform_user),
                        s: AsyncSession = Depends(get_session)):
    """Provision a tenant. Same path as the CLI — scripts.create_tenant and this route both
    call provision_tenant, so a tenant cannot be created two different ways."""
    try:
        r = await provision_tenant(s, slug=body.slug, name=body.name,
                                   owner_email=body.owner_email, hostname=body.hostname,
                                   businesses=body.businesses, plan=body.plan)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # Audited INSIDE the new tenant, so its own trail begins with its creation and names the
    # operator who did it, and in the operator trail.
    created = await s.get(Tenant, r.tenant_id)
    _record(s, op, request, created, "tenant.created", "tenant", r.tenant_id,
            slug=r.slug, hostname=r.hostname, plan=body.plan)
    await s.commit()
    # Emailed AND returned. The operator keeps the link for the case the customer never sees
    # the mail, which on a brand-new sending domain is the case worth planning for.
    bg.add_task(mailer.send, r.owner_email,
                *mail_templates.owner_invite(r.invite_url, body.name, INVITE_VALID_DAYS))
    return {"slug": r.slug, "hostname": r.hostname, "owner_email": r.owner_email,
            "invite_url": r.invite_url, "catalogs": r.catalogs}


@router.post("/tenants/{slug}/suspend")
async def suspend_tenant(request: Request, slug: str, body: SuspendBody,
                         op: PlatformUser = Depends(current_platform_user),
                         s: AsyncSession = Depends(get_session)):
    """Suspend a workspace: no new logins, every live session in it stops working, and the
    scheduled sync skips it. Share links keep serving until revoked separately."""
    reason = (body.reason or "").strip()[:500]
    if not reason:
        raise HTTPException(400, "Give a reason. It is recorded and shown with the suspension.")
    t = await _get(s, slug)
    t.status = "suspended"
    _record(s, op, request, t, "tenant.suspended", "tenant", t.id, reason=reason)
    await s.commit()
    return {"slug": t.slug, "status": t.status}


@router.post("/tenants/{slug}/resume")
async def resume_tenant(request: Request, slug: str, op: PlatformUser = Depends(current_platform_user),
                        s: AsyncSession = Depends(get_session)):
    t = await _get(s, slug)
    t.status = "active"
    _record(s, op, request, t, "tenant.resumed", "tenant", t.id)
    await s.commit()
    return {"slug": t.slug, "status": t.status}


@router.post("/tenants/{slug}/resend-invite")
async def resend_owner_invite(request: Request, slug: str, bg: BackgroundTasks,
                              op: PlatformUser = Depends(current_platform_user),
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
    _record(s, op, request, t, "tenant.invite_resent", "user", owner.id, category="People",
            email=owner.email)
    await s.commit()
    url = invite_url(await primary_host(s, t.id), raw)
    bg.add_task(mailer.send, owner.email,
                *mail_templates.owner_invite(url, t.name, INVITE_VALID_DAYS),
                idempotency_key=f"owner-invite-{owner.id}-{owner.action_token_expires.isoformat()}")
    return {"owner_email": owner.email, "invite_url": url}


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
    emails = {}
    actor_ids = {r.actor_user_id for r in rows if r.actor_user_id}
    if actor_ids:
        emails = dict((await s.execute(select(User.id, User.email).where(
            User.id.in_(actor_ids)))).all())
    return {"slug": t.slug, "events": [
        {"action": r.action, "target_type": r.target_type, "target_id": r.target_id,
         "detail": r.detail, "category": r.category,
         "summary": operator_audit.operator_summary(r.action, r.summary),
         # Who did it: a named person in the workspace, Acumyn (no actor, and the operator's
         # address in detail.by), or the system.
         "actor": ("acumyn" if not r.actor_user_id and (r.detail or {}).get("by")
                   else "user" if r.actor_user_id else "system"),
         "actor_label": ((r.detail or {}).get("by") or (r.detail or {}).get("email")
                         if not r.actor_user_id
                         else emails.get(r.actor_user_id) or r.actor_label),
         "at": r.created_at.isoformat() if r.created_at else None} for r in rows]}


# ── phase 2: the fleet, and one workspace's panes ─────────────────────────────────────────
async def _fleet_rows(s: AsyncSession, now: dt.datetime) -> list[dict]:
    tenants = (await s.execute(select(Tenant).order_by(Tenant.created_at))).scalars().all()
    return [await _tenant_row(s, t, now) for t in tenants]


def _rollup(rows: list[dict]) -> dict:
    capped = [r for r in rows if r["tokens"]["budget"] is not None]
    configured = sum(r["sources"] for r in rows)
    broken = sum(r["sources_in_error"] for r in rows)
    stale = sum(r["sources_stale"] for r in rows)
    return {
        "workspaces": {"total": len(rows),
                       "suspended": sum(1 for r in rows if r["status"] == "suspended"),
                       "live": sum(1 for r in rows if r["status"] != "suspended")},
        "people": {k: sum(r["people"][k] for r in rows)
                   for k in ("active", "invited", "disabled", "locked", "two_factor")},
        "sources": {"configured": configured, "broken": broken, "stale": stale,
                    "ok": configured - broken - stale},
        "failed_runs_7d": sum(r["sync_failures_7d"] for r in rows),
        # A capped total can only describe the capped workspaces: an unlimited budget is None, and
        # summing it as zero would understate the cap.
        "tokens": {"used": sum(r["tokens"]["used"] for r in rows),
                   "capped_budget": sum(r["tokens"]["budget"] for r in capped),
                   "capped_workspaces": len(capped)},
        "states": {state: sum(1 for r in rows if r["health"]["state"] == state)
                   for state in fleet_health.RANK},
        "mrr_cents": None,
    }


def _triage(rows: list[dict]) -> list[dict]:
    signals = [sig for r in rows for sig in r["signals"]]
    return sorted(signals, key=lambda sig: (fleet_health.RANK[sig["severity"]],
                                            sig["tenant_name"].lower(), sig["title"]))


@router.get("/fleet")
async def fleet(op: PlatformUser = Depends(current_platform_user),
                s: AsyncSession = Depends(get_session)):
    """The fleet in one read: the rollup and the triage queue, from one pass over every workspace,
    so the two can never disagree."""
    now = _now()
    rows = await _fleet_rows(s, now)
    rollup = _rollup(rows)
    # MRR is Acumyn's own revenue from its mirror of Stripe, never a workspace's figures: active
    # subscriptions only, a yearly price spread over twelve. Null until platform billing is connected.
    cfg = await platform_billing.config(s)
    if cfg is not None and cfg.secret_key_enc:
        subs = (await s.execute(select(PlatformSubscription))).scalars().all()
        rollup["mrr_cents"] = sum(platform_billing.monthly_cents(r) for r in subs)
        rollup["billed_workspaces"] = sum(1 for r in subs if r.status not in ("canceled", "incomplete_expired"))
    return {"rollup": rollup, "triage": _triage(rows), "read_at": now.isoformat()}


@router.get("/fleet/triage")
async def fleet_triage(op: PlatformUser = Depends(current_platform_user),
                       s: AsyncSession = Depends(get_session)):
    """Every open signal across the fleet, ranked by severity. Each carries its derivation and
    the action that clears it, and one cause is one row however many runs it failed."""
    return {"triage": _triage(await _fleet_rows(s, _now()))}


@router.get("/fleet/providers")
async def fleet_providers(op: PlatformUser = Depends(current_platform_user),
                          s: AsyncSession = Depends(get_session)):
    return {"providers": await fleet_rollup.providers(s, _now())}


@router.get("/fleet/incidents")
async def fleet_incidents(op: PlatformUser = Depends(current_platform_user),
                          s: AsyncSession = Depends(get_session)):
    return {"incidents": await fleet_rollup.incidents(s, _now()),
            "window_days": fleet_rollup.INCIDENT_WINDOW_DAYS}


def _iso(d: dt.datetime | None) -> str | None:
    d = fleet_health._aware(d)
    return d.isoformat() if d else None


@router.get("/tenants/{slug}/people")
async def tenant_people(slug: str, op: PlatformUser = Depends(current_platform_user),
                        s: AsyncSession = Depends(get_session)):
    """Every account in the workspace: who, what role, whether they have signed in, whether they
    are locked out or have a second factor. Never a password hash, a token or a TOTP secret."""
    t = await _get(s, slug)
    now = _now()
    descriptors = {d["key"]: d["label"] for d in await tenant_tab_descriptors(s, t.id)}
    users = (await s.execute(select(User).where(User.tenant_id == t.id)
                             .order_by(User.created_at))).scalars().all()
    people = []
    for u in users:
        invite_expires = (fleet_health._aware(u.action_token_expires)
                          if u.action_token_purpose == "invite" else None)
        locked_until = fleet_health._aware(u.locked_until)
        people.append({
            "id": str(u.id), "name": u.name, "email": u.email, "role": u.role, "status": u.status,
            "locked": bool(locked_until and locked_until > now), "locked_until": _iso(locked_until),
            "last_login_at": _iso(u.last_login_at), "created_at": _iso(u.created_at),
            "two_factor": u.totp_confirmed_at is not None,
            "invited_at": (_iso(invite_expires - dt.timedelta(days=INVITE_VALID_DAYS))
                           if invite_expires else None),
            "invite_expires": _iso(invite_expires),
            "invite_expired": bool(u.status == "invited" and invite_expires and invite_expires <= now),
            # Owners and admins see every tab by role; a member sees their grants.
            "tabs": ("all" if u.role in ("owner", "admin")
                     else [descriptors[k] for k in (u.tab_access or []) if k in descriptors]),
            "expires_at": _iso(getattr(u, "expires_at", None)),
        })
    return {"slug": t.slug, "people": people}


@router.get("/tenants/{slug}/sources")
async def tenant_sources(slug: str, op: PlatformUser = Depends(current_platform_user),
                         s: AsyncSession = Depends(get_session)):
    """Each connection with its actual error, its last sync and its recent runs, plus the
    providers the plan offers that nobody has connected."""
    t = await _get(s, slug)
    now = _now()
    facts = await fleet_health.gather(s, t, now)
    paused = t.status == "suspended" or facts.syncs_frozen
    sources = []
    for src in facts.sources:
        last = fleet_health._aware(src.last_synced_at)
        limit = fleet_health.stale_after(src.provider)
        state = ("broken" if src.status == "error"
                 else "off" if src.status != "connected"
                 else "paused" if paused
                 else "stale" if last is None or now - last > limit
                 else "healthy")
        sources.append({
            "id": src.id, "provider": src.provider,
            "provider_name": fleet_health.provider_name(src.provider),
            "business": src.business, "status": src.status, "state": state,
            "last_synced_at": _iso(last), "last_error": (src.last_error or "")[:500] or None,
            "failed_runs_7d": src.failed_runs_7d,
            "stale_after_minutes": int(limit.total_seconds() // 60)})
    runs = (await s.execute(select(SyncRun).where(SyncRun.tenant_id == t.id)
                            .order_by(SyncRun.started_at.desc()).limit(200))).scalars().all()
    configured = {src.provider for src in facts.sources if src.configured}
    available = sorted(plans.limits(t)["sources"] - configured)
    return {"slug": t.slug, "paused": paused, "syncs_frozen": facts.syncs_frozen,
            "sources": sources, "runs": fleet_rollup.group_runs(runs),
            "available": [{"key": k, "name": fleet_health.provider_name(k)} for k in available],
            "sync_interval_minutes": settings.SYNC_INTERVAL_MINUTES}


@router.get("/tenants/{slug}/modules")
async def tenant_modules(slug: str, op: PlatformUser = Depends(current_platform_user),
                         s: AsyncSession = Depends(get_session)):
    """TWO GROUPS, because plans.py draws exactly two.

    `gated` is the platform modules the TIER decides: Books, the flywheel, Binder, AI employees.
    There is no per-workspace override, so nothing in it is switchable: a module the plan excludes
    is refused by the API, not merely hidden. `own` is the workspace's businesses and programmes,
    which a plan limits in number but never hides. Flattening the two is how a Team workspace
    once reported Books as enabled.
    """
    t = await _get(s, slug)
    tier = plans.plan_of(t)
    descriptors = await tenant_tab_descriptors(s, t.id)
    labels = {d["key"]: d["label"] for d in descriptors}
    gated = []
    for key in plans.gated_tabs():
        lowest = plans.lowest_tier_with(key)
        gated.append({"key": key,
                      "name": labels.get(key) or PLATFORM_TABS.get(key, {}).get("label", key),
                      "included": key in plans.PLANS[tier]["extra_tabs"],
                      "lowest_tier": lowest, "lowest_tier_name": plans.PLANS[lowest]["name"]})
    gated_keys = set(plans.gated_tabs())
    own = [{"key": d["key"], "name": d["label"], "always": d["key"] == "portfolio"}
           for d in descriptors if d["key"] not in gated_keys and d["key"] != "ads"]
    if not any(o["key"] == "portfolio" for o in own):
        own.insert(0, {"key": "portfolio", "name": "Portfolio", "always": True})
    return {"slug": t.slug, "plan": tier, "plan_name": plans.PLANS[tier]["name"],
            "plan_set": (t.plan or "").strip().lower() in plans.PLANS,
            "gated": gated, "own": own}


@router.patch("/tenants/{slug}/modules")
async def patch_tenant_modules(slug: str, op: PlatformUser = Depends(current_platform_user),
                               s: AsyncSession = Depends(get_session)):
    """Refused, always. A gated module follows the plan, and a per-workspace switch would be a
    setting the product ignores. This route exists so the refusal says where the decision lives."""
    await _get(s, slug)
    raise HTTPException(403, "Modules follow the plan. Change the workspace's plan instead.")


@router.get("/tenants/{slug}/usage")
async def tenant_usage(slug: str, op: PlatformUser = Depends(current_platform_user),
                       s: AsyncSession = Depends(get_session)):
    """What the workspace consumes against the caps its plan enforces. Caps come from plans.py and
    nowhere else."""
    from ..models import BinderDocument

    t = await _get(s, slug)
    now = _now()
    facts = await fleet_health.gather(s, t, now)
    lim = plans.limits(t)
    biz = (await s.execute(select(func.count()).select_from(Business)
                           .where(Business.tenant_id == t.id))).scalar_one()
    runs = (await s.execute(select(func.count()).select_from(SyncRun).where(
        SyncRun.tenant_id == t.id, SyncRun.started_at >= now - dt.timedelta(days=7)))).scalar_one()
    docs = (await s.execute(select(func.count()).select_from(BinderDocument)
                            .where(BinderDocument.tenant_id == t.id))).scalar_one()
    seats = sum(1 for p in facts.people
                if p.status in ("active", "invited") and p.expires_at is None)
    return {
        "slug": t.slug, "plan": plans.plan_of(t),
        "tokens": {"used": facts.tokens_used, "budget": facts.token_budget,
                   "platform_default": settings.AI_EMPLOYEES_TOKEN_BUDGET or None,
                   "override": "ai_token_budget" in (t.config or {})},
        "seats": {"used": seats, "invited": sum(1 for p in facts.people if p.status == "invited"),
                  "cap": lim["max_users"]},
        "businesses": {"used": biz, "cap": lim["max_businesses"]},
        "share_links": {"live": facts.share_links_live, "cap": lim["max_share_links"]},
        "sync_runs_7d": runs, "sync_interval_minutes": settings.SYNC_INTERVAL_MINUTES,
        # Counted, never sized: binder_document has no byte column, so storage is not a figure.
        "documents": {"count": docs, "bytes": None},
    }


@router.get("/tenants/{slug}/share-links")
async def tenant_share_links(slug: str, op: PlatformUser = Depends(current_platform_user),
                             s: AsyncSession = Depends(get_session)):
    """Public share links serve without a login, and suspension does not stop them. The token
    itself is never returned: whoever holds it can open the link."""
    t = await _get(s, slug)
    now = _now()
    rows = (await s.execute(select(ShareLink).where(ShareLink.tenant_id == t.id)
                            .order_by(ShareLink.created_at.desc()))).scalars().all()
    creators = {r.created_by for r in rows if r.created_by}
    emails = (dict((await s.execute(select(User.id, User.email).where(
        User.id.in_(creators)))).all()) if creators else {})
    links = []
    for r in rows:
        expires = fleet_health._aware(r.expires_at)
        links.append({"id": str(r.id), "scope": r.scope, "scope_ref": r.scope_ref,
                      "created_at": _iso(r.created_at), "expires_at": _iso(expires),
                      "revoked_at": _iso(r.revoked_at),
                      "live": r.revoked_at is None and (expires is None or expires > now),
                      "created_by": emails.get(r.created_by)})
    return {"slug": t.slug, "links": links}


@router.get("/tenants/{slug}/security")
async def tenant_security(slug: str, op: PlatformUser = Depends(current_platform_user),
                          s: AsyncSession = Depends(get_session)):
    """Sign-in security, read-only. Everything here is the workspace's own; an operator can unlock
    an account but can never read, set or bypass anybody's second factor."""
    t = await _get(s, slug)
    now = _now()
    facts = await fleet_health.gather(s, t, now)
    active = [p for p in facts.people if p.status == "active"]
    return {
        "slug": t.slug,
        "two_factor": {"on": sum(1 for p in active if p.two_factor), "active": len(active)},
        "locked": [{"id": p.id, "email": p.email, "locked_until": _iso(p.locked_until)}
                   for p in facts.people
                   if p.locked_until and fleet_health._aware(p.locked_until) > now],
        "step_up_sections": [scope for scope in STEP_UP_SCOPES if plans.allows(t, scope)],
        "two_factor_required": False,
    }


# ── phase 3: write actions ────────────────────────────────────────────────────────────────
# Every one is recorded through _record, in the workspace's own audit log.
#
# LINKS GO BY EMAIL, NEVER BACK TO THE OPERATOR. An invite or a password reset for somebody in a
# workspace is a way to sign in as that person, so returning one here would be a path from the
# operator realm into the tenant's data: the one thing the two realms exist to prevent. The owner
# invite above is the exception, and it predates the console: it is how a workspace nobody has
# entered yet gets its first person.
FULL_SYNC_GUARD_MINUTES = 15


def _paused(t: Tenant) -> str | None:
    """Why nothing may be pulled for this workspace right now, or None."""
    if t.status == "suspended":
        return "This workspace is suspended, so nothing syncs for it. Resume it first."
    if sync_jobs.syncs_frozen(t.config):
        return "Syncs are frozen for this workspace. Unfreeze them first."
    return None


async def _workspace_base(s: AsyncSession, t: Tenant) -> str:
    """The workspace's own web origin: its primary domain, else {slug}.{PLATFORM_DOMAIN}. Never the
    origin of the request, which here is the operator console's."""
    host = await primary_host(s, t.id)
    return f"{url_scheme(host)}://{host}"


async def _source(s: AsyncSession, t: Tenant, source_id: str) -> Integration:
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(404, "No such source in this workspace")
    integ = (await s.execute(select(Integration).where(
        Integration.id == sid, Integration.tenant_id == t.id))).scalar_one_or_none()
    if integ is None:
        raise HTTPException(404, "No such source in this workspace")
    return integ


async def _person(s: AsyncSession, t: Tenant, person_id: str) -> User:
    try:
        pid = uuid.UUID(person_id)
    except ValueError:
        raise HTTPException(404, "No such person in this workspace")
    u = (await s.execute(select(User).where(User.id == pid, User.tenant_id == t.id))
         ).scalar_one_or_none()
    if u is None:
        raise HTTPException(404, "No such person in this workspace")
    return u


async def _administrators(s: AsyncSession, t: Tenant) -> list[User]:
    """The people who can connect a source: active owners, then active admins."""
    rows = (await s.execute(select(User).where(
        User.tenant_id == t.id, User.status == "active", User.role.in_(("owner", "admin")))
        .order_by(User.created_at))).scalars().all()
    return sorted(rows, key=lambda u: u.role != "owner")


def _ten_minutes() -> int:
    """A send's idempotency window. A double-pressed button sends one email; pressing again a
    quarter of an hour later sends another, because by then it is a second request."""
    return int(_now().timestamp() // 600)


@router.post("/tenants/{slug}/sync", status_code=202)
async def sync_tenant(request: Request, slug: str, bg: BackgroundTasks,
                      op: PlatformUser = Depends(current_platform_user),
                      s: AsyncSession = Depends(get_session)):
    """Pull every connected source now, on the same job the workspace's own Sync button runs."""
    t = await _get(s, slug)
    if why := _paused(t):
        raise HTTPException(409, why)
    configured = (await s.execute(select(func.count()).select_from(Integration).where(
        Integration.tenant_id == t.id, Integration.status.in_(("connected", "error"))))).scalar_one()
    if not configured:
        raise HTTPException(409, "Nothing is connected, so there is nothing to sync.")
    running = (await s.execute(select(SyncRun).where(
        SyncRun.tenant_id == t.id, SyncRun.provider == "all", SyncRun.status == "running",
        SyncRun.started_at >= _now() - dt.timedelta(minutes=FULL_SYNC_GUARD_MINUTES))
        .order_by(SyncRun.started_at.desc()).limit(1))).scalar_one_or_none()
    if running is not None:
        started = fleet_health._span(_now() - fleet_health._aware(running.started_at))
        raise HTTPException(409, f"A full sync is already running. It started {started} ago.")
    run = SyncRun(tenant_id=t.id, provider="all", status="running")
    s.add(run)
    _record(s, op, request, t, "tenant.sync_requested", "tenant", t.id, category="Integrations",
            sources=configured)
    await s.commit()
    bg.add_task(sync_jobs.run_all_job, t.id, run.id)
    return {"job_id": str(run.id), "sources": configured}


@router.post("/tenants/{slug}/sources/{source_id}/sync", status_code=202)
async def sync_source(request: Request, slug: str, source_id: str, bg: BackgroundTasks,
                      op: PlatformUser = Depends(current_platform_user),
                      s: AsyncSession = Depends(get_session)):
    t = await _get(s, slug)
    integ = await _source(s, t, source_id)
    if why := _paused(t):
        raise HTTPException(409, why)
    name = fleet_health.provider_name(integ.provider)
    if integ.status not in ("connected", "error"):
        raise HTTPException(409, f"{name} has no credentials to sync with. Send a reconnect link "
                                 "instead.")
    _record(s, op, request, t, "tenant.sync_requested", "integration", integ.id, category="Integrations",
            provider=integ.provider)
    await s.commit()
    bg.add_task(sync_jobs.run_one_job, t.id, integ.id)
    return {"provider": integ.provider, "provider_name": name}


@router.post("/tenants/{slug}/sources/{source_id}/reconnect-link")
async def send_reconnect_link(request: Request, slug: str, source_id: str, bg: BackgroundTasks,
                              op: PlatformUser = Depends(current_platform_user),
                              s: AsyncSession = Depends(get_session)):
    """Email the workspace's owners and admins a link to reconnect a source.

    Operators cannot re-authorise on a tenant's behalf: a connection has to be granted by somebody
    who can sign in to the provider. No token is minted either. The link is the workspace's own
    Settings page and whoever follows it signs in as themselves; a link that skipped sign-in would
    let anyone holding the email connect a data source to the workspace.
    """
    t = await _get(s, slug)
    integ = await _source(s, t, source_id)
    admins = await _administrators(s, t)
    if not admins:
        raise HTTPException(409, "Nobody can reconnect it: the workspace has no active owner or "
                                 "admin. Reissue the owner's invite first.")
    business = (await s.get(Business, integ.business_id)).name if integ.business_id else None
    name = fleet_health.provider_name(integ.provider)
    url = f"{await _workspace_base(s, t)}/settings/integrations"
    sent_to = [u.email for u in admins]
    _record(s, op, request, t, "tenant.reconnect_link_sent", "integration", integ.id,
            category="Integrations", provider=integ.provider, sent_to=sent_to)
    await s.commit()
    window = _ten_minutes()
    for u in admins:
        bg.add_task(mailer.send, u.email, *mail_templates.reconnect_source(url, t.name, name, business),
                    idempotency_key=f"reconnect-{integ.id}-{u.id}-{window}")
    return {"sent_to": sent_to, "url": url, "provider_name": name}


@router.post("/tenants/{slug}/sources/setup-link")
async def send_setup_link(request: Request, slug: str, bg: BackgroundTasks,
                          op: PlatformUser = Depends(current_platform_user),
                          s: AsyncSession = Depends(get_session)):
    """Email the owners and admins of a workspace with nothing connected a link to connect their
    first system. The same kind of link as a reconnect: their Settings page, no token."""
    t = await _get(s, slug)
    admins = await _administrators(s, t)
    if not admins:
        raise HTTPException(409, "Nobody can connect anything yet: the workspace has no active "
                                 "owner or admin. Reissue the owner's invite first.")
    url = f"{await _workspace_base(s, t)}/settings/integrations"
    sent_to = [u.email for u in admins]
    _record(s, op, request, t, "tenant.setup_link_sent", "tenant", t.id, category="Integrations",
            sent_to=sent_to)
    await s.commit()
    window = _ten_minutes()
    for u in admins:
        bg.add_task(mailer.send, u.email, *mail_templates.connect_first_source(url, t.name),
                    idempotency_key=f"setup-{t.id}-{u.id}-{window}")
    return {"sent_to": sent_to, "url": url}


@router.post("/tenants/{slug}/freeze-syncs")
async def freeze_syncs(request: Request, slug: str, body: FreezeBody,
                       op: PlatformUser = Depends(current_platform_user),
                       s: AsyncSession = Depends(get_session)):
    """Stop pulling from every source while people stay signed in.

    For a credential that may be compromised, where the first job is to stop it being used. The
    scheduled sync, the daily roster and ads jobs, the workspace's own Sync buttons and this
    console's all refuse a frozen workspace until it is unfrozen.
    """
    reason = (body.reason or "").strip()[:500]
    if not reason:
        raise HTTPException(400, "Give a reason. It is recorded and shown for as long as syncs stay "
                                 "frozen.")
    t = await _get(s, slug)
    if sync_jobs.syncs_frozen(t.config):
        raise HTTPException(409, "Syncs are already frozen for this workspace.")
    t.config = {**(t.config or {}), "syncs_frozen": True}
    _record(s, op, request, t, "tenant.syncs_frozen", "tenant", t.id, category="Integrations", reason=reason)
    await s.commit()
    return {"slug": t.slug, "syncs_frozen": True}


@router.post("/tenants/{slug}/unfreeze-syncs")
async def unfreeze_syncs(request: Request, slug: str, op: PlatformUser = Depends(current_platform_user),
                         s: AsyncSession = Depends(get_session)):
    t = await _get(s, slug)
    if not sync_jobs.syncs_frozen(t.config):
        raise HTTPException(409, "Syncs are not frozen for this workspace.")
    cfg = dict(t.config or {})
    cfg.pop("syncs_frozen", None)
    t.config = cfg
    _record(s, op, request, t, "tenant.syncs_unfrozen", "tenant", t.id, category="Integrations")
    await s.commit()
    return {"slug": t.slug, "syncs_frozen": False}


def _mint_invite(u: User) -> str:
    raw, token_hash = new_action_token()
    u.action_token_hash, u.action_token_purpose = token_hash, "invite"
    u.action_token_expires = _now() + dt.timedelta(days=INVITE_VALID_DAYS)
    return raw


def _invite_mail(t: Tenant, u: User, base: str, raw: str):
    url = f"{base}/accept-invite?token={raw}"
    if u.role == "owner":
        return mail_templates.owner_invite(url, t.name, INVITE_VALID_DAYS)
    # No inviter named: the person who first invited them did not send this one.
    return mail_templates.invite(url, None, t.name, INVITE_VALID_DAYS)


@router.post("/tenants/{slug}/people/resend-idle")
async def resend_idle_invites(request: Request, slug: str, bg: BackgroundTasks,
                              op: PlatformUser = Depends(current_platform_user),
                              s: AsyncSession = Depends(get_session)):
    """Reissue every invite that has sat unaccepted past the idle threshold: the accounts the
    fleet's idle-invites row counts, read from the same rule, so pressing it clears that row."""
    t = await _get(s, slug)
    now = _now()
    facts = await fleet_health.gather(s, t, now)
    idle = {uuid.UUID(p.id) for p in fleet_health.idle_invites(facts.people, now)}
    if not idle:
        raise HTTPException(409, f"No invite has been waiting more than "
                                 f"{fleet_health.IDLE_INVITE_DAYS} days.")
    users = (await s.execute(select(User).where(User.tenant_id == t.id, User.id.in_(idle))
                             .order_by(User.created_at))).scalars().all()
    base = await _workspace_base(s, t)
    mails = []
    for u in users:
        raw = _mint_invite(u)
        _record(s, op, request, t, "user.reinvited", "user", u.id, category="People", email=u.email)
        mails.append((u, _invite_mail(t, u, base, raw)))
    await s.commit()
    for u, mail in mails:
        bg.add_task(mailer.send, u.email, *mail,
                    idempotency_key=f"invite-{u.id}-{u.action_token_expires.isoformat()}")
    return {"sent_to": [u.email for u in users]}


@router.post("/tenants/{slug}/people/{person_id}/resend")
async def resend_person_invite(request: Request, slug: str, person_id: str, bg: BackgroundTasks,
                               op: PlatformUser = Depends(current_platform_user),
                               s: AsyncSession = Depends(get_session)):
    t = await _get(s, slug)
    u = await _person(s, t, person_id)
    if u.status == "active":
        raise HTTPException(409, "They have already accepted their invite. Send a password reset "
                                 "instead.")
    if u.status != "invited":
        raise HTTPException(409, "That account is disabled. Only the workspace can re-enable it.")
    raw = _mint_invite(u)
    mail = _invite_mail(t, u, await _workspace_base(s, t), raw)
    _record(s, op, request, t, "user.reinvited", "user", u.id, category="People", email=u.email)
    await s.commit()
    bg.add_task(mailer.send, u.email, *mail,
                idempotency_key=f"invite-{u.id}-{u.action_token_expires.isoformat()}")
    return {"email": u.email, "invite_expires": _iso(u.action_token_expires)}


@router.post("/tenants/{slug}/people/{person_id}/unlock")
async def unlock_person(request: Request, slug: str, person_id: str,
                        op: PlatformUser = Depends(current_platform_user),
                        s: AsyncSession = Depends(get_session)):
    """Clear a password lockout, and only that.

    A second-factor lockout is left in place: clearing it would hand whoever tripped it a fresh set
    of guesses at somebody's second factor, and nothing about a person's second factor is an
    operator's to change.
    """
    t = await _get(s, slug)
    u = await _person(s, t, person_id)
    if not (u.locked_until and _aware(u.locked_until) > _now()):
        raise HTTPException(409, "That account is not locked out.")
    u.locked_until, u.failed_logins = None, 0
    _record(s, op, request, t, "user.unlocked", "user", u.id, category="People", email=u.email)
    await s.commit()
    return {"email": u.email}


@router.post("/tenants/{slug}/people/{person_id}/reset-link")
async def send_reset_link(request: Request, slug: str, person_id: str, bg: BackgroundTasks,
                          op: PlatformUser = Depends(current_platform_user),
                          s: AsyncSession = Depends(get_session)):
    """Email somebody a password reset link. It goes to them, never to the operator."""
    t = await _get(s, slug)
    u = await _person(s, t, person_id)
    if u.status == "invited":
        raise HTTPException(409, "They have not accepted their invite yet. Resend the invite "
                                 "instead.")
    if u.status != "active":
        raise HTTPException(409, "That account is disabled. Only the workspace can re-enable it.")
    raw, token_hash = new_action_token()
    u.action_token_hash, u.action_token_purpose = token_hash, "reset"
    u.action_token_expires = _now() + dt.timedelta(hours=RESET_HOURS)
    url = f"{await _workspace_base(s, t)}/reset-password?token={raw}"
    _record(s, op, request, t, "user.reset_link", "user", u.id, category="People", email=u.email)
    await s.commit()
    bg.add_task(mailer.send, u.email, *mail_templates.reset(url, t.name, RESET_HOURS))
    return {"email": u.email, "expires_at": _iso(u.action_token_expires)}


@router.post("/tenants/{slug}/share-links/revoke-all")
async def revoke_share_links(request: Request, slug: str, op: PlatformUser = Depends(current_platform_user),
                             s: AsyncSession = Depends(get_session)):
    """Revoke every live public share link in the workspace.

    Suspension does not stop share links, so this is how an operator does. A revoked link reads as
    not found everywhere and cannot be restored; the workspace can make new ones.
    """
    t = await _get(s, slug)
    now = _now()
    rows = (await s.execute(select(ShareLink).where(
        ShareLink.tenant_id == t.id, ShareLink.revoked_at.is_(None)))).scalars().all()
    live = [r for r in rows if r.expires_at is None or fleet_health._aware(r.expires_at) > now]
    if not live:
        raise HTTPException(409, "No share link is live.")
    for r in live:
        r.revoked_at = now
    _record(s, op, request, t, "tenant.share_links_revoked", "tenant", t.id, category="Access",
            count=len(live), scopes=sorted({r.scope for r in live}))
    await s.commit()
    return {"revoked": len(live)}


# ── phase 4: the platform's own trail and health ──────────────────────────────────────────
PROCESS_STARTED = _now()


def _parse_before(before: str | None) -> dt.datetime | None:
    if not before:
        return None
    # A `+00:00` offset pasted into a URL unencoded arrives as a space; read it as the plus it was.
    try:
        return fleet_health._aware(dt.datetime.fromisoformat(before.strip().replace(" ", "+").replace("Z", "+00:00")))
    except ValueError:
        raise HTTPException(400, "`before` must be an ISO 8601 timestamp, as returned in next_before.")


@router.get("/audit")
async def audit_trail(scope: str = "all", operator: str | None = None, tenant: str | None = None,
                      before: str | None = None, limit: int = 100,
                      op: PlatformUser = Depends(current_platform_user),
                      s: AsyncSession = Depends(get_session)):
    """Every change across the platform, newest first.

    `acumyn` is the operator trail, platform_audit. `tenants` is what each workspace's own team
    changed, read from the workspaces' audit logs: entries with a person as the actor, leaving out
    sign-ins and second-factor checks because they are not changes. An operator's change also appears
    in the workspace's log with no actor, and is only ever read from platform_audit, so nothing is
    listed twice. `operator=me` narrows the operator trail to the caller.
    """
    if scope not in ("all", "acumyn", "tenants"):
        raise HTTPException(400, "scope must be all, acumyn or tenants")
    limit = max(1, min(200, limit))
    cutoff = _parse_before(before)
    slug = tenant.strip().lower() if tenant else None
    events: list[dict] = []

    if scope in ("all", "acumyn"):
        q = select(PlatformAudit).order_by(PlatformAudit.created_at.desc()).limit(limit + 1)
        if cutoff is not None:
            q = q.where(PlatformAudit.created_at < cutoff)
        if operator == "me":
            q = q.where(PlatformAudit.operator_id == op.id)
        if slug:
            q = q.where(PlatformAudit.tenant_slug == slug)
        for r in (await s.execute(q)).scalars().all():
            events.append({
                "id": f"p:{r.id}", "at": _iso(r.created_at), "scope": "acumyn",
                "who": r.operator_email, "action": r.action, "summary": "",
                "tenant_slug": r.tenant_slug, "target_type": r.target_type, "target_id": r.target_id,
                "detail": r.detail or {}, "reason": r.reason, "ip": r.ip})

    if scope in ("all", "tenants") and operator != "me":
        q = (select(AuditLog, Tenant.slug, User.email)
             .join(Tenant, Tenant.id == AuditLog.tenant_id)
             .outerjoin(User, User.id == AuditLog.actor_user_id)
             .where(or_(AuditLog.actor_user_id.is_not(None), AuditLog.actor_member_id.is_not(None)),
                    AuditLog.action.not_in(operator_audit.NOT_CHANGES))
             .order_by(AuditLog.created_at.desc()).limit(limit + 1))
        if cutoff is not None:
            q = q.where(AuditLog.created_at < cutoff)
        if slug:
            q = q.where(Tenant.slug == slug)
        for r, tenant_slug, email in (await s.execute(q)).all():
            events.append({
                "id": f"t:{r.id}", "at": _iso(r.created_at), "scope": "tenant",
                "who": email or r.actor_label, "action": r.action,
                "summary": operator_audit.operator_summary(r.action, r.summary),
                "tenant_slug": tenant_slug, "target_type": r.target_type, "target_id": r.target_id,
                "detail": r.detail or {}, "reason": (r.detail or {}).get("reason"), "ip": None})

    events.sort(key=lambda e: e["at"] or "", reverse=True)
    page = events[:limit]
    more = len(events) > limit
    return {"events": page, "next_before": page[-1]["at"] if more and page else None,
            "retention_days": operator_audit.RETENTION_DAYS}


@functools.lru_cache(maxsize=1)
def _code_heads() -> tuple[str, ...]:
    """The migration heads in the deployed code. Read once per process: the files on disk cannot
    change without a new deploy, and a new deploy is a new process."""
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    return tuple(sorted(ScriptDirectory.from_config(cfg).get_heads()))


def _deployed_env_risk() -> bool:
    """ENV left at its development default on a real database: the combination a deployment where
    nobody set ENV produces, which is indistinguishable from a laptop."""
    return (not settings.is_sqlite) and settings.ENV.strip().lower() == "development"


@router.get("/system")
async def system_health(op: PlatformUser = Depends(current_platform_user),
                        s: AsyncSession = Depends(get_session)):
    """What is deployed, and whether the API, the worker, the database and the migrations are well.

    Each figure says where it came from. What the platform does not measure (request latency, error
    rates) is left out rather than estimated.
    """
    now = _now()

    started = time.monotonic()
    await s.execute(text("SELECT 1"))
    database = {"state": "ok", "dialect": "sqlite" if settings.is_sqlite else "postgresql",
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "size_bytes": None, "connections": None}
    if not settings.is_sqlite:
        database["size_bytes"] = (await s.execute(
            text("SELECT pg_database_size(current_database())"))).scalar()
        database["connections"] = (await s.execute(text(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"))).scalar()

    heads = list(_code_heads())
    try:
        applied = sorted((await s.execute(text("SELECT version_num FROM alembic_version"))).scalars().all())
    except Exception:  # noqa: BLE001 — no alembic_version table: a database built without migrations
        await s.rollback()
        applied = []
    if len(heads) > 1:
        mstate, mwhy = "forked", (f"{len(heads)} heads in the code. Two migrations branched from the same "
                                  "parent, and the next deploy will not apply cleanly.")
    elif not applied:
        mstate, mwhy = "unknown", ("The database records no migration version, which is normal for a local "
                                   "database built from the models.")
    elif set(applied) == set(heads):
        mstate, mwhy = "ok", f"One head, and the database is at it ({heads[0]})."
    else:
        mstate, mwhy = "behind", (f"The code's head is {', '.join(heads)} and the database is at "
                                  f"{', '.join(applied)}. Deploys run `alembic upgrade head` first, so "
                                  "this usually means that step failed.")

    beats = {r.job: r for r in (await s.execute(select(JobHeartbeat))).scalars().all()}
    job_rows = []
    for entry in jobs.catalog():
        row = beats.get(entry["job"])
        job_rows.append({**entry, "state": jobs.state_of(entry, row, now),
                         "last_started_at": _iso(row.last_started_at) if row else None,
                         "last_ok_at": _iso(row.last_ok_at) if row else None,
                         "last_failed_at": _iso(row.last_failed_at) if row else None,
                         "last_error": row.last_error if row else None,
                         "last_seconds": row.last_seconds if row else None})
    tick = next(j for j in job_rows if j["job"] == "tick")
    every = settings.SYNC_INTERVAL_MINUTES
    if tick["state"] == "never":
        wstate, wwhy = "never", ("No scheduled job has reported. Either nothing runs the scheduler "
                                 "(RUN_WORKER_IN_API is off and no worker service is deployed) or it "
                                 "has not started since heartbeats were added.")
    elif tick["state"] == "late":
        wstate, wwhy = "late", (f"The sync tick last started {ago_words(now, tick['last_started_at'])} ago "
                                f"and runs every {every} minutes.")
    elif tick["state"] == "check":
        wstate, wwhy = "check", f"The sync tick's latest run failed: {tick['last_error']}"
    else:
        wstate, wwhy = "ok", (f"The sync tick last started {ago_words(now, tick['last_started_at'])} ago, "
                              f"on its {every}-minute schedule.")

    return {
        "read_at": now.isoformat(),
        "release": {
            "commit": (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or "")[:7] or None,
            "branch": os.environ.get("RAILWAY_GIT_BRANCH") or None,
            "message": (os.environ.get("RAILWAY_GIT_COMMIT_MESSAGE") or "").split("\n")[0][:200] or None,
            "service": os.environ.get("RAILWAY_SERVICE_NAME") or None,
            "environment": os.environ.get("RAILWAY_ENVIRONMENT_NAME") or None,
            "started_at": PROCESS_STARTED.isoformat(),
            "env": settings.ENV, "env_risk": _deployed_env_risk(),
        },
        "api": {"state": "ok", "started_at": PROCESS_STARTED.isoformat()},
        "database": database,
        "migrations": {"state": mstate, "why": mwhy, "heads": heads, "head_count": len(heads),
                       "database": applied},
        "worker": {"state": wstate, "why": wwhy, "runs_in_api": settings.RUN_WORKER_IN_API,
                   "jobs": job_rows},
    }


def ago_words(now: dt.datetime, iso: str | None) -> str:
    return fleet_health._span(now - dt.datetime.fromisoformat(iso)) if iso else "never"


@router.get("/system/flags")
async def system_flags(op: PlatformUser = Depends(current_platform_user)):
    """The settings that change behaviour for every workspace at once, with why each is shown.
    Values only: nothing here is a secret, and no secret setting is ever listed."""
    fallback = settings.SINGLE_TENANT_FALLBACK
    return {"flags": [
        {"key": "ENV", "value": settings.ENV, "risk": _deployed_env_risk(),
         "note": ("Defaults to development. A deployment where nobody set it looks exactly like a "
                  "laptop, and production-only safety checks key on it.")},
        {"key": "SINGLE_TENANT_FALLBACK", "value": fallback, "risk": fallback,
         "note": ("When true, a host that matches no workspace resolves to the default workspace, but "
                  "only while a single workspace exists; the second one closes it. Set it false before "
                  "any workspace goes on a custom domain.")},
        {"key": "RUN_WORKER_IN_API", "value": settings.RUN_WORKER_IN_API, "risk": False,
         "note": ("True runs the scheduler inside the API process. False needs a separate worker "
                  "service, or nothing syncs on schedule.")},
        {"key": "SYNC_INTERVAL_MINUTES", "value": settings.SYNC_INTERVAL_MINUTES, "risk": False,
         "note": ("Applies to every workspace; there is no per-workspace cadence. A source reads as "
                  "stale after twice this.")},
        {"key": "ADS_SYNC_INTERVAL_MINUTES", "value": settings.ADS_SYNC_INTERVAL_MINUTES, "risk": False,
         "note": "Deliberately slower than the main sync, because ad-level insights are expensive to pull."},
        {"key": "AI_EMPLOYEES_ENABLED", "value": settings.AI_EMPLOYEES_ENABLED, "risk": False,
         "note": "Gates the AI Employees routers, their two worker jobs and the rail item."},
        {"key": "AI_EMPLOYEES_WRITEBACK_ENABLED", "value": settings.AI_EMPLOYEES_WRITEBACK_ENABLED, "risk": False,
         "note": "Off means approve and export only: nothing an AI employee drafts is written back to Go High Level."},
    ]}


# ── phase 5: Acumyn charging workspaces, through Acumyn's own Stripe account ──────────────
class BillingConfigBody(BaseModel):
    # Each optional so the webhook secret can be added after the key, and billing switched off
    # without re-pasting either. Never returned, by any route.
    secret_key: str | None = None
    webhook_secret: str | None = None
    enabled: bool | None = None


class BillingPatch(BaseModel):
    plan: str | None = None
    # None leaves the budget alone. `token_budget_default: true` removes the workspace's own
    # budget so it follows the platform default again. Zero is a real value: unlimited.
    token_budget: int | None = None
    token_budget_default: bool = False
    billing_contact_email: str | None = None
    po_reference: str | None = None


class CustomerBody(BaseModel):
    email: str | None = None
    trial_days: int | None = None


def _config_out(cfg) -> dict:
    return {"connected": bool(cfg and cfg.secret_key_enc), "enabled": bool(cfg and cfg.enabled),
            "webhook_configured": bool(cfg and cfg.webhook_secret_enc),
            "account_id": cfg.account_id if cfg else None, "account_name": cfg.account_name if cfg else None,
            "livemode": cfg.livemode if cfg else None,
            "updated_by": cfg.updated_by if cfg else None, "updated_at": _iso(cfg.updated_at) if cfg else None,
            "webhook_path": "/api/v1/platform/webhooks/stripe",
            "events": sorted(platform_billing.HANDLED_EVENTS)}


def _record_platform(s: AsyncSession, op: PlatformUser, request: Request, action: str, **detail) -> None:
    """An operator change that belongs to no workspace: written to the operator trail only."""
    s.add(PlatformAudit(operator_id=op.id, operator_email=op.email, action=action, detail=detail,
                        ip=client_ip(request)[:45]))


@router.get("/billing/config")
async def billing_config(op: PlatformUser = Depends(current_platform_user),
                         s: AsyncSession = Depends(get_session)):
    return _config_out(await platform_billing.config(s))


@router.put("/billing/config")
async def set_billing_config(body: BillingConfigBody, request: Request,
                             op: PlatformUser = Depends(current_platform_user),
                             s: AsyncSession = Depends(get_session)):
    """Connect Acumyn's Stripe account, or change or switch off the connection.

    A new secret key is verified against Stripe before it is stored: an account that cannot be read
    is refused rather than saved and discovered broken at the first charge. Keys are stored
    encrypted and never returned.
    """
    cfg = await platform_billing.config(s)
    if cfg is None:
        cfg = PlatformBillingConfig(id=1, enabled=False)
        s.add(cfg)
    changed = []
    if body.secret_key is not None:
        key = body.secret_key.strip()
        mode = platform_billing.mode_of(key)
        if mode is None:
            raise HTTPException(400, "That is not a Stripe secret key. It starts sk_live_ or sk_test_ "
                                     "(or rk_ for a restricted key).")
        try:
            account = await platform_billing.stripe(key, "GET", "/account")
        except platform_billing.StripeError as e:
            raise HTTPException(400, f"Stripe refused that key: {e}")
        cfg.secret_key_enc = enc(key)
        cfg.account_id = (account.get("id") or "")[:64] or None
        profile = account.get("business_profile") or {}
        cfg.account_name = (profile.get("name") or (account.get("settings") or {}).get("dashboard", {}).get("display_name")
                            or account.get("email") or cfg.account_id)
        cfg.account_name = (cfg.account_name or "")[:255] or None
        cfg.livemode = mode == "live"
        cfg.verified_at = _now()
        changed.append("secret_key")
    if body.webhook_secret is not None:
        secret = body.webhook_secret.strip()
        if not secret.startswith("whsec_"):
            raise HTTPException(400, "That is not a webhook signing secret. It starts whsec_.")
        cfg.webhook_secret_enc = enc(secret)
        changed.append("webhook_secret")
    if body.enabled is not None:
        if body.enabled and not cfg.secret_key_enc:
            raise HTTPException(409, "Connect a secret key before switching platform billing on.")
        cfg.enabled = body.enabled
        changed.append("enabled" if body.enabled else "disabled")
    if not changed:
        raise HTTPException(400, "Nothing to change.")
    cfg.updated_by, cfg.updated_at = op.email, _now()
    _record_platform(s, op, request, "billing.config_changed", changed=changed,
                     account_id=cfg.account_id, livemode=cfg.livemode)
    await s.commit()
    return _config_out(cfg)


@router.delete("/billing/config")
async def disconnect_billing(request: Request, op: PlatformUser = Depends(current_platform_user),
                             s: AsyncSession = Depends(get_session)):
    """Forget the account's keys. Mirrored subscriptions and invoices stay, as the last known state."""
    cfg = await platform_billing.config(s)
    if cfg is None or not cfg.secret_key_enc:
        raise HTTPException(409, "Platform billing is not connected.")
    account = cfg.account_id
    cfg.secret_key_enc = cfg.webhook_secret_enc = None
    cfg.enabled = False
    cfg.updated_by, cfg.updated_at = op.email, _now()
    _record_platform(s, op, request, "billing.disconnected", account_id=account)
    await s.commit()
    return _config_out(cfg)


def _subscription_out(row, cfg) -> dict | None:
    if row is None:
        return None
    live = cfg.livemode if cfg else None
    return {
        "status": row.status, "customer_id": row.stripe_customer_id,
        "subscription_id": row.stripe_subscription_id, "price_id": row.stripe_price_id,
        "amount_cents": row.amount_cents, "currency": row.currency, "interval": row.interval,
        "current_period_end": _iso(row.current_period_end), "trial_end": _iso(row.trial_end),
        "cancel_at": _iso(row.cancel_at), "payment_method": row.default_payment_method,
        "payment_method_exp": row.payment_method_exp, "collected_cents": row.collected_cents,
        "billing_contact_email": row.billing_contact_email, "po_reference": row.po_reference,
        "synced_at": _iso(row.synced_at), "monthly_cents": platform_billing.monthly_cents(row),
        "customer_url": platform_billing.dashboard_url(f"customers/{row.stripe_customer_id}", live),
        "subscription_url": (platform_billing.dashboard_url(f"subscriptions/{row.stripe_subscription_id}", live)
                             if row.stripe_subscription_id else None),
    }


@router.get("/tenants/{slug}/billing")
async def tenant_billing(slug: str, op: PlatformUser = Depends(current_platform_user),
                         s: AsyncSession = Depends(get_session)):
    """The Stripe mirror, read-only, beside the four fields Acumyn enforces. Prices appear here
    because this is the operator's billing surface (C3); no tenant-facing response carries one."""
    t = await _get(s, slug)
    cfg = await platform_billing.config(s)
    row = await s.get(PlatformSubscription, t.id)
    invoices = (await s.execute(select(PlatformInvoice).where(PlatformInvoice.tenant_id == t.id)
                                .order_by(PlatformInvoice.created_at.desc()).limit(24))).scalars().all()
    tier = plans.plan_of(t)
    list_cents = plans.PLANS[tier]["price_monthly"] * 100 if plans.PLANS[tier].get("price_monthly") is not None else None
    sub = _subscription_out(row, cfg)
    cfg_budget = (t.config or {}).get("ai_token_budget")
    return {
        "slug": t.slug, "billing": _config_out(cfg) | {"events": None},
        "subscription": sub,
        # C13: a workspace with no Stripe customer is not billed. Internal and not-yet-billed are the
        # same fact until someone decides otherwise.
        "billed": row is not None,
        "invoices": [{"id": i.stripe_invoice_id, "number": i.number, "status": i.status,
                      "amount_due_cents": i.amount_due_cents, "amount_paid_cents": i.amount_paid_cents,
                      "attempt_count": i.attempt_count, "hosted_invoice_url": i.hosted_invoice_url,
                      "created_at": _iso(i.created_at), "paid_at": _iso(i.paid_at)} for i in invoices],
        "enforced": {
            "plan": tier, "plan_set": (t.plan or "").strip().lower() in plans.PLANS,
            "list_price_cents": list_cents,
            "price_differs": bool(sub and sub["amount_cents"] is not None and list_cents is not None
                                  and sub["interval"] == "month" and sub["amount_cents"] != list_cents),
            "token_budget": fleet_health.token_budget_for(t),
            "token_budget_own": cfg_budget is not None,
            "token_budget_platform": settings.AI_EMPLOYEES_TOKEN_BUDGET or None,
            "billing_contact_email": row.billing_contact_email if row else None,
            "po_reference": row.po_reference if row else None,
        },
    }


@router.patch("/tenants/{slug}/billing")
async def patch_tenant_billing(slug: str, body: BillingPatch, request: Request,
                               op: PlatformUser = Depends(current_platform_user),
                               s: AsyncSession = Depends(get_session)):
    """The four fields Acumyn owns. None of them calls Stripe: changing the plan changes what the
    workspace can do on its next request, and does not change what Stripe charges."""
    t = await _get(s, slug)
    row = await s.get(PlatformSubscription, t.id)
    if body.plan is not None:
        plan = body.plan.strip().lower()
        if plan not in plans.PLANS:
            raise HTTPException(400, f"Unknown plan. Choose one of {', '.join(plans.ORDER)}.")
        if plan != (t.plan or "").strip().lower():
            _record(s, op, request, t, "billing.plan_changed", "tenant", t.id, category="Workspace",
                    **{"from": t.plan, "to": plan})
            t.plan = plan
    if body.token_budget_default or body.token_budget is not None:
        cfg = dict(t.config or {})
        before = cfg.get("ai_token_budget")
        if body.token_budget_default:
            cfg.pop("ai_token_budget", None)
        else:
            if body.token_budget < 0:
                raise HTTPException(400, "A token budget cannot be negative. Zero means unlimited.")
            cfg["ai_token_budget"] = body.token_budget
        if cfg.get("ai_token_budget") != before:
            t.config = cfg
            _record(s, op, request, t, "billing.budget_changed", "tenant", t.id, category="AI",
                    **{"from": before, "to": cfg.get("ai_token_budget")})
    for field in ("billing_contact_email", "po_reference"):
        value = getattr(body, field)
        if value is None:
            continue
        if row is None:
            raise HTTPException(409, "This workspace has no Stripe customer, so there is nowhere to keep "
                                     "a billing contact or PO. Create the customer first.")
        value = value.strip()[:255 if field == "billing_contact_email" else 128] or None
        if value != getattr(row, field):
            setattr(row, field, value)
            _record(s, op, request, t, f"billing.{'contact' if field.startswith('billing') else 'po'}_changed",
                    "tenant", t.id, category="Workspace", to=value)
    await s.commit()
    return await tenant_billing(slug, op, s)


async def _billing_key(s: AsyncSession) -> str:
    try:
        return await platform_billing.active_key(s)
    except platform_billing.BillingUnavailable as e:
        raise HTTPException(409, str(e))


@router.post("/tenants/{slug}/billing/customer", status_code=201)
async def create_billing_customer(slug: str, body: CustomerBody, request: Request,
                                  op: PlatformUser = Depends(current_platform_user),
                                  s: AsyncSession = Depends(get_session)):
    """Create the Stripe customer and a subscription on the workspace's plan's price, found by its
    lookup key. The subscription starts incomplete (or trialing), with an open invoice the customer
    pays from Stripe's own page; no card is ever entered here."""
    t = await _get(s, slug)
    key = await _billing_key(s)
    if await s.get(PlatformSubscription, t.id) is not None:
        raise HTTPException(409, "This workspace already has a Stripe customer.")
    owner = (await s.execute(select(User).where(User.tenant_id == t.id, User.role == "owner")
                             .order_by(User.created_at))).scalars().first()
    email = (body.email or (owner.email if owner else "")).strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Give a billing email. The workspace has no owner to default to.")
    tier = plans.plan_of(t)
    lookup = platform_billing.LOOKUP_KEYS[tier]
    try:
        prices = await platform_billing.stripe(key, "GET", "/prices",
                                               params=[("lookup_keys[]", lookup), ("active", "true")])
        if not prices.get("data"):
            raise HTTPException(409, f"Stripe has no active price with the lookup key {lookup}. Create it in "
                                     "Stripe (see OPERATOR-CONSOLE.md) and try again.")
        price = prices["data"][0]
        meta = [("metadata[acumyn_tenant_id]", str(t.id)), ("metadata[acumyn_slug]", t.slug)]
        customer = await platform_billing.stripe(key, "POST", "/customers",
                                                 data=[("email", email), ("name", t.name), *meta])
        sub_data = [("customer", customer["id"]), ("items[0][price]", price["id"]),
                    ("payment_behavior", "default_incomplete"), ("collection_method", "charge_automatically"),
                    ("expand[]", "latest_invoice"), *meta]
        if body.trial_days:
            sub_data.append(("trial_period_days", str(max(1, min(90, body.trial_days)))))
        subscription = await platform_billing.stripe(key, "POST", "/subscriptions", data=sub_data)
    except platform_billing.StripeError as e:
        raise HTTPException(502, f"Stripe refused: {e}")
    s.add(PlatformSubscription(tenant_id=t.id, stripe_customer_id=customer["id"],
                               status=subscription.get("status") or "incomplete",
                               billing_contact_email=email))
    await s.flush()
    await platform_billing.apply_subscription(s, subscription, _now())
    latest = subscription.get("latest_invoice")
    if isinstance(latest, dict):
        latest.setdefault("metadata", {})["acumyn_tenant_id"] = str(t.id)
        await platform_billing.apply_invoice(s, latest)
    _record(s, op, request, t, "billing.customer_created", "subscription", subscription.get("id"),
            category="Workspace", plan=tier, price=lookup)
    await s.commit()
    return await tenant_billing(slug, op, s)


async def _open_invoice(s: AsyncSession, t: Tenant) -> PlatformInvoice | None:
    return (await s.execute(select(PlatformInvoice).where(
        PlatformInvoice.tenant_id == t.id, PlatformInvoice.status == "open")
        .order_by(PlatformInvoice.created_at.desc()).limit(1))).scalar_one_or_none()


@router.post("/tenants/{slug}/billing/payment-link")
async def send_payment_link(slug: str, request: Request, bg: BackgroundTasks,
                            op: PlatformUser = Depends(current_platform_user),
                            s: AsyncSession = Depends(get_session)):
    """Email the billing contact Stripe's own page for the open invoice, where they pay it or add a
    payment method. The link is Stripe's, so no card detail ever passes through Acumyn."""
    t = await _get(s, slug)
    await _billing_key(s)
    row = await s.get(PlatformSubscription, t.id)
    if row is None:
        raise HTTPException(409, "This workspace has no Stripe customer.")
    invoice = await _open_invoice(s, t)
    if invoice is None or not invoice.hosted_invoice_url:
        raise HTTPException(409, "There is no open invoice to pay. Stripe raises one when a trial "
                                 "converts or a period renews; sync from Stripe if you expected one.")
    to = row.billing_contact_email
    if not to:
        raise HTTPException(409, "There is no billing contact to send it to. Set one first.")
    _record(s, op, request, t, "billing.payment_link_sent", "invoice", invoice.stripe_invoice_id,
            category="Workspace", to=to)
    await s.commit()
    bg.add_task(mailer.send, to, *mail_templates.payment_link(invoice.hosted_invoice_url, t.name,
                                                               invoice.amount_due_cents, row.currency),
                idempotency_key=f"paylink-{invoice.stripe_invoice_id}-{_ten_minutes()}")
    return {"sent_to": to, "invoice": invoice.number or invoice.stripe_invoice_id}


@router.post("/tenants/{slug}/billing/retry")
async def retry_billing(slug: str, request: Request, op: PlatformUser = Depends(current_platform_user),
                        s: AsyncSession = Depends(get_session)):
    """Ask Stripe to try the open invoice again, against the payment method on file."""
    t = await _get(s, slug)
    key = await _billing_key(s)
    invoice = await _open_invoice(s, t)
    if invoice is None:
        raise HTTPException(409, "There is no open invoice to retry.")
    try:
        paid = await platform_billing.stripe(key, "POST", f"/invoices/{invoice.stripe_invoice_id}/pay")
    except platform_billing.StripeError as e:
        _record(s, op, request, t, "billing.retry_failed", "invoice", invoice.stripe_invoice_id,
                category="Workspace", error=str(e)[:300])
        await s.commit()
        raise HTTPException(402, f"Stripe could not collect it: {e}")
    paid.setdefault("metadata", {})["acumyn_tenant_id"] = str(t.id)
    await platform_billing.apply_invoice(s, paid)
    _record(s, op, request, t, "billing.retried", "invoice", invoice.stripe_invoice_id,
            category="Workspace", status=paid.get("status"))
    await s.commit()
    return {"status": paid.get("status")}


@router.post("/tenants/{slug}/billing/sync")
async def sync_billing(slug: str, op: PlatformUser = Depends(current_platform_user),
                       s: AsyncSession = Depends(get_session)):
    """Pull this workspace's subscription and invoices from Stripe now."""
    t = await _get(s, slug)
    key = await _billing_key(s)
    try:
        changed = await platform_billing.sync_tenant(s, t, key)
    except platform_billing.StripeError as e:
        raise HTTPException(502, f"Stripe refused: {e}")
    body = await tenant_billing(slug, op, s)
    return body | {"corrected": changed}


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, s: AsyncSession = Depends(get_session)):
    """Stripe's deliveries for Acumyn's own account. NOT operator-gated: Stripe is the caller, and
    the signature is the authentication. Verified against the raw body before anything is parsed;
    an unsigned or wrongly signed request is refused with nothing applied."""
    cfg = await platform_billing.config(s)
    payload = await request.body()
    secret = dec(cfg.webhook_secret_enc) if cfg and cfg.webhook_secret_enc else ""
    if not platform_billing.verify_signature(payload, request.headers.get("stripe-signature"), secret):
        raise HTTPException(400, "Invalid signature")
    try:
        event = json.loads(payload)
    except ValueError:
        raise HTTPException(400, "Invalid payload")
    outcome = await platform_billing.handle_event(s, event)
    return {"received": True, "outcome": outcome}
