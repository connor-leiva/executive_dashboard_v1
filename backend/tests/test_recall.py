"""Recall.ai recording bots (2026-08-18): who gets a bot, when, and what the webhook does.

The costly mistakes here are silent: a bot booked for a test row, a bot sent to a vanity
landing page that isn't a meeting, a second bot for a call that already has one, or a held
call whose recording never came back and nobody noticed.
"""
import datetime as dt
import json

import httpx
import pytest
from sqlalchemy import select, delete

from app.config import settings
from app.db import SessionLocal
from app.models import Business, CallTranscript, Launch, SalesCall
from app.seed import seed
from app.services import recall
from app.services.launch import DEFAULT_STAGE_MAP, DEFAULT_PAYMENT_PLAN_MAP

U = dt.timezone.utc
ZOOM = "https://us06web.zoom.us/j/8021825042?pwd=abc"


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _launch():
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(CallTranscript))     # bulk delete does not cascade on SQLite
        await s.execute(delete(SalesCall))
        await s.execute(delete(Launch).where(Launch.business_id == biz.id))
        L = Launch(tenant_id=biz.tenant_id, business_id=biz.id, name="R", program="beCollective",
                   window_start=dt.date(2026, 8, 11), window_end=dt.date(2026, 9, 12),
                   goal_arr=1_000_000, ticket_pif=12000, ticket_plan=14000, price_map={},
                   goal_basis="seats", seat_goal=100, stage_map=DEFAULT_STAGE_MAP,
                   payment_plan_map=DEFAULT_PAYMENT_PLAN_MAP, default_tz="America/Denver",
                   pipeline_match="be collective experience #1 sales")
        s.add(L)
        await s.flush()
        tid, lid = biz.tenant_id, L.id
        await s.commit()
    return tid, lid


def test_resolve_meeting_url_handles_the_links_reps_actually_use(monkeypatch):
    monkeypatch.setattr(settings, "RECALL_URL_OVERRIDES",
                        "allisonhare.com/zoom=https://us02web.zoom.us/j/9507511092")
    # a vanity redirect is a landing PAGE - joining it records nothing
    assert recall.resolve_meeting_url("https://allisonhare.com/zoom") == "https://us02web.zoom.us/j/9507511092"
    assert recall.resolve_meeting_url("https://us02web.zoom.us/j/95#success") == "https://us02web.zoom.us/j/95"
    assert recall.resolve_meeting_url("https://meet.google.com/abc-defg-hij")  # a quarter of calls
    assert recall.resolve_meeting_url("Host's conferencing (pending)") is None
    assert recall.resolve_meeting_url(None) is None


async def test_only_imminent_unbooked_real_calls_get_a_bot(monkeypatch):
    tid, lid = await _launch()
    NOW = dt.datetime(2026, 8, 19, 12, tzinfo=U)
    monkeypatch.setattr(settings, "RECALL_API_KEY", "k")
    monkeypatch.setattr(settings, "RECALL_LEAD_MINUTES", 5)
    monkeypatch.setattr(settings, "RECALL_TICK_MINUTES", 5)

    def SC(name, mins, **kw):
        return SalesCall(tenant_id=tid, launch_id=lid, opportunity_id=name, booking_id=name,
                         contact_name=name, meeting_url=kw.pop("url", ZOOM), is_current=True,
                         call_time_utc=NOW + dt.timedelta(minutes=mins), **kw)
    async with SessionLocal() as s:
        s.add_all([
            SC("due", 8),                                  # inside the window -> book it
            SC("Test KlientBoost", 8),                     # a live test booking -> never book
            SC("vanity", 8, url="https://allisonhare.com/zoom"),   # unresolvable here -> skip
            SC("nolink", 8, url=None),
            SC("already", 8, recall_bot_id="bot-existing"), # idempotent: no second bot
            SC("far", 600),                                # tomorrow - a later tick, with its real time
            SC("past", -30),                               # already underway - too late to join
        ])
        await s.commit()

    sent = []

    async def fake_create(client, url, join_at):
        sent.append((url, join_at))
        return f"bot-{len(sent)}", ""
    monkeypatch.setattr(recall, "create_bot", fake_create)

    async with SessionLocal() as s:
        stat = await recall.schedule_due_bots(s, now=NOW)
        rows = {c.contact_name: c for c in (await s.execute(select(SalesCall))).scalars()}

    assert stat.get("recall_bots_created") == 1, stat
    assert stat.get("recall_test_skipped") == 1
    assert stat.get("recall_no_joinable_url") == 1          # the vanity link, unmapped
    assert rows["due"].recall_bot_id and rows["due"].recording_status == "scheduled"
    for never in ("Test KlientBoost", "vanity", "nolink", "far", "past"):
        assert rows[never].recall_bot_id in (None, "bot-existing"), never
    assert rows["already"].recall_bot_id == "bot-existing"  # untouched

    # Recall guarantees on-time joins only with >=10 min of lead, so a call 8 minutes out
    # must be asked for later than "5 minutes before" - not silently booked to arrive late.
    _, join_at = sent[0]
    assert join_at >= NOW + dt.timedelta(minutes=11)


async def test_webhook_status_updates_the_call_and_ignores_unknown_bots():
    tid, lid = await _launch()
    async with SessionLocal() as s:
        s.add(SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="o1", booking_id="b1",
                        contact_name="Jane", recall_bot_id="bot-1", recording_status="scheduled",
                        call_time_utc=dt.datetime(2026, 8, 19, 15, tzinfo=U), is_current=True))
        await s.commit()

    async with SessionLocal() as s:
        assert await recall.apply_bot_status(s, "bot-1", "in_waiting_room") is True
        assert await recall.apply_bot_status(s, "nobody", "done") is False   # backfill bots
    async with SessionLocal() as s:
        sc = (await s.execute(select(SalesCall).where(SalesCall.recall_bot_id == "bot-1"))).scalar_one()
    assert sc.recording_status == "waiting" and sc.recording_at is None

    async with SessionLocal() as s:
        await recall.apply_bot_status(s, "bot-1", "done", "https://recall/rec/1")
    async with SessionLocal() as s:
        sc = (await s.execute(select(SalesCall).where(SalesCall.recall_bot_id == "bot-1"))).scalar_one()
    assert sc.recording_status == "done" and sc.recording_url == "https://recall/rec/1"
    assert sc.recording_at is not None


