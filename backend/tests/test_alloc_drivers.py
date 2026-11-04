"""Monthly drivers and the driven method (ALLOCATION-RULES-SPEC 4.3, 5.4, Phase 5).

The thing being defended is that NOTHING CARRIES FORWARD BY ITSELF. A month with no commit of
its own allocates nothing and says `no_driver_values`; it does not quietly reuse last month's
answer, because on the Statement a repeated figure nobody looked at is indistinguishable from
one somebody decided. `prefill` is a form convenience and is never consulted at resolution.

Appendix B2 is the staff-time case: a contractor paid by name, 70% to the Forum, a flat 2,500 to
beCollective through August, zero from September, and the remainder to the pool entity.
Appendix B3 is the subject with no numbers in any commit, which must report for its own rule
only while every other rule still resolves.
"""
import datetime as dt
from decimal import Decimal as D
from types import SimpleNamespace as NS

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.main import app

from app.db import SessionLocal
from app.models import (AllocationDriver, AllocationDriverCommit, AllocationDriverValue,
                        Business, Tenant, User)
from app.seed import seed
from app.services import alloc_drivers as DR
from app.services import alloc_engine as E

JAN, AUG, SEP = dt.date(2026, 1, 1), dt.date(2026, 8, 1), dt.date(2026, 9, 1)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
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
        return t.id, biz, actor


@pytest.fixture(autouse=True)
async def _clean():
    tenant_id, _b, _a = await _ctx()
    async with SessionLocal() as s:
        await s.execute(delete(AllocationDriverValue).where(
            AllocationDriverValue.tenant_id == tenant_id))
        await s.execute(delete(AllocationDriverCommit).where(
            AllocationDriverCommit.tenant_id == tenant_id))
        await s.execute(delete(AllocationDriver).where(
            AllocationDriver.tenant_id == tenant_id))
        await s.commit()
    yield


OSTER, PAM = "S. Ostermiller", "Pamela Bakalian"


def _row(biz, key, vk, value=None, subject=OSTER):
    return {"subject": subject, "business_id": str(biz[key]), "value_kind": vk, "value": value}


def _b2(biz, bc_amount="2500"):
    """B2's commit: 70% to the Forum, a flat amount to beCollective, the rest to the pool."""
    return [_row(biz, "the_forum", "share", "0.7"),
            _row(biz, "becollective", "amount", bc_amount),
            _row(biz, "springb", "remainder")]


async def _staff_time(tenant_id, actor):
    async with SessionLocal() as s:
        return await DR.create_driver(s, tenant_id, actor, key="staff_time",
                                      label="Staff time commitment", kind="split",
                                      unit="percent")


# ── validation ────────────────────────────────────────────────────────────────────────────

def test_a_split_driver_needs_a_subject_and_a_count_driver_must_not_have_one():
    known = {"a", "b"}
    with pytest.raises(ValueError, match="needs the person"):
        DR.validate_values("split", [{"business_id": "a", "value_kind": "share",
                                      "value": "1"}], known_business_ids=known)
    with pytest.raises(ValueError, match="has no subject"):
        DR.validate_values("count", [{"business_id": "a", "value_kind": "count",
                                      "value": "5", "subject": "Someone"}],
                           known_business_ids=known)


def test_each_kind_takes_only_its_own_value_kinds():
    known = {"a"}
    with pytest.raises(ValueError, match="takes count"):
        DR.validate_values("count", [{"business_id": "a", "value_kind": "share",
                                      "value": "1"}], known_business_ids=known)
    with pytest.raises(ValueError, match="takes share, amount, remainder"):
        DR.validate_values("split", [{"business_id": "a", "value_kind": "count",
                                      "value": "1", "subject": "X"}],
                           known_business_ids=known)


def test_a_remainder_takes_no_number_and_only_one_entity_can_have_it():
    known = {"a", "b"}
    with pytest.raises(ValueError, match="takes no number"):
        DR.validate_values("split", [{"business_id": "a", "value_kind": "remainder",
                                      "value": "100", "subject": "X"}],
                           known_business_ids=known)
    with pytest.raises(ValueError, match="one entity can take the remainder"):
        DR.validate_values("split", [
            {"business_id": "a", "value_kind": "remainder", "subject": "X"},
            {"business_id": "b", "value_kind": "remainder", "subject": "X"}],
            known_business_ids=known)


