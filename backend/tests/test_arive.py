"""ARIVE integration — client normalization, sync_arive mapping, Sympli KPIs +
the ULRG→Sympli flywheel. Mocks the HighLevel-style network; no live calls."""
import json

import pytest
from sqlalchemy import select, func

from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant, Business, Integration, MetricRecord
from app.security import enc
from app.integrations import arive
from app.services.sync import sync_arive
from app.services.metrics import build_dashboard


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


# ── pure client helpers (no network) ─────────────────────────────────
def test_status_and_funded_classification():
    ln = {"currentLoanStatus": {"status": "broker_check_received", "date": "2026-07-01"}}
    assert arive.loan_status(ln) == "BROKER_CHECK_RECEIVED"
    assert arive.is_funded("BROKER_CHECK_RECEIVED") is True    # past funding = still funded
    assert arive.is_funded("LOAN_FUNDED") is True
    assert arive.is_funded("PREAPPROVED") is False
    assert arive.is_dead("ADVERSE") is True
    assert arive.is_dead("LOAN_FUNDED") is False


def test_amount_and_borrower_normalization():
    ln = {"baseLoanAmount": "412500.50",
          "loanBorrowers": [{"firstName": "Jane", "lastName": "Doe",
                             "emailAddressText": "Jane.Doe@Example.COM",
                             "mobilePhone10digit": "(801) 555-2020"}]}
    assert arive.loan_amount(ln) == 412500.50
    b = arive.loan_borrower(ln)
    assert b["name"] == "Jane Doe"
    assert b["email"] == "jane.doe@example.com"      # lowercased
    assert b["phone"] == "8015552020"                # digits only, last 10


def test_pick_alias_fallthrough():
    assert arive.pick({"a": None, "b": "", "c": "x"}, "a", "b", "c") == "x"
    assert arive.pick({}, "a", default="z") == "z"


# ── dashboard: Sympli KPIs + flywheel from the seeded Arive loans ─────
async def test_sympli_kpis_and_flywheel():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        d = await build_dashboard(s, t.id, "mtd")

    ops = {o.label: o.value for o in d.areas["sympli"].ops}
    assert ops["Funded Loans"] == "14"
    assert ops["Pre-approvals"] == "41"
    assert ops["Pull-through Rate"] == "74%"          # 14 funded / (14 funded + 5 dead)

    # Three-signal flywheel: 16 financeable (3 cash excluded from 19 buy-side),
    # 7 captured (4 Sympli-vid + 3 email-matched) → 44%, below the 60% target.
    fw = d.flywheel
    assert fw.available is True
    assert fw.buyer_closings == 16 and fw.captured == 7 and fw.lost == 9 and fw.capture_pct == 44
    assert fw.capture_target == 60 and fw.per_loan_share == 2100
    assert fw.gap_dollars == 9 * 2100 and fw.gap_at_target < fw.gap_dollars   # shrinks at target
    assert sum(a.refs for a in fw.referrers) == fw.captured                   # reconciliation invariant
    assert {l.name for l in fw.lost_to} >= {"UMortgage-Adam", "Intercap Lending"}
    assert fw.sympli_referred == 10 and fw.sympli_referred_linked == 6   # Arive-side (Utah Life)
    assert fw.vendor_no_loan == 1 and fw.referral_no_deal == 4            # data-quality gaps
    sc = {c.label: c.value for c in d.scorecards}
    assert sc["Attach Rate"] == "44%"
    assert sc["Loans Funded"] == "14"


