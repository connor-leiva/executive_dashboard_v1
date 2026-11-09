"""SkySlope Phases 4 to 6, and the sample that demonstrates them.

What has to hold: SkySlope's statuses map as the spec and Connor's decisions say; its files light the
existing production tiles without touching a Sisu business; GCI is a stated gap, never $0; and the
sample is invented, marked on every surface, never mixed with a real key, and removable."""
import datetime as dt
import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.integrations import skyslope
from app.main import app
from app.models import (Agent, AuditLog, Business, Domain, Integration, SkyslopeListing, SkyslopeSale, Tenant,
                        Transaction, User)
from app.security import enc, hash_pw, make_token
from app.services import skyslope_normalize as norm
from app.services import skyslope_project, skyslope_purge, skyslope_sample, sync as sync_module, sync_jobs
from app.services.integrations_view import build_integrations_view
from app.services.lineage import metric_detail
from app.services.metrics import build_dashboard
from app.services.skyslope_metrics import overview
from scripts import skyslope_sample as loader
from tests.skyslope_fake import fake  # noqa: F401

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    from app.seed import seed
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token, slug):
    return {"Authorization": f"Bearer {token}", "x-tenant-host": f"{slug}.testhost"}


async def _workspace(role="owner", tab_access=None) -> dict:
    slug = f"sd{uuid.uuid4().hex[:10]}"
    async with SessionLocal() as s:
        t = Tenant(slug=slug, name=slug, status="active", plan="portfolio", config={})
        s.add(t)
        await s.flush()
        s.add(Domain(tenant_id=t.id, hostname=f"{slug}.testhost", is_primary=True))
        b = Business(tenant_id=t.id, key="main", name="Main", tag="Business", sort_order=0, kind="real_estate",
                     accent="#61835E", ink="#4D6A4D")
        s.add(b)
        await s.flush()
        u = User(tenant_id=t.id, email=f"{slug}@example.test", name="Owner", role=role, tab_access=tab_access,
                 password_hash=hash_pw("password123"), status="active")
        s.add(u)
        await s.commit()
        return {"tenant": t.id, "business": b.id, "slug": slug, "user": u.id, "token": make_token(u.id, t.id, 0)}


async def _load(w) -> int:
    return await loader._run(w["slug"], None, False, False)


async def _count(model, w, *where):
    async with SessionLocal() as s:
        return (await s.execute(select(func.count()).select_from(model).where(
            model.tenant_id == w["tenant"], *where))).scalar_one()


# ── Phase 4: the status map ───────────────────────────────────────────────────────────────────
def test_the_map_is_the_specs_table_with_connors_two_decisions():
    assert norm.normalize("listing", "Active") == "active"
    assert norm.normalize("listing", "Incomplete") == "incomplete"          # decision 5: not active
    assert norm.normalize("listing", "Transaction") == "converted"          # counted as its sale
    assert norm.normalize("listing", "Canceled / Pend") == "canceled"
    assert norm.normalize("listing", "Archived") == "canceled"
    assert norm.normalize("sale", "Pending") == "under_contract"
    assert norm.normalize("sale", "Pre-contract") == "pre_contract"         # decision 4: not under contract
    assert norm.normalize("sale", "Expired") == "past_due"
    assert norm.normalize("sale", "Archived") == "closed"
    assert norm.normalize("sale", "  closed ") == "closed"
    # Never guessed: a string nobody mapped counts for nothing, visibly.
    assert norm.normalize("sale", "Unknown") == "unmapped"
    assert norm.normalize("listing", "Pending") == "unmapped"
    assert norm.normalize("sale", None) == "unmapped"


def test_a_workspace_can_reverse_a_decision_but_not_invent_a_state():
    mapping = norm.status_map({norm.CONFIG_KEY: {"sale": {"Pre-contract": "under_contract", "Weird": "bogus"},
                                                 "listing": {"Incomplete": "active"}}})
    assert norm.normalize("sale", "pre-contract", mapping) == "under_contract"
    assert norm.normalize("listing", "INCOMPLETE", mapping) == "active"
    assert norm.normalize("sale", "Weird", mapping) == "unmapped"


