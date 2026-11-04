"""Phase 4 of ALLOCATIONS-UI-SPEC: the exception queue, grouped by payee (6.2, 7.3, D8).

A rule is per payee. Rendered per charge, a vendor billing monthly filled the screen with its
own name and a reader answered the same question once per charge -- so the queue groups the
way a rule does.

The suggestion is the other half: Payables already knows some of these vendors' entities, and
a queue that did not say so was asking a question it could have half-answered. It SUGGESTS and
never creates, because deciding the thing it exists to ask about is the one move that would
make it untrustworthy.
"""
import datetime as dt
from decimal import Decimal as D

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (AccountPeriodBalance, AllocationContribution, AllocationRule, BookTxn,
                        Business, CoaMap, StandardAccount, Tenant, User, Vendor)
from app.seed import seed
from app.services import alloc_engine as E
from app.services import alloc_rules as AR
from app.services.binder_extract import normalize_name
from app.services.coa import seed_standard_chart
from app.services.coa_balances import get_settings

MONTH = (dt.date(2026, 7, 1), dt.date(2026, 7, 31))
TODAY = dt.date(2026, 9, 30)
CHART = [("6010", "Advertising:Meta", "Expense", "6040"),
         ("6020", "Education/Dues:Coaching/Consulting", "Expense", "6050")]


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        await seed_standard_chart(s, t.id)
        have = {b.key for b in (await s.execute(
            select(Business).where(Business.tenant_id == t.id))).scalars()}
        for key, name, order in (("the_forum", "The Forum", 40),):
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
    tenant_id, biz, _a, std = await _ctx()
    async with SessionLocal() as s:
        await s.execute(delete(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))
        await s.execute(delete(AllocationRule).where(AllocationRule.tenant_id == tenant_id))
        await s.execute(delete(BookTxn).where(BookTxn.tenant_id == tenant_id))
        await s.execute(delete(Vendor).where(Vendor.tenant_id == tenant_id))
        await s.execute(delete(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == biz["springb"]))
        await s.execute(delete(CoaMap).where(CoaMap.business_id == biz["springb"]))
        await s.commit()
    async with SessionLocal() as s:
        for qid, fqn, qtype, code in CHART:
            s.add(CoaMap(tenant_id=tenant_id, business_id=biz["springb"], qbo_account_id=qid,
                         qbo_account_name=fqn.split(":")[-1], qbo_account_fqn=fqn,
                         qbo_account_type=qtype, standard_account_id=std[code],
                         mapped_via="manual", is_ignored=False))
        cfg = await get_settings(s, tenant_id)
        cfg.alloc_pool_business_ids = [str(biz["springb"])]
        await s.commit()
    yield


async def _txn(tenant_id, business_id, *, payee, amount, qid="6020", day=14, memo="a memo"):
    async with SessionLocal() as s:
        s.add(BookTxn(tenant_id=tenant_id, business_id=business_id, realm_id="r1",
                      qbo_type="Purchase", qbo_id=f"{payee}-{amount}-{day}-{qid}",
                      txn_date=dt.date(2026, 7, day), amount=D(amount), payee=payee,
                      memo=memo, account_qbo_id=qid))
        await s.commit()


async def _queue(tenant_id):
    async with SessionLocal() as s:
        return await E.exceptions(s, tenant_id, MONTH, today=TODAY)


async def test_three_charges_from_one_payee_are_one_group():
    """The gate. A vendor billing three times is one question, not three."""
    tenant_id, biz, _a, _s = await _ctx()
    for day, amount in ((2, "5500.00"), (12, "5500.00"), (22, "5500.00")):
        await _txn(tenant_id, biz["springb"], payee="Breakthroughs LLC", amount=amount,
                   day=day)

    q = await _queue(tenant_id)
    assert len(q["rows"]) == 3, "the per-charge list is kept for anything still reading it"
    assert len(q["groups"]) == 1
    g = q["groups"][0]
    assert g["payee"] == "Breakthroughs LLC"
    assert g["count"] == 3
    assert g["total"] == D("16500.00")
    assert g["total"] == q["total"], "the group totals have to add to the queue's headline"
    assert g["latest_date"] == "2026-07-22", "the latest charge, for the card's date line"
    assert g["accounts"] == ["Coaching/Consulting"], \
        "the last FQN segment, deduplicated; the full path belongs on the charge row"
    assert len(g["txn_ids"]) == 3


async def test_the_same_payee_on_two_pool_entities_stays_two_groups():
    """A rule is per payee AND per pool entity, so one card for both would promise a rule
    that cannot exist. Mutating the group key to drop the entity has to fail here."""
    tenant_id, biz, _a, std = await _ctx()
    async with SessionLocal() as s:
        cfg = await get_settings(s, tenant_id)
        cfg.alloc_pool_business_ids = [str(biz["springb"]), str(biz["the_forum"])]
        for qid, fqn, qtype, code in CHART:
            s.add(CoaMap(tenant_id=tenant_id, business_id=biz["the_forum"],
                         qbo_account_id=qid, qbo_account_name=fqn.split(":")[-1],
                         qbo_account_fqn=fqn, qbo_account_type=qtype,
                         standard_account_id=std[code], mapped_via="manual",
                         is_ignored=False))
        await s.commit()
    await _txn(tenant_id, biz["springb"], payee="Shared Vendor", amount="100.00", day=3)
    await _txn(tenant_id, biz["the_forum"], payee="Shared Vendor", amount="200.00", day=4)

    q = await _queue(tenant_id)
    assert len(q["groups"]) == 2, "same payee, two entities, two rules, two cards"
    assert {g["total"] for g in q["groups"]} == {D("100.00"), D("200.00")}


