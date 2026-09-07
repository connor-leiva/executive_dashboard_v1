"""beCollective Launch section — compute + API + roles (SPEC-becollective-launch §8/§11).

The compute test pins the spec's sample payload: at the seeded August pricing/mix the
blended seat is $13,000 → seat_target 77; enrolled 13 PIF + 11 plan → $310K ARR; at
as_of 2026-08-23 (12 of 32 days) expected $375K, gap -$65K within the $100K band → onpace.
"""
import datetime as dt

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.seed import seed, OWNER_EMAIL, OWNER_PASSWORD

AUG = dict(
    name="August 2026 Cohort", program="beCollective",
    event_start=dt.date(2026, 8, 11), event_end=dt.date(2026, 8, 13),
    window_start=dt.date(2026, 8, 11), window_end=dt.date(2026, 9, 12),
    goal_arr=1_000_000, ticket_pif=12_000, ticket_plan=14_000, plan_installments=12,
    mix_pif="0.50", pipeline_match="Be Collective August 2026 Sales Funnel",
    cohort_value="Aug 2026",
)
ASOF = dt.date(2026, 8, 23)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _make_launch():
    """A fresh August launch + the sample opportunity spread + a little weekly history."""
    from app.db import SessionLocal
    from app.models import Business, Launch, LaunchWeekly, MetricRecord
    from app.services.launch import DEFAULT_STAGE_MAP, DEFAULT_PAYMENT_PLAN_MAP
    from sqlalchemy import select, delete

    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(LaunchWeekly))
        await s.execute(delete(Launch).where(Launch.business_id == biz.id))
        await s.execute(delete(MetricRecord).where(
            MetricRecord.business_id == biz.id, MetricRecord.kind == "bc_launch_opp"))

        launch = Launch(tenant_id=biz.tenant_id, business_id=biz.id,
                        stage_map=DEFAULT_STAGE_MAP, payment_plan_map=DEFAULT_PAYMENT_PLAN_MAP, **AUG)
        s.add(launch)
        await s.flush()
        lid = str(launch.id)

        n = [0]

        def opp(group, payment_type=None, app_in=False):
            n[0] += 1
            s.add(MetricRecord(
                tenant_id=biz.tenant_id, business_id=biz.id, source="ghl", kind="bc_launch_opp",
                external_id=f"opp{n[0]}", status="open",
                meta={"launch_id": lid, "group": group, "payment_type": payment_type, "app_in": app_in}))

        for _ in range(19):
            opp("leads")
        for i in range(8):
            opp("booked", app_in=(i < 5))       # 5 of 8 apps in
        for _ in range(11):
            opp("deciding")
        for _ in range(2):
            opp("committed", "pif")
        for _ in range(3):
            opp("committed", "plan")
        for _ in range(13):
            opp("enrolled", "pif")
        for _ in range(11):
            opp("enrolled", "plan")
        for _ in range(9):
            opp("noshow")
        for _ in range(32):
            opp("nurture")

        for i, (o, c, cl, cum) in enumerate([(40, 6, 2, 6), (55, 9, 5, 11), (70, 12, 7, 18),
                                             (88, 14, 6, 24)]):
            s.add(LaunchWeekly(tenant_id=biz.tenant_id, launch_id=launch.id,
                               week_start=dt.date(2026, 8, 3) + dt.timedelta(days=7 * i),
                               optins=o, calls=c, closes=cl, enrolled_cum=cum, calls_source="proxy"))
        await s.commit()
        return biz.tenant_id, launch.id


async def _compute():
    from app.db import SessionLocal
    from app.models import Launch
    from app.services.launch import compute_launch
    from sqlalchemy import select
    tenant_id, launch_id = await _make_launch()
    async with SessionLocal() as s:
        launch = (await s.execute(select(Launch).where(Launch.id == launch_id))).scalar_one()
        return await compute_launch(s, tenant_id, launch, today=ASOF)


async def test_sample_payload_matches_spec():
    d = await _compute()
    assert d["blended_seat"] == 13000.0
    assert d["seat_target"] == 77
    assert d["status"] == "open"
    assert d["window_days"] == 32 and d["days_elapsed"] == 12 and d["days_remaining"] == 20
    assert d["enrolled"] == {"pif": 13, "plan": 11, "seats": 24, "arr": 310000.0}
    assert d["committed"] == {"pif": 2, "plan": 3, "seats": 5, "arr": 66000.0}
    assert d["deciding"] == {"count": 11, "arr": 143000.0}
    assert d["pct_to_goal"] == 0.31
    assert d["seats_remaining"] == 53 and d["arr_remaining"] == 690000.0


