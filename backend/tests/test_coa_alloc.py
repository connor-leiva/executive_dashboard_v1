"""Shared-cost allocations — Phase 4 (SPEC-coa-mapping-provenance 2.5, 6.6).

What these defend:

- Contributions are OBSERVED. The cost is already on the target's books, so a contribution
  describes a line rather than adding to it. Getting this backwards double-counts every
  shared cost in the portfolio.
- The zero-sum check is structural and therefore weak. The reconciliation against actual
  intercompany movement is the one that finds things — it is what catches money crossing
  between entities that no allocation explains.
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
from app.services.coa import seed_standard_chart

TRANSPORT = ASGITransport(app=app)
PERIOD = (dt.date(2026, 7, 1), dt.date(2026, 7, 31))


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        await seed_standard_chart(s, t.id)
        # The demo seed carries three entities; the allocations live between Spring B and The
        # Forum, so the fixture adds the one it needs rather than the tests bending around
        # whichever businesses happen to be seeded.
        if not (await s.execute(select(Business).where(
                Business.tenant_id == t.id, Business.key == "the_forum"))).scalars().first():
            s.add(Business(tenant_id=t.id, key="the_forum", name="The Forum",
                           tag="Mastermind", archetype="program", sort_order=40))
            await s.commit()


async def _ids():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        biz = {b.key: b.id for b in (await s.execute(
            select(Business).where(Business.tenant_id == t.id))).scalars()}
        return t.id, biz


async def _std(s, tenant_id, code):
    return (await s.execute(select(StandardAccount).where(
        StandardAccount.tenant_id == tenant_id, StandardAccount.code == code))).scalar_one()


async def _books(tenant_id, business_id, rows, period=PERIOD):
    """The Forum's shape, in miniature: its own advertising, a shared-service subtree funded by
    somebody else, and the payable that shared cost created."""
    ps, pe = period
    async with SessionLocal() as s:
        await s.execute(delete(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == business_id))
        await s.execute(delete(CoaMap).where(CoaMap.business_id == business_id))
        await s.commit()
    async with SessionLocal() as s:
        for qid, fqn, qtype, code, amount in rows:
            std = await _std(s, tenant_id, code) if code else None
            s.add(CoaMap(tenant_id=tenant_id, business_id=business_id, qbo_account_id=qid,
                         qbo_account_name=fqn.split(":")[-1], qbo_account_fqn=fqn,
                         qbo_account_type=qtype,
                         standard_account_id=(std.id if std else None),
                         mapped_via=("manual" if std else None)))
            s.add(AccountPeriodBalance(
                tenant_id=tenant_id, business_id=business_id, period_start=ps, period_end=pe,
                qbo_account_id=qid, amount=amount, balance_end=amount, source="qbo_tb"))
        await s.commit()


FORUM = [
    ("1", "Shared Service Expenses:Shared Service Expense - Payroll", "Expense", "8010",
     Decimal("16022.00")),
    ("2", "Shared Service Expenses:Shared Service Expense - Advertising", "Expense", "6090",
     Decimal("13491.00")),
    ("3", "Shared Service Expenses:Shared Service Expense - Dues", "Expense", "8510",
     Decimal("2937.07")),
    ("4", "Advertising & Marketing", "Expense", "6090", Decimal("6455.00")),
    # The payable the shared cost created: a credit, so debit-positive negative.
    ("5", "Due To SB Coaching", "Other Current Liability", "2400", Decimal("-32450.07")),
    ("6", "Ticket Revenue", "Income", "4410", Decimal("-6455.00")),
]


async def _rule(tenant_id, biz, target="the_forum", source="springb",
                pattern="Shared Service Expenses:"):
    async with SessionLocal() as s:
        return await AL.create_rule(
            s, tenant_id, None, pattern=pattern, pool_name="Shared Services",
            source_business_id=biz[source], target_business_id=biz[target],
            basis="Headcount, 40/60", driver_source="Staffing Allocation tab, Q3 2026")


async def _clear(tenant_id):
    async with SessionLocal() as s:
        await s.execute(delete(AllocationContribution))
        await s.execute(delete(AllocationRule))
        await s.commit()


# ── matching ──────────────────────────────────────────────────────────────────────────────

class _R:
    def __init__(self, pattern, sid, active=True):
        self.pattern, self.source_business_id, self.is_active = pattern, sid, active


def test_longest_prefix_wins_here_too():
    """The same mechanism coa_map_rule uses, for the same reason."""
    rules = [_R("Shared Service Expenses:", "broad"),
             _R("Shared Service Expenses:Shared Service Expense - Payroll", "narrow")]
    got = AL.match_alloc_rule("Shared Service Expenses:Shared Service Expense - Payroll", rules)
    assert got.source_business_id == "narrow"
    assert AL.match_alloc_rule(
        "Shared Service Expenses:Shared Service Expense - Dues", rules).source_business_id == "broad"
    assert AL.match_alloc_rule("Advertising & Marketing", rules) is None
    assert AL.match_alloc_rule("Shared Service Expenses:x",
                               [_R("shared service expenses:", "s", active=False)]) is None


# ── deriving ──────────────────────────────────────────────────────────────────────────────

async def test_a_rule_turns_a_subtree_into_contributions():
    tenant_id, biz = await _ids()
    await _clear(tenant_id)
    await _books(tenant_id, biz["the_forum"], FORUM)
    await _rule(tenant_id, biz)
    async with SessionLocal() as s:
        stat = await AL.sync_allocations(s, tenant_id, PERIOD)
    assert stat["contributions"] == 3, "one per standard account the subtree lands on"
    assert stat["amount"] == 32450.07
    assert stat["pairs"] == 1
    async with SessionLocal() as s:
        rows = list((await s.execute(select(AllocationContribution))).scalars())
    assert {r.booking for r in rows} == {"observed"}
    assert all(r.pool_name == "Shared Services" for r in rows)
    assert all(r.basis == "Headcount, 40/60" for r in rows), "policy travels from the rule"
    assert all(r.source_business_id == biz["springb"] for r in rows)
    assert all(r.target_business_id == biz["the_forum"] for r in rows)


async def test_the_entitys_own_spending_is_not_a_contribution():
    """Forum's own advertising and the shared advertising both land on 6090. Only the shared
    part is a contribution, or the composition would claim the whole line was funded."""
    tenant_id, biz = await _ids()
    await _clear(tenant_id)
    await _books(tenant_id, biz["the_forum"], FORUM)
    await _rule(tenant_id, biz)
    async with SessionLocal() as s:
        await AL.sync_allocations(s, tenant_id, PERIOD)
        adv = await _std(s, tenant_id, "6090")
        rows = list((await s.execute(select(AllocationContribution).where(
            AllocationContribution.standard_account_id == adv.id))).scalars())
    assert len(rows) == 1
    assert rows[0].amount == Decimal("13491.00"), "not 19,946 — the own-spend stays out"


async def test_rebuilding_replaces_rather_than_accumulates():
    """A rule that stops matching must stop contributing. An upsert would leave the old
    contribution behind, and a stale allocation reconciles perfectly against last month."""
    tenant_id, biz = await _ids()
    await _clear(tenant_id)
    await _books(tenant_id, biz["the_forum"], FORUM)
    rule = await _rule(tenant_id, biz)
    async with SessionLocal() as s:
        await AL.sync_allocations(s, tenant_id, PERIOD)
        await AL.sync_allocations(s, tenant_id, PERIOD)
        n = len((await s.execute(select(AllocationContribution))).scalars().all())
    assert n == 3, "run twice, still three"
    async with SessionLocal() as s:
        await AL.update_rule(s, tenant_id, None, rule.id, is_active=False)
        await AL.sync_allocations(s, tenant_id, PERIOD)
        n = len((await s.execute(select(AllocationContribution))).scalars().all())
    assert n == 0, "deactivating the rule withdraws its contributions"


async def test_an_entity_cannot_fund_its_own_costs():
    tenant_id, biz = await _ids()
    await _clear(tenant_id)
    async with SessionLocal() as s:
        with pytest.raises(ValueError):
            await AL.create_rule(s, tenant_id, None, pattern="Shared Service Expenses:",
                                 pool_name="Shared Services",
                                 source_business_id=biz["the_forum"],
                                 target_business_id=biz["the_forum"])


async def test_a_pool_needs_a_name_and_a_pattern_needs_substance():
    tenant_id, biz = await _ids()
    await _clear(tenant_id)
    async with SessionLocal() as s:
        with pytest.raises(ValueError):
            await AL.create_rule(s, tenant_id, None, pattern="Shared Service Expenses:",
                                 pool_name="   ", source_business_id=biz["springb"],
                                 target_business_id=biz["the_forum"])
        with pytest.raises(ValueError):
            await AL.create_rule(s, tenant_id, None, pattern="x", pool_name="Shared",
                                 source_business_id=biz["springb"],
                                 target_business_id=biz["the_forum"])


# ── the checks (6.6) ──────────────────────────────────────────────────────────────────────

async def test_the_intercompany_reconciliation_is_the_one_that_finds_things():
    """Zero-sum is structural: one row makes both sides, so it always balances. What matters
    is whether the intercompany account moved by what the allocations explain."""
    tenant_id, biz = await _ids()
    await _clear(tenant_id)
    await _books(tenant_id, biz["the_forum"], FORUM)
    await _rule(tenant_id, biz)
    async with SessionLocal() as s:
        await AL.sync_allocations(s, tenant_id, PERIOD)
        check = await AL.allocation_check(s, tenant_id, PERIOD)

    assert check["zero_sum"]["balanced"] is True and check["zero_sum"]["net"] == 0.0
    forum = next(e for e in check["entities"] if e["business"] == "the_forum")
    # Charged 32,450.07 of expense, took on 32,450.07 of payable. Opposite signs, nets to zero.
    assert forum["allocated_net"] == 32450.07
    assert forum["intercompany_movement"] == -32450.07
    assert forum["unexplained"] == 0.0
    assert check["pairs"][0]["amount"] == 32450.07


async def test_money_crossing_that_no_allocation_explains_is_surfaced():
    """beCollective's July intercompany balance moved 5,000 more than its shared-service
    charge. That is a cash transfer, invisible to the zero-sum check, and exactly what a close
    checklist should put in front of a person."""
    tenant_id, biz = await _ids()
    await _clear(tenant_id)
    rows = FORUM[:-2] + [
        ("5", "Due To SB Coaching", "Other Current Liability", "2400", Decimal("-37450.07")),
        ("6", "Ticket Revenue", "Income", "4410", Decimal("-6455.00")),
        ("7", "Operating Cash", "Bank", "1000", Decimal("5000.00")),
    ]
    await _books(tenant_id, biz["the_forum"], rows)
    await _rule(tenant_id, biz)
    async with SessionLocal() as s:
        await AL.sync_allocations(s, tenant_id, PERIOD)
        check = await AL.allocation_check(s, tenant_id, PERIOD)
    forum = next(e for e in check["entities"] if e["business"] == "the_forum")
    assert forum["unexplained"] == -5000.0, "the transfer shows up, named as unexplained"
    assert check["zero_sum"]["balanced"] is True, "and zero-sum still says everything is fine"


async def test_contributions_split_into_in_and_out_for_each_side():
    """The shape Phase 5's composition needs: the target sees it as inbound, the funder as
    outbound, from one row."""
    tenant_id, biz = await _ids()
    await _clear(tenant_id)
    await _books(tenant_id, biz["the_forum"], FORUM)
    await _rule(tenant_id, biz)
    async with SessionLocal() as s:
        await AL.sync_allocations(s, tenant_id, PERIOD)
        target = await AL.contributions_for(s, tenant_id, biz["the_forum"], PERIOD)
        source = await AL.contributions_for(s, tenant_id, biz["springb"], PERIOD)
    assert len(target["in"]) == 3 and target["out"] == {}
    assert len(source["out"]) == 3 and source["in"] == {}
    assert sum(c.amount for cs in target["in"].values() for c in cs) == Decimal("32450.07")


# ── the API ───────────────────────────────────────────────────────────────────────────────

async def _token(email="spring@springb.com", pw="springtime"):
    async with AsyncClient(transport=TRANSPORT, base_url="http://testserver") as c:
        r = await c.post("/api/v1/auth/login", json={"email": email, "password": pw})
    return r.json()["token"]


async def test_rules_are_a_cfo_decision_and_the_check_is_readable():
    tenant_id, biz = await _ids()
    await _clear(tenant_id)
    await _books(tenant_id, biz["the_forum"], FORUM)
    owner = await _token()
    H = {"Authorization": f"Bearer {owner}"}
    q = "period_start=2026-07-01&period_end=2026-07-31"
    async with AsyncClient(transport=TRANSPORT, base_url="http://testserver") as c:
        r = await c.post("/api/v1/books/allocations/rules", headers=H, json={
            "pattern": "Shared Service Expenses:", "pool_name": "Shared Services",
            "source_business_id": str(biz["springb"]),
            "target_business_id": str(biz["the_forum"]), "basis": "Headcount, 40/60"})
        assert r.status_code == 200
        built = await c.post(f"/api/v1/books/allocations/rebuild?{q}", headers=H)
        assert built.status_code == 200 and built.json()["contributions"] == 3
        chk = await c.get(f"/api/v1/books/allocation-check?{q}", headers=H)
        assert chk.status_code == 200
        body = chk.json()
        assert body["zero_sum"]["balanced"] is True
        assert body["pairs"][0]["source"] == "Spring B"
        assert body["pairs"][0]["target"] == "The Forum"
        listed = await c.get("/api/v1/books/allocations/rules", headers=H)
        assert listed.json()["rules"][0]["pool_name"] == "Shared Services"

        # a duplicate pattern for the same scope is refused, not silently doubled
        dup = await c.post("/api/v1/books/allocations/rules", headers=H, json={
            "pattern": "shared service expenses:", "pool_name": "Shared Services",
            "source_business_id": str(biz["springb"]),
            "target_business_id": str(biz["the_forum"])})
        assert dup.status_code == 400


async def test_the_check_names_which_intercompany_account_carries_the_difference():
    """An entity usually has more than one intercompany account. The Forum's `Due To SB
    Coaching` reconciles to the cent against its shared-service charge while its `Forum
    Transfer Account` moves separately — summing them nets a real answer into a meaningless
    one, so the breakdown is what makes the check usable."""
    tenant_id, biz = await _ids()
    await _clear(tenant_id)
    rows = FORUM + [("8", "Forum Transfer Account", "Other Current Asset", "1300",
                     Decimal("36200.00"))]
    await _books(tenant_id, biz["the_forum"], rows)
    await _rule(tenant_id, biz)
    async with SessionLocal() as s:
        await AL.sync_allocations(s, tenant_id, PERIOD)
        check = await AL.allocation_check(s, tenant_id, PERIOD)
    forum = next(e for e in check["entities"] if e["business"] == "the_forum")
    accounts = {a["account"]: a["movement"] for a in forum["intercompany_accounts"]}
    assert accounts["Due To SB Coaching"] == -32450.07, "reconciles against the allocation"
    assert accounts["Forum Transfer Account"] == 36200.00, "and this one does not"
    # The aggregate on its own would have read as a 3,749.93 discrepancy and explained nothing.
    assert forum["unexplained"] == 36200.00