async def test_a_held_call_with_no_recording_shows_up_in_data_health(monkeypatch):
    """The whole point of the integration: catching the bot that never got admitted. This is
    the only signal that a rep left it in the waiting room, and it must drill to the person."""
    from app.services import sales_desk as sd
    tid, lid = await _launch()
    NOW = dt.datetime(2026, 8, 20, 12, tzinfo=U)
    async with SessionLocal() as s:
        def SC(name, hrs, **kw):
            return SalesCall(tenant_id=tid, launch_id=lid, opportunity_id=name, booking_id=name,
                             contact_name=name, rep_email="a@x.com", meeting_url=ZOOM,
                             is_current=True, call_time_utc=NOW + dt.timedelta(hours=hrs), **kw)
        s.add_all([
            SC("Recorded", -3, outcome="Showed", recall_bot_id="b1", recording_status="done",
               recording_url="https://recall/1"),
            SC("Stranded", -3, outcome="Showed", recall_bot_id="b2", recording_status="waiting"),
            SC("NoShow", -3, outcome="No Show", recall_bot_id="b3", recording_status="waiting"),
            SC("Upcoming", +3, recall_bot_id="b4", recording_status="scheduled"),
        ])
        await s.commit()
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        d = await sd.compute_sales_desk(s, tid, L, now=NOW)

    w = [x for x in d["warnings"] if x["key"] == "dh.no_recording"]
    assert w and w[0]["n"] == 1, d["warnings"]        # only the held call that came back empty
    async with SessionLocal() as s:
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        drill = await sd.drill_sales_desk(s, tid, L, "dh.no_recording", now=NOW)
    assert {r["contact"] for r in drill["rows"]} == {"Stranded"}
    assert "recording" in drill["columns"]            # the drawer carries the link once live


async def test_the_recording_column_stays_hidden_until_recording_is_live():
    """No bots yet means no dead column in every existing drill drawer."""
    from app.services import sales_desk as sd
    tid, lid = await _launch()
    NOW = dt.datetime(2026, 8, 20, 12, tzinfo=U)
    async with SessionLocal() as s:
        s.add(SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="o", booking_id="b",
                        contact_name="Plain", rep_email="a@x.com", outcome="Showed",
                        call_time_utc=NOW - dt.timedelta(hours=2), is_current=True))
        await s.commit()
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        d = await sd.compute_sales_desk(s, tid, L, now=NOW)
        drill = await sd.drill_sales_desk(s, tid, L, "kpi.held", now=NOW)
    assert not [x for x in d["warnings"] if x["key"] == "dh.no_recording"]
    assert "recording" not in drill["columns"]


async def test_a_bot_recall_already_has_is_adopted_not_duplicated(monkeypatch):
    """The 14 backfill bots were booked by a standalone script and never touched the database,
    so recall_bot_id is NULL on those rows. Without adoption the scheduler books a SECOND bot
    for every one of them - two bots in the call, and double the bill."""
    tid, lid = await _launch()
    NOW = dt.datetime(2026, 8, 19, 12, tzinfo=U)
    monkeypatch.setattr(settings, "RECALL_API_KEY", "k")
    monkeypatch.setattr(settings, "RECALL_LEAD_MINUTES", 5)
    monkeypatch.setattr(settings, "RECALL_TICK_MINUTES", 5)

    async with SessionLocal() as s:
        s.add_all([
            SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="a", booking_id="a",
                      contact_name="Backfilled", meeting_url=ZOOM, is_current=True,
                      call_time_utc=NOW + dt.timedelta(minutes=8)),
            SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="b", booking_id="b",
                      contact_name="Brand New", meeting_url=ZOOM, is_current=True,
                      call_time_utc=NOW + dt.timedelta(minutes=9)),
        ])
        await s.commit()

    # Recall already holds a bot for the first call, in the SAME room as the second - so the
    # URL cannot disambiguate them and the join time has to.
    existing = [{"id": "bot-backfill", "meeting_url": ZOOM,
                 "join_at": (NOW + dt.timedelta(minutes=3)).isoformat()}]

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"results": existing}

    async def fake_get(url, **kw): return FakeResp()
    created = []

    async def fake_create(client, url, join_at):
        created.append(url)
        return f"bot-new-{len(created)}", ""

    monkeypatch.setattr(httpx.AsyncClient, "get", lambda self, url, **kw: fake_get(url, **kw))
    monkeypatch.setattr(recall, "create_bot", fake_create)

    async with SessionLocal() as s:
        stat = await recall.schedule_due_bots(s, now=NOW)
        rows = {c.contact_name: c for c in (await s.execute(select(SalesCall))).scalars()}

    assert stat.get("recall_bots_adopted") == 1, stat
    assert rows["Backfilled"].recall_bot_id == "bot-backfill"   # linked, NOT re-booked
    assert len(created) == 1                                    # only the genuinely new call
    assert rows["Brand New"].recall_bot_id == "bot-new-1"


