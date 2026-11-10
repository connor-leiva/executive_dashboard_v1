"""Round 2 of the Spring B L10: seats, Meta CPL, stitched cash, three books, tickets, renewals."""
import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import SessionLocal, engine
from app.models import (AdAccount, AdCampaign, AdInsightDaily, Base, BookTxn, Business, Integration,
                        MetricRecord, ScorecardGroup, ScorecardMetric, Tenant)
from app.services import scorecard_resolvers as R
from app.services.billing import forum_offering, spring_break_offering
from app.services.sync import (_membership_field_ids, enters_guest_seat, is_confirmed_seat,
                               materialize_vip_seat_proxies)


@pytest.fixture
async def env():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        t = Tenant(slug=f"r2-{uuid.uuid4().hex[:8]}", name="Round2")
        s.add(t)
        await s.flush()
        b = Business(tenant_id=t.id, key=f"forum{uuid.uuid4().hex[:4]}", name="The Forum",
                     tag="mb", kind="membership", sort_order=0)
        s.add(b)
        await s.flush()
        s.add(Integration(tenant_id=t.id, provider="ghl", business_id=b.id, status="connected",
                          last_synced_at=dt.datetime.now(dt.timezone.utc)))
        s.add(Integration(tenant_id=t.id, provider="stripe_legacy", business_id=b.id, status="connected",
                          last_synced_at=dt.datetime.now(dt.timezone.utc)))
        await s.commit()
        yield {"tid": t.id, "bid": b.id}


def test_renewal_status_does_not_steal_membership_status():
    defs = [
        {"id": "rs", "name": "Renewal Status", "fieldKey": "contact.renewal_status"},
        {"id": "st", "name": "Membership Status"},
        {"id": "rd", "name": "Renewal Date"},
    ]
    m = _membership_field_ids(defs, {})
    assert m["renewal_status"] == "rs"
    assert m["status"] == "st"
    assert m["renewal_date"] == "rd"
    only = _membership_field_ids([{"id": "rs", "name": "Renewal Status"}], {})
    assert "status" not in only and only["renewal_status"] == "rs"


def test_seat_stage_rules():
    assert is_confirmed_seat("VIP Guest: Confirmed")
    assert is_confirmed_seat("Dallas VIP guest confirmed")
    assert not is_confirmed_seat("VIP Guest: Opt-In")
    assert enters_guest_seat("VIP Guest: Confirmed", None, "VIP Guest: Opt-In", None)
    assert not enters_guest_seat("VIP Guest: Attended", None, "VIP Guest: Confirmed", None)
    assert enters_guest_seat("VIP Guest: Attended", None, "VIP Guest: Opt-In", None)
    assert enters_guest_seat("VIP Guest: Confirmed", "33081fcb-978a-48f0-9b98-3e7df8b8bce7", None, None)


def test_spring_break_is_tickets_and_not_forum():
    assert spring_break_offering("Spring Break VIP Ticket") == (True, "vip")
    assert spring_break_offering("Spring Break GA Pass") == (True, "ga")
    assert spring_break_offering("The Forum Monthly") == (False, None)
    assert forum_offering("Spring Break VIP Ticket", amount=400) == (False, None)


async def test_seat_proxy_and_count(env):
    e = env
    mon = dt.date(2026, 11, 2)
    async with SessionLocal() as s:
        s.add(MetricRecord(
            tenant_id=e["tid"], business_id=e["bid"], source="ghl", kind="recruiting",
            external_id="opp1", status="open", name="Guest",
            meta={"stage": "VIP Guest: Confirmed", "last_stage_change_at": "2026-11-03T18:00:00Z"}))
        s.add(MetricRecord(
            tenant_id=e["tid"], business_id=e["bid"], source="ghl", kind="recruiting",
            external_id="opp2", status="open", name="Opted",
            meta={"stage": "VIP Guest: Opt-In", "last_stage_change_at": "2026-11-03T18:00:00Z"}))
        await s.commit()
        assert await materialize_vip_seat_proxies(s, e["tid"], e["bid"]) == 1
        await s.commit()
        assert await materialize_vip_seat_proxies(s, e["tid"], e["bid"]) == 0
        assert await R.forum_vip_guests_seats(s, e["tid"], e["bid"], mon, mon + dt.timedelta(days=6)) == 1


