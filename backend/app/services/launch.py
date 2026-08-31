"""beCollective Launch section — the derive() math, server-side (SPEC-becollective-launch
Sections 5 + 8). One Launch computed from its config + the launch-opportunity records the
beCollective GHL sync snapshots (source='ghl', kind='bc_launch_opp', scoped per launch).
Launch-only: ARR here is annualized revenue ADDED by the cohort, not recurring — no
renewal machinery lives in this section."""
import datetime as dt
import math
from collections import Counter

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
    "booked_app": [],                       # apps-in sub-signal retired 2026-08-14 (No App vs App
                                            # Submitted combined) — phrases here re-enable the tag
    # Post-call the opp sits in an "Appointment Complete - …" disposition until cash lands.
    # A SENT payment link isn't cash; Committed is strictly cash-received-but-unsigned.
    "deciding": ["appointment complete", "needs decision", "decision"],
    "committed": ["payment received", "custom payment"],
    "enrolled": ["won: onboarded", "onboarded"],
    "noshow": ["no show", "cancel"],
    "nurture": ["future cohort", "nurture"],
    "lost": ["lost", "dq", "abandon"],
    # Sales Desk disposition columns (§8 rework) — sub-signals WITHIN deciding/committed; the
    # funnel ignores them, the rep leaderboard buckets by them.
    "likely_yes": ["likely yes"],
    "likely_no": ["likely no"],
    "link_sent": ["payment link", "link sent"],
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


# Shift registrant source classification (organic vs paid channels, from GHL contact UTM).
# Attribution is Shift-scoped: UTM only counts when utm_campaign references the Shift, so a
# long-time member's stale UTM from an old campaign falls to Organic instead of a paid ad.
SHIFT_PAID_MED = {"cpc", "ppc", "paid", "paid-social", "paidsocial", "cpm", "display"}
SHIFT_SRC_CHANNEL = {"meta": "Meta", "facebook": "Meta", "instagram": "Meta", "fb": "Meta",
                     "ig": "Meta", "google": "Google", "adwords": "Google", "youtube": "Google",
                     "bing": "Google", "tiktok": "TikTok", "email": "Email"}
SHIFT_CHANNEL_ORDER = ["Meta", "Google", "TikTok", "Email", "Paid (other)", "Comped", "Organic / Existing"]


def classify_shift_source(utm: dict, is_comp: bool, campaign_match: str = "shift") -> str:
    """A registrant's channel from their (Shift-scoped) UTM. Comped seats are their own
    channel; anything without Shift-campaign UTM is Organic / Existing (owned audience)."""
    if is_comp:
        return "Comped"
    utm = utm or {}
    src = str(utm.get("source") or "").lower()
    med = str(utm.get("medium") or "").lower()
    camp = str(utm.get("campaign") or "").lower()
    if campaign_match and campaign_match.lower() not in camp:
        return "Organic / Existing"      # no Shift-scoped attribution → owned/existing
    ch = SHIFT_SRC_CHANNEL.get(src)
    if ch:
        return ch
    if med in SHIFT_PAID_MED:
        return "Paid (other)"
    return "Organic / Existing"


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


async def _shift_recs(s, tenant_id, business_id) -> list[dict]:
    """The synced Shift-registrant meta records (kind='bc_shift_reg'). The beCollective sync
    scopes these to the launch's registrant tag set + captures each contact's channel from its
    GHL UTM, so every record here is a current registrant carrying its source."""
    recs = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "bc_shift_reg"))).scalars().all()
    return [(r.meta or {}) for r in recs]


_PAID_CHANNELS = {"Meta", "Google", "TikTok", "Paid (other)"}
_CHANNEL_SLUG = {"Meta": "meta", "Google": "google", "TikTok": "tiktok", "Email": "email",
                 "Paid (other)": "paid_other", "Comped": "comped", "Organic / Existing": "organic"}


def channel_slug(label: str) -> str:
    return _CHANNEL_SLUG.get(label, (label or "organic").lower().split()[0])


def _shift_sources(recs: list[dict]) -> dict | None:
    """Aggregate synced registrants by acquisition channel (from meta.channel the sync set)."""
    if not recs:
        return None
    ch = Counter(m.get("channel") or "Organic / Existing" for m in recs)
    total = sum(ch.values())
    order = {c: i for i, c in enumerate(SHIFT_CHANNEL_ORDER)}
    items = sorted(ch.items(), key=lambda kv: (order.get(kv[0], 99), -kv[1]))
    return {
        "total": total,
        "paid": sum(v for k, v in ch.items() if k in _PAID_CHANNELS),
        "organic": ch.get("Organic / Existing", 0),
        "comped": ch.get("Comped", 0),
        "channels": [{"key": channel_slug(k), "label": k, "count": v,
                      "pct": round(v / total * 100) if total else 0,
                      "paid": k in _PAID_CHANNELS} for k, v in items],
    }