async def test_pace_onpace_within_band():
    d = await _compute()
    assert d["pace"]["expected_arr"] == 375000.0
    assert d["pace"]["gap_arr"] == -65000.0        # 310K - 375K
    assert d["pace"]["state"] == "onpace"          # |65K| <= 10% * 1M = 100K


async def test_funnel_and_side_signals():
    d = await _compute()
    stages = {f["key"]: f for f in d["funnel"]}
    assert stages["leads"]["count"] == 19
    assert stages["booked"]["count"] == 8 and stages["booked"]["tag"] == "5 of 8 apps in"
    assert stages["deciding"]["tag"] == "$143K on the table"
    assert stages["committed"]["tag"] == "2 PIF · 3 plan"
    assert d["side"] == {"no_show": 9, "nurture": 32}
    assert d["cash"]["source"] == "estimate"
    assert d["momentum"]["optins"][-1] == 88 and len(d["momentum"]["optins"]) == 4
    assert d["warnings"] == []


def test_curve_expected_interpolates_and_clamps():
    from app.services.launch import curve_expected, DEFAULT_SHIFT_CURVE as C
    assert curve_expected(C, 14) == 0.19          # exact point
    assert curve_expected(C, 0) == 0.94           # event day
    assert curve_expected(C, 15) == 0.19          # earlier than first point → clamp low
    assert curve_expected(C, -3) == 0.94          # past event → clamp high
    assert curve_expected(C, 7) == 0.432          # exact mid point
    assert abs(curve_expected(C, 7.5) - 0.411) < 1e-6   # interp between 7 (.432) & 8 (.39)
    assert curve_expected({}, 5) == 0.0           # no curve


async def _make_shift_launch(registrants=175, shift_actual=None, tag="the shift"):
    """A seat-primary launch (target 100) with a Shift layer + N synced registrants."""
    from app.db import SessionLocal
    from app.models import Business, Launch, MetricRecord
    from app.services.launch import DEFAULT_STAGE_MAP, DEFAULT_PAYMENT_PLAN_MAP, DEFAULT_SHIFT_CURVE
    from sqlalchemy import select, delete
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(Launch).where(Launch.business_id == biz.id))
        await s.execute(delete(MetricRecord).where(
            MetricRecord.business_id == biz.id, MetricRecord.kind == "bc_shift_reg"))
        launch = Launch(
            tenant_id=biz.tenant_id, business_id=biz.id, name="August 2026 Cohort",
            window_start=dt.date(2026, 8, 11), window_end=dt.date(2026, 9, 12),
            goal_arr=1_000_000, ticket_pif=12_000, ticket_plan=14_000, mix_pif="0.5",
            pipeline_match="Be Collective August 2026 Sales Funnel",
            goal_basis="seats", seat_goal=100, pace_model="curve",
            shift_name="The Shift", shift_event_date=dt.date(2026, 8, 11), shift_goal=2000,
            shift_reg_tag=tag, shift_actual=shift_actual, shift_pace_curve=DEFAULT_SHIFT_CURVE,
            shift_pace_tolerance="0.08",
            stage_map=DEFAULT_STAGE_MAP, payment_plan_map=DEFAULT_PAYMENT_PLAN_MAP)
        s.add(launch)
        await s.flush()
        for i in range(registrants):
            s.add(MetricRecord(tenant_id=biz.tenant_id, business_id=biz.id, source="ghl",
                               kind="bc_shift_reg", external_id=f"sr{i}", status="registered",
                               meta={"shift_tag": "the shift", "contact_id": f"c{i}"}))
        await s.commit()
        return biz.tenant_id, launch.id


async def _compute_at(launch_id, tenant_id, today):
    from app.db import SessionLocal
    from app.models import Launch
    from app.services.launch import compute_launch
    from sqlalchemy import select
    async with SessionLocal() as s:
        launch = (await s.execute(select(Launch).where(Launch.id == launch_id))).scalar_one()
        return await compute_launch(s, tenant_id, launch, today=today)


