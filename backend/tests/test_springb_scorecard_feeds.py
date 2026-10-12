"""Spring B L10 scorecard — the Forum feeds and the lines they power.

The Spring B board (shared by the Forum, beCollective and Edge tabs) shipped all-manual. Four Forum
lines are now fed: Members Added (GHL enrollment dates — the Forum tab's own 'joined'), VIP Guests
Added (funnel stage-change events the Forum sync now records), and Appointments Set / Met (the Forum
calendar the sync now reads). Each test builds its own tenant so counts are exact."""
import datetime as dt
import uuid
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.db import SessionLocal, engine
from app.models import Base, Business, Integration, MetricRecord, Tenant
from app.services import scorecard_resolvers as R
from app.services import sync as S
from app.services.scorecard_routing import FORUM_APPOINTMENT_SPECS, run_spec, validate_spec

TZ = ZoneInfo("America/Denver")
MON, WED, SUN = dt.date(2026, 7, 27), dt.date(2026, 7, 29), dt.date(2026, 8, 2)
PREV_MON, PREV_SUN = dt.date(2026, 7, 20), dt.date(2026, 7, 26)


@pytest.fixture
async def forum_env():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        t = Tenant(slug=f"ff-{uuid.uuid4().hex[:8]}", name="Forum Feed Test")
        s.add(t)
        await s.flush()
        # A UNIQUE key, never "springb": the feeds key off business_id, and many other tests look the
        # seeded Spring B business up by key alone (scalar_one) — a second "springb" here broke 14
        # of them later in the same run.
        b = Business(tenant_id=t.id, key=f"fb{uuid.uuid4().hex[:8]}", name="Spring B", tag="mb",
                     kind="membership")
        s.add(b)
        await s.flush()
        s.add(Integration(tenant_id=t.id, provider="ghl", business_id=b.id, status="connected",
                          last_synced_at=dt.datetime.now(dt.timezone.utc)))
        await s.commit()
        yield {"tid": t.id, "bid": b.id}


def _rec(e, kind, ext, **kw):
    return MetricRecord(tenant_id=e["tid"], business_id=e["bid"], source="ghl", kind=kind,
                        external_id=ext, **kw)


def _opp(ext, stage, moved="2026-07-29T16:00:00Z", source=None):
    """A 'recruiting' row as sync_ghl builds it for an open sales-funnel opportunity."""
    return {"external_id": ext, "name": ext,
            "meta": {"stage": stage, "last_stage_change_at": moved, "opp_source": source}}


# ── stage groups ──────────────────────────────────────────────────────────────────────────────
def test_stage_group_is_the_name_prefix():
    g = S._forum_stage_group
    assert g("VIP Guest: Opt-In") == g("VIP Guest: Confirmed") == "vip_guest"
    assert g("Leads: (Qualifi)") == "leads"
    assert g("Hot Nurture: Current VIP Event") == "hot_nurture"
    assert g("Sent Contract: Dual - PIF") == "sent_contract"
    assert g("Onboarded") == "onboarded"                 # no colon → the whole name
    assert g("") == g(None) == "other"


# ── Members Added ─────────────────────────────────────────────────────────────────────────────
async def test_members_added_counts_enrollments_in_the_week(forum_env):
    e = forum_env
    async with SessionLocal() as s:
        s.add_all([
            _rec(e, "member", "m1", status="active", meta={"membership": {"enrollment_date": "2026-07-28"}}),
            _rec(e, "member", "m2", status="active",
                 meta={"membership": {"enrollment_date": "2026-08-02T10:00:00"}}),
            _rec(e, "member", "m3", status="active", meta={"membership": {"enrollment_date": "2026-07-26"}}),
            _rec(e, "member", "m4", status="admin",                         # staff, not a member
                 meta={"membership": {"enrollment_date": "2026-07-29"}}),
            _rec(e, "member", "m5", status="active", meta={"membership": {}}),   # no date: unplaceable
        ])
        await s.commit()
        assert await R.forum_members_added(s, e["tid"], e["bid"], MON, SUN) == 2.0
        assert await R.forum_members_added(s, e["tid"], e["bid"], PREV_MON, PREV_SUN) == 1.0