def test_an_empty_commit_and_an_all_zero_count_are_both_refused():
    with pytest.raises(ValueError, match="not a commit"):
        DR.validate_values("split", [], known_business_ids={"a"})
    with pytest.raises(ValueError, match="nothing to share out"):
        DR.validate_values("count", [{"business_id": "a", "value_kind": "count", "value": "0"}],
                           known_business_ids={"a"})


def test_a_share_is_a_fraction_and_a_count_cannot_be_negative():
    known = {"a"}
    with pytest.raises(ValueError, match="between 0 and 1"):
        DR.validate_values("split", [{"business_id": "a", "value_kind": "share",
                                      "value": "70", "subject": "X"}],
                           known_business_ids=known)
    with pytest.raises(ValueError, match="cannot be negative"):
        DR.validate_values("count", [{"business_id": "a", "value_kind": "count",
                                      "value": "-1"}], known_business_ids=known)


def test_the_same_entity_twice_for_one_person_is_refused():
    with pytest.raises(ValueError, match="listed twice"):
        DR.validate_values("split", [
            {"business_id": "a", "value_kind": "share", "value": "0.5", "subject": "X"},
            {"business_id": "a", "value_kind": "amount", "value": "100", "subject": "X"}],
            known_business_ids={"a"})
    # The same entity for a DIFFERENT person is a different answer and is fine.
    out = DR.validate_values("split", [
        {"business_id": "a", "value_kind": "share", "value": "0.5", "subject": "X"},
        {"business_id": "a", "value_kind": "share", "value": "0.5", "subject": "Y"}],
        known_business_ids={"a"})
    assert len(out) == 2


# ── commits are append-only ───────────────────────────────────────────────────────────────

async def test_a_correction_supersedes_rather_than_editing():
    tenant_id, biz, actor = await _ctx()
    d = await _staff_time(tenant_id, actor)
    async with SessionLocal() as s:
        first = await DR.commit_month(s, tenant_id, actor, d.id, period=JAN, rows=_b2(biz))
    async with SessionLocal() as s:
        second = await DR.commit_month(s, tenant_id, actor, d.id, period=JAN,
                                       rows=_b2(biz, bc_amount="3000"), note="corrected")
    async with SessionLocal() as s:
        rows = (await s.execute(select(AllocationDriverCommit).where(
            AllocationDriverCommit.tenant_id == tenant_id)
            .order_by(AllocationDriverCommit.committed_at))).scalars().all()
        old_values = await DR.values_for(s, tenant_id, first.id)
        cur = await DR.current_commit(s, tenant_id, d.id, JAN)
    assert len(rows) == 2
    assert [r.is_current for r in rows] == [False, True]
    assert rows[0].superseded_by == second.id
    assert cur.id == second.id
    # The superseded commit keeps its own numbers, which is what makes a closed month readable.
    assert {v.value for v in old_values if v.value_kind == "amount"} == {D("2500")}


async def test_a_commit_is_found_for_its_own_month_and_no_other():
    tenant_id, biz, actor = await _ctx()
    d = await _staff_time(tenant_id, actor)
    async with SessionLocal() as s:
        await DR.commit_month(s, tenant_id, actor, d.id, period=AUG, rows=_b2(biz))
    async with SessionLocal() as s:
        assert await DR.current_commit(s, tenant_id, d.id, AUG) is not None
        assert await DR.current_commit(s, tenant_id, d.id, SEP) is None
        assert await DR.current_commit(s, tenant_id, d.id, dt.date(2026, 8, 31)) is not None


async def test_prefill_offers_the_prior_month_but_is_not_a_fallback():
    """The distinction the whole design rests on. `prefill` fills a FORM; resolution never
    calls it, and a month with no commit of its own resolves to nothing."""
    tenant_id, biz, actor = await _ctx()
    d = await _staff_time(tenant_id, actor)
    async with SessionLocal() as s:
        await DR.commit_month(s, tenant_id, actor, d.id, period=AUG, rows=_b2(biz))
    async with SessionLocal() as s:
        got = await DR.prefill(s, tenant_id, d.id, SEP)
        assert got["from_month"] == "2026-08-01"
        assert len(got["rows"]) == 3
        # And September itself still has no commit, which is what resolution reads.
        assert await DR.current_commit(s, tenant_id, d.id, SEP) is None
        assert (await DR.prefill(s, tenant_id, d.id, JAN))["from_month"] is None


