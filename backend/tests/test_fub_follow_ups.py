"""Follow-ups from Follow Up Boss: the sync that feeds them, the rules, and who sees what.

Needs You Today was three rows compiled into the portal. These hold the replacement to its spec
(FUB-FOLLOW-UPS-SPEC.md): the sync passes against a fake FUB, each rule at its edges, one row per
person, the order, the team view and its permission, the six different empties, and the console
screens that make a member's CRM identity and the rules configurable without a deploy.
"""
import datetime as dt
import uuid
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import (Agent, Business, CrmTask, Domain, Integration, IntranetIntegration,
                        IntranetMember, IntranetPermission, IntranetCapability, IntranetRole,
                        IntranetWorkspace, IntranetWtdList, Lead, SyncRun, Tenant, User)
from app.security import enc, hash_pw, make_token
from app.services import follow_ups, fub_sync
from app.services.intranet_bootstrap import bootstrap_intranet
from app.services.sync import _fub_creds, _sync_integration
from tests.fub_fake import fake, person, task, user  # noqa: F401 -- `fake` is a fixture

ASGI = httpx.ASGITransport(app=app)
DENVER = ZoneInfo("America/Denver")
NOW = dt.datetime(2026, 9, 18, 18, 0, tzinfo=dt.timezone.utc)      # noon in Denver
TODAY = NOW.astimezone(DENVER).date()


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


def _client():
    return httpx.AsyncClient(transport=ASGI, base_url="http://testserver")


def _H(token, host):
    return {"Authorization": f"Bearer {token}", "x-tenant-host": host}


# ── a workspace, its people, and FUB rows written directly ────────────────────────────────

async def _workspace(slug: str, *, fub: str | None = "synced") -> dict:
    """A bootstrapped portal (owner/manager/member roles with the standard matrix), one
    business, and -- unless `fub` is None -- a Follow Up Boss connection: "synced", "never"
    (connected, no sync finished) or "error" (failing, but synced before)."""
    async with SessionLocal() as s:
        t = Tenant(slug=slug, name=slug.title(), status="active", plan="portfolio",
                   config={"features": {"intranet": True}})
        s.add(t)
        await s.flush()
        s.add(Domain(tenant_id=t.id, hostname=f"{slug}.localhost", is_primary=True))
        b = Business(tenant_id=t.id, key="main", name="Main", tag="Business", sort_order=0)
        s.add(b)
        owner = User(tenant_id=t.id, email=f"owner@{slug}.test", name="Owner",
                     password_hash=hash_pw("pw"), role="owner", status="active", tab_access=[],
                     token_version=0)
        s.add(owner)
        await s.flush()
        await bootstrap_intranet(s, t.id, workspace_name=slug.title(), subdomain=slug,
                                 owner_email=owner.email)
        wsrow = (await s.execute(select(IntranetWorkspace).where(
            IntranetWorkspace.tenant_id == t.id))).scalar_one()
        wsrow.timezone = "America/Denver"
        integ = None
        if fub:
            integ = Integration(
                tenant_id=t.id, provider="fub", business_id=b.id,
                status="error" if fub == "error" else "connected",
                access_token_enc=enc("fka_test"),
                last_synced_at=NOW if fub in ("synced", "error") else None,
                last_error="HTTP 401 from Follow Up Boss" if fub == "error" else None,
                config={"fub_state": {"account_id": 777, "account_domain": "acme"}})
            s.add(integ)
        await s.commit()
        roles = {r.key: r.id for r in (await s.execute(select(IntranetRole).where(
            IntranetRole.tenant_id == t.id))).scalars()}
        return {"tenant_id": t.id, "business_id": b.id, "host": f"{slug}.localhost",
                "owner_token": make_token(owner.id, t.id, 0), "roles": roles,
                "integration_id": integ.id if integ else None, "slug": slug}


async def _person(ws, email, *, role="member", on_roster=True, user_role="member", links=None):
    async with SessionLocal() as s:
        u = User(tenant_id=ws["tenant_id"], email=email, name=email.split("@")[0].title(),
                 password_hash=hash_pw("pw"), role=user_role, status="active", tab_access=[],
                 token_version=0)
        s.add(u)
        await s.flush()
        member_id = None
        if on_roster:
            m = IntranetMember(tenant_id=ws["tenant_id"], full_name=u.name, email=email,
                               role_id=ws["roles"][role], status="Active", auth_source="Manual",
                               user_id=u.id, agent_links=links)
            s.add(m)
            await s.flush()
            member_id = m.id
        await s.commit()
        return {"token": make_token(u.id, ws["tenant_id"], 0), "member_id": member_id,
                "user_id": u.id}


