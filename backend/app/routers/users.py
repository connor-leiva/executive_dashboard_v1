import datetime as dt
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..db import get_session
from .. import plans
from ..deps import require_role
from ..models import User, Tenant, Domain
from ..schemas import InviteRequest, UserUpdate, UserOut
from ..security import new_action_token
from ..services import binder_storage
from ..services.audit import audit
from ..services.tabs import tenant_tabs, effective_tabs
from ..services.users import (assert_can_manage, assert_grantable_role, assert_not_last_owner)

router = APIRouter(tags=["users"])

INVITE_DAYS = 7
RESET_HOURS = 24


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _user_out(u: User, all_tabs: list[str]) -> UserOut:
    implicit = u.role in ("owner", "admin")
    return UserOut(
        id=str(u.id), name=u.name, email=u.email, role=u.role, status=u.status,
        tabs=list(all_tabs) if implicit else effective_tabs(u, all_tabs),
        all_tabs=implicit,
        last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
    )


async def _primary_host(s, tenant_id) -> str:
    # Pick the primary domain, but tolerate a tenant that has several domains
    # flagged primary (real setups often add both an app host and an api host):
    # order primary-first and take one, rather than scalar_one_or_none() which
    # RAISES on >1 row and would 500 the whole invite after the user is committed.
    d = (await s.execute(select(Domain).where(Domain.tenant_id == tenant_id)
                         .order_by(Domain.is_primary.desc()))).scalars().first()
    if d:
        return d.hostname
    t = (await s.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    return f"{t.slug}.acumyn.io"


async def _link_base(request: Request, s, tenant_id) -> str:
    """Base URL for invite/reset links. Prefer the origin the admin is actually
    using — that host is, by definition, serving a working frontend — over the
    stored primary-domain row, which may be a custom domain that isn't live yet
    (a dead link there just loads a broken page for the invitee)."""
    origin = (request.headers.get("origin") or "").rstrip("/")
    if origin:
        return origin
    return f"https://{await _primary_host(s, tenant_id)}"


async def _get_target(s, tenant_id, user_id) -> User:
    u = (await s.execute(select(User).where(
        User.id == user_id, User.tenant_id == tenant_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "Unknown user")     # 404, never a 403-with-existence-leak
    return u


def _clean_tabs(role: str, tab_access, all_tabs: list[str]) -> list | None:
    """owner/admin → NULL (implicit all); member → validated subset (≥1)."""
    if role in ("owner", "admin"):
        return None
    grants = [t for t in (tab_access or []) if t in all_tabs]
    if not grants:
        raise HTTPException(400, "A member needs at least one tab")
    return grants


@router.get("/users", response_model=list[UserOut])
async def list_users(user: User = Depends(require_role("owner", "admin")), s: AsyncSession = Depends(get_session)):
    all_tabs = await tenant_tabs(s, user.tenant_id)
    rows = (await s.execute(select(User).where(User.tenant_id == user.tenant_id)
                            .order_by(User.created_at))).scalars().all()
    return [_user_out(u, all_tabs) for u in rows]


@router.get("/tenant/tabs")
async def tenant_tab_vocab(user: User = Depends(require_role("owner", "admin")), s: AsyncSession = Depends(get_session)):
    return {"tabs": await tenant_tabs(s, user.tenant_id)}


SEED_KEYS = ("brand", "surface", "ink", "positive", "negative")
# Mirrors frontend/src/typefaces.js. Deliberately a NAME LIST rather than the stacks themselves:
# the browser owns what each pairing resolves to, and this owns which names are legitimate. A
# workspace supplying its own font-family string would be injecting CSS into every page.
TYPEFACES = ("acumyn", "classic", "neutral", "editorial")
_HEX = __import__("re").compile(r"^#[0-9A-Fa-f]{6}$")


@router.get("/settings/appearance")
async def get_appearance(user: User = Depends(require_role("owner", "admin")),
                         s: AsyncSession = Depends(get_session)):
    """This workspace's chosen seeds, and whether its plan lets it choose at all."""
    tenant = await s.get(Tenant, user.tenant_id)
    brand = ((tenant.config or {}).get("brand") or {})
    return {"seeds": brand.get("seeds") or {},
            "typeface": brand.get("typeface") or "acumyn",
            "logo": brand.get("logo"), "logomark": brand.get("logomark"),
            "allowed": plans.allows(tenant, "custom_branding"),
            "plan": plans.describe(tenant)}


@router.patch("/settings/appearance")
async def set_appearance(body: dict, user: User = Depends(require_role("owner", "admin")),
                         s: AsyncSession = Depends(get_session)):
    """Save the five seeds. The other twenty-five tokens are derived in the browser from these,
    so what is stored is the CHOICE rather than its consequences — a workspace that picked five
    colours a year ago still gets today's derivation rather than a frozen copy of the old one.

    WHAT IS VALIDATED HERE, AND WHAT IS NOT. Structure is: the five known keys, real six-digit
    hex, nothing else stored. Contrast is not, and that is deliberate rather than an oversight —
    the colour maths lives in the browser, and a second implementation in Python would be two
    implementations drifting apart with no test able to catch it. The contrast rules are a
    usability guardrail, not an access control: the only workspace harmed by an unreadable
    palette is the one that chose it. The UI refuses to save a failing combination and says why.
    """
    tenant = await s.get(Tenant, user.tenant_id)
    if not plans.allows(tenant, "custom_branding"):
        lim = plans.limits(tenant)
        raise HTTPException(402, f"Custom branding is not included in the {lim['name']} plan.")

    # The typeface is a named pairing, never a font stack: accepting arbitrary CSS here would let
    # a workspace inject a font-family string into every page it renders.
    typeface = body.get("typeface")
    if typeface is not None and typeface not in TYPEFACES:
        raise HTTPException(400, f"Unknown typeface {typeface!r}. "
                                 f"Expected one of: {', '.join(sorted(TYPEFACES))}.")

    seeds = body.get("seeds")
    if seeds is None:
        seeds = {}
    if not isinstance(seeds, dict):
        raise HTTPException(400, "Expected a `seeds` object.")
    unknown = set(seeds) - set(SEED_KEYS)
    if unknown:
        raise HTTPException(400, f"Unknown colour(s): {', '.join(sorted(unknown))}. "
                                 f"Expected: {', '.join(SEED_KEYS)}.")
    clean = {}
    for key, value in seeds.items():
        if value in (None, ""):
            continue                       # dropping a seed returns it to the platform default
        if not isinstance(value, str) or not _HEX.match(value.strip()):
            raise HTTPException(400, f"{key} must be a colour like #3F6B66, got {value!r}.")
        clean[key] = value.strip().upper()

    cfg = dict(tenant.config or {})
    brand = dict(cfg.get("brand") or {})
    brand["seeds"] = clean
    if typeface is not None:
        brand["typeface"] = typeface
    # The derived palette is NOT stored. Storing it would freeze a workspace's colours against the
    # derivation that happened to exist the day they saved, and every later improvement to the
    # ramps would reach new workspaces only.
    brand.pop("palette", None)
    cfg["brand"] = brand
    tenant.config = cfg
    flag_modified(tenant, "config")
    audit(s, user.tenant_id, user.id, "brand.appearance_changed", "tenant", tenant.id,
          {"seeds": sorted(clean)})
    await s.commit()
    return {"seeds": clean, "typeface": brand.get("typeface")}


# A workspace's mark. Deliberately small: these render at 46px in a hero watermark and 28px in
# the rail, so anything larger is bytes on every page load for detail nobody sees.
MAX_LOGO_BYTES = 512 * 1024
LOGO_TYPES = {"image/png": ".png", "image/svg+xml": ".svg", "image/jpeg": ".jpg",
              "image/webp": ".webp"}
LOGO_KINDS = ("logo", "logomark")


@router.post("/settings/appearance/logo")
async def upload_logo(kind: str = Form("logo"), file: UploadFile = File(...),
                      user: User = Depends(require_role("owner", "admin")),
                      s: AsyncSession = Depends(get_session)):
    """Replace this workspace's wordmark or logomark.

    Stored through the same R2 path the Binder and AI Employees use, so there is one storage
    implementation rather than a second one written for images. The ref is scoped to the tenant,
    and the SERVING route never accepts a ref from the caller — it resolves the tenant from the
    host and streams whatever that workspace's own config points at, so this cannot become a way
    to read somebody else's object by guessing a key.

    Marks are rendered as CSS masks over a coloured box (see Brand.jsx), which is why a
    transparent-ground PNG or SVG works best and why no colourway is asked for: one file tints to
    whatever the palette says.
    """
    tenant = await s.get(Tenant, user.tenant_id)
    if not plans.allows(tenant, "custom_branding"):
        lim = plans.limits(tenant)
        raise HTTPException(402, f"Custom branding is not included in the {lim['name']} plan.")
    if kind not in LOGO_KINDS:
        raise HTTPException(400, f"kind must be one of: {', '.join(LOGO_KINDS)}")

    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in LOGO_TYPES:
        raise HTTPException(400, f"Use a PNG, SVG, JPEG or WebP. Got {ctype or 'no type'}.")
    data = await file.read()
    if not data:
        raise HTTPException(400, "That file is empty.")
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(413, f"Keep the mark under {MAX_LOGO_BYTES // 1024}KB — it renders at "
                                 f"46px, so anything bigger is weight on every page load.")

    # Content-addressed, so replacing a mark never serves a stale cached copy under an old name.
    digest = binder_storage.content_hash(data)[:16]
    ref = f"brand/{user.tenant_id}/{kind}-{digest}{LOGO_TYPES[ctype]}"
    binder_storage.put(ref, data, ctype)

    cfg = dict(tenant.config or {})
    brand = dict(cfg.get("brand") or {})
    brand[kind] = f"/api/v1/public/brand/{kind}?v={digest}"
    brand[f"{kind}_ref"] = ref
    cfg["brand"] = brand
    tenant.config = cfg
    flag_modified(tenant, "config")
    audit(s, user.tenant_id, user.id, "brand.logo_uploaded", "tenant", tenant.id,
          {"kind": kind, "bytes": len(data), "type": ctype})
    await s.commit()
    return {"kind": kind, "url": brand[kind]}


@router.delete("/settings/appearance/logo")
async def clear_logo(kind: str = "logo", user: User = Depends(require_role("owner", "admin")),
                     s: AsyncSession = Depends(get_session)):
    """Remove a mark. The workspace falls back to rendering its own NAME as a wordmark, which is
    always correct and never somebody else's logo."""
    if kind not in LOGO_KINDS:
        raise HTTPException(400, f"kind must be one of: {', '.join(LOGO_KINDS)}")
    tenant = await s.get(Tenant, user.tenant_id)
    cfg = dict(tenant.config or {})
    brand = dict(cfg.get("brand") or {})
    brand.pop(kind, None)
    brand.pop(f"{kind}_ref", None)
    cfg["brand"] = brand
    tenant.config = cfg
    flag_modified(tenant, "config")
    audit(s, user.tenant_id, user.id, "brand.logo_cleared", "tenant", tenant.id, {"kind": kind})
    await s.commit()
    return {"kind": kind, "url": None}


@router.post("/users/invite")
async def invite_user(body: InviteRequest, request: Request,
                      user: User = Depends(require_role("owner", "admin")),
                      s: AsyncSession = Depends(get_session)):
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "A valid email is required")
    assert_grantable_role(user, body.role)
    exists = (await s.execute(select(User).where(
        User.tenant_id == user.tenant_id, User.email == email))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "A user with that email already exists in this tenant")
    tenant = await s.get(Tenant, user.tenant_id)
    # Counts INVITED as well as active: an invitation is a seat somebody is expected to take, and
    # a limit that only counts accepted users is a limit you get around by never accepting.
    have = (await s.execute(select(func.count()).select_from(User).where(
        User.tenant_id == user.tenant_id, User.status != "disabled"))).scalar_one()
    if plans.over_limit(tenant, "max_users", have):
        lim = plans.limits(tenant)
        raise HTTPException(402, f"The {lim['name']} plan includes {lim['max_users']} users and "
                                 f"this workspace has {have}. Upgrade to invite another.")
    all_tabs = await tenant_tabs(s, user.tenant_id)
    grants = _clean_tabs(body.role, body.tab_access, all_tabs)

    raw, th = new_action_token()
    u = User(tenant_id=user.tenant_id, email=email, name=email.split("@")[0][:200],
             password_hash=None, role=body.role, status="invited", tab_access=grants,
             token_version=0, invited_by=user.id, action_token_hash=th,
             action_token_purpose="invite", action_token_expires=_now() + dt.timedelta(days=INVITE_DAYS))
    s.add(u)
    await s.flush()
    audit(s, user.tenant_id, user.id, "user.invited", "user", u.id, {"role": body.role})
    await s.commit()
    base = await _link_base(request, s, user.tenant_id)
    return {"user": _user_out(u, all_tabs),
            "invite_url": f"{base}/accept-invite?token={raw}"}


@router.post("/users/{user_id}/resend-invite")
async def resend_invite(user_id: uuid.UUID, request: Request,
                        user: User = Depends(require_role("owner", "admin")),
                        s: AsyncSession = Depends(get_session)):
    u = await _get_target(s, user.tenant_id, user_id)
    assert_can_manage(user, u)
    if u.status != "invited":
        raise HTTPException(400, "This user has already accepted their invite")
    raw, th = new_action_token()
    u.action_token_hash = th
    u.action_token_purpose = "invite"
    u.action_token_expires = _now() + dt.timedelta(days=INVITE_DAYS)
    audit(s, user.tenant_id, user.id, "user.reinvited", "user", u.id)
    await s.commit()
    base = await _link_base(request, s, user.tenant_id)
    return {"invite_url": f"{base}/accept-invite?token={raw}"}


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: uuid.UUID, body: UserUpdate,
                      user: User = Depends(require_role("owner", "admin")), s: AsyncSession = Depends(get_session)):
    u = await _get_target(s, user.tenant_id, user_id)
    assert_can_manage(user, u)
    all_tabs = await tenant_tabs(s, user.tenant_id)
    if body.name is not None:
        u.name = body.name.strip()[:200] or u.name

    new_role = body.role if body.role is not None else u.role
    if body.role is not None and body.role != u.role:
        if u.id == user.id:
            raise HTTPException(403, "You can't change your own role")
        assert_grantable_role(user, body.role)
        # demoting an owner must not drop the last one
        if u.role == "owner" and new_role != "owner":
            await assert_not_last_owner(s, user.tenant_id, u)
        detail = {"from": u.role, "to": new_role}
        u.role = new_role
        audit(s, user.tenant_id, user.id, "user.role_changed", "user", u.id, detail)

    # tab_access: revalidate for the effective role (nulled for owner/admin)
    if body.tab_access is not None or body.role is not None:
        u.tab_access = _clean_tabs(u.role, body.tab_access if body.tab_access is not None else u.tab_access, all_tabs)
    await s.commit()
    return _user_out(u, all_tabs)


@router.post("/users/{user_id}/disable", response_model=UserOut)
async def disable_user(user_id: uuid.UUID, user: User = Depends(require_role("owner", "admin")),
                       s: AsyncSession = Depends(get_session)):
    u = await _get_target(s, user.tenant_id, user_id)
    assert_can_manage(user, u)
    if u.id == user.id:
        raise HTTPException(403, "You can't disable yourself")
    await assert_not_last_owner(s, user.tenant_id, u)
    u.status = "disabled"
    u.token_version = (u.token_version or 0) + 1        # instantly invalidate sessions
    # ...and any outstanding invite/reset link, which is a credential too. Bumping
    # token_version only kills issued SESSIONS; a reset link minted minutes earlier is a
    # separate path back in, and it survived the disable.
    u.action_token_hash = u.action_token_purpose = u.action_token_expires = None
    audit(s, user.tenant_id, user.id, "user.disabled", "user", u.id)
    await s.commit()
    return _user_out(u, await tenant_tabs(s, user.tenant_id))


@router.post("/users/{user_id}/enable", response_model=UserOut)
async def enable_user(user_id: uuid.UUID, user: User = Depends(require_role("owner", "admin")),
                      s: AsyncSession = Depends(get_session)):
    u = await _get_target(s, user.tenant_id, user_id)
    assert_can_manage(user, u)
    # A never-accepted invite goes back to 'invited'; an accepted user to 'active'.
    u.status = "invited" if u.password_hash is None else "active"
    audit(s, user.tenant_id, user.id, "user.enabled", "user", u.id)
    await s.commit()
    return _user_out(u, await tenant_tabs(s, user.tenant_id))


@router.post("/users/{user_id}/reset-link")
async def reset_link(user_id: uuid.UUID, request: Request,
                     user: User = Depends(require_role("owner", "admin")),
                     s: AsyncSession = Depends(get_session)):
    u = await _get_target(s, user.tenant_id, user_id)
    assert_can_manage(user, u)
    raw, th = new_action_token()
    u.action_token_hash = th
    u.action_token_purpose = "reset"
    u.action_token_expires = _now() + dt.timedelta(hours=RESET_HOURS)
    audit(s, user.tenant_id, user.id, "user.reset_link", "user", u.id)
    await s.commit()
    base = await _link_base(request, s, user.tenant_id)
    return {"reset_url": f"{base}/reset-password?token={raw}"}
