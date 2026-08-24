from contextvars import ContextVar
import uuid

from fastapi import Request, HTTPException
from sqlalchemy import func, select

from .config import settings
from .db import SessionLocal
from .models import Domain, Tenant

_current_tenant: ContextVar[uuid.UUID | None] = ContextVar("current_tenant", default=None)

# Slugs no tenant may claim (they name platform hosts under PLATFORM_DOMAIN).
RESERVED_SLUGS = {"api", "www", "app", "admin", "staging", "auth", "static", "assets"}


def is_local_host(hostname: str) -> bool:
    """A hostname that is only reachable on this machine, so its URLs are http not https.
    Covers `localhost`, any `*.localhost` (a tenant subdomain in dev resolves there without
    touching /etc/hosts on most modern resolvers), and raw loopback addresses."""
    h = (hostname or "").lower()
    return h == "localhost" or h.endswith(".localhost") or h in ("127.0.0.1", "::1")


def url_scheme(hostname: str) -> str:
    return "http" if is_local_host(hostname) else "https"


def current_tenant_id() -> uuid.UUID:
    tid = _current_tenant.get()
    if tid is None:
        raise HTTPException(400, "No tenant in context")
    return tid


def set_tenant(tid: uuid.UUID | None) -> None:
    _current_tenant.set(tid)


def request_tenant_host(request: Request) -> str:
    """The hostname this request is claiming to be for.

    The SPA is served from the tenant's own host but calls the API on a DIFFERENT origin
    (see frontend/api.js), so the API's `Host` header names the API, not the tenant. The
    browser therefore declares its own hostname on `X-Tenant-Host` and that wins.

    This is not a weaker signal than `Host`: on a public API both are equally attacker-
    supplied — anyone can curl with any Host they like. Selecting a realm is not the same
    as entering it. What actually guards the boundary sits downstream: `deps.current_user`
    requires the token's `tid` to equal the resolved tenant, and `auth.login` returns a
    neutral error plus a 10-strike lockout, so an unauthenticated caller learns nothing by
    pointing at someone else's realm.
    """
    host = request.headers.get("x-tenant-host") or request.headers.get("host", "")
    return host.split(":")[0].strip().lower()


async def _dev_tenant(s) -> uuid.UUID | None:
    t = (await s.execute(
        select(Tenant).where(Tenant.slug == settings.DEV_TENANT_SLUG))).scalar_one_or_none()
    return t.id if t else None


async def _fallback_tenant(s) -> uuid.UUID | None:
    """The single-tenant fallback — which closes itself in production.

    A Host matching no `domain` row resolves to DEV_TENANT_SLUG. That is what lets a Railway
    subdomain, a localhost dev server and a preview build work before custom domains are
    wired. It is only SAFE while one tenant exists: with two, guessing would serve one
    customer another's data.

    So in production the TENANT COUNT is the real gate, not the flag. Provisioning a second
    tenant disables the fallback by itself — no config change to remember, no deploy to
    forget, and no window where an unrecognized host quietly returns the first customer's
    dashboard. The flag remains as an earlier off switch.

    Development is exempt from the count, deliberately: dev and the test suite routinely
    hold several tenants in one database with no DNS in front of them, and the data there
    is nobody's. The exemption is keyed to ENV, so it cannot follow a build into prod.
    """
    if settings.ENV == "development":
        return await _dev_tenant(s)
    if not settings.SINGLE_TENANT_FALLBACK:
        return None
    if (await s.execute(select(func.count()).select_from(Tenant))).scalar_one() > 1:
        return None
    return await _dev_tenant(s)


async def resolve_tenant(request: Request) -> uuid.UUID:
    """Resolve the tenant for this request, or 404.

    Order: an exact `domain` row (custom domains and the provisioned {slug}.PLATFORM_DOMAIN
    both live there) -> the {slug}.PLATFORM_DOMAIN wildcard -> the single-tenant fallback.
    """
    host = request_tenant_host(request)
    suffix = "." + settings.PLATFORM_DOMAIN.lower()
    async with SessionLocal() as s:
        row = (await s.execute(select(Domain).where(Domain.hostname == host))).scalar_one_or_none()
        if row:
            _current_tenant.set(row.tenant_id)
            return row.tenant_id
        # Wildcard: {slug}.PLATFORM_DOMAIN resolves by slug, so a tenant works the moment it
        # is provisioned. An exact domain row above still wins; reserved slugs never resolve.
        if host.endswith(suffix):
            slug = host[: -len(suffix)]
            if slug and slug not in RESERVED_SLUGS:
                t = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
                if t:
                    _current_tenant.set(t.id)
                    return t.id
        tid = await _fallback_tenant(s)
        if tid:
            _current_tenant.set(tid)
            return tid
    # Do not echo the host back: on a shared API host anyone can probe arbitrary names, and
    # a message that distinguishes "unknown" from "known" enumerates the customer list.
    raise HTTPException(404, "Not found")


async def tenant_app_url(s, tenant_id: uuid.UUID) -> str:
    """The tenant's OWN web origin, for any URL a human will click.

    Share links, rep desk links and the OAuth return all get handed to somebody outside the
    request that built them, so they cannot use a single platform-wide APP_PUBLIC_URL — that
    would send every tenant's users to the first tenant's domain. Resolution order: the
    tenant's primary `domain` row, then any domain row, then APP_PUBLIC_URL as the local-dev
    fallback (where there is no domain row and one tenant).
    """
    row = (await s.execute(
        select(Domain).where(Domain.tenant_id == tenant_id)
        .order_by(Domain.is_primary.desc()))).scalars().first()
    if not row:
        return settings.APP_PUBLIC_URL.rstrip("/")
    return f"{url_scheme(row.hostname)}://{row.hostname}"
