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
                   goal_basis="seats", seat_goal=100,
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


def test_parse_call_time_honors_zone_suffix():
    # a trailing zone abbreviation pins the REAL zone, overriding the launch default (Denver)
    utc, ok = sd.parse_call_time("Friday, August 14, 2026 at 11:00 AM EDT", DENVER)
    assert ok and utc.hour == 15 and utc.minute == 0                   # 11:00 EDT (UTC-4) → 15:00 UTC
    utc2, ok2 = sd.parse_call_time("Thursday, August 13, 2026 at 5:00 PM MST", DENVER)
    assert ok2 and utc2.hour == 0 and utc2.day == 14                   # 17:00 MST (UTC-7) → 00:00 UTC next day
    # no suffix → still the launch tz (unchanged behavior); "AM"/"PM" never mistaken for a zone
    utc3, ok3 = sd.parse_call_time("Friday, August 14, 2026 at 8:30 AM", DENVER)
    assert ok3 and utc3.hour == 14 and utc3.minute == 30


def test_title_name_standardizes_lead_names():
    tn = sd.title_name
    assert tn("Dalila OROZCO") == "Dalila Orozco"        # all-caps surname → Title Case
    assert tn("nina watson") == "Nina Watson"            # all-lowercase → Title Case
    assert tn("KATHLEEN HUEBNER") == "Kathleen Huebner"
    assert tn("Karrie McKinnon") == "Karrie McKinnon"    # intentional intercap preserved
    assert tn("JaRelle Bailey") == "JaRelle Bailey"
    assert tn("Salle MJ Bayer-Carney") == "Salle MJ Bayer-Carney"   # initials + hyphen kept
    assert tn("bayer-carney") == "Bayer-Carney"          # capitalize across a hyphen
    assert tn("o'brien") == "O'Brien"                    # capitalize after an apostrophe
    assert tn("Dori C Davenport") == "Dori C Davenport"  # single-letter middle initial
    assert tn(None) is None and tn("") == ""             # empties pass through untouched


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


async def test_backfill_reparses_stored_call_times_that_never_resolved():
    """Rows written before a parser improvement keep call_time_utc NULL (the diff only re-parses
    on a CHANGED raw). The backfill pass heals them — including superseded rows."""
    tid, lid = await _fresh_launch()
    async with SessionLocal() as s:
        s.add(SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="oZ", booking_id="bOld",
                        rep_email="a@x.com", call_time_raw="Thursday, August 13, 2026 at 5:00 PM MST",
                        call_time_utc=None, outcome="No Show", is_current=False))   # superseded
        s.add(SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="oZ", booking_id="bNew",
                        rep_email="a@x.com", call_time_raw="Friday, August 14, 2026 at 11:00 AM EDT",
                        call_time_utc=None, outcome=None, is_current=True))
        await s.commit()
    async with SessionLocal() as s:
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        await sd.apply_sales_diff(s, tid, L, [], DENVER)               # no records — pure backfill
    async with SessionLocal() as s:
        rows = {r.booking_id: r for r in (await s.execute(
            select(SalesCall).where(SalesCall.launch_id == lid))).scalars()}
    assert rows["bOld"].call_time_utc is not None                      # 17:00 MST → 00:00 UTC
    assert rows["bNew"].call_time_utc is not None and rows["bNew"].call_time_utc.hour == 15


async def test_snapshot_prefers_salescall_payment_and_clears_unknown_warning():
    """The Launch snapshot's payment classification (drill PAYMENT column + the 'unknown payment
    type' banner) must prefer the Sales Desk event log — the rep's Payment Type on the opp —
    over the legacy contact-field/tag/stage classifier."""
    from app.services.sync import snapshot_launch_opps
    from app.services.launch import compute_launch
    tid, lid = await _fresh_launch()
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        s.add(SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="oAmy", booking_id="bAmy",
                        rep_email="a@x.com", outcome="Showed", payment_type="PIF", is_current=True,
                        call_time_utc=dt.datetime(2026, 8, 12, tzinfo=U)))
        await s.commit()
        # stage carries no payment keyword and the contact has no plan field/tag — the legacy
        # classifier returns None; only the SalesCall PIF can classify this opp.
        opp = {"id": "oAmy", "contactId": "c1", "status": "open",
               "pipelineId": "P1", "pipelineStageId": "S1"}
        n = await snapshot_launch_opps(
            s, tid, biz.id, [opp], {"S1": "Payment Received - Contract Sent"},
            {"P1": "be Collective Experience #1 Sales"}, {}, set(), today=dt.date(2026, 8, 13))
        assert n == 1
        rec = (await s.execute(select(MetricRecord).where(
            MetricRecord.kind == "bc_launch_opp", MetricRecord.external_id == "oAmy"))).scalar_one()
        assert (rec.meta or {}).get("group") == "committed"
        assert (rec.meta or {}).get("payment_type") == "pif"           # from the event log
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        d = await compute_launch(s, tid, L, today=dt.date(2026, 8, 13))
    assert not any("unknown payment type" in w for w in d["warnings"])  # the banner clears


