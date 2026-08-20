"""Chart of accounts — Phase 1.

Most of these are invariants of the chart itself rather than of the code, and that is
deliberate. The chart is a policy document transcribed into a tuple; the way it breaks is
somebody editing a row and quietly putting an operating expense back in the 5000s. These tests
are what notice.
"""
import pytest
from sqlalchemy import select

from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant, Business, StandardAccount
from app.services.coa import (
    BUCKETS, SECTIONS, RECOGNITION, ARCHETYPES, BELOW_THE_LINE,
    CHART, chart_rows, seed_standard_chart, accounts_for_archetype,
)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _tenant_id():
    async with SessionLocal() as s:
        return (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id


def _section_for_code(code: str) -> set:
    """One series, one meaning. The whole point of the renumbering."""
    n = int(code)
    if 1000 <= n < 2000:
        return {"asset"}
    if 2000 <= n < 3000:
        return {"liability"}
    if 3000 <= n < 4000:
        return {"equity"}
    if 4000 <= n < 5000:
        return {"revenue"}                       # 4900s are contra, still a revenue section
    if 5000 <= n < 6000:
        return {"cogs"}
    if 6000 <= n < 9000:
        return {"opex"}
    return {"other_income", "other_expense"}


# ── the chart ─────────────────────────────────────────────────────────────────────────────

def test_codes_are_unique():
    codes = [r["code"] for r in chart_rows()]
    assert len(codes) == len(set(codes))


def test_vocabularies_are_closed():
    for r in chart_rows():
        assert r["bucket"] in BUCKETS, r
        assert r["section"] in SECTIONS, r
        assert r["statement"] in ("pl", "bs"), r
        assert r["normal_balance"] in ("debit", "credit"), r
        assert r["recognition"] in RECOGNITION or r["recognition"] is None, r
        for a in r["archetypes"]:
            assert a in ARCHETYPES, r


def test_one_series_one_meaning():
    for r in chart_rows():
        assert r["section"] in _section_for_code(r["code"]), \
            f"{r['code']} {r['name']} is section {r['section']}"


def test_no_operating_expense_in_the_cost_of_sale_series():
    """The specific defect the renumbering corrects. The July template put Salaries at 5500,
    which pushed opex into the series that conventionally belongs to cost of sale."""
    for r in chart_rows():
        if r["code"].startswith("5"):
            assert r["section"] == "cogs", r


def test_operating_buckets_occupy_their_documented_ranges():
    expected = {"advertising": (6000, 6499), "sales_promotion": (6500, 6999),
                "occupancy": (7000, 7499), "office_expense": (7500, 7999),
                "salaries_wages": (8000, 8499), "general_admin": (8500, 8699)}
    for r in chart_rows():
        if r["bucket"] in expected:
            lo, hi = expected[r["bucket"]]
            assert lo <= int(r["code"]) <= hi, r


def test_8700_to_8999_stays_empty_as_headroom():
    assert not [r for r in chart_rows() if 8700 <= int(r["code"]) <= 8999]


def test_program_and_event_revenue_always_defers():
    """Every account in 4200-4499 generates deferred revenue by definition. That is what makes
    the Deferred Revenue schedule auditable by inspection rather than by memory."""
    for r in chart_rows():
        if 4200 <= int(r["code"]) <= 4499:
            assert r["recognition"] in ("ratable", "event_date"), r


def test_transactional_revenue_never_defers():
    for r in chart_rows():
        if 4000 <= int(r["code"]) <= 4199:
            assert r["recognition"] == "point_in_time", r


def test_contra_revenue_is_debit_normal_and_not_an_expense():
    contra = [r for r in chart_rows() if r["bucket"] == "contra_revenue"]
    assert contra, "4900 range is missing and it is material"
    for r in contra:
        assert r["normal_balance"] == "debit", r
        assert r["section"] == "revenue", r      # negative revenue, never an expense


def test_intercompany_flag_is_on_exactly_the_right_accounts():
    flagged = {r["code"] for r in chart_rows() if r["is_intercompany_account"]}
    assert flagged == {"1300", "2400", "4710", "4720", "4730"}


def test_place_flow_through_sits_below_the_operating_line():
    """Decision 2, resolved: PLACE is other income and other expense, below the line. Roughly
    $3.49M out and $2.76M in on ULRG — run through revenue it would make every margin
    percentage on the dashboard meaningless. Below the line it is out of them by construction,
    so no separate exclusion flag is needed."""
    place = [r for r in chart_rows() if r["name"].startswith("PLACE")]
    assert len(place) == 3
    for r in place:
        assert int(r["code"]) >= 9000, r
        assert r["section"] in ("other_income", "other_expense"), r
        assert r["statement"] == "pl", r
    assert not [r for r in chart_rows()
                if r["section"] in ("revenue", "cogs") and r["name"].startswith("PLACE")]


def test_below_the_line_is_outside_the_operating_buckets():
    """If depreciation and interest sit inside opex, Net Operating Income is net income wearing
    the wrong label and margin comparisons are distorted by capital structure."""
    for r in chart_rows():
        if r["bucket"] in BELOW_THE_LINE:
            assert int(r["code"]) >= 9000, r
            assert r["section"] in ("other_income", "other_expense"), r


def test_sort_order_is_strictly_increasing():
    orders = [r["sort_order"] for r in chart_rows()]
    assert orders == sorted(orders)
    assert len(set(orders)) == len(orders)


# ── archetype activation ──────────────────────────────────────────────────────────────────

def test_property_entities_have_no_cost_of_sale():
    """Rental income has no variable cost that scales with it. A COGS balance on a property
    entity is a finding, not a category."""
    assert not [r for r in accounts_for_archetype("property") if r["section"] == "cogs"]


def test_program_archetype_covers_spring_b():
    """Spring B is 'Program plus Event' in the chart doc. `program` is the right single value:
    the activation matrix reaches Event Revenue and Merchant from Program."""
    codes = {r["code"] for r in accounts_for_archetype("program")}
    for code in ("4210", "4410", "5210", "5310"):
        assert code in codes, code
    assert "5010" not in codes                   # a coaching business opens no agent split


def test_transactional_archetype_is_a_brokerage():
    codes = {r["code"] for r in accounts_for_archetype("transactional")}
    assert {"4010", "4020", "5010", "5110"} <= codes
    assert "5220" not in codes                   # a brokerage never opens Food and Beverage


def test_dormant_gets_the_balance_sheet_only():
    rows = accounts_for_archetype("dormant")
    assert rows and all(r["statement"] == "bs" for r in rows)


def test_every_archetype_opens_the_universal_opex_buckets():
    for a in ("transactional", "program", "event", "property", "holding"):
        buckets = {r["bucket"] for r in accounts_for_archetype(a)}
        assert {"advertising", "sales_promotion", "occupancy",
                "office_expense", "salaries_wages", "general_admin"} <= buckets, a


# ── the seeder ────────────────────────────────────────────────────────────────────────────

async def test_seed_creates_the_whole_chart():
    tid = await _tenant_id()
    async with SessionLocal() as s:
        await seed_standard_chart(s, tid)
    async with SessionLocal() as s:
        rows = (await s.execute(select(StandardAccount).where(
            StandardAccount.tenant_id == tid))).scalars().all()
        assert len(rows) == len(CHART)
        assert {r.code for r in rows} == {r["code"] for r in chart_rows()}


async def test_seed_is_idempotent_and_preserves_ids():
    """Re-seeding must never orphan a map. coa_map.standard_account_id points at these rows,
    and somebody will have spent hours building it."""
    tid = await _tenant_id()
    async with SessionLocal() as s:
        await seed_standard_chart(s, tid)
    async with SessionLocal() as s:
        before = {r.code: r.id for r in (await s.execute(select(StandardAccount).where(
            StandardAccount.tenant_id == tid))).scalars()}
    async with SessionLocal() as s:
        result = await seed_standard_chart(s, tid)
    assert result["created"] == 0
    async with SessionLocal() as s:
        after = {r.code: r.id for r in (await s.execute(select(StandardAccount).where(
            StandardAccount.tenant_id == tid))).scalars()}
    assert before == after


async def test_seed_restores_a_hand_edited_policy_field():
    tid = await _tenant_id()
    async with SessionLocal() as s:
        await seed_standard_chart(s, tid)
    async with SessionLocal() as s:
        acct = (await s.execute(select(StandardAccount).where(
            StandardAccount.tenant_id == tid, StandardAccount.code == "8620"))).scalar_one()
        acct.bucket = "occupancy"                # someone mis-edits Bank Charges
        acct.is_active = False                   # and deliberately deactivates it
        await s.commit()
    async with SessionLocal() as s:
        await seed_standard_chart(s, tid)
    async with SessionLocal() as s:
        acct = (await s.execute(select(StandardAccount).where(
            StandardAccount.tenant_id == tid, StandardAccount.code == "8620"))).scalar_one()
        assert acct.bucket == "general_admin"    # policy field is restored
        assert acct.is_active is False           # a deliberate deactivation is not


async def test_business_carries_archetype_and_label():
    async with SessionLocal() as s:
        ulrg = (await s.execute(select(Business).where(Business.key == "ulrg"))).scalar_one()
        assert ulrg.archetype in ARCHETYPES
        assert ulrg.gross_profit_label


def test_charitable_giving_sits_below_the_operating_line():
    """Connor's Decision 4, 2026-08-20. Inside an operating bucket it makes Net Operating
    Income move with a discretionary choice, so two entities with identical operations show
    different operating margins."""
    rows = {r["code"]: r for r in chart_rows()}
    assert "6570" not in rows, "charitable donations left the Sales Promotion bucket"
    giving = rows["9410"]
    assert giving["section"] == "other_expense" and giving["statement"] == "pl"
    assert giving["bucket"] in BELOW_THE_LINE
    # and nothing else anywhere in the operating range answers to the same idea
    opex_names = [r["name"].lower() for r in chart_rows()
                  if 6000 <= int(r["code"]) < 9000]
    assert not any("charit" in n or "donation" in n for n in opex_names)


def test_gross_presentation_has_both_sides_of_the_split():
    """Revenue is gross on ULRG and Spring B (Connor, 2026-08-20). Gross only works if the
    contra side exists: the full commission needs somewhere for the split to land, and the
    full membership price needs somewhere for the closer's commission to land."""
    rows = {r["code"]: r for r in chart_rows()}
    for revenue, cost in (("4010", "5010"), ("4020", "5020"), ("4210", "5040")):
        assert rows[revenue]["section"] == "revenue"
        assert rows[cost]["section"] == "cogs", f"{revenue} has no cost-of-sale counterpart"
