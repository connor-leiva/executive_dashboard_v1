"""SkySlope as a provider of the product: when it runs, who may start it, what Settings and the
operator console say about it, and that it can all be destroyed on request."""
import datetime as dt
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app import plans
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import (AuditLog, Base, Business, Domain, Integration, PlatformUser, SkyslopeAgentLink,
                        SkyslopeSale, SyncRun, Tenant, User)
from app.security import dec, enc, hash_pw, make_token
from app.services import fleet_health, jobs, skyslope_purge, skyslope_sync, sync as sync_module, sync_jobs, sync_policy
from app.services.integrations_view import build_integrations_view
from tests.skyslope_fake import fake, guid, listing, sale, user  # noqa: F401

TRANSPORT = ASGITransport(app=app)
UTC = dt.timezone.utc
OP_EMAIL = "skyslope-operator@platform.test"
OP_PASSWORD = "skyslope-operator-password-1"


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    from app.seed import seed
    await seed()
    async with SessionLocal() as s:
        if not (await s.execute(select(PlatformUser).where(PlatformUser.email == OP_EMAIL))).scalar_one_or_none():
            s.add(PlatformUser(email=OP_EMAIL, name="Operator", password_hash=hash_pw(OP_PASSWORD)))
            await s.commit()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token, slug=None):
    return {"Authorization": f"Bearer {token}", **({"x-tenant-host": f"{slug}.testhost"} if slug else {})}


async def _workspace(kind="real_estate", role="owner") -> dict:
    slug = f"sp{uuid.uuid4().hex[:10]}"
    async with SessionLocal() as s:
        t = Tenant(slug=slug, name=slug, status="active", plan="portfolio", config={})
        s.add(t)
        await s.flush()
        s.add(Domain(tenant_id=t.id, hostname=f"{slug}.testhost", is_primary=True))
        b = Business(tenant_id=t.id, key="main", name="Main", tag="Brokerage", sort_order=0, kind=kind)
        s.add(b)
        await s.flush()
        u = User(tenant_id=t.id, email=f"{slug}@example.test", name="Owner", role=role,
                 password_hash=hash_pw("password123"), status="active")
        s.add(u)
        await s.commit()
        return {"tenant": t.id, "business": b.id, "slug": slug, "token": make_token(u.id, t.id, 0)}


async def _key(w, key="key-1", secret="secret-1", status="connected", **over) -> uuid.UUID:
    async with SessionLocal() as s:
        integ = Integration(tenant_id=w["tenant"], provider="skyslope", business_id=w["business"], status=status,
                            access_token_enc=enc(json.dumps({"access_key": key, "access_secret": secret})),
                            config={"key_hint": key[-4:]}, **over)
        s.add(integ)
        await s.commit()
        return integ.id


async def _connect(w, **body):
    async with _client() as c:
        return await c.post("/api/v1/integrations", headers=_H(w["token"], w["slug"]),
                            json={"provider": "skyslope", "business_key": "main", **body})


async def _keys(w):
    async with SessionLocal() as s:
        return (await s.execute(select(Integration).where(
            Integration.tenant_id == w["tenant"], Integration.provider == "skyslope"))).scalars().all()


async def _no_first_sync(monkeypatch):
    ran = []

    async def record(tenant_id, integ_id, period="mtd"):
        ran.append(integ_id)

    monkeypatch.setattr(sync_jobs, "run_one_job", record)
    return ran


# ── the gate ──────────────────────────────────────────────────────────────────────────────────
def test_it_is_off_until_somebody_turns_it_on():
    """An unset env var means the default IS the live config, so the defaults are the promise."""
    from app.config import Settings
    defaults = {k: Settings.model_fields[k].default for k in ("SKYSLOPE_CLIENT_ID", "SKYSLOPE_CLIENT_SECRET", "SKYSLOPE_SCHEDULED_SYNC")}
    assert defaults == {"SKYSLOPE_CLIENT_ID": "", "SKYSLOPE_CLIENT_SECRET": "", "SKYSLOPE_SCHEDULED_SYNC": False}