async def test_edge_synced_with_no_starts_is_zero(env):
    today = dt.date.today()
    ws = today - dt.timedelta(days=today.weekday())
    async with SessionLocal() as s:
        assert await R.sb_edge_paid_added(s, env["tid"], env["bid"], ws, ws + dt.timedelta(days=6)) == 0


async def test_cash_is_dollar_weighted_and_drops_non_forum(env):
    e = env
    mon = dt.date(2026, 11, 2)
    async with SessionLocal() as s:
        s.add(MetricRecord(tenant_id=e["tid"], business_id=e["bid"], source="ghl", kind="payment",
                           external_id="ok", status="succeeded", occurred_on=mon, amount=Decimal("80"),
                           meta={"entity_source_name": "The Forum Monthly"}))
        s.add(MetricRecord(tenant_id=e["tid"], business_id=e["bid"], source="ghl", kind="payment",
                           external_id="bad", status="failed", occurred_on=mon, amount=Decimal("20"),
                           meta={"entity_source_name": "The Forum Monthly"}))
        s.add(MetricRecord(tenant_id=e["tid"], business_id=e["bid"], source="ghl", kind="payment",
                           external_id="edge", status="succeeded", occurred_on=mon, amount=Decimal("500"),
                           meta={"entity_source_name": "The Edge Monthly"}))
        await s.commit()
        assert await R.sb_cash_collected_rate(s, e["tid"], e["bid"], mon, mon + dt.timedelta(days=6)) == 80.0


async def test_net_cash_sums_three_books_and_skips_the_brokerage(env):
    e = env
    mon = dt.date(2026, 11, 2)
    async with SessionLocal() as s:
        bc = Business(tenant_id=e["tid"], key="bc1", name="beCollective", tag="bc", kind="membership", sort_order=1)
        coach = Business(tenant_id=e["tid"], key="coach", name="SB Coaching", tag="c", kind="holding", sort_order=2)
        re = Business(tenant_id=e["tid"], key="ulrg", name="ULRG", tag="re", kind="real_estate", sort_order=3)
        s.add_all([bc, coach, re])
        await s.flush()
        for biz, qbo, amt, kind in (
            (e["bid"], "f", "100", "Deposit"),
            (bc.id, "b", "40", "Deposit"),
            (coach.id, "c", "10", "Purchase"),
            (re.id, "r", "999", "Deposit"),
        ):
            s.add(BookTxn(tenant_id=e["tid"], business_id=biz, realm_id=qbo, qbo_type=kind,
                          qbo_id=qbo, txn_date=mon, amount=Decimal(amt)))
        await s.commit()
        assert await R.sb_net_cash(s, e["tid"], e["bid"], mon, mon + dt.timedelta(days=6)) == 130.0


async def test_cost_per_lead_uses_campaign_spend_only(env):
    e = env
    mon = dt.date(2026, 11, 2)
    async with SessionLocal() as s:
        s.add(MetricRecord(tenant_id=e["tid"], business_id=e["bid"], source="ghl",
                           kind="forum_stage_tracking", external_id="since", occurred_on=dt.date(2026, 10, 1),
                           status="tracking"))
        s.add(MetricRecord(tenant_id=e["tid"], business_id=e["bid"], source="ghl", kind="forum_stage_event",
                           external_id="lead1", segment="vip_guest", occurred_on=mon, status="entered",
                           meta={"opp_source": "VIP Page"}))
        integ = Integration(tenant_id=e["tid"], provider="meta_ads", status="connected",
                            last_synced_at=dt.datetime.now(dt.timezone.utc))
        s.add(integ)
        await s.flush()
        acct = AdAccount(tenant_id=e["tid"], integration_id=integ.id, business_id=e["bid"],
                         platform="meta", external_id="act_587749862890426", name="Meta",
                         last_synced_at=dt.datetime.now(dt.timezone.utc))
        s.add(acct)
        await s.flush()
        forum = AdCampaign(tenant_id=e["tid"], ad_account_id=acct.id, external_id="c-forum",
                           name="Forum Dallas - VIP")
        edge = AdCampaign(tenant_id=e["tid"], ad_account_id=acct.id, external_id="c-edge",
                          name="The Edge Webinar")
        s.add_all([forum, edge])
        await s.flush()
        s.add(AdInsightDaily(tenant_id=e["tid"], ad_account_id=acct.id, level="campaign",
                             campaign_id=forum.id, object_external_id="c-forum", occurred_on=mon,
                             spend=Decimal("50")))
        s.add(AdInsightDaily(tenant_id=e["tid"], ad_account_id=acct.id, level="campaign",
                             campaign_id=edge.id, object_external_id="c-edge", occurred_on=mon,
                             spend=Decimal("80")))
        s.add(AdInsightDaily(tenant_id=e["tid"], ad_account_id=acct.id, level="ad",
                             object_external_id="a1", occurred_on=mon, spend=Decimal("50")))
        await s.commit()
        assert await R.sb_cost_per_qualified_lead(s, e["tid"], e["bid"], mon, mon + dt.timedelta(days=6)) == 50.0


