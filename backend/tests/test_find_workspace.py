"""The workspace finder — POST /api/v1/auth/find-workspace, the form on app.acumyn.io.

A workspace is its own host, so somebody who has lost the address has nowhere to sign in. This
endpoint emails them the list. The EMAIL is the security property: an endpoint that answered
with the list would turn a public form into a customer-list lookup, one brokerage address at a
time. So the response is one constant whatever the address matched, and everything that varies
goes to the mailbox — which only the owner of the address can read.

Nothing here reaches Resend: `mailer._post` is replaced, as in test_mailer.py.
"""
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app import tenancy
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import AuditLog, Domain, Tenant, User
from app.seed import seed
from app.services import mailer

TRANSPORT = ASGITransport(app=app)
OK = {"ok": True}
# Where the finder is served. It resolves to no tenant by design (tenancy.WILDCARD_RESERVED).
FRONT_DOOR = f"app.{settings.PLATFORM_DOMAIN}"


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _capture(monkeypatch):
    calls = []

    async def fake_post(payload, headers):
        calls.append(payload)
        return 200, "{}"
    monkeypatch.setattr(mailer, "_post", fake_post)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(settings, "MAIL_FROM", "Acumyn <hello@mail.acumyn.io>")
    monkeypatch.setattr(settings, "MAIL_REPLY_TO", "")
    return calls


async def _workspace(slug, name, *, host="", status="active"):
    """A tenant with, by default, a primary domain row at `host`. Built directly rather than
    provisioned: this file is about who is listed and where the link points, and provisioning's
    catalog seeding is a lot of work to learn neither."""
    async with SessionLocal() as s:
        t = Tenant(slug=slug, name=name, status=status)
        s.add(t)
        await s.flush()
        if host:
            s.add(Domain(tenant_id=t.id, hostname=host, is_primary=True))
        await s.commit()
        return t.id


async def _member(tid, email, *, status="active"):
    async with SessionLocal() as s:
        s.add(User(tenant_id=tid, email=email, name=email.split("@")[0], role="member",
                   status=status, tab_access=[], token_version=0))
        await s.commit()


async def _find(c, email, host=FRONT_DOOR):
    return await c.post("/api/v1/auth/find-workspace", json={"email": email},
                        headers={"x-tenant-host": host})


async def test_an_address_in_one_workspace_is_emailed_its_address(monkeypatch):
    calls = _capture(monkeypatch)
    tid = await _workspace("findone", "Find One Realty", host="findone.brokerage.test")
    await _member(tid, "solo@findone.test")

    async with _client() as c:
        r = await _find(c, "  Solo@FindOne.test ")      # as somebody actually types it

    assert r.status_code == 200 and r.json() == OK
    assert len(calls) == 1
    mail = calls[0]
    assert mail["to"] == ["solo@findone.test"], "the lookup and the send must use one spelling"
    assert "https://findone.brokerage.test" in mail["text"]
    assert "https://findone.brokerage.test" in mail["html"]
    assert "Find One Realty" in mail["text"]


async def test_an_address_in_several_workspaces_is_emailed_all_of_them(monkeypatch):
    """One address can hold an account in several tenants (the unique constraint is per tenant).
    They get ONE email naming every workspace — not one email per workspace — and each tenant
    gets its own audit row, so a lookup is visible from inside every workspace it touched."""
    calls = _capture(monkeypatch)
    zeta = await _workspace("findzeta", "Zeta Group", host="zeta.brokerage.test")
    alpha = await _workspace("findalpha", "Alpha Homes", host="alpha.brokerage.test")
    await _member(zeta, "both@twice.test")
    await _member(alpha, "both@twice.test", status="invited")   # invited still signs in (Google)

    async with _client() as c:
        r = await _find(c, "both@twice.test")

    assert r.status_code == 200 and r.json() == OK
    assert len(calls) == 1, "one email listing both, not one per workspace"
    text = calls[0]["text"]
    assert "https://zeta.brokerage.test" in text and "https://alpha.brokerage.test" in text
    assert text.index("Alpha Homes") < text.index("Zeta Group"), "sorted by workspace name"

    async with SessionLocal() as s:
        rows = (await s.execute(select(AuditLog).where(
            AuditLog.action == "auth.find_workspace",
            AuditLog.tenant_id.in_((zeta, alpha))))).scalars().all()
    assert {row.tenant_id for row in rows} == {zeta, alpha}


