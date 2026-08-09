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

import datetime as dt
import logging

from sqlalchemy import select

from ..models import Tenant, ScorecardGroup, ScorecardMetric, ScorecardValue, ScorecardGoal

log = logging.getLogger("app")


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
    a = attainment(vals[-4:], goal, type_, direction)
    b = attainment(vals[-8:-4], goal, type_, direction)
    return None if a is None or b is None else round(a - b, 1)   # goal 0 → no scoreable trend


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
    if att is None:                          # no scoreable goal (e.g. goal 0) → not a cumulative row
        return None
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


# ── payload assembly (SPEC Part 5.1) ─────────────────────────────────────────
def _fv(v):
    return None if v is None else float(v)


def quarter_of(fiscal_quarters, today: dt.date) -> dict:
    """Resolve the current fiscal quarter from tenant.config.fiscal_quarters (Part 4.7); fall back
    to the calendar quarter with a warning if none is configured / contains today."""
    for q in (fiscal_quarters or []):
        start, end = dt.date.fromisoformat(q["start"]), dt.date.fromisoformat(q["end"])
        if start <= today <= end:
            total = max(1, round((end - start).days / 7))
            closed = min(total, max(0, (today - start).days // 7))
            return {"key": q["key"], "start": q["start"], "end": q["end"], "start_date": start,
                    "weeks_total": total, "weeks_closed": closed, "weeks_left": max(0, total - closed)}
    log.warning("scorecard: no fiscal quarter contains %s — falling back to the calendar quarter", today)
    qn = (today.month - 1) // 3
    start = dt.date(today.year, qn * 3 + 1, 1)
    end = dt.date(today.year, 12, 31) if qn == 3 else dt.date(today.year, qn * 3 + 4, 1) - dt.timedelta(days=1)
    total = max(1, round((end - start).days / 7))
    closed = min(total, max(0, (today - start).days // 7))
    return {"key": f"{today.year}Q{qn + 1}", "start": start.isoformat(), "end": end.isoformat(),
            "start_date": start, "weeks_total": total, "weeks_closed": closed, "weeks_left": max(0, total - closed)}


async def build_scorecard(s, tenant_id, business_id, weeks_param: int, today: dt.date | None = None) -> dict:
    """The GET /ulrg/scorecard payload. All math server-side; the client reverses for display only."""
    today = today or dt.date.today()
    tenant = await s.get(Tenant, tenant_id)
    fq = (tenant.config or {}).get("fiscal_quarters") or []
    q = quarter_of(fq, today)
    wl = q["weeks_left"]
    cur_key = q["key"]

    # per-period goals (Phase C): (metric_id, period_key) → weekly goal; absent → the metric's default.
    # cmap holds the optional per-period CUMULATIVE goal (the whole-period total, e.g. 130 homes/qtr).
    goal_rows = (await s.execute(select(
        ScorecardGoal.metric_id, ScorecardGoal.period_key, ScorecardGoal.goal,
        ScorecardGoal.cumulative_goal).where(ScorecardGoal.tenant_id == tenant_id))).all()
    gmap = {(str(mid), pk): float(gv) for mid, pk, gv, _ in goal_rows}
    cmap = {(str(mid), pk): float(cg) for mid, pk, _, cg in goal_rows if cg is not None}

    def _period_of(d: dt.date):
        iso = d.isoformat()
        for p in fq:
            if p["start"] <= iso <= p["end"]:            # ISO date strings compare correctly
                return p["key"]
        return None

    def _goal_for(mid, period_key, default: float) -> float:
        if period_key is not None:
            v = gmap.get((str(mid), period_key))
            if v is not None:
                return v
        return default

    groups = (await s.execute(select(ScorecardGroup).where(
        ScorecardGroup.tenant_id == tenant_id, ScorecardGroup.business_id == business_id)
        .order_by(ScorecardGroup.sort_order))).scalars().all()
    metrics = (await s.execute(select(ScorecardMetric).where(
        ScorecardMetric.tenant_id == tenant_id, ScorecardMetric.active.is_(True))
        .order_by(ScorecardMetric.sort_order))).scalars().all()
    by_group: dict = {}
    for m in metrics:
        by_group.setdefault(m.group_id, []).append(m)
    values = (await s.execute(select(ScorecardValue).where(
        ScorecardValue.tenant_id == tenant_id))).scalars().all()
    vmap: dict = {}
    for v in values:
        vmap.setdefault(v.metric_id, {})[v.week_start] = _fv(v.value)

    all_weeks = sorted({v.week_start for v in values})
    weeks = all_weeks[-weeks_param:] if weeks_param else all_weeks
    weeks_out = [{"n": w.isocalendar()[1], "start": w.isoformat(),
                  "end": (w + dt.timedelta(days=6)).isoformat(), "label": f"{w.month}/{w.day:02d}"}
                 for w in weeks]
    wkey = f"w{weeks_param}"

    def _cum(all_vals, m, goal):
        if m.type == "snapshot":
            return None
        d = m.direction
        in_q = [vmap.get(m.id, {}).get(w) for w in all_weeks if w >= q["start_date"]]
        qtd = None if q["weeks_closed"] < 2 else cumulative_block(in_q, goal, m.type, d, len(in_q), wl)
        return {"w4": cumulative_block(all_vals, goal, m.type, d, 4, wl),
                wkey: cumulative_block(all_vals, goal, m.type, d, weeks_param, wl), "qtd": qtd}

    groups_out = []
    for g in groups:
        rows, move_rows = [], []
        for m in by_group.get(g.id, []):
            all_vals = [vmap.get(m.id, {}).get(w) for w in all_weeks]
            default_goal, d = float(m.goal), m.direction
            goal = _goal_for(m.id, cur_key, default_goal)   # WEEKLY goal: cells + display + streak
            # cumulative basis: a flow metric with a period-total goal tracks toward that total, so the
            # per-week pace = total / weeks-in-period drives the cumulative block; else the weekly goal.
            cum_goal = cmap.get((str(m.id), cur_key)) if m.type == "flow" else None
            cum_basis = (cum_goal / q["weeks_total"]) if cum_goal is not None else goal
            cum = _cum(all_vals, m, cum_basis)
            rows.append({
                "id": str(m.id), "measurable": m.name, "note": m.note, "goal": goal,
                "cumulative_goal": cum_goal,            # the period total (flow only); None → weekly×weeks
                "direction": d, "type": m.type, "stage": m.stage, "lever": m.lever,
                "owner": {"initials": m.owner_initials} if m.owner_initials else None,
                "source": m.source, "source_synced_at": None,
                "auto": m.resolver_key is not None,     # auto-sourced (no HAND chip); else hand-entered
                "values": [vmap.get(m.id, {}).get(w) for w in weeks],
                # each week keeps its OWN period's goal, so past periods don't recolor when a new
                # period's goal changes (Phase C history)
                "week_goals": [_goal_for(m.id, _period_of(w), default_goal) for w in weeks],
                "trend_4v4": trend_4v4(all_vals, cum_basis, m.type, d),   # trend follows the cumulative basis
                "streak": miss_streak(all_vals, goal, d),                 # streak = missed WEEKLY goal
                "cumulative": cum,
            })
            move_rows.append({"id": str(m.id), "stage": m.stage, "lever": m.lever,
                              "cum": (cum or {}).get(wkey) if cum else None})
        constraint, free_win = move(move_rows)
        groups_out.append({
            "id": str(g.id), "key": g.key, "name": g.name, "is_team_room": g.is_team_room,
            "owner": ({"name": g.owner_name,
                       "photo_url": (f"/ulrg/group/{g.id}/photo?v={str(g.owner_photo_ref)[-8:]}"
                                     if g.owner_photo_ref else None)}
                      if (g.owner_name or g.owner_photo_ref) else None),
            "read": g.read,
            "move": {"constraint_metric_id": constraint["id"] if constraint else None,
                     "free_win_metric_id": free_win["id"] if free_win else None},
            "rows": rows,
        })

    return {
        "weeks": weeks_out,
        "current_week": weeks_out[-1]["n"] if weeks_out else None,
        "quarter": {k: q[k] for k in ("key", "start", "end", "weeks_total", "weeks_closed", "weeks_left")},
        "windows": [4, weeks_param, "qtd"], "default_window": weeks_param,
        "groups": groups_out,
    }
