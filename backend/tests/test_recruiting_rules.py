"""ULRG Recruiting — the rule engine (RECRUITING-SPEC §6, Phase 3).

Phase 3's "Done when" asks for four things and this file is those four: every rule's positive AND
negative case, window idempotency, auto-clear when a condition lapses, and business-local due
labels across midnight and a DST boundary.

The negative cases are the point. A rule that fires is visible the moment somebody opens the tab;
a rule that fires when it SHOULD NOT is a recruiter texting a candidate who replied an hour ago,
and nobody reports that as a bug -- they just stop trusting the list.

The rules are pure functions of plain data, so most of this needs no database at all.
"""
import datetime as dt
import types
import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    Business, Integration, RecruitingCandidate, RecruitingQueueItem, RecruitingSeat,
)
from app.seed import seed
from app.services import recruiting_rules as R

TZ = R.business_tz()
NOW = dt.datetime(2026, 9, 23, 18, 0, tzinfo=dt.timezone.utc)          # noon in Denver
SDR = uuid.uuid4()
TL = uuid.uuid4()


def _cand(**kw):
    base = dict(id=uuid.uuid4(), stage_group="Met", status="open", owner_seat_id=TL,
                entered_stage_at=NOW - dt.timedelta(days=1),
                created_at_src=NOW - dt.timedelta(days=30), first_seen_at=NOW - dt.timedelta(days=30),
                ghl_task_id=None, name="Sunny Kaur")
    base.update(kw)
    return types.SimpleNamespace(**base)


def _act(cand, kind, when):
    return types.SimpleNamespace(candidate_id=cand.id, kind=kind, occurred_at=when)


def _appt(cand, start, status="confirmed", seat_id=TL, created=None, event_id="ev1"):
    return types.SimpleNamespace(candidate_id=cand.id, start_at=start, status=status,
                                 seat_id=seat_id, event_id=event_id,
                                 created_at_src=created or (NOW - dt.timedelta(days=2)))


def _facts(cands, appts=(), acts=(), watch=None):
    by = {}
    for a in acts:
        by.setdefault(str(a.candidate_id), []).append(a)
    return R.Facts(candidates=list(cands), appointments=list(appts), activity_by_candidate=by,
                   seats_by_id={}, sdr_seat_id=SDR, watch_start=watch,
                   group_owner={"Sourced": "sdr", "Appointment set": "sdr", "Met": "team_leader",
                                "Offer out": "team_leader", "Signed": "team_leader",
                                "Nurture": "team_leader"})


def _fire(rule_key, cands, appts=(), acts=(), **over):
    cfg = dict(R.DEFAULTS[rule_key], **over)
    fn = dict(R.RULES)[rule_key]
    facts = _facts(cands, appts, acts)
    return [fn(facts, c, NOW, TZ, cfg) for c in facts.candidates]


# ── new_lead_untouched ──────────────────────────────────────────────────────────────────────

def test_new_lead_untouched_fires_and_then_does_not():
    fresh = _cand(stage_group="Sourced", owner_seat_id=None,
                  created_at_src=NOW - dt.timedelta(hours=3))
    [hit] = _fire("new_lead_untouched", [fresh])
    assert hit and hit.primary_action == "text"
    # Unassigned goes to the SDR rather than nowhere: that is what "the SDR owns up to
    # Appointment set" has to mean when nobody has been assigned yet.
    assert hit.owner_seat_id == SDR
    assert "nobody has reached out" in hit.why

    # NEGATIVE: one outbound of any kind and the rule is silent.
    assert _fire("new_lead_untouched", [fresh], acts=[_act(fresh, "sms_out", NOW - dt.timedelta(minutes=5))]) == [None]
    # NEGATIVE: too new to nag about.
    assert _fire("new_lead_untouched", [_cand(stage_group="Sourced", created_at_src=NOW - dt.timedelta(minutes=10))]) == [None]
    # NEGATIVE: already signed, or parked in nurture.
    assert _fire("new_lead_untouched", [_cand(stage_group="Signed", created_at_src=NOW - dt.timedelta(days=9))]) == [None]
    assert _fire("new_lead_untouched", [_cand(stage_group="Nurture", created_at_src=NOW - dt.timedelta(days=9))]) == [None]


# ── appt_24h ────────────────────────────────────────────────────────────────────────────────

