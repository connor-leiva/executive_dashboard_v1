"""The `derived` method: a pool that follows other pools (ALLOCATION-RULES-SPEC 10.1).

Connor, on being shown the four driven subjects: "I had assumed that if we set the payroll and
taxes to driven, that it would automatically calculate based on what our allocations were set
to for our other staffing amounts."

It does now. The thing these tests exist to defend is that it follows the DOLLAR-WEIGHTED
split and not the average of the percentages. Those two differ whenever the followed pools are
of unequal size, which is always, and the averaged answer looks entirely plausible: it is
wrong by a few points every month and nothing downstream would flag it.
"""
import datetime as dt
from decimal import Decimal as D

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (AccountPeriodBalance, AllocationContribution, AllocationRule,
                        Business, BookTxn, CoaMap, StandardAccount, Tenant, User)
from app.seed import seed
from app.services import alloc_engine as E
from app.services import alloc_rules as AR
from app.services.coa import seed_standard_chart
from app.services.coa_balances import get_settings

MONTH = (dt.date(2026, 7, 1), dt.date(2026, 7, 31))
TODAY = dt.date(2026, 9, 30)
# Three accounts, so three pools can hold different amounts of money in one month.
CHART = [("6010", "Payroll Expenses:Big Earner", "Expense", "6040"),
         ("6020", "Payroll Expenses:Small Earner", "Expense", "6050"),
         ("6030", "Payroll Expenses:Payroll Taxes", "Expense", "6060")]


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
    tenant_id, biz, _a, std = await _ctx()
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
        for qid, fqn, qtype, code in CHART:
            s.add(CoaMap(tenant_id=tenant_id, business_id=biz["springb"], qbo_account_id=qid,
                         qbo_account_name=fqn.split(":")[-1], qbo_account_fqn=fqn,
                         qbo_account_type=qtype, standard_account_id=std[code],
                         mapped_via="manual", is_ignored=False))
        cfg = await get_settings(s, tenant_id)
        cfg.alloc_pool_business_ids = [str(biz["springb"])]
        await s.commit()
    yield


async def _balance(tenant_id, business_id, qid, amount):
    async with SessionLocal() as s:
        s.add(AccountPeriodBalance(
            tenant_id=tenant_id, business_id=business_id, period_start=MONTH[0],
            period_end=MONTH[1], qbo_account_id=qid, amount=D(amount), source="qbo_tb"))
        await s.commit()


async def _split_rule(tenant_id, actor, biz, *, pattern, pool_name, targets):
    async with SessionLocal() as s:
        rule, _v = await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern=pattern, pool_name=pool_name,
            source_business_id=biz["springb"],
            method="split" if len(targets) > 1 else "direct",
            effective_from=MONTH[0], reach="forward",
            rationale="How this person's time actually went.", targets=targets)
        return rule.id


async def _derived_rule(tenant_id, actor, biz, follows, *, pattern="Payroll Expenses:Payroll",
                        pool_name="Payroll taxes and fees"):
    async with SessionLocal() as s:
        rule, _v = await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern=pattern, pool_name=pool_name,
            source_business_id=biz["springb"], method="derived",
            effective_from=MONTH[0], reach="forward",
            rationale="Payroll tax follows the wages it was charged on.",
            source_spec={"source": "follows_rules",
                         "rule_ids": [str(r) for r in follows]})
        return rule.id


async def _derive(tenant_id):
    async with SessionLocal() as s:
        out = await E.derive_all(s, tenant_id, today=TODAY, months=[MONTH])
        await s.commit()
    return out


async def _rows_for(tenant_id, rule_id):
    async with SessionLocal() as s:
        rows = list((await s.execute(select(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id,
            AllocationContribution.rule_id == rule_id,
            AllocationContribution.booking == "applied"))).scalars())
    return {str(r.target_business_id): r.amount for r in rows}, rows


# ── the arithmetic, pure ──────────────────────────────────────────────────────────────────

