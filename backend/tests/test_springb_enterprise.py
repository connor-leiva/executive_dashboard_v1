"""Spring B enterprise scorecard — the tiered board, the VIP source split, and hand entry."""
import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as SyncSession

from app.db import SessionLocal, engine
from app.models import (Base, BookTxn, Business, Integration, MetricRecord, ScorecardGoal,
                        ScorecardGroup, ScorecardMetric, ScorecardValue, Tenant)
from app.services import scorecard_resolvers as R
from app.services.scorecard import build_scorecard, miss_streak
from app.services.springb_enterprise import CORE_SLUGS, apply_enterprise


@pytest.fixture
async def board():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        t = Tenant(slug=f"ent-{uuid.uuid4().hex[:8]}", name="Enterprise")
        s.add(t)
        await s.flush()
        b = Business(tenant_id=t.id, key=f"mb{uuid.uuid4().hex[:8]}", name="Spring B", tag="mb",
                     kind="membership")
        s.add(b)
        await s.flush()
        s.add(Integration(tenant_id=t.id, provider="ghl", business_id=b.id, status="connected",
                          last_synced_at=dt.datetime.now(dt.timezone.utc)))
        # the pre-tier board: a few lines the seed must move, not duplicate
        g = ScorecardGroup(tenant_id=t.id, business_id=b.id, key="forum", name="Forum", sort_order=0)
        s.add(g)
        await s.flush()
        vip = ScorecardMetric(tenant_id=t.id, group_id=g.id, name="VIP Guests Added", type="flow",
                              direction="gte", goal=Decimal("2"), active=True,
                              resolver_key="forum_vip_guests_added")
        s.add(vip)
        await s.flush()
        s.add(ScorecardGoal(tenant_id=t.id, metric_id=vip.id, period_key="2026Q4",
                            goal=Decimal("2"), cumulative_goal=Decimal("24")))
        s.add(ScorecardMetric(tenant_id=t.id, group_id=g.id, name="Appointments Set", type="flow",
                              direction="gte", goal=Decimal("2"), active=True,
                              source_spec={"source": "ghl", "dataset": "metric_record"}))
        s.add(ScorecardMetric(tenant_id=t.id, group_id=g.id, name="Members at Risk", type="snapshot",
                              direction="lte", goal=Decimal("3"), active=True))
        await s.commit()
        yield {"tid": t.id, "bid": b.id}


def _apply(sync_conn, tenant_id):
    sess = SyncSession(bind=sync_conn)
    stats = apply_enterprise(sess, tenant_id=tenant_id)
    sess.flush()
    return stats


async def test_seed_is_additive_and_covers_the_fifty_two(board):
    e = board
    async with engine.begin() as conn:
        stats = await conn.run_sync(lambda c: _apply(c, e["tid"]))
    assert stats["created"] > 40
    async with engine.begin() as conn:
        again = await conn.run_sync(lambda c: _apply(c, e["tid"]))
    assert again["created"] == 0
    async with SessionLocal() as s:
        rows = (await s.execute(select(ScorecardMetric).where(
            ScorecardMetric.tenant_id == e["tid"], ScorecardMetric.active.is_(True)))).scalars().all()
        slugs = {m.slug for m in rows if m.slug in CORE_SLUGS}
        assert slugs == set(CORE_SLUGS)
        vip = next(m for m in rows if m.slug == "vip")
        assert vip.name == "VIP Guests Added"
        assert vip.resolver_key == "forum_vip_guests_seats"
        assert vip.goal == Decimal("7")
        assert vip.tier == "l10"
        appt = next(m for m in rows if m.slug == "apptset")
        assert appt.resolver_key is None and appt.source_spec is None and appt.provenance == "hand"
        assert appt.tier == "dept"
        retired = (await s.execute(select(ScorecardMetric).where(
            ScorecardMetric.tenant_id == e["tid"], ScorecardMetric.name == "Members at Risk"))).scalar_one()
        assert retired.active is False
        # the legacy quarter goal moved off 2/week and 24, history row still exists
        goal = (await s.execute(select(ScorecardGoal).where(ScorecardGoal.metric_id == vip.id))).scalar_one()
        assert float(goal.goal) == 7 and goal.cumulative_goal is None
        # a second apply did not add a second VIP row
        assert sum(1 for m in rows if m.slug == "vip") == 1


