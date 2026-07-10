"""Legacy Stripe backfill — the read-only charge accessors (cents→dollars, email
resolution, epoch dates) and the cross-source dedupe that keeps a charge counted
once when it exists in both the legacy Stripe feed and the CSV-backfilled GHL feed.
All pure — no network."""
import datetime as dt
from types import SimpleNamespace

from app.integrations import stripe_legacy as sl
from app.services.billing import merge_payment_sources, _pay_key
from app.services import legacy_export as le


# ── charge accessors ────────────────────────────────────────────────
def _charge(**kw):
    base = {"id": "ch_1", "amount": 200000, "amount_refunded": 0, "currency": "usd",
            "status": "succeeded", "created": 1751990400,  # 2025-07-08 UTC
            "description": "Subscription update", "payment_intent": "pi_9",
            "billing_details": {"email": "Member@Forum.com", "name": "A Member"},
            "customer": {"email": "cust@forum.com", "name": "Cust"}}
    base.update(kw)
    return base


def test_amounts_are_cents_to_dollars():
    assert sl.charge_amount(_charge(amount=200000)) == 2000.0
    assert sl.charge_refunded(_charge(amount_refunded=5000)) == 50.0
    assert sl.charge_amount(_charge(amount=0)) == 0.0


def test_email_prefers_billing_then_receipt_then_customer():
    assert sl.charge_email(_charge()) == "member@forum.com"                 # billing_details wins
    c = _charge(billing_details={}, receipt_email="Receipt@x.com")
    assert sl.charge_email(c) == "receipt@x.com"                            # falls to receipt
    c = _charge(billing_details={}, receipt_email=None)
    assert sl.charge_email(c) == "cust@forum.com"                           # then the expanded customer
    assert sl.charge_email(_charge(billing_details={}, receipt_email=None, customer=None)) is None


def test_status_and_date():
    assert sl.charge_status(_charge(status="succeeded")) == "succeeded"
    assert sl.charge_status(_charge(status="failed")) == "failed"
    assert sl.charge_status(_charge(status="pending")) == "pending"
    assert sl.charge_date(_charge(created=1751990400)) == dt.date(2025, 7, 8)
    assert sl.charge_date(_charge(created=None)) is None


def test_dashboard_url_uses_payment_intent():
    assert sl.dashboard_url(_charge(payment_intent="pi_9")).endswith("/payments/pi_9")
    assert sl.dashboard_url(_charge(payment_intent=None, id="ch_2")).endswith("/payments/ch_2")


# ── cross-source dedupe ─────────────────────────────────────────────
def _p(email, amount, day, source="ghl"):
    return SimpleNamespace(email=email, amount=amount, occurred_on=dt.date(2026, 7, day), source=source)


def test_dedupe_suppresses_the_ghl_backfill_copy():
    legacy = [_p("a@x.com", 2000, 8, "stripe_legacy")]
    ghl = [_p("a@x.com", 2000, 8, "ghl")]                    # the imported copy of the same charge
    merged, suppressed = merge_payment_sources(ghl, legacy)
    assert suppressed == 1
    assert len(merged) == 1 and merged[0].source == "stripe_legacy"


def test_native_ghl_charge_survives():
    legacy = [_p("a@x.com", 2000, 8, "stripe_legacy")]
    ghl = [_p("b@x.com", 500, 8, "ghl")]                     # unrelated new-sub-account charge
    merged, suppressed = merge_payment_sources(ghl, legacy)
    assert suppressed == 0 and len(merged) == 2


def test_dedupe_is_one_to_one_not_greedy():
    # One legacy charge, but TWO same-key GHL rows (one backfill copy + one genuine
    # same-day/same-amount native charge). Only one GHL row is folded away.
    legacy = [_p("a@x.com", 2000, 8, "stripe_legacy")]
    ghl = [_p("a@x.com", 2000, 8, "ghl"), _p("a@x.com", 2000, 8, "ghl")]
    merged, suppressed = merge_payment_sources(ghl, legacy)
    assert suppressed == 1 and len(merged) == 2


def test_dedupe_key_rounds_whole_dollars_and_lowercases():
    assert _pay_key(_p("A@X.com", 2000.4, 8)) == _pay_key(_p("a@x.com", 1999.6, 8))


# ── GHL delta CSV format (the proven leading-space / zero-padded / no-seconds shape) ─
def _rec(name="Nathan Abbott", email="a@x.com", amount=2000, day=8, status="succeeded",
         charged_at="2026-07-08T16:48:00+00:00", contact=None):
    meta = {"charged_at": charged_at, "currency": "usd", "contact": contact or {}}
    return SimpleNamespace(name=name, email=email, amount=amount, status=status,
                           occurred_on=dt.date(2026, 7, day), meta=meta)