def test_appt_24h_fires_inside_the_window_only():
    c = _cand()
    start = NOW + dt.timedelta(hours=24)
    [hit] = _fire("appt_24h", [c], appts=[_appt(c, start, status="new")])
    assert hit and hit.primary_action == "text" and hit.window_key == "ev1"
    assert "Thursday at" in hit.why                     # rendered without POSIX-only strftime

    # NEGATIVE: outside the window on both sides.
    assert _fire("appt_24h", [c], appts=[_appt(c, NOW + dt.timedelta(hours=4), status="new")]) == [None]
    assert _fire("appt_24h", [c], appts=[_appt(c, NOW + dt.timedelta(hours=60), status="new")]) == [None]
    # NEGATIVE: already confirmed, or already happened.
    assert _fire("appt_24h", [c], appts=[_appt(c, start, status="confirmed")]) == [None]
    assert _fire("appt_24h", [c], appts=[_appt(c, start, status="showed")]) == [None]
    # NEGATIVE: they replied after it was booked, which IS the confirmation whatever the status says.
    booked = NOW - dt.timedelta(days=2)
    assert _fire("appt_24h", [c], appts=[_appt(c, start, status="new", created=booked)],
                 acts=[_act(c, "sms_in", booked + dt.timedelta(hours=1))]) == [None]


# ── met_no_next_step ────────────────────────────────────────────────────────────────────────

def test_met_no_next_step_respects_a_task_or_a_booking():
    c = _cand(stage_group="Met", entered_stage_at=NOW - dt.timedelta(days=4))
    [hit] = _fire("met_no_next_step", [c])
    assert hit and hit.primary_action == "email"

    # NEGATIVE: an open next-step task in GHL is the next step.
    assert _fire("met_no_next_step", [_cand(stage_group="Met", ghl_task_id="t1",
                                            entered_stage_at=NOW - dt.timedelta(days=4))]) == [None]
    # NEGATIVE: something on the calendar ahead of them.
    assert _fire("met_no_next_step", [c], appts=[_appt(c, NOW + dt.timedelta(days=3))]) == [None]
    # ...but a CANCELLED future appointment is not a next step.
    assert _fire("met_no_next_step", [c],
                 appts=[_appt(c, NOW + dt.timedelta(days=3), status="cancelled")])[0] is not None
    # NEGATIVE: not long enough ago, and not in Met at all.
    assert _fire("met_no_next_step", [_cand(stage_group="Met", entered_stage_at=NOW - dt.timedelta(hours=6))]) == [None]
    assert _fire("met_no_next_step", [_cand(stage_group="Offer out", entered_stage_at=NOW - dt.timedelta(days=4))]) == [None]


# ── offer_out_stale ─────────────────────────────────────────────────────────────────────────

def test_offer_out_stale_counts_silence_not_age():
    old = _cand(stage_group="Offer out", entered_stage_at=NOW - dt.timedelta(days=7))
    [hit] = _fire("offer_out_stale", [old])
    assert hit and hit.primary_action == "call" and "have not replied at all" in hit.why

    # NEGATIVE: an offer out for a fortnight with a reply yesterday is not stale.
    assert _fire("offer_out_stale", [old],
                 acts=[_act(old, "sms_in", NOW - dt.timedelta(days=1))]) == [None]
    # POSITIVE: a reply, but older than the threshold — and the line says when.
    [stale] = _fire("offer_out_stale", [old],
                    acts=[_act(old, "sms_in", NOW - dt.timedelta(days=9))])
    assert stale and "No reply since" in stale.why
    # NEGATIVE: too fresh.
    assert _fire("offer_out_stale", [_cand(stage_group="Offer out", entered_stage_at=NOW - dt.timedelta(days=2))]) == [None]


# ── no_touch_7d and stage_14d ───────────────────────────────────────────────────────────────

def test_no_touch_and_stuck_stage_fire_and_stay_silent():
    quiet = _cand(entered_stage_at=NOW - dt.timedelta(days=3),
                  created_at_src=NOW - dt.timedelta(days=20))
    [hit] = _fire("no_touch_7d", [quiet])
    assert hit and hit.primary_action == "text"
    # NEGATIVE: any activity at all, inbound or out, resets it.
    assert _fire("no_touch_7d", [quiet], acts=[_act(quiet, "note", NOW - dt.timedelta(days=1))]) == [None]
    # NEGATIVE: signed and nurture are not chased.
    assert _fire("no_touch_7d", [_cand(stage_group="Signed", created_at_src=NOW - dt.timedelta(days=40))]) == [None]

    stuck = _cand(entered_stage_at=NOW - dt.timedelta(days=20))
    [hit2] = _fire("stage_14d", [stuck])
    assert hit2 and hit2.primary_action == "call" and "Met" in hit2.why
    assert _fire("stage_14d", [_cand(entered_stage_at=NOW - dt.timedelta(days=5))]) == [None]


