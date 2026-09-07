import asyncio
import datetime as dt
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from .db import SessionLocal
from .config import settings
from .startup_checks import enforce_config
from .models import Tenant
from .services.sync import run_all


def _mtd_range():
    today = dt.date.today()
    return today.replace(day=1).isoformat(), today.isoformat()


async def tick():
    """The main sync. One tenant per session, each isolated.

    This shared ONE session across every tenant with no try/except, while the four jobs below
    it already did the right thing. Two consequences, both silent: an exception escaping
    run_all skipped every tenant later in the loop, on every tick, forever; and a failed flush
    left the shared session unable to commit, so the tenants after the failure could not
    persist their work either.

    Serial on purpose. run_all opens sessions of its own, and the pool (5 + 10 overflow) is
    shared with request handling when RUN_WORKER_IN_API is set — a gather() here would exhaust
    it at around seven concurrent tenants.
    """
    start, end = _mtd_range()
    async with SessionLocal() as s:
        tenant_ids = (await s.execute(select(Tenant.id))).scalars().all()
    for tid in tenant_ids:
        try:
            async with SessionLocal() as s2:
                await run_all(s2, tid, start, end)
        except Exception as e:  # noqa: BLE001 — one tenant's failure must not stop the rest
            print(f"[tick] tenant {tid}: {type(e).__name__}: {e}", flush=True)


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
                # Rollback, not a fresh session per tenant: ai_execute runs every 15 seconds
                # and would churn the pool. What matters is that a poisoned session cannot
                # commit for the tenants that follow — this is the fix for that, not for the
                # isolation, which the try/except already handles.
                await s.rollback()
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
                await s.rollback()                              # see the note in ai_dispatch
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
        tenant_ids = (await s.execute(select(Tenant.id))).scalars().all()
    for tid in tenant_ids:
        # Per tenant, per session. schedule_due_bots now requires a tenant and returns
        # immediately for one that has not opted in — booking a billable, attendee-visible
        # bot into a client call is not something to do for a tenant that never asked.
        try:
            async with SessionLocal() as s2:
                stat = await schedule_due_bots(s2, tid)
        except Exception as e:                       # noqa: BLE001 — never kill the scheduler
            print(f"[recall] tenant {tid} tick failed: {type(e).__name__}: {e}", flush=True)
            continue
        if stat:
            print(f"[recall] tenant {tid}: {stat}", flush=True)


async def transcript_tick():
    """Store transcripts for finished recordings, and purge any past their retention date.

    Both halves run in this one job on purpose: the job that CREATES the records is the job
    that expires them, so retention cannot quietly stop being enforced while ingestion
    continues. They no longer share a try — see below.
    """
    if not settings.RECALL_API_KEY:
        return
    from .services.call_chapters import generate_pending
    from .services.recall import purge_expired_transcripts, store_transcripts
    async with SessionLocal() as s:
        tenant_ids = (await s.execute(select(Tenant.id))).scalars().all()

    # RETENTION FIRST, in its own session and its own try. The promise above was not actually
    # kept: the purge shared a try with store_transcripts, so ANY storage error skipped it and
    # returned. Deleting a verbatim client conversation on schedule is the one thing here that
    # must not depend on anything else succeeding.
    purged = 0
    try:
        async with SessionLocal() as s2:
            purged = await purge_expired_transcripts(s2)
    except Exception as e:                           # noqa: BLE001
        print(f"[recall] retention purge failed: {type(e).__name__}: {e}", flush=True)

    for tid in tenant_ids:
        stat: dict = {}
        try:
            async with SessionLocal() as s2:
                stat = await store_transcripts(s2, tid)
        except Exception as e:                       # noqa: BLE001 - never kill the scheduler
            print(f"[recall] tenant {tid} transcripts failed: {type(e).__name__}: {e}", flush=True)
        # Chaptering is a separate try: it talks to a different vendor and is the only part of
        # this tick that can fail on its own. A model outage must not take storage down with it.
        try:
            async with SessionLocal() as s2:
                stat |= await generate_pending(s2, tid)
        except Exception as e:                       # noqa: BLE001
            print(f"[chapters] tenant {tid} failed: {type(e).__name__}: {e}", flush=True)
        if stat:
            print(f"[recall] tenant {tid} transcripts {stat}", flush=True)
    if purged:
        print(f"[recall] purged={purged}", flush=True)


