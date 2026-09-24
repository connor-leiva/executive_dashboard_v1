"""ULRG Recruiting — the payload the tab renders (RECRUITING-SPEC §3, §7).

ALL THE MATH IS HERE. The client renders what it is given and computes nothing: two numbers for
one thing drift, and the one on screen is the one people act on.

WHAT PHASE 1 CAN HONESTLY ANSWER. Half this payload depends on tables that do not exist yet --
the queue is Phase 3, commitments and goals are Phase 5, dials and replies need Phase 6's
conversation poll. Those blocks come back `null`, and every one of them names its cause in
`unavailable`. That is the house rule from follow_ups.py: "no data" and "not built yet" and
"caught up" are three different facts, and a UI that cannot tell them apart teaches people to
distrust all three.

Seat scoping is applied here rather than in the router, because "what a Team Leader may see"
is a property of the data, not of the URL.
"""
from __future__ import annotations

import datetime as dt
import uuid
import zoneinfo
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import (
    Integration, RecruitingActivity, RecruitingAppointment, RecruitingCandidate,
    RecruitingSeat, RecruitingStageEvent, User,
)
from . import roles

SIGNED_GROUP = "Signed"
NURTURE_GROUP = "Nurture"
# The two groups the "path to goal" is drawn from, nearest the signature first (§7, Path).
PATH_GROUPS = ("Offer out", "Met")

