"""Phase 2 gate: every background job leaves the other tenant alone.

The defects these cover were all invisible with one tenant, and none of them fails loudly —
a mis-adopted Recall bot produces no error, just one customer's client conversation playing
behind another customer's Watch link. So each test here sets up TWO tenants and asserts on
tenant B while acting as tenant A.
"""
import datetime as dt
import uuid

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import SessionLocal
from app.models import (Business, CallTranscript, Launch, SalesCall, Tenant)
from app.seed import seed
from app.services import call_chapters as ch
from app.services import recall
from app.services.launch import DEFAULT_PAYMENT_PLAN_MAP, DEFAULT_STAGE_MAP
from app.services.provisioning import provision_tenant

U = dt.timezone.utc
ROOM_A = "https://us06web.zoom.us/j/1111111111?pwd=a"
ROOM_B = "https://us06web.zoom.us/j/2222222222?pwd=b"
NOW = dt.datetime(2026, 8, 19, 12, tzinfo=U)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _launch_for(tenant_id, business_id, name):
    async with SessionLocal() as s:
        L = Launch(tenant_id=tenant_id, business_id=business_id, name=name,
                   program="beCollective", window_start=dt.date(2026, 8, 11),
                   window_end=dt.date(2026, 9, 12), goal_arr=1_000_000,
                   ticket_pif=12000, ticket_plan=14000, price_map={}, goal_basis="seats",
                   seat_goal=100, stage_map=DEFAULT_STAGE_MAP,
                   payment_plan_map=DEFAULT_PAYMENT_PLAN_MAP, default_tz="America/Denver",
                   pipeline_match="be collective experience #1 sales")
        s.add(L)
        await s.commit()
        return L.id


async def _two_tenants(*, b_records=True, b_legacy=None):
    """Spring (tenant A, recording on) plus a freshly provisioned tenant B."""
    async with SessionLocal() as s:
        await s.execute(delete(CallTranscript))
        await s.execute(delete(SalesCall))
        biz_a = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        a_tid, a_bid = biz_a.tenant_id, biz_a.id
        ta = await s.get(Tenant, a_tid)
        ta.config = {**(ta.config or {}), "recall_enabled": True,
                     "recall_legacy_adopt_before": "2099-01-01"}
        await s.execute(delete(Launch).where(Launch.business_id == a_bid))

        existing = (await s.execute(select(Tenant).where(Tenant.slug == "isoco"))).scalar_one_or_none()
        if existing is None:
            await s.commit()
            async with SessionLocal() as s2:
                r = await provision_tenant(s2, slug="isoco", name="Iso Co",
                                           owner_email="owner@isoco.test",
                                           hostname="isoco.localhost", seed_catalogs=False)
                b_tid = r.tenant_id
        else:
            b_tid = existing.id
            await s.commit()

    async with SessionLocal() as s:
        tb = await s.get(Tenant, b_tid)
        cfg = {"recall_enabled": True} if b_records else {}
        if b_legacy:
            cfg["recall_legacy_adopt_before"] = b_legacy
        tb.config = cfg
        biz_b = (await s.execute(select(Business).where(Business.tenant_id == b_tid))).scalars().first()
        b_bid = biz_b.id
        await s.execute(delete(Launch).where(Launch.business_id == b_bid))
        await s.commit()

    return (a_tid, await _launch_for(a_tid, a_bid, "A")), (b_tid, await _launch_for(b_tid, b_bid, "B"))


def _call(tid, lid, name, *, url, mins=8, bot=None, status=None):
    return SalesCall(tenant_id=tid, launch_id=lid, opportunity_id=name, booking_id=name,
                     contact_name=name, meeting_url=url, is_current=True,
                     recall_bot_id=bot, recording_status=status,
                     call_time_utc=NOW + dt.timedelta(minutes=mins))


def _mock_recall(monkeypatch, bots, created=None):
    """Point the bot-list endpoint at `bots` and record every create."""
    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"results": bots}

    async def fake_create(client, url, join_at):
        if created is not None:
            created.append(url)
        return f"bot-created-{len(created or [1])}", ""

    monkeypatch.setattr(httpx.AsyncClient, "get", lambda self, url, **kw: _resp(FakeResp()))
    monkeypatch.setattr(recall, "create_bot", fake_create)


async def _resp(r):
    return r