def test_it_weights_by_dollars_and_not_by_averaging_the_percentages():
    """THE TEST THIS METHOD EXISTS FOR.

    A big pool split 80/20 and a small one split 50/50. The average of the percentages is
    65/35. The dollar-weighted answer is 77.27/22.73. Both look like plausible splits, and
    only one of them is what the money did.
    """
    shares, reason = E.derived_shares(
        {"big": {"f": D("800"), "g": D("200")},
         "small": {"f": D("50"), "g": D("50")}},
        set(), {"rule_ids": ["big", "small"]})
    assert reason is None
    assert (shares["f"] * 100).quantize(D("0.01")) == D("77.27")
    assert (shares["g"] * 100).quantize(D("0.01")) == D("22.73")
    assert (shares["f"] * 100).quantize(D("0.01")) != D("65.00"), \
        "65 is the average of 80 and 50, which is the wrong answer this method replaces"


def test_the_shares_are_not_quantized_so_a_month_does_not_land_a_cent_short():
    """Three equal weights. Quantized to four places they are 0.3333 each, which sums to
    0.9999, and the month reconciles a cent short -- correctly short, every month, on every
    such rule, and nothing would flag it. The count branch of `driven_shares` documents the
    same trap.

    The shares themselves do NOT sum to exactly 1 at full precision either (Decimal thirds
    never do). What has to be exact is the MONEY, because `split_amounts` places the residue
    deliberately -- and it can only do that if it is handed the full precision to place.
    """
    shares, _ = E.derived_shares(
        {"a": {"x": D("100")}, "b": {"y": D("100")}, "c": {"z": D("100")}},
        set(), {"rule_ids": ["a", "b", "c"]})
    assert sum(E.split_amounts(D("100.00"), shares).values()) == D("100.00")

    # And the contrast, so this test fails if somebody quantizes the shares "for tidiness".
    squashed = {k: v.quantize(AR.SHARE_PLACES) for k, v in shares.items()}
    assert sum(E.split_amounts(D("100.00"), squashed).values()) == D("99.99"), \
        "four decimal places loses a cent here, which is why the shares keep full precision"


def test_it_refuses_to_guess_when_a_rule_it_follows_has_not_resolved():
    """Allocating from the rules that DID resolve would reweight the split toward whoever
    happened to be ready, which is a wrong answer wearing a right one's clothes."""
    shares, reason = E.derived_shares({"a": {"x": D("100")}}, {"b"},
                                      {"rule_ids": ["a", "b"]})
    assert shares == {} and reason == "followed_rules_pending"


def test_it_says_so_when_the_rules_it_follows_allocated_nothing():
    shares, reason = E.derived_shares({"a": {}}, set(), {"rule_ids": ["a"]})
    assert shares == {} and reason == "no_followed_amounts"


def test_every_reason_it_can_report_has_a_label_a_person_can_read():
    for code in ("followed_rules_pending", "no_followed_amounts"):
        assert code in E.PENDING_REASONS
        assert AR.PENDING_REASON_LABELS[code]["label"]
        assert AR.PENDING_REASON_LABELS[code]["action"]


# ── end to end, through the engine ────────────────────────────────────────────────────────

async def test_payroll_tax_follows_the_wages_it_was_charged_on():
    """Connor's case, end to end.

    A big earner split 80/20 and a small one split 50/50, then payroll tax derived from
    both. The tax pool must land 77.27/22.73, which is neither person's split and not the
    average of the two.
    """
    tenant_id, biz, actor, _s = await _ctx()
    big = await _split_rule(tenant_id, actor, biz, pattern="Payroll Expenses:Big",
                            pool_name="Big Earner",
                            targets=[{"business_id": str(biz["the_forum"]), "share": "0.8"},
                                     {"business_id": str(biz["becollective"]),
                                      "share": "0.2"}])
    small = await _split_rule(tenant_id, actor, biz, pattern="Payroll Expenses:Small",
                              pool_name="Small Earner",
                              targets=[{"business_id": str(biz["the_forum"]), "share": "0.5"},
                                       {"business_id": str(biz["becollective"]),
                                        "share": "0.5"}])
    tax = await _derived_rule(tenant_id, actor, biz, [big, small])

    await _balance(tenant_id, biz["springb"], "6010", "1000.00")
    await _balance(tenant_id, biz["springb"], "6020", "100.00")
    await _balance(tenant_id, biz["springb"], "6030", "220.00")
    await _derive(tenant_id)

    got, rows = await _rows_for(tenant_id, tax)
    assert got[str(biz["the_forum"])] == D("170.00"), got        # 220 * .772727...
    assert got[str(biz["becollective"])] == D("50.00"), got      # 220 * .227272...
    assert sum(got.values()) == D("220.00"), "and the pool still adds back to the cent"
    # The averaged answer would have been 220 * .65 = 143.00 to the Forum.
    assert got[str(biz["the_forum"])] != D("143.00"), \
        "143 is what averaging the percentages gives, and it is the wrong answer"
    assert "Derived" in rows[0].basis and "follows 2 rules" in rows[0].basis


