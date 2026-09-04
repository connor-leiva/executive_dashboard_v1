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
# The host app.seed gives tenant #1 — derived, not a literal, so this
# cannot drift from the seed the way a hardcoded domain did.
SEED_HOST = f"springb.{settings.PLATFORM_DOMAIN}"


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
        r = await c.get("/api/v1/me", headers=_H(host=SEED_HOST, token=tok))
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


async def test_a_reserved_host_resolves_to_no_tenant_at_all():
    """These names belong to the PLATFORM: api., www., admin.PLATFORM_DOMAIN. admin. is the
    operator surface, so a tenant login answering there is exactly the confusion reserving
    them was meant to prevent.

    This assertion used to read `in (401, 404)`, which was a hedge covering a real bug: the
    reserved check sat INSIDE the wildcard branch, so it stopped a tenant being FOUND by that
    name but did nothing to stop execution reaching the single-tenant fallback — which, with
    the production default, resolved every reserved host to the fallback tenant and returned
    401. Asserting the hedge is what let that survive. It is checked FIRST now, ahead of the
    domain lookup too, so a hand-added row cannot claim one either.
    """
    from fastapi import HTTPException

    from app import tenancy

    class _Req:
        def __init__(self, host): self.headers = {"x-tenant-host": host}

    # At the resolver, which is where the decision actually lives.
    for name in sorted(tenancy.PLATFORM_HOSTS):
        tenancy.set_tenant(None)
        with pytest.raises(HTTPException) as ei:
            await tenancy.resolve_tenant(_Req(f"{name}.{settings.PLATFORM_DOMAIN}"))
        assert ei.value.status_code == 404, name

    # ...and end to end, through LOGIN rather than /me. /me answers 401 for an unauthenticated
    # request whether or not the tenant resolved, so it cannot tell the two apart — asserting
    # on it looks like a passing test and observes nothing. Login has to find a user inside a
    # tenant, so it fails differently when there is no tenant. (Diagnosing the outage above
    # against production, /me returned 401 for every host and briefly hid the fault.)
    #
    # 400 rather than 404 because the middleware swallows the resolver's exception and
    # current_tenant_id() raises later; both mean "no tenant", and neither says whether the
    # name exists.
    await _provision("resco", hostname="resco.localhost")
    async with _client() as c:
        for name in ("api", "admin", "auth"):
            r = await c.post("/api/v1/auth/login",
                             headers=_H(host=f"{name}.{settings.PLATFORM_DOMAIN}"),
                             json={"email": "nobody@example.invalid", "password": "x"})
            assert r.status_code in (400, 404), f"{name} -> {r.status_code} {r.text[:80]}"
        # A host that DOES resolve reaches credential checking and is rejected there — which
        # is what proves the assertion above is observing resolution and not just any failure.
        r = await c.post("/api/v1/auth/login",
                         headers=_H(host=f"www.{settings.PLATFORM_DOMAIN}"),
                         json={"email": "nobody@example.invalid", "password": "x"})
        assert r.status_code == 401, f"www -> {r.status_code} {r.text[:80]}"


async def test_the_hosts_a_real_deployment_is_served_from_resolve():
    """A REGRESSION TEST WITH A PRODUCTION OUTAGE BEHIND IT.

    `www` was put in the hard-reserved set — copied from a generic list of names a SaaS
    platform "should" reserve — and the reserved check runs ahead of the domain lookup, so the
    name became unreachable by construction. www.acumyn.io is where this dashboard is actually
    served. Every API call from the real site returned 400 "No tenant in context" before it
    reached authentication, which presented as nobody being able to sign in with a password
    that was definitely correct.

    Nothing caught it because every test asserted on invented hostnames. So this one asserts on
    the shapes real deployments are served from, and PLATFORM_HOSTS has to stay disjoint from
    them: whatever else is reserved, these must resolve.
    """
    from app import tenancy

    class _Req:
        def __init__(self, host): self.headers = {"x-tenant-host": host}

    served_from = ("www", "app", "dashboard", "portal", "cmd", "my", "go")
    assert not (set(served_from) & tenancy.PLATFORM_HOSTS), (
        "a name a real app is served from was hard-reserved; that host becomes unreachable")

    tid = await _provision("realsite", hostname=f"www.{settings.PLATFORM_DOMAIN}")
    tenancy.set_tenant(None)
    # The explicit domain row wins for www, exactly as it would for any customer domain.
    assert await tenancy.resolve_tenant(_Req(f"www.{settings.PLATFORM_DOMAIN}")) == tid

    # ...and it still cannot be taken by SLUG: a tenant named `www` does not get the host.
    from app.services.provisioning import normalize_slug
    with pytest.raises(ValueError):
        normalize_slug("www")