def test_webhook_signature_is_verified_the_way_recall_actually_signs():
    """Recall signs with the workspace Verification Secret (whsec_...). An earlier version of
    this endpoint compared a custom header, which would have rejected every real delivery."""
    import base64, hashlib, hmac
    key = base64.b64encode(b"k" * 24).decode()
    secret = "whsec_" + key
    body, mid = b'{"data":{"bot":{"id":"b1"}}}', "msg_2"
    ts = str(int(dt.datetime.now(U).timestamp()))
    sig = base64.b64encode(
        hmac.new(base64.b64decode(key), f"{mid}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    H = {"svix-id": mid, "svix-timestamp": ts, "svix-signature": f"v1,{sig}"}

    assert recall.verify_signature(secret, H, body) is True
    assert recall.verify_signature(secret, H, b'{"data":{"bot":{"id":"HACKED"}}}') is False
    assert recall.verify_signature(secret, {**H, "svix-signature": "v1,bogus"}, body) is False
    # a replayed delivery from an hour ago must not be accepted
    old = str(int(ts) - 3600)
    assert recall.verify_signature(secret, {**H, "svix-timestamp": old}, body) is False
    assert recall.verify_signature("", H, body) is False
    # the Standard Webhooks spelling is equivalent
    assert recall.verify_signature(
        secret, {"webhook-id": mid, "webhook-timestamp": ts, "webhook-signature": f"v1,{sig}"}, body) is True


async def test_two_calls_in_one_shared_room_cannot_claim_the_same_bot(monkeypatch):
    """Aimee, Michele and Ingrid each run EVERY call through one static personal room, 30
    minutes apart. If adoption matched on URL alone, the 10:00 and 10:30 calls would both
    claim the 10:00 bot and the 10:30 call would go unrecorded with nothing to show for it."""
    tid, lid = await _launch()
    NOW = dt.datetime(2026, 8, 19, 9, 50, tzinfo=U)
    monkeypatch.setattr(settings, "RECALL_API_KEY", "k")
    monkeypatch.setattr(settings, "RECALL_LEAD_MINUTES", 5)
    monkeypatch.setattr(settings, "RECALL_TICK_MINUTES", 45)   # wide enough to see both

    ten = dt.datetime(2026, 8, 19, 10, tzinfo=U)
    async with SessionLocal() as s:
        s.add_all([
            SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="x", booking_id="x",
                      contact_name="Ten", meeting_url=ZOOM, is_current=True, call_time_utc=ten),
            SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="y", booking_id="y",
                      contact_name="TenThirty", meeting_url=ZOOM, is_current=True,
                      call_time_utc=ten + dt.timedelta(minutes=30)),
        ])
        await s.commit()

    existing = [{"id": "bot-10", "meeting_url": ZOOM, "join_at": (ten - dt.timedelta(minutes=5)).isoformat()},
                {"id": "bot-1030", "meeting_url": ZOOM,
                 "join_at": (ten + dt.timedelta(minutes=25)).isoformat()}]

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"results": existing}
    monkeypatch.setattr(httpx.AsyncClient, "get", lambda self, url, **kw: _resp(FakeResp()))

    async def boom(*a, **k):
        raise AssertionError("should not book a new bot - both calls already have one")
    monkeypatch.setattr(recall, "create_bot", boom)

    async with SessionLocal() as s:
        stat = await recall.schedule_due_bots(s, now=NOW)
        rows = {c.contact_name: c.recall_bot_id for c in (await s.execute(select(SalesCall))).scalars()}

    assert stat.get("recall_bots_adopted") == 2, stat
    assert rows["Ten"] == "bot-10" and rows["TenThirty"] == "bot-1030"   # each to its own


async def _resp(r):
    return r


async def test_the_bot_joins_before_the_call_not_after_it(monkeypatch):
    """Recall guarantees an on-time join only when join_at is >=10 minutes out, so a call has
    to be picked up while it is still that far away. A window of lead+tick alone meant every
    call was found with ~10 minutes left, floored to now+11, and the bot walked in AFTER the
    call had started - on every booking, with nothing to indicate it."""
    tid, lid = await _launch()
    NOW = dt.datetime(2026, 8, 19, 12, tzinfo=U)
    monkeypatch.setattr(settings, "RECALL_API_KEY", "k")
    monkeypatch.setattr(settings, "RECALL_LEAD_MINUTES", 5)
    monkeypatch.setattr(settings, "RECALL_TICK_MINUTES", 5)

    start = NOW + dt.timedelta(minutes=18)          # a normal call, found on an ordinary tick
    async with SessionLocal() as s:
        s.add(SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="p", booking_id="p",
                        contact_name="Punctual", meeting_url=ZOOM, is_current=True,
                        call_time_utc=start))
        await s.commit()

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"results": []}
    monkeypatch.setattr(httpx.AsyncClient, "get", lambda self, url, **kw: _resp(FakeResp()))
    sent = []

    async def fake_create(client, url, join_at):
        sent.append(join_at)
        return "bot-x", ""
    monkeypatch.setattr(recall, "create_bot", fake_create)

    async with SessionLocal() as s:
        await recall.schedule_due_bots(s, now=NOW)

    assert sent, "the call was never picked up"
    join_at = sent[0]
    assert join_at < start, "the bot must join BEFORE the call starts"
    assert join_at >= NOW + dt.timedelta(minutes=10), "Recall needs >=10 min of lead"
    assert join_at == start - dt.timedelta(minutes=5), "and it should honour the configured lead"


async def test_the_call_board_has_no_upper_bound():
    """Connor, 2026-08-18: the far-out bookings are exactly the ones that go stale unnoticed,
    so nothing is cut off at 48 hours. (Where the board STARTS is pinned separately, in
    test_the_board_is_the_schedule_not_a_history.)"""
    from app.services import sales_desk as sd
    tid, lid = await _launch()
    NOW = dt.datetime(2026, 8, 19, 12, tzinfo=U)
    async with SessionLocal() as s:
        for name, days in (("Tomorrow", 1), ("Week", 7), ("Fortnight", 15), ("Month", 32)):
            s.add(SalesCall(tenant_id=tid, launch_id=lid, opportunity_id=name, booking_id=name,
                            contact_name=name, rep_email="a@x.com", is_current=True,
                            call_time_utc=NOW + dt.timedelta(days=days)))
        await s.commit()
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        d = await sd.compute_sales_desk(s, tid, L, now=NOW)

    on_board = [c["contact_name"] for c in d["calls"]]
    assert on_board == ["Tomorrow", "Week", "Fortnight", "Month"], on_board