async def test_apply_sales_diff_isolates_a_bad_record():
    """Per-record savepoint: one malformed record rolls back only itself (counted in warn) —
    the good records in the same batch still land."""
    tid, lid = await _fresh_launch()
    good = _rec("oppGood", "bA", outcome="Showed")
    bad = {"booking_id": "bB", "outcome_raw": "Showed"}          # no 'opportunity_id' → KeyError
    async with SessionLocal() as s:
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        warn = await sd.apply_sales_diff(s, tid, L, [bad, good], DENVER)  # bad one FIRST
    assert warn.get("record_error") == 1
    async with SessionLocal() as s:
        rows = (await s.execute(select(SalesCall).where(SalesCall.launch_id == lid))).scalars().all()
    assert len(rows) == 1 and rows[0].opportunity_id == "oppGood" and rows[0].outcome == "Showed"


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


async def test_launch_arr_cash_goal_from_price_map_four_type():
    """§9.3/9.4/9.5 — with logged Payment Types, the Launch tab's ARR, cash, and goal come from
    the four-type price_map (real counts), not the assumed two-price mix."""
    from app.services.launch import compute_launch
    tid, lid = await _fresh_launch()
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        for opp, pay in (("o1", "PIF"), ("o2", "Financed")):
            s.add(MetricRecord(tenant_id=tid, business_id=biz.id, source="ghl", kind="bc_launch_opp",
                               external_id=opp, name=opp, status="won",
                               meta={"launch_id": str(lid), "group": "enrolled"}))
            s.add(SalesCall(tenant_id=tid, launch_id=lid, opportunity_id=opp, booking_id="bk" + opp,
                            rep_email="a@x.com", outcome="Showed", payment_type=pay, is_current=True,
                            call_time_utc=dt.datetime(2026, 8, 18, tzinfo=U)))
        await s.commit()
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        d = await compute_launch(s, tid, L, today=dt.date(2026, 8, 20))
    assert d["enrolled"]["arr"] == 26000                       # 12000 PIF + 14000 Financed (real counts)
    assert d["enrolled"]["mix"] == {"PIF": 1, "Financed": 1} and d["enrolled"]["seats"] == 2
    assert d["cash"] == {"collected": 17000, "source": "upfront"}   # §9.4 — 12000 + 5000 upfront
    assert d["blended_seat"] == 13000.0                         # (12000 + 14000) / 2
    assert d["derived_goal_arr"] == 1_300_000                   # §9.5 — 100 seats × 13000, not "$1M"


async def test_won_without_payment_is_a_seat_but_unpriced_and_repricing_never_recounts():
    """§12 — a won deal with no Payment Type still counts as a seat, adds nothing to priced_arr,
    and raises a warning; and editing price_map reprices blended/on-the-table WITHOUT touching
    any call count."""
    tid, lid = await _fresh_launch()
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(SalesRep))
        s.add(SalesRep(tenant_id=tid, email="a@x.com", display_name="Rep A", is_active=True))

        def SC(opp, pay):
            return SalesCall(tenant_id=tid, launch_id=lid, opportunity_id=opp, booking_id="b" + opp,
                             rep_email="a@x.com", outcome="Showed", payment_type=pay, is_current=True,
                             contact_name=opp, call_time_utc=dt.datetime(2026, 8, 18, tzinfo=U),
                             outcome_at=dt.datetime(2026, 8, 19, tzinfo=U))
        s.add_all([SC("o1", "PIF"), SC("o2", None)])                 # o2 = won with NO payment type
        for opp, grp in (("o1", "enrolled"), ("o2", "enrolled"), ("o5", "deciding")):
            s.add(MetricRecord(tenant_id=tid, business_id=biz.id, source="ghl", kind="bc_launch_opp",
                               external_id=opp, name=opp, status="open",
                               meta={"launch_id": str(lid), "group": grp}))
        await s.commit()

    NOW = dt.datetime(2026, 8, 20, 12, tzinfo=U)
    async with SessionLocal() as s:
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        d1 = await sd.compute_sales_desk(s, tid, L, now=NOW)

        assert d1["totals"]["won"] == 2                          # both are seats
        assert d1["priced_arr"] == 12000                        # PIF only; the null-pay seat adds nothing
        wpay = next(w for w in d1["warnings"] if w["label"] == "won with no payment type")
        assert wpay["n"] == 1
        assert d1["totals"]["blended"] == 12000 and d1["totals"]["on_the_table"] == 12000  # 1 deciding × 12000

        # bump the PIF price → blended/on-the-table move, but no count changes
        L.price_map = {**PRICE_MAP, "PIF": {**PRICE_MAP["PIF"], "acv": 20000, "upfront": 20000}}
        d2 = await sd.compute_sales_desk(s, tid, L, now=NOW)

    assert (d2["totals"]["blended"], d2["totals"]["on_the_table"], d2["priced_arr"]) == (20000, 20000, 20000)
    assert d2["totals"]["won"] == d1["totals"]["won"]           # repricing never recounts
    assert d2["totals"]["booked"] == d1["totals"]["booked"] == 2
    assert d2["totals"]["held"] == d1["totals"]["held"] == 2


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