# ── the exclusion set must be GLOBAL ──────────────────────────────────────────────────
async def test_a_bot_another_tenant_already_owns_can_never_be_adopted(monkeypatch):
    """The defect: `taken` — the set of bots some call already owns — was scoped to
    `candidates[0].tenant_id`, one arbitrary tenant. Every OTHER tenant's bots therefore
    looked free. Narrowing an exclusion set by tenant removes protection; it must be global.
    """
    (a_tid, a_lid), (b_tid, b_lid) = await _two_tenants()
    monkeypatch.setattr(settings, "RECALL_API_KEY", "k")
    monkeypatch.setattr(settings, "RECALL_LEAD_MINUTES", 5)
    monkeypatch.setattr(settings, "RECALL_TICK_MINUTES", 5)

    async with SessionLocal() as s:
        # Tenant A already owns bot-shared. Tenant B has an unlinked call in the SAME room at
        # the same time — the worst case, and the one a shared workspace makes reachable.
        s.add(_call(a_tid, a_lid, "A-owner", url=ROOM_A, bot="bot-shared", status="done"))
        s.add(_call(b_tid, b_lid, "B-thief", url=ROOM_A))
        await s.commit()

    created = []
    _mock_recall(monkeypatch, [{"id": "bot-shared", "meeting_url": ROOM_A,
                                "join_at": (NOW + dt.timedelta(minutes=3)).isoformat()}], created)
    async with SessionLocal() as s:
        await recall.schedule_due_bots(s, b_tid, now=NOW)

    async with SessionLocal() as s:
        rows = {c.contact_name: c for c in (await s.execute(select(SalesCall))).scalars()}
    assert rows["A-owner"].recall_bot_id == "bot-shared"      # untouched
    assert rows["B-thief"].recall_bot_id != "bot-shared", \
        "tenant B adopted a bot that tenant A already owned"


async def test_a_pass_for_one_tenant_does_not_touch_the_others_calls(monkeypatch):
    """The candidate query had no tenant predicate, so a single pass wrote to — and booked
    billable bots for — calls belonging to whoever else happened to be in the window."""
    (a_tid, a_lid), (b_tid, b_lid) = await _two_tenants()
    monkeypatch.setattr(settings, "RECALL_API_KEY", "k")
    monkeypatch.setattr(settings, "RECALL_LEAD_MINUTES", 5)
    monkeypatch.setattr(settings, "RECALL_TICK_MINUTES", 5)

    async with SessionLocal() as s:
        s.add(_call(a_tid, a_lid, "A-waiting", url=ROOM_A))
        s.add(_call(b_tid, b_lid, "B-waiting", url=ROOM_B))
        await s.commit()

    created = []
    _mock_recall(monkeypatch, [], created)
    async with SessionLocal() as s:
        stat = await recall.schedule_due_bots(s, b_tid, now=NOW)

    async with SessionLocal() as s:
        rows = {c.contact_name: c for c in (await s.execute(select(SalesCall))).scalars()}
    assert rows["B-waiting"].recall_bot_id is not None
    assert rows["A-waiting"].recall_bot_id is None, "the other tenant's call was booked a bot"
    assert len(created) == 1, "a bot was billed for a tenant that did not ask"
    assert stat.get("candidates") == 1, f"stat counted other tenants' calls: {stat}"


# ── recording is opt-in, and fails closed ─────────────────────────────────────────────
async def test_a_tenant_that_has_not_opted_in_gets_no_bots(monkeypatch):
    """Before this gate, the only check was a global API key — so the day a new tenant's GHL
    produced calls with an Appointment Link, an attendee-visible bot joined their clients'
    conversations and stored verbatim transcripts, with nobody there having consented."""
    (a_tid, a_lid), (b_tid, b_lid) = await _two_tenants(b_records=False)
    monkeypatch.setattr(settings, "RECALL_API_KEY", "k")

    async with SessionLocal() as s:
        s.add(_call(b_tid, b_lid, "B-unconsented", url=ROOM_B))
        await s.commit()

    created = []
    _mock_recall(monkeypatch, [], created)
    async with SessionLocal() as s:
        assert await recall.schedule_due_bots(s, b_tid, now=NOW) == {}
    async with SessionLocal() as s:
        row = (await s.execute(select(SalesCall).where(
            SalesCall.contact_name == "B-unconsented"))).scalar_one()
    assert row.recall_bot_id is None
    assert created == []


