"""Phase 2 of the lead-source port: the facts the reports read, synced -- against fakes only.

Follow Up Boss activity through the real full sync (tests/fub_fake.py), and the Sisu join and
commission lines through `lead_source_sync.after_sisu` against an httpx mock of Sisu's contact
routes. The acceptance list in docs/specs/ulrg-reporting-dashboard-port.md §10 phase 2.
"""
import datetime as dt
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.integrations import sisu
from app.models import (Business, CrmActivity, Integration, IntranetWorkspace, Lead, LeadSourceGroup,
                        Tenant, Transaction)
from app.security import enc
from app.services import fub_sync
from app.services.lead_source_sync import after_sisu
from app.services.sync import _fub_creds, _sync_integration
from tests.fub_fake import appointment, call, em_event, fake, person, text, user  # noqa: F401


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


async def _workspace(slug: str) -> dict:
    """A brokerage with one Realtor.com group from 1 February 2026, and FUB connected."""
    async with SessionLocal() as s:
        t = Tenant(slug=slug, name=slug.title(), status="active", plan="portfolio")
        s.add(t)
        await s.flush()
        b = Business(tenant_id=t.id, key="brokerage", name="Brokerage", tag="Real estate",
                     kind="real_estate")
        s.add(b)
        await s.flush()
        s.add(IntranetWorkspace(tenant_id=t.id, portal_name=slug, subdomain=slug, timezone="America/Denver"))
        s.add(LeadSourceGroup(tenant_id=t.id, business_id=b.id, key="realtor_com", display_name="Realtor.com",
                              variants=["Realtor.com", "Market VIP via Opcity BLA"], match_mode="contains",
                              program_start=dt.date(2026, 2, 1), is_active=True, display_order=0,
                              exp_risk_team_default=Decimal(69), exp_risk_agent_default=Decimal(49)))
        integ = Integration(tenant_id=t.id, provider="fub", business_id=b.id, status="connected",
                            access_token_enc=enc("fka_test"))
        s.add(integ)
        await s.commit()
        return {"tenant_id": t.id, "business_id": b.id, "integration_id": integ.id}


# ── Follow Up Boss activity ──────────────────────────────────────────────────────────────────

async def test_activity_is_read_for_in_scope_people_only_and_classified(fake):
    fake.users = [user(1)]
    fake.people = [
        person(1, source="Realtor.com MVIP", created="2026-02-10T17:00:00Z"),
        person(2, source="Zillow", created="2026-02-10T17:00:00Z"),
        person(3, source="Realtor.com", created="2026-01-05T17:00:00Z"),          # before program start
        person(4, source="Market VIP via Opcity BLA", created="2026-03-01T17:00:00Z"),
    ]
    fake.activity["calls"] = [
        call(101, person_id=1, created="2026-02-10T17:03:00Z", duration=0, note="Spoke to Bryn, 2-3 bed"),
        call(102, person_id=1, created="2026-02-10T17:01:00Z", duration=0, note=""),
        call(103, person_id=1, created="2026-02-11T17:00:00Z", duration=95),
        call(104, person_id=2, created="2026-02-10T18:00:00Z", duration=30),        # out of scope
    ]
    fake.activity["textMessages"] = [text(101, person_id=1, created="2026-02-10T17:02:00Z")]
    fake.activity["emEvents"] = [em_event(201, person_id=1, occurred="2026-02-10T18:00:00Z"),
                                 em_event(202, person_id=1, occurred="2026-02-10T19:00:00Z", kind="opened")]
    fake.activity["appointments"] = [appointment(301, person_id=4, start="2026-03-05T18:00:00Z", outcome="Met")]
    ws = await _workspace(f"act-{uuid.uuid4().hex[:6]}")
    async with SessionLocal() as s:
        await _sync_integration(s, ws["tenant_id"], await s.get(Integration, ws["integration_id"]),
                                "2026-02-01", "2026-02-28")
    asked = {p["personId"] for p in fake.calls_to("/calls")}
    assert asked == {"1", "4"}                        # never Zillow, never a pre-program lead
    async with SessionLocal() as s:
        rows = {(r.kind, r.external_id): r for r in (await s.execute(select(CrmActivity).where(
            CrmActivity.tenant_id == ws["tenant_id"]))).scalars()}
    assert rows[("call", "101")].is_live and rows[("call", "101")].has_note    # duration 0 + note = live
    assert not rows[("call", "102")].is_live                                    # duration 0, no note
    assert rows[("call", "103")].is_live and float(rows[("call", "103")].duration_sec) == 95
    assert rows[("text", "101")].direction == "outbound" and not rows[("text", "101")].is_live
    assert rows[("call", "101")].kind != rows[("text", "101")].kind             # same FUB id, two events
    assert ("email", "201") in rows and ("email", "202") not in rows            # an open is not a touch
    assert rows[("appointment", "301")].is_live and rows[("appointment", "301")].outcome == "met"
    assert ("call", "104") not in rows


