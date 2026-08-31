"""Scoping the whole ads page to one launch. SPEC-ads-module.md Parts 9.5 and 10.2.

Spring runs one campaign per launch - `KB - The Shift - August2026` IS the August launch - so
"what did this launch convert?" is a campaign filter, and it has to narrow the headline figures,
the funnel and the creative wall TOGETHER. A funnel scoped to one launch beside spend for the
whole account puts a wrong CAC on screen, presented as a right one.
"""
import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import (AdAccount, AdAttribution, AdCampaign, AdConversion, AdInsightDaily,
                        Business, Integration, Tenant)
from app.seed import seed

TRANSPORT = ASGITransport(app=app)
TODAY = dt.date.today()


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


async def _token():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def _fixture():
    """Two campaigns with different spend, and one attributed person per campaign who converted.

    Reuse-or-create: (tenant_id, external_id) is unique on ad_account, so a helper that always
    inserts works for one test and fails the rest of the file.
    """
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        # A business with the "program" archetype, because that is what FUNNEL_DEFS keys on -
        # borrowing a seeded business meant the funnel assertion silently SKIPPED, which is the
        # test quietly not running rather than passing.
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == t.id, Business.key == "scopeprog"))).scalar_one_or_none()
        if biz is None:
            biz = Business(tenant_id=t.id, key="scopeprog", name="Scope Program",
                           tag="scope", kind="membership", archetype="program",
                           include_in_portfolio=False)
            s.add(biz)
            await s.flush()
        acct = (await s.execute(select(AdAccount).where(
            AdAccount.tenant_id == t.id,
            AdAccount.external_id == "act_scope"))).scalar_one_or_none()
        if acct is None:
            integ = Integration(tenant_id=t.id, provider="meta_ads", status="connected")
            s.add(integ)
            await s.flush()
            acct = AdAccount(tenant_id=t.id, integration_id=integ.id, platform="meta",
                             external_id="act_scope", name="Scope", timezone_name="UTC",
                             business_id=biz.id)
            s.add(acct)
            await s.flush()

            for ext, name, spend in (("c_aug", "KB - The Shift - August2026", 900.0),
                                     ("c_jul", "KB - The Shift - July2026", 300.0)):
                camp = AdCampaign(tenant_id=t.id, ad_account_id=acct.id,
                                  external_id=ext, name=name)
                s.add(camp)
                await s.flush()
                s.add(AdInsightDaily(
                    tenant_id=t.id, ad_account_id=acct.id, level="campaign",
                    object_external_id=ext, campaign_id=camp.id,
                    occurred_on=TODAY - dt.timedelta(days=2), spend=spend,
                    impressions=1000, clicks=50, inline_link_clicks=40, leads=5))
                attr = AdAttribution(
                    tenant_id=t.id, identity_kind="ghl_contact", identity_key=f"person_{ext}",
                    business_id=biz.id, ad_account_id=acct.id,
                    campaign_id=camp.id, match_method="campaign", confidence="probable",
                    channel="Meta",                       # NOT NULL on the model
                    first_seen_on=TODAY - dt.timedelta(days=2),
                    last_seen_on=TODAY - dt.timedelta(days=2))
                s.add(attr)
                await s.flush()
                s.add(AdConversion(
                    tenant_id=t.id, attribution_id=attr.id,
                    business_id=biz.id, stage_key="registered",
                    source_kind="bc_shift_reg", source_ref=f"reg_{ext}",   # both NOT NULL
                    occurred_on=TODAY - dt.timedelta(days=2), dated=True))
        await s.commit()
        camps = {c.name: str(c.id) for c in (await s.execute(select(AdCampaign).where(
            AdCampaign.ad_account_id == acct.id))).scalars()}
        return str(acct.id), camps


async def test_the_whole_page_narrows_together():
    """Spend, the funnel and the scope label all move to the one launch. If spend stayed
    account-wide while the funnel narrowed, cost-per-anything would be silently overstated."""
    acct, camps = await _fixture()
    aug = camps["KB - The Shift - August2026"]
    h = await _token()
    async with _client() as c:
        whole = (await c.get(f"/api/v1/ads?account={acct}&period=30d", headers=h)).json()
        one = (await c.get(f"/api/v1/ads?account={acct}&period=30d&campaign={aug}",
                           headers=h)).json()

    assert whole["totals"]["spend"] == 1200.0
    assert one["totals"]["spend"] == 900.0, "headline spend did not narrow with the funnel"
    assert len(one["campaigns"]) == 1


