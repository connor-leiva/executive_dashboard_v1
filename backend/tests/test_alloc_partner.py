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


# ── what the draw query must not reach (added after a mutation run) ───────────────────────
#
# Dropping the business filter and dropping the period filter both left all five tests above
# passing. Either one would put somebody else's money, or another period's, on a partner's
# page under their own name, which is the worst thing this view could do.

PROBE_ACCOUNT = "910"            # the_forum's draw, cleaned up by qbo id so nothing else moves


@pytest.fixture(autouse=True)
async def _probe_cleanup():
    """Scoped to ONE account id on ONE entity, deliberately. This module already takes
    ownership of `ulrg`'s books; widening that to a second entity wholesale would delete rows
    another test module set up, so the cleanup names the account instead."""
    yield
    tenant_id, biz, _std = await _ctx()
    async with SessionLocal() as s:
        await s.execute(delete(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == biz["the_forum"],
            AccountPeriodBalance.qbo_account_id == PROBE_ACCOUNT))
        await s.execute(delete(CoaMap).where(
            CoaMap.business_id == biz["the_forum"],
            CoaMap.qbo_account_id == PROBE_ACCOUNT))
        await s.commit()


async def test_the_draw_is_this_entity_s_only():
    """Another entity draws in the same period. Each view reports its own figure, and neither
    reports the sum: a partner page showing a number that includes somebody else's draw is
    wrong in the direction that matters, because it reads as a statement about them."""
    tenant_id, biz, std = await _ctx()
    async with SessionLocal() as s:
        s.add(CoaMap(tenant_id=tenant_id, business_id=biz["the_forum"],
                     qbo_account_id=PROBE_ACCOUNT, qbo_account_name="Partner Draws",
                     qbo_account_fqn="Partner Draws", qbo_account_type="Equity",
                     standard_account_id=std["3100"], mapped_via="manual", is_ignored=False))
        s.add(AccountPeriodBalance(
            tenant_id=tenant_id, business_id=biz["the_forum"], period_start=PERIOD[0],
            period_end=PERIOD[1], qbo_account_id=PROBE_ACCOUNT, amount=D("4000.00"),
            source="qbo_tb"))
        await s.commit()

    assert (await _view())["drawn"] == 15000.00, "unchanged by the other entity's draw"
    assert (await _view("the_forum"))["drawn"] == 4000.00, "its own draw, not the sum"


async def test_the_draw_is_this_period_s_only():
    """A draw in August does not belong on July's page. Without the period filter a monthly
    view would report every draw ever taken, and it would grow every month while looking
    like a figure for the month on the heading."""
    tenant_id, biz, _std = await _ctx()
    async with SessionLocal() as s:
        s.add(AccountPeriodBalance(
            tenant_id=tenant_id, business_id=biz["ulrg"], period_start=dt.date(2026, 8, 1),
            period_end=dt.date(2026, 8, 31), qbo_account_id="310", amount=D("9000.00"),
            source="qbo_tb"))
        # SAME START, different end. The row above moves both bounds, so it is excluded the
        # moment EITHER predicate survives and the test passes with one of them deleted. A
        # year-to-date row starting on the same day is the realistic shape of that mistake,
        # and only `period_end` keeps it out.
        s.add(AccountPeriodBalance(
            tenant_id=tenant_id, business_id=biz["ulrg"], period_start=PERIOD[0],
            period_end=dt.date(2026, 12, 31), qbo_account_id="310", amount=D("7000.00"),
            source="qbo_tb"))
        await s.commit()
    v = await _view()
    assert v["drawn"] == 15000.00, "neither the later month nor the wider window counted"
    assert v["undrawn"] == 25000.00


async def test_the_view_cannot_be_flipped_by_the_statement_s_mode():
    """Why the mutation that set `mode="booked"` survived: nothing this view reads depends on
    it. `mode` chooses each line's `amount` key and the `totals` built from it, while the two
    net-income figures are computed from `as_booked` and `as_allocated` independently and both
    always ship (D10). The view reads those, so its answer is mode-independent BY CONSTRUCTION.

    Pinned because that is a property worth keeping rather than a coincidence: a later change
    that read `amount` or `totals` here would make a partner's headline follow a display toggle,
    and this test is what would notice."""
    tenant_id, biz, _std = await _ctx()
    async with SessionLocal() as s:
        allocated = await CB.build_mapped_statement(s, tenant_id, biz["ulrg"], PERIOD, "pl",
                                                    mode="allocated")
        booked = await CB.build_mapped_statement(s, tenant_id, biz["ulrg"], PERIOD, "pl",
                                                 mode="booked")
    assert allocated["totals"]["net_income"] != 0, "a statement with something in it"
    for key in ("net_income_allocated", "net_income_booked"):
        assert allocated[key] == booked[key], f"{key} does not follow the mode"

    v = await _view()
    assert v["earned_allocated"] == allocated["net_income_allocated"] == booked["net_income_allocated"]
    assert v["earned_booked"] == allocated["net_income_booked"] == booked["net_income_booked"]


