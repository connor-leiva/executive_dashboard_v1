"""The exception queue, the preview, and the reconciliation (SPEC 5.6 to 5.8, Phase 7).

The gate is one claim: creating a rule from an exception removes every matching transaction from
the queue in one call, and the number removed equals the number the preview promised. If those
two disagree, the drawer lied about what saving would achieve, and a person who trusted it is
left hunting for rows that never left.

The queue is computed live, never stored, for the reason `useNextRun` gives about payment
proposals: a stored queue goes stale the moment somebody writes a rule, and a stale queue is
read as fact.
"""
import datetime as dt
from decimal import Decimal as D

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import (AccountPeriodBalance, AllocationContribution, AllocationRule, BookTxn,
                        Business, CoaMap, CoaSettings, StandardAccount, Tenant, User)
from app.seed import seed
from app.services import alloc_engine as E
from app.services import alloc_rules as AR
from app.services.coa import seed_standard_chart

MONTH = (dt.date(2026, 7, 1), dt.date(2026, 7, 31))
TODAY = dt.date(2026, 9, 30)
JULY = dt.date(2026, 7, 14)
TRANSPORT = ASGITransport(app=app)

# "1000" is an asset and "4010" is revenue: both must stay out of a shared-COST queue, and a
# queue that swept in revenue would invite somebody to allocate income by a staff-time split.
CHART = [
    ("6010", "Advertising:Meta", "Expense", "6040"),
    ("6011", "Advertising:Qualifi", "Expense", "6040"),
    ("6020", "Software", "Expense", "6050"),
    ("1000", "Operating Checking", "Bank", "1000"),
    ("4010", "Commission Income", "Income", "4010"),
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
    tenant_id, biz, _a, std = await _ctx()
    async with SessionLocal() as s:
        await s.execute(delete(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))
        await s.execute(delete(AllocationRule).where(AllocationRule.tenant_id == tenant_id))
        await s.execute(delete(BookTxn).where(BookTxn.tenant_id == tenant_id))
        await s.execute(delete(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == biz["springb"]))
        await s.execute(delete(CoaMap).where(CoaMap.business_id == biz["springb"]))
        cfg = (await s.execute(select(CoaSettings).where(
            CoaSettings.tenant_id == tenant_id))).scalars().first()
        if cfg is not None:
            cfg.alloc_pool_business_ids = None
        await s.commit()
    async with SessionLocal() as s:
        for qid, fqn, qtype, code in CHART:
            s.add(CoaMap(tenant_id=tenant_id, business_id=biz["springb"], qbo_account_id=qid,
                         qbo_account_name=fqn.split(":")[-1], qbo_account_fqn=fqn,
                         qbo_account_type=qtype, standard_account_id=std[code],
                         mapped_via="manual", is_ignored=False))
        await s.commit()
    yield


async def _pools(tenant_id, ids):
    async with SessionLocal() as s:
        cfg = await E.__dict__["exceptions"].__globals__["get_settings"](s, tenant_id) \
            if False else None
    from app.services.coa_balances import get_settings
    async with SessionLocal() as s:
        cfg = await get_settings(s, tenant_id)
        cfg.alloc_pool_business_ids = None if ids is None else [str(i) for i in ids]
        await s.commit()


async def _txn(s, tenant_id, business_id, *, payee, amount, qid="6011", memo="a memo",
               qbo_type="Purchase", multi=False, date=JULY, qbo_id=None):
    t = BookTxn(tenant_id=tenant_id, business_id=business_id, realm_id="r1",
                qbo_type=qbo_type, qbo_id=(qbo_id or f"{payee}-{amount}-{qid}-{date}"),
                txn_date=date, amount=D(amount), payee=payee, memo=memo,
                account_qbo_id=qid, flags=({"multi_line": True} if multi else None))
    s.add(t)
    await s.flush()
    return t.id


async def _q(tenant_id):
    async with SessionLocal() as s:
        return await E.exceptions(s, tenant_id, MONTH, today=TODAY)


# ── the queue (5.6) ───────────────────────────────────────────────────────────────────────

async def test_an_unconfigured_pool_list_says_so_rather_than_looking_empty():
    """"Nothing to do" and "nobody has said where to look" are different answers, and an empty
    list renders as the first."""
    tenant_id, biz, _a, _s = await _ctx()
    await _pools(tenant_id, None)
    got = await _q(tenant_id)
    assert got["configured"] is False and got["rows"] == []
    await _pools(tenant_id, [biz["springb"]])
    assert (await _q(tenant_id))["configured"] is True


async def test_only_expense_accounts_on_a_pool_entity_reach_the_queue():
    tenant_id, biz, _a, _s = await _ctx()
    await _pools(tenant_id, [biz["springb"]])
    async with SessionLocal() as s:
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="100.00", qid="6011")
        await _txn(s, tenant_id, biz["springb"], payee="Bank", amount="500.00", qid="1000")
        await _txn(s, tenant_id, biz["springb"], payee="A Client", amount="-900.00", qid="4010")
        await _txn(s, tenant_id, biz["the_forum"], payee="Qualifi", amount="700.00",
                   qid="6011", qbo_id="not-a-pool-entity")
        await s.commit()
    rows = (await _q(tenant_id))["rows"]
    assert [r["payee"] for r in rows] == ["Qualifi"], "no asset, no revenue, no other entity"
    assert rows[0]["amount"] == D("100.00")