async def test_sympli_calculated_financials():
    """The Sympli three-lens: Live = Arive commission → the 55% LO split (cost of
    sale) → net commission (true margin) → 29% operating costs → NOI → Spring's 50%
    JV share. Booked (QBO) mirrors the same shape and reconciles within ~1%."""
    from app.services.financials import compute_financials
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        sym = (await s.execute(select(Business).where(
            Business.tenant_id == t.id, Business.key == "sympli"))).scalar_one()
        f = await compute_financials(s, t.id, sym, "mtd")

    live = f["lenses"]["live"]
    rows = {r["l"]: r["v"] for r in live["rows"]}
    assert live["tag"] == "Arive · funded loans" and live["units"] == 14
    rev = rows["Commission revenue"]                              # net Arive commission
    assert rev > 0
    # LO comp is the configurable cost of sale (55%); net commission is the true margin.
    assert rows["Loan officer comp"] == -round(rev * 0.55, 2)
    assert rows["Net commission"] == round(rev - round(rev * 0.55, 2), 2)
    assert rows["Operating costs"] == -round(rev * 0.29, 2)
    # NOI is the hero, and Spring's JV share (50%) falls out the bottom (~8% of rev).
    assert rows["Net operating income"] == live["profit"]
    assert rows["Net operating income"] == round(rows["Net commission"] - round(rev * 0.29, 2), 2)
    share = rows["Spring's JV share (50%)"]
    assert share == round(live["profit"] * 0.5, 2)
    assert abs(share - rev * 0.08) < rev * 0.005                  # ≈ 8% of commission

    # Booked (QBO) lens mirrors the structure row-for-row — true side-by-side.
    booked = f["lenses"]["booked"]
    brows = {r["l"]: r["v"] for r in booked["rows"]}
    assert brows["Commission revenue"] == 152000 and brows["Loan officer comp"] == -83600
    assert brows["Net commission"] == 68400 and brows["Operating costs"] == -44080
    assert booked["profit"] == 24320 and brows["Spring's JV share (50%)"] == 12160

    r = f["reconciliation"]
    assert r["source"] == "Arive" and r["metric"] == "in commissions"
    assert r["sisu_closed"] == rev and r["qbo_booked"] == 152000
    assert r["gap_gci"] == round(rev - 152000, 2)
    assert r["gap_profit"] == round(live["profit"] - 24320, 2)
    assert abs(r["gap_gci"]) < r["qbo_booked"] * 0.03            # Live vs Booked within ~1-3%


async def test_loan_officers():
    """Per-LO rollup on the Sympli area: funded / volume / gross commission, ranked,
    reconciling to the commission total. Seed mirrors prod's concentration."""
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        d = await build_dashboard(s, t.id, "mtd")
    los = d.areas["sympli"].loan_officers
    assert len(los) == 3
    top = los[0]
    assert top.name == "Jared Browning" and top.funded == 10      # writes most of the volume
    assert los == sorted(los, key=lambda x: x.revenue, reverse=True)
    assert round(sum(l.revenue for l in los)) == 156395           # gross production credit (before cures)
    assert all(0 <= l.pull_through <= 100 for l in los)
    assert top.avg_loan == round(top.volume / top.funded, 2)


async def test_utah_state_filter():
    """The Arive LOS is multi-state; the dashboard shows Utah only. The 3 seeded TX
    loans exist as records but never reach the Sympli card. (Runs before the sync
    test, which snapshots the arive loans away.)"""
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "sympli"))).scalar_one()
        funded = (await s.execute(select(MetricRecord).where(
            MetricRecord.business_id == biz.id, MetricRecord.source == "arive",
            MetricRecord.kind == "loan", MetricRecord.segment == "funded"))).scalars().all()
        tx = sum(1 for f in funded if (f.meta or {}).get("property_state") == "TX")
        d = await build_dashboard(s, biz.tenant_id, "mtd")
    assert len(funded) == 17 and tx == 3           # stored: 14 UT + 3 TX
    carded = int([o.value for o in d.areas["sympli"].ops if o.label == "Funded Loans"][0])
    assert carded == 14                            # only Utah reaches the card


# ── sync_arive maps live-shaped loans → metric records (mutates; keep last) ──
_LOANS = [
    {"ariveLoanId": "L1", "baseLoanAmount": 400000, "loanPurpose": "Purchase",
     "loanOriginatorEmail": "lo@symp.com", "subjectProperty": {"state": "UT"},
     "currentLoanStatus": {"status": "LOAN_FUNDED", "date": "2026-07-03"},
     "loanBorrowers": [{"emailAddressText": "b1@x.com", "firstName": "B", "lastName": "One"}]},
    {"ariveLoanId": "L2", "baseLoanAmount": 500000,
     "currentLoanStatus": {"status": "BROKER_CHECK_RECEIVED", "date": "2026-07-04"},
     "loanBorrowers": [{"emailAddressText": "b2@x.com"}]},
    {"ariveLoanId": "L3", "baseLoanAmount": 300000,
     "currentLoanStatus": {"status": "PREAPPROVED", "date": "2026-07-05"}},
    {"ariveLoanId": "L4", "baseLoanAmount": 350000,
     "currentLoanStatus": {"status": "UNDERWRITING_SUBMITTED", "date": "2026-07-05"}},
    {"ariveLoanId": "L5", "baseLoanAmount": 320000,
     "currentLoanStatus": {"status": "ADVERSE", "date": "2026-07-02"}},
]