# ── appt_set_no_event ───────────────────────────────────────────────────────────────────────

def test_appt_set_without_an_event_is_the_sdrs_problem():
    c = _cand(stage_group="Appointment set", owner_seat_id=None,
              entered_stage_at=NOW - dt.timedelta(days=2))
    [hit] = _fire("appt_set_no_event", [c])
    assert hit and hit.primary_action == "book" and hit.owner_seat_id == SDR
    # NEGATIVE: there is an appointment ahead of them after all.
    assert _fire("appt_set_no_event", [c], appts=[_appt(c, NOW + dt.timedelta(days=1))]) == [None]
    # NEGATIVE: a PAST appointment does not count — being booked last week is why they are stuck.
    assert _fire("appt_set_no_event", [c], appts=[_appt(c, NOW - dt.timedelta(days=1))])[0] is not None


def test_signed_produces_no_task():
    """§6 lists `signed` as a rule and gives it no queue item on purpose: nobody needs a to-do
    saying somebody already signed. It is a stage_event and a Scorecard resolver."""
    assert "signed" not in dict(R.RULES)


# ── turning a rule off, and the bounds ──────────────────────────────────────────────────────

def test_a_rule_that_is_off_produces_nothing():
    """Off means THAT rule is silent, not that the candidate disappears. A neglected offer also
    trips no_touch_7d and stage_14d, and switching one rule off must not quietly switch those
    off with it."""
    old = _cand(stage_group="Offer out", entered_stage_at=NOW - dt.timedelta(days=30))
    off = {i.rule_key for i in R.evaluate(_facts([old]), NOW, TZ,
                                          R.clean_rules({"offer_out_stale": {"on": False}}))}
    on = {i.rule_key for i in R.evaluate(_facts([old]), NOW, TZ, R.clean_rules({}))}
    assert "offer_out_stale" in on and "offer_out_stale" not in off
    assert off == on - {"offer_out_stale"}, "turning one rule off silenced another"


def test_a_backwards_window_is_refused_rather_than_matching_nothing():
    """from >= to would silently match no appointment, which reads as "the rule is broken"."""
    with pytest.raises(ValueError):
        R.clean_rules({"appt_24h": {"from_hours": 40, "to_hours": 10}}, strict=True)
    assert R.clean_rules({"appt_24h": {"from_hours": 40, "to_hours": 10}})["appt_24h"] == R.DEFAULTS["appt_24h"]


# ── due labels, business-local, across midnight and DST ─────────────────────────────────────

def test_due_labels_are_decided_in_the_brokerages_own_day():
    """23:30 UTC is still YESTERDAY afternoon in Denver. A label computed in UTC says "Tomorrow"
    to somebody whose tomorrow has not started."""
    late_utc = dt.datetime(2026, 9, 23, 23, 30, tzinfo=dt.timezone.utc)   # 17:30 Denver, same day
    assert R.due_label(late_utc + dt.timedelta(hours=1), late_utc, TZ)["label"] == "Today"
    # An hour later in UTC it is a new UTC day and still the same Denver afternoon.
    past_utc = dt.datetime(2026, 9, 24, 1, 0, tzinfo=dt.timezone.utc)     # 19:00 Denver, 23rd
    assert R.due_label(past_utc + dt.timedelta(minutes=30), past_utc, TZ)["label"] == "Today"
    # Overdue at 4pm, read at 7pm the same day: LATE, not "Today". Comparing dates got this
    # wrong, and it is the case the chip exists for.
    overdue = R.due_label(past_utc - dt.timedelta(hours=3), past_utc, TZ)
    assert overdue["tone"] == "late" and overdue["label"] == "3 hrs late"
    assert R.due_label(past_utc - dt.timedelta(minutes=20), past_utc, TZ)["label"] == "20 min late"
    assert R.due_label(past_utc - dt.timedelta(days=3), past_utc, TZ)["label"] == "3 days late"
    assert R.due_label(None, past_utc, TZ)["tone"] == "mute"


def test_snooze_lands_on_a_business_morning_across_dst():
    """Friday snoozes to Monday, and the hour survives the November fall-back — 06:00 local is
    06:00 local, not 06:00 minus an hour because the offset moved."""
    friday = dt.datetime(2026, 9, 25, 20, 0, tzinfo=dt.timezone.utc)
    landed = R.next_business_morning(friday, TZ)
    assert landed.astimezone(TZ).weekday() == 0 and landed.astimezone(TZ).hour == 6

    before_dst = dt.datetime(2026, 10, 30, 20, 0, tzinfo=dt.timezone.utc)   # Fri before the change
    after = R.next_business_morning(before_dst, TZ)
    assert after.astimezone(TZ).hour == 6, "06:00 local must stay 06:00 local across DST"
    assert after.astimezone(TZ).weekday() == 0


