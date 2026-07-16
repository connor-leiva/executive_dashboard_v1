"""Settings › Integrations grouped view (spec v3 Part 1.4)."""
import datetime as dt

import pytest
from sqlalchemy import select, update

from app.seed import seed
from app.db import SessionLocal
from app.models import Business, Integration, SyncRun
from app.services.integrations_view import build_integrations_view


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _tenant(s):
    return (await s.execute(select(Business).where(Business.key == "ulrg"))).scalar_one().tenant_id


async def test_qbo_grouped_and_attention():
    async with SessionLocal() as s:
        t = await _tenant(s)
        biz = {b.key: b.id for b in (await s.execute(
            select(Business).where(Business.tenant_id == t))).scalars().all()}
        await s.execute(update(Integration).where(
            Integration.tenant_id == t, Integration.provider == "qbo",
            Integration.business_id == biz["springb"]).values(status="error", last_error="Token expired Jun 29"))
        await s.commit()
        out = await build_integrations_view(s, t)

    qbo = next(x for x in out.sources if x.provider == "qbo")
    assert qbo.status == "attention"
    assert "1 of 3" in qbo.status_note
    assert len(qbo.entities) == 3                       # grouped, ordered by sort_order
    assert [e.business_key for e in qbo.entities] == ["ulrg", "springb", "sympli"]
    err = [e for e in qbo.entities if e.state == "error"]
    assert len(err) == 1 and "expired" in err[0].detail.lower()


async def test_freshness_stale():
    async with SessionLocal() as s:
        t = await _tenant(s)
        old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=3 * 30)   # 3× the interval
        await s.execute(update(Integration).where(
            Integration.tenant_id == t, Integration.provider == "sisu").values(
            status="connected", last_synced_at=old))
        await s.commit()
        out = await build_integrations_view(s, t)

    sisu = next(x for x in out.sources if x.provider == "sisu")
    assert sisu.status == "stale"
    assert sisu.fresh and "ago" in sisu.fresh


async def test_stats_last_run():
    async with SessionLocal() as s:
        t = await _tenant(s)
        # fresh sisu + a run with stats → last_run renders counts.
        await s.execute(update(Integration).where(
            Integration.tenant_id == t, Integration.provider == "sisu").values(
            status="connected", last_synced_at=dt.datetime.now(dt.timezone.utc)))
        s.add(SyncRun(tenant_id=t, provider="sisu", status="ok",
                      finished_at=dt.datetime.now(dt.timezone.utc), stats={"records": 412, "seconds": 3.1}))
        await s.commit()
        out = await build_integrations_view(s, t)

    sisu = next(x for x in out.sources if x.provider == "sisu")
    assert sisu.last_run and "412 records" in sisu.last_run


async def test_qbo_disconnected_entity_marked_distinctly():
    """A disconnected QBO integration renders with state='disconnected' (not 'ok'), so
    the UI can show it as disconnected + offer Reconnect rather than looking unchanged."""
    async with SessionLocal() as s:
        t = await _tenant(s)
        biz = {b.key: b.id for b in (await s.execute(
            select(Business).where(Business.tenant_id == t))).scalars().all()}
        await s.execute(update(Integration).where(
            Integration.tenant_id == t, Integration.provider == "qbo",
            Integration.business_id == biz["sympli"]).values(status="disconnected", access_token_enc=None))
        await s.commit()
        out = await build_integrations_view(s, t)
    qbo = next(x for x in out.sources if x.provider == "qbo")
    sympli = next(e for e in qbo.entities if e.business_key == "sympli")
    assert sympli.state == "disconnected"      # not "ok" — the UI reflects the disconnect