async def test_a_duplicate_driver_key_is_refused():
    tenant_id, _biz, actor = await _ctx()
    await _staff_time(tenant_id, actor)
    async with SessionLocal() as s:
        with pytest.raises(ValueError, match="already exists"):
            await DR.create_driver(s, tenant_id, actor, key="Staff Time", label="Again",
                                   kind="split")


# ── driven resolution: B2 ─────────────────────────────────────────────────────────────────

def _v(bid, vk, value=None, subject=OSTER):
    return NS(business_id=bid, value_kind=vk, value=(D(value) if value is not None else None),
              subject=subject)


def test_b2_resolves_fixed_then_share_then_remainder():
    """Appendix B2. 70% of the pool to the Forum, a flat 2,500 to beCollective, the rest to the
    pool entity, and the three add back to the pool exactly."""
    vals = [_v("forum", "share", "0.7"), _v("bc", "amount", "2500"), _v("pool", "remainder")]
    out, reason = E.driven_shares("split", vals, subject=OSTER, pool=D("10000.00"))
    assert reason is None
    assert out == {"forum": D("7000.00"), "bc": D("2500.00"), "pool": D("500.00")}
    assert sum(out.values()) == D("10000.00")


def test_b2_from_september_the_flat_amount_is_zero_and_the_rest_moves_to_the_pool():
    vals = [_v("forum", "share", "0.7"), _v("bc", "amount", "0"), _v("pool", "remainder")]
    out, reason = E.driven_shares("split", vals, subject=OSTER, pool=D("10000.00"))
    assert reason is None
    assert out == {"forum": D("7000.00"), "pool": D("3000.00")}, "a zero amount writes no row"
    assert sum(out.values()) == D("10000.00")


def test_b3_an_unset_subject_reports_and_the_other_subject_still_resolves():
    """Appendix B3. Pamela has no numbers in the commit; S. Ostermiller does. Her rule reports
    and his resolves, from the same commit."""
    vals = [_v("forum", "share", "0.7"), _v("bc", "amount", "2500"), _v("pool", "remainder")]
    out, reason = E.driven_shares("split", vals, subject=PAM, pool=D("66000.00"))
    assert out == {} and reason == "driver_subject_missing"
    out, reason = E.driven_shares("split", vals, subject=OSTER, pool=D("10000.00"))
    assert reason is None and sum(out.values()) == D("10000.00")


def test_fixed_amounts_above_the_pool_are_reported_not_absorbed():
    vals = [_v("bc", "amount", "2500"), _v("pool", "remainder")]
    out, reason = E.driven_shares("split", vals, subject=OSTER, pool=D("1000.00"))
    assert out == {} and reason == "fixed_exceeds_pool"


def test_a_share_and_a_fixed_amount_that_together_overcommit_are_reported():
    """The literal reading of 5.4 makes this reachable: 70% of 3,000 is 2,100 and the flat 2,500
    takes it past the pool. Reported rather than written as a negative remainder, which would
    allocate money out of an entity that never held it."""
    vals = [_v("forum", "share", "0.7"), _v("bc", "amount", "2500"), _v("pool", "remainder")]
    out, reason = E.driven_shares("split", vals, subject=OSTER, pool=D("3000.00"))
    assert out == {} and reason == "allocation_exceeds_pool"


def test_without_a_remainder_entity_a_short_split_simply_leaves_the_rest_behind():
    vals = [_v("forum", "share", "0.7"), _v("bc", "amount", "500")]
    out, reason = E.driven_shares("split", vals, subject=OSTER, pool=D("10000.00"))
    assert reason is None
    assert out == {"forum": D("7000.00"), "bc": D("500.00")}
    assert E.retained(D("10000.00"), out) == D("2500.00"), "and `retained` says what stayed"


def test_a_count_driver_shares_by_count_over_the_total():
    vals = [_v("forum", "count", "60", subject=None), _v("bc", "count", "40", subject=None)]
    out, reason = E.driven_shares("count", vals, subject=None, pool=D("1000.00"))
    assert reason is None
    assert out == {"forum": D("600.00"), "bc": D("400.00")}
    assert sum(out.values()) == D("1000.00")