async def _set_level(ws, capability: str, role: str, level: str):
    async with SessionLocal() as s:
        cap = (await s.execute(select(IntranetCapability).where(
            IntranetCapability.tenant_id == ws["tenant_id"],
            IntranetCapability.key == capability))).scalar_one()
        perm = (await s.execute(select(IntranetPermission).where(
            IntranetPermission.tenant_id == ws["tenant_id"],
            IntranetPermission.capability_id == cap.id,
            IntranetPermission.role_id == ws["roles"][role]))).scalar_one()
        perm.level = level
        await s.commit()


async def _agent(ws, ext, email, name=None):
    async with SessionLocal() as s:
        a = Agent(tenant_id=ws["tenant_id"], business_id=ws["business_id"], source="fub",
                  external_id=str(ext), name=name or f"Agent {ext}", email=email, is_active=True)
        s.add(a)
        await s.commit()
        return a.id


async def _lead(ws, ext, agent_id, *, created: dt.datetime | None = None, contacted=False,
                stage="Lead", last_activity: dt.datetime | None = None, name=None):
    created = created or NOW - dt.timedelta(hours=3)
    async with SessionLocal() as s:
        s.add(Lead(tenant_id=ws["tenant_id"], business_id=ws["business_id"], source="fub",
                   external_id=str(ext), stage=stage, agent_id=agent_id,
                   created_at_src=created.astimezone(DENVER).date(),
                   name=name or f"Person {ext}", origin="Zillow", contacted=contacted,
                   src_created_at=created, src_updated_at=created,
                   last_activity_at=last_activity or created, synced_at=NOW))
        await s.commit()


async def _task(ws, ext, person_ext, agent_id, due_on: dt.date, due_at: dt.datetime | None = None,
                name="Call back"):
    async with SessionLocal() as s:
        s.add(CrmTask(tenant_id=ws["tenant_id"], source="fub", external_id=str(ext),
                      person_external_id=str(person_ext) if person_ext else None,
                      agent_id=agent_id, name=name, task_type="Call", due_on=due_on,
                      due_at=due_at, synced_at=NOW))
        await s.commit()


async def _q(ws, agent_id, **rules):
    async with SessionLocal() as s:
        return await follow_ups.queue(s, ws["tenant_id"], agent_id,
                                      follow_ups.clean_settings(rules), today=TODAY, now=NOW,
                                      domain="acme")


# ═════════════════════════════════════════════════════════════════════════════════════════
# The sync
# ═════════════════════════════════════════════════════════════════════════════════════════

def _live_today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).astimezone(DENVER).date()


def _ago(**kw) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _sync(ws):
    async with SessionLocal() as s:
        integ = await s.get(Integration, ws["integration_id"])
        await _sync_integration(s, ws["tenant_id"], integ, "2026-09-01", "2026-09-30")
    async with SessionLocal() as s:
        return await s.get(Integration, ws["integration_id"])


async def test_the_first_full_sync_brings_identity_people_tasks_and_history(fake):
    ws = await _workspace("fsfirst", fub="never")
    today = _live_today()
    fake.today = today
    fake.users = [user(1), user(2)]
    fake.people = [
        person(10, assigned=1, created=_ago(hours=5)),                               # a new lead
        person(11, assigned=1, created=_ago(days=400), contacted=True, stage="Past Client"),
        person(12, assigned=2, created=_ago(days=300), contacted=True),
    ]
    fake.tasks = [task(100, person_id=12, due=today, assigned=2),
                  task(101, person_id=11, due=today - dt.timedelta(days=3)),
                  task(102, person_id=11, due=today - dt.timedelta(days=90))]
    integ = await _sync(ws)

    assert integ.status == "connected", integ.last_error
    state = integ.config["fub_state"]
    assert state["account_domain"] == "acme" and state["account_id"] == 777
    assert state["backfill"]["completed_at"] and state["activity_watermark"]
    async with SessionLocal() as s:
        leads = {l.external_id: l for l in (await s.execute(select(Lead).where(
            Lead.tenant_id == ws["tenant_id"]))).scalars()}
        tasks = {t.external_id: t for t in (await s.execute(select(CrmTask).where(
            CrmTask.tenant_id == ws["tenant_id"]))).scalars()}
        run = (await s.execute(select(SyncRun).where(SyncRun.tenant_id == ws["tenant_id"],
                                                     SyncRun.provider == "fub"))).scalar_one()
    assert set(leads) == {"10", "11", "12"}
    new = leads["10"]
    assert new.name == "Person 10" and new.contacted is False and new.origin == "Zillow"
    assert new.src_created_at is not None and new.last_activity_at is not None
    assert set(tasks) == {"100", "101"}, "a task older than the 30-day window is not fetched"
    assert tasks["100"].due_on == today
    assert run.status == "ok" and run.stats["fub"]["tasks_window"] == "dueStart"
    assert any("dueStart" in p for p in fake.calls_to("/tasks"))


