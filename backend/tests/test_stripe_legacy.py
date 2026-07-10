"""Legacy Stripe backfill — the read-only charge accessors (cents→dollars, email
resolution, epoch dates) and the cross-source dedupe that keeps a charge counted
once when it exists in both the legacy Stripe feed and the CSV-backfilled GHL feed.
All pure — no network."""
import datetime as dt
from types import SimpleNamespace

from app.integrations import stripe_legacy as sl
from app.services.billing import merge_payment_sources, _pay_key, forum_offering
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
def _p(email, amount, day, source="ghl", charge_id=None):
    return SimpleNamespace(email=email, amount=amount, occurred_on=dt.date(2026, 7, day),
                           source=source, meta=({"charge_id": charge_id} if charge_id else {}))


def test_backfill_copy_suppressed_even_with_wrong_date():
    # The backfill copy is id-less and dated the import day (day 30); its legacy twin is
    # dated the real charge day (day 8). Matched by payer+amount, ignoring date.
    legacy = [_p("a@x.com", 2000, 8, "stripe_legacy")]
    ghl = [_p("a@x.com", 2000, 30, "ghl")]                   # no charge_id → treated as backfill
    merged, suppressed = merge_payment_sources(ghl, legacy)
    assert suppressed == 1
    assert len(merged) == 1 and merged[0].source == "stripe_legacy"


def test_idless_ghl_with_no_legacy_twin_is_kept():
    # A GHL-only payment (manual / non-Stripe / other channel) legacy doesn't have —
    # must NEVER be dropped (legacy is not the complete history).
    legacy = [_p("a@x.com", 2000, 8, "stripe_legacy")]
    ghl = [_p("b@x.com", 500, 8, "ghl")]                     # no legacy twin
    merged, suppressed = merge_payment_sources(ghl, legacy)
    assert suppressed == 0 and len(merged) == 2


def test_id_bearing_ghl_charge_always_survives():
    # A native new-account charge carries a charge id; even if it happens to share
    # payer+amount with a legacy charge (different Stripe accounts), it's kept.
    legacy = [_p("a@x.com", 2000, 8, "stripe_legacy")]
    ghl = [_p("a@x.com", 2000, 8, "ghl", charge_id="pi_native")]
    merged, suppressed = merge_payment_sources(ghl, legacy)
    assert suppressed == 0 and len(merged) == 2


def test_dedupe_is_one_to_one_not_greedy():
    # One legacy charge, two id-less GHL rows with the same payer+amount → only one folded
    # away; the second is kept (could be a genuine second payment).
    legacy = [_p("a@x.com", 2000, 8, "stripe_legacy")]
    ghl = [_p("a@x.com", 2000, 8, "ghl"), _p("a@x.com", 2000, 30, "ghl")]
    merged, suppressed = merge_payment_sources(ghl, legacy)
    assert suppressed == 1 and len(merged) == 2


def test_dedupe_key_rounds_whole_dollars_and_lowercases():
    assert _pay_key(_p("A@X.com", 2000.4, 8)) == _pay_key(_p("a@x.com", 1999.6, 8))


# ── Forum-vs-non-Forum offering classifier (Connor's description rule) ────
def test_forum_offering_rules():
    assert forum_offering("The Forum – Monthly Dues") == (True, "forum")
    assert forum_offering("Inner Circle Monthly") == (True, "inner_circle")
    assert forum_offering("Membership Financed") == (True, "inner_circle")   # no 'Forum' → IC
    assert forum_offering("The Edge Monthly") == (False, None)               # denylisted product
    assert forum_offering("beCollective Cohort") == (False, None)
    assert forum_offering("Event Ticket") == (False, None)                   # the $7 tickets
    assert forum_offering("The Forum VIP Ticket") == (True, "forum")         # a Forum event IS kept
    assert forum_offering("Buyer Mastery Course") == (False, None)
    assert forum_offering("Some Random Charge") == (False, None)             # small ambiguous one-off → out
    # a recurring subscription for a roster member is a membership even if thinly named
    assert forum_offering("Standard Plan", is_subscription=True) == (True, "inner_circle")
    # thin description but recurring or membership-sized → a membership (not dropped)
    assert forum_offering("Subscription update", recurring=True) == (True, "inner_circle")
    assert forum_offering("Some Random Charge", amount=2000) == (True, "inner_circle")
    assert forum_offering("Tiny one-off", amount=25) == (False, None)        # small → out
    assert forum_offering("The Edge Intensive", amount=5000) == (False, None)  # denylisted beats size


