"""beCollective focused view (/api/v1/becollective) — cohort model, bc_* kinds."""
import datetime as dt

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.seed import seed, OWNER_EMAIL, OWNER_PASSWORD


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _token():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
        return r.json()["token"]


async def _seed_bc():
    from app.db import SessionLocal
    from app.models import Business, MetricRecord
    from sqlalchemy import select, delete
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(MetricRecord).where(
            MetricRecord.business_id == biz.id, MetricRecord.kind.like("bc_%")))

        def add(**kw):
            s.add(MetricRecord(tenant_id=biz.tenant_id, business_id=biz.id, source="ghl", **kw))

        for n in ("A", "B", "C"):
            add(kind="bc_member", external_id=n, name=n, status="active", segment="becollective")
        add(kind="bc_membership", external_id="m1", name="A", status="active", amount=6000,
            meta={"payment": "pif", "contact_id": "A"})
        add(kind="bc_membership", external_id="m2", name="B", status="active", amount=6000,
            meta={"payment": "pif", "contact_id": "B"})
        add(kind="bc_membership", external_id="m3", name="C", status="active", amount=6500,
            meta={"payment": "monthly", "contact_id": "C"})    # financed
        for ext, stage in [("o1", "Opt In - No Call Booked"), ("o2", "Scheduled Appointment - App Submitted"),
                           ("o3", "Appointment Complete - Needs Decision"), ("o4", "Payment Sent: Financed"),
                           ("o5", "Appointment No Show / Cancel")]:
            add(kind="bc_recruiting", external_id=ext, name=ext, status="open",
                meta={"stage": stage})
        add(kind="bc_onboarded", external_id="on1", name="New", occurred_on=dt.date.today(),
            amount=6000, segment="becollective")
        for ext, nm, guest in [("r1", "A", False), ("r2", "B", False), ("rg", "Guest", True)]:
            add(kind="bc_registration", external_id=ext, name=nm, status="registered",
                segment="becollective", meta={"guest": guest, "contact_id": nm})
        await s.commit()


async def test_becollective_payload():
    await _seed_bc()
    token = await _token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        d = (await c.get("/api/v1/becollective?period=mtd",
                         headers={"Authorization": f"Bearer {token}"})).json()

    assert d["members_total"] == 3
    kpis = {k["label"]: k["value"] for k in d["kpis"]}
    assert kpis["Active Members"] == "3"
    assert kpis["New Members"] == "1"
    assert kpis["Registered"] == "2"            # members only, guest excluded
    assert kpis["Financed"] == "1"              # one monthly/financed membership
    assert kpis["In Pipeline"] == "5"           # all open recruiting opps

    # funnel groups (No-Show → footer, not a bar); billing omitted for the cohort model
    labels = [st["label"] for st in d["funnel"]["stages"]]
    assert labels == ["Applied", "Appointment", "Payment sent"]
    assert d["billing"] is None
    assert "event" in {c["k"] for c in d["deck"]}


async def test_forum_still_works():
    # Regression: beCollective must not touch the Forum endpoint.
    token = await _token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        f = (await c.get("/api/v1/forum?period=mtd", headers={"Authorization": f"Bearer {token}"})).json()
    assert f["members_total"] == 70 and "billing" in f    # Forum numbers intact
