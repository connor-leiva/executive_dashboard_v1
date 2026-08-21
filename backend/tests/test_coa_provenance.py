"""Provenance — Phase 5 (SPEC-coa-mapping-provenance 6.2 to 6.5).

Every behavioural criterion in section 10 that concerns provenance lives here, plus the one the
spec could not have written: on this portfolio allocations are ALREADY in the books, so "as
booked" strips them out rather than "as allocated" adding them on. Backwards, this
double-counts every shared cost — and the tie-out cannot see it, because mapped and booked
would move together.
"""
import datetime as dt
from decimal import Decimal

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import delete, select

from app.main import app
from app.seed import seed
from app.db import SessionLocal
from app.models import (AccountPeriodBalance, AllocationContribution, AllocationRule, Business,
                        CoaMap, StandardAccount, Tenant)
from app.services import coa_alloc as AL
from app.services import coa_balances as CB
from app.services.coa import seed_standard_chart

TRANSPORT = ASGITransport(app=app)
PERIOD = (dt.date(2026, 7, 1), dt.date(2026, 7, 31))


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        await seed_standard_chart(s, t.id)
        if not (await s.execute(select(Business).where(
                Business.tenant_id == t.id, Business.key == "the_forum"))).scalars().first():
            s.add(Business(tenant_id=t.id, key="the_forum", name="The Forum",
                           tag="Mastermind", archetype="program", sort_order=40))
            await s.commit()


async def _ids():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        return t.id, {b.key: b.id for b in (await s.execute(
            select(Business).where(Business.tenant_id == t.id))).scalars()}


async def _std(s, tenant_id, code):
    return (await s.execute(select(StandardAccount).where(
        StandardAccount.tenant_id == tenant_id, StandardAccount.code == code))).scalar_one()


# The Forum in miniature. 8010 is entirely shared cost; 6090 is part shared and part its own;
# 6510 is all its own. That mix is what makes the share column mean anything.
FORUM = [
    ("1", "Shared Service Expenses:Shared Service Expense - Payroll", "Expense", "8010",
     Decimal("16000.00")),
    ("2", "Shared Service Expenses:Shared Service Expense - Advertising", "Expense", "6090",
     Decimal("12000.00")),
    ("3", "Advertising & Marketing", "Expense", "6090", Decimal("8000.00")),
    ("4", "Travel", "Expense", "6510", Decimal("2000.00")),
    ("5", "Due To SB Coaching", "Other Current Liability", "2400", Decimal("-28000.00")),
    ("6", "Ticket Revenue", "Income", "4410", Decimal("-10000.00")),
]


async def _setup(tenant_id, biz, rows=FORUM):
    ps, pe = PERIOD
    async with SessionLocal() as s:
        await s.execute(delete(AllocationContribution))
        await s.execute(delete(AllocationRule))
        await s.execute(delete(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == biz["the_forum"]))
        await s.execute(delete(CoaMap).where(CoaMap.business_id == biz["the_forum"]))
        await s.commit()
    async with SessionLocal() as s:
        for qid, fqn, qtype, code, amount in rows:
            std = await _std(s, tenant_id, code)
            s.add(CoaMap(tenant_id=tenant_id, business_id=biz["the_forum"], qbo_account_id=qid,
                         qbo_account_name=fqn.split(":")[-1], qbo_account_fqn=fqn,
                         qbo_account_type=qtype, standard_account_id=std.id,
                         mapped_via="manual"))
            s.add(AccountPeriodBalance(
                tenant_id=tenant_id, business_id=biz["the_forum"], period_start=ps,
                period_end=pe, qbo_account_id=qid, amount=amount, balance_end=amount,
                source="qbo_tb"))
        await s.commit()
    async with SessionLocal() as s:
        await AL.create_rule(s, tenant_id, None, pattern="Shared Service Expenses:",
                             pool_name="Shared Services",
                             source_business_id=biz["springb"],
                             target_business_id=biz["the_forum"],
                             basis="Headcount, 40/60",
                             driver_source="Staffing Allocation tab, Q3 2026")
        await AL.sync_allocations(s, tenant_id, PERIOD)


def _line(st, code):
    for sec in st["sections"]:
        for b in sec["buckets"]:
            for line in b["lines"]:
                if line["code"] == code:
                    return line
    return None


def _flagged(st):
    return [line for sec in st["sections"] for b in sec["buckets"]
            for line in b["lines"] if line["flagged"]]


# ── composition (6.2) ─────────────────────────────────────────────────────────────────────

