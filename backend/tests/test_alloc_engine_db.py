"""Pools and derivation against the database (ALLOCATION-RULES-SPEC 5.4, 5.5, Phase 4).

The arithmetic is in `test_alloc_engine.py` and is pure. This is about which money goes into a
pool, which is where the wrong answer still ties out: a pool that quietly excludes an unmapped
account, or counts a vendor's cost twice, splits to the cent either way and the Statement's
tie-out compares mapped against booked rather than against the pool.

So the fixtures are built to tell those apart. The vendor's account sits INSIDE the account
rule's subtree with a non-zero amount, because a fixture where it does not would let "counted
once" pass with the D6 carve-out deleted entirely.
"""
import datetime as dt
from decimal import Decimal as D

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (AccountPeriodBalance, AllocationContribution, AllocationRule, BookTxn,
                        Business, CoaMap, StandardAccount, Tenant, User)
from app.seed import seed
from app.services import alloc_engine as E
from app.services import alloc_rules as AR
from app.services import coa_alloc as CA
from app.services import coa_balances as CB
from app.services.coa import seed_standard_chart

MONTH = (dt.date(2026, 7, 1), dt.date(2026, 7, 31))
TODAY = dt.date(2026, 9, 30)
JULY = dt.date(2026, 7, 14)

# The chart this entity posts to. "Advertising" is the account rule's subtree, and the vendor
# Qualifi posts INSIDE it, which is B4.
CHART = [
    ("6010", "Advertising:Meta", "Expense", "6040"),
    ("6011", "Advertising:Qualifi", "Expense", "6040"),
    ("6020", "Software", "Expense", "6050"),
    ("1000", "Advertising Prepaid", "Other Current Asset", "1000"),   # a prefix trap
]


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        await seed_standard_chart(s, t.id)
        have = {b.key for b in (await s.execute(
            select(Business).where(Business.tenant_id == t.id))).scalars()}
        for key, name, order in (("the_forum", "The Forum", 40),
                                 ("becollective", "beCollective", 50)):
            if key not in have:
                s.add(Business(tenant_id=t.id, key=key, name=name, tag="Mastermind",
                               archetype="program", sort_order=order))
        await s.commit()


async def _ctx():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        biz = {b.key: b.id for b in (await s.execute(
            select(Business).where(Business.tenant_id == t.id))).scalars()}
        actor = (await s.execute(select(User).where(User.tenant_id == t.id))).scalars().first()
        std = {m.code: m.id for m in (await s.execute(select(StandardAccount).where(
            StandardAccount.tenant_id == t.id))).scalars()}
        return t.id, biz, actor, std


@pytest.fixture(autouse=True)
async def _clean():
    tenant_id, biz, _a, _s = await _ctx()
    async with SessionLocal() as s:
        await s.execute(delete(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))
        await s.execute(delete(AllocationRule).where(AllocationRule.tenant_id == tenant_id))
        await s.execute(delete(BookTxn).where(BookTxn.tenant_id == tenant_id))
        await s.execute(delete(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == biz["springb"]))
        await s.execute(delete(CoaMap).where(CoaMap.business_id == biz["springb"]))
        await s.commit()
    async with SessionLocal() as s:
        tid, bz, _a, std = await _ctx()
        for qid, fqn, qtype, code in CHART:
            s.add(CoaMap(tenant_id=tid, business_id=bz["springb"], qbo_account_id=qid,
                         qbo_account_name=fqn.split(":")[-1], qbo_account_fqn=fqn,
                         qbo_account_type=qtype, standard_account_id=std[code],
                         mapped_via="manual", is_ignored=False))
        await s.commit()
    yield


async def _txn(s, tenant_id, business_id, *, payee, amount, qid="6011",
               qbo_type="Purchase", multi=False, date=JULY, qbo_id=None):
    t = BookTxn(tenant_id=tenant_id, business_id=business_id, realm_id="r1",
                qbo_type=qbo_type, qbo_id=(qbo_id or f"{payee}-{amount}-{qid}"),
                txn_date=date, amount=D(amount), payee=payee, account_qbo_id=qid,
                flags=({"multi_line": True} if multi else None))
    s.add(t)
    await s.flush()
    return t.id


async def _apb(s, tenant_id, business_id, qid, amount, month=MONTH):
    s.add(AccountPeriodBalance(
        tenant_id=tenant_id, business_id=business_id, period_start=month[0],
        period_end=month[1], qbo_account_id=qid, amount=D(amount), source="qbo_tb"))