async def test_seat_primary_target_is_the_member_count():
    tenant_id, lid = await _make_shift_launch()
    d = await _compute_at(lid, tenant_id, dt.date(2026, 8, 11))
    assert d["goal_basis"] == "seats"
    assert d["seat_target"] == 100          # NOT ceil(1M/13k)=77 — seat-primary


async def test_shift_layer_behind_the_curve():
    tenant_id, lid = await _make_shift_launch(registrants=175)
    d = await _compute_at(lid, tenant_id, dt.date(2026, 7, 28))   # 14 days to Aug 11
    sh = d["shift"]
    assert sh["registrants"] == 175 and sh["goal"] == 2000 and sh["source"] == "synced"
    assert sh["days_to_event"] == 14
    assert sh["expected_pct"] == 0.19 and sh["expected"] == 380     # curve, not linear
    assert sh["gap"] == -205 and sh["state"] == "behind"            # honest: below the curve
    assert sh["projected_members"] == 9 and sh["members_at_goal"] == 100  # 175 * 100/2000
    assert len(sh["curve"]) == 15


async def test_shift_manual_fallback_when_nothing_synced():
    tenant_id, lid = await _make_shift_launch(registrants=0, shift_actual=210)
    d = await _compute_at(lid, tenant_id, dt.date(2026, 7, 28))
    assert d["shift"]["registrants"] == 210 and d["shift"]["source"] == "manual"


async def test_shift_source_breakdown_and_channel_drill():
    """Registrants aggregate by acquisition channel (from meta.channel the sync sets), and
    each channel drills to its registrant list."""
    from app.db import SessionLocal
    from app.models import Business, Launch, MetricRecord
    from app.services.launch import (compute_launch, drill_launch, DEFAULT_STAGE_MAP,
                                      DEFAULT_PAYMENT_PLAN_MAP, DEFAULT_SHIFT_CURVE)
    from sqlalchemy import select, delete
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(Launch).where(Launch.business_id == biz.id))
        await s.execute(delete(MetricRecord).where(
            MetricRecord.business_id == biz.id, MetricRecord.kind == "bc_shift_reg"))
        launch = Launch(
            tenant_id=biz.tenant_id, business_id=biz.id, name="Aug", window_start=dt.date(2026, 8, 11),
            window_end=dt.date(2026, 9, 12), goal_arr=1_000_000, ticket_pif=12_000, ticket_plan=14_000,
            mix_pif="0.5", pipeline_match="x", goal_basis="seats", seat_goal=100,
            shift_name="The Shift", shift_event_date=dt.date(2026, 8, 11), shift_goal=2000,
            shift_reg_tags=["shift-ga-purchaser"], shift_campaign_match="shift",
            shift_pace_curve=DEFAULT_SHIFT_CURVE, stage_map=DEFAULT_STAGE_MAP,
            payment_plan_map=DEFAULT_PAYMENT_PLAN_MAP)
        s.add(launch)
        await s.flush()
        for ch, n in [("Meta", 6), ("Organic / Existing", 3), ("Comped", 1)]:
            for i in range(n):
                s.add(MetricRecord(tenant_id=biz.tenant_id, business_id=biz.id, source="ghl",
                                   kind="bc_shift_reg", external_id=f"{ch}{i}", status="registered",
                                   meta={"channel": ch, "utm_campaign": "KB The Shift Aug" if ch == "Meta" else None}))
        await s.commit()
        lid, tid = launch.id, biz.tenant_id

    d = await _compute_at(lid, tid, dt.date(2026, 7, 28))
    src = d["shift"]["sources"]
    assert src["total"] == 10 and src["paid"] == 6 and src["organic"] == 3 and src["comped"] == 1
    chans = {c["key"]: c for c in src["channels"]}
    assert chans["meta"]["count"] == 6 and chans["meta"]["pct"] == 60 and chans["meta"]["paid"] is True
    assert d["shift"]["registrants"] == 10 and d["shift"]["source"] == "synced"

    async with SessionLocal() as s:
        from sqlalchemy import select as _sel
        launch = (await s.execute(_sel(Launch).where(Launch.id == lid))).scalar_one()
        dr = await drill_launch(s, tid, launch, "shift.source.meta", today=dt.date(2026, 7, 28))
    assert dr["type"] == "records" and dr["count"] == 6 and "Meta" in dr["title"]


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _login(c, email=OWNER_EMAIL, password=OWNER_PASSWORD):
    r = await c.post("/api/v1/auth/login", json={"email": email, "password": password})
    return r.json()["token"]


