"""beCollective Launch section — the derive() math, server-side (SPEC-becollective-launch
Sections 5 + 8). One Launch computed from its config + the launch-opportunity records the
beCollective GHL sync snapshots (source='ghl', kind='bc_launch_opp', scoped per launch).
Launch-only: ARR here is annualized revenue ADDED by the cohort, not recurring — no
renewal machinery lives in this section."""
import datetime as dt
import math

from sqlalchemy import select

from ..models import Launch, LaunchWeekly, MetricRecord

# Product structure (in code — the executive model Acumyn imposes, same for every tenant).
# The five accountable groups; each boundary is a different owner + lever (Section 1).
ACTIVE_FUNNEL = [("leads", "Leads", "marketing"), ("booked", "Booked", "setters"),
                 ("deciding", "Deciding", "closers"), ("committed", "Committed", "payment ops")]
GROUPS = ("leads", "booked", "deciding", "committed", "enrolled",
          "noshow", "nurture", "lost", "uncategorized")

# Seeded defaults (tenant-editable via the settings drawer — never hardcoded in the sync).
DEFAULT_STAGE_MAP = {
    "leads": ["opt in"],
    "booked": ["scheduled appointment", "appointment"],
    "booked_app": ["application", "app submitted", "app in"],   # sub-signal: booked + application in
    "deciding": ["needs decision", "decision"],
    "committed": ["payment sent"],
    "enrolled": ["payment received", "custom payment", "won: onboarded", "onboarded"],
    "noshow": ["no show", "cancel"],
    "nurture": ["future cohort", "nurture"],
    "lost": ["lost", "dq", "abandon"],
}
DEFAULT_PAYMENT_PLAN_MAP = {"pif": ["paid in full", "pif"],
                            "plan": ["payment plan", "financed", "monthly", "plan"]}

# Empirical Shift-registration pace curve (days-to-event → cumulative fraction of the goal),
# from Spring's last Shift launch. Back-loaded: ~19% two weeks out, ~48% at 6 days, then the
# final-week surge to ~94% by event day. This is the pace_model="curve" reference line.
DEFAULT_SHIFT_CURVE = {
    "14": 0.190, "13": 0.220, "12": 0.247, "11": 0.275, "10": 0.309, "9": 0.348,
    "8": 0.390, "7": 0.432, "6": 0.481, "5": 0.584, "4": 0.670, "3": 0.734,
    "2": 0.801, "1": 0.864, "0": 0.940,
}


# Group precedence: a won opp must resolve as 'enrolled' even though its stage text may also
# brush a looser keyword. booked_app is a sub-signal that still lands in 'booked'.
_GROUP_ORDER = ("enrolled", "committed", "deciding", "noshow", "lost", "nurture",
                "booked_app", "booked", "leads")


def classify_stage(stage_name: str, stage_map: dict) -> tuple[str, bool]:
    """(group, app_in) for a raw GHL stage name, via the tenant's stage_map — no stage
    literals live in the sync. Unmatched → 'uncategorized' (surfaced as a warning)."""
    low = (stage_name or "").lower()
    for g in _GROUP_ORDER:
        subs = stage_map.get(g) or []
        if any(sub in low for sub in subs):
            return ("booked", True) if g == "booked_app" else (g, False)
    return "uncategorized", False


def classify_payment(field_value, financed: bool, payment_plan_map: dict, stage_low: str = "") -> str | None:
    """PIF vs plan for a committed/enrolled opp. Prefer the member's Payment Plan field,
    then a financed signal, then the stage text. None when it can't be told."""
    raw = (field_value or "").lower()
    for pt in ("plan", "pif"):     # plan first — 'monthly'/'financed' are the specific signal
        if raw and any(sub in raw for sub in payment_plan_map.get(pt, [])):
            return pt
    if financed:
        return "plan"
    if raw:
        return "pif"               # a value we couldn't map defaults to paid-in-full
    for pt in ("plan", "pif"):
        if any(sub in stage_low for sub in payment_plan_map.get(pt, [])):
            return pt
    return None


def _f(x) -> float:
    return float(x or 0)


def _kmoney(n) -> str:
    a = abs(round(n))
    if a >= 1_000_000:
        return f"${a / 1_000_000:.1f}M".replace(".0M", "M")
    if a >= 1_000:
        return f"${round(a / 1000)}K"
    return f"${a}"