async def test_the_board_is_the_schedule_not_a_history():
    """Connor: a finished call showing a recording inside a pane labelled "upcoming" reads as
    a bug. The board keeps a short look-back so a call already under way stays visible - that
    is when "the bot is in the waiting room, go admit it" matters - but this morning's
    finished calls belong in the drills, not here."""
    from app.services import sales_desk as sd
    tid, lid = await _launch()
    NOW = dt.datetime(2026, 8, 20, 15, tzinfo=U)
    async with SessionLocal() as s:
        def C(name, mins, **kw):
            return SalesCall(tenant_id=tid, launch_id=lid, opportunity_id=name, booking_id=name,
                             contact_name=name, rep_email="a@x.com", is_current=True,
                             call_time_utc=NOW + dt.timedelta(minutes=mins), **kw)
        s.add_all([
            C("Morning", -300, outcome="Showed", recall_bot_id="b1",
              recording_status="done", recording_url="https://rec/1"),   # done hours ago
            C("Live", -20, recall_bot_id="b2", recording_status="waiting"),
            C("Soon", 30), C("Distant", 60 * 24 * 7),
        ])
        await s.commit()
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        d = await sd.compute_sales_desk(s, tid, L, now=NOW)

    board = [c["contact_name"] for c in d["calls"]]
    assert board == ["Live", "Soon", "Distant"], board
    assert "Morning" not in board
    # ...but it is still reachable, with its recording, from the all-calls drill
    async with SessionLocal() as s:
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        drill = await sd.drill_sales_desk(s, tid, L, "kpi.booked", now=NOW)
    got = {r["contact"]: r.get("recording") for r in drill["rows"]}
    # NOT the stored media URL: Recall signs those and they die after 5 hours, so the payload
    # carries the call id and the link is minted when somebody actually clicks.
    assert got.get("Morning", "").startswith("rec:")
    assert "https://" not in str(got.get("Morning"))
    assert len(drill["rows"]) == 4


async def test_expiring_media_urls_are_never_baked_into_the_payload():
    """Recall's media URLs are signed S3 links that expire after 5 hours. Rendering a stored
    one gives a link that works this afternoon and 404s tomorrow, with nothing to indicate
    why - so the payload carries an id and the link is minted at click time."""
    from app.services import sales_desk as sd
    tid, lid = await _launch()
    NOW = dt.datetime(2026, 8, 20, 12, tzinfo=U)
    async with SessionLocal() as s:
        s.add(SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="o", booking_id="b",
                        contact_name="Watched", rep_email="a@x.com", outcome="Showed",
                        call_time_utc=NOW - dt.timedelta(minutes=20), is_current=True,
                        recall_bot_id="bot-9", recording_status="done",
                        recording_url="https://s3.example/signed?X-Amz-Expires=18000"))
        await s.commit()
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        d = await sd.compute_sales_desk(s, tid, L, now=NOW)
        drill = await sd.drill_sales_desk(s, tid, L, "kpi.booked", now=NOW)

    blob = json.dumps({"board": d["calls"], "drill": drill["rows"]})
    assert "X-Amz-Expires" not in blob and "s3.example" not in blob, "an expiring URL leaked"
    assert d["calls"][0]["recording_id"], "the board needs the id to mint a link"
    assert drill["rows"][0]["recording"].startswith("rec:")


async def test_create_bot_sends_automatic_leave_where_recall_reads_it(monkeypatch):
    """Verified against a real bot's payload (2026-08-19): automatic_leave is a TOP-LEVEL
    field and everyone_left_timeout takes an object. Nested under recording_config, as it
    was, Recall accepts the request and silently applies its own defaults - so the timeouts
    read as configured here while 1200/1200/2 were actually in force. Nothing surfaces that."""
    monkeypatch.setattr(settings, "RECALL_API_KEY", "k")
    monkeypatch.setattr(settings, "RECALL_BOT_NAME", "Spring - Call Notetaker")
    sent = {}

    class FakeResp:
        status_code = 200
        def json(self): return {"id": "bot-z"}

    class FakeClient:
        async def post(self, url, headers=None, json=None):
            sent.update(json or {})
            return FakeResp()

    bot, err = await recall.create_bot(FakeClient(), "https://us06web.zoom.us/j/1",
                                       dt.datetime(2026, 8, 20, 14, tzinfo=U))
    assert bot == "bot-z" and not err
    assert "automatic_leave" not in sent.get("recording_config", {}), "nested = silently ignored"
    al = sent["automatic_leave"]
    assert al["waiting_room_timeout"] == 900
    assert isinstance(al["everyone_left_timeout"], dict), "a bare number is ignored"
    # the transcript config DOES belong under recording_config - that one was right
    assert sent["recording_config"]["transcript"]["provider"] == {"meeting_captions": {}}
    assert sent["bot_name"] == "Spring - Call Notetaker"      # the disclosure attendees see
    assert sent["join_at"] == "2026-08-20T14:00:00Z"


async def test_a_past_call_gets_its_backfill_bot_linked_with_the_real_status(monkeypatch):
    """The 14 bots created by scripts/recall_bots.py never touched the database, so their
    calls have recall_bot_id NULL. Adoption originally only looked at the SCHEDULING window,
    which is future-only - so a call that had already happened could never be linked and its
    recording was orphaned permanently. The webhook doesn't rescue it either: it only fires on
    NEW status changes, so a call that finished before we knew the bot never gets an update.
    """
    tid, lid = await _launch()
    NOW = dt.datetime(2026, 8, 19, 18, tzinfo=U)
    monkeypatch.setattr(settings, "RECALL_API_KEY", "k")
    monkeypatch.setattr(settings, "RECALL_LEAD_MINUTES", 5)
    monkeypatch.setattr(settings, "RECALL_TICK_MINUTES", 5)
    monkeypatch.setattr(settings, "RECALL_ADOPT_LOOKBACK_DAYS", 14)

    ran = NOW - dt.timedelta(hours=4)                      # this morning's call, already done
    async with SessionLocal() as s:
        s.add_all([
            SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="k", booking_id="k",
                      contact_name="Kristen", meeting_url=ZOOM, is_current=True,
                      call_time_utc=ran, outcome="Showed"),
            SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="old", booking_id="old",
                      contact_name="Ancient", meeting_url=ZOOM, is_current=True,
                      call_time_utc=NOW - dt.timedelta(days=40)),      # outside the lookback
        ])
        await s.commit()

    existing = [{"id": "bot-kristen", "meeting_url": ZOOM,
                 "join_at": (ran - dt.timedelta(minutes=5)).isoformat(),
                 "recordings": [{"status": {"code": "done"},
                                 "media_shortcuts": {"video_mixed": {"data": {}}}}]}]

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"results": existing}
    monkeypatch.setattr(httpx.AsyncClient, "get", lambda self, url, **kw: _resp(FakeResp()))

    async def boom(*a, **k):
        raise AssertionError("a past call must never be scheduled a new bot")
    monkeypatch.setattr(recall, "create_bot", boom)

    async with SessionLocal() as s:
        stat = await recall.schedule_due_bots(s, now=NOW)
        rows = {c.contact_name: c for c in (await s.execute(select(SalesCall))).scalars()}

    assert stat.get("recall_bots_adopted") == 1, stat
    k = rows["Kristen"]
    assert k.recall_bot_id == "bot-kristen"
    # ...and marked DONE from the bot itself, not left at "scheduled" with no recording
    assert k.recording_status == "done" and k.recording_at is not None
    assert rows["Ancient"].recall_bot_id is None          # lookback is bounded