async def test_an_unknown_address_gets_the_same_response(monkeypatch):
    """The enumeration guard. Byte-identical, not merely equal-looking: a difference in key
    order, whitespace or a header-driven encoding is still a difference a script can read."""
    calls = _capture(monkeypatch)
    tid = await _workspace("findreal", "Real Workspace", host="real.brokerage.test")
    await _member(tid, "known@real.test")

    async with _client() as c:
        known = await _find(c, "known@real.test")
        unknown = await _find(c, "nobody.at.all@nowhere.test")

    assert known.status_code == unknown.status_code == 200
    assert known.content == unknown.content
    assert known.headers.get("content-type") == unknown.headers.get("content-type")

    # Behind the identical answer, the unknown address is still told so — in its own mailbox.
    assert [m["to"] for m in calls] == [["known@real.test"], ["nobody.at.all@nowhere.test"]]
    nothing = calls[1]
    assert "brokerage.test" not in nothing["text"] and "brokerage.test" not in nothing["html"]


async def test_a_disabled_user_is_not_listed(monkeypatch):
    """An administrator turned the account off. It must not be handed a way back to the door."""
    calls = _capture(monkeypatch)
    live = await _workspace("findlive", "Live Team", host="live.brokerage.test")
    gone = await _workspace("findgone", "Gone Team", host="gone.brokerage.test")
    await _member(live, "mixed@status.test")
    await _member(gone, "mixed@status.test", status="disabled")

    async with _client() as c:
        await _find(c, "mixed@status.test")

    text = calls[0]["text"]
    assert "live.brokerage.test" in text
    assert "gone.brokerage.test" not in text and "Gone Team" not in text


async def test_a_suspended_workspace_is_not_listed(monkeypatch):
    calls = _capture(monkeypatch)
    ok = await _workspace("findopen", "Open Workspace", host="open.brokerage.test")
    shut = await _workspace("findshut", "Shut Workspace", host="shut.brokerage.test",
                            status="suspended")
    await _member(ok, "two@places.test")
    await _member(shut, "two@places.test")

    async with _client() as c:
        await _find(c, "two@places.test")

    text = calls[0]["text"]
    assert "open.brokerage.test" in text
    assert "shut.brokerage.test" not in text and "Shut Workspace" not in text