async def test_a_vendor_rule_takes_its_transactions_out_and_an_account_rule_takes_its_accounts():
    tenant_id, biz, actor, _s = await _ctx()
    await _pools(tenant_id, [biz["springb"]])
    async with SessionLocal() as s:
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="100.00", qid="6011")
        await _txn(s, tenant_id, biz["springb"], payee="Mighty", amount="200.00", qid="6010")
        await _txn(s, tenant_id, biz["springb"], payee="Stan", amount="300.00", qid="6020")
        await s.commit()
    assert len((await _q(tenant_id))["rows"]) == 3

    async with SessionLocal() as s:
        await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="vendor", pattern="Qualifi", pool_name="Qualifi",
            source_business_id=biz["springb"], method="direct", effective_from=MONTH[0],
            reach="forward", rationale="Billed centrally.",
            targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    assert sorted(r["payee"] for r in (await _q(tenant_id))["rows"]) == ["Mighty", "Stan"]

    async with SessionLocal() as s:
        await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Advertising",
            pool_name="Advertising", source_business_id=biz["springb"], method="retained",
            effective_from=MONTH[0], reach="forward",
            rationale="The rest of advertising stays here.")
    rows = (await _q(tenant_id))["rows"]
    assert [r["payee"] for r in rows] == ["Stan"], "retained still counts as covered"


async def test_a_multi_line_transaction_always_appears_however_well_the_payee_matches():
    """D7. It is the one thing the engine refuses to claim by payee, so the queue is the only
    place it can surface. A vendor rule covering its payee must not hide it."""
    tenant_id, biz, actor, _s = await _ctx()
    await _pools(tenant_id, [biz["springb"]])
    async with SessionLocal() as s:
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="100.00")
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="4000.00", multi=True,
                   qbo_id="the-multi-line")
        await s.commit()
    async with SessionLocal() as s:
        await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="vendor", pattern="Qualifi", pool_name="Qualifi",
            source_business_id=biz["springb"], method="direct", effective_from=MONTH[0],
            reach="forward", rationale="Billed centrally.",
            targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])
    rows = (await _q(tenant_id))["rows"]
    assert len(rows) == 1
    assert rows[0]["amount"] == D("4000.00")
    assert "multi_line" in rows[0]["gaps"]


async def test_the_gap_codes_describe_the_row():
    tenant_id, biz, _a, _s = await _ctx()
    await _pools(tenant_id, [biz["springb"]])
    async with SessionLocal() as s:
        await _txn(s, tenant_id, biz["springb"], payee=None, amount="10.00", memo="has one",
                   qbo_id="no-payee")
        await _txn(s, tenant_id, biz["springb"], payee="Has Payee", amount="20.00", memo=None,
                   qbo_id="no-memo")
        for i in range(3):
            await _txn(s, tenant_id, biz["springb"], payee="Mighty Networks",
                       amount=f"3{i}.00", qbo_id=f"recurring-{i}")
        await s.commit()
    by_id = {r["txn_id"]: r for r in (await _q(tenant_id))["rows"]}
    gaps = {r["payee"]: r["gaps"] for r in by_id.values()}
    assert "no_payee" in gaps[None]
    assert "no_memo" in gaps["Has Payee"]
    assert "recurring" in gaps["Mighty Networks"], "three of the same payee in the window"
    assert "recurring" not in gaps["Has Payee"]