async def test_sync_arive_lands_loans(monkeypatch):
    async def fake_get_loans(cid, secret, api_key, max_loans=2000):
        assert (cid, secret, api_key) == ("cid", "sec", "key")   # creds decoded + passed
        return _LOANS

    async def fake_detail(ids, cid, secret, api_key, concurrency=8):
        # L1 is referred by Utah Life; L2 has no referral.
        return {"L1": {"ariveLoanId": "L1", "referralContactSourceEmail": "agent@liveutah.com",
                       "referralContactSourceName": "An Agent",
                       "businessContacts": [{"role": "REAL_ESTATE_AGENT", "subType": "BUYERS_AGENT",
                                             "emailAddressText": "agent@liveutah.com"}]}}
    monkeypatch.setattr(arive, "get_loans", fake_get_loans)
    monkeypatch.setattr(arive, "get_loans_detail", fake_detail)

    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "sympli"))).scalar_one()
        integ = (await s.execute(select(Integration).where(
            Integration.business_id == biz.id, Integration.provider == "arive"))).scalar_one()
        integ.access_token_enc = enc(json.dumps({"client_id": "cid", "secret": "sec", "api_key": "key"}))
        await s.commit()

        n = await sync_arive(s, biz.tenant_id, integ)
        assert n == 5

        async def count(seg):
            return int((await s.execute(select(func.count()).select_from(MetricRecord).where(
                MetricRecord.business_id == biz.id, MetricRecord.source == "arive",
                MetricRecord.kind == "loan", MetricRecord.segment == seg))).scalar() or 0)

        assert await count("funded") == 2         # LOAN_FUNDED + BROKER_CHECK_RECEIVED
        assert await count("pipeline") == 2       # PREAPPROVED + UNDERWRITING_SUBMITTED
        assert await count("dead") == 1           # ADVERSE

        rec = (await s.execute(select(MetricRecord).where(
            MetricRecord.business_id == biz.id, MetricRecord.source == "arive",
            MetricRecord.external_id == "L1"))).scalar_one()
        assert rec.email == "b1@x.com" and float(rec.amount) == 400000.0
        assert rec.status == "LOAN_FUNDED" and (rec.meta or {}).get("property_state") == "UT"
        # referral enrichment: L1 carries the Utah Life referral from the detail fetch.
        assert (rec.meta or {}).get("referral_email") == "agent@liveutah.com"


def test_resolve_vendor_config():
    from app.integrations import sisu
    vendors = [
        {"vendor_id": 155743, "name": "Sympli Mortgage of Utah", "vendor_type": "M"},
        {"vendor_id": 249290, "name": "Sympli Mortgage", "vendor_type": "M"},
        {"vendor_id": 156576, "name": "Sympli Insurance", "vendor_type": "I"},   # NOT mortgage
        {"vendor_id": 125147, "name": "UMortgage-Adam", "vendor_type": "M"},
        {"vendor_id": 12, "name": "Cash-No Lender", "vendor_type": "M"},
        {"vendor_id": 43830, "name": "Meraki Title", "vendor_type": "T"},        # title, ignored
    ]
    cfg = sisu.resolve_vendor_config(vendors)
    assert set(cfg["sympli_mortgage_vids"]) == {155743, 249290}   # insurance excluded
    assert cfg["cash_vids"] == [12]
    assert cfg["lender_names"]["125147"] == "UMortgage-Adam"
    assert "43830" not in cfg["lender_names"]                     # non-mortgage not a lender
