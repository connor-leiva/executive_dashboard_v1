"""The ads API's authorization and its empty state. SPEC-ads-module.md Parts 11 and 12.

Authorization is enforced IN THE DATA, not only in the nav. A member without the tab must not
reach a spend figure, and an account id belonging to another workspace must not resolve at all -
the tenant filter is the authorization, so there is nothing there to 403 about.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import AdAccount, Business, Integration, Tenant, User
from app.security import hash_pw, make_token
from app.seed import seed

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token):
    return {"Authorization": f"Bearer {token}"}


async def _owner_token():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


async def _member(email, tabs):
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = User(tenant_id=t.id, email=email, name=email.split("@")[0], password_hash=hash_pw("x"),
                 role="member", status="active", tab_access=tabs, token_version=0)
        s.add(u)
        await s.commit()
        return make_token(u.id, t.id, 0)


async def test_a_member_without_the_ads_tab_gets_no_spend_figure():
    """The gate that matters. Ad spend and, once Phase 3 lands, closed revenue are the numbers a
    member with no business seeing them must not receive - and it must be a 403 rather than an
    empty payload, because an empty payload reads as "no spend" rather than "not for you"."""
    tok = await _member("noads@springb.com", ["portfolio"])
    async with _client() as c:
        r = await c.get("/api/v1/ads?period=30d", headers=_H(tok))
    assert r.status_code == 403
    assert "spend" not in r.text.lower()


async def test_a_member_with_the_tab_is_allowed_through():
    tok = await _member("yesads@springb.com", ["portfolio", "ads"])
    async with _client() as c:
        r = await c.get("/api/v1/ads?period=30d", headers=_H(tok))
    assert r.status_code == 200


async def test_an_unconnected_workspace_gets_a_connect_state_not_an_error():
    """A workspace that has never connected Meta is in a NORMAL state. A 404 here renders as a
    broken tab and generates a support message about a feature that is simply not set up yet."""
    tok = await _owner_token()
    async with _client() as c:
        r = await c.get("/api/v1/ads?period=30d", headers=_H(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is False
    assert "Settings" in body["reason"]          # tells them where to go, not just that it is empty


async def test_the_owner_can_list_accounts_and_gets_an_empty_list():
    tok = await _owner_token()
    async with _client() as c:
        r = await c.get("/api/v1/ads/accounts", headers=_H(tok))
    assert r.status_code == 200 and r.json() == []


async def test_an_account_from_another_workspace_does_not_resolve():
    """Cross-tenant, and the important half is HOW it fails: the tenant filter means the row is
    not found rather than found-and-refused, so the endpoint cannot leak that the id exists."""
    from app.services.provisioning import provision_tenant

    async with SessionLocal() as s:
        r = await provision_tenant(s, slug="adsco", name="Ads Co",
                                   owner_email="owner@adsco.test", hostname="adsco.localhost")
        other_tid = r.tenant_id
        integ = Integration(tenant_id=other_tid, provider="meta_ads", status="connected")
        s.add(integ)
        await s.flush()
        acct = AdAccount(tenant_id=other_tid, integration_id=integ.id, platform="meta",
                         external_id="act_999", name="Someone else's account")
        s.add(acct)
        await s.commit()
        foreign_id = str(acct.id)

    try:
        tok = await _owner_token()               # springb's owner, asking for adsco's account
        async with _client() as c:
            r = await c.get(f"/api/v1/ads?account={foreign_id}", headers=_H(tok))
        assert r.status_code == 404
        assert "act_999" not in r.text
        assert "Someone else" not in r.text
    finally:
        await _remove_tenant(other_tid)


async def _remove_tenant(tenant_id) -> None:
    """Delete a tenant AND everything scoped to it.

    Two reasons this is not just `s.delete(tenant)`:

      * The module-scoped database is SHARED. A second tenant closes the single-tenant fallback
        for every test that runs after this file, so leaving one behind changes global state.
      * SQLITE DOES NOT ENFORCE FOREIGN KEYS by default, so ON DELETE CASCADE does not fire here
        even though it will in Postgres. Deleting only the tenant row leaves its businesses and
        users ORPHANED - and an orphaned owner is still the first row a `select(User)` finds,
        which is how this broke two assistant tests that pass perfectly well on their own.

    So every table carrying a tenant_id is cleared explicitly. Driven off the metadata rather
    than a hand-written list, because a hand-written list is one migration away from being wrong.
    """
    from sqlalchemy import delete as sa_delete

    from app.models import Base

    tables = [t for t in reversed(Base.metadata.sorted_tables) if "tenant_id" in t.c]
    async with SessionLocal() as s:
        for t in tables:
            await s.execute(sa_delete(t).where(t.c.tenant_id == tenant_id))
        tenant = await s.get(Tenant, tenant_id)
        if tenant is not None:
            await s.delete(tenant)
        await s.commit()


async def test_creatives_say_why_revenue_is_missing_rather_than_showing_a_blank_column():
    """Part 4.4. Ad-level revenue needs utm_content={{ad.id}} on the ads AND a matching field in
    the CRM - measured at 0 of 1416 registrations in production. A blank column invites somebody
    to conclude the ads produced nothing."""
    tok = await _owner_token()
    async with _client() as c:
        r = await c.get("/api/v1/ads/creatives?period=30d", headers=_H(tok))
    assert r.status_code == 200
    assert r.json()["revenue_available"] is False


async def test_re_attaching_repairs_the_account_instead_of_refusing():
    """A dead end, found live. The first attach left a row with a null business_id and a null
    timezone; the way to fix that is to attach again; and there is no Remove control in the UI.
    Refusing the duplicate meant the only route out was editing the database by hand.

    Somebody re-submitting the connect form is trying to MEND the connection. That is precisely
    when it has to work.
    """
    from sqlalchemy import select as _select

    from app.models import AdAccount as _AA
    from app.models import Integration as _I
    from app.models import Tenant as _T

    async with SessionLocal() as s:
        t = (await s.execute(_select(_T).where(_T.slug == "springb"))).scalar_one()
        integ = _I(tenant_id=t.id, provider="meta_ads", status="connected")
        s.add(integ)
        await s.flush()
        # The broken shape the live connect produced.
        s.add(_AA(tenant_id=t.id, integration_id=integ.id, platform="meta",
                  external_id="act_repair", name="act_repair", business_id=None,
                  timezone_name=None, last_error="MetaError: something earlier"))
        await s.commit()
        integ_id, before = str(integ.id), None

    tok = await _owner_token()
    async with _client() as c:
        r = await c.post("/api/v1/ads/accounts",
                         json={"integration_id": integ_id, "external_id": "act_repair",
                               "business_key": "springb"}, headers=_H(tok))
    # Meta is unreachable in tests, so ping fails and the attach reports Meta's own words rather
    # than pretending. What must NOT happen is the old flat "already attached" refusal.
    assert "already attached" not in r.text.lower()

    async with SessionLocal() as s:
        row = (await s.execute(_select(_AA).where(_AA.external_id == "act_repair"))).scalar_one()
        assert row is not None, "the existing row was replaced rather than repaired"


async def test_attaching_an_account_requires_an_integration_first():
    """Two steps on purpose: one System User token routinely carries several ad accounts, so the
    token and the account are separate objects."""
    tok = await _owner_token()
    async with _client() as c:
        r = await c.post("/api/v1/ads/accounts",
                         json={"integration_id": "00000000-0000-0000-0000-000000000000",
                               "external_id": "act_1"}, headers=_H(tok))
    assert r.status_code == 404
    assert "Connect Meta Ads first" in r.text
