"""Live beCollective GHL sync (sync_becollective_ghl) against mocked HighLevel.

Drives the sync with canned contacts/pipelines/opportunities from beCollective's
own GHL location and asserts it lands the bc_* metric records the view reads —
without touching the network or the Forum's records."""
import datetime as dt

import pytest
from sqlalchemy import select, func

from app.seed import seed
from app.db import SessionLocal
from app.models import Business, Integration, MetricRecord
from app.security import enc
from app.integrations import ghl
from app.services.sync import sync_becollective_ghl
from app.services.becollective import build_becollective


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


# ── Canned beCollective GHL payloads ───────────────────────────────
_TODAY = dt.date.today().isoformat()

_CONTACTS = [
    {"id": "c1", "firstName": "Fin", "lastName": "Anced", "tags": ["be collective financed"]},
    {"id": "c2", "firstName": "Paid", "lastName": "Full", "tags": ["be collective payment complete"]},
    {"id": "c3", "firstName": "New", "lastName": "Grad",
     "tags": ["be collective won onboarded group 1", "the shift ticket purchased"]},
    {"id": "c4", "firstName": "Just", "lastName": "Guest", "tags": ["the shift ticket purchased"]},
    {"id": "c5", "firstName": "Cold", "lastName": "Lead", "tags": ["random"]},
]
_PIPELINES = [{
    "id": "pipe-bc", "name": "Be Collective Main Sales Funnel",
    "stages": [
        {"id": "st-optin", "name": "Opt In - No Call Booked"},
        {"id": "st-appt", "name": "Appointment Complete - Needs Decision"},
        {"id": "st-pay", "name": "Payment Sent: Financed"},
        {"id": "st-won", "name": "Won: Onboarded Group 1"},
    ],
}]
_OPPS = [
    {"id": "o1", "pipelineId": "pipe-bc", "pipelineStageId": "st-won", "status": "won",
     "contactId": "c1", "monetaryValue": 6500, "lastStatusChangeAt": _TODAY, "name": "Fin Anced"},
    {"id": "o2", "pipelineId": "pipe-bc", "pipelineStageId": "st-won", "status": "won",
     "contactId": "c2", "monetaryValue": 6000, "lastStatusChangeAt": _TODAY, "name": "Paid Full"},
    {"id": "o3", "pipelineId": "pipe-bc", "pipelineStageId": "st-optin", "status": "open",
     "contactId": "c9", "monetaryValue": 0, "name": "Prospect A"},
    {"id": "o4", "pipelineId": "pipe-bc", "pipelineStageId": "st-appt", "status": "open",
     "contactId": "c8", "monetaryValue": 0, "name": "Prospect B"},
]


@pytest.fixture
def _mock_ghl(monkeypatch):
    async def contacts(token, location_id, max_pages=None):
        return _CONTACTS

    async def pipelines(token, location_id):
        return _PIPELINES

    async def opportunities(token, location_id, max_pages=None):
        return _OPPS

    monkeypatch.setattr(ghl, "get_contacts", contacts)
    monkeypatch.setattr(ghl, "get_pipelines", pipelines)
    monkeypatch.setattr(ghl, "get_opportunities", opportunities)


async def _bc_integration(s):
    biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
    integ = (await s.execute(select(Integration).where(
        Integration.business_id == biz.id, Integration.provider == "ghl_bc"))).scalar_one()
    integ.access_token_enc = enc("bc-fake-token")
    integ.config = {**(integ.config or {}), "location_id": "bc-loc-123"}
    await s.commit()
    return biz, integ


async def test_sync_lands_bc_records(_mock_ghl):
    async with SessionLocal() as s:
        biz, integ = await _bc_integration(s)
        n = await sync_becollective_ghl(s, biz.tenant_id, integ)

        async def count(kind, *extra):
            return int((await s.execute(select(func.count()).select_from(MetricRecord).where(
                MetricRecord.business_id == biz.id, MetricRecord.source == "ghl",
                MetricRecord.kind == kind, *extra))).scalar() or 0)

        assert await count("bc_member") == 3                 # c1, c2, c3
        assert await count("bc_registration") == 2           # c3 (member) + c4 (guest)
        assert await count("bc_membership") == 2             # two won-onboarded opps
        assert await count("bc_onboarded") == 2
        assert await count("bc_recruiting") == 2             # open (non-won) opps in the funnel
        assert n == 3 + 2 + 2 + 2 + 2

        # Payment split: c1 is tagged financed → its membership is "monthly".
        memberships = (await s.execute(select(MetricRecord).where(
            MetricRecord.business_id == biz.id, MetricRecord.kind == "bc_membership"))).scalars().all()
        assert sum(1 for m in memberships if (m.meta or {}).get("payment") == "monthly") == 1

        # The Forum's records are untouched by the beCollective sync.
        assert await count("member") == 70


async def test_sync_feeds_the_view(_mock_ghl):
    async with SessionLocal() as s:
        biz, integ = await _bc_integration(s)
        await sync_becollective_ghl(s, biz.tenant_id, integ)
        d = await build_becollective(s, biz.tenant_id, "mtd")

    assert d["members_total"] == 3
    kpis = {k["label"]: k["value"] for k in d["kpis"]}
    assert kpis["Active Members"] == "3"
    assert kpis["Registered"] == "1"       # c3 only; c4 is a guest
    assert kpis["Financed"] == "1"
    assert kpis["In Pipeline"] == "2"      # two open recruiting opps
    assert kpis["New Members"] == "2"      # both onboarded this period
    assert d["event"]["title"].startswith("beCollective")   # event config from the ghl_bc row