# ── vendor pools ──────────────────────────────────────────────────────────────────────────

async def _vendor_rule(tenant_id, biz, actor, pattern="Qualifi", target="the_forum"):
    async with SessionLocal() as s:
        rule, _v = await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="vendor", pattern=pattern, pool_name=pattern,
            source_business_id=biz["springb"], method="direct", effective_from=MONTH[0],
            reach="forward", rationale="Billed centrally on one card.",
            targets=[{"business_id": str(biz[target]), "share": "1"}])
        return rule


async def test_a_vendor_pool_sums_what_its_payee_spent_by_standard_account():
    tenant_id, biz, actor, std = await _ctx()
    rule = await _vendor_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi, LLC", amount="1200.00")
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi Inc", amount="2494.00")
        await _txn(s, tenant_id, biz["springb"], payee="Somebody Else", amount="999.00")
        await s.commit()
    async with SessionLocal() as s:
        got = await E.vendor_pools(s, tenant_id, biz["springb"], MONTH,
                                   today=TODAY, rules=[rule])
    # Both spellings are one vendor to `normalize_name`, which is what the rule matched on.
    assert got["pools"][rule.id] == {std["6040"]: D("3694.00")}
    assert got["pending"] == []


async def test_a_multi_line_transaction_is_never_claimed_and_is_reported():
    """D7 and F9. It carries one account id and its header amount, so claiming it by payee
    would charge line one's account for the whole transaction."""
    tenant_id, biz, actor, std = await _ctx()
    rule = await _vendor_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="500.00")
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="4000.00", multi=True)
        await s.commit()
    async with SessionLocal() as s:
        got = await E.vendor_pools(s, tenant_id, biz["springb"], MONTH,
                                   today=TODAY, rules=[rule])
    assert got["pools"][rule.id] == {std["6040"]: D("500.00")}, "the 4,000 stayed out"
    assert [p["reason"] for p in got["pending"]] == ["multi_line"]
    assert got["pending"][0]["amount"] == D("4000.00")


async def test_only_purchases_and_bills_feed_a_vendor_pool():
    """A BillPayment debits accounts payable, so counting it beside the Bill it settles would
    charge the pool twice for one cost."""
    tenant_id, biz, actor, std = await _ctx()
    rule = await _vendor_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="100.00",
                   qbo_type="Purchase")
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="200.00",
                   qbo_type="Bill")
        for bad in ("BillPayment", "JournalEntry", "Transfer", "Deposit"):
            await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="5000.00",
                       qbo_type=bad)
        await s.commit()
    async with SessionLocal() as s:
        got = await E.vendor_pools(s, tenant_id, biz["springb"], MONTH,
                                   today=TODAY, rules=[rule])
    assert got["pools"][rule.id] == {std["6040"]: D("300.00")}


async def test_an_unmapped_account_is_reported_rather_than_quietly_shrinking_the_pool():
    """The outer join. The observed path joins inner and filters on a mapped account, which is
    right for describing what the books did; here it would make the pool smaller and the split
    would still come to the cent."""
    tenant_id, biz, actor, std = await _ctx()
    rule = await _vendor_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="700.00")
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="300.00",
                   qid="9999")                   # no CoaMap row at all
        await s.commit()
    async with SessionLocal() as s:
        got = await E.vendor_pools(s, tenant_id, biz["springb"], MONTH,
                                   today=TODAY, rules=[rule])
    assert got["pools"][rule.id] == {std["6040"]: D("700.00")}
    assert [p["reason"] for p in got["pending"]] == ["unmapped_account"]
    assert got["pending"][0]["amount"] == D("300.00")


async def test_an_ignored_account_and_another_entity_and_another_month_stay_out():
    tenant_id, biz, actor, std = await _ctx()
    rule = await _vendor_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="400.00")
        await _txn(s, tenant_id, biz["the_forum"], payee="Qualifi", amount="8000.00",
                   qbo_id="other-entity")
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="9000.00",
                   date=dt.date(2026, 6, 30), qbo_id="last-month")
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="7000.00",
                   qid="6020", qbo_id="ignored-acct")
        m = (await s.execute(select(CoaMap).where(
            CoaMap.business_id == biz["springb"], CoaMap.qbo_account_id == "6020"))).scalar_one()
        m.is_ignored = True
        await s.commit()
    async with SessionLocal() as s:
        got = await E.vendor_pools(s, tenant_id, biz["springb"], MONTH,
                                   today=TODAY, rules=[rule])
    assert got["pools"][rule.id] == {std["6040"]: D("400.00")}