async def test_a_scheduled_run_skips_it_and_an_admins_request_does_not(fake, monkeypatch):
    """The whole of SKYSLOPE-SPEC.md §5. `tick` calls run_all with no flag, so run_all's default is the
    gate, and it holds with polling allowed as well: then its own job runs it, not the tick."""
    w = await _workspace()
    await _key(w)
    fake.files = [sale()]

    async def nothing(*a, **k):
        return None

    import app.services.binder_extract as be
    import app.services.binder_reminders as br
    monkeypatch.setattr(be, "run_binder_extraction", nothing)
    monkeypatch.setattr(br, "run_reminders", nothing)

    for allowed in (False, True):
        monkeypatch.setattr(settings, "SKYSLOPE_SCHEDULED_SYNC", allowed)
        async with SessionLocal() as s:
            await sync_module.run_all(s, w["tenant"], "2026-11-01", "2026-11-30")
        assert fake.calls == [], f"run_all reached SkySlope with polling {'on' if allowed else 'off'}"

    monkeypatch.setattr(settings, "SKYSLOPE_SCHEDULED_SYNC", False)
    async with SessionLocal() as s:
        await sync_module.run_all(s, w["tenant"], "2026-11-01", "2026-11-30", requested=True)
    assert fake.exports(), "an admin pressing Sync has to reach it"


async def test_the_workspaces_sync_all_button_is_the_one_that_says_requested(monkeypatch):
    w = await _workspace()
    seen = []

    async def record(tenant_id, run_id, period="mtd", *, requested=False):
        seen.append(requested)

    monkeypatch.setattr(sync_jobs, "run_all_job", record)
    async with _client() as c:
        r = await c.post("/api/v1/sync/all", headers=_H(w["token"], w["slug"]))
    assert r.status_code == 200, r.text
    assert seen == [True]


def test_only_the_workspaces_own_button_may_set_requested():
    """Everything else that reaches run_all has to leave the safe default alone."""
    root = Path(__file__).resolve().parents[1] / "app"
    setters = [p.relative_to(root).as_posix() for p in root.rglob("*.py") if "requested=True" in p.read_text(encoding="utf-8")]
    assert setters == ["routers/integrations.py"], setters


def test_the_job_exists_only_when_polling_is_allowed(monkeypatch):
    from app.worker import build_scheduler

    def registered():
        return {j.func.__name__ for j in build_scheduler().get_jobs()}

    monkeypatch.setattr(settings, "SKYSLOPE_SCHEDULED_SYNC", False)
    assert "skyslope_tick" not in registered()
    assert not next(e for e in jobs.catalog() if e["job"] == "skyslope_tick")["enabled"]

    monkeypatch.setattr(settings, "SKYSLOPE_SCHEDULED_SYNC", True)
    assert "skyslope_tick" in registered()
    entry = next(e for e in jobs.catalog() if e["job"] == "skyslope_tick")
    assert entry["enabled"] and entry["every_minutes"] == settings.SKYSLOPE_SYNC_INTERVAL_MINUTES
    assert registered() == {e["job"] for e in jobs.catalog() if e["enabled"]}


async def test_the_job_skips_a_paused_workspace_and_survives_a_failing_key(fake, monkeypatch):
    from app import worker
    healthy, broken, paused = await _workspace(), await _workspace(), await _workspace()
    await _key(healthy)
    await _key(broken, secret="wrong")
    await _key(paused)
    async with SessionLocal() as s:
        t = await s.get(Tenant, paused["tenant"])
        t.config = {"syncs_frozen": True}
        await s.commit()
    fake.files = [sale()]
    monkeypatch.setattr(settings, "SKYSLOPE_SCHEDULED_SYNC", True)
    await worker.skyslope_tick()
    async with SessionLocal() as s:
        count = lambda w: s.execute(select(func.count()).select_from(SkyslopeSale).where(SkyslopeSale.tenant_id == w["tenant"]))
        assert (await count(healthy)).scalar_one() == 1
        assert (await count(paused)).scalar_one() == 0, "frozen means nothing is pulled"
        assert (await s.execute(select(Integration.status).where(Integration.tenant_id == broken["tenant"]))).scalar_one() == "error"