async def test_a_call_with_no_stored_link_can_still_be_adopted(monkeypatch):
    """Every booking made before the Appointment Link field existed in GHL has meeting_url
    NULL - and those are exactly the calls the 14 backfill bots belong to. Requiring the URL
    for adoption made them permanently unmatchable, which is why Kristen's recording never
    appeared. Time alone identifies them; the greedy pass still stops two calls sharing a bot.
    """
    tid, lid = await _launch()
    NOW = dt.datetime(2026, 8, 19, 18, tzinfo=U)
    monkeypatch.setattr(settings, "RECALL_API_KEY", "k")
    monkeypatch.setattr(settings, "RECALL_ADOPT_LOOKBACK_DAYS", 14)
    monkeypatch.setattr(settings, "RECALL_LEAD_MINUTES", 5)
    monkeypatch.setattr(settings, "RECALL_TICK_MINUTES", 5)

    ran = dt.datetime(2026, 8, 19, 14, 30, tzinfo=U)          # Kristen's real call time
    async with SessionLocal() as s:
        s.add(SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="k", booking_id="k",
                        contact_name="Kristen", rep_email="b@x.com", meeting_url=None,
                        is_current=True, call_time_utc=ran, outcome="No Show"))
        await s.commit()

    existing = [{"id": "bot-kristen", "meeting_url": "https://meet.google.com/tuz-dhwp-zrw",
                 "join_at": (ran - dt.timedelta(minutes=5)).isoformat(),
                 "recordings": [{"status": {"code": "done"},
                                 "media_shortcuts": {"video_mixed": {"data": {}}}}]}]

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"results": existing}
    monkeypatch.setattr(httpx.AsyncClient, "get", lambda self, url, **kw: _resp(FakeResp()))

    async def boom(*a, **k):
        raise AssertionError("nothing here should be sent a new bot")
    monkeypatch.setattr(recall, "create_bot", boom)

    async with SessionLocal() as s:
        stat = await recall.schedule_due_bots(s, now=NOW)
        sc = (await s.execute(select(SalesCall))).scalars().first()

    assert stat.get("recall_bots_adopted") == 1, stat
    assert sc.recall_bot_id == "bot-kristen" and sc.recording_status == "done"


async def test_a_url_confirmed_pairing_wins_over_a_time_only_one(monkeypatch):
    """When two calls could claim one bot, the one whose stored URL actually matches should
    get it - otherwise a URL-less neighbour can steal a recording that isn't its."""
    tid, lid = await _launch()
    NOW = dt.datetime(2026, 8, 19, 18, tzinfo=U)
    monkeypatch.setattr(settings, "RECALL_API_KEY", "k")
    monkeypatch.setattr(settings, "RECALL_ADOPT_LOOKBACK_DAYS", 14)
    ran = dt.datetime(2026, 8, 19, 14, 30, tzinfo=U)
    async with SessionLocal() as s:
        s.add_all([
            SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="url", booking_id="url",
                      contact_name="HasUrl", meeting_url=ZOOM, is_current=True, call_time_utc=ran),
            SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="bare", booking_id="bare",
                      contact_name="NoUrl", meeting_url=None, is_current=True, call_time_utc=ran),
        ])
        await s.commit()

    existing = [{"id": "bot-1", "meeting_url": ZOOM, "join_at": (ran - dt.timedelta(minutes=5)).isoformat(),
                 "recordings": [{"status": {"code": "done"},
                                 "media_shortcuts": {"video_mixed": {"data": {}}}}]}]

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"results": existing}
    monkeypatch.setattr(httpx.AsyncClient, "get", lambda self, url, **kw: _resp(FakeResp()))
    monkeypatch.setattr(recall, "create_bot", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))

    async with SessionLocal() as s:
        await recall.schedule_due_bots(s, now=NOW)
        rows = {c.contact_name: c.recall_bot_id for c in (await s.execute(select(SalesCall))).scalars()}
    assert rows["HasUrl"] == "bot-1" and rows["NoUrl"] is None


def test_meeting_identity_survives_recalls_object_shape():
    """The bug behind "114 unlinked calls, nothing matched": Recall returns meeting_url as
    {meeting_id, platform} while we store the full joinable URL from GHL. Compared directly
    they can never be equal, so every call that HAD a link skipped every bot."""
    ZM = "https://us06web.zoom.us/j/8021825042?pwd=vE8R4HEUA5VxiAd9Abq8zi7WbLFMb4.1"
    MEET = "https://meet.google.com/tuz-dhwp-zrw"
    assert recall.meeting_key(ZM) == recall._bot_url({"meeting_url": {"meeting_id": "8021825042",
                                                                     "platform": "zoom"}})
    assert recall.meeting_key(MEET) == recall._bot_url({"meeting_url": {"meeting_id": "tuz-dhwp-zrw",
                                                                       "platform": "google_meet"}})
    # a different room must still NOT match
    assert recall.meeting_key(ZM) != recall._bot_url({"meeting_url": {"meeting_id": "9507511092"}})
    # the same room reached by different hosts/query strings is one identity
    assert recall.meeting_key("https://us02web.zoom.us/j/9507511092") == \
           recall.meeting_key("https://us02web.zoom.us/j/9507511092?pwd=zzz")
    assert recall.meeting_key(None) is None


