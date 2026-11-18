"""The ads the gallery shows: each carries its brand, and the Brand filter reaches ads.

Before this, /ads/creatives rows had no `group`. The tab's Brand filter compared `row.group` to
the brand, found nothing to compare, and let every row through: picking Forum Dallas narrowed
the campaigns and left every brand's ads on screen. The route is called directly, against its
own tenant, so neither the HTTP layer's tenancy nor anyone else's ads are in the way.
"""
import datetime as dt
import uuid
from decimal import Decimal

import pytest

from app.db import SessionLocal, engine
from app.models import Ad, AdAccount, AdCampaign, AdInsightDaily, Base, Integration, Tenant, User
from app.routers.ads import creatives

WINDOW = "c:2026-11-01:2026-11-30"


async def _remove_tenant(tenant_id) -> None:
    """Clear every tenant-scoped row, then the tenant. The test database is shared, and SQLite
    does not enforce the foreign keys that would cascade this in Postgres."""
    from sqlalchemy import delete as sa_delete

    tables = [t for t in reversed(Base.metadata.sorted_tables) if "tenant_id" in t.c]
    async with SessionLocal() as s:
        for t in tables:
            await s.execute(sa_delete(t).where(t.c.tenant_id == tenant_id))
        tenant = await s.get(Tenant, tenant_id)
        if tenant is not None:
            await s.delete(tenant)
        await s.commit()


@pytest.fixture
async def env():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        t = Tenant(slug=f"g-{uuid.uuid4().hex[:8]}", name="Gallery")
        s.add(t)
        await s.flush()
        owner = User(tenant_id=t.id, email=f"{uuid.uuid4().hex[:6]}@x.com", name="Ada",
                     role="owner", status="active")
        integ = Integration(tenant_id=t.id, provider="meta_ads", status="connected")
        s.add_all([owner, integ])
        await s.flush()
        acct = AdAccount(tenant_id=t.id, integration_id=integ.id, platform="meta",
                         external_id="act_g", name="Meta", currency="USD",
                         timezone_name="America/Denver")
        s.add(acct)
        await s.flush()
        forum = AdCampaign(tenant_id=t.id, ad_account_id=acct.id, external_id="c-forum",
                           name="Forum Dallas - VIP")
        shift = AdCampaign(tenant_id=t.id, ad_account_id=acct.id, external_id="c-shift",
                           name="KB - The Shift - August2026")
        s.add_all([forum, shift])
        await s.flush()
        for ext, camp, spend in (("ad-forum-1", forum, 300), ("ad-forum-2", forum, 120),
                                 ("ad-shift", shift, 900), ("ad-orphan", None, 50)):
            ad = Ad(tenant_id=t.id, ad_account_id=acct.id, campaign_id=camp.id if camp else None,
                    external_id=ext, name=ext, headline=f"Headline {ext}",
                    thumbnail_url=f"https://img.test/{ext}.jpg")
            s.add(ad)
            await s.flush()
            s.add(AdInsightDaily(tenant_id=t.id, ad_account_id=acct.id, level="ad",
                                 object_external_id=ext, ad_id=ad.id,
                                 campaign_id=camp.id if camp else None,
                                 occurred_on=dt.date(2026, 11, 10), spend=Decimal(spend),
                                 impressions=1000, inline_link_clicks=10))
        await s.commit()
        ids = {"tid": t.id, "aid": acct.id, "uid": owner.id}
    yield ids
    await _remove_tenant(ids["tid"])


async def _ask(env, **kw):
    async with SessionLocal() as s:
        user = await s.get(User, env["uid"])
        args = {"account": str(env["aid"]), "period": WINDOW, "campaign": None, "brand": None,
                "sort": "spend", "limit": 40, "user": user, "s": s}
        args.update(kw)
        return await creatives(**args)


@pytest.mark.asyncio
async def test_every_ad_carries_its_brand_and_campaign(env):
    body = await _ask(env)
    got = {a["external_id"]: (a["group"], a["campaign"]) for a in body["ads"]}
    assert got == {
        "ad-shift": ("beCollective / The Shift", "KB - The Shift - August2026"),
        "ad-forum-1": ("Forum Dallas", "Forum Dallas - VIP"),
        "ad-forum-2": ("Forum Dallas", "Forum Dallas - VIP"),
        "ad-orphan": ("Other", None),
    }
    # The picture and the words the audience saw travel with every row.
    assert all(a["thumbnail_url"] and a["headline"] for a in body["ads"])


@pytest.mark.asyncio
async def test_the_brand_filter_reaches_ads(env):
    body = await _ask(env, brand="Forum Dallas")
    assert [a["external_id"] for a in body["ads"]] == ["ad-forum-1", "ad-forum-2"]
    assert body["total"] == 2


@pytest.mark.asyncio
async def test_the_brand_filter_is_applied_before_the_limit(env):
    """With the limit applied first, the account's single biggest ad is The Shift's, and a
    Forum Dallas view of the top one would be empty. A brand's gallery is ITS top ads."""
    body = await _ask(env, brand="Forum Dallas", limit=1)
    assert [a["external_id"] for a in body["ads"]] == ["ad-forum-1"]
    assert body["total"] == 2
