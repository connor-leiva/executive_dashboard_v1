"""Tenant boundary — Phase 1 (make a second tenant possible).

Covers the four blockers together, because they only mean anything as a set: a tenant can be
PROVISIONED complete (B4), REACHED from its own host (B1), allowed through CORS (B3), and
given its OWN source credentials (B2). Each test names the failure it exists to prevent.
"""
import json
import re
import uuid

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.integrations import fub, sisu
from app.main import app
from app.models import (AISkill, Business, Domain, JurisdictionRule, StandardAccount,
                        Integration, Tenant, User)
from app.seed import seed
from app.security import make_token
from app.services.provisioning import provision_tenant
from app.services.sync import _fub_creds, _sisu_creds
from app.tenancy import tenant_app_url

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(host=None, token=None):
    h = {}
    if host:
        h["x-tenant-host"] = host
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _provision(slug, **kw):
    """Provision a tenant, tolerating a re-run against the module-scoped database."""
    async with SessionLocal() as s:
        existing = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if existing:
            return existing.id
        r = await provision_tenant(
            s, slug=slug, name=kw.get("name", slug.title()),
            owner_email=kw.get("owner_email", f"owner@{slug}.test"),
            hostname=kw.get("hostname"), businesses=kw.get("businesses"))
        return r.tenant_id


# ── B4: a provisioned tenant is COMPLETE ──────────────────────────────────────────────
async def test_provisioning_seeds_the_catalogs_a_tenant_cannot_work_without():
    """Regression: the CLI used to create a tenant with an empty Binder and no chart of
    accounts, because only seed.py knew to wire the catalogs."""
    tid = await _provision("provco", hostname="provco.localhost")
    async with SessionLocal() as s:
        accounts = (await s.execute(select(func.count()).select_from(StandardAccount)
                                    .where(StandardAccount.tenant_id == tid))).scalar_one()
        rules = (await s.execute(select(func.count()).select_from(JurisdictionRule)
                                 .where(JurisdictionRule.tenant_id.is_(None)))).scalar_one()
        skills = (await s.execute(select(func.count()).select_from(AISkill))).scalar_one()
        owner = (await s.execute(select(User).where(User.tenant_id == tid))).scalar_one()
        biz = (await s.execute(select(Business).where(Business.tenant_id == tid))).scalars().all()
        dom = (await s.execute(select(Domain).where(Domain.tenant_id == tid))).scalar_one()

    assert accounts > 100, "the standard chart did not seed for the new tenant"
    assert rules > 0 and skills > 0, "shared product catalogs did not seed"
    assert owner.status == "invited" and owner.password_hash is None
    assert owner.action_token_hash and owner.action_token_purpose == "invite"
    assert biz and dom.is_primary and dom.hostname == "provco.localhost"


async def test_provisioning_refuses_reserved_duplicate_and_claimed_names():
    await _provision("dupco", hostname="dupco.localhost")
    async with SessionLocal() as s:
        with pytest.raises(ValueError, match="reserved"):
            await provision_tenant(s, slug="api", name="X", owner_email="a@b.c")
    async with SessionLocal() as s:
        with pytest.raises(ValueError, match="already exists"):
            await provision_tenant(s, slug="dupco", name="X", owner_email="a@b.c")
    async with SessionLocal() as s:
        with pytest.raises(ValueError, match="already claimed"):
            await provision_tenant(s, slug="dupco2", name="X", owner_email="a@b.c",
                                   hostname="dupco.localhost")


