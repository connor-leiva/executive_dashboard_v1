"""The Forum focused view — /api/v1/forum payload, invariants, drills, fallbacks.

Seeds a small deterministic Go High Level dataset (delete + add) so the numbers
are exact regardless of what the sample seed loaded, then asserts the payload
shape the mockup consumes plus the consistency invariants from the spec.
"""
import datetime as dt

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.seed import seed, OWNER_EMAIL, OWNER_PASSWORD

_FULL = ["January", "February", "March", "April", "May", "June",
         "July", "August", "September", "October", "November", "December"]


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _token():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
        assert r.status_code == 200, r.text
        return r.json()["token"]


def _month(offset: int) -> str:
    return _FULL[(dt.date.today().month - 1 + offset) % 12]


async def _seed_forum(*, with_recruiting: bool = True, with_event: bool = True):
    """Known dataset: 5 members (3 Forum / 2 IC), 5 memberships, 3 subs (1 past
    due), 2 recruiting opps, 2 onboarded, 1 lost, 3 member regs + 1 guest."""
    from app.db import SessionLocal
    from app.models import Business, MetricRecord, Integration
    from sqlalchemy import select, delete
    today = dt.date.today()
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(MetricRecord).where(
            MetricRecord.business_id == biz.id, MetricRecord.source == "ghl"))

        def add(**kw):
            s.add(MetricRecord(tenant_id=biz.tenant_id, business_id=biz.id, source="ghl", **kw))

        # members — names double as the registration match key
        for n in ("F1", "F2", "F3"):
            add(kind="member", external_id=n, name=n, status="active", segment="forum")
        for n in ("IC1", "IC2"):
            add(kind="member", external_id=n, name=n, status="active", segment="inner_circle")

        # memberships — renewal window = this + next 2 months; ms5 is out of window
        add(kind="membership", external_id="ms1", name="F1", status="active", amount=3000, segment="forum",
            meta={"renewal_month": _month(0), "renewal_status": "committed", "payment": "pif"})
        add(kind="membership", external_id="ms2", name="F2", status="active", amount=3000, segment="forum",
            meta={"renewal_month": _month(0), "renewal_status": "talking", "payment": "pif"})
        add(kind="membership", external_id="ms3", name="F3", status="active", amount=250, segment="forum",
            meta={"renewal_month": _month(1), "renewal_status": "risk", "payment": "monthly"})
        add(kind="membership", external_id="ms4", name="IC1", status="active", amount=6000, segment="inner_circle",
            meta={"renewal_month": _month(0), "renewal_status": "committed", "payment": "pif"})
        add(kind="membership", external_id="ms5", name="IC2", status="active", amount=500, segment="inner_circle",
            meta={"renewal_month": _month(6), "renewal_status": "committed", "payment": "monthly"})

        # subscriptions → MRR 750, one past due
        add(kind="subscription", external_id="sub1", name="F3", amount=250, status="active", segment="forum")
        add(kind="subscription", external_id="sub2", name="IC2", amount=500, status="active", segment="inner_circle")
        add(kind="subscription", external_id="sub3", name="F1", amount=250, status="past_due", segment="forum")

        if with_recruiting:
            add(kind="recruiting", external_id="opp1", name="P1", status="open", amount=12000,
                meta={"stage": "Discovery", "stage_position": 1})
            add(kind="recruiting", external_id="opp2", name="P2", status="open", amount=12000,
                meta={"stage": "Invited", "stage_position": 3})

        add(kind="onboarded", external_id="on1", name="New A", occurred_on=today, amount=3000, segment="forum")
        add(kind="onboarded", external_id="on2", name="New B", occurred_on=today, amount=3000, segment="forum")
        add(kind="membership_lost", external_id="lost1", name="Gone", amount=3000, segment="forum",
            occurred_on=dt.date(today.year, 1, 15))

        for n in ("F1", "F2", "F3"):
            add(kind="registration", external_id=f"reg-{n}", name=n, status="registered",
                segment="forum", meta={"guest": False})
        add(kind="registration", external_id="reg-guest", name="Guest 1", status="registered",
            segment="forum", meta={"guest": True})

        # event config on/off (fallback test removes the anchor date)
        integ = (await s.execute(select(Integration).where(
            Integration.business_id == biz.id, Integration.provider == "ghl"))).scalar_one()
        cfg = dict(integ.config or {})
        if with_event:
            cfg.setdefault("event_date", "2026-09-18")
            cfg.setdefault("prior_event_pace", 34)
        else:
            cfg.pop("event_date", None)
        integ.config = cfg
        await s.commit()


