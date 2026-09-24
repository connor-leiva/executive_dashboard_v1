"""ULRG Recruiting — writing back to GoHighLevel (RECRUITING-SPEC §5, Phase 4).

This is the first code in the product that writes to a customer's CRM and sends messages to real
people, so the tests here are about what must NOT happen at least as much as what must.

Phase 4's list, in §9: idempotency, the gate matrix, DND and quiet-hours refusals, 429 → queued →
retried, a stuck send failing rather than resending, a frozen workspace, request bodies matching
§5.4, and the guard that keeps every write in one module.
"""
import datetime as dt
import pathlib
import re
import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    Business, Integration, RecruitingAction, RecruitingCandidate, RecruitingQueueItem,
    RecruitingSeat, Tenant, User,
)
from app.seed import seed
from app.services import recruiting_actions as A


# ── the guard: one module may write ─────────────────────────────────────────────────────────

def test_only_the_outbox_writes_to_ghl():
    """THE BLAST-RADIUS TEST.

    `ghl_request` takes a method, so any module could POST with it. Only one may: a write here
    is somebody else's recruit receiving a text message, and "we all agreed only the outbox
    writes" is not a control. If a second module ever needs to write, it goes through
    recruiting_actions -- or this test is changed deliberately, in a diff somebody reads.
    """
    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    allowed = {"recruiting_actions.py"}
    offenders = {}
    for path in sorted(app_dir.rglob("*.py")):
        if path.name in allowed:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        code = "\n".join(l for l in text.splitlines() if not l.strip().startswith(("#", "*")))
        for m in re.finditer(r'ghl_request\(\s*["\'](\w+)["\']', code):
            if m.group(1).upper() not in ("GET", "HEAD"):
                offenders.setdefault(path.name, []).append(m.group(1))
    assert not offenders, f"a write to GHL outside the outbox: {offenders}"