def test_zero_goal_is_track_only_unless_it_is_a_real_cap():
    assert miss_streak([1, 2, 3], 0, "lte") == 0
    assert miss_streak([1, 2, 3], 0, "lte", score_zero=True) == 3
    assert miss_streak([0, 2, 0], 0, "lte", score_zero=True) == 0   # newest week hit the cap


def test_vip_channel_uses_source_then_tag_then_url():
    assert R.vip_channel({"opp_source": "Sisu Private Mastermind"}) == "sisu"
    assert R.vip_channel({"opp_source": "Sisu"}) == "sisu"
    assert R.vip_channel({"opp_source": "VIP Page"}) == "spring"
    assert R.vip_channel({"opp_source": "forummastermind.com/guest-application"}) == "spring"
    assert R.vip_channel({"opp_source": "Member Referral"}) is None
    assert R.vip_channel({"tags": ["forum vip guest sisu nov 2026"]}) == "sisu"
    assert R.vip_channel({"page_url": "https://forummastermind.com/vip?x=1"}) == "spring"
    assert R.vip_channel({"page_url": "https://forummastermind.com/sisu"}) == "sisu"
    # source wins over a contradictory URL
    assert R.vip_channel({"opp_source": "VIP Page", "page_url": "https://x.com/sisu"}) == "spring"
    assert R.vip_channel({}) is None


async def test_vip_split_counts_and_leaves_blanks_out(board):
    e = board
    mon, wed = dt.date(2026, 11, 2), dt.date(2026, 11, 4)
    async with SessionLocal() as s:
        s.add(MetricRecord(tenant_id=e["tid"], business_id=e["bid"], source="ghl",
                           kind="forum_stage_tracking", external_id="since", occurred_on=mon,
                           status="tracking"))
        def ev(ext, **meta):
            return MetricRecord(tenant_id=e["tid"], business_id=e["bid"], source="ghl",
                                kind="forum_stage_event", external_id=ext, segment="vip_guest",
                                occurred_on=wed, status="entered", name=ext, meta=meta)
        s.add_all([
            ev("a", opp_source="Sisu", opp_id="a"),
            ev("b", opp_source="Sisu Private Mastermind", opp_id="b"),
            ev("c", opp_source="VIP Page", opp_id="c"),
            ev("d", opp_id="d"),  # blank source, tag lives on the current snapshot
            ev("e", opp_id="e"),  # blank, no fallback — counted in neither
        ])
        s.add(MetricRecord(tenant_id=e["tid"], business_id=e["bid"], source="ghl",
                           kind="recruiting", external_id="d", status="open",
                           meta={"tags": ["forum vip guest sisu nov 2026"]}))
        await s.commit()
        assert await R.forum_vip_guests_sisu(s, e["tid"], e["bid"], mon, mon + dt.timedelta(days=6)) == 3
        assert await R.forum_vip_guests_spring(s, e["tid"], e["bid"], mon, mon + dt.timedelta(days=6)) == 1
        # the unfiltered total still counts every VIP entry, including the blank
        assert await R.forum_vip_guests_added(s, e["tid"], e["bid"], mon, mon + dt.timedelta(days=6)) == 5