async def compute_shift(s, tenant_id, launch: Launch, seat_target: int, today: dt.date) -> dict | None:
    """The Shift layer — the lead-up webinar that feeds memberships. Paces registrants against
    the empirical curve (pace_model="curve"); projects members via seat_target/shift_goal."""
    if not launch.shift_goal:
        return None
    goal = int(launch.shift_goal)
    recs = await _shift_recs(s, tenant_id, launch.business_id)
    synced = len(recs)
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
        "sources": _shift_sources(recs),
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
            "mix_pif": _f(launch.mix_pif), "price_map": launch.price_map or {},
            "default_tz": getattr(launch, "default_tz", None) or "America/Denver",
            "stage_map": launch.stage_map or DEFAULT_STAGE_MAP,
            "pipeline_match": launch.pipeline_match,
            "cohort_value": launch.cohort_value, "pace_model": launch.pace_model,
            "pace_tolerance": _f(launch.pace_tolerance),
            "goal_basis": getattr(launch, "goal_basis", "arr"), "seat_goal": launch.seat_goal,
            "shift_name": launch.shift_name,
            "shift_event_date": launch.shift_event_date.isoformat() if launch.shift_event_date else None,
            "shift_goal": launch.shift_goal, "shift_reg_tag": launch.shift_reg_tag,
            "shift_reg_tags": launch.shift_reg_tags or [],
            "shift_campaign_match": launch.shift_campaign_match,
            "shift_actual": launch.shift_actual, "shift_pace_curve": launch.shift_pace_curve or {},
            "shift_pace_tolerance": _f(launch.shift_pace_tolerance or 0.08)}


async def compute_launch(s, tenant_id, launch: Launch, today=None) -> dict:
    """Assemble the LaunchResponse. Moves the mockup's derive() server-side; actuals price
    off the tickets (editing a price reprices live), the assumed mix only sets the target."""
    today = today or dt.date.today()
    opps = await _launch_opps(s, tenant_id, launch.business_id, launch)
    g = group_counts(opps)
    enr_split, com_split = payment_split(opps, "enrolled"), payment_split(opps, "committed")

    # §9.3 — price ARR off the REAL four-type enrolment counts (price_map) when the Payment Type
    # is logged; fall back to the legacy two-price estimate otherwise, so the live tab never
    # regresses to $0. blended (§7's shared figure) comes from the same real counts.
    price_map = launch.price_map or {}
    grp_counts: dict = {}
    if price_map:
        from .sales_desk import payment_counts_by_group, blended_price, PAYMENT_TYPES
        grp_counts = await payment_counts_by_group(s, tenant_id, launch)
        blended = blended_price(price_map, grp_counts.get("enrolled") or Counter())[0] or 1.0
    else:
        mix_pif = _f(launch.mix_pif)
        blended = (mix_pif * _f(launch.ticket_pif) + (1 - mix_pif) * _f(launch.ticket_plan)) or 1.0

    def _price_group(group, legacy_split):
        counts = grp_counts.get(group) if price_map else None
        if counts:                                     # four-type ARR from the real Payment Type
            arr = sum(((price_map.get(t) or {}).get("acv") or 0) * counts.get(t, 0) for t in PAYMENT_TYPES)
            return {"pif": counts.get("PIF", 0), "plan": counts.get("Financed", 0) + counts.get("Monthly", 0),
                    "seats": sum(counts.values()), "arr": arr, "mix": dict(counts)}
        return _priced(legacy_split, launch)           # legacy two-price fallback (unchanged; mix omitted)

    # Seat-primary launches target a member count directly (e.g. "100 women"); ARR-primary
    # ones back the seat target out of the goal / blended price.
    if getattr(launch, "goal_basis", "arr") == "seats" and launch.seat_goal:
        seat_target = int(launch.seat_goal)
    else:
        seat_target = max(1, math.ceil(_f(launch.goal_arr) / blended))

    enrolled, committed = _price_group("enrolled", enr_split), _price_group("committed", com_split)
    deciding = {"count": g["deciding"], "arr": g["deciding"] * blended}
    # §9.5 — the goal is DERIVED (seat_goal × blended ≈ $1.3M at 100 seats), never a hardcoded "$1M".
    derived_goal_arr = round((launch.seat_goal or seat_target) * blended)
    # §9.4 — cash = sum(upfront × count) over EVERYONE who has paid: Committed (cash received,
    # contract unsigned) + Enrolled. Committed IS paid by definition, so cash must include it.
    paid_counts = Counter(grp_counts.get("enrolled") or {}) + Counter(grp_counts.get("committed") or {}) \
        if price_map else Counter()
    if paid_counts:
        cash = {"collected": round(sum(((price_map.get(t) or {}).get("upfront") or 0) * paid_counts.get(t, 0)
                                       for t in PAYMENT_TYPES)), "source": "upfront"}
    else:
        cash = await collected_cash(s, tenant_id, launch, enr_split)

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
            # apps-in tag only when the booked_app sub-signal is configured (retired by default)
            cnt, tag = g["booked"], (f"{booked_app} of {g['booked']} apps in" if booked_app else None)
        else:
            cnt, tag = g["leads"], None
        return {"key": key, "label": label, "owner": owner, "count": cnt, "tag": tag}

    warnings = []
    if g["uncategorized"]:
        warnings.append(f"{g['uncategorized']} stages unmapped")
    unknown_pt = sum(1 for o in opps if o.get("group") in ("committed", "enrolled")
                     and o.get("payment_type") not in ("pif", "plan", "custom"))
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
        "derived_goal_arr": derived_goal_arr,
        "cash": cash,
        "momentum": await momentum_series(s, tenant_id, launch),
        "warnings": warnings,
    }