# ── nothing raises a false alarm ──────────────────────────────────────────────────────────────
def test_a_manual_source_is_never_stale_and_a_scheduled_one_can_be(monkeypatch):
    now = dt.datetime.now(UTC)
    long_ago = now - dt.timedelta(days=30)
    monkeypatch.setattr(settings, "SKYSLOPE_SCHEDULED_SYNC", False)
    assert sync_policy.is_manual("skyslope")
    assert not fleet_health.is_stale("skyslope", long_ago, now)
    assert not fleet_health.is_stale("skyslope", None, now)
    assert fleet_health.is_stale("sisu", long_ago, now), "the exemption is for this source alone"
    monkeypatch.setattr(settings, "SKYSLOPE_SCHEDULED_SYNC", True)
    assert not sync_policy.is_manual("skyslope")
    assert fleet_health.is_stale("skyslope", long_ago, now)
    assert not fleet_health.is_stale("skyslope", now - dt.timedelta(minutes=10), now)


async def test_settings_does_not_call_a_manual_source_late(fake):
    w = await _workspace()
    await _key(w, last_synced_at=dt.datetime.now(UTC) - dt.timedelta(days=9))
    async with SessionLocal() as s:
        s.add(SyncRun(tenant_id=w["tenant"], provider="skyslope", status="ok",
                      finished_at=dt.datetime.now(UTC) - dt.timedelta(days=9), stats={"records": 12, "seconds": 2.0}))
        await s.commit()
        view = await build_integrations_view(s, w["tenant"])
    src = next(x for x in view.sources if x.provider == "skyslope")
    assert src.status == "ok"
    assert "missed" not in (src.last_run or "")
    assert "on request" in src.meta, "a source with no schedule says so"
    assert view.needs_attention == 0


async def test_a_manual_source_keeps_a_workspace_out_of_watch(fake):
    w = await _workspace()
    await _key(w, last_synced_at=dt.datetime.now(UTC) - dt.timedelta(days=9))
    now = dt.datetime.now(UTC)
    async with SessionLocal() as s:
        facts = await fleet_health.gather(s, await s.get(Tenant, w["tenant"]), now)
    assert [src.provider for src in facts.sources] == ["skyslope"], "or this proves nothing"
    assert not [sig for sig in fleet_health.signals(facts, now) if "stale" in sig.key]


async def test_with_polling_on_a_late_source_does_put_a_workspace_in_watch(fake, monkeypatch):
    """Green for the right reason: the test above has to be able to fail."""
    monkeypatch.setattr(settings, "SKYSLOPE_SCHEDULED_SYNC", True)
    w = await _workspace()
    await _key(w, last_synced_at=dt.datetime.now(UTC) - dt.timedelta(days=9))
    now = dt.datetime.now(UTC)
    async with SessionLocal() as s:
        facts = await fleet_health.gather(s, await s.get(Tenant, w["tenant"]), now)
    assert [sig for sig in fleet_health.signals(facts, now) if "stale" in sig.key]


# ── Settings ──────────────────────────────────────────────────────────────────────────────────
async def test_without_the_application_credentials_it_is_not_offered_at_all(monkeypatch):
    """No licence to connect under, and a card that names the vendor is not ours to show. This is what
    makes deploying this code a no-op for every customer."""
    monkeypatch.setattr(settings, "SKYSLOPE_CLIENT_ID", "")
    monkeypatch.setattr(settings, "SKYSLOPE_CLIENT_SECRET", "")
    w = await _workspace()
    async with SessionLocal() as s:
        view = await build_integrations_view(s, w["tenant"])
    assert "skyslope" not in [x.provider for x in view.sources]
    r = await _connect(w, access_key="key-1", access_secret="secret-1")
    assert r.status_code == 409 and "not available" in r.json()["detail"]
    assert await _keys(w) == []


