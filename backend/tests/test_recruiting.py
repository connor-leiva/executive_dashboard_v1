"""ULRG Recruiting — Phase 1, the read path (RECRUITING-SPEC §9).

The five things Phase 1's "Done when" asks to be held: that a stage diff writes exactly one
event, that an upsert never destroys history, that the payload keeps its shape, that a seat only
sees what its role may see, and that real dates go into Date columns.

The last one reads like paranoia and is not. `asyncpg` refuses a string where a timestamp belongs
and SQLite silently accepts one, so a suite running on SQLite cannot notice -- which is how 26 of
26 FUB production syncs failed, each after eight minutes of paging, with every test green.
"""
import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    Business, Integration, RecruitingActivity, RecruitingAppointment, RecruitingCandidate,
    RecruitingSeat, RecruitingStageEvent, User,
)
from app.seed import seed
from app.services import recruiting, recruiting_settings
from app.services.recruiting_sync import _dtm, _trunc, stage_group_for

STAGES = {"sourced": "st_sourced", "appt": "st_appt", "met": "st_met",
          "offer": "st_offer", "signed": "st_signed", "nurture": "st_nurture"}
GROUPS = [
    ["Sourced", "sdr", [STAGES["sourced"]]],
    ["Appointment set", "sdr", [STAGES["appt"]]],
    ["Met", "team_leader", [STAGES["met"]]],
    ["Offer out", "team_leader", [STAGES["offer"]]],
    ["Signed", "team_leader", [STAGES["signed"]]],
    ["Nurture", "team_leader", [STAGES["nurture"]]],
]


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _tenant(s):
    return (await s.execute(select(Business).where(Business.key == "ulrg"))).scalar_one().tenant_id


async def _fixture(s):
    """A connected, configured recruiting location with two seats and one candidate."""
    t = await _tenant(s)
    biz = (await s.execute(select(Business).where(Business.key == "ulrg"))).scalar_one()
    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == t, Integration.provider == "ghl_recruiting"))).scalars().first()
    if integ is None:
        integ = Integration(tenant_id=t, provider="ghl_recruiting", business_id=biz.id,
                            status="connected")
        s.add(integ)
    integ.config = {"location_id": "loc_rec", "pipeline_id": "pipe_1",
                    "recruiting_stage_groups": GROUPS,
                    "synced_at": dt.datetime.now(dt.timezone.utc).isoformat()}

    seats = {}
    for role, name in (("team_leader", "Jenna Ruiz"), ("team_leader", "Marcus Bell"),
                       ("sdr", "Cole Whittaker")):
        seat = (await s.execute(select(RecruitingSeat).where(
            RecruitingSeat.tenant_id == t,
            RecruitingSeat.display_name == name))).scalars().first()
        if seat is None:
            seat = RecruitingSeat(tenant_id=t, business_id=biz.id, role=role, display_name=name)
            s.add(seat)
        seats[name] = seat
    await s.flush()
    await s.commit()
    return t, biz, integ, seats


async def _candidate(s, t, biz, *, opp="opp_1", stage=STAGES["offer"], group="Offer out",
                     owner=None, name="Sunny Kaur"):
    row = (await s.execute(select(RecruitingCandidate).where(
        RecruitingCandidate.tenant_id == t,
        RecruitingCandidate.opportunity_id == opp))).scalars().first()
    if row is None:
        row = RecruitingCandidate(tenant_id=t, business_id=biz.id, opportunity_id=opp)
        s.add(row)
    row.stage_id, row.stage_group, row.name, row.status = stage, group, name, "open"
    row.owner_seat_id = owner.id if owner else None
    row.entered_stage_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
    row.created_at_src = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    await s.flush()
    return row


# ── the sync's two load-bearing behaviours ──────────────────────────────────────────────────

async def test_a_stage_change_writes_exactly_one_event():
    """One observed change, one row. A sync that ran twice over the same change would double
    "signed this month" -- and the second run is the normal case, because the tick runs on a
    schedule whether or not anything moved."""
    async with SessionLocal() as s:
        t, biz, _, seats = await _fixture(s)
        cand = await _candidate(s, t, biz, opp="opp_stage", stage=STAGES["met"], group="Met")
        await s.commit()
        before = len((await s.execute(select(RecruitingStageEvent).where(
            RecruitingStageEvent.candidate_id == cand.id))).scalars().all())

        # The diff the sync performs, run twice against the same observed state.
        for _ in range(2):
            if cand.stage_id != STAGES["offer"]:
                s.add(RecruitingStageEvent(
                    tenant_id=t, candidate_id=cand.id, from_stage_id=cand.stage_id,
                    to_stage_id=STAGES["offer"], to_group="Offer out",
                    occurred_at=dt.datetime.now(dt.timezone.utc), source="sync"))
                cand.stage_id, cand.stage_group = STAGES["offer"], "Offer out"
        await s.commit()

        after = (await s.execute(select(RecruitingStageEvent).where(
            RecruitingStageEvent.candidate_id == cand.id))).scalars().all()
    assert len(after) - before == 1, "a repeated sync must not re-record the same move"
    assert after[-1].from_stage_id == STAGES["met"] and after[-1].to_group == "Offer out"


