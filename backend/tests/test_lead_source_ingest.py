"""The scoped Follow Up Boss lead ingestion: the lead-source reports' people, fetched by source.

  * the FETCHER, replaying FUB-shaped fixtures (tests/fixtures/fub): the request it makes, that it
    follows `nextLink`, and what it keeps and drops;
  * the MAPPING and UPSERT into `lead`: origin, the workspace's cohort date, agent and FUB's own
    assigned-to name, stage, price, the primary email, the FUB link, and never a phone;
  * the run inside the FULL SYNC: people in scope that no other pass reached, their touches and
    deals, the Sisu join finding them, the log line;
  * the CURSORS: a long history resumed from the saved `nextLink`, then `updatedAfter`, checked,
    with the fallback when FUB ignores or refuses it;
  * the variant DISCOVERY Settings shows, and its API.

No network: FUB is the fixtures or tests/fub_fake.py, Sisu a stub.
"""
import datetime as dt
import json
import logging
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.integrations import fub, sisu
from app.main import app
from app.models import (Business, CrmActivity, Integration, IntranetWorkspace, Lead, LeadSourceGroup,
                        Tenant, User)
from app.security import enc, hash_pw, make_token
from app.services import fub_sync
from app.services import lead_source_discovery as disc
from app.services import lead_sources as ls
from app.services.lead_source_pipeline import classify_stage
from app.services.lead_source_report import load_universe
from app.services.lead_source_sync import after_sisu
from app.services.sync import _fub_creds
from tests.fub_fake import call, deal, fake, person, user  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures" / "fub"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


async def _workspace(slug: str, variants=("Realtor.com",), *, owner=False) -> dict:
    """A Denver brokerage with one Realtor.com group from 1 February 2026, FUB connected to it."""
    async with SessionLocal() as s:
        t = Tenant(slug=slug, name=slug.title(), status="active", plan="portfolio")
        s.add(t)
        await s.flush()
        b = Business(tenant_id=t.id, key="brokerage", name="Brokerage", tag="Real estate", kind="real_estate")
        s.add(b)
        await s.flush()
        s.add(IntranetWorkspace(tenant_id=t.id, portal_name=slug, subdomain=slug, timezone="America/Denver"))
        g = LeadSourceGroup(tenant_id=t.id, business_id=b.id, key="realtor_com", display_name="Realtor.com",
                            variants=list(variants), match_mode="contains", program_start=dt.date(2026, 2, 1),
                            is_active=True, display_order=0, exp_risk_team_default=Decimal(69),
                            exp_risk_agent_default=Decimal(49))
        s.add(g)
        integ = Integration(tenant_id=t.id, provider="fub", business_id=b.id, status="connected",
                            access_token_enc=enc("fka_test"), config={"fub_state": {"account_domain": "acme"}})
        s.add(integ)
        await s.flush()
        out = {"tenant_id": t.id, "business_id": b.id, "integration_id": integ.id}
        if owner:
            u = User(tenant_id=t.id, email=f"owner@{slug}.test", name="Owner", password_hash=hash_pw("x" * 12),
                     role="owner", status="active", token_version=0)
            s.add(u)
            await s.flush()
            out["headers"] = {"Authorization": f"Bearer {make_token(u.id, t.id, 0)}",
                              "X-Tenant-Host": f"{slug}.{settings.PLATFORM_DOMAIN}"}
        await s.commit()
        return out


async def _ingest(ws) -> fub_sync._Run:
    """The ingestion step alone, as the full sync runs it, with its state saved after."""
    async with SessionLocal() as s:
        integ = await s.get(Integration, ws["integration_id"])
        async with fub.client(_fub_creds(integ)) as c:
            run = await fub_sync._run_for(s, c, ws["tenant_id"], integ)
            await fub_sync._lead_sources(run)
        fub_sync._save(integ, run.state)
        await s.commit()
        return run


async def _leads(tid) -> dict[str, Lead]:
    async with SessionLocal() as s:
        return {lead.external_id: lead for lead in (await s.execute(
            select(Lead).where(Lead.tenant_id == tid))).scalars()}


# ── the fetcher, against recorded-shape fixtures ─────────────────────────────────────────────

class Replay:
    """FUB as the fixture files: page 1 of the source walk, page 2 only at page 1's `nextLink`."""

    def __init__(self):
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/v1")
        if path == "/users":
            return httpx.Response(200, json={"users": [{"id": 12, "name": "Jordan Ellis", "status": "Active",
                                                        "email": "jordan@example.com", "role": "Agent"}],
                                             "_metadata": {"next": None}})
        if path == "/people":
            page = "people_by_source_page2.json" if "next" in request.url.params else "people_by_source_page1.json"
            return httpx.Response(200, json=_load(page))
        if path == "/deals":
            return httpx.Response(200, json=_load("deals_by_person.json"))
        key = path.strip("/")
        return httpx.Response(200, json={key: [], "_metadata": {"next": None}})


