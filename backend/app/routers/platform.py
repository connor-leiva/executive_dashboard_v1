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

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import plans
from ..config import settings
from ..db import get_session
from ..deps import STEP_UP_SCOPES, current_platform_user
from ..models import (AuditLog, Business, Domain, Integration, PlatformUser, ShareLink, SyncRun,
                      Tenant, User)
from ..security import hash_pw, make_platform_token, new_action_token, verify_pw
from ..services import fleet_health, fleet_rollup, mail_templates, mailer, sync_jobs
from ..services.audit import audit
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


def _record(s: AsyncSession, op: PlatformUser, t: Tenant, action: str,
            target_type: str | None = None, target_id=None, *, category: str = "Workspace",
            **detail) -> None:
    """Record an operator's action in the workspace's own audit log.

    A customer can see what Acumyn did to their workspace. actor_user_id stays null, because the
    actor is not a user of this tenant; `by` names the operator, and the label reads as Acumyn
    wherever the workspace lists its trail.
    """
    audit(s, t.id, None, action, target_type, target_id, {"by": op.email, **detail},
          category=category, actor_label=f"{op.name or op.email} (Acumyn)"[:255])


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
async def create_tenant(body: NewTenant, bg: BackgroundTasks,
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
    # operator who did it. actor_user_id stays null — the actor is not a user of this tenant.
    audit(s, r.tenant_id, None, "tenant.created", "tenant", r.tenant_id,
          {"by": op.email, "slug": r.slug, "hostname": r.hostname, "plan": body.plan},
          category="Workspace", actor_label=f"{op.name or op.email} (Acumyn)"[:255])
    await s.commit()
    # Emailed AND returned. The operator keeps the link for the case the customer never sees
    # the mail, which on a brand-new sending domain is the case worth planning for.
    bg.add_task(mailer.send, r.owner_email,
                *mail_templates.owner_invite(r.invite_url, body.name, INVITE_VALID_DAYS))
    return {"slug": r.slug, "hostname": r.hostname, "owner_email": r.owner_email,
            "invite_url": r.invite_url, "catalogs": r.catalogs}


@router.post("/tenants/{slug}/suspend")
async def suspend_tenant(slug: str, body: SuspendBody,
                         op: PlatformUser = Depends(current_platform_user),
                         s: AsyncSession = Depends(get_session)):
    """Suspend a workspace: no new logins, every live session in it stops working, and the
    scheduled sync skips it. Share links keep serving until revoked separately."""
    reason = (body.reason or "").strip()[:500]
    if not reason:
        raise HTTPException(400, "Give a reason. It is recorded and shown with the suspension.")
    t = await _get(s, slug)
    t.status = "suspended"
    _record(s, op, t, "tenant.suspended", "tenant", t.id, reason=reason)
    await s.commit()
    return {"slug": t.slug, "status": t.status}


@router.post("/tenants/{slug}/resume")
async def resume_tenant(slug: str, op: PlatformUser = Depends(current_platform_user),
                        s: AsyncSession = Depends(get_session)):
    t = await _get(s, slug)
    t.status = "active"
    _record(s, op, t, "tenant.resumed", "tenant", t.id)
    await s.commit()
    return {"slug": t.slug, "status": t.status}


@router.post("/tenants/{slug}/resend-invite")
async def resend_owner_invite(slug: str, bg: BackgroundTasks,
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
    _record(s, op, t, "tenant.invite_resent", "user", owner.id, category="People",
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
         "detail": r.detail, "category": r.category, "summary": r.summary,
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
    return {"rollup": _rollup(rows), "triage": _triage(rows), "read_at": now.isoformat()}


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
async def sync_tenant(slug: str, bg: BackgroundTasks,
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
    _record(s, op, t, "tenant.sync_requested", "tenant", t.id, category="Integrations",
            sources=configured)
    await s.commit()
    bg.add_task(sync_jobs.run_all_job, t.id, run.id)
    return {"job_id": str(run.id), "sources": configured}


@router.post("/tenants/{slug}/sources/{source_id}/sync", status_code=202)
async def sync_source(slug: str, source_id: str, bg: BackgroundTasks,
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
    _record(s, op, t, "tenant.sync_requested", "integration", integ.id, category="Integrations",
            provider=integ.provider)
    await s.commit()
    bg.add_task(sync_jobs.run_one_job, t.id, integ.id)
    return {"provider": integ.provider, "provider_name": name}


@router.post("/tenants/{slug}/sources/{source_id}/reconnect-link")
async def send_reconnect_link(slug: str, source_id: str, bg: BackgroundTasks,
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
    _record(s, op, t, "tenant.reconnect_link_sent", "integration", integ.id,
            category="Integrations", provider=integ.provider, sent_to=sent_to)
    await s.commit()
    window = _ten_minutes()
    for u in admins:
        bg.add_task(mailer.send, u.email, *mail_templates.reconnect_source(url, t.name, name, business),
                    idempotency_key=f"reconnect-{integ.id}-{u.id}-{window}")
    return {"sent_to": sent_to, "url": url, "provider_name": name}


@router.post("/tenants/{slug}/sources/setup-link")
async def send_setup_link(slug: str, bg: BackgroundTasks,
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
    _record(s, op, t, "tenant.setup_link_sent", "tenant", t.id, category="Integrations",
            sent_to=sent_to)
    await s.commit()
    window = _ten_minutes()
    for u in admins:
        bg.add_task(mailer.send, u.email, *mail_templates.connect_first_source(url, t.name),
                    idempotency_key=f"setup-{t.id}-{u.id}-{window}")
    return {"sent_to": sent_to, "url": url}


@router.post("/tenants/{slug}/freeze-syncs")
async def freeze_syncs(slug: str, body: FreezeBody,
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
    _record(s, op, t, "tenant.syncs_frozen", "tenant", t.id, category="Integrations", reason=reason)
    await s.commit()
    return {"slug": t.slug, "syncs_frozen": True}


@router.post("/tenants/{slug}/unfreeze-syncs")
async def unfreeze_syncs(slug: str, op: PlatformUser = Depends(current_platform_user),
                         s: AsyncSession = Depends(get_session)):
    t = await _get(s, slug)
    if not sync_jobs.syncs_frozen(t.config):
        raise HTTPException(409, "Syncs are not frozen for this workspace.")
    cfg = dict(t.config or {})
    cfg.pop("syncs_frozen", None)
    t.config = cfg
    _record(s, op, t, "tenant.syncs_unfrozen", "tenant", t.id, category="Integrations")
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
async def resend_idle_invites(slug: str, bg: BackgroundTasks,
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
        _record(s, op, t, "user.reinvited", "user", u.id, category="People", email=u.email)
        mails.append((u, _invite_mail(t, u, base, raw)))
    await s.commit()
    for u, mail in mails:
        bg.add_task(mailer.send, u.email, *mail,
                    idempotency_key=f"invite-{u.id}-{u.action_token_expires.isoformat()}")
    return {"sent_to": [u.email for u in users]}


@router.post("/tenants/{slug}/people/{person_id}/resend")
async def resend_person_invite(slug: str, person_id: str, bg: BackgroundTasks,
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
    _record(s, op, t, "user.reinvited", "user", u.id, category="People", email=u.email)
    await s.commit()
    bg.add_task(mailer.send, u.email, *mail,
                idempotency_key=f"invite-{u.id}-{u.action_token_expires.isoformat()}")
    return {"email": u.email, "invite_expires": _iso(u.action_token_expires)}


@router.post("/tenants/{slug}/people/{person_id}/unlock")
async def unlock_person(slug: str, person_id: str,
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
    _record(s, op, t, "user.unlocked", "user", u.id, category="People", email=u.email)
    await s.commit()
    return {"email": u.email}


@router.post("/tenants/{slug}/people/{person_id}/reset-link")
async def send_reset_link(slug: str, person_id: str, bg: BackgroundTasks,
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
    _record(s, op, t, "user.reset_link", "user", u.id, category="People", email=u.email)
    await s.commit()
    bg.add_task(mailer.send, u.email, *mail_templates.reset(url, t.name, RESET_HOURS))
    return {"email": u.email, "expires_at": _iso(u.action_token_expires)}


@router.post("/tenants/{slug}/share-links/revoke-all")
async def revoke_share_links(slug: str, op: PlatformUser = Depends(current_platform_user),
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
    _record(s, op, t, "tenant.share_links_revoked", "tenant", t.id, category="Access",
            count=len(live), scopes=sorted({r.scope for r in live}))
    await s.commit()
    return {"revoked": len(live)}
