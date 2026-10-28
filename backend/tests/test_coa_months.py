"""Monthly grain: `allocation_months`, the second pass in `sync_trial_balances`, and the
containment filter (ALLOCATION-RULES-SPEC Phase 2).

Three things worth defending.

**The months have to sum to the year.** Period activity is the difference of two as-of trial
balances, so twelve monthly differences telescope to the year-to-date one: every intermediate
pull cancels. If they do not sum, the monthly figures are wrong in a way that still renders and
still ties out per month, which is the most expensive shape of error this layer can produce.

**The fiscal-year zeroing has to survive the refactor.** A trial balance resets P&L accounts at
the year boundary, so January's opening pull carries the WHOLE prior year on them. Both passes
difference through one `_write_period` for exactly this reason: if they disagreed about the
boundary, the months would sum to something, just not to the year.

**The existing windows must not move.** `account_period_balance` now holds rows at two grains
with nothing in a row saying which, so a reader that does not pin both period columns would
double count. `allocation_contribution` likewise.

`sync_trial_balances` had no test before this file. The differencing, the fiscal-year crossing
and the as-of cache were all live and unexercised.
"""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (AccountPeriodBalance, AllocationContribution, Business, ClosePeriod,
                        CoaMap, StandardAccount, Tenant)
from app.services import coa_alloc as CA
from app.services import coa_balances as CB
from app.seed import seed
from app.services.coa import seed_standard_chart

TODAY = dt.date(2026, 9, 30)          # a month end, so the year-to-date window closes on it
FY = 1                                # calendar fiscal year
REALM = "realm-months"


# ── the ledger the fake QuickBooks serves ────────────────────────────────────────────────
#
# Monthly activity, debit-positive. DELIBERATELY UNEQUAL month to month: equal amounts would
# let an off-by-one month, a reused opening or a dropped period pass unnoticed, which is the
# trap the spec names for this gate. Nine months, Jan to Sep.
ACTIVITY = {
    "100": [5000, 4000, 9000, 3000, 6000, 1000, 11000, 7000, 2000],       # Bank
    "200": [-12000, -9000, -15000, -11000, -13000, -8000, -17000, -14000, -10000],  # Income
    "400": [2000, 2000, 2000, 2500, 2500, 2500, 2500, 2500, 2500],        # Expense
    # Starts in May, skips July. A month with no movement but a standing balance still gets a
    # row; a month with neither gets none.
    "600": [0, 0, 0, 0, 700, 800, 0, 1200, 300],                          # Expense
    "500": [0, 0, 0, 0, 0, 0, 0, 0, 0],                                   # Equity, never moves
}
TYPES = {"100": "Bank", "200": "Income", "400": "Expense", "600": "Expense",
         "500": "Equity", "900": "Other Current Liability"}
PL = {q for q, t in TYPES.items() if t in CB.PL_TYPES}

# What the prior fiscal year left behind, as of 2025-12-31. Non-zero on BOTH sides on purpose:
# the balance-sheet openings must be subtracted and the P&L ones must be discarded, and a
# fixture with zero openings cannot tell the two apart.
PRIOR = {"100": 20000, "500": -155000, "200": -700000, "400": 175000, "600": 0}

DEC31 = dt.date(2025, 12, 31)
MONTH_ENDS = [CB._month_end(2026, m) for m in range(1, 10)]


def _as_of_amounts(on: dt.date) -> dict[str, Decimal]:
    """The as-of trial balance this fake QuickBooks would report on `on`.

    A P&L account carries fiscal-year-to-date and resets at the boundary; a balance-sheet
    account carries its cumulative balance across it. That difference is the whole reason
    `crosses_fy` exists.
    """
    out: dict[str, Decimal] = {}
    for qid, months in ACTIVITY.items():
        if on <= DEC31:
            out[qid] = Decimal(PRIOR[qid])
            continue
        through = sum(a for a, end in zip(months, MONTH_ENDS) if end <= on)
        base = 0 if qid in PL else PRIOR[qid]
        out[qid] = Decimal(base + through)
    # A trial balance sums to zero. The plug keeps it that way on every date, so a sign error
    # anywhere above shows up as a report that does not balance rather than as nothing.
    out["900"] = -sum(out.values())
    return out