@pytest.fixture
def replay(monkeypatch):
    server = Replay()
    monkeypatch.setattr(fub, "TRANSPORT", httpx.MockTransport(server))
    return server


async def test_the_fetcher_asks_by_source_and_program_start_and_follows_next_link(replay):
    ws = await _workspace(f"rep-{uuid.uuid4().hex[:6]}")
    run = await _ingest(ws)
    people = [r for r in replay.requests if r.url.path == "/v1/people"]
    first = dict(people[0].url.params)
    assert first["source"] == "Realtor.com" and first["limit"] == "100" and first["includeTrash"] == "true"
    assert first["createdAfter"] == "2026-02-01 07:00:00"            # the workspace's midnight, in UTC
    assert "assignedTo" in first["fields"].split(",")
    # The second page is exactly the URL FUB handed back, not a request rebuilt from the token.
    assert str(people[1].url) == _load("people_by_source_page1.json")["_metadata"]["nextLink"]
    assert len(people) == 2
    stats = run.stats["lead_sources"]
    assert stats["people_fetched"] == 4 and stats["people_upserted"] == 2 and stats["skipped_source"] == 2
    walk = run.state["lead_sources"]["realtor_com"]["variants"]["Realtor.com"]
    assert walk["walking"] is False and walk["complete_at"] and walk["next"] is None


async def test_the_mapping_keeps_what_the_reports_read_and_never_a_phone(replay):
    ws = await _workspace(f"map-{uuid.uuid4().hex[:6]}")
    await _ingest(ws)
    leads = await _leads(ws["tenant_id"])
    assert set(leads) == {"10001", "10003"}        # 10002 arrived 31 Jan in Denver; 10004 is Zillow
    a, c = leads["10001"], leads["10003"]
    assert a.origin == "Realtor.com" and a.business_id == ws["business_id"] and a.source == "fub"
    assert a.created_at_src == dt.date(2026, 2, 3) and a.stage == "Lead" and a.contacted is True
    assert a.price == Decimal("485000.00")                            # "$485,000"
    assert a.email == "avery.lindqvist@example.com"                    # the primary, not the first listed
    assert a.src_url == "https://acme.followupboss.com/2/people/view/10001"
    assert a.assigned_name == "Jordan Ellis" and a.agent_id is not None   # user 12, read from /users
    assert c.agent_id is None and c.assigned_name == "Morgan Pruitt"       # FUB names an agent Axcion lacks
    assert c.stage == "Appointment Set" and float(c.price) == 612500
    held = " ".join(str(getattr(a, col.name)) for col in Lead.__table__.columns)
    assert "5550101" not in held and "avery.old" not in held          # no phone, no secondary address


async def test_a_deal_is_read_per_person_with_its_stage_and_status(replay):
    ws = await _workspace(f"deal-{uuid.uuid4().hex[:6]}")
    async with SessionLocal() as s:
        integ = await s.get(Integration, ws["integration_id"])
        async with fub.client(_fub_creds(integ)) as c:
            run = await fub_sync._run_for(s, c, ws["tenant_id"], integ)
            rows = await fub_sync._person_activity(run, "10001")
    deals = [r for r in rows if r["kind"] == "deal"]
    assert deals == [{**deals[0], "external_id": "5501", "deal_stage": "offer written",
                      "deal_status": "active", "channel": "deal", "occurred_at": None}]
    assert [r.url.params["personId"] for r in replay.requests if r.url.path == "/v1/deals"] == ["10001"]


# ── inside the full sync, against the fake FUB ──────────────────────────────────────────────

def _general_passes_miss_february(monkeypatch):
    """On an account of 130,000 the newest-first page and the whole-CRM backfill do not reach
    February's leads in time: that is the production gap this ingestion closes."""
    async def nothing(_run):
        return None
    monkeypatch.setattr(fub_sync, "_newest_people", nothing)
    monkeypatch.setattr(fub_sync, "_backfill", nothing)