# ── account pools, and the D6 carve-out ───────────────────────────────────────────────────

async def test_an_account_pool_takes_the_months_balances_under_its_prefix():
    tenant_id, biz, _a, std = await _ctx()
    async with SessionLocal() as s:
        await _apb(s, tenant_id, biz["springb"], "6010", "5000.00")
        await _apb(s, tenant_id, biz["springb"], "6011", "3694.00")
        await _apb(s, tenant_id, biz["springb"], "6020", "1111.00")   # outside the prefix
        await s.commit()
    async with SessionLocal() as s:
        pool = await E.account_pool(s, tenant_id, biz["springb"], MONTH, "Advertising")
    assert pool == {std["6040"]: D("8694.00")}, "both advertising accounts, not Software"


async def test_a_balance_sheet_account_under_the_same_prefix_stays_out():
    """"Advertising Prepaid" is an Other Current Asset. A pool that swept it in would still
    split to the cent and the Statement would still tie out."""
    tenant_id, biz, _a, std = await _ctx()
    async with SessionLocal() as s:
        await _apb(s, tenant_id, biz["springb"], "6010", "5000.00")
        await _apb(s, tenant_id, biz["springb"], "1000", "40000.00")
        await s.commit()
    async with SessionLocal() as s:
        pool = await E.account_pool(s, tenant_id, biz["springb"], MONTH, "Advertising")
    assert pool == {std["6040"]: D("5000.00")}
    assert std["1000"] not in pool


async def test_a_vendor_claim_inside_an_account_subtree_is_counted_once():
    """B4 and D6. Qualifi's 3,694 posts to an account under the Advertising prefix, so the
    account pool must come down by exactly that or the same cost is allocated twice.

    The claim is non-zero and the account IS inside the subtree on purpose: a fixture where
    either were untrue would let this pass with the carve-out deleted.
    """
    tenant_id, biz, actor, std = await _ctx()
    rule = await _vendor_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="3694.00")
        await _apb(s, tenant_id, biz["springb"], "6010", "5000.00")
        await _apb(s, tenant_id, biz["springb"], "6011", "3694.00")
        await s.commit()
    async with SessionLocal() as s:
        vend = await E.vendor_pools(s, tenant_id, biz["springb"], MONTH,
                                    today=TODAY, rules=[rule])
        gross = await E.account_pool(s, tenant_id, biz["springb"], MONTH, "Advertising")
        net = await E.account_pool(s, tenant_id, biz["springb"], MONTH, "Advertising",
                                   vendor_claims=vend["pools"][rule.id])
    assert gross == {std["6040"]: D("8694.00")}
    assert net == {std["6040"]: D("5000.00")}, "the vendor's 3,694 is carved out"
    assert gross[std["6040"]] - net[std["6040"]] == vend["pools"][rule.id][std["6040"]]


def test_the_longest_prefix_wins_among_account_rules():
    class R:
        def __init__(self, p):
            self.pattern = p
    general, specific = R("Advertising"), R("Advertising:Meta")
    assert E.longest_prefix_wins([general, specific], "Advertising:Meta").pattern == \
        "Advertising:Meta"
    assert E.longest_prefix_wins([general, specific], "Advertising:Qualifi").pattern == \
        "Advertising"
    assert E.longest_prefix_wins([general, specific], "Software") is None


# ── derivation ────────────────────────────────────────────────────────────────────────────

async def _split_rule(tenant_id, biz, actor, shares=(("the_forum", "0.7"),
                                                     ("becollective", "0.3"))):
    async with SessionLocal() as s:
        rule, v = await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Advertising",
            pool_name="Advertising", source_business_id=biz["springb"], method="split",
            effective_from=MONTH[0], reach="forward",
            rationale="Run centrally and billed to one card.",
            targets=[{"business_id": str(biz[k]), "share": v} for k, v in shares])
        return rule, v


