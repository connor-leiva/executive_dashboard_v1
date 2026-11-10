"""What's Working (docs/specs/ulrg-reporting-dashboard-port.md §4.5, phase 5), and the funnel
diagnostics the weekly digest scores agents with.

A port of the legacy `pipeline_behavior.py` over the same stored universe as the cohort and
pipeline reports. Three framings over one payload: an agent against the team median, two agents
against each other, and leads that closed in Sisu against leads treated as dead.

TEAM MEDIANS ARE POOLED. The team's median time to first touch is the median over every lead, not
the average of the agents' medians -- averaging medians is exactly how a small agent's outlier
moves the team number. Every median travels with its sample size.
"""
from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict

from .lead_source_pipeline import (STAGE_ORDER, WINDOWS, _window_meta, classify_stage, in_window,
                                   is_stuck)
from .lead_source_report import load_universe, safe_pct

JOURNEY_WINDOW_HOURS = 72
JOURNEY_CAP = 25
PRICE_COVERAGE_MIN = 60.0
SMALL_SAMPLE_CLOSES = 30

# The six transitions the digest coaches against -- `qualified` is skipped on purpose, as it was.
FUNNEL_TRANSITIONS = [
    ("leads_received", "contacted", "Lead to contact"), ("contacted", "appt_set", "Contact to appointment"),
    ("appt_set", "met_with", "Appointment to met"), ("met_with", "showing", "Met to showing"),
    ("showing", "offer_written", "Showing to offer"), ("offer_written", "closed", "Offer to close"),
]
DUR_CAVEAT = ("Follow Up Boss records a duration only for calls placed through a dialer. A call logged "
              "by hand shows 0 seconds even when a conversation happened, so this median covers the calls "
              "with timer data only. Read it beside its count.")
METRICS = [
    ("speed_to_first_min", "Median time to first outbound", "speed", "lower_better", None, "n_with_speed", "min"),
    ("attempts_to_live", "Attempts before first live connect", "speed", "higher_better", None, "n_with_attempts_log", "count"),
    ("pct_5plus_attempts", "Leads with 5+ outbound attempts", "speed", "higher_better", None, "n_leads", "pct"),
    ("multichannel_24h_pct", "Two or more channels in the first 24 hours", "channel", "higher_better", None, "n_leads", "pct"),
    ("median_call_dur_sec", "Median live call length", "channel", "higher_better", DUR_CAVEAT, "n_live_calls", "sec"),
    ("live_to_appt_days", "Live connect to appointment", "timing", "lower_better", None, "n_with_live_to_appt", "days"),
    ("appts_kept_pct", "Appointments kept", "timing", "higher_better", None, "n_appts_with_outcome", "pct"),
    ("median_price", "Median price of the lead", "fairness", "neutral", None, "n_with_price", "money"),
    ("arrival_daytime_pct", "Leads arriving 8am to 8pm", "fairness", "neutral", None, "n_leads", "pct"),
]


def _speed(p):
    fc, c = p.get("first_outbound_at"), p.get("created")
    if fc is None or c is None or fc < c:
        return None
    return (fc - c).total_seconds() / 60.0


def attempts_before_first_live(p) -> int | None:
    """Touches strictly before the first live one. None when the lead never connected -- those
    people are left out of the median, not counted as zero."""
    for i, t in enumerate(p.get("touches") or []):
        if t.get("kind") == "live":
            return i
    return None


def multichannel_24h(p) -> bool:
    c = p.get("created")
    if c is None:
        return False
    cutoff = c + dt.timedelta(hours=24)
    return len({t["channel"] for t in (p.get("touches") or []) if t.get("channel") and t["at"] <= cutoff}) >= 2


def live_to_appt_days(p) -> float | None:
    first_live = next((t["at"] for t in (p.get("touches") or []) if t.get("kind") == "live"), None)
    starts = [a["start"] for a in (p.get("appointments") or []) if a.get("start")]
    if first_live is None or not starts:
        return None
    d = (min(starts) - first_live).total_seconds() / 86400.0
    return d if d >= 0 else None


def logging_diagnostics(people: list[dict]) -> dict:
    """Is this agent's logging thick enough to diagnose? Two signals, either one flags it."""
    n = len(people)
    if not n:
        return {"logging_density": None, "total_touches": 0, "outcome_count": 0,
                "is_hygiene_suspect": False, "hygiene_reason": None}
    touches = sum(len(p.get("touches") or []) for p in people)
    density = touches / n
    outcomes = sum(1 for p in people if (p.get("sisu_state") or "") in ("closed", "under_contract"))
    reason = None
    if n >= 8 and density < 1.5:
        reason = f"{density:.1f} logged touches a lead across {n} leads, too few to read behavior from."
    elif outcomes >= 2 and touches < 3 * outcomes:
        reason = f"{outcomes} deals but only {touches} logged touches: the work is happening outside Follow Up Boss."
    return {"logging_density": round(density, 2), "total_touches": touches, "outcome_count": outcomes,
            "is_hygiene_suspect": reason is not None, "hygiene_reason": reason}


