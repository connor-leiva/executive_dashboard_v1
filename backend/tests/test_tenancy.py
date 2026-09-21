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
from tests.fub_fake import fake  # noqa: F401 -- a fixture: FUB is asked before a key is stored

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
            hostname=kw.get("hostname"), businesses=kw.get("businesses"),
            plan=kw.get("plan"))
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
        r = await c.post("/api/v1/auth/login", headers=_H(host="nope.axcion.io"),
                         json={"email": "x@y.z", "password": "nope"})
    assert "nope.axcion.io" not in r.text


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
    name became unreachable by construction. www.axcion.io is where this dashboard is actually
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


async def test_the_bare_apex_is_the_marketing_site_and_resolves_to_no_tenant():
    """The apex serves the marketing site, so it resolves to NO workspace — and nothing made
    that true before. PLATFORM_HOSTS is only ever consulted as `{label}.PLATFORM_DOMAIN`, and the
    apex has no label: `axcion.io` does not end with `.axcion.io`, so it reached neither that
    guard nor the wildcard and fell through to the single-tenant fallback. With one tenant the
    marketing host answered as that customer; with two it began 404-ing by itself, on the day
    the second was provisioned.

    Every spelling below is the same name to DNS and to a browser, which keeps a trailing dot in
    `location.hostname`. They are different STRINGS, and the trailing dot used to walk past every
    guard — api. and admin. included.

    ENV is development here, so the fallback is open and catches any host that gets past the
    guards: a spelling that slipped through would RETURN a tenant instead of raising. That is
    what makes pytest.raises observe the guard rather than an unknown host.
    """
    from fastapi import HTTPException

    from app import tenancy

    class _Req:
        def __init__(self, host): self.headers = {"x-tenant-host": host}

    apex = settings.PLATFORM_DOMAIN
    for host in (apex, apex.upper(), f"{apex}:443", f"{apex}.", f"{apex.upper()}.:443",
                 f"  {apex}  ", f"api.{apex}."):
        tenancy.set_tenant(None)
        with pytest.raises(HTTPException) as ei:
            await tenancy.resolve_tenant(_Req(host))
        assert ei.value.status_code == 404, repr(host)

    # End to end through LOGIN, for the reason test_a_reserved_host_resolves_to_no_tenant_at_all
    # gives: login has to find a user inside a tenant, so it fails differently without one.
    async with _client() as c:
        for host in (apex, f"{apex}."):
            r = await c.post("/api/v1/auth/login", headers=_H(host=host),
                             json={"email": "nobody@example.invalid", "password": "x"})
            assert r.status_code in (400, 404), f"{host!r} -> {r.status_code} {r.text[:80]}"
        # The control: a workspace's host reaches credential checking and is refused THERE.
        r = await c.post("/api/v1/auth/login", headers=_H(host=SEED_HOST),
                         json={"email": "nobody@example.invalid", "password": "x"})
        assert r.status_code == 401, f"{SEED_HOST} -> {r.status_code} {r.text[:80]}"


async def test_the_apex_being_dead_does_not_take_the_wildcard_with_it():
    """The apex check is an EXACT match. Written as a suffix match it would refuse every
    workspace at <slug>.PLATFORM_DOMAIN, which is all of them — so a tenant one label deeper
    still resolves by slug, in the spellings the apex was refused in."""
    from app import tenancy

    class _Req:
        def __init__(self, host): self.headers = {"x-tenant-host": host}

    # The domain row is on another host, so the wildcard name can only resolve by SLUG. Nor can
    # it be the fallback: that returns DEV_TENANT_SLUG, a different tenant, and the equality
    # below tells the two apart.
    tid = await _provision("apexkin", hostname="apexkin.internal")
    host = f"apexkin.{settings.PLATFORM_DOMAIN}"
    for spelling in (host, host.upper(), f"{host}:443", f"{host}.", f"  {host}.  "):
        tenancy.set_tenant(None)
        assert await tenancy.resolve_tenant(_Req(spelling)) == tid, repr(spelling)


