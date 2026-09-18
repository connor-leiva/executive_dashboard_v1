"""The Follow Up Boss sync, run the way production runs it.

Nothing ran `sync_fub` before these. Its upsert was Postgres-only, so SQLite could not execute it,
and a string went into a Date column on every production run: 26 runs, 26 errors, 0 leads, while
the suite stayed green. These drive the real sync against a fake FUB served over httpx, through the
same dialect-aware upsert production uses, and hold the mapping to the model's own column types.
"""
import datetime as dt
import uuid

import httpx
import pytest
from sqlalchemy import Boolean, Date, DateTime, String, func, select

from app.db import SessionLocal
from app.integrations import fub
from app.models import (Agent, Business, Integration, IntranetWorkspace, Lead, SyncRun, Tenant)
from app.security import enc
from app.services import metrics
from app.services.sync import _sync_integration, sync_fub


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


class FakeFub:
    """Just enough of Follow Up Boss: cursor paging, a few filters, and a 429 on request."""

    def __init__(self, users=(), people=(), tasks=()):
        self.users, self.people, self.tasks = list(users), list(people), list(tasks)
        self.calls: list[tuple[str, dict]] = []
        self.throttle: dict[str, int] = {}          # path -> how many 429s to send first
        self.identity = {"account": {"id": 777, "domain": "acme"},
                         "user": {"id": 1, "name": "Owner", "email": "owner@acme.test"}}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        params = dict(request.url.params)
        self.calls.append((path, params))
        if self.throttle.get(path):
            self.throttle[path] -= 1
            return httpx.Response(429, headers={"Retry-After": "0"})
        if path == "/identity":
            return httpx.Response(200, json=self.identity)
        if path == "/users":
            return self._page("users", self.users, params)
        if path == "/people":
            return self._page("people", self._people(params), params)
        if path == "/tasks":
            return self._page("tasks", self._tasks(params), params)
        return httpx.Response(404, json={"errorMessage": f"no route {path}"})

    def _people(self, params):
        rows = self.people
        if "id" in params:
            wanted = set(params["id"].split(","))
            rows = [p for p in rows if str(p["id"]) in wanted]
        if params.get("includeTrash") != "true" and "id" not in params:
            rows = [p for p in rows if p.get("stage") != "Trash"]
        if "contacted" in params:
            flag = params["contacted"] == "true"
            rows = [p for p in rows if bool(p.get("contacted")) == flag]
        if "lastActivityAfter" in params:
            after = dt.datetime.strptime(params["lastActivityAfter"], "%Y-%m-%d %H:%M:%S")
            rows = [p for p in rows if p.get("lastActivity") and dt.datetime.fromisoformat(
                p["lastActivity"].replace("Z", "")) > after]
        return rows

    def _tasks(self, params):
        return list(self.tasks)

    @staticmethod
    def _page(key, rows, params):
        start = int(params.get("next") or 0)
        limit = int(params.get("limit") or 10)
        chunk = rows[start:start + limit]
        nxt = str(start + limit) if start + limit < len(rows) else None
        return httpx.Response(200, json={key: chunk, "_metadata": {
            "collection": key, "offset": start, "limit": limit, "total": len(rows), "next": nxt}})


@pytest.fixture
def fake(monkeypatch):
    server = FakeFub()
    monkeypatch.setattr(fub, "TRANSPORT", httpx.MockTransport(server))

    async def _no_wait(_seconds):
        return None
    monkeypatch.setattr(fub, "_sleep", _no_wait)
    return server


def _user(i, email=None, status="Active"):
    return {"id": i, "name": f"Agent {i}", "email": email or f"agent{i}@acme.test",
            "status": status, "role": "Agent"}


def _person(i, *, assigned=1, created="2026-09-10T15:00:00Z", stage="Lead", contacted=False,
            last_activity=None, name=None):
    return {"id": i, "name": name if name is not None else f"Person {i}", "stage": stage,
            "source": "Zillow", "contacted": contacted, "assignedUserId": assigned,
            "created": created, "updated": created, "lastActivity": last_activity or created}


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


# ── the regression ─────────────────────────────────────────────────────────────────────────

