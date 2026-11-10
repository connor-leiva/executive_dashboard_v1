"""The cohort report (spec phase 3), on stored rows only -- no network.

Ten February leads built so each lands in a known speed bucket and funnel state, one January lead
whose UTC timestamp is already February, a Trash lead, a Realty.com lead that must not appear, and
QuickBooks lines in February and March. The acceptance list in §10 phase 3, one assertion each.
"""
import datetime as dt
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import SessionLocal
from app.main import app
from app.models import (BookTxn, BookTxnLine, Business, CrmActivity, Integration, IntranetWorkspace, Lead,
                        LeadSourceCostShare, LeadSourceGroup, LeadSourceSpendRule, Tenant, Transaction, User)
from app.security import hash_pw, make_token
from app.services.lead_source_report import cohort_report

NOW = dt.datetime(2026, 11, 10, 18, 0, tzinfo=dt.timezone.utc)
BASE = dt.datetime(2026, 2, 3, 17, 0, tzinfo=dt.timezone.utc)
TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


@pytest.fixture(scope="module")
async def ws():
    async with SessionLocal() as s:
        t = Tenant(slug=f"coh-{uuid.uuid4().hex[:6]}", name="Cohort Realty", plan="portfolio")
        s.add(t)
        await s.flush()
        b = Business(tenant_id=t.id, key="brokerage", name="Cohort Realty", tag="Real estate", kind="real_estate")
        jv = Business(tenant_id=t.id, key="lender", name="Partner Lending", tag="JV", kind="commission_jv", sort_order=1)
        s.add_all([b, jv])
        await s.flush()
        s.add(IntranetWorkspace(tenant_id=t.id, portal_name="c", subdomain=t.slug, timezone="America/Denver"))
        g = LeadSourceGroup(tenant_id=t.id, business_id=b.id, key="realtor_com", display_name="Realtor.com",
                            variants=["Realtor.com", "Market VIP via Opcity BLA"], match_mode="contains",
                            program_start=dt.date(2026, 1, 1), is_active=True, display_order=0,
                            exp_risk_team_default=Decimal("69"), exp_risk_agent_default=Decimal("49"))
        bare = LeadSourceGroup(tenant_id=t.id, business_id=b.id, key="opcity_direct", display_name="Opcity direct",
                               variants=["Opcity Direct"], match_mode="exact", is_active=True, display_order=5)
        realty = LeadSourceGroup(tenant_id=t.id, business_id=b.id, key="realty_com", display_name="Realty.com",
                                 variants=[], is_active=False, display_order=10)
        s.add_all([g, bare, realty])
        await s.flush()
        s.add(LeadSourceSpendRule(tenant_id=t.id, lead_source_group_id=g.id, business_id=b.id, account_qbo_id="365"))
        s.add(LeadSourceCostShare(tenant_id=t.id, lead_source_group_id=g.id, bearer_business_id=jv.id,
                                  share_pct=Decimal("50")))
        s.add(Integration(tenant_id=t.id, provider="qbo", business_id=b.id, status="connected",
                          realm_id="r1", last_synced_at=NOW))

        # (minutes to first touch, channel, stage, appointment outcome, deal)
        plan = [(3, "call", "Met With Client", "held", None), (20, "text", "Lead", None, None),
                (60, "email", "Lead", None, None), (300, "call", "Lead", None, None),
                (1800, "call", "Lead", None, None), (6000, "call", "Lead", None, None),
                (None, None, "Trash", None, None), (None, None, "Lead", None, None),
                (2, "call", "Closed", None, "closed"), (10, "call", "Pending", None, "uc")]
        for i, (mins, channel, stage, outcome, deal) in enumerate(plan, start=1):
            created = BASE + dt.timedelta(hours=i)
            lead = Lead(tenant_id=t.id, business_id=b.id, external_id=str(i), stage=stage, origin="Realtor.com MVIP",
                        created_at_src=dt.date(2026, 2, 3), src_created_at=created, email=f"p{i}@x.com", name=f"Person {i}")
            s.add(lead)
            await s.flush()
            if mins is not None:
                kind = {"call": "call", "text": "text", "email": "email"}[channel]
                s.add(CrmActivity(tenant_id=t.id, business_id=b.id, kind=kind, external_id=f"a{i}",
                                  person_external_id=str(i), occurred_at=created + dt.timedelta(minutes=mins),
                                  direction="outbound", channel=channel, is_live=channel == "call",
                                  duration_sec=Decimal(60) if channel == "call" else None))
            if outcome:
                s.add(CrmActivity(tenant_id=t.id, business_id=b.id, kind="appointment", external_id=f"ap{i}",
                                  person_external_id=str(i), occurred_at=created + dt.timedelta(days=2),
                                  direction="unknown", channel="appt", outcome=outcome))
            if deal == "closed":
                s.add(Transaction(tenant_id=t.id, business_id=b.id, external_id="tx-closed", status="closed",
                                  gci=Decimal(10000), trans_fee=Decimal(295), exp_risk_team=Decimal(69),
                                  exp_risk_agent=Decimal(49), agent_payment=Decimal(7000), exp_risk_source="api",
                                  company_dollar=Decimal("3177.00"), closed_volume=Decimal(400000),
                                  close_date=dt.date(2026, 5, 1), contract_date=dt.date(2026, 4, 1),
                                  lead_id=lead.id, lost_signal=False))
            if deal == "uc":
                s.add(Transaction(tenant_id=t.id, business_id=b.id, external_id="tx-uc", status="pending",
                                  gci=Decimal(9000), contract_date=dt.date(2026, 10, 20),
                                  expected_close_date=dt.date(2026, 12, 1), lead_id=lead.id, lost_signal=False))
        # 11pm Mountain on 31 January is 06:00 UTC on 1 February: a January lead here.
        s.add(Lead(tenant_id=t.id, business_id=b.id, external_id="jan", origin="Realtor.com", stage="Lead",
                   created_at_src=dt.date(2026, 1, 31),
                   src_created_at=dt.datetime(2026, 2, 1, 6, 0, tzinfo=dt.timezone.utc)))
        s.add(Lead(tenant_id=t.id, business_id=b.id, external_id="apr", origin="Realtor.com", stage="Lead",
                   created_at_src=dt.date(2026, 4, 14),
                   src_created_at=dt.datetime(2026, 4, 14, 18, 0, tzinfo=dt.timezone.utc)))
        s.add(Lead(tenant_id=t.id, business_id=b.id, external_id="realty", origin="Realty.com", stage="Lead",
                   created_at_src=dt.date(2026, 2, 5), src_created_at=BASE))

        async def line(qid, day, amount, posting=None, credit=False):
            tx = BookTxn(tenant_id=t.id, business_id=b.id, realm_id="r1", qbo_type="Purchase", qbo_id=qid,
                         txn_date=day, amount=Decimal(amount), purchase_credit=credit)
            s.add(tx)
            await s.flush()
            s.add(BookTxnLine(tenant_id=t.id, book_txn_id=tx.id, line_num=0, amount=Decimal(amount),
                              account_qbo_id="365", posting_type=posting))
        await line("feb", dt.date(2026, 2, 27), 4000)
        await line("mar", dt.date(2026, 3, 2), 1000)
        await line("mar-credit", dt.date(2026, 3, 9), 200, posting="credit")
        u = User(tenant_id=t.id, email=f"o@{t.slug}.test", name="Owner", role="owner", status="active",
                 password_hash=hash_pw("x" * 12))
        m = User(tenant_id=t.id, email=f"m@{t.slug}.test", name="Member", role="member", status="active",
                 password_hash=hash_pw("x" * 12), tab_access=["portfolio"])
        s.add_all([u, m])
        await s.commit()
        return {"tenant_id": t.id, "business": b, "group": g, "bare": bare,
                "owner": make_token(u.id, t.id, 0), "member": make_token(m.id, t.id, 0)}


