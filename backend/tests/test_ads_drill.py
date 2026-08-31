"""Opening a funnel rung. SPEC-ads-module.md Part 15.

Every rung is a count of PEOPLE, and a count nobody can open is a count nobody can check. The
rule that matters most is that the drill must open the SAME population the figure was computed
from - a drill that resolves its own window silently answers a different question than the one
that was clicked, which is worse than having no drill at all.
"""
import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import (AdAccount, AdAttribution, AdCampaign, AdConversion, AdInsightDaily,
                        Business, Integration, MetricRecord, Tenant, User)
from app.security import hash_pw, make_token
from app.seed import seed

TRANSPORT = ASGITransport(app=app)
TODAY = dt.date.today()
ACCOUNT_EXT = "act_drill"
BUSINESS_KEY = "drillprog"
RECENT = TODAY - dt.timedelta(days=3)
OLD = TODAY - dt.timedelta(days=200)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


@pytest.fixture(scope="module", autouse=True)
async def _cleanup(_seeded):
    """Remove what this file added, AFTER it runs.

    The database is shared across the whole suite. These fixtures write ad accounts,
    attributions and closed conversions carrying contract values into the springb tenant, and a
    file that leaves those behind changes what every later file counts - it broke an unrelated
    annualization test that passes perfectly well on its own. SQLite does not enforce the foreign
    keys that would cascade this in Postgres, so every table is cleared explicitly.
    """
    yield
    async with SessionLocal() as s:
        acct = (await s.execute(select(AdAccount).where(
            AdAccount.external_id == ACCOUNT_EXT))).scalar_one_or_none()
        if acct is not None:
            for model in (AdConversion, AdAttribution):
                await s.execute(sa_delete(model).where(model.ad_account_id == acct.id)
                                if hasattr(model, "ad_account_id") else
                                sa_delete(model))
            await s.execute(sa_delete(AdInsightDaily).where(
                AdInsightDaily.ad_account_id == acct.id))
            await s.execute(sa_delete(AdCampaign).where(AdCampaign.ad_account_id == acct.id))
            integ_id = acct.integration_id
            await s.execute(sa_delete(AdAccount).where(AdAccount.id == acct.id))
            await s.execute(sa_delete(Integration).where(Integration.id == integ_id))
        biz = (await s.execute(select(Business).where(
            Business.key == BUSINESS_KEY))).scalar_one_or_none()
        if biz is not None:
            await s.execute(sa_delete(MetricRecord).where(MetricRecord.business_id == biz.id))
            await s.execute(sa_delete(AdConversion).where(AdConversion.business_id == biz.id))
            await s.execute(sa_delete(AdAttribution).where(AdAttribution.business_id == biz.id))
            await s.execute(sa_delete(Business).where(Business.id == biz.id))
        await s.commit()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(t):
    return {"Authorization": f"Bearer {t}"}


async def _owner():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