async def test_members_added_is_unavailable_when_the_forum_feed_is_down(forum_env):
    e = forum_env
    async with SessionLocal() as s:
        integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == e["tid"]))).scalar_one()
        integ.status = "error"
        await s.commit()
        assert await R.forum_members_added(s, e["tid"], e["bid"], MON, SUN) is R.UNAVAILABLE


# ── stage-change tracking ─────────────────────────────────────────────────────────────────────
async def test_first_ever_sync_is_a_baseline_not_a_backfill(forum_env):
    """No prior snapshot → nothing is emitted (and no tracking marker), so counts start from the
    first real diff rather than inventing history from each opportunity's current stage."""
    e = forum_env
    async with SessionLocal() as s:
        n = await S._record_forum_stage_changes(s, e["tid"], e["bid"],
                                                [_opp("o1", "VIP Guest: Opt-In")], TZ)
        await s.commit()
        assert n == 0
        assert (await s.execute(select(MetricRecord.id).where(
            MetricRecord.tenant_id == e["tid"],
            MetricRecord.kind.in_(("forum_stage_event", "forum_stage_tracking"))))).first() is None


async def test_stage_changes_are_group_entries(forum_env):
    e = forum_env
    async with SessionLocal() as s:
        s.add_all([                                        # the PRIOR snapshot (last sync)
            _rec(e, "recruiting", "moved", status="open", meta={"stage": "Leads: (Qualifi)"}),
            _rec(e, "recruiting", "within", status="open", meta={"stage": "VIP Guest: Opt-In"}),
            _rec(e, "recruiting", "still", status="open", meta={"stage": "VIP Guest: Confirmed"}),
        ])
        await s.commit()
        now_opps = [
            _opp("moved", "VIP Guest: Opt-In"),           # Leads → VIP Guest: an entry
            _opp("within", "VIP Guest: Confirmed"),       # VIP → VIP: NOT a second entry
            _opp("still", "VIP Guest: Confirmed"),        # unchanged
            _opp("fresh", "VIP Guest: Opt-In", source="Member Referral"),   # new, straight into VIP
        ]
        n = await S._record_forum_stage_changes(s, e["tid"], e["bid"], now_opps, TZ)
        await s.commit()
        assert n == 2
        evs = (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == e["tid"], MetricRecord.kind == "forum_stage_event"))).scalars().all()
        assert sorted(ev.meta["opp_id"] for ev in evs) == ["fresh", "moved"]
        assert all(ev.segment == "vip_guest" and ev.occurred_on == WED for ev in evs)
        assert next(ev for ev in evs if ev.meta["opp_id"] == "fresh").meta["opp_source"] == "Member Referral"
        marks = (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == e["tid"], MetricRecord.kind == "forum_stage_tracking"))).scalars().all()
        assert len(marks) == 1                             # written once, on the first real diff
        # a re-run against the same prior state writes nothing new
        assert await S._record_forum_stage_changes(s, e["tid"], e["bid"], now_opps, TZ) == 0


# ── VIP Guests Added ──────────────────────────────────────────────────────────────────────────
async def test_vip_guests_added_is_unavailable_before_tracking_began(forum_env):
    e = forum_env
    async with SessionLocal() as s:
        assert await R.forum_vip_guests_added(s, e["tid"], e["bid"], MON, SUN) is R.UNAVAILABLE
        s.add(_rec(e, "forum_stage_tracking", "since", occurred_on=WED, status="tracking"))
        s.add_all([
            _rec(e, "forum_stage_event", "a", segment="vip_guest", occurred_on=WED, status="entered"),
            _rec(e, "forum_stage_event", "b", segment="vip_guest", occurred_on=SUN, status="entered"),
            _rec(e, "forum_stage_event", "c", segment="leads", occurred_on=WED, status="entered"),
        ])
        await s.commit()
        assert await R.forum_vip_guests_added(s, e["tid"], e["bid"], MON, SUN) == 2.0
        # the week before tracking began was never observed: UNAVAILABLE, never a 0
        assert await R.forum_vip_guests_added(s, e["tid"], e["bid"], PREV_MON, PREV_SUN) is R.UNAVAILABLE


# ── Forum calendar → Appointments Set / Met ───────────────────────────────────────────────────
class _Resp:
    """The bits of an httpx.Response the calendar sync reads."""
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