async def _report(ws, group=None, **kw):
    async with SessionLocal() as s:
        b = await s.get(Business, ws["business"].id)
        g = await s.get(LeadSourceGroup, (group or ws["group"]).id)
        return await cohort_report(s, ws["tenant_id"], b, g, now=NOW, **kw)


async def test_the_february_row_matches_the_fixture(ws):
    r = await _report(ws)
    feb = next(x for x in r["cohort_rows"] if x["cohort_key"] == "2026-02")
    assert feb["leads_received"] == 10                     # Trash stays in the denominator
    assert feb["contacted_count"] == 8 and feb["contacted_pct"] == 80.0
    assert feb["pct_contacted_5min"] == 20.0 and feb["pct_contacted_24hr"] == 60.0
    assert feb["avg_time_to_contact_minutes"] == round((3 + 20 + 60 + 300 + 1800 + 6000 + 2 + 10) / 8, 1)
    assert (feb["appts_set_count"], feb["appts_held_count"], feb["appts_held_pct"]) == (3, 3, 100.0)
    assert (feb["closed_count"], feb["under_contract_count"], feb["dead_count"], feb["active_count"]) == (1, 1, 1, 7)
    speed = next(x for x in r["speed_table"] if x["cohort_key"] == "2026-02")["buckets"]
    assert speed == {"u5min": 2, "u30min": 2, "u2hr": 1, "u24hr": 1, "u72hr": 1, "o72hr": 1, "never": 2}
    unc = next(x for x in r["uncontacted_table"] if x["cohort_key"] == "2026-02")
    assert (unc["uncontacted_total"], unc["u72_count"]) == (2, 2)
    assert feb["sisu_closed_count"] == 1 and feb["sisu_under_contract_count"] == 1
    assert feb["sisu_company_dollar_dollars"] == 3177.00 and feb["sisu_matched_count"] == 2
    assert feb["industry_comparable"] is False and feb["show_conversion"] is True


