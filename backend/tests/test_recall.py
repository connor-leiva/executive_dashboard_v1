"""Recall.ai recording bots (2026-08-18): who gets a bot, when, and what the webhook does.

The costly mistakes here are silent: a bot booked for a test row, a bot sent to a vanity
landing page that isn't a meeting, a second bot for a call that already has one, or a held
call whose recording never came back and nobody noticed.
"""
import datetime as dt

import pytest
from sqlalchemy import select, delete

from app.config import settings
from app.db import SessionLocal
from app.models import Business, Launch, SalesCall
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
