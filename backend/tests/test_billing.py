"""The Forum · Cash & Billing — classifiers (pure), the billing block invariants
(net cash, MRR excludes installments, streams reconcile), and the lineage drills.
Live-shaped seed: 35 succeeded $141,993 − $10,250 refunded = net $131,743, 2 failed
$20,000; 12 perpetual subs = $21,800 MRR + 1 "3 pay" installment $8,800."""
import pytest
from sqlalchemy import select

from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant, Business
from app.services.billing import classify_stream, classify_installment, compute_billing, mrr_of


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


# ── pure classifiers ────────────────────────────────────────────────
def test_stream_classifier():
    assert classify_stream("Forum Sponsorship") == "sponsorships"
    assert classify_stream("The Forum VIP Guest Ticket") == "event_tickets"
    assert classify_stream("Q2 Scottsdale RSVP") == "event_tickets"
    assert classify_stream("New Invoice") == "invoices"
    assert classify_stream("Subscription for Thomas Davis") == "memberships"
    assert classify_stream("Membership for 1 - PIF") == "memberships"
    assert classify_stream("Forum Membership 3 pay - 27k") == "memberships"
    assert classify_stream("Totally Unlabeled Thing") == "other"
    # exact-name config override beats the ordered rules
    assert classify_stream("Forum Sponsorship", {"Forum Sponsorship": "memberships"}) == "memberships"


def test_installment_classifier():
    assert classify_installment("Forum Membership 3 pay - 27k", "2026-06-22", "2026-08-22", {}) == ("installment", 3)
    assert classify_installment("5 pay plan", None, None, {}) == ("installment", 5)
    assert classify_installment("Monthly membership", "2026-05-01", None, {}) == ("perpetual", None)
    # finite term (end within ~13 months) → installment even without an "N pay" name
    assert classify_installment("Termed plan", "2026-01-01", "2026-06-01", {})[0] == "installment"
    # config override list
    assert classify_installment("Weird Plan", None, None, {"installment_plan_names": ["Weird Plan"]})[0] == "installment"


# ── billing block (via the live-shaped seed) ────────────────────────
async def _billing():
    from app.services.forum import build_forum
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        f = await build_forum(s, t.id, "ytd")     # span covers Apr–Jul
    return f


async def test_cash_math_and_invariants():
    f = await _billing()
    b = f["billing"]
    assert b["available"] is True
    assert b["gross"] == 141993 and b["refunded"] == 10250 and b["net_cash"] == 131743
    assert b["failed_count"] == 2 and b["failed_amount"] == 20000
    # net = gross − refunded; failed excluded
    assert round(b["gross"] - b["refunded"], 2) == b["net_cash"]
    # streams reconcile to net cash (the invariant)
    assert round(sum(s["amount"] for s in b["streams"]), 2) == b["net_cash"]
    assert {s["key"] for s in b["streams"]} <= {"memberships", "event_tickets", "sponsorships", "invoices", "other"}


async def test_mrr_excludes_installment_and_is_single_source():
    f = await _billing()
    b = f["billing"]
    assert b["mrr"] == 21800 and b["perpetual_count"] == 12         # installment excluded
    assert len(b["installments"]) == 1 and b["installments"][0]["total"] == 3
    assert b["run_rate"] == 21800 * 12
    # the top-row MRR tile reads the SAME value (one computation source)
    mrr_tile = next(x for x in f["kpis"] if x["key"] == "mrr")
    from app.services.forum import _usd
    assert mrr_tile["value"] == _usd(21800)


async def test_arr_book_reused_and_next30():
    f = await _billing()
    b = f["billing"]
    assert b["arr_book"] > 0                                        # renewal book reused, not recomputed
    assert b["next30"]["charges"] >= 1 and b["next30"]["amount"] > 0


async def test_availability_false_when_no_payments():
    # compute_billing with no payment rows → not available (degraded state)
    import datetime as dt
    out = compute_billing([], [], 1000, dt.date(2026, 7, 1), dt.date(2026, 7, 31), dt.date(2026, 7, 7))
    assert out == {"available": False}


async def test_forum_lineage_drills():
    from app.services.lineage import metric_detail
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        failed = await metric_detail(s, t.id, "forum_failed_payments", "ytd", business="springb")
        assert failed["count"] == 2 and all(r["tone"] == "watch" for r in failed["rows"])
        mrr_subs = await metric_detail(s, t.id, "forum_mrr_subs", "ytd", business="springb")
        assert mrr_subs["count"] == 12
        inst = await metric_detail(s, t.id, "forum_installments", "ytd", business="springb")
        assert inst["count"] == 1
        spon = await metric_detail(s, t.id, "forum_streams", "ytd", business="springb", stream="sponsorships")
        assert spon["count"] >= 1 and all("id" in r for r in spon["rows"])
