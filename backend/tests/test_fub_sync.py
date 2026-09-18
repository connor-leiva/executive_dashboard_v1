"""The Follow Up Boss sync, run the way production runs it.

Nothing ran `sync_fub` before these. Its upsert was Postgres-only, so SQLite could not execute it,
and a string went into a Date column on every production run: 26 runs, 26 errors, 0 leads, while
the suite stayed green. These drive the real sync against a fake FUB served over httpx, through the
same dialect-aware upsert production uses, and hold the mapping to the models' own column types.
The follow-up passes themselves are covered in test_fub_follow_ups.py.
"""
import datetime as dt
import uuid

import pytest
from sqlalchemy import Boolean, Date, DateTime, String, func, select
from sqlalchemy.orm.attributes import flag_modified

from app.db import SessionLocal
from app.integrations import fub
from app.models import (Agent, Business, CrmTask, Integration, IntranetWorkspace, Lead, SyncRun,
                        Tenant)
from app.security import enc
from app.services import metrics
from app.services.sync import _sync_integration, sync_fub
from tests.fub_fake import fake, person, task, user  # noqa: F401 -- `fake` is a fixture


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


async def _workspace(slug: str, tz: str | None = "America/Denver") -> dict:
    async with SessionLocal() as s:
        t = Tenant(slug=slug, name=slug.title(), status="active", plan="portfolio",
                   config={"features": {"intranet": True}})
        s.add(t)
        await s.flush()
        b = Business(tenant_id=t.id, key="main", name="Main", tag="Business", sort_order=0)
        s.add(b)
        await s.flush()
        if tz:
            s.add(IntranetWorkspace(tenant_id=t.id, portal_name=slug.title(), subdomain=slug,
                                    timezone=tz))
        integ = Integration(tenant_id=t.id, provider="fub", business_id=b.id,
                            status="connected", access_token_enc=enc("fka_test"))
        s.add(integ)
        await s.commit()
        return {"tenant_id": t.id, "business_id": b.id, "integration_id": integ.id}


async def _integration(s, ws):
    return await s.get(Integration, ws["integration_id"])


def _ago(**kw) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _lead(ws, ext):
    async with SessionLocal() as s:
        return (await s.execute(select(Lead).where(
            Lead.tenant_id == ws["tenant_id"], Lead.external_id == str(ext)))).scalar_one()


# ── the regression ─────────────────────────────────────────────────────────────────────────

async def test_a_sync_stores_leads_with_real_dates_and_marks_the_source_healthy(fake):
    """The production failure, end to end: through `_sync_integration`, which is what the worker
    calls. It must finish `ok`, leave the integration `connected`, and store `created_at_src` as
    a date -- in the workspace's calendar, not UTC's."""
    fake.users = [user(1), user(2)]
    fake.people = [
        person(10, assigned=1, created="2026-09-10T15:00:00Z"),
        # 03:00 UTC on 1 September is still 31 August in Denver.
        person(11, assigned=2, created="2026-09-01T03:00:00Z"),
        person(12, assigned=0, created="2026-09-12T18:30:00.000Z"),
    ]
    ws = await _workspace("fubsync")
    async with SessionLocal() as s:
        await _sync_integration(s, ws["tenant_id"], await _integration(s, ws),
                                "2026-09-01", "2026-09-30")
    async with SessionLocal() as s:
        run = (await s.execute(select(SyncRun).where(
            SyncRun.tenant_id == ws["tenant_id"], SyncRun.provider == "fub"))).scalar_one()
        assert run.status == "ok", run.detail
        integ = await _integration(s, ws)
        assert integ.status == "connected" and integ.last_error is None
        leads = {l.external_id: l for l in (await s.execute(select(Lead).where(
            Lead.tenant_id == ws["tenant_id"]))).scalars().all()}
        agents = dict((await s.execute(select(Agent.external_id, Agent.id).where(
            Agent.tenant_id == ws["tenant_id"], Agent.source == "fub"))).all())

    assert set(leads) == {"10", "11", "12"}
    assert all(isinstance(l.created_at_src, dt.date) for l in leads.values())
    assert leads["10"].created_at_src == dt.date(2026, 9, 10)
    assert leads["11"].created_at_src == dt.date(2026, 8, 31), "the workspace's day, not UTC's"
    assert leads["10"].agent_id == agents["1"] and leads["11"].agent_id == agents["2"]
    assert leads["12"].agent_id is None, "assignedUserId 0 is nobody"
    assert run.stats["fub"]["backfill"] == "complete"