async def test_recurring_counts_the_window_not_the_leftovers():
    """Counted before the claimed rows are filtered out. A payee a rule already covers should
    not make its unclaimed siblings look like one-offs."""
    tenant_id, biz, actor, _s = await _ctx()
    await _pools(tenant_id, [biz["springb"]])
    async with SessionLocal() as s:
        for i in range(3):
            await _txn(s, tenant_id, biz["springb"], payee="Mighty Networks", amount="10.00",
                       qid="6010", qbo_id=f"m-{i}")
        await _txn(s, tenant_id, biz["springb"], payee="Mighty Networks", amount="99.00",
                   qid="6020", qbo_id="m-other-account")
        await s.commit()
    async with SessionLocal() as s:
        await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Advertising:Meta",
            pool_name="Meta", source_business_id=biz["springb"], method="retained",
            effective_from=MONTH[0], reach="forward", rationale="Meta stays here.")
    rows = (await _q(tenant_id))["rows"]
    assert len(rows) == 1, "three were covered by the account rule"
    assert "recurring" in rows[0]["gaps"], "and the fourth still knows it is a repeat"


# ── the gate: preview says what saving will do, and saving does it ────────────────────────

async def test_what_the_preview_promises_is_what_leaves_the_queue():
    """THE Phase 7 gate. The count the drawer showed before the save must equal the number of
    rows that actually leave, or it lied about what saving achieves."""
    tenant_id, biz, actor, _s = await _ctx()
    await _pools(tenant_id, [biz["springb"]])
    async with SessionLocal() as s:
        for i in range(4):
            await _txn(s, tenant_id, biz["springb"], payee="Qualifi, LLC", amount=f"10{i}.00",
                       qbo_id=f"q-{i}")
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi Inc.", amount="500.00",
                   qbo_id="q-other-spelling")
        await _txn(s, tenant_id, biz["springb"], payee="Qualifi", amount="900.00", multi=True,
                   qbo_id="q-multi")
        await _txn(s, tenant_id, biz["springb"], payee="Someone Else", amount="77.00",
                   qbo_id="not-qualifi")
        await s.commit()

    before = (await _q(tenant_id))["rows"]
    assert len(before) == 7

    draft = {"scope_kind": "vendor", "pattern": "Qualifi", "pool_name": "Qualifi",
             "source_business_id": str(biz["springb"]), "method": "direct",
             "effective_from": MONTH[0], "reach": "retro",
             "rationale": "Qualifi is the Forum's platform.",
             "targets": [{"business_id": str(biz["the_forum"]), "share": "1"}]}
    async with SessionLocal() as s:
        plan = await E.preview(s, tenant_id, draft, today=TODAY)
    promised = plan["claims"]["count"]
    assert promised == 5, "four spellings plus the other one, and NOT the multi-line"

    async with SessionLocal() as s:
        await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="vendor", pattern="Qualifi", pool_name="Qualifi",
            source_business_id=biz["springb"], method="direct", effective_from=MONTH[0],
            reach="retro", rationale="Qualifi is the Forum's platform.",
            targets=[{"business_id": str(biz["the_forum"]), "share": "1"}])

    after = (await _q(tenant_id))["rows"]
    assert len(before) - len(after) == promised, "the queue moved by exactly what was promised"
    assert {r["txn_id"] for r in before} - {r["txn_id"] for r in after} == \
        set(plan["claims"]["txn_ids"]), "and by exactly WHICH rows it promised"
    # The multi-line one is still there, which is why the promise was 5 and not 6.
    assert any("multi_line" in r["gaps"] for r in after)