# ── the materiality gate this view must not inherit (found by review, not by mutation) ────
#
# `build_mapped_statement` carries a line's `contributions` only when the line is FLAGGED, which
# means at least `ic_flag_threshold_pct` (5 by default) of the line is intercompany. That is a
# materiality judgement about what is worth expanding on the Statement. `earned_allocated`
# obeys no such gate: it moves on every line with a non-zero `ic_net`.
#
# The shipped test above uses 5,000 against a 20,000 line, 25 percent, so it sails over the gate
# and never saw it. Both tests here sit under it.

async def test_a_small_allocation_still_shows_the_line_it_moved():
    """2.44 percent of the line, well under the 5 percent flag threshold. Before `compose_all`
    the page rendered "Cost came in from another entity" directly above "No shared cost reached
    this entity in this period", which is the page contradicting its own headline."""
    tenant_id, biz, std = await _ctx()
    async with SessionLocal() as s:
        s.add(AllocationContribution(
            tenant_id=tenant_id, period_start=PERIOD[0], period_end=PERIOD[1],
            source_business_id=biz["springb"], target_business_id=biz["ulrg"],
            standard_account_id=std["7010"], amount=D("500.00"),
            booking="applied", grain="window", pool_name="Advertising",
            basis="Tier 3 Split, 70/30"))
        await s.commit()
    v = await _view()

    assert v["shared_cost_effect"] == -500.00, "the headline moved"
    assert v["shared_lines"], "so the table must say which line moved"
    line = next(l for l in v["shared_lines"] if l["code"] == "7010")
    assert line["as_booked"] == 20000.00 and line["as_allocated"] == 20500.00
    assert line["contributions"][0]["amount"] == 500.00


async def test_a_line_allocated_away_entirely_still_appears():
    """Worse than small, and size-independent. A line allocated out in full nets `as_allocated`
    to zero, so it has no intercompany SHARE to compare against any threshold and was dropped
    whatever the amount. 20,000 left this entity and the table showed nothing."""
    tenant_id, biz, std = await _ctx()
    async with SessionLocal() as s:
        s.add(AllocationContribution(
            tenant_id=tenant_id, period_start=PERIOD[0], period_end=PERIOD[1],
            source_business_id=biz["ulrg"], target_business_id=biz["the_forum"],
            standard_account_id=std["7010"], amount=D("20000.00"),
            booking="applied", grain="window", pool_name="Rent",
            basis="Tier 1 Direct"))
        await s.commit()
    v = await _view()

    assert v["earned_booked"] == 40000.00
    assert v["earned_allocated"] == 60000.00, "the whole rent line left"
    assert v["shared_cost_effect"] == 20000.00
    line = next(l for l in v["shared_lines"] if l["code"] == "7010")
    assert line["as_allocated"] == 0.00, "nothing of it stayed"
    assert line["contributions"][0]["direction"] == "out"
    assert line["contributions"][0]["amount"] == -20000.00, "out renders negative (7.4)"


async def test_the_statement_itself_still_obeys_the_materiality_gate():
    """`compose_all` must widen the PARTNER view only. The Statement's promise is unchanged:
    contributions on a flagged line, empty otherwise, so the page never needs a second request
    to expand a row (6.5). A flag that quietly widened both would make every statement heavier
    and change a payload Phase 2 promised not to move."""
    tenant_id, biz, std = await _ctx()
    async with SessionLocal() as s:
        s.add(AllocationContribution(
            tenant_id=tenant_id, period_start=PERIOD[0], period_end=PERIOD[1],
            source_business_id=biz["springb"], target_business_id=biz["ulrg"],
            standard_account_id=std["7010"], amount=D("500.00"),
            booking="applied", grain="window", pool_name="Advertising", basis="small"))
        await s.commit()
    async with SessionLocal() as s:
        plain = await CB.build_mapped_statement(s, tenant_id, biz["ulrg"], PERIOD, "pl",
                                                mode="allocated")
        wide = await CB.build_mapped_statement(s, tenant_id, biz["ulrg"], PERIOD, "pl",
                                               mode="allocated", compose_all=True)

    def contribs(stmt, code):
        for sec in stmt["sections"]:
            for buc in sec["buckets"]:
                for line in buc["lines"]:
                    if line["code"] == code:
                        return line["contributions"], line["flagged"]
        raise AssertionError(f"no line {code}")

    plain_c, plain_flagged = contribs(plain, "7010")
    wide_c, wide_flagged = contribs(wide, "7010")
    assert plain_flagged is False and wide_flagged is False, "the FLAG does not move"
    assert plain_c == [], "the Statement still drops a sub-threshold line's detail"
    assert len(wide_c) == 1, "the partner view does not"


async def test_an_unknown_business_is_a_bad_request_not_a_crash():
    """`build_mapped_statement` raises ValueError for an id that is not this tenant's. Unhandled
    it answered 500, which tells a caller the server broke when the caller sent a bad id, and
    hides a tenant-isolation refusal inside an error class that reads as an outage."""
    import uuid
    async with AsyncClient(transport=TRANSPORT, base_url="http://t") as c:
        tok = (await c.post("/api/v1/auth/login",
                            json={"email": "spring@springb.com",
                                  "password": "springtime"})).json()["token"]
        r = await c.get(f"/api/v1/books/allocations/partner/{uuid.uuid4()}",
                        params={"period_start": "2026-07-01", "period_end": "2026-07-31"},
                        headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 400, r.status_code