async def test_users_are_paged_past_the_first_hundred(fake):
    """Exactly 100 FUB agents were stored for a team with more: the first page, and no others."""
    fake.users = [user(i) for i in range(1, 251)]
    ws = await _workspace("fubusers")
    async with SessionLocal() as s:
        await sync_fub(s, ws["tenant_id"], await _integration(s, ws))
        n = (await s.execute(select(func.count()).select_from(Agent).where(
            Agent.tenant_id == ws["tenant_id"], Agent.source == "fub"))).scalar_one()
    assert n == 250
    assert [p.get("next") for p in fake.calls_to("/users")] == [None, "100", "200"]


async def test_the_whole_crm_is_walked_with_the_cursor_and_only_the_fields_kept(fake):
    fake.users = [user(1)]
    fake.people = [person(i, created="2025-01-01T00:00:00Z") for i in range(1, 231)]
    ws = await _workspace("fubpeople")
    async with SessionLocal() as s:
        await sync_fub(s, ws["tenant_id"], await _integration(s, ws))
        n = (await s.execute(select(func.count()).select_from(Lead).where(
            Lead.tenant_id == ws["tenant_id"]))).scalar_one()
    assert n == 230
    people = fake.calls_to("/people")
    backfill = [p for p in people
                if not {"lastActivityAfter", "contacted", "id", "sort"} & set(p)]
    assert [p.get("next") for p in backfill] == [None, "100", "200"]
    assert all("offset" not in p for p in people), "offset paging is what took eight minutes"
    assert all(p.get("fields") == fub.PERSON_FIELDS for p in people)
    for banned in ("emails", "phones", "addresses"):
        assert banned not in fub.PERSON_FIELDS, "no contact details are stored"


async def test_activity_brings_a_change_in_on_the_next_run(fake):
    fake.users = [user(1), user(2)]
    fake.people = [person(10, assigned=1, stage="Lead", created=_ago(days=40), contacted=True)]
    ws = await _workspace("fubactive")
    async with SessionLocal() as s:
        await sync_fub(s, ws["tenant_id"], await _integration(s, ws))
    fake.people = [person(10, assigned=2, stage="Hot Prospect", created=_ago(days=40),
                          contacted=True, last_activity=_ago(minutes=1))]
    async with SessionLocal() as s:
        await sync_fub(s, ws["tenant_id"], await _integration(s, ws))
        agent2 = (await s.execute(select(Agent.id).where(
            Agent.tenant_id == ws["tenant_id"], Agent.external_id == "2"))).scalar_one()
    lead = await _lead(ws, 10)
    assert lead.stage == "Hot Prospect" and lead.agent_id == agent2


async def test_a_change_with_no_activity_arrives_on_the_daily_rewalk(fake):
    """The designed bound. A stage change or reassignment that FUB records as no activity is not
    in any quick pass -- the uncontacted set covers new leads, the task snapshot covers tasks --
    so it arrives when the whole CRM is re-walked, at most a day later."""
    fake.users = [user(1), user(2)]
    # A hundred newer people, so the quiet one is not on the first newest-first page either.
    newer = [person(1000 + i, created=_ago(days=10, minutes=i), contacted=True) for i in range(100)]
    fake.people = newer + [person(10, assigned=1, stage="Lead", created=_ago(days=40),
                                  contacted=True)]
    ws = await _workspace("fubrewalk")
    async with SessionLocal() as s:
        await sync_fub(s, ws["tenant_id"], await _integration(s, ws))
    fake.people = newer + [person(10, assigned=2, stage="Active Client", created=_ago(days=40),
                                  contacted=True)]
    async with SessionLocal() as s:
        await sync_fub(s, ws["tenant_id"], await _integration(s, ws))
    assert (await _lead(ws, 10)).stage == "Lead", "inside the day, the quiet change waits"

    async with SessionLocal() as s:
        integ = await _integration(s, ws)
        state = dict(integ.config["fub_state"])
        state["backfill"] = {**state["backfill"], "completed_at": _ago(hours=25)}
        integ.config = {**integ.config, "fub_state": state}
        flag_modified(integ, "config")
        await s.commit()
    async with SessionLocal() as s:
        await sync_fub(s, ws["tenant_id"], await _integration(s, ws))
    assert (await _lead(ws, 10)).stage == "Active Client"


async def test_a_rate_limited_page_is_retried_not_fatal(fake):
    """FUB answers 429 with Retry-After when a walk is too fast. That failed the whole sync."""
    fake.users = [user(1)]
    fake.people = [person(i, created="2025-01-01T00:00:00Z") for i in range(1, 151)]
    fake.throttle["/people"] = 2
    ws = await _workspace("fub429")
    async with SessionLocal() as s:
        await sync_fub(s, ws["tenant_id"], await _integration(s, ws))
        n = (await s.execute(select(func.count()).select_from(Lead).where(
            Lead.tenant_id == ws["tenant_id"]))).scalar_one()
    assert n == 150