async def test_a_payee_payables_already_knows_carries_a_suggestion():
    tenant_id, biz, _a, _s = await _ctx()
    async with SessionLocal() as s:
        s.add(Vendor(tenant_id=tenant_id, legal_name="Breakthroughs LLC",
                     name_norm=normalize_name("Breakthroughs LLC"),
                     display_name="Breakthroughs LLC",
                     default_business_id=biz["the_forum"]))
        await s.commit()
    await _txn(tenant_id, biz["springb"], payee="Breakthroughs LLC", amount="5500.00")

    g = (await _queue(tenant_id))["groups"][0]
    assert g["suggestion"] == {"tier": 1, "business_id": str(biz["the_forum"]),
                               "source": "payables"}


async def test_a_suggestion_never_becomes_a_rule_on_its_own():
    """Suggest, never create. A queue that wrote rules on a guess would be deciding the
    thing it exists to ask about."""
    tenant_id, biz, _a, _s = await _ctx()
    async with SessionLocal() as s:
        s.add(Vendor(tenant_id=tenant_id, legal_name="Breakthroughs LLC",
                     name_norm=normalize_name("Breakthroughs LLC"),
                     display_name="Breakthroughs LLC",
                     default_business_id=biz["the_forum"]))
        await s.commit()
    await _txn(tenant_id, biz["springb"], payee="Breakthroughs LLC", amount="5500.00")
    await _queue(tenant_id)

    async with SessionLocal() as s:
        rules = (await s.execute(select(AllocationRule).where(
            AllocationRule.tenant_id == tenant_id))).scalars().all()
    assert rules == [], "reading the queue must never write a rule"


async def test_a_vendor_with_no_default_entity_suggests_nothing():
    tenant_id, biz, _a, _s = await _ctx()
    async with SessionLocal() as s:
        s.add(Vendor(tenant_id=tenant_id, legal_name="Unknown Co",
                     name_norm=normalize_name("Unknown Co"), display_name="Unknown Co",
                     default_business_id=None))
        await s.commit()
    await _txn(tenant_id, biz["springb"], payee="Unknown Co", amount="400.00")

    assert (await _queue(tenant_id))["groups"][0]["suggestion"] is None


async def test_charges_with_no_payee_group_by_account_because_no_vendor_rule_can_claim_them():
    tenant_id, biz, _a, _s = await _ctx()
    await _txn(tenant_id, biz["springb"], payee=None, amount="120.00", qid="6010", day=5)
    await _txn(tenant_id, biz["springb"], payee=None, amount="80.00", qid="6010", day=9)
    await _txn(tenant_id, biz["springb"], payee=None, amount="60.00", qid="6020", day=11)

    groups = (await _queue(tenant_id))["groups"]
    assert len(groups) == 2, "two accounts, two groups"
    assert all(g["payee"] is None and g["has_payee"] is False for g in groups)
    assert {g["total"] for g in groups} == {D("200.00"), D("60.00")}


async def test_a_partly_covered_charge_is_in_no_group_at_all():
    """It is not waiting on a rule, it is waiting on somebody to look. Counting it here
    would put it back in the figure U4 took it out of."""
    tenant_id, biz, actor, _s = await _ctx()
    async with SessionLocal() as s:
        await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Education/Dues",
            pool_name="Coaching", source_business_id=biz["springb"], method="direct",
            effective_from=MONTH[0], reach="forward", rationale="The Forum's coaching.",
            targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    async with SessionLocal() as s:
        s.add(BookTxn(tenant_id=tenant_id, business_id=biz["springb"], realm_id="r1",
                      qbo_type="Purchase", qbo_id="multi-1", txn_date=dt.date(2026, 7, 14),
                      amount=D("8031.59"), payee="Payroll Pay", memo=None,
                      account_qbo_id="6020", flags={"multi_line": True}))
        await s.commit()

    q = await _queue(tenant_id)
    assert [r["status"] for r in q["rows"]] == ["partly_covered"]
    assert q["groups"] == [], "listed, but not a question anybody has to answer with a rule"
    assert q["total"] == D("0.00")


async def test_groups_are_ordered_by_what_they_cost():
    """Principle 2: amount before name.

    THE NAMES FIGHT THE AMOUNTS ON PURPOSE. The first version used Big, Middle and Small at
    9000, 500 and 100 -- the same sequence alphabetically and by amount, so it passed against
    a sort by NAME and proved nothing. Mutating the sort is what found that.
    """
    tenant_id, biz, _a, _s = await _ctx()
    await _txn(tenant_id, biz["springb"], payee="Mid Co", amount="100.00", day=2)
    await _txn(tenant_id, biz["springb"], payee="Zeta Co", amount="9000.00", day=3)
    await _txn(tenant_id, biz["springb"], payee="Alpha Co", amount="500.00", day=4)

    got = [g["payee"] for g in (await _queue(tenant_id))["groups"]]
    assert got == ["Zeta Co", "Alpha Co", "Mid Co"], got
    assert got != sorted(got), "an order that is also alphabetical tests nothing"


async def test_the_gaps_on_a_group_are_the_union_of_its_charges():
    """The card carries the gaps, so a reader does not open three charges to find the one
    with no memo."""
    tenant_id, biz, _a, _s = await _ctx()
    await _txn(tenant_id, biz["springb"], payee="Mixed Co", amount="100.00", day=2,
               memo="has a memo")
    await _txn(tenant_id, biz["springb"], payee="Mixed Co", amount="200.00", day=3, memo=None)

    g = (await _queue(tenant_id))["groups"][0]
    assert "no_memo" in g["gaps"], "one charge has none, so the group says so"
    assert g["gaps"] == sorted(g["gaps"]), "a stable order, so the chips do not reshuffle"