def test_a_count_driver_still_adds_back_when_the_counts_do_not_divide():
    vals = [_v("a", "count", "1", subject=None), _v("b", "count", "1", subject=None),
            _v("c", "count", "1", subject=None)]
    out, reason = E.driven_shares("count", vals, subject=None, pool=D("100.00"))
    assert reason is None
    assert sum(out.values()) == D("100.00"), "thirds of a hundred still come to a hundred"
    assert sorted(str(v) for v in out.values()) == ["33.33", "33.33", "33.34"]


def test_no_values_at_all_reports_rather_than_resolving_to_zero():
    out, reason = E.driven_shares("count", [], subject=None, pool=D("1000.00"))
    assert out == {} and reason == "no_driver_values"
    out, reason = E.driven_shares("split", [], subject=OSTER, pool=D("1000.00"))
    assert out == {} and reason == "driver_subject_missing"


# ── the routes ────────────────────────────────────────────────────────────────────────────

TRANSPORT = ASGITransport(app=app)


async def _token(c) -> str:
    r = await c.post("/api/v1/auth/login",
                     json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


async def test_the_routes_create_a_driver_commit_a_month_and_read_it_back():
    tenant_id, biz, _actor = await _ctx()
    async with AsyncClient(transport=TRANSPORT, base_url="http://t") as c:
        h = {"Authorization": f"Bearer {await _token(c)}"}
        r = await c.post("/api/v1/books/allocations/drivers", headers=h, json={
            "key": "Staff Time", "label": "Staff time commitment",
            "kind": "split", "unit": "percent"})
        assert r.status_code == 200, r.text
        d = r.json()["driver"]
        assert d["key"] == "staff_time", "the key is normalized"
        assert d["value_kinds"] == ["share", "amount", "remainder"]
        did = d["id"]

        r = await c.get("/api/v1/books/allocations/drivers", headers=h)
        assert [x["key"] for x in r.json()["drivers"]] == ["staff_time"]

        # A month with nothing committed says so, and offers nothing to prefill from.
        r = await c.get(f"/api/v1/books/allocations/drivers/{did}/month",
                        params={"period": "2026-08-01"}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["commit"] is None
        assert r.json()["prefill"] == {"from_month": None, "rows": []}

        r = await c.post(f"/api/v1/books/allocations/drivers/{did}/commits", headers=h,
                         json={"period": "2026-08-14", "rows": _b2(biz),
                               "note": "August as agreed"})
        assert r.status_code == 200, r.text
        assert r.json()["commit"]["period_month"] == "2026-08-01", "normalized to the month"
        assert len(r.json()["commit"]["values"]) == 3

        # September still has no commit of its own, and says so even though August exists.
        r = await c.get(f"/api/v1/books/allocations/drivers/{did}/month",
                        params={"period": "2026-09-01"}, headers=h)
        assert r.json()["commit"] is None, "August is not September's answer"
        assert r.json()["prefill"]["from_month"] == "2026-08-01"
        assert len(r.json()["prefill"]["rows"]) == 3

        bad = await c.post(f"/api/v1/books/allocations/drivers/{did}/commits", headers=h,
                           json={"period": "2026-09-01",
                                 "rows": [_row(biz, "the_forum", "share", "70")]})
        assert bad.status_code == 400 and "between 0 and 1" in bad.json()["detail"]


# ── who a split driver is about (the first month's form) ──────────────────────────────────

async def _wipe_rules_and_drivers(tenant_id):
    """`_clean` clears drivers but not RULES, and these two tests read the rules.

    Without this the first test's rules survived into the second and answered for a driver the
    fixture had already deleted, so the second test failed on a dict with an extra key. The
    rules had kept their `driver_id`: SQLite does not enforce ON DELETE SET NULL unless the
    foreign-keys pragma is on, so the column still pointed at a row that was gone.
    """
    from app.models import AllocationRule, AllocationRuleVersion
    async with SessionLocal() as s:
        await s.execute(delete(AllocationRuleVersion).where(
            AllocationRuleVersion.tenant_id == tenant_id))
        await s.execute(delete(AllocationRule).where(AllocationRule.tenant_id == tenant_id))
        await s.execute(delete(AllocationDriver).where(AllocationDriver.tenant_id == tenant_id))
        await s.commit()


async def test_subjects_come_from_the_rules_that_read_the_driver_not_from_past_commits():
    """The first month of a driver has no commits, so the form's rows cannot come from them.

    This is what made the first month uncommittable: the Drivers screen offered a commit
    control only inside the "start from last month" card, which needs a previous commit, so a
    driver with no history showed the Not set panel and nothing else. The rows have to come
    from the rules instead, and from the CURRENT version of each, so a person added to a rule
    this month appears before they have ever been committed for.
    """
    from app.services import alloc_rules as AR

    tenant_id, biz, actor = await _ctx()
    await _wipe_rules_and_drivers(tenant_id)
    d = await _staff_time(tenant_id, actor)

    # No rule reads it yet: no subjects, and the form says so rather than showing a dead end.
    async with SessionLocal() as s:
        assert await DR.subjects_by_driver(s, tenant_id) == {}

    async with SessionLocal() as s:
        for who, pattern in ((OSTER, "Payroll Expenses:S. Ostermiller"),
                             (PAM, "Contract Labor:Pamela Bakalian")):
            await AR.create_applied_rule(
                s, tenant_id, actor, scope_kind="account", pattern=pattern,
                pool_name=who, source_business_id=biz["springb"], method="driven",
                effective_from=JAN, reach="forward",
                rationale="Split by how the time was spent.",
                driver_id=d.id, driver_subject=who)

    async with SessionLocal() as s:
        got = await DR.subjects_by_driver(s, tenant_id)
    # Both people, sorted, against THIS driver, with no commit anywhere in the workspace.
    assert got == {str(d.id): sorted([OSTER, PAM])}


async def test_a_rule_that_has_been_ended_stops_offering_its_person_a_row():
    """An ended rule claims nothing, so its person is not a row to answer for any more.

    `effective_version` skips `ended`, and the form has to agree: offering a row for somebody
    no rule reads would invite a number that allocates nothing, which reads on the Drivers tab
    as a figure somebody decided.
    """
    from app.services import alloc_rules as AR

    tenant_id, biz, actor = await _ctx()
    await _wipe_rules_and_drivers(tenant_id)
    d = await _staff_time(tenant_id, actor)

    async with SessionLocal() as s:
        rule, _v = await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Payroll Expenses:S. O",
            pool_name=OSTER, source_business_id=biz["springb"], method="driven",
            effective_from=JAN, reach="forward", rationale="Split by time.",
            driver_id=d.id, driver_subject=OSTER)
    async with SessionLocal() as s:
        assert await DR.subjects_by_driver(s, tenant_id) == {str(d.id): [OSTER]}

    async with SessionLocal() as s:
        await AR.end_rule(s, tenant_id, actor, rule.id, effective_from=SEP,
                          rationale="She left.")
    async with SessionLocal() as s:
        assert await DR.subjects_by_driver(s, tenant_id) == {}


async def test_a_rule_on_a_count_driver_contributes_no_subject():
    """A count driver is answered per entity and has no subject, so it offers no person rows.

    `validate_version` stores `driver_subject` as None when it is not given, which is exactly
    what a rule against a count driver has: "a count driver has no subject: it answers per
    entity". Without the guard on the subject, that NULL would be collected and the form would
    render a nameless group of rows to fill in for nobody. Found by mutation: dropping the
    guard left every test green.
    """
    from app.services import alloc_rules as AR

    tenant_id, biz, actor = await _ctx()
    await _wipe_rules_and_drivers(tenant_id)
    async with SessionLocal() as s:
        heads = await DR.create_driver(s, tenant_id, actor, key="headcount",
                                       label="Headcount", kind="count", unit="people")
    async with SessionLocal() as s:
        await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Member gifts",
            pool_name="Member gifts", source_business_id=biz["springb"], method="driven",
            effective_from=JAN, reach="forward", rationale="Shared by how many members.",
            driver_id=heads.id)                      # no subject: a count driver has none

    async with SessionLocal() as s:
        got = await DR.subjects_by_driver(s, tenant_id)
    assert got == {}, f"a count driver's rule should offer no person rows, got {got}"
