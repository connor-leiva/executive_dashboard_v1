import asyncio
import datetime as dt
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from .db import SessionLocal
from .config import settings
from .startup_checks import enforce_config
from .models import Tenant
from .services.jobs import heartbeat
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
        tenant_ids = await syncable_tenant_ids(s)
    for tid in tenant_ids:
        try:
            async with SessionLocal() as s2:
                await run_all(s2, tid, start, end)
        except Exception as e:  # noqa: BLE001 — one tenant's failure must not stop the rest
            print(f"[tick] tenant {tid}: {type(e).__name__}: {e}", flush=True)


async def syncable_tenant_ids(s) -> list:
    """Every workspace the scheduled jobs should pull for, or send to a provider for: the sync tick,
    the daily Sisu roster and the ads funnel.

    Not a suspended one: the operator console tells an operator that suspending "stops all
    scheduled syncs", and until this existed the tick iterated every tenant regardless, so a
    suspended workspace kept calling its customers' QuickBooks and CRM every thirty minutes. Not
    one whose syncs an operator froze either (config.syncs_frozen): freezing is how a compromised
    credential stops being used while its people stay signed in.
    """
    rows = (await s.execute(select(Tenant.id, Tenant.status, Tenant.config))).all()
    return [tid for tid, status, cfg in rows
            if status != "suspended" and not (cfg or {}).get("syncs_frozen")]


async def recruiting_activity_tick():
    """Every few minutes: read back what happened in GoHighLevel (§5.6).

    Private Integration Tokens have no webhooks, so this polls. It is what makes the SDR card's
    dials and conversations real, what fills speed-to-lead, and what lets a rule clear itself
    when a recruit replies IN GHL -- none of which needed new logic, only rows.

    NEVER BESIDE A RUNNING SYNC and never before the first one: the service checks that the
    connection is configured and has synced, because without candidates there is nothing to
    attach a message to and every row fetched would be paid for and discarded.
    """
    from .models import Integration
    from .services.recruiting_poll import poll_activity
    async with SessionLocal() as s:
        tenant_ids = set(await syncable_tenant_ids(s))
        rows = (await s.execute(select(Integration.tenant_id).where(
            Integration.provider == "ghl_recruiting",
            Integration.status == "connected"))).all()
    for (tid,) in rows:
        if tid not in tenant_ids:
            continue
        try:
            async with SessionLocal() as s2:
                out = await poll_activity(s2, tid)
            # Anything the poll could not classify is printed rather than swallowed. V3 (the
            # message `type` values) is unsettled, and this is how it gets answered from real
            # traffic instead of from a guess.
            if out.get("unknown_types"):
                print(f"[recruiting_activity] {tid}: unclassified {out['unknown_types']}", flush=True)
        except Exception as e:  # noqa: BLE001 - one workspace must not stop the rest
            print(f"[recruiting_activity] {tid}: {e}", flush=True)


async def recruiting_outbox_tick():
    """Every minute: retry what is queued, fail what is stuck, purge bodies past retention.

    Runs even when the platform flag is off, on purpose. With sending disabled there is nothing
    queued to retry, and the purge still has a year of `dry_run` request bodies to age out.
    """
    from .models import Integration
    from .services.recruiting_actions import drain
    async with SessionLocal() as s:
        tenant_ids = set(await syncable_tenant_ids(s))
        rows = (await s.execute(select(Integration.tenant_id).where(
            Integration.provider == "ghl_recruiting"))).all()
    for (tid,) in rows:
        if tid not in tenant_ids:
            continue
        try:
            async with SessionLocal() as s2:
                await drain(s2, tid)
        except Exception as e:  # noqa: BLE001 - one workspace's outbox must not stop the rest
            print(f"[recruiting_outbox] {tid}: {e}", flush=True)


async def recruiting_queue_tick():
    """Every few minutes: re-evaluate the recruiting rules and reconcile the queue.

    Cheap by construction -- pure functions over rows this workspace already has, no GHL call --
    which is what makes a five-minute cadence reasonable. It RECONCILES rather than rebuilds:
    items the rules still claim are left alone (including ones already marked Done), and items
    they have stopped claiming become auto_cleared. That last part is how a text sent from inside
    GHL clears somebody's Axcion list without them touching Axcion.

    Only for workspaces the scheduler should act on at all: a suspended or frozen one is skipped,
    like every other tick.
    """
    from .models import Integration
    from .services.recruiting_accountability import roll_forward
    from .services.recruiting_rules import build_queue, business_tz
    async with SessionLocal() as s:
        tenant_ids = set(await syncable_tenant_ids(s))
        rows = (await s.execute(select(Integration.tenant_id).where(
            Integration.provider == "ghl_recruiting",
            Integration.status == "connected"))).all()
    for (tid,) in rows:
        if tid not in tenant_ids:
            continue
        try:
            async with SessionLocal() as s2:
                # Monday rollover rides this tick rather than owning one. It only FILLS GAPS, so
                # running it every five minutes is the same as running it once -- and a separate
                # weekly job would be a thing that can fail quietly for a week before anybody
                # notices the commitments are blank.
                await roll_forward(s2, tid, dt.datetime.now(business_tz()).date())
                await build_queue(s2, tid)
        except Exception as e:  # noqa: BLE001 - one workspace's rules must not stop the rest
            print(f"[recruiting_queue] {tid}: {e}", flush=True)


