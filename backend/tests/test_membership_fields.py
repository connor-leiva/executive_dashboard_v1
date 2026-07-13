"""GHL membership fields (renewal date / enrollment / total cost / payment plan,
populated from ClickUp) → payment mix + PIF renewal projection. Pure unit tests plus
one seed-backed end-to-end forecast check."""
import datetime as dt
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.services.billing import normalize_payment_plan, project_renewals, compute_billing
from app.services.sync import _parse_any_date, _parse_money, _membership_field_ids, _read_membership


# ── payment-plan normalization ──────────────────────────────────────
def test_normalize_payment_plan():
    assert normalize_payment_plan("PIF") == "pif"
    assert normalize_payment_plan("Paid in Full") == "pif"
    assert normalize_payment_plan("Monthly") == "monthly"
    assert normalize_payment_plan("Financed - 12 months") == "financed"
    assert normalize_payment_plan("") is None
    assert normalize_payment_plan("mystery") is None


# ── value parsers ───────────────────────────────────────────────────
def test_parse_any_date():
    assert _parse_any_date("2026-09-15") == dt.date(2026, 9, 15)
    assert _parse_any_date("09/15/2026") == dt.date(2026, 9, 15)
    assert isinstance(_parse_any_date("1757894400000"), dt.date)   # epoch-ms
    assert _parse_any_date("") is None
    assert _parse_any_date("not a date") is None


def test_parse_money():
    assert _parse_money("$27,000") == 27000.0
    assert _parse_money("30000") == 30000.0
    assert _parse_money("") is None
    assert _parse_money("n/a") is None


# ── custom-field id mapping ─────────────────────────────────────────
def test_membership_field_ids_by_name_and_override():
    defs = [
        {"id": "a", "name": "Membership Renewal Date"},
        {"id": "b", "name": "Membership Enrollment Date"},
        {"id": "c", "name": "Total Membership Amount"},
        {"id": "d", "name": "Payment Plan"},
        {"id": "z", "name": "Some Other Field"},
    ]
    assert _membership_field_ids(defs, {}) == {
        "renewal_date": "a", "enrollment_date": "b", "total_cost": "c", "payment_plan": "d"}
    # config override wins (by field name)
    m = _membership_field_ids(defs, {"membership_fields": {"payment_plan": "Some Other Field"}})
    assert m["payment_plan"] == "z"


def test_read_membership():
    field_ids = {"renewal_date": "a", "total_cost": "c", "payment_plan": "d"}
    values = {"a": "2026-09-15", "c": "$27,000", "d": "PIF"}
    assert _read_membership(values, field_ids) == {
        "renewal_date": "2026-09-15", "total_cost": 27000.0, "payment": "pif"}


def test_membership_field_ids_richer_fields():
    defs = [
        {"id": "mt", "name": "Member Type"},
        {"id": "st", "name": "Membership Status"},
        {"id": "bk", "name": "Brokerage Affiliation"},
        {"id": "sa", "name": "Stripe Account"},
    ]
    m = _membership_field_ids(defs, {})
    assert m["member_type"] == "mt" and m["status"] == "st"
    assert m["brokerage"] == "bk" and m["stripe_account"] == "sa"


def test_read_membership_richer_fields():
    field_ids = {"member_type": "mt", "status": "st", "brokerage": "bk", "stripe_account": "sa"}
    values = {"mt": "Add-On Member", "st": "Active",
              "bk": "eXp Realty", "sa": ["Legacy SB Account"]}   # multi-select → first
    out = _read_membership(values, field_ids)
    assert out["member_type"] == "Add-On Member" and out["member_kind"] == "add_on"
    assert out["status"] == "Active" and out["brokerage"] == "eXp Realty"
    assert out["stripe_account"] == "Legacy SB Account"
    # a primary label normalizes to 'primary'
    assert _read_membership({"mt": "Primary Member"}, {"member_type": "mt"})["member_kind"] == "primary"


# ── PIF renewal projection ──────────────────────────────────────────
def _mem(payment, renewal, cost, name="M"):
    return SimpleNamespace(name=name, source_url=None,
                           meta={"membership": {"payment": payment, "renewal_date": renewal, "total_cost": cost}})


