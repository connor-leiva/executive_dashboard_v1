"""Scale, keep, or kill. Pure: the caller supplies the numbers.

A missing seat count is not zero seats. Zero is a finding only when the count
was actually measured. Not enough delivery is keep, not kill: pausing an ad
that spent $2 teaches the wrong lesson.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .ads_rules import band

DEFAULT_GUARD = {
    "on": False,
    "pause_only": False,
    "max_daily_spend": None,
    "max_increase_pct": 20,
    "max_daily_increase_pct": 20,
    "min_spend": 25,
    "min_impressions": 1000,
}


def _money(n) -> str:
    return f"${float(n):,.0f}"


def recommend(row: dict, thresholds: dict | None = None, guard: dict | None = None) -> dict:
    """One verdict for an ad, ad set, or campaign row."""
    g = {**DEFAULT_GUARD, **(guard or {})}
    spend = float(row.get("spend") or 0)
    impressions = int(row.get("impressions") or 0)
    leads = int(row.get("leads") or 0)
    seats = row.get("seats")
    name = row.get("name") or "this ad"
    base = {
        "level": row.get("level"),
        "external_id": row.get("external_id"),
        "name": name,
        "group": row.get("group"),
        "spend": spend,
        "daily_budget": row.get("daily_budget"),
        "budget_external_id": row.get("budget_external_id"),
    }
    if spend < float(g["min_spend"]) or impressions < int(g["min_impressions"]):
        return {**base, "verdict": "keep", "action": None, "metric": "delivery",
                "proposed": None,
                "reason": f"Below the minimum delivery ({_money(g['min_spend'])} and "
                          f"{int(g['min_impressions']):,} impressions) before a change."}

    ctr_band = band("ctr", row.get("ctr"), thresholds)
    cpl_band = band("cpl", row.get("cpl"), thresholds)
    seat_band = band("cpl", row.get("cost_per_seat"), thresholds)

    if seats is not None and int(seats) == 0 and leads == 0:
        return {**base, "verdict": "kill", "action": "pause", "metric": "seats",
                "proposed": {"status": "PAUSED"},
                "reason": f"{name} spent {_money(spend)}, no seats / no enrollments"}
    if seats is not None and int(seats) == 0:
        return {**base, "verdict": "kill", "action": "pause", "metric": "seats",
                "proposed": {"status": "PAUSED"},
                "reason": f"{name} spent {_money(spend)}, no seats"}
    if seats is None and leads == 0:
        return {**base, "verdict": "kill", "action": "pause", "metric": "leads",
                "proposed": {"status": "PAUSED"},
                "reason": f"{name} spent {_money(spend)}, no enrollments"}
    if row.get("fatigue"):
        return {**base, "verdict": "kill", "action": "pause", "metric": "fatigue",
                "proposed": {"status": "PAUSED"},
                "reason": f"{name}: frequency is rising and CTR is falling"}
    if ctr_band == "bad" and cpl_band in ("bad", "none"):
        return {**base, "verdict": "kill", "action": "pause", "metric": "ctr",
                "proposed": {"status": "PAUSED"},
                "reason": f"{name}: CTR is in the bad band and cost per lead is not healthy"}

    healthy_cost = cpl_band == "good" or (row.get("cost_per_seat") is not None and seat_band == "good")
    if ctr_band == "good" and healthy_cost and row.get("daily_budget"):
        proposed = _raised(row.get("daily_budget"), float(g["max_increase_pct"]))
        return {**base, "verdict": "scale", "action": "set_daily_budget", "metric": "cpl",
                "proposed": {"daily_budget": proposed},
                "reason": f"{name}: CTR and cost are in the good band. "
                          f"Raise the daily budget from {_money(row['daily_budget'])} "
                          f"to {_money(proposed)}."}
    if ctr_band == "good" and healthy_cost:
        return {**base, "verdict": "keep", "action": None, "metric": "cpl", "proposed": None,
                "reason": f"{name} looks strong and has no daily budget to raise."}
    return {**base, "verdict": "keep", "action": None, "metric": "ctr", "proposed": None,
            "reason": f"{name} stays. The numbers are inside the bands."}


def _raised(current, pct: float) -> float:
    nxt = Decimal(str(current)) * (Decimal("1") + Decimal(str(pct)) / Decimal("100"))
    return float(nxt.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def clamp_increase(current, proposed, *, weekly_budget, week_spend, today_spend,
                   already_pct: float, guard: dict | None) -> tuple[float | None, str | None]:
    """The budget autopilot is allowed to set, or None and why it may not.

    Caps: percent per change, percent already applied today, the planned weekly
    budget, and a max daily spend for the brand.
    """
    g = {**DEFAULT_GUARD, **(guard or {})}
    if current in (None, 0, 0.0):
        return None, "No daily budget to raise."
    cap_pct = float(g["max_increase_pct"])
    ceiling = _raised(current, cap_pct)
    nxt = min(float(proposed), ceiling)
    if already_pct >= float(g["max_daily_increase_pct"]) - 0.01:
        return None, "This budget was already raised the daily maximum."
    room = float(g["max_daily_increase_pct"]) - already_pct
    nxt = min(nxt, _raised(current, room))
    if weekly_budget:
        week_cap = float(weekly_budget) / 7.0
        if float(week_spend or 0) >= float(weekly_budget):
            return None, "This week's spend is already at the planned budget."
        nxt = min(nxt, week_cap)
    if g.get("max_daily_spend") not in (None, ""):
        if float(today_spend or 0) >= float(g["max_daily_spend"]):
            return None, "Today's spend is at the brand's daily cap."
        nxt = min(nxt, float(g["max_daily_spend"]))
    if nxt <= float(current) + 0.009:
        return None, "The guardrails leave no room to raise this budget."
    return round(nxt, 2), None