# ── the mapping, held to the models rather than to a list somebody keeps ───────────────────

def _check_fits(mapped: dict, model) -> int:
    columns = {c.name: c for c in model.__table__.columns}
    checked = 0
    for key, value in mapped.items():
        col = columns.get(key)
        if col is None or value is None:
            continue
        checked += 1
        kind = col.type
        if isinstance(kind, DateTime):
            assert isinstance(value, dt.datetime) and value.tzinfo is not None, key
        elif isinstance(kind, Date):
            assert isinstance(value, dt.date) and not isinstance(value, dt.datetime), key
        elif isinstance(kind, String) and kind.length:
            assert isinstance(value, str) and len(value) <= kind.length, (key, len(value))
        elif isinstance(kind, Boolean):
            assert isinstance(value, bool), key
    return checked


def test_every_mapped_value_fits_the_column_it_is_written_to():
    """Derived from the models' own columns, so a column added later is checked without anybody
    remembering to add it here: dates are dates, timestamps are aware datetimes, text fits its
    length, flags are booleans. The production failure was exactly a type this would have named."""
    lead = person(1, name="N" * 500, stage="S" * 500, source="Z" * 500, last_activity=_ago(days=1))
    assert _check_fits(fub.map_person(lead), Lead) >= 8, \
        "the mapping no longer writes the columns this test is here for"

    open_task = task(5, person_id=1, due=dt.date(2026, 9, 18), name="T" * 900, kind="K" * 90,
                     at="2026-09-18T20:00:00Z")
    assert _check_fits(fub.map_task(open_task), CrmTask) >= 5

    agent_cols = {c.name: c for c in Agent.__table__.columns}
    mapped_user = fub.map_user({"id": 5, "name": "U" * 500, "email": ("E" * 300) + "@x.test"})
    for key in ("name", "email", "external_id"):
        assert len(mapped_user[key]) <= agent_cols[key].type.length, key


def test_timestamps_parse_in_every_shape_fub_sends():
    utc = dt.timezone.utc
    assert fub.parse_ts("2026-09-10T15:00:00Z") == dt.datetime(2026, 9, 10, 15, tzinfo=utc)
    assert fub.parse_ts("2026-09-10T15:00:00.000Z").tzinfo is not None
    assert fub.parse_ts("2026-09-10 15:00:00") == dt.datetime(2026, 9, 10, 15, tzinfo=utc)
    assert fub.parse_ts("2026-09-10T09:00:00-06:00") == dt.datetime(2026, 9, 10, 15, tzinfo=utc)
    assert fub.parse_ts(None) is None and fub.parse_ts("") is None and fub.parse_ts("soon") is None


def test_a_task_lands_on_the_workspaces_day():
    from zoneinfo import ZoneInfo
    denver = ZoneInfo("America/Denver")
    # A time with no date: 02:00 UTC on the 19th is still the 18th in Denver.
    timed = fub.map_task({"id": 1, "personId": 9, "assignedUserId": 3,
                          "dueDateTime": "2026-09-19T02:00:00Z"}, denver)
    assert timed["due_on"] == dt.date(2026, 9, 18) and timed["due_at"].tzinfo is not None
    dated = fub.map_task({"id": 2, "personId": 0, "dueDate": "2026-09-20"}, denver)
    assert dated["due_on"] == dt.date(2026, 9, 20) and dated["person_external_id"] is None


# ── the dashboard funnel reads the period, not the whole CRM ──────────────────────────────

async def test_the_funnel_counts_the_leads_that_came_in_this_period():
    """It counted every lead ever held, with no window -- unnoticed only because the sync never
    stored any. Trash is excluded; the appointment count is of this period's leads."""
    ws = await _workspace("fubfunnel", tz=None)
    tid, bid = ws["tenant_id"], ws["business_id"]
    async with SessionLocal() as s:
        def lead(ext, created, stage="Lead"):
            s.add(Lead(id=uuid.uuid4(), tenant_id=tid, business_id=bid, source="fub",
                       external_id=ext, stage=stage, created_at_src=created))
        lead("in1", dt.date(2026, 9, 3))
        lead("in2", dt.date(2026, 9, 20), stage="Appointment")
        lead("junk", dt.date(2026, 9, 5), stage="Trash")
        lead("old", dt.date(2025, 1, 5), stage="Appointment")
        lead("next", dt.date(2026, 10, 1))
        await s.commit()
        rows = await metrics._funnel(s, tid, bid, dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    funnel = {r.label: r.v for r in rows}
    assert funnel["Leads"] == 2
    assert funnel["Appointments"] == 1