async def fub_followups_tick():
    """Every few minutes: the follow-up passes of the Follow Up Boss sync, for Needs You Today.

    The full sync runs on the half-hourly tick and owns the integration's status and history. This
    only re-reads what an agent is waiting on -- new leads, contacts, open tasks -- so a lead that
    arrives at 9:05 is on their home page before 9:30. Only for a workspace whose full sync has
    succeeded once (it knows its account), and never beside one still running: see
    services/fub_sync.quick_refresh.
    """
    from .models import Integration
    from .services import fub_sync
    from .services.sync import _fub_creds
    async with SessionLocal() as s:
        tenant_ids = set(await syncable_tenant_ids(s))
        rows = (await s.execute(select(Integration.id, Integration.tenant_id).where(
            Integration.provider == "fub", Integration.status == "connected"))).all()
    for integ_id, tid in rows:
        if tid not in tenant_ids:
            continue
        try:
            async with SessionLocal() as s2:
                integ = await s2.get(Integration, integ_id)
                if integ is None or not fub_sync.state_of(integ).get("account_id"):
                    continue
                await fub_sync.quick_refresh(s2, tid, integ, _fub_creds(integ))
        except Exception as e:  # noqa: BLE001 -- one workspace's failure must not stop the rest
            print(f"[fub_followups] tenant {tid}: {type(e).__name__}: {e}", flush=True)


async def roster_tick():
    """Daily: refresh Sisu agent group memberships (agent.sisu_group_ids) — the live source for
    per-team scorecard attribution. Runs before scorecard_tick; one tenant's failure is isolated.
    A no-op when Sisu isn't configured (fetches just fail and leave the roster as-is)."""
    from .services.sync import sync_agent_offices
    async with SessionLocal() as s:
        tenant_ids = await syncable_tenant_ids(s)            # a Sisu pull, so not while paused
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
        tenant_ids = await syncable_tenant_ids(s)            # sends conversions, so not while paused
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


async def platform_audit_prune():
    """Monthly: delete operator audit entries past retention (OPERATOR-CONSOLE-SPEC §4.2). The
    console tells an operator the trail is kept 400 days; this is what makes that true."""
    from .services.operator_audit import prune
    async with SessionLocal() as s:
        gone = await prune(s)
    if gone:
        print(f"[platform_audit_prune] deleted {gone} entries past retention", flush=True)


async def platform_billing_reconcile():
    """Hourly: correct the Stripe mirror from Stripe (OPERATOR-CONSOLE-SPEC §6.4). Webhooks get
    missed; this is what makes a missed one temporary. A no-op until platform billing is connected
    and switched on. Every field it has to correct is logged, because a divergence means a webhook
    did not arrive."""
    from .models import PlatformSubscription
    from .services import platform_billing
    async with SessionLocal() as s:
        try:
            key = await platform_billing.active_key(s)
        except platform_billing.BillingUnavailable:
            return
        tenant_ids = (await s.execute(select(PlatformSubscription.tenant_id))).scalars().all()
    for tid in tenant_ids:
        try:
            async with SessionLocal() as s2:
                tenant = await s2.get(Tenant, tid)
                changed = await platform_billing.sync_tenant(s2, tenant, key)
            if changed:
                print(f"[billing_reconcile] {tenant.slug}: corrected {', '.join(changed)}", flush=True)
        except Exception as e:  # noqa: BLE001 — one workspace's failure must not stop the rest
            print(f"[billing_reconcile] tenant {tid}: {type(e).__name__}: {e}", flush=True)


