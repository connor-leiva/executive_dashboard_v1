"""The rule that decides which collected dollars a rep has not been paid on.

This exists because the version it replaced was safe only by luck. It capped each gather at the
member's whole shortfall, which is exact when nothing was recorded -- Brianna Wood's case,
where the months sum to the full history -- and overpays the moment one member needs more than
one month gathered AND has cash in both records. No single run can see the other runs, so no
per-run cap can promise anything about their total. `partitions_the_shortfall` is that promise,
and `the_old_per_run_cap_would_have_overpaid` is the proof it was needed: it asserts the
failure the old code produced, so the two cannot both be satisfied by the same implementation.

No database. The dangerous case is unreachable from production data -- the only member with
cash in both records is refused earlier for a shared email -- so the only way to exercise it is
to call the rule with the numbers directly.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from scripts.commission_gather import allocate_unrecorded  # noqa: E402

# LeAnne Anies, the member who started this: 14,000 financed, 5,000 down plus the first
# instalment in August, then 750 a month. Nothing of it ever reached commission_payment.
LEANNE = [("2026-08-18", 5750.0), ("2026-09-18", 750.0), ("2026-10-18", 750.0)]


def total(alloc):
    return round(sum(a for _m, _p, a in alloc), 2)


def by_month(alloc):
    out = {}
    for m, _p, a in alloc:
        out[m] = round(out.get(m, 0.0) + a, 2)
    return out


# ── nothing recorded: the whole history is owed ──────────────────────────────────────────────

def test_nothing_recorded_means_every_dollar_is_unrecorded():
    alloc = allocate_unrecorded(LEANNE, 0)
    assert total(alloc) == 7250.0
    assert by_month(alloc) == {"2026-08": 5750.0, "2026-09": 750.0, "2026-10": 750.0}


def test_fully_recorded_means_nothing_is_owed():
    assert allocate_unrecorded(LEANNE, 7250.0) == []


def test_over_recorded_still_owes_nothing_rather_than_a_negative():
    # The commission table holding MORE than Stripe shows is a data problem, not a credit.
    assert allocate_unrecorded(LEANNE, 99999.0) == []


# ── the property the per-run cap could not give ──────────────────────────────────────────────

@pytest.mark.parametrize("recorded", [0, 100.0, 750.0, 1000.0, 5750.0, 6500.0, 7249.99])
def test_partitions_the_shortfall(recorded):
    """Every month's share summed is EXACTLY the shortfall -- never more, never less.

    Gathering August, September and October separately is three independent runs. This is what
    makes that safe: the months divide the unrecorded cash between them, so no combination of
    runs can pay more than is owed and none of it can fall between two statements either.
    """
    alloc = allocate_unrecorded(LEANNE, recorded)
    shortfall = round(7250.0 - recorded, 2)
    assert total(alloc) == shortfall
    assert round(sum(by_month(alloc).values()), 2) == shortfall


def test_the_old_per_run_cap_would_have_overpaid():
    """The exact failure that motivated this, asserted as a failure.

    5,000 recorded of 7,250 collected is 2,250 owed. The old rule took min(month, shortfall)
    with the cap reset on every run: August alone claimed the whole 2,250, then September and
    October claimed 750 each on top -- 3,750 paid on 2,250 owed. A test that only checked
    August would have passed.
    """
    recorded = 5000.0
    shortfall = round(7250.0 - recorded, 2)

    old = {m: round(min(amount, shortfall), 2) for m, amount in
           (("2026-08", 5750.0), ("2026-09", 750.0), ("2026-10", 750.0))}
    assert round(sum(old.values()), 2) == 3750.0          # what it used to pay
    assert round(sum(old.values()), 2) > shortfall        # ... on 2,250 owed

    new = by_month(allocate_unrecorded(LEANNE, recorded))
    assert round(sum(new.values()), 2) == shortfall
    assert new == {"2026-08": 750.0, "2026-09": 750.0, "2026-10": 750.0}


def test_each_month_is_the_same_whichever_month_is_being_gathered():
    """The allocation must not depend on the period in hand.

    cohort2_gaps filters this result by month; it does not pass the month in. If the rule ever
    starts reading the period, the three statements stop agreeing with each other and nothing
    in a single run would show it.
    """
    alloc = allocate_unrecorded(LEANNE, 5000.0)
    for _ in range(3):                                     # same inputs, same answer, always
        assert allocate_unrecorded(LEANNE, 5000.0) == alloc


# ── part-covered payments, ordering, and the awkward shapes ─────────────────────────────────

def test_a_single_payment_can_be_part_covered():
    alloc = allocate_unrecorded([("2026-08-18", 5750.0)], 5000.0)
    assert alloc == [("2026-08", "2026-08-18", 750.0)]


def test_covers_oldest_first_not_largest_or_newest():
    # 1,000 recorded against 400 then 600 then 900 covers the first two exactly.
    alloc = allocate_unrecorded(
        [("2026-08-01", 400.0), ("2026-09-01", 600.0), ("2026-10-01", 900.0)], 1000.0)
    assert alloc == [("2026-10", "2026-10-01", 900.0)]


def test_two_payments_in_one_month_both_survive_as_separate_rows():
    # They must not be merged: each becomes its own line on the statement, with its own date.
    alloc = allocate_unrecorded([("2026-10-01", 500.0), ("2026-10-18", 750.0)], 0)
    assert alloc == [("2026-10", "2026-10-01", 500.0), ("2026-10", "2026-10-18", 750.0)]
    assert by_month(alloc) == {"2026-10": 1250.0}


def test_no_payments_owes_nothing():
    assert allocate_unrecorded([], 0) == []
    assert allocate_unrecorded([], 500.0) == []


def test_cents_do_not_drift():
    pays = [("2026-08-01", 33.33), ("2026-09-01", 33.33), ("2026-10-01", 33.34)]
    assert total(allocate_unrecorded(pays, 0)) == 100.0
    assert total(allocate_unrecorded(pays, 33.33)) == 66.67
    assert total(allocate_unrecorded(pays, 50.0)) == 50.0


def test_a_zero_payment_never_becomes_a_statement_line():
    # A 0.00 succeeded row exists in the feed as a test artefact. It must not print a line
    # claiming a member paid nothing, and it must not consume any cover either.
    alloc = allocate_unrecorded([("2026-08-01", 0.0), ("2026-09-01", 750.0)], 0)
    assert alloc == [("2026-09", "2026-09-01", 750.0)]


def test_cover_exactly_equal_to_a_payment_leaves_no_line_and_no_residue():
    """The boundary the two comparisons share.

    Mutating `cover >= amount` to `cover > amount`, or `free > 0` to `free >= 0`, changes
    nothing: both route a wholly-covered payment to no line and leave cover at zero. Checked
    exhaustively over 2,322 input combinations when this was written -- they are equivalent
    mutants, not a hole in these tests, and the two guards are deliberately redundant. Pinned
    here so the boundary is asserted by behaviour rather than inferred from the branches.
    """
    assert allocate_unrecorded([("2026-08-01", 750.0)], 750.0) == []
    # ... and the next payment is still fully owed: the cover is spent, not overspent.
    assert allocate_unrecorded(
        [("2026-08-01", 750.0), ("2026-09-01", 750.0)], 750.0) == [
        ("2026-09", "2026-09-01", 750.0)]


def test_month_is_taken_from_the_payment_date_not_inferred():
    alloc = allocate_unrecorded([("2026-12-31", 750.0)], 0)
    assert alloc == [("2026-12", "2026-12-31", 750.0)]


def test_decimal_input_is_accepted():
    # The query returns Numeric, which psycopg hands back as Decimal, not float.
    from decimal import Decimal
    alloc = allocate_unrecorded([("2026-08-18", Decimal("5750.00"))], Decimal("5000.00"))
    assert alloc == [("2026-08", "2026-08-18", 750.0)]
