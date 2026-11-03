"""What the Allocations page reads (SPEC 6, Phase 9).

The claim worth defending: Rules and Reconciliation never disagree. They show the same pool
figure for the same rule because `rules_payload` calls `reconciliation` rather than computing
pools a second time. Two computations would both look authoritative, neither would be obviously
wrong, and whichever view somebody opened first would win the argument.

`to_partners` is read from the contributions the engine WROTE, not recomputed from the version's
shares. A version says what should happen; the rows say what did. Where a month is pending those
differ, and that difference is the thing a reader needs.
"""
import datetime as dt
from decimal import Decimal as D

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import (AccountPeriodBalance, AllocationContribution, AllocationRule, Business,
                        CoaMap, StandardAccount, Tenant, User)
from app.seed import seed
from app.services import alloc_engine as E
from app.services import alloc_rules as AR
from app.services.coa import seed_standard_chart

MONTH = (dt.date(2026, 7, 1), dt.date(2026, 7, 31))
TODAY = dt.date(2026, 9, 30)
TRANSPORT = ASGITransport(app=app)

CHART = [("6010", "Advertising:Meta", "Expense", "6040"),
         ("6020", "Software", "Expense", "6050")]


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
        s.add(AccountPeriodBalance(
            tenant_id=tenant_id, business_id=biz["springb"], period_start=MONTH[0],
            period_end=MONTH[1], qbo_account_id="6010", amount=D("1000.05"), source="qbo_tb"))
        await s.commit()
    yield


async def _split_rule(tenant_id, biz, actor):
    async with SessionLocal() as s:
        return await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Advertising",
            pool_name="Advertising", source_business_id=biz["springb"], method="split",
            effective_from=MONTH[0], reach="forward",
            rationale="Run centrally on one card and split by programme.",
            targets=[{"business_id": str(biz["the_forum"]), "share": "0.7"},
                     {"business_id": str(biz["becollective"]), "share": "0.3"}])


async def test_the_payload_carries_identity_versions_and_the_vocabulary():
    tenant_id, biz, actor, _s = await _ctx()
    rule, v1 = await _split_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        v2 = await AR.supersede(
            s, tenant_id, actor, rule.id, method="split", effective_from=MONTH[0],
            reach="from_date", rationale="Spring B took a share.",
            targets=[{"business_id": str(biz["the_forum"]), "share": "0.5"},
                     {"business_id": str(biz["becollective"]), "share": "0.3"},
                     {"business_id": str(biz["springb"]), "share": "0.2"}])
    async with SessionLocal() as s:
        out = await E.rules_payload(s, tenant_id, MONTH, today=TODAY)

    assert [t["tier"] for t in out["meta"]["tiers"]] == [1, 2, 3, 4]
    assert [t["code"] for t in out["meta"]["tiers"]] == ["T1", "T2", "T3", "T4"]
    assert all(t["gloss"] for t in out["meta"]["tiers"]), "every tier explains itself"

    # Entity names and accents come from the payload, never derived from a business key (F11).
    assert {b["name"] for b in out["businesses"]} >= {"The Forum", "beCollective"}
    assert all("accent" in b for b in out["businesses"])

    r = out["rules"][0]
    assert r["pool_name"] == "Advertising" and r["scope_kind"] == "account"
    assert r["current"]["version"] == 2, "the current version, not the first"
    assert [v["version"] for v in r["versions"]] == [2, 1], "newest first (7.3)"
    assert r["versions"][1]["is_current"] is False
    assert "Spring B took a share" in r["current"]["rationale"]


async def test_rules_and_reconciliation_never_disagree_about_a_pool():
    """One source, two views. Two computations would both look authoritative."""
    tenant_id, biz, actor, _s = await _ctx()
    await _split_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        payload = await E.rules_payload(s, tenant_id, MONTH, today=TODAY)
        recon = await E.reconciliation(s, tenant_id, MONTH, today=TODAY)
    by_id = {r["rule_id"]: r for r in recon["rules"]}
    assert payload["rules"], "nothing to compare"
    for r in payload["rules"]:
        assert r["pool_amount"] == by_id[r["id"]]["pool_amount"]
        assert r["pending"] == by_id[r["id"]]["pending"]


async def test_to_partners_is_what_left_not_what_the_shares_say():
    """A version's shares are an intention; the rows are the fact. With nothing derived yet,
    the whole pool is retained even though the split says 70/30 should have gone out."""
    tenant_id, biz, actor, std = await _ctx()
    rule, v = await _split_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        before = await E.rules_payload(s, tenant_id, MONTH, today=TODAY)
    r = before["rules"][0]
    assert r["pool_amount"] == D("1000.05")
    assert r["to_partners"] == D("0.00"), "nothing has been derived, so nothing has left"
    assert r["retained"] == D("1000.05")

    async with SessionLocal() as s:
        await E.derive_applied(s, tenant_id, MONTH, rules_and_versions=[(rule, v)],
                               pools_by_rule={rule.id: {std["6040"]: D("1000.05")}},
                               today=TODAY)
        await s.commit()
    async with SessionLocal() as s:
        after = await E.rules_payload(s, tenant_id, MONTH, today=TODAY)
    r = after["rules"][0]
    assert r["to_partners"] == D("1000.05"), "now it has left"
    assert r["retained"] == D("0.00")
    assert r["to_partners"] + r["retained"] == r["pool_amount"], "and it still adds up"


