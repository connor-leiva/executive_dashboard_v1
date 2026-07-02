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
    assert ops["Units Closed"] == "38"
    assert ops["Volume"] == "$14.2M"
    assert ops["GCI"] == "$420K"
    assert ops["Agents Producing"] == "24"
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
    assert labels["Combined Profit"]["value"] == "$109K"
    assert labels["Combined Profit"]["business_key"] == "portfolio"
    assert labels["Active Members"]["value"] == "—"       # placeholder until GHL syncs

    # Sources collapse per provider; QBO connected, Arive still pending (Phase 3).
    src = {s["name"]: s["status"] for s in d["sources"]}
    assert src["QuickBooks"] == "connected"
    assert src["Arive"] == "disconnected"

    # Flywheel stays gated until Phase 3.
    assert d["flywheel"]["available"] is False


async def test_metric_detail_units_closed():
    token = await _client_token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.get("/api/v1/metrics/units_closed/detail?period=mtd",
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "Sisu"
    assert d["count"] == 38 and len(d["rows"]) == 38
    row = d["rows"][0]
    assert {"name", "sale_price", "source_url"} <= set(row)
    assert row["source_url"] and "app.sisu.co" in row["source_url"]


async def test_create_ghl_integration():
    token = await _client_token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        H = {"Authorization": f"Bearer {token}"}
        r = await c.post("/api/v1/integrations", headers=H, json={
            "provider": "ghl", "business_key": "springb", "token": "pit-secret",
            "config": {"location_id": "loc1", "member_tags": ["the forum active"]}})
        assert r.status_code == 200, r.text
        integs = (await c.get("/api/v1/integrations", headers=H)).json()
        ghl = [i for i in integs if i["provider"] == "ghl"]
        assert ghl and ghl[0]["status"] == "connected" and ghl[0]["business_key"] == "springb"


async def test_active_members_drilldown():
    # Insert a couple of GHL member records, then confirm the drawer lists them.
    from app.db import SessionLocal
    from app.models import Business, MetricRecord
    from sqlalchemy import select as _select
    async with SessionLocal() as s:
        biz = (await s.execute(_select(Business).where(Business.key == "springb"))).scalar_one()
        for i in range(2):
            s.add(MetricRecord(tenant_id=biz.tenant_id, business_id=biz.id, source="ghl",
                               kind="member", external_id=f"c{i}", name=f"Member {i}",
                               status="active", segment="forum",
                               source_url=f"https://app.gohighlevel.com/x/{i}"))
        await s.commit()
    token = await _client_token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.get("/api/v1/metrics/active_members/detail",
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "Go High Level"
    assert d["count"] == 2 and len(d["rows"]) == 2
    assert d["rows"][0]["source_url"]


async def test_forum_kpis_from_ghl_records():
    """The Forum panel: members (segmented) + ARR/renewals (renewals pipeline) +
    new members (onboarded) + registered (event tag) + MRR (subscriptions)."""
    import datetime as _dt
    from app.db import SessionLocal
    from app.models import Business, MetricRecord
    from sqlalchemy import select as _select, delete as _delete
    today = _dt.date.today()
    this_month = today.strftime("%B")
    async with SessionLocal() as s:
        biz = (await s.execute(_select(Business).where(Business.key == "springb"))).scalar_one()
        # deterministic: clear any GHL records a prior test seeded
        await s.execute(_delete(MetricRecord).where(
            MetricRecord.business_id == biz.id, MetricRecord.source == "ghl"))

        def add(**kw):
            s.add(MetricRecord(tenant_id=biz.tenant_id, business_id=biz.id, source="ghl", **kw))

        # members: 2 Forum + 1 Inner Circle (Active Members = 3, segmented)
        add(kind="member", external_id="m1", name="ann lee", status="active", segment="forum")
        add(kind="member", external_id="m2", name="bo diaz", status="active", segment="forum")
        add(kind="member", external_id="m3", name="cy roe", status="active", segment="inner_circle")
        # memberships (renewals pipeline): ARR = 27000+30000+26000 = 83000; one renews this month
        add(kind="membership", external_id="o1", name="Ann Lee", status="active", amount=27000,
            meta={"renewal_month": this_month})
        add(kind="membership", external_id="o2", name="Bo Diaz", status="active", amount=30000,
            meta={"renewal_month": "March"})
        add(kind="membership", external_id="o3", name="Cy Roe", status="active", amount=26000,
            meta={"renewal_month": "May"})
        # onboarded: 2 this period, 1 outside → New Members = 2
        add(kind="onboarded", external_id="w1", name="New A", status="won", occurred_on=today)
        add(kind="onboarded", external_id="w2", name="New B", status="won", occurred_on=today)
        add(kind="onboarded", external_id="w3", name="Old", status="won",
            occurred_on=today - _dt.timedelta(days=90))
        # registrations (event tag): 4
        for i in range(4):
            add(kind="registration", external_id=f"r{i}", name=f"reg {i}", status="registered")
        # subscriptions: 2 active ($2500 + $1500 = $4000 MRR) + 1 cancelled
        add(kind="subscription", external_id="s1", name="Sub A", amount=2500, status="active")
        add(kind="subscription", external_id="s2", name="Sub B", amount=1500, status="active")
        add(kind="subscription", external_id="s3", name="Sub C", amount=999, status="cancelled")
        await s.commit()

    token = await _client_token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        H = {"Authorization": f"Bearer {token}"}
        d = (await c.get("/api/v1/dashboard?period=mtd", headers=H)).json()
        ops = {o["label"]: o for o in d["areas"]["springb"]["ops"]}
        assert ops["Active Members"]["value"] == "3"
        assert "Forum 2" in ops["Active Members"]["sub"] and "Inner Circle 1" in ops["Active Members"]["sub"]
        assert ops["Forum ARR"]["value"] == "$83K" and ops["Forum ARR"]["key"] == "forum_arr"
        assert ops["New Members"]["value"] == "2"
        assert ops["Renewals Due"]["value"] == "1"                 # only the one renewing this month
        assert ops["Registered"]["value"] == "4"
        assert ops["MRR"]["value"] == "$4K"

        arr = (await c.get("/api/v1/metrics/forum_arr/detail", headers=H)).json()
        assert arr["count"] == 3 and "renews" in arr["rows"][0]["status"]
        nm = (await c.get("/api/v1/metrics/new_members/detail?period=mtd", headers=H)).json()
        assert nm["count"] == 2
        reg = (await c.get("/api/v1/metrics/registered/detail", headers=H)).json()
        assert reg["count"] == 4


async def test_edit_business_and_derived_status():
    token = await _client_token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        H = {"Authorization": f"Bearer {token}"}
        # Partial update: tag + a watch threshold. ULRG's margin is 17% (72k/420k).
        r = await c.put("/api/v1/businesses/ulrg", headers=H,
                        json={"tag": "Real estate team", "watch_margin_below": 20})
        assert r.status_code == 200, r.text
        assert r.json()["tag"] == "Real estate team"
        assert r.json()["watch_margin_below"] == 20.0

        # 17% < 20% threshold → status derives to 'watch'.
        d = (await c.get("/api/v1/dashboard?period=mtd", headers=H)).json()
        assert d["areas"]["ulrg"]["status"] == "watch"
        assert d["areas"]["ulrg"]["tag"] == "Real estate team"

        # Drop the threshold below the margin → falls back to stored 'healthy'.
        await c.put("/api/v1/businesses/ulrg", headers=H, json={"watch_margin_below": 10})
        d2 = (await c.get("/api/v1/dashboard?period=mtd", headers=H)).json()
        assert d2["areas"]["ulrg"]["status"] == "healthy"

        # Validation: bad status + out-of-range jv_share are rejected.
        assert (await c.put("/api/v1/businesses/ulrg", headers=H, json={"status": "nope"})).status_code == 400
        assert (await c.put("/api/v1/businesses/ulrg", headers=H, json={"jv_share": 1.5})).status_code == 400
        assert (await c.put("/api/v1/businesses/ghostbiz", headers=H, json={"tag": "x"})).status_code == 404


async def test_edit_integration_token_optional_and_config_returned():
    token = await _client_token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        H = {"Authorization": f"Bearer {token}"}
        # Connecting a brand-new token-based source requires a token.
        r0 = await c.post("/api/v1/integrations", headers=H,
                          json={"provider": "arive", "business_key": "sympli", "config": {"pipeline": "x"}})
        assert r0.status_code == 400

        # Connect GHL with a token, then EDIT it with no token (keeps the key) + new config.
        await c.post("/api/v1/integrations", headers=H, json={
            "provider": "ghl", "business_key": "springb", "token": "pit-secret",
            "config": {"location_id": "loc1", "member_tags": ["the forum active"]}})
        r1 = await c.post("/api/v1/integrations", headers=H, json={
            "provider": "ghl", "business_key": "springb",
            "config": {"location_id": "loc1", "member_tags": ["the forum active"],
                       "forum_calendar_id": "cal-123"}})
        assert r1.status_code == 200, r1.text

        ghl = [i for i in (await c.get("/api/v1/integrations", headers=H)).json() if i["provider"] == "ghl"][0]
        assert ghl["status"] == "connected"                       # token preserved, still connected
        assert ghl["config"]["forum_calendar_id"] == "cal-123"    # config editable + returned