async def test_the_preview_totals_what_each_entity_would_receive_and_what_stays():
    tenant_id, biz, _a, std = await _ctx()
    await _pools(tenant_id, [biz["springb"]])
    async with SessionLocal() as s:
        await _apb(s, tenant_id, biz["springb"], "6010", "1000.05")
        await s.commit()
    # A split MUST sum to 1: amendment 16 makes a short one a refusal, not a state. So the way
    # to leave money on the pool entity is to list it among the targets, which is 4.2's own
    # mechanism, and its share is what stays.
    draft = {"scope_kind": "account", "pattern": "Advertising", "pool_name": "Advertising",
             "source_business_id": str(biz["springb"]), "method": "split",
             "effective_from": MONTH[0], "reach": "forward",
             "rationale": "Run centrally.",
             "targets": [{"business_id": str(biz["the_forum"]), "share": "0.7"},
                         {"business_id": str(biz["becollective"]), "share": "0.2"},
                         {"business_id": str(biz["springb"]), "share": "0.1"}]}
    async with SessionLocal() as s:
        plan = await E.preview(s, tenant_id, draft, today=TODAY)
    assert plan["pool_amount"] == D("1000.05")
    assert [t["business_id"] for t in plan["targets"]] != [str(biz["springb"])], \
        "the pool entity is never a target row: its share stayed"
    out = sum(t["amount"] for t in plan["targets"])
    assert out == D("900.05")
    assert plan["retained"] == D("100.00"), "what did not leave, including the rounding"
    assert out + plan["retained"] == D("1000.05"), "every cent of the pool is accounted for"


async def test_a_split_that_does_not_add_up_is_refused_by_the_preview_too():
    """Amendment 16, through the preview. The drawer calls this on every keystroke, so it is
    where somebody finds out a split is short, long before they press save."""
    tenant_id, biz, _a, _s = await _ctx()
    draft = {"scope_kind": "account", "pattern": "Advertising", "pool_name": "Advertising",
             "source_business_id": str(biz["springb"]), "method": "split",
             "effective_from": MONTH[0], "reach": "forward", "rationale": "Run centrally.",
             "targets": [{"business_id": str(biz["the_forum"]), "share": "0.7"},
                         {"business_id": str(biz["becollective"]), "share": "0.2"}]}
    async with SessionLocal() as s:
        with pytest.raises(ValueError, match="add up to 1"):
            await E.preview(s, tenant_id, draft, today=TODAY)


async def _apb(s, tenant_id, business_id, qid, amount, month=MONTH):
    s.add(AccountPeriodBalance(
        tenant_id=tenant_id, business_id=business_id, period_start=month[0],
        period_end=month[1], qbo_account_id=qid, amount=D(amount), source="qbo_tb"))


async def test_the_preview_writes_nothing():
    tenant_id, biz, _a, _s = await _ctx()
    await _pools(tenant_id, [biz["springb"]])
    async with SessionLocal() as s:
        await _apb(s, tenant_id, biz["springb"], "6010", "5000.00")
        await s.commit()
    draft = {"scope_kind": "account", "pattern": "Advertising", "pool_name": "Advertising",
             "source_business_id": str(biz["springb"]), "method": "direct",
             "effective_from": MONTH[0], "reach": "forward", "rationale": "Because.",
             "targets": [{"business_id": str(biz["the_forum"]), "share": "1"}]}
    async with SessionLocal() as s:
        await E.preview(s, tenant_id, draft, today=TODAY)
    async with SessionLocal() as s:
        assert (await s.execute(select(AllocationRule).where(
            AllocationRule.tenant_id == tenant_id))).scalars().all() == []
        assert (await s.execute(select(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))).scalars().all() == []


async def test_the_preview_refuses_a_draft_the_service_would_refuse():
    tenant_id, biz, _a, _s = await _ctx()
    draft = {"scope_kind": "account", "pattern": "Advertising", "pool_name": "Advertising",
             "source_business_id": str(biz["springb"]), "method": "split",
             "effective_from": MONTH[0], "reach": "forward", "rationale": "   ",
             "targets": [{"business_id": str(biz["the_forum"]), "share": "0.7"}]}
    async with SessionLocal() as s:
        with pytest.raises(ValueError, match="in your own words"):
            await E.preview(s, tenant_id, draft, today=TODAY)


# ── reconciliation (5.8) ──────────────────────────────────────────────────────────────────