# Why a block is null. Written once, here, so the tab and the tests agree on the wording and a
# reader can tell "not built" from "nothing to show".
WHY = {
    "queue": "The Do next queue arrives with the rule engine (Phase 3).",
    "commitments": "Weekly commitments arrive with accountability (Phase 5).",
    "goal": "Monthly goals are set in Settings › Recruiting › Goals (Phase 5). Signings are counted already.",
    "calendars": "Open slots are read live from GHL when booking ships (Phase 4b).",
    "activity": "Dials, conversations and replies arrive with the conversation poll (Phase 6).",
    "not_connected": "No recruiting location is connected. Connect one in Settings › Integrations.",
    "not_configured": "Connected, but no pipeline is chosen yet. Finish Settings › Recruiting.",
    "not_synced": "Connected and configured; the first sync has not finished yet.",
}


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    """A stored timestamp as an AWARE datetime.

    Postgres hands these back with a timezone and SQLite hands them back without one, so any
    arithmetic against `now` works in exactly one of the two places unless it goes through here.
    A naive value is read as UTC, which is what both dialects were given.
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def _tz() -> zoneinfo.ZoneInfo:
    """Business-local. Dates are decided in the brokerage's own day, not UTC's -- a Monday-keyed
    week computed in UTC flips a day early for six hours every night."""
    try:
        return zoneinfo.ZoneInfo(settings.BILLING_TIMEZONE or "America/Denver")
    except Exception:  # noqa: BLE001
        return zoneinfo.ZoneInfo("America/Denver")


def _business_days(start: dt.date, end_exclusive: dt.date) -> int:
    """Weekdays in [start, end). D7 may add holidays; it does not need to to be useful."""
    days = 0
    cur = start
    while cur < end_exclusive:
        if cur.weekday() < 5:
            days += 1
        cur += dt.timedelta(days=1)
    return days


def resolve_period(period: str | None, today: dt.date) -> dict:
    """`mtd` (default) or `YYYY-MM`. Returns the window plus how far through it we are.

    `elapsed` is a BUSINESS-DAY fraction, not a calendar one. Pace measured on calendar days
    tells a team on the 1st of a month that starts on a Saturday that they are already behind.
    """
    key = (period or "mtd").strip().lower()
    if key in ("", "mtd", "month"):
        first = today.replace(day=1)
    else:
        try:
            year, month = (int(p) for p in key.split("-")[:2])
            first = dt.date(year, month, 1)
        except (ValueError, TypeError):
            first = today.replace(day=1)
    nxt = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    total = _business_days(first, nxt)
    # A past month is complete; the current one is elapsed through TODAY inclusive.
    through = min(max(today + dt.timedelta(days=1), first), nxt)
    done = _business_days(first, through)
    return {
        "key": first.strftime("%Y-%m"),
        "label": first.strftime("%B"),
        "start": first,
        "end": nxt,
        "days_left": max(0, (nxt - dt.timedelta(days=1) - today).days) if first <= today < nxt else 0,
        "elapsed": round(done / total, 4) if total else 1.0,
        "business_days": total,
    }


def _initials(name: str | None) -> str:
    parts = [p for p in (name or "").replace(".", " ").split() if p]
    if not parts:
        return "?"
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


def _first(name: str | None) -> str:
    return (name or "").split(" ")[0] or "?"


async def _seat_for_viewer(s: AsyncSession, tenant_id, user: User,
                           seats: list[RecruitingSeat], as_seat: str | None) -> dict:
    """Which seat the viewer IS, and what they may therefore see.

    `?as=` is owner/admin only. Letting a Team Leader preview another seat would hand them the
    candidate detail the scoping below exists to withhold -- and it would look like a feature.
    """
    is_admin = (user.role or "") in ("owner", "admin")
    own = next((x for x in seats if x.user_id and str(x.user_id) == str(user.id)), None)
    if as_seat and is_admin:
        picked = next((x for x in seats if str(x.id) == str(as_seat)), None)
        if picked is not None:
            return {"seat_id": str(picked.id), "role": picked.role, "name": picked.display_name,
                    "previewing": True, "is_admin": True}
    if is_admin:
        return {"seat_id": str(own.id) if own else None, "role": "owner",
                "name": own.display_name if own else (user.name or "Owner"),
                "previewing": False, "is_admin": True}
    if own is not None:
        return {"seat_id": str(own.id), "role": own.role, "name": own.display_name,
                "previewing": False, "is_admin": False}
    # A viewer with the tab and no seat: they see the team's shape and nobody's candidate detail.
    return {"seat_id": None, "role": "viewer", "name": user.name or "", "previewing": False,
            "is_admin": False}


async def _connection(s: AsyncSession, tenant_id, is_admin: bool) -> tuple[Integration | None, dict]:
    row = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id,
        Integration.provider == "ghl_recruiting"))).scalars().first()
    if row is None:
        return None, {"state": "not_connected", "synced_at": None, "sync_failed": False,
                      "error": None, "reason": WHY["not_connected"],
                      "writeback": {"open": False, "reason": "Nothing is connected."}}
    cfg = row.config or {}
    if not cfg.get("pipeline_id"):
        state, reason = "not_configured", WHY["not_configured"]
    elif not cfg.get("synced_at"):
        state, reason = "not_synced", WHY["not_synced"]
    else:
        state, reason = "ready", None
    return row, {
        "state": state,
        "synced_at": cfg.get("synced_at"),
        "sync_failed": (row.status or "") == "error",
        # A raw provider error can carry a location id or a token fragment. Owners only.
        "error": (row.last_error if is_admin else None),
        "reason": reason,
        # Phase 1 writes nothing. Stated as a closed gate rather than omitted, so the UI has the
        # same shape it will have in Phase 4 and the button reads "Sending is off" from day one.
        "writeback": {"open": False, "reason": "Write-back ships in Phase 4."},
    }


def _seat_public(seat: RecruitingSeat) -> dict:
    return {"seat_id": str(seat.id), "name": seat.display_name, "first": _first(seat.display_name),
            "initials": _initials(seat.display_name), "role": seat.role, "title": seat.title}


async def build_recruiting(s: AsyncSession, tenant_id: uuid.UUID, user: User, *,
                           period: str | None = None, as_seat: str | None = None) -> dict:
    """The §3 payload, as far as Phase 1's tables can honestly fill it."""
    tz = _tz()
    now = dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(tz).date()
    per = resolve_period(period, today)

    seats = list((await s.execute(select(RecruitingSeat).where(
        RecruitingSeat.tenant_id == tenant_id, RecruitingSeat.active.is_(True))
        .order_by(RecruitingSeat.role, RecruitingSeat.display_name))).scalars().all())
    viewer = await _seat_for_viewer(s, tenant_id, user, seats, as_seat)
    integ, connection = await _connection(s, tenant_id, viewer["is_admin"])
    by_seat = {str(x.id): x for x in seats}
    leaders = [x for x in seats if x.role == "team_leader"]
    sdr = next((x for x in seats if x.role == "sdr"), None)

    unavailable = {"queue": WHY["queue"], "commitments": WHY["commitments"],
                   "calendars": WHY["calendars"]}

    payload: dict = {
        "as_of": now.isoformat(),
        "connection": connection,
        "viewer": viewer,
        "period": {"key": per["key"], "label": per["label"], "days_left": per["days_left"],
                   "elapsed": per["elapsed"]},
        "seats": [_seat_public(x) for x in seats],
        "goal": None, "path": None, "sdr": None, "calendars": None,
        "queue": {"counts": {"total": 0, "cleared": 0, "by_seat": {}}, "items": [], "cleared": []},
        "commitments": None, "leaderboard": [], "pipeline": None, "sources": None,
        "unavailable": unavailable,
    }
    if connection["state"] != "ready":
        # Every block stays null and `connection.reason` says why. An empty tab that does not
        # say "nothing is connected" gets read as "we recruited nobody".
        return payload

    # ── the facts every block is built from ─────────────────────────────────────────────────
    cands = list((await s.execute(select(RecruitingCandidate).where(
        RecruitingCandidate.tenant_id == tenant_id))).scalars().all())
    open_cands = [c for c in cands if (c.status or "open") == "open"]

    signed_rows = list((await s.execute(select(RecruitingStageEvent).where(
        RecruitingStageEvent.tenant_id == tenant_id,
        RecruitingStageEvent.to_group == SIGNED_GROUP,
        RecruitingStageEvent.occurred_at >= dt.datetime.combine(per["start"], dt.time.min, tz),
        RecruitingStageEvent.occurred_at < dt.datetime.combine(per["end"], dt.time.min, tz),
    ).order_by(RecruitingStageEvent.occurred_at))).scalars().all())
    # Distinct CANDIDATES, not events: a candidate who is moved out of Signed and back in has
    # signed once. The event is the evidence, not the unit.
    signed_by_candidate: dict = {}
    for ev in signed_rows:
        signed_by_candidate.setdefault(str(ev.candidate_id), ev)
    cand_by_id = {str(c.id): c for c in cands}

    appts = list((await s.execute(select(RecruitingAppointment).where(
        RecruitingAppointment.tenant_id == tenant_id))).scalars().all())

    # ── goal: the countable half only ───────────────────────────────────────────────────────
    signings = []
    for cid, ev in signed_by_candidate.items():
        cand = cand_by_id.get(cid)
        signings.append({
            "candidate_id": cid,
            "name": cand.name if cand else None,
            "initials": _initials(cand.name if cand else None),
            "occurred_on": _aware(ev.occurred_at).astimezone(tz).date().isoformat(),
            "seat_id": str(ev.actor_seat_id) if ev.actor_seat_id else None,
        })
    payload["goal"] = {
        "scope": "team", "signed": len(signings), "signings": signings,
        # Everything below needs a goal, and a goal is a Phase 5 setting. Null rather than 0:
        # "on pace for 0 of 0" is a sentence that means nothing and looks like a bug.
        "goal": None, "pace": None, "pace_pct": None, "verdict": None, "need": None, "split": None,
    }
    unavailable["goal"] = WHY["goal"]

    # ── path: who is closest to signing ─────────────────────────────────────────────────────
    path_rows = []
    for cand in open_cands:
        if cand.stage_group not in PATH_GROUPS:
            continue
        entered = _aware(cand.entered_stage_at)
        days = max(0, (now - entered).days) if entered else None
        path_rows.append({
            "candidate_id": str(cand.id), "name": cand.name, "stage": cand.stage_group,
            "days": days, "brokerage": cand.brokerage,
            "owner_initials": _initials(
                by_seat[str(cand.owner_seat_id)].display_name
                if cand.owner_seat_id and str(cand.owner_seat_id) in by_seat else None),
            "owner_seat_id": str(cand.owner_seat_id) if cand.owner_seat_id else None,
            # The status chip is a QUEUE fact (overdue against a rule), and there are no rules
            # until Phase 3. `mute` is the tone that says "no claim", which is the truth here.
            "status": {"label": None, "tone": "mute"},
        })
    path_rows.sort(key=lambda r: (PATH_GROUPS.index(r["stage"]), -(r["days"] or 0)))
    payload["path"] = {"line": _path_line(len(signings), path_rows), "rows": path_rows}

    # ── the SDR card: booked and held are real; dials and replies are not yet ───────────────
    if sdr is not None:
        booked = [a for a in appts if a.booked_by_seat_id and str(a.booked_by_seat_id) == str(sdr.id)
                  and _in_period(a.created_at_src, per, tz)]
        held = [a for a in appts if (a.status or "").lower() == "showed"
                and _in_period(a.start_at, per, tz)]
        # Show rate counts only appointments whose outcome is KNOWN: one that has not happened
        # yet is not a no-show, and counting it as one punishes a team for booking ahead.
        settled = [a for a in appts if _in_period(a.start_at, per, tz)
                   and (a.status or "").lower() in ("showed", "noshow", "no-show", "cancelled")]
        payload["sdr"] = {
            **_seat_public(sdr),
            "month": {"booked": len(booked), "held": len(held),
                      "show_rate": round(100 * len(held) / len(settled)) if settled else None,
                      "goal": None, "pace": None, "show_goal": None, "verdict": None},
            "week": None, "speed_to_lead": None,
            "by_calendar": [_calendar_strip(x, appts, now, tz) for x in leaders],
        }
        unavailable["sdr_week"] = WHY["commitments"]
        unavailable["speed_to_lead"] = WHY["activity"]

    # ── leaderboard: signings and held are countable; pace is not ───────────────────────────
    held_by_seat: dict = defaultdict(int)
    for a in appts:
        if (a.status or "").lower() == "showed" and a.seat_id and _in_period(a.start_at, per, tz):
            held_by_seat[str(a.seat_id)] += 1
    board = []
    for seat in leaders:
        sid = str(seat.id)
        mine = [x for x in signings if x["seat_id"] == sid]
        board.append({**_seat_public(seat), "signed": len(mine), "held_mtd": held_by_seat.get(sid, 0),
                      "goal": None, "pace": None, "pace_pct": None, "close_rate_90d": None,
                      "queue": None})
    board.sort(key=lambda r: (-r["signed"], -r["held_mtd"], r["name"] or ""))
    for i, row in enumerate(board, 1):
        row["rank"] = i
    payload["leaderboard"] = board

    # ── pipeline ────────────────────────────────────────────────────────────────────────────
    groups_cfg = (integ.config or {}).get("recruiting_stage_groups") or []
    counts: dict = defaultdict(lambda: {"n": 0, "gci": 0.0, "stuck": 0})
    for cand in open_cands:
        bucket = counts[cand.stage_group or "Unmapped"]
        bucket["n"] += 1
        bucket["gci"] += float(cand.gci_ttm or 0)
        entered = _aware(cand.entered_stage_at)
        if entered and (now - entered).days >= 7:
            bucket["stuck"] += 1
    ordered = []
    for row in groups_cfg:
        try:
            label, owner_role = row[0], row[1]
        except (TypeError, IndexError):
            continue
        if label == NURTURE_GROUP:
            continue
        got = counts.get(label, {"n": 0, "gci": 0.0, "stuck": 0})
        ordered.append({"label": label, "owner_role": owner_role, "n": got["n"],
                        "gci": round(got["gci"]), "stuck": got["stuck"],
                        # A 90-day conversion needs stage history that only accrues after this
                        # ships. Null now; real once there are 90 days of events.
                        "conv_90d": None})
    payload["pipeline"] = {
        "active": sum(1 for c in open_cands if c.stage_group != NURTURE_GROUP),
        "nurture": sum(1 for c in open_cands if c.stage_group == NURTURE_GROUP),
        "unmapped": sum(1 for c in open_cands if not c.stage_group),
        "stages": ordered,
    }

    # ── sources: owners only ────────────────────────────────────────────────────────────────
    if viewer["is_admin"]:
        by_source: dict = defaultdict(lambda: {"candidates": 0, "signed": 0, "gci": 0.0})
        signed_ids = set(signed_by_candidate)
        for cand in cands:
            row = by_source[cand.source or "Unattributed"]
            row["candidates"] += 1
            if str(cand.id) in signed_ids:
                row["signed"] += 1
                row["gci"] += float(cand.gci_ttm or 0)
        payload["sources"] = sorted(
            [{"name": k, "candidates": v["candidates"], "signed": v["signed"],
              "rate": round(100 * v["signed"] / v["candidates"]) if v["candidates"] else 0,
              # No spend is joined to recruiting, so this is time rather than money. Saying
              # "Time only" is honest; a 0 would read as free.
              "cost_each": None, "gci": round(v["gci"])}
             for k, v in by_source.items()],
            key=lambda r: (-r["signed"], -r["candidates"]))

    return payload