async def test_the_next_run_reads_what_changed_not_the_whole_crm(fake):
    ws = await _workspace("fsnext", fub="never")
    fake.today = _live_today()
    fake.users = [user(1)]
    fake.people = [person(10, created=_ago(days=200), contacted=True)]
    await _sync(ws)
    before = len(fake.calls)
    await _sync(ws)
    second = [p for path, p in fake.calls[before:] if path == "/people"]
    assert second, "the second run read no people at all"
    assert all(("lastActivityAfter" in p) or ("id" in p) or p.get("sort") == "-created"
               for p in second), "the whole CRM was walked again inside a day"


async def test_a_completed_task_leaves_the_snapshot(fake):
    ws = await _workspace("fsdone", fub="never")
    today = _live_today()
    fake.today = today
    fake.users = [user(1)]
    fake.people = [person(10, created=_ago(days=2))]
    fake.tasks = [task(100, person_id=10, due=today)]
    await _sync(ws)
    fake.tasks[0]["isCompleted"] = True
    await _sync(ws)
    async with SessionLocal() as s:
        n = (await s.execute(select(func.count()).select_from(CrmTask).where(
            CrmTask.tenant_id == ws["tenant_id"]))).scalar_one()
    assert n == 0


async def test_a_refused_due_start_falls_back_and_filters_here(fake):
    ws = await _workspace("fsfallback", fub="never")
    today = _live_today()
    fake.today = today
    fake.refuse_due_start = True
    fake.users = [user(1)]
    fake.people = [person(10, created=_ago(days=2))]
    fake.tasks = [task(100, person_id=10, due=today - dt.timedelta(days=2)),
                  task(101, person_id=10, due=today - dt.timedelta(days=200))]
    integ = await _sync(ws)
    assert integ.status == "connected", integ.last_error
    assert integ.config["fub_state"]["last_run"]["tasks_window"] == "fallback"
    async with SessionLocal() as s:
        ids = set((await s.execute(select(CrmTask.external_id).where(
            CrmTask.tenant_id == ws["tenant_id"]))).scalars())
    assert ids == {"100"}


async def test_a_key_for_another_account_forgets_the_first(fake):
    ws = await _workspace("fsswitch", fub="never")
    fake.today = _live_today()
    fake.users = [user(1)]
    fake.people = [person(10, created=_ago(days=1))]
    await _sync(ws)
    fake.identity = {"account": {"id": 888, "domain": "other"}, "user": {"id": 5}}
    fake.users = [user(5)]
    fake.people = [person(20, assigned=5, created=_ago(days=1))]
    integ = await _sync(ws)
    assert integ.config["fub_state"]["account_id"] == 888
    async with SessionLocal() as s:
        leads = set((await s.execute(select(Lead.external_id).where(
            Lead.tenant_id == ws["tenant_id"]))).scalars())
        agents = set((await s.execute(select(Agent.external_id).where(
            Agent.tenant_id == ws["tenant_id"], Agent.source == "fub"))).scalars())
    assert leads == {"20"} and agents == {"5"}


async def test_the_backfill_resumes_where_it_stopped(fake, monkeypatch):
    monkeypatch.setattr(settings, "FUB_BACKFILL_PAGES_PER_RUN", 1)
    ws = await _workspace("fsresume", fub="never")
    fake.today = _live_today()
    fake.users = [user(1)]
    fake.people = [person(i, created=_ago(days=500), contacted=True) for i in range(1, 251)]
    integ = await _sync(ws)
    assert integ.config["fub_state"]["backfill"]["next"] == "100"
    integ = await _sync(ws)
    assert integ.config["fub_state"]["backfill"]["next"] == "200"
    integ = await _sync(ws)
    assert integ.config["fub_state"]["backfill"].get("completed_at")
    async with SessionLocal() as s:
        n = (await s.execute(select(func.count()).select_from(Lead).where(
            Lead.tenant_id == ws["tenant_id"]))).scalar_one()
    assert n == 250