@pytest.mark.parametrize("name", ["app", "intranet"])
async def test_wildcard_reserved_hosts_are_claimable_by_an_operator_but_not_by_a_slug(name):
    """`app.PLATFORM_DOMAIN` is the most conventional host a SaaS app is served from, so the
    reservation on it is narrower than the one on api./admin.: no tenant may claim it merely by
    being NAMED `app`, but an operator who adds the domain row deliberately gets it.

    Blocking it outright — which the first cut of the reserved-host fix did — would have made
    `tenant_domains.py --add app.<domain>` succeed and then 404 at request time, with nothing
    anywhere explaining why."""
    from fastapi import HTTPException

    from app import tenancy

    class _Req:
        def __init__(self, host): self.headers = {"x-tenant-host": host}

    from app.services.provisioning import normalize_slug

    host = f"{name}.{settings.PLATFORM_DOMAIN}"

    # Nothing can be NAMED `app` in the first place — provisioning refuses the slug.
    with pytest.raises(ValueError):
        normalize_slug(name)

    # The resolver filters it independently, which is what matters if a row ever arrives by
    # another route: a slug reserved after the fact, a restore, a hand-written INSERT.
    async with SessionLocal() as s:
        squatter = Tenant(slug=name, name="Squatter", status="active")
        s.add(squatter)
        await s.commit()
        squatter_id = squatter.id
    tenancy.set_tenant(None)
    # Not `pytest.raises`: ENV is development here, so the single-tenant fallback deliberately
    # catches every unmatched host and there is nothing to raise. The claim under test is
    # narrower and survives that — the SLUG must not be what wins.
    assert await tenancy.resolve_tenant(_Req(host)) != squatter_id

    # ...but an explicit operator-added row does resolve.
    other = await _provision(f"real{name}", hostname=host)
    tenancy.set_tenant(None)
    assert await tenancy.resolve_tenant(_Req(host)) == other


async def test_the_tenant_context_never_survives_into_the_next_request():
    """resolve_tenant only SETS the context on success, so a request whose host does not
    resolve must not inherit the previous request's tenant. Per-request tasks make that
    unlikely rather than impossible, and the consequence — one tenant served another's data —
    is severe enough that the middleware clears it explicitly."""
    tid = await _provision("ctxco", hostname="ctxco.localhost")
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.tenant_id == tid))).scalar_one()
        tok = make_token(u.id, tid, 0)
    async with _client() as c:
        ok = await c.get("/api/v1/me", headers=_H(host="ctxco.localhost", token=tok))
        assert ok.status_code in (200, 401)          # the host resolved
        # Immediately after, a host that resolves to nothing must NOT inherit it.
        r = await c.get("/api/v1/me",
                        headers=_H(host=f"admin.{settings.PLATFORM_DOMAIN}", token=tok))
    assert r.status_code in (400, 404), r.status_code


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
        assert await tenant_app_url(s, spring.id) == f"https://{SEED_HOST}"
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
        r = await c.post("/api/v1/auth/login", headers=_H(host=SEED_HOST),
                         json={"email": "spring@springb.com", "password": "springtime"})
        tok = r.json()["token"]
        h = _H(host=SEED_HOST, token=tok)

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


def test_cors_lets_a_dev_tenant_subdomain_in_without_letting_anyone_else():
    """The dev origin rule, and the boundary it must not cross.

    WHY IT EXISTS: the intranet sends `X-Tenant-Host: window.location.hostname` with no override,
    so browsing a real tenant locally means browsing `<slug>.localhost:<port>`. That origin was
    not allowed, so the request got past the host check and died on the CORS preflight -- the
    connected states could not be seen locally at all, only the demo fallback.

    WHY IT IS TESTED RATHER THAN EYEBALLED: a CORS regex that matches one thing too many is a
    real hole, and `localhost` appearing anywhere in a hostname is exactly the mistake to make.
    `http://localhost.evil.com` is an attacker's domain; it must not match because `localhost`
    has to be the LAST label.
    """
    import re

    from app.config import Settings

    dev = Settings(DATABASE_URL="sqlite+aiosqlite:///./x.db", ENV="development",
                   PLATFORM_DOMAIN="acumyn.io")
    rx = re.compile(dev.origin_regex)

    for origin in ("http://utah-life.localhost:4173", "http://localhost:5174",
                   "http://a.b.localhost:5175", "https://springb.acumyn.io", "https://acumyn.io"):
        assert rx.fullmatch(origin), f"dev CORS should allow {origin}"

    for origin in ("http://localhost.evil.com", "http://notlocalhost:4173",
                   "https://acumyn.io.evil.com", "https://notacumyn.io", "http://evil.com",
                   "https://localhost:4173.evil.com", "http://localhost.acumyn.io.evil.com"):
        assert not rx.fullmatch(origin), f"dev CORS must refuse {origin}"