async def test_a_rule_with_no_current_version_does_not_break_the_payload():
    """Defensive rather than reachable: `create_applied_rule` writes v1 in the same act, so a
    versionless rule should not exist. If one ever did, the page must still render."""
    tenant_id, biz, _a, _s = await _ctx()
    async with SessionLocal() as s:
        s.add(AllocationRule(tenant_id=tenant_id, mode="applied", scope_kind="account",
                             source_business_id=biz["springb"], pattern="Orphan",
                             pool_name="Orphan", is_active=True))
        await s.commit()
    async with SessionLocal() as s:
        out = await E.rules_payload(s, tenant_id, MONTH, today=TODAY)
    orphan = next(r for r in out["rules"] if r["pool_name"] == "Orphan")
    assert orphan["current"] is None and orphan["versions"] == []
    assert orphan["pool_amount"] == D("0.00"), "it claims nothing, so it pools nothing"


async def test_the_route_returns_numbers_and_keeps_the_observed_list_beside_them():
    tenant_id, biz, actor, _s = await _ctx()
    await _split_rule(tenant_id, biz, actor)
    async with AsyncClient(transport=TRANSPORT, base_url="http://t") as c:
        tok = (await c.post("/api/v1/auth/login",
                            json={"email": "spring@springb.com",
                                  "password": "springtime"})).json()["token"]
        r = await c.get("/api/v1/books/allocations/rules",
                        params={"period_start": "2026-07-01", "period_end": "2026-07-31"},
                        headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    body = r.json()
    # `rules` is the route's OBSERVED list and keeps the shape it shipped with; the applied
    # side rides beside it. Swapping those two keys broke `test_coa_alloc.py`, which is how
    # this contract got pinned: a test is a consumer, and the grep that "proved" there were
    # none had covered frontend/src and app/ but not tests/.
    assert isinstance(body["applied"][0]["pool_amount"], float), "a number, never a string"
    assert body["applied"][0]["pool_amount"] == 1000.05
    assert body["meta"]["tiers"][2]["label"] == "Driven"
    assert isinstance(body["rules"], list), "the observed list, under the key it always had"


# ── what must NOT count as money that left (added after a mutation run) ───────────────────
#
# `rules_payload` reads `to_partners` with ONE query carrying four filters, and a mutation run
# dropped each in turn while all five tests above kept passing. Every one of the four is
# reachable in production, so each got a case here rather than a note.
CONTAMINANT = {
    # An OBSERVED row for the same rule. The observed path is the one already in production and
    # its rows carry a rule_id too, so without the booking filter this page would report
    # somebody else's bookkeeping as this rule's outbound money.
    "observed_booking": dict(booking="observed", grain="month", period=MONTH),
    # A WINDOW row beside the month rows. `derive_applied` writes month grain and the observed
    # path writes window grain; counting both reports the same money twice.
    "window_grain": dict(booking="applied", grain="window", period=MONTH),
    # A month that starts after the window ends. Without the end filter a year-to-date figure
    # absorbs a month that has not happened inside it.
    "month_after_window": dict(booking="applied", grain="month",
                               period=(dt.date(2026, 8, 1), dt.date(2026, 8, 31))),
    # A month that ends before the window starts.
    "month_before_window": dict(booking="applied", grain="month",
                                period=(dt.date(2026, 6, 1), dt.date(2026, 6, 30))),
}


@pytest.mark.parametrize("which", sorted(CONTAMINANT))
async def test_only_applied_month_rows_inside_the_window_count_as_money_that_left(which):
    tenant_id, biz, actor, std = await _ctx()
    rule, v = await _split_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        await E.derive_applied(s, tenant_id, MONTH, rules_and_versions=[(rule, v)],
                               pools_by_rule={rule.id: {std["6040"]: D("1000.05")}},
                               today=TODAY)
        await s.commit()
    async with SessionLocal() as s:
        clean = (await E.rules_payload(s, tenant_id, MONTH, today=TODAY))["rules"][0]
    assert clean["to_partners"] == D("1000.05"), "the derived split, before any contaminant"

    spec = CONTAMINANT[which]
    async with SessionLocal() as s:
        s.add(AllocationContribution(
            tenant_id=tenant_id, period_start=spec["period"][0], period_end=spec["period"][1],
            source_business_id=biz["springb"], target_business_id=biz["the_forum"],
            standard_account_id=std["6040"], amount=D("7.77"), rule_id=rule.id,
            booking=spec["booking"], grain=spec["grain"], pool_name="Advertising",
            basis="a row that must not be counted"))
        await s.commit()
    async with SessionLocal() as s:
        after = (await E.rules_payload(s, tenant_id, MONTH, today=TODAY))["rules"][0]

    assert after["to_partners"] == D("1000.05"), f"{which} leaked into to_partners"
    assert after["retained"] == D("0.00"), f"{which} moved retained off the pool"
    assert after["to_partners"] + after["retained"] == after["pool_amount"]


async def test_an_observed_rule_never_appears_on_the_applied_page():
    """D1 is the module's premise: observed rules and their contributions are untouched, so
    nothing already on the Statement can move. The page listing one would break that promise in
    the most visible way, by inviting somebody to supersede a rule this module does not own."""
    tenant_id, biz, actor, _s = await _ctx()
    await _split_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        s.add(AllocationRule(tenant_id=tenant_id, mode="observed", scope_kind="account",
                             source_business_id=biz["springb"], pattern="Legacy",
                             pool_name="A rule this module does not own", is_active=True))
        await s.commit()
    async with SessionLocal() as s:
        out = await E.rules_payload(s, tenant_id, MONTH, today=TODAY)
    names = [r["pool_name"] for r in out["rules"]]
    assert "Advertising" in names, "the applied rule is still listed"
    assert "A rule this module does not own" not in names, names


# ── two defects a review found that mutation could not (the report must match the write) ──

async def test_a_general_and_a_specific_rule_do_not_both_claim_the_same_account():
    """LONGEST PREFIX, in the REPORT and not only in the derivation (5.4).

    `account_pools` arbitrates among the rules it is HANDED. `reconciliation` used to ask for
    one rule's pool at a time, so it could not arbitrate at all: "Advertising" and
    "Advertising:Meta" each claimed Meta's spend in full and the same cost appeared in two
    pools. The page's "In the books" column then overstated, and disagreed with what
    `derive_all` actually wrote, while Rules and Reconciliation happily agreed with each other.
    """
    tenant_id, biz, actor, std = await _ctx()
    async with SessionLocal() as s:
        # 6010 is Advertising:Meta, mapped to 6040, carrying 1000.05 for the month.
        await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Advertising",
            pool_name="Advertising, everything else", source_business_id=biz["springb"],
            method="retained", effective_from=MONTH[0], reach="forward",
            rationale="Whatever Meta does not claim stays here.", targets=[])
    async with SessionLocal() as s:
        await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Advertising:Meta",
            pool_name="Meta only", source_business_id=biz["springb"], method="split",
            effective_from=MONTH[0], reach="forward",
            rationale="Meta is split by programme.",
            targets=[{"business_id": str(biz["the_forum"]), "share": "1.0"}])

    async with SessionLocal() as s:
        recon = await E.reconciliation(s, tenant_id, MONTH, today=TODAY)
    pools = {r["pool_name"]: r["pool_amount"] for r in recon["rules"]}
    assert pools["Meta only"] == D("1000.05"), "the specific rule takes the account"
    assert pools["Advertising, everything else"] == D("0.00"), "the general one does not"
    assert sum(pools.values()) == D("1000.05"), "the cost is in the books exactly once"


