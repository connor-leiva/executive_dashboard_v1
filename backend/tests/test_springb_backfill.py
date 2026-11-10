"""The one-time Spring B backfill fills empty auto cells and leaves hand entry alone."""
import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import SessionLocal, engine
from app.models import (Base, Business, Integration, MetricRecord, ScorecardBackfillMark,
                        ScorecardGroup, ScorecardMetric, ScorecardValue, Tenant)
from app.services.springb_enterprise import BACKFILL_MARK, _fill_cell, backfill_tenant, run_backfill_once


@pytest.fixture
async def env():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        t = Tenant(slug=f"bf-{uuid.uuid4().hex[:8]}", name="Backfill")
        s.add(t)
        await s.flush()
        b = Business(tenant_id=t.id, key=f"bf{uuid.uuid4().hex[:6]}", name="Spring B", tag="mb",
                     kind="membership")
        s.add(b)
        await s.flush()
        s.add(Integration(tenant_id=t.id, provider="ghl", business_id=b.id, status="connected",
                          last_synced_at=dt.datetime.now(dt.timezone.utc)))
        g = ScorecardGroup(tenant_id=t.id, business_id=b.id, key="sb_l10_enroll", name="Enrollment",
                           lens="l10", sort_order=1)
        s.add(g)
        await s.flush()
        m = ScorecardMetric(tenant_id=t.id, group_id=g.id, name="Members added", slug="members",
                            type="flow", direction="gte", goal=Decimal("0"), active=True,
                            resolver_key="forum_members_added", tier="l10", provenance="live")
        s.add(m)
        await s.flush()
        today = dt.date.today()
        mon = today - dt.timedelta(days=today.weekday())
        prev = mon - dt.timedelta(days=7)
        s.add(MetricRecord(tenant_id=t.id, business_id=b.id, source="ghl", kind="member",
                           external_id="new", status="active",
                           meta={"membership": {"enrollment_date": (mon + dt.timedelta(days=1)).isoformat()}}))
        s.add(ScorecardValue(tenant_id=t.id, metric_id=m.id, week_start=prev,
                             value=Decimal("4"), source="manual"))
        await s.commit()
        yield {"tid": t.id, "mid": m.id, "mon": mon, "prev": prev}


async def test_backfill_fills_gaps_and_keeps_hand_entry(env):
    stats = await backfill_tenant(env["tid"], weeks=2)
    assert stats["filled"] >= 1
    assert stats["skipped_manual"] >= 1
    assert stats["sample"]["members"] == 1
    async with SessionLocal() as s:
        prev = (await s.execute(select(ScorecardValue).where(
            ScorecardValue.metric_id == env["mid"],
            ScorecardValue.week_start == env["prev"]))).scalar_one()
        assert prev.value == Decimal("4") and prev.source == "manual"
        cur = (await s.execute(select(ScorecardValue).where(
            ScorecardValue.metric_id == env["mid"],
            ScorecardValue.week_start == env["mon"]))).scalar_one()
        assert cur.value == Decimal("1") and cur.source == "resolver"


async def test_a_blank_result_does_not_erase_a_stored_number(env):
    async with SessionLocal() as s:
        row = ScorecardValue(tenant_id=env["tid"], metric_id=env["mid"], week_start=env["mon"],
                             value=Decimal("5"), source="resolver")
        s.add(row)
        await s.commit()
        assert await _fill_cell(s, env["tid"], env["mid"], env["mon"], None, "forum_members_added") == "blank"
        await s.commit()
        kept = (await s.execute(select(ScorecardValue).where(ScorecardValue.id == row.id))).scalar_one()
        assert kept.value == Decimal("5")


async def test_marker_makes_the_second_boot_a_no_op(env):
    first = await run_backfill_once(weeks=2)
    assert first is not None and first["tenants"] >= 1
    second = await run_backfill_once(weeks=2)
    assert second is None
    async with SessionLocal() as s:
        marks = (await s.execute(select(ScorecardBackfillMark).where(
            ScorecardBackfillMark.tenant_id == env["tid"],
            ScorecardBackfillMark.key == BACKFILL_MARK))).scalars().all()
        assert len(marks) == 1
