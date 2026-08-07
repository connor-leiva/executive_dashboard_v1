"""ULRG L10 Scorecard — server-side derivations (SPEC-ulrg-scorecard Part 4).

All scorecard arithmetic lives here; the client renders what it's given (never recomputes
attainment/gap/required). Values arrive CHRONOLOGICAL (ascending, oldest first) — reversal for
display happens only at the render boundary (Part 0.3). Three row types (Part 0.2):
  flow      count of things that happened this week — sums; gap in native units
  rate      a percentage — averages across the window (mean of weekly rates, see Part 8); gap in pts
  snapshot  a point-in-time level or already-cumulative figure — NEVER summed; cumulative is null

The `reset` verdict (Part 4.4) is the highest-value output: it says the gap can't be closed at
the current goal, turning it into a goal-setting (IDS) conversation, not a performance one.
"""
from __future__ import annotations


def _clean(values) -> list[float]:
    """Non-null values in chronological order (nulls = uncollected, skipped — never treated as 0)."""
    return [float(v) for v in values if v is not None]


def attainment(values, goal: float, type_: str, direction: str):
    """Percent of goal for a window. `values` non-null, chronological. None if empty / no goal."""
    vals = _clean(values)
    if not vals or not goal:
        return None
    if type_ == "rate":
        level = sum(vals) / len(vals)                       # mean of weekly rates (Part 0.2, Part 8)
        return (goal / level * 100) if (direction == "lte" and level) else (level / goal * 100)
    total = sum(vals)
    target = goal * len(vals)
    if direction == "lte":
        return (target / total * 100) if total else None
    return total / target * 100


def gap(values, goal: float, type_: str):
    """flow: native units; rate: percentage points; snapshot: None."""
    vals = _clean(values)
    if type_ == "snapshot" or not vals:
        return None
    if type_ == "rate":
        return sum(vals) / len(vals) - goal
    return sum(vals) - goal * len(vals)


def required(values, goal: float, type_: str, weeks_left: int):
    """The weekly number needed over the remaining weeks to erase the gap (Part 4.3)."""
    vals = _clean(values)
    if not vals or not weeks_left:
        return None
    n = len(vals)
    if type_ == "rate":
        return (goal * (n + weeks_left) - (sum(vals) / n) * n) / weeks_left
    g = sum(vals) - goal * n
    return goal + (-g / weeks_left if g < 0 else 0)


def verdict(gap_, req, best):
    if gap_ is None:
        return None
    if gap_ >= 0:
        return "ahead"
    if req is None or best is None:
        return None
    if req <= best:
        return "catchable"
    if req <= best * 1.10:
        return "stretch"
    return "reset"


def trend_4v4(values, goal: float, type_: str, direction: str):
    """Attainment of the last 4 weeks minus the 4 before it (window-independent). None if < 8."""
    vals = _clean(values)
    if len(vals) < 8:
        return None
    return round(attainment(vals[-4:], goal, type_, direction)
                 - attainment(vals[-8:-4], goal, type_, direction), 1)


def miss_streak(values, goal: float, direction: str) -> int:
    """Consecutive weeks from the NEWEST end failing the goal test. Nulls skipped, not misses."""
    streak = 0
    for v in reversed(values):                              # newest end = end of chronological array
        if v is None:
            continue
        ok = (v <= goal) if direction == "lte" else (v >= goal)
        if ok:
            break
        streak += 1
    return streak


def cumulative_block(values, goal: float, type_: str, direction: str, window: int, weeks_left: int):
    """The counted-band summary for a window: the most-recent `window` weeks (the leftmost N in
    render). Returns None for a snapshot row — the API must never emit cumulative for one (Part 0.2)."""
    if type_ == "snapshot":
        return None
    counted = list(values)[-window:] if window else list(values)
    vals = _clean(counted)
    if not vals:
        return None
    att = attainment(vals, goal, type_, direction)
    g = gap(vals, goal, type_)
    best = max(vals)
    req = required(vals, goal, type_, weeks_left)
    is_rate = type_ == "rate"
    return {
        "n": len(vals),
        "actual": round(sum(vals) / len(vals), 1) if is_rate else round(sum(vals)),
        "target": round(goal, 1) if is_rate else round(goal * len(vals)),
        "attain": round(att, 1) if att is not None else None,
        "gap": round(g, 1) if is_rate else round(g),
        "required": round(req, 1) if req is not None else None,
        "best": round(best, 1) if is_rate else round(best),
        "verdict": verdict(g, req, best),
    }


def move(group_rows):
    """(constraint, free_win) for a group's Move card (Part 4.9). The constraint is the earliest
    funnel stage below 100% (ranked by funnel position, not gap — a downstream stage can't be
    fixed while an upstream one is starved). The free win is the lowest-attainment behavior row."""
    def cum_attain(r):
        c = r.get("cum") or r.get("cumulative")
        w = (c or {}).get("attain") if isinstance(c, dict) else None
        return w

    flows = sorted([r for r in group_rows if r.get("stage")], key=lambda r: r["stage"])
    constraint = next((r for r in flows if (cum_attain(r) is not None and cum_attain(r) < 100)), None)
    behav = sorted([r for r in group_rows
                    if r.get("lever") == "behavior" and cum_attain(r) is not None and cum_attain(r) < 100],
                   key=lambda r: cum_attain(r))
    return constraint, (behav[0] if behav else None)