def test_classify_stage_committed_means_paid():
    """Connor's rule (2026-08-13): a SENT payment link isn't cash — Deciding. Committed is
    strictly cash-received-unsigned; Enrolled is signed+onboarded only."""
    from app.services.launch import classify_stage, DEFAULT_STAGE_MAP as M
    assert classify_stage("Payment Sent: PIF", M)[0] == "deciding"
    assert classify_stage("Payment Sent: Financed", M)[0] == "deciding"
    assert classify_stage("Payment Received - Contract Sent", M)[0] == "committed"
    assert classify_stage("Won: Onboarded", M)[0] == "enrolled"
    assert classify_stage("Scheduled Appointment - App Submitted", M) == ("booked", True)
    assert classify_stage("Appointment No Show / Cancel", M)[0] == "noshow"
    assert classify_stage("Future Cohort - Nuture", M)[0] == "nurture"      # their spelling
    assert classify_stage("Lost: DQ / Abandon", M)[0] == "lost"


async def test_cash_spans_committed_and_enrolled():
    """§9.4 under Committed-=-paid: cash = upfronts of EVERYONE who paid, committed included."""
    from app.services.launch import compute_launch
    tid, lid = await _fresh_launch()
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        for opp, grp, pay in (("o1", "committed", "PIF"), ("o2", "enrolled", "Financed")):
            s.add(MetricRecord(tenant_id=tid, business_id=biz.id, source="ghl", kind="bc_launch_opp",
                               external_id=opp, name=opp, status="open",
                               meta={"launch_id": str(lid), "group": grp}))
            s.add(SalesCall(tenant_id=tid, launch_id=lid, opportunity_id=opp, booking_id="b" + opp,
                            rep_email="a@x.com", outcome="Showed", payment_type=pay, is_current=True,
                            call_time_utc=dt.datetime(2026, 8, 18, tzinfo=U)))
        await s.commit()
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        d = await compute_launch(s, tid, L, today=dt.date(2026, 8, 20))
    assert d["committed"]["arr"] == 12000 and d["enrolled"]["arr"] == 14000
    assert d["cash"] == {"collected": 17000, "source": "upfront"}   # 12000 PIF + 5000 Financed upfront

    # the cash drill explains the same math over the same base
    from app.services.launch import drill_launch
    async with SessionLocal() as s:
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        dr = await drill_launch(s, tid, L, "cash", today=dt.date(2026, 8, 20))
    assert dr["value"] == "$17,000" and dr["formula"] == "sum(upfront × paid seats)"
    assert any("committed + enrolled" in str(st["value"]) for st in dr["steps"])