async def test_a_connection_that_exists_stays_visible_if_the_credentials_go_away(monkeypatch):
    monkeypatch.setattr(settings, "SKYSLOPE_CLIENT_ID", "")
    w = await _workspace()
    await _key(w)
    async with SessionLocal() as s:
        view = await build_integrations_view(s, w["tenant"])
    assert [x.status for x in view.sources if x.provider == "skyslope"] == ["ok"]


async def test_with_them_a_brokerage_is_offered_it_and_others_are_told_what_is_missing(fake):
    brokerage, programme = await _workspace(), await _workspace(kind="membership")
    async with SessionLocal() as s:
        one = next(x for x in (await build_integrations_view(s, brokerage["tenant"])).sources if x.provider == "skyslope")
        other = next(x for x in (await build_integrations_view(s, programme["tenant"])).sources if x.provider == "skyslope")
    assert one.status == "disconnected" and one.business_key == "main" and one.needs_kind is None
    assert one.multi_entity and one.connect_provider == "skyslope" and one.category == "Production"
    assert other.needs_kind == "real_estate"


async def test_several_keys_show_as_rows_and_the_page_gets_no_sync_state(fake):
    w = await _workspace()
    first = await _key(w, key="abc-one1")
    second = await _key(w, key="abc-two2", status="error", last_error="SkySlope answered 401 on /auth/login")
    async with SessionLocal() as s:
        integ = await s.get(Integration, first)
        integ.config = {**integ.config, skyslope_sync.STATE_KEY: {"watermark": "a-private-watermark"}}
        await s.commit()
    await _key(w, key="abc-old3", status="disconnected")
    async with SessionLocal() as s:
        view = await build_integrations_view(s, w["tenant"])
    src = next(x for x in view.sources if x.provider == "skyslope")
    assert src.status == "attention" and src.tag == "2 keys"
    assert src.integration_id is None, "a Connect on this card is always another key"
    rows = {e.integration_id: e for e in src.entities}
    assert set(rows) == {str(first), str(second)}, "a disconnected key is not shown"
    assert rows[str(first)].business_name == "Access key ····one1"
    assert rows[str(first)].actions == ["sync", "edit", "disconnect"]
    assert rows[str(second)].actions == ["reconnect", "edit", "disconnect"]
    assert rows[str(second)].detail == "SkySlope answered 401 on /auth/login"
    assert (src.entity_singular, src.entity_plural) == ("access key", "Access keys")
    assert [a.integration_id for a in view.alerts if a.provider == "skyslope"] == [str(second)]
    dumped = src.model_dump_json()
    assert "abc-one1" not in dumped and "abc-two2" not in dumped, "a full key must not reach the page"
    assert skyslope_sync.STATE_KEY not in dumped and "a-private-watermark" not in dumped


# ── connecting ────────────────────────────────────────────────────────────────────────────────
async def test_connecting_checks_the_key_stores_it_encrypted_and_starts_the_first_sync(fake, monkeypatch):
    ran = await _no_first_sync(monkeypatch)
    w = await _workspace()
    r = await _connect(w, access_key=" key-1 ", access_secret=" secret-1 ", label="Office admin")
    assert r.status_code == 200, r.text
    assert "secret" not in r.text
    rows = await _keys(w)
    assert len(rows) == 1 and rows[0].status == "connected"
    assert "secret-1" not in rows[0].access_token_enc and "key-1" not in rows[0].access_token_enc
    assert json.loads(dec(rows[0].access_token_enc)) == {"access_key": "key-1", "access_secret": "secret-1"}
    assert rows[0].config["label"] == "Office admin" and rows[0].config["key_hint"] == "ey-1"
    assert ran == [rows[0].id]
    assert fake.logins == 1, "checked once, before anything was stored"


async def test_a_refused_key_is_refused_there_and_then_and_nothing_is_kept(fake, monkeypatch):
    ran = await _no_first_sync(monkeypatch)
    w = await _workspace()
    r = await _connect(w, access_key="key-1", access_secret="wrong")
    assert r.status_code == 400 and "refused" in r.json()["detail"]
    assert "wrong" not in r.text
    assert await _keys(w) == [] and ran == []