# ── drill-down: resolve any number on the Launch tab to its records or its math ──────────
def _usd0(n) -> str:
    return "$" + format(int(round(n or 0)), ",")


async def _shift_records(s, tenant_id, business_id):
    """The Shift registrant RECORDS (not just their meta), so a caller can join on contact_id."""
    return (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "bc_shift_reg"))).scalars().all()


async def _opp_records(s, tenant_id, business_id, launch: Launch):
    recs = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "bc_launch_opp"))).scalars().all()
    lid = str(launch.id)
    return [r for r in recs if (r.meta or {}).get("launch_id") == lid]


_GROUP_TITLE = {"leads": "Leads", "booked": "Booked", "deciding": "Deciding",
                "committed": "Committed", "enrolled": "Enrolled",
                "noshow": "No-show / cancel", "nurture": "Warm reserve"}
_METRIC_GROUP = {"funnel.leads": "leads", "funnel.booked": "booked", "funnel.deciding": "deciding",
                 "funnel.committed": "committed", "funnel.enrolled": "enrolled",
                 "side.no_show": "noshow", "side.nurture": "nurture",
                 "enrolled.seats": "enrolled", "committed.seats": "committed"}


async def drill_launch(s, tenant_id, launch: Launch, metric: str, today=None) -> dict:
    """What's behind a number on the Launch tab: either the underlying records (registrants,
    opportunities, weeks — with GHL links) or the calculation (inputs + formula) for a derived
    figure. One shape the drawer renders both ways."""
    today = today or dt.date.today()
    d = await compute_launch(s, tenant_id, launch, today)
    biz = launch.business_id
    pif, plan = _f(launch.ticket_pif), _f(launch.ticket_plan)
    sh = d.get("shift") or {}

    def records(title, subtitle, rows, columns):
        return {"metric": metric, "type": "records", "title": title, "subtitle": subtitle,
                "count": len(rows), "columns": columns, "rows": rows}

    def calc(title, value, steps, formula=None, note=None, table=None):
        return {"metric": metric, "type": "calc", "title": title, "value": str(value),
                "steps": steps, "formula": formula, "note": note, "table": table}

    # ── record-backed numbers ───────────────────────────────────────────────
    if metric in _METRIC_GROUP:
        g = _METRIC_GROUP[metric]
        # Where each person came from, joined by contact_id to their Shift registration. This is
        # the SAME channel the "where they came from" bar above uses, so the drawer reconciles
        # against the bar rather than against a second opinion computed a different way.
        #
        # No registration record says exactly one thing: this contact is not in the Shift
        # registrant set. It does NOT say they arrived some other way - they may have come
        # through an earlier Shift, and a label asserting an origin we did not observe would be
        # a guess wearing a fact's clothes. So the cell names the measurement, not the inference.
        by_contact = {
            str((r.meta or {}).get("contact_id") or ""): (r.meta or {}).get("channel")
            for r in await _shift_records(s, tenant_id, biz)
            if str((r.meta or {}).get("contact_id") or "")}
        rows = []
        for r in await _opp_records(s, tenant_id, biz, launch):
            meta = r.meta or {}
            if meta.get("group") != g:
                continue
            cid = str(meta.get("contact_id") or "")
            rows.append({"name": r.name or "-", "stage": meta.get("stage"),
                         "source": by_contact.get(cid) or "No Shift registration",
                         "payment": meta.get("payment_type"), "url": r.source_url})
        return records(_GROUP_TITLE.get(g, g),
                       f"{len(rows)} in this stage | {launch.pipeline_match}",
                       rows, ["name", "stage", "source", "payment", "url"])

    if metric == "shift.registrants" or metric.startswith("shift.source."):
        slug = metric.split(".", 2)[2] if metric.startswith("shift.source.") else None
        recs = (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == biz,
            MetricRecord.source == "ghl", MetricRecord.kind == "bc_shift_reg"))).scalars().all()
        rows = [{"name": r.name or "-", "email": r.email,
                 "channel": (r.meta or {}).get("channel"),
                 "campaign": (r.meta or {}).get("utm_campaign"), "url": r.source_url}
                for r in recs
                if slug is None or channel_slug((r.meta or {}).get("channel", "")) == slug]
        if slug:
            label = next((c["label"] for c in (sh.get("sources") or {}).get("channels", [])
                          if c["key"] == slug), slug.title())
            return records(f"The Shift - {label} registrants",
                           f"{len(rows)} registrants attributed to {label}",
                           rows, ["name", "email", "channel", "campaign", "url"])
        sub = (f"{len(rows)} registrants (live)" if sh.get("source") == "synced"
               else f"manual count of {sh.get('registrants', 0)} - no live tags synced yet")
        return records("The Shift - registrants", sub, rows, ["name", "email", "channel", "campaign", "url"])

    if metric.startswith("momentum."):
        key = metric.split(".", 1)[1]
        weeks = (await s.execute(select(LaunchWeekly).where(
            LaunchWeekly.tenant_id == tenant_id, LaunchWeekly.launch_id == launch.id)
            .order_by(LaunchWeekly.week_start))).scalars().all()
        label = {"optins": "New opt-ins", "calls": "Calls held", "closes": "Closes"}.get(key, key)
        rows = [{"week": w.week_start.isoformat(), "value": getattr(w, key, 0)} for w in weeks]
        note = " | calls are a proxy until appointment data wires in" if key == "calls" else ""
        return records(f"Momentum - {label}", "by ISO week, most recent last" + note,
                       rows, ["week", "value"])

    # ── calculated numbers ──────────────────────────────────────────────────
    if metric in ("seat_target", "goal.seats", "goal.arr"):
        seat_primary = d["goal_basis"] == "seats"
        return calc("Seat / member target", d["seat_target"],
                    [{"label": "Goal basis", "value": d["goal_basis"]},
                     {"label": "Member goal" if seat_primary else "ARR goal",
                      "value": str(launch.seat_goal) if seat_primary else _usd0(launch.goal_arr)},
                     {"label": "Blended seat price", "value": _usd0(d["blended_seat"])},
                     {"label": "Implied ARR at goal", "value": _usd0(d["seat_target"] * d["blended_seat"])}],
                    formula=("seat_target = member goal" if seat_primary
                             else "seat_target = ceil(ARR goal / blended)"),
                    note=None if seat_primary else "Seat-derived; set goal basis to 'members' to target people.")

    if metric == "blended":
        m = _f(launch.mix_pif)
        return calc("Blended seat price", _usd0(d["blended_seat"]),
                    [{"label": "Assumed mix", "value": f"{round(m*100)}% PIF / {round((1-m)*100)}% plan"},
                     {"label": "PIF price", "value": _usd0(pif)}, {"label": "Plan price", "value": _usd0(plan)}],
                    formula="blended = mix*PIF + (1-mix)*plan",
                    note="An assumption used only to set the target; actuals price off real counts.")

    if metric in ("shift.expected", "shift.curve", "shift.pace"):
        goal = int(launch.shift_goal or 0)
        dte = sh.get("days_to_event")
        days = [p["d"] for p in sh.get("curve", [])]
        clamped = max(min(dte, max(days)), min(days)) if (dte is not None and days) else None
        table = [{"day": p["d"], "pct": p["pct"], "expected": p["count"],
                  "today": p["d"] == clamped} for p in sh.get("curve", [])]
        return calc("On-curve target (where we should be)", f"{sh.get('expected')} of {goal}",
                    [{"label": "Days to the Shift", "value": sh.get("days_to_event")},
                     {"label": "Curve % today", "value": f"{round(sh.get('expected_pct', 0)*100)}%"},
                     {"label": "Expected today", "value": f"{sh.get('expected')} registrants"},
                     {"label": "Actual", "value": f"{sh.get('registrants')} registrants"}],
                    formula="expected = curve%(days-to-event) x goal",
                    note="Empirical curve from your last Shift - the honest, back-loaded pace line.",
                    table=table)

    if metric == "shift.gap":
        return calc("Gap to pace", sh.get("gap"),
                    [{"label": "Actual registrants", "value": sh.get("registrants")},
                     {"label": "On-curve target", "value": sh.get("expected")},
                     {"label": "State", "value": sh.get("state")}],
                    formula="gap = actual - on-curve target")

    if metric == "shift.projected":
        return calc("Projected members from registrants",
                    f"{sh.get('projected_members')} of {sh.get('members_at_goal')}",
                    [{"label": "Registrants", "value": sh.get("registrants")},
                     {"label": "Conversion", "value": f"{round(sh.get('reg_to_member', 0)*100)}%"},
                     {"label": "Member goal", "value": sh.get("members_at_goal")}],
                    formula="projected = registrants x (member goal / registrant goal)")

    if metric == "shift.pct":
        return calc("The Shift - % to goal", f"{round(sh.get('pct_to_goal', 0)*100)}%",
                    [{"label": "Registrants", "value": sh.get("registrants")},
                     {"label": "Registrant goal", "value": sh.get("goal")}],
                    formula="% = registrants / registrant goal")

    if metric in ("enrolled.arr", "committed.arr"):
        grp = d["enrolled"] if metric.startswith("enrolled") else d["committed"]
        return calc(f"{metric.split('.')[0].title()} ARR", _usd0(grp["arr"]),
                    [{"label": "PIF seats x price", "value": f"{grp['pif']} x {_usd0(pif)} = {_usd0(grp['pif']*pif)}"},
                     {"label": "Plan seats x price", "value": f"{grp['plan']} x {_usd0(plan)} = {_usd0(grp['plan']*plan)}"}],
                    formula="ARR = PIF*PIF-price + plan*plan-price",
                    note="Click the seat counts to see the underlying opportunities.")

    if metric in ("deciding.arr", "deciding.count"):
        return calc("Deciding - value on the table", _usd0(d["deciding"]["arr"]),
                    [{"label": "Opportunities", "value": d["deciding"]["count"]},
                     {"label": "Blended seat price", "value": _usd0(d["blended_seat"])}],
                    formula="value = deciding count x blended price")

    if metric == "cash":
        if d["cash"].get("source") == "upfront":
            # Four-type path: real upfronts across EVERYONE who has paid (committed + enrolled).
            pm = launch.price_map or {}
            paid = Counter(d["enrolled"].get("mix") or {}) + Counter(d["committed"].get("mix") or {})
            steps = [{"label": f"{t} × {n}", "value": _usd0((pm.get(t) or {}).get("upfront") or 0)}
                     for t, n in sorted(paid.items())]
            steps.append({"label": "Base", "value": "committed + enrolled — everyone who has paid"})
            return calc("Cash collected", _usd0(d["cash"]["collected"]), steps,
                        formula="sum(upfront × paid seats)",
                        note="Committed = cash received, contract unsigned. The balance of "
                             "financed/monthly seats arrives across the payment schedule.")
        inst = launch.plan_installments or 1
        return calc("Cash collected (estimate)", _usd0(d["cash"]["collected"]),
                    [{"label": "PIF seats (full)", "value": f"{d['enrolled']['pif']} x {_usd0(pif)}"},
                     {"label": "Plan seats (1st installment)", "value": f"{d['enrolled']['plan']} x {_usd0(plan/inst)}"},
                     {"label": "Source", "value": d["cash"]["source"]}],
                    formula="collected ~= PIF*price + plan*(price / installments)",
                    note="Demoted vs ARR - the balance arrives across the payment schedule.")

    if metric in ("days_left", "window_days", "days_to_open"):
        return calc("Cart window", f"{d['days_remaining']}d left",
                    [{"label": "Cart opens", "value": launch.window_start.isoformat()},
                     {"label": "Cart closes", "value": launch.window_end.isoformat()},
                     {"label": "Window length", "value": f"{d['window_days']} days"},
                     {"label": "Status", "value": d["status"]}])

    return calc(metric, "-", [], note="No drill-down defined for this value yet.")