def test_past_due_is_skyslopes_expired_or_a_missed_closing_date():
    today = dt.date(2026, 11, 5)
    yesterday = dt.datetime(2026, 11, 4, tzinfo=dt.timezone.utc)
    assert norm.is_past_due("past_due", None, today)
    assert norm.is_past_due("under_contract", yesterday, today)
    assert not norm.is_past_due("under_contract", yesterday + dt.timedelta(days=2), today)
    assert not norm.is_past_due("closed", yesterday, today)


# ── the sample itself ─────────────────────────────────────────────────────────────────────────
def test_the_sample_is_stable_and_invented():
    anchor = dt.date(2026, 11, 5)
    assert skyslope_sample.build(anchor) == skyslope_sample.build(anchor), "a second sync must change nothing"
    files, users = skyslope_sample.build(anchor)
    assert all(u["email"].split("_")[0].endswith("@example.com") for u in users)
    banned = {"sellers", "buyers", "streetAddress", "streetNumber", "email", "customFields", "checklist"}
    for f in files:
        assert not banned & set(f), f"the sample carries {banned & set(f)}"
        assert not banned & set(f.get("property") or {})
    month = [f for f in files if f["objectType"] == "sale" and f["status"] == "Closed"
             and f["actualClosingDate"][:7] == anchor.isoformat()[:7]]
    assert month, "month to date, the default view, must not be empty on the day of a demo"


