"""ULRG scorecard resolvers (SPEC-ulrg-scorecard Part 3.1, Step 5). Controlled Sisu `Transaction`
data drives the Overall homes-closed resolver + the worker runner: week windowing, null≠zero,
idempotency, the Monday-settles-the-closed-week rule, and manual-vs-resolver overwrite.

Each test builds its own isolated tenant so counts are deterministic (no seeded transactions leak
in). Weeks are Monday-keyed: 2026-07-27 is a Monday, so its week is Mon 7/27 … Sun 8/2."""
import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import SessionLocal, engine
from app.models import (Base, Tenant, Business, Integration, Transaction,
                        ScorecardGroup, ScorecardMetric, ScorecardValue)
from app.services import scorecard_resolvers as R

MON = dt.date(2026, 7, 27)     # Monday — week start
WED = dt.date(2026, 7, 29)     # inside the 7/27 week
SUN = dt.date(2026, 8, 2)      # Sunday — week end (inclusive boundary)


async def _txn(s, tid, bid, status, close_date, ext, sale_price=350000):
    s.add(Transaction(tenant_id=tid, business_id=bid, source="sisu", external_id=ext,
                      status=status, close_date=close_date, sale_price=sale_price))


async def _val(s, tid, mid, week_start):
    return (await s.execute(select(ScorecardValue).where(
        ScorecardValue.metric_id == mid, ScorecardValue.week_start == week_start))).scalar_one()