async def test_invite_links_use_the_configured_platform_domain(monkeypatch):
    """primary_host is the base for invite and reset links when a tenant has no domain row, and
    it spelled the production domain out literally — so under any other PLATFORM_DOMAIN (staging,
    a renamed platform) those links pointed at production. It follows the setting now, as
    provisioning's tenant_hostname already did."""
    from starlette.requests import Request

    from app.services import users

    async with SessionLocal() as s:
        bare = Tenant(slug="nodomainco", name="No Domain Co", status="active")
        s.add(bare)
        await s.commit()
        tid = bare.id

    monkeypatch.setattr(settings, "PLATFORM_DOMAIN", "axcion-staging.test")
    async with SessionLocal() as s:
        assert await users.primary_host(s, tid) == "nodomainco.axcion-staging.test"
        # ...and through link_base, which the invite and reset routes actually call. With no
        # Origin to prefer (a script, a server-side call) the fallback is the whole answer.
        no_origin = Request({"type": "http", "headers": []})
        assert await users.link_base(no_origin, s, tid) == "https://nodomainco.axcion-staging.test"


async def test_the_domains_script_refuses_the_apex():
    """`tenant_domains.py --add axcion.io --primary` is how the apex became a tenant's primary
    domain in production once, which sent every reset link to a parked page. As the marketing
    site it would be worse: resolve_tenant refuses the host, so the row looks added and never
    works, while tenant_app_url hands it out as the workspace's own origin — every invite,
    reset, share link and QuickBooks return landing on the marketing page."""
    import argparse

    from scripts.tenant_domains import _normalize_host, _run

    apex = settings.PLATFORM_DOMAIN
    for raw in (apex, apex.upper(), f"{apex}.", f"https://{apex}/", f"{apex}:443", f" {apex} "):
        with pytest.raises(SystemExit) as ei:
            _normalize_host(raw)
        assert "platform's own domain" in str(ei.value), repr(raw)

    # Through the command itself, so the refusal is proven wired in and not only written: it
    # has to fire before anything is added.
    args = argparse.Namespace(tenant="springb", add=f"{apex}.", primary=True, force=True,
                              remove=None)
    with pytest.raises(SystemExit):
        await _run(args)
    async with SessionLocal() as s:
        rows = (await s.execute(select(Domain).where(
            Domain.hostname.in_((apex, f"{apex}."))))).scalars().all()
    assert rows == [], "the apex was written as a domain row"

    # A trailing dot no longer walks past the platform-host refusal either...
    with pytest.raises(SystemExit):
        _normalize_host(f"api.{apex}.")
    # ...and the hosts workspaces really are served from still go through, stored the way
    # request_tenant_host will compare them.
    assert _normalize_host(f"https://App.{apex}./") == f"app.{apex}"
    assert _normalize_host("portal.example.com:443") == "portal.example.com"


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


async def test_sisu_and_fub_are_connectable_through_the_api(fake):
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
                   PLATFORM_DOMAIN="axcion.io")
    rx = re.compile(dev.origin_regex)

    for origin in ("http://utah-life.localhost:4173", "http://localhost:5174",
                   "http://a.b.localhost:5175", "https://springb.axcion.io", "https://axcion.io"):
        assert rx.fullmatch(origin), f"dev CORS should allow {origin}"

    for origin in ("http://localhost.evil.com", "http://notlocalhost:4173",
                   "https://axcion.io.evil.com", "https://notaxcion.io", "http://evil.com",
                   "https://localhost:4173.evil.com", "http://localhost.axcion.io.evil.com"):
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
        deployed = Settings(DATABASE_URL=url, ENV=env, PLATFORM_DOMAIN="axcion.io")
        assert deployed._deployed, f"{env}/{url} should count as deployed"
        rx = re.compile(deployed.origin_regex)
        assert "localhost" not in deployed.origin_regex, f"localhost rule leaked into {env}"
        assert not rx.fullmatch("http://utah-life.localhost:4173")
        assert rx.fullmatch("https://springb.axcion.io"), "platform origins must still work"


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