async def test_as_booked_strips_what_somebody_else_funded():
    """The books already carry the shared cost, so `allocated` is the observed number and
    `booked` is the entity's own activity."""
    tenant_id, biz = await _ids()
    await _setup(tenant_id, biz)
    async with SessionLocal() as s:
        st = await CB.build_mapped_statement(s, tenant_id, biz["the_forum"], PERIOD)
    payroll = _line(st, "8010")
    assert payroll["as_allocated"] == 16000.0, "what QuickBooks says"
    assert payroll["as_booked"] == 0.0, "none of it was The Forum's own spending"
    adv = _line(st, "6090")
    assert adv["as_allocated"] == 20000.0
    assert adv["as_booked"] == 8000.0, "its own advertising survives the strip"


async def test_the_mode_toggle_moves_net_income_not_just_styling():
    tenant_id, biz = await _ids()
    await _setup(tenant_id, biz)
    async with SessionLocal() as s:
        alloc = await CB.build_mapped_statement(s, tenant_id, biz["the_forum"], PERIOD,
                                                mode="allocated")
        booked = await CB.build_mapped_statement(s, tenant_id, biz["the_forum"], PERIOD,
                                                 mode="booked")
    assert alloc["totals"]["operating_expenses"] == 38000.0
    assert booked["totals"]["operating_expenses"] == 10000.0
    assert alloc["totals"]["net_income"] != booked["totals"]["net_income"]
    # Both figures ride along in either mode, so the toggle's worth is visible without flipping.
    assert alloc["net_income_booked"] == booked["totals"]["net_income"]
    assert booked["net_income_allocated"] == alloc["totals"]["net_income"]


async def test_a_line_that_nets_to_nothing_has_no_share_rather_than_zero_percent():
    """`ic_share` is undefined, not zero, when the line nets to zero — rendered blank, because
    0.0% reads as a measured result."""
    tenant_id, biz = await _ids()
    rows = FORUM + [("7", "Suspense", "Expense", "8690", Decimal("0.00"))]
    await _setup(tenant_id, biz, rows)
    async with SessionLocal() as s:
        st = await CB.build_mapped_statement(s, tenant_id, biz["the_forum"], PERIOD)
    zero = _line(st, "8690")
    assert zero is not None and zero["as_allocated"] == 0.0
    assert zero["ic_share_pct"] is None
    # Also blank where there IS a denominator but no intercompany: "0.0%" on every direct
    # row reads as a measured result, and it is noise on the rows that need no attention.
    assert _line(st, "6510")["ic_share_pct"] is None, "no intercompany at all, so no share"
    assert _line(st, "6510")["as_allocated"] == 2000.0, "and the line itself is not empty"


# ── flagging (6.3, 6.4) ───────────────────────────────────────────────────────────────────

async def test_only_lines_with_intercompany_are_flagged():
    tenant_id, biz = await _ids()
    await _setup(tenant_id, biz)
    async with SessionLocal() as s:
        st = await CB.build_mapped_statement(s, tenant_id, biz["the_forum"], PERIOD)
    assert _line(st, "8010")["flagged"] is True
    assert _line(st, "8010")["provenance"] == "allocated"
    assert _line(st, "6090")["flagged"] is True
    assert _line(st, "6090")["ic_share_pct"] == 60.0
    own = _line(st, "6510")
    assert own["flagged"] is False and own["provenance"] == "direct"


async def test_a_higher_threshold_flags_strictly_fewer_lines():
    tenant_id, biz = await _ids()
    await _setup(tenant_id, biz)
    async with SessionLocal() as s:
        any_pct = await CB.build_mapped_statement(s, tenant_id, biz["the_forum"], PERIOD,
                                                  threshold_pct=0)
        high = await CB.build_mapped_statement(s, tenant_id, biz["the_forum"], PERIOD,
                                               threshold_pct=75)
    assert len(_flagged(high)) < len(_flagged(any_pct)), "60% drops out at a 75% threshold"
    assert _line(high, "8010")["flagged"] is True, "100% stays"
    assert _line(high, "6090")["flagged"] is False


async def test_due_to_and_due_from_never_flag():
    """Entirely intercompany by definition. Flagging them is noise that dilutes the signal
    everywhere else (SPEC 6.4)."""
    tenant_id, biz = await _ids()
    await _setup(tenant_id, biz)
    async with SessionLocal() as s:
        st = await CB.build_mapped_statement(s, tenant_id, biz["the_forum"], PERIOD,
                                             statement="bs", threshold_pct=0)
    due_to = _line(st, "2400")
    assert due_to is not None and due_to["is_intercompany_account"] is True
    assert due_to["flagged"] is False