def test_project_renewals_pif_only():
    today, until = dt.date(2026, 7, 9), dt.date(2026, 12, 31)
    members = [
        _mem("pif", "2026-09-15", 27000, "Ann"),
        _mem("monthly", "2026-09-15", 27000, "Bob"),   # not PIF → billed via a sub
        _mem("pif", None, 27000, "Cy"),                # no renewal date
        _mem("pif", "2026-10-01", 0, "Dee"),           # no total cost
        _mem("pif", "2025-03-01", 30000, "Eve"),       # rolls to 2027 → outside the window
    ]
    out = project_renewals(members, today, until)
    assert len(out) == 1 and out[0]["who"] == "Ann"
    assert out[0]["amount"] == 27000 and out[0]["date"] == "2026-09-15" and out[0]["note"] == "renewal"


def test_extra_projected_lifts_forecast_not_mrr():
    today = dt.date(2026, 7, 9)
    pay = SimpleNamespace(occurred_on=dt.date(2026, 7, 1), status="succeeded", amount=1000,
                          meta={"stream": "memberships", "amount_refunded": 0})
    extra = [{"date": "2026-08-15", "amount": 27000, "who": "Ann", "note": "renewal", "source_url": None}]
    b = compute_billing([pay], [], 1000, dt.date(2026, 7, 1), dt.date(2026, 7, 31), today, extra_projected=extra)
    assert b["mrr"] == 0                               # a PIF renewal is NOT recurring MRR
    assert b["forecast"]["next_90"] >= 27000           # but it IS in the forward projection
    aug = next(m for m in b["monthly"] if m["ym"] == "2026-08")
    assert aug["projected"] >= 27000


# ── end-to-end: a PIF member's renewal lifts the forecast ───────────
async def test_build_forum_includes_pif_renewal():
    from app.seed import seed
    from app.db import SessionLocal
    from app.models import Tenant, Business, MetricRecord
    from app.services.forum import build_forum

    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == t.id, Business.key == "springb"))).scalar_one()
        base = (await build_forum(s, t.id, "ytd"))["billing"]["forecast"]["rest_of_year"]

        today = dt.date.today()
        renewal = dt.date(today.year, 12, 28) if today.month == 12 else dt.date(today.year, today.month + 1, 15)
        s.add(MetricRecord(tenant_id=t.id, business_id=biz.id, source="ghl", kind="member",
                           external_id="pif_forecast", email="pifforecast@forum.com", status="active",
                           meta={"membership": {"payment": "pif", "renewal_date": renewal.isoformat(),
                                                "total_cost": 30000}}))
        await s.commit()

        lifted = (await build_forum(s, t.id, "ytd"))["billing"]["forecast"]["rest_of_year"]
        assert lifted == base + 30000


async def test_cashflow_current_month_shows_collected_and_scheduled():
    """The clicked current-month drawer excludes failed charges and shows both what's
    been collected AND what's still scheduled (subs + PIF renewals)."""
    from app.seed import seed
    from app.db import SessionLocal
    from app.models import Tenant, Business, MetricRecord
    from app.services.lineage import metric_detail

    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == t.id, Business.key == "springb"))).scalar_one()
        today = dt.date.today()
        ym = f"{today.year}-{today.month:02d}"
        # collected (succeeded) this month
        s.add(MetricRecord(tenant_id=t.id, business_id=biz.id, source="stripe_legacy", kind="payment",
                           external_id="ok1", name="Paid Member", email="paid@forum.com", amount=2000,
                           status="succeeded", occurred_on=today.replace(day=1),
                           meta={"stream": "memberships", "amount_refunded": 0, "charge_id": "ch_ok1"}))
        # a FAILED charge this month — must be excluded from the cash drawer
        s.add(MetricRecord(tenant_id=t.id, business_id=biz.id, source="stripe_legacy", kind="payment",
                           external_id="fail1", name="Failed Member", email="fail@forum.com", amount=2000,
                           status="failed", occurred_on=today.replace(day=1),
                           meta={"stream": "memberships", "amount_refunded": 0, "charge_id": "ch_fail1"}))
        # a PIF member renewing later this month → scheduled
        s.add(MetricRecord(tenant_id=t.id, business_id=biz.id, source="ghl", kind="member",
                           external_id="pifm", name="PIF Member", email="pifm@forum.com", status="active",
                           meta={"membership": {"payment": "pif",
                                                "renewal_date": today.replace(day=min(today.day + 8, 28)).isoformat(),
                                                "total_cost": 30000}}))
        await s.commit()

        d = await metric_detail(s, t.id, "forum_cashflow", "ytd", business="springb", month=ym)
        names = [r["name"] for r in d["rows"]]
        assert "Paid Member" in names and "Failed Member" not in names   # collected, failed excluded
        assert "collected + scheduled" in d["label"]
        sched = [r for r in d["rows"] if r.get("tone") == "projected"]
        assert any(r["name"] == "PIF Member" and r["r1"] == "$30,000" for r in sched)