def _in_period(when: dt.datetime | None, per: dict, tz) -> bool:
    when = _aware(when)
    if when is None:
        return False
    local = when.astimezone(tz).date()
    return per["start"] <= local < per["end"]


def _calendar_strip(seat: RecruitingSeat, appts, now, tz) -> dict:
    """One Team Leader's week: the six nearest appointments, as cells the strip can draw."""
    week_start = (now.astimezone(tz).date()
                  - dt.timedelta(days=now.astimezone(tz).date().weekday()))
    mine = [a for a in appts if a.seat_id and str(a.seat_id) == str(seat.id) and a.start_at
            and week_start <= _aware(a.start_at).astimezone(tz).date()
            < week_start + dt.timedelta(days=7)]
    mine.sort(key=lambda a: _aware(a.start_at))
    cells = []
    for a in mine[:6]:
        status = (a.status or "").lower()
        cells.append("held" if status == "showed"
                     else "noshow" if status in ("noshow", "no-show")
                     else "up")
    cells += ["open"] * (6 - len(cells))
    return {**_seat_public(seat), "cells": cells,
            "held": sum(1 for c in cells if c == "held"), "booked": len(mine)}


def _path_line(signed: int, rows: list[dict]) -> str:
    """The sentence above the path list.

    It does NOT mention the goal, because Phase 1 has no goal to mention. Saying "closing every
    offer out gets you to 8" without knowing the target would be inventing the only number that
    matters.
    """
    offers = sum(1 for r in rows if r["stage"] == "Offer out")
    met = sum(1 for r in rows if r["stage"] == "Met")
    if not rows:
        return "Nobody is past the first meeting yet."
    parts = []
    if offers:
        parts.append(f"{offers} offer{'s' if offers != 1 else ''} out")
    if met:
        parts.append(f"{met} met and deciding")
    return f"{' · '.join(parts)}. {signed} signed so far this month."