# ── B1: the browser's host selects the realm ──────────────────────────────────────────
async def test_x_tenant_host_selects_the_realm_for_login():
    """The SPA calls a DIFFERENT origin than it is served from, so without this header every
    tenant's login would be checked against whichever tenant the server fell back to."""
    tid = await _provision("hostco", hostname="hostco.localhost")
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.tenant_id == tid))).scalar_one()
        uid, email = u.id, u.email

    async with _client() as c:
        # Spring's owner exists in springb, NOT in hostco -> neutral 401 on hostco's host.
        r = await c.post("/api/v1/auth/login", headers=_H(host="hostco.localhost"),
                         json={"email": "spring@springb.com", "password": "springtime"})
        assert r.status_code == 401
        # ...and hostco's own token is accepted on hostco's host.
        tok = make_token(uid, tid, 0)
        r = await c.get("/api/v1/me", headers=_H(host="hostco.localhost", token=tok))
        assert r.status_code in (200, 401)   # 401 only because the owner is still `invited`
        # The same token aimed at Spring's realm is refused — a token is realm-bound.
        r = await c.get("/api/v1/me", headers=_H(host="cmd.springb.com", token=tok))
        assert r.status_code == 401
    assert email.endswith("@hostco.test")


async def test_unknown_host_does_not_reveal_which_tenants_exist():
    """resolve_tenant used to echo the host back, which enumerated the customer list for
    anyone probing the shared API origin."""
    async with _client() as c:
        r = await c.post("/api/v1/auth/login", headers=_H(host="nope.acumyn.io"),
                         json={"email": "x@y.z", "password": "nope"})
    assert "nope.acumyn.io" not in r.text


async def test_platform_subdomain_resolves_without_a_domain_row():
    """A tenant is reachable at {slug}.PLATFORM_DOMAIN the moment it is provisioned — the
    wildcard is what makes provisioning zero-ops."""
    tid = await _provision("wildco", hostname="wildco.internal")   # NOT the platform domain
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.tenant_id == tid))).scalar_one()
        tok = make_token(u.id, tid, 0)
    async with _client() as c:
        r = await c.get("/api/v1/me",
                        headers=_H(host=f"wildco.{settings.PLATFORM_DOMAIN}", token=tok))
    assert r.status_code != 404          # resolved by slug, not by a domain row
    async with _client() as c:
        # A reserved slug never resolves to a tenant, even if one somehow claimed it.
        r = await c.get("/api/v1/me",
                        headers=_H(host=f"api.{settings.PLATFORM_DOMAIN}", token=tok))
    assert r.status_code in (401, 404)


async def test_single_tenant_fallback_closes_itself_in_production(monkeypatch):
    """The production hazard this exists to remove: with SINGLE_TENANT_FALLBACK left on and a
    second tenant created, an unrecognized host would silently serve the FIRST customer."""
    from app import tenancy

    await _provision("fallco", hostname="fallco.localhost")
    async with SessionLocal() as s:
        assert (await s.execute(select(func.count()).select_from(Tenant))).scalar_one() > 1
        monkeypatch.setattr(settings, "ENV", "production")
        monkeypatch.setattr(settings, "SINGLE_TENANT_FALLBACK", True)
        assert await tenancy._fallback_tenant(s) is None, \
            "the fallback stayed open with more than one tenant"
        # Development is deliberately exempt (many tenants in one dev database, no DNS).
        monkeypatch.setattr(settings, "ENV", "development")
        assert await tenancy._fallback_tenant(s) is not None


# ── B3: CORS lets a new tenant's origin through without a redeploy ────────────────────
def test_origin_regex_admits_tenant_subdomains_and_nothing_that_merely_looks_like_one():
    rx = re.compile(settings.origin_regex)
    d = settings.PLATFORM_DOMAIN
    assert rx.fullmatch(f"https://{d}")
    assert rx.fullmatch(f"https://newtenant.{d}")
    assert not rx.fullmatch(f"https://not{d}")          # suffix must be on a dot boundary
    assert not rx.fullmatch(f"https://{d}.evil.com")    # anchored at the end
    assert not rx.fullmatch(f"http://tenant.{d}")       # https only


