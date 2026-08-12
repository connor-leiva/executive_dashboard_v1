"""Sales Desk sync — the append-only diff (SPEC-becollective-salesdesk §6) and its
acceptance criteria (§12): a rebooked no-show yields two rows and the no-show survives;
Call Time parses; outcome strings come from config; the Desk never writes to GHL."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, delete

from app.seed import seed
from app.db import SessionLocal
from app.models import Business, Launch, SalesCall, SalesCallChange, SalesRep
from app.services.launch import DEFAULT_STAGE_MAP, DEFAULT_PAYMENT_PLAN_MAP
from app.services import sales_desk as sd

DENVER = ZoneInfo("America/Denver")


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _fresh_launch():
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(SalesCallChange))
        await s.execute(delete(SalesCall))
        await s.execute(delete(Launch).where(Launch.business_id == biz.id))
        L = Launch(tenant_id=biz.tenant_id, business_id=biz.id, name="SD", program="beCollective",
                   window_start=dt.date(2026, 8, 11), window_end=dt.date(2026, 9, 12),
                   goal_arr=1_000_000, ticket_pif=12000, ticket_plan=14000,
                   pipeline_match="be collective experience #1 sales",
                   stage_map=DEFAULT_STAGE_MAP, payment_plan_map=DEFAULT_PAYMENT_PLAN_MAP,
                   default_tz="America/Denver")
        s.add(L)
        await s.flush()
        tid, lid = biz.tenant_id, L.id
        await s.commit()
    return tid, lid


def _rec(opp, booking, outcome=None, rep="rep@x.com", when="Friday, August 14, 2026 at 8:30 AM", name="Rachel"):
    return {"opportunity_id": opp, "contact_id": "c1", "contact_name": name,
            "booking_id": booking, "rep_email": rep, "call_time_raw": when, "outcome_raw": outcome}


# ── pure helpers ──────────────────────────────────────────────────────────────────────────
def test_parse_call_time_denver_to_utc():
    utc, ok = sd.parse_call_time("Friday, August 14, 2026 at 8:30 AM", DENVER)
    assert ok and utc.tzinfo == dt.timezone.utc
    assert (utc.year, utc.month, utc.day, utc.hour, utc.minute) == (2026, 8, 14, 14, 30)  # MDT = UTC-6
    assert sd.parse_call_time("not a date", DENVER) == (None, False)   # genuine failure → warned
    assert sd.parse_call_time("", DENVER) == (None, True)              # nothing to parse ≠ failure


def test_norm_outcome_from_config():
    assert sd.norm_outcome("Showed") == "Showed"
    assert sd.norm_outcome("no show") == "No Show"
    assert sd.norm_outcome("NoShow") == "No Show"
    assert sd.norm_outcome("Rescheduled") == "Rescheduled"
    assert sd.norm_outcome("gibberish") is None
    assert sd.norm_outcome(None) is None


def test_field_ids_resolved_by_fieldkey():
    defs = [{"id": "F1", "name": "Sales Rep", "fieldKey": "opportunity.sales_rep"},
            {"id": "F2", "name": "Booking ID", "fieldKey": "opportunity.booking_id"},
            {"id": "F3", "name": "Call Outcome", "fieldKey": "opportunity.call_outcome"}]
    ids = sd.salescall_field_ids(defs)
    assert ids["sales_rep"] == "F1" and ids["booking_id"] == "F2" and ids["call_outcome"] == "F3"


def test_no_writes_to_ghl():
    """§12: the Desk sync performs no GHL writes."""
    import pathlib
    src = pathlib.Path(sd.__file__).read_text(encoding="utf-8")
    for verb in (".post(", ".put(", ".patch(", "create_opportunity", "update_contact", "add_tag"):
        assert verb not in src, f"Sales Desk must not write to GHL (found {verb!r})"


# ── the diff (§6 / §12) ─────────────────────────────────────────────────────────────────────
async def test_rebooked_no_show_yields_two_rows_and_survives():
    tid, lid = await _fresh_launch()
    # round 1: booked, then no-show on booking A
    async with SessionLocal() as s:
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        await sd.apply_sales_diff(s, tid, L, [_rec("oppX", "A", outcome="No Show")], DENVER)
    # round 2: rebook → booking B, no outcome yet (should NOT erase A's no-show)
    async with SessionLocal() as s:
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        await sd.apply_sales_diff(s, tid, L, [_rec("oppX", "B", when="Monday, August 17, 2026 at 10:00 AM")], DENVER)
    # round 3: booking B shows
    async with SessionLocal() as s:
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        await sd.apply_sales_diff(s, tid, L, [_rec("oppX", "B", outcome="Showed",
                                                   when="Monday, August 17, 2026 at 10:00 AM")], DENVER)
    async with SessionLocal() as s:
        rows = (await s.execute(select(SalesCall).where(SalesCall.opportunity_id == "oppX"))).scalars().all()
        assert len(rows) == 2, [(r.booking_id, r.outcome, r.is_current) for r in rows]
        a = next(r for r in rows if r.booking_id == "A")
        b = next(r for r in rows if r.booking_id == "B")
        assert a.outcome == "No Show" and a.is_current is False       # the no-show is preserved
        assert b.outcome == "Showed" and b.is_current is True
        held = sum(1 for r in rows if r.outcome == "Showed")
        no_show = sum(1 for r in rows if r.outcome == "No Show")
        assert (held, no_show) == (1, 1)                              # leaderboard: 1 held, 1 no-show


async def test_outcome_change_is_logged_once_null_to_value():
    tid, lid = await _fresh_launch()
    async with SessionLocal() as s:
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        await sd.apply_sales_diff(s, tid, L, [_rec("oppY", "K")], DENVER)          # booked, no outcome
    async with SessionLocal() as s:
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        await sd.apply_sales_diff(s, tid, L, [_rec("oppY", "K", outcome="Showed")], DENVER)  # now shows
    async with SessionLocal() as s:
        changes = (await s.execute(select(SalesCallChange))).scalars().all()
        outcome_changes = [c for c in changes if c.field == "outcome"]
        assert len(outcome_changes) == 1
        assert outcome_changes[0].old_value is None and outcome_changes[0].new_value == "Showed"
        sc = (await s.execute(select(SalesCall).where(SalesCall.opportunity_id == "oppY"))).scalar_one()
        assert sc.outcome == "Showed" and sc.outcome_at is not None


async def test_missing_booking_id_still_counts_with_warning():
    tid, lid = await _fresh_launch()
    async with SessionLocal() as s:
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        warn = await sd.apply_sales_diff(s, tid, L, [_rec("oppZ", None, outcome="Showed")], DENVER)
        assert warn.get("booking_id_missing") == 1
        rows = (await s.execute(select(SalesCall).where(SalesCall.opportunity_id == "oppZ"))).scalars().all()
        assert len(rows) == 1 and rows[0].booking_id is None and rows[0].outcome == "Showed"


async def test_seed_reps_from_users_upserts_display_names():
    tid, _ = await _fresh_launch()
    async with SessionLocal() as s:
        await s.execute(delete(SalesRep))
        await s.commit()
    users = [{"id": "u1", "email": "aimee@purposeledperformance.com", "name": "Aimee Stephens"},
             {"id": "u2", "email": "blake@springb.com", "name": "Blake Jacobsen"},
             {"id": "u3", "email": "", "name": "no email"}]
    async with SessionLocal() as s:
        n = await sd.seed_reps_from_users(s, tid, users)
        await s.commit()
        assert n == 2                                                # the blank-email user is skipped
        reps = {r.email: r.display_name for r in (await s.execute(select(SalesRep))).scalars()}
        assert reps["aimee@purposeledperformance.com"] == "Aimee Stephens"
    async with SessionLocal() as s:                                 # idempotent re-run adds nothing
        assert await sd.seed_reps_from_users(s, tid, users) == 0
