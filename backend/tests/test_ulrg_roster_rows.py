"""ULRG L10 recruiting-result rows fed by the Sisu team roster: Onboardings, Offboardings, Net agent
count. The roster sync (team list → start dates, departures) and the three resolvers that read it.

Weeks are Monday-keyed: 2026-10-12 is a Monday, so its week is Mon 10/12 … Sun 10/18."""
import datetime as dt
import json
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import SessionLocal, engine
from app.integrations import sisu
from app.models import Agent, Base, Business, Integration, ScorecardGroup, Tenant
from app.security import enc
from app.services import scorecard_resolvers as R
from app.services import sync as sync_mod

DAVIS, SLC, OTHER = 43958, 43957, 99999
W1 = (dt.date(2026, 10, 5), dt.date(2026, 10, 11))
W2 = (dt.date(2026, 10, 12), dt.date(2026, 10, 18))


@pytest.fixture
async def env():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        t = Tenant(slug=f"rr-{uuid.uuid4().hex[:8]}", name="Roster Test")
        s.add(t); await s.flush()
        b = Business(tenant_id=t.id, key=f"re{uuid.uuid4().hex[:6]}", name="ULRG", tag="re")
        s.add(b); await s.flush()
        s.add(Integration(tenant_id=t.id, provider="sisu", business_id=b.id, status="connected",
                          last_synced_at=dt.datetime.now(dt.timezone.utc),
                          access_token_enc=enc(json.dumps({"username": "u", "token": "t"}))))
        s.add_all([ScorecardGroup(tenant_id=t.id, business_id=b.id, key="davis", name="Davis",
                                  is_team_room=True, sisu_group_id=DAVIS),
                   ScorecardGroup(tenant_id=t.id, business_id=b.id, key="slc", name="SLC",
                                  is_team_room=True, sisu_group_id=SLC)])
        await s.commit()
        yield {"tid": t.id, "bid": b.id}


def _agent(tid, ext, *, active=True, groups=None, start=None, ended=None, name=None):
    return Agent(tenant_id=tid, source="sisu", external_id=ext, name=name or f"Agent {ext}",
                 is_active=active, sisu_group_ids=groups, start_date=start, ended_date=ended)


async def _count(key, env, week, sgid):
    async with SessionLocal() as s:
        return await R.RESOLVERS[key](s, env["tid"], env["bid"], *week,
                                      group={"key": "x", "sisu_group_id": sgid})


async def test_roster_rows_count_by_office_and_week(env):
    tid = env["tid"]
    async with SessionLocal() as s:
        s.add_all([
            _agent(tid, "old-d", groups=[DAVIS], start=dt.date(2024, 1, 8)),
            _agent(tid, "new-d", groups=[DAVIS, 48550], start=dt.date(2026, 10, 14)),    # joined W2
            _agent(tid, "new-both", groups=[DAVIS, SLC], start=dt.date(2026, 10, 6)),     # joined W1, two offices
            _agent(tid, "left-d", active=False, groups=[DAVIS], start=dt.date(2025, 3, 3),
                   ended=dt.date(2026, 10, 15)),                                         # left W2
            _agent(tid, "slc-1", groups=[SLC], start=dt.date(2025, 6, 2)),
            _agent(tid, "remote", groups=[OTHER], start=dt.date(2026, 10, 13)),          # out-of-office
            _agent(tid, "gone-long-ago", active=False, groups=[DAVIS]),                  # no dates: not counted
        ])
        await s.commit()

    # Davis: onboard W1 = new-both, W2 = new-d; offboard W2 = left-d
    assert await _count("ulrg_roster_onboarded", env, W1, DAVIS) == 1.0
    assert await _count("ulrg_roster_onboarded", env, W2, DAVIS) == 1.0
    assert await _count("ulrg_roster_offboarded", env, W1, DAVIS) == 0.0
    assert await _count("ulrg_roster_offboarded", env, W2, DAVIS) == 1.0
    # Net at week end: W1 (Sun 10/11) = old-d, new-both, left-d (still here) = 3;
    # W2 (Sun 10/18) = old-d, new-both, new-d = 3 (left-d gone on 10/15)
    assert await _count("ulrg_roster_net", env, W1, DAVIS) == 3.0
    assert await _count("ulrg_roster_net", env, W2, DAVIS) == 3.0
    # SLC counts the two-office agent too; Overall (no office) is the whole team incl. out-of-office
    assert await _count("ulrg_roster_net", env, W2, SLC) == 2.0
    assert await _count("ulrg_roster_net", env, W2, None) == 5.0
    assert await _count("ulrg_roster_onboarded", env, W2, None) == 2.0       # new-d + remote

    # the drill lists exactly what was counted
    async with SessionLocal() as s:
        recs = await R.resolver_records(s, tid, env["bid"], "ulrg_roster_net", *W2,
                                        group={"sisu_group_id": DAVIS})
    assert len(recs) == 3 and {r["client"] for r in recs} == {"Agent old-d", "Agent new-both", "Agent new-d"}