async def test_a_sync_stores_leads_with_real_dates_and_marks_the_source_healthy(fake):
    """The production failure, end to end: users, then people, through `_sync_integration`, which
    is what the worker calls. It must finish `ok`, leave the integration `connected`, and store
    `created_at_src` as a date -- in the workspace's calendar, not UTC's."""
    fake.users = [_user(1), _user(2)]
    fake.people = [
        _person(10, assigned=1, created="2026-09-10T15:00:00Z"),
        # 03:00 UTC on 1 September is still 31 August in Denver.
        _person(11, assigned=2, created="2026-09-01T03:00:00Z"),
        _person(12, assigned=0, created="2026-09-12T18:30:00.000Z"),
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


async def test_users_are_paged_past_the_first_hundred(fake):
    """Exactly 100 FUB agents were stored for a team with more: the first page, and no others."""
    fake.users = [_user(i) for i in range(1, 251)]
    ws = await _workspace("fubusers")
    async with SessionLocal() as s:
        await sync_fub(s, ws["tenant_id"], await _integration(s, ws))
        n = (await s.execute(select(func.count()).select_from(Agent).where(
            Agent.tenant_id == ws["tenant_id"], Agent.source == "fub"))).scalar_one()
    assert n == 250
    assert [p.get("next") for path, p in fake.calls if path == "/users"] == [None, "100", "200"]


async def test_people_are_walked_with_the_cursor_and_only_the_fields_kept(fake):
    fake.users = [_user(1)]
    fake.people = [_person(i) for i in range(1, 231)]
    ws = await _workspace("fubpeople")
    async with SessionLocal() as s:
        await sync_fub(s, ws["tenant_id"], await _integration(s, ws))
        n = (await s.execute(select(func.count()).select_from(Lead).where(
            Lead.tenant_id == ws["tenant_id"]))).scalar_one()
    assert n == 230
    walked = [p for path, p in fake.calls if path == "/people"]
    assert [p.get("next") for p in walked] == [None, "100", "200"]
    assert all("offset" not in p for p in walked), "offset paging is what took eight minutes"
    assert all(p.get("fields") == fub.PERSON_FIELDS for p in walked)
    for banned in ("emails", "phones", "addresses"):
        assert banned not in fub.PERSON_FIELDS, "no contact details are stored"


async def test_a_second_run_updates_in_place(fake):
    fake.users = [_user(1), _user(2)]
    fake.people = [_person(10, assigned=1, stage="Lead")]
    ws = await _workspace("fubrerun")
    async with SessionLocal() as s:
        await sync_fub(s, ws["tenant_id"], await _integration(s, ws))
    fake.people = [_person(10, assigned=2, stage="Hot Prospect")]
    async with SessionLocal() as s:
        await sync_fub(s, ws["tenant_id"], await _integration(s, ws))
        rows = (await s.execute(select(Lead).where(Lead.tenant_id == ws["tenant_id"]))).scalars().all()
        agent2 = (await s.execute(select(Agent.id).where(
            Agent.tenant_id == ws["tenant_id"], Agent.external_id == "2"))).scalar_one()
    assert len(rows) == 1
    assert rows[0].stage == "Hot Prospect" and rows[0].agent_id == agent2


async def test_a_rate_limited_page_is_retried_not_fatal(fake):
    """FUB answers 429 with Retry-After when a walk is too fast. That failed the whole sync."""
    fake.users = [_user(1)]
    fake.people = [_person(i) for i in range(1, 151)]
    fake.throttle["/people"] = 2
    ws = await _workspace("fub429")
    async with SessionLocal() as s:
        await sync_fub(s, ws["tenant_id"], await _integration(s, ws))
        n = (await s.execute(select(func.count()).select_from(Lead).where(
            Lead.tenant_id == ws["tenant_id"]))).scalar_one()
    assert n == 150


# ── the mapping, held to the model rather than to a list somebody keeps ────────────────────

def test_every_mapped_value_fits_the_column_it_is_written_to():
    """Derived from `Lead`'s own columns, so a column added later is checked without anybody
    remembering to add it here: dates are dates, timestamps are aware datetimes, text fits its
    length, flags are booleans. The production failure was exactly a type this would have named."""
    person = _person(1, name="N" * 500, stage="S" * 500)
    person["source"] = "Z" * 500
    mapped = fub.map_person(person)
    columns = {c.name: c for c in Lead.__table__.columns}
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
    assert checked >= 2, "the mapping no longer writes the columns this test is here for"

    user = fub.map_user({"id": 5, "name": "U" * 500, "email": ("E" * 300) + "@x.test"})
    agent_cols = {c.name: c for c in Agent.__table__.columns}
    for key in ("name", "email", "external_id"):
        assert len(user[key]) <= agent_cols[key].type.length, key


def test_timestamps_parse_in_every_shape_fub_sends():
    assert fub.parse_ts("2026-09-10T15:00:00Z") == dt.datetime(2026, 9, 10, 15, tzinfo=dt.timezone.utc)
    assert fub.parse_ts("2026-09-10T15:00:00.000Z").tzinfo is not None
    assert fub.parse_ts("2026-09-10 15:00:00") == dt.datetime(2026, 9, 10, 15, tzinfo=dt.timezone.utc)
    assert fub.parse_ts("2026-09-10T09:00:00-06:00") == dt.datetime(2026, 9, 10, 15, tzinfo=dt.timezone.utc)
    assert fub.parse_ts(None) is None and fub.parse_ts("") is None and fub.parse_ts("soon") is None


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