async def test_loading_it_never_reaches_the_network_and_a_second_sync_changes_nothing(monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("the sample opened a SkySlope client")

    monkeypatch.setattr(skyslope, "client", no_network)
    w = await _workspace()
    assert not skyslope.configured(), "the sample must work before the application credentials exist"
    assert await _load(w) == 0
    assert await _load(w) == 0
    async with SessionLocal() as s:
        from app.models import SyncRun
        runs = (await s.execute(select(SyncRun).where(SyncRun.tenant_id == w["tenant"]))).scalars().all()
    stats = sorted((r.stats["skyslope"] for r in runs), key=lambda x: x["rows_changed"])
    assert len(stats) == 2 and stats[0]["rows_changed"] == 0 and stats[0]["users_changed"] == 0
    assert stats[1]["rows_changed"] > 100


async def test_the_loader_will_not_share_a_workspace_with_a_real_key():
    w = await _workspace()
    async with SessionLocal() as s:
        s.add(Integration(tenant_id=w["tenant"], provider="skyslope", business_id=w["business"],
                          status="disconnected", config={"key_hint": "abcd"}))
        await s.commit()
    assert await _load(w) == 1
    assert await _count(SkyslopeSale, w) == 0


# ── the projection into the tiles ─────────────────────────────────────────────────────────────
async def test_the_tiles_light_up_with_gci_as_a_gap_and_a_neutral_source():
    w = await _workspace()
    await _load(w)
    async with SessionLocal() as s:
        for period in ("mtd", "ytd"):
            dash = await build_dashboard(s, w["tenant"], period)
            area = dash.areas["main"]
            tiles = {t.key: t for t in area.ops}
            # The Portfolio strip names the same neutral source, and a GCI that is a gap has none.
            strip = {c.key: c for c in dash.scorecards if c.key}
            assert strip["units_closed"].source == strip["pending"].source == "Transactions"
            assert strip["gci"].source is None
            assert all(c.source != "SkySlope" for c in dash.scorecards)
            assert area.transactions_source == "skyslope" and area.sample is True
            assert area.sources == ["QuickBooks", "Transactions: sample"]
            assert "SkySlope" not in " ".join(area.sources)
            assert tiles["gci"].value == "—" and "definition" in tiles["gci"].sub
            assert int(tiles["units_closed"].value) > 0
            assert int(tiles["active_listings"].value) == 12
            assert area.funnel is None


async def test_a_sisu_business_is_exactly_as_it_was():
    async with SessionLocal() as s:
        spring = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        ulrg = (await build_dashboard(s, spring.id, "ytd")).areas["ulrg"]
    assert ulrg.transactions_source is None and ulrg.sample is False
    assert ulrg.sources == ["QuickBooks", "Sisu", "Follow Up Boss"]
    assert next(t for t in ulrg.ops if t.key == "gci").value != "—"


async def test_the_projection_writes_no_contact_fields_no_gci_and_follows_the_mapping():
    w = await _workspace()
    await _load(w)
    async with SessionLocal() as s:
        txns = (await s.execute(select(Transaction).where(Transaction.tenant_id == w["tenant"]))).scalars().all()
        pre = (await s.execute(select(SkyslopeSale.sale_guid).where(
            SkyslopeSale.tenant_id == w["tenant"], SkyslopeSale.status == "pre_contract"))).scalars().all()
    assert txns and all(t.source == "skyslope" for t in txns)
    for t in txns:
        assert t.gci is None
        assert (t.address, t.buyer_name, t.buyer_email, t.buyer_email2, t.buyer_phone) == (None,) * 5
    by = {}
    for t in txns:
        by.setdefault(t.status, []).append(t)
    assert set(by) == {"active", "pending", "closed", "dead"}
    assert all(t.side == "sell" and t.listing_date for t in by["active"])
    assert all(t.close_date for t in by["closed"]) and not any(t.close_date for t in by["pending"])
    assert pre and not {t.external_id for t in txns} & set(pre), "pre-contract is not under contract"


async def test_skyslope_owns_the_deals_wherever_it_is_connected_unless_the_business_says_otherwise():
    """Connor, 2026-11-05: SkySlope is how the agents get paid, so where it is connected its deals are
    the record, Sisu or no Sisu. An explicit `production_source` still wins, and handing the business
    to Sisu takes SkySlope's rows out, because no reader filters by source."""
    w = await _workspace()
    await _load(w)
    async with SessionLocal() as s:
        s.add(Integration(tenant_id=w["tenant"], provider="sisu", business_id=w["business"], status="connected"))
        await s.commit()
        b = await s.get(Business, w["business"])
        assert await skyslope_project.production_source(s, w["tenant"], b) == "skyslope"
        await skyslope_project.project(s, w["tenant"])
        await s.commit()
    assert await _count(Transaction, w, Transaction.source == "skyslope") > 0
    async with SessionLocal() as s:
        b = await s.get(Business, w["business"])
        b.config = {"production_source": "sisu"}
        await s.commit()
        await skyslope_project.project(s, w["tenant"])
        await s.commit()
    assert await _count(Transaction, w, Transaction.source == "skyslope") == 0
    assert await _count(Agent, w, Agent.source == "skyslope") == 0


async def _sisu_rows_written(monkeypatch, w) -> list[dict]:
    """Run the Sisu writer against a stub feed and return the transaction rows it would upsert.
    `_upsert_many` is Postgres-only, so it is the seam."""
    wrote = []

    async def record(s, model, rows, keys, update):
        if model is Transaction:
            wrote.extend(rows)

    async def nothing(*a, **k):
        return []

    async def clients(*a, **k):
        deal = {"external_id": "c1", "status": "closed", "sale_price": 512000, "gci": 15360,
                "agent_commission": 12000, "contract_date": dt.date(2026, 10, 1), "close_date": dt.date(2026, 11, 2),
                "expected_close_date": dt.date(2026, 11, 2), "listing_date": dt.date(2026, 9, 1),
                "lead_date": dt.date(2026, 8, 1), "appt_set_date": dt.date(2026, 8, 5),
                "appt_met_date": dt.date(2026, 8, 7), "signed_date": dt.date(2026, 8, 9), "side": "sell",
                "agent_external_id": "a1"}
        return [deal], {"a1": {"external_id": "a1", "name": "A"}}

    async def zero(*a, **k):
        return 0

    monkeypatch.setattr(sync_module, "_upsert_many", record)
    monkeypatch.setattr(sync_module, "_sisu_creds", lambda integ: object())
    monkeypatch.setattr(sync_module.sisu, "get_team_vendors", nothing)
    monkeypatch.setattr(sync_module.sisu, "fetch_all_clients", clients)
    monkeypatch.setattr(sync_module.sisu, "enrich_commissions", zero)
    async with SessionLocal() as s:
        integ = Integration(tenant_id=w["tenant"], provider="sisu", business_id=w["business"], status="connected")
        await sync_module.sync_sisu(s, w["tenant"], integ)
    return wrote


async def test_sisu_is_split_into_activity_only_where_skyslope_is_enabled(monkeypatch):
    """Multi-tenant: a workspace without SkySlope gets Sisu's rows whole, exactly as before."""
    plain = await _workspace()
    [row] = await _sisu_rows_written(monkeypatch, plain)
    assert row["status"] == "closed" and row["sale_price"] == 512000 and row["gci"] == 15360

    sky = await _workspace()
    await _load(sky)
    [row] = await _sisu_rows_written(monkeypatch, sky)
    assert row["status"] == skyslope_project.ACTIVITY_STATUS
    assert all(row[f] is None for f in skyslope_project.DEAL_FIELDS), "a deal fact would be counted twice"
    assert (row["lead_date"], row["appt_set_date"], row["appt_met_date"], row["signed_date"]) == (
        dt.date(2026, 8, 1), dt.date(2026, 8, 5), dt.date(2026, 8, 7), dt.date(2026, 8, 9)), "the activity stays"
    from app.services.sync import _TXN_UPDATE_KEYS
    assert set(skyslope_project.DEAL_FIELDS) <= set(_TXN_UPDATE_KEYS), \
        "a Sisu sync after SkySlope leaves must be able to write every blanked field back"


async def test_one_page_counts_deals_from_skyslope_and_activity_from_sisu():
    """Sisu's existing rows on the business become activity the moment SkySlope syncs: its closing
    leaves the tiles, its leads and appointments stay in the funnel, and the team is counted once."""
    w = await _workspace()
    today = dt.date.today()
    async with SessionLocal() as s:
        s.add(Integration(tenant_id=w["tenant"], provider="sisu", business_id=w["business"], status="connected"))
        a = Agent(tenant_id=w["tenant"], business_id=w["business"], source="sisu", external_id="s1", name="Sisu Agent")
        s.add(a)
        await s.flush()
        s.add(Transaction(tenant_id=w["tenant"], business_id=w["business"], source="sisu", external_id="sisu-1",
                          status="closed", side="sell", sale_price=999000, gci=29970, agent_id=a.id,
                          contract_date=today, close_date=today, lead_date=today, appt_set_date=today))
        await s.commit()
    await _load(w)
    async with SessionLocal() as s:
        sisu = (await s.execute(select(Transaction).where(
            Transaction.tenant_id == w["tenant"], Transaction.source == "sisu"))).scalar_one()
        area = (await build_dashboard(s, w["tenant"], "mtd")).areas["main"]
        sky_agents = (await s.execute(select(func.count()).select_from(Agent).where(
            Agent.tenant_id == w["tenant"], Agent.source == "skyslope", Agent.is_active.is_(True)))).scalar_one()
        sky_closed = (await s.execute(select(func.count()).select_from(Transaction).where(
            Transaction.tenant_id == w["tenant"], Transaction.source == "skyslope", Transaction.status == "closed",
            Transaction.close_date >= today.replace(day=1), Transaction.close_date <= today))).scalar_one()
    assert sisu.status == "activity" and sisu.sale_price is None and sisu.appt_set_date == today
    tiles = {t.key: t for t in area.ops}
    assert int(tiles["units_closed"].value) == sky_closed, "Sisu's closing must not be added to SkySlope's"
    assert tiles["agents_producing"].sub == f"of {sky_agents}", "the same people, counted once"
    funnel = {r.label: r.v for r in area.funnel}
    assert funnel["Leads"] >= 1 and funnel["Appointments"] >= 1, "Sisu's activity still reaches the funnel"
    assert funnel["Closed"] == sky_closed
    assert area.funnel_source == "Sisu · Transactions"


async def test_disconnecting_skyslope_hands_the_deals_back_to_sisu(monkeypatch):
    synced = []

    async def record(tenant_id, integ_id, period="mtd"):
        synced.append(integ_id)

    monkeypatch.setattr(sync_jobs, "run_one_job", record)
    w = await _workspace()
    async with SessionLocal() as s:
        sisu = Integration(tenant_id=w["tenant"], provider="sisu", business_id=w["business"], status="connected")
        s.add(sisu)
        s.add(Transaction(tenant_id=w["tenant"], business_id=w["business"], source="sisu", external_id="sisu-1",
                          status="closed", sale_price=1, close_date=dt.date.today()))
        await s.commit()
        sisu_id = sisu.id
    await _load(w)
    async with SessionLocal() as s:
        sample = (await s.execute(select(Integration).where(
            Integration.tenant_id == w["tenant"], Integration.provider == "skyslope"))).scalar_one()
    async with _client() as c:
        r = await c.post(f"/api/v1/integrations/{sample.id}/disconnect", headers=_H(w["token"], w["slug"]))
    assert r.status_code == 200
    assert await _count(Transaction, w, Transaction.source == "skyslope") == 0
    assert synced == [sisu_id], "Sisu's next sync is what writes its deals back, so it runs at once"


async def test_the_drill_down_names_its_rows_and_never_links_into_sisu():
    w = await _workspace()
    await _load(w)
    async with SessionLocal() as s:
        for key in ("units_closed", "pending", "active_listings"):
            d = await metric_detail(s, w["tenant"], key, "ytd")
            assert d["source"] == "Transactions", key
            assert d["rows"], key
            for row in d["rows"]:
                assert row["source_url"] is None
                assert len(row["name"]) < 60 and "-" not in row["name"].split(" · ")[0][:8], row["name"]


# ── Phase 5: the panel's numbers ─────────────────────────────────────────────────────────────
async def test_the_panel_counts_by_the_rules_and_says_how_far_to_trust_them():
    w = await _workspace()
    await _load(w)
    today = dt.datetime.now(dt.timezone.utc).date()
    async with SessionLocal() as s:
        o = await overview(s, w["tenant"], w["business"], "ytd", today=today)
        listings = (await s.execute(select(SkyslopeListing).where(SkyslopeListing.tenant_id == w["tenant"]))).scalars().all()
    m = {x["key"]: x for x in o["metrics"]}
    assert o["sample"] is True and o["coverage"]["state"] == "full"
    assert {x["chip"] for x in o["metrics"]} == {"live"}
    assert o["gci"]["chip"] == "needs_definition"
    assert m["active_listings"]["count"] == sum(x.status == "active" for x in listings) == 12
    assert o["pre_contract"] == 2 and len(o["past_due"]) == 3
    assert o["not_producing"]["count"] >= 1 and "Rowan Price" not in o["not_producing"]["agents"], \
        "a deactivated agent has left, so is not 'not producing'"
    assert sum(a["closed"] for a in o["agents"]) == m["closed"]["count"], "a file is one unit, to its primary agent"
    assert o["unmapped_statuses"] == []


async def test_before_any_sync_nothing_claims_to_be_live():
    w = await _workspace()
    async with SessionLocal() as s:
        s.add(Integration(tenant_id=w["tenant"], provider="skyslope", business_id=w["business"], status="connected",
                          config={"label": skyslope_sample.LABEL, "sample": True}))
        await s.commit()
        o = await overview(s, w["tenant"], w["business"], "mtd")
    assert {x["chip"] for x in o["metrics"]} == {"needs_sync"}
    assert o["coverage"]["state"] == "unknown"


async def test_the_panel_endpoint_sits_behind_the_business_tab():
    w = await _workspace()
    await _load(w)
    async with _client() as c:
        ok = await c.get("/api/v1/businesses/main/skyslope?period=ytd", headers=_H(w["token"], w["slug"]))
    assert ok.status_code == 200 and ok.json()["sample"] is True
    async with SessionLocal() as s:
        m = User(tenant_id=w["tenant"], email=f"m{uuid.uuid4().hex[:6]}@example.test", name="M", role="member",
                 tab_access=["portfolio"], password_hash=hash_pw("password123"), status="active")
        s.add(m)
        await s.commit()
        token = make_token(m.id, w["tenant"], 0)
    async with _client() as c:
        denied = await c.get("/api/v1/businesses/main/skyslope", headers=_H(token, w["slug"]))
    assert denied.status_code == 403


# ── the sample never mixes with real data, and leaves when asked ──────────────────────────────
async def test_a_real_key_clears_the_sample_and_a_mistyped_one_does_not(fake, monkeypatch):
    async def no_sync(*a, **k):
        return None

    monkeypatch.setattr(sync_jobs, "run_one_job", no_sync)
    w = await _workspace()
    await _load(w)
    async with _client() as c:
        bad = await c.post("/api/v1/integrations", headers=_H(w["token"], w["slug"]),
                           json={"provider": "skyslope", "business_key": "main", "access_key": "key-1", "access_secret": "nope"})
    assert bad.status_code == 400
    assert await _count(SkyslopeSale, w) > 0, "a mistyped key must not cost the demo its data"
    async with _client() as c:
        good = await c.post("/api/v1/integrations", headers=_H(w["token"], w["slug"]),
                            json={"provider": "skyslope", "business_key": "main", "access_key": "key-1", "access_secret": "secret-1"})
    assert good.status_code == 200, good.text
    assert await _count(SkyslopeSale, w) == 0
    assert await _count(Transaction, w, Transaction.source == "skyslope") == 0
    async with SessionLocal() as s:
        rows = (await s.execute(select(Integration).where(Integration.tenant_id == w["tenant"]))).scalars().all()
        purged = (await s.execute(select(func.count()).select_from(AuditLog).where(
            AuditLog.tenant_id == w["tenant"], AuditLog.action == "skyslope.purged"))).scalar_one()
    assert [skyslope_sample.is_sample(r) for r in rows] == [False]
    assert purged == 1


async def test_the_sample_has_no_key_to_edit_and_disconnecting_it_removes_it():
    w = await _workspace()
    await _load(w)
    async with SessionLocal() as s:
        integ = (await s.execute(select(Integration).where(Integration.tenant_id == w["tenant"]))).scalar_one()
        view = await build_integrations_view(s, w["tenant"])
    sky = next(src for src in view.model_dump()["sources"] if src["provider"] == "skyslope")
    assert sky["tag"] == "Sample data"
    assert "edit" not in sky["entities"][0]["actions"]
    async with _client() as c:
        edit = await c.post("/api/v1/integrations", headers=_H(w["token"], w["slug"]),
                            json={"provider": "skyslope", "business_key": "main", "integration_id": str(integ.id)})
        assert edit.status_code in (400, 409)
        gone = await c.post(f"/api/v1/integrations/{integ.id}/disconnect", headers=_H(w["token"], w["slug"]))
    assert gone.status_code == 200 and gone.json()["purged"] is True
    assert await _count(SkyslopeSale, w) == 0 and await _count(Integration, w) == 0
    assert await _count(Transaction, w, Transaction.source == "skyslope") == 0


async def test_the_purge_takes_the_projected_rows_too():
    w = await _workspace()
    await _load(w)
    async with SessionLocal() as s:
        counts = await skyslope_purge.counts(s, w["tenant"])
        assert counts["transaction"] > 0 and counts["agent"] > 0
        await skyslope_purge.purge_tenant(s, w["tenant"])
        await s.commit()
        after = await skyslope_purge.counts(s, w["tenant"])
    assert set(after.values()) == {0}


async def test_the_assistant_is_told_the_numbers_are_invented():
    from app.services.assistant import _build_context
    w = await _workspace()
    await _load(w)
    async with SessionLocal() as s:
        user = await s.get(User, w["user"])
        ctx, _ = await _build_context(s, user, "ytd")
    assert "main" in ctx["data"]["transactions_detail"]
    assert "INVENTED SAMPLE DATA" in ctx["data"]["notice"]
