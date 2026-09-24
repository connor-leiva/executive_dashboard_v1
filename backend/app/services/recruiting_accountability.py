"""ULRG Recruiting — pace, goals and weekly commitments (RECRUITING-SPEC §7, §9 Phase 5).

Every figure in this file is DERIVED. Commitments store what somebody said they would do;
nothing stores what they did. Actuals come from the stage events, appointments and activities
each time they are asked for, so a commitment and its progress cannot drift apart -- a stored
actual that disagrees with the rows underneath it is not recoverable after the fact, and it makes
the weekly number quietly meaningless.

THE VERDICT VOCABULARY IS THE SCORECARD'S. `ahead | catchable | stretch | reset`, from
services/scorecard.verdict, given recruiting's own gap, required rate and best month. A second
vocabulary would mean two words for the same judgement on two tabs of one product, and people
would have to learn which board they were looking at before they could read it.

BUSINESS DAYS, NOT CALENDAR DAYS, everywhere. Pace on calendar days tells a team on the 1st of a
month that starts on a Saturday that they are already behind; a week keyed off UTC starts on
Sunday evening in Denver.
"""
from __future__ import annotations

import datetime as dt
import statistics
import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    RecruitingActivity, RecruitingAppointment, RecruitingCandidate, RecruitingCommitment,
    RecruitingGoal, RecruitingSeat, RecruitingStageEvent,
)
from . import scorecard

SEAT_METRICS = ("held", "offers", "booked", "dials", "convos")
GOAL_METRICS = ("signed", "booked", "show_rate", "dials", "convos")
SIGNED_GROUP = "Signed"