async def test_the_reserved_third_state_exists_and_is_never_set():
    """ADJUSTED is defined now so depreciation and deferred revenue have somewhere to go later.
    Retrofitting a third state onto a boolean is the expensive version of this."""
    assert CB.Provenance.ADJUSTED.value == "adjusted"
    tenant_id, biz = await _ids()
    await _setup(tenant_id, biz)
    async with SessionLocal() as s:
        st = await CB.build_mapped_statement(s, tenant_id, biz["the_forum"], PERIOD)
    seen = {line["provenance"] for sec in st["sections"] for b in sec["buckets"]
            for line in b["lines"]}
    assert seen <= {"direct", "allocated"} and "adjusted" not in seen


# ── the composition panel (6.5, 7.4) ──────────────────────────────────────────────────────

async def test_a_flagged_line_carries_its_composition_and_an_unflagged_one_carries_none():
    """The frontend must not need a second request to expand a row."""
    tenant_id, biz = await _ids()
    await _setup(tenant_id, biz)
    async with SessionLocal() as s:
        st = await CB.build_mapped_statement(s, tenant_id, biz["the_forum"], PERIOD)
    c = _line(st, "8010")["contributions"]
    assert len(c) == 1
    assert c[0]["direction"] == "in"
    assert c[0]["counterparty_business"] == "Spring B"
    assert c[0]["amount"] == 16000.0
    assert c[0]["pool_name"] == "Shared Services"
    assert c[0]["basis"] == "Headcount, 40/60"
    assert c[0]["driver_source"] == "Staffing Allocation tab, Q3 2026"
    assert _line(st, "6510")["contributions"] == []


async def test_the_funder_sees_no_phantom_cost_on_its_own_pl():
    """Spring B books its payment to a receivable, so the cost was never on its P&L. Treating
    the outbound side as a P&L movement would invent an expense it never had."""
    tenant_id, biz = await _ids()
    await _setup(tenant_id, biz)
    ps, pe = PERIOD
    async with SessionLocal() as s:
        await s.execute(delete(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == biz["springb"]))
        await s.execute(delete(CoaMap).where(CoaMap.business_id == biz["springb"]))
        await s.commit()
    async with SessionLocal() as s:
        for qid, fqn, code, amount in (("s1", "Payroll Expenses", "8010", Decimal("860.00")),
                                       ("s2", "Due from The Forum", "1300", Decimal("28000.00")),
                                       ("s3", "Operating Cash", "1000", Decimal("-28860.00"))):
            std = await _std(s, tenant_id, code)
            s.add(CoaMap(tenant_id=tenant_id, business_id=biz["springb"], qbo_account_id=qid,
                         qbo_account_name=fqn, qbo_account_fqn=fqn, qbo_account_type="Expense",
                         standard_account_id=std.id, mapped_via="manual"))
            s.add(AccountPeriodBalance(
                tenant_id=tenant_id, business_id=biz["springb"], period_start=ps, period_end=pe,
                qbo_account_id=qid, amount=amount, balance_end=amount, source="qbo_tb"))
        await s.commit()
    async with SessionLocal() as s:
        st = await CB.build_mapped_statement(s, tenant_id, biz["springb"], PERIOD)
    payroll = _line(st, "8010")
    assert payroll["as_allocated"] == 860.0
    assert payroll["as_booked"] == 860.0, "not 28,860 — the funded cost was never Spring B's"
    assert payroll["flagged"] is False


# ── the API ───────────────────────────────────────────────────────────────────────────────

async def test_the_api_carries_mode_threshold_and_refuses_a_nonsense_mode():
    tenant_id, biz = await _ids()
    await _setup(tenant_id, biz)
    q = (f"business_id={biz['the_forum']}"
         "&period_start=2026-07-01&period_end=2026-07-31")
    async with AsyncClient(transport=TRANSPORT, base_url="http://testserver") as c:
        tok = (await c.post("/api/v1/auth/login", json={
            "email": "spring@springb.com", "password": "springtime"})).json()["token"]
        H = {"Authorization": f"Bearer {tok}"}
        a = await c.get(f"/api/v1/books/statement?{q}", headers=H)
        b = await c.get(f"/api/v1/books/statement?{q}&mode=booked", headers=H)
        bad = await c.get(f"/api/v1/books/statement?{q}&mode=sideways", headers=H)
    assert a.status_code == 200 and a.json()["mode"] == "allocated"
    assert a.json()["threshold_pct"] == 5.0
    assert b.status_code == 200 and b.json()["mode"] == "booked"
    assert a.json()["totals"]["net_income"] != b.json()["totals"]["net_income"]
    assert bad.status_code == 400