async def test_the_quick_tick_never_reads_activity_and_a_quiet_person_is_not_re_read(fake):
    fake.users = [user(1)]
    fake.people = [person(1, source="Realtor.com", created="2026-02-10T17:00:00Z")]
    fake.activity["calls"] = [call(501, person_id=1, created="2026-02-10T17:03:00Z", duration=12)]
    ws = await _workspace(f"quick-{uuid.uuid4().hex[:6]}")
    async with SessionLocal() as s:
        integ = await s.get(Integration, ws["integration_id"])
        await fub_sync.full_sync(s, ws["tenant_id"], integ, _fub_creds(integ))
        n = len(fake.calls_to("/calls"))
        assert n == 1
        await fub_sync.quick_refresh(s, ws["tenant_id"], integ, _fub_creds(integ))
        assert len(fake.calls_to("/calls")) == n
        await fub_sync.full_sync(s, ws["tenant_id"], integ, _fub_creds(integ))
        assert len(fake.calls_to("/calls")) == n          # nothing new on FUB's side: not asked again
        lead = (await s.execute(select(Lead).where(Lead.tenant_id == ws["tenant_id"]))).scalar_one()
        assert lead.activity_synced_at is not None


# ── Sisu: company dollar ─────────────────────────────────────────────────────────────────────

CI = {"adjustments": {"a": [{"category": "EXP Risk Management & Transaction Review Fees", "amount": 69},
                            {"category": "EXP Risk Management & Transaction Review Fees", "amount": 49}]},
      "summaries": {"final": {"1": {"external_type": 1, "value": 2525},
                              "2": {"external_type": 2, "value": 7000, "name": "Agent A"}}},
      "rebates": {"f": {"team": 0}}, "adjusted_gci": 9882, "team_income": 2525}


def test_company_dollar_is_the_spec_formula_to_the_cent():
    lines = sisu.commission_lines(gci=10000, summary_trans_fee=None, ci=CI, detail={"trans_fee_amt": 295},
                                  exp_team_default=69, exp_agent_default=49)
    assert lines["company_dollar"] == 10000 + 295 - 69 - 49 - 7000 == 3177
    assert lines["exp_risk_source"] == "api" and lines["agent_payment"] == 7000
    assert lines["team_income"] == 2525                    # kept, labelled, never the headline


def test_no_exp_lines_takes_the_groups_defaults_and_says_so():
    ci = {k: v for k, v in CI.items() if k != "adjustments"}
    lines = sisu.commission_lines(gci=10000, summary_trans_fee=295, ci=ci, detail={},
                                  exp_team_default=69, exp_agent_default=49)
    assert (lines["exp_risk_team"], lines["exp_risk_agent"], lines["exp_risk_source"]) == (69, 49, "default")
    assert lines["company_dollar"] == 3177 and lines["trans_fee"] == 295


def test_map_client_keeps_the_report_fields():
    t = sisu.map_client({"client_id": 9, "closed_volume_amt": 400000, "trans_amt": 410000,
                         "status_code": "LOSTT", "external_ids": [{"external_service_id": "fub",
                                                                   "external_contact_id": "777_123"}]})
    assert t["closed_volume"] == 400000 and t["sale_price"] == 410000
    assert t["lost_signal"] is False                       # LOSTT alone is not the report's "lost"
    assert t["fub_person_id"] == "123"
    assert sisu.map_client({"client_id": 9, "archive_ts": "Wed, 1 Apr 2026 00:00:00 GMT"})["lost_signal"]


# ── Sisu: the join ───────────────────────────────────────────────────────────────────────────

class FakeSisu:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.by_id: dict[str, dict] = {}         # FUB person id -> contact
        self.by_email: dict[str, dict] = {}      # email -> contact (fallback)
        self.search: dict[str, list] = {}        # email -> contacts
        self.txs: dict[str, list] = {}           # contact id -> transactions
        self.ci: dict[str, dict] = {}
        self.detail: dict[str, dict] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, q = request.url.path.removeprefix("/api"), dict(request.url.params)
        if path == "/v1/contact/external":
            hit = self.by_id.get(q["external_contact_id"])
            if hit is None and "fallback_email" in q:
                hit = self.by_email.get(q["fallback_email"])
            return httpx.Response(200 if hit else 404, json={"contact": hit} if hit else {})
        if path == "/v1/contact":
            return httpx.Response(200, json={"contacts": self.search.get(q.get("email"), [])})
        if path.startswith("/v1/contact/") and path.endswith("/transactions"):
            return httpx.Response(200, json={"transactions": self.txs.get(path.split("/")[3], [])})
        if path.startswith("/v1/client/commission-info/"):
            return httpx.Response(200, json={"commission_info": self.ci.get(path.rsplit("/", 1)[1], {})})
        if path.startswith("/v1/client/edit-client/"):
            return httpx.Response(200, json={"client": self.detail.get(path.rsplit("/", 1)[1], {})})
        return httpx.Response(404, json={})

    def external_calls(self):
        return [dict(r.url.params) for r in self.requests if r.url.path.endswith("/v1/contact/external")]