async def test_a_rule_that_resolved_nothing_shows_its_whole_pool_as_unresolved():
    """The Phase 7 gate's second half. A driven rule whose month was never committed resolves
    NOTHING while its pool still sits in the books, and a report showing only `resolved` would
    make it look finished."""
    tenant_id, biz, actor, std = await _ctx()
    await _pools(tenant_id, [biz["springb"]])
    async with SessionLocal() as s:
        from app.services import alloc_drivers as DR
        d = await DR.create_driver(s, tenant_id, actor, key="staff_time",
                                   label="Staff time", kind="split")
    async with SessionLocal() as s:
        await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Advertising",
            pool_name="Advertising", source_business_id=biz["springb"], method="driven",
            effective_from=MONTH[0], reach="forward",
            rationale="Split by how the team spent its time.",
            driver_id=d.id, driver_subject="S. Ostermiller")
    async with SessionLocal() as s:
        await _apb(s, tenant_id, biz["springb"], "6010", "8000.00")
        await s.commit()
    async with SessionLocal() as s:
        recon = await E.reconciliation(s, tenant_id, MONTH, today=TODAY)
    row = next(r for r in recon["rules"] if r["pool_name"] == "Advertising")
    assert row["pool_amount"] == D("8000.00")
    assert row["resolved_amount"] == D("0.00"), "nothing resolved: no commit for the month"
    assert row["unresolved_amount"] == D("8000.00"), "and the whole pool is still waiting"
    assert row["variance"] == D("-8000.00")
    assert [p["month"] for p in row["pending"]] == ["2026-07-01"]
    assert row["pending"][0]["tier"] == 3


async def test_a_retained_rule_reconciles_cleanly_because_staying_put_was_decided():
    tenant_id, biz, actor, std = await _ctx()
    await _pools(tenant_id, [biz["springb"]])
    async with SessionLocal() as s:
        await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Advertising",
            pool_name="Advertising", source_business_id=biz["springb"], method="retained",
            effective_from=MONTH[0], reach="forward", rationale="This one is ours.")
    async with SessionLocal() as s:
        await _apb(s, tenant_id, biz["springb"], "6010", "3000.00")
        await s.commit()
    async with SessionLocal() as s:
        recon = await E.reconciliation(s, tenant_id, MONTH, today=TODAY)
    row = recon["rules"][0]
    assert row["resolved_amount"] == D("3000.00")
    assert row["unresolved_amount"] == D("0.00")
    assert row["variance"] == D("0.00") and row["pending"] == []


# ── the routes ────────────────────────────────────────────────────────────────────────────