def _report(on: dt.date) -> dict:
    """A real QBO TrialBalance payload, so the real parser runs. An account at exactly zero is
    absent from the report entirely, which is how QuickBooks behaves and is what makes the
    "appears partway through the year" case real."""
    rows = []
    for qid, amt in sorted(_as_of_amounts(on).items()):
        if amt == 0:
            continue
        rows.append({"ColData": [
            {"value": f"Account {qid}", "id": qid},
            {"value": f"{amt:.2f}" if amt > 0 else ""},
            {"value": f"{-amt:.2f}" if amt < 0 else ""}]})
    return {"Rows": {"Row": rows}}


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


@pytest.fixture
def fake_qbo(monkeypatch):
    """Patch the three seams between `sync_trial_balances` and Intuit, and count the calls.

    The parser is NOT patched: it has its own tests and bypassing it here would stop this file
    from noticing a change in how a report is read.
    """
    calls: list[dt.date] = []

    async def trial_balance(realm_id, token, start, end):
        calls.append(dt.date.fromisoformat(end))
        return _report(dt.date.fromisoformat(end))

    async def token(s, integ):
        return "tok"

    async def fy(realm_id, access_token):
        return FY

    monkeypatch.setattr(CB.qbo, "trial_balance", trial_balance)
    monkeypatch.setattr(CB.qbo, "fiscal_year_start_month", fy)
    monkeypatch.setattr(CB, "_valid_access_token", token)
    return calls


async def _run(tenant_id, business_id, today=TODAY) -> int:
    integ = SimpleNamespace(realm_id=REALM, business_id=business_id)
    async with SessionLocal() as s:
        await s.execute(delete(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == business_id))
        await s.execute(delete(CoaMap).where(CoaMap.business_id == business_id))
        for qid, qtype in TYPES.items():
            s.add(CoaMap(tenant_id=tenant_id, business_id=business_id, qbo_account_id=qid,
                         qbo_account_name=f"Account {qid}", qbo_account_fqn=f"Account {qid}",
                         qbo_account_type=qtype))
        await s.commit()
    async with SessionLocal() as s:
        return await CB.sync_trial_balances(s, tenant_id, integ, today=today)


async def _rows(tenant_id, business_id) -> dict:
    async with SessionLocal() as s:
        rows = (await s.execute(select(AccountPeriodBalance).where(
            AccountPeriodBalance.tenant_id == tenant_id,
            AccountPeriodBalance.business_id == business_id))).scalars().all()
    out: dict = {}
    for r in rows:
        out.setdefault((r.period_start, r.period_end), {})[r.qbo_account_id] = r.amount
    return out


# ── allocation_months, pure ───────────────────────────────────────────────────────────────