def days_between(a: dt.date, b: dt.date) -> int:
    return (b - a).days


def curve_expected(curve: dict, days_to_event) -> float:
    """Cumulative fraction of goal expected at `days_to_event`, read off an empirical curve
    keyed by integer days-to-event (higher key = earlier = lower %). Linear-interpolates
    between points and clamps outside the defined range. This is pace_model="curve": for a
    back-loaded event it tells the honest 'where you should be' instead of a straight line."""
    if not curve or days_to_event is None:
        return 0.0
    pts = sorted((int(k), float(v)) for k, v in curve.items())   # ascending by day
    d = float(days_to_event)
    if d <= pts[0][0]:
        return pts[0][1]                       # at/after the nearest-to-event point
    if d >= pts[-1][0]:
        return pts[-1][1]                      # earlier than the first measured point
    for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
        if d0 <= d <= d1:
            t = (d - d0) / (d1 - d0) if d1 != d0 else 0.0
            return v0 + t * (v1 - v0)
    return pts[-1][1]


# ── launch selection + the synced opp store ──────────────────────────────────
async def active_launch_for(s, tenant_id, business_id, today=None) -> Launch | None:
    """The active launch to show: one whose window contains today (nearest window_end),
    else one opening within 30 days, else any active launch. None if none is active
    (the section is then absent — no empty shell)."""
    today = today or dt.date.today()
    rows = (await s.execute(select(Launch).where(
        Launch.tenant_id == tenant_id, Launch.business_id == business_id,
        Launch.is_active.is_(True)))).scalars().all()
    live = [l for l in rows if l.window_start <= today <= l.window_end]
    if live:
        return min(live, key=lambda l: l.window_end)
    upcoming = [l for l in rows if today < l.window_start <= today + dt.timedelta(days=30)]
    if upcoming:
        return min(upcoming, key=lambda l: l.window_start)
    return min(rows, key=lambda l: abs((l.window_end - today).days)) if rows else None


async def _launch_opps(s, tenant_id, business_id, launch: Launch) -> list[dict]:
    """The synced launch-opportunity meta records scoped to this launch."""
    recs = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "bc_launch_opp"))).scalars().all()
    lid = str(launch.id)
    return [(r.meta or {}) for r in recs if (r.meta or {}).get("launch_id") == lid]


async def _shift_registrants(s, tenant_id, business_id, tag) -> int:
    """Live count of Shift registrants — contacts the beCollective sync tagged for the Shift
    (kind='bc_shift_reg'). 0 when nothing is synced yet (compute falls back to the manual seed)."""
    if not tag:
        return 0
    recs = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "bc_shift_reg"))).scalars().all()
    t = str(tag).lower()
    return sum(1 for r in recs if str((r.meta or {}).get("shift_tag", "")).lower() == t)


async def compute_shift(s, tenant_id, launch: Launch, seat_target: int, today: dt.date) -> dict | None:
    """The Shift layer — the lead-up webinar that feeds memberships. Paces registrants against
    the empirical curve (pace_model="curve"); projects members via seat_target/shift_goal."""
    if not launch.shift_goal:
        return None
    goal = int(launch.shift_goal)
    synced = await _shift_registrants(s, tenant_id, launch.business_id, launch.shift_reg_tag)
    registrants = synced or int(launch.shift_actual or 0)
    curve = launch.shift_pace_curve or {}
    dte = days_between(today, launch.shift_event_date) if launch.shift_event_date else None
    exp_pct = curve_expected(curve, dte) if curve else (0.0 if dte is None else 1.0)
    expected = round(exp_pct * goal)
    gap = registrants - expected
    tol = _f(launch.shift_pace_tolerance or 0.08) * goal
    state = ("pending" if dte is None else "done" if dte < 0
             else "behind" if gap < -tol else "ahead" if gap > tol else "onpace")
    ratio = (seat_target / goal) if goal else 0.0     # reg → member conversion
    return {
        "name": launch.shift_name or "The Shift",
        "event_date": launch.shift_event_date.isoformat() if launch.shift_event_date else None,
        "goal": goal, "registrants": registrants,
        "pct_to_goal": round(registrants / goal, 4) if goal else 0.0,
        "days_to_event": dte,
        "expected": expected, "expected_pct": round(exp_pct, 4), "gap": gap, "state": state,
        "source": "synced" if synced else ("manual" if launch.shift_actual else "none"),
        "reg_to_member": round(ratio, 5), "projected_members": round(registrants * ratio),
        "members_at_goal": seat_target,
        "curve": [{"d": d, "pct": round(v, 4), "count": round(v * goal)}
                  for d, v in sorted((int(k), float(x)) for k, x in curve.items())],
    }


