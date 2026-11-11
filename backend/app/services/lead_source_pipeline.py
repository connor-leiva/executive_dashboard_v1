"""Team and agent pipeline for a lead-source group (docs/specs/ulrg-reporting-dashboard-port.md §4.4, phase 4).

A port of the legacy `pipeline.py`, rule for rule, over the universe `lead_source_report` loads from
Postgres. What changed is only what Axcion is: agents are keyed by their Axcion id (names collide
and travel badly in a URL), the preset windows are read in the workspace's calendar, and the Follow
Up Boss link is built from the account domain the sync learned.

STUCK IS A PROXY, and the payload says so: days since the lead was CREATED, plus no touch in 7 days.
Follow Up Boss stage history is not synced, so "40 days in Met" can mean 40 days since arrival.
"""
from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict

from . import metrics as _metrics
from .lead_source_report import load_universe, safe_pct

STAGE_ORDER = ["leads_received", "contacted", "qualified", "appt_set", "met_with", "showing",
               "offer_written", "closed"]
STAGE_LABELS = {
    "leads_received": "New leads received", "contacted": "Contacted (2-way)",
    "qualified": "Qualified / buyer connection", "appt_set": "Appointment set",
    "met_with": "Met with client", "showing": "Showings conducted", "offer_written": "Offer written",
    "closed": "Closed transactions",
}
STAGE_DESCRIPTIONS = {
    "leads_received": "Routed in the window", "contacted": "An outbound touch, or Follow Up Boss marked them contacted",
    "qualified": "Stage says qualified, buyer connection or hot", "appt_set": "An appointment, or the stage says so",
    "met_with": "Met, consulted, or an appointment kept", "showing": "At least one home shown",
    "offer_written": "An offer, or under contract in Sisu", "closed": "Closed in Sisu",
}
_KW_MET_WITH = ("met", "consult")
_KW_SHOWING = ("showing", "tour", "view")
_KW_OFFERS = ("offer", "negotiat")
_KW_QUALIFIED = ("qualified", "buyer connection", "hot")
_KW_APPT = ("appointment", "appt")
_KW_UC = ("under contract", "pending", "escrow", "contract")

STUCK_THRESHOLDS_DAYS = {"leads_received": 1, "contacted": 5, "qualified": 7, "appt_set": 14,
                         "met_with": 21, "showing": 14, "offer_written": 7}
STUCK_INACTIVITY_DAYS = 7
INDUSTRY_BENCHMARKS = {
    "contacted": {"q1": 50.0, "median": 65.0, "q3": 80.0},
    "qualified": {"q1": 40.0, "median": 55.0, "q3": 70.0},
    "appt_set": {"q1": 30.0, "median": 45.0, "q3": 60.0},
    "met_with": {"q1": 40.0, "median": 55.0, "q3": 70.0},
    "showing": {"q1": 50.0, "median": 65.0, "q3": 80.0},
    "offer_written": {"q1": 30.0, "median": 40.0, "q3": 55.0},
    "closed": {"q1": 30.0, "median": 50.0, "q3": 70.0},
}
CRITICAL_PICKUP_RATE_PCT = 30.0
CRITICAL_UNCONTACTED_COUNT = 5
CRITICAL_PREDICTED_CVR_PCT = 3.0
BOTTLENECK_CRIT_PP, BOTTLENECK_WARN_PP, BOTTLENECK_MILD_PP = -20.0, -10.0, -3.0
TIER_ORDER = ["critical", "needs_coaching", "on_track", "strong"]
TIER_LABELS = {"critical": "Critical", "needs_coaching": "Needs coaching", "on_track": "On track",
               "strong": "Strong"}
SPEED_BUCKETS = [("u5m", "Under 5 min", 0, 5), ("u1h", "5–60 min", 5, 60), ("u24h", "1–24 hr", 60, 1440),
                 ("u3d", "1–3 days", 1440, 4320), ("o3d", "Over 3 days", 4320, float("inf"))]
STUCK_FILTERS = ["all_active", "stuck_at_leads_received", "stuck_at_contacted", "stuck_at_qualified",
                 "stuck_at_appt_set", "stuck_at_met_with", "stuck_at_showing", "stuck_at_offer_written",
                 "no_show"]
