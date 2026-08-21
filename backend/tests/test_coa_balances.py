"""The mapped statement — Phase 3 (SPEC-coa-mapping-provenance 5.2 to 5.4).

The three things worth defending:

- One sign convention. A sign bug renders perfectly and totals wrongly, which is the most
  expensive kind of error this layer can make.
- The guard refuses. An unmapped account WITH activity blocks the render; a dead one does not,
  or 130 of the portfolio's 693 accounts would block every statement forever.
- The tie-out refuses. If the mapped total does not equal the trial balance, the view is wrong,
  and a wrong financial view that renders is worse than an error message.
"""
import datetime as dt
from decimal import Decimal

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import delete, select

from app.main import app
from app.seed import seed
from app.db import SessionLocal
from app.models import (AccountPeriodBalance, Business, CoaMap, CoaSettings, StandardAccount,
                        Tenant)
from app.services import coa_balances as CB
from app.services import coa_map
from app.services.coa import seed_standard_chart

TRANSPORT = ASGITransport(app=app)
PERIOD = (dt.date(2026, 7, 1), dt.date(2026, 7, 31))


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        await seed_standard_chart(s, t.id)


async def _ids():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        biz = {b.key: b.id for b in (await s.execute(
            select(Business).where(Business.tenant_id == t.id))).scalars()}
        return t.id, biz


async def _std(s, tenant_id, code):
    return (await s.execute(select(StandardAccount).where(
        StandardAccount.tenant_id == tenant_id, StandardAccount.code == code))).scalar_one()


# A miniature entity that balances: revenue 100,000 credit, cost 40,000 and opex 25,000 debit,
# cash 35,000 debit. Debit-positive, sums to zero, exactly as a trial balance must.
BOOKS = [
    ("100", "Operating Checking", "Bank", "1000", Decimal("35000.00")),
    ("200", "Commission Income", "Income", "4010", Decimal("-100000.00")),
    ("300", "Agent Splits", "Cost of Goods Sold", "5010", Decimal("40000.00")),
    ("400", "Office Rent", "Expense", "7010", Decimal("15000.00")),
    ("410", "Marketing", "Expense", "6040", Decimal("10000.00")),
]


async def _load(tenant_id, business_id, rows=BOOKS, period=PERIOD, mapped=True):
    """Put an entity's chart and trial balance in place. Returns nothing; the tests read it
    back through the real service so nothing is asserted against a shortcut."""
    ps, pe = period
    async with SessionLocal() as s:
        await s.execute(delete(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == business_id))
        await s.execute(delete(CoaMap).where(CoaMap.business_id == business_id))
        await s.commit()
    async with SessionLocal() as s:
        for qid, name, qtype, code, amount in rows:
            std = await _std(s, tenant_id, code) if (code and mapped) else None
            s.add(CoaMap(tenant_id=tenant_id, business_id=business_id, qbo_account_id=qid,
                         qbo_account_name=name, qbo_account_fqn=name, qbo_account_type=qtype,
                         standard_account_id=(std.id if std else None),
                         mapped_via=("manual" if std else None)))
            s.add(AccountPeriodBalance(
                tenant_id=tenant_id, business_id=business_id, period_start=ps, period_end=pe,
                qbo_account_id=qid, amount=amount, source="qbo_tb"))
        await s.commit()


# ── sign normalization (5.2) ──────────────────────────────────────────────────────────────

def test_a_trial_balance_is_already_debit_positive():
    """parse_trial_balance returns debit - credit, so qbo_tb needs no adjustment. The function
    still exists as the ONE place that claim is written down."""
    assert CB.normalize_sign(Decimal("1500.00"), "qbo_tb") == Decimal("1500.00")
    assert CB.normalize_sign(-1500, "qbo_tb") == Decimal("-1500")
    assert CB.normalize_sign("42.50", "qbo_tb") == Decimal("42.50")


def test_a_pl_report_needs_its_credit_side_flipped():
    """QBO's P&L presents revenue and expense both as positive magnitudes."""
    assert CB.normalize_sign(100, "qbo_pl", section="revenue") == Decimal("-100")
    assert CB.normalize_sign(100, "qbo_pl", section="other_income") == Decimal("-100")
    assert CB.normalize_sign(100, "qbo_pl", section="opex") == Decimal("100")
    assert CB.normalize_sign(100, "qbo_pl", section="cogs") == Decimal("100")