async def test_ad_spend_vs_budget_uses_the_weekly_plan(env):
    e = env
    mon = dt.date(2026, 11, 2)
    async with SessionLocal() as s:
        integ = Integration(tenant_id=e["tid"], provider="meta_ads", status="connected")
        s.add(integ)
        await s.flush()
        acct = AdAccount(tenant_id=e["tid"], integration_id=integ.id, business_id=e["bid"],
                         platform="meta", external_id="act_budget", name="Meta",
                         weekly_budgets={"Forum Dallas": 1000, "Edge Webinar": 500})
        s.add(acct)
        await s.flush()
        s.add(AdInsightDaily(tenant_id=e["tid"], ad_account_id=acct.id, level="campaign",
                             object_external_id="c1", occurred_on=mon, spend=Decimal("200")))
        await s.commit()
        assert await R.sb_ad_spend_vs_budget(
            s, e["tid"], e["bid"], mon, mon + dt.timedelta(days=6)) == 13.3
        acct.weekly_budgets = {}
        await s.commit()
        assert await R.sb_ad_spend_vs_budget(
            s, e["tid"], e["bid"], mon, mon + dt.timedelta(days=6)) is None


async def test_renewals_confirmed_rate(env):
    e = env
    today = dt.date.today()
    ws = today - dt.timedelta(days=today.weekday())
    we = ws + dt.timedelta(days=6)
    due = (we + dt.timedelta(days=30)).isoformat()
    async with SessionLocal() as s:
        s.add(MetricRecord(tenant_id=e["tid"], business_id=e["bid"], source="ghl", kind="member",
                           external_id="a", status="active",
                           meta={"membership": {"renewal_date": due, "renewal_status": "Confirmed"}}))
        s.add(MetricRecord(tenant_id=e["tid"], business_id=e["bid"], source="ghl", kind="member",
                           external_id="b", status="active",
                           meta={"membership": {"renewal_date": due, "renewal_status": "Not yet"}}))
        await s.commit()
        assert await R.sb_renewals_confirmed(s, e["tid"], e["bid"], ws, we) == 50.0
        assert await R.sb_renewals_confirmed(s, e["tid"], e["bid"], dt.date(2020, 1, 6), dt.date(2020, 1, 12)) is R.UNAVAILABLE


async def test_spring_break_is_blank_before_the_season_and_cumulative_after(env):
    e = env
    async with SessionLocal() as s:
        s.add(MetricRecord(tenant_id=e["tid"], business_id=e["bid"], source="stripe_legacy",
                           kind="spring_break_payment", external_id="t1", status="succeeded",
                           occurred_on=dt.date(2026, 11, 28), amount=Decimal("397"), segment="vip"))
        await s.commit()
        assert await R.sb_spring_break_tickets(
            s, e["tid"], e["bid"], dt.date(2026, 11, 9), dt.date(2026, 11, 15)) is None
        assert await R.sb_spring_break_tickets(
            s, e["tid"], e["bid"], dt.date(2026, 11, 23), dt.date(2026, 11, 29)) == 1