async def test_the_basis_spells_the_computed_shares_because_nobody_chose_them():
    tenant_id, biz, actor, _s = await _ctx()
    big = await _split_rule(tenant_id, actor, biz, pattern="Payroll Expenses:Big",
                            pool_name="Big Earner",
                            targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    tax = await _derived_rule(tenant_id, actor, biz, [big])
    await _balance(tenant_id, biz["springb"], "6010", "1000.00")
    await _balance(tenant_id, biz["springb"], "6030", "100.00")
    await _derive(tenant_id)

    _got, rows = await _rows_for(tenant_id, tax)
    assert rows[0].basis == "Tier 3 Derived, follows 1 rule, 100.00", rows[0].basis


async def test_a_derived_rule_waits_when_the_rule_it_follows_is_waiting():
    """A driven rule with no commit resolves nothing, so the tax rule that follows it must
    allocate nothing and SAY WHY, rather than splitting on whatever else resolved."""
    tenant_id, biz, actor, _s = await _ctx()
    async with SessionLocal() as s:
        from app.services import alloc_drivers as DR
        d = await DR.create_driver(s, tenant_id, actor, key="staff_time_derived",
                                   label="Staff time", kind="split")
    async with SessionLocal() as s:
        waiting, _v = await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Payroll Expenses:Big",
            pool_name="Big Earner", source_business_id=biz["springb"], method="driven",
            effective_from=MONTH[0], reach="forward", rationale="By how time was spent.",
            driver_id=d.id, driver_subject="Big Earner")
    resolved = await _split_rule(tenant_id, actor, biz, pattern="Payroll Expenses:Small",
                                 pool_name="Small Earner",
                                 targets=[{"business_id": str(biz["the_forum"]),
                                           "share": "1"}])
    tax = await _derived_rule(tenant_id, actor, biz, [waiting.id, resolved])

    await _balance(tenant_id, biz["springb"], "6010", "1000.00")
    await _balance(tenant_id, biz["springb"], "6020", "100.00")
    await _balance(tenant_id, biz["springb"], "6030", "220.00")
    out = await _derive(tenant_id)

    got, _rows = await _rows_for(tenant_id, tax)
    assert got == {}, "it must not allocate from the one rule that happened to resolve"
    assert any(p["reason"] == "followed_rules_pending" and p["rule_id"] == str(tax)
               for p in out["pending"]), out["pending"]

    async with SessionLocal() as s:
        recon = await E.reconciliation(s, tenant_id, MONTH, today=TODAY)
    row = next(r for r in recon["rules"] if r["pool_name"] == "Payroll taxes and fees")
    assert row["unresolved_amount"] == D("220.00")
    assert row["pending"][0]["reason"] == "followed_rules_pending", \
        "and Reconciliation reports the same cause, from the same computation"


