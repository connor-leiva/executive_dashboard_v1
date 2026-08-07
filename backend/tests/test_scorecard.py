"""ULRG L10 Scorecard derivations (SPEC-ulrg-scorecard Part 4). The headline case is Part 7's
acceptance math: Davis Appointments Met over 8 weeks."""
from app.services import scorecard


def test_davis_appointments_met_part7_acceptance():
    v = [34, 20, 25, 19, 20, 18, 18, 33]          # ascending, oldest first
    c = scorecard.cumulative_block(v, 30, "flow", "gte", window=13, weeks_left=5)
    assert c["n"] == 8 and c["actual"] == 187 and c["target"] == 240
    assert c["attain"] == 77.9 and c["gap"] == -53 and c["required"] == 40.6
    assert c["best"] == 34 and c["verdict"] == "reset"
    assert scorecard.trend_4v4(v, 30, "flow", "gte") == -7.5


def test_verdict_boundary_at_best_times_1_10():
    assert scorecard.verdict(5, 40, 34) == "ahead"        # gap >= 0
    assert scorecard.verdict(-1, 34, 34) == "catchable"   # required <= best
    assert scorecard.verdict(-1, 37.4, 34) == "stretch"   # required <= best*1.10 (37.4)
    assert scorecard.verdict(-1, 37.5, 34) == "reset"     # just above → reset
    assert scorecard.verdict(None, 1, 1) is None


def test_snapshot_never_gets_a_cumulative_block():
    assert scorecard.cumulative_block([90, 88, 92], 85, "snapshot", "gte", 13, 5) is None
    assert scorecard.gap([90, 88, 92], 85, "snapshot") is None


def test_goalless_row_has_no_cumulative_block():
    # A track-only measurable (goal 0, e.g. "# of Mastermind RSVPs") has no scoreable target, so
    # there is nothing to attain against. The block must be None — an emitted block with attain=None
    # crashes the client, which renders the number as a percentage. Regression guard.
    assert scorecard.attainment([20, 25, 20], 0, "flow", "gte") is None
    assert scorecard.cumulative_block([20, 25, 20], 0, "flow", "gte", 13, 13) is None


def test_rate_averages_across_the_window():
    assert round(scorecard.attainment([20, 22, 24], 22, "rate", "gte"), 1) == 100.0   # mean 22 / goal 22
    assert round(scorecard.attainment([2, 3, 4], 3, "rate", "lte"), 1) == 100.0       # lower is better
    assert scorecard.gap([20, 22, 24], 22, "rate") == 0.0                             # percentage points


def test_null_is_not_zero():
    assert scorecard.attainment([None, 30, None, 30], 30, "flow", "gte") == 100.0     # nulls skipped
    # two newest weeks miss; the null between them is skipped, the older 30 stops the streak
    assert scorecard.miss_streak([30, None, 10, 10], 30, "gte") == 2


def test_move_ranks_constraint_by_funnel_position_not_gap():
    rows = [
        {"stage": 3, "lever": "volume", "cum": {"attain": 50}},    # worst gap, but downstream
        {"stage": 1, "lever": "behavior", "cum": {"attain": 90}},  # earliest stage below 100
        {"stage": 2, "lever": "volume", "cum": {"attain": 100}},
    ]
    constraint, free_win = scorecard.move(rows)
    assert constraint["stage"] == 1        # a starved upstream stage, not the biggest downstream hole
    assert free_win["stage"] == 1          # lowest-attainment behavior row
