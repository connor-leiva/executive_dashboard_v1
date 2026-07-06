"""Flywheel v2 payload + lineage (spec §7): period label, reconciliation invariant,
the unset-share regression (the live $0 bug), the attach delta, and the new drills."""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant, Business
from app.services.metrics import build_dashboard
from app.services.lineage import metric_detail


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _fw(period="mtd"):
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        return (await build_dashboard(s, t.id, period)).flywheel


async def test_period_label_is_derived_server_side():
    assert (await _fw("mtd")).period_label == "this month"
    assert (await _fw("ytd")).period_label == "this year"
    assert (await _fw("qtd")).period_label == "this quarter"


async def test_reconciliation_invariant():
    fw = await _fw("mtd")
    assert sum(a.refs for a in fw.referrers) == fw.captured      # leaderboard reconciles
    assert fw.lost == fw.buyer_closings - fw.captured
    assert fw.capture_target == 60 and fw.gap_at_target < fw.gap_dollars


async def test_attach_delta_null_when_prior_period_empty():
    # The seed's closings are all this month → last month is empty → delta is null.
    assert (await _fw("mtd")).attach_delta_pts is None


async def test_lineage_zero_and_agent_drills():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        fw = (await build_dashboard(s, t.id, "mtd")).flywheel
        z = await metric_detail(s, t.id, "flywheel_zero_referrals", "mtd", "sympli")
        assert z["count"] == len(fw.zero_agents)                 # call list == zero_agents
        top = fw.referrers[0]
        a = await metric_detail(s, t.id, "flywheel_agent_referrals", "mtd", "sympli", top.id)
        assert a["count"] == top.refs                            # only that agent's matches


async def test_unset_share_hides_the_dollars():
    """per_loan_share of 0 (or NULL) → the API returns null money fields, not a $0
    opportunity (the live bug). Kept last; restores the value in a finally."""
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        sym = (await s.execute(select(Business).where(
            Business.tenant_id == t.id, Business.key == "sympli"))).scalar_one()
        try:
            sym.per_loan_share = Decimal(0)
            await s.commit()
            fw = (await build_dashboard(s, t.id, "mtd")).flywheel
            assert fw.per_loan_share is None
            assert fw.gap_dollars is None and fw.gap_at_target is None and fw.per_point_value is None
        finally:
            sym.per_loan_share = Decimal(2100)
            await s.commit()