async def test_a_future_call_with_an_existing_bot_is_linked_before_it_is_due(monkeypatch):
    """The backfill booked bots up to nine days out. Adoption used to reach only ~20 minutes
    forward, so those calls sat unlinked - no recording status anywhere on the Desk - until
    each one was nearly starting."""
    tid, lid = await _launch()
    NOW = dt.datetime(2026, 8, 19, 18, tzinfo=U)
    monkeypatch.setattr(settings, "RECALL_API_KEY", "k")
    monkeypatch.setattr(settings, "RECALL_ADOPT_LOOKBACK_DAYS", 14)
    monkeypatch.setattr(settings, "RECALL_ADOPT_LOOKAHEAD_DAYS", 30)
    monkeypatch.setattr(settings, "RECALL_LEAD_MINUTES", 5)
    monkeypatch.setattr(settings, "RECALL_TICK_MINUTES", 5)

    far = dt.datetime(2026, 8, 28, 14, tzinfo=U)          # nine days out
    async with SessionLocal() as s:
        s.add(SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="f", booking_id="f",
                        contact_name="Kristina", meeting_url=ZOOM, is_current=True,
                        call_time_utc=far))
        await s.commit()

    existing = [{"id": "bot-far",
                 "meeting_url": {"meeting_id": "8021825042", "platform": "zoom"},
                 "join_at": (far - dt.timedelta(minutes=5)).isoformat(),
                 "status_changes": [{"code": "joining_call"}]}]

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"results": existing}
    monkeypatch.setattr(httpx.AsyncClient, "get", lambda self, url, **kw: _resp(FakeResp()))

    async def boom(*a, **k):
        raise AssertionError("it already has a bot - never book a second")
    monkeypatch.setattr(recall, "create_bot", boom)

    async with SessionLocal() as s:
        stat = await recall.schedule_due_bots(s, now=NOW)
        sc = (await s.execute(select(SalesCall))).scalars().first()

    assert stat.get("recall_bots_adopted") == 1, stat
    assert sc.recall_bot_id == "bot-far" and sc.recording_status == "scheduled"


# ── from the adversarial audit, 2026-08-19: seven confirmed, all but one silent ──────────

def test_call_ended_is_not_proof_that_anything_was_recorded():
    """A bot that sat in the waiting room and was never admitted still emits call_ended.
    Mapping that to "done" reported the call as recorded and silenced the no-recording
    warning for exactly the failure the warning exists to catch."""
    assert recall._STATUS_MAP["call_ended"] == recall.ST_ENDED
    never_admitted = {"status_changes": [{"code": "in_waiting_room"}, {"code": "call_ended"}]}
    assert recall.bot_status(never_admitted) == recall.ST_ENDED
    # a completed recording row with no media attached is not "done" either
    assert recall.bot_status({"recordings": [{"status": {"code": "done"}}]}) == recall.ST_ENDED
    assert recall.bot_status({"recordings": [{"status": {"code": "done"},
                                              "media_shortcuts": {"video_mixed": None}}]}) == recall.ST_ENDED
    # ...but media present, even as an empty dict, is
    assert recall.bot_status({"recordings": [{"status": {"code": "done"},
                                              "media_shortcuts": {"video_mixed": {}}}]}) == recall.ST_DONE


async def test_matching_accounts_for_the_lead_the_bot_was_booked_with(monkeypatch):
    """Bots are booked to join BEFORE the call. Ranking on abs(call_at - join_at) scored a
    correct pairing at `lead` rather than 0, putting a neighbouring call in the same shared
    room at the same distance - at lead 15 with 30-minute spacing the two tie exactly and
    insertion order decides which client's recording lands on which call."""
    tid, lid = await _launch()
    NOW = dt.datetime(2026, 8, 19, 9, 40, tzinfo=U)
    monkeypatch.setattr(settings, "RECALL_API_KEY", "k")
    monkeypatch.setattr(settings, "RECALL_LEAD_MINUTES", 15)     # the backfill script's default
    monkeypatch.setattr(settings, "RECALL_TICK_MINUTES", 120)
    monkeypatch.setattr(settings, "RECALL_ADOPT_LOOKBACK_DAYS", 14)
    monkeypatch.setattr(settings, "RECALL_ADOPT_LOOKAHEAD_DAYS", 30)

    ten = dt.datetime(2026, 8, 19, 10, tzinfo=U)
    async with SessionLocal() as s:
        s.add_all([
            SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="a", booking_id="a",
                      contact_name="Ten", meeting_url=ZOOM, is_current=True, call_time_utc=ten),
            SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="b", booking_id="b",
                      contact_name="Tenthirty", meeting_url=ZOOM, is_current=True,
                      call_time_utc=ten + dt.timedelta(minutes=30)),
        ])
        await s.commit()

    MU = {"meeting_id": "8021825042", "platform": "zoom"}
    # newest-first, the order that used to make the 10:00 call adopt the 10:30 call's bot
    existing = [
        {"id": "bot-1030", "meeting_url": MU, "join_at": (ten + dt.timedelta(minutes=15)).isoformat()},
        {"id": "bot-1000", "meeting_url": MU, "join_at": (ten - dt.timedelta(minutes=15)).isoformat()},
    ]

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"results": existing}
    monkeypatch.setattr(httpx.AsyncClient, "get", lambda self, url, **kw: _resp(FakeResp()))
    monkeypatch.setattr(recall, "create_bot",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("both already have bots")))

    async with SessionLocal() as s:
        await recall.schedule_due_bots(s, now=NOW)
        got = {c.contact_name: c.recall_bot_id for c in (await s.execute(select(SalesCall))).scalars()}
    assert got["Ten"] == "bot-1000", got
    assert got["Tenthirty"] == "bot-1030", got