def test_forum_offering_config_overrides():
    assert forum_offering("Widget", {"forum_keywords": ["widget"]}) == (True, "forum")
    assert forum_offering("The Forum Dues", {"non_forum_keywords": ["forum"]}) == (False, None)


# ── subscription accessors ──────────────────────────────────────────
def _sub(**kw):
    base = {"id": "sub_1", "status": "active", "start_date": 1735689600,  # 2025-01-01
            "current_period_end": 1793000000, "cancel_at": None,
            "customer": {"email": "M@Forum.com", "name": "Dues Member"},
            "items": {"data": [{"quantity": 1, "price": {"unit_amount": 250000,
                      "recurring": {"interval": "month"}, "product": {"name": "The Forum Monthly"}}}]}}
    base.update(kw)
    return base


def test_sub_accessors():
    assert sl.sub_amount(_sub()) == 2500.0                    # cents → dollars, ×quantity
    assert sl.sub_interval(_sub()) == "month"
    assert sl.sub_email(_sub()) == "m@forum.com"
    assert sl.sub_plan_name(_sub()) == "The Forum Monthly"
    assert sl.sub_status(_sub(status="active")) == "active"
    assert sl.sub_status(_sub(status="past_due")) == "past_due"
    assert sl.sub_status(_sub(status="canceled")) == "canceled"
    assert sl.sub_start_date(_sub()) == dt.date(2025, 1, 1)
    assert sl.sub_end_date(_sub(cancel_at=None)) is None


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
        async def fake_list_subs(key, max_pages=None):
            return []
        monkeypatch.setattr(sl_mod, "list_charges", fake_list_charges)
        monkeypatch.setattr(sl_mod, "list_subscriptions", fake_list_subs)

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


async def test_cashflow_drill_shows_legacy_only_month():
    """The bug: clicking a pre-sub-account month showed '0 records' because the drill
    read GHL only, while the chart bar included the merged legacy charge. Now the drill
    merges the same way."""
    from sqlalchemy import select
    from app.seed import seed
    from app.db import SessionLocal
    from app.models import Tenant, Business, MetricRecord
    from app.services.lineage import metric_detail

    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == t.id, Business.key == "springb"))).scalar_one()
        # A legacy charge in January — before the 4-month-old sub-account existed (no GHL rows).
        s.add(MetricRecord(tenant_id=t.id, business_id=biz.id, source="stripe_legacy", kind="payment",
                           external_id="ch_jan1", name="Legacy Member", email="jan@forum.com",
                           amount=2000, status="succeeded", occurred_on=dt.date(2026, 1, 15),
                           source_url="https://dashboard.stripe.com/payments/pi_jan1",
                           meta={"stream": "memberships", "charged_at": "2026-01-15T12:00:00+00:00",
                                 "amount_refunded": 0, "charge_id": "ch_jan1", "legacy": True}))
        await s.commit()

        jan = await metric_detail(s, t.id, "forum_cashflow", "ytd", business="springb", month="2026-01")
        assert jan["count"] >= 1 and jan["source"] == "Stripe payments"
        assert any(r["name"] == "Legacy Member" and r["r1"] == "$2,000" for r in jan["rows"])
        # And it surfaces in the all-transactions drill too.
        allt = await metric_detail(s, t.id, "forum_payments", "ytd", business="springb")
        assert any(r["name"] == "Legacy Member" for r in allt["rows"])