async def candidate_detail(s: AsyncSession, tenant_id, user: User, candidate_id: str,
                           seats: list[RecruitingSeat] | None = None) -> dict | None:
    """One candidate, with the timeline. Scoped: see the router for who may call this."""
    cand = (await s.execute(select(RecruitingCandidate).where(
        RecruitingCandidate.tenant_id == tenant_id,
        RecruitingCandidate.id == candidate_id))).scalars().first()
    if cand is None:
        return None
    seats = seats if seats is not None else list((await s.execute(select(RecruitingSeat).where(
        RecruitingSeat.tenant_id == tenant_id))).scalars().all())
    by_seat = {str(x.id): x for x in seats}
    tz = _tz()
    now = dt.datetime.now(dt.timezone.utc)

    acts = list((await s.execute(select(RecruitingActivity).where(
        RecruitingActivity.tenant_id == tenant_id,
        RecruitingActivity.candidate_id == cand.id)
        .order_by(RecruitingActivity.occurred_at.desc()).limit(100))).scalars().all())
    moves = list((await s.execute(select(RecruitingStageEvent).where(
        RecruitingStageEvent.tenant_id == tenant_id,
        RecruitingStageEvent.candidate_id == cand.id)
        .order_by(RecruitingStageEvent.occurred_at.desc()).limit(50))).scalars().all())

    timeline = [{"kind": a.kind, "at": _aware(a.occurred_at).isoformat(), "summary": a.summary,
                 "seat_id": str(a.seat_id) if a.seat_id else None, "source": a.source}
                for a in acts]
    timeline += [{"kind": "stage_move", "at": _aware(m.occurred_at).isoformat(),
                  "summary": f"Moved to {m.to_group or m.to_stage_id}",
                  "seat_id": str(m.actor_seat_id) if m.actor_seat_id else None,
                  "source": m.source} for m in moves]
    timeline.sort(key=lambda r: r["at"], reverse=True)

    owner = by_seat.get(str(cand.owner_seat_id)) if cand.owner_seat_id else None
    booker = by_seat.get(str(cand.booker_seat_id)) if cand.booker_seat_id else None
    return {
        "candidate_id": str(cand.id), "name": cand.name, "brokerage": cand.brokerage,
        "city": cand.city, "source": cand.source, "gci_ttm": float(cand.gci_ttm or 0) or None,
        "stage": cand.stage_group, "stage_id": cand.stage_id, "status": cand.status,
        "days_in_stage": (max(0, (now - _aware(cand.entered_stage_at)).days)
                          if cand.entered_stage_at else None),
        "owner": _seat_public(owner) if owner else None,
        "booker": _seat_public(booker) if booker else None,
        "dnd": cand.dnd or {},
        "source_url": cand.source_url,
        "next_step": None,          # mirrors the GHL task; the task is written in Phase 4
        "timeline": timeline,
        "unavailable": {"next_step": WHY["activity"], "actions": "Write-back ships in Phase 4."},
    }