@pytest.fixture
def fake_sisu(monkeypatch):
    server = FakeSisu()
    monkeypatch.setattr(sisu, "TRANSPORT", httpx.MockTransport(server))

    async def _no_wait(_s):
        return None
    monkeypatch.setattr(sisu, "_sleep", _no_wait)
    return server


async def test_the_join_writes_back_only_on_the_email_fallback_and_merges_duplicates(fake_sisu):
    ws = await _workspace(f"join-{uuid.uuid4().hex[:6]}")
    tid, bid = ws["tenant_id"], ws["business_id"]
    async with SessionLocal() as s:
        for ext, email, created in (("1", "a@x.com", "2026-02-03"), ("2", "b@x.com", "2026-02-04"),
                                    ("3", "c@x.com", "2026-02-05"), ("4", None, "2026-02-06"),
                                    ("5", "z@x.com", "2026-02-07")):
            s.add(Lead(tenant_id=tid, business_id=bid, external_id=ext, email=email,
                       origin="Zillow" if ext == "5" else "Realtor.com",
                       created_at_src=dt.date.fromisoformat(created)))
        for ext, status, email, gci, comm in (("501", "closed", None, 10000, Decimal(7000)),
                                              ("502", "closed", None, 8000, None),
                                              ("503", "pending", "C@x.com", 5000, None),
                                              ("504", "closed", "z@x.com", 9000, None)):
            s.add(Transaction(tenant_id=tid, business_id=bid, source="sisu", external_id=ext, status=status,
                              gci=Decimal(gci), buyer_email=email, agent_commission=comm,
                              close_date=dt.date(2026, 4, 1) if status == "closed" else None))
        s.add(Integration(tenant_id=tid, provider="sisu", business_id=bid, status="connected"))
        await s.commit()

    fake_sisu.by_id["1"] = {"contact_id": 11}                       # hits by id
    fake_sisu.by_email["b@x.com"] = {"contact_id": 12}              # only the fallback finds it
    fake_sisu.search["b@x.com"] = [{"contact_id": 12}, {"contact_id": 13}]
    fake_sisu.txs = {"11": [{"client_id": 501}], "12": [], "13": [{"client_id": 502}]}
    fake_sisu.ci = {"501": CI}
    fake_sisu.detail = {"501": {"trans_fee_amt": 295}}

    async with SessionLocal() as s:
        integ = (await s.execute(select(Integration).where(Integration.tenant_id == tid,
                                                           Integration.provider == "sisu"))).scalar_one()
        stats = await after_sisu(s, tid, integ, sisu.SisuCreds("u", "t", "https://sisu.test/api"))
    assert stats["in_scope"] == 4 and stats["mappings_saved"] == 1

    calls = fake_sisu.external_calls()
    first_for = {}
    for q in calls:
        first_for.setdefault(q["external_contact_id"], []).append(q)
    assert first_for["1"] == [{"external_contact_id": "1", "external_service_id": "fub", "allowed_statuses": "N"}]
    assert first_for["2"][0].get("save_mapping_on_fallback") is None             # id first, no write
    assert first_for["2"][1]["save_mapping_on_fallback"] == "true"               # then the fallback writes
    assert "4" not in first_for                                                  # no email: not looked up
    assert "5" not in first_for                                                  # Zillow: out of scope

    async with SessionLocal() as s:
        leads = {lead.external_id: lead for lead in (await s.execute(select(Lead).where(Lead.tenant_id == tid))).scalars()}
        txns = {t.external_id: t for t in (await s.execute(select(Transaction).where(Transaction.tenant_id == tid))).scalars()}
        integ = (await s.execute(select(Integration).where(Integration.tenant_id == tid,
                                                           Integration.provider == "sisu"))).scalar_one()
    assert txns["501"].lead_id == leads["1"].id                     # contact by FUB id
    assert txns["502"].lead_id == leads["2"].id                     # duplicate contact, merged
    assert txns["503"].lead_id == leads["3"].id                     # buyer email, case-insensitive
    assert txns["504"].lead_id is None                              # Zillow lead is not in scope
    assert txns["501"].company_dollar == Decimal("3177.00") and txns["501"].exp_risk_source == "api"
    assert txns["501"].agent_commission == Decimal(7000)          # the financial cards' number, untouched
    assert txns["502"].exp_risk_source == "default"                 # no commission lines → the group's 69/49
    assert txns["502"].exp_risk_team == Decimal("69.00")
    assert integ.config["lead_source_last_run"]["mapping_writes"] == ["2"]