async def test_a_lead_with_no_activity_yet_arrives_on_the_newest_first_walk(fake):
    """The lead that matters most is the one that arrived a minute ago, and FUB may not have logged
    any activity on it yet -- so the activity watermark cannot be what finds it."""
    ws = await _workspace("fsnewest", fub="never")
    fake.today = _live_today()
    fake.users = [user(1)]
    fake.people = [person(i, created=_ago(days=300 + i), contacted=True) for i in range(1, 5)]
    await _sync(ws)
    brand_new = person(99, created=_ago(minutes=2))
    brand_new["lastActivity"] = None
    fake.people.append(brand_new)
    async with SessionLocal() as s:
        integ = await s.get(Integration, ws["integration_id"])
        stats = await fub_sync.quick_refresh(s, ws["tenant_id"], integ, _fub_creds(integ))
    assert stats.get("people_newest"), stats
    async with SessionLocal() as s:
        lead = (await s.execute(select(Lead).where(Lead.tenant_id == ws["tenant_id"],
                                                   Lead.external_id == "99"))).scalar_one()
    assert lead.contacted is False and lead.src_created_at is not None


async def test_the_newest_walk_stops_at_the_window(fake):
    """On an account of 130,000 people an unbounded walk is an eight-minute one."""
    ws = await _workspace("fsnewstop", fub="never")
    fake.today = _live_today()
    fake.users = [user(1)]
    fake.people = ([person(i, created=_ago(hours=i)) for i in range(1, 4)]
                   + [person(100 + i, created=_ago(days=30 + i), contacted=True)
                      for i in range(250)])
    await _sync(ws)
    newest = [p for p in fake.calls_to("/people") if p.get("sort") == "-created"]
    assert len(newest) == 1, "the first page already reached past the window"


async def test_an_unsorted_or_refused_newest_walk_reads_one_page_and_says_so(fake):
    ws = await _workspace("fsunsorted", fub="never")
    fake.today = _live_today()
    fake.users = [user(1)]
    fake.people = [person(i, created=_ago(days=i)) for i in range(1, 251)]
    fake.ignore_sort_order = True
    integ = await _sync(ws)
    assert integ.status == "connected", integ.last_error
    assert integ.config["fub_state"]["last_run"]["newest_order"] == "unsorted"
    assert len([p for p in fake.calls_to("/people") if p.get("sort") == "-created"]) == 1

    other = await _workspace("fsrefused", fub="never")
    fake.ignore_sort_order = False
    fake.refuse_sort = True
    integ = await _sync(other)
    assert integ.status == "connected", integ.last_error
    assert integ.config["fub_state"]["last_run"]["newest_order"] == "refused"


async def test_the_quick_refresh_skips_while_a_full_sync_holds_the_lock(fake):
    ws = await _workspace("fslock", fub="never")
    async with SessionLocal() as s:
        integ = await s.get(Integration, ws["integration_id"])
        async with fub_sync.lock_for(integ.id):
            result = await fub_sync.quick_refresh(s, ws["tenant_id"], integ, _fub_creds(integ))
    assert result is None and not fake.calls


async def test_the_quick_refresh_reads_new_leads_and_new_hires_without_a_sync_run(fake):
    ws = await _workspace("fsquick", fub="never")
    fake.today = _live_today()
    fake.users = [user(1)]
    fake.people = [person(10, created=_ago(days=3))]
    await _sync(ws)
    fake.users.append(user(3, email="new@acme.test"))
    fake.people.append(person(11, assigned=3, created=_ago(minutes=5)))
    async with SessionLocal() as s:
        runs = (await s.execute(select(func.count()).select_from(SyncRun).where(
            SyncRun.tenant_id == ws["tenant_id"]))).scalar_one()
        integ = await s.get(Integration, ws["integration_id"])
        stats = await fub_sync.quick_refresh(s, ws["tenant_id"], integ, _fub_creds(integ))
    assert stats is not None
    async with SessionLocal() as s:
        lead = (await s.execute(select(Lead).where(Lead.tenant_id == ws["tenant_id"],
                                                   Lead.external_id == "11"))).scalar_one()
        hire = (await s.execute(select(Agent.id).where(Agent.tenant_id == ws["tenant_id"],
                                                       Agent.external_id == "3"))).scalar_one()
        after = (await s.execute(select(func.count()).select_from(SyncRun).where(
            SyncRun.tenant_id == ws["tenant_id"]))).scalar_one()
        integ = await s.get(Integration, ws["integration_id"])
    assert lead.agent_id == hire, "a new hire's first lead sat unassigned"
    assert after == runs, "the quick refresh is not a sync run"
    assert integ.config["fub_state"]["quick"]["ok"] is True


