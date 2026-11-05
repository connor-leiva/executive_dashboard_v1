"""Phase 1 of ALLOCATIONS-UI-SPEC: the figures and words the page was getting wrong.

U1 to U8 are not taste. Four of them are a wrong number or a wrong word on screen, and three
of those four were invisible to review: they need a reader to compare what is rendered against
what the engine meant. These are the gates that make them visible to a test instead.

The parity test at the bottom is the one that earns the refactor. Pulling `resolve_rule_month`
out of `derive_applied` cut up the only function in this module that WRITES contributions,
which is what the to-the-cent invariants defend, so something has to compare the writes.
"""
import datetime as dt
from decimal import Decimal as D

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (AccountPeriodBalance, AllocationContribution, AllocationRule,
                        AllocationRuleVersion, Business, BookTxn, CoaMap, CoaSettings,
                        StandardAccount, Tenant, User)
from app.seed import seed
from app.services import alloc_drivers as DR
from app.services import alloc_engine as E
from app.services import alloc_rules as AR
from app.services.coa import seed_standard_chart
from app.services.coa_balances import get_settings

MONTH = (dt.date(2026, 7, 1), dt.date(2026, 7, 31))
TODAY = dt.date(2026, 9, 30)
CHART = [("6010", "Advertising:Meta", "Expense", "6040")]


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


async def _balance(tenant_id, business_id, amount, qid="6010"):
    async with SessionLocal() as s:
        s.add(AccountPeriodBalance(
            tenant_id=tenant_id, business_id=business_id, period_start=MONTH[0],
            period_end=MONTH[1], qbo_account_id=qid, amount=D(amount), source="qbo_tb"))
        await s.commit()


async def _driver(tenant_id, actor, key):
    async with SessionLocal() as s:
        return await DR.create_driver(s, tenant_id, actor, key=key, label="Staff time",
                                      kind="split")


async def _rule(tenant_id, actor, **terms):
    async with SessionLocal() as s:
        rule, v = await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Advertising",
            pool_name="Advertising", effective_from=MONTH[0], reach="forward", **terms)
        return rule.id, v.id


async def _recon(tenant_id):
    async with SessionLocal() as s:
        out = await E.reconciliation(s, tenant_id, MONTH, today=TODAY)
    return next(r for r in out["rules"] if r["pool_name"] == "Advertising")


# ── U2: a Direct rule that keeps its own cost ─────────────────────────────────────────────

async def test_a_direct_rule_to_its_own_entity_resolves_instead_of_pending_forever():
    """The pool entity's own share is never a row: `ck_alloc_not_self` forbids it and 5.4
    derives what stays as `pool - sum(targets)`. So asking "was a row written" reported this
    rule unresolved every month, forever, gold on Rules and counted in the badge.

    It is as resolved as a `retained` rule. Somebody decided, and the decision was that the
    cost does not move.
    """
    tenant_id, biz, actor, _s = await _ctx()
    await _rule(tenant_id, actor, source_business_id=biz["springb"], method="direct",
                rationale="This cost belongs to the entity that books it.",
                targets=[{"business_id": str(biz["springb"]), "share": "1"}])
    await _balance(tenant_id, biz["springb"], "1666.67")

    row = await _recon(tenant_id)
    assert row["pool_amount"] == D("1666.67")
    assert row["resolved_amount"] == D("1666.67"), "deciding it stays put IS resolving it"
    assert row["unresolved_amount"] == D("0.00")
    assert row["pending"] == [], "and it must leave the Reconciliation badge"