async def test_skyslope_being_down_does_not_blame_the_key(fake, monkeypatch):
    ran = await _no_first_sync(monkeypatch)
    w = await _workspace()

    async def down(_creds):
        raise skyslope_sync.skyslope.SkySlopeError(503, path="/auth/login")

    monkeypatch.setattr(skyslope_sync.skyslope, "check_credentials", down)
    r = await _connect(w, access_key="key-1", access_secret="secret-1")
    assert r.status_code == 200, r.text
    assert len(await _keys(w)) == 1 and len(ran) == 1


async def test_a_second_key_makes_a_second_connection_and_the_same_key_does_not(fake, monkeypatch):
    await _no_first_sync(monkeypatch)
    w = await _workspace()
    assert (await _connect(w, access_key="key-1", access_secret="secret-1")).status_code == 200
    assert (await _connect(w, access_key="key-2", access_secret="secret-2")).status_code == 200
    assert len(await _keys(w)) == 2, "this is what the generic connect path's one-row lookup would raise on"
    assert (await _connect(w, access_key="key-1", access_secret="secret-1")).status_code == 200
    assert len(await _keys(w)) == 2, "pasting a key again must not duplicate it"


async def test_editing_with_blanks_keeps_the_key_and_its_place_in_the_sync(fake, monkeypatch):
    ran = await _no_first_sync(monkeypatch)
    w = await _workspace()
    integ_id = await _key(w)
    async with SessionLocal() as s:
        integ = await s.get(Integration, integ_id)
        integ.config = {**integ.config, skyslope_sync.STATE_KEY: {"watermark": "w", "backfilled_at": "x"}}
        await s.commit()
    r = await _connect(w, integration_id=str(integ_id), label="Renamed")
    assert r.status_code == 200, r.text
    row = (await _keys(w))[0]
    assert row.config["label"] == "Renamed" and row.config[skyslope_sync.STATE_KEY]["watermark"] == "w"
    assert json.loads(dec(row.access_token_enc))["access_key"] == "key-1"
    assert ran == [], "a rename is not a reason to pull anything"


async def test_a_different_key_starts_again_rather_than_inheriting_a_watermark(fake, monkeypatch):
    """Otherwise it would skip the backfill and the new key's history would never arrive."""
    await _no_first_sync(monkeypatch)
    w = await _workspace()
    integ_id = await _key(w)
    async with SessionLocal() as s:
        integ = await s.get(Integration, integ_id)
        integ.config = {**integ.config, skyslope_sync.STATE_KEY: {"watermark": "w", "backfilled_at": "x"}}
        await s.commit()
    r = await _connect(w, integration_id=str(integ_id), access_key="key-2", access_secret="secret-2")
    assert r.status_code == 200, r.text
    assert skyslope_sync.STATE_KEY not in (await _keys(w))[0].config


async def test_reconnecting_reuses_a_disconnected_row_without_its_old_state(fake, monkeypatch):
    await _no_first_sync(monkeypatch)
    w = await _workspace()
    integ_id = await _key(w)
    async with SessionLocal() as s:
        integ = await s.get(Integration, integ_id)
        integ.config = {**integ.config, skyslope_sync.STATE_KEY: {"watermark": "w", "backfilled_at": "x"}}
        await s.commit()
    async with _client() as c:
        r = await c.post(f"/api/v1/integrations/{integ_id}/disconnect", headers=_H(w["token"], w["slug"]))
    assert r.status_code == 200, r.text
    assert (await _keys(w))[0].access_token_enc is None
    assert (await _connect(w, access_key="key-2", access_secret="secret-2")).status_code == 200
    rows = await _keys(w)
    assert len(rows) == 1 and rows[0].id == integ_id and rows[0].status == "connected"
    assert skyslope_sync.STATE_KEY not in rows[0].config


