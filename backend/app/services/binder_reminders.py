"""Acumyn Binder — reminders (SPEC-binder-module Part 7).

The mechanism that turns the Binder from a page you must remember to check into something
that reaches out. Runs in the worker (after status recompute), stages each applicable
obligation, fires each stage at most once (dedup via ``last_reminded_stage`` — invariant 5),
and assembles a per-entity digest for the owner/admins + the ``binder``-tab maintainer.

Delivery: in-app is always on (the matrix flags + the review badge render the counts). Email
is a digest (``BINDER_REMINDER_DIGEST`` = daily | weekly) plus an immediate note when something
first crosses into overdue. ``_deliver`` sends it through ``services.mailer``, and no behavior
depends on that succeeding: with no ``RESEND_API_KEY`` the mailer logs the digest and the worker
tick proceeds exactly as it did before there was any transport at all.

THIS RUNS IN THE WORKER, not the api. ``RESEND_API_KEY`` and ``MAIL_FROM`` have to be set on
BOTH Railway services — set on the api alone and these digests keep silently logging while
every invite sends fine, which reads as a Binder bug rather than a missing variable.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Obligation, LegalEntity, User, SyncRun
from . import mail_templates, mailer
from .binder import KIND_LABELS

log = logging.getLogger("app")

# none < lead < urgent < overdue — a reminder only fires when the stage advances.
_STAGE_RANK = {None: 0, "none": 0, "lead": 1, "urgent": 2, "overdue": 3}
_URGENT_DAYS = 14


def compute_stage(due_date: dt.date | None, today: dt.date, lead_days: int) -> str | None:
    """The reminder stage an obligation should be at, or None if it's not yet in a window
    (Part 7): overdue (past), urgent (<=14d), lead (<=lead_days)."""
    if due_date is None:
        return None
    if due_date < today:
        return "overdue"
    days = (due_date - today).days
    if days <= _URGENT_DAYS:
        return "urgent"
    if days <= lead_days:
        return "lead"
    return None


async def _recipients(s: AsyncSession, tenant_id) -> list[str]:
    """Owner/admins + members granted the binder tab (the maintainer)."""
    users = (await s.execute(select(User).where(
        User.tenant_id == tenant_id, User.status == "active"))).scalars().all()
    out = []
    for u in users:
        if u.role in ("owner", "admin") or ("binder" in (u.tab_access or [])):
            if u.email:
                out.append(u.email)
    return sorted(set(out))


async def _deliver(recipients: list[str], subject: str, body: str) -> None:
    """Send the assembled digest. Deliberately never raises — a reminder-send failure must not
    break the worker tick, and the SyncRun row is written either way."""
    if not recipients:
        log.info("binder_reminders digest: no recipients")
        return
    await mailer.send(recipients, *mail_templates.binder_digest(subject, body))


def _cadence_days() -> int:
    return 7 if (settings.BINDER_REMINDER_DIGEST or "daily").lower() == "weekly" else 1


async def run_reminders(s: AsyncSession, tenant_id, today: dt.date | None = None) -> dict:
    """Stage every applicable obligation (dedup-firing), then deliver the digest on its cadence.
    Idempotent: the staging can run every worker cycle; the digest sends once per cadence window.
    Returns a summary for observability."""
    today = today or dt.date.today()
    now = dt.datetime.now(dt.timezone.utc)
    obs = (await s.execute(select(Obligation).where(
        Obligation.tenant_id == tenant_id, Obligation.applicable.is_(True)))).scalars().all()
    ents = {e.id: e for e in (await s.execute(select(LegalEntity).where(
        LegalEntity.tenant_id == tenant_id))).scalars().all()}

    fired, overdue_new = 0, 0
    flagged: dict = {}          # entity_id -> [(kind, due_date, stage)]
    for ob in obs:
        stage = compute_stage(ob.due_date, today, ob.lead_days)
        if stage is None:
            continue
        flagged.setdefault(ob.entity_id, []).append((ob.kind, ob.due_date, stage))
        if _STAGE_RANK[stage] > _STAGE_RANK.get(ob.last_reminded_stage, 0):
            was = ob.last_reminded_stage
            ob.last_reminded_stage, ob.last_reminded_at = stage, now
            fired += 1
            if stage == "overdue" and _STAGE_RANK.get(was, 0) < _STAGE_RANK["overdue"]:
                overdue_new += 1

    # Digest cadence gate: only assemble/deliver once per daily/weekly window.
    last = (await s.execute(select(SyncRun).where(
        SyncRun.tenant_id == tenant_id, SyncRun.provider == "binder_reminders",
        SyncRun.status == "ok").order_by(SyncRun.finished_at.desc()).limit(1))).scalar_one_or_none()
    window = _cadence_days()
    last_at = last.finished_at if last else None
    if last_at is not None and last_at.tzinfo is None:      # SQLite hands back naive datetimes
        last_at = last_at.replace(tzinfo=dt.timezone.utc)
    due_for_digest = last_at is None or (now - last_at) >= dt.timedelta(days=window)

    delivered = False
    if due_for_digest and flagged:
        lines = []
        for eid, items in flagged.items():
            name = ents[eid].legal_name if eid in ents else "Unknown entity"
            lines.append(f"{name}:")
            for kind, due, stage in sorted(items, key=lambda x: (x[2] != "overdue", x[1] or dt.date.max)):
                due_s = due.isoformat() if due else "no date"
                lines.append(f"  - {KIND_LABELS.get(kind, kind)} ({stage}, due {due_s})")
        subject = f"Binder: {sum(len(v) for v in flagged.values())} obligation(s) need attention"
        if overdue_new:
            subject = f"Binder: {overdue_new} newly overdue + more"
        await _deliver(await _recipients(s, tenant_id), subject, "\n".join(lines))
        run = SyncRun(tenant_id=tenant_id, provider="binder_reminders", status="ok",
                      finished_at=now, stats={"fired": fired, "overdue_new": overdue_new,
                                              "entities_flagged": len(flagged)})
        s.add(run)
        delivered = True

    await s.commit()
    return {"fired": fired, "overdue_new": overdue_new, "entities_flagged": len(flagged),
            "digest_delivered": delivered}