async def test_no_roster_dates_yet_is_unavailable_not_zero(env):
    async with SessionLocal() as s:
        s.add(_agent(env["tid"], "a", groups=[DAVIS]))            # synced groups, but no start dates
        await s.commit()
    assert await _count("ulrg_roster_net", env, W2, DAVIS) is R.UNAVAILABLE
    assert await _count("ulrg_roster_onboarded", env, W2, None) is R.UNAVAILABLE


def _fake_sisu(monkeypatch, team, records):
    async def team_agents(creds):
        return team
    async def agent_records(creds, ids, concurrency=8):
        return {str(i): records.get(str(i), {"agent_groups": []}) for i in ids}
    monkeypatch.setattr(sisu, "fetch_team_agents", team_agents)
    monkeypatch.setattr(sisu, "fetch_agent_records", agent_records)


def _groups(*gids):
    return {"agent_groups": [{"group_id": g, "is_included": True} for g in gids]}


async def test_roster_sync_adds_dates_and_departs(env, monkeypatch):
    tid = env["tid"]
    async with SessionLocal() as s:
        s.add_all([
            _agent(tid, "stay", groups=[DAVIS]),
            _agent(tid, "leaving", groups=[SLC]),                                  # active, not on team
            _agent(tid, "recent-exit", active=False, groups=[DAVIS]),              # inactive, in an office
            _agent(tid, "historic", active=False, groups=[48550]),                 # long gone, no office
        ])
        await s.commit()
    team = [{"agent_id": "stay", "start_dt": "Mon, 08 Jan 2024 00:00:00 GMT", "status": "N"},
            {"agent_id": "brand-new", "first_name": "New", "last_name": "Agent",
             "start_dt": "Wed, 14 Oct 2026 00:00:00 GMT", "status": "N"}]
    records = {"stay": _groups(DAVIS), "brand-new": _groups(SLC),
               "leaving": {"agent_groups": [], "updated_ts": "Mon, 19 Oct 2026 19:30:00 GMT"},
               "recent-exit": {"agent_groups": [], "updated_ts": "Tue, 20 Oct 2026 00:38:00 GMT"},
               "historic": _groups(48550)}
    _fake_sisu(monkeypatch, team, records)
    async with SessionLocal() as s:
        await sync_mod.sync_agent_offices(s, tid)
    async with SessionLocal() as s:
        a = {x.external_id: x for x in (await s.execute(select(Agent).where(Agent.tenant_id == tid))).scalars()}
    assert a["brand-new"].is_active and a["brand-new"].start_date == dt.date(2026, 10, 14)
    assert a["brand-new"].name == "New Agent" and a["brand-new"].sisu_group_ids == [SLC]
    assert a["stay"].start_date == dt.date(2024, 1, 8) and a["stay"].ended_date is None
    # departures are dated from Sisu's last update, and keep the office they left from
    assert not a["leaving"].is_active and a["leaving"].ended_date == dt.date(2026, 10, 19)
    assert a["leaving"].sisu_group_ids == [SLC]
    assert a["recent-exit"].ended_date == dt.date(2026, 10, 20) and a["recent-exit"].sisu_group_ids == [DAVIS]
    assert a["historic"].ended_date is None                                    # not in an office: untouched


