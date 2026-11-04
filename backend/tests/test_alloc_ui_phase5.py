"""Phase 5 of ALLOCATIONS-UI-SPEC: percents in, subjects with their stakes out (6.3, 7.6).

The driver form asked for "a number between 0 and 1" in every box under a card that said
"percent" (U14). That is the mismatch that gets a 70 typed where 0.7 was meant, and allocates
seventy times the pool. Percents are typed as percents now and converted once, on the server;
storage stays a fraction (amendment 12).

The other half is what the form never said: what each row MOVES. A person typing a split for
somebody they half remember has no way to know whether the number is worth getting right.
"""
import datetime as dt
from decimal import Decimal as D

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (AccountPeriodBalance, AllocationContribution, AllocationRule,
                        BookTxn, Business, CoaMap, StandardAccount, Tenant, User)
from app.seed import seed
from app.services import alloc_drivers as DR
from app.services import alloc_rules as AR
from app.services.coa import seed_standard_chart
from app.services.coa_balances import get_settings

JULY = (dt.date(2026, 7, 1), dt.date(2026, 7, 31))
AUG = dt.date(2026, 8, 1)
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
    from app.models import AllocationDriver, AllocationDriverCommit, AllocationDriverValue
    tenant_id, biz, _a, std = await _ctx()
    async with SessionLocal() as s:
        await s.execute(delete(AllocationDriverValue).where(
            AllocationDriverValue.tenant_id == tenant_id))
        await s.execute(delete(AllocationDriverCommit).where(
            AllocationDriverCommit.tenant_id == tenant_id))
        await s.execute(delete(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))
        await s.execute(delete(AllocationRule).where(AllocationRule.tenant_id == tenant_id))
        await s.execute(delete(AllocationDriver).where(
            AllocationDriver.tenant_id == tenant_id))
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


# ── pct_to_share, the one conversion ──────────────────────────────────────────────────────

def test_a_typed_percent_becomes_a_stored_share():
    assert AR.pct_to_share("70") == D("0.7000")
    assert AR.pct_to_share("12.5") == D("0.1250")
    assert AR.pct_to_share("100") == D("1.0000")
    assert AR.pct_to_share("0") == D("0.0000")


def test_a_percent_quantizes_to_the_same_places_a_share_does():
    """`_share` quantizes to SHARE_PLACES, and a second path rounding differently is how two
    figures for one share appear."""
    assert AR.pct_to_share("33.333333").as_tuple().exponent == \
        AR.SHARE_PLACES.as_tuple().exponent
    assert AR.pct_to_share("33.333333") == D("0.3333")


def test_a_percent_outside_nought_to_a_hundred_is_refused_in_words():
    for bad in ("101", "-1", "", None, "abc"):
        with pytest.raises(ValueError, match="between 0 and 100"):
            AR.pct_to_share(bad)


def test_a_share_renders_back_as_the_percent_that_was_typed():
    """Round trip, because the committed matrix is read back from storage and a figure a
    person cannot recognise as the one they typed is a figure they will retype."""
    for typed in ("70", "12.5", "33.33", "100", "0"):
        assert AR.share_to_pct(AR.pct_to_share(typed)) == typed


# ── the commit takes percents ─────────────────────────────────────────────────────────────

async def _driver(tenant_id, actor, key="staff_time", kind="split"):
    async with SessionLocal() as s:
        return await DR.create_driver(s, tenant_id, actor, key=key, label="Staff time",
                                      kind=kind)


async def test_committing_seventy_stores_nought_point_seven():
    """The phase gate."""
    tenant_id, biz, actor, _s = await _ctx()
    d = await _driver(tenant_id, actor)
    async with SessionLocal() as s:
        await DR.commit_month(s, tenant_id, actor, d.id, period=JULY[0], rows=[
            {"subject": "S. Ostermiller", "business_id": str(biz["the_forum"]),
             "value_kind": "share", "pct": "70"},
            {"subject": "S. Ostermiller", "business_id": str(biz["becollective"]),
             "value_kind": "share", "pct": "30"}])
    async with SessionLocal() as s:
        c = await DR.current_commit(s, tenant_id, d.id, JULY[0])
        vals = await DR.values_for(s, tenant_id, c.id)
        out = DR.commit_out(c, vals)
    stored = {v["business_id"]: v["value"] for v in out["values"]}
    assert stored[str(biz["the_forum"])] == "0.7000"
    assert stored[str(biz["becollective"])] == "0.3000"
    back = {v["business_id"]: v["pct"] for v in out["values"]}
    assert back[str(biz["the_forum"])] == "70", "and it reads back as what was typed"


async def test_a_share_can_still_be_sent_as_a_fraction():
    """The engine's own fixtures and every existing caller send `value`. Breaking them to add
    a second input would be the change making the product worse to fix the form."""
    tenant_id, biz, actor, _s = await _ctx()
    d = await _driver(tenant_id, actor)
    async with SessionLocal() as s:
        await DR.commit_month(s, tenant_id, actor, d.id, period=JULY[0], rows=[
            {"subject": "S. Ostermiller", "business_id": str(biz["the_forum"]),
             "value_kind": "share", "value": "0.6"}])
    async with SessionLocal() as s:
        c = await DR.current_commit(s, tenant_id, d.id, JULY[0])
        vals = await DR.values_for(s, tenant_id, c.id)
    assert str(vals[0].value) == "0.6000"