async def test_an_edit_cannot_reach_another_workspaces_key(fake, monkeypatch):
    await _no_first_sync(monkeypatch)
    mine, theirs = await _workspace(), await _workspace()
    their_key = await _key(theirs)
    r = await _connect(mine, integration_id=str(their_key), label="Mine now")
    assert r.status_code == 404
    assert "label" not in (await _keys(theirs))[0].config


async def test_a_member_cannot_connect_one(fake, monkeypatch):
    await _no_first_sync(monkeypatch)
    w = await _workspace(role="member")
    r = await _connect(w, access_key="key-1", access_secret="secret-1")
    assert r.status_code == 403
    assert fake.calls == [], "not even the check reached SkySlope"


async def test_a_frozen_workspace_can_connect_but_nothing_is_pulled(fake, monkeypatch):
    ran = await _no_first_sync(monkeypatch)
    w = await _workspace()
    async with SessionLocal() as s:
        (await s.get(Tenant, w["tenant"])).config = {"syncs_frozen": True}
        await s.commit()
    assert (await _connect(w, access_key="key-1", access_secret="secret-1")).status_code == 200
    assert ran == []


@pytest.mark.parametrize("tier", sorted(plans.PLANS))
def test_it_is_on_every_plan_that_has_sisu(tier):
    assert ("skyslope" in plans.PLANS[tier]["sources"]) == ("sisu" in plans.PLANS[tier]["sources"])


def test_every_provider_a_workspace_can_connect_is_on_some_plan():
    """`allows_source` is checked before anything else on connect, so a provider on no plan answers
    402 on every tier, and the offered list and the plans table are in different files.

    KNOWN_GAP is meta_ads: on no plan today, which is a live bug outside this work and is flagged to
    Connor rather than fixed here, because which tier gets ads is a product decision. Asserting that
    the gap is still a gap makes whoever fixes it take it off this list."""
    from app.services.integrations_view import CONNECTABLE_KIND
    KNOWN_GAP = {"meta_ads"}
    every = set().union(*(p["sources"] for p in plans.PLANS.values()))
    assert set(CONNECTABLE_KIND) - every == KNOWN_GAP


