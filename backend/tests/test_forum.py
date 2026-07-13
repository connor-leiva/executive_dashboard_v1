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

        # members — names double as the registration match key; each carries the CRM
        # membership detail (type / plan / value / brokerage) for the roster view.
        _mem = {
            "F1": {"member_type": "Primary Member", "member_kind": "primary", "payment": "monthly",
                   "status": "Active", "total_cost": 24000, "brokerage": "eXp Realty",
                   "stripe_account": "Legacy SB Account"},
            "F2": {"member_type": "Primary Member", "member_kind": "primary", "payment": "pif",
                   "status": "Active", "total_cost": 24000, "stripe_account": "Forum Sub-Account"},
            "F3": {"member_type": "Add-On Member", "member_kind": "add_on", "payment": "pif",
                   "status": "Active", "total_cost": 0, "stripe_account": "Legacy SB Account"},
            "IC1": {"member_type": "Primary Member", "member_kind": "primary", "payment": "installments",
                    "status": "Active", "total_cost": 6000, "brokerage": "Compass"},
            "IC2": {"member_type": "Primary Member", "member_kind": "primary", "payment": "quarterly",
                    "status": "Active", "total_cost": 6000},
        }
        for n in ("F1", "F2", "F3"):
            add(kind="member", external_id=n, name=n, email=f"{n.lower()}@forum.test",
                status="active", segment="forum", meta={"membership": _mem[n]})
        for n in ("IC1", "IC2"):
            add(kind="member", external_id=n, name=n, email=f"{n.lower()}@forum.test",
                status="active", segment="inner_circle", meta={"membership": _mem[n]})
        # an Admin (staff) — record status 'admin' keeps it out of member counts.
        add(kind="member", external_id="ADM1", name="Admin One", status="admin", segment="forum",
            meta={"membership": {"member_type": "Admin", "member_kind": "admin", "status": "Active"}})
        # F1's most-recent succeeded charge → last payment on the roster
        add(kind="payment", external_id="pay-f1", name="F1", email="f1@forum.test", amount=2500,
            status="succeeded", occurred_on=today - dt.timedelta(days=5), meta={"stream": "memberships"})

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
        add(kind="subscription", external_id="sub2", name="IC2", amount=500, status="active", segment="inner_circle",
            meta={"contact_id": "IC2", "next_payment_date": (today + dt.timedelta(days=20)).isoformat(),
                  "next_payment_amount": 500})
        add(kind="subscription", external_id="sub3", name="F1", amount=250, status="past_due", segment="forum")

        if with_recruiting:
            # Real Forum Main Sales Funnel stage names → group into Appointment / Contract sent.
            add(kind="recruiting", external_id="opp1", name="P1", status="open", amount=12000,
                meta={"stage": "Scheduled Appointment", "stage_position": 4})
            add(kind="recruiting", external_id="opp2", name="P2", status="open", amount=12000,
                meta={"stage": "Sent Contract: Single - PIF", "stage_position": 16})

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
            for _k in ("event_date", "event_name", "event_tag", "event_title",
                       "event_dates", "prior_event_pace"):
                cfg.pop(_k, None)
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
                      "funnel", "renewals", "event", "billing"}
    assert d["members_total"] == 5

    kpis = {k["label"]: k for k in d["kpis"]}
    assert kpis["Active Members"]["value"] == "5"
    assert kpis["Forum ARR"]["value"] == "$13K"        # 12,750 → $13K
    assert kpis["New Members"]["value"] == "2"
    assert kpis["Registered"]["value"] == "3"          # members only, guest excluded
    assert kpis["MRR"]["value"] == "$750"

    # deck has one card per available deep dive (Revenue Quality retired → 3 cards)
    assert {c["k"] for c in d["deck"]} == {"pipeline", "renewals", "event"}