def test_an_unknown_source_raises_rather_than_guessing():
    """Silently trusting an unrecognised convention is the exact failure this prevents."""
    with pytest.raises(ValueError):
        CB.normalize_sign(100, "some_new_report")
    with pytest.raises(ValueError):
        CB.normalize_sign(100, "qbo_pl")            # no section: which way is up?


def test_display_is_the_only_place_the_sign_turns_back():
    """Revenue stored as -100 reads as 100; an expense is untouched. Round-trips exactly."""
    stored = CB.normalize_sign(100, "qbo_pl", section="revenue")
    assert CB.for_display(stored, "revenue") == Decimal("100")
    assert CB.for_display(Decimal("100"), "opex") == Decimal("100")
    for section in ("revenue", "other_income", "liability", "equity", "opex", "cogs", "asset"):
        v = Decimal("1234.56")
        assert CB.for_display(CB.for_display(v, section), section) == v


# ── the guard (5.3) ───────────────────────────────────────────────────────────────────────

async def test_a_clean_entity_renders_and_ties():
    tenant_id, biz = await _ids()
    await _load(tenant_id, biz["ulrg"])
    async with SessionLocal() as s:
        st = await CB.build_mapped_statement(s, tenant_id, biz["ulrg"], PERIOD)
    assert st["tie_out"]["status"] == "tied" and st["tie_out"]["delta"] == 0.0
    assert st["tie_out"]["excluded"] == 0.0
    t = st["totals"]
    # Revenue reads positive, cost reads positive, and the calculated lines follow.
    assert t["net_revenue"] == 100000.0
    assert t["cost_of_sale"] == 40000.0
    assert t["gross_profit"] == 60000.0
    assert t["operating_expenses"] == 25000.0
    assert t["net_operating_income"] == 35000.0
    assert t["net_income"] == 35000.0


async def test_an_unmapped_account_with_activity_blocks_the_render():
    tenant_id, biz = await _ids()
    rows = BOOKS[:-1] + [("410", "Marketing", "Expense", None, Decimal("10000.00"))]
    await _load(tenant_id, biz["ulrg"], rows)
    async with SessionLocal() as s:
        with pytest.raises(CB.UnmappedAccountsError) as e:
            await CB.build_mapped_statement(s, tenant_id, biz["ulrg"], PERIOD)
    assert len(e.value.accounts) == 1
    assert e.value.accounts[0]["name"] == "Marketing"
    assert e.value.accounts[0]["reason"] == "unmapped"
    assert "10,000.00" in str(e.value), "the error says how much is unaccounted for"


async def test_a_dead_unmapped_account_does_not_block():
    """130 of the portfolio's 693 accounts have no activity. Blocking on them would mean no
    statement ever renders."""
    tenant_id, biz = await _ids()
    rows = BOOKS + [("999", "Dead Clearing Account", "Expense", None, Decimal("0.00"))]
    await _load(tenant_id, biz["ulrg"], rows)
    async with SessionLocal() as s:
        st = await CB.build_mapped_statement(s, tenant_id, biz["ulrg"], PERIOD)
    assert st["tie_out"]["status"] == "tied"


async def test_ignoring_a_live_account_blocks_just_like_unmapping_it():
    """Ignoring is for dead accounts. Ignoring a live one removes real money from the
    statement — the same hole under a friendlier name."""
    tenant_id, biz = await _ids()
    await _load(tenant_id, biz["ulrg"])
    async with SessionLocal() as s:
        await coa_map.set_ignored(s, tenant_id, None, biz["ulrg"], ["410"], "not sure yet")
        with pytest.raises(CB.UnmappedAccountsError) as e:
            await CB.build_mapped_statement(s, tenant_id, biz["ulrg"], PERIOD)
    assert e.value.accounts[0]["reason"].startswith("ignored:")