async def test_connecting_checks_the_key_and_says_when_it_only_sees_one_agent(fake):
    ws = await _workspace("fsconnect", fub=None)
    h = _H(ws["owner_token"], ws["host"])
    fake.refuse_key = True
    async with _client() as c:
        r = await c.post("/api/v1/integrations", headers=h,
                         json={"provider": "fub", "business_key": "main", "token": "fka_wrong"})
    assert r.status_code == 400 and "rejected" in r.json()["detail"]
    async with SessionLocal() as s:
        n = (await s.execute(select(func.count()).select_from(Integration).where(
            Integration.tenant_id == ws["tenant_id"]))).scalar_one()
    assert n == 0, "a refused key was stored anyway"

    fake.refuse_key = False
    fake.users = [user(1, role="Agent")]
    async with _client() as c:
        r = await c.post("/api/v1/integrations", headers=h,
                         json={"provider": "fub", "business_key": "main", "token": "fka_agent"})
    assert r.status_code == 200, r.text
    assert "only" in (r.json().get("warning") or ""), r.json()

    fake.users = [user(1, role="Broker", is_owner=True)]
    async with _client() as c:
        r = await c.post("/api/v1/integrations", headers=h,
                         json={"provider": "fub", "business_key": "main", "token": "fka_owner"})
    assert r.status_code == 200 and not r.json().get("warning"), r.json()


# ═════════════════════════════════════════════════════════════════════════════════════════
# The rules
# ═════════════════════════════════════════════════════════════════════════════════════════

async def test_the_new_lead_rule_at_its_edges():
    ws = await _workspace("rnew")
    a = await _agent(ws, 1, "a@x.test")
    await _lead(ws, "in", a, created=NOW - dt.timedelta(days=6))
    await _lead(ws, "contacted", a, contacted=True)
    await _lead(ws, "stale", a, created=NOW - dt.timedelta(days=8))
    await _lead(ws, "trash", a, stage="Trash")
    async with SessionLocal() as s:
        s.add(Lead(tenant_id=ws["tenant_id"], business_id=ws["business_id"], source="fub",
                   external_id="unknown", stage="Lead", agent_id=a, contacted=None,
                   src_created_at=NOW, name="Unknown"))
        await s.commit()
    q = await _q(ws, a)
    assert [i["person"]["id"] for i in q["items"]] == ["in"]
    assert q["counts"]["new_lead"] == 1
    wider = await _q(ws, a, new_lead_days=10)
    assert {i["person"]["id"] for i in wider["items"]} == {"in", "stale"}


async def test_the_task_rules_at_their_edges():
    ws = await _workspace("rtask")
    a = await _agent(ws, 1, "a@x.test")
    for ext in ("p1", "p2", "p3", "p4", "p5"):
        await _lead(ws, ext, a, contacted=True)
    await _lead(ws, "gone", a, contacted=True, stage="Trash")
    await _task(ws, "t1", "p1", a, TODAY)
    await _task(ws, "t2", "p2", a, TODAY - dt.timedelta(days=1))
    await _task(ws, "t3", "p3", a, TODAY - dt.timedelta(days=31))         # outside 30 days
    await _task(ws, "t4", "p4", a, TODAY + dt.timedelta(days=1))          # tomorrow
    await _task(ws, "t5", "gone", a, TODAY)                               # a trashed lead
    q = await _q(ws, a)
    kinds = {i["person"]["id"]: i["kind"] for i in q["items"]}
    assert kinds == {"p1": "due_today", "p2": "overdue"}
    assert q["counts"]["overdue"] == 1 and q["counts"]["due_today"] == 1
    everything = await _q(ws, a, overdue_max_days=0)
    assert {i["person"]["id"] for i in everything["items"]} == {"p1", "p2", "p3"}


async def test_one_row_per_person_under_the_more_urgent_reason():
    ws = await _workspace("rone")
    a = await _agent(ws, 1, "a@x.test")
    await _lead(ws, "p1", a)
    await _task(ws, "t1", "p1", a, TODAY, name="Intro call")
    await _task(ws, "t2", "p1", a, TODAY - dt.timedelta(days=2), name="Missed call")
    q = await _q(ws, a)
    assert len(q["items"]) == 1
    item = q["items"][0]
    assert item["kind"] == "new_lead" and set(item["also"]) == {"overdue", "due_today"}
    assert item["task"] is not None
    assert item["url"] == "https://acme.followupboss.com/2/people/view/p1"
    assert q["counts"] == {"new_lead": 1, "overdue": 1, "due_today": 1, "going_cold": 0,
                           "total": 1}


