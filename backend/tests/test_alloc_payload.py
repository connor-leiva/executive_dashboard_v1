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
    assert isinstance(body["rules"][0]["pool_amount"], float), "a number, never a string"
    assert body["rules"][0]["pool_amount"] == 1000.05
    assert body["meta"]["tiers"][2]["label"] == "Driven"
    assert "observed" in body, "the older shape is carried, under its own key"
    assert isinstance(body["observed"], list)
