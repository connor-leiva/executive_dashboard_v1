"""Forum VIP and Edge ladders: named people, missing rungs stay blank, seats follow utm_content."""
import datetime as dt
import uuid

import pytest

from app.db import SessionLocal, engine
from app.models import Base, Business, Integration, MetricRecord, Tenant
from app.services.ads_brand_funnels import build_brand_funnels, seats_by_ad


@pytest.fixture
async def env():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        t = Tenant(slug=f"b-{uuid.uuid4().hex[:8]}", name="PhaseB")
        s.add(t)
        await s.flush()
        b = Business(tenant_id=t.id, key=f"f{uuid.uuid4().hex[:4]}", name="The Forum",
                     tag="mb", kind="membership", sort_order=0)
        s.add(b)
        await s.flush()
        s.add(Integration(tenant_id=t.id, provider="stripe_legacy", business_id=b.id,
                          status="connected"))
        await s.commit()
        yield {"tid": t.id, "bid": b.id}


def _row(env, **kw):
    base = dict(tenant_id=env["tid"], business_id=env["bid"], source="ghl", status="entered")
    base.update(kw)
    return MetricRecord(**base)


async def test_forum_and_edge_ladders_name_the_people_and_leave_attendance_blank(env):
    start, end = dt.date(2026, 11, 2), dt.date(2026, 11, 8)
    async with SessionLocal() as s:
        s.add_all([
            _row(env, kind="forum_stage_event", external_id="q1", name="Ada Lovelace",
                 email="ada@example.com", segment="vip_guest", occurred_on=start,
                 meta={"opp_id": "o1", "opp_source": "VIP Page", "utm_content": "120210"}),
            _row(env, kind="forum_stage_event", external_id="q2", name="Justin Guest",
                 segment="vip_guest", occurred_on=start,
                 meta={"opp_id": "o2", "opp_source": "Sisu"}),
            _row(env, kind="forum_stage_event", external_id="s1", name="Ada Lovelace",
                 email="ada@example.com", segment="vip_guest_seat", occurred_on=start,
                 meta={"opp_id": "o1", "to_stage": "VIP Guest: Confirmed", "utm_content": "120210"}),
            _row(env, kind="forum_stage_event", external_id="s2", name="Walk In",
                 segment="vip_guest_seat", occurred_on=start,
                 meta={"opp_id": "o3", "to_stage": "VIP Guest: Attended"}),
            _row(env, kind="onboarded", external_id="m1", name="Ada Lovelace",
                 email="ada@example.com", occurred_on=end, meta={"stage": "Onboarded"}),
            _row(env, kind="edge_registration", external_id="e1", name="Grace Hopper",
                 email="grace@example.com", status="registered", occurred_on=start),
            _row(env, source="stripe_legacy", kind="edge_subscription", external_id="sub1",
                 name="Grace Hopper", email="grace@example.com", status="active", amount=99,
                 meta={"start_date": "2026-11-04", "plan_name": "Edge Monthly"}),
        ])
        await s.commit()
        out = await build_brand_funnels(
            s, env["tid"], env["bid"], start, end,
            {"spend": 700, "clicks": 100, "leads": 10},
            {"spend": 200, "clicks": 40, "leads": 4})

    forum = {r["key"]: r for r in out["forum"]["rungs"]}
    assert forum["qualified"]["n"] == 1
    assert forum["qualified"]["people"][0]["name"] == "Ada Lovelace"
    # Meta's lead actions and Spring's guests are different denominators.
    # $700 / 10 Meta leads is not $700 / 1 Spring guest.
    assert forum["lead"]["n"] == 10
    assert forum["lead"]["cost_per"] == 70
    assert forum["qualified"]["cost_per"] == 700
    assert forum["seat"]["n"] == 1
    assert forum["seat"]["cost_per"] == 700
    assert forum["member"]["n"] == 1
    assert forum["member"]["cost_per"] == 700
    edge = {r["key"]: r for r in out["edge"]["rungs"]}
    assert edge["registered"]["n"] == 1
    assert edge["attended"]["missing"] is True and edge["attended"]["n"] is None
    assert edge["purchase"]["n"] == 1
    assert edge["purchase"]["people"][0]["name"] == "Grace Hopper"


def test_seats_follow_utm_content_and_stay_blank_without_it():
    class Row:
        def __init__(self, content):
            self.meta = {"utm_content": content} if content else {}

    assert seats_by_ad([Row(None)], {"120210"}) is None
    assert seats_by_ad([Row("120210"), Row("other")], {"120210"}) == {"120210": 1}