async def test_an_uncommitted_driver_month_stays_pending_though_its_targets_are_null():
    """The mutation `_keeps_all` has to survive, and the reason it is not a one-liner.

    `all()` of an empty list is True, and `targets` is NULL on every driven, traced, retained
    and ended version. Without the non-empty guard, an uncommitted driver month reports as
    RESOLVED: the exact opposite of what this view exists to show, and it would have been
    green, because the obvious test only checks the case the fix was written for.
    """
    tenant_id, biz, actor, _s = await _ctx()
    d = await _driver(tenant_id, actor, "staff_time_null")
    _rid, vid = await _rule(tenant_id, actor, source_business_id=biz["springb"],
                            method="driven", rationale="By how the team spent its time.",
                            driver_id=d.id, driver_subject="S. Ostermiller")
    async with SessionLocal() as s:
        v = (await s.execute(select(AllocationRuleVersion).where(
            AllocationRuleVersion.id == vid))).scalar_one()
        assert v.targets is None, "the premise: a driven version carries no targets"
        assert not E._keeps_all(v, biz["springb"]), "empty targets must not read as kept"
    await _balance(tenant_id, biz["springb"], "5500.00")

    row = await _recon(tenant_id)
    assert row["unresolved_amount"] == D("5500.00")
    assert row["pending"], "an uncommitted driver month is pending, not resolved"


async def test_keeps_all_is_false_when_only_some_targets_are_the_pool_entity():
    """A split that leaves part of the cost behind still MOVES part of it, so it is not kept.
    Mutating the helper's `all` to `any` has to fail something."""
    tenant_id, biz, actor, _s = await _ctx()
    _rid, vid = await _rule(
        tenant_id, actor, source_business_id=biz["springb"], method="split",
        rationale="Most of it is the Forum's; the rest is ours.",
        targets=[{"business_id": str(biz["the_forum"]), "share": "0.8"},
                 {"business_id": str(biz["springb"]), "share": "0.2"}])
    async with SessionLocal() as s:
        v = (await s.execute(select(AllocationRuleVersion).where(
            AllocationRuleVersion.id == vid))).scalar_one()
        assert not E._keeps_all(v, biz["springb"])


# ── U3: the reason, not the method ────────────────────────────────────────────────────────

async def test_every_pending_month_says_why_and_every_reason_has_a_label():
    """The page printed the METHOD where the reason belongs. "2026-07-01 . driven" tells
    nobody what to do, and the reasons existed all along, inside the writer, discarded.

    This also holds the vocabulary in step with the engine: a reason with no entry in
    `PENDING_REASON_LABELS` reaches a person as a bare code.
    """
    tenant_id, biz, actor, _s = await _ctx()
    d = await _driver(tenant_id, actor, "staff_time_reason")
    await _rule(tenant_id, actor, source_business_id=biz["springb"], method="driven",
                rationale="By how the team spent its time.",
                driver_id=d.id, driver_subject="S. Ostermiller")
    await _balance(tenant_id, biz["springb"], "8000.00")

    pending = (await _recon(tenant_id))["pending"][0]
    assert pending["reason"] == "no_driver_values", "the reason, not the method"
    assert pending["method"] == "driven", "the method stays; it is just not the explanation"
    assert AR.PENDING_REASON_LABELS[pending["reason"]]["target"] == "drivers"
    missing = set(E.PENDING_REASONS) - set(AR.PENDING_REASON_LABELS)
    assert not missing, f"these would render as bare codes: {sorted(missing)}"


async def test_the_reason_distinguishes_a_missing_commit_from_a_missing_subject():
    """Two reasons, two different actions. A commit that exists but says nothing about this
    person is a different job from no commit at all, and one word for both sends the reader
    to the wrong place."""
    tenant_id, biz, actor, _s = await _ctx()
    d = await _driver(tenant_id, actor, "staff_time_subject")
    await _rule(tenant_id, actor, source_business_id=biz["springb"], method="driven",
                rationale="By how the team spent its time.",
                driver_id=d.id, driver_subject="S. Ostermiller")
    await _balance(tenant_id, biz["springb"], "8000.00")
    async with SessionLocal() as s:
        await DR.commit_month(s, tenant_id, actor, d.id, period=MONTH[0], rows=[
            {"subject": "Somebody Else", "business_id": str(biz["the_forum"]),
             "value_kind": "share", "value": "1"}])

    pending = (await _recon(tenant_id))["pending"][0]
    assert pending["reason"] == "driver_subject_missing", \
        "the month was committed; this person was not in it"