async def test_one_row_per_target_and_the_month_adds_back_to_the_pool():
    tenant_id, biz, actor, std = await _ctx()
    rule, v = await _split_rule(tenant_id, biz, actor)
    pool = {std["6040"]: D("1000.05")}
    async with SessionLocal() as s:
        out = await E.derive_applied(s, tenant_id, MONTH, rules_and_versions=[(rule, v)],
                                     pools_by_rule={rule.id: pool}, today=TODAY)
        await s.commit()
    assert out["contributions"] == 2 and out["pending"] == []
    async with SessionLocal() as s:
        rows = (await s.execute(select(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))).scalars().all()
    assert {r.grain for r in rows} == {"month"}
    assert {r.booking for r in rows} == {"applied"}
    assert {r.source_business_id for r in rows} == {biz["springb"]}
    assert sum(r.amount for r in rows) == D("1000.05"), "the split adds back to the pool"
    assert {r.rule_version_id for r in rows} == {v.id}
    assert all(r.basis == "Tier 3 Split, 70/30" for r in rows)


async def test_the_pool_entitys_own_share_is_retained_and_never_written():
    """`ck_alloc_not_self` forbids a row from an entity to itself, and 5.4 defines retained as
    derived. A version may list the pool entity among its targets, and that share is exactly
    what stays behind."""
    tenant_id, biz, actor, std = await _ctx()
    rule, v = await _split_rule(tenant_id, biz, actor,
                                shares=(("the_forum", "0.5"), ("becollective", "0.3"),
                                        ("springb", "0.2")))
    async with SessionLocal() as s:
        out = await E.derive_applied(s, tenant_id, MONTH, rules_and_versions=[(rule, v)],
                                     pools_by_rule={rule.id: {std["6040"]: D("1000.00")}},
                                     today=TODAY)
        await s.commit()
    assert out["contributions"] == 2, "two rows, not three"
    assert out["amount"] == D("800.00")
    async with SessionLocal() as s:
        rows = (await s.execute(select(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))).scalars().all()
    assert biz["springb"] not in {r.target_business_id for r in rows}
    assert E.retained(D("1000.00"), {r.target_business_id: r.amount for r in rows}) == D("200.00")


async def test_a_second_derivation_replaces_the_month_rather_than_adding_to_it():
    tenant_id, biz, actor, std = await _ctx()
    rule, v = await _split_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        await E.derive_applied(s, tenant_id, MONTH, rules_and_versions=[(rule, v)],
                               pools_by_rule={rule.id: {std["6040"]: D("1000.00")}},
                               today=TODAY)
        await s.commit()
    async with SessionLocal() as s:
        await E.derive_applied(s, tenant_id, MONTH, rules_and_versions=[(rule, v)],
                               pools_by_rule={rule.id: {std["6040"]: D("2000.00")}},
                               today=TODAY)
        await s.commit()
    async with SessionLocal() as s:
        rows = (await s.execute(select(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))).scalars().all()
    assert len(rows) == 2
    assert sum(r.amount for r in rows) == D("2000.00")


async def test_an_observed_row_in_the_way_is_reported_as_a_double_count_risk():
    """F7. The carrier already books this as a shared-service charge, so an applied split from
    the pool entity would charge it twice in `as_allocated`, and each half is individually
    correct."""
    tenant_id, biz, actor, std = await _ctx()
    rule, v = await _split_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        s.add(AllocationContribution(
            tenant_id=tenant_id, period_start=MONTH[0], period_end=MONTH[1],
            source_business_id=biz["springb"], target_business_id=biz["the_forum"],
            standard_account_id=std["6040"], amount=D("4000.00"),
            booking="observed", pool_name="Shared Services"))
        await s.commit()
    async with SessionLocal() as s:
        out = await E.derive_applied(s, tenant_id, MONTH, rules_and_versions=[(rule, v)],
                                     pools_by_rule={rule.id: {std["6040"]: D("1000.00")}},
                                     today=TODAY)
        await s.commit()
    assert [p["reason"] for p in out["pending"]] == ["double_count_risk"]
    assert out["contributions"] == 1, "beCollective's share still resolves"
    async with SessionLocal() as s:
        applied = (await s.execute(select(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id,
            AllocationContribution.booking == "applied"))).scalars().all()
    assert {r.target_business_id for r in applied} == {biz["becollective"]}


