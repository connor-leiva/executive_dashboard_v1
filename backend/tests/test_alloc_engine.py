"""The money arithmetic (ALLOCATION-RULES-SPEC 5.4, Phase 4).

Two invariants, asserted on every case below rather than on a chosen few:

  sum(parts)            == rate(pool, total share)
  sum(parts) + retained == pool

The second is the one that matters to a reader. A split that does not add back to its pool is
wrong by a cent a month per rule, and it renders, and it ties out, because the tie-out compares
the mapped total against the booked total rather than against the pool.

The first version of `split_amounts` satisfied the second invariant only when the shares summed
to 1, and the spec allows shares that do not: a `retained` method sends nothing, and a split may
deliberately stop short. Given `{forum: 0.6}` over 1000.00 it returned 1000.00 out and nothing
retained -- the whole pool allocated away and the remainder silently deleted. Every fixture in
Appendix B1 is a full split, so neither the gate nor a review would have caught it.
"""
from decimal import Decimal as D

import pytest

from app.services.alloc_engine import rate, retained, split_amounts

FORUM, BC, POOL = "forum", "becollective", "springb"
B1 = D("52049.87")
# Nine unequal months summing to B1's pool, which does not divide by nine (52049.87/9 is
# 5783.3188...). Two of them make a 0.7/0.3 split round to a cent more than the pool between
# them, so the residue is exercised in both directions.
MONTHS = [D(x) for x in ("5783.25", "4211.11", "7002.49", "3998.02", "6150.00",
                         "4777.77", "8001.23", "6625.95", "5500.05")]


def _both(pool, shares):
    """Both invariants, returned so a caller can assert on the figures as well."""
    parts = split_amounts(pool, shares)
    left = retained(pool, parts)
    total_share = sum(D(s) for s in shares.values())
    assert sum(parts.values()) == rate(pool, total_share), (pool, shares, parts)
    assert sum(parts.values()) + left == D(pool), (pool, shares, parts, left)
    return parts, left


def test_the_nine_months_do_not_divide_evenly_which_is_the_point():
    assert sum(MONTHS) == B1
    assert B1 / 9 != (B1 / 9).quantize(D("0.01")), "a pool that divides evenly tests nothing"
    assert len(set(MONTHS)) == 9, "equal months would hide an off-by-one month"


def test_every_month_of_b1_adds_back_to_its_pool_to_the_cent():
    """The Phase 4 gate, on the arithmetic alone."""
    shares = {FORUM: D("0.7"), BC: D("0.3"), POOL: D("0")}
    totals, left_over = {}, D(0)
    for m in MONTHS:
        parts, left = _both(m, shares)
        left_over += left
        for k, v in parts.items():
            totals[k] = totals.get(k, D(0)) + v
    assert sum(totals.values()) + left_over == B1, "the year has to add back too"
    assert totals[FORUM] == D("36434.90")
    assert totals[BC] == D("15614.97")
    assert totals[POOL] == D("0.00")
    assert left_over == D("0.00"), "a full split leaves nothing behind"


def test_independent_rounding_would_drift_and_this_does_not():
    """The case that proves the residue is doing something. 0.7 and 0.3 of 1000.05 both round
    UP, to 1000.06 between them, so the largest line has to come DOWN a cent."""
    shares = {FORUM: D("0.7"), BC: D("0.3")}
    naive = {k: rate(D("1000.05"), s) for k, s in shares.items()}
    assert sum(naive.values()) == D("1000.06"), "independently rounded, it gains a cent"
    parts, left = _both(D("1000.05"), shares)
    assert parts == {FORUM: D("700.03"), BC: D("300.02")}
    assert left == D("0.00")


def test_a_share_that_stops_short_keeps_its_remainder():
    """The bug. Reconciling the parts to the POOL rather than to the allocated whole forced the
    entire pool onto the largest line and deleted what should have stayed behind."""
    parts, left = _both(D("1000.00"), {FORUM: D("0.6")})
    assert parts == {FORUM: D("600.00")}
    assert left == D("400.00"), "400 stays on the pool entity"

    parts, left = _both(D("1000.05"), {FORUM: D("0.7"), BC: D("0.2")})
    assert left == D("100.01")
    assert sum(parts.values()) == D("900.04")


def test_a_retained_rule_sends_nothing_and_keeps_everything():
    parts, left = _both(D("1000.00"), {})
    assert parts == {}
    assert left == D("1000.00")


def test_a_refund_places_its_residue_the_same_way_a_charge_does():
    """A credit to a shared pool is a real thing, and `AllocationContribution.amount` is signed
    for it. Largest by MAGNITUDE, so the rule does not invert below zero."""
    parts, left = _both(D("-2500.33"), {FORUM: D("0.7"), BC: D("0.3")})
    assert left == D("0.00")
    assert parts[FORUM] < parts[BC] < 0, "both sides negative, the forum's the larger debt"
    assert sum(parts.values()) == D("-2500.33")


def test_a_tie_is_broken_predictably_so_a_rebuild_reproduces_the_row():
    """A 50/50 split has two equally large lines. `max` alone would take whichever the dict
    yielded first, and a rebuild has to produce the row it replaced."""
    one = split_amounts(D("1000.01"), {"aaa": D("0.5"), "zzz": D("0.5")})
    other = split_amounts(D("1000.01"), {"zzz": D("0.5"), "aaa": D("0.5")})
    assert one == other
    assert sum(one.values()) == D("1000.01")
    assert one["zzz"] == D("500.01"), "the cent goes to the later key, every time"


def test_thirds_and_tiny_and_zero_pools():
    parts, left = _both(D("100.00"), {"a": D("0.3333"), "b": D("0.3333"), "c": D("0.3334")})
    assert sorted(str(v) for v in parts.values()) == ["33.33", "33.33", "33.34"]
    assert left == D("0.00")
    _both(D("0.01"), {FORUM: D("0.7"), BC: D("0.3")})
    _both(D("0.00"), {FORUM: D("0.7"), BC: D("0.3")})


def test_rate_rounds_half_to_even_not_upward():
    """A ledger rounding a run of half-cents should not drift. Half-up walks upward on every
    one of them; half-even cancels."""
    assert rate(D("0.05"), D("0.5")) == D("0.02")      # 0.025 -> 0.02
    assert rate(D("0.15"), D("0.5")) == D("0.08")      # 0.075 -> 0.08
    assert rate(D("100.00"), D("1")) == D("100.00")
    assert rate(D("100.00"), D("0")) == D("0.00")


@pytest.mark.parametrize("pool", ["0.00", "0.01", "1.00", "33.33", "999.99", "52049.87",
                                  "-1.00", "-0.01", "-52049.87"])
@pytest.mark.parametrize("shares", [
    {FORUM: "1"}, {FORUM: "0.5", BC: "0.5"}, {FORUM: "0.7", BC: "0.3"},
    {FORUM: "0.3333", BC: "0.3333", POOL: "0.3334"},
    {FORUM: "0.6"}, {FORUM: "0.1", BC: "0.1"}, {},
])
def test_the_invariants_hold_across_the_grid(pool, shares):
    """Sixty-three combinations, because the two invariants are the whole contract and a
    hand-picked case is how a partial split went unnoticed in the first place."""
    _both(D(pool), {k: D(v) for k, v in shares.items()})