async def test_forum_invariants():
    await _seed_forum()
    d = await _get_forum()

    # renewals: by month + segment (no health status — GHL doesn't track it)
    sm = d["renewals"]["summary"]
    assert sm["count"] == 4                              # ms1,ms2,ms3,ms4 in window; ms5 out
    assert sm["segments"] == {"F": 3, "IC": 1}          # 3 Forum + 1 Inner Circle
    assert sum(sm["segments"].values()) == sm["count"]
    assert "mix" not in sm                               # health status removed
    assert all("status" not in r for r in d["renewals"]["rows"])

    # event: registered + unregistered accounting
    ev = d["event"]
    assert ev["registered"] == 3 and ev["guests"] == 1
    assert ev["unregistered"] == d["members_total"] - ev["registered"]

    # funnel: raw stages collapsed into the clean groups, in order
    positions = [st["label"] for st in d["funnel"]["stages"]]
    assert positions == ["Appointment", "Contract sent"]


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


async def test_forum_roster_view():
    """The roster summary in the payload + the rich forum_roster drill both reflect the
    CRM membership detail (program split, primary/add-on, payment mix, contract value)."""
    await _seed_forum()
    d = await _get_forum()
    r = d["roster"]
    assert r["total"] == 5 and r["forum"] == 3 and r["inner_circle"] == 2   # members only
    assert r["primary"] == 4 and r["add_on"] == 1 and r["admin"] == 1        # admin counted apart
    assert r["payment_mix"] == {"monthly": 1, "quarterly": 1, "pif": 2, "installments": 1}
    assert d["members_total"] == 5                                           # admin not a member

    token = await _token()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        H = {"Authorization": f"Bearer {token}"}
        roster = (await c.get("/api/v1/metrics/forum_roster/detail?business=springb", headers=H)).json()
    assert roster["count"] == 6 and roster["view"] == "roster"   # 5 members + 1 admin row
    sm = roster["summary"]
    assert sm["total"] == 5 and sm["admin"] == 1                 # total is members only
    assert sm["add_on"] == 1 and sm["primary"] == 4
    assert sm["book"] == 60000                                  # 24000+24000+0+6000+6000 (admin excluded)
    by_name = {row["name"]: row for row in roster["rows"]}
    assert by_name["F3"]["kind"] == "add_on" and by_name["F3"]["member_type"] == "Add-On Member"
    assert by_name["F1"]["amount"] == 24000 and by_name["F1"]["brokerage"] == "eXp Realty"
    assert by_name["Admin One"]["kind"] == "admin"              # admin present in the rows
    # last payment (most recent succeeded charge by email) + next payment (from the sub)
    assert by_name["F1"]["last_payment"]["amount"] == 2500
    assert by_name["Ic2"]["next_payment"]["amount"] == 500      # IC2 title-cased → "Ic2"


async def test_recruiting_funnel_grouping():
    # VIP Guest must not fall into Applied on the word "application"; dead/nurture
    # stages (Unresponsive) are excluded from the bars and counted in the footer;
    # and the funnel is count-only (no dollar column).
    await _seed_forum()
    from app.db import SessionLocal
    from app.models import Business, MetricRecord
    from sqlalchemy import select, delete
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(MetricRecord).where(
            MetricRecord.business_id == biz.id, MetricRecord.source == "ghl",
            MetricRecord.kind == "recruiting"))
        for ext, stage in [("o1", "Qualifi"), ("o2", "VIP Guest- application submitted"),
                           ("o3", "VIP GUEST - Call Booked"), ("o4", "Unresponsive"),
                           ("o5", "Sent Contract: Dual - PIF")]:
            s.add(MetricRecord(tenant_id=biz.tenant_id, business_id=biz.id, source="ghl",
                               kind="recruiting", external_id=ext, name=ext, status="open",
                               meta={"stage": stage}))
        await s.commit()
    f = (await _get_forum())["funnel"]
    labels = {st["label"]: st["v"] for st in f["stages"]}
    assert labels == {"Applied": 1, "VIP Guest": 2, "Contract sent": 1}   # Unresponsive excluded
    assert all("value" not in st for st in f["stages"])                    # dollar column dropped
    assert "1 more" in (f["footer"] or "")                                 # the 1 nurture opp