async def test_the_full_sync_ingests_the_group_its_touches_and_puts_them_in_scope(fake, monkeypatch, caplog):
    _general_passes_miss_february(monkeypatch)
    fake.users = [user(1)]
    fake.people = [
        person(1, source="Realtor.com", created="2026-02-10T17:00:00Z", emails=[{"value": "a@x.com", "isPrimary": True}]),
        person(2, source="Market VIP via Opcity BLA", created="2026-03-01T17:00:00Z"),
        person(3, source="Realtor.com", created="2026-02-01T06:00:00Z"),        # 31 Jan, 11pm Denver
        person(4, source="Zillow", created="2026-02-10T17:00:00Z"),
        person(5, source="Realtor.com MVIP", created="2026-02-12T17:00:00Z"),  # a string nobody listed
    ]
    fake.activity["calls"] = [call(101, person_id=1, created="2026-02-10T17:04:00Z", duration=40)]
    fake.activity["deals"] = [deal(901, person_id=1, stage="Offer Written")]
    ws = await _workspace(f"full-{uuid.uuid4().hex[:6]}", variants=("Realtor.com", "Market VIP via Opcity BLA"))
    caplog.set_level(logging.INFO, logger="app")
    async with SessionLocal() as s:
        integ = await s.get(Integration, ws["integration_id"])
        await fub_sync.full_sync(s, ws["tenant_id"], integ, _fub_creds(integ))

    assert set(await _leads(ws["tenant_id"])) == {"1", "2"}
    walks = {p["source"] for p in fake.calls_to("/people") if "source" in p and p.get("limit") != "1"}
    assert walks == {"Realtor.com", "Market VIP via Opcity BLA"}
    # The daily discovery ran as well: one-row probes, a bare "Realtor" among them.
    probes = {p["source"] for p in fake.calls_to("/people") if "source" in p and p.get("limit") == "1"}
    assert "Realtor" in probes and integ.config["fub_state"]["lead_source_discovery"]["realtor_com"]["sources"]
    async with SessionLocal() as s:
        where = await ls.scope_filter(s, ws["tenant_id"], ws["business_id"])
        assert {lead.external_id for lead in (await s.execute(select(Lead).where(where))).scalars()} == {"1", "2"}
        acts = {(a.kind, a.external_id): a for a in (await s.execute(select(CrmActivity).where(
            CrmActivity.tenant_id == ws["tenant_id"]))).scalars()}
        assert acts[("call", "101")].is_live and acts[("deal", "901")].deal_stage == "offer written"
        g = (await s.execute(select(LeadSourceGroup).where(LeadSourceGroup.tenant_id == ws["tenant_id"]))).scalar_one()
        universe = await load_universe(s, ws["tenant_id"], ws["business_id"], g)
        one = next(p for p in universe["people"] if p["person_id"] == "1")
        assert one["deals"][0]["stage"] == "offer written" and classify_stage(one) == "offer_written"
        assert one["fub_url"] == "https://acme.followupboss.com/2/people/view/1"
        assert one["first_outbound_at"] == dt.datetime(2026, 2, 10, 17, 4, tzinfo=dt.timezone.utc)

    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("fub lead sources"))
    assert "groups=1 variants=2" in line and "people_upserted=2" in line and "touches_people=2" in line
    assert line.endswith("errors=0")


async def test_the_sisu_join_finds_the_ingested_people(fake, monkeypatch):
    _general_passes_miss_february(monkeypatch)
    fake.users = [user(1)]
    fake.people = [person(i, source="Realtor.com", created=f"2026-02-1{i}T17:00:00Z",
                          emails=[{"value": f"p{i}@x.com", "isPrimary": True}]) for i in (1, 2, 3)]
    ws = await _workspace(f"sisu-{uuid.uuid4().hex[:6]}")
    async with SessionLocal() as s:
        integ = await s.get(Integration, ws["integration_id"])
        await fub_sync.full_sync(s, ws["tenant_id"], integ, _fub_creds(integ))
        sisu_integ = Integration(tenant_id=ws["tenant_id"], provider="sisu", business_id=ws["business_id"],
                                 status="connected")
        s.add(sisu_integ)
        await s.commit()
    asked = []

    def nobody(request):
        asked.append(dict(request.url.params))
        return httpx.Response(404, json={})
    monkeypatch.setattr(sisu, "TRANSPORT", httpx.MockTransport(nobody))

    async def _no_wait(_s):
        return None
    monkeypatch.setattr(sisu, "_sleep", _no_wait)
    async with SessionLocal() as s:
        stats = await after_sisu(s, ws["tenant_id"], await s.get(Integration, sisu_integ.id),
                                 sisu.SisuCreds("u", "t", "https://sisu.test/api"))
    assert stats["in_scope"] == 3 and stats["lookups"] == 3
    assert {q["external_contact_id"] for q in asked} == {"1", "2", "3"}


# ── cursors ──────────────────────────────────────────────────────────────────────────────────