async def test_drill_sales_desk_metrics():
    """§8 drills — records for call/opp metrics (rep-scopable), calc for derived figures."""
    tid, lid = await _seed_desk_scenario()
    NOW = dt.datetime(2026, 8, 20, 12, tzinfo=U)
    async with SessionLocal() as s:
        L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
        booked = await sd.drill_sales_desk(s, tid, L, "kpi.booked", now=NOW)
        booked_a = await sd.drill_sales_desk(s, tid, L, "kpi.booked", rep="a@x.com", now=NOW)
        booked_u = await sd.drill_sales_desk(s, tid, L, "kpi.booked", rep="__unassigned__", now=NOW)
        show = await sd.drill_sales_desk(s, tid, L, "kpi.show_rate", now=NOW)
        won = await sd.drill_sales_desk(s, tid, L, "kpi.won", now=NOW)
        mix_pif = await sd.drill_sales_desk(s, tid, L, "mix.PIF", now=NOW)
        no_rep = await sd.drill_sales_desk(s, tid, L, "dh.no_rep", now=NOW)

    assert booked["type"] == "records" and booked["count"] == 4          # all four calls
    assert booked_a["count"] == 2 and booked_u["count"] == 1             # rep + unassigned scoping
    assert show["type"] == "calc" and show["value"] == "50%"             # 1 held / (1+1) resolved
    assert won["count"] == 1 and won["rows"][0]["payment"] == "PIF"      # o1, event-log payment
    assert mix_pif["count"] == 1
    assert no_rep["count"] == 1                                          # o4 has no rep
    with pytest.raises(Exception):
        async with SessionLocal() as s:
            L = (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()
            await sd.drill_sales_desk(s, tid, L, "nope", now=NOW)        # unknown metric → 404


async def test_drill_route_serves_and_404s():
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    await _seed_desk_scenario()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        tok = (await c.post("/api/v1/auth/login",
                            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})).json()["token"]
        H = {"Authorization": f"Bearer {tok}"}
        r = await c.get("/api/v1/businesses/springb/launches/active/sales-desk/drill/kpi.booked", headers=H)
        assert r.status_code == 200 and r.json()["type"] == "records" and r.json()["count"] == 4
        r2 = await c.get("/api/v1/businesses/springb/launches/active/sales-desk/drill/kpi.booked",
                         params={"rep": "a@x.com"}, headers=H)
        assert r2.json()["count"] == 2
        r3 = await c.get("/api/v1/businesses/springb/launches/active/sales-desk/drill/nope", headers=H)
        assert r3.status_code == 404


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


async def test_rep_roster_edit_is_owner_admin_only_and_audited():
    """§11.7 / §12 — roster display-name edits are owner/admin only, audited; the change sticks."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.models import AuditLog, User
    from app.security import hash_pw
    await _fresh_launch()
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(SalesRep))
        await s.execute(delete(User).where(User.email == "sdmember@x.com"))
        s.add(SalesRep(tenant_id=biz.tenant_id, email="rep1@x.com", display_name="rep1@x.com", is_active=True))
        s.add(User(tenant_id=biz.tenant_id, email="sdmember@x.com", name="M",
                   password_hash=hash_pw("password123"), role="member", status="active"))
        await s.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        otok = (await c.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})).json()["token"]
        mtok = (await c.post("/api/v1/auth/login", json={"email": "sdmember@x.com", "password": "password123"})).json()["token"]
        body = {"reps": [{"email": "rep1@x.com", "display_name": "Rep One", "is_active": True}]}
        rm = await c.put("/api/v1/businesses/springb/sales-desk/reps", json=body,
                         headers={"Authorization": f"Bearer {mtok}"})
        assert rm.status_code == 403                                   # member rejected
        ro = await c.put("/api/v1/businesses/springb/sales-desk/reps", json=body,
                         headers={"Authorization": f"Bearer {otok}"})
        assert ro.status_code == 200 and ro.json()["updated"] == 1     # owner accepted
        g = await c.get("/api/v1/businesses/springb/sales-desk/reps", headers={"Authorization": f"Bearer {otok}"})
        assert any(x["email"] == "rep1@x.com" and x["display_name"] == "Rep One" for x in g.json())
    async with SessionLocal() as s:
        assert (await s.execute(select(AuditLog).where(AuditLog.action == "sales_rep.roster_updated"))).scalars().first()


async def test_roster_surfaces_call_reps_outside_the_directory_and_naming_maps_them():
    """§11.7 — the `Sales Rep` field is free-form, so reps booking calls are often NOT in the GHL
    directory. The roster must still list them (unmapped, needs-name-first) so they can be named;
    naming one upserts a SalesRep and it stops being unmapped, while an unmapped rep left unnamed
    makes no ghost row."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    tid, lid = await _fresh_launch()
    async with SessionLocal() as s:
        await s.execute(delete(SalesRep))
        s.add(SalesRep(tenant_id=tid, email="dir@springb.com", display_name="Dir Member", is_active=True))
        for opp, rep in (("o1", "jplove1978@gmail.com"), ("o2", "ikwillsey@gmail.com")):
            s.add(SalesCall(tenant_id=tid, launch_id=lid, opportunity_id=opp, booking_id="b" + opp,
                            rep_email=rep, outcome=None, is_current=True, contact_name=opp,
                            call_time_utc=dt.datetime(2026, 8, 21, tzinfo=U)))
        await s.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        tok = (await c.post("/api/v1/auth/login",
                            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})).json()["token"]
        H = {"Authorization": f"Bearer {tok}"}
        g1 = (await c.get("/api/v1/businesses/springb/sales-desk/reps", headers=H)).json()
        by = {r["email"]: r for r in g1}
        assert by["jplove1978@gmail.com"]["unmapped"] is True and by["jplove1978@gmail.com"]["display_name"] is None
        assert by["ikwillsey@gmail.com"]["unmapped"] is True
        assert by["dir@springb.com"]["unmapped"] is False
        assert [r["email"] for r in g1][:2] == sorted(["jplove1978@gmail.com", "ikwillsey@gmail.com"])  # needs-name first

        body = {"reps": [
            {"email": "jplove1978@gmail.com", "display_name": "J.P. Love", "is_active": True},
            {"email": "ikwillsey@gmail.com", "display_name": "", "is_active": True},      # left unnamed
            {"email": "dir@springb.com", "display_name": "Dir Member", "is_active": True},
        ]}
        r = await c.put("/api/v1/businesses/springb/sales-desk/reps", json=body, headers=H)
        assert r.status_code == 200 and r.json()["updated"] == 1                          # only the named one changed

        g2 = {x["email"]: x for x in (await c.get("/api/v1/businesses/springb/sales-desk/reps", headers=H)).json()}
        assert g2["jplove1978@gmail.com"]["display_name"] == "J.P. Love" and g2["jplove1978@gmail.com"]["unmapped"] is False
        assert g2["ikwillsey@gmail.com"]["unmapped"] is True                              # still surfaced, no ghost

    async with SessionLocal() as s:
        emails = {r.email for r in
                  (await s.execute(select(SalesRep).where(SalesRep.tenant_id == tid))).scalars().all()}
        assert "jplove1978@gmail.com" in emails and "ikwillsey@gmail.com" not in emails   # upsert vs no ghost