# ── grouping / pricing / window / pace ───────────────────────────────────────
def group_counts(opps) -> dict:
    g = {k: 0 for k in GROUPS}
    for o in opps:
        grp = o.get("group") or "uncategorized"
        g[grp if grp in g else "uncategorized"] += 1
    return g


def payment_split(opps, group) -> dict:
    pif = sum(1 for o in opps if o.get("group") == group and o.get("payment_type") == "pif")
    plan = sum(1 for o in opps if o.get("group") == group and o.get("payment_type") == "plan")
    return {"pif": pif, "plan": plan}


def _priced(split, launch) -> dict:
    pif, plan = split["pif"], split["plan"]
    return {"pif": pif, "plan": plan, "seats": pif + plan,
            "arr": pif * _f(launch.ticket_pif) + plan * _f(launch.ticket_plan)}


def window_status(launch: Launch, today: dt.date) -> dict:
    ws, we = launch.window_start, launch.window_end
    window_days = max(1, days_between(ws, we))
    if today < ws:
        return {"status": "pre", "window_days": window_days, "elapsed": 0,
                "remaining": window_days, "days_to_open": days_between(today, ws), "prop": 0.0}
    if today > we:
        return {"status": "closed", "window_days": window_days, "elapsed": window_days,
                "remaining": 0, "days_to_open": 0, "prop": 1.0}
    elapsed = days_between(ws, today)
    return {"status": "open", "window_days": window_days, "elapsed": elapsed,
            "remaining": window_days - elapsed, "days_to_open": 0, "prop": elapsed / window_days}


async def collected_cash(s, tenant_id, launch: Launch, enrolled_split) -> dict:
    """Demoted cash line — ARR is the headline. v1: no per-launch payment attribution yet,
    so use the snapshot estimate (PIF lands in full, plan seats have paid ~one installment).
    Always labels its source so the UI is honest."""
    pif, plan = enrolled_split["pif"], enrolled_split["plan"]
    collected = pif * _f(launch.ticket_pif) + plan * (_f(launch.ticket_plan) / max(1, launch.plan_installments))
    return {"collected": round(collected), "source": "estimate"}


async def momentum_series(s, tenant_id, launch: Launch) -> dict:
    rows = (await s.execute(select(LaunchWeekly).where(
        LaunchWeekly.tenant_id == tenant_id, LaunchWeekly.launch_id == launch.id)
        .order_by(LaunchWeekly.week_start.asc()))).scalars().all()
    last6 = rows[-6:]
    return {"optins": [r.optins for r in last6], "calls": [r.calls for r in last6],
            "closes": [r.closes for r in last6],
            "calls_source": (last6[-1].calls_source if last6 else "proxy")}


def config_out(launch: Launch) -> dict:
    return {"name": launch.name, "program": launch.program,
            "event_start": launch.event_start.isoformat() if launch.event_start else None,
            "event_end": launch.event_end.isoformat() if launch.event_end else None,
            "window_start": launch.window_start.isoformat(), "window_end": launch.window_end.isoformat(),
            "goal_arr": _f(launch.goal_arr), "ticket_pif": _f(launch.ticket_pif),
            "ticket_plan": _f(launch.ticket_plan), "plan_installments": launch.plan_installments,
            "mix_pif": _f(launch.mix_pif), "pipeline_match": launch.pipeline_match,
            "cohort_value": launch.cohort_value, "pace_model": launch.pace_model,
            "pace_tolerance": _f(launch.pace_tolerance),
            "goal_basis": getattr(launch, "goal_basis", "arr"), "seat_goal": launch.seat_goal,
            "shift_name": launch.shift_name,
            "shift_event_date": launch.shift_event_date.isoformat() if launch.shift_event_date else None,
            "shift_goal": launch.shift_goal, "shift_reg_tag": launch.shift_reg_tag,
            "shift_actual": launch.shift_actual, "shift_pace_curve": launch.shift_pace_curve or {},
            "shift_pace_tolerance": _f(launch.shift_pace_tolerance or 0.08)}