# ── U5: the approver ──────────────────────────────────────────────────────────────────────

async def test_a_version_names_the_person_who_approved_it():
    """The approver is the one field the version table exists to carry, and every row read
    "Unknown" because `version_out` returned no name at all."""
    tenant_id, biz, actor, _s = await _ctx()
    _rid, vid = await _rule(tenant_id, actor, source_business_id=biz["springb"],
                            method="retained", rationale="Ours, and it stays ours.")
    async with SessionLocal() as s:
        rows = list((await s.execute(select(AllocationRuleVersion).where(
            AllocationRuleVersion.id == vid))).scalars())
        out = AR.version_out(rows[0], await AR.approver_names(s, tenant_id, rows))
    assert out["approved_by"] is not None, "the version was approved by somebody"
    assert out["approved_by"]["id"] == str(actor.id)
    assert out["approved_by"]["name"] == actor.name
    assert out["approved_by"]["name"], "a name, not an empty string standing in for one"


# ── U7: the server does the arithmetic ────────────────────────────────────────────────────

async def test_the_payload_carries_its_own_totals_so_the_browser_sums_nothing():
    tenant_id, biz, actor, _s = await _ctx()
    await _rule(tenant_id, actor, source_business_id=biz["springb"], method="split",
                rationale="Half each, and the Forum carries the rest.",
                targets=[{"business_id": str(biz["the_forum"]), "share": "0.5"},
                         {"business_id": str(biz["becollective"]), "share": "0.3"},
                         {"business_id": str(biz["springb"]), "share": "0.2"}])
    await _balance(tenant_id, biz["springb"], "1000.00")
    async with SessionLocal() as s:
        await E.derive_all(s, tenant_id, today=TODAY, months=[MONTH])
        await s.commit()
    async with SessionLocal() as s:
        payload = await E.rules_payload(s, tenant_id, MONTH, today=TODAY)

    totals = payload["meta"]["totals"]
    assert totals["pool_amount"] == D("1000.00")
    assert totals["to_partners"] == D("800.00")
    assert totals["retained"] == D("200.00")
    assert totals["pool_amount"] == totals["to_partners"] + totals["retained"], \
        "the totals have to describe one split, not two"
    tier3 = payload["meta"]["tier_totals"]["3"]
    assert tier3["count"] == 1 and tier3["pool_amount"] == D("1000.00")

    rule = next(r for r in payload["rules"] if r["pool_name"] == "Advertising")
    assert rule["to_partners_by_target"][str(biz["the_forum"])] == D("500.00")
    assert rule["to_partners_by_target"][str(biz["becollective"])] == D("300.00")
    assert sum(rule["to_partners_by_target"].values()) == rule["to_partners"], \
        "the per-target figures must add to the total they sit beside"
    assert str(biz["springb"]) not in rule["to_partners_by_target"], \
        "what stayed is not a partner row; it is the derived remainder"