STUCK_FILTER_LABELS = {
    "all_active": "All active", "stuck_at_leads_received": "Uncontacted",
    "stuck_at_contacted": "Contacted, no follow-up", "stuck_at_qualified": "Qualified, no follow-up",
    "stuck_at_appt_set": "Appointment set, stuck", "stuck_at_met_with": "Met, no offer",
    "stuck_at_showing": "Showing, no offer", "stuck_at_offer_written": "Offer, no update",
    "no_show": "No-show after appointment",
}
_STAGE_FILTER_MAP = {f"stuck_at_{s}": s for s in STAGE_ORDER[:-1]}
WINDOWS = [("all_time", "All time"), ("rolling_30", "Last 30 days"), ("rolling_90", "Last 90 days"),
           ("last_4mo", "Last 4 months"), ("ytd", "Year to date")]
STUCK_PROXY_NOTE = ("Days in stage are counted from the day the lead arrived, because Follow Up Boss stage "
                    "history isn't synced. A lead shown as 40 days in Met may have reached Met recently.")


def _has_any(stage: str, tokens) -> bool:
    return any(t in stage for t in tokens)


def window_bounds(key: str, today: dt.date) -> tuple[dt.date | None, dt.date | None]:
    """The leads a report reads, by the day they arrived, in the workspace's calendar.

    `key` is either one of the legacy presets (the digest still reads `rolling_30`) or the app's
    date-bar period -- `mtd`, `qtd`, `last_7`, `c:2026-02-01:2026-11-11` and the rest -- which
    `metrics._period_range` reads exactly as every other page does, so the report and the bar can't
    disagree about which days are in. Last 4 months is the first of this month, rolled back four
    months, through today -- not 120 days."""
    if key == "all_time":
        return None, None
    if key == "rolling_30":
        return today - dt.timedelta(days=30), today
    if key == "rolling_90":
        return today - dt.timedelta(days=90), today
    if key == "last_4mo":
        y, m = today.year, today.month - 4
        while m <= 0:
            m += 12
            y -= 1
        return dt.date(y, m, 1), today
    return _metrics._period_range(_metrics.canonical_period(key), today)


def in_window(people: list[dict], key: str, today: dt.date) -> list[dict]:
    start, end = window_bounds(key, today)
    return [p for p in people if (start is None or p["created_date"] >= start)
            and (end is None or p["created_date"] <= end)]


def classify_stage(p: dict) -> str:
    """The deepest stage a lead reached. Sisu decides closed and under contract before any FUB
    stage does; a Follow Up Boss "Closed" with no Sisu close is not a close here."""
    sisu = (p.get("sisu_state") or "").lower()
    if sisu == "closed":
        return "closed"
    stage = (p.get("stage") or "").strip().lower()
    deals, appts = p.get("deals") or [], p.get("appointments") or []
    if sisu == "under_contract" or _has_any(stage, _KW_OFFERS) or _has_any(stage, _KW_UC):
        return "offer_written"
    for d in deals:
        ds = (d.get("stage") or "").lower()
        if _has_any(ds, _KW_OFFERS) or _has_any(ds, _KW_UC):
            return "offer_written"
    if _has_any(stage, _KW_SHOWING) or any("showing" in (a.get("outcome") or "") for a in appts):
        return "showing"
    if _has_any(stage, _KW_MET_WITH) or any((a.get("outcome") or "") in ("met", "kept", "completed", "converted")
                                            for a in appts):
        return "met_with"
    if appts or _has_any(stage, _KW_APPT):
        return "appt_set"
    if _has_any(stage, _KW_QUALIFIED):
        return "qualified"
    if p.get("contacted_flag") or p.get("first_outbound_at"):
        return "contacted"
    return "leads_received"


def is_stuck(p: dict, stage: str, now: dt.datetime) -> bool:
    if stage == "closed":
        return False
    threshold = STUCK_THRESHOLDS_DAYS.get(stage)
    created = p.get("created")
    if threshold is None or created is None or (now - created).days < threshold:
        return False
    last = p.get("last_activity_at")
    return last is None or (now - last).days >= STUCK_INACTIVITY_DAYS


def age_severity(days: int) -> str:
    """The legacy labels, kept as written although they are not in age order."""
    if days <= 3:
        return "hot"
    if days <= 14:
        return "cool"
    if days <= 30:
        return "warm"
    return "cold"