async def test_roster_case_insensitive_collapse_blank_preserve_and_dup_safe():
    """§11.7 hardening — a SalesCall rep_email differing only in CASE from a directory SalesRep
    collapses to ONE roster row; a blank name never wipes an already-named rep; and the same email
    repeated in one PUT body upserts once (no IntegrityError / no duplicate row)."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    tid, lid = await _fresh_launch()
    async with SessionLocal() as s:
        await s.execute(delete(SalesRep))
        s.add(SalesRep(tenant_id=tid, email="Rep@X.com", display_name="Rep Case", is_active=True))
        s.add(SalesCall(tenant_id=tid, launch_id=lid, opportunity_id="o1", booking_id="b1",
                        rep_email="rep@x.com", outcome=None, is_current=True, contact_name="o1",
                        call_time_utc=dt.datetime(2026, 8, 21, tzinfo=U)))         # same rep, lowercased
        await s.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        tok = (await c.post("/api/v1/auth/login",
                            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})).json()["token"]
        H = {"Authorization": f"Bearer {tok}"}
        g = (await c.get("/api/v1/businesses/springb/sales-desk/reps", headers=H)).json()
        rep_rows = [r for r in g if (r["email"] or "").lower() == "rep@x.com"]
        assert len(rep_rows) == 1                                    # case-variant collapses to one row
        assert rep_rows[0]["unmapped"] is False and rep_rows[0]["on_calls"] is True

        # a blank name against the already-named rep must NOT wipe it
        r = await c.put("/api/v1/businesses/springb/sales-desk/reps",
                        json={"reps": [{"email": "Rep@X.com", "display_name": "", "is_active": True}]}, headers=H)
        assert r.status_code == 200 and r.json()["updated"] == 0     # blank == no change
        g2 = {x["email"]: x for x in (await c.get("/api/v1/businesses/springb/sales-desk/reps", headers=H)).json()}
        assert g2["Rep@X.com"]["display_name"] == "Rep Case"         # preserved

        # the same email twice in one body upserts once, no 500
        r3 = await c.put("/api/v1/businesses/springb/sales-desk/reps", json={"reps": [
            {"email": "New@Rep.com", "display_name": "First"},
            {"email": "new@rep.com", "display_name": "Second"}]}, headers=H)
        assert r3.status_code == 200

    async with SessionLocal() as s:
        rows = (await s.execute(select(SalesRep).where(SalesRep.tenant_id == tid))).scalars().all()
        assert len([x for x in rows if (x.email or "").lower() == "new@rep.com"]) == 1   # one row, no dup


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