async def test_an_ended_version_claims_nothing_and_the_observed_rows_are_untouched():
    tenant_id, biz, actor, std = await _ctx()
    rule, _v = await _split_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        ended = await AR.end_rule(s, tenant_id, actor, rule.id,
                                  effective_from=MONTH[0], rationale="Cards moved.")
        s.add(AllocationContribution(
            tenant_id=tenant_id, period_start=MONTH[0], period_end=MONTH[1],
            source_business_id=biz["springb"], target_business_id=biz["sympli"],
            standard_account_id=std["6050"], amount=D("777.00"),
            booking="observed", pool_name="Shared Services"))
        await s.commit()
    async with SessionLocal() as s:
        out = await E.derive_applied(s, tenant_id, MONTH, rules_and_versions=[(rule, ended)],
                                     pools_by_rule={rule.id: {std["6040"]: D("1000.00")}},
                                     today=TODAY)
        await s.commit()
    assert out["contributions"] == 0
    async with SessionLocal() as s:
        rows = (await s.execute(select(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))).scalars().all()
    assert len(rows) == 1 and rows[0].booking == "observed", "it deleted nothing of the books'"


# ── the gate: what applied rows must NOT disturb ──────────────────────────────────────────

async def test_the_statement_still_ties_with_applied_month_rows_present():
    """D10. The tie-out compares the mapped total against the booked total, never against the
    pool, so an applied row cannot break it. Verified rather than asserted on the page."""
    tenant_id, biz, actor, std = await _ctx()
    rule, v = await _split_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        await _apb(s, tenant_id, biz["springb"], "6010", "5000.00")
        await _apb(s, tenant_id, biz["springb"], "6020", "1000.00")
        await s.commit()
    async with SessionLocal() as s:
        before = await CB.build_mapped_statement(s, tenant_id, biz["springb"], MONTH, "pl")
    assert before["tie_out"]["status"] == "tied"

    async with SessionLocal() as s:
        out = await E.derive_applied(s, tenant_id, MONTH, rules_and_versions=[(rule, v)],
                                     pools_by_rule={rule.id: {std["6040"]: D("5000.00")}},
                                     today=TODAY)
        await s.commit()
    assert out["contributions"] == 2
    async with SessionLocal() as s:
        after = await CB.build_mapped_statement(s, tenant_id, biz["springb"], MONTH, "pl")
    assert after["tie_out"]["status"] == "tied", "an applied row cannot break the tie-out"
    assert after["tie_out"]["delta"] == before["tie_out"]["delta"]
    # The ALLOCATED figures do move, and by exactly what went out: the pool entity sent 5,000
    # to two other entities, so its own expense falls by 5,000. That is the feature, and
    # asserting the totals were unchanged would have been asserting it does not work.
    assert float(out["amount"]) == 5000.00
    assert before["totals"]["operating_expenses"] - after["totals"]["operating_expenses"] == \
        float(out["amount"])
    assert after["totals"]["net_income"] - before["totals"]["net_income"] == float(out["amount"])


async def test_allocation_check_is_unmoved_by_applied_rows():
    """5.5 leaves `allocation_check` reading observed rows only. It reconciles against
    intercompany MOVEMENT, which an applied allocation has none of."""
    tenant_id, biz, actor, std = await _ctx()
    rule, v = await _split_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        before = await CA.allocation_check(s, tenant_id, MONTH)
    async with SessionLocal() as s:
        await E.derive_applied(s, tenant_id, MONTH, rules_and_versions=[(rule, v)],
                               pools_by_rule={rule.id: {std["6040"]: D("9000.00")}},
                               today=TODAY)
        await s.commit()
    async with SessionLocal() as s:
        after = await CA.allocation_check(s, tenant_id, MONTH)
    assert after["zero_sum"]["balanced"] is True
    assert after == before, "an applied row changed a check that only reads observed ones"