async def test_a_derived_rule_resolves_cleanly_on_reconciliation_when_it_worked():
    tenant_id, biz, actor, _s = await _ctx()
    big = await _split_rule(tenant_id, actor, biz, pattern="Payroll Expenses:Big",
                            pool_name="Big Earner",
                            targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    await _derived_rule(tenant_id, actor, biz, [big])
    await _balance(tenant_id, biz["springb"], "6010", "1000.00")
    await _balance(tenant_id, biz["springb"], "6030", "100.00")
    await _derive(tenant_id)

    async with SessionLocal() as s:
        recon = await E.reconciliation(s, tenant_id, MONTH, today=TODAY)
    row = next(r for r in recon["rules"] if r["pool_name"] == "Payroll taxes and fees")
    assert row["resolved_amount"] == D("100.00")
    assert row["unresolved_amount"] == D("0.00") and row["pending"] == []


# ── the cycle guard ───────────────────────────────────────────────────────────────────────

async def test_a_derived_rule_cannot_follow_another_derived_rule():
    """The second pass runs ONCE, so a chain two deep would read a figure that had not been
    computed yet and allocate from a zero, silently. Refused at the door instead."""
    tenant_id, biz, actor, _s = await _ctx()
    big = await _split_rule(tenant_id, actor, biz, pattern="Payroll Expenses:Big",
                            pool_name="Big Earner",
                            targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    first = await _derived_rule(tenant_id, actor, biz, [big])
    with pytest.raises(ValueError, match="itself a derived rule"):
        await _derived_rule(tenant_id, actor, biz, [first],
                            pattern="Payroll Expenses:Payroll Taxes",
                            pool_name="Second order")


async def test_a_derived_rule_cannot_follow_itself():
    tenant_id, biz, actor, _s = await _ctx()
    big = await _split_rule(tenant_id, actor, biz, pattern="Payroll Expenses:Big",
                            pool_name="Big Earner",
                            targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    tax = await _derived_rule(tenant_id, actor, biz, [big])
    async with SessionLocal() as s:
        with pytest.raises(ValueError, match="cannot follow itself"):
            await AR.supersede(
                s, tenant_id, actor, tax, method="derived", effective_from=MONTH[0],
                reach="forward", rationale="Trying to follow itself.",
                source_spec={"source": "follows_rules", "rule_ids": [str(tax)]})


async def test_it_refuses_a_rule_that_is_not_in_this_workspace():
    tenant_id, biz, actor, _s = await _ctx()
    import uuid as _uuid
    with pytest.raises(ValueError, match="not in this workspace"):
        await _derived_rule(tenant_id, actor, biz, [_uuid.uuid4()])


async def test_it_refuses_an_empty_follow_list_in_words():
    tenant_id, biz, actor, _s = await _ctx()
    with pytest.raises(ValueError, match="which rules this one follows"):
        await _derived_rule(tenant_id, actor, biz, [])


async def test_it_refuses_the_same_rule_listed_twice():
    """Listed twice, it would count double in the weighting."""
    tenant_id, biz, actor, _s = await _ctx()
    big = await _split_rule(tenant_id, actor, biz, pattern="Payroll Expenses:Big",
                            pool_name="Big Earner",
                            targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    with pytest.raises(ValueError, match="listed twice"):
        await _derived_rule(tenant_id, actor, biz, [big, big])


async def test_a_derived_rule_takes_no_targets_and_no_driver():
    tenant_id, biz, actor, _s = await _ctx()
    with pytest.raises(ValueError, match="does not read a driver"):
        async with SessionLocal() as s:
            await AR.create_applied_rule(
                s, tenant_id, actor, scope_kind="account", pattern="Payroll Expenses:Tax",
                pool_name="Taxes", source_business_id=biz["springb"], method="derived",
                effective_from=MONTH[0], reach="forward", rationale="Follows the wages.",
                driver_id="00000000-0000-0000-0000-000000000001",
                source_spec={"source": "follows_rules", "rule_ids": ["x"]})


# ── the payload the page reads ────────────────────────────────────────────────────────────

async def test_the_ledger_says_what_the_rule_follows_rather_than_how_it_works():
    tenant_id, biz, actor, _s = await _ctx()
    big = await _split_rule(tenant_id, actor, biz, pattern="Payroll Expenses:Big",
                            pool_name="Big Earner",
                            targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    await _derived_rule(tenant_id, actor, biz, [big])
    async with SessionLocal() as s:
        payload = await E.rules_payload(s, tenant_id, MONTH, today=TODAY)
    label = next(r for r in payload["rules"]
                 if r["pool_name"] == "Payroll taxes and fees")["targets_label"]
    assert label == "Follows Big Earner", label
    assert "derived" not in label, "no enum code reaches the screen"


async def test_derived_is_in_the_vocabulary_the_page_renders():
    assert AR.METHOD_TIER["derived"] == 3
    assert AR.METHOD_LABELS["derived"]
    assert "derived" not in AR.METHOD_LABELS["derived"].lower()