def test_the_months_run_from_the_fiscal_year_start_to_today():
    m = CB.allocation_months(dt.date(2026, 10, 28), 1)
    assert len(m) == 10
    assert m[0] == (dt.date(2026, 1, 1), dt.date(2026, 1, 31))
    assert m[-1] == (dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    # contiguous and non-overlapping: each month opens the day after the last one closed
    for (a_s, a_e), (b_s, _b_e) in zip(m, m[1:]):
        assert b_s == a_e + dt.timedelta(days=1)
        assert a_s < a_e


def test_the_months_follow_a_fiscal_year_that_is_not_the_calendar_one():
    """A July fiscal year in March reaches back across the calendar boundary, which is exactly
    the case `balance_periods()` cannot express: April is unreachable from October."""
    m = CB.allocation_months(dt.date(2026, 3, 15), 7)
    assert m[0] == (dt.date(2025, 7, 1), dt.date(2025, 7, 31))
    assert m[-1] == (dt.date(2026, 3, 1), dt.date(2026, 3, 31))
    assert len(m) == 9


def test_the_first_month_of_a_fiscal_year_is_one_month_not_none():
    assert CB.allocation_months(dt.date(2026, 1, 5), 1) == [
        (dt.date(2026, 1, 1), dt.date(2026, 1, 31))]
    assert CB.allocation_months(dt.date(2026, 7, 1), 7) == [
        (dt.date(2026, 7, 1), dt.date(2026, 7, 31))]


def test_month_ends_are_real_month_ends():
    assert CB._month_end(2024, 2) == dt.date(2024, 2, 29)      # leap
    assert CB._month_end(2026, 2) == dt.date(2026, 2, 28)
    assert CB._month_end(2026, 12) == dt.date(2026, 12, 31)    # the year rollover
    assert CB._month_end(2026, 4) == dt.date(2026, 4, 30)


# ── the gate: the months sum to the year ──────────────────────────────────────────────────

async def test_the_months_sum_to_the_year_to_date_window_per_account(fake_qbo):
    """The Phase 2 gate. Nine monthly differences must telescope to the year-to-date one, to
    the cent, for every account."""
    tenant_id, biz = await _ids()
    await _run(tenant_id, biz["ulrg"])
    rows = await _rows(tenant_id, biz["ulrg"])

    ytd = rows[(dt.date(2026, 1, 1), dt.date(2026, 12, 31))]
    months = [rows[(dt.date(2026, m, 1), CB._month_end(2026, m))] for m in range(1, 10)]
    assert len(months) == 9

    accounts = set(ytd) | {q for m in months for q in m}
    assert accounts, "the fixture produced no rows at all"
    for qid in accounts:
        summed = sum((m.get(qid, CB.ZERO) for m in months), CB.ZERO)
        assert summed == ytd.get(qid, CB.ZERO), (
            f"account {qid}: months sum to {summed}, year-to-date says {ytd.get(qid)}")

    # And the sums are the fixture's own, not an artefact of everything being zero.
    assert ytd["200"] == Decimal("-109000")
    assert ytd["400"] == Decimal("21000")
    assert ytd["600"] == Decimal("3000")
    assert ytd["100"] == Decimal("48000")


async def test_the_prior_year_is_discarded_on_the_profit_and_loss_and_subtracted_elsewhere(fake_qbo):
    """The fiscal-year boundary, which is the one place the two passes could have diverged.

    Income carried 700,000 of prior-year fiscal-year-to-date on the opening pull. If that were
    differenced, January would report it. The bank carried a real 20,000 opening, which MUST be
    differenced away or January would invent it.
    """
    tenant_id, biz = await _ids()
    await _run(tenant_id, biz["ulrg"])
    rows = await _rows(tenant_id, biz["ulrg"])
    jan = rows[(dt.date(2026, 1, 1), dt.date(2026, 1, 31))]
    ytd = rows[(dt.date(2026, 1, 1), dt.date(2026, 12, 31))]

    assert jan["200"] == Decimal("-12000"), "January's income, not the prior year's"
    assert jan["400"] == Decimal("2000")
    assert jan["100"] == Decimal("5000"), "January's movement, not the opening balance"
    assert ytd["200"] == Decimal("-109000")
    # The equity account never moved and carries a standing balance: zero activity, real
    # as-of. A reader that confused the two columns would show a 155,000 swing.
    assert jan["500"] == Decimal("0")
    async with SessionLocal() as s:
        row = (await s.execute(select(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == biz["ulrg"],
            AccountPeriodBalance.period_start == dt.date(2026, 1, 1),
            AccountPeriodBalance.period_end == dt.date(2026, 1, 31),
            AccountPeriodBalance.qbo_account_id == "500"))).scalar_one()
    assert row.balance_end == Decimal("-155000")


async def test_an_account_that_starts_mid_year_gets_rows_only_once_it_exists(fake_qbo):
    """Account 600 first moves in May and sits still in July. A month with movement OR a
    standing balance earns a row; a month with neither does not."""
    tenant_id, biz = await _ids()
    await _run(tenant_id, biz["ulrg"])
    rows = await _rows(tenant_id, biz["ulrg"])
    present = {m for m in range(1, 10)
               if "600" in rows[(dt.date(2026, m, 1), CB._month_end(2026, m))]}
    assert present == {5, 6, 7, 8, 9}, "nothing before May, and July still reports its balance"
    jul = rows[(dt.date(2026, 7, 1), dt.date(2026, 7, 31))]
    assert jul["600"] == Decimal("0")                     # no movement
    assert rows[(dt.date(2026, 6, 1), dt.date(2026, 6, 30))]["600"] == Decimal("800")


async def test_a_period_nets_to_zero_unless_it_crosses_the_fiscal_year(fake_qbo):
    """A difference of two zero-sums is a zero-sum, and that is the cheap structural check that
    a sign flipped somewhere. It holds for every month that sits inside one fiscal year.

    It does NOT hold across the boundary, and the original docstring said it did. Zeroing the
    P&L openings discards the prior year, and what it discards stays missing from the sum: both
    January and year-to-date net to the same figure, one prior year of P&L, discarded once. The
    tie-out survives it because it compares the mapped total against the booked total rather
    than against zero, so the residue sits on both sides and cancels.
    """
    tenant_id, biz = await _ids()
    await _run(tenant_id, biz["ulrg"])
    rows = await _rows(tenant_id, biz["ulrg"])

    inside_one_year = [(dt.date(2026, m, 1), CB._month_end(2026, m)) for m in range(2, 10)]
    for key in inside_one_year:
        assert sum(rows[key].values(), CB.ZERO) == CB.ZERO, f"{key} does not balance"

    discarded = sum(Decimal(v) for q, v in PRIOR.items() if q in PL)   # -525,000
    jan = sum(rows[(dt.date(2026, 1, 1), dt.date(2026, 1, 31))].values(), CB.ZERO)
    ytd = sum(rows[(dt.date(2026, 1, 1), dt.date(2026, 12, 31))].values(), CB.ZERO)
    assert jan == ytd == discarded, "the residue is the prior year's P&L, discarded once"
    assert discarded != 0, "a fixture with no prior-year P&L cannot see this at all"


async def test_every_as_of_date_is_pulled_exactly_once(fake_qbo):
    """The month pass shares the window pass's cache. Without that it would re-pull dates the
    windows already hold, and each pull is a QuickBooks report call."""
    tenant_id, biz = await _ids()
    await _run(tenant_id, biz["ulrg"])
    assert len(fake_qbo) == len(set(fake_qbo)), (
        f"{len(fake_qbo)} pulls for {len(set(fake_qbo))} distinct dates")
    # Consecutive months chain: one month's close is the next one's opening. So nine months
    # cost ten dates, not eighteen.
    assert set(fake_qbo) >= {DEC31, *MONTH_ENDS}
    assert len(set(fake_qbo)) <= len(MONTH_ENDS) + 1 + len(CB.balance_periods())


async def _age(business_id, hours: int):
    """Push every stored row's `synced_at` back, so the time-based skip does not fire and the
    write path itself is what the next assertion is about."""
    async with SessionLocal() as s:
        rows = (await s.execute(select(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == business_id))).scalars().all()
        for r in rows:
            r.synced_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
        await s.commit()


async def test_a_second_run_writes_the_same_thing(fake_qbo):
    """Delete-then-insert per key, at both grains. A re-run must not double the rows, and the
    two month keys that are also window keys must not end up written twice.

    The rows are aged first ON PURPOSE. Without that the second pass would skip every past
    month on the time rule and this would assert that doing nothing changes nothing.
    """
    tenant_id, biz = await _ids()
    await _run(tenant_id, biz["ulrg"])
    first = await _rows(tenant_id, biz["ulrg"])
    async with SessionLocal() as s:
        before = len((await s.execute(select(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == biz["ulrg"]))).scalars().all())
    await _age(biz["ulrg"], CB.MONTH_REFRESH_HOURS + 1)

    fake_qbo.clear()
    integ = SimpleNamespace(realm_id=REALM, business_id=biz["ulrg"])
    async with SessionLocal() as s:
        await CB.sync_trial_balances(s, tenant_id, integ, today=TODAY)
    assert fake_qbo, "the second pass skipped everything, so it proved nothing"

    async with SessionLocal() as s:
        after = len((await s.execute(select(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == biz["ulrg"]))).scalars().all())
    assert after == before
    assert await _rows(tenant_id, biz["ulrg"]) == first


# ── when a month may be left alone ────────────────────────────────────────────────────────

MAR = (dt.date(2026, 3, 1), dt.date(2026, 3, 31))
SEP = (dt.date(2026, 9, 1), dt.date(2026, 9, 30))       # the current month, given TODAY


async def _settled(tenant_id, business_id, key, today=TODAY) -> bool:
    async with SessionLocal() as s:
        return await CB._month_is_settled(s, tenant_id, business_id, key[0], key[1],
                                          today=today)


async def _close(tenant_id, business_id, month: dt.date, status="closed"):
    async with SessionLocal() as s:
        await s.execute(delete(ClosePeriod).where(ClosePeriod.tenant_id == tenant_id))
        s.add(ClosePeriod(tenant_id=tenant_id, business_id=business_id,
                          period=month, status=status))
        await s.commit()


async def test_a_month_with_no_rows_is_never_skipped(fake_qbo):
    """The load-bearing half. The first pass after this ships finds no month rows at all, and
    skipping a closed month then would leave the closed part of the year permanently empty with
    nothing left to trigger a backfill."""
    tenant_id, biz = await _ids()
    async with SessionLocal() as s:
        await s.execute(delete(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == biz["ulrg"]))
        await s.commit()
    await _close(tenant_id, biz["ulrg"], dt.date(2026, 3, 1))
    assert await _settled(tenant_id, biz["ulrg"], MAR) is False

    await _run(tenant_id, biz["ulrg"])                    # and it really does get written
    rows = await _rows(tenant_id, biz["ulrg"])
    assert rows[MAR]["400"] == Decimal("2000")
    async with SessionLocal() as s:
        await s.execute(delete(ClosePeriod).where(ClosePeriod.tenant_id == tenant_id))
        await s.commit()


async def test_a_closed_month_is_skipped_and_keeps_its_rows(fake_qbo):
    tenant_id, biz = await _ids()
    await _run(tenant_id, biz["ulrg"])
    await _age(biz["ulrg"], CB.MONTH_REFRESH_HOURS + 1)   # so only `closed` can do the skipping
    assert await _settled(tenant_id, biz["ulrg"], MAR) is False
    await _close(tenant_id, biz["ulrg"], dt.date(2026, 3, 1))
    assert await _settled(tenant_id, biz["ulrg"], MAR) is True

    fake_qbo.clear()
    integ = SimpleNamespace(realm_id=REALM, business_id=biz["ulrg"])
    async with SessionLocal() as s:
        await CB.sync_trial_balances(s, tenant_id, integ, today=TODAY)
    rows = await _rows(tenant_id, biz["ulrg"])
    assert rows[MAR]["400"] == Decimal("2000"), "its rows survive the skip"
    async with SessionLocal() as s:
        await s.execute(delete(ClosePeriod).where(ClosePeriod.tenant_id == tenant_id))
        await s.commit()


async def test_an_open_month_marked_open_is_not_skipped_by_the_checklist(fake_qbo):
    tenant_id, biz = await _ids()
    await _run(tenant_id, biz["ulrg"])
    await _age(biz["ulrg"], CB.MONTH_REFRESH_HOURS + 1)
    await _close(tenant_id, biz["ulrg"], dt.date(2026, 3, 1), status="open")
    assert await _settled(tenant_id, biz["ulrg"], MAR) is False
    async with SessionLocal() as s:
        await s.execute(delete(ClosePeriod).where(ClosePeriod.tenant_id == tenant_id))
        await s.commit()


async def test_a_past_month_pulled_recently_is_left_alone_and_a_stale_one_is_not(fake_qbo):
    """The backstop for a workspace that never closes a month. Without it the month pass costs
    about five extra QuickBooks report calls per realm on every thirty-minute tick, forever."""
    tenant_id, biz = await _ids()
    await _run(tenant_id, biz["ulrg"])
    assert await _settled(tenant_id, biz["ulrg"], MAR) is True, "just pulled"
    await _age(biz["ulrg"], CB.MONTH_REFRESH_HOURS + 1)
    assert await _settled(tenant_id, biz["ulrg"], MAR) is False, "stale again"
    await _age(biz["ulrg"], CB.MONTH_REFRESH_HOURS - 1)
    assert await _settled(tenant_id, biz["ulrg"], MAR) is True


async def test_the_current_month_is_never_skipped_by_time(fake_qbo):
    """September is the month containing TODAY. Its figures move every day and it is the month
    somebody is most likely to be looking at, so freshness cannot buy it a skip."""
    tenant_id, biz = await _ids()
    await _run(tenant_id, biz["ulrg"])
    assert await _settled(tenant_id, biz["ulrg"], SEP) is False
    # Closing it still skips it: a signed-off month cannot change, whichever month it is.
    await _close(tenant_id, biz["ulrg"], dt.date(2026, 9, 1))
    assert await _settled(tenant_id, biz["ulrg"], SEP) is True
    async with SessionLocal() as s:
        await s.execute(delete(ClosePeriod).where(ClosePeriod.tenant_id == tenant_id))
        await s.commit()


async def test_the_skip_reads_only_its_own_entitys_rows(fake_qbo):
    """One entity's rows must not answer for another's.

    The freshness has to DIFFER between the two, or this proves nothing: with both aged, a
    lookup that forgot its `business_id` filter still returns a stale row and still answers
    False, and the test goes green on a mutation that would let any entity's recent pull
    silence every other entity's month. Found by mutating exactly that filter away.
    """
    tenant_id, biz = await _ids()
    await _run(tenant_id, biz["ulrg"])
    await _run(tenant_id, biz["sympli"])
    await _age(biz["sympli"], CB.MONTH_REFRESH_HOURS + 1)      # only sympli is stale
    assert await _settled(tenant_id, biz["ulrg"], MAR) is True, "ulrg was just pulled"
    assert await _settled(tenant_id, biz["sympli"], MAR) is False, "sympli's own rows are stale"
    # And the checklist half is per entity too.
    await _age(biz["ulrg"], CB.MONTH_REFRESH_HOURS + 1)
    await _close(tenant_id, biz["ulrg"], dt.date(2026, 3, 1))
    assert await _settled(tenant_id, biz["ulrg"], MAR) is True
    assert await _settled(tenant_id, biz["sympli"], MAR) is False
    async with SessionLocal() as s:
        await s.execute(delete(ClosePeriod).where(ClosePeriod.tenant_id == tenant_id))
        await s.execute(delete(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == biz["sympli"]))
        await s.commit()


async def test_a_month_crossing_the_fiscal_year_is_never_left_alone(fake_qbo):
    """January's figure is the one that depends on `types` classifying P&L accounts, and
    `coa_map`'s sync records its own failure without aborting the pass — so this can run with
    `types` empty. Today a wrong opening can only reach the year-to-date window, which is
    re-pulled every pass and heals. A closed month does not heal, so January is never frozen.
    """
    tenant_id, biz = await _ids()
    await _run(tenant_id, biz["ulrg"])
    jan = (dt.date(2026, 1, 1), dt.date(2026, 1, 31))
    await _close(tenant_id, biz["ulrg"], dt.date(2026, 1, 1))
    assert await _settled(tenant_id, biz["ulrg"], jan) is True, "the skip itself says settled"

    fake_qbo.clear()
    integ = SimpleNamespace(realm_id=REALM, business_id=biz["ulrg"])
    async with SessionLocal() as s:
        await CB.sync_trial_balances(s, tenant_id, integ, today=TODAY)
    assert dt.date(2026, 1, 31) in fake_qbo, "January was pulled anyway"
    assert (await _rows(tenant_id, biz["ulrg"]))[jan]["200"] == Decimal("-12000")
    async with SessionLocal() as s:
        await s.execute(delete(ClosePeriod).where(ClosePeriod.tenant_id == tenant_id))
        await s.commit()


async def test_no_months_are_written_at_all_without_the_chart(fake_qbo):
    """`types` empty means every account looks like a balance-sheet account, which means
    January differences against a trial balance carrying the whole prior fiscal year. Wrong
    month rows are worse than none: a window heals on the next pass and a closed month cannot.
    """
    tenant_id, biz = await _ids()
    async with SessionLocal() as s:
        await s.execute(delete(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == biz["ulrg"]))
        await s.execute(delete(CoaMap).where(CoaMap.business_id == biz["ulrg"]))
        await s.commit()
    integ = SimpleNamespace(realm_id=REALM, business_id=biz["ulrg"])
    async with SessionLocal() as s:
        await CB.sync_trial_balances(s, tenant_id, integ, today=TODAY)
    rows = await _rows(tenant_id, biz["ulrg"])
    assert (dt.date(2026, 3, 1), dt.date(2026, 3, 31)) not in rows, "a month was written blind"
    assert (dt.date(2026, 1, 1), dt.date(2026, 12, 31)) in rows, "the windows still run"


# ── the other half of the gate: the existing windows do not move ──────────────────────────

BOOKS = [
    ("100", "Operating Checking", "Bank", "1000", Decimal("35000.00")),
    ("200", "Commission Income", "Income", "4010", Decimal("-100000.00")),
    ("300", "Agent Splits", "Cost of Goods Sold", "5010", Decimal("40000.00")),
    ("400", "Office Rent", "Expense", "7010", Decimal("25000.00")),
]
WINDOW = (dt.date(2026, 7, 1), dt.date(2026, 7, 31))
INSIDE = (dt.date(2026, 7, 1), dt.date(2026, 7, 31))


async def _plain_books(tenant_id, business_id):
    async with SessionLocal() as s:
        await s.execute(delete(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == business_id))
        await s.execute(delete(CoaMap).where(CoaMap.business_id == business_id))
        await s.commit()
    async with SessionLocal() as s:
        for qid, name, qtype, code, amount in BOOKS:
            std = (await s.execute(select(StandardAccount).where(
                StandardAccount.tenant_id == tenant_id,
                StandardAccount.code == code))).scalar_one()
            s.add(CoaMap(tenant_id=tenant_id, business_id=business_id, qbo_account_id=qid,
                         qbo_account_name=name, qbo_account_fqn=name, qbo_account_type=qtype,
                         standard_account_id=std.id, mapped_via="manual"))
            s.add(AccountPeriodBalance(
                tenant_id=tenant_id, business_id=business_id, period_start=WINDOW[0],
                period_end=WINDOW[1], qbo_account_id=qid, amount=amount, source="qbo_tb"))
        await s.commit()
        return std.id


async def test_month_grain_balance_rows_do_not_reach_a_window_statement():
    """Every AccountPeriodBalance reader pins BOTH period columns. If one did not, the monthly
    rows this phase adds would be counted on top of the window's own.

    July is excluded from the month rows below because July IS the window under test: a month
    key and a one-month window key are the same `(period_start, period_end)` pair, and
    `uq_apb_account_period` rightly refuses a second row for it. That overlap is the one the
    spec calls harmless, and it is harmless because both passes difference through
    `_write_period` and so compute the same figure for it.
    """
    tenant_id, biz = await _ids()
    await _plain_books(tenant_id, biz["sympli"])
    async with SessionLocal() as s:
        before = await CB.build_mapped_statement(s, tenant_id, biz["sympli"], WINDOW, "pl")
    async with SessionLocal() as s:
        for m in [1, 2, 3, 4, 5, 6, 8, 9]:                # the month rows Phase 2 now writes
            for qid, _n, _t, _c, amount in BOOKS:
                s.add(AccountPeriodBalance(
                    tenant_id=tenant_id, business_id=biz["sympli"],
                    period_start=dt.date(2026, m, 1), period_end=CB._month_end(2026, m),
                    qbo_account_id=qid, amount=amount, source="qbo_tb"))
        await s.commit()
    async with SessionLocal() as s:
        after = await CB.build_mapped_statement(s, tenant_id, biz["sympli"], WINDOW, "pl")
    assert after == before, "month rows changed a window statement"
    assert before["totals"]["net_income"] == 35000.0      # not an empty payload comparing equal
    # And the month rows really are there, so the comparison above was not vacuous.
    async with SessionLocal() as s:
        assert len((await s.execute(select(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == biz["sympli"]))).scalars().all()) == 9 * 4


async def test_a_key_both_passes_write_is_written_once_with_one_figure(fake_qbo):
    """August and September are window keys AND month keys. Both passes write them in a single
    call, so the month pass MUST delete before inserting or `uq_apb_account_period` fires. That
    the sync completes at all is the proof; this pins it so a future refactor cannot lose it."""
    tenant_id, biz = await _ids()
    await _run(tenant_id, biz["ulrg"])
    aug = (dt.date(2026, 8, 1), dt.date(2026, 8, 31))
    assert aug in CB.balance_periods(), "August is a window key as well as a month"
    async with SessionLocal() as s:
        rows = (await s.execute(select(AccountPeriodBalance).where(
            AccountPeriodBalance.business_id == biz["ulrg"],
            AccountPeriodBalance.period_start == aug[0],
            AccountPeriodBalance.period_end == aug[1],
            AccountPeriodBalance.qbo_account_id == "400"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].amount == Decimal("2500")              # August's own activity, once


async def test_a_month_grain_observed_contribution_is_not_read_by_a_window():
    """`grain` and `booking` together are what keep the new filter inert. Every row that exists
    today is grain `window`; an observed month row must stay out of a window read."""
    tenant_id, biz = await _ids()
    std_id = await _plain_books(tenant_id, biz["sympli"])
    async with SessionLocal() as s:
        await s.execute(delete(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))
        await s.commit()
    async with SessionLocal() as s:
        base = await CA.contributions_for(s, tenant_id, biz["sympli"], WINDOW)
        assert base == {"in": {}, "out": {}}
    async with SessionLocal() as s:
        s.add(AllocationContribution(
            tenant_id=tenant_id, period_start=INSIDE[0], period_end=INSIDE[1],
            source_business_id=biz["springb"], target_business_id=biz["sympli"],
            standard_account_id=std_id, amount=Decimal("1000.00"),
            booking="observed", grain="month", pool_name="Shared"))
        await s.commit()
    async with SessionLocal() as s:
        assert await CA.contributions_for(s, tenant_id, biz["sympli"], WINDOW) == {
            "in": {}, "out": {}}
    async with SessionLocal() as s:
        await s.execute(delete(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))
        await s.commit()


async def test_an_applied_month_row_is_read_by_a_window_that_contains_it_and_no_other():
    """What the filter is FOR. A year-to-date window has to contain ten monthly rows, and a
    month that only partly overlaps a window is not that window's."""
    tenant_id, biz = await _ids()
    std_id = await _plain_books(tenant_id, biz["sympli"])
    ytd = (dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    august = (dt.date(2026, 8, 1), dt.date(2026, 8, 31))
    async with SessionLocal() as s:
        await s.execute(delete(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))
        s.add(AllocationContribution(
            tenant_id=tenant_id, period_start=INSIDE[0], period_end=INSIDE[1],
            source_business_id=biz["springb"], target_business_id=biz["sympli"],
            standard_account_id=std_id, amount=Decimal("1000.00"),
            booking="applied", grain="month", pool_name="Shared"))
        await s.commit()
    async with SessionLocal() as s:
        assert len((await CA.contributions_for(
            s, tenant_id, biz["sympli"], WINDOW))["in"][std_id]) == 1
        assert len((await CA.contributions_for(
            s, tenant_id, biz["sympli"], ytd))["in"][std_id]) == 1, "the year contains July"
        assert (await CA.contributions_for(
            s, tenant_id, biz["sympli"], august))["in"] == {}, "August does not contain July"
    # Containment is closed on both ends: a month hanging off the end of a window is out.
    async with SessionLocal() as s:
        assert (await CA.contributions_for(
            s, tenant_id, biz["sympli"],
            (dt.date(2026, 7, 15), dt.date(2026, 8, 15))))["in"] == {}
        await s.execute(delete(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))
        await s.commit()


async def test_a_window_grain_row_still_matches_only_its_own_window():
    """The old behaviour, unchanged. A window row is keyed on the window it was derived for and
    must not leak into a wider one just because the dates fit inside it."""
    tenant_id, biz = await _ids()
    std_id = await _plain_books(tenant_id, biz["sympli"])
    ytd = (dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    async with SessionLocal() as s:
        await s.execute(delete(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))
        s.add(AllocationContribution(
            tenant_id=tenant_id, period_start=WINDOW[0], period_end=WINDOW[1],
            source_business_id=biz["springb"], target_business_id=biz["sympli"],
            standard_account_id=std_id, amount=Decimal("1000.00"),
            booking="observed", pool_name="Shared"))       # grain defaults to window
        await s.commit()
    async with SessionLocal() as s:
        assert len((await CA.contributions_for(
            s, tenant_id, biz["sympli"], WINDOW))["in"][std_id]) == 1
        assert (await CA.contributions_for(s, tenant_id, biz["sympli"], ytd))["in"] == {}
        await s.execute(delete(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))
        await s.commit()