async def test_forum_calendar_feeds_appointments_set_and_met(forum_env, monkeypatch):
    e = forum_env
    calls = []
    payload = {"events": [
        {"id": "ev1", "startTime": "2026-07-29T17:00:00Z", "dateAdded": "2026-07-21T15:00:00Z",
         "appointmentStatus": "Confirmed", "contactId": "c1"},
        {"id": "ev2", "startTime": "2026-07-30T17:00:00Z", "dateAdded": "2026-07-28T15:00:00Z",
         "appointmentStatus": "showed", "contactId": "c2"},
    ]}

    async def fake_request(method, path, **kw):
        assert path == "/calendars/events" and kw["params"]["calendarId"] == "cal"
        calls.append(kw["params"])
        return _Resp(200, payload)

    monkeypatch.setattr(S.ghl, "ghl_request", fake_request)
    now = dt.datetime(2026, 8, 1, 12, tzinfo=dt.timezone.utc)
    async with SessionLocal() as s:
        assert await S.sync_forum_calendar(s, e["tid"], e["bid"], "tok", "loc", ["cal"], TZ, now=now) == 2
        backfill_calls = len(calls)
        assert backfill_calls >= 4, "the first sync backfills 120 days in month-sized chunks"
        rows = {(r.kind, r.external_id): r for r in (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == e["tid"],
            MetricRecord.kind.in_(("forum_appt", "forum_appt_set"))))).scalars()}
        assert rows[("forum_appt", "ev1")].occurred_on == WED          # dated by START
        assert rows[("forum_appt", "ev1")].status == "confirmed"       # normalised to lower case
        assert rows[("forum_appt_set", "ev1")].occurred_on == dt.date(2026, 7, 21)   # dated by BOOKING

        # GHL later marks ev1 'showed': an update in place, and only the recent window is re-read
        payload["events"][0]["appointmentStatus"] = "showed"
        calls.clear()
        await S.sync_forum_calendar(s, e["tid"], e["bid"], "tok", "loc", ["cal"], TZ, now=now)
        assert len(calls) < backfill_calls, "a backfilled calendar only re-reads the recent window"
        appts = (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == e["tid"], MetricRecord.kind == "forum_appt"))).scalars().all()
        assert len(appts) == 2 and all(a.status == "showed" for a in appts)

        for spec in FORUM_APPOINTMENT_SPECS.values():
            assert validate_spec(spec) == []
        met = await run_spec(FORUM_APPOINTMENT_SPECS["forum_appointments_met"],
                             s, e["tid"], e["bid"], MON, SUN)
        booked = await run_spec(FORUM_APPOINTMENT_SPECS["forum_appointments_set"],
                                s, e["tid"], e["bid"], MON, SUN)
        assert met == 2.0          # both showed, both started this week
        assert booked == 1.0       # only ev2 was BOOKED this week; ev1 was booked the week before


async def test_a_calendar_the_token_cannot_read_raises_instead_of_reading_zero(forum_env, monkeypatch):
    """ghl_json turns any non-200 into {} — which a first version read as "0 appointments" for a
    calendar it could not see. A bad calendar now raises (so the sync log names it), while a good
    calendar in the same run is still written and marked backfilled; the bad one is NOT marked, so it
    backfills once it works."""
    e = forum_env

    async def fake_request(method, path, **kw):
        if kw["params"]["calendarId"] == "bad":
            return _Resp(403, {"message": "The token is not authorized for this scope."})
        return _Resp(200, {"events": [{"id": "ok1", "startTime": "2026-07-29T17:00:00Z",
                                       "dateAdded": "2026-07-28T15:00:00Z", "appointmentStatus": "showed"}]})

    monkeypatch.setattr(S.ghl, "ghl_request", fake_request)
    now = dt.datetime(2026, 8, 1, 12, tzinfo=dt.timezone.utc)
    async with SessionLocal() as s:
        with pytest.raises(RuntimeError, match="calendar bad: HTTP 403"):
            await S.sync_forum_calendar(s, e["tid"], e["bid"], "tok", "loc", ["good", "bad"], TZ, now=now)
        got = {(r.kind, r.external_id) for r in (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == e["tid"],
            MetricRecord.kind.in_(("forum_appt", "forum_appt_cal"))))).scalars()}
    assert ("forum_appt", "ok1") in got, "the good calendar's appointments were lost"
    assert ("forum_appt_cal", "good") in got and ("forum_appt_cal", "bad") not in got