async def test_the_order_is_new_then_latest_miss_then_earliest_today():
    ws = await _workspace("rorder")
    a = await _agent(ws, 1, "a@x.test")
    await _lead(ws, "new-old", a, created=NOW - dt.timedelta(days=2))
    await _lead(ws, "new-new", a, created=NOW - dt.timedelta(hours=1))
    for ext in ("od-week", "od-yday", "td-late", "td-early", "td-anytime"):
        await _lead(ws, ext, a, contacted=True)
    await _task(ws, "t1", "od-week", a, TODAY - dt.timedelta(days=7))
    await _task(ws, "t2", "od-yday", a, TODAY - dt.timedelta(days=1))
    noon = dt.datetime.combine(TODAY, dt.time(12), tzinfo=DENVER)
    await _task(ws, "t3", "td-late", a, TODAY, due_at=noon + dt.timedelta(hours=4))
    await _task(ws, "t4", "td-early", a, TODAY, due_at=noon - dt.timedelta(hours=3))
    await _task(ws, "t5", "td-anytime", a, TODAY)
    q = await _q(ws, a)
    assert [i["person"]["id"] for i in q["items"]] == [
        "new-new", "new-old", "od-yday", "od-week", "td-early", "td-late", "td-anytime"]


async def test_going_cold_is_off_until_stages_are_picked():
    ws = await _workspace("rcold")
    a = await _agent(ws, 1, "a@x.test")
    await _lead(ws, "quiet", a, contacted=True, stage="Active Client",
                created=NOW - dt.timedelta(days=90), last_activity=NOW - dt.timedelta(days=20))
    await _lead(ws, "busy", a, contacted=True, stage="Active Client",
                created=NOW - dt.timedelta(days=90), last_activity=NOW - dt.timedelta(days=5))
    await _lead(ws, "other", a, contacted=True, stage="Sphere",
                created=NOW - dt.timedelta(days=90), last_activity=NOW - dt.timedelta(days=60))
    assert (await _q(ws, a))["items"] == []
    assert (await _q(ws, a, cold_enabled=True))["items"] == [], "no stages picked, no rule"
    q = await _q(ws, a, cold_enabled=True, cold_stages=["Active Client"])
    assert [(i["person"]["id"], i["kind"]) for i in q["items"]] == [("quiet", "going_cold")]


async def test_going_cold_is_counted_in_full_though_the_list_is_capped():
    ws = await _workspace("rcoldcap")
    a = await _agent(ws, 1, "a@x.test")
    for i in range(5):
        await _lead(ws, f"q{i}", a, contacted=True, stage="Active Client",
                    created=NOW - dt.timedelta(days=90),
                    last_activity=NOW - dt.timedelta(days=20 + i))
    rules = follow_ups.clean_settings({"cold_enabled": True, "cold_stages": ["Active Client"]})
    async with SessionLocal() as s:
        q = await follow_ups.queue(s, ws["tenant_id"], a, rules, today=TODAY, now=NOW,
                                   domain="acme", cap=2)
    assert q["counts"]["going_cold"] == 5 and q["counts"]["total"] == 5
    assert len(q["items"]) == 2 and q["truncated"]


async def test_the_team_counts_people_not_tasks_and_names_the_unassigned():
    ws = await _workspace("rteam")
    a = await _agent(ws, 1, "a@x.test", "Ada")
    b = await _agent(ws, 2, "b@x.test", "Bo")
    await _lead(ws, "a1", a)
    await _lead(ws, "a2", a, contacted=True)
    await _task(ws, "t1", "a2", a, TODAY - dt.timedelta(days=1))
    await _task(ws, "t2", "a2", a, TODAY - dt.timedelta(days=2))
    await _lead(ws, "b1", b, contacted=True)
    await _task(ws, "t3", "b1", b, TODAY)
    await _lead(ws, "u1", None)
    async with SessionLocal() as s:
        team = await follow_ups.team(s, ws["tenant_id"], follow_ups.clean_settings({}),
                                     today=TODAY, now=NOW)
    rows = {r["name"]: r for r in team["by_agent"]}
    assert rows["Ada"]["overdue"] == 1 and rows["Ada"]["new_lead"] == 1 and rows["Ada"]["total"] == 2
    assert rows["Bo"]["due_today"] == 1 and rows["Bo"]["total"] == 1
    assert team["unassigned_new_leads"] == 1
    assert [r["name"] for r in team["by_agent"]] == ["Ada", "Bo"]


