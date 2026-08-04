import asyncio
import datetime as dt

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from .db import SessionLocal
from .config import settings
from .models import Tenant
from .services.sync import run_all


def _mtd_range():
    today = dt.date.today()
    return today.replace(day=1).isoformat(), today.isoformat()


async def tick():
    start, end = _mtd_range()
    async with SessionLocal() as s:
        tenants = (await s.execute(select(Tenant))).scalars().all()
        for t in tenants:
            await run_all(s, t.id, start, end)


async def ai_dispatch():
    """AI Employees — every minute: for each tenant, queue AIRun rows for cron-due skills and
    the pace_check condition. No model calls here; just scheduling (SPEC §4)."""
    if not settings.AI_EMPLOYEES_ENABLED:
        return
    from .services.ai_employees import dispatch_tenant
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        for t in (await s.execute(select(Tenant))).scalars().all():
            try:
                await dispatch_tenant(s, t.id, now)
            except Exception as e:                              # one tenant's fault mustn't stop the rest
                print(f"[ai_dispatch] tenant {t.id}: {type(e).__name__}: {e}", flush=True)


async def ai_execute():
    """AI Employees — every 15s: drain queued runs (oldest first) into draft artifacts via the
    Anthropic call. Bounded per tick so a large backlog paces over ticks (SPEC §4)."""
    if not settings.AI_EMPLOYEES_ENABLED:
        return
    from .services.ai_employees import execute_one
    async with SessionLocal() as s:
        for t in (await s.execute(select(Tenant))).scalars().all():
            try:
                for _ in range(5):                              # up to 5 runs per tenant per tick
                    if not await execute_one(s, t.id):
                        break
            except Exception as e:
                print(f"[ai_execute] tenant {t.id}: {type(e).__name__}: {e}", flush=True)


def build_scheduler() -> AsyncIOScheduler:
    """Configure the scheduler with the sync tick + (when the flag is on) the two AI jobs.
    Shared by the standalone worker (`python -m app.worker`) and the in-API scheduler
    (RUN_WORKER_IN_API) so both run exactly the same jobs."""
    sched = AsyncIOScheduler()
    sched.add_job(tick, "interval", minutes=settings.SYNC_INTERVAL_MINUTES,
                  next_run_time=dt.datetime.now())
    if settings.AI_EMPLOYEES_ENABLED:
        sched.add_job(ai_dispatch, "interval", minutes=1, next_run_time=dt.datetime.now())
        sched.add_job(ai_execute, "interval", seconds=15, next_run_time=dt.datetime.now())
    return sched


async def main():
    build_scheduler().start()
    print(f"[worker] started · interval={settings.SYNC_INTERVAL_MINUTES}m"
          + (" · ai=on" if settings.AI_EMPLOYEES_ENABLED else ""))
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