def compute_metric_set(people: list[dict]) -> dict:
    speeds = [m for m in (_speed(p) for p in people) if m is not None]
    attempts = [a for a in (attempts_before_first_live(p) for p in people) if a is not None]
    l2a = [d for d in (live_to_appt_days(p) for p in people) if d is not None]
    durations = [t["duration_sec"] for p in people for t in (p.get("touches") or [])
                 if t.get("channel") == "call" and isinstance(t.get("duration_sec"), (int, float)) and t["duration_sec"] > 0]
    with_outcome = [a for p in people for a in (p.get("appointments") or []) if (a.get("outcome") or "").strip()]
    kept = sum(1 for a in with_outcome if a["outcome"] in ("met", "kept", "completed"))
    prices = [p["price"] for p in people if isinstance(p.get("price"), (int, float))]
    # The legacy clock for "daytime" was the UTC-naive timestamp. Here it is the workspace's own
    # hour, which is what "arrived during the day" means to the people reading it.
    hours = [p["created_local_hour"] for p in people if p.get("created_local_hour") is not None]
    n = len(people)
    return {
        "speed_to_first_min": round(statistics.median(speeds), 1) if speeds else None,
        "attempts_to_live": round(statistics.median(attempts), 1) if attempts else None,
        "pct_5plus_attempts": round(safe_pct(sum(1 for p in people if len(p.get("touches") or []) >= 5), n), 1) if n else None,
        "multichannel_24h_pct": round(safe_pct(sum(1 for p in people if multichannel_24h(p)), n), 1) if n else None,
        "median_call_dur_sec": statistics.median(durations) if durations else None,
        "live_to_appt_days": round(statistics.median(l2a), 1) if l2a else None,
        "appts_kept_pct": round(safe_pct(kept, len(with_outcome)), 1) if with_outcome else None,
        "median_price": statistics.median(prices) if prices else None,
        "arrival_daytime_pct": round(safe_pct(sum(1 for h in hours if 8 <= h < 20), len(hours)), 1) if hours else None,
        "n_leads": n, "n_closed": sum(1 for p in people if (p.get("sisu_state") or "") == "closed"),
        "n_with_speed": len(speeds), "n_with_attempts_log": len(attempts), "n_live_calls": len(durations),
        "n_with_live_to_appt": len(l2a), "n_appts_with_outcome": len(with_outcome), "n_with_price": len(prices),
        "logging_diagnostics": logging_diagnostics(people),
    }


def _counts(people):
    counts = {s: 0 for s in STAGE_ORDER}
    for p in people:
        idx = STAGE_ORDER.index(classify_stage(p))
        for s in STAGE_ORDER[:idx + 1]:
            counts[s] += 1
    return counts


def funnel_diagnostics(agent_people, team_people, floors: dict, *, min_volume: int = 2) -> dict:
    """Each transition's rate against the coaching FLOOR (not the team), and the worst one by leads
    lost -- the leads the agent would have converted at the floor rate, given their volume."""
    a, t = _counts(agent_people), _counts(team_people)
    out = []
    for frm, to, label in FUNNEL_TRANSITIONS:
        a_rate = a[to] / a[frm] if a[frm] else None
        t_rate = t[to] / t[frm] if t[frm] else None
        floor = floors.get(f"{frm}__{to}")
        gap = (floor - a_rate) * 100 if a_rate is not None and floor is not None else None
        out.append({"from": frm, "to": to, "label": label, "agent_rate": a_rate, "team_rate": t_rate,
                    "benchmark_rate": floor, "agent_volume_at_from": a[frm], "gap_pp": gap,
                    "leads_lost": max(0.0, gap / 100.0 * a[frm]) if gap is not None else None,
                    "meets_volume_floor": a[frm] >= min_volume})
    eligible = [x for x in out if x["gap_pp"] is not None and x["gap_pp"] > 0 and x["meets_volume_floor"]]
    worst = max(eligible, key=lambda x: x["leads_lost"]) if eligible else None
    return {"agent_stage_counts": a, "team_stage_counts": t, "transitions": out, "worst_transition": worst}


