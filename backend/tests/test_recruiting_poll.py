"""ULRG Recruiting — the conversations poll (RECRUITING-SPEC §5.6, Phase 6a).

Most of this file is about tolerating a contract nobody has verified yet. V3 in the sourcing doc
— the conversation search parameters and the message `type` values — cannot be settled until the
probe runs, and HighLevel's own documentation shows `TYPE_SMS` in some places and bare `SMS` in
others, with the messages list sometimes flat and sometimes nested.

So the tests pin two things: every documented shape is accepted, and anything unrecognised is
COUNTED rather than dropped. A poll that silently ignores an unknown message type under-counts
somebody's dials forever and looks like it is working, which is the worst way for this to fail.
"""
import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    Business, Integration, RecruitingAction, RecruitingActivity, RecruitingCandidate,
)
from app.security import enc
from app.seed import seed
from app.services import recruiting_poll as P


# ── the shapes ──────────────────────────────────────────────────────────────────────────────

def test_every_documented_spelling_of_a_message_type_is_accepted():
    """`TYPE_SMS` and `SMS` are both in HighLevel's docs. Accepting one and not the other would
    produce a tab that worked on one location and was mysteriously empty on another."""
    for raw in ("TYPE_SMS", "SMS", "sms", "Text"):
        assert P.classify({"messageType": raw, "direction": "inbound"}) == ("sms_in", True)
    for raw in ("TYPE_CALL", "Call", "voicemail"):
        kind, inbound = P.classify({"type": raw, "direction": "outbound"})
        assert kind == "call_out" and inbound is False
    for raw in ("TYPE_EMAIL", "Email"):
        assert P.classify({"messageType": raw, "direction": "inbound"})[0] == "email_in"
    # `type` is read when `messageType` is absent, and the other way round.
    assert P.classify({"type": "SMS", "direction": "inbound"})[0] == "sms_in"


def test_inbound_is_recognised_however_it_is_spelled():
    for word in ("inbound", "in", "incoming", "received", "INBOUND"):
        assert P.classify({"messageType": "SMS", "direction": word})[1] is True
    for word in ("outbound", "out", ""):
        assert P.classify({"messageType": "SMS", "direction": word})[1] is False


def test_an_unknown_type_is_refused_rather_than_guessed():
    """A message mapped to the wrong kind becomes a dial that never happened. These numbers end
    up on a scorecard, so "I do not know" has to be representable."""
    assert P.classify({"messageType": "TYPE_WHATSAPP", "direction": "inbound"}) == (None, False)
    assert P.classify({"messageType": "", "direction": "inbound"}) == (None, False)
    assert P.classify({}) == (None, False)


def test_both_documented_message_list_shapes_are_read():
    assert len(P._messages_of({"messages": [{"id": 1}, {"id": 2}]})) == 2
    assert len(P._messages_of({"messages": {"messages": [{"id": 1}]}})) == 1
    for junk in ({"messages": None}, {}, {"messages": "nope"}, None):
        assert P._messages_of(junk) == []


# ── PII ─────────────────────────────────────────────────────────────────────────────────────

def test_a_summary_describes_a_message_and_never_quotes_it():
    """§5.5. What a recruit wrote back is not ours to keep, and a table of inbound bodies is a
    copy of somebody's inbox sitting in our database."""
    secret = "Please stop contacting me, this is my private number"
    for kind in ("sms_in", "email_in", "sms_out"):
        line = P._describe(kind, {"body": secret, "message": secret})
        assert secret not in line and "private" not in line
    assert P._describe("sms_in", {}) == "Text received"
    assert P._describe("email_out", {}) == "Email sent"
    # A call carries its LENGTH, because that is what separates a dial from a conversation (D9).
    assert P._describe("call_out", {"duration": 95}) == "Call · 1m 35s"
    assert P._describe("call_out", {"duration": 20}) == "Call · 20s"
    assert P._describe("call_out", {"duration": 0}) == "Call · no answer"


def test_timestamps_survive_every_format_ghl_sends():
    assert P._dtm("2026-09-24T09:00:00Z").tzinfo is not None
    assert P._dtm("2026-09-24T09:00:00-06:00").hour == 9
    assert P._dtm(1758700000000).year == 2025 or P._dtm(1758700000000).year == 2026
    assert P._dtm("1758700000000") is not None
    naive = P._dtm("2026-09-24T09:00:00")
    assert naive is not None and naive.tzinfo is not None, "a naive stamp must be made aware"
    assert P._dtm(None) is None and P._dtm("") is None and P._dtm("nonsense") is None


