"""ULRG Recruiting — pace, goals, commitments and the Scorecard bridge (Phase 5).

§9's list: pace and verdict at the edges (day one, the last day, the goal already met), the
commitment band on each weekday, and the resolvers against hand-entered values.

The resolver tests carry the most weight. `UNAVAILABLE` and `0.0` were spelled the same thing
once in this codebase and it cost weeks of collected scorecard history, because a disconnected
feed erased a fresh week every day as the window slid. A recruiting resolver that returns 0 when
it simply cannot look would do exactly that to rows a person has been typing by hand for months.
"""
import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    Business, Integration, RecruitingAppointment, RecruitingCandidate, RecruitingCommitment,
    RecruitingGoal, RecruitingSeat, RecruitingStageEvent,
)
from app.seed import seed
from app.services import recruiting_accountability as A
from app.services.scorecard_resolvers import RESOLVERS, UNAVAILABLE


# ── pace and verdict, at the edges ──────────────────────────────────────────────────────────

def test_pace_on_day_one_does_not_declare_a_month_lost():
    """One business day in, nobody has signed anybody. If that read "reset", the verdict would be
    useless for the first week of every month."""
    day_one = A.judge(signed=0, goal=9, elapsed=1 / 22, best=7)
    assert day_one["pace"] == 0.0
    assert day_one["need"] == 9
    # Needing 9 in a month when the best month ever is 7 is a stretch, not a reset -- and
    # certainly not "ahead".
    assert day_one["verdict"] in ("stretch", "reset")
    # With a normal target it is plainly catchable on day one.
    assert A.judge(0, 5, 1 / 22, 7)["verdict"] == "catchable"


def test_a_met_goal_reads_ahead_and_needs_nothing():
    met = A.judge(signed=9, goal=9, elapsed=0.6, best=7)
    assert met["need"] == 0 and met["verdict"] == "ahead"
    over = A.judge(signed=12, goal=9, elapsed=0.6, best=7)
    assert over["need"] == 0, "a need cannot be negative"
    assert over["pace_pct"] > 100


def test_the_last_day_cannot_divide_by_zero():
    """elapsed == 1.0 means no remaining fraction to divide by. A month that ends with a gap is
    a reset, not a traceback."""
    done = A.judge(signed=5, goal=9, elapsed=1.0, best=7)
    assert done["need"] == 4
    assert done["pace"] == 5.0
    assert done["verdict"] in (None, "reset")
    # And a month that ended on target is still ahead.
    assert A.judge(9, 9, 1.0, 7)["verdict"] == "ahead"


def test_no_goal_means_no_verdict_rather_than_a_flattering_one():
    """Zero would read as "goal met". Null is the only honest answer before somebody sets one."""
    out = A.judge(signed=5, goal=None, elapsed=0.5, best=7)
    assert out["goal"] is None and out["verdict"] is None and out["need"] is None
    assert out["pace"] == 10.0, "pace is still knowable without a target"


def test_a_team_with_no_history_gets_no_verdict_not_a_guess():
    """`best` is what makes "catchable" mean anything. Without any completed month there is
    nothing to judge reachability against, and inventing one would be a guess wearing a chip."""
    assert A.judge(2, 9, 0.5, None)["verdict"] is None


# ── the commitment band, weekday by weekday ─────────────────────────────────────────────────

def test_the_band_is_business_days_not_calendar_days():
    """§7: week_elapsed is the business day of the week ÷ 5. Wednesday is 0.6, not 3/7 -- a band
    on calendar days shows somebody behind on Saturday for work nobody does on Saturday."""
    expected = {0: 0.2, 1: 0.4, 2: 0.6, 3: 0.8, 4: 1.0, 5: 1.0, 6: 1.0}
    monday = dt.date(2026, 9, 21)
    for offset, want in expected.items():
        assert A.week_elapsed(monday + dt.timedelta(days=offset)) == want, f"day {offset}"