async def test_a_window_outside_the_fiscal_year_does_not_report_a_negative_retained():
    """`pool_amount` comes from `allocation_months`, which runs from the FISCAL YEAR start to
    the month containing today, so a window outside it has no months and pools nothing. The
    outbound total was bounded by the raw window instead, so it still found last year's
    contributions: a zero pool, a real outflow, and `retained = pool - went` going NEGATIVE.
    Both figures read the same month list now, so they cannot describe different sets."""
    tenant_id, biz, actor, std = await _ctx()
    rule, v = await _split_rule(tenant_id, biz, actor)
    old_month = (dt.date(2025, 7, 1), dt.date(2025, 7, 31))
    async with SessionLocal() as s:
        s.add(AllocationContribution(
            tenant_id=tenant_id, period_start=old_month[0], period_end=old_month[1],
            source_business_id=biz["springb"], target_business_id=biz["the_forum"],
            standard_account_id=std["6040"], amount=D("900.00"), rule_id=rule.id,
            booking="applied", grain="month", pool_name="Advertising",
            basis="last fiscal year"))
        await s.commit()

    async with SessionLocal() as s:
        out = await E.rules_payload(s, tenant_id, old_month, today=TODAY)
    r = out["rules"][0]
    assert out["months"] == [], "today is 2026, so 2025 holds no allocation months"
    assert r["pool_amount"] == D("0.00")
    assert r["to_partners"] == D("0.00"), "and the outflow is read off the same empty set"
    assert r["retained"] == D("0.00"), "never negative"
    assert r["to_partners"] + r["retained"] == r["pool_amount"]