async def may_see_candidate(s: AsyncSession, tenant_id, user: User,
                            cand: RecruitingCandidate) -> bool:
    """Who may open a candidate in full.

    Owners and admins, the seat that owns the candidate, and the SDR for candidates still in
    SDR-owned stages. A Team Leader does NOT get another Team Leader's candidate detail: the
    tab shows them the team's shape and its numbers, which is a different question from a named
    person's contact record and notes.
    """
    if (user.role or "") in ("owner", "admin"):
        return True
    seat = (await s.execute(select(RecruitingSeat).where(
        RecruitingSeat.tenant_id == tenant_id, RecruitingSeat.user_id == user.id,
        RecruitingSeat.active.is_(True)))).scalars().first()
    if seat is None:
        return False
    if cand.owner_seat_id and str(cand.owner_seat_id) == str(seat.id):
        return True
    if seat.role == "sdr":
        integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == tenant_id,
            Integration.provider == "ghl_recruiting"))).scalars().first()
        groups = (integ.config or {}).get("recruiting_stage_groups") if integ else []
        for row in groups or []:
            try:
                if row[0] == cand.stage_group:
                    return row[1] == "sdr"
            except (TypeError, IndexError):
                continue
    return False


async def default_business(s: AsyncSession, tenant_id):
    """The brokerage. Recruiting is the real-estate business's, resolved by KIND -- a hardcoded
    business key is one customer's name for her own company (integrations_view carries the scar)."""
    return await roles.real_estate(s, tenant_id)