async def test_the_cohort_month_is_the_workspace_date_and_other_brands_stay_out(ws):
    r = await _report(ws)
    jan = next(x for x in r["cohort_rows"] if x["cohort_key"] == "2026-01")
    assert jan["leads_received"] == 1                       # 1 Feb UTC, 31 Jan in Denver
    assert "Realty.com" not in r["source_variants_matched"]
    assert sum(x["leads_received"] for x in r["cohort_rows"]) == 12


async def test_spend_share_and_roi_follow_quickbooks(ws):
    r = await _report(ws)
    rows = {x["cohort_key"]: x for x in r["cohort_rows"]}
    feb, mar = rows["2026-02"], rows["2026-03"]
    assert (feb["gross_spend"], feb["net_spend"], feb["share_pct"]) == (4000.0, 2000.0, 50.0)
    assert feb["roi_gross"] == round(3177 / 4000, 2) and feb["roi_net"] == round(3177 / 2000, 2)
    assert feb["net_profit_gross"] == 3177 - 4000 and feb["net_profit_net"] == 3177 - 2000
    assert mar["gross_spend"] == 800.0                      # March's $1,000 less a $200 credit; not in February
    assert mar["leads_received"] == 0 and mar["spend_only"] is True
    assert r["header"]["gross_spend"] == 4800.0 and r["header"]["net_spend"] == 2400.0
    assert r["spend"]["bearer_name"] == "Partner Lending"
    apr = rows["2026-04"]
    assert apr["gross_spend"] == 0.0 and apr["roi_gross"] is None   # synced, no lines: $0, no ROI
    jan = rows["2026-01"]
    # Before the first stored QuickBooks line: unknown, not free -- and left out of the header ROI.
    assert jan["spend_known"] is False and jan["gross_spend"] is None
    assert r["header"]["spend_months_known"] == 3 and r["header"]["spend_months"] == 4


async def test_a_month_filter_recomputes_the_header(ws):
    r = await _report(ws, start="2026-02", end="2026-02")
    assert [x["cohort_key"] for x in r["cohort_rows"]] == ["2026-02"]
    assert r["header"]["gross_spend"] == 4000.0 and r["header"]["leads"] == 10
    assert r["header"]["company_dollar"] == 3177.0


async def test_no_spend_rule_is_null_never_zero(ws):
    r = await _report(ws, group=ws["bare"])
    assert r["spend"]["mapped"] is False and r["cohort_rows"] == []
    assert r["header"]["gross_spend"] is None and r["header"]["roi_gross"] is None
    assert r["header"]["roi_net"] is None


async def test_the_api_gates_and_404s(ws):
    from app.config import settings
    async with SessionLocal() as s:
        slug = (await s.get(Tenant, ws["tenant_id"])).slug
    host = {"X-Tenant-Host": f"{slug}.{settings.PLATFORM_DOMAIN}"}
    H = {"Authorization": f"Bearer {ws['owner']}", **host}
    M = {"Authorization": f"Bearer {ws['member']}", **host}
    url = "/api/v1/businesses/brokerage/lead-sources/{}/cohorts"
    async with AsyncClient(transport=TRANSPORT, base_url="http://testserver") as c:
        r = await c.get(url.format("realtor_com"), headers=H)
        assert r.status_code == 200 and r.json()["header"]["leads"] == 12
        assert (await c.get(url.format("realty_com"), headers=H)).status_code == 404   # inactive
        assert (await c.get(url.format("nope"), headers=H)).status_code == 404
        assert (await c.get(url.format("realtor_com"), headers=M)).status_code == 403   # no brokerage tab
        assert (await c.get(url.format("realtor_com") + "?start=2026-05&end=2026-02", headers=H)).status_code == 400