@pytest.fixture
async def env():
    """A fresh tenant with an ULRG business, a connected+synced Sisu feed, and one resolver-backed
    Overall metric. A second business exists so business-scoping can be asserted."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        t = Tenant(slug=f"rt-{uuid.uuid4().hex[:8]}", name="Resolver Test")
        s.add(t); await s.flush()
        b = Business(tenant_id=t.id, key="ulrg", name="ULRG", tag="re")
        other = Business(tenant_id=t.id, key="other", name="Other", tag="ot")
        s.add_all([b, other]); await s.flush()
        s.add(Integration(tenant_id=t.id, provider="sisu", business_id=b.id,
                          status="connected", last_synced_at=dt.datetime.now(dt.timezone.utc)))
        g = ScorecardGroup(tenant_id=t.id, business_id=b.id, key="overall",
                           name="Overall", is_team_room=False)
        s.add(g); await s.flush()
        m = ScorecardMetric(tenant_id=t.id, group_id=g.id, name="ULRG Q2 - 250 Homes",
                            goal=Decimal("20"), direction="gte", type="flow",
                            resolver_key="ulrg_homes_closed", active=True)
        s.add(m); await s.flush()
        await s.commit()
        yield {"tid": t.id, "bid": b.id, "other": other.id, "mid": m.id}


async def test_homes_closed_counts_only_closed_in_window(env):
    async with SessionLocal() as s:
        tid, bid, other = env["tid"], env["bid"], env["other"]
        await _txn(s, tid, bid, "closed", MON, "a")                    # in window (Mon boundary)
        await _txn(s, tid, bid, "closed", SUN, "b")                    # in window (Sun boundary)
        await _txn(s, tid, bid, "closed", dt.date(2026, 7, 26), "c")   # day before → excluded
        await _txn(s, tid, bid, "closed", dt.date(2026, 8, 3), "d")    # day after → excluded
        await _txn(s, tid, bid, "pending", MON, "e")                   # not closed → excluded
        await _txn(s, tid, other, "closed", MON, "f")                  # other business → excluded
        await _txn(s, tid, bid, "closed", MON, "g", sale_price=0)      # $0 outbound referral → excluded
        await _txn(s, tid, bid, "closed", MON, "h", sale_price=None)   # no sale price → excluded
        await s.commit()
        n = await R.homes_closed(s, tid, bid, MON, SUN)
    assert n == 2.0   # only the two real closings (a, b) — $0/NULL referrals don't count


async def test_live_zero_is_real_zero_but_dead_feed_is_null(env):
    # Sisu live, no closings this week → a real 0 (not a gap)
    async with SessionLocal() as s:
        assert await R.homes_closed(s, env["tid"], env["bid"], MON, SUN) == 0.0
    # Sisu disconnected → None (collection gap, never 0)
    async with SessionLocal() as s:
        integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == env["tid"]))).scalar_one()
        integ.status = "disconnected"
        await s.commit()
    async with SessionLocal() as s:
        assert await R.homes_closed(s, env["tid"], env["bid"], MON, SUN) is None


async def test_run_resolvers_writes_resolver_source_and_is_idempotent(env):
    async with SessionLocal() as s:
        for i in range(3):
            await _txn(s, env["tid"], env["bid"], "closed", WED, f"x{i}")
        await s.commit()
    # look-back = 3 weeks → one row per week; the open week (7/27) carries the 3 closings, the two
    # prior weeks are a real 0 (Sisu live, nothing closed)
    assert await R.run_resolvers(SessionLocal, env["tid"], WED) == 3
    async with SessionLocal() as s:
        rows = {r.week_start: (float(r.value), r.source) for r in (await s.execute(select(
            ScorecardValue).where(ScorecardValue.metric_id == env["mid"]))).scalars().all()}
    assert len(rows) == 3
    assert rows[MON] == (3.0, "resolver")
    assert rows[dt.date(2026, 7, 20)] == (0.0, "resolver")   # live prior week, no closings → real 0
    # re-run updates in place — no duplicate rows for any (metric, week)
    await R.run_resolvers(SessionLocal, env["tid"], WED)
    async with SessionLocal() as s:
        rows = (await s.execute(select(ScorecardValue).where(
            ScorecardValue.metric_id == env["mid"]))).scalars().all()
    assert len(rows) == 3 and {float(r.value) for r in rows} == {3.0, 0.0}


async def test_resolver_overwrites_manual_but_a_gap_never_erases_it(env):
    async with SessionLocal() as s:
        s.add(ScorecardValue(tenant_id=env["tid"], metric_id=env["mid"], week_start=MON,
                             value=Decimal("99"), source="manual"))
        await _txn(s, env["tid"], env["bid"], "closed", WED, "y1")
        await s.commit()
    # a numeric resolver result displaces the manual value (with a logged warning)
    await R.run_resolvers(SessionLocal, env["tid"], WED)
    async with SessionLocal() as s:
        v = await _val(s, env["tid"], env["mid"], MON)
        assert float(v.value) == 1.0 and v.source == "resolver"
    # feed goes dark and someone re-types a manual number → the resolver's None must NOT erase it
    async with SessionLocal() as s:
        (await s.execute(select(Integration).where(
            Integration.tenant_id == env["tid"]))).scalar_one().status = "disconnected"
        v = await _val(s, env["tid"], env["mid"], MON)
        v.value, v.source = Decimal("42"), "manual"
        await s.commit()
    await R.run_resolvers(SessionLocal, env["tid"], WED)
    async with SessionLocal() as s:
        v = await _val(s, env["tid"], env["mid"], MON)
        assert float(v.value) == 42.0 and v.source == "manual"


async def test_trailing_lookback_settles_recent_weeks(env):
    # mid-week (Wed 7/29), the trailing look-back re-settles the prior week too, not just the open
    # one — so a late sync or a missed daily run self-heals without waiting for a special Monday tick
    async with SessionLocal() as s:
        await _txn(s, env["tid"], env["bid"], "closed", dt.date(2026, 7, 22), "p1")  # 7/20 week (prior)
        await _txn(s, env["tid"], env["bid"], "closed", dt.date(2026, 7, 29), "c1")  # 7/27 week (open)
        await s.commit()
    await R.run_resolvers(SessionLocal, env["tid"], WED)             # look-back covers 7/27, 7/20, 7/13
    async with SessionLocal() as s:
        rows = {r.week_start: float(r.value) for r in (await s.execute(select(
            ScorecardValue).where(ScorecardValue.metric_id == env["mid"]))).scalars().all()}
    assert rows.get(dt.date(2026, 7, 27)) == 1.0 and rows.get(dt.date(2026, 7, 20)) == 1.0


async def test_a_gap_clears_a_stale_resolver_value(env):
    # a resolver-sourced number exists, then Sisu goes dark: the resolver's None must clear its own
    # stale figure (source stays "resolver"), distinct from never erasing a *manual* value
    async with SessionLocal() as s:
        s.add(ScorecardValue(tenant_id=env["tid"], metric_id=env["mid"], week_start=MON,
                             value=Decimal("5"), source="resolver"))
        (await s.execute(select(Integration).where(
            Integration.tenant_id == env["tid"]))).scalar_one().status = "disconnected"
        await s.commit()
    await R.run_resolvers(SessionLocal, env["tid"], WED)
    async with SessionLocal() as s:
        v = await _val(s, env["tid"], env["mid"], MON)
        assert v.value is None and v.source == "resolver"


async def test_overwriting_a_manual_value_logs_a_warning(env, caplog):
    import logging
    async with SessionLocal() as s:
        s.add(ScorecardValue(tenant_id=env["tid"], metric_id=env["mid"], week_start=MON,
                             value=Decimal("99"), source="manual"))
        await _txn(s, env["tid"], env["bid"], "closed", MON, "w1")
        await s.commit()
    with caplog.at_level(logging.WARNING, logger="app"):
        await R.run_resolvers(SessionLocal, env["tid"], WED)
    assert any("overrode a manual value" in r.getMessage() for r in caplog.records)
    async with SessionLocal() as s:
        v = await _val(s, env["tid"], env["mid"], MON)
        assert v.source == "resolver" and float(v.value) == 1.0


async def test_a_raising_resolver_is_isolated(env):
    @R.resolver("boom_test")
    async def _boom(s, tenant_id, business_id, ws, we):
        raise RuntimeError("kaboom")
    async with SessionLocal() as s:
        g = (await s.execute(select(ScorecardGroup).where(
            ScorecardGroup.tenant_id == env["tid"]))).scalar_one()
        s.add(ScorecardMetric(tenant_id=env["tid"], group_id=g.id, name="Boom", goal=Decimal("1"),
                              direction="gte", type="flow", resolver_key="boom_test", active=True))
        await _txn(s, env["tid"], env["bid"], "closed", WED, "g1")
        await s.commit()
    await R.run_resolvers(SessionLocal, env["tid"], WED)            # must not raise
    async with SessionLocal() as s:
        v = await _val(s, env["tid"], env["mid"], MON)
        assert float(v.value) == 1.0                                # the healthy resolver still wrote
