"""The Sympli lending board: loan timelines, the as-of figures, office/LO scoping, null≠zero, the
leadership-only agent drills, plan gates, the share link and the setup script.

Weeks are Monday-keyed: 2026-10-12 is a Monday, so its week is Mon 10/12 … Sun 10/18."""
import datetime as dt
import json
import sys
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal, engine
from app.integrations import arive, sisu
from app.main import app
from app.models import (Agent, Base, Business, Integration, MetricRecord, ScorecardGate, ScorecardGoal,
                        ScorecardGroup, ScorecardMetric, ScorecardValue, Tenant, Transaction, User)
from app.security import enc, hash_pw, make_token
from app.seed import seed
from app.seed_sympli_scorecard import board_groups, load_data, load_sympli_scorecard
from app.services import roles
from app.services import scorecard_resolvers as R
from app.services import scorecard_sympli as Y
from app.services import sync as sync_mod

W = (dt.date(2026, 10, 12), dt.date(2026, 10, 18))
PRIOR = (dt.date(2026, 10, 5), dt.date(2026, 10, 11))
SLC, DAVIS = 43957, 43958
TRANSPORT = ASGITransport(app=app)


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(t):
    return {"Authorization": f"Bearer {t}"}


def _hist(**days):
    return {k.upper(): v for k, v in days.items()}


def _loan(tid, bid, ext, *, seg="pipeline", status="LOAN_SETUP", amount=400000, occurred=dt.date(2026, 10, 14),
          history=None, lo="jared@sympli.test", state="UT", **tl):
    timeline = {"history": history if history is not None else {status: occurred.isoformat()}, **tl}
    return MetricRecord(tenant_id=tid, business_id=bid, source="arive", kind="loan", external_id=ext,
                        name=f"Borrower {ext}", amount=Decimal(amount), status=status, segment=seg, occurred_on=occurred,
                        meta={"status": status, "segment": seg, "lo_email": lo, "property_state": state,
                              "created_on": tl.get("created_on"), "timeline": timeline})