async def test_the_portal_link_appears_only_once_the_portal_exists():
    """Entitlement and existence are different questions, and collapsing them causes a visible
    regression.

    Moving the portal from a per-workspace flag to a plan feature meant every workspace on an
    including plan would have been shown an "Intranet" link the moment that shipped -- leading to
    a portal with no roles, no tiles and no content, and a console that 403s because nobody is on
    its roster. A new app appearing and not working reads as a bug, not as an upsell.
    """
    from app.models import IntranetWorkspace
    from app.routers.auth import _tenant_apps

    tid = await _provision("entitledco", hostname="entitledco.internal",
                           owner_email="owner@entitledco.test")
    async with SessionLocal() as s:
        tenant = await s.get(Tenant, tid)
        tenant.plan = "portfolio"          # a plan that includes the portal
        await s.commit()

    # Bootstrapped by provisioning, so it exists and the link should show.
    async with SessionLocal() as s:
        tenant = await s.get(Tenant, tid)
        owner = (await s.execute(select(User).where(
            User.tenant_id == tid, User.role == "owner"))).scalars().first()
        apps = await _tenant_apps(s, tenant, owner, ["portfolio"])
    assert "intranet" in {a["id"] for a in apps}

    # Now remove the workspace row: entitled, but nothing set up. The link must disappear.
    async with SessionLocal() as s:
        ws = (await s.execute(select(IntranetWorkspace).where(
            IntranetWorkspace.tenant_id == tid))).scalars().one()
        await s.delete(ws)
        await s.commit()
    async with SessionLocal() as s:
        tenant = await s.get(Tenant, tid)
        owner = (await s.execute(select(User).where(
            User.tenant_id == tid, User.role == "owner"))).scalars().first()
        apps = await _tenant_apps(s, tenant, owner, ["portfolio"])
    assert "intranet" not in {a["id"] for a in apps}, (
        "an entitled workspace with no portal was offered a link to one")


async def test_a_portal_member_is_not_offered_a_dashboard_they_cannot_use():
    """The same rule as the Intranet link above, pointed the other way.

    Somebody invited through the console is a portal member -- a buyer agent, an ISA -- and
    carries no dashboard tabs on purpose, because the executive numbers are not theirs. Offering
    them a Dashboard whose every screen is empty reads as the product being broken rather than as
    a permission they were never given.
    """
    from app.routers.auth import _tenant_apps

    tid = await _provision("portalco", hostname="portalco.internal",
                           owner_email="owner@portalco.test", plan="portfolio")
    async with SessionLocal() as s:
        tenant = await s.get(Tenant, tid)
        # An owner: tabs, so both apps.
        owner = (await s.execute(select(User).where(
            User.tenant_id == tid, User.role == "owner"))).scalars().first()
        member = User(tenant_id=tid, email="agent@portalco.test", name="A", password_hash=None,
                      role="member", status="invited", tab_access=[], token_version=0)
        s.add(member)
        await s.flush()

        both = {a["id"] for a in await _tenant_apps(s, tenant, owner, ["portfolio", "flywheel"])}
        assert both == {"dashboard", "intranet"}, both
        # A portal member: no tabs, so the portal only.
        only = {a["id"] for a in await _tenant_apps(s, tenant, member, [])}
        assert only == {"intranet"}, only
        # ...but an OWNER of a workspace that has nothing set up yet keeps the dashboard, because
        # that is where they go to set it up. Redirecting them would lock a new workspace out of
        # its own configuration.
        empty_owner = {a["id"] for a in await _tenant_apps(s, tenant, owner, [])}
    assert "dashboard" in empty_owner, empty_owner