async def test_drill_resolves_records_and_calc():
    from app.db import SessionLocal
    from app.models import Launch
    from app.services.launch import drill_launch
    from sqlalchemy import select
    tenant_id, launch_id = await _make_launch()   # the full sample spread (opps + weekly)
    async with SessionLocal() as s:
        launch = (await s.execute(select(Launch).where(Launch.id == launch_id))).scalar_one()
        # records: enrolled opps
        enr = await drill_launch(s, tenant_id, launch, "funnel.enrolled", today=ASOF)
        assert enr["type"] == "records" and enr["count"] == 24
        # `source` joins each person to the channel their Shift registration recorded, so the
        # drawer ties out against the "where they came from" bar rather than only in aggregate.
        assert set(enr["columns"]) == {"name", "stage", "source", "payment", "url"}
        # Nobody in this fixture has a registration record, and that is a real answer.
        assert {r["source"] for r in enr["rows"]} == {"No Shift registration"}
        # calc: enrolled ARR breakdown
        arr = await drill_launch(s, tenant_id, launch, "enrolled.arr", today=ASOF)
        assert arr["type"] == "calc" and arr["value"] == "$310,000" and len(arr["steps"]) == 2
        # unknown metric → graceful fallback
        assert (await drill_launch(s, tenant_id, launch, "nope", today=ASOF))["value"] == "-"


async def test_drill_shift_expected_returns_curve_table():
    from app.db import SessionLocal
    from app.models import Launch
    from app.services.launch import drill_launch
    from sqlalchemy import select
    tenant_id, launch_id = await _make_shift_launch(registrants=175)
    async with SessionLocal() as s:
        launch = (await s.execute(select(Launch).where(Launch.id == launch_id))).scalar_one()
        r = await drill_launch(s, tenant_id, launch, "shift.expected", today=dt.date(2026, 7, 28))
    assert r["type"] == "calc" and len(r["table"]) == 15          # full day-by-day curve
    today_row = [t for t in r["table"] if t["today"]][0]
    assert today_row["day"] == 14 and today_row["expected"] == 380  # where we should be, day 14
    # records path for registrants
    async with SessionLocal() as s:
        launch = (await s.execute(select(Launch).where(Launch.id == launch_id))).scalar_one()
        regs = await drill_launch(s, tenant_id, launch, "shift.registrants", today=dt.date(2026, 7, 28))
    assert regs["type"] == "records" and regs["count"] == 175


