"""The morning digest: yesterday against the plan, and one email per day."""
import datetime as dt
import uuid
from decimal import Decimal

import pytest

from app.db import SessionLocal, engine
from app.models import (Ad, AdAccount, AdCampaign, AdDigest, AdInsightDaily, Base, Business,
                        Integration, Tenant, User)
from app.security import enc
from app.services import ads_digest as D


@pytest.fixture
async def env():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        t = Tenant(slug=f"d-{uuid.uuid4().hex[:8]}", name="PhaseD")
        s.add(t)
        await s.flush()
        b = Business(tenant_id=t.id, key=f"f{uuid.uuid4().hex[:4]}", name="Forum",
                     tag="mb", kind="membership", sort_order=0)
        owner = User(tenant_id=t.id, email=f"{uuid.uuid4().hex[:6]}@x.com", name="Ada",
                     role="owner", status="active")
        s.add_all([b, owner])
        await s.flush()
        integ = Integration(tenant_id=t.id, provider="meta_ads", business_id=b.id, status="connected",
                            access_token_enc=enc("t"))
        s.add(integ)
        await s.flush()
        acct = AdAccount(tenant_id=t.id, integration_id=integ.id, business_id=b.id, platform="meta",
                         external_id="act_d", name="Meta", currency="USD", timezone_name="America/Denver",
                         weekly_budgets={"Forum Dallas": 700})
        s.add(acct)
        await s.flush()
        camp = AdCampaign(tenant_id=t.id, ad_account_id=acct.id, external_id="c-forum",
                          name="Forum Dallas - VIP", daily_budget=100)
        ad = Ad(tenant_id=t.id, ad_account_id=acct.id, campaign_id=None, external_id="ad1",
                name="Dallas creative")
        s.add_all([camp, ad])
        await s.flush()
        ad.campaign_id = camp.id
        day = dt.date(2026, 11, 9)
        prior = day - dt.timedelta(days=8)
        s.add(AdInsightDaily(tenant_id=t.id, ad_account_id=acct.id, level="campaign",
                             object_external_id="c-forum", campaign_id=camp.id, occurred_on=day,
                             spend=Decimal("100")))
        s.add(AdInsightDaily(tenant_id=t.id, ad_account_id=acct.id, level="ad",
                             object_external_id="ad1", ad_id=ad.id, campaign_id=camp.id,
                             occurred_on=day, spend=Decimal("40"), impressions=2000,
                             inline_link_clicks=10, frequency=Decimal("3")))
        s.add(AdInsightDaily(tenant_id=t.id, ad_account_id=acct.id, level="ad",
                             object_external_id="ad1b", ad_id=ad.id, campaign_id=camp.id,
                             occurred_on=prior, spend=Decimal("40"), impressions=2000,
                             inline_link_clicks=40, frequency=Decimal("1")))
        await s.commit()
        yield {"aid": acct.id, "day": day}


@pytest.mark.asyncio
async def test_the_digest_compares_yesterday_with_the_plan_and_flags_fatigue(env):
    async with SessionLocal() as s:
        account = await s.get(AdAccount, env["aid"])
        body = await D.compose(s, account, env["day"])
    forum = next(b for b in body["brands"] if b["name"] == "Forum Dallas")
    assert forum["spent"] == 100
    assert forum["daily_plan"] == 100
    assert "Dallas creative" in body["fatigue"]
    text = D.render_text(body)
    assert "Yesterday vs plan" in text and "Forum Dallas" in text


@pytest.mark.asyncio
async def test_deliver_emails_once(env, monkeypatch):
    sent = []

    async def fake_send(to, subject, html, text, reply_to=None, idempotency_key=None):
        sent.append((to, subject, idempotency_key))
        return True

    monkeypatch.setattr("app.services.mailer.send", fake_send)
    async with SessionLocal() as s:
        account = await s.get(AdAccount, env["aid"])
        await D.deliver(s, account, env["day"])
        await D.deliver(s, account, env["day"])
        await s.commit()
        rows = (await s.execute(
            __import__("sqlalchemy").select(AdDigest).where(AdDigest.ad_account_id == env["aid"]))).scalars().all()
    assert len(sent) == 1
    assert sent[0][1] == "Ads 2026-11-09"
    assert len(rows) == 1 and rows[0].emailed is True