async def test_a_year_to_date_window_groups_the_months_by_version():
    """5.5. Nine monthly rows per entity become two: the split before April and the split
    after. Grouped on the VERSION, not the rule, so a restatement does not merge the old terms
    into the new ones."""
    tenant_id, biz, actor, std = await _ctx()
    # v1 is effective from JANUARY here, not July: the months below are derived with it, and a
    # version that does not govern the month it is written against is a fixture that disagrees
    # with `effective_version` rather than a test of anything.
    async with SessionLocal() as s:
        rule, v1 = await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Advertising",
            pool_name="Advertising", source_business_id=biz["springb"], method="split",
            effective_from=dt.date(2026, 1, 1), reach="forward",
            rationale="Run centrally and billed to one card.",
            targets=[{"business_id": str(biz["the_forum"]), "share": "0.7"},
                     {"business_id": str(biz["becollective"]), "share": "0.3"}])
    async with SessionLocal() as s:
        v2 = await AR.supersede(
            s, tenant_id, actor, rule.id, method="split",
            effective_from=dt.date(2026, 4, 1), reach="from_date",
            rationale="Spring B started taking a share in April.",
            targets=[{"business_id": str(biz["the_forum"]), "share": "0.5"},
                     {"business_id": str(biz["becollective"]), "share": "0.3"},
                     {"business_id": str(biz["springb"]), "share": "0.2"}])

    months = [(dt.date(2026, m, 1), CB._month_end(2026, m)) for m in range(1, 10)]
    async with SessionLocal() as s:
        for i, m in enumerate(months):
            ver = v1 if m[0] < dt.date(2026, 4, 1) else v2
            await E.derive_applied(s, tenant_id, m, rules_and_versions=[(rule, ver)],
                                   pools_by_rule={rule.id: {std["6040"]: D(f"{1000 + i}.00")}},
                                   today=TODAY)
        await s.commit()

    ytd = (dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    async with SessionLocal() as s:
        rows = (await CA.contributions_for(s, tenant_id, biz["the_forum"], ytd))["in"][std["6040"]]
        who = {b.id: b.name for b in (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id))).scalars()}
        from app.models import AllocationRuleVersion
        versions = {x.id: x for x in (await s.execute(select(AllocationRuleVersion).where(
            AllocationRuleVersion.tenant_id == tenant_id))).scalars()}
    assert len(rows) == 9, "nine monthly rows reach the year"

    composed = CB._compose(rows, "in", who, {}, versions)
    assert len(composed) == 2, "and become two, one per version"
    v1_row = next(r for r in composed if r["rule"]["version"] == 1)
    v2_row = next(r for r in composed if r["rule"]["version"] == 2)
    assert v1_row["rule"]["months"] == ["2026-01-01", "2026-02-01", "2026-03-01"]
    assert len(v2_row["rule"]["months"]) == 6
    assert v1_row["rule"]["effective_from"] == "2026-01-01"
    assert v2_row["rule"]["effective_from"] == "2026-04-01"
    assert "April" in v2_row["rule"]["rationale"]
    # The grouped amounts still add up to what the nine rows held.
    assert round(v1_row["amount"] + v2_row["amount"], 2) == \
        round(float(sum(r.amount for r in rows)), 2)


async def test_a_window_rows_composition_is_byte_identical_to_before():
    """Phase 2 promised an existing window's payload would not move, and `_compose` is where it
    could. A pass-through row must come out exactly as `_contribution` makes it, in the query's
    order, with no `rule` block."""
    tenant_id, biz, _a, std = await _ctx()
    async with SessionLocal() as s:
        for i, (src, amt) in enumerate((("the_forum", "100.00"), ("becollective", "200.00"))):
            s.add(AllocationContribution(
                tenant_id=tenant_id, period_start=MONTH[0], period_end=MONTH[1],
                source_business_id=biz[src], target_business_id=biz["springb"],
                standard_account_id=std["6040"], amount=D(amt),
                booking="observed", pool_name=f"Shared {i}"))
        await s.commit()
    async with SessionLocal() as s:
        rows = (await CA.contributions_for(s, tenant_id, biz["springb"], MONTH))["in"][std["6040"]]
        who = {b.id: b.name for b in (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id))).scalars()}
    assert CB._compose(rows, "in", who, {}, {}) == \
        [CB._contribution(r, "in", who, {}) for r in rows]
    assert all("rule" not in r for r in CB._compose(rows, "in", who, {}, {}))


