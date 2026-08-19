"""Chapters for a recorded call (2026-08-19).

The model's output is the untrusted part. Everything here pins what happens to it AFTER it
comes back, because a chapter rail that disagrees with the scrubber is worse than no rail —
it moves the playhead somewhere the label did not promise.
"""
import datetime as dt

import pytest
from sqlalchemy import select, delete

from app.config import settings
from app.db import SessionLocal
from app.models import Business, CallTranscript, Launch, SalesCall
from app.seed import seed
from app.services import call_chapters as ch
from app.services.launch import DEFAULT_STAGE_MAP, DEFAULT_PAYMENT_PLAN_MAP

U = dt.timezone.utc


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def test_lengths_come_from_the_next_boundary_not_from_the_model():
    # A model-supplied length can disagree with the model's own start times; the scrubber and
    # the rail would then tell two different stories about the same call.
    out = ch.clean([{"start": 0, "title": "Warm-up", "len": 999},
                    {"start": 120, "title": "Discovery"},
                    {"start": 300, "title": "Next steps"}], duration_s=600)
    assert [c["len"] for c in out] == [120, 180, 300]


def test_output_is_ordered_deduped_and_bounded_by_the_recording():
    out = ch.clean([{"start": 300, "title": "Third"},
                    {"start": 0, "title": "First"},
                    {"start": 302, "title": "Duplicate boundary"},
                    {"start": 9999, "title": "Past the end of the call"},
                    {"start": -5, "title": "Before it started"},
                    {"start": 120, "title": ""}], duration_s=600)
    assert [c["title"] for c in out] == ["First", "Third"]
    assert all(0 <= c["start"] < 600 for c in out)


def test_the_rail_always_reaches_the_opening_of_the_call():
    # A first chapter at 0:45 leaves the first 45 seconds unreachable from the rail.
    out = ch.clean([{"start": 45, "title": "Rapport"}, {"start": 200, "title": "Money"}], 600)
    assert out[0]["start"] == 0
    assert out[0]["len"] == 200


def test_nothing_survives_when_the_model_returns_nothing_usable():
    assert ch.clean([], 600) == []
    assert ch.clean(None, 600) == []
    assert ch.clean([{"start": "abc", "title": "x"}], 600) == []


def test_long_transcripts_drop_the_middle_and_keep_the_end():
    segs = [{"start": i * 10, "speaker": "Aimee" if i % 2 else "Casey", "text": f"line {i}"}
            for i in range(900)]
    body = ch.transcript_lines(segs)
    assert "line 0" in body and "line 899" in body        # both ends survive
    assert "line 450" not in body                          # the middle is what gets dropped
    assert "lines omitted" in body


async def test_short_calls_settle_without_paying_a_model(monkeypatch):
    # A no-show leaves a one-line transcript. Asking a model to chapter it is waste, and
    # returning None would make the worker retry it every 15 minutes for a year.
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "k")
    import anthropic

    class _NoClient:
        def __init__(self, **kw):
            raise AssertionError("a short call must never reach the model")

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _NoClient)
    assert await ch.generate([{"start": 0, "text": "hi", "speaker": "A"}], 40) == []
    assert await ch.generate([], None) == []


async def test_generator_is_off_without_a_key(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    segs = [{"start": i * 10, "speaker": "A", "text": f"t{i}"} for i in range(40)]
    assert await ch.generate(segs, 900) is None
    async with SessionLocal() as s:
        assert await ch.generate_pending(s) == {}


async def _transcript(**kw):
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(CallTranscript))
        await s.execute(delete(SalesCall))
        await s.execute(delete(Launch).where(Launch.business_id == biz.id))
        L = Launch(tenant_id=biz.tenant_id, business_id=biz.id, name="C", program="beCollective",
                   window_start=dt.date(2026, 8, 11), window_end=dt.date(2026, 9, 12),
                   goal_arr=1_000_000, ticket_pif=12000, ticket_plan=14000, price_map={},
                   goal_basis="seats", seat_goal=100, stage_map=DEFAULT_STAGE_MAP,
                   payment_plan_map=DEFAULT_PAYMENT_PLAN_MAP, default_tz="America/Denver",
                   pipeline_match="be collective experience #1 sales")
        s.add(L)
        await s.flush()
        sc = SalesCall(tenant_id=biz.tenant_id, launch_id=L.id, opportunity_id="o1",
                       booking_id="b1", contact_name="Casey", is_current=True,
                       call_time_utc=dt.datetime(2026, 8, 19, 15, 30, tzinfo=U))
        s.add(sc)
        await s.flush()
        segs = [{"start": i * 20, "speaker": "A", "text": f"t{i}"} for i in range(40)]
        tr = CallTranscript(tenant_id=biz.tenant_id, sales_call_id=sc.id, segments=segs,
                            text="t", speakers={}, duration_s=800, **kw)
        s.add(tr)
        await s.commit()
        return tr.id