def test_the_band_reads_on_target_midweek():
    """Three of four held by Wednesday IS on pace: 3 / (4 × 0.6) = 125%."""
    assert A.band_pct(3, 4, 0.6) == 125
    assert A.band_pct(2, 4, 0.6) == 83          # slightly behind
    assert A.band_pct(1, 4, 0.6) == 42          # well behind
    assert A.band_pct(4, 4, 1.0) == 100         # finished the week on the number


def test_a_band_without_a_commitment_is_nothing_rather_than_green():
    """A colour with no target behind it is worse than no colour: it says "fine" about a number
    nobody chose."""
    assert A.band_pct(5, None, 0.6) is None
    assert A.band_pct(5, 0, 0.6) is None
    assert A.band_pct(0, 4, 0) is None


def test_monday_is_business_local():
    for day, monday in ((dt.date(2026, 9, 21), dt.date(2026, 9, 21)),
                        (dt.date(2026, 9, 23), dt.date(2026, 9, 21)),
                        (dt.date(2026, 9, 27), dt.date(2026, 9, 21))):
        assert A.monday_of(day) == monday


# ── the path line's four cases ──────────────────────────────────────────────────────────────

def test_the_path_line_names_the_goal_only_when_there_is_one():
    rows = [{"stage": "Offer out"}] * 3 + [{"stage": "Met"}] * 2
    assert A.path_line(5, 9, rows) == ("Closing every offer out gets you to 8. "
                                       "1 more from Met gets you to 9.")
    assert A.path_line(5, 7, [{"stage": "Offer out"}] * 2) == "Closing every offer out gets you to 7."
    assert "still has to come from somewhere" in A.path_line(2, 9, [{"stage": "Met"}])
    assert A.path_line(9, 9, rows).startswith("Goal met")
    assert A.path_line(3, None, rows) == "3 offers out · 2 met and deciding. 3 signed so far this month."
    assert A.path_line(0, 9, []) == "Nobody is past the first meeting yet."


# ── the database half ───────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _ws(s, *, ready=True):
    biz = (await s.execute(select(Business).where(Business.key == "ulrg"))).scalar_one()
    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == biz.tenant_id,
        Integration.provider == "ghl_recruiting"))).scalars().first()
    if integ is None:
        integ = Integration(tenant_id=biz.tenant_id, provider="ghl_recruiting",
                            business_id=biz.id, status="connected")
        s.add(integ)
    integ.status = "connected"
    integ.config = ({"location_id": "loc", "pipeline_id": "pipe",
                     "synced_at": dt.datetime.now(dt.timezone.utc).isoformat()}
                    if ready else {"location_id": "loc"})
    seat = (await s.execute(select(RecruitingSeat).where(
        RecruitingSeat.tenant_id == biz.tenant_id,
        RecruitingSeat.display_name == "Acct Leader"))).scalars().first()
    if seat is None:
        seat = RecruitingSeat(tenant_id=biz.tenant_id, business_id=biz.id, role="team_leader",
                              display_name="Acct Leader")
        s.add(seat)
    await s.flush()
    await s.commit()
    return biz.tenant_id, biz, integ, seat


async def test_the_monday_rollover_fills_gaps_and_overwrites_nothing():
    """Last week's numbers become this week's DEFAULTS. A rollover that overwrote would erase the
    one thing somebody had already decided on Monday morning."""
    async with SessionLocal() as s:
        t, _, _, seat = await _ws(s)
        today = dt.date(2026, 9, 23)                       # a Wednesday
        this_week, last_week = A.monday_of(today), A.monday_of(today) - dt.timedelta(days=7)
        await s.execute(RecruitingCommitment.__table__.delete().where(
            RecruitingCommitment.tenant_id == t))
        s.add(RecruitingCommitment(tenant_id=t, seat_id=seat.id, week_start=last_week,
                                   metric="held", commit=4))
        s.add(RecruitingCommitment(tenant_id=t, seat_id=seat.id, week_start=last_week,
                                   metric="offers", commit=2))
        # Already decided for THIS week, and must survive.
        s.add(RecruitingCommitment(tenant_id=t, seat_id=seat.id, week_start=this_week,
                                   metric="held", commit=6))
        await s.commit()

        made = await A.roll_forward(s, t, today)
        got = await A.commitments_for(s, t, this_week)

    assert made == 1, "only the gap should have been filled"
    assert got[str(seat.id)]["held"] == 6, "an existing commitment was overwritten"
    assert got[str(seat.id)]["offers"] == 2, "last week's number did not carry forward"


