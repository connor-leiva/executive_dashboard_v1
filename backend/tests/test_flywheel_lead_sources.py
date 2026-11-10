"""Lead source on the Referral Flywheel drawer (spec phase 7), from the seeded fixtures -- no network."""
import datetime as dt

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Business, Lead, LeadSourceGroup, Tenant, Transaction
from app.services.lineage import metric_detail
from app.services.metrics import build_dashboard


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    from app.seed import seed
    await seed()


async def _ctx(s):
    t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
    ulrg = (await s.execute(select(Business).where(Business.tenant_id == t.id, Business.key == "ulrg"))).scalar_one()
    return t, ulrg


async def test_a_realtor_lead_is_under_realtor_com_and_not_realty_and_the_card_does_not_move():
    async with SessionLocal() as s:
        t, ulrg = await _ctx(s)
        before = (await build_dashboard(s, t.id, "mtd")).flywheel
        buys = (await s.execute(select(Transaction).where(
            Transaction.tenant_id == t.id, Transaction.side == "buy", Transaction.status == "closed",
            Transaction.mortgage_vid == 155743))).scalars().all()
        tagged, plain = buys[0], buys[1]
        lead = Lead(tenant_id=t.id, business_id=ulrg.id, external_id="fw-lead", origin="Realtor.com MVIP",
                    created_at_src=dt.date(2026, 3, 1))
        s.add(lead)
        await s.flush()
        tagged.lead_id = lead.id
        realty = (await s.execute(select(LeadSourceGroup).where(
            LeadSourceGroup.tenant_id == t.id, LeadSourceGroup.key == "realty_com"))).scalar_one()
        realty.is_active, realty.variants = True, ["Realty.com"]
        await s.commit()

        d = await metric_detail(s, t.id, "flywheel_captured", "mtd", "sympli")
        by_id = {r["id"]: r for r in d["rows"]}
        assert by_id[str(tagged.id)]["lead_source_key"] == "realtor_com"
        assert by_id[str(plain.id)]["lead_source_key"] == "unassigned"          # no lead, no lead_type
        chips = {c["key"]: c for c in d["lead_sources"]}
        assert chips["realtor_com"]["count"] == 1 and chips["realty_com"]["count"] == 0
        assert chips["realtor_com"]["label"] == "Realtor.com" and chips["unassigned"]["label"] == "Unassigned"
        assert sum(c["count"] for c in d["lead_sources"]) == len(d["rows"])

        after = (await build_dashboard(s, t.id, "mtd")).flywheel
        assert (after.capture_pct, after.captured, after.buyer_closings) == (
            before.capture_pct, before.captured, before.buyer_closings)
        realty.is_active, realty.variants = False, []
        await s.commit()


async def test_lead_type_is_the_fallback_and_the_agent_list_has_no_chips():
    async with SessionLocal() as s:
        t, _ulrg = await _ctx(s)
        buys = (await s.execute(select(Transaction).where(
            Transaction.tenant_id == t.id, Transaction.side == "buy", Transaction.status == "closed",
            Transaction.lead_id.is_(None)))).scalars().all()
        buys[0].lead_type = "realtor.com mvip"
        await s.commit()
        d = await metric_detail(s, t.id, "flywheel_buyers", "mtd", "sympli")
        row = next(r for r in d["rows"] if r["id"] == str(buys[0].id))
        assert row["lead_source_key"] == "realtor_com"
        z = await metric_detail(s, t.id, "flywheel_zero_referrals", "mtd", "sympli")
        assert "lead_sources" not in z and all("lead_source_key" not in r for r in z["rows"])


async def test_loan_rows_take_the_matched_deals_source_or_unassigned():
    async with SessionLocal() as s:
        t, _ulrg = await _ctx(s)
        d = await metric_detail(s, t.id, "flywheel_sympli_referred", "mtd", "sympli")
        assert d["rows"], "the seed credits loans to Utah Life"
        assert {r["lead_source_key"] for r in d["rows"]} <= {"realtor_com", "realty_com", "unassigned"}
        nodeal = await metric_detail(s, t.id, "flywheel_referral_no_deal", "mtd", "sympli")
        assert all(r["lead_source_key"] == "unassigned" for r in nodeal["rows"])
