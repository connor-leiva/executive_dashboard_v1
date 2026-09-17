"""The scheduler's jobs as the operator console describes them, and the heartbeat each one writes.

Without a heartbeat the System view's only evidence that the worker runs would be the newest sync
run, and that cannot tell a stopped scheduler from a fleet with nothing connected. Each scheduled job
is wrapped in `heartbeat()` when it is registered (worker.build_scheduler), which records when it
last started, last finished cleanly and last failed. Calling a job directly, as the tests do, writes
nothing.
"""
from __future__ import annotations

import datetime as dt
import functools
import time

from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..db import SessionLocal
from ..models import JobHeartbeat

DAY = 24 * 60


def catalog() -> list[dict]:
    """Every job build_scheduler registers: what it does, how often, and whether this environment
    runs it at all."""
    recall = bool(settings.RECALL_API_KEY)
    ai = bool(settings.AI_EMPLOYEES_ENABLED)
    return [
        {"job": "tick", "what": "Syncs every connected source", "every_minutes": settings.SYNC_INTERVAL_MINUTES, "enabled": True},
        {"job": "roster_tick", "what": "Refreshes Sisu agent offices", "every_minutes": DAY, "enabled": True},
        {"job": "scorecard_tick", "what": "Resolves scorecard metrics", "every_minutes": DAY, "enabled": True},
        {"job": "ads_funnel_tick", "what": "Attributes registrations to ads", "every_minutes": DAY, "enabled": True},
        {"job": "marketing_delivery_tick", "what": "Delivers marketing requests", "every_minutes": 1, "enabled": True},
        {"job": "recall_tick", "what": "Books call-recording bots", "every_minutes": settings.RECALL_TICK_MINUTES, "enabled": recall},
        {"job": "transcript_tick", "what": "Stores call transcripts and purges expired ones", "every_minutes": 15, "enabled": recall},
        {"job": "ai_dispatch", "what": "Queues AI employee runs", "every_minutes": 1, "enabled": ai},
        {"job": "ai_execute", "what": "Runs queued AI employee work", "every_minutes": 0.25, "enabled": ai},
        {"job": "platform_audit_prune", "what": "Deletes operator audit entries past retention", "every_minutes": 30 * DAY, "enabled": True},
    ]


def _aware(d: dt.datetime | None) -> dt.datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def state_of(entry: dict, row: JobHeartbeat | None, now: dt.datetime) -> str:
    """ok | late | check | never | idle, for one job.

    `idle` is a job this environment does not run, or a daily-or-slower job that has not come due
    since heartbeats began. `never` is a frequent job that should have reported and has not. `check`
    is a job whose latest run failed. `late` is one that has not started within twice its cadence.
    """
    if not entry["enabled"]:
        return "idle"
    if row is None or row.last_started_at is None:
        return "idle" if entry["every_minutes"] >= DAY else "never"
    failed, ok = _aware(row.last_failed_at), _aware(row.last_ok_at)
    if failed and (ok is None or failed > ok):
        return "check"
    if now - _aware(row.last_started_at) > dt.timedelta(minutes=2 * max(entry["every_minutes"], 1)):
        return "late"
    return "ok"


async def _beat(job: str, **fields) -> None:
    """Upsert one job's heartbeat. Never raises: a heartbeat that could not be written must not
    stop the job it describes."""
    try:
        async with SessionLocal() as s:
            row = await s.get(JobHeartbeat, job)
            if row is None:
                row = JobHeartbeat(job=job)
                s.add(row)
            for key, value in fields.items():
                setattr(row, key, value)
            try:
                await s.commit()
            except IntegrityError:                 # another process inserted the row first
                await s.rollback()
    except Exception as e:  # noqa: BLE001
        print(f"[heartbeat] {job}: {type(e).__name__}: {e}", flush=True)


def heartbeat(fn, name: str | None = None):
    job = name or fn.__name__

    @functools.wraps(fn)
    async def run(*args, **kwargs):
        started = time.monotonic()
        await _beat(job, last_started_at=dt.datetime.now(dt.timezone.utc))
        try:
            result = await fn(*args, **kwargs)
        except Exception as e:
            await _beat(job, last_failed_at=dt.datetime.now(dt.timezone.utc),
                        last_error=f"{type(e).__name__}: {e}"[:1000],
                        last_seconds=round(time.monotonic() - started, 1))
            raise
        await _beat(job, last_ok_at=dt.datetime.now(dt.timezone.utc),
                    last_seconds=round(time.monotonic() - started, 1))
        return result
    return run