async def compute_launch(s, tenant_id, launch: Launch, today=None) -> dict:
    """Assemble the LaunchResponse. Moves the mockup's derive() server-side; actuals price
    off the tickets (editing a price reprices live), the assumed mix only sets the target."""
    today = today or dt.date.today()
    opps = await _launch_opps(s, tenant_id, launch.business_id, launch)
    g = group_counts(opps)
    enr_split, com_split = payment_split(opps, "enrolled"), payment_split(opps, "committed")

    mix_pif = _f(launch.mix_pif)
    blended = (mix_pif * _f(launch.ticket_pif) + (1 - mix_pif) * _f(launch.ticket_plan)) or 1.0
    # Seat-primary launches target a member count directly (e.g. "100 women"); ARR-primary
    # ones back the seat target out of the goal / blended price.
    if getattr(launch, "goal_basis", "arr") == "seats" and launch.seat_goal:
        seat_target = int(launch.seat_goal)
    else:
        seat_target = max(1, math.ceil(_f(launch.goal_arr) / blended))

    enrolled, committed = _priced(enr_split, launch), _priced(com_split, launch)
    deciding = {"count": g["deciding"], "arr": g["deciding"] * blended}

    win = window_status(launch, today)
    goal = _f(launch.goal_arr)
    expected = goal * win["prop"]
    gap = enrolled["arr"] - expected
    tol = _f(launch.pace_tolerance) * goal
    state = ("pending" if win["status"] == "pre"
             else "behind" if gap < -tol else "ahead" if gap > tol else "onpace")

    booked_app = sum(1 for o in opps if o.get("group") == "booked" and o.get("app_in"))

    def _stage(key, label, owner):
        if key == "committed":
            cnt, tag = committed["seats"], (f"{com_split['pif']} PIF · {com_split['plan']} plan" if committed["seats"] else None)
        elif key == "deciding":
            cnt, tag = g["deciding"], (f"{_kmoney(deciding['arr'])} on the table" if g["deciding"] else None)
        elif key == "booked":
            cnt, tag = g["booked"], (f"{booked_app} of {g['booked']} apps in" if g["booked"] else None)
        else:
            cnt, tag = g["leads"], None
        return {"key": key, "label": label, "owner": owner, "count": cnt, "tag": tag}

    warnings = []
    if g["uncategorized"]:
        warnings.append(f"{g['uncategorized']} stages unmapped")
    unknown_pt = sum(1 for o in opps if o.get("group") in ("committed", "enrolled")
                     and o.get("payment_type") not in ("pif", "plan"))
    if unknown_pt:
        warnings.append(f"{unknown_pt} opps unknown payment type")

    shift = await compute_shift(s, tenant_id, launch, seat_target, today)

    return {
        "id": str(launch.id),
        "launch": config_out(launch),
        "goal_basis": getattr(launch, "goal_basis", "arr"),
        "shift": shift,
        "status": win["status"], "as_of": today.isoformat(),
        "window_days": win["window_days"], "days_elapsed": win["elapsed"],
        "days_remaining": win["remaining"], "days_to_open": win["days_to_open"],
        "blended_seat": round(blended, 2), "seat_target": seat_target,
        "enrolled": {**enrolled, "arr": round(enrolled["arr"], 2)},
        "committed": {**committed, "arr": round(committed["arr"], 2)},
        "deciding": {"count": deciding["count"], "arr": round(deciding["arr"], 2)},
        "funnel": [_stage(*x) for x in ACTIVE_FUNNEL],
        "side": {"no_show": g["noshow"], "nurture": g["nurture"]},
        "pace": {"expected_arr": round(expected, 2), "gap_arr": round(gap, 2), "state": state},
        "pct_to_goal": round((enrolled["arr"] / goal) if goal else 0.0, 4),
        "pct_to_goal_seats": round((enrolled["seats"] / seat_target) if seat_target else 0.0, 4),
        "seats_remaining": max(0, seat_target - enrolled["seats"]),
        "arr_remaining": round(max(0.0, goal - enrolled["arr"]), 2),
        "cash": await collected_cash(s, tenant_id, launch, enr_split),
        "momentum": await momentum_series(s, tenant_id, launch),
        "warnings": warnings,
    }