def test_fmt_date_is_leading_space_zero_padded_ddmmyyyy():
    assert le.fmt_date(dt.date(2026, 7, 8)) == " 08/07/2026"
    assert le.fmt_date(dt.date(2026, 12, 25)) == " 25/12/2026"


def test_fmt_time_is_leading_space_two_digit_hour_no_seconds():
    assert le.fmt_time("2026-07-08T16:48:00+00:00") == " 04:48 PM"
    assert le.fmt_time("2026-07-08T09:05:00+00:00") == " 09:05 AM"
    assert le.fmt_time(None) == " 12:00 PM"          # default when no timestamp
    assert le.fmt_time("garbage") == " 12:00 PM"


def test_payment_row_has_17_cols_and_split_name():
    row = le.payment_row(_rec(name="Nathan Abbott", amount=2000,
                              contact={"phone": "18507720635", "country": "United States"}))
    assert len(row) == len(le.TEMPLATE) == 17
    assert row[0] == "Nathan" and row[1] == "Abbott" and row[2] == "a@x.com"
    assert row[3] == "18507720635" and row[4] == "USD"
    assert row[7] == "2000"                          # amount, trimmed
    assert row[8] == " 08/07/2026" and row[9] == " 04:48 PM"   # the critical two
    assert row[10] == "Card" and row[11] == "Stripe" and row[15] == "United States"


def test_pending_filters_succeeded_after_watermark_and_sorts():
    recs = [_rec(email="a@x.com", day=10, status="succeeded"),
            _rec(email="b@x.com", day=3, status="succeeded"),
            _rec(email="c@x.com", day=9, status="refunded"),   # refunds don't upload
            _rec(email="d@x.com", day=6, status="succeeded")]
    pend = le.pending(recs, through=dt.date(2026, 7, 5))        # only day>5, succeeded
    assert [r.occurred_on.day for r in pend] == [6, 10]         # day 3 excluded, sorted


def test_build_csv_header_no_bom_and_watermark():
    recs = [_rec(email="a@x.com", day=8), _rec(email="b@x.com", day=11)]
    text, n, new_wm = le.build_csv(recs, through=None)
    assert not text.startswith("﻿")                       # no BOM
    lines = text.splitlines()
    assert lines[0].startswith("Customer First Name")          # header intact
    assert n == 2 and new_wm == dt.date(2026, 7, 11)           # advance to latest
    assert " 08/07/2026" in text                               # leading-space date present


# ── sync (seed-backed): roster filter + record shape, Stripe HTTP monkeypatched ──
async def test_sync_stripe_legacy_filters_to_roster(monkeypatch):
    from sqlalchemy import select
    from app.seed import seed
    from app.db import SessionLocal
    from app.models import Tenant, Business, Integration, MetricRecord
    from app.security import enc
    from app.services import sync as sync_mod
    from app.integrations import stripe_legacy as sl_mod

    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == t.id, Business.key == "springb"))).scalar_one()
        # A known Forum member on the roster (the GHL sync is what normally provides this).
        member_email = "legacy.member@forum.com"
        s.add(MetricRecord(tenant_id=t.id, business_id=biz.id, source="ghl", kind="member",
                           external_id="roster_seed_1", email=member_email, status="active"))
        integ = Integration(tenant_id=t.id, provider="stripe_legacy", business_id=biz.id,
                            status="connected", access_token_enc=enc("rk_test_x"))
        s.add(integ)
        await s.commit()

        async def fake_list_charges(key, created_gt=None, max_pages=None):
            return [
                {"id": "ch_match", "amount": 250000, "amount_refunded": 0, "currency": "usd",
                 "status": "succeeded", "created": 1751990400, "description": "Membership for 1",
                 "payment_intent": "pi_m", "billing_details": {"email": member_email, "name": "Legacy Member"}},
                {"id": "ch_stranger", "amount": 99900, "amount_refunded": 0, "currency": "usd",
                 "status": "succeeded", "created": 1751990400, "description": "Some Other Product",
                 "payment_intent": "pi_s", "billing_details": {"email": "stranger@nowhere.com", "name": "X"}},
            ]
        monkeypatch.setattr(sl_mod, "list_charges", fake_list_charges)

        n = await sync_mod.sync_stripe_legacy(s, t.id, integ)
        assert n == 1                                          # stranger filtered out
        legacy = (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == t.id, MetricRecord.source == "stripe_legacy",
            MetricRecord.kind == "payment"))).scalars().all()
        assert len(legacy) == 1
        r = legacy[0]
        assert r.email == member_email.strip().lower() and float(r.amount) == 2500.0
        assert r.meta["stream"] == "memberships" and r.meta["legacy"] is True
        assert r.meta["charge_id"] == "ch_match" and r.meta["charged_at"]