async def test_time_only_adoption_needs_the_tenants_own_legacy_window(monkeypatch):
    """The real cross-tenant vector. meeting_url comes from GHL's Appointment Link, so a
    tenant that has not mapped that field has it NULL on EVERY call — which put every one of
    their calls on the time-only matching path against the whole shared workspace. That path
    is now open only inside a window the tenant explicitly sets, so it fails closed."""
    (a_tid, a_lid), (b_tid, b_lid) = await _two_tenants(b_legacy=None)
    monkeypatch.setattr(settings, "RECALL_API_KEY", "k")
    monkeypatch.setattr(settings, "RECALL_LEAD_MINUTES", 5)
    monkeypatch.setattr(settings, "RECALL_TICK_MINUTES", 5)

    async with SessionLocal() as s:
        s.add(_call(b_tid, b_lid, "B-no-url", url=None))
        await s.commit()

    stray = [{"id": "bot-stray", "meeting_url": ROOM_A,
              "join_at": (NOW + dt.timedelta(minutes=3)).isoformat()}]
    _mock_recall(monkeypatch, stray, [])
    async with SessionLocal() as s:
        await recall.schedule_due_bots(s, b_tid, now=NOW)
    async with SessionLocal() as s:
        row = (await s.execute(select(SalesCall).where(
            SalesCall.contact_name == "B-no-url"))).scalar_one()
    assert row.recall_bot_id is None, "a URL-less call matched a bot on time alone"

    # With a window that covers the call, the historical path still works — that is what the
    # existing backfill bots depend on.
    async with SessionLocal() as s:
        tb = await s.get(Tenant, b_tid)
        tb.config = {**(tb.config or {}), "recall_legacy_adopt_before": "2099-01-01"}
        await s.commit()
    async with SessionLocal() as s:
        await recall.schedule_due_bots(s, b_tid, now=NOW)
    async with SessionLocal() as s:
        row = (await s.execute(select(SalesCall).where(
            SalesCall.contact_name == "B-no-url"))).scalar_one()
    assert row.recall_bot_id == "bot-stray"


# ── the storage layer is the backstop ─────────────────────────────────────────────────
async def test_two_calls_can_never_share_a_bot_id():
    """apply_bot_status resolves the webhook's bot with .first(), so a duplicate meant one
    client's Watch link could play another's conversation. Now unique by construction — in
    the same tenant and across tenants."""
    (a_tid, a_lid), (b_tid, b_lid) = await _two_tenants()
    async with SessionLocal() as s:
        s.add(_call(a_tid, a_lid, "first", url=ROOM_A, bot="bot-one"))
        await s.commit()
    for tid, lid, label in ((a_tid, a_lid, "same-tenant"), (b_tid, b_lid, "other-tenant")):
        async with SessionLocal() as s:
            s.add(_call(tid, lid, f"dupe-{label}", url=ROOM_A, bot="bot-one"))
            with pytest.raises(IntegrityError):
                await s.commit()


# ── batches are shared fairly, and a stuck row stops blocking ─────────────────────────
async def test_one_tenants_transcript_backlog_cannot_starve_another(monkeypatch):
    """The batch was global and unordered, so whoever had the biggest backlog took the whole
    per-tick budget. Scoped per tenant, each gets its own."""
    (a_tid, a_lid), (b_tid, b_lid) = await _two_tenants()
    monkeypatch.setattr(settings, "RECALL_API_KEY", "k")
    monkeypatch.setattr(settings, "RECALL_TRANSCRIPT_BATCH", 2)

    async with SessionLocal() as s:
        for i in range(4):
            s.add(_call(a_tid, a_lid, f"A{i}", url=ROOM_A, bot=f"a-bot-{i}", status="done"))
        s.add(_call(b_tid, b_lid, "B0", url=ROOM_B, bot="b-bot-0", status="done"))
        await s.commit()

    async def fake_fetch(bot_id):
        return {"segments": [{"start": 0, "text": "hi", "speaker": "A"}],
                "text": "hi", "speakers": {}, "duration_s": 60}
    monkeypatch.setattr(recall, "fetch_transcript", fake_fetch)

    async with SessionLocal() as s:
        await recall.store_transcripts(s, a_tid, now=NOW)      # A's turn: takes its 2
        stat_b = await recall.store_transcripts(s, b_tid, now=NOW)
    assert stat_b.get("transcripts_stored") == 1, \
        f"tenant B was starved by tenant A's backlog: {stat_b}"

    async with SessionLocal() as s:
        got = {t.recall_bot_id for t in (await s.execute(select(CallTranscript))).scalars()}
    assert "b-bot-0" in got