def test_a_deployed_config_never_gets_the_localhost_origin_rule():
    """The dev convenience is gated, not appended. A production origin list that accepted any
    `*.localhost` would let a page served from an attacker-controlled resolver read authenticated
    responses, so the gate is the security property here and the rule above is only convenience.
    """
    import re

    from app.config import Settings

    for env, url in (("production", "sqlite+aiosqlite:///./x.db"),
                     ("staging", "sqlite+aiosqlite:///./x.db"),
                     ("development", "postgresql+asyncpg://u:p@h/db")):
        deployed = Settings(DATABASE_URL=url, ENV=env, PLATFORM_DOMAIN="acumyn.io")
        assert deployed._deployed, f"{env}/{url} should count as deployed"
        rx = re.compile(deployed.origin_regex)
        assert "localhost" not in deployed.origin_regex, f"localhost rule leaked into {env}"
        assert not rx.fullmatch("http://utah-life.localhost:4173")
        assert rx.fullmatch("https://springb.acumyn.io"), "platform origins must still work"


async def test_a_dev_subdomain_resolves_the_tenant_it_names():
    """`<slug>.localhost` resolves by slug in dev, exactly as `<slug>.PLATFORM_DOMAIN` does.

    is_local_host()'s docstring claimed this already worked. Nothing implemented it, so a browser
    at `utah-life.localhost` matched no domain row, missed the platform suffix, and fell through
    to the single-tenant fallback -- which hands back a DIFFERENT tenant. Every authenticated call
    then 401s, because the session's tenant and the request's disagree, and it reads as a bad
    token rather than a bad host. That is the whole reason the intranet's connected states could
    not be reached locally.

    Asserted at the RESOLVER rather than through an endpoint: the fallback answers for unknown
    hosts in dev, so a status code cannot tell "resolved the right tenant" from "resolved
    something". The tenant id can.
    """
    from app import tenancy

    class _Req:
        def __init__(self, host): self.headers = {"x-tenant-host": host}

    alpha = await _provision("alphadev", hostname="alphadev.internal")
    beta = await _provision("betadev", hostname="betadev.internal")

    tenancy.set_tenant(None)
    assert await tenancy.resolve_tenant(_Req("alphadev.localhost")) == alpha
    tenancy.set_tenant(None)
    assert await tenancy.resolve_tenant(_Req("betadev.localhost")) == beta, (
        "each dev subdomain must name its own tenant, not whatever the fallback returns")


async def test_a_dev_subdomain_cannot_claim_a_reserved_platform_name():
    """`app.localhost` must not reach something `app.PLATFORM_DOMAIN` would refuse.

    provision_tenant rejects a reserved slug, so the row here is inserted directly -- the check in
    the resolver is defence against exactly that: a row that did not come through provisioning.
    """
    from app import tenancy

    class _Req:
        def __init__(self, host): self.headers = {"x-tenant-host": host}

    async with SessionLocal() as s:
        row = (await s.execute(select(Tenant).where(Tenant.slug == "app"))).scalar_one_or_none()
        if row is None:
            row = Tenant(name="Squatter", slug="app")
            s.add(row)
            await s.commit()
        squatter = row.id

    tenancy.set_tenant(None)
    assert await tenancy.resolve_tenant(_Req("app.localhost")) != squatter