async def test_the_targets_label_says_where_the_money_goes_in_words():
    """Replaces a Method column that printed one of six sentences over and over without ever
    saying where the money went."""
    tenant_id, biz, actor, _s = await _ctx()
    await _rule(tenant_id, actor, source_business_id=biz["springb"], method="direct",
                rationale="All of it is the Forum's.",
                targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    async with SessionLocal() as s:
        payload = await E.rules_payload(s, tenant_id, MONTH, today=TODAY)
    rule = next(r for r in payload["rules"] if r["pool_name"] == "Advertising")
    assert rule["targets_label"] == "The Forum 100%"
    assert "direct" not in (rule["targets_label"] or ""), "no enum code reaches the screen"


async def test_a_driven_rule_names_its_driver_rather_than_the_method():
    """"A monthly number sets the split" is true of every driven rule on the page, so it
    tells a reader nothing about this one. 6.1 asks for the driver's name."""
    tenant_id, biz, actor, _s = await _ctx()
    d = await _driver(tenant_id, actor, "staff_time_label")
    await _rule(tenant_id, actor, source_business_id=biz["springb"], method="driven",
                rationale="By how the team spent its time.",
                driver_id=d.id, driver_subject="S. Ostermiller")
    async with SessionLocal() as s:
        payload = await E.rules_payload(s, tenant_id, MONTH, today=TODAY)
    label = next(r for r in payload["rules"]
                 if r["pool_name"] == "Advertising")["targets_label"]
    assert label == "By Staff time", label
    assert "driven" not in label, "no enum code reaches the screen"


async def test_a_retained_rule_names_the_entity_it_stays_on():
    tenant_id, biz, actor, _s = await _ctx()
    await _rule(tenant_id, actor, source_business_id=biz["springb"], method="retained",
                rationale="Ours, and it stays ours.")
    async with SessionLocal() as s:
        payload = await E.rules_payload(s, tenant_id, MONTH, today=TODAY)
    label = next(r for r in payload["rules"]
                 if r["pool_name"] == "Advertising")["targets_label"]
    async with SessionLocal() as s:
        name = (await s.execute(select(Business.name).where(
            Business.id == biz["springb"]))).scalar_one()
    assert label == f"Stays on {name}"


# ── U8: one vocabulary ────────────────────────────────────────────────────────────────────

async def test_the_payload_carries_every_vocabulary_the_page_needs():
    """Principle 4: the frontend holds none. Three `REASON` maps that disagreed about the same
    code is what happens when a word is defined wherever it is first needed."""
    tenant_id, _b, _a, _s = await _ctx()
    async with SessionLocal() as s:
        meta = (await E.rules_payload(s, tenant_id, MONTH, today=TODAY))["meta"]
    for key in ("tiers", "methods", "scopes", "reasons", "gaps", "value_kinds"):
        assert meta.get(key), f"meta.{key} is what the page renders instead of a code"
    assert set(meta["methods"]) >= set(AR.METHOD_TIER), "every method needs a word"
    assert set(meta["scopes"]) == set(AR.SCOPE_KINDS)
    for code, entry in meta["reasons"].items():
        assert entry["label"] and entry["action"] and entry["target"], \
            f"{code} has no action, so its Reconciliation row has nowhere to send anybody"


# ── the parity gate for the extraction ────────────────────────────────────────────────────

async def test_the_extracted_resolver_and_the_writer_agree_to_the_cent():
    """Resolve a month through the pure function, derive the same month through the writer,
    and compare what was persisted field for field.

    The fixture uses a pool with an odd half-cent across three unequal shares, so the rounding
    residue is live: a parity test on a figure that divides evenly proves nothing about the
    one place this module cannot afford to drift.
    """
    tenant_id, biz, actor, _s = await _ctx()
    rule_id, _vid = await _rule(
        tenant_id, actor, source_business_id=biz["springb"], method="split",
        rationale="Three ways, and the thirds do not divide evenly.",
        targets=[{"business_id": str(biz["the_forum"]), "share": "0.5"},
                 {"business_id": str(biz["becollective"]), "share": "0.3"},
                 {"business_id": str(biz["springb"]), "share": "0.2"}])
    await _balance(tenant_id, biz["springb"], "5783.25")

    async with SessionLocal() as s:
        rules = list((await s.execute(select(AllocationRule).where(
            AllocationRule.tenant_id == tenant_id))).scalars())
        history = {r.id: await AR.versions_for(s, tenant_id, r.id) for r in rules}
        live = [(r, AR.effective_version(history[r.id], MONTH[0])) for r in rules]
        pools, _vp = await E.pools_for_month(s, tenant_id, live, MONTH, today=TODAY)
        ctx = E.ResolveCtx(await E._observed_inbound(s, tenant_id, MONTH), {}, {}, {}, {})
        rule, v = live[0]
        expected, pending = E.resolve_rule_month(rule, v, pools.get(rule.id) or {}, MONTH, ctx)
    assert pending == [] and expected, "the fixture must resolve, or this proves nothing"

    async with SessionLocal() as s:
        await E.derive_all(s, tenant_id, today=TODAY, months=[MONTH])
        await s.commit()
    async with SessionLocal() as s:
        written = list((await s.execute(select(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id,
            AllocationContribution.rule_id == rule_id,
            AllocationContribution.booking == "applied",
            AllocationContribution.grain == "month"))).scalars())

    assert {(str(r["target_business_id"]), str(r["standard_account_id"])): r["amount"]
            for r in expected} == \
           {(str(w.target_business_id), str(w.standard_account_id)): w.amount
            for w in written}, \
        "the pure resolver and the writer disagree about what this month allocates"
    assert sum(w.amount for w in written) == D("4626.60")
    assert sum(w.amount for w in written) + D("1156.65") == D("5783.25"), \
        "and the split still adds back to its pool to the cent"


async def test_the_resolver_reports_a_double_count_without_writing_the_row():
    """`resolve_rule_month` is called by a reporting path, so it must be unable to add anything
    to the session. The double-count branch is the one that returns a reason and no row."""
    tenant_id, biz, actor, std = await _ctx()
    rule_id, _vid = await _rule(
        tenant_id, actor, source_business_id=biz["springb"], method="direct",
        rationale="All of it is the Forum's.",
        targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    await _balance(tenant_id, biz["springb"], "900.00")
    async with SessionLocal() as s:
        s.add(AllocationContribution(
            tenant_id=tenant_id, period_start=MONTH[0], period_end=MONTH[1],
            source_business_id=biz["springb"], target_business_id=biz["the_forum"],
            standard_account_id=std["6040"], amount=D("900.00"),
            pool_name="Advertising", booking="observed", grain="window"))
        await s.commit()

    async with SessionLocal() as s:
        rules = list((await s.execute(select(AllocationRule).where(
            AllocationRule.tenant_id == tenant_id))).scalars())
        history = {r.id: await AR.versions_for(s, tenant_id, r.id) for r in rules}
        live = [(r, AR.effective_version(history[r.id], MONTH[0])) for r in rules]
        pools, _vp = await E.pools_for_month(s, tenant_id, live, MONTH, today=TODAY)
        ctx = E.ResolveCtx(await E._observed_inbound(s, tenant_id, MONTH), {}, {}, {}, {})
        rule, v = live[0]
        rows, pending = E.resolve_rule_month(rule, v, pools.get(rule.id) or {}, MONTH, ctx)
        assert not s.new, "the resolver must not add anything to the session"
    assert rows == []
    assert pending and pending[0]["reason"] == "double_count_risk"

    row = await _recon(tenant_id)
    assert row["pending"][0]["reason"] == "double_count_risk", \
        "and reconciliation reports the same cause, from the same function"


# ── U4: the queue counted cost that was already allocated ─────────────────────────────────

async def _txn(tenant_id, business_id, *, payee, amount, multi=False, qid="6010"):
    async with SessionLocal() as s:
        s.add(BookTxn(tenant_id=tenant_id, business_id=business_id, realm_id="r1",
                      qbo_type="Purchase", qbo_id=f"{payee}-{amount}-{multi}",
                      txn_date=dt.date(2026, 7, 14), amount=D(amount), payee=payee,
                      memo="a memo", account_qbo_id=qid,
                      flags=({"multi_line": True} if multi else None)))
        await s.commit()


async def _queue(tenant_id):
    async with SessionLocal() as s:
        return await E.exceptions(s, tenant_id, MONTH, today=TODAY)


async def test_a_multi_line_charge_on_a_covered_account_is_listed_but_not_counted():
    """An account rule reads trial balances, which already include every line of a multi-line
    transaction. So the charge was in the pool AND in the unclaimed total, and the figure at
    the top of the queue was the sum of the two (U4, D6).

    It still APPEARS, because the engine cannot know a multi-line charge is all one vendor's
    and a person should look. It is just no longer counted as money nobody has allocated.
    """
    tenant_id, biz, actor, _s = await _ctx()
    await _rule(tenant_id, actor, source_business_id=biz["springb"], method="direct",
                rationale="Advertising is the Forum's.",
                targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    await _txn(tenant_id, biz["springb"], payee="Payroll Pay", amount="8031.59", multi=True)

    q = await _queue(tenant_id)
    row = next(r for r in q["rows"] if r["payee"] == "Payroll Pay")
    assert row["status"] == "partly_covered"
    assert row["covered_by"]["pool_name"] == "Advertising", \
        "a reader has to be able to check WHICH rule already claims it"
    assert "multi_line" in row["gaps"]
    assert q["total"] == D("0.00"), "the one figure U4 is about"


async def test_a_multi_line_charge_on_an_uncovered_account_is_still_unclaimed():
    """The other side of the same branch. Mutating the status to always say partly_covered
    has to fail something, or the fix just empties the queue."""
    tenant_id, biz, actor, _s = await _ctx()
    await _txn(tenant_id, biz["springb"], payee="Payroll Pay", amount="8031.59", multi=True)

    q = await _queue(tenant_id)
    row = next(r for r in q["rows"] if r["payee"] == "Payroll Pay")
    assert row["status"] == "unclaimed" and row["covered_by"] is None
    assert q["total"] == D("8031.59"), "nothing claims it, so it is still the queue's problem"


async def test_d7_still_holds_a_vendor_rule_never_claims_a_multi_line_charge():
    """The decision U4 does NOT touch. A vendor rule matching the payee perfectly still
    leaves a multi-line transaction in the queue, and counted, because the engine cannot
    know every line of it belongs to that payee."""
    tenant_id, biz, actor, _s = await _ctx()
    async with SessionLocal() as s:
        await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="vendor", pattern="Payroll Pay",
            pool_name="Payroll Pay", source_business_id=biz["springb"], method="direct",
            effective_from=MONTH[0], reach="forward",
            rationale="Payroll Pay bills the Forum.",
            targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    await _txn(tenant_id, biz["springb"], payee="Payroll Pay", amount="8031.59", multi=True)

    q = await _queue(tenant_id)
    row = next(r for r in q["rows"] if r["payee"] == "Payroll Pay")
    assert row["status"] == "unclaimed", "D7: never auto-claimed by payee"
    assert q["total"] == D("8031.59")


async def test_a_single_line_charge_on_a_covered_account_leaves_the_queue_entirely():
    """Unchanged behaviour, asserted so the new branch cannot quietly start listing rows that
    an account rule fully covers."""
    tenant_id, biz, actor, _s = await _ctx()
    await _rule(tenant_id, actor, source_business_id=biz["springb"], method="direct",
                rationale="Advertising is the Forum's.",
                targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    await _txn(tenant_id, biz["springb"], payee="A Supplier", amount="300.00")

    q = await _queue(tenant_id)
    assert [r["payee"] for r in q["rows"]] == [], "covered single-line charges do not appear"
    assert q["total"] == D("0.00")


async def test_the_reconciliation_total_is_the_servers_sum_not_the_browsers():
    """The ledger foot. Summed here because the page does no money arithmetic, and a total
    the browser added would be a second computation of figures the rows already carry."""
    tenant_id, biz, actor, _s = await _ctx()
    await _rule(tenant_id, actor, source_business_id=biz["springb"], method="direct",
                rationale="All of it is the Forum's.",
                targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    await _balance(tenant_id, biz["springb"], "1234.56")
    async with SessionLocal() as s:
        recon = await E.reconciliation(s, tenant_id, MONTH, today=TODAY)

    t = recon["totals"]
    assert t["count"] == len(recon["rules"])
    assert t["pool_amount"] == sum(r["pool_amount"] for r in recon["rules"])
    assert t["resolved_amount"] == sum(r["resolved_amount"] for r in recon["rules"])
    assert t["unresolved_amount"] == sum(r["unresolved_amount"] for r in recon["rules"])
    assert t["resolved_amount"] + t["unresolved_amount"] == t["pool_amount"], \
        "the foot has to describe one split, not two"