async def test_an_upsert_never_destroys_history():
    """The whole reason these tables exist. `_ghl_snapshot` replaces a kind's rows every run;
    if candidates worked that way, every stage event would lose its candidate and "who signed in
    September" would empty itself at the next sync."""
    async with SessionLocal() as s:
        t, biz, _, _ = await _fixture(s)
        cand = await _candidate(s, t, biz, opp="opp_history", stage=STAGES["met"], group="Met")
        s.add(RecruitingStageEvent(
            tenant_id=t, candidate_id=cand.id, to_stage_id=STAGES["met"], to_group="Met",
            occurred_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3), source="sync"))
        s.add(RecruitingActivity(
            tenant_id=t, candidate_id=cand.id, kind="note", source="ghl_poll",
            occurred_at=dt.datetime.now(dt.timezone.utc), summary="Asked about the cap"))
        await s.commit()
        cand_id = cand.id

        # Re-sync the same opportunity: the row is FOUND and updated, never replaced.
        again = await _candidate(s, t, biz, opp="opp_history", stage=STAGES["offer"],
                                 group="Offer out")
        await s.commit()
        events = (await s.execute(select(RecruitingStageEvent).where(
            RecruitingStageEvent.candidate_id == cand_id))).scalars().all()
        acts = (await s.execute(select(RecruitingActivity).where(
            RecruitingActivity.candidate_id == cand_id))).scalars().all()
    assert again.id == cand_id, "the upsert made a second row instead of updating the first"
    assert len(events) == 1 and len(acts) == 1, "history was destroyed by a re-sync"


async def test_date_columns_get_real_datetimes_not_strings():
    """FUB-SPEC A1, as a test. SQLite accepts a string here; asyncpg does not."""
    async with SessionLocal() as s:
        t, biz, _, _ = await _fixture(s)
        cand = await _candidate(s, t, biz, opp="opp_dates")
        s.add(RecruitingAppointment(
            tenant_id=t, event_id="ev_dates", start_at=_dtm("2026-09-24T09:00:00-06:00"),
            created_at_src=_dtm(1758700000000), status="confirmed"))
        await s.commit()
        appt = (await s.execute(select(RecruitingAppointment).where(
            RecruitingAppointment.event_id == "ev_dates"))).scalars().first()

    for label, value in (("entered_stage_at", cand.entered_stage_at),
                         ("created_at_src", cand.created_at_src),
                         ("appt.start_at", appt.start_at),
                         ("appt.created_at_src", appt.created_at_src)):
        assert isinstance(value, dt.datetime), f"{label} is {type(value).__name__}, not datetime"
    # And the parser's own contract: aware, or None. Never a naive datetime, which compares
    # against an aware one by raising.
    assert _dtm("2026-09-24T09:00:00-06:00").tzinfo is not None
    assert _dtm("not a date") is None and _dtm(None) is None
    assert _dtm("2026-09-24T09:00:00").tzinfo is not None, "a naive string must be made aware"


# ── stage groups are matched on ID ──────────────────────────────────────────────────────────

async def test_a_stage_is_matched_by_id_never_by_name():
    """D2. A brokerage renaming "Offer out" to "ICA sent" must not empty half the tab."""
    assert stage_group_for(STAGES["offer"], GROUPS) == ("Offer out", "team_leader")
    assert stage_group_for("Offer out", GROUPS) == (None, None), "matched on the NAME"
    assert stage_group_for(None, GROUPS) == (None, None)
    assert stage_group_for(STAGES["offer"], None) == (None, None)


def test_the_suggester_is_the_only_place_names_are_matched():
    """And it never reaches stored config without somebody pressing Save."""
    suggested = recruiting_settings.suggest_groups(
        [{"id": "a", "name": "Offer Out"}, {"id": "b", "name": "Signed"},
         {"id": "c", "name": "Nurture — long term"}])
    got = {label: ids for label, _role, ids in suggested if ids}
    assert got == {"Offer out": ["a"], "Signed": ["b"], "Nurture": ["c"]}
    # A stage is claimed once even when two groups' hints could match it.
    twice = recruiting_settings.suggest_groups([{"id": "x", "name": "Met / Offer"}])
    claimed = [label for label, _r, ids in twice if "x" in ids]
    assert len(claimed) == 1, f"one stage landed in {claimed}"