@pytest.fixture
async def env():
    """A workspace with a brokerage and a lender, Arive on the lender and Sisu on the brokerage."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        t = Tenant(slug=f"sy-{uuid.uuid4().hex[:8]}", name="Lender Test")
        s.add(t); await s.flush()
        b = Business(tenant_id=t.id, key="jv", name="Lender", tag="jv", kind=roles.COMMISSION_JV, sort_order=1)
        u = Business(tenant_id=t.id, key="re", name="Brokerage", tag="re", kind=roles.REAL_ESTATE, sort_order=0)
        s.add_all([b, u]); await s.flush()
        now = dt.datetime.now(dt.timezone.utc)
        s.add_all([
            Integration(tenant_id=t.id, provider="arive", business_id=b.id, status="connected",
                        last_synced_at=now, config={"states": ["UT"]}),
            Integration(tenant_id=t.id, provider="sisu", business_id=u.id, status="connected", last_synced_at=now,
                        config={"sympli_mortgage_vids": [155743], "cash_vids": [12], "referral_domains": ["liveutah.test"]}),
        ])
        await s.commit()
        yield {"tid": t.id, "bid": b.id, "uid": u.id, "slug": t.slug}


async def _run(key, env, week=W, **group):
    async with SessionLocal() as s:
        return await R.RESOLVERS[key](s, env["tid"], env["bid"], *week, group=group or None)


async def _add(*rows):
    async with SessionLocal() as s:
        s.add_all(rows)
        await s.commit()


# ── arive.loan_timeline ──────────────────────────────────────────────────────────────────────────

def test_timeline_reads_the_real_dates_from_status_history():
    d = {"loanStatusHistory": [
            {"status": "CLEAR_TO_CLOSE", "date": "2026-10-30"}, {"status": "LOAN_SETUP", "date": "2026-02-26"},
            {"status": "LOAN_FUNDED", "date": "2026-11-02"}, {"status": "RE_SUBMITTAL", "date": "2026-08-01"},
            {"status": "RE_SUBMITTAL", "date": "2026-08-09"}, {"status": "BROKER_CHECK_RECEIVED", "date": "2026-11-09"}],
         "lockDate": "2026-10-12T07:00:00.000Z", "grossLoanRevenue": "26,665.94",
         "keyDates": {"tridDate": "2026-02-20"}, "referralContactSourceEmail": "Agent@LiveUtah.test"}
    t = arive.loan_timeline(d)
    assert t["funded_on"] == "2026-11-02"              # the funding, not the later broker check
    assert t["applied_on"] == "2026-02-26" and t["lock_on"] == "2026-10-12"
    assert t["history"]["RE_SUBMITTAL"] == "2026-08-09"   # latest of a repeated status
    assert t["gross_revenue"] == 26665.94 and t["referral_email"] == "agent@liveutah.test"
    assert arive.loan_timeline({"keyDates": {"tridDate": "2026-02-20"}})["applied_on"] == "2026-02-20"
    assert "funded_on" not in arive.loan_timeline({"loanStatusHistory": []})


async def test_sync_stores_the_timeline_apart_from_what_dashboards_read(env, monkeypatch):
    rows = [{"ariveLoanId": "F1", "currentLoanStatus": {"status": "BROKER_CHECK_RECEIVED", "date": "2026-11-09"},
             "baseLoanAmount": 300000, "subjectProperty": {"state": "UT"}, "createDateTime": "2026-02-01T10:00:00Z"},
            {"ariveLoanId": "P1", "currentLoanStatus": {"status": "LOAN_SETUP", "date": "2026-10-14"},
             "baseLoanAmount": 200000, "subjectProperty": {"state": "UT"}, "createDateTime": "2026-10-01T10:00:00Z"},
            {"ariveLoanId": "TX", "currentLoanStatus": {"status": "LOAN_SETUP", "date": "2026-10-14"},
             "baseLoanAmount": 100000, "subjectProperty": {"state": "TX"}, "createDateTime": "2026-10-01T10:00:00Z"}]
    asked = []

    async def fake_loans(cid, secret, api_key, max_loans=2000):
        return rows

    async def fake_detail(ids, cid, secret, api_key, concurrency=8):
        asked.extend(ids)
        return {"F1": {"loanStatusHistory": [{"status": "LOAN_FUNDED", "date": "2026-11-02"}], "grossLoanRevenue": 6000},
                "P1": {"loanStatusHistory": [{"status": "LOAN_SETUP", "date": "2026-10-14"}], "grossLoanRevenue": 4000,
                       "referralContactSourceEmail": "a@liveutah.test"}}
    monkeypatch.setattr(arive, "get_loans", fake_loans)
    monkeypatch.setattr(arive, "get_loans_detail", fake_detail)
    async with SessionLocal() as s:
        integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == env["tid"], Integration.provider == "arive"))).scalar_one()
        integ.access_token_enc = enc(json.dumps({"client_id": "c", "secret": "s", "api_key": "k"}))
        await s.commit()
        await sync_mod.sync_arive(s, env["tid"], integ)
    async with SessionLocal() as s:
        recs = {r.external_id: r for r in (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == env["tid"], MetricRecord.kind == "loan"))).scalars()}
    assert set(asked) == {"F1", "P1"}                                    # the market's loans, not Texas
    assert recs["F1"].meta["timeline"]["funded_on"] == "2026-11-02"
    assert recs["P1"].meta["created_on"] == "2026-10-01"
    # a pipeline loan's referral stays inside the timeline: the tab's by-source view must not move
    assert "referral_email" not in recs["P1"].meta and recs["P1"].meta["timeline"]["referral_email"] == "a@liveutah.test"
    assert "timeline" not in recs["TX"].meta


# ── loan figures ─────────────────────────────────────────────────────────────────────────────────

async def test_funded_counts_the_funding_week_not_the_broker_check_week(env):
    t, b = env["tid"], env["bid"]
    await _add(
        # funded 10/14, but the broker check on 10/21 moved the current-status date a week later
        _loan(t, b, "moved", seg="funded", status="BROKER_CHECK_RECEIVED", occurred=dt.date(2026, 10, 21),
              history=_hist(loan_funded="2026-10-14", broker_check_received="2026-10-21"), funded_on="2026-10-14"),
        _loan(t, b, "other-lo", seg="funded", status="LOAN_FUNDED", occurred=dt.date(2026, 10, 15), lo="kitty@sympli.test",
              history=_hist(loan_funded="2026-10-15"), funded_on="2026-10-15"),
        _loan(t, b, "texas", seg="funded", status="LOAN_FUNDED", occurred=dt.date(2026, 10, 15), state="TX",
              history=_hist(loan_funded="2026-10-15"), funded_on="2026-10-15"),
    )
    assert await _run("sympli_funded", env) == 2.0
    assert await _run("sympli_funded", env, week=(dt.date(2026, 10, 19), dt.date(2026, 10, 25))) == 0.0
    assert await _run("sympli_funded", env, lo_email="KITTY@sympli.test") == 1.0
    async with SessionLocal() as s:
        recs = await R.resolver_records(s, t, b, "sympli_funded", *W, group=None)
    assert sorted(r["id"] for r in recs) == ["moved", "other-lo"] and recs[0]["date"] in ("2026-10-14", "2026-10-15")


async def test_pipeline_is_rebuilt_as_of_each_week_and_ignores_stale_files(env):
    t, b = env["tid"], env["bid"]
    await _add(
        # in processing during W, funded the week after: it was open then, even though it's funded now
        _loan(t, b, "later-funded", seg="funded", status="LOAN_FUNDED", occurred=dt.date(2026, 10, 22),
              history=_hist(loan_setup="2026-10-01", disclosure_sent="2026-10-10", loan_funded="2026-10-22"),
              funded_on="2026-10-22"),
        _loan(t, b, "conditional", status="APPROVED_WITH_CONDITION", amount=500000, occurred=dt.date(2026, 10, 13),
              history=_hist(loan_setup="2026-09-20", underwriting_submitted="2026-10-08", approved_with_condition="2026-10-13")),
        _loan(t, b, "ctc", status="DOCS_OUT", occurred=dt.date(2026, 10, 12),
              history=_hist(docs_out="2026-10-12", clear_to_close="2026-10-12", loan_setup="2026-09-01")),
        # three statuses stamped the same day, across stages: the furthest along wins, not dict order
        _loan(t, b, "same-day", status="DISCLOSURE_SENT", occurred=dt.date(2026, 10, 14),
              history=_hist(preapproved="2026-10-14", disclosure_sent="2026-10-14", loan_setup="2026-10-14")),
        # last moved 120 days before week end: stale, whatever its status says
        _loan(t, b, "stale", status="APPROVED_WITH_CONDITION", occurred=dt.date(2026, 6, 20),
              history=_hist(approved_with_condition="2026-06-20")),
        _loan(t, b, "inquiry", status="PREAPPROVED", occurred=dt.date(2026, 10, 2), history=_hist(preapproved="2026-10-02")),
        _loan(t, b, "dead", seg="dead", status="ADVERSE", occurred=dt.date(2026, 10, 5),
              history=_hist(loan_setup="2026-09-01", adverse="2026-10-05")),
    )
    assert await _run("sympli_stage_processing", env) == 2.0               # later-funded + same-day
    assert await _run("sympli_stage_application", env) == 0.0
    assert await _run("sympli_stage_conditional", env) == 1.0
    assert await _run("sympli_stage_conditional_volume", env) == 500.0     # $K
    assert await _run("sympli_stage_ctc", env) == 1.0
    assert await _run("sympli_stage_pre", env) == 1.0
    assert await _run("sympli_open_count", env) == 4.0                     # the inquiry isn't an open file
    assert await _run("sympli_stale_files", env) == 1.0
    # a week before: the conditional file was still in underwriting
    assert await _run("sympli_stage_underwriting", env, week=PRIOR) == 1.0
    assert await _run("sympli_stage_conditional", env, week=PRIOR) == 0.0
    async with SessionLocal() as s:
        recs = await R.resolver_records(s, t, b, "sympli_open_count", *W, group=None)
    assert {r["id"] for r in recs} == {"later-funded", "conditional", "ctc", "same-day"}   # reads back as the count


async def test_margin_is_amount_weighted_and_needs_a_denominator(env):
    t, b = env["tid"], env["bid"]
    assert await _run("sympli_margin_locks", env) is R.UNAVAILABLE         # no timelines yet: can't look
    await _add(
        _loan(t, b, "a", amount=100000, lock_on="2026-10-13", gross_revenue=3000),
        _loan(t, b, "b", amount=300000, lock_on="2026-10-14", gross_revenue=6000),
        _loan(t, b, "no-rev", amount=900000, lock_on="2026-10-14"),
        _loan(t, b, "dead", seg="dead", status="ADVERSE", amount=900000, lock_on="2026-10-14", gross_revenue=90000),
    )
    assert await _run("sympli_margin_locks", env) == 2.25                  # 9,000 / 400,000, not the mean of 3 and 2
    assert await _run("sympli_margin_locks", env, week=PRIOR) is None


async def test_pull_through_and_files_past_target(env):
    t, b = env["tid"], env["bid"]
    await _add(
        # cohort for week end 10/18 = applied 6/20 .. 7/20
        _loan(t, b, "won", seg="funded", status="LOAN_FUNDED", occurred=dt.date(2026, 8, 1),
              history=_hist(loan_setup="2026-07-01", loan_funded="2026-08-01"), applied_on="2026-07-01", funded_on="2026-08-01"),
        _loan(t, b, "slow", seg="funded", status="LOAN_FUNDED", occurred=dt.date(2026, 10, 15),
              history=_hist(loan_setup="2026-07-01", loan_funded="2026-10-15"), applied_on="2026-07-01", funded_on="2026-10-15"),
        _loan(t, b, "lost", seg="dead", status="ADVERSE", occurred=dt.date(2026, 7, 20),
              history=_hist(loan_setup="2026-07-05", adverse="2026-07-20"), applied_on="2026-07-05"),
        _loan(t, b, "too-new", history=_hist(loan_setup="2026-09-30"), applied_on="2026-09-30"),
        # past target: applied 40 days before week end, active
        _loan(t, b, "late", status="DISCLOSURE_SENT", occurred=dt.date(2026, 10, 10),
              history=_hist(loan_setup="2026-09-08", disclosure_sent="2026-10-10"), applied_on="2026-09-08"),
        # same age but already clear to close
        _loan(t, b, "cleared", status="CLEAR_TO_CLOSE", occurred=dt.date(2026, 10, 10),
              history=_hist(loan_setup="2026-09-08", clear_to_close="2026-10-10"), applied_on="2026-09-08"),
    )
    assert await _run("sympli_pull_through", env) == 33.3                  # 1 of 3; "slow" took over 90 days
    assert await _run("sympli_past_ctc", env) == 1.0
    assert await _run("sympli_pull_through", env, week=(dt.date(2026, 1, 5), dt.date(2026, 1, 11))) is None
    async with SessionLocal() as s:
        integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == t, Integration.provider == "arive"))).scalar_one()
        integ.config = {"states": ["UT"], "ctc_target_days": 60}
        await s.commit()
    assert await _run("sympli_past_ctc", env) == 0.0                       # the target is config, not code


async def test_a_dead_arive_feed_is_unavailable_not_zero(env):
    t, b = env["tid"], env["bid"]
    await _add(_loan(t, b, "a"))
    assert await _run("sympli_open_count", env) == 1.0
    async with SessionLocal() as s:
        integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == t, Integration.provider == "arive"))).scalar_one()
        integ.status = "disconnected"
        await s.commit()
    assert await _run("sympli_open_count", env) is R.UNAVAILABLE


# ── brokerage figures ────────────────────────────────────────────────────────────────────────────

async def _brokerage(env):
    """Two offices, closings and new contracts in W. Returns the agents."""
    t, u = env["tid"], env["uid"]
    async with SessionLocal() as s:
        a = Agent(tenant_id=t, business_id=u, source="sisu", external_id="a", name="Avery SLC", email="avery@liveutah.test",
                  is_active=True, sisu_group_ids=[SLC], start_date=dt.date(2024, 1, 1))
        z = Agent(tenant_id=t, business_id=u, source="sisu", external_id="z", name="Zed SLC", email="zed@x.test",
                  is_active=True, sisu_group_ids=[SLC], start_date=dt.date(2024, 1, 1))
        d = Agent(tenant_id=t, business_id=u, source="sisu", external_id="d", name="Dana Davis", email="dana@x.test",
                  is_active=True, sisu_group_ids=[DAVIS], start_date=dt.date(2024, 1, 1))
        n = Agent(tenant_id=t, business_id=u, source="sisu", external_id="n", name="Never Closed", email="n@x.test",
                  is_active=True, sisu_group_ids=[SLC], start_date=dt.date(2024, 1, 1))
        s.add_all([a, z, d, n]); await s.flush()

        def txn(ext, agent, **kw):
            base = dict(tenant_id=t, business_id=u, source="sisu", external_id=ext, agent_id=agent.id, side="buy",
                        status="closed", sale_price=Decimal(400000), buyer_email=f"{ext}@b.test", buyer_name=f"Buyer {ext}")
            base.update(kw)
            return Transaction(**base)
        s.add_all([
            txn("c1", a, close_date=dt.date(2026, 10, 13), mortgage_vid=155743, lead_type="Zillow Preferred"),
            txn("c2", a, close_date=dt.date(2026, 10, 14), mortgage_vid=999, lead_type="realtor.com mvip"),
            txn("c3", z, close_date=dt.date(2026, 10, 14), mortgage_vid=None, lead_type="SOI"),
            txn("c4", d, close_date=dt.date(2026, 10, 15), mortgage_vid=155743, lead_type="Zillow"),
            txn("cash", z, close_date=dt.date(2026, 10, 15), mortgage_vid=12, lead_type="SOI"),
            # new contracts: 2 recorded of 3 financeable, plus a cash deal that can't record a lender
            txn("k1", a, status="pending", contract_date=dt.date(2026, 10, 13), mortgage_vid=999),
            txn("k2", z, status="pending", contract_date=dt.date(2026, 10, 14), mortgage_vid=None),
            txn("k3", d, status="pending", contract_date=dt.date(2026, 10, 14), mortgage_vid=155743),
            txn("k4", z, status="pending", contract_date=dt.date(2026, 10, 14), mortgage_vid=12),
        ])
        await s.commit()
        return a, z, d, n


async def test_attach_reads_sisu_on_the_brokerage_from_the_lenders_board(env):
    """The trap: run_resolvers hands every resolver the BOARD's business. Sisu lives on the brokerage,
    so the shared _sisu_live gate reads a live feed as dead from here."""
    await _brokerage(env)
    await _add(_loan(env["tid"], env["bid"], "a", seg="funded", status="LOAN_FUNDED",
                     history=_hist(loan_funded="2026-10-14"), funded_on="2026-10-14"))
    assert await _run("ulrg_team_sympli_attach", env) is R.UNAVAILABLE     # what reusing the old key would do
    assert await _run("sympli_attach", env) == 50.0                        # 2 of 4 financed; the cash deal excluded
    assert await _run("sympli_attach", env, sisu_group_id=SLC) == round(1 / 3 * 100, 1)
    assert await _run("sympli_attach", env, sisu_group_id=DAVIS) == 100.0
    assert await _run("sympli_attach_zillow", env) == 100.0
    assert await _run("sympli_attach_realtor", env) == 0.0
    assert await _run("sympli_attach_sphere", env) == 0.0
    assert [Y.lead_bucket(x) for x in ("Zillow Flex", "Realty.com", "market vip via opcity bla", "Opcity", "SOI", None)] == \
        ["zillow", "realtor", "realtor", "realtor", "sphere", "sphere"]


async def test_lead_source_attach_waits_for_sisu_to_resync(env):
    await _brokerage(env)
    await _add(_loan(env["tid"], env["bid"], "a", seg="funded", status="LOAN_FUNDED",
                     history=_hist(loan_funded="2026-10-14"), funded_on="2026-10-14"))
    async with SessionLocal() as s:
        for t in (await s.execute(select(Transaction).where(Transaction.tenant_id == env["tid"]))).scalars():
            t.lead_type = None
        await s.commit()
    assert await _run("sympli_attach_sphere", env) is R.UNAVAILABLE        # not "0% of everything is sphere"


async def test_lender_recorded_excludes_cash_and_scopes_to_the_office(env):
    await _brokerage(env)
    assert await _run("sympli_lender_recorded", env) == round(2 / 3 * 100, 1)
    assert await _run("sympli_lender_recorded", env, sisu_group_id=SLC) == 50.0
    assert await _run("sympli_lender_recorded", env, week=PRIOR) is None


async def test_agents_introducing_and_the_gap_list(env):
    t, b = env["tid"], env["bid"]
    a, z, d, n = await _brokerage(env)
    bea = Agent(id=uuid.uuid4(), tenant_id=t, business_id=env["uid"], source="sisu", external_id="b", name="Bea SLC",
                email="bea@x.test", is_active=True, sisu_group_ids=[SLC], start_date=dt.date(2024, 1, 1))
    await _add(bea, Transaction(tenant_id=t, business_id=env["uid"], source="sisu", external_id="bea1", agent_id=bea.id,
                                side="sell", status="closed", sale_price=Decimal(400000), close_date=dt.date(2026, 9, 1)))
    await _add(
        # Avery introduced through a pipeline loan's referral; never-closed agent isn't on the roster
        _loan(t, b, "p1", created_on="2026-10-01", referral_email="AVERY@liveutah.test".lower()),
        _loan(t, b, "p2", created_on="2026-10-01", buyer_agent_email="n@x.test"),
        # an old introduction is outside the 90 days
        _loan(t, b, "old", created_on="2026-03-01", referral_email="zed@x.test"),
    )
    # roster = Avery, Bea, Zed, Dana (producing). Avery by referral, Dana by the Sisu contract naming Sympli.
    assert await _run("sympli_agents_introducing", env) == 50.0
    assert await _run("sympli_agents_introducing", env, sisu_group_id=SLC) == round(1 / 3 * 100, 1)
    async with SessionLocal() as s:
        recs = await R.resolver_records(s, t, b, "sympli_agents_introducing", *W, group=None)
    assert [r["client"] for r in recs] == ["Bea SLC", "Zed SLC"]           # the gap list, alphabetical
    assert "refs" not in recs[0] and "rank" not in recs[0]


# ── the board ────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
async def springb():
    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        cfg = dict(t.config or {})
        cfg["fiscal_quarters"] = [{"key": "2026Q3", "start": "2026-07-27", "end": "2026-10-30"},
                                  {"key": "2026Q4", "start": "2026-10-31", "end": "2027-01-31"}]
        t.config = cfg
        await s.commit()
        await load_sympli_scorecard(s, t.id)
        return t.id


async def _token(tid, email, role="member", tabs=None):
    async with SessionLocal() as s:
        u = User(tenant_id=tid, email=f"{uuid.uuid4().hex[:6]}{email}", name="x", password_hash=hash_pw("password123"),
                 role=role, status="active", tab_access=tabs, token_version=0)
        s.add(u)
        await s.commit()
        return make_token(u.id, tid, 0)


def test_seed_fits_its_columns_and_names_only_real_resolvers():
    data = load_data()
    for g in board_groups(data):
        assert len(g["key"]) <= 40 and len(g["name"]) <= 120
        for m in g["metrics"]:
            assert len(m["name"]) <= 160 and len(m.get("note") or "") <= 160, m["name"]
            assert m["type"] in ("flow", "rate", "snapshot") and len(m.get("owner") or "") <= 4
            assert not m.get("resolver_key") or m["resolver_key"] in R.RESOLVERS, m["resolver_key"]
    assert all(len(g["label"]) <= 240 for g in data["gates"])


async def test_board_renders_gates_goals_and_follows_the_sympli_tab(springb):
    owner = await _token(springb, "o@x.test", role="owner")
    member = await _token(springb, "m@x.test", tabs=["sympli"])
    outsider = await _token(springb, "f@x.test", tabs=["forum"])
    async with _client() as c:
        r = await c.get("/api/v1/ulrg/scorecard?scope=sympli", headers=_H(owner))
        assert r.status_code == 200
        d = r.json()
        assert (await c.get("/api/v1/ulrg/scorecard?scope=sympli", headers=_H(member))).status_code == 200
        assert (await c.get("/api/v1/ulrg/scorecard?scope=sympli", headers=_H(outsider))).status_code == 403
        names = [g["name"] for g in d["groups"]]
        assert names[:2] == ["North Star", "Pipeline"] and names[-1] == "Cost Controls" and "Jared Browning" in names
        rows = {r["measurable"]: r for g in d["groups"] if g["key"] == "north_star" for r in g["rows"]}
        ns = rows["Funded (40/mo)"]
        assert ns["auto"] and ns["cumulative_goal"] == 120 and ns["owner"]["initials"] == "JA"
        assert rows["Funded (26/mo)"]["cumulative_goal"] == 78
        assert rows["Net Operating Income ($K)"]["auto"] is False            # hand-entered, no fake feed
        assert [x["provider"] for x in d["sources"]] == ["sisu"]             # stamped per source; Arive never synced here
        assert len(d["gates"]) == 8 and all(g["status"] in ("done", "due", "missed") for g in d["gates"])
        # a member with the tab can type a hand-entered number
        wk = d["weeks"][-1]["start"]
        noi = rows["Net Operating Income ($K)"]["id"]
        assert (await c.post("/api/v1/ulrg/scorecard/values", headers=_H(member),
                             json={"metric_id": noi, "week_start": wk, "value": -1.7})).status_code == 201


async def test_seed_is_additive_and_keeps_what_settings_changed(springb):
    async with SessionLocal() as s:
        m = (await s.execute(select(ScorecardMetric).join(ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id).where(
            ScorecardGroup.key == "leading", ScorecardMetric.name == "Same-Day LO Contact",
            ScorecardMetric.tenant_id == springb))).scalar_one()
        m.goal = Decimal("85")
        await s.commit()
        again = await load_sympli_scorecard(s, springb)
        assert again == {"rows": 0, "groups": 0, "gates": 0, "goals": 0}
    async with SessionLocal() as s:
        assert float((await s.get(ScorecardMetric, m.id)).goal) == 85.0


async def test_agent_level_drills_are_leadership_only(springb):
    owner = await _token(springb, "o@x.test", role="owner")
    member = await _token(springb, "m@x.test", tabs=["sympli"])
    async with SessionLocal() as s:
        m = (await s.execute(select(ScorecardMetric).join(ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id).where(
            ScorecardGroup.key == "leading", ScorecardMetric.resolver_key == "sympli_agents_introducing",
            ScorecardMetric.tenant_id == springb))).scalar_one()
        loan_row = (await s.execute(select(ScorecardMetric).join(ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id).where(
            ScorecardGroup.key == "north_star", ScorecardMetric.resolver_key == "sympli_margin_funded",
            ScorecardMetric.tenant_id == springb))).scalar_one()
    q = "week_start=2026-10-12&week_end=2026-10-18"
    async with _client() as c:
        r = await c.get(f"/api/v1/ulrg/metric/{m.id}/records?{q}", headers=_H(member))
        assert r.status_code == 200 and r.json()["restricted"] is True and r.json()["records"] == []
        r = await c.get(f"/api/v1/ulrg/metric/{m.id}/records?{q}", headers=_H(owner))
        assert r.status_code == 200 and "restricted" not in r.json() and r.json()["source_label"] == "Sisu"
        # a loan row isn't agent-level: anyone with the tab drills it
        r = await c.get(f"/api/v1/ulrg/metric/{loan_row.id}/records?{q}", headers=_H(member))
        assert r.status_code == 200 and "restricted" not in r.json() and r.json()["source_label"] == "Arive"


async def test_every_agent_naming_resolver_is_gated():
    """Derived, not listed: anything reading Sisu on this board names agents or Utah Life's clients."""
    data = load_data()
    sisu_rows = {m["resolver_key"] for g in board_groups(data) for m in g["metrics"] if m.get("source") == "sisu"}
    assert sisu_rows and sisu_rows <= Y.AGENT_LEVEL_KEYS