# ── the DB half: idempotency and auto-clear ─────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _ready_workspace(s):
    biz = (await s.execute(select(Business).where(Business.key == "ulrg"))).scalar_one()
    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == biz.tenant_id,
        Integration.provider == "ghl_recruiting"))).scalars().first()
    if integ is None:
        integ = Integration(tenant_id=biz.tenant_id, provider="ghl_recruiting",
                            business_id=biz.id, status="connected")
        s.add(integ)
    integ.config = {"location_id": "loc", "pipeline_id": "pipe",
                    "recruiting_stage_groups": [["Offer out", "team_leader", ["st_offer"]]]}
    seat = (await s.execute(select(RecruitingSeat).where(
        RecruitingSeat.tenant_id == biz.tenant_id,
        RecruitingSeat.display_name == "Rules Leader"))).scalars().first()
    if seat is None:
        seat = RecruitingSeat(tenant_id=biz.tenant_id, business_id=biz.id, role="team_leader",
                              display_name="Rules Leader")
        s.add(seat)
    await s.flush()
    return biz.tenant_id, biz, integ, seat


async def test_running_the_tick_twice_makes_one_item():
    """The whole reason `window_key` and its unique constraint exist. The tick runs every five
    minutes; without this it would file 288 copies of the same job a day."""
    async with SessionLocal() as s:
        t, biz, _, seat = await _ready_workspace(s)
        cand = RecruitingCandidate(
            tenant_id=t, business_id=biz.id, opportunity_id="opp_rules_1",
            stage_id="st_offer", stage_group="Offer out", status="open", owner_seat_id=seat.id,
            name="Stale Sam", entered_stage_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=9))
        s.add(cand)
        await s.commit()

        first = await R.build_queue(s, t)
        second = await R.build_queue(s, t)
        rows = (await s.execute(select(RecruitingQueueItem).where(
            RecruitingQueueItem.tenant_id == t,
            RecruitingQueueItem.candidate_id == cand.id))).scalars().all()

    assert first["opened"] == 1 and second["opened"] == 0
    assert len(rows) == 1 and rows[0].rule_key == "offer_out_stale"
    assert rows[0].state == "open" and rows[0].primary_action == "call"


async def test_an_item_clears_itself_when_the_work_happens_anywhere():
    """The condition stops holding and the item goes -- whether the reply arrived through Axcion
    or somebody answered in GHL. `cleared_by="rule"` records which, because "I did this" and
    "this got done" are different sentences in somebody's own history."""
    async with SessionLocal() as s:
        t, biz, _, seat = await _ready_workspace(s)
        cand = RecruitingCandidate(
            tenant_id=t, business_id=biz.id, opportunity_id="opp_rules_2",
            stage_id="st_offer", stage_group="Offer out", status="open", owner_seat_id=seat.id,
            name="Quiet Quinn",
            entered_stage_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=9))
        s.add(cand)
        await s.commit()
        await R.build_queue(s, t)

        # The work happens: they move on in GHL, and the next sync brings the new stage.
        cand.stage_group, cand.stage_id = "Signed", "st_signed"
        await s.commit()
        summary = await R.build_queue(s, t)

        row = (await s.execute(select(RecruitingQueueItem).where(
            RecruitingQueueItem.tenant_id == t,
            RecruitingQueueItem.candidate_id == cand.id))).scalars().first()

    assert summary["auto_cleared"] >= 1
    assert row.state == "auto_cleared" and row.cleared_by == "rule" and row.cleared_at is not None


async def test_something_already_done_does_not_come_back():
    """A reconcile that rebuilt the list would undo every Done the moment the tick ran, which is
    the failure that makes people stop using a queue."""
    async with SessionLocal() as s:
        t, biz, _, seat = await _ready_workspace(s)
        cand = RecruitingCandidate(
            tenant_id=t, business_id=biz.id, opportunity_id="opp_rules_3",
            stage_id="st_offer", stage_group="Offer out", status="open", owner_seat_id=seat.id,
            name="Done Dana",
            entered_stage_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=9))
        s.add(cand)
        await s.commit()
        await R.build_queue(s, t)

        row = (await s.execute(select(RecruitingQueueItem).where(
            RecruitingQueueItem.tenant_id == t,
            RecruitingQueueItem.candidate_id == cand.id))).scalars().first()
        row.state, row.cleared_by = "done", "user"
        row.cleared_at = dt.datetime.now(dt.timezone.utc)
        await s.commit()

        await R.build_queue(s, t)          # the condition STILL holds; the rules re-claim it
        again = (await s.execute(select(RecruitingQueueItem).where(
            RecruitingQueueItem.id == row.id))).scalars().first()

    assert again.state == "done", "a reconcile reopened something a person had cleared"