@pytest.mark.parametrize("team", [None, [{"agent_id": "a1", "status": "N"}]])
async def test_roster_sync_never_reads_a_failed_or_short_list_as_departures(env, monkeypatch, team):
    tid = env["tid"]
    async with SessionLocal() as s:
        s.add_all([_agent(tid, f"a{i}", groups=[DAVIS]) for i in range(1, 7)])
        await s.commit()
    _fake_sisu(monkeypatch, team, {})
    async with SessionLocal() as s:
        await sync_mod.sync_agent_offices(s, tid)
    async with SessionLocal() as s:
        rows = (await s.execute(select(Agent).where(Agent.tenant_id == tid))).scalars().all()
    assert all(r.is_active and r.ended_date is None for r in rows)            # nobody "left"


async def test_add_rows_script_adds_once_backfills_only_its_rows(env, monkeypatch):
    import sys
    from app.models import ScorecardMetric, ScorecardValue
    from app.services import roles
    from scripts import add_ulrg_recruiting_rows as S
    tid = env["tid"]
    async with SessionLocal() as s:
        t = await s.get(Tenant, tid)
        b = await s.get(Business, env["bid"])
        b.kind = roles.REAL_ESTATE
        s.add_all([ScorecardGroup(tenant_id=tid, business_id=b.id, key="utco", name="Utah County",
                                  is_team_room=True, sisu_group_id=45345),
                   ScorecardGroup(tenant_id=tid, business_id=b.id, key="overall", name="Overall",
                                  is_team_room=False)])
        await s.flush()
        davis = (await s.execute(select(ScorecardGroup).where(ScorecardGroup.tenant_id == tid,
                                                              ScorecardGroup.key == "davis"))).scalar_one()
        met = ScorecardMetric(tenant_id=tid, group_id=davis.id, name="Appointments Met", type="flow",
                              direction="gte", goal=Decimal("30"), resolver_key="ulrg_team_appts_met",
                              sort_order=1, active=True)
        s.add(met)
        await s.flush()
        monday = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
        s.add(ScorecardValue(tenant_id=tid, metric_id=met.id, week_start=monday - dt.timedelta(days=14),
                             value=Decimal("7"), source="manual"))       # a hand-corrected MET week
        s.add(_agent(tid, "d1", groups=[DAVIS], start=monday - dt.timedelta(days=60)))
        slug = t.slug
        await s.commit()
    _fake_sisu(monkeypatch, [{"agent_id": "d1", "start_dt": None, "status": "N"}], {"d1": _groups(DAVIS)})

    monkeypatch.setattr(sys, "argv", ["x", "--tenant", slug, "--apply"])
    assert await S.main() == 0
    assert await S.main() == 0                                      # second run: nothing new
    async with SessionLocal() as s:
        rows = (await s.execute(select(ScorecardMetric).where(ScorecardMetric.tenant_id == tid))).scalars().all()
        added = [m for m in rows if m.id != met.id]
        assert len(added) == 16                                     # 4 rows x (davis, slc, utco, overall)
        net = {m.group_id: m for m in added if m.name == "Net Agent Count"}
        assert float(net[davis.id].goal) == 35 and net[davis.id].type == "snapshot"
        assert {float(m.goal) for m in added if m.name == "Hello Week Entries"} == {4.0, 12.0}
        vals = (await s.execute(select(ScorecardValue).where(ScorecardValue.metric_id == net[davis.id].id))).scalars().all()
        assert vals and all(float(v.value) == 1.0 for v in vals)   # d1 has been on the team all 5 weeks
        # the MET row was never re-run: its hand-corrected value and source are exactly as they were
        mv = (await s.execute(select(ScorecardValue).where(ScorecardValue.metric_id == met.id))).scalars().all()
        assert [(float(v.value), v.source) for v in mv] == [(7.0, "manual")]