def test_sprints_handle_a_gap_and_a_midweek_start():
    from app.services.scorecard import assign_sprints
    sprints = assign_sprints([
        {"key": "2026Q3", "start": "2026-07-27", "end": "2026-10-30"},
        {"key": "2026Q4", "start": "2026-11-02", "end": "2027-01-18"},
    ], dt.date(2026, 11, 10))
    q3 = next(s for s in sprints if s["key"] == "2026Q3")
    q4 = next(s for s in sprints if s["key"] == "2026Q4")
    assert "2026-10-26" in q3["mondays"] and "2026-11-02" not in q3["mondays"]
    assert q4["mondays"][0] == "2026-11-02" and q4["current"] is True
    assert "2027-01-18" in q4["mondays"]
    mid = assign_sprints([
        {"key": "A", "start": "2026-10-05", "end": "2026-11-03"},
        {"key": "B", "start": "2026-11-04", "end": "2026-12-31"},
    ], dt.date(2026, 11, 10))
    assert "2026-11-02" not in next(s for s in mid if s["key"] == "A")["mondays"]
    assert "2026-11-02" in next(s for s in mid if s["key"] == "B")["mondays"]
    gap = assign_sprints([
        {"key": "A", "start": "2026-10-05", "end": "2026-10-25"},
        {"key": "B", "start": "2026-11-09", "end": "2026-12-31"},
    ], dt.date(2026, 11, 10))
    assert "2026-11-02" not in {m for s in gap for m in s["mondays"]}


async def test_drawers_match_the_cell(env):
    e = env
    mon = dt.date(2026, 11, 2)
    we = mon + dt.timedelta(days=6)
    async with SessionLocal() as s:
        s.add(MetricRecord(
            tenant_id=e["tid"], business_id=e["bid"], source="ghl", kind="forum_stage_event",
            external_id="opp1:vip_guest_seat", segment="vip_guest_seat", occurred_on=mon,
            status="entered", name="Ada",
            meta={"opp_id": "opp1", "to_stage": "VIP Guest: Confirmed", "opp_source": "VIP Page", "proxy": True}))
        s.add(MetricRecord(tenant_id=e["tid"], business_id=e["bid"], source="ghl", kind="payment",
                           external_id="ok", status="succeeded", occurred_on=mon, amount=Decimal("80"),
                           name="Ada", meta={"entity_source_name": "The Forum Monthly"}))
        bc = Business(tenant_id=e["tid"], key="bc1", name="beCollective", tag="bc", kind="membership", sort_order=1)
        coach = Business(tenant_id=e["tid"], key="coach", name="SB Coaching", tag="c", kind="holding", sort_order=2)
        s.add_all([bc, coach])
        await s.flush()
        s.add(BookTxn(tenant_id=e["tid"], business_id=e["bid"], realm_id="forum", qbo_type="JournalEntry",
                      qbo_id="je", txn_date=mon, amount=Decimal("11500"), payee="Kimmi Shaw"))
        s.add(BookTxn(tenant_id=e["tid"], business_id=e["bid"], realm_id="forum", qbo_type="Purchase",
                      qbo_id="pay", txn_date=mon, amount=Decimal("8032"), payee="Payroll Pay"))
        s.add(BookTxn(tenant_id=e["tid"], business_id=coach.id, realm_id="coach", qbo_type="Deposit",
                      qbo_id="dep", txn_date=mon, amount=Decimal("500"), payee="Client"))
        await s.commit()
        seats = await R.springb_drill(s, e["tid"], e["bid"], "forum_vip_guests_seats", mon, we)
        assert len(seats["records"]) == 1
        assert "proxy" in seats["records"][0]["detail"] and "VIP Page" in seats["records"][0]["detail"]
        cash = await R.springb_drill(s, e["tid"], e["bid"], "sb_cash_collected_rate", mon, we)
        assert cash["records"] and "Succeeded $80.00" in cash["summary"]
        books = await R.springb_drill(s, e["tid"], e["bid"], "sb_net_cash", mon, we)
        assert all(not r["excluded"] for r in books["records"])
        assert any(r["client"] == "Kimmi Shaw" for r in books["excluded"])
        nets = {g["company"]: g["net"] for g in books["groups"]}
        assert nets["The Forum"] == -8032
        assert nets["SB Coaching"] == 500
        assert nets["beCollective"] == 0
        assert round(sum(nets.values()), 2) == await R.sb_net_cash(s, e["tid"], e["bid"], mon, we)