def _aware(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def monday_of(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def week_elapsed(today: dt.date) -> float:
    """How far through the working week we are, as §7 defines it: business day ÷ 5.

    Wednesday is 0.6, not 3/7. A commitment band measured on calendar days would show somebody
    behind on Saturday for work nobody does on Saturday.
    """
    weekday = today.weekday()
    if weekday >= 5:
        return 1.0                      # the weekend is the week finished, not 5/7 of it
    return round((weekday + 1) / 5, 4)


async def goals_for(s: AsyncSession, tenant_id, period_key: str) -> dict:
    """{seat_id or None: {metric: float}}. None is the team row."""
    rows = list((await s.execute(select(RecruitingGoal).where(
        RecruitingGoal.tenant_id == tenant_id,
        RecruitingGoal.period_key == period_key))).scalars().all())
    out: dict = defaultdict(dict)
    for row in rows:
        out[str(row.seat_id) if row.seat_id else None][row.metric] = float(row.goal)
    return dict(out)


async def commitments_for(s: AsyncSession, tenant_id, week_start: dt.date) -> dict:
    rows = list((await s.execute(select(RecruitingCommitment).where(
        RecruitingCommitment.tenant_id == tenant_id,
        RecruitingCommitment.week_start == week_start))).scalars().all())
    out: dict = defaultdict(dict)
    for row in rows:
        out[str(row.seat_id)][row.metric] = int(row.commit)
    return dict(out)


async def roll_forward(s: AsyncSession, tenant_id, today: dt.date) -> int:
    """Monday rollover: last week's commitments become this week's DEFAULTS.

    Copied rather than carried forward by reference, so editing this week cannot rewrite last
    week's history -- and so a week somebody never touched still records what they were working
    to, which is what makes a trend readable a quarter later.

    Only fills gaps. A commitment already set for this week is somebody's decision and is left
    exactly as it is.
    """
    this_week = monday_of(today)
    last_week = this_week - dt.timedelta(days=7)
    have = await commitments_for(s, tenant_id, this_week)
    prior = await commitments_for(s, tenant_id, last_week)
    if not prior:
        return 0
    made = 0
    for seat_id, metrics in prior.items():
        for metric, value in metrics.items():
            if metric in have.get(seat_id, {}):
                continue
            s.add(RecruitingCommitment(tenant_id=tenant_id, seat_id=uuid.UUID(seat_id),
                                       week_start=this_week, metric=metric, commit=value))
            made += 1
    if made:
        await s.commit()
    return made


def band_pct(actual, commit, elapsed: float):
    """`actual / (commit × week_elapsed) × 100` (§7), for the client's band().

    None when there is no commitment: a band without a target is a colour with no meaning, and
    showing green because nobody set a number is worse than showing nothing.
    """
    if not commit or elapsed <= 0:
        return None
    return round((actual / (commit * elapsed)) * 100)


def pace(signed: float, elapsed: float):
    """Where this lands if the month carries on as it has. None before anything has elapsed."""
    if elapsed <= 0:
        return None
    return round(signed / elapsed, 1)


def judge(signed: float, goal: float | None, elapsed: float, best: float | None) -> dict:
    """Pace, need and the Scorecard's verdict for a monthly goal.

    Mapped onto scorecard.verdict's three inputs rather than reimplemented:
      gap  -- how far ahead of or behind the straight-line pace, in signings
      req  -- the MONTHLY rate needed from here to still land on the goal
      best -- the best completed month, which is what makes "catchable" mean anything

    Without `best` there is no honest verdict: "you need 4 more" is a fact, and whether that is
    reachable depends entirely on what this team has ever actually done.
    """
    if not goal:
        return {"goal": None, "pace": pace(signed, elapsed), "pace_pct": None,
                "verdict": None, "need": None}
    projected = pace(signed, elapsed)
    need = max(0.0, goal - signed)
    remaining = max(0.0, 1.0 - elapsed)
    req = (need / remaining) if remaining > 0 else None
    gap = signed - (goal * elapsed)
    return {
        "goal": goal,
        "pace": projected,
        "pace_pct": round((projected / goal) * 100) if projected is not None and goal else None,
        "verdict": scorecard.verdict(gap, req, best),
        "need": int(need) if need == int(need) else need,
    }


async def best_month(s: AsyncSession, tenant_id, seat_id=None, *, months: int = 12) -> float | None:
    """The most signings in any COMPLETE month in the trailing window.

    Complete on purpose: the month in progress is not evidence of what a month can hold, and
    including it would make every early month's verdict read "reset".
    """
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=31 * months)
    q = select(RecruitingStageEvent).where(
        RecruitingStageEvent.tenant_id == tenant_id,
        RecruitingStageEvent.to_group == SIGNED_GROUP,
        RecruitingStageEvent.occurred_at >= since)
    if seat_id:
        q = q.where(RecruitingStageEvent.actor_seat_id == seat_id)
    rows = list((await s.execute(q)).scalars().all())
    if not rows:
        return None
    this_month = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")
    per_month: dict = defaultdict(set)
    for ev in rows:
        key = _aware(ev.occurred_at).strftime("%Y-%m")
        if key == this_month:
            continue
        per_month[key].add(str(ev.candidate_id))
    return float(max((len(v) for v in per_month.values()), default=0)) or None


# ── the weekly actuals a commitment is measured against ─────────────────────────────────────

def _in_week(when, week_start: dt.date, tz) -> bool:
    when = _aware(when)
    if when is None:
        return False
    day = when.astimezone(tz).date()
    return week_start <= day < week_start + dt.timedelta(days=7)


def weekly_actuals(seat_id, week_start: dt.date, tz, *, appointments, activities,
                   stage_events) -> dict:
    """The five seat metrics for one week, all derived.

    `offers` counts stage events INTO the Offer out group rather than candidates sitting in it:
    the commitment is "send two offers this week", and somebody who sent two and had both
    accepted has met it even though nobody is in the stage any more.
    """
    sid = str(seat_id)
    held = sum(1 for a in appointments
               if str(a.seat_id or "") == sid and (a.status or "").lower() == "showed"
               and _in_week(a.start_at, week_start, tz))
    booked = sum(1 for a in appointments
                 if str(a.booked_by_seat_id or "") == sid
                 and _in_week(a.created_at_src, week_start, tz))
    offers = sum(1 for e in stage_events
                 if str(e.actor_seat_id or "") == sid and e.to_group == "Offer out"
                 and _in_week(e.occurred_at, week_start, tz))
    dials = sum(1 for x in activities
                if str(x.seat_id or "") == sid and x.kind == "call_out"
                and _in_week(x.occurred_at, week_start, tz))
    # A conversation is a call of 60s or more, or any inbound reply (D9).
    convos = sum(1 for x in activities
                 if str(x.seat_id or "") == sid and _in_week(x.occurred_at, week_start, tz)
                 and ((x.kind == "call_out" and (x.duration_s or 0) >= 60)
                      or x.kind in ("sms_in", "email_in", "call_in")))
    return {"held": held, "booked": booked, "offers": offers, "dials": dials, "convos": convos}


def close_rate_90d(seat_id, *, stage_events, appointments, now) -> int | None:
    """Signed ÷ held for one Team Leader over a rolling 90 days (§7).

    None rather than 0 when nothing has been held: a leader with no meetings yet has no close
    rate, and 0% reads as "they close nothing".
    """
    since = now - dt.timedelta(days=90)
    sid = str(seat_id)
    held = sum(1 for a in appointments
               if str(a.seat_id or "") == sid and (a.status or "").lower() == "showed"
               and _aware(a.start_at) and _aware(a.start_at) >= since)
    if not held:
        return None
    signed = len({str(e.candidate_id) for e in stage_events
                  if str(e.actor_seat_id or "") == sid and e.to_group == SIGNED_GROUP
                  and _aware(e.occurred_at) >= since})
    return round(100 * signed / held)


def speed_to_lead(candidates, activities, now) -> dict | None:
    """Median minutes from a candidate arriving to the first outbound touch (§7).

    A candidate NOBODY has touched counts as (now − created), not as missing. Excluding them
    would make a team that ignores half its leads look faster than one that answers all of them
    slowly, which is exactly backwards.
    """
    first_out: dict = {}
    for a in activities:
        if a.kind not in ("sms_out", "email_out", "call_out") or not a.candidate_id:
            continue
        key = str(a.candidate_id)
        when = _aware(a.occurred_at)
        if when and (key not in first_out or when < first_out[key]):
            first_out[key] = when

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    minutes = []
    for cand in candidates:
        created = _aware(cand.created_at_src) or _aware(cand.first_seen_at)
        if created is None or created < month_start:
            continue
        touched = first_out.get(str(cand.id))
        delta = (touched - created) if touched else (now - created)
        minutes.append(max(0, int(delta.total_seconds() // 60)))
    if not minutes:
        return None
    return {"median_minutes": int(statistics.median(minutes)), "n": len(minutes),
            "untouched": sum(1 for c in candidates
                             if str(c.id) not in first_out
                             and (_aware(c.created_at_src) or _aware(c.first_seen_at) or now) >= month_start)}


def path_line(signed: int, goal: float | None, rows: list[dict]) -> str:
    """The sentence above the path list (§3, §7).

    Four cases, and the goal is only named when there IS one -- saying "closing every offer out
    gets you to 8" when nobody has set a target would be inventing the number that matters most.
    """
    offers = sum(1 for r in rows if r["stage"] == "Offer out")
    met = sum(1 for r in rows if r["stage"] == "Met")
    if not rows:
        return "Nobody is past the first meeting yet."
    if not goal:
        parts = []
        if offers:
            parts.append(f"{offers} offer{'s' if offers != 1 else ''} out")
        if met:
            parts.append(f"{met} met and deciding")
        return f"{' · '.join(parts)}. {signed} signed so far this month."

    goal = int(goal)
    if signed >= goal:
        return f"Goal met: {signed} of {goal}. Everything else is next month's head start."
    if signed + offers >= goal:
        return f"Closing every offer out gets you to {goal}."
    if signed + offers + met >= goal:
        short = goal - signed - offers
        return (f"Closing every offer out gets you to {signed + offers}. "
                f"{short} more from Met gets you to {goal}.")
    short = goal - signed - offers - met
    return (f"Everything in flight gets you to {signed + offers + met}. "
            f"{short} more still has to come from somewhere.")