def stage_counts(leads: list[dict]) -> dict[str, int]:
    counts = {s: 0 for s in STAGE_ORDER}
    for p in leads:
        idx = STAGE_ORDER.index(classify_stage(p)) + 1
        for s in STAGE_ORDER[:idx]:
            counts[s] += 1
    return counts


def stage_conv(counts: dict[str, int]) -> dict[str, float]:
    return {STAGE_ORDER[i]: round(safe_pct(counts[STAGE_ORDER[i]], counts[STAGE_ORDER[i - 1]]), 1)
            for i in range(1, len(STAGE_ORDER))}


def vs_benchmark(conv: dict[str, float]) -> dict[str, float]:
    return {k: round(v - INDUSTRY_BENCHMARKS[k]["median"], 1) for k, v in conv.items() if k in INDUSTRY_BENCHMARKS}


def predicted_cvr(counts: dict[str, int], conv: dict[str, float], closed: int) -> float:
    """Closed ÷ leads once there is a close; before that, the product of four rates with a GENUINE
    zero replaced by the fallback -- so no met-with data forecasts 0.55 × 0.65 × 0.40 × 0.50."""
    leads = counts["leads_received"]
    if leads == 0:
        return 0.0
    if closed >= 1:
        return round(closed / leads * 100.0, 1)
    rate = 1.0
    for key, fallback in (("met_with", 0.55), ("showing", 0.65), ("offer_written", 0.40), ("closed", 0.50)):
        rate *= (conv.get(key, 0.0) / 100.0) or fallback
    return round(rate * 100.0, 1)


def bottleneck(vs: dict[str, float]) -> dict:
    if not vs:
        return {"stage": None, "label": "", "severity": "none", "gap_pp": None}
    worst = min(vs, key=vs.get)
    gap = vs[worst]
    sev = ("crit" if gap < BOTTLENECK_CRIT_PP else "warn" if gap < BOTTLENECK_WARN_PP
           else "mild" if gap < BOTTLENECK_MILD_PP else "none")
    # "Contacted (2-way), 51 pts below" -- words, not "(-51pp)".
    side = "below" if gap < 0 else "above"
    return {"stage": worst, "label": f"{STAGE_LABELS[worst]}, {abs(gap):.0f} pts {side}", "severity": sev, "gap_pp": gap}


def speed_info(people: list[dict]) -> dict:
    speeds, counts = [], {b[0]: 0 for b in SPEED_BUCKETS}
    for p in people:
        fc, c = p.get("first_outbound_at"), p.get("created")
        if fc is None or c is None or fc < c:
            continue
        m = (fc - c).total_seconds() / 60.0
        speeds.append(m)
        for key, _l, lo, hi in SPEED_BUCKETS:
            if lo <= m < hi:
                counts[key] += 1
                break
    n = len(speeds)
    return {"buckets": [{"key": k, "label": lbl, "count": counts[k]} for k, lbl, _a, _b in SPEED_BUCKETS],
            "median_min": round(statistics.median(speeds)) if speeds else None,
            "pct_under_5min": round(sum(1 for x in speeds if x <= 5.0) / n * 100) if n else 0, "n": n}