async def test_rolling_forward_twice_changes_nothing():
    """It rides a five-minute tick, so it has to be idempotent or Monday would multiply."""
    async with SessionLocal() as s:
        t, _, _, seat = await _ws(s)
        today = dt.date(2026, 9, 23)
        first = await A.roll_forward(s, t, today)
        second = await A.roll_forward(s, t, today)
    assert second == 0, f"a second rollover created {second} more rows"


async def test_goals_are_per_period_and_do_not_bleed():
    """Setting October must not change what September was judged against. A verdict that moves
    after the fact is not a verdict."""
    async with SessionLocal() as s:
        t, _, _, seat = await _ws(s)
        await s.execute(RecruitingGoal.__table__.delete().where(RecruitingGoal.tenant_id == t))
        s.add(RecruitingGoal(tenant_id=t, seat_id=None, period_key="2026-09",
                             metric="signed", goal=9))
        s.add(RecruitingGoal(tenant_id=t, seat_id=seat.id, period_key="2026-09",
                             metric="signed", goal=3))
        s.add(RecruitingGoal(tenant_id=t, seat_id=None, period_key="2026-10",
                             metric="signed", goal=12))
        await s.commit()

        sept = await A.goals_for(s, t, "2026-09")
        octo = await A.goals_for(s, t, "2026-10")

    assert sept[None]["signed"] == 9 and sept[str(seat.id)]["signed"] == 3
    assert octo[None]["signed"] == 12
    assert str(seat.id) not in octo, "a seat goal leaked into another period"


async def test_best_month_ignores_the_month_in_progress():
    """The month running now is not evidence of what a month can hold, and counting it would make
    every early verdict read "reset"."""
    async with SessionLocal() as s:
        t, biz, _, seat = await _ws(s)
        cand = RecruitingCandidate(tenant_id=t, business_id=biz.id,
                                   opportunity_id=f"opp_best_{uuid.uuid4().hex[:6]}")
        s.add(cand)
        await s.flush()
        now = dt.datetime.now(dt.timezone.utc)
        # Two signings last month, one this month.
        last_month = (now.replace(day=1) - dt.timedelta(days=5))
        for when in (last_month, last_month - dt.timedelta(days=1), now):
            s.add(RecruitingStageEvent(tenant_id=t, candidate_id=cand.id, to_group="Signed",
                                       occurred_at=when, source="sync", actor_seat_id=seat.id))
        await s.commit()
        best = await A.best_month(s, t)

    # One candidate, so "distinct candidates in a month" is 1 however many events there were.
    assert best == 1.0, f"best month counted events or included the current month: {best}"


# ── the Scorecard bridge ────────────────────────────────────────────────────────────────────

async def test_a_resolver_that_cannot_look_returns_unavailable_not_zero():
    """THE EXPENSIVE ONE. A workspace with no pipeline chosen has not recruited nobody -- it has
    not been asked yet. Returning 0.0 would overwrite a hand-entered row, and the runner
    re-resolves the trailing weeks daily, so the hole would grow as the window slid."""
    async with SessionLocal() as s:
        t, biz, integ, _ = await _ws(s, ready=False)
        week_start = dt.date(2026, 9, 21)
        for key in ("ghl_recruiting_new", "ghl_recruiting_held", "ghl_recruiting_booked",
                    "ghl_recruiting_signed_qtd"):
            got = await RESOLVERS[key](s, t, biz.id, week_start,
                                       week_start + dt.timedelta(days=6))
            assert got is UNAVAILABLE, f"{key} returned {got!r} when it could not look"
            assert not got, f"{key}'s UNAVAILABLE must be falsy, never a number"