async def test_as_of_and_books_and_cash(board):
    e = board
    today = dt.date.today()
    ws = today - dt.timedelta(days=today.weekday())
    we = ws + dt.timedelta(days=6)
    old = dt.date(2020, 1, 6)
    async with SessionLocal() as s:
        assert await R.sb_active_members(s, e["tid"], e["bid"], old, old + dt.timedelta(days=6)) is R.UNAVAILABLE
        s.add(MetricRecord(tenant_id=e["tid"], business_id=e["bid"], source="ghl", kind="member",
                           external_id="m", status="active", meta={"membership": {"renewal_date": "2027-01-01"}}))
        s.add(MetricRecord(tenant_id=e["tid"], business_id=e["bid"], source="ghl", kind="member",
                           external_id="n", status="active", meta={"membership": {}}))
        s.add(MetricRecord(tenant_id=e["tid"], business_id=e["bid"], source="ghl", kind="payment",
                           external_id="p1", status="succeeded", occurred_on=ws, amount=Decimal("10")))
        s.add(MetricRecord(tenant_id=e["tid"], business_id=e["bid"], source="ghl", kind="payment",
                           external_id="p2", status="failed", occurred_on=ws, amount=Decimal("10")))
        s.add(BookTxn(tenant_id=e["tid"], business_id=e["bid"], realm_id="r", qbo_type="Deposit",
                      qbo_id="d1", txn_date=ws, amount=Decimal("100")))
        s.add(BookTxn(tenant_id=e["tid"], business_id=e["bid"], realm_id="r", qbo_type="Purchase",
                      qbo_id="d2", txn_date=ws, amount=Decimal("40")))
        await s.commit()
        assert await R.sb_active_members(s, e["tid"], e["bid"], ws, we) == 2
        assert await R.sb_renewal_dates_current(s, e["tid"], e["bid"], ws, we) == 50
        assert await R.sb_cash_collected_rate(s, e["tid"], e["bid"], ws, we) == 50
        assert await R.sb_net_cash(s, e["tid"], e["bid"], ws, we) == 60
        # a past week does not get today's headcount written over it
        assert await R.sb_active_members(s, e["tid"], e["bid"], old, old + dt.timedelta(days=6)) is R.UNAVAILABLE


async def test_title_case_rename_leaves_the_value(board):
    """A stored sentence-case label moves to the catalog name. The week's number stays."""
    e = board
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: _apply(c, e["tid"]))
    async with SessionLocal() as s:
        vip = (await s.execute(select(ScorecardMetric).where(
            ScorecardMetric.tenant_id == e["tid"], ScorecardMetric.slug == "vip"))).scalar_one()
        vip.name = "VIP guests added"
        week = dt.date(2026, 11, 2)
        s.add(ScorecardValue(tenant_id=e["tid"], metric_id=vip.id, week_start=week,
                             value=Decimal("3"), source="resolver"))
        await s.commit()
        vid = vip.id
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: _apply(c, e["tid"]))
    async with SessionLocal() as s:
        vip = await s.get(ScorecardMetric, vid)
        stored = (await s.execute(select(ScorecardValue).where(
            ScorecardValue.metric_id == vid))).scalar_one()
        assert vip.name == "VIP Guests Added"
        assert stored.value == Decimal("3") and stored.source == "resolver"


async def test_build_marks_the_enterprise_board(board):
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: _apply(c, board["tid"]))
    async with SessionLocal() as s:
        payload = await build_scorecard(s, board["tid"], board["bid"], 13, today=dt.date(2026, 11, 10))
    assert payload["enterprise"] is True and payload["ids_after"] == 2
    l10 = [g for g in payload["groups"] if g.get("lens") == "l10"]
    assert {g["key"] for g in l10} >= {"sb_l10_growth", "sb_l10_enroll", "sb_l10_finance"}
    vip = next(r for g in l10 if g["key"] == "sb_l10_enroll" for r in g["rows"] if r["slug"] == "vip")
    assert vip["goal"] == 7 and vip["auto"] is True and vip["provenance"] == "live"
    leads = next(r for g in payload["groups"] if g["key"] == "sb_l10_growth" for r in g["rows"] if r["slug"] == "leads")
    assert leads["provenance"] == "live" and leads["auto"] is True
    red = next(r for g in l10 if g["key"] == "sb_l10_experience" for r in g["rows"] if r["slug"] == "red")
    assert red["goal"] is None and red["scored"] is False and red["auto"] is False