def trend(people: list[dict], now: dt.datetime, weeks: int = 12) -> dict:
    buckets = [[0, 0] for _ in range(weeks)]
    for p in people:
        c = p.get("created")
        if c is None:
            continue
        age = (now - c).days
        idx = weeks - 1 - (age // 7) if age >= 0 else -1
        if 0 <= idx < weeks:
            buckets[idx][0] += 1
            buckets[idx][1] += (p.get("sisu_state") or "") == "closed"
    pts = [round(c / n * 100.0, 1) if n else 0.0 for n, c in buckets]
    recent, prior = pts[-4:], pts[-8:-4]
    return {"current_pct": pts[-1] if pts else 0.0, "sparkline_points": pts,
            "weekly_leads": [n for n, _c in buckets],
            "delta_last_30d_pp": round(sum(recent) / 4 - sum(prior) / 4, 1) if weeks >= 8 else 0.0}


class AgentStats:
    def __init__(self, key: str, name: str, leads: list[dict], now: dt.datetime):
        self.key, self.name, self.leads = key, name, leads
        self.counts = stage_counts(leads)
        self.conv = stage_conv(self.counts)
        self.vs = vs_benchmark(self.conv)
        total, contacted = self.counts["leads_received"], self.counts["contacted"]
        self.pickup = round(safe_pct(contacted, total), 1)
        self.uncontacted = total - contacted
        self.closed = self.counts["closed"]
        self.raw_cvr = round(safe_pct(self.closed, total), 1)
        self.predicted = predicted_cvr(self.counts, self.conv, self.closed)
        sp = speed_info(leads)
        self.speed_median, self.pct_u5 = sp["median_min"], sp["pct_under_5min"]
        self.stuck = sum(1 for p in leads if is_stuck(p, classify_stage(p), now))
        self.bottleneck = bottleneck(self.vs)
        self.tier = self._tier()

    def _tier(self) -> str:
        if (self.pickup < CRITICAL_PICKUP_RATE_PCT or self.uncontacted > CRITICAL_UNCONTACTED_COUNT
                or self.predicted < CRITICAL_PREDICTED_CVR_PCT):
            return "critical"
        if sum(1 for k, v in self.conv.items() if v >= INDUSTRY_BENCHMARKS[k]["q3"]) >= 3:
            return "strong"
        if min(self.vs.values(), default=0.0) < -20.0:
            return "needs_coaching"
        return "on_track"

    def tier_reason(self) -> str:
        if self.tier == "critical":
            t = []
            if self.pickup < CRITICAL_PICKUP_RATE_PCT:
                t.append(f"pickup {self.pickup:.0f}%")
            if self.uncontacted > CRITICAL_UNCONTACTED_COUNT:
                t.append(f"{self.uncontacted} uncontacted")
            if self.predicted < CRITICAL_PREDICTED_CVR_PCT:
                t.append(f"predicted close {self.predicted:.1f}%")
            return ", ".join(t)
        if self.tier == "strong":
            return "Top quartile on three or more stage conversions"
        if self.tier == "needs_coaching":
            return self.bottleneck.get("label") or "A stage well below benchmark"
        return "Holding the benchmark line"


def aggregate_by_agent(people: list[dict], now: dt.datetime) -> dict[str, AgentStats]:
    groups: dict[str, list[dict]] = defaultdict(list)
    names: dict[str, str] = {}
    for p in people:
        key = p.get("agent_id") or "unassigned"
        groups[key].append(p)
        names[key] = p.get("assigned_to") or "Unassigned"
    return {k: AgentStats(k, names[k], v, now) for k, v in groups.items()}


def team_funnel(agents: dict[str, AgentStats], people: list[dict], now: dt.datetime) -> dict:
    totals = {s: 0 for s in STAGE_ORDER}
    for a in agents.values():
        for k, v in a.counts.items():
            totals[k] += v
    conv = stage_conv(totals)
    bottleneck_stage, worst = None, 101.0
    for k in STAGE_ORDER[1:]:
        if conv[k] < worst:
            worst, bottleneck_stage = conv[k], k
    stuck_by = defaultdict(int)
    for p in people:
        st = classify_stage(p)
        if is_stuck(p, st, now):
            stuck_by[st] += 1
    leads, closed = totals["leads_received"], totals["closed"]
    stages = []
    for i, k in enumerate(STAGE_ORDER):
        bench = INDUSTRY_BENCHMARKS.get(k, {}).get("median")
        pct = 100.0 if i == 0 else conv[k]
        stages.append({"key": k, "label": STAGE_LABELS[k], "description": STAGE_DESCRIPTIONS[k],
                       "count": totals[k], "conv_pct_from_prev": pct, "benchmark_median_pct": bench,
                       "vs_benchmark_pp": round(pct - bench, 1) if bench is not None and i else None,
                       "is_bottleneck": k == bottleneck_stage, "stuck_count": stuck_by.get(k, 0)})
    return {"stages": stages, "team_cvr": round(safe_pct(closed, leads), 1),
            # Always the blended forecast for the TEAM, closes or not -- the legacy team rule, which
            # differs from the per-agent one. Zero leads is zero, not the fallbacks' 7.1%.
            "team_predicted_cvr": predicted_cvr(totals, conv, 0),
            "team_pickup_rate_pct": round(safe_pct(totals["contacted"], leads), 1)}


def coaching_reasons(a: AgentStats) -> list[str]:
    """Plain sentences a coach can read aloud, as the legacy summary wrote them."""
    out = []
    if a.tier == "critical":
        if a.pickup < CRITICAL_PICKUP_RATE_PCT:
            out.append(f"Pickup is {a.pickup:.0f}%, under the {CRITICAL_PICKUP_RATE_PCT:.0f}% floor: "
                       f"{a.uncontacted} of {a.counts['leads_received']} leads have no outbound touch.")
        if a.uncontacted > CRITICAL_UNCONTACTED_COUNT:
            out.append(f"{a.uncontacted} leads are still uncontacted.")
        if a.predicted < CRITICAL_PREDICTED_CVR_PCT:
            out.append(f"Predicted close rate is {a.predicted:.1f}%, under the {CRITICAL_PREDICTED_CVR_PCT:.0f}% floor.")
    elif a.tier == "needs_coaching":
        b = a.bottleneck
        if b.get("stage"):
            pct, bench = a.conv.get(b["stage"], 0.0), INDUSTRY_BENCHMARKS[b["stage"]]["median"]
            out.append(f"{STAGE_LABELS[b['stage']]} converts at {pct:.0f}%, {bench - pct:.0f} points under "
                       f"the {bench:.0f}% benchmark.")
        if a.stuck >= 4:
            out.append(f"{a.stuck} leads have had no touch in {STUCK_INACTIVITY_DAYS}+ days.")
    elif a.tier == "strong":
        tops = [STAGE_LABELS[k] for k, v in a.conv.items() if v >= INDUSTRY_BENCHMARKS[k]["q3"]]
        if tops:
            out.append(f"Top quartile on {len(tops)} stages: {', '.join(tops[:3]).lower()}.")
        if a.closed:
            out.append(f"{a.closed} closed in Sisu.")
    else:
        if a.pickup >= 70:
            out.append(f"Pickup is {a.pickup:.0f}% ({a.counts['contacted']} of {a.counts['leads_received']}).")
        if a.stuck == 0:
            out.append("No stuck leads.")
    return out or ["No flagged gaps."]


def _agent_row(a: AgentStats) -> dict:
    return {
        "key": a.key, "agent": a.name, "tier": a.tier, "tier_label": TIER_LABELS[a.tier],
        "tier_reason": a.tier_reason(), "stage_counts": dict(a.counts),
        "stage_conv": {k: {"pct": a.conv.get(k, 0.0), "vs_benchmark_pp": a.vs.get(k, 0.0)} for k in STAGE_ORDER[1:]},
        "biggest_bottleneck": a.bottleneck, "stuck_count": a.stuck,
        "stuck_severity": "crit" if a.stuck >= 10 else "warn" if a.stuck >= 4 else "ok",
        "predicted_cvr": a.predicted, "raw_cvr": a.raw_cvr, "pickup_rate": a.pickup,
        "uncontacted_count": a.uncontacted, "lead_count": a.counts["leads_received"],
        "closed_count": a.closed, "speed_median_min": a.speed_median,
    }


def _window_meta(key: str, today: dt.date) -> dict:
    legacy = dict(WINDOWS)
    if key not in legacy:
        key = _metrics.canonical_period(key)
    start, end = window_bounds(key, today)
    return {"key": key, "label": legacy.get(key) or _metrics.period_label(key),
            "start": start.isoformat() if start else None, "end": end.isoformat() if end else None}


def program_meta(people: list[dict], group, today: dt.date) -> dict:
    """The whole program, whatever the date range: how many leads it holds, the day it starts, and
    today in the workspace's calendar. The page tells "no leads at all" from "none in this range"
    with it, and widens the range to `since`-`until` when asked."""
    first = min((p["created_date"] for p in people if p.get("created_date")), default=None)
    since = group.program_start or first
    return {"since": since.isoformat() if since else None, "until": today.isoformat(), "leads": len(people)}


async def team_pipeline(s, tenant_id, business, group, *, window: str = "all_time", now=None) -> dict:
    uni = await load_universe(s, tenant_id, business.id, group, now=now)
    now, today = uni["now"], uni["today"]
    people = in_window(uni["people"], window, today)
    agents = aggregate_by_agent(people, now)
    funnel = team_funnel(agents, people, now)
    rank = {t: i for i, t in enumerate(TIER_ORDER)}
    ordered = sorted(agents.values(), key=lambda a: (rank[a.tier], -a.stuck, a.name.lower()))
    tiers = {t: {"label": TIER_LABELS[t], "count": 0, "agents": []} for t in TIER_ORDER}
    for a in ordered:
        tiers[a.tier]["count"] += 1
        tiers[a.tier]["agents"].append({"key": a.key, "name": a.name, "lead_count": a.counts["leads_received"],
                                        "stuck_count": a.stuck, "pickup_rate_pct": a.pickup,
                                        "predicted_cvr_pct": a.predicted, "closed_count": a.closed,
                                        "reasons": coaching_reasons(a)})
    return {
        "group": {"key": group.key, "display_name": group.display_name},
        "window": _window_meta(window, today), "windows": [{"key": k, "label": lbl} for k, lbl in WINDOWS],
        "program": program_meta(uni["people"], group, today),
        "as_of": now.isoformat(timespec="seconds"),
        "team_funnel": funnel,
        "summary": {"agents": len(agents), "leads": funnel["stages"][0]["count"],
                    "closed": funnel["stages"][-1]["count"], "stuck": sum(a.stuck for a in agents.values()),
                    "pickup_rate_pct": funnel["team_pickup_rate_pct"],
                    "predicted_cvr": funnel["team_predicted_cvr"], "team_cvr": funnel["team_cvr"]},
        "tiers": tiers, "agents": [_agent_row(a) for a in ordered],
        "stage_labels": STAGE_LABELS, "stage_order": STAGE_ORDER, "benchmarks": INDUSTRY_BENCHMARKS,
        "stuck_note": STUCK_PROXY_NOTE,
    }


def _lead_detail(p: dict, now: dt.datetime, tz) -> dict:
    st = classify_stage(p)
    created = p.get("created")
    age = (now - created).days if created else 0
    last = p.get("last_activity_at")
    touches = [{"kind": t["kind"], "channel": t["channel"], "label": t["label"]} for t in (p.get("touches") or [])[-5:]]
    return {"person_id": p["person_id"], "name": p.get("full_name") or "Unnamed lead",
            "stage_key": st, "stage_label": STAGE_LABELS[st], "fub_stage": p.get("stage") or None,
            "source": p.get("source"), "routed": created.astimezone(tz).date().isoformat() if created else None,
            "age_days": age, "age_severity": age_severity(age),
            "last_touch": last.astimezone(tz).date().isoformat() if last else None,
            "days_since_touch": (now - last).days if last else None,
            "last_5_touches": touches, "fub_url": p.get("fub_url")}


def matches_filter(p: dict, key: str, now: dt.datetime) -> bool:
    st = classify_stage(p)
    if st == "closed" or (p.get("sisu_state") or "") == "lost":
        return False
    if key == "all_active":
        return True
    target = _STAGE_FILTER_MAP.get(key)
    if target is not None:
        return st == target and is_stuck(p, st, now)
    if key == "no_show":
        if st != "appt_set":
            return False
        return any(a.get("start") and a["start"] < now and (a.get("outcome") or "") not in ("met", "kept", "completed")
                   for a in (p.get("appointments") or []))
    return False


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _highlights(a: AgentStats, team_pickup: float, rank: int, rank_of: int) -> list[dict]:
    """The agent page's four tiles. Speed to first contact has its own card below them, so the
    tiles carry predicted close in its place rather than say speed twice."""
    delta = round(a.pickup - team_pickup, 1)
    mw = a.conv.get("met_with", 0.0)
    mw_bench = INDUSTRY_BENCHMARKS["met_with"]["median"]
    mw_delta = round(mw - mw_bench, 1)
    return [
        {"key": "pickup_rate", "label": "Pickup rate", "value": f"{a.pickup:.0f}%",
         "context": f"{a.counts['contacted']} of {a.counts['leads_received']} leads contacted",
         "delta": f"{delta:+.0f} pts vs team" if delta else "Same as team",
         "direction": "pos" if delta >= 0 else "neg",
         "severity": "alert" if a.pickup < CRITICAL_PICKUP_RATE_PCT else "warn" if a.pickup < 50 else "none"},
        {"key": "predicted", "label": "Predicted close", "value": f"{a.predicted:.1f}%".replace(".0%", "%"),
         "context": f"{_ordinal(rank)} of {_plural(rank_of, 'agent')}",
         "delta": "" if a.closed else "a forecast, no close yet", "direction": "neu", "severity": "none"},
        {"key": "met_with", "label": "Appointment to met", "value": f"{mw:.0f}%",
         "context": f"{a.counts['met_with']} met from {_plural(a.counts['appt_set'], 'appointment')}",
         "delta": f"{mw_delta:+.0f} pts vs {mw_bench:.0f}% benchmark", "direction": "pos" if mw_delta >= 0 else "neg",
         "severity": "alert" if mw_delta < BOTTLENECK_CRIT_PP else "warn" if mw_delta < BOTTLENECK_WARN_PP else "none"},
        {"key": "stuck", "label": "Stuck", "value": f"{a.stuck}",
         "context": f"no touch in {STUCK_INACTIVITY_DAYS}+ days" if a.stuck else "Pipeline clean",
         "delta": "", "direction": "neu",
         "severity": "alert" if a.stuck >= 10 else "warn" if a.stuck >= 4 else "none"},
    ]


async def agent_pipeline(s, tenant_id, business, group, agent_key: str, *, window: str = "all_time",
                         now=None) -> dict | None:
    uni = await load_universe(s, tenant_id, business.id, group, now=now)
    now, today, tz = uni["now"], uni["today"], uni["tz"]
    people = in_window(uni["people"], window, today)
    agents = aggregate_by_agent(people, now)
    a = agents.get(agent_key)
    if a is None:
        return None
    funnel = team_funnel(agents, people, now)
    team_stages = {st["key"]: st for st in funnel["stages"]}
    ordered = sorted(agents.values(), key=lambda x: (-x.predicted, -x.raw_cvr))
    vertical, prev = [], None
    for i, k in enumerate(STAGE_ORDER):
        count = a.counts[k]
        conv = a.conv.get(k) if i else None
        vs = a.vs.get(k, 0.0)
        sev = ("none" if i == 0 else "crit" if vs < BOTTLENECK_CRIT_PP else "warn" if vs < BOTTLENECK_WARN_PP
               else "mild" if vs < BOTTLENECK_MILD_PP else "none")
        stuck = sum(1 for p in a.leads if classify_stage(p) == k and is_stuck(p, k, now))
        vertical.append({"stage_key": k, "stage_label": STAGE_LABELS[k], "description": STAGE_DESCRIPTIONS[k],
                         "count": count, "of": prev, "conv_pct_from_prev": conv,
                         "benchmark_median_pct": INDUSTRY_BENCHMARKS.get(k, {}).get("median"),
                         "vs_benchmark_pp": vs if i else None, "severity": sev, "stuck_count": stuck,
                         "is_bottleneck": a.bottleneck.get("stage") == k, "team_count": team_stages[k]["count"]})
        prev = count
    filters = {}
    for key in STUCK_FILTERS:
        matched = [p for p in a.leads if matches_filter(p, key, now)]
        matched.sort(key=lambda p: p.get("created") or now)
        filters[key] = {"label": STUCK_FILTER_LABELS[key], "count": len(matched),
                        "leads": [_lead_detail(p, now, tz) for p in matched[:200]]}
    non_all = {k: v for k, v in filters.items() if k != "all_active"}
    default = max(non_all, key=lambda k: non_all[k]["count"]) if any(v["count"] for v in non_all.values()) else "all_active"
    sp = speed_info(a.leads)
    team_sp = speed_info(people)
    tr, team_tr = trend(a.leads, now), trend(people, now)
    rank = next(i for i, x in enumerate(ordered, 1) if x.key == a.key)
    return {
        "group": {"key": group.key, "display_name": group.display_name},
        "window": _window_meta(window, today), "windows": [{"key": k, "label": lbl} for k, lbl in WINDOWS],
        "program": program_meta(uni["people"], group, today),
        "as_of": now.isoformat(timespec="seconds"),
        "agent": {"key": a.key, "name": a.name, "tier": a.tier, "tier_label": TIER_LABELS[a.tier],
                  "tier_reason": a.tier_reason(), "lead_count": a.counts["leads_received"],
                  "rank": rank, "rank_of": len(ordered),
                  "predicted_cvr": a.predicted, "raw_cvr": a.raw_cvr},
        "highlights": _highlights(a, funnel["team_pickup_rate_pct"], rank, len(ordered)),
        "funnel": vertical,
        "stuck_leads_by_filter": filters, "default_stuck_filter": default,
        "speed_to_first_contact": {**sp, "team_median_min": team_sp["median_min"],
                                   "team_pct_under_5min": team_sp["pct_under_5min"]},
        "conversion_trend": {**tr, "team_avg_pct": team_tr["current_pct"], "predicted_pct": a.predicted},
        "stuck_note": STUCK_PROXY_NOTE,
    }