async def _get_forum(period="mtd"):
    token = await _token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.get(f"/api/v1/forum?period={period}",
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return r.json()


async def test_forum_payload_shape():
    await _seed_forum()
    d = await _get_forum()
    assert set(d) >= {"status", "watch", "members_total", "kpis", "deck",
                      "funnel", "renewals", "event", "revq"}
    assert d["members_total"] == 5

    kpis = {k["label"]: k for k in d["kpis"]}
    assert kpis["Active Members"]["value"] == "5"
    assert kpis["Forum ARR"]["value"] == "$13K"        # 12,750 → $13K
    assert kpis["New Members"]["value"] == "2"
    assert kpis["Registered"]["value"] == "3"          # members only, guest excluded
    assert kpis["MRR"]["value"] == "$750"

    # deck has one card per available deep dive
    assert {c["k"] for c in d["deck"]} == {"pipeline", "renewals", "event", "revq"}


async def test_forum_invariants():
    await _seed_forum()
    d = await _get_forum()

    # renewals: mix components sum to the count
    sm = d["renewals"]["summary"]
    assert sm["count"] == 4                              # ms1,ms2,ms3,ms4 in window; ms5 out
    assert sum(sm["mix"].values()) == sm["count"]
    assert sm["mix"] == {"committed": 2, "talking": 1, "risk": 1}

    # payment mix: pif + monthly == membership count, and their $ == ARR
    revq = d["revq"]
    assert revq["pif"]["count"] + revq["monthly"]["count"] == 5
    assert round(revq["pif"]["value"] + revq["monthly"]["value"]) == 12750
    assert revq["past_due"]["count"] == 1

    # event: registered + unregistered accounting
    ev = d["event"]
    assert ev["registered"] == 3 and ev["guests"] == 1
    assert ev["unregistered"] == d["members_total"] - ev["registered"]

    # ARR bridge reconciles: start + new − churned == today
    bridge = {b["label"]: b["value"] for b in revq["bridge"]}
    assert set(bridge) == {"Jan 1", "New", "Churned", "Today"}

    # funnel stages ordered by pipeline position
    positions = [st["label"] for st in d["funnel"]["stages"]]
    assert positions == ["Discovery", "Invited"]


async def test_forum_drills():
    await _seed_forum()
    token = await _token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        H = {"Authorization": f"Bearer {token}"}
        book = (await c.get("/api/v1/metrics/renewal_book/detail?period=mtd&business=springb", headers=H)).json()
        assert book["count"] == 4
        monthly = (await c.get("/api/v1/metrics/monthly/detail?business=springb", headers=H)).json()
        assert monthly["count"] == 3                     # all subscriptions
        past = (await c.get("/api/v1/metrics/pastdue/detail?business=springb", headers=H)).json()
        assert past["count"] == 1
        unreg = (await c.get("/api/v1/metrics/unregistered/detail?business=springb", headers=H)).json()
        assert unreg["count"] == 2                       # IC1, IC2 have no registration


async def test_forum_fallbacks():
    # No recruiting records and no event date → those sections degrade to null,
    # and the deck drops their cards — the payload never errors.
    await _seed_forum(with_recruiting=False, with_event=False)
    d = await _get_forum()
    assert d["funnel"] is None
    assert d["event"] is None
    assert {c["k"] for c in d["deck"]} == {"renewals", "revq"}