async def test_sending_both_a_percent_and_a_share_is_refused():
    """Two answers to one question. Defaulting to either would silently discard the other."""
    tenant_id, biz, actor, _s = await _ctx()
    d = await _driver(tenant_id, actor)
    async with SessionLocal() as s:
        with pytest.raises(ValueError, match="not both"):
            await DR.commit_month(s, tenant_id, actor, d.id, period=JULY[0], rows=[
                {"subject": "S. Ostermiller", "business_id": str(biz["the_forum"]),
                 "value_kind": "share", "value": "0.7", "pct": "70"}])


async def test_a_percent_over_a_hundred_is_refused_at_the_commit():
    tenant_id, biz, actor, _s = await _ctx()
    d = await _driver(tenant_id, actor)
    async with SessionLocal() as s:
        with pytest.raises(ValueError, match="between 0 and 100"):
            await DR.commit_month(s, tenant_id, actor, d.id, period=JULY[0], rows=[
                {"subject": "S. Ostermiller", "business_id": str(biz["the_forum"]),
                 "value_kind": "share", "pct": "700"}])


# ── the subjects, and what they move ──────────────────────────────────────────────────────

async def _driven_rule(tenant_id, actor, driver_id, subject, pattern="Advertising",
                       pool_name=None, effective_from=None):
    async with SessionLocal() as s:
        return await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern=pattern,
            pool_name=pool_name or subject, source_business_id=(await _ctx())[1]["springb"],
            method="driven", effective_from=effective_from or JULY[0], reach="forward",
            rationale="Split by how the month was actually spent.",
            driver_id=driver_id, driver_subject=subject)


async def test_each_subject_says_what_it_feeds_and_what_that_pool_holds():
    """6.3: the dollars at stake, and the reason to get the number right. The form said
    nothing about what any row moved."""
    tenant_id, biz, actor, _s = await _ctx()
    d = await _driver(tenant_id, actor)
    await _driven_rule(tenant_id, actor, d.id, "S. Ostermiller")
    async with SessionLocal() as s:
        s.add(AccountPeriodBalance(
            tenant_id=tenant_id, business_id=biz["springb"], period_start=JULY[0],
            period_end=JULY[1], qbo_account_id="6010", amount=D("5500.00"), source="qbo_tb"))
        await s.commit()

    async with SessionLocal() as s:
        subs = await DR.subjects_for_month(s, tenant_id, d.id, JULY[0])
    assert [x["name"] for x in subs] == ["S. Ostermiller"]
    assert subs[0]["pool_amount"] == D("5500.00")
    assert subs[0]["rules"][0]["pool_name"] == "S. Ostermiller"
    assert subs[0]["rules"][0]["pool_amount"] == D("5500.00")


async def test_the_roster_is_the_months_roster_not_todays():
    """A person added in August must not appear on JULY's form.

    `subjects_by_driver` reads `is_current`, which is right for "who is on the form today"
    and wrong for any month in the past -- and the page gained a month control, so the past
    is now one click away. Resolved through `effective_version`, the way the engine does it.
    """
    tenant_id, biz, actor, _s = await _ctx()
    d = await _driver(tenant_id, actor)
    await _driven_rule(tenant_id, actor, d.id, "In July", pattern="Advertising")
    await _driven_rule(tenant_id, actor, d.id, "Joined August", pattern="Advertising:Meta",
                       pool_name="Later", effective_from=AUG)

    async with SessionLocal() as s:
        july = [x["name"] for x in await DR.subjects_for_month(s, tenant_id, d.id, JULY[0])]
        august = [x["name"] for x in await DR.subjects_for_month(s, tenant_id, d.id, AUG)]
    assert july == ["In July"], july
    assert "Joined August" in august, august
    # And the stale read is genuinely different, or this test proves nothing.
    async with SessionLocal() as s:
        today_roster = (await DR.subjects_by_driver(s, tenant_id)).get(str(d.id), [])
    assert sorted(today_roster) == ["In July", "Joined August"], \
        "is_current returns both, which is exactly why July must not use it"


async def test_a_driver_no_rule_reads_has_no_subjects_rather_than_an_error():
    tenant_id, _b, actor, _s = await _ctx()
    d = await _driver(tenant_id, actor, key="unused")
    async with SessionLocal() as s:
        assert await DR.subjects_for_month(s, tenant_id, d.id, JULY[0]) == []


async def test_a_committed_month_names_who_committed_it():
    tenant_id, biz, actor, _s = await _ctx()
    d = await _driver(tenant_id, actor)
    async with SessionLocal() as s:
        await DR.commit_month(s, tenant_id, actor, d.id, period=JULY[0], rows=[
            {"subject": "S. Ostermiller", "business_id": str(biz["the_forum"]),
             "value_kind": "share", "pct": "100"}])
    async with SessionLocal() as s:
        c = await DR.current_commit(s, tenant_id, d.id, JULY[0])
        out = DR.commit_out(c, await DR.values_for(s, tenant_id, c.id),
                            who=await DR.committer_name(s, tenant_id, c))
    assert out["committed_by"]["name"] == actor.name
    assert out["committed_at"], "a name and a date, which is what an audit trail is"