async def test_an_unconfigured_workspace_builds_nothing_rather_than_erroring():
    async with SessionLocal() as s:
        t, _, integ, _ = await _ready_workspace(s)
        keep = integ.config
        integ.config = {"location_id": "loc"}          # connected, no pipeline chosen
        await s.commit()
        out = await R.build_queue(s, t)
        integ.config = keep
        await s.commit()
    assert out["skipped"] == "not configured" and out["opened"] == 0


# ── the watch-start floor ────────────────────────────────────────────────────────────────────
#
# Two rules reason from the ABSENCE of an activity row. The poll reads conversations forward from
# the day the location was connected and never backfills, so for a candidate last contacted before
# that there is no row and never will be -- which makes "nobody has reached out yet" a claim about
# our own blindness. On ULRG's first live day it was 202 such claims across 1,654 candidates, only
# 19 of which had any history we had actually seen.

def _rule(key, cand, facts, **over):
    return dict(R.RULES)[key](facts, cand, NOW, TZ, dict(R.DEFAULTS[key], **over))


def test_a_candidate_that_arrived_before_we_were_watching_is_not_a_new_lead():
    watch = NOW - dt.timedelta(hours=6)
    old = _cand(stage_group="Sourced", owner_seat_id=None,
                created_at_src=NOW - dt.timedelta(days=270))
    assert _rule("new_lead_untouched", old, _facts([old], watch=watch)) is None,         "claimed nobody reached out to a candidate that predates the connection"

    # One that genuinely arrived after we connected is still caught.
    fresh = _cand(stage_group="Sourced", owner_seat_id=None,
                  created_at_src=NOW - dt.timedelta(hours=3))
    hit = _rule("new_lead_untouched", fresh, _facts([fresh], watch=watch))
    assert hit is not None and hit.rule_key == "new_lead_untouched"

    # With no watch_start known, behaviour is exactly what it was.
    assert _rule("new_lead_untouched", old, _facts([old])) is not None


def test_silence_is_measured_from_when_we_started_listening():
    old = _cand(stage_group="Sourced", created_at_src=NOW - dt.timedelta(days=270))

    # Connected this morning: there is no seven-day silence to report, however long the candidate
    # has been sitting in GHL.
    today = _facts([old], watch=NOW - dt.timedelta(hours=6))
    assert _rule("no_touch_7d", old, today) is None,         "reported a silence longer than we have been watching"

    # Watching three weeks and still nothing seen: reportable, and it says which it is.
    weeks = _facts([old], watch=NOW - dt.timedelta(days=21))
    hit = _rule("no_touch_7d", old, weeks)
    assert hit is not None
    assert "since we connected" in hit.why, hit.why
    assert "months" not in hit.why, "still claiming a silence it never observed"


def test_a_real_activity_row_always_beats_the_floor():
    """The floor covers only the case where we have NO evidence. A row we actually saw is
    evidence even when it predates the floor, and must not be overridden by it."""
    watch = NOW - dt.timedelta(hours=6)
    old = _cand(stage_group="Sourced", created_at_src=NOW - dt.timedelta(days=270))
    # The poll's first run looks back 30 days, so activity CAN predate the first sync.
    seen = _act(old, "sms_out", NOW - dt.timedelta(days=23))
    hit = _rule("no_touch_7d", old, _facts([old], acts=[seen], watch=watch))
    assert hit is not None, "a 23-day silence we DID observe was suppressed by the floor"
    assert "since we connected" not in hit.why, "reported observed silence as our own blindness"

    # An outbound we saw still cancels the new-lead rule regardless of the floor.
    fresh = _cand(stage_group="Sourced", owner_seat_id=None,
                  created_at_src=NOW - dt.timedelta(hours=3))
    touched = _facts([fresh], acts=[_act(fresh, "sms_out", NOW - dt.timedelta(hours=1))],
                     watch=watch)
    assert _rule("new_lead_untouched", fresh, touched) is None


def test_load_facts_derives_the_watch_start_from_the_earliest_candidate():
    """It is derived, not stored, so it needs no migration and cannot drift out of date."""
    import inspect
    src = inspect.getsource(R.load_facts)
    assert "watch_start=" in src and "first_seen_at" in src