# ── the guards before it calls anything ─────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _ws(s, **cfg_over):
    biz = (await s.execute(select(Business).where(Business.key == "ulrg"))).scalar_one()
    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == biz.tenant_id,
        Integration.provider == "ghl_recruiting"))).scalars().first()
    if integ is None:
        integ = Integration(tenant_id=biz.tenant_id, provider="ghl_recruiting",
                            business_id=biz.id, status="connected")
        s.add(integ)
    base = {"location_id": "loc", "pipeline_id": "pipe",
            "synced_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    base.update(cfg_over)
    integ.config = base
    # A token, because the poll checks its credential BEFORE it loads anything -- which is the
    # right order (cheaper, and a missing token is the more fundamental problem) and is why a
    # fixture without one skips with "incomplete connection" and never reaches the assertions.
    integ.access_token_enc = enc("pit-test-token")
    await s.flush()
    await s.commit()
    return biz.tenant_id, biz, integ


async def test_it_does_not_poll_before_the_first_sync(monkeypatch):
    """Without candidates there is nothing to attach a message to, so every row fetched would be
    discarded after being paid for. Worse, it would burn the shared rate budget the sync needs."""
    called = []
    monkeypatch.setattr("app.integrations.ghl.ghl_json",
                        lambda *a, **kw: called.append(a))

    async with SessionLocal() as s:
        t, _, integ = await _ws(s)
        integ.config = {"location_id": "loc", "pipeline_id": "pipe"}   # never synced
        await s.commit()
        out = await P.poll_activity(s, t)
    assert out["skipped"] == "not synced yet" and not called

    async with SessionLocal() as s:
        t, _, integ = await _ws(s)
        integ.config = {"location_id": "loc"}                          # no pipeline chosen
        await s.commit()
        out = await P.poll_activity(s, t)
    assert out["skipped"] == "not configured" and not called


async def test_a_workspace_with_no_candidates_asks_ghl_nothing(monkeypatch):
    called = []
    monkeypatch.setattr("app.integrations.ghl.ghl_json", lambda *a, **kw: called.append(a))
    async with SessionLocal() as s:
        t, _, _ = await _ws(s)
        await s.execute(RecruitingCandidate.__table__.delete().where(
            RecruitingCandidate.tenant_id == t))
        await s.commit()
        out = await P.poll_activity(s, t)
    assert out["skipped"] == "no candidates" and not called


# ── ingesting ───────────────────────────────────────────────────────────────────────────────

async def _candidate(s, t, biz, contact_id="contact_poll"):
    cand = (await s.execute(select(RecruitingCandidate).where(
        RecruitingCandidate.tenant_id == t,
        RecruitingCandidate.opportunity_id == "opp_poll"))).scalars().first()
    if cand is None:
        cand = RecruitingCandidate(tenant_id=t, business_id=biz.id, opportunity_id="opp_poll")
        s.add(cand)
    cand.contact_id, cand.name = contact_id, "Reply Riley"
    await s.flush()
    await s.commit()
    return cand


def _fake_ghl(conversations, messages, events=None):
    """Stand in for GHL. Returns the right body for whichever path is asked for."""
    async def _json(method, path, **kw):
        if "/conversations/search" in path:
            return {"conversations": conversations}
        if "/conversations/" in path and path.endswith("/messages"):
            return {"messages": messages}
        if "/calendars/events" in path:
            return {"events": events or []}
        return {}
    return _json


async def test_a_reply_lands_and_moves_the_candidates_last_inbound(monkeypatch):
    """This is what makes `offer_out_stale` clear itself when somebody answers in GHL. No new
    rule code -- the rows simply start existing."""
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        t, biz, integ = await _ws(s, activity_cursor=(now - dt.timedelta(days=2)).isoformat())
        cand = await _candidate(s, t, biz)
        await s.execute(RecruitingActivity.__table__.delete().where(
            RecruitingActivity.tenant_id == t))
        await s.commit()

        monkeypatch.setattr("app.integrations.ghl.ghl_json", _fake_ghl(
            conversations=[{"id": "conv1", "contactId": "contact_poll",
                            "lastMessageDate": now.isoformat()}],
            messages=[{"id": "m1", "messageType": "TYPE_SMS", "direction": "inbound",
                       "dateAdded": (now - dt.timedelta(hours=1)).isoformat(),
                       "body": "Yes, still interested"}]))
        out = await P.poll_activity(s, t)

        rows = (await s.execute(select(RecruitingActivity).where(
            RecruitingActivity.tenant_id == t))).scalars().all()
        again = await s.get(RecruitingCandidate, cand.id)

    assert out["written"] == 1 and out["conversations"] == 1
    assert len(rows) == 1 and rows[0].kind == "sms_in" and rows[0].source == "ghl_poll"
    assert "Yes, still interested" not in (rows[0].summary or ""), "the body was stored"
    assert again.last_inbound_at is not None


async def test_our_own_text_is_not_counted_twice(monkeypatch):
    """A message the outbox sent comes back through this poll carrying the same id. Without the
    dedupe it would be one text and two rows -- and two dials on somebody's scorecard."""
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        t, biz, integ = await _ws(s, activity_cursor=(now - dt.timedelta(days=2)).isoformat())
        cand = await _candidate(s, t, biz)
        await s.execute(RecruitingActivity.__table__.delete().where(
            RecruitingActivity.tenant_id == t))
        # The outbox already recorded this one.
        s.add(RecruitingActivity(tenant_id=t, candidate_id=cand.id, kind="sms_out",
                                 occurred_at=now - dt.timedelta(hours=2), source="axcion",
                                 ghl_message_id="m_ours", summary="Text sent"))
        await s.commit()

        monkeypatch.setattr("app.integrations.ghl.ghl_json", _fake_ghl(
            conversations=[{"id": "conv1", "contactId": "contact_poll",
                            "lastMessageDate": now.isoformat()}],
            messages=[{"id": "m_ours", "messageType": "SMS", "direction": "outbound",
                       "dateAdded": (now - dt.timedelta(hours=1)).isoformat()}]))
        await P.poll_activity(s, t)

        rows = (await s.execute(select(RecruitingActivity).where(
            RecruitingActivity.tenant_id == t,
            RecruitingActivity.ghl_message_id == "m_ours"))).scalars().all()
    assert len(rows) == 1, "our own send was ingested a second time"


async def test_an_unknown_type_is_reported_rather_than_dropped(monkeypatch):
    """How V3 gets answered from real traffic instead of from a guess."""
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        t, biz, _ = await _ws(s, activity_cursor=(now - dt.timedelta(days=2)).isoformat())
        await _candidate(s, t, biz)
        monkeypatch.setattr("app.integrations.ghl.ghl_json", _fake_ghl(
            conversations=[{"id": "conv1", "contactId": "contact_poll",
                            "lastMessageDate": now.isoformat()}],
            messages=[{"id": "mx", "messageType": "TYPE_WHATSAPP", "direction": "inbound",
                       "dateAdded": (now - dt.timedelta(hours=1)).isoformat()}]))
        out = await P.poll_activity(s, t)
    assert out["unknown_types"] == {"TYPE_WHATSAPP": 1}
    assert out["written"] == 0


async def test_the_cursor_advances_to_the_newest_message_seen_not_to_now(monkeypatch):
    """If a page was truncated or a call failed, the next run must start where this one really
    got to. Moving the cursor to `now` would skip whatever happened in between, silently."""
    now = dt.datetime.now(dt.timezone.utc)
    newest = now - dt.timedelta(hours=3)
    async with SessionLocal() as s:
        t, biz, integ = await _ws(s, activity_cursor=(now - dt.timedelta(days=2)).isoformat())
        await _candidate(s, t, biz)
        monkeypatch.setattr("app.integrations.ghl.ghl_json", _fake_ghl(
            conversations=[{"id": "conv1", "contactId": "contact_poll",
                            "lastMessageDate": now.isoformat()}],
            messages=[{"id": f"m{uuid.uuid4().hex[:6]}", "messageType": "SMS",
                       "direction": "inbound", "dateAdded": newest.isoformat()}]))
        out = await P.poll_activity(s, t)
        stored = (await s.get(Integration, integ.id)).config["activity_cursor"]

    assert abs((P._dtm(out["newest"]) - newest).total_seconds()) < 2
    assert abs((P._dtm(stored) - newest).total_seconds()) < 2
    assert P._dtm(stored) < now - dt.timedelta(hours=2), "the cursor jumped to now"


async def test_a_failed_delivery_reaches_the_action_that_sent_it(monkeypatch):
    """A 200 from the send endpoint meant QUEUED. A silent failure is the one thing somebody
    would want to know and would otherwise never learn."""
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        t, biz, _ = await _ws(s, activity_cursor=(now - dt.timedelta(days=2)).isoformat())
        cand = await _candidate(s, t, biz)
        await s.execute(RecruitingActivity.__table__.delete().where(
            RecruitingActivity.tenant_id == t))
        s.add(RecruitingActivity(tenant_id=t, candidate_id=cand.id, kind="sms_out",
                                 occurred_at=now - dt.timedelta(hours=2), source="axcion",
                                 ghl_message_id="m_bad", summary="Text sent"))
        action = RecruitingAction(tenant_id=t, idempotency_key=uuid.uuid4().hex,
                                  candidate_id=cand.id, kind="send_sms", status="sent",
                                  ghl_ref="m_bad", request={"message": "hi"})
        s.add(action)
        await s.commit()
        action_id = action.id

        monkeypatch.setattr("app.integrations.ghl.ghl_json", _fake_ghl(
            conversations=[{"id": "conv1", "contactId": "contact_poll",
                            "lastMessageDate": now.isoformat()}],
            messages=[{"id": "m_bad", "messageType": "SMS", "direction": "outbound",
                       "status": "undelivered",
                       "dateAdded": (now - dt.timedelta(hours=1)).isoformat()}]))
        await P.poll_activity(s, t)
        again = await s.get(RecruitingAction, action_id)

    assert again.status == "failed" and "undelivered" in (again.error or "")