# ── the operator console ──────────────────────────────────────────────────────────────────────
async def _op_token():
    async with _client() as c:
        r = await c.post("/api/v1/platform/login", json={"email": OP_EMAIL, "password": OP_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def test_an_operator_cannot_start_a_pull_while_it_is_manual(fake, monkeypatch):
    """The licence allows a pull while a user of the workspace is present, and an operator is not one."""
    ran = await _no_first_sync(monkeypatch)
    w = await _workspace()
    integ_id = await _key(w)
    token = await _op_token()
    url = f"/api/v1/platform/tenants/{w['slug']}/sources/{integ_id}/sync"
    async with _client() as c:
        r = await c.post(url, headers=_H(token))
        assert r.status_code == 409 and "admin of this workspace" in r.json()["detail"]
        assert ran == []
        monkeypatch.setattr(settings, "SKYSLOPE_SCHEDULED_SYNC", True)
        r = await c.post(url, headers=_H(token))
        assert r.status_code == 202, r.text
    assert ran == [integ_id]


async def test_an_operators_full_sync_does_not_count_it(fake, monkeypatch):
    w = await _workspace()
    await _key(w)
    token = await _op_token()
    async with _client() as c:
        r = await c.post(f"/api/v1/platform/tenants/{w['slug']}/sync", headers=_H(token))
    assert r.status_code == 409 and "Nothing is connected" in r.json()["detail"]


async def test_the_operator_console_names_it_and_reads_its_state_correctly(fake):
    w = await _workspace()
    await _key(w, last_synced_at=dt.datetime.now(UTC) - dt.timedelta(days=9))
    token = await _op_token()
    async with _client() as c:
        r = await c.get(f"/api/v1/platform/tenants/{w['slug']}/sources", headers=_H(token))
    assert r.status_code == 200, r.text
    row = next(x for x in r.json()["sources"] if x["provider"] == "skyslope")
    assert row["provider_name"] == "SkySlope" and row["state"] == "healthy"
    assert fleet_health.provider_name("skyslope") == "SkySlope"


# ── purge ─────────────────────────────────────────────────────────────────────────────────────
async def _fill(w, fake):
    fake.files = [sale(commissionSplits=[{"agentGuid": guid(), "percentage": 100.0}]), listing()]
    fake.users = [user()]
    integ_id = await _key(w)
    async with SessionLocal() as s:
        integ = await s.get(Integration, integ_id)
        await sync_module._sync_integration(s, w["tenant"], integ, "2026-11-01", "2026-11-30")
        s.add(SkyslopeAgentLink(tenant_id=w["tenant"], user_guid=guid(), agent_id=None))
        await s.commit()
    return integ_id


def test_the_purge_covers_every_skyslope_table_by_deriving_them():
    """A hand-kept list is only as current as the last person who remembered it, and somebody signs a
    certificate on the strength of this one."""
    in_schema = {name for name in Base.metadata.tables if "skyslope" in name}
    assert in_schema == {t.name for t in skyslope_purge.tables()}
    assert in_schema == {"skyslope_user", "skyslope_listing", "skyslope_sale", "skyslope_file_agent", "skyslope_agent_link"}


async def test_a_purge_removes_everything_for_that_workspace_only_and_keeps_a_record(fake):
    w, other = await _workspace(), await _workspace()
    await _fill(w, fake)
    await _fill(other, fake)

    async with SessionLocal() as s:
        before = await skyslope_purge.counts(s, w["tenant"])
        assert before["skyslope_sale"] == 1 and before["skyslope_listing"] == 1 and before["skyslope_user"] == 1
        assert before["skyslope_file_agent"] >= 3 and before["skyslope_agent_link"] == 1
        assert before["integration"] == 1 and before["sync_run"] >= 1

        done = await skyslope_purge.purge_tenant(s, w["tenant"])
        await s.commit()
        assert done["removed"] == before

        after = await skyslope_purge.counts(s, w["tenant"])
        assert set(after.values()) == {0}, after
        untouched = await skyslope_purge.counts(s, other["tenant"])
        assert untouched["skyslope_sale"] == 1 and untouched["integration"] == 1

        record = (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == w["tenant"], AuditLog.action == "skyslope.purged"))).scalar_one()
        assert record.detail["removed"]["skyslope_sale"] == 1 and record.detail["at"]
        assert record.actor_label == "purge command"
        tenant = await s.get(Tenant, w["tenant"])
        assert tenant is not None, "the workspace itself is not SkySlope's data"


async def test_a_workspace_can_reconnect_from_nothing_after_a_purge(fake, monkeypatch):
    await _no_first_sync(monkeypatch)
    w = await _workspace()
    await _fill(w, fake)
    async with SessionLocal() as s:
        await skyslope_purge.purge_tenant(s, w["tenant"])
        await s.commit()
    assert (await _connect(w, access_key="key-1", access_secret="secret-1")).status_code == 200
    row = (await _keys(w))[0]
    assert skyslope_sync.STATE_KEY not in row.config


async def test_the_purge_does_not_touch_other_providers(fake):
    w = await _workspace()
    await _fill(w, fake)
    async with SessionLocal() as s:
        s.add(Integration(tenant_id=w["tenant"], provider="sisu", business_id=w["business"], status="connected"))
        s.add(SyncRun(tenant_id=w["tenant"], provider="sisu", status="ok"))
        await s.commit()
        await skyslope_purge.purge_tenant(s, w["tenant"])
        await s.commit()
        assert (await s.execute(select(func.count()).select_from(Integration).where(
            Integration.tenant_id == w["tenant"]))).scalar_one() == 1
        assert (await s.execute(select(func.count()).select_from(SyncRun).where(
            SyncRun.tenant_id == w["tenant"]))).scalar_one() == 1


def test_the_purge_command_needs_to_be_told_who():
    backend = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "-m", "scripts.skyslope_purge"], cwd=backend, capture_output=True, text=True)
    assert r.returncode != 0 and "--tenant" in r.stderr
