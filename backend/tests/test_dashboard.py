"""End-to-end dashboard test against the ASGI app (in-process, SQLite).

Seeds tenant #1, logs in, fetches /dashboard, and asserts the shape + the
headline operational/financial numbers the mockup defines.
"""
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.seed import seed, OWNER_EMAIL, OWNER_PASSWORD


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _client_token():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
        assert r.status_code == 200, r.text
        return r.json()["token"]


async def test_login_and_dashboard_shape():
    token = await _client_token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.get("/api/v1/dashboard?period=mtd",
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    d = r.json()

    # Top-level shape mirrors the mockup data objects.
    assert set(d) >= {"period", "portfolio", "scorecards", "areas", "flywheel", "sources"}
    assert set(d["areas"]) == {"ulrg", "springb", "sympli"}

    # ULRG operational figures are computed from seeded transactions/leads.
    ulrg = d["areas"]["ulrg"]
    ops = {o["label"]: o["value"] for o in ulrg["ops"]}
    assert ops["Units closed"] == "38"
    assert ops["Volume"] == "$14.2M"
    assert ops["Agents producing"] == "24"
    funnel = {f["label"]: f["v"] for f in ulrg["funnel"]}
    assert funnel == {"Leads": 680, "Appointments": 142, "Under contract": 46, "Closed": 38}

    # Financials (Phase 2) light up from the seeded P&L snapshots.
    assert ulrg["revenue"] == 420000
    assert ulrg["noi"] == 72000
    assert d["portfolio"]["revenue"] == 570000      # full sum (matches mockup)
    assert d["portfolio"]["noi"] == 109000
    assert d["portfolio"]["cash"] == 340000

    # Composition adds up and is ordered ulrg, sympli, springb.
    comp = d["portfolio"]["composition"]
    assert [c["key"] for c in comp] == ["ulrg", "sympli", "springb"]
    assert abs(sum(c["pct"] for c in comp) - 100) < 0.2

    # Sympli is a JV — its P&L carries the "Spring's JV share" row.
    sympli_pl = {r["kind"]: r for r in d["areas"]["sympli"]["pl"]}
    assert "share" in sympli_pl
    assert sympli_pl["share"]["value"] == 11000     # 50% of 22000 NOI

    # Scorecards present with the mockup labels + business_key for dot colors.
    labels = {s["label"]: s for s in d["scorecards"]}
    assert labels["Combined profit"]["value"] == "$109K"
    assert labels["Combined profit"]["business_key"] == "portfolio"
    assert labels["Active members"]["value"] == "142"

    # Sources collapse per provider; QBO connected, Arive still pending (Phase 3).
    src = {s["name"]: s["status"] for s in d["sources"]}
    assert src["QuickBooks"] == "connected"
    assert src["Arive"] == "disconnected"

    # Flywheel stays gated until Phase 3.
    assert d["flywheel"]["available"] is False