async def _fixture():
    """One recent enrollee and one long-past one, so the window rule is actually exercised."""
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == t.id, Business.key == "drillprog"))).scalar_one_or_none()
        if biz is None:
            biz = Business(tenant_id=t.id, key="drillprog", name="Drill Program", tag="drill",
                           kind="membership", archetype="program", include_in_portfolio=False)
            s.add(biz)
            await s.flush()
        acct = (await s.execute(select(AdAccount).where(
            AdAccount.tenant_id == t.id,
            AdAccount.external_id == "act_drill"))).scalar_one_or_none()
        if acct is None:
            integ = Integration(tenant_id=t.id, provider="meta_ads", status="connected")
            s.add(integ)
            await s.flush()
            acct = AdAccount(tenant_id=t.id, integration_id=integ.id, platform="meta",
                             external_id="act_drill", name="Drill", timezone_name="UTC",
                             business_id=biz.id)
            s.add(acct)
            await s.flush()
            camp = AdCampaign(tenant_id=t.id, ad_account_id=acct.id,
                              external_id="c_drill", name="KB - The Shift - August2026")
            s.add(camp)
            await s.flush()
            s.add(AdInsightDaily(
                tenant_id=t.id, ad_account_id=acct.id, level="campaign",
                object_external_id="c_drill", campaign_id=camp.id, occurred_on=RECENT,
                spend=500.0, impressions=900, clicks=40, inline_link_clicks=30, leads=4))

            for who, day, name in (("recent", RECENT, "Dana Recent"), ("old", OLD, "Percy Past")):
                attr = AdAttribution(
                    tenant_id=t.id, identity_kind="ghl_contact", identity_key=f"cid_{who}",
                    email_norm=f"{who}@example.test", business_id=biz.id,
                    ad_account_id=acct.id, campaign_id=camp.id, match_method="campaign",
                    confidence="probable", channel="Meta",
                    first_seen_on=day, last_seen_on=day)
                s.add(attr)
                await s.flush()
                s.add(AdConversion(
                    tenant_id=t.id, attribution_id=attr.id, business_id=biz.id,
                    stage_key="closed", source_kind="bc_onboarded", source_ref=f"onb_{who}",
                    occurred_on=day, dated=True, value_contracted=12000))
                s.add(MetricRecord(
                    tenant_id=t.id, business_id=biz.id, source="ghl", kind="bc_onboarded",
                    name=name, email=f"{who}@example.test", occurred_on=day,
                    external_id=f"onb_{who}", source_url=f"https://ghl.test/{who}",
                    meta={"contact_id": f"cid_{who}"}))
        await s.commit()
        return str(acct.id)


async def test_enrolled_opens_to_the_people_in_it():
    """The ask, precisely: two enrollments on screen, and who they are."""
    acct = await _fixture()
    tok = await _owner()
    async with _client() as c:
        r = await c.get(f"/api/v1/ads/drill/funnel.closed?account={acct}&period=30d",
                        headers=_H(tok))
    assert r.status_code == 200
    b = r.json()
    assert b["type"] == "records" and b["title"] == "Enrolled"
    names = [row["name"] for row in b["rows"]]
    assert "Dana Recent" in names, "the drill did not resolve a name from the source record"
    assert b["rows"][0]["email"] == "recent@example.test"
    assert "value" in b["columns"], "closed rows should carry what they contracted for"


async def test_the_drill_opens_the_same_window_the_number_was_counted_in():
    """THE RULE. A drill that resolves its own window answers a different question than the one
    that was clicked. Percy enrolled 200 days ago and must not appear under a 30-day figure."""
    acct = await _fixture()
    tok = await _owner()
    async with _client() as c:
        short = (await c.get(f"/api/v1/ads/drill/funnel.closed?account={acct}&period=30d",
                             headers=_H(tok))).json()
        long = (await c.get(f"/api/v1/ads/drill/funnel.closed?account={acct}&period=90d",
                            headers=_H(tok))).json()
    assert "Percy Past" not in [r["name"] for r in short["rows"]]
    assert "Dana Recent" in [r["name"] for r in short["rows"]]
    assert short["count"] <= long["count"]


async def test_the_drill_count_equals_the_rung_it_opened():
    """If these two ever disagree, one of them is lying and there is no way to tell which."""
    acct = await _fixture()
    tok = await _owner()
    async with _client() as c:
        page = (await c.get(f"/api/v1/ads?account={acct}&period=30d", headers=_H(tok))).json()
        drill = (await c.get(f"/api/v1/ads/drill/funnel.closed?account={acct}&period=30d",
                             headers=_H(tok))).json()
    rung = next((r["n"] for r in (page.get("funnel") or []) if r["key"] == "closed"), None)
    assert rung is not None
    assert drill["count"] == rung, f"drill says {drill['count']}, the rung says {rung}"


