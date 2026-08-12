"""Sales Desk sync — the append-only diff (SPEC-becollective-salesdesk §6) and its
acceptance criteria (§12): a rebooked no-show yields two rows and the no-show survives;
Call Time parses; outcome strings come from config; the Desk never writes to GHL."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, delete

from app.seed import seed, OWNER_EMAIL, OWNER_PASSWORD
from app.db import SessionLocal
from app.models import Business, Launch, MetricRecord, SalesCall, SalesCallChange, SalesRep
from app.services.launch import DEFAULT_STAGE_MAP, DEFAULT_PAYMENT_PLAN_MAP
from app.services import sales_desk as sd

DENVER = ZoneInfo("America/Denver")
U = dt.timezone.utc


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


PRICE_MAP = {
    "PIF":      {"acv": 12000, "upfront": 12000, "monthly": 0,    "months": 0,  "provisional": False},
    "Financed": {"acv": 14000, "upfront": 5000,  "monthly": 750,  "months": 12, "provisional": False},
    "Monthly":  {"acv": 14400, "upfront": 1200,  "monthly": 1200, "months": 12, "provisional": True},
    "Custom":   {"acv": None,  "upfront": None,  "monthly": None, "months": 0,  "provisional": True},
}


async def _fresh_launch():
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(SalesCallChange))
        await s.execute(delete(SalesCall))
        await s.execute(delete(MetricRecord).where(MetricRecord.kind == "bc_launch_opp"))
        await s.execute(delete(Launch).where(Launch.business_id == biz.id))
        L = Launch(tenant_id=biz.tenant_id, business_id=biz.id, name="SD", program="beCollective",
                   window_start=dt.date(2026, 8, 11), window_end=dt.date(2026, 9, 12),
                   goal_arr=1_000_000, ticket_pif=12000, ticket_plan=14000, price_map=PRICE_MAP,
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


async def _seed_desk_scenario():
    """A@x: 1 show + 1 no-show + 1 won (PIF); Z@x: 1 upcoming (unmapped); Unassigned: 1 unscheduled.
    Plus a deciding opp with no logged call."""
    tid, lid = await _fresh_launch()
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(SalesRep))
        s.add(SalesRep(tenant_id=tid, email="a@x.com", display_name="Rep A", is_active=True))

        def SC(opp, bk, rep, outcome, ct, pay=None):
            return SalesCall(tenant_id=tid, launch_id=lid, opportunity_id=opp, booking_id=bk,
                             rep_email=rep, outcome=outcome, call_time_utc=ct, payment_type=pay,
                             contact_name=opp, is_current=True,
                             outcome_at=(dt.datetime(2026, 8, 19, tzinfo=U) if outcome else None))
        s.add_all([
            SC("o1", "b1", "a@x.com", "Showed", dt.datetime(2026, 8, 18, 10, tzinfo=U), "PIF"),
            SC("o2", "b2", "a@x.com", "No Show", dt.datetime(2026, 8, 17, 10, tzinfo=U)),
            SC("o3", "b3", "z@x.com", None, dt.datetime(2026, 8, 21, 10, tzinfo=U)),   # unmapped rep, upcoming
            SC("o4", None, None, None, None),                                          # Unassigned, unscheduled
        ])
        for opp, grp in (("o1", "enrolled"), ("o5", "deciding")):
            s.add(MetricRecord(tenant_id=tid, business_id=biz.id, source="ghl", kind="bc_launch_opp",
                               external_id=opp, name=opp, status="open",
                               meta={"launch_id": str(lid), "group": grp}))
        await s.commit()
    return tid, lid


async def test_compute_math_and_payload_shape():
    tid, lid = await _seed_desk_scenario()
    NOW = dt.datetime(2026, 8, 20, 12, tzinfo=U)
    async with SessionLocal() as s:
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        d = await sd.compute_sales_desk(s, tid, L, now=NOW)

    t = d["totals"]
    assert (t["booked"], t["held"], t["no_show"], t["deciding"], t["won"]) == (4, 1, 1, 1, 1)
    assert t["show_rate"] == 50.0 and t["close_rate"] == 100.0          # 1/(1+1) held, 1/1 won
    assert t["blended"] == 12000 and t["on_the_table"] == 12000         # deciding 1 × PIF-blended 12000

    reps = {r["rep_email"]: r for r in d["reps"]}
    assert reps["a@x.com"]["won"] == 1 and reps["a@x.com"]["show_rate"] == 50.0
    assert reps["a@x.com"]["display_name"] == "Rep A" and reps["a@x.com"]["unmapped"] is False
    assert reps["z@x.com"]["unmapped"] is True and reps["z@x.com"]["upcoming"] == 1
    assert reps[None]["unassigned"] is True and reps[None]["inplay"] == 1

    mix = {m["type"]: m for m in d["payment_mix"]}
    assert mix["PIF"]["count"] == 1 and mix["Custom"]["acv"] is None and mix["Monthly"]["provisional"] is True
    assert d["priced_arr"] == 12000 and d["upfront_total"] == 12000

    assert any(c["unscheduled"] for c in d["calls"])                    # o4 lands under unscheduled
    assert any(c["call_time_utc"] and "2026-08-21" in c["call_time_utc"] for c in d["calls"])
    assert len(d["no_shows"]) == 1 and d["no_shows"][0]["rebooked"] is False

    labels = [w["label"] for w in d["warnings"]]
    assert "bookings with no rep" in labels and "rep not in the roster" in labels
    assert set(d) >= {"totals", "reps", "calls", "no_shows", "payment_mix", "warnings", "history_since"}


async def test_show_rate_is_null_not_zero_on_empty_denominator():
    tid, lid = await _fresh_launch()
    async with SessionLocal() as s:
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        s.add(SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="oX", booking_id="bX",
                        rep_email="a@x.com", outcome=None, is_current=True,
                        call_time_utc=dt.datetime(2027, 1, 1, tzinfo=U)))     # only an upcoming call
        await s.commit()
        d = await sd.compute_sales_desk(s, tid, L, now=dt.datetime(2026, 8, 20, tzinfo=U))
    assert d["totals"]["show_rate"] is None and d["totals"]["close_rate"] is None   # dash, never 0%


async def test_sales_desk_route_returns_full_payload():
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    await _seed_desk_scenario()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        tok = (await c.post("/api/v1/auth/login",
                            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})).json()["token"]
        r = await c.get("/api/v1/businesses/springb/launches/active/sales-desk",
                        headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j) >= {"totals", "reps", "calls", "no_shows", "payment_mix", "warnings", "history_since"}
    assert j["totals"]["booked"] >= 4


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