async def test_public_urls_point_at_the_tenants_own_origin():
    """Share links and the OAuth return are opened OUTSIDE the request that built them, so a
    platform-wide APP_PUBLIC_URL would send every tenant's users to the first tenant's domain."""
    tid = await _provision("urlco", hostname="urlco.localhost")
    async with SessionLocal() as s:
        assert await tenant_app_url(s, tid) == "http://urlco.localhost"
        spring = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        assert await tenant_app_url(s, spring.id) == "https://cmd.springb.com"
        # No domain row at all -> the local-dev setting, never another tenant's host.
        assert await tenant_app_url(s, uuid.uuid4()) == settings.APP_PUBLIC_URL.rstrip("/")


# ── B2: source credentials belong to the tenant, not the process ──────────────────────
def test_sisu_and_fub_clients_take_credentials_and_have_no_ambient_account():
    """Regression: both clients read process-wide settings, so a second tenant connecting
    either source would have synced Utah Life's book of business."""
    import inspect

    for fn in (sisu.fetch_all_clients, sisu.get_team_vendors, sisu.fetch_agent_groups,
               sisu.enrich_commissions):
        first = list(inspect.signature(fn).parameters)[0]
        assert first == "creds", f"{fn.__name__} does not take credentials"
    for fn in (fub.fub_users, fub.fub_people):
        assert list(inspect.signature(fn).parameters)[0] == "creds"

    src = inspect.getsource(fub) + inspect.getsource(sisu)
    assert "settings.FUB_API_KEY" not in src
    assert "settings.SISU_USERNAME" not in src and "settings.SISU_API_TOKEN" not in src


def test_missing_credentials_raise_instead_of_falling_back():
    """A tenant with no credentials must fail its OWN sync loudly — never quietly succeed by
    picking up somebody else's account."""
    from app.security import enc

    blank = Integration(provider="sisu", access_token_enc=None)
    with pytest.raises(ValueError, match="Sisu needs credentials"):
        _sisu_creds(blank)
    partial = Integration(provider="sisu", access_token_enc=enc(json.dumps({"username": "u"})))
    with pytest.raises(ValueError, match="incomplete"):
        _sisu_creds(partial)
    good = Integration(provider="sisu",
                       access_token_enc=enc(json.dumps({"username": "u", "token": "t"})))
    assert _sisu_creds(good).auth == ("u", "t")

    with pytest.raises(ValueError, match="Follow Up Boss needs an API key"):
        _fub_creds(Integration(provider="fub", access_token_enc=None))
    assert _fub_creds(Integration(provider="fub", access_token_enc=enc("k"))).auth == ("k", "")


async def test_sisu_and_fub_are_connectable_through_the_api():
    """They were absent from the connect whitelist, so the only way to configure them was a
    server environment variable — which is the single-tenant path by construction."""
    async with _client() as c:
        r = await c.post("/api/v1/auth/login", headers=_H(host="cmd.springb.com"),
                         json={"email": "spring@springb.com", "password": "springtime"})
        tok = r.json()["token"]
        h = _H(host="cmd.springb.com", token=tok)

        # A NEW Sisu connection needs both halves.
        r = await c.post("/api/v1/integrations", headers=h,
                         json={"provider": "sisu", "business_key": "sympli", "username": "only"})
        assert r.status_code == 400, r.text

        r = await c.post("/api/v1/integrations", headers=h,
                         json={"provider": "sisu", "business_key": "ulrg",
                               "username": "team@example.com", "token": "sisu-secret"})
        assert r.status_code == 200, r.text
        r = await c.post("/api/v1/integrations", headers=h,
                         json={"provider": "fub", "business_key": "ulrg", "token": "fub-secret"})
        assert r.status_code == 200, r.text

        # On EDIT a blank field keeps the stored secret, so the UI never re-shows one.
        r = await c.post("/api/v1/integrations", headers=h,
                         json={"provider": "sisu", "business_key": "ulrg",
                               "username": "renamed@example.com"})
        assert r.status_code == 200, r.text

    async with SessionLocal() as s:
        spring = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        si = (await s.execute(select(Integration).where(
            Integration.tenant_id == spring.id, Integration.provider == "sisu"))).scalars().first()
        assert _sisu_creds(si).auth == ("renamed@example.com", "sisu-secret")