async def test_an_empty_rung_says_WHY_it_is_empty():
    """A zero is either "nobody got here" or "we do not record this", and those are completely
    different facts. Applied reads zero on live data because `app_in` is false on every record in
    the system - and the funnel presented that as the biggest leak in the business."""
    acct = await _fixture()
    tok = await _owner()
    async with _client() as c:
        r = await c.get(f"/api/v1/ads/drill/funnel.applied?account={acct}&period=30d",
                        headers=_H(tok))
    b = r.json()
    assert b["count"] == 0
    assert b["note"] and "not being recorded" in b["note"]


async def test_a_member_without_the_tab_cannot_open_a_rung():
    """A drill inherits its tile's permission. Otherwise the drill is a hole around the tab gate,
    and this one returns names and emails."""
    acct = await _fixture()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = User(tenant_id=t.id, email="nodrill@springb.com", name="nd",
                 password_hash=hash_pw("x"), role="member", status="active",
                 tab_access=["portfolio"], token_version=0)
        s.add(u)
        await s.commit()
        tok = make_token(u.id, t.id, 0)
    async with _client() as c:
        r = await c.get(f"/api/v1/ads/drill/funnel.closed?account={acct}&period=30d",
                        headers=_H(tok))
    assert r.status_code == 403
    assert "example.test" not in r.text


async def test_an_unknown_metric_is_a_404_not_an_empty_table():
    acct = await _fixture()
    tok = await _owner()
    async with _client() as c:
        r = await c.get(f"/api/v1/ads/drill/funnel.nonsense?account={acct}&period=30d",
                        headers=_H(tok))
    assert r.status_code == 404


# ── the tie-out column on the launch drawer ───────────────────────────────────────────
async def test_the_launch_group_drill_says_where_each_person_came_from():
    """The Enrolled drawer lists people; the bar above it says 60% came from Meta. Without a
    per-person source those two can only be compared in aggregate, which is exactly the gap that
    made an 845-versus-800 question take a database query to answer.

    Joined by contact_id to the Shift registration, so the drawer reconciles against the BAR
    rather than against a second opinion computed a different way.
    """
    from app.services.launch import drill_launch
    from app.models import Launch

    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        launch = (await s.execute(select(Launch).where(
            Launch.tenant_id == t.id))).scalars().first()
        if launch is None:
            pytest.skip("no launch configured in the seed")

        s.add(MetricRecord(
            tenant_id=t.id, business_id=launch.business_id, source="ghl", kind="bc_shift_reg",
            name="Sourced Sam", email="sam@example.test", external_id="reg_sam",
            meta={"contact_id": "cid_sam", "channel": "Meta"}))
        s.add(MetricRecord(
            tenant_id=t.id, business_id=launch.business_id, source="ghl", kind="bc_launch_opp",
            name="Sourced Sam", email="sam@example.test", external_id="opp_sam",
            meta={"contact_id": "cid_sam", "group": "enrolled", "stage": "Won: Onboarded",
                  "launch_id": str(launch.id), "payment_type": "pif"}))
        # Somebody who never registered for the Shift: a real answer, not a blank.
        s.add(MetricRecord(
            tenant_id=t.id, business_id=launch.business_id, source="ghl", kind="bc_launch_opp",
            name="Direct Dana", email="dana@example.test", external_id="opp_dana",
            meta={"contact_id": "cid_dana", "group": "enrolled", "stage": "Won: Onboarded",
                  "launch_id": str(launch.id), "payment_type": "plan"}))
        await s.commit()

    try:
        async with SessionLocal() as s:
            launch = await s.get(Launch, launch.id)
            d = await drill_launch(s, launch.tenant_id, launch, "funnel.enrolled")
        assert "source" in d["columns"]
        by_name = {r["name"]: r["source"] for r in d["rows"]}
        assert by_name.get("Sourced Sam") == "Meta"
        assert by_name.get("Direct Dana") == "No Shift registration", \
            "names what was observed - not in the registrant set - rather than asserting an origin nobody measured"
    finally:
        async with SessionLocal() as s:
            await s.execute(sa_delete(MetricRecord).where(
                MetricRecord.external_id.in_(("reg_sam", "opp_sam", "opp_dana"))))
            await s.commit()