async def test_a_long_history_resumes_from_the_saved_next_link(fake, monkeypatch):
    monkeypatch.setattr(fub, "PAGE", 1)
    monkeypatch.setattr(settings, "FUB_LEAD_SOURCE_PAGES_PER_RUN", 1)
    fake.users = [user(1)]
    fake.people = [person(i, source="Realtor.com", created=f"2026-02-1{i}T17:00:00Z") for i in (1, 2, 3)]
    ws = await _workspace(f"resume-{uuid.uuid4().hex[:6]}")

    run = await _ingest(ws)
    walk = run.state["lead_sources"]["realtor_com"]["variants"]["Realtor.com"]
    assert walk["walking"] is True and walk["next"].startswith("https://api.followupboss.com/v1/people?")
    assert set(await _leads(ws["tenant_id"])) == {"1"}

    fake.calls.clear()
    await _ingest(ws)
    assert [p.get("next") for p in fake.calls_to("/people")] == ["1"]      # resumed, not restarted
    run = await _ingest(ws)
    walk = run.state["lead_sources"]["realtor_com"]["variants"]["Realtor.com"]
    assert walk["walking"] is False and walk["complete_at"]
    assert set(await _leads(ws["tenant_id"])) == {"1", "2", "3"}


async def test_after_the_backfill_only_changes_are_read_with_updated_after_checked(fake):
    fake.users = [user(1)]
    fake.people = [person(1, source="Realtor.com", created="2026-02-10T17:00:00Z", stage="Lead"),
                   person(2, source="Realtor.com", created="2026-02-11T17:00:00Z")]
    ws = await _workspace(f"incr-{uuid.uuid4().hex[:6]}")
    await _ingest(ws)                                                   # the backfill

    # FUB honours updatedAfter: one walk, only the changed person.
    fake.people[0].update(stage="Met With Client", updated="2099-01-01T00:00:00Z")
    fake.calls.clear()
    run = await _ingest(ws)
    walks = [p for p in fake.calls_to("/people") if "source" in p]
    assert len(walks) == 1 and "updatedAfter" in walks[0] and "createdAfter" in walks[0]
    assert run.state["lead_sources_updated_after"] == "ok" and run.stats["lead_sources"]["people_fetched"] == 1
    assert (await _leads(ws["tenant_id"]))["1"].stage == "Met With Client"

    # FUB ignores it: a person updated before the mark comes back, so the run falls back to
    # creation and activity cursors, and remembers.
    fake.ignore_updated_after = True
    fake.calls.clear()
    run = await _ingest(ws)
    assert run.state["lead_sources_updated_after"] == "ignored"
    params = [p for p in fake.calls_to("/people") if "source" in p]
    assert "updatedAfter" in params[0] and any("lastActivityAfter" in p for p in params[1:])
    fake.calls.clear()
    await _ingest(ws)
    assert not any("updatedAfter" in p for p in fake.calls_to("/people"))


async def test_a_refused_updated_after_falls_back_too(fake):
    fake.users = [user(1)]
    fake.people = [person(1, source="Realtor.com", created="2026-02-10T17:00:00Z")]
    ws = await _workspace(f"refuse-{uuid.uuid4().hex[:6]}")
    await _ingest(ws)
    fake.refuse_updated_after = True
    fake.people.append(person(2, source="Realtor.com", created="2099-01-02T00:00:00Z"))
    run = await _ingest(ws)
    assert run.state["lead_sources_updated_after"] == "ignored" and not run.stats["lead_sources"]["errors"]
    assert "2" in await _leads(ws["tenant_id"])                     # found by createdAfter instead


async def test_no_active_group_reads_nobody(fake):
    ws = await _workspace(f"none-{uuid.uuid4().hex[:6]}")
    async with SessionLocal() as s:
        g = (await s.execute(select(LeadSourceGroup).where(LeadSourceGroup.tenant_id == ws["tenant_id"]))).scalar_one()
        g.is_active = False
        await s.commit()
    run = await _ingest(ws)
    assert run.stats["lead_sources"]["note"] == "no active lead-source group on this business"
    assert not [p for p in fake.calls_to("/people") if "source" in p]


# ── discovery ────────────────────────────────────────────────────────────────────────────────

def test_the_probe_words_come_from_the_group_itself():
    variants = ["Realtor.com", "Realtor.com MVIP", "Market VIP via Opcity BLA"]
    assert disc.stems(variants) == ["Realtor", "Market"]
    assert disc.hints(variants) == ["realtor", "market", "market vip"]