async def test_a_quiet_week_on_a_connected_workspace_is_a_real_zero():
    """Connected and synced with nothing that week IS zero, and the board should say so."""
    async with SessionLocal() as s:
        t, biz, _, _ = await _ws(s, ready=True)
        far_past = dt.date(2020, 1, 6)
        got = await RESOLVERS["ghl_recruiting_new"](s, t, biz.id, far_past,
                                                    far_past + dt.timedelta(days=6))
    assert got == 0.0 and got is not UNAVAILABLE


async def test_booked_and_held_count_different_weeks_for_one_meeting():
    """The SDR is measured on the booking and the Team Leader on the meeting. If one resolver
    counted both, one of them would be shown the other's work."""
    async with SessionLocal() as s:
        t, biz, _, seat = await _ws(s, ready=True)
        await s.execute(RecruitingAppointment.__table__.delete().where(
            RecruitingAppointment.tenant_id == t))
        from app.services.recruiting_rules import business_tz
        tz = business_tz()
        booked_week = dt.date(2026, 9, 14)
        held_week = dt.date(2026, 9, 21)
        s.add(RecruitingAppointment(
            tenant_id=t, event_id="ev_split", seat_id=seat.id, status="showed",
            created_at_src=dt.datetime.combine(booked_week + dt.timedelta(days=1),
                                               dt.time(10, 0), tzinfo=tz),
            start_at=dt.datetime.combine(held_week + dt.timedelta(days=2),
                                         dt.time(9, 0), tzinfo=tz)))
        await s.commit()

        b_in_booked = await RESOLVERS["ghl_recruiting_booked"](
            s, t, biz.id, booked_week, booked_week + dt.timedelta(days=6))
        h_in_booked = await RESOLVERS["ghl_recruiting_held"](
            s, t, biz.id, booked_week, booked_week + dt.timedelta(days=6))
        b_in_held = await RESOLVERS["ghl_recruiting_booked"](
            s, t, biz.id, held_week, held_week + dt.timedelta(days=6))
        h_in_held = await RESOLVERS["ghl_recruiting_held"](
            s, t, biz.id, held_week, held_week + dt.timedelta(days=6))

    assert (b_in_booked, h_in_booked) == (1.0, 0.0), "booked counted in the wrong week"
    assert (b_in_held, h_in_held) == (0.0, 1.0), "held counted in the wrong week"


async def test_qtd_signed_is_cumulative_and_counts_people_once():
    """A snapshot row, never summed across weeks. And somebody moved out of Signed and back has
    still been recruited once."""
    async with SessionLocal() as s:
        t, biz, _, seat = await _ws(s, ready=True)
        await s.execute(RecruitingStageEvent.__table__.delete().where(
            RecruitingStageEvent.tenant_id == t))
        cand = RecruitingCandidate(tenant_id=t, business_id=biz.id,
                                   opportunity_id=f"opp_qtd_{uuid.uuid4().hex[:6]}")
        s.add(cand)
        await s.flush()
        from app.services.recruiting_rules import business_tz
        tz = business_tz()
        for day in (dt.date(2026, 7, 8), dt.date(2026, 8, 12), dt.date(2026, 8, 20)):
            s.add(RecruitingStageEvent(
                tenant_id=t, candidate_id=cand.id, to_group="Signed", source="sync",
                actor_seat_id=seat.id,
                occurred_at=dt.datetime.combine(day, dt.time(12, 0), tzinfo=tz)))
        await s.commit()

        july = await RESOLVERS["ghl_recruiting_signed_qtd"](
            s, t, biz.id, dt.date(2026, 7, 6), dt.date(2026, 7, 12))
        august = await RESOLVERS["ghl_recruiting_signed_qtd"](
            s, t, biz.id, dt.date(2026, 8, 17), dt.date(2026, 8, 23))
        q4 = await RESOLVERS["ghl_recruiting_signed_qtd"](
            s, t, biz.id, dt.date(2026, 10, 5), dt.date(2026, 10, 11))

    assert july == 1.0
    assert august == 1.0, "three events for one person is one agent recruited"
    assert q4 == 0.0, "the quarter did not reset"