def test_the_ghl_client_still_has_no_verbs_of_its_own():
    """integrations/ghl.py is the transport. It must not grow convenience writers -- the moment
    it has `send_sms`, the gates and the outbox become optional."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "integrations" / "ghl.py"
           ).read_text(encoding="utf-8")
    body = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    for verb in (".post(", ".put(", ".patch(", ".delete("):
        assert verb not in body, f"integrations/ghl.py calls {verb} directly"


# ── fixtures ────────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _setup(s, *, platform=True, workspace=True, seat_on=True, dry=False, frozen=False):
    """A workspace with every gate in a stated position."""
    biz = (await s.execute(select(Business).where(Business.key == "ulrg"))).scalar_one()
    tenant = await s.get(Tenant, biz.tenant_id)
    cfg = dict(tenant.config or {})
    cfg.pop("syncs_frozen", None)
    if frozen:
        cfg["syncs_frozen"] = True
    tenant.config = cfg

    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == biz.tenant_id,
        Integration.provider == "ghl_recruiting"))).scalars().first()
    if integ is None:
        integ = Integration(tenant_id=biz.tenant_id, provider="ghl_recruiting",
                            business_id=biz.id, status="connected")
        s.add(integ)
    integ.config = {"location_id": "loc", "pipeline_id": "pipe", "writeback_enabled": workspace,
                    "dry_run": dry, "quiet_hours": {"start": 8, "end": 21},
                    "location_timezone": "America/Denver"}

    user = (await s.execute(select(User).where(
        User.tenant_id == biz.tenant_id, User.email == "wb@example.test"))).scalars().first()
    if user is None:
        user = User(tenant_id=biz.tenant_id, email="wb@example.test", name="Wanda Writer",
                    role="member", password_hash="x")
        s.add(user)
        await s.flush()
    seat = (await s.execute(select(RecruitingSeat).where(
        RecruitingSeat.tenant_id == biz.tenant_id,
        RecruitingSeat.display_name == "Wanda Writer"))).scalars().first()
    if seat is None:
        seat = RecruitingSeat(tenant_id=biz.tenant_id, business_id=biz.id, role="team_leader",
                              display_name="Wanda Writer")
        s.add(seat)
    seat.user_id, seat.writeback_enabled, seat.active = user.id, seat_on, True
    seat.from_number = "+18015550142"

    cand = (await s.execute(select(RecruitingCandidate).where(
        RecruitingCandidate.tenant_id == biz.tenant_id,
        RecruitingCandidate.opportunity_id == "opp_wb"))).scalars().first()
    if cand is None:
        cand = RecruitingCandidate(tenant_id=biz.tenant_id, business_id=biz.id,
                                   opportunity_id="opp_wb")
        s.add(cand)
    cand.contact_id, cand.name, cand.dnd = "contact_wb", "Brandon Reyes", {}
    cand.stage_group, cand.status = "Offer out", "open"
    await s.flush()
    await s.commit()
    return biz.tenant_id, integ, user, seat, cand


def _body(kind="send_sms", **over):
    out = {"idempotency_key": uuid.uuid4().hex, "kind": kind,
           "payload": {"message": "Hi Brandon, still interested?"}}
    out.update(over)
    return out


# ── the gate matrix ─────────────────────────────────────────────────────────────────────────

async def test_every_gate_refuses_on_its_own_and_names_itself(monkeypatch):
    """Four gates (§5.2). Each must refuse ALONE, and say which one it is -- a dead button that
    cannot explain itself becomes a support ticket instead of a setting somebody changes."""
    from app.config import settings

    async with SessionLocal() as s:
        t, integ, user, seat, cand = await _setup(s)

        # 1. the platform flag. It defaults False, which is the shipped state.
        monkeypatch.setattr(settings, "RECRUITING_WRITEBACK_ENABLED", False)
        state = await A.writeback_state(s, t, integ, seat)
        assert state["open"] is False and state["gate"] == "platform"

        monkeypatch.setattr(settings, "RECRUITING_WRITEBACK_ENABLED", True)
        # 2. the workspace flag.
        integ.config = {**integ.config, "writeback_enabled": False}
        assert (await A.writeback_state(s, t, integ, seat))["gate"] == "workspace"
        integ.config = {**integ.config, "writeback_enabled": True}
        # 3. the seat.
        seat.writeback_enabled = False
        assert (await A.writeback_state(s, t, integ, seat))["gate"] == "seat"
        seat.writeback_enabled = True
        # ...and no seat at all is its own answer, not a crash.
        assert (await A.writeback_state(s, t, integ, None))["gate"] == "seat"
        # 4. frozen. Freezing is how a compromised credential stops being used, and a write is
        # exactly what it is meant to stop.
        tenant = await s.get(Tenant, t)
        tenant.config = {**(tenant.config or {}), "syncs_frozen": True}
        assert (await A.writeback_state(s, t, integ, seat))["gate"] == "frozen"
        tenant.config = {k: v for k, v in (tenant.config or {}).items() if k != "syncs_frozen"}

        assert (await A.writeback_state(s, t, integ, seat))["open"] is True
        await s.rollback()


async def test_a_frozen_workspace_refuses_the_write_itself(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "RECRUITING_WRITEBACK_ENABLED", True)
    async with SessionLocal() as s:
        t, _, user, _, cand = await _setup(s, frozen=True)
        with pytest.raises(A.WriteRefused) as e:
            await A.submit(s, t, user, _body(candidate_id=str(cand.id)))
        assert e.value.code == 409 and e.value.gate == "frozen"


# ── compliance ──────────────────────────────────────────────────────────────────────────────

async def test_an_opt_out_is_refused_and_recorded(monkeypatch):
    """And there is no override. Not a flag, not an admin bypass -- the refusal is the feature."""
    from app.config import settings
    monkeypatch.setattr(settings, "RECRUITING_WRITEBACK_ENABLED", True)
    async with SessionLocal() as s:
        t, _, user, _, cand = await _setup(s)
        cand.dnd = {"sms": True, "email": False}
        await s.commit()

        with pytest.raises(A.WriteRefused) as e:
            await A.submit(s, t, user, _body(candidate_id=str(cand.id)))
        assert e.value.code == 422 and "opted out of texts" in e.value.reason
        assert "Brandon" in e.value.reason, "the refusal should name the person, not an id"

        # The attempt is RECORDED as refused. A refusal that leaves no trace is indistinguishable
        # from nobody having tried.
        row = (await s.execute(select(RecruitingAction).where(
            RecruitingAction.tenant_id == t,
            RecruitingAction.status == "refused"))).scalars().first()
        assert row is not None and row.kind == "send_sms"

        # Email is a different channel and is NOT blocked by an SMS opt-out.
        assert A.check_compliance("send_email", cand, await _integ(s, t),
                                  dt.datetime.now(dt.timezone.utc)) is not None


async def _integ(s, t):
    return (await s.execute(select(Integration).where(
        Integration.tenant_id == t, Integration.provider == "ghl_recruiting"))).scalars().first()


async def test_quiet_hours_hold_the_send_rather_than_refusing_it():
    """Refusing at 11pm just moves the same text to somebody's personal phone at the same hour,
    with none of the logging. Holding it until 8am is the honest control."""
    async with SessionLocal() as s:
        t, integ, _, _, cand = await _setup(s)
        tz = A._tz_for(integ)

        at_2am = dt.datetime(2026, 9, 24, 2, 0, tzinfo=tz)
        held = A.check_compliance("send_sms", cand, integ, at_2am)
        assert "scheduled_at" in held
        assert held["scheduled_at"].astimezone(tz).hour == 8
        assert held["scheduled_at"].astimezone(tz).date() == at_2am.date(), "same morning"

        at_10pm = dt.datetime(2026, 9, 24, 22, 0, tzinfo=tz)
        later = A.check_compliance("send_sms", cand, integ, at_10pm)
        assert later["scheduled_at"].astimezone(tz).date() == at_10pm.date() + dt.timedelta(days=1)

        # Inside the window it simply proceeds.
        assert A.check_compliance("send_sms", cand, integ,
                                  dt.datetime(2026, 9, 24, 10, 0, tzinfo=tz)) == {}
        # A note is not a communication with the recruit, so it is never held.
        assert A.check_compliance("add_note", cand, integ, at_2am) == {}


def test_quiet_hours_cannot_be_widened_by_a_stored_value():
    """Clamped where they are USED, not only where they are saved. A value written by a migration
    or an older build must not be able to text somebody at 6am."""
    fake = type("I", (), {"config": {"quiet_hours": {"start": 5, "end": 23}}})()
    assert A.quiet_hours(fake) == (8, 21)
    fake.config = {"quiet_hours": {"start": "nonsense"}}
    assert A.quiet_hours(fake) == (8, 21)


# ── idempotency and dry run ─────────────────────────────────────────────────────────────────

async def test_the_same_key_sends_once(monkeypatch):
    """A double-click, a retried request and a refresh all carry the same key. GHL has no
    idempotency for a message, so this table is the only thing between them and two texts."""
    from app.config import settings
    monkeypatch.setattr(settings, "RECRUITING_WRITEBACK_ENABLED", True)
    async with SessionLocal() as s:
        t, _, user, _, cand = await _setup(s, dry=True)
        body = _body(candidate_id=str(cand.id))

        first = await A.submit(s, t, user, body)
        second = await A.submit(s, t, user, dict(body))        # the same key, again
        rows = (await s.execute(select(RecruitingAction).where(
            RecruitingAction.tenant_id == t,
            RecruitingAction.idempotency_key == body["idempotency_key"]))).scalars().all()

    assert len(rows) == 1, "the same key made two rows"
    assert first["id"] == second["id"]


async def test_dry_run_records_the_exact_request_and_sends_nothing(monkeypatch):
    """The week before anything is switched on: the whole pipeline runs, the body is stored, and
    GHL is never called. If this ever calls out, the test fails on a network error rather than
    passing quietly."""
    from app.config import settings
    monkeypatch.setattr(settings, "RECRUITING_WRITEBACK_ENABLED", True)

    called = []

    async def _boom(*a, **kw):
        called.append(a)
        raise AssertionError("dry run called GHL")

    monkeypatch.setattr("app.integrations.ghl.ghl_request", _boom)

    async with SessionLocal() as s:
        t, _, user, _, cand = await _setup(s, dry=True)
        out = await A.submit(s, t, user, _body(candidate_id=str(cand.id)))
        row = (await s.execute(select(RecruitingAction).where(
            RecruitingAction.id == out["id"]))).scalars().first()

    assert out["status"] == "dry_run" and not called
    assert row.request["message"] == "Hi Brandon, still interested?"
    assert row.sent_at is None and row.ghl_ref is None


async def test_a_missing_key_is_refused_rather_than_generated(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "RECRUITING_WRITEBACK_ENABLED", True)
    async with SessionLocal() as s:
        t, _, user, _, cand = await _setup(s, dry=True)
        with pytest.raises(A.WriteRefused) as e:
            await A.submit(s, t, user, {"kind": "send_sms", "candidate_id": str(cand.id),
                                        "payload": {"message": "hi"}})
        assert e.value.code == 422
        # Generating one server-side would give at-least-once delivery of a text message, which
        # is precisely the guarantee nobody wants here.
        assert "idempotency" in e.value.reason.lower()


async def test_credentials_never_reach_the_stored_request(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "RECRUITING_WRITEBACK_ENABLED", True)
    async with SessionLocal() as s:
        t, _, user, _, cand = await _setup(s, dry=True)
        out = await A.submit(s, t, user, _body(
            candidate_id=str(cand.id),
            payload={"message": "hi", "token": "pit-secret", "Authorization": "Bearer nope"}))
        row = (await s.execute(select(RecruitingAction).where(
            RecruitingAction.id == out["id"]))).scalars().first()
    assert "token" not in row.request and "Authorization" not in row.request
    assert row.request["message"] == "hi"


# ── retry, and the send whose outcome is unknown ────────────────────────────────────────────

async def test_a_stuck_send_fails_and_is_never_resent(monkeypatch):
    """A row in `sending` for two minutes has an UNKNOWN outcome: the message may well have gone.
    Retrying would risk a second text to a recruit, which is worse than a missing one. Only a
    person can check."""
    from app.config import settings
    monkeypatch.setattr(settings, "RECRUITING_WRITEBACK_ENABLED", True)
    async with SessionLocal() as s:
        t, _, user, seat, cand = await _setup(s)
        row = RecruitingAction(
            tenant_id=t, idempotency_key=uuid.uuid4().hex, seat_id=seat.id, actor_user_id=user.id,
            candidate_id=cand.id, kind="send_sms", request={"message": "hi"}, status="sending",
            attempts=1, created_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5))
        s.add(row)
        await s.commit()
        row_id = row.id

        called = []
        monkeypatch.setattr("app.integrations.ghl.ghl_request",
                            lambda *a, **kw: called.append(a))
        out = await A.drain(s, t)
        again = (await s.execute(select(RecruitingAction).where(
            RecruitingAction.id == row_id))).scalars().first()

    assert out["stuck_failed"] >= 1
    assert again.status == "failed" and "Unknown outcome" in again.error
    assert not called, "a stuck send was retried"


def test_the_backoff_is_bounded():
    """Three attempts, then failed. An unbounded retry against somebody's CRM is a way to get a
    token rate-limited into uselessness."""
    assert A.MAX_ATTEMPTS == 3 and len(A.BACKOFF) == 3
    assert A.BACKOFF == (30, 120, 600)
    assert 429 in A.RETRY_STATUS and 500 in A.RETRY_STATUS
    # A 4xx that is not 429 is a request that will fail the same way forever.
    assert 400 not in A.RETRY_STATUS and 403 not in A.RETRY_STATUS and 422 not in A.RETRY_STATUS


def test_error_mapping_says_something_a_person_can_act_on():
    """§5.7. A bare "403" sends somebody to the docs for twenty minutes."""
    from app.integrations.ghl import GhlError

    def mapped(status, body=""):
        row = type("R", (), {"http_status": None, "error": None})()
        A._map_error(row, GhlError(status, body, path="/x"))
        return row.error

    assert "reconnected" in mapped(401)
    assert "scope" in mapped(403)
    assert "deleted" in mapped(404)
    assert "just taken" in mapped(409, "slot not available")
    assert "A2P 10DLC" in mapped(422, "number is not registered for a2p 10dlc")
    assert "retry" in mapped(429)
    assert "retry" in mapped(503)