async def test_the_guard_can_be_turned_off_and_then_the_hole_is_reported():
    """With block_render_on_unmapped false the statement renders, the tie-out still passes —
    and `excluded` is what stops the missing money from being invisible."""
    tenant_id, biz = await _ids()
    rows = BOOKS[:-1] + [("410", "Marketing", "Expense", None, Decimal("10000.00"))]
    await _load(tenant_id, biz["ulrg"], rows)
    async with SessionLocal() as s:
        cfg = await CB.get_settings(s, tenant_id)
        cfg.block_render_on_unmapped = False
        await s.commit()
    try:
        async with SessionLocal() as s:
            st = await CB.build_mapped_statement(s, tenant_id, biz["ulrg"], PERIOD)
        assert st["tie_out"]["status"] == "tied"
        assert st["tie_out"]["excluded"] == 10000.0, "the hole is visible in the payload"
        assert st["totals"]["operating_expenses"] == 15000.0
    finally:
        async with SessionLocal() as s:
            cfg = await CB.get_settings(s, tenant_id)
            cfg.block_render_on_unmapped = True
            await s.commit()


# ── the tie-out (5.4) ─────────────────────────────────────────────────────────────────────

async def test_moving_a_balance_does_not_break_the_tie_out_and_should_not():
    """Worth stating explicitly, because it is the invariant's most misunderstood property.
    The tie-out compares the MAPPED total against the TRIAL BALANCE total. Change a balance
    and both move together, so it still ties — as it should. This check proves the rollup is
    faithful to QuickBooks; it can never tell you QuickBooks is wrong."""
    tenant_id, biz = await _ids()
    await _load(tenant_id, biz["springb"])
    async with SessionLocal() as s:
        row = (await s.execute(select(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == biz["springb"],
            AccountPeriodBalance.qbo_account_id == "400"))).scalar_one()
        row.amount = Decimal("15500.00")             # the books are now wrong by 500
        await s.commit()
    async with SessionLocal() as s:
        st = await CB.build_mapped_statement(s, tenant_id, biz["springb"], PERIOD)
    assert st["tie_out"]["delta"] == 0.0
    assert st["totals"]["operating_expenses"] == 25500.0, "the wrong number renders faithfully"
    await _load(tenant_id, biz["springb"])


async def test_a_line_that_would_vanish_from_the_statement_raises():
    """The failure the tie-out alone would miss: an amount counted in the total but dropped
    from every section, so the statement looks tied and is missing a line. Written straight to
    the database, because the service layer refuses to create this."""
    tenant_id, biz = await _ids()
    await _load(tenant_id, biz["springb"])
    async with SessionLocal() as s:
        other = Tenant(slug="ghost-tenant", name="Ghost")
        s.add(other)
        await s.commit()
        await seed_standard_chart(s, other.id)
        stray = await _std(s, other.id, "7010")           # a real account, wrong tenant
        m = (await s.execute(select(CoaMap).where(
            CoaMap.business_id == biz["springb"], CoaMap.qbo_account_id == "400"))).scalar_one()
        m.standard_account_id = stray.id
        await s.commit()
    async with SessionLocal() as s:
        with pytest.raises(CB.TieOutError) as e:
            await CB.build_mapped_statement(s, tenant_id, biz["springb"], PERIOD)
    assert e.value.orphaned and "missing those lines" in str(e.value)
    await _load(tenant_id, biz["springb"])


async def test_the_tie_out_tolerance_is_read_from_settings_not_hardcoded():
    tenant_id, biz = await _ids()
    await _load(tenant_id, biz["sympli"])
    async with SessionLocal() as s:
        cfg = await CB.get_settings(s, tenant_id)
        assert cfg.tie_out_tolerance == Decimal("0.01")
        st = await CB.build_mapped_statement(s, tenant_id, biz["sympli"], PERIOD)
    assert st["tie_out"]["tolerance"] == 0.01


async def test_several_qbo_accounts_merge_onto_one_standard_line():
    """The merge IS the product: five entities' charts become one shape. Every contributing
    account is listed on the line so the number can be taken apart again."""
    tenant_id, biz = await _ids()
    rows = [
        ("100", "Operating Checking", "Bank", "1000", Decimal("35000.00")),
        ("200", "Commission Income", "Income", "4010", Decimal("-100000.00")),
        ("300", "Agent Splits", "Cost of Goods Sold", "5010", Decimal("40000.00")),
        ("400", "Rent - Main", "Expense", "7010", Decimal("15000.00")),
        ("401", "Rent - Annex", "Expense", "7010", Decimal("10000.00")),
    ]
    await _load(tenant_id, biz["ulrg"], rows)
    async with SessionLocal() as s:
        st = await CB.build_mapped_statement(s, tenant_id, biz["ulrg"], PERIOD)
    opex = next(sec for sec in st["sections"] if sec["key"] == "opex")
    rent = next(l for b in opex["buckets"] for l in b["lines"] if l["code"] == "7010")
    assert rent["amount"] == 25000.0
    assert {x["name"] for x in rent["sources"]} == {"Rent - Main", "Rent - Annex"}
    assert st["tie_out"]["delta"] == 0.0


