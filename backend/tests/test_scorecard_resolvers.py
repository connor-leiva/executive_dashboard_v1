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
from app.models import (Agent, Base, Tenant, Business, Integration, Transaction,
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


# ── per-team resolvers (SPEC Step 5, agent→office attribution) ────────────────
DAVIS_GID, SLC_GID = 43958, 43957     # Sisu office group_ids
DAVIS = {"key": "davis", "sisu_group_id": DAVIS_GID}


async def _txn_agent(s, tid, bid, agent_id, status, ext, close_date=None,
                     contract_date=None, sale_price=350000, appt_met_date=None, signed_date=None):
    s.add(Transaction(tenant_id=tid, business_id=bid, source="sisu", external_id=ext, status=status,
                      close_date=close_date, contract_date=contract_date, sale_price=sale_price,
                      agent_id=agent_id, appt_met_date=appt_met_date, signed_date=signed_date))


@pytest.fixture
async def team_env():
    """A tenant whose ULRG business has a Davis scorecard team (mapped to office 43958) and a synced
    roster: 2 Davis agents, 1 SLC agent, 1 out-of-office agent."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        t = Tenant(slug=f"tt-{uuid.uuid4().hex[:8]}", name="Team Test")
        s.add(t); await s.flush()
        b = Business(tenant_id=t.id, key="ulrg", name="ULRG", tag="re")
        s.add(b); await s.flush()
        s.add(Integration(tenant_id=t.id, provider="sisu", business_id=b.id,
                          status="connected", last_synced_at=dt.datetime.now(dt.timezone.utc)))
        agents = {
            "d1": Agent(tenant_id=t.id, source="sisu", external_id="d1", name="Davis One",
                        sisu_group_ids=[DAVIS_GID, 48550]),          # office + a tier
            "d2": Agent(tenant_id=t.id, source="sisu", external_id="d2", name="Davis Two",
                        sisu_group_ids=[DAVIS_GID]),
            "s1": Agent(tenant_id=t.id, source="sisu", external_id="s1", name="Slc One",
                        sisu_group_ids=[SLC_GID]),
            "n1": Agent(tenant_id=t.id, source="sisu", external_id="n1", name="Nomad",
                        sisu_group_ids=[99999]),                     # out-of-office (e.g. Team Alabama)
        }
        s.add_all(list(agents.values())); await s.flush()
        g = ScorecardGroup(tenant_id=t.id, business_id=b.id, key="davis", name="Davis",
                           is_team_room=True, sisu_group_id=DAVIS_GID)
        s.add(g); await s.flush()
        m = ScorecardMetric(tenant_id=t.id, group_id=g.id, name="130 Homes Sold Q2", goal=Decimal("8"),
                            direction="gte", type="flow", resolver_key="ulrg_team_homes_closed", active=True)
        muc = ScorecardMetric(tenant_id=t.id, group_id=g.id, name="Under Contract", goal=Decimal("10"),
                              direction="gte", type="flow", resolver_key="ulrg_team_under_contract",
                              active=True, sort_order=1)
        s.add_all([m, muc]); await s.flush()
        await s.commit()
        yield {"tid": t.id, "bid": b.id, "mid": m.id, "muc": muc.id,
               "d": [agents["d1"].id, agents["d2"].id], "s": agents["s1"].id, "n": agents["n1"].id}


async def test_team_homes_closed_only_counts_the_offices_agents(team_env):
    e = team_env
    async with SessionLocal() as s:
        await _txn_agent(s, e["tid"], e["bid"], e["d"][0], "closed", "a", close_date=MON)
        await _txn_agent(s, e["tid"], e["bid"], e["d"][1], "closed", "b", close_date=SUN)
        await _txn_agent(s, e["tid"], e["bid"], e["s"], "closed", "c", close_date=MON)      # other office
        await _txn_agent(s, e["tid"], e["bid"], e["n"], "closed", "d", close_date=MON)      # out-of-office
        await _txn_agent(s, e["tid"], e["bid"], e["d"][0], "closed", "z", close_date=MON, sale_price=0)  # $0
        await s.commit()
        n = await R.team_homes_closed(s, e["tid"], e["bid"], MON, SUN, group=DAVIS)
    assert n == 2.0     # only the two Davis-agent real sales


async def test_team_under_contract_uses_contract_date(team_env):
    e = team_env
    async with SessionLocal() as s:
        await _txn_agent(s, e["tid"], e["bid"], e["d"][0], "pending", "u1", contract_date=WED)  # UC this week
        await _txn_agent(s, e["tid"], e["bid"], e["d"][1], "closed", "u2", contract_date=MON,
                         close_date=dt.date(2026, 9, 1))                                        # UC this wk, later closed
        await _txn_agent(s, e["tid"], e["bid"], e["s"], "pending", "u3", contract_date=WED)     # other office
        await _txn_agent(s, e["tid"], e["bid"], e["d"][0], "pending", "u4",
                         contract_date=dt.date(2026, 7, 20))                                    # prior week
        await s.commit()
        n = await R.team_under_contract(s, e["tid"], e["bid"], MON, SUN, group=DAVIS)
    assert n == 2.0


async def test_team_appts_met_and_signed_use_their_dates(team_env):
    e = team_env
    async with SessionLocal() as s:
        # appointments HELD this week — counted by appt_met_date, no sale_price gate (am1 has none)
        await _txn_agent(s, e["tid"], e["bid"], e["d"][0], "active", "am1", appt_met_date=WED, sale_price=None)
        await _txn_agent(s, e["tid"], e["bid"], e["d"][1], "closed", "am2", appt_met_date=MON,
                         close_date=dt.date(2026, 9, 1))                                    # met this wk, later closed
        await _txn_agent(s, e["tid"], e["bid"], e["s"], "active", "am3", appt_met_date=WED)  # other office
        await _txn_agent(s, e["tid"], e["bid"], e["d"][0], "active", "am4",
                         appt_met_date=dt.date(2026, 7, 20))                                 # prior week
        # agreements signed this week — counted by signed_date
        await _txn_agent(s, e["tid"], e["bid"], e["d"][0], "active", "sg1", signed_date=WED)
        await _txn_agent(s, e["tid"], e["bid"], e["d"][0], "active", "sg2",
                         signed_date=dt.date(2026, 7, 20))                                   # prior week
        await s.commit()
        am = await R.team_appts_met(s, e["tid"], e["bid"], MON, SUN, group=DAVIS)
        sg = await R.team_signed(s, e["tid"], e["bid"], MON, SUN, group=DAVIS)
    assert am == 2.0   # d1 (Wed) + d2 (Mon), both Davis; other office & prior week excluded
    assert sg == 1.0   # d1 signed this week; prior-week signing excluded


async def test_resolver_records_match_the_count(team_env):
    e = team_env
    async with SessionLocal() as s:
        await _txn_agent(s, e["tid"], e["bid"], e["d"][0], "closed", "r1", close_date=MON)
        await _txn_agent(s, e["tid"], e["bid"], e["d"][1], "closed", "r2", close_date=SUN)
        await _txn_agent(s, e["tid"], e["bid"], e["s"], "closed", "r3", close_date=MON)   # other office → excluded
        await _txn_agent(s, e["tid"], e["bid"], e["d"][0], "closed", "r0", close_date=MON, sale_price=0)  # $0 → excluded
        await s.commit()
        n = await R.team_homes_closed(s, e["tid"], e["bid"], MON, SUN, group=DAVIS)
        recs = await R.resolver_records(s, e["tid"], e["bid"], "ulrg_team_homes_closed", MON, SUN, group=DAVIS)
    assert n == len(recs) == 2.0                            # the drill-down list is exactly the count
    assert {r["id"] for r in recs} == {"r1", "r2"}          # same office/real-sale filter as the count
    assert all("date" in r and "sale_price" in r and "agent" in r for r in recs)


async def test_team_live_zero_vs_unsynced_roster_and_unmapped_group(team_env):
    e = team_env
    # Davis agents exist but nothing closed this week → real 0 (not a gap)
    async with SessionLocal() as s:
        assert await R.team_homes_closed(s, e["tid"], e["bid"], MON, SUN, group=DAVIS) == 0.0
    # an unmapped group (overall, sisu_group_id None) → None, never a per-team count
    async with SessionLocal() as s:
        assert await R.team_homes_closed(s, e["tid"], e["bid"], MON, SUN,
                                         group={"key": "overall", "sisu_group_id": None}) is None
    # roster not synced (no agent carries memberships) → None (gap), not 0
    async with SessionLocal() as s:
        for a in (await s.execute(select(Agent).where(Agent.tenant_id == e["tid"]))).scalars():
            a.sisu_group_ids = None
        await s.commit()
    async with SessionLocal() as s:
        assert await R.team_homes_closed(s, e["tid"], e["bid"], MON, SUN, group=DAVIS) is None


async def test_synced_but_empty_office_is_a_real_zero(team_env):
    e = team_env
    # roster IS synced, but this mapped office has no agents assigned → a real 0, not a gap (None)
    async with SessionLocal() as s:
        n = await R.team_homes_closed(s, e["tid"], e["bid"], MON, SUN,
                                      group={"key": "ghost", "sisu_group_id": 55555})
    assert n == 0.0


async def test_run_resolvers_wires_per_team_group_context(team_env):
    e = team_env
    async with SessionLocal() as s:
        await _txn_agent(s, e["tid"], e["bid"], e["d"][0], "closed", "h1", close_date=WED)
        await _txn_agent(s, e["tid"], e["bid"], e["d"][1], "closed", "h2", close_date=WED)
        await _txn_agent(s, e["tid"], e["bid"], e["d"][0], "pending", "uc1", contract_date=WED)
        await s.commit()
    await R.run_resolvers(SessionLocal, e["tid"], WED)
    async with SessionLocal() as s:
        homes = await _val(s, e["tid"], e["mid"], MON)
        uc = await _val(s, e["tid"], e["muc"], MON)
        assert float(homes.value) == 2.0 and homes.source == "resolver"
        assert float(uc.value) == 1.0 and uc.source == "resolver"
