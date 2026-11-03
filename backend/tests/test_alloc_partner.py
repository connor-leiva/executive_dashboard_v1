"""The partner view (ALLOCATION-RULES-SPEC 7.3, Phase 10).

Two answers from Connor on 2026-11-04 shape this entirely.

ALLOCATED leads. The headline is the figure after shared cost has been pushed in and out,
because that is what a partner is owed against. Booked stays beside it rather than hidden, which
is D10 and not a style preference: a partner asking why their number moved needs to see the
figure it moved from.

ACCRUAL, which is why there is no second revenue path. The books are already on that basis, so
this reads them as they are. Had the answer been cash, the view would have had to rebuild
revenue from payments and show the difference between the two readings as a thing in itself.
"""
import datetime as dt
from decimal import Decimal as D

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import (AccountPeriodBalance, AllocationContribution, Business, CoaMap,
                        StandardAccount, Tenant, User)
from app.seed import seed
from app.services import coa_balances as CB
from app.services.coa import seed_standard_chart

PERIOD = (dt.date(2026, 7, 1), dt.date(2026, 7, 31))
TODAY = dt.date(2026, 9, 30)
TRANSPORT = ASGITransport(app=app)

# A miniature entity that balances, with a draw account on it. Debit-positive throughout:
# revenue negative, costs positive, and a draw positive because taking money out is a debit to
# equity.
BOOKS = [
    ("100", "Operating Checking", "Bank", "1000", D("25000.00")),
    ("200", "Commission Income", "Income", "4010", D("-100000.00")),
    ("300", "Agent Splits", "Cost of Goods Sold", "5010", D("40000.00")),
    ("400", "Office Rent", "Expense", "7010", D("20000.00")),
    ("310", "Member Draws", "Equity", "3100", D("15000.00")),
]


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        await seed_standard_chart(s, t.id)
        have = {b.key for b in (await s.execute(
            select(Business).where(Business.tenant_id == t.id))).scalars()}
        if "the_forum" not in have:
            s.add(Business(tenant_id=t.id, key="the_forum", name="The Forum",
                           tag="Mastermind", archetype="program", sort_order=40))
        await s.commit()


async def _ctx():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        biz = {b.key: b.id for b in (await s.execute(
            select(Business).where(Business.tenant_id == t.id))).scalars()}
        std = {m.code: m.id for m in (await s.execute(select(StandardAccount).where(
            StandardAccount.tenant_id == t.id))).scalars()}
        return t.id, biz, std


@pytest.fixture(autouse=True)
async def _books():
    tenant_id, biz, std = await _ctx()
    async with SessionLocal() as s:
        await s.execute(delete(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))
        await s.execute(delete(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == biz["ulrg"]))
        await s.execute(delete(CoaMap).where(CoaMap.business_id == biz["ulrg"]))
        await s.commit()
    async with SessionLocal() as s:
        for qid, name, qtype, code, amount in BOOKS:
            s.add(CoaMap(tenant_id=tenant_id, business_id=biz["ulrg"], qbo_account_id=qid,
                         qbo_account_name=name, qbo_account_fqn=name, qbo_account_type=qtype,
                         standard_account_id=std[code], mapped_via="manual", is_ignored=False))
            s.add(AccountPeriodBalance(
                tenant_id=tenant_id, business_id=biz["ulrg"], period_start=PERIOD[0],
                period_end=PERIOD[1], qbo_account_id=qid, amount=amount, source="qbo_tb"))
        await s.commit()
    yield


async def _view(business_key="ulrg"):
    tenant_id, biz, _std = await _ctx()
    async with SessionLocal() as s:
        return await CB.partner_view(s, tenant_id, biz[business_key], PERIOD, today=TODAY)


async def test_it_leads_with_allocated_and_shows_booked_beside_it():
    """Connor's answer, as a test. Both figures present, allocated named as the lead."""
    v = await _view()
    assert v["leads_with"] == "allocated"
    assert v["basis"] == "accrual"
    assert "earned_allocated" in v and "earned_booked" in v
    # With no shared cost yet, the two agree and the effect is zero. That they are both
    # present and equal is the point: a reader can see nothing moved.
    assert v["earned_allocated"] == v["earned_booked"] == 40000.00
    assert v["shared_cost_effect"] == 0.0


async def test_the_draw_is_read_from_the_standard_chart_and_is_positive():
    """A draw is a debit to equity, and the module stores debit-positive throughout, so taking
    money out is a POSITIVE number here rather than something the caller has to negate."""
    v = await _view()
    assert v["drawn"] == 15000.00
    assert v["undrawn"] == 25000.00, "earned 40,000 less 15,000 taken"
    assert v["earned_allocated"] - v["drawn"] == v["undrawn"]


async def test_shared_cost_moves_the_headline_and_the_lines_say_why():
    """An inbound allocation raises this entity's cost, so its allocated profit falls below its
    booked one and `shared_cost_effect` is negative. The line carries the contribution that did
    it, so the partner can see which rule and whose."""
    tenant_id, biz, std = await _ctx()
    async with SessionLocal() as s:
        s.add(AllocationContribution(
            tenant_id=tenant_id, period_start=PERIOD[0], period_end=PERIOD[1],
            source_business_id=biz["springb"], target_business_id=biz["ulrg"],
            standard_account_id=std["7010"], amount=D("5000.00"),
            booking="applied", grain="window", pool_name="Advertising",
            basis="Tier 3 Split, 70/30"))
        await s.commit()
    v = await _view()
    assert v["earned_booked"] == 40000.00, "what the books alone say"
    assert v["earned_allocated"] == 35000.00, "after 5,000 of somebody else's cost came in"
    assert v["shared_cost_effect"] == -5000.00
    assert v["undrawn"] == 20000.00, "and the undrawn figure follows the allocated one"

    # Pinned on the CODE, not the name. The statement renders the STANDARD chart's name for a
    # line, not the QuickBooks alias the entity happens to use, so asserting "Office Rent" was
    # asserting the wrong half of the mapping.
    codes = [l["code"] for l in v["shared_lines"]]
    assert "7010" in codes, codes
    line = next(l for l in v["shared_lines"] if l["code"] == "7010")
    assert line["contributions"][0]["pool_name"] == "Advertising"
    assert line["contributions"][0]["counterparty_business"] == "Spring B"
    assert line["contributions"][0]["direction"] == "in"


async def test_an_entity_with_no_shared_cost_has_no_shared_lines_and_still_renders():
    v = await _view()
    assert v["shared_lines"] == []
    assert v["tie_out"]["status"] == "tied"
    assert v["business"]["name"]


async def test_the_route_answers_and_carries_both_readings():
    tenant_id, biz, _std = await _ctx()
    async with AsyncClient(transport=TRANSPORT, base_url="http://t") as c:
        tok = (await c.post("/api/v1/auth/login",
                            json={"email": "spring@springb.com",
                                  "password": "springtime"})).json()["token"]
        r = await c.get(f"/api/v1/books/allocations/partner/{biz['ulrg']}",
                        params={"period_start": "2026-07-01", "period_end": "2026-07-31"},
                        headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["leads_with"] == "allocated" and body["basis"] == "accrual"
    assert isinstance(body["earned_allocated"], float), "a number, never a formatted string"
    assert body["drawn"] == 15000.00
    assert "earned_booked" in body, "both readings always, never one (D10)"