def build_journey(p: dict, tz) -> dict:
    """One lead's touches on a 0–72 hour axis from arrival. Touches past 72 hours sit on the right
    edge and say so (`beyond_window`), which the strip draws as a continuing cap."""
    c = p.get("created")
    events = []
    for t in p.get("touches") or []:
        if c is None or t["at"] < c:
            continue
        hours = (t["at"] - c).total_seconds() / 3600.0
        events.append({"channel": t.get("channel") or "other", "kind": t.get("kind") or "try",
                       "hours_in": round(hours, 2),
                       "axis_pct": round(min(100.0, min(hours, JOURNEY_WINDOW_HOURS) / JOURNEY_WINDOW_HOURS * 100), 2),
                       "beyond_window": hours > JOURNEY_WINDOW_HOURS, "duration_sec": t.get("duration_sec")})
    return {"person_id": p["person_id"], "name": p.get("full_name") or "Unnamed lead",
            "agent": p.get("assigned_to") or "Unassigned", "agent_key": p.get("agent_id") or "unassigned",
            "created": c.astimezone(tz).date().isoformat() if c else None,
            "sisu_state": p.get("sisu_state"), "stage_key": classify_stage(p),
            "touch_count_24h": sum(1 for e in events if e["hours_in"] <= 24),
            "continues": any(e["beyond_window"] for e in events), "events": events,
            "fub_url": p.get("fub_url")}


def is_dead(p: dict, now: dt.datetime) -> bool:
    """Lost in Sisu, or not closed / under contract / active and stuck -- identical to the pipeline's
    exclusion rule so the two tabs cannot disagree about who is dead."""
    st = p.get("sisu_state") or ""
    return st == "lost" or (st not in ("closed", "under_contract", "active") and is_stuck(p, classify_stage(p), now))


async def whats_working(s, tenant_id, business, group, *, window: str = "all_time", now=None) -> dict:
    uni = await load_universe(s, tenant_id, business.id, group, now=now)
    now, today, tz = uni["now"], uni["today"], uni["tz"]
    people = in_window(uni["people"], window, today)
    for p in people:
        p["created_local_hour"] = p["created"].astimezone(tz).hour if p.get("created") else None
    by_agent: dict[str, list] = defaultdict(list)
    names = {}
    for p in people:
        key = p.get("agent_id") or "unassigned"
        by_agent[key].append(p)
        names[key] = p.get("assigned_to") or "Unassigned"
    closed = [p for p in people if (p.get("sisu_state") or "") == "closed"]
    dead = [p for p in people if is_dead(p, now)]
    team = compute_metric_set(people)
    have_price = sum(1 for p in people if isinstance(p.get("price"), (int, float)))
    coverage = round(safe_pct(have_price, len(people)), 1) if people else 0.0
    agents = sorted(({"key": k, "name": names[k], "metrics": compute_metric_set(v)} for k, v in by_agent.items()),
                    key=lambda a: (-a["metrics"]["n_leads"], a["name"].lower()))
    dead_ids = {p["person_id"] for p in dead}
    return {
        "group": {"key": group.key, "display_name": group.display_name},
        "window": _window_meta(window, today), "windows": [{"key": k, "label": lbl} for k, lbl in WINDOWS],
        "as_of": now.isoformat(timespec="seconds"),
        "metric_definitions": [{"key": k, "label": lbl, "group": g, "direction": d, "caveat": c, "n_key": nk, "unit": u}
                               for k, lbl, g, d, c, nk, u in METRICS],
        "team_metrics": team, "agents": agents,
        "outcome_metrics": {"closed": compute_metric_set(closed), "dead": compute_metric_set(dead)},
        "journeys": {
            "by_agent_closed": {k: [build_journey(p, tz) for p in v if (p.get("sisu_state") or "") == "closed"][:JOURNEY_CAP]
                                for k, v in by_agent.items()},
            "by_agent_dead": {k: [build_journey(p, tz) for p in v if p["person_id"] in dead_ids][:JOURNEY_CAP]
                              for k, v in by_agent.items()},
            "pooled_closed": [build_journey(p, tz) for p in closed][:JOURNEY_CAP * 2],
            "pooled_dead": [build_journey(p, tz) for p in dead][:JOURNEY_CAP * 2],
        },
        "price_coverage_pct": coverage, "price_available": coverage >= PRICE_COVERAGE_MIN,
        "sample_warning": {"team_closed": team["n_closed"], "is_small_sample": team["n_closed"] < SMALL_SAMPLE_CLOSES},
        "journey_window_hours": JOURNEY_WINDOW_HOURS,
    }