async def test_drill_endpoint_authorized():
    await _make_launch()
    async with await _client() as c:
        tok = await _login(c)
        r = await c.get("/api/v1/businesses/springb/launches/active/drill/funnel.leads",
                        headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200, r.text
        assert r.json()["type"] == "records"


async def test_active_endpoint_serves_launch():
    await _make_launch()
    async with await _client() as c:
        tok = await _login(c)
        r = await c.get("/api/v1/businesses/springb/launches/active",
                        headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["launch"]["name"] == "August 2026 Cohort"
        assert body["seat_target"] == 77
        assert body["funnel"][0]["owner"] == "marketing"


async def _make_launch_only():
    """Just the launch config (no opp records) — for exercising the sync writer."""
    from app.db import SessionLocal
    from app.models import Business, Launch, LaunchWeekly, MetricRecord
    from app.services.launch import DEFAULT_STAGE_MAP, DEFAULT_PAYMENT_PLAN_MAP
    from sqlalchemy import select, delete
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(LaunchWeekly))
        await s.execute(delete(Launch).where(Launch.business_id == biz.id))
        await s.execute(delete(MetricRecord).where(
            MetricRecord.business_id == biz.id, MetricRecord.kind == "bc_launch_opp"))
        launch = Launch(tenant_id=biz.tenant_id, business_id=biz.id,
                        stage_map=DEFAULT_STAGE_MAP, payment_plan_map=DEFAULT_PAYMENT_PLAN_MAP, **AUG)
        s.add(launch)
        await s.commit()
        return biz.tenant_id, biz.id, launch.id


async def test_sync_snapshot_from_ghl_opps():
    """Section-7 writer: GHL-shaped opps → bc_launch_opp + LaunchWeekly, then compute reads them.
    Verifies stage_map grouping, app-in sub-signal, PIF/plan resolution, pipeline scoping,
    and won-date grace."""
    from app.db import SessionLocal
    from app.models import Launch
    from app.services.sync import snapshot_launch_opps
    from app.services.launch import compute_launch
    from sqlalchemy import select

    tenant_id, biz_id, launch_id = await _make_launch_only()
    PIPE = "pl1"
    pipeline_name = {PIPE: "Be Collective August 2026 Sales Funnel", "other": "Renewals"}
    stage_name = {"s_lead": "Opt In - No Call Booked",
                  "s_book": "Scheduled Appointment - App Submitted",
                  "s_dec": "Appointment Complete - Needs Decision",
                  # Committed = cash received (2026-08-13 rule); "Payment Sent" now sits in Deciding.
                  "s_com": "Payment Received - Contract Sent",
                  "s_enr": "Won: Onboarded", "s_ns": "Appointment No Show / Cancel"}
    plan_by_contact = {"cP": "pif", "cF": "monthly"}     # Payment Plan field values
    W = "2026-08-20"                                       # in this ISO week + in window

    def o(oid, sid, contact="c0", status="open", won=None, created=W, pipe=PIPE):
        return {"id": oid, "contactId": contact, "pipelineId": pipe, "pipelineStageId": sid,
                "status": status, "monetaryValue": 12000, "createdAt": created,
                "lastStatusChangeAt": won, "name": oid}

    opps = [
        o("l1", "s_lead"), o("l2", "s_lead"), o("l3", "s_lead"),
        o("b1", "s_book"), o("b2", "s_book"),
        o("d1", "s_dec"), o("d2", "s_dec"),
        o("c1", "s_com", contact="cP", status="won"),                       # committed, PIF
        o("e1", "s_enr", contact="cP", status="won", won=W),                # enrolled PIF
        o("e2", "s_enr", contact="cF", status="won", won=W),                # enrolled plan
        o("ns", "s_ns"),
        o("x1", "s_enr", status="won", won=W, pipe="other"),               # other pipeline → excluded
        o("late", "s_enr", contact="cP", status="won", won="2026-07-01"),  # won before window → excluded
    ]
    async with SessionLocal() as s:
        n = await snapshot_launch_opps(s, tenant_id, biz_id, opps, stage_name, pipeline_name,
                                       plan_by_contact, financed_contacts=set(),
                                       location_id="loc", today=ASOF)
        assert n == 11        # 13 opps − other-pipeline − out-of-window won

    async with SessionLocal() as s:
        launch = (await s.execute(select(Launch).where(Launch.id == launch_id))).scalar_one()
        d = await compute_launch(s, tenant_id, launch, today=ASOF)
    stages = {f["key"]: f for f in d["funnel"]}
    assert stages["leads"]["count"] == 3
    assert stages["booked"]["count"] == 2 and stages["booked"]["tag"] is None   # apps-in retired
    assert d["deciding"]["count"] == 2
    assert d["committed"] == {"pif": 1, "plan": 0, "seats": 1, "arr": 12000.0}
    assert d["enrolled"] == {"pif": 1, "plan": 1, "seats": 2, "arr": 26000.0}
    assert d["side"]["no_show"] == 1
    assert d["momentum"]["closes"][-1] == 2 and d["momentum"]["optins"][-1] == 3


async def test_create_requires_admin_and_reprices():
    async with await _client() as c:
        tok = await _login(c)
        payload = {**{k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in AUG.items()}}
        r = await c.post("/api/v1/businesses/springb/launches", json=payload,
                         headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 201, r.text
        assert r.json()["seat_target"] == 77
        lid = None
        rows = await c.get("/api/v1/businesses/springb/launches",
                           headers={"Authorization": f"Bearer {tok}"})
        lid = rows.json()[0]["id"]
        # raise the PIF price → blended rises → target falls (reprice on read, no resync)
        r2 = await c.put(f"/api/v1/businesses/springb/launches/{lid}",
                         json={"ticket_pif": 16000},
                         headers={"Authorization": f"Bearer {tok}"})
        assert r2.status_code == 200, r2.text
        assert r2.json()["seat_target"] < 77


async def test_a_member_who_enrolled_without_a_booked_call_still_counts_as_a_seat():
    """Enrolled read 19 against a stage of 22 where 20 was right (Connor, 2026-09-07).

    Seats were counted off the four-type Payment Type, which only exists on a SalesCall row —
    and a SalesCall row only exists for an opp with a booking, an outcome or a rep. Somebody
    who enrolled with none of those was dropped from the headline while the audit drawer,
    reading the opportunity snapshot, still listed them. The comped members (no payment type
    at all) must STILL be excluded, which is the distinction the old code lost.
    """
    import datetime as dt
    from app.db import SessionLocal
    from app.models import Business, Launch, MetricRecord, SalesCall
    from app.services.launch import (compute_launch, DEFAULT_STAGE_MAP,
                                     DEFAULT_PAYMENT_PLAN_MAP)
    from sqlalchemy import select, delete

    PRICES = {"PIF": {"acv": 12000, "upfront": 12000},
              "Financed": {"acv": 14000, "upfront": 5000, "monthly": 750, "months": 12},
              "Monthly": {"acv": 13000, "upfront": 1000, "monthly": 1000, "months": 12},
              "Custom": {"acv": None}}

    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(SalesCall))
        await s.execute(delete(Launch).where(Launch.business_id == biz.id))
        await s.execute(delete(MetricRecord).where(
            MetricRecord.business_id == biz.id, MetricRecord.kind == "bc_launch_opp"))
        launch = Launch(tenant_id=biz.tenant_id, business_id=biz.id, name="P",
                        program="beCollective", window_start=dt.date(2026, 8, 1),
                        window_end=dt.date(2026, 9, 12), goal_arr=1_000_000, ticket_pif=12000,
                        ticket_plan=14000, price_map=PRICES, goal_basis="seats", seat_goal=40,
                        stage_map=DEFAULT_STAGE_MAP, payment_plan_map=DEFAULT_PAYMENT_PLAN_MAP,
                        pipeline_match="be collective experience #1 sales")
        s.add(launch)
        await s.flush()
        lid = str(launch.id)

        def opp(eid, payment_type):
            s.add(MetricRecord(
                tenant_id=biz.tenant_id, business_id=biz.id, source="ghl", kind="bc_launch_opp",
                external_id=eid, status="won",
                meta={"launch_id": lid, "group": "enrolled", "payment_type": payment_type}))

        def call(eid, four_type):
            s.add(SalesCall(tenant_id=biz.tenant_id, launch_id=launch.id, opportunity_id=eid,
                            booking_id=f"b{eid}", contact_name=eid, is_current=True,
                            payment_type=four_type,
                            call_time_utc=dt.datetime(2026, 8, 20, 15, tzinfo=dt.timezone.utc)))

        for i in range(3):                       # ordinary members: call logged, four-type known
            opp(f"pif{i}", "pif")
            call(f"pif{i}", "PIF")
        # Cortni: a real payment type on the opportunity, but no booking, outcome or rep — so
        # sync never wrote a SalesCall row and her four-type is nowhere in the database.
        opp("cortni", "plan")
        # Beth and Lauren: comped. No payment type anywhere, and they must stay out of the count.
        opp("beth", None)
        opp("lauren", None)
        await s.commit()
        lid_uuid = launch.id

    async with SessionLocal() as s:
        launch = (await s.execute(select(Launch).where(Launch.id == lid_uuid))).scalar_one()
        d = await compute_launch(s, launch.tenant_id, launch, today=dt.date(2026, 8, 25))

    assert d["enrolled"]["seats"] == 4, "3 with calls + Cortni; the two comped stay out"
    # The audit drawer counts the STAGE, and must still show all six - the comped members are
    # members, they just are not paid seats. That gap between 6 and 4 is the intended one.
    async with SessionLocal() as s:
        launch2 = (await s.execute(select(Launch).where(Launch.id == lid_uuid))).scalar_one()
        from app.services.launch import _launch_opps, group_counts
        opps = await _launch_opps(s, launch2.tenant_id, launch2.business_id, launch2)
    assert group_counts(opps)["enrolled"] == 6
    # Priced, not silently free: 3 x 12000 known, Cortni at the blended price of the known ones.
    assert d["enrolled"]["arr"] == 12000 * 3 + 12000
    assert any("blended price" in w for w in d["warnings"]), d["warnings"]