# ── the payload ─────────────────────────────────────────────────────────────────────────────

async def _owner(s, t) -> User:
    return (await s.execute(select(User).where(
        User.tenant_id == t, User.role == "owner"))).scalars().first()


async def test_the_payload_keeps_its_shape_and_names_what_is_missing():
    """The §3 contract, plus the rule from follow_ups.py: an empty block says WHY it is empty.
    "No data" and "not built yet" are different facts, and a UI that cannot tell them apart
    teaches people to distrust both."""
    async with SessionLocal() as s:
        t, biz, _, seats = await _fixture(s)
        await _candidate(s, t, biz, opp="opp_shape", stage=STAGES["offer"], group="Offer out",
                         owner=seats["Jenna Ruiz"])
        await s.commit()
        out = await recruiting.build_recruiting(s, t, await _owner(s, t))

    for key in ("as_of", "connection", "viewer", "period", "goal", "path", "queue",
                "leaderboard", "pipeline", "unavailable"):
        assert key in out, f"the payload lost {key}"
    assert out["connection"]["state"] == "ready"
    # Phase 1 writes nothing, and says so rather than omitting the key -- so the UI has the shape
    # it will keep and the button reads "Sending is off" from the first day.
    assert out["connection"]["writeback"]["open"] is False
    assert out["connection"]["writeback"]["reason"]

    # Blocks that are null MUST carry a reason.
    for block in ("queue", "commitments", "goal", "calendars"):
        assert block in out["unavailable"], f"{block} is empty with no reason given"
    assert out["goal"]["goal"] is None, "a goal cannot be invented before Phase 5"
    assert out["goal"]["signed"] == len(out["goal"]["signings"])
    assert out["path"]["rows"], "an Offer out candidate should appear on the path"
    assert out["path"]["rows"][0]["stage"] == "Offer out"


async def test_an_unconnected_workspace_says_so_rather_than_showing_zero():
    """Zero recruits and no connection look identical on a dashboard, and only one of them is
    somebody's fault."""
    async with SessionLocal() as s:
        t = await _tenant(s)
        integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == t,
            Integration.provider == "ghl_recruiting"))).scalars().first()
        keep = integ.config if integ else None
        if integ is not None:
            integ.config = {"location_id": "loc_rec"}       # connected, never configured
            await s.commit()
        out = await recruiting.build_recruiting(s, t, await _owner(s, t))
        if integ is not None:
            integ.config = keep
            await s.commit()
    assert out["connection"]["state"] == "not_configured"
    assert out["connection"]["reason"]
    assert out["pipeline"] is None and out["goal"] is None


async def test_the_period_is_business_days_not_calendar_days():
    """Pace on calendar days tells a team on the 1st of a month starting Saturday that they are
    already behind."""
    per = recruiting.resolve_period("mtd", dt.date(2026, 9, 23))
    assert per["key"] == "2026-09" and per["label"] == "September"
    assert per["business_days"] == 22
    assert per["days_left"] == 7
    assert 0.76 < per["elapsed"] < 0.79
    # A month that has ended is complete, not partly elapsed.
    past = recruiting.resolve_period("2026-08", dt.date(2026, 9, 23))
    assert past["elapsed"] == 1.0 and past["days_left"] == 0


# ── per-role scoping ────────────────────────────────────────────────────────────────────────

async def test_a_team_leader_cannot_open_another_leaders_candidate():
    """The tab shows every Team Leader the team's shape and its numbers. A named person's contact
    record, notes and timeline is a different question, and it belongs to whoever owns them."""
    async with SessionLocal() as s:
        t, biz, _, seats = await _fixture(s)
        jenna, marcus = seats["Jenna Ruiz"], seats["Marcus Bell"]
        member = User(tenant_id=t, email=f"tl-{uuid.uuid4().hex[:8]}@example.test",
                      name="Jenna Ruiz", role="member", password_hash="x")
        s.add(member)
        await s.flush()
        jenna.user_id = member.id
        hers = await _candidate(s, t, biz, opp="opp_hers", owner=jenna)
        his = await _candidate(s, t, biz, opp="opp_his", owner=marcus)
        await s.commit()

        assert await recruiting.may_see_candidate(s, t, member, hers) is True
        assert await recruiting.may_see_candidate(s, t, member, his) is False
        # ...and an owner sees both.
        owner = await _owner(s, t)
        assert await recruiting.may_see_candidate(s, t, owner, his) is True