async def test_gates_derive_status_and_only_leadership_marks_them(springb):
    owner = await _token(springb, "o@x.test", role="owner")
    member = await _token(springb, "m@x.test", tabs=["sympli"])
    from app.services.scorecard import build_scorecard
    async with SessionLocal() as s:
        biz = await roles.commission_jv(s, springb)
        early = await build_scorecard(s, springb, biz.id, 13, today=dt.date(2026, 11, 3))
        late = await build_scorecard(s, springb, biz.id, 13, today=dt.date(2026, 11, 7))
    assert early["gates"][0]["status"] == "due" and late["gates"][0]["status"] == "missed"
    gid = late["gates"][0]["id"]
    async with _client() as c:
        assert (await c.patch(f"/api/v1/ulrg/gate/{gid}", headers=_H(member), json={"done": True})).status_code == 403
        assert (await c.patch(f"/api/v1/ulrg/gate/{uuid.uuid4()}", headers=_H(owner), json={"done": True})).status_code == 404
        assert (await c.patch(f"/api/v1/ulrg/gate/{gid}", headers=_H(owner), json={"done": True})).status_code == 200
    async with SessionLocal() as s:
        after = await build_scorecard(s, springb, biz.id, 13, today=dt.date(2026, 11, 7))
    assert after["gates"][0]["status"] == "done"
    async with _client() as c:
        await c.patch(f"/api/v1/ulrg/gate/{gid}", headers=_H(owner), json={"done": False})
    async with SessionLocal() as s:
        assert (await build_scorecard(s, springb, biz.id, 13, today=dt.date(2026, 11, 7)))["gates"][0]["status"] == "missed"