async def test_provisioning_takes_a_plan_and_refuses_one_that_does_not_exist():
    """A workspace created without a plan landed on the column default, `team`, which includes no
    team portal -- so a customer who had just bought the portal got a workspace without it, and
    the failure looked like a bug rather than a tier.

    The typo case matters as much as the happy one: a workspace silently sitting on a plan that
    does not exist would read as unlimited in some gates and empty in others.
    """
    import pytest as _pytest

    from app.services.provisioning import provision_tenant

    tid = await _provision("plannedco", hostname="plannedco.internal",
                           owner_email="owner@plannedco.test", plan="business")
    async with SessionLocal() as s:
        tenant = await s.get(Tenant, tid)
        assert tenant.plan == "business"

    from app import plans
    assert plans.allows(tenant, "intranet"), "the plan it was sold did not grant the portal"

    async with SessionLocal() as s:
        with _pytest.raises(ValueError) as ei:
            await provision_tenant(s, slug="typoco", name="Typo Co",
                                   owner_email="owner@typoco.test", plan="portfolioo")
    assert "Unknown plan" in str(ei.value)


async def test_omitting_the_plan_still_works_for_existing_callers():
    """Optional, not required: the operator console and the CLI both had callers that predate
    this, and breaking them to enforce a decision would trade one failure for another."""
    tid = await _provision("defaultco", hostname="defaultco.internal",
                           owner_email="owner@defaultco.test")
    async with SessionLocal() as s:
        tenant = await s.get(Tenant, tid)
    assert tenant.plan == "team", "the column default should still apply when none is given"


# ── the cutover interlock ─────────────────────────────────────────────────────────────────
# Both of the following are UNSET on their Railway services, so in each case the default
# compiled into the source is not a fallback — it is production's live configuration. That
# makes renaming either one, on its own, a deploy-time outage rather than a code change:
#
#   * PLATFORM_DOMAIN builds the CORS origin regex. Point it at the new domain before the
#     cutover and every workspace's browser is refused by its own API, because ALLOWED_ORIGINS
#     lists only www and the apex. Tenant resolution survives on the `domain` rows; CORS does not.
#   * The Caddy host defaults ARE the web service's content routing. Point them at the new
#     domain and the marketing site and operator console stop being served at the live hosts.
#
# They describe one fact — the domain this deployment is actually serving today — in two
# languages, and during a rebrand it is exactly the kind of fact that gets updated in one place.
# This ties them together so the cutover has to move both or neither.

def test_the_platform_domain_and_the_caddy_hosts_name_the_same_live_domain():
    from pathlib import Path

    from app.config import Settings
    caddy = (Path(__file__).resolve().parents[2] / "frontend" / "Caddyfile").read_text(
        encoding="utf-8")
    # MARKETING_ALT_HOST is deliberately excluded. Its entire job is to name the host being
    # redirected FROM, which during a domain move is the OLD domain -- it is what makes an old
    # marketing or legal link 308 to its new address instead of dying. Holding it to the same
    # rule as the others would forbid the one configuration that keeps old links alive.
    defaults = re.findall(
        r"\{\$(?:MARKETING_HOST|FRONTDOOR_HOST|OPERATOR_HOST):([^}]+)\}", caddy)
    assert defaults, "no Caddy host defaults found — the interlock is not watching anything"

    domain = Settings(DATABASE_URL="sqlite://").PLATFORM_DOMAIN
    wrong = [d for d in defaults if d != domain and not d.endswith("." + domain)]
    assert not wrong, (
        f"PLATFORM_DOMAIN defaults to {domain!r} but the Caddyfile still defaults to {wrong}. "
        "Both are live production config because neither variable is set on its service, so "
        "they move together at the cutover or not at all.")

    # And the third one, which was missed the first two times this net was cast and reached
    # production: the URL the sign-in page's Privacy and Terms links actually point at, and
    # where "Powered by" goes. It is served at MARKETING_HOST, so it moves when that moves.
    # Renamed on its own it sends every workspace's legal links to the dashboard catch-all --
    # a page that returns 200 and looks fine, which is why nothing caught it.
    brand = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "brand"
             / "axcion.jsx").read_text(encoding="utf-8")
    site = re.search(r'AXCION_SITE\s*=\s*"https://([^"]+)"', brand)
    assert site, "AXCION_SITE not found — the interlock is not watching the marketing URL"
    assert site.group(1).endswith(domain), (
        f"AXCION_SITE points at {site.group(1)!r} but the marketing site is served at "
        f"{domain}. The legal links would 200 onto the dashboard shell.")