async def test_discovery_finds_the_bare_realtor_string_and_settings_sees_it(fake):
    fake.people = [
        *[person(i, source="Realtor.com", created=f"2026-03-0{i}T17:00:00Z") for i in (1, 2, 3)],
        *[person(i, source="Realtor", created=f"2026-03-1{i}T17:00:00Z") for i in (4, 5)],
        person(6, source="Realtor", created="2026-01-10T17:00:00Z"),               # before the program
        person(7, source="Realtor.com MVIP", created="2026-04-01T17:00:00Z"),
        *[person(i, source="Zillow", created=f"2026-04-0{i - 6}T17:00:00Z") for i in (8, 9)],
    ]
    ws = await _workspace(f"disc-{uuid.uuid4().hex[:6]}", owner=True)
    async with SessionLocal() as s:
        for ext, origin in (("7", "Realtor.com MVIP"), ("8", "Zillow")):
            s.add(Lead(tenant_id=ws["tenant_id"], business_id=ws["business_id"], external_id=ext,
                       origin=origin, created_at_src=dt.date(2026, 4, 1)))
        await s.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        r = await c.post("/api/v1/businesses/brokerage/lead-sources/realtor_com/fub-sources/check",
                         headers=ws["headers"])
        assert r.status_code == 200, r.text
        r = await c.get("/api/v1/businesses/brokerage/lead-sources/realtor_com/fub-sources",
                        headers=ws["headers"])
    view = r.json()
    rows = {x["source"]: x for x in view["sources"]}
    assert rows["Realtor.com"]["fub_count"] == 3 and rows["Realtor.com"]["in_group"] and rows["Realtor.com"]["is_variant"]
    assert rows["Realtor"]["fub_count"] == 2 and not rows["Realtor"]["in_group"] and rows["Realtor"]["via"] == "first word"
    assert rows["Realtor.com MVIP"]["synced_count"] == 1 and rows["Realtor.com MVIP"]["in_group"]
    assert "Zillow" not in rows and view["other_sources"] == 1
    assert view["searches"][0]["q"] == "realtor" and view["searches"][0]["total"] == 6
    assert view["fub"]["state"] == "connected" and view["checked_at"]
    probes = [p for p in fake.calls_to("/people") if p.get("limit") == "1"]
    assert probes and all(p["createdAfter"] == "2026-02-01 07:00:00" for p in probes)
    assert all(p["includeTrash"] == "true" for p in probes)          # the same people the walk reads


def test_a_probed_word_nobody_uses_is_not_suggested_but_an_unused_variant_is_shown():
    g = LeadSourceGroup(key="g", variants=["Realtor.com", "Realtor MVIP"], match_mode="contains",
                        program_start=dt.date(2026, 2, 1))
    found = {"at": "2026-11-11T00:00:00+00:00", "program_start": "2026-02-01", "searches": [],
             "sources": [{"source": "Realtor.com", "fub_count": 9, "via": "variant"},
                         {"source": "Realtor MVIP", "fub_count": 0, "via": "variant"},
                         {"source": "Realtor", "fub_count": 0, "via": "first word"}]}
    rows = {r["source"]: r for r in disc.view(g, found, {"Realtor.com": 9})["sources"]}
    assert set(rows) == {"Realtor.com", "Realtor MVIP"} and rows["Realtor MVIP"]["is_variant"]


async def test_settings_says_where_fub_is_connected_when_it_is_not_this_business(fake):
    ws = await _workspace(f"else-{uuid.uuid4().hex[:6]}", owner=True)
    async with SessionLocal() as s:
        other = Business(tenant_id=ws["tenant_id"], key="sympli", name="Sympli Mortgage", tag="Mortgage")
        s.add(other)
        await s.flush()
        integ = await s.get(Integration, ws["integration_id"])
        integ.business_id = other.id
        await s.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        r = await c.get("/api/v1/businesses/brokerage/lead-sources/realtor_com/fub-sources", headers=ws["headers"])
        assert r.json()["fub"] == {**r.json()["fub"], "state": "elsewhere", "elsewhere": "Sympli Mortgage"}
        r = await c.post("/api/v1/businesses/brokerage/lead-sources/realtor_com/fub-sources/check",
                         headers=ws["headers"])
    assert r.status_code == 400 and "Sympli Mortgage" in r.json()["detail"]


def test_a_spelling_that_differs_only_in_case_is_kept_for_the_fub_query():
    out = ls.clean_variants(["Market VIP via Opcity BLA", " market vip via opcity bla ", "Market VIP via Opcity BLA", ""],
                            "contains")
    assert out == ["Market VIP via Opcity BLA", "market vip via opcity bla"]