async def test_a_bot_already_linked_to_another_call_is_never_re_claimed(monkeypatch):
    """used_bots only dedupes within a single pass, so on a LATER tick a neighbouring call
    could claim a bot that already belongs to someone else: two rows sharing a bot id, one
    client's Watch link playing another's conversation, and the robbed call never booking
    a bot of its own."""
    tid, lid = await _launch()
    NOW = dt.datetime(2026, 8, 19, 12, tzinfo=U)
    monkeypatch.setattr(settings, "RECALL_API_KEY", "k")
    monkeypatch.setattr(settings, "RECALL_LEAD_MINUTES", 5)
    monkeypatch.setattr(settings, "RECALL_TICK_MINUTES", 5)
    monkeypatch.setattr(settings, "RECALL_ADOPT_LOOKBACK_DAYS", 14)
    monkeypatch.setattr(settings, "RECALL_ADOPT_LOOKAHEAD_DAYS", 30)

    ten = dt.datetime(2026, 8, 19, 10, tzinfo=U)
    async with SessionLocal() as s:
        s.add_all([
            SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="linked", booking_id="linked",
                      contact_name="Alreadylinked", meeting_url=ZOOM, is_current=True,
                      call_time_utc=ten, recall_bot_id="bot-10", recording_status="recording"),
            SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="near", booking_id="near",
                      contact_name="Neighbour", meeting_url=ZOOM, is_current=True,
                      call_time_utc=ten + dt.timedelta(minutes=15)),
        ])
        await s.commit()

    existing = [{"id": "bot-10", "meeting_url": {"meeting_id": "8021825042", "platform": "zoom"},
                 "join_at": (ten - dt.timedelta(minutes=5)).isoformat()}]

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"results": existing}
    monkeypatch.setattr(httpx.AsyncClient, "get", lambda self, url, **kw: _resp(FakeResp()))
    monkeypatch.setattr(recall, "create_bot", lambda *a, **k: _resp(("bot-new", "")))

    async with SessionLocal() as s:
        await recall.schedule_due_bots(s, now=NOW)
        got = {c.contact_name: c.recall_bot_id for c in (await s.execute(select(SalesCall))).scalars()}
    assert got["Alreadylinked"] == "bot-10"
    assert got["Neighbour"] != "bot-10", "a bot must belong to exactly one call"


# ── transcripts (Phase 1) ─────────────────────────────────────────────────────────────────

def _utt(name, host, chunks):
    """One entry in Recall's real payload shape, confirmed against a live call 2026-08-19."""
    return {"participant": {"name": name, "is_host": host, "email": None},
            "words": [{"text": t,
                       "start_timestamp": {"relative": a, "absolute": "2026-08-19T14:41:50Z"},
                       "end_timestamp": {"relative": b, "absolute": "2026-08-19T14:41:55Z"}}
                      for t, a, b in chunks],
            "language_code": "en-US"}


def test_transcript_parses_recalls_real_shape():
    """Not a guess: a LIST of utterances, each with participant.name / is_host and a words[]
    whose entries are caption CHUNKS (35 chars over 4.4s in the sample), timed by
    start_timestamp.relative - seconds from the top of the recording, which is what the
    player seeks to."""
    doc = [
        _utt("Casey Styers", False, [("so what does it actually cost", 0.06, 4.44)]),
        _utt("Casey Styers", False, [("like all in", 5.0, 6.5)]),          # merges with above
        _utt("Brianna Wood", True, [("twelve thousand", 8.0, 11.0), ("or a plan", 11.1, 13.0)]),
        _utt("Casey Styers", False, [("thats a third of what I make", 40.0, 45.0)]),  # gap: own turn
    ]
    r = recall.parse_transcript(doc)
    segs = r["segments"]
    assert [s["speaker"] for s in segs] == ["Casey Styers", "Brianna Wood", "Casey Styers"]
    assert segs[0]["text"] == "so what does it actually cost like all in"
    assert segs[0]["start"] == 0.06 and segs[0]["end"] == 6.5
    assert segs[1]["is_host"] is True                       # is_host marks the REP
    assert r["duration_s"] == 45
    # talk ratio comes free from the same data
    assert r["speakers"]["Brianna Wood"]["seconds"] == 5.0
    assert round(r["speakers"]["Casey Styers"]["seconds"], 1) == 11.4
    # flattened for search
    assert "twelve thousand" in r["text"] and "Brianna Wood:" in r["text"]


def test_transcript_parser_survives_junk():
    """It runs unattended on every finished call; one odd payload must not stall the tick."""
    assert recall.parse_transcript([])["segments"] == []
    assert recall.parse_transcript(None)["segments"] == []
    assert recall.parse_transcript([{"participant": {"name": "X"}}])["segments"] == []   # no words
    assert recall.parse_transcript(["nonsense", 7])["segments"] == []
    # an entry with no name still counts rather than being dropped
    r = recall.parse_transcript([_utt("", False, [("hello", 1.0, 2.0)])])
    assert r["segments"][0]["speaker"] == "Unknown"