async def test_share_link_opens_this_board_read_only(springb):
    owner = await _token(springb, "o@x.test", role="owner")
    async with _client() as c:
        r = await c.post("/api/v1/ulrg/share", headers=_H(owner), json={"scope": "sympli_scorecard"})
        assert r.status_code == 201
        tok = r.json()["token"]
        sc = await c.get(f"/api/v1/share/{tok}/scorecard")               # no auth: the token is the credential
        assert sc.status_code == 200 and sc.headers.get("cache-control") == "no-store"
        body = sc.json()
        assert body["groups"][0]["name"] == "North Star" and len(body["gates"]) == 8
        # no route on the token reaches a record: the embed has numbers, never names
        assert "records" not in json.dumps(body)


async def test_setup_script_backfills_only_this_board(env, monkeypatch):
    from scripts import setup_sympli_scorecard as S
    t, b = env["tid"], env["bid"]
    today = dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())
    funded_day = (monday - dt.timedelta(days=14)).isoformat()
    await _add(_loan(t, b, "f1", seg="funded", status="LOAN_FUNDED", occurred=dt.date.fromisoformat(funded_day),
                     history={"LOAN_FUNDED": funded_day}, funded_on=funded_day))
    async with SessionLocal() as s:
        # another board's auto row with a hand-corrected value: this script must not re-run it
        other = ScorecardGroup(tenant_id=t, business_id=env["uid"], key="davis", name="Davis", is_team_room=True)
        s.add(other); await s.flush()
        met = ScorecardMetric(tenant_id=t, group_id=other.id, name="Appointments Met", type="flow", direction="gte",
                              goal=Decimal(30), resolver_key="sympli_funded", active=True)
        s.add(met); await s.flush()
        s.add(ScorecardValue(tenant_id=t, metric_id=met.id, week_start=monday - dt.timedelta(days=14),
                             value=Decimal(7), source="manual"))
        tenant = await s.get(Tenant, t)
        tenant.config = {"fiscal_quarters": [{"key": "2026Q3", "start": "2026-07-27", "end": "2026-10-30"}]}
        await s.commit()
        met_id = met.id

    async def no_sync(s, tid, integ):
        raise AssertionError("--no-sync")
    monkeypatch.setattr(S, "sync_arive", no_sync)

    monkeypatch.setattr(sys, "argv", ["x", "--tenant", env["slug"]])
    assert await S.main() == 0                                           # dry run writes nothing
    async with SessionLocal() as s:
        assert not (await s.execute(select(ScorecardGroup).where(ScorecardGroup.business_id == b))).scalars().all()
        assert len((await s.get(Tenant, t)).config["fiscal_quarters"]) == 1

    monkeypatch.setattr(sys, "argv", ["x", "--tenant", env["slug"], "--apply", "--no-sync"])
    assert await S.main() == 0
    assert await S.main() == 0                                           # idempotent
    async with SessionLocal() as s:
        assert {"key": "2026Q4", "start": "2026-10-31", "end": "2027-01-31"} in (await s.get(Tenant, t)).config["fiscal_quarters"]
        gs = (await s.execute(select(ScorecardGroup).where(ScorecardGroup.business_id == b))).scalars().all()
        assert len(gs) == len(board_groups(load_data()))
        fm = (await s.execute(select(ScorecardMetric).join(ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id).where(
            ScorecardGroup.key == "north_star", ScorecardGroup.business_id == b,
            ScorecardMetric.name == "Funded (26/mo)"))).scalar_one()
        vals = {v.week_start: float(v.value) for v in (await s.execute(select(ScorecardValue).where(
            ScorecardValue.metric_id == fm.id))).scalars() if v.value is not None}
        assert vals[monday - dt.timedelta(days=14)] == 1.0 and sum(vals.values()) == 1.0
        goal = (await s.execute(select(ScorecardGoal).where(
            ScorecardGoal.metric_id == fm.id, ScorecardGoal.period_key == "2026Q4"))).scalar_one()
        assert float(goal.cumulative_goal) == 78
        assert len((await s.execute(select(ScorecardGate).where(ScorecardGate.business_id == b))).scalars().all()) == 8
        mv = (await s.execute(select(ScorecardValue).where(ScorecardValue.metric_id == met_id))).scalars().all()
        assert [(float(v.value), v.source) for v in mv] == [(7.0, "manual")]   # the MET row was never touched

    monkeypatch.setattr(sys, "argv", ["x", "--tenant", env["slug"], "--apply", "--no-sync",
                                      "--add-lo", "New Person,New@Sympli.test,NP"])
    assert await S.main() == 0
    async with SessionLocal() as s:
        g = (await s.execute(select(ScorecardGroup).where(ScorecardGroup.business_id == b,
                                                          ScorecardGroup.key == "lo_new_person"))).scalar_one()
        assert g.lo_email == "new@sympli.test"
        names = [gg.key for gg in sorted((await s.execute(select(ScorecardGroup).where(
            ScorecardGroup.business_id == b))).scalars().all(), key=lambda x: x.sort_order)]
        assert names[-1] == "costs"                                      # LOs stay above Cost Controls
