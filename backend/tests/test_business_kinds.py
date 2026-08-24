"""A tenant whose businesses are named nothing like Spring's gets a working dashboard.

This is the point of Phase 3, and the only test that can show it. The compute layer used to
find businesses by literal key — `Business.key == "ulrg"`, `bmap.get("sympli")` — about thirty
times. None of those raised for a second tenant; they returned None, and the panel rendered
empty with no explanation. So the failure this file guards against is silence.

`Business.kind` is the axis those comparisons meant. Migration 0014 added it and backfilled
ulrg->real_estate, sympli->commission_jv, springb->membership; the dispatch just never moved
onto it until now.
"""
import datetime as dt
import uuid

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (Agent, Business, Integration, MetricRecord, PLSnapshot, Tenant,
                        Transaction)
from app.seed import seed
from app.services import becollective, edge, financials, forum, lineage, metrics, roles
from app.services.provisioning import provision_tenant
from app.services.tabs import tenant_tabs

TODAY = dt.date.today()
MONTH_START = TODAY.replace(day=1)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _acme():
    """A tenant modelled on a real portfolio but sharing NONE of Spring's names.

    Deliberately different in every visible way: different keys, different display names,
    reversed sort order. If anything downstream still keys off a literal, this tenant finds it.
    """
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "acmeco"))).scalar_one_or_none()
        if t is not None:
            return t.id
    async with SessionLocal() as s:
        r = await provision_tenant(
            s, slug="acmeco", name="Acme Holdings", owner_email="owner@acme.test",
            hostname="acmeco.localhost", seed_catalogs=False,
            businesses=[
                {"key": "coastal", "name": "Coastal Realty", "tag": "Brokerage",
                 "kind": roles.REAL_ESTATE},
                {"key": "guild", "name": "The Guild", "tag": "Membership",
                 "kind": roles.MEMBERSHIP},
                {"key": "harbor_lending", "name": "Harbor Lending", "tag": "Mortgage JV",
                 "kind": roles.COMMISSION_JV, "is_jv": True, "jv_share": 0.5},
            ])
        return r.tenant_id


async def _biz(tid, key) -> Business:
    async with SessionLocal() as s:
        return (await s.execute(select(Business).where(
            Business.tenant_id == tid, Business.key == key))).scalar_one()


# ── the resolver itself ───────────────────────────────────────────────────────────────
async def test_a_business_is_found_by_what_it_is_not_what_it_is_called():
    tid = await _acme()
    async with SessionLocal() as s:
        assert (await roles.real_estate(s, tid)).key == "coastal"
        assert (await roles.membership(s, tid)).key == "guild"
        assert (await roles.commission_jv(s, tid)).key == "harbor_lending"
        re_b, jv_b = await roles.flywheel_pair(s, tid)
        assert (re_b.key, jv_b.key) == ("coastal", "harbor_lending")


async def test_a_missing_kind_is_a_normal_state_not_an_error():
    """A membership-only customer has no brokerage. The panels that need one must render
    empty, not raise — which is why every resolver returns None rather than throwing."""
    async with SessionLocal() as s:
        r = await provision_tenant(s, slug="guildonly", name="Guild Only",
                                   owner_email="o@guildonly.test", hostname="guildonly.localhost",
                                   seed_catalogs=False,
                                   businesses=[{"key": "g", "name": "G", "tag": "M",
                                                "kind": roles.MEMBERSHIP}])
        tid = r.tenant_id
    async with SessionLocal() as s:
        assert await roles.real_estate(s, tid) is None
        assert await roles.commission_jv(s, tid) is None
        assert (await roles.membership(s, tid)).key == "g"
        re_b, jv_b = await roles.flywheel_pair(s, tid)
        assert re_b is None and jv_b is None
    # ...and the dashboard still builds.
    async with SessionLocal() as s:
        payload = await metrics.build_dashboard(s, tid, "mtd")
    assert payload is not None


async def test_primary_is_the_lowest_sort_order_when_a_tenant_has_two_of_a_kind():
    """The old lookups silently assumed exactly one business of each kind. Nothing enforces
    that, so `primary()` has to give a defined answer rather than an arbitrary one."""
    tid = await _acme()
    async with SessionLocal() as s:
        second = Business(tenant_id=tid, key="inland", name="Inland Realty", tag="Brokerage",
                          kind=roles.REAL_ESTATE, sort_order=99)
        s.add(second)
        await s.commit()
        sid = second.id
    async with SessionLocal() as s:
        assert (await roles.real_estate(s, tid)).key == "coastal"        # sort_order 0 wins
        assert {b.key for b in await roles.of_kind(s, tid, roles.REAL_ESTATE)} == {"coastal", "inland"}
    async with SessionLocal() as s:                                       # leave the fixture clean
        await s.execute(delete(Business).where(Business.id == sid))
        await s.commit()


