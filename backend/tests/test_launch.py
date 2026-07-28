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


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _login(c, email=OWNER_EMAIL, password=OWNER_PASSWORD):
    r = await c.post("/api/v1/auth/login", json={"email": email, "password": password})
    return r.json()["token"]


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
                  "s_com": "Payment Sent: Financed",
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
    assert stages["booked"]["count"] == 2 and stages["booked"]["tag"] == "2 of 2 apps in"
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
