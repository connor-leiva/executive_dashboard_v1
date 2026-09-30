"""Onboarding — reading a plan and its progress, and seeding one from a written programme.

ONBOARDING-SPEC.md §4. Two jobs, deliberately in one module:

  * `plan_payload` assembles everything the tab draws in one query set. The tab has five views
    over the SAME plan -- the day, the week, the scoreboard, the script library, the rules -- so
    five endpoints would be five round trips for one screen and four chances for them to
    disagree about how far along somebody is.

  * `seed_plan` turns a written programme into rows. The first one arrived as a hand-built HTML
    page with the days in the bundle; this is what makes the second one an insert.

NO DERIVED PROGRESS IS STORED. "14 of 23 days done", "5 of 9 targets met" and the rest are
computed here on every read. They are cheap (a plan is a hundred rows), and a stored counter is
a second source of truth that goes wrong silently the first time a row is edited by anything
that forgets to update it.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (OnboardingBlock, OnboardingConversation, OnboardingDay, OnboardingPlan,
                      OnboardingScript, OnboardingTarget, OnboardingWeek, User)


def _today() -> dt.date:
    return dt.date.today()


def _iso(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


def _day_title(day: OnboardingDay) -> str:
    """"Thursday, October 1" — or "Weekend, October 3 to 4" for a span.

    The month is spelled from the date rather than stored, so a plan that crosses a month
    boundary reads correctly without its author having written the label out.
    """
    # Built by hand rather than with strftime("%B %-d"): the no-pad flag is %-d on Linux and
    # %#d on Windows, and the wrong one raises rather than mis-formatting.
    start = f"{day.day_date.strftime('%B')} {day.day_date.day}"
    if day.end_date:
        if day.end_date.month == day.day_date.month:
            return f"{day.dow}, {start} to {day.end_date.day}"
        return f"{day.dow}, {start} to {day.end_date.strftime('%B')} {day.end_date.day}"
    return f"{day.dow}, {start}"


def _relative(due: dt.date, today: dt.date) -> str:
    diff = (due - today).days
    if diff > 1:
        return f"in {diff} days"
    return {1: "tomorrow", 0: "today"}.get(diff, "passed")


async def plan_summaries(s: AsyncSession, tenant_id, user: User) -> list[dict]:
    """Every plan this user may open, newest start first.

    A member sees only their own. That is not a tab grant question -- the grant is what puts
    Onboarding in their rail at all -- it is that a colleague's day-by-day debrief of a month
    they found hard is not something a tab grant should hand out.
    """
    q = select(OnboardingPlan).where(OnboardingPlan.tenant_id == tenant_id)
    if user.role not in ("owner", "admin"):
        q = q.where(OnboardingPlan.user_id == user.id)
    rows = (await s.execute(q.order_by(OnboardingPlan.starts_on.desc()))).scalars().all()
    return [{"id": str(p.id), "subject_name": p.subject_name, "title": p.title,
             "starts_on": _iso(p.starts_on), "ends_on": _iso(p.ends_on), "status": p.status,
             "is_mine": p.user_id == user.id} for p in rows]


async def readable_plan(s: AsyncSession, tenant_id, user: User,
                        plan_id: uuid.UUID | None) -> OnboardingPlan | None:
    """The plan to show: the one asked for, else this user's own, else the newest they may see.

    Returns None rather than raising when there is nothing -- an empty Onboarding tab is a real
    state (a workspace that has not written a plan yet), not an error.
    """
    q = select(OnboardingPlan).where(OnboardingPlan.tenant_id == tenant_id)
    if user.role not in ("owner", "admin"):
        q = q.where(OnboardingPlan.user_id == user.id)
    if plan_id is not None:
        return (await s.execute(q.where(OnboardingPlan.id == plan_id))).scalar_one_or_none()
    mine = (await s.execute(q.where(OnboardingPlan.user_id == user.id)
                            .order_by(OnboardingPlan.starts_on.desc()))).scalars().first()
    if mine is not None:
        return mine
    return (await s.execute(q.order_by(OnboardingPlan.starts_on.desc()))).scalars().first()


def may_write(user: User, plan: OnboardingPlan) -> bool:
    """Whose plan it is to tick off.

    The subject, plus owners and admins. Admins are included because a plan is run WITH somebody
    -- the seeded one ends in a review with two of them -- and because a plan whose subject has
    not been given a login yet would otherwise be unwritable by anyone.
    """
    return user.role in ("owner", "admin") or plan.user_id == user.id


async def plan_payload(s: AsyncSession, plan: OnboardingPlan, user: User,
                       today: dt.date | None = None) -> dict:
    """Everything the tab draws, for one plan."""
    today = today or _today()
    weeks = (await s.execute(select(OnboardingWeek)
                             .where(OnboardingWeek.plan_id == plan.id)
                             .order_by(OnboardingWeek.sort_order))).scalars().all()
    days = (await s.execute(select(OnboardingDay)
                            .where(OnboardingDay.plan_id == plan.id)
                            .order_by(OnboardingDay.sort_order))).scalars().all()
    blocks = (await s.execute(select(OnboardingBlock)
                              .where(OnboardingBlock.day_id.in_([d.id for d in days] or [None]))
                              .order_by(OnboardingBlock.sort_order))).scalars().all()
    targets = (await s.execute(select(OnboardingTarget)
                               .where(OnboardingTarget.plan_id == plan.id)
                               .order_by(OnboardingTarget.sort_order))).scalars().all()
    scripts = (await s.execute(select(OnboardingScript)
                               .where(OnboardingScript.plan_id == plan.id)
                               .order_by(OnboardingScript.created_at.desc()))).scalars().all()
    convos = (await s.execute(select(OnboardingConversation)
                              .where(OnboardingConversation.plan_id == plan.id)
                              .order_by(OnboardingConversation.created_at.desc()))).scalars().all()

    by_day: dict[uuid.UUID, list[OnboardingBlock]] = {}
    for b in blocks:
        by_day.setdefault(b.day_id, []).append(b)

    target_vms = []
    for t in targets:
        met = (t.actual >= t.target) if t.target is not None else t.done
        target_vms.append({
            "id": str(t.id), "label": t.label, "target": t.target, "actual": t.actual,
            "done": t.done, "is_count": t.target is not None,
            "due_on": _iso(t.due_on), "relative": _relative(t.due_on, today), "met": met,
            # A target is only MISSED once its date has gone by. Before that it is outstanding,
            # which is a different thing to say to somebody mid-week.
            "missed": bool(not met and t.due_on < today),
        })

    day_vms = []
    for d in days:
        bs = by_day.get(d.id, [])
        done = sum(1 for b in bs if b.done)
        hits = sum(1 for b in bs if b.outcome_state == "hit")
        misses = sum(1 for b in bs if b.outcome_state == "miss")
        day_vms.append({
            "id": str(d.id), "week_id": str(d.week_id), "title": _day_title(d),
            "date": _iso(d.day_date), "end_date": _iso(d.end_date), "dow": d.dow,
            "location": d.location, "tag": d.tag, "offsite": d.offsite,
            "notes": list(d.notes or []), "debrief": d.debrief or "",
            "blocks": [{"id": str(b.id), "time": b.time_label, "task": b.task,
                        "outcome": b.outcome, "done": b.done, "outcome_state": b.outcome_state}
                       for b in bs],
            "done": done, "total": len(bs), "hits": hits, "misses": misses,
            # Targets that fall due on this day, so the day card can say what is riding on it.
            "due": [t for t in target_vms if t["due_on"] == _iso(d.day_date)],
            "is_today": d.day_date <= today <= (d.end_date or d.day_date),
            "is_past": (d.end_date or d.day_date) < today,
        })

    week_vms = []
    for w in weeks:
        wd = [v for v in day_vms if v["week_id"] == str(w.id)]
        week_vms.append({
            "id": str(w.id), "label": w.label, "date_range": w.date_range,
            "subtitle": w.subtitle, "intro": list(w.intro or []), "outro": list(w.outro or []),
            "show_rules": w.show_rules, "day_ids": [v["id"] for v in wd],
            "done": sum(v["done"] for v in wd), "total": sum(v["total"] for v in wd),
            "hits": sum(v["hits"] for v in wd), "misses": sum(v["misses"] for v in wd),
        })

    total_blocks = sum(v["total"] for v in day_vms)
    return {
        "id": str(plan.id), "subject_name": plan.subject_name, "title": plan.title,
        "starts_on": _iso(plan.starts_on), "ends_on": _iso(plan.ends_on), "status": plan.status,
        "motions": list(plan.motions or []), "rules": list(plan.rules or []),
        "can_write": may_write(user, plan),
        # Before the first day, the tab says so rather than opening on a day that has not
        # happened: the plan is a thing somebody is about to start, and pretending otherwise
        # makes every counter read as a failure.
        "not_started": today < plan.starts_on,
        "weeks": week_vms, "days": day_vms, "targets": target_vms,
        "scripts": [{"id": str(x.id), "motion": x.motion, "text": x.text,
                     "created_at": x.created_at.isoformat() if x.created_at else None}
                    for x in scripts],
        "conversations": [{"id": str(c.id), "name": c.name, "team": c.team, "context": c.context,
                           "next_step": c.next_step, "heat": c.heat,
                           "appointment_set": c.appointment_set,
                           "created_at": c.created_at.isoformat() if c.created_at else None}
                          for c in convos],
        "totals": {
            "blocks": total_blocks,
            "blocks_done": sum(v["done"] for v in day_vms),
            "hits": sum(v["hits"] for v in day_vms),
            "misses": sum(v["misses"] for v in day_vms),
            "targets": len(target_vms),
            "targets_met": sum(1 for t in target_vms if t["met"]),
            "targets_missed": sum(1 for t in target_vms if t["missed"]),
            "conversations": len(convos),
            "appointments": sum(1 for c in convos if c.appointment_set),
            "scripts": len(scripts),
        },
    }


# ── seeding ───────────────────────────────────────────────────────────────────────────────

async def seed_plan(s: AsyncSession, tenant_id, spec: dict, user_id=None) -> OnboardingPlan:
    """Write a programme out as rows. `spec` is the shape ONBOARDING-SPEC.md §5 documents.

    Returns the plan without committing -- the caller owns the transaction, which is what lets a
    seeding script write several plans or roll the lot back.
    """
    plan = OnboardingPlan(
        tenant_id=tenant_id, user_id=user_id, subject_name=spec["subject_name"],
        title=spec["title"], starts_on=dt.date.fromisoformat(spec["starts_on"]),
        ends_on=dt.date.fromisoformat(spec["ends_on"]),
        status=spec.get("status", "active"),
        motions=list(spec.get("motions") or []), rules=list(spec.get("rules") or []))
    s.add(plan)
    await s.flush()

    day_order = 0
    for wi, w in enumerate(spec.get("weeks") or []):
        week = OnboardingWeek(
            tenant_id=tenant_id, plan_id=plan.id, label=w["label"],
            date_range=w["date_range"], subtitle=w.get("subtitle"),
            intro=list(w.get("intro") or []), outro=list(w.get("outro") or []),
            show_rules=bool(w.get("show_rules")), sort_order=wi)
        s.add(week)
        await s.flush()
        for d in w.get("days") or []:
            day = OnboardingDay(
                tenant_id=tenant_id, plan_id=plan.id, week_id=week.id,
                day_date=dt.date.fromisoformat(d["date"]),
                end_date=dt.date.fromisoformat(d["end_date"]) if d.get("end_date") else None,
                dow=d["dow"], location=d.get("location"), tag=d.get("tag"),
                offsite=bool(d.get("offsite")), notes=list(d.get("notes") or []),
                sort_order=day_order)
            day_order += 1
            s.add(day)
            await s.flush()
            for bi, b in enumerate(d.get("blocks") or []):
                s.add(OnboardingBlock(tenant_id=tenant_id, day_id=day.id, time_label=b["time"],
                                      task=b["task"], outcome=b["outcome"], sort_order=bi))

    for ti, t in enumerate(spec.get("targets") or []):
        s.add(OnboardingTarget(tenant_id=tenant_id, plan_id=plan.id, label=t["label"],
                               target=t.get("target"), due_on=dt.date.fromisoformat(t["due_on"]),
                               sort_order=ti))
    await s.flush()
    return plan