async def test_the_endpoint_works_on_a_host_that_resolves_to_no_tenant(monkeypatch):
    """THE REGRESSION TEST THAT MATTERS. The finder is served from app.<platform domain>, which
    resolves to no workspace — so a single current_tenant_id() anywhere in this route would 400
    every lookup, and nothing else in the suite would notice, because the dev fallback quietly
    resolves unknown hosts to the dev tenant.

    So the fallback is closed first, as it is in production, and a tenant-scoped route on the
    same host is shown to fail. That control is what proves the lookup below really ran with no
    tenant rather than borrowing one.
    """
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "SINGLE_TENANT_FALLBACK", False)

    # Production has no domain row for this host (DEPLOY.md says never to add one), but the test
    # database is shared across modules, and test_tenancy deliberately gives app.<domain> to an
    # operator-chosen tenant. Set those rows aside for the length of this test.
    async with SessionLocal() as s:
        parked = [(d.tenant_id, d.is_primary) for d in (await s.execute(
            select(Domain).where(Domain.hostname == FRONT_DOOR))).scalars()]
        for d in (await s.execute(select(Domain).where(Domain.hostname == FRONT_DOOR))).scalars():
            await s.delete(d)
        await s.commit()
    try:
        class _Req:
            headers = {"x-tenant-host": FRONT_DOOR}

        from fastapi import HTTPException
        tenancy.set_tenant(None)
        with pytest.raises(HTTPException):
            await tenancy.resolve_tenant(_Req())

        calls = _capture(monkeypatch)
        tid = await _workspace("findnohost", "No Host Needed", host="nohost.brokerage.test")
        await _member(tid, "anyone@nohost.test")

        async with _client() as c:
            control = await c.post("/api/v1/auth/forgot-password",
                                   json={"email": "anyone@nohost.test"},
                                   headers={"x-tenant-host": FRONT_DOOR})
            assert control.status_code == 400, "the host resolved a tenant; this test observes nothing"

            r = await _find(c, "anyone@nohost.test")
        assert r.status_code == 200 and r.json() == OK
        assert len(calls) == 1 and "https://nohost.brokerage.test" in calls[0]["text"]
    finally:
        async with SessionLocal() as s:
            for tenant_id, is_primary in parked:
                s.add(Domain(tenant_id=tenant_id, hostname=FRONT_DOOR, is_primary=is_primary))
            await s.commit()


async def test_the_workspace_url_comes_from_the_domain_row_not_the_slug(monkeypatch):
    """A workspace on its own domain is linked there. And one with no row at all is linked at
    its platform subdomain, which the wildcard resolves by slug — never at APP_PUBLIC_URL, which
    is this finder: an email answering "where is my workspace?" with a link back to the form
    that sent it is a loop."""
    calls = _capture(monkeypatch)
    custom = await _workspace("findcustom", "Custom Domain Co", host="portal.customdomain.test")
    bare = await _workspace("findbare", "No Row Co")
    await _member(custom, "where@domain.test")
    await _member(bare, "where@domain.test")

    async with _client() as c:
        await _find(c, "where@domain.test")

    text = calls[0]["text"]
    assert "https://portal.customdomain.test" in text
    assert f"findcustom.{settings.PLATFORM_DOMAIN}" not in text
    assert f"https://findbare.{settings.PLATFORM_DOMAIN}" in text
    assert settings.APP_PUBLIC_URL not in text


async def test_a_workspace_name_with_html_in_it_is_escaped(monkeypatch):
    """Workspace names are typed by customers and land in HTML."""
    calls = _capture(monkeypatch)
    name = '<img src=x onerror="alert(1)"> & Sons'
    tid = await _workspace("findhtml", name, host="html.brokerage.test")
    await _member(tid, "escape@html.test")

    async with _client() as c:
        await _find(c, "escape@html.test")

    html = calls[0]["html"]
    assert "<img" not in html and 'onerror="' not in html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt; &amp; Sons" in html
    assert name in calls[0]["text"], "the plain-text part is not HTML and is not escaped"


async def test_repeated_lookups_are_throttled(monkeypatch):
    """Five lookups per address in five minutes: a person looks up their own workspace once.

    And the budget belongs to the caller's ADDRESS alone. Every other throttled route also keys
    on the tenant host, so one workspace cannot spend another's allowance — but that host is
    whatever the caller writes in X-Tenant-Host, and for a route that serves no workspace it is
    not a partition, it is a reset: a made-up host per request would be a fresh budget per
    request, and the limit would stop nobody who read this file.
    """
    _capture(monkeypatch)
    async with _client() as c:
        for i in range(5):
            assert (await _find(c, f"spray{i}@targets.test")).status_code == 200
        assert (await _find(c, "spray5@targets.test")).status_code == 429
        rotated = await _find(c, "spray6@targets.test", host=f"made-up-{6}.{settings.PLATFORM_DOMAIN}")
        assert rotated.status_code == 429, "a new X-Tenant-Host bought a new budget"