async def ads_funnel_tick():
    """Attribution and (from Phase 3) conversions, daily. SPEC-ads-module.md Part 11.1.

    Daily rather than on the sync tick, deliberately. Attribution is WRITE-ONCE, so re-running it
    every thirty minutes would do nothing but read the whole registration table twenty-four times
    an hour - and the one field it can move, last_seen_on, does not need that resolution.

    Per tenant, with one workspace's failure isolated from the rest: most tenants will never
    connect Meta, and for them this is a no-op rather than an error in the log every night.
    """
    from .services.ads_funnel import sync_ad_attribution, sync_ad_conversions
    async with SessionLocal() as s:
        tenant_ids = (await s.execute(select(Tenant.id))).scalars().all()
    for tid in tenant_ids:
        try:
            async with SessionLocal() as s:
                await sync_ad_attribution(s, tid)
                # Conversions read the attribution rows written a moment ago, so the order is
                # load-bearing rather than incidental.
                await sync_ad_conversions(s, tid)
        except Exception as e:  # noqa: BLE001
            print(f"[ads_funnel_tick] tenant {tid}: {type(e).__name__}: {e}", flush=True)


async def marketing_delivery_tick():
    """Drain the marketing-request delivery queue. Intranet Phase 3.

    Every request that is due, across every tenant, in one pass -- the index leads with the due
    time rather than the tenant for exactly this reason. A workspace that has never switched
    marketing requests on contributes no rows, so this costs an empty query.

    ONE SESSION PER REQUEST, and a failure in one is caught here rather than allowed to end the
    pass. A single tenant whose webhook host has vanished must not stop every other workspace's
    requests from going out, which is precisely what a shared session and one bubbling exception
    would do.

    The due list is read first and closed before any delivery runs. Holding a transaction open
    across ten seconds of somebody else's HTTP is how a connection pool gets exhausted by a slow
    third party.
    """
    from .models import IntranetMarketingRequest, IntranetMarketingSetting
    from .services import marketing_delivery
    from .services.users import primary_host

    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        due = (await s.execute(
            select(IntranetMarketingRequest.id)
            .where(IntranetMarketingRequest.delivery_next_attempt_at.is_not(None),
                   IntranetMarketingRequest.delivery_next_attempt_at <= now,
                   IntranetMarketingRequest.delivered_at.is_(None))
            .order_by(IntranetMarketingRequest.delivery_next_attempt_at)
            .limit(200))).scalars().all()

    sent = failed = 0
    for request_id in due:
        try:
            async with SessionLocal() as s:
                row = await s.get(IntranetMarketingRequest, request_id)
                # Re-checked rather than trusted: the row was selected in an earlier transaction
                # and another pass, or a console action, may have settled it since.
                if row is None or row.delivered_at is not None \
                        or row.delivery_next_attempt_at is None:
                    continue
                cfg = await s.get(IntranetMarketingSetting, row.tenant_id)
                try:
                    host = await primary_host(s, row.tenant_id)
                    # The console list, with the request named so it can be highlighted. A
                    # per-id detail route does not exist -- linking to one would 404 whoever
                    # clicked it, which is worse than landing them on the queue.
                    link = f"https://{host}/console/marketing?request={row.id}" if host else None
                except Exception:  # noqa: BLE001 - a missing domain row is not a delivery failure
                    link = None
                outcome = await marketing_delivery.send_one(s, row, cfg, link)
                marketing_delivery.record(row, outcome)
                await s.commit()
                sent += 1 if outcome.delivered else 0
                failed += 0 if outcome.delivered else 1
        except Exception as e:  # noqa: BLE001
            print(f"[marketing_delivery_tick] request {request_id}: {type(e).__name__}: {e}",
                  flush=True)
    if sent or failed:
        print(f"[marketing_delivery] sent={sent} failed={failed}", flush=True)


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
    # After the syncs have had the night to land: attribution reads registrations the GHL sync
    # wrote, so running it earlier would freeze cohort days against yesterday's data.
    sched.add_job(ads_funnel_tick, "cron", hour=5, minute=45, timezone=_tz)
    # Every minute, because this is the path a person is waiting on: an agent files a request and
    # somebody in marketing should see it while they still remember filing it. It is also cheap --
    # one indexed query that usually returns nothing.
    sched.add_job(marketing_delivery_tick, "interval", minutes=1,
                  next_run_time=dt.datetime.now())
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
    # The same guard the API runs. Without this a worker deployed WITHOUT its secrets starts
    # quietly and syncs customer data, while the identically-configured API next to it refuses to
    # boot — two processes, one config, opposite answers. It also decrypts integration
    # credentials, so a wrong FERNET_KEY here is not a smaller problem than it is over there.
    enforce_config()
    build_scheduler().start()
    print(f"[worker] started · interval={settings.SYNC_INTERVAL_MINUTES}m"
          + (" · ai=on" if settings.AI_EMPLOYEES_ENABLED else ""))
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
