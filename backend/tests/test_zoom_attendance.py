"""Zoom attendance: dedupe, topic mapping, red members, and the Edge rate."""
import datetime as dt
import uuid

import pytest

from app.db import SessionLocal, engine
from app.models import (AttendancePerson, AttendanceSession, AttendanceSync, Base, Business,
                        MetricRecord, Tenant)
from app.services.attendance import (backfill_bounds, classify_topic, dedupe_participants,
                                     edge_live_rate, forum_red_members)
from app.services.scorecard_resolvers import UNAVAILABLE


def test_rejoins_collapse_and_topics_map():
    people = dedupe_participants([
        {"user_email": "Ada@Example.com", "name": "Ada", "duration": 100, "join_time": "2026-11-10T18:00:00Z"},
        {"user_email": "ada@example.com", "name": "Ada Lovelace", "duration": 50, "join_time": "2026-11-10T18:20:00Z"},
        {"name": "No Email", "duration": 10},
    ])
    assert len(people) == 2
    ada = next(p for p in people if p["email"] == "ada@example.com")
    assert ada["duration_seconds"] == 150
    assert classify_topic("Edge Weekly Workshop") == "Edge Webinar"
    assert classify_topic("Forum Dallas Office Hours") == "Forum"
    assert classify_topic("Weekly Huddle") == "Unmapped"


def test_backfill_covers_the_sprint_and_thirteen_weeks_before_it():
    start, end = backfill_bounds(dt.date(2026, 11, 11), [
        {"key": "2026Q4", "start": "2026-11-02", "end": "2027-01-18"},
    ])
    assert end == dt.date(2026, 11, 11)
    assert start == dt.date(2026, 11, 2) - dt.timedelta(weeks=13)


@pytest.fixture
async def env():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        t = Tenant(slug=f"z-{uuid.uuid4().hex[:8]}", name="Zoom")
        s.add(t)
        await s.flush()
        b = Business(tenant_id=t.id, key=f"f{uuid.uuid4().hex[:4]}", name="Forum",
                     tag="mb", kind="membership", sort_order=0)
        s.add(b)
        await s.flush()
        s.add(AttendanceSync(tenant_id=t.id, last_synced_at=dt.datetime.now(dt.timezone.utc)))
        s.add(MetricRecord(tenant_id=t.id, business_id=b.id, source="ghl", kind="member",
                           external_id="m1", name="Ada Lovelace", email="ada@example.com", status="active"))
        s.add(MetricRecord(tenant_id=t.id, business_id=b.id, source="stripe_legacy",
                           kind="edge_subscription", external_id="sub1", name="Grace",
                           email="grace@example.com", status="active", amount=99,
                           meta={"plan_name": "Edge Monthly"}))
        await s.flush()
        now = dt.datetime(2026, 11, 10, 18, tzinfo=dt.timezone.utc)
        for i, topic in enumerate(("Forum A", "Forum B", "Forum C", "Edge Weekly")):
            sess = AttendanceSession(tenant_id=t.id, zoom_id=str(i), kind="webinar", topic=topic,
                                     started_at=now - dt.timedelta(days=i), duration_minutes=60)
            s.add(sess)
            await s.flush()
            if topic == "Edge Weekly":
                s.add(AttendancePerson(tenant_id=t.id, session_id=sess.id, person_key="grace@example.com",
                                       email="grace@example.com", name="Grace", duration_seconds=1000,
                                       match_method="email"))
        await s.commit()
        yield {"tid": t.id, "bid": b.id}


@pytest.mark.asyncio
async def test_three_missed_forum_sessions_is_red_and_edge_attendance_counts(env):
    async with SessionLocal() as s:
        red = await forum_red_members(s, env["tid"], env["bid"])
        rate = await edge_live_rate(s, env["tid"], env["bid"], dt.date(2026, 11, 11))
    assert len(red) == 1 and red[0]["client"] == "Ada Lovelace"
    assert rate == 100.0


@pytest.mark.asyncio
async def test_edge_rate_is_unavailable_before_the_first_sync(env):
    async with SessionLocal() as s:
        state = (await s.execute(
            __import__("sqlalchemy").select(AttendanceSync).where(
                AttendanceSync.tenant_id == env["tid"]))).scalar_one()
        state.last_synced_at = None
        await s.commit()
        rate = await edge_live_rate(s, env["tid"], env["bid"], dt.date(2026, 11, 11))
    assert rate is UNAVAILABLE