async def test_two_overlapping_account_rules_do_not_both_claim_one_account():
    """Longest prefix wins, THROUGH `account_pools` and not just in the helper. "Advertising"
    and "Advertising:Meta" together mean Meta's spend is the second rule's and the rest the
    first's. Both claiming it would allocate one cost twice while each split still came to the
    cent."""
    tenant_id, biz, actor, std = await _ctx()
    async with SessionLocal() as s:
        general, _v = await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Advertising",
            pool_name="Advertising", source_business_id=biz["springb"], method="direct",
            effective_from=MONTH[0], reach="forward", rationale="The rest of advertising.",
            targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    async with SessionLocal() as s:
        specific, _v2 = await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Advertising:Meta",
            pool_name="Meta", source_business_id=biz["springb"], method="direct",
            effective_from=MONTH[0], reach="forward", rationale="Meta is beCollective's.",
            targets=[{"business_id": str(biz["becollective"]), "share": "1"}])
    async with SessionLocal() as s:
        # 6010 is Advertising:Meta, 6011 is Advertising:Qualifi. Both map to 6040, so the two
        # rules' pools are told apart only by which account each one won.
        await _apb(s, tenant_id, biz["springb"], "6010", "3000.00")
        await _apb(s, tenant_id, biz["springb"], "6011", "2000.00")
        await s.commit()
    async with SessionLocal() as s:
        pools = await E.account_pools(s, tenant_id, biz["springb"], MONTH, [general, specific])
    assert pools[specific.id] == {std["6040"]: D("3000.00")}, "Meta's own account only"
    assert pools[general.id] == {std["6040"]: D("2000.00")}, "and the general rule gets the rest"
    total = sum(v for by in pools.values() for v in by.values())
    assert total == D("5000.00"), "the two pools add to the month, with nothing counted twice"


# ── the production path's own controls ────────────────────────────────────────────────────
#
# `account_pool` is the single-rule form, used for preview. `account_pools` is what `derive_all`
# calls, and the two carry the SAME two controls: P&L only, and the D6 carve-out. Mutating the
# plural's copies of both survived every test above, because every test exercised the singular.
# Same mistake twice, in the function that actually runs.

async def test_the_production_path_also_leaves_balance_sheet_accounts_out():
    tenant_id, biz, actor, std = await _ctx()
    async with SessionLocal() as s:
        rule, _v = await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Advertising",
            pool_name="Advertising", source_business_id=biz["springb"], method="direct",
            effective_from=MONTH[0], reach="forward", rationale="Billed centrally.",
            targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    async with SessionLocal() as s:
        await _apb(s, tenant_id, biz["springb"], "6010", "5000.00")
        await _apb(s, tenant_id, biz["springb"], "1000", "40000.00")   # Advertising Prepaid
        await s.commit()
    async with SessionLocal() as s:
        pools = await E.account_pools(s, tenant_id, biz["springb"], MONTH, [rule])
    assert pools[rule.id] == {std["6040"]: D("5000.00")}
    assert std["1000"] not in pools[rule.id], "a prepaid asset is not a shared cost"


async def test_the_production_path_also_carves_out_a_vendor_claim():
    tenant_id, biz, actor, std = await _ctx()
    vend = await _vendor_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        acct, _v = await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Advertising",
            pool_name="Advertising", source_business_id=biz["springb"], method="direct",
            effective_from=MONTH[0], reach="forward", rationale="The rest of it.",
            targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    async with SessionLocal() as s:
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="3694.00")
        await _apb(s, tenant_id, biz["springb"], "6010", "5000.00")
        await _apb(s, tenant_id, biz["springb"], "6011", "3694.00")
        await s.commit()
    async with SessionLocal() as s:
        vp = await E.vendor_pools(s, tenant_id, biz["springb"], MONTH,
                                  today=TODAY, rules=[vend])
        claims = {k: v for by in vp["pools"].values() for k, v in by.items()}
        gross = await E.account_pools(s, tenant_id, biz["springb"], MONTH, [acct])
        net = await E.account_pools(s, tenant_id, biz["springb"], MONTH, [acct],
                                    vendor_claims=claims)
    assert gross[acct.id] == {std["6040"]: D("8694.00")}
    assert net[acct.id] == {std["6040"]: D("5000.00")}, "the vendor's 3,694 is carved out"
    assert gross[acct.id][std["6040"]] - net[acct.id][std["6040"]] == D("3694.00")


async def test_another_entitys_spend_stays_out_even_when_it_shares_the_chart():
    """The earlier version of this could not see the entity filter at all: the other entity had
    no `CoaMap` rows, so its transaction came back unmapped and never reached the pool whether
    the filter was there or not. It needs the SAME account mapped on both entities."""
    tenant_id, biz, actor, std = await _ctx()
    rule = await _vendor_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        s.add(CoaMap(tenant_id=tenant_id, business_id=biz["the_forum"],
                     qbo_account_id="6011", qbo_account_name="Qualifi",
                     qbo_account_fqn="Advertising:Qualifi", qbo_account_type="Expense",
                     standard_account_id=std["6040"], mapped_via="manual", is_ignored=False))
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="400.00")
        await _txn(s, tenant_id, biz["the_forum"], payee="Qualifi", amount="8000.00",
                   qbo_id="forum-own-spend")
        await s.commit()
    async with SessionLocal() as s:
        got = await E.vendor_pools(s, tenant_id, biz["springb"], MONTH,
                                   today=TODAY, rules=[rule])
    assert got["pools"][rule.id] == {std["6040"]: D("400.00")}, "the forum's 8,000 is its own"
    assert got["pending"] == [], "and it was not reported as unmapped either"