async def test_transcripts_are_stored_once_with_a_retention_date(monkeypatch):
    """Connor's decisions: store (so the corpus is queryable) and keep one year. The purge
    date is written at insert time - a verbatim record of a client conversation must not
    outlive its policy because nobody remembered to apply one later."""
    from app.models import CallTranscript
    tid, lid = await _launch()
    NOW = dt.datetime(2026, 8, 19, 18, tzinfo=U)
    monkeypatch.setattr(settings, "RECALL_API_KEY", "k")
    monkeypatch.setattr(settings, "RECALL_TRANSCRIPT_RETAIN_DAYS", 365)

    async with SessionLocal() as s:
        s.add_all([
            SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="d", booking_id="d",
                      contact_name="Done", is_current=True, call_time_utc=NOW,
                      recall_bot_id="bot-done", recording_status="done"),
            SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="w", booking_id="w",
                      contact_name="Waiting", is_current=True, call_time_utc=NOW,
                      recall_bot_id="bot-wait", recording_status="waiting"),
        ])
        await s.commit()

    parsed = recall.parse_transcript([_utt("Rep", True, [("hi", 0.0, 1.0)])])

    async def fake_fetch(bot_id):
        return parsed if bot_id == "bot-done" else None
    monkeypatch.setattr(recall, "fetch_transcript", fake_fetch)

    async with SessionLocal() as s:
        stat = await recall.store_transcripts(s, now=NOW)
        rows = (await s.execute(select(CallTranscript))).scalars().all()
    assert stat.get("transcripts_stored") == 1, stat
    assert len(rows) == 1                                   # only the finished recording
    want = NOW + dt.timedelta(days=365)
    got = rows[0].purge_after
    got = got if got.tzinfo else got.replace(tzinfo=U)      # SQLite returns naive
    assert got == want
    assert rows[0].text and rows[0].duration_s == 1

    # a second pass must not duplicate it
    async with SessionLocal() as s:
        again = await recall.store_transcripts(s, now=NOW)
        rows = (await s.execute(select(CallTranscript))).scalars().all()
    assert not again.get("transcripts_stored") and len(rows) == 1


async def test_retention_actually_deletes(monkeypatch):
    """A retention policy that is never enforced is just a comment."""
    from app.models import CallTranscript
    tid, lid = await _launch()
    NOW = dt.datetime(2027, 9, 1, tzinfo=U)
    async with SessionLocal() as s:
        sc = SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="o", booking_id="o",
                       contact_name="Old", is_current=True, call_time_utc=NOW)
        s.add(sc)
        await s.flush()
        s.add_all([
            CallTranscript(tenant_id=tid, sales_call_id=sc.id, text="expired",
                           purge_after=NOW - dt.timedelta(days=1)),
        ])
        await s.commit()
    async with SessionLocal() as s:
        n = await recall.purge_expired_transcripts(s, now=NOW)
        left = (await s.execute(select(CallTranscript))).scalars().all()
    assert n == 1 and left == []


async def test_search_finds_the_call_and_the_moment(monkeypatch):
    """A result that only names the call leaves you scrubbing a 20-minute recording for it.
    Every hit carries its offset so the player can open on the sentence."""
    from app.models import CallTranscript
    tid, lid = await _launch()
    NOW = dt.datetime(2026, 8, 19, 12, tzinfo=U)

    async def add(name, when, utts):
        async with SessionLocal() as s:
            sc = SalesCall(tenant_id=tid, launch_id=lid, opportunity_id=name, booking_id=name,
                           contact_name=name, rep_email="a@x.com", is_current=True,
                           outcome="Showed", call_time_utc=when)
            s.add(sc)
            await s.flush()
            parsed = recall.parse_transcript(utts)
            s.add(CallTranscript(tenant_id=tid, sales_call_id=sc.id, segments=parsed["segments"],
                                 text=parsed["text"], speakers=parsed["speakers"],
                                 duration_s=parsed["duration_s"]))
            await s.commit()

    await add("Casey Styers", NOW - dt.timedelta(days=1), [
        _utt("Aimee", True, [("what brought you in", 10.0, 12.0)]),
        _utt("Casey Styers", False, [("so what does it cost", 60.0, 62.0)]),
        _utt("Aimee", True, [("twelve thousand or a payment plan", 90.0, 95.0)]),
    ])
    await add("Nellie Krueger", NOW - dt.timedelta(days=2), [
        _utt("Nellie Krueger", False, [("I love the community idea", 5.0, 8.0)]),
    ])

    async with SessionLocal() as s:
        r = await recall.search_transcripts(s, tid, lid, "payment plan")
    assert r["count"] == 1, r
    call = r["calls"][0]
    assert call["contact"] == "Casey Styers"
    assert call["hits"][0]["start"] == 90.0            # the MOMENT, not just the call
    assert call["hits"][0]["is_host"] is True          # said by the rep
    assert "twelve thousand" in call["hits"][0]["text"]

    # case-insensitive, and a term in neither call finds nothing
    async with SessionLocal() as s:
        assert (await recall.search_transcripts(s, tid, lid, "PAYMENT PLAN"))["count"] == 1
        assert (await recall.search_transcripts(s, tid, lid, "refund policy"))["count"] == 0
        # a term in both returns both, newest first ("cost" / "community")
        both = await recall.search_transcripts(s, tid, lid, "co")
        assert [c["contact"] for c in both["calls"]] == ["Casey Styers", "Nellie Krueger"]


async def test_search_refuses_a_query_too_short_to_mean_anything():
    """Without this, an empty box scans every transcript in the corpus on each keystroke."""
    tid, lid = await _launch()
    async with SessionLocal() as s:
        for q in ("", " ", "a"):
            r = await recall.search_transcripts(s, tid, lid, q)
            assert r["too_short"] and r["count"] == 0, q


async def test_search_never_leaks_across_launches():
    """Transcripts are visible to anyone with the tab - that is scoped to THIS launch, not
    to every call the tenant has ever recorded."""
    from app.models import CallTranscript
    tid, lid = await _launch()
    async with SessionLocal() as s:
        sc = SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="x", booking_id="x",
                       contact_name="Mine", is_current=True,
                       call_time_utc=dt.datetime(2026, 8, 19, tzinfo=U))
        s.add(sc)
        await s.flush()
        s.add(CallTranscript(tenant_id=tid, sales_call_id=sc.id, text="secret word here",
                             segments=[{"start": 1.0, "speaker": "A", "is_host": False,
                                        "text": "secret word here"}]))
        await s.commit()
    import uuid as _uuid
    async with SessionLocal() as s:
        mine = await recall.search_transcripts(s, tid, lid, "secret")
        other = await recall.search_transcripts(s, tid, _uuid.uuid4(), "secret")
    assert mine["count"] == 1 and other["count"] == 0