async def _token(c) -> str:
    r = await c.post("/api/v1/auth/login",
                     json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


async def test_the_routes_answer_and_the_pool_setting_round_trips():
    tenant_id, biz, _a, _s = await _ctx()
    await _pools(tenant_id, None)
    async with AsyncClient(transport=TRANSPORT, base_url="http://t") as c:
        h = {"Authorization": f"Bearer {await _token(c)}"}
        r = await c.get("/api/v1/books/allocations/exceptions",
                        params={"period_start": "2026-07-01", "period_end": "2026-07-31"},
                        headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["configured"] is False

        r = await c.put("/api/v1/books/coa/settings/alloc-pools", headers=h,
                        json={"business_ids": [str(biz["springb"])]})
        assert r.status_code == 200, r.text
        assert r.json()["pool_business_ids"] == [str(biz["springb"])]

        r = await c.get("/api/v1/books/allocations/exceptions",
                        params={"period_start": "2026-07-01", "period_end": "2026-07-31"},
                        headers=h)
        assert r.json()["configured"] is True and r.json()["total"] == 0.0

        r = await c.get("/api/v1/books/allocations/recon",
                        params={"period_start": "2026-07-01", "period_end": "2026-07-31"},
                        headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["rules"] == [] and "check" in r.json()

        bad = await c.put("/api/v1/books/coa/settings/alloc-pools", headers=h,
                          json={"business_ids": ["0f3b1f4e-0000-4000-8000-000000000000"]})
        assert bad.status_code == 400 and "not in this workspace" in bad.json()["detail"]


async def test_the_preview_route_returns_dollars_as_numbers():
    tenant_id, biz, _a, _s = await _ctx()
    await _pools(tenant_id, [biz["springb"]])
    async with SessionLocal() as s:
        await _apb(s, tenant_id, biz["springb"], "6010", "1000.05")
        await s.commit()
    async with AsyncClient(transport=TRANSPORT, base_url="http://t") as c:
        h = {"Authorization": f"Bearer {await _token(c)}"}
        r = await c.post("/api/v1/books/allocations/preview", headers=h, json={
            "scope_kind": "account", "pattern": "Advertising",
            "source_business_id": str(biz["springb"]), "method": "split",
            "effective_from": "2026-07-01", "reach": "forward", "rationale": "Run centrally.",
            "targets": [{"business_id": str(biz["the_forum"]), "share": "0.7"},
                        {"business_id": str(biz["becollective"]), "share": "0.3"}]})
        assert r.status_code == 200, r.text
        body = r.json()
    assert body["pool_amount"] == 1000.05
    assert isinstance(body["pool_amount"], float), "a number, never a formatted string"
    assert sum(t["amount"] for t in body["targets"]) == 1000.05
    assert body["retained"] == 0.0
    assert body["reach"] == "forward"


# ── gaps the mutation harness found ───────────────────────────────────────────────────────

async def test_another_entitys_cost_stays_out_even_when_it_shares_the_chart():
    """Dropping the pool-entity filter survived the earlier test, because the other entity had
    no `CoaMap` rows and the inner join to the chart removed its transaction whether the filter
    was there or not. It needs the SAME account mapped on both entities.

    This is the second time this exact blindness has appeared: Phase 4's vendor-pool test had
    it too. The pattern to remember is that a fixture entity with no chart cannot test anything
    that joins through the chart.
    """
    tenant_id, biz, _a, std = await _ctx()
    await _pools(tenant_id, [biz["springb"]])
    async with SessionLocal() as s:
        s.add(CoaMap(tenant_id=tenant_id, business_id=biz["the_forum"],
                     qbo_account_id="6011", qbo_account_name="Qualifi",
                     qbo_account_fqn="Advertising:Qualifi", qbo_account_type="Expense",
                     standard_account_id=std["6040"], mapped_via="manual", is_ignored=False))
        await _txn(s, tenant_id, biz["springb"], payee="Ours", amount="100.00")
        await _txn(s, tenant_id, biz["the_forum"], payee="Theirs", amount="8000.00",
                   qbo_id="forum-own-cost")
        await s.commit()
    rows = (await _q(tenant_id))["rows"]
    assert [r["payee"] for r in rows] == ["Ours"], "the forum's own cost is not Spring B's queue"
    assert (await _q(tenant_id))["total"] == D("100.00")


async def test_a_preview_covers_the_months_from_its_effective_date_onward():
    """Nothing asserted which months a preview spans, so a version that ignored
    `effective_from` entirely and claimed the whole fiscal year survived. Reach does not widen
    the set either: section 4.2 keeps `reach` for the audit trail and the restatement warning,
    and `effective_version` resolves on `effective_from` alone."""
    tenant_id, biz, _a, _s = await _ctx()
    await _pools(tenant_id, [biz["springb"]])
    base = {"scope_kind": "account", "pattern": "Advertising", "pool_name": "Advertising",
            "source_business_id": str(biz["springb"]), "method": "retained",
            "rationale": "This one stays here."}
    async with SessionLocal() as s:
        july = await E.preview(s, tenant_id, {**base, "effective_from": dt.date(2026, 7, 1),
                                              "reach": "forward"}, today=TODAY)
        march = await E.preview(s, tenant_id, {**base, "effective_from": dt.date(2026, 3, 1),
                                               "reach": "forward"}, today=TODAY)
        retro = await E.preview(s, tenant_id, {**base, "effective_from": dt.date(2026, 7, 1),
                                               "reach": "retro"}, today=TODAY)
    assert july["months"][0] == "2026-07-01", "it starts where it says it starts"
    assert march["months"][0] == "2026-03-01"
    assert len(march["months"]) > len(july["months"]), "an earlier date covers more months"
    assert "2026-01-01" not in july["months"], "and not the whole year"
    assert retro["months"] == july["months"], \
        "reach is recorded, not a second resolution rule"
