import asyncio
import datetime as dt
from zoneinfo import ZoneInfo

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


async def roster_tick():
    """Daily: refresh Sisu agent group memberships (agent.sisu_group_ids) — the live source for
    per-team scorecard attribution. Runs before scorecard_tick; one tenant's failure is isolated.
    A no-op when Sisu isn't configured (fetches just fail and leave the roster as-is)."""
    from .services.sync import sync_agent_offices
    async with SessionLocal() as s:
        tenant_ids = (await s.execute(select(Tenant.id))).scalars().all()
    for tid in tenant_ids:
        try:
            async with SessionLocal() as s2:
                await sync_agent_offices(s2, tid)
        except Exception as e:
            print(f"[roster_tick] tenant {tid}: {type(e).__name__}: {e}", flush=True)


async def scorecard_tick():
    """ULRG L10 Scorecard resolvers (SPEC 3.1): daily, resolving the open week plus a trailing
    look-back so late syncs self-heal. `today` is the business-local date — the server runs UTC, so
    a naive date would flip the Monday-keyed week a day early and drop late closings. No-op until a
    metric sets a resolver_key. One tenant's failure is isolated so it can't stop the rest."""
    from .services.scorecard_resolvers import run_resolvers
    today = dt.datetime.now(ZoneInfo(settings.BILLING_TIMEZONE)).date()
    async with SessionLocal() as s:
        tenant_ids = (await s.execute(select(Tenant.id))).scalars().all()
    for tid in tenant_ids:                              # run_resolvers isolates each (metric, week)
        try:
            await run_resolvers(SessionLocal, tid, today)
        except Exception as e:
            print(f"[scorecard_tick] tenant {tid}: {type(e).__name__}: {e}", flush=True)


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


async def recall_tick():
    """Book recording bots for Alignment Calls about to start.

    Runs often and does almost nothing most times: it only touches calls inside a short
    lookahead window that have a meeting link and no bot yet. Inert without RECALL_API_KEY.
    """
    if not settings.RECALL_API_KEY:
        return
    from .services.recall import schedule_due_bots
    async with SessionLocal() as s:
        try:
            stat = await schedule_due_bots(s)
        except Exception as e:                       # noqa: BLE001 — never kill the scheduler
            print(f"[recall] tick failed: {type(e).__name__}: {e}", flush=True)
            return
    if stat:
        print(f"[recall] {stat}", flush=True)


async def transcript_tick():
    """Store transcripts for finished recordings, and purge any past their retention date.

    Both halves run together on purpose: the job that CREATES the records is the job that
    expires them, so retention can't quietly stop being enforced while ingestion continues.
    """
    if not settings.RECALL_API_KEY:
        return
    from .services.recall import purge_expired_transcripts, store_transcripts
    async with SessionLocal() as s:
        try:
            stat = await store_transcripts(s)
            purged = await purge_expired_transcripts(s)
        except Exception as e:                       # noqa: BLE001 - never kill the scheduler
            print(f"[recall] transcript tick failed: {type(e).__name__}: {e}", flush=True)
            return
    if stat or purged:
        print(f"[recall] transcripts {stat} purged={purged}", flush=True)


def build_scheduler() -> AsyncIOScheduler:
    """Configure the scheduler with the sync tick, the daily agent-roster + scorecard-resolver ticks,
    and (when the flag is on) the two AI jobs. Shared by the standalone worker (`python -m app.worker`)
    and the in-API scheduler (RUN_WORKER_IN_API) so both run exactly the same jobs."""
    sched = AsyncIOScheduler()
    sched.add_job(tick, "interval", minutes=settings.SYNC_INTERVAL_MINUTES,
                  next_run_time=dt.datetime.now())
    _tz = ZoneInfo(settings.BILLING_TIMEZONE)
    sched.add_job(roster_tick, "cron", hour=4, minute=45, timezone=_tz)   # refresh agent→office first
    sched.add_job(scorecard_tick, "cron", hour=5, minute=15, timezone=_tz)  # then resolve, business-local
    if settings.RECALL_API_KEY:
        sched.add_job(recall_tick, "interval", minutes=settings.RECALL_TICK_MINUTES,
                      next_run_time=dt.datetime.now())
        # Transcripts land minutes after a call ends, so this need not be as eager as the
        # bot scheduler - and it carries the retention purge with it.
        sched.add_job(transcript_tick, "interval", minutes=15, next_run_time=dt.datetime.now())
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