async def test_a_refund_with_a_residue_places_it_by_magnitude():
    """The earlier refund case summed to exactly its pool, so it had NO residue and could not
    tell "largest by magnitude" from "largest by value". -1000.05 does: both parts round away
    from zero, a cent too far, so the residue is positive and must land on the bigger debt."""
    parts = E.split_amounts(D("-1000.05"), {"forum": D("0.7"), "becollective": D("0.3")})
    assert sum(parts.values()) == D("-1000.05")
    # By magnitude the forum's -700.04 is largest and comes back a cent to -700.03. By value
    # beCollective's -300.02 would be "largest" and would become -300.01 instead.
    assert parts == {"forum": D("-700.03"), "becollective": D("-300.02")}


async def test_an_ended_version_claims_nothing_even_if_it_somehow_carries_targets():
    """`validate_version` refuses targets on an ended version, so the guard in `derive_applied`
    is unreachable through the service and mutating it away survived every test. Reached here
    directly, which is the only way to make the guard load-bearing: a stored version from a
    future method, or a hand-written row, must not resume claiming."""
    tenant_id, biz, actor, std = await _ctx()
    rule, v = await _split_rule(tenant_id, biz, actor)

    class _Ended:
        id, version, tier = v.id, 99, None
        method = "ended"
        targets = [{"business_id": str(biz["the_forum"]), "share": "1"}]
        approved_by = None

    async with SessionLocal() as s:
        out = await E.derive_applied(s, tenant_id, MONTH, rules_and_versions=[(rule, _Ended())],
                                     pools_by_rule={rule.id: {std["6040"]: D("1000.00")}},
                                     today=TODAY)
        await s.commit()
    assert out["contributions"] == 0, "an ended rule claims nothing, whatever it carries"
    async with SessionLocal() as s:
        assert (await s.execute(select(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))).scalars().all() == []


async def test_the_vendor_carve_out_survives_the_ORCHESTRATION_not_only_the_helper():
    """D6 end to end, through `pools_for_month`.

    `test_a_vendor_claim_inside_an_account_subtree_is_counted_once` above passes
    `vendor_claims` to `account_pool` BY HAND, so it proves the subtraction works and says
    nothing about whether anything ever performs it. Deleting `vendor_claims=claims` from
    `pools_for_month` left all 52 allocation tests green: the logic was defended and the wiring
    was not, which is the same shape as a test that asserts the right 404 without reaching the
    guard it is aimed at.

    `pools_for_month` is now the ONLY place pools are computed, so this one test covers the
    derivation and the reconciliation report together.
    """
    tenant_id, biz, actor, std = await _ctx()
    vendor = await _vendor_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        account, _v = await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Advertising",
            pool_name="Advertising", source_business_id=biz["springb"], method="split",
            effective_from=MONTH[0], reach="forward",
            rationale="Everything the vendor rules do not claim.",
            targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    async with SessionLocal() as s:
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="3694.00")
        await _apb(s, tenant_id, biz["springb"], "6010", "5000.00")
        await _apb(s, tenant_id, biz["springb"], "6011", "3694.00")
        await s.commit()

    async with SessionLocal() as s:
        vers = {r.id: await AR.versions_for(s, tenant_id, r.id) for r in (vendor, account)}
        live = [(r, next(v for v in vers[r.id] if v.is_current)) for r in (vendor, account)]
        pools, _pending = await E.pools_for_month(s, tenant_id, live, MONTH, today=TODAY)

    assert pools[vendor.id] == {std["6040"]: D("3694.00")}, "the vendor claims its payee"
    assert pools[account.id] == {std["6040"]: D("5000.00")}, \
        "and the account rule holds 8,694 LESS the 3,694 already claimed"
    assert sum(pools[vendor.id].values()) + sum(pools[account.id].values()) == D("8694.00"), \
        "the money in the books is claimed exactly once between them"