# ── the compute layer, end to end, under foreign names ────────────────────────────────
async def test_the_program_views_resolve_for_a_differently_named_membership_entity():
    """forum / beCollective / The Edge are three views of ONE membership entity's GHL
    location. Each looked it up as `Business.key == "springb"`, so for Acme all three
    returned the 'pending' placeholder forever."""
    tid = await _acme()
    guild = await _biz(tid, "guild")
    async with SessionLocal() as s:
        s.add(Integration(tenant_id=tid, provider="ghl", business_id=guild.id,
                          status="connected", config={}))
        for i in range(4):
            s.add(MetricRecord(tenant_id=tid, business_id=guild.id, source="ghl",
                               kind="member", external_id=f"m{i}", name=f"Member {i}",
                               status="active", segment="forum"))
        await s.commit()

    async with SessionLocal() as s:
        f = await forum.build_forum(s, tid, "mtd")
    assert f["status"] != "pending", "the membership entity was not found by kind"
    assert f["members_total"] == 4

    # The other two resolve the same entity and simply have no rows of their own yet.
    async with SessionLocal() as s:
        assert await becollective.build_becollective(s, tid, "mtd") is not None
        assert await edge.build_edge(s, tid, "mtd") is not None


async def test_the_dashboard_gives_a_foreign_brokerage_its_operational_tiles():
    """`if b.key == "ulrg"` gated every operational tile — units closed, GCI, agents
    producing. Acme's brokerage got a card with financials and no operations at all."""
    tid = await _acme()
    coastal = await _biz(tid, "coastal")
    async with SessionLocal() as s:
        a = Agent(tenant_id=tid, business_id=coastal.id, source="sisu",
                  external_id="ag-1", name="Dana Reyes", is_active=True)
        s.add(a)
        await s.flush()
        for i in range(3):
            s.add(Transaction(tenant_id=tid, business_id=coastal.id, agent_id=a.id,
                              source="sisu", external_id=f"t{i}", status="closed",
                              side="buy", close_date=MONTH_START, sale_price=500_000,
                              gci=15_000, buyer_name=f"Buyer {i}"))
        await s.commit()

    async with SessionLocal() as s:
        payload = await metrics.build_dashboard(s, tid, "mtd")
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload
    area = data["areas"]["coastal"]
    assert area["ops"], "a brokerage with closings showed no operational tiles"
    labels = {o["label"]: o["value"] for o in area["ops"]}
    assert any("Closed" in k for k in labels), labels
    # and the portfolio scorecard names the same business
    keys = {c.get("business_key") for c in data["scorecards"]}
    assert "coastal" in keys, keys


async def test_a_foreign_jv_gets_commission_financials_not_deal_financials():
    """`if business.key == "sympli"` chose the loan-commission P&L shape. Any other JV fell
    through to the brokerage shape and reported a deal pipeline it does not have."""
    tid = await _acme()
    harbor = await _biz(tid, "harbor_lending")
    async with SessionLocal() as s:
        biz = await s.get(Business, harbor.id)
        fin = await financials.compute_financials(s, tid, biz, "mtd")
    assert fin is not None
    # The commission shape is Booked + a calculated Live from loan commissions; the brokerage
    # shape carries a Sisu-sourced projection. Assert we did NOT get the brokerage shape.
    lenses = fin.get("lenses") or {}
    assert "booked" in lenses
    assert fin.get("pipeline_deals") is None, "a lending JV was given a deal pipeline"


async def test_drills_resolve_under_foreign_names():
    """lineage.py held 12 of the 30 literal lookups — the drill-downs behind every tile. A
    drill that cannot find the business returns an empty, unexplained drawer."""
    tid = await _acme()
    for key in ("units_closed", "gci", "active_members", "forum_roster"):
        async with SessionLocal() as s:
            res = await lineage.metric_detail(s, tid, key, "mtd")
        assert isinstance(res, dict) and "label" in res, f"{key} produced no drill payload"


async def test_the_nav_is_built_from_this_tenants_own_businesses():
    tid = await _acme()
    async with SessionLocal() as s:
        tabs = await tenant_tabs(s, tid)
    assert "portfolio" in tabs and "coastal" in tabs and "harbor_lending" in tabs
    # Spring's program tabs come from a per-business config, not from anyone's key.
    assert "ulrg" not in tabs and "sympli" not in tabs