def test_settings_are_bounded_and_trash_is_never_a_cold_stage():
    clean = follow_ups.clean_settings({"new_lead_days": 999, "overdue_max_days": -4,
                                       "cold_stages": ["Trash", "Hot", "Hot", " "]})
    assert clean["new_lead_days"] == 60 and clean["overdue_max_days"] == 0
    assert clean["cold_stages"] == ["Hot"]
    with pytest.raises(ValueError):
        follow_ups.clean_settings({"new_lead_days": 0}, strict=True)


# ═════════════════════════════════════════════════════════════════════════════════════════
# Who sees what
# ═════════════════════════════════════════════════════════════════════════════════════════

async def _get(token, host, query=""):
    async with _client() as c:
        return await c.get(f"/api/v1/intranet/follow-ups{query}", headers=_H(token, host))


async def test_a_member_sees_their_own_queue_and_nobody_elses():
    ws = await _workspace("wown")
    me = await _person(ws, "ada@wown.test", role="member")
    a = await _agent(ws, 1, "ada@wown.test", "Ada")
    b = await _agent(ws, 2, "bo@wown.test", "Bo")
    await _lead(ws, "mine", a)
    await _lead(ws, "theirs", b)
    r = await _get(me["token"], ws["host"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert [i["person"]["id"] for i in body["own"]["items"]] == ["mine"]
    assert body["own"]["agent"]["matched_by"] == "email"
    assert body["team"] is None, "a member's role does not include the team"
    r = await _get(me["token"], ws["host"], f"?agent={b}")
    assert r.status_code == 403


async def test_a_leader_sees_the_team_and_can_open_one_agent():
    ws = await _workspace("wlead")
    boss = await _person(ws, "boss@wlead.test", role="manager")
    a = await _agent(ws, 1, "ada@wlead.test", "Ada")
    await _lead(ws, "a1", a)
    await _lead(ws, "u1", None)
    r = await _get(boss["token"], ws["host"])
    body = r.json()
    assert body["own"] is None and body["own_reason"] == "unmatched"
    assert body["team"]["by_agent"][0]["name"] == "Ada"
    assert body["team"]["unassigned_new_leads"] == 1
    r = await _get(boss["token"], ws["host"], f"?agent={a}")
    assert [i["person"]["id"] for i in r.json()["viewing"]["items"]] == ["a1"]
    r = await _get(boss["token"], ws["host"], "?agent=unassigned")
    assert [i["person"]["id"] for i in r.json()["viewing"]["items"]] == ["u1"]

    other = await _workspace("wleadother")
    stranger = await _agent(other, 9, "x@other.test")
    r = await _get(boss["token"], ws["host"], f"?agent={stranger}")
    assert r.status_code == 404, "another workspace's agent"
    r = await _get(boss["token"], ws["host"], "?agent=not-a-uuid")
    assert r.status_code == 404


async def test_each_empty_says_why():
    none = await _workspace("wnone", fub=None)
    r = (await _get(none["owner_token"], none["host"])).json()
    assert r["connection"]["state"] == "not_connected" and r["own"] is None

    never = await _workspace("wnever", fub="never")
    r = (await _get(never["owner_token"], never["host"])).json()
    assert r["connection"]["state"] == "not_synced"

    ws = await _workspace("wladder")
    off = await _person(ws, "off@wladder.test", on_roster=False)
    r = (await _get(off["token"], ws["host"])).json()
    assert r["own_reason"] == "not_on_roster" and r["team"] is None

    # The owner is on the roster (bootstrap put them there) but is nobody in FUB.
    r = (await _get(ws["owner_token"], ws["host"])).json()
    assert r["own_reason"] == "unmatched" and r["team"] is not None

    denied = await _person(ws, "denied@wladder.test", role="member")
    await _agent(ws, 3, "denied@wladder.test")
    await _set_level(ws, "wtd", "member", "None")
    r = (await _get(denied["token"], ws["host"])).json()
    assert r["own_reason"] == "denied" and r["own"] is None

    ok = await _workspace("wcaught")
    fine = await _person(ws=ok, email="fine@wcaught.test")
    await _agent(ok, 4, "fine@wcaught.test")
    r = (await _get(fine["token"], ok["host"])).json()
    assert r["own"]["counts"]["total"] == 0 and r["own"]["items"] == [], "caught up is a result"


async def test_a_failing_sync_keeps_the_last_data_and_tells_only_admins_why():
    ws = await _workspace("wfail", fub="error")
    agent = await _person(ws, "ada@wfail.test")
    a = await _agent(ws, 1, "ada@wfail.test")
    await _lead(ws, "mine", a)
    r = (await _get(agent["token"], ws["host"])).json()
    assert r["connection"]["state"] == "ready" and r["connection"]["sync_failed"] is True
    assert r["connection"]["error"] is None, "the raw error is for admins"
    assert [i["person"]["id"] for i in r["own"]["items"]] == ["mine"]
    r = (await _get(ws["owner_token"], ws["host"])).json()
    assert "401" in r["connection"]["error"]


async def test_an_explicit_link_beats_the_email():
    ws = await _workspace("wlink")
    b_ext = "2"
    await _agent(ws, 1, "ada@wlink.test", "Ada by email")
    b = await _agent(ws, b_ext, "ada.old@crm.test", "Ada in FUB")
    me = await _person(ws, "ada@wlink.test", links={"fub": b_ext})
    await _lead(ws, "linked", b)
    r = (await _get(me["token"], ws["host"])).json()
    assert r["own"]["agent"] == {"id": str(b), "name": "Ada in FUB", "matched_by": "link"}
    assert [i["person"]["id"] for i in r["own"]["items"]] == ["linked"]


# ═════════════════════════════════════════════════════════════════════════════════════════
# The console
# ═════════════════════════════════════════════════════════════════════════════════════════

async def test_the_roster_shows_each_members_crm_match_and_takes_a_link():
    ws = await _workspace("croster")
    me = await _person(ws, "ada@croster.test")
    await _agent(ws, 1, "ada@croster.test", "Ada")
    await _agent(ws, 2, "other@croster.test", "Other")
    h = _H(ws["owner_token"], ws["host"])
    async with _client() as c:
        members = (await c.get("/api/console/members", headers=h)).json()["items"]
        ada = next(m for m in members if m["id"] == str(me["member_id"]))
        assert ada["crm"]["fub"]["name"] == "Ada" and ada["crm"]["fub"]["matched_by"] == "email"

        agents = (await c.get("/api/console/crm-agents?source=fub", headers=h)).json()["items"]
        assert {a["external_id"] for a in agents} == {"1", "2"}

        bad = await c.patch(f"/api/console/members/{me['member_id']}", headers=h,
                            json={"agent_links": {"fub": "999"}})
        assert bad.status_code == 422, bad.text

        ok = await c.patch(f"/api/console/members/{me['member_id']}", headers=h,
                           json={"agent_links": {"fub": "2"}})
        assert ok.status_code == 200, ok.text
        assert ok.json()["item"]["crm"]["fub"]["name"] == "Other"
        assert ok.json()["item"]["crm"]["fub"]["matched_by"] == "link"

        back = await c.patch(f"/api/console/members/{me['member_id']}", headers=h,
                             json={"agent_links": {"fub": None}})
        assert back.json()["item"]["crm"]["fub"]["matched_by"] == "email"


async def test_the_follow_up_settings_round_trip_and_are_bounded():
    ws = await _workspace("csettings")
    a = await _agent(ws, 1, "a@x.test")
    await _lead(ws, "hot", a, stage="Hot Prospect", contacted=True)
    h = _H(ws["owner_token"], ws["host"])
    async with _client() as c:
        got = (await c.get("/api/console/follow-ups", headers=h)).json()
        assert got["settings"] == follow_ups.DEFAULTS
        assert {"stage": "Hot Prospect", "people": 1} in got["stages"]
        bad = await c.patch("/api/console/follow-ups", headers=h, json={"new_lead_days": 0})
        assert bad.status_code == 422
        ok = await c.patch("/api/console/follow-ups", headers=h,
                           json={"new_lead_days": 3, "cold_enabled": True,
                                 "cold_stages": ["Hot Prospect"]})
        assert ok.status_code == 200, ok.text
        assert ok.json()["settings"]["new_lead_days"] == 3
    async with SessionLocal() as s:
        tenant = await s.get(Tenant, ws["tenant_id"])
    assert follow_ups.settings_for(tenant)["cold_stages"] == ["Hot Prospect"]


async def test_a_win_the_day_list_links_through_the_fub_account_when_no_base_url_is_set():
    ws = await _workspace("cwtd")
    async with SessionLocal() as s:
        s.add(IntranetWtdList(tenant_id=ws["tenant_id"], position=1, name="New leads",
                              provider="follow_up_boss", external_list_id="42",
                              published_at=NOW, draft_dirty=False))
        await s.commit()
    async with _client() as c:
        r = await c.get("/api/v1/intranet/config", headers=_H(ws["owner_token"], ws["host"]))
    lists = {l["name"]: l for l in r.json()["config"]["content"]["wtd"]["lists"]["items"]}
    assert lists["New leads"]["url"] == "https://acme.followupboss.com/2/people/list/42"