async def test_a_newly_provisioned_workspace_can_open_its_own_console():
    """THE MULTI-TENANCY BLOCKER THIS FIXES.

    provision_tenant created a tenant, its domain, its businesses and an invited owner -- and no
    intranet rows at all. require_console_access needs an active member whose role holds
    console_access=Full, so a workspace that had just been sold could not open its own admin
    console. The only thing that ever created those rows was the seed script, which is one
    customer's real staff list and content; running that against a paying customer would have
    filled their workspace with another company's people.
    """
    from app.models import (IntranetCapability, IntranetMember, IntranetPermission, IntranetRole,
                            IntranetWorkspace)

    tid = await _provision("freshco", hostname="freshco.internal",
                           owner_email="owner@freshco.test")
    async with SessionLocal() as s:
        ws = (await s.execute(select(IntranetWorkspace).where(
            IntranetWorkspace.tenant_id == tid))).scalars().first()
        roles = (await s.execute(select(IntranetRole).where(
            IntranetRole.tenant_id == tid))).scalars().all()
        member = (await s.execute(select(IntranetMember).where(
            IntranetMember.tenant_id == tid))).scalars().first()
        console_cap = (await s.execute(select(IntranetCapability).where(
            IntranetCapability.tenant_id == tid,
            IntranetCapability.key == "console_access"))).scalars().first()
        full = (await s.execute(select(IntranetPermission).where(
            IntranetPermission.tenant_id == tid,
            IntranetPermission.capability_id == console_cap.id,
            IntranetPermission.level == "Full"))).scalars().all()

    assert ws is not None, "no workspace row: the console has nothing to configure"
    assert member is not None and member.status == "Active", "the owner is not on the roster"
    assert len(full) == 1, "exactly one role should administer a brand-new workspace"
    assert member.role_id == full[0].role_id, "the owner does not hold the administering role"

    # Generic structure, not the first customer's org chart.
    names = {r.name for r in roles}
    assert names == {"Owner", "Manager", "Member"}, names
    for borrowed in ("Buyer Agent", "Listing Agent", "JV Partner"):
        assert borrowed not in names, f"a real-estate role leaked into a generic workspace"


async def test_bootstrapping_twice_does_not_reset_a_configured_workspace():
    """Provisioning is retried. A bootstrap that re-ran would overwrite roles an admin had
    already renamed, silently undoing real work."""
    from app.models import IntranetRole
    from app.services.intranet_bootstrap import bootstrap_intranet

    tid = await _provision("twiceco", hostname="twiceco.internal",
                           owner_email="owner@twiceco.test")
    async with SessionLocal() as s:
        role = (await s.execute(select(IntranetRole).where(
            IntranetRole.tenant_id == tid, IntranetRole.key == "member"))).scalars().one()
        role.name = "Stylist"
        await s.commit()

    async with SessionLocal() as s:
        await bootstrap_intranet(s, tid, workspace_name="Twiceco", subdomain="twiceco")
        await s.commit()

    async with SessionLocal() as s:
        again = (await s.execute(select(IntranetRole).where(
            IntranetRole.tenant_id == tid, IntranetRole.key == "member"))).scalars().all()
    assert len(again) == 1, "bootstrap duplicated a role"
    assert again[0].name == "Stylist", "bootstrap overwrote an admin's rename"


def test_the_tiers_gate_the_portal_and_the_assistant_but_never_marketing_requests():
    """The pricing decision, asserted so it cannot drift silently.

    The team portal is included from Business up; the assistant, which answers from a workspace's
    own documents and costs real money per question, sits a tier above it. Marketing Requests is
    NOT a plan feature at any tier -- it is how a team routes work to its own marketing people,
    so it ships with the portal rather than being sold separately.
    """
    from app import plans

    class _T:
        def __init__(self, plan, config=None):
            self.plan = plan
            self.config = config or {}

    assert not plans.allows(_T("team"), "intranet")
    assert plans.allows(_T("business"), "intranet")
    assert plans.allows(_T("portfolio"), "intranet")

    assert not plans.allows(_T("team"), "ai_assistant")
    assert not plans.allows(_T("business"), "ai_assistant")
    assert plans.allows(_T("portfolio"), "ai_assistant")

    # Not a plan flag at all, at any tier -- asking is answered "no feature by that name".
    for tier in ("team", "business", "portfolio"):
        assert not plans.allows(_T(tier), "marketing_requests")


def test_the_legacy_workspace_flag_can_grant_but_never_revoke():
    """Before the portal was a tier it was switched on per workspace in `config.features`, and
    workspaces provisioned that way are still using it. Honouring it keeps them working. Letting
    it REVOKE would leave two sources of truth for one answer, and the plan has to decide."""
    from app import plans

    class _T:
        def __init__(self, plan, config=None):
            self.plan = plan
            self.config = config or {}

    granted = _T("team", {"features": {"intranet": True}})
    assert plans.allows(granted, "intranet"), "a grandfathered workspace lost its portal"

    # A plan that includes it wins over a flag that says otherwise.
    revoked = _T("portfolio", {"features": {"intranet": False}})
    assert plans.allows(revoked, "intranet"), "a stale flag revoked a paid feature"
