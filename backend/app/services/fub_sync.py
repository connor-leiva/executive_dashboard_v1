"""The Follow Up Boss sync: who the agents are, which people changed, and what is due.

WHAT IT HAS TO ANSWER. "Who needs a follow-up from this agent today" -- new leads nobody has
contacted, open tasks due today or overdue -- and, for the dashboard's funnel, which leads came in
when. The first two must be minutes-fresh; the funnel's history only has to be complete.

SO IT IS SPLIT BY FRESHNESS rather than read end to end. The old sync read every person on every
run (eight minutes each, every half hour) and still could not answer the first question, because it
kept four fields and no tasks. Now:

  * QUICK (every few minutes, and inside every full run):
      - the newest people, newest first, back to the start of the New Lead window -- a lead that
        arrived a minute ago, whether or not FUB has logged activity on them yet;
      - people with activity since the watermark (`lastActivityAfter`): contacts, calls, changes;
      - every uncontacted new lead we hold, re-read by id, so a lead the CRM reassigns to somebody
        else moves to them on the next pass;
      - open tasks due today, and overdue within the workspace's window, as a snapshot;
      - any person a task points at that we do not hold yet.
  * FULL (the half-hourly tick, and Sync now): the account's identity and users, the quick passes,
    then a bounded slice of the BACKFILL -- the whole CRM, a few pages a run, resumed
    from a saved cursor -- repeated daily so stage changes and reassignments made without any
    activity are caught too.

SIZED FOR THE REAL ACCOUNT. The first live workspace holds about 130,000 people and 135 users;
reading all of them took 252 seconds. So no quick pass reads the CRM whole: every one is bounded by
a window, a watermark or a list of ids.

Features FUB documents are used as documented: the `next` cursor, `fields`, `includeTrash`,
`lastActivityAfter`, `id=1,2,3`, `/identity`, and tasks' `isCompleted`/`due`/`dueStart`. Two are
less certain -- `dueStart`'s format, and `-created` as a descending sort -- so both are checked
against what comes back, fall back rather than fail, and are named in the run's stats, where the
real account settles the question.

STATE lives in `integration.config["fub_state"]`: the account, the watermark, the backfill cursor,
and each run's stats. The connect route keeps it. A key for a DIFFERENT FUB account resets it and
forgets the old account's people, tasks and agents first, because FUB ids from two accounts
would otherwise collide on the same unique key and quietly overwrite each other.

ONE AT A TIME per integration: the quick tick skips a workspace whose full sync is still running.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from dataclasses import dataclass, field

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..config import settings
from ..integrations import fub
from ..models import Agent, CrmTask, Integration, Lead, LoanRecord, Tenant, Transaction
from . import follow_ups
from .upsert import upsert

# Every write re-reads FUB a little behind where the last run stopped: clocks differ, and an
# upsert of a row we already hold costs nothing.
MARGIN = dt.timedelta(minutes=10)
# The whole CRM is re-walked this often, which is how a change with no activity arrives.
REWALK_EVERY = dt.timedelta(hours=24)
# If FUB refuses `dueStart`, overdue tasks are read up to this many pages and filtered here.
OVERDUE_FALLBACK_PAGES = 50
# People a task points at that we do not hold, fetched 100 at a time, at most this many batches.
MISSING_PEOPLE_BATCHES = 20
# The newest-first walk stops at the New Lead window; this caps it on a day of heavy intake.
NEWEST_PAGES = 20

_LOCKS: dict = {}


def lock_for(integration_id) -> asyncio.Lock:
    return _LOCKS.setdefault(integration_id, asyncio.Lock())


def fmt(ts: dt.datetime) -> str:
    """FUB's documented query shape for a time ("2016-11-23 01:02:03"), in UTC."""
    return ts.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _iso(ts: dt.datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def state_of(integ: Integration) -> dict:
    return dict((integ.config or {}).get("fub_state") or {})


def _save(integ: Integration, state: dict) -> None:
    integ.config = {**(integ.config or {}), "fub_state": state}
    flag_modified(integ, "config")


@dataclass
class _Run:
    s: AsyncSession
    c: httpx.AsyncClient
    integ: Integration
    tenant_id: uuid.UUID
    business_id: uuid.UUID
    tz: dt.tzinfo
    now: dt.datetime
    rules: dict
    state: dict
    agents: dict = field(default_factory=dict)       # FUB user id -> agent row id
    users_read: bool = False
    stats: dict = field(default_factory=dict)

    def bump(self, key: str, n: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + n


async def _run_for(s: AsyncSession, c: httpx.AsyncClient, tenant_id, integ: Integration) -> _Run:
    from .sync import _workspace_tz          # one definition of the workspace's zone
    tenant = await s.get(Tenant, tenant_id)
    agents = dict((await s.execute(select(Agent.external_id, Agent.id).where(
        Agent.tenant_id == tenant_id, Agent.source == "fub"))).all())
    return _Run(s=s, c=c, integ=integ, tenant_id=tenant_id, business_id=integ.business_id,
                tz=await _workspace_tz(s, tenant_id), now=dt.datetime.now(dt.timezone.utc),
                rules=follow_ups.settings_for(tenant), state=state_of(integ), agents=agents)


# ── identity and users ───────────────────────────────────────────────────────────────────

async def _identity(run: _Run) -> None:
    """Which account this key opens. A different one than last time resets everything held."""
    ident = await fub.get(run.c, "/identity")
    account = ident.get("account") or {}
    known = run.state.get("account_id")
    if known is not None and account.get("id") is not None and str(known) != str(account["id"]):
        await forget_account(run.s, run.tenant_id)
        run.state = {}
        run.agents = {}
        run.stats["account_changed"] = True
    run.state["account_id"] = account.get("id")
    run.state["account_domain"] = (account.get("domain") or "").strip() or None
    run.state["key_user_id"] = (ident.get("user") or {}).get("id")
    # Saved now, not at the end: a run that fails after forgetting the old account must not
    # leave the old id behind, or the next run would forget the new account's rows as well.
    _save(run.integ, run.state)
    await run.s.commit()


async def forget_account(s: AsyncSession, tenant_id) -> None:
    """Delete what one FUB account left behind, before another account's ids arrive."""
    fub_agents = select(Agent.id).where(Agent.tenant_id == tenant_id, Agent.source == "fub")
    await s.execute(delete(CrmTask).where(CrmTask.tenant_id == tenant_id, CrmTask.source == "fub"))
    await s.execute(delete(Lead).where(Lead.tenant_id == tenant_id, Lead.source == "fub"))
    # Nothing else is meant to point at an FUB agent, but a dangling reference would stop the
    # delete below, so any that do are let go rather than left to fail it.
    await s.execute(update(Transaction).where(Transaction.tenant_id == tenant_id,
                                              Transaction.agent_id.in_(fub_agents))
                    .values(agent_id=None))
    await s.execute(update(LoanRecord).where(LoanRecord.tenant_id == tenant_id,
                                             LoanRecord.referring_agent_id.in_(fub_agents))
                    .values(referring_agent_id=None))
    await s.execute(delete(Agent).where(Agent.tenant_id == tenant_id, Agent.source == "fub"))
    await s.commit()


async def _users(run: _Run) -> None:
    rows = []
    async for page, _ in fub.pages(run.c, "/users", "users"):
        rows.extend(page)
    await upsert(run.s, Agent, [
        {"id": uuid.uuid4(), "tenant_id": run.tenant_id, "business_id": run.business_id,
         "source": "fub", "external_id": u["external_id"], "name": u["name"],
         "email": u["email"], "is_active": u["is_active"]}
        for u in map(fub.map_user, rows)],
        keys=["tenant_id", "source", "external_id"], update=["name", "email", "is_active"])
    await run.s.commit()
    run.agents = dict((await run.s.execute(select(Agent.external_id, Agent.id).where(
        Agent.tenant_id == run.tenant_id, Agent.source == "fub"))).all())
    run.users_read = True
    run.stats["users"] = len(rows)


async def _know_users(run: _Run, ids) -> None:
    """Re-read the users once per run if a person or task names one we have never seen: a new
    hire's first leads should not sit unassigned until the next full sync."""
    if not run.users_read and any(i and i not in run.agents for i in ids):
        await _users(run)


# ── people ───────────────────────────────────────────────────────────────────────────────

async def _write_people(run: _Run, rows: list[dict]) -> None:
    mapped = [fub.map_person(p, run.tz) for p in rows]
    await _know_users(run, [p["agent_external_id"] for p in mapped])
    await upsert(run.s, Lead, [
        {"id": uuid.uuid4(), "tenant_id": run.tenant_id, "business_id": run.business_id,
         "source": "fub", "external_id": p["external_id"], "stage": p["stage"],
         "agent_id": run.agents.get(p["agent_external_id"]),
         "created_at_src": p["created_at_src"], "name": p["name"], "origin": p["origin"],
         "contacted": p["contacted"], "src_created_at": p["src_created_at"],
         "src_updated_at": p["src_updated_at"], "last_activity_at": p["last_activity_at"],
         "synced_at": run.now}
        for p in mapped],
        keys=["tenant_id", "source", "external_id"],
        update=["stage", "agent_id", "created_at_src", "name", "origin", "contacted",
                "src_created_at", "src_updated_at", "last_activity_at", "synced_at"])
    await run.s.commit()


async def _walk_people(run: _Run, params: dict, stat: str, *, start=None, max_pages=None,
                       on_page=None) -> None:
    query = {"fields": fub.PERSON_FIELDS, **params}
    async for rows, token in fub.pages(run.c, "/people", "people", query,
                                       start=start, max_pages=max_pages):
        await _write_people(run, rows)
        run.bump(stat, len(rows))
        if on_page is not None:
            on_page(token)


async def _changed_people(run: _Run) -> None:
    """Everyone with activity since the last run. On the very first run there is no watermark,
    and the recent days are enough to make the follow-up list right while the backfill works
    through the history."""
    mark = fub.parse_ts(run.state.get("activity_watermark"))
    since = (mark or run.now - dt.timedelta(days=run.rules["new_lead_days"] + 1)) - MARGIN
    await _walk_people(run, {"lastActivityAfter": fmt(since), "includeTrash": "true"},
                       "people_changed")


async def _newest_people(run: _Run) -> None:
    """The people who arrived inside the New Lead window, newest first, whether or not FUB has
    logged any activity on them yet: a brand-new lead is the one that must not be missed.

    Asked for as `sort=-created` and walked until a page reaches older than the window. The order
    is CHECKED rather than trusted -- if FUB answers in any other order, one page is read and the
    run's stats say so, because an unsorted walk has no place to stop on an account this size.
    """
    since = run.now - dt.timedelta(days=run.rules["new_lead_days"] + 1)
    params = {"fields": fub.PERSON_FIELDS, "includeTrash": "true", "sort": "-created"}
    last = None
    try:
        walk = fub.pages(run.c, "/people", "people", params, max_pages=NEWEST_PAGES)
        async for rows, _ in walk:
            await _write_people(run, rows)
            run.bump("people_newest", len(rows))
            stamps = [fub.parse_ts(p.get("created")) for p in rows]
            stamps = [t for t in stamps if t is not None]
            ordered = all(a >= b for a, b in zip(stamps, stamps[1:])) and (
                last is None or not stamps or stamps[0] <= last)
            if not ordered:
                run.stats["newest_order"] = "unsorted"
                break
            if stamps:
                last = stamps[-1]
            if not stamps or stamps[-1] < since:
                break
    except httpx.HTTPStatusError as e:
        if e.response.status_code not in (400, 422):
            raise
        run.stats["newest_order"] = "refused"


async def _uncontacted(run: _Run) -> None:
    """Every new lead we hold that nobody has contacted, re-read by id: its stage and owner are
    what the New Lead rule shows, and a reassignment is not activity, so the watermark alone would
    miss a lead the CRM hands to somebody else. By id, because on an account of 130,000 people the
    "uncontacted with recent activity" filter returns old leads browsing the website too."""
    since = run.now - dt.timedelta(days=run.rules["new_lead_days"] + 1)
    ids = sorted((await run.s.execute(select(Lead.external_id).where(
        Lead.tenant_id == run.tenant_id, Lead.source == "fub", Lead.contacted.is_(False),
        Lead.src_created_at >= since))).scalars())
    for i in range(0, min(len(ids), 100 * MISSING_PEOPLE_BATCHES), 100):
        await _walk_people(run, {"id": ",".join(ids[i:i + 100]), "includeTrash": "true"},
                           "people_uncontacted")


async def _missing_people(run: _Run) -> None:
    """People an open task points at that we do not hold yet -- an old lead with a new task,
    before the backfill reaches them."""
    wanted = set((await run.s.execute(select(CrmTask.person_external_id).where(
        CrmTask.tenant_id == run.tenant_id, CrmTask.person_external_id.is_not(None)))).scalars())
    if not wanted:
        return
    have = set((await run.s.execute(select(Lead.external_id).where(
        Lead.tenant_id == run.tenant_id, Lead.source == "fub",
        Lead.external_id.in_(wanted)))).scalars())
    missing = sorted(wanted - have)
    for i in range(0, min(len(missing), 100 * MISSING_PEOPLE_BATCHES), 100):
        batch = missing[i:i + 100]
        await _walk_people(run, {"id": ",".join(batch), "includeTrash": "true"},
                           "people_for_tasks")


async def _backfill(run: _Run) -> None:
    """A slice of the whole CRM, resumed where the last run stopped."""
    bf = dict(run.state.get("backfill") or {})
    done = fub.parse_ts(bf.get("completed_at"))
    if done is not None and run.now - done < REWALK_EVERY:
        run.stats["backfill"] = "current"
        return
    if done is not None:
        bf = {}                                  # a day old: walk it again from the start
    bf.setdefault("started_at", _iso(run.now))
    bf.setdefault("pages", 0)

    def keep(token):
        bf["next"] = token
        bf["pages"] += 1
        run.state["backfill"] = bf
        _save(run.integ, run.state)

    params = {"includeTrash": "true"}
    try:
        await _walk_people(run, params, "people_backfill", start=bf.get("next"),
                           max_pages=settings.FUB_BACKFILL_PAGES_PER_RUN, on_page=keep)
    except httpx.HTTPStatusError as e:
        if not bf.get("next") or e.response.status_code not in (400, 404, 410, 422):
            raise
        # A saved cursor FUB no longer honours. Start over rather than stick on it forever.
        bf.clear()
        bf.update({"started_at": _iso(run.now), "pages": 0})
        await _walk_people(run, params, "people_backfill",
                           max_pages=settings.FUB_BACKFILL_PAGES_PER_RUN, on_page=keep)
    if not bf.get("next"):
        bf["next"] = None
        bf["completed_at"] = _iso(run.now)
    run.state["backfill"] = bf
    run.stats["backfill"] = "complete" if bf.get("completed_at") else f"page {bf['pages']}"


# ── tasks ────────────────────────────────────────────────────────────────────────────────

def _day_start(day: dt.date, tz: dt.tzinfo) -> dt.datetime:
    return dt.datetime.combine(day, dt.time.min, tzinfo=tz)


async def _tasks(run: _Run) -> None:
    """Open tasks due today, and overdue ones inside the workspace's window, as a snapshot:
    a task completed in FUB is gone from here on the next pass."""
    fetched: list[dict] = []
    async for rows, _ in fub.pages(run.c, "/tasks", "tasks", {"isCompleted": "false", "due": "today"}):
        fetched.extend(rows)
    n_today = len(fetched)

    days = run.rules["overdue_max_days"]
    today = run.now.astimezone(run.tz).date()
    window = today - dt.timedelta(days=days) if days else None
    overdue = {"isCompleted": "false", "due": "overdue"}
    complete = True
    try:
        params = {**overdue, "dueStart": fmt(_day_start(window, run.tz))} if window else overdue
        async for rows, _ in fub.pages(run.c, "/tasks", "tasks", params):
            fetched.extend(rows)
        run.stats["tasks_window"] = "dueStart" if window else "all"
    except httpx.HTTPStatusError as e:
        if window is None or e.response.status_code not in (400, 422):
            raise
        token = None
        async for rows, token in fub.pages(run.c, "/tasks", "tasks", overdue,
                                           max_pages=OVERDUE_FALLBACK_PAGES):
            fetched.extend(rows)
        complete = token is None
        run.stats["tasks_window"] = "fallback" if complete else "fallback-capped"

    mapped = [fub.map_task(t, run.tz) for t in fetched]
    if window is not None:
        mapped = [t for t in mapped if t["due_on"] is None or t["due_on"] >= window]
    await _know_users(run, [t["agent_external_id"] for t in mapped])
    await upsert(run.s, CrmTask, [
        {"id": uuid.uuid4(), "tenant_id": run.tenant_id, "source": "fub",
         "external_id": t["external_id"], "person_external_id": t["person_external_id"],
         "agent_id": run.agents.get(t["agent_external_id"]), "name": t["name"],
         "task_type": t["task_type"], "due_on": t["due_on"], "due_at": t["due_at"],
         "synced_at": run.now}
        for t in mapped],
        keys=["tenant_id", "source", "external_id"],
        update=["person_external_id", "agent_id", "name", "task_type", "due_on", "due_at",
                "synced_at"])
    # Mark and sweep rather than NOT IN: a big team's open tasks would overflow the bound
    # parameters. A capped read is not the whole picture, so nothing is swept from one.
    if complete:
        await run.s.execute(delete(CrmTask).where(
            CrmTask.tenant_id == run.tenant_id, CrmTask.source == "fub",
            (CrmTask.synced_at < run.now) | CrmTask.synced_at.is_(None)))
    await run.s.commit()
    run.stats["tasks_today"] = n_today
    run.stats["tasks_overdue"] = len(mapped) - n_today if len(mapped) >= n_today else 0

    # How many overdue tasks the window leaves out, across the account: one request, shown to
    # admins beside the setting that controls it.
    if window is not None and run.stats.get("tasks_window") == "dueStart":
        data = await fub.get(run.c, "/tasks", {**overdue, "limit": 1})
        total = (data.get("_metadata") or {}).get("total")
        if isinstance(total, int):
            run.state["older_overdue"] = max(0, total - run.stats["tasks_overdue"])


# ── the two entry points ─────────────────────────────────────────────────────────────────

async def _quick_passes(run: _Run) -> None:
    await _newest_people(run)
    await _changed_people(run)
    await _uncontacted(run)
    await _tasks(run)
    await _missing_people(run)
    run.state["activity_watermark"] = _iso(run.now)


async def full_sync(s: AsyncSession, tenant_id, integ: Integration, creds: fub.FubCreds) -> int:
    """Everything, under the integration's lock. Returns the number of records written."""
    async with lock_for(integ.id):
        async with fub.client(creds) as c:
            run = await _run_for(s, c, tenant_id, integ)
            await _identity(run)
            await _users(run)
            await _quick_passes(run)
            await _backfill(run)
        run.state["quick"] = {"at": _iso(run.now), "ok": True}
        run.state["last_run"] = {"at": _iso(run.now), **run.stats}
        _save(integ, run.state)
        await s.commit()
    return sum(v for k, v in run.stats.items()
               if isinstance(v, int) and not isinstance(v, bool)
               and (k.startswith("people_") or k in ("users", "tasks_today", "tasks_overdue")))


async def quick_refresh(s: AsyncSession, tenant_id, integ: Integration,
                        creds: fub.FubCreds) -> dict | None:
    """The follow-up passes only. None when a full sync already holds the lock."""
    lock = lock_for(integ.id)
    if lock.locked():
        return None
    async with lock:
        async with fub.client(creds) as c:
            run = await _run_for(s, c, tenant_id, integ)
            try:
                await _quick_passes(run)
            except Exception as e:  # noqa: BLE001 -- recorded for the portal, raised for the log
                # The session may hold a failed statement; start clean before recording why.
                await s.rollback()
                await s.refresh(integ)
                state = state_of(integ)
                state["quick"] = {"at": _iso(run.now), "ok": False, "error": str(e)[:300]}
                _save(integ, state)
                await s.commit()
                raise
        run.state["quick"] = {"at": _iso(run.now), "ok": True, **run.stats}
        _save(integ, run.state)
        await s.commit()
        return run.stats