async def test_the_payload_names_its_scope():
    """Same rule as `basis`. A launch-scoped number under an account-wide heading is a wrong
    decision, not a cosmetic slip - the page has to be able to say which it is showing."""
    acct, camps = await _fixture()
    aug = camps["KB - The Shift - August2026"]
    h = await _token()
    async with _client() as c:
        whole = (await c.get(f"/api/v1/ads?account={acct}&period=30d", headers=h)).json()
        one = (await c.get(f"/api/v1/ads?account={acct}&period=30d&campaign={aug}",
                           headers=h)).json()
    assert whole["scope"]["kind"] == "account"
    assert one["scope"]["kind"] == "campaign"
    assert one["scope"]["name"] == "KB - The Shift - August2026"


async def test_the_picker_still_offers_every_campaign_once_one_is_chosen():
    """THE ONE-WAY DOOR. campaigns_available was first built from the DISPLAYED campaigns, so
    selecting a launch left the picker offering only that launch and no route back to the others
    or to the account. Built from the unfiltered rows instead."""
    acct, camps = await _fixture()
    aug = camps["KB - The Shift - August2026"]
    h = await _token()
    async with _client() as c:
        one = (await c.get(f"/api/v1/ads?account={acct}&period=30d&campaign={aug}",
                           headers=h)).json()
    names = {c["name"] for c in one["campaigns_available"]}
    assert names == {"KB - The Shift - August2026", "KB - The Shift - July2026"}
    assert [c["name"] for c in one["campaigns_available"]][0] == "KB - The Shift - August2026", \
        "the picker should lead with the biggest spender"


async def test_the_funnel_counts_only_that_launchs_people():
    acct, camps = await _fixture()
    aug, jul = camps["KB - The Shift - August2026"], camps["KB - The Shift - July2026"]
    h = await _token()
    async with _client() as c:
        both = (await c.get(f"/api/v1/ads?account={acct}&period=30d", headers=h)).json()
        one = (await c.get(f"/api/v1/ads?account={acct}&period=30d&campaign={aug}",
                           headers=h)).json()
        other = (await c.get(f"/api/v1/ads?account={acct}&period=30d&campaign={jul}",
                             headers=h)).json()

    def registered(payload):
        # `funnel` IS the rung list on this payload, not an object wrapping one.
        rungs = payload.get("funnel") or []
        return next((r["n"] for r in rungs if r["key"] == "registered"), None)

    assert registered(both) is not None, "the funnel did not build; the archetype is wrong"
    assert registered(both) == 2
    assert registered(one) == 1 and registered(other) == 1


async def test_a_campaign_from_another_workspace_does_not_resolve():
    """Cross-tenant, and the shape of the failure matters: the tenant filter means the row is not
    found rather than found-and-refused, so the endpoint cannot confirm the id exists."""
    from app.services.provisioning import provision_tenant

    acct, _ = await _fixture()
    async with SessionLocal() as s:
        r = await provision_tenant(s, slug="scopeco", name="Scope Co",
                                   owner_email="owner@scopeco.test", hostname="scopeco.localhost")
        other = r.tenant_id
        integ = Integration(tenant_id=other, provider="meta_ads", status="connected")
        s.add(integ)
        await s.flush()
        a2 = AdAccount(tenant_id=other, integration_id=integ.id, platform="meta",
                       external_id="act_theirs", name="Theirs")
        s.add(a2)
        await s.flush()
        camp = AdCampaign(tenant_id=other, ad_account_id=a2.id,
                          external_id="c_theirs", name="Their Secret Launch")
        s.add(camp)
        await s.commit()
        foreign = str(camp.id)

    try:
        h = await _token()
        async with _client() as c:
            resp = await c.get(f"/api/v1/ads?account={acct}&period=30d&campaign={foreign}",
                               headers=h)
        assert resp.status_code == 404
        assert "Their Secret Launch" not in resp.text
    finally:
        from app.models import Base
        async with SessionLocal() as s:
            for tbl in [t for t in reversed(Base.metadata.sorted_tables) if "tenant_id" in t.c]:
                await s.execute(sa_delete(tbl).where(tbl.c.tenant_id == other))
            tt = await s.get(Tenant, other)
            if tt is not None:
                await s.delete(tt)
            await s.commit()