async def test_a_call_with_no_chapters_is_not_retried_forever(monkeypatch):
    """The whole reason chapters_at exists: [] and NULL must not look the same to the worker."""
    tid = await _transcript()
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "k")
    calls = 0

    async def fake(segments, duration_s):
        nonlocal calls
        calls += 1
        return []                                   # generated, and there was no structure

    monkeypatch.setattr(ch, "generate", fake)
    async with SessionLocal() as s:
        assert await ch.generate_pending(s) == {"chapter_empty": 1}
    async with SessionLocal() as s:
        assert await ch.generate_pending(s) == {}   # second tick does no work
    assert calls == 1
    async with SessionLocal() as s:
        row = (await s.execute(select(CallTranscript).where(CallTranscript.id == tid))).scalar_one()
        assert row.chapters == [] and row.chapters_at is not None


async def test_a_failed_generation_is_retried_but_a_stored_one_is_not(monkeypatch):
    await _transcript()
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "k")
    outcomes = [None, [{"start": 0, "title": "Warm-up", "len": 800}]]

    async def fake(segments, duration_s):
        return outcomes.pop(0)

    monkeypatch.setattr(ch, "generate", fake)
    async with SessionLocal() as s:
        assert await ch.generate_pending(s) == {"chapter_failed": 1}   # left for the next tick
    async with SessionLocal() as s:
        assert await ch.generate_pending(s) == {"chaptered": 1}
    async with SessionLocal() as s:
        assert await ch.generate_pending(s) == {}                      # and then it stops


def test_starts_are_read_as_seconds_even_when_the_model_answers_in_clock_time():
    assert ch._seconds(493) == 493
    assert ch._seconds("8:13") == 493            # mm:ss, the shape it reaches for when guessing
    assert ch._seconds("1:02:03") == 3723
    assert ch._seconds("nope") is None and ch._seconds(None) is None


class _Block:
    type = "tool_use"

    def __init__(self, payload):
        self.input = payload


class _Resp:
    def __init__(self, payload):
        self.content = [_Block(payload)]


def _fake_anthropic(monkeypatch, payload):
    class _Msgs:
        async def create(self, **kw):
            return _Resp(payload)

    class _Client:
        def __init__(self, **kw):
            self.messages = _Msgs()

    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _Client)


async def test_chapters_crammed_into_the_opening_are_discarded(monkeypatch):
    """The first real generation returned 1.03 and 10.02 for a 13-minute call — mm:ss echoed
    back as a decimal. Every value is a plausible number of seconds, so only the SPREAD gives
    it away, and unnoticed it puts the whole rail inside the first ten seconds."""
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "k")
    _fake_anthropic(monkeypatch, {"chapters": [
        {"start": 0, "title": "How Casey found The Shift"},
        {"start": 1.03, "title": "Burnout from eleven years"},
        {"start": 10.02, "title": "Guarantee and next steps"}]})
    segs = [{"start": i * 20, "speaker": "A", "text": f"t{i}"} for i in range(40)]
    assert await ch.generate(segs, 800) == []      # settled, not stored, not retried forever


async def test_real_shaped_output_survives_the_guard(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "k")
    _fake_anthropic(monkeypatch, {"chapters": [
        {"start": 0, "title": "How Casey found The Shift"},
        {"start": 355, "title": "Program structure and time commitment"},
        {"start": 660, "title": "Guarantee and next steps"}]})
    segs = [{"start": i * 20, "speaker": "A", "text": f"t{i}"} for i in range(40)]
    got = await ch.generate(segs, 800)
    assert [c["start"] for c in got] == [0, 355, 660]
    assert [c["len"] for c in got] == [355, 305, 140]     # tiles to the end of the recording


def test_every_line_carries_seconds_so_the_model_cannot_guess_the_unit():
    line = ch.transcript_lines([{"start": 139, "speaker": "Casey", "text": "up at five"}])
    assert line.startswith("[139s] 2:19 Casey:")