async def test_the_sdr_sees_sdr_owned_stages_and_not_the_rest():
    """The SDR owns candidates up to Appointment set (§0). Which stages those ARE is
    configuration, so the check reads the stage-group map rather than a hardcoded list."""
    async with SessionLocal() as s:
        t, biz, _, seats = await _fixture(s)
        cole = seats["Cole Whittaker"]
        member = User(tenant_id=t, email=f"sdr-{uuid.uuid4().hex[:8]}@example.test",
                      name="Cole Whittaker", role="member", password_hash="x")
        s.add(member)
        await s.flush()
        cole.user_id = member.id
        early = await _candidate(s, t, biz, opp="opp_early", stage=STAGES["appt"],
                                 group="Appointment set", owner=None)
        late = await _candidate(s, t, biz, opp="opp_late", stage=STAGES["offer"],
                                group="Offer out", owner=seats["Marcus Bell"])
        await s.commit()

        assert await recruiting.may_see_candidate(s, t, member, early) is True
        assert await recruiting.may_see_candidate(s, t, member, late) is False


async def test_preview_as_another_seat_is_owner_only():
    """`?as=` is how an owner checks what a Team Leader sees. In anybody else's hands it is the
    scoping above, undone."""
    async with SessionLocal() as s:
        t, _, _, seats = await _fixture(s)
        jenna = seats["Jenna Ruiz"]
        member = User(tenant_id=t, email=f"nos-{uuid.uuid4().hex[:8]}@example.test",
                      name="Nosy Member", role="member", password_hash="x")
        s.add(member)
        await s.commit()

        owner_view = await recruiting.build_recruiting(
            s, t, await _owner(s, t), as_seat=str(jenna.id))
        member_view = await recruiting.build_recruiting(s, t, member, as_seat=str(jenna.id))

    assert owner_view["viewer"]["seat_id"] == str(jenna.id)
    assert owner_view["viewer"]["previewing"] is True
    assert member_view["viewer"]["seat_id"] != str(jenna.id), "?as= worked for a non-admin"
    assert member_view["viewer"]["previewing"] is False
    assert member_view["sources"] is None, "source attribution is owners-only"


# ── bounded settings ────────────────────────────────────────────────────────────────────────

def test_settings_refuse_from_the_ui_and_clamp_from_the_database():
    """A value typed into a form is refused, because silently clamping it teaches somebody the
    field does nothing. The same value already stored is clamped, because it has to render."""
    raw = {"recruiting_stage_groups": [["Met", "team_leader", ["s1"]],
                                       ["Offer out", "team_leader", ["s1"]]]}
    with pytest.raises(ValueError):
        recruiting_settings.clean_settings(raw, strict=True)
    clamped = recruiting_settings.clean_settings(raw)
    stages = [sid for _l, _r, ids in clamped["recruiting_stage_groups"] for sid in ids]
    assert stages == ["s1"], "a stage ended up in two groups"

    # Quiet hours are not a preference (§5.5): clamped inward in BOTH modes.
    assert recruiting_settings.clean_settings(
        {"quiet_hours": {"start": 5, "end": 23}}, strict=True)["quiet_hours"] == {"start": 8, "end": 21}


def test_saving_settings_never_drops_the_location():
    """A PUT that replaced the whole config would take `location_id` with it, and the connection
    would go on looking connected while syncing nothing."""
    merged = recruiting_settings.merge_settings(
        {"location_id": "loc_rec", "ghl_users": [{"id": "u1"}]},
        {"pipeline_id": "pipe_9"})
    assert merged["location_id"] == "loc_rec"
    assert merged["ghl_users"] == [{"id": "u1"}]
    assert merged["pipeline_id"] == "pipe_9"


def test_a_sender_number_must_be_e164():
    """Refused here, or discovered as a failed send three phases from now."""
    with pytest.raises(ValueError):
        recruiting_settings.clean_seat(
            {"role": "sdr", "display_name": "Cole", "from_number": "801-555-0142"})
    ok = recruiting_settings.clean_seat(
        {"role": "sdr", "display_name": "Cole", "from_number": "+18015550142"})
    assert ok["from_number"] == "+18015550142"
    # And a seat is never born able to send: that is gate 3 of 4 (§5.2).
    assert ok["writeback_enabled"] is False


def test_every_string_is_truncated_against_its_real_column():
    """An over-long value passes SQLite and raises on Postgres. The widths live in one dict so
    they cannot drift from models.py without somebody noticing."""
    assert _trunc("x" * 500, 200) == "x" * 200
    assert _trunc("  spaced  ", 200) == "spaced"
    assert _trunc("", 10) is None and _trunc(None, 10) is None