async def test_a_transcript_that_never_arrives_stops_holding_a_batch_slot(monkeypatch):
    """The permanent block, and it predates multi-tenancy: the batch selected on 'has no
    transcript yet' with no record of having TRIED, so a row whose fetch never succeeds held a
    slot on every tick forever. Per-tenant batching does not fix that — the block is per-row."""
    (a_tid, a_lid), _ = await _two_tenants()
    monkeypatch.setattr(settings, "RECALL_API_KEY", "k")
    monkeypatch.setattr(settings, "RECALL_TRANSCRIPT_BATCH", 5)
    monkeypatch.setattr(recall, "TRANSCRIPT_MAX_ATTEMPTS", 3)

    async with SessionLocal() as s:
        s.add(_call(a_tid, a_lid, "never-ready", url=ROOM_A, bot="stuck-bot", status="done"))
        await s.commit()

    tries = 0

    async def fake_fetch(bot_id):
        nonlocal tries
        tries += 1
        return None                                   # never ready

    monkeypatch.setattr(recall, "fetch_transcript", fake_fetch)
    for _ in range(6):
        async with SessionLocal() as s:
            await recall.store_transcripts(s, a_tid, now=NOW)
    assert tries == 3, f"a hopeless row was retried forever ({tries} fetches)"

    async with SessionLocal() as s:
        row = (await s.execute(select(SalesCall).where(
            SalesCall.contact_name == "never-ready"))).scalar_one()
    assert row.transcript_attempts == 3


async def test_a_transcript_the_model_keeps_failing_on_stops_being_re_billed(monkeypatch):
    """Chaptering left chapters_at NULL on failure by design, so with no attempt counter a
    failing transcript was re-sent to Anthropic every 15 minutes indefinitely — the only
    unbounded spend loop in the module, and it existed with one tenant."""
    (a_tid, a_lid), _ = await _two_tenants()
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(ch, "CHAPTER_MAX_ATTEMPTS", 2)

    async with SessionLocal() as s:
        sc = _call(a_tid, a_lid, "chapter-me", url=ROOM_A, bot="ch-bot", status="done")
        s.add(sc)
        await s.flush()
        s.add(CallTranscript(tenant_id=a_tid, sales_call_id=sc.id, recall_bot_id="ch-bot",
                             segments=[{"start": i * 20, "speaker": "A", "text": f"t{i}"}
                                       for i in range(40)],
                             text="t", speakers={}, duration_s=800))
        await s.commit()

    calls = 0

    async def fake_generate(segments, duration_s):
        nonlocal calls
        calls += 1
        return None                                   # the model keeps failing

    monkeypatch.setattr(ch, "generate", fake_generate)
    for _ in range(5):
        async with SessionLocal() as s:
            await ch.generate_pending(s, a_tid, now=NOW)
    assert calls == 2, f"a failing transcript kept being billed ({calls} model calls)"


# ── the sync tick isolates a failing tenant ───────────────────────────────────────────
async def test_one_tenants_sync_failure_does_not_starve_the_rest(monkeypatch):
    """tick() shared ONE session across every tenant with no try/except, while its four
    sibling jobs already isolated each one. An exception escaping run_all therefore skipped
    every tenant later in the loop, silently, on every tick — and a failed flush poisoned the
    shared session so the survivors could not commit either."""
    from app import worker

    await _two_tenants()
    seen, boom = [], {"done": False}

    async def fake_run_all(s, tenant_id, start, end):
        seen.append(tenant_id)
        if not boom["done"]:
            boom["done"] = True
            raise RuntimeError("this tenant's source is down")

    monkeypatch.setattr(worker, "run_all", fake_run_all)
    async with SessionLocal() as s:
        total = len((await s.execute(select(Tenant.id))).scalars().all())

    await worker.tick()
    assert len(seen) == total, \
        f"one tenant's failure stopped the loop: {len(seen)} of {total} were attempted"