async def test_event_renders_without_date():
    # Prod scenario: event_name is configured but event_date hasn't been set yet.
    # The event card must still render (days_out None) rather than disappear.
    await _seed_forum()
    from app.db import SessionLocal
    from app.models import Business, Integration
    from sqlalchemy import select
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        integ = (await s.execute(select(Integration).where(
            Integration.business_id == biz.id, Integration.provider == "ghl"))).scalar_one()
        cfg = dict(integ.config or {})
        cfg.pop("event_date", None)
        cfg["event_name"] = "Park City, UT"
        integ.config = cfg
        await s.commit()
    d = await _get_forum()
    assert d["event"] is not None and d["event"]["days_out"] is None
    assert d["event"]["where"] == "Park City, UT"
    assert "event" in {c["k"] for c in d["deck"]}


async def test_forum_period_scoping():
    # Only New Members respects the period; ARR / funnel / renewals are current-state.
    await _seed_forum()
    from app.db import SessionLocal
    from app.models import Business, MetricRecord
    from sqlalchemy import select
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        s.add(MetricRecord(tenant_id=biz.tenant_id, business_id=biz.id, source="ghl",
                           kind="onboarded", external_id="on-early", name="Early Joiner",
                           occurred_on=dt.date(dt.date.today().year, 2, 1), amount=3000, segment="forum"))
        await s.commit()
    mtd, ytd = await _get_forum("mtd"), await _get_forum("ytd")
    nm = lambda d: int({k["label"]: k["value"] for k in d["kpis"]}["New Members"])
    assert nm(ytd) > nm(mtd)                                  # ytd includes the earlier joiner
    kv = lambda d, lbl: {k["label"]: k["value"] for k in d["kpis"]}[lbl]
    assert kv(mtd, "Forum ARR") == kv(ytd, "Forum ARR")      # current-state, period-independent
    assert mtd["funnel"] == ytd["funnel"]
    assert mtd["renewals"]["summary"]["count"] == ytd["renewals"]["summary"]["count"]


async def test_reg_count_appends():
    # reg_count appends one row per event per day (external_id = "tag:date");
    # a same-day re-sync upserts rather than duplicating.
    await _seed_forum()
    from app.db import SessionLocal
    from app.models import Business, MetricRecord
    from sqlalchemy import select, func
    tag = "the forum q3 2026"
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()

        async def upsert(day, amount):
            ext = f"{tag}:{day.isoformat()}"
            row = (await s.execute(select(MetricRecord).where(
                MetricRecord.tenant_id == biz.tenant_id, MetricRecord.source == "ghl",
                MetricRecord.kind == "reg_count", MetricRecord.external_id == ext))).scalar_one_or_none()
            if row:
                row.amount = amount
            else:
                s.add(MetricRecord(tenant_id=biz.tenant_id, business_id=biz.id, source="ghl",
                                   kind="reg_count", external_id=ext, amount=amount, occurred_on=day))
            await s.commit()

        d1, d2 = dt.date(2026, 6, 1), dt.date(2026, 6, 2)
        await upsert(d1, 20)
        await upsert(d2, 24)
        await upsert(d1, 22)                                  # same-day re-sync → upsert
        n = (await s.execute(select(func.count()).select_from(MetricRecord).where(
            MetricRecord.business_id == biz.id, MetricRecord.kind == "reg_count"))).scalar()
        assert n == 2                                         # two days, not three rows
        row1 = (await s.execute(select(MetricRecord).where(
            MetricRecord.kind == "reg_count",
            MetricRecord.external_id == f"{tag}:{d1.isoformat()}"))).scalar_one()
        assert float(row1.amount) == 22                       # upserted, not duplicated


async def test_forum_fallbacks():
    # No recruiting records and no event date → those sections degrade to null,
    # and the deck drops their cards — the payload never errors.
    await _seed_forum(with_recruiting=False, with_event=False)
    d = await _get_forum()
    assert d["funnel"] is None
    assert d["event"] is None
    assert {c["k"] for c in d["deck"]} == {"renewals"}   # Revenue Quality retired