async def expire_support_access():
    """Every five minutes: disable support accounts past their expires_at (C8). The session gate
    already refuses them at that minute; this makes it permanent, bumps token_version so no token
    can outlive a later reopening, and writes the expiry to both audit trails."""
    from .models import PlatformAudit, User
    from .services.audit import audit
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        due = (await s.execute(select(User, Tenant.slug).join(Tenant, Tenant.id == User.tenant_id).where(
            User.expires_at.is_not(None), User.expires_at <= now, User.status == "active"))).all()
        for u, slug in due:
            u.status = "disabled"
            u.token_version = (u.token_version or 0) + 1
            audit(s, u.tenant_id, None, "support.access_expired", "user", u.id,
                  {"by": "Axcion (expiry)", "account": u.email}, category="Access", actor_label="Axcion")
            s.add(PlatformAudit(operator_id=None, operator_email=None, action="support.access_expired",
                                tenant_id=u.tenant_id, tenant_slug=slug, target_type="user", target_id=str(u.id),
                                detail={"account": u.email, "source": "expiry job"}))
        await s.commit()
    if due:
        print(f"[expire_support_access] disabled {len(due)} support account(s)", flush=True)


def build_scheduler() -> AsyncIOScheduler:
    """Configure the scheduler with the sync tick, the daily agent-roster + scorecard-resolver ticks,
    and (when the flag is on) the two AI jobs. Shared by the standalone worker (`python -m app.worker`)
    and the in-API scheduler (RUN_WORKER_IN_API) so both run exactly the same jobs."""
    sched = AsyncIOScheduler()
    # Every job is registered through heartbeat(), which records when it last started, finished
    # and failed for the operator console's System view. services/jobs.catalog() lists the same
    # jobs; a test holds the two together.
    beat = heartbeat
    sched.add_job(beat(tick), "interval", minutes=settings.SYNC_INTERVAL_MINUTES,
                  next_run_time=dt.datetime.now())
    # Needs You Today between full syncs. No next_run_time: the tick above starts the first full
    # sync at once, and this has nothing to do until that one has read the account.
    sched.add_job(beat(fub_followups_tick), "interval",
                  minutes=settings.FUB_FOLLOWUPS_INTERVAL_MINUTES)
    _tz = ZoneInfo(settings.BILLING_TIMEZONE)
    # The recruiting queue, twice over. The interval keeps it current through the day; the 06:00
    # cron is the MORNING BUILD the tab promises ("Tomorrow's list builds at 6:00 am"), and it is
    # business-local because that sentence is about somebody's morning, not about UTC.
    sched.add_job(beat(recruiting_queue_tick), "interval",
                  minutes=settings.RECRUITING_QUEUE_INTERVAL_MINUTES)
    sched.add_job(beat(recruiting_queue_tick), "cron", hour=6, minute=0, timezone=_tz)
    sched.add_job(beat(recruiting_outbox_tick), "interval",
                  minutes=settings.RECRUITING_OUTBOX_INTERVAL_MINUTES)
    # Offset from the queue tick rather than sharing its cadence: the poll WRITES the activity
    # rows the rules then read, so running them on the same beat would have the rules evaluating
    # a reply that landed a moment after they looked.
    sched.add_job(beat(recruiting_activity_tick), "interval",
                  minutes=settings.RECRUITING_ACTIVITY_INTERVAL_MINUTES)
    sched.add_job(beat(roster_tick), "cron", hour=4, minute=45, timezone=_tz)   # refresh agent→office first
    sched.add_job(beat(scorecard_tick), "cron", hour=5, minute=15, timezone=_tz)  # then resolve, business-local
    # After the syncs have had the night to land: attribution reads registrations the GHL sync
    # wrote, so running it earlier would freeze cohort days against yesterday's data.
    sched.add_job(beat(ads_funnel_tick), "cron", hour=5, minute=45, timezone=_tz)
    # Every minute, because this is the path a person is waiting on: an agent files a request and
    # somebody in marketing should see it while they still remember filing it. It is also cheap --
    # one indexed query that usually returns nothing.
    sched.add_job(beat(marketing_delivery_tick), "interval", minutes=1,
                  next_run_time=dt.datetime.now())
    sched.add_job(beat(platform_audit_prune), "cron", day=1, hour=3, minute=30, timezone=_tz)
    # Hourly and always registered: it returns at once until platform billing is switched on, which
    # is a setting in the operator console rather than a deploy.
    sched.add_job(beat(platform_billing_reconcile), "interval", hours=1)
    sched.add_job(beat(expire_support_access), "interval", minutes=5, next_run_time=dt.datetime.now())
    if settings.RECALL_API_KEY:
        sched.add_job(beat(recall_tick), "interval", minutes=settings.RECALL_TICK_MINUTES,
                      next_run_time=dt.datetime.now())
        # Transcripts land minutes after a call ends, so this need not be as eager as the
        # bot scheduler - and it carries the retention purge with it.
        sched.add_job(beat(transcript_tick), "interval", minutes=15, next_run_time=dt.datetime.now())
    if settings.AI_EMPLOYEES_ENABLED:
        sched.add_job(beat(ai_dispatch), "interval", minutes=1, next_run_time=dt.datetime.now())
        sched.add_job(beat(ai_execute), "interval", seconds=15, next_run_time=dt.datetime.now())
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