async def test_legacy_subscriptions_lift_mrr(monkeypatch):
    """The legacy recurring dues live only in the old account; pulling them from Stripe
    must raise MRR (previously understated to the ~13 new-sub-account subs only)."""
    from sqlalchemy import select
    from app.seed import seed
    from app.db import SessionLocal
    from app.models import Tenant, Business, Integration, MetricRecord
    from app.security import enc
    from app.services import sync as sync_mod
    from app.integrations import stripe_legacy as sl_mod
    from app.services.forum import build_forum

    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == t.id, Business.key == "springb"))).scalar_one()
        s.add(MetricRecord(tenant_id=t.id, business_id=biz.id, source="ghl", kind="member",
                           external_id="rs_dues", email="dues@forum.com", status="active"))
        integ = Integration(tenant_id=t.id, provider="stripe_legacy", business_id=biz.id,
                            status="connected", access_token_enc=enc("rk_test"))
        s.add(integ)
        await s.commit()

        base_mrr = (await build_forum(s, t.id, "ytd"))["billing"]["mrr"]

        async def fake_charges(key, created_gt=None, max_pages=None):
            return []

        async def fake_subs(key, max_pages=None):
            return [{
                "id": "sub_legacy1", "status": "active", "start_date": 1735689600,
                "current_period_end": 1793000000, "cancel_at": None,
                "customer": {"email": "dues@forum.com", "name": "Dues Member"},
                "items": {"data": [{"quantity": 1, "price": {"unit_amount": 250000,
                          "recurring": {"interval": "month"}, "product": {"name": "The Forum Monthly"}}}]}}]
        monkeypatch.setattr(sl_mod, "list_charges", fake_charges)
        monkeypatch.setattr(sl_mod, "list_subscriptions", fake_subs)

        n = await sync_mod.sync_stripe_legacy(s, t.id, integ)
        assert n == 1                                          # 0 charges + 1 subscription
        subs = (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == t.id, MetricRecord.source == "stripe_legacy",
            MetricRecord.kind == "subscription"))).scalars().all()
        assert len(subs) == 1 and subs[0].segment == "forum" and float(subs[0].amount) == 2500.0

        lifted = (await build_forum(s, t.id, "ytd"))["billing"]["mrr"]
        assert lifted == base_mrr + 2500.0                     # legacy dues now in MRR


async def test_backfill_ghl_rows_dropped_for_legacy():
    """The CSV-backfill GHL rows (no charge id, dated the import day) are dropped once
    legacy Stripe is present; the charge shows once, on its real legacy date."""
    from sqlalchemy import select
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
        # the mis-dated GHL backfill copy (no charge_id, dated the import day)
        s.add(MetricRecord(tenant_id=t.id, business_id=biz.id, source="ghl", kind="payment",
                           external_id="ghl_bf", name="Backfill Person", email="bf@forum.com",
                           amount=9999, status="succeeded", occurred_on=today,
                           meta={"stream": "memberships", "amount_refunded": 0}))
        # the real legacy Stripe charge (charge_id + correct earlier date)
        s.add(MetricRecord(tenant_id=t.id, business_id=biz.id, source="stripe_legacy", kind="payment",
                           external_id="ch_bf", name="Backfill Person", email="bf@forum.com",
                           amount=9999, status="succeeded", occurred_on=dt.date(2026, 2, 10),
                           source_url="https://dashboard.stripe.com/payments/pi_bf",
                           meta={"stream": "memberships", "amount_refunded": 0,
                                 "charge_id": "ch_bf", "legacy": True}))
        await s.commit()

        allt = await metric_detail(s, t.id, "forum_payments", "ytd", business="springb")
        bf = [r for r in allt["rows"] if r["name"] == "Backfill Person"]
        assert len(bf) == 1 and bf[0]["r2"] == "Feb 10"        # once, on the real legacy date