async def test_the_report_says_what_tying_out_does_not_prove():
    """Section 9: a clean-looking statement can make the underlying mess LESS visible. That
    has to be in the payload, not only in the spec."""
    tenant_id, biz = await _ids()
    await _load(tenant_id, biz["ulrg"])
    async with SessionLocal() as s:
        st = await CB.build_mapped_statement(s, tenant_id, biz["ulrg"], PERIOD)
    assert "nothing about whether the books are right" in st["caveat"]


async def test_tie_out_report_covers_every_entity_and_never_raises():
    tenant_id, biz = await _ids()
    await _load(tenant_id, biz["ulrg"])
    await _load(tenant_id, biz["springb"])
    rows = BOOKS[:-1] + [("410", "Marketing", "Expense", None, Decimal("10000.00"))]
    await _load(tenant_id, biz["sympli"], rows)
    async with SessionLocal() as s:
        rep = await CB.tie_out_report(s, tenant_id, PERIOD)
    by = {e["business"]: e for e in rep["entities"]}
    assert by["ulrg"]["status"] == "tied"
    assert by["springb"]["status"] == "tied"
    assert by["sympli"]["status"] == "unmapped", "a broken entity is reported, not raised"
    assert rep["gate"]["passed"] is False
    assert by["sympli"]["worst"][0]["amount"] == 10000.0
    await _load(tenant_id, biz["sympli"])
    async with SessionLocal() as s:
        rep = await CB.tie_out_report(s, tenant_id, PERIOD)
    assert rep["gate"]["passed"] is True and rep["gate"]["tied"] == rep["gate"]["checked"]


# ── the API ───────────────────────────────────────────────────────────────────────────────

async def _owner_token():
    async with AsyncClient(transport=TRANSPORT, base_url="http://testserver") as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


async def test_the_api_refuses_with_something_actionable():
    """Not a partial statement, and not a bare 500 — a 409 carrying the offending accounts, so
    the frontend can list them with a link to the mapping screen."""
    tenant_id, biz = await _ids()
    rows = BOOKS[:-1] + [("410", "Marketing", "Expense", None, Decimal("10000.00"))]
    await _load(tenant_id, biz["ulrg"], rows)
    tok = await _owner_token()
    q = f"business_id={biz['ulrg']}&period_start=2026-07-01&period_end=2026-07-31"
    async with AsyncClient(transport=TRANSPORT, base_url="http://testserver") as c:
        r = await c.get(f"/api/v1/books/statement?{q}", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["error"] == "unmapped_accounts"
    assert d["accounts"][0]["fqn"] == "Marketing" and d["accounts"][0]["amount"] == 10000.0

    await _load(tenant_id, biz["ulrg"])
    async with AsyncClient(transport=TRANSPORT, base_url="http://testserver") as c:
        r = await c.get(f"/api/v1/books/statement?{q}", headers={"Authorization": f"Bearer {tok}"})
        g = await c.get("/api/v1/books/coa/tie-out?period_start=2026-07-01&period_end=2026-07-31",
                        headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["tie_out"]["status"] == "tied"
    assert g.status_code == 200 and "gate" in g.json()


async def test_the_statement_says_whether_the_books_are_closed_and_when_it_synced():
    """A statement that ties is still a statement built on an open, mid-sync month. Section 9
    makes showing that a requirement, not a nicety."""
    tenant_id, biz = await _ids()
    await _load(tenant_id, biz["ulrg"])
    async with SessionLocal() as s:
        st = await CB.build_mapped_statement(s, tenant_id, biz["ulrg"], PERIOD)
    assert st["period"]["books_closed"] is False
    assert st["period"]["synced_at"], "the reader can see how fresh this is"

    from app.models import ClosePeriod
    async with SessionLocal() as s:
        s.add(ClosePeriod(tenant_id=tenant_id, business_id=biz["ulrg"],
                          period=PERIOD[0], status="closed"))
        await s.commit()
        st = await CB.build_mapped_statement(s, tenant_id, biz["ulrg"], PERIOD)
    assert st["period"]["books_closed"] is True
