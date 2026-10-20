"""Commissions for the beCollective Sales Desk (2026-10-20).

Two guarantees carry this whole feature, and both are about money leaving twice:
a payment is RECORDED once however often the sync runs, and PAID once however much the
opportunity changes after the month is closed. Most of what follows is those two.
"""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select, delete

from app.db import SessionLocal
from app.models import (Business, CommissionPayment, CommissionStatement, Launch,
                        MetricRecord, Payable, PayableApproval, PayableEvent, SalesCall,
                        SalesRep, User, Vendor)
from app.seed import seed
from app.services import commissions as cm
from app.services.launch import DEFAULT_STAGE_MAP, DEFAULT_PAYMENT_PLAN_MAP

U = dt.timezone.utc


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def _txn(ext, contact, amount, when, status="succeeded", refunded=0):
    return {"_id": ext, "contactId": contact, "contactName": contact.title(),
            "amount": amount, "amountRefunded": refunded, "status": status,
            "createdAt": when.isoformat()}


async def _world(rates=(("rep-a@x.com", "Rep A", Decimal("10")),
                        ("rep-b@x.com", "Rep B", Decimal("20")))):
    """A launch, two members with reps, and a vendor to bill."""
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        tid = biz.tenant_id
        # Children first: a bulk delete does not cascade on SQLite, and Payable holds the
        # vendor with ondelete=RESTRICT.
        await s.execute(delete(CommissionPayment))
        await s.execute(delete(CommissionStatement))
        await s.execute(delete(PayableEvent))
        await s.execute(delete(PayableApproval))
        await s.execute(delete(Payable))
        await s.execute(delete(Vendor).where(Vendor.tenant_id == tid))
        await s.execute(delete(SalesCall))
        await s.execute(delete(SalesRep).where(SalesRep.tenant_id == tid))
        await s.execute(delete(MetricRecord).where(MetricRecord.kind == "bc_launch_opp"))
        await s.execute(delete(Launch).where(Launch.business_id == biz.id))
        launch = Launch(tenant_id=tid, business_id=biz.id, name="C", program="beCollective",
                        window_start=dt.date(2026, 8, 1), window_end=dt.date(2026, 12, 31),
                        goal_arr=1_000_000, ticket_pif=12000, ticket_plan=14000,
                        goal_basis="seats", seat_goal=40, stage_map=DEFAULT_STAGE_MAP,
                        payment_plan_map=DEFAULT_PAYMENT_PLAN_MAP, default_tz="America/Denver",
                        pipeline_match="be collective experience #1 sales")
        s.add(launch)
        await s.flush()
        for email, name, pct in rates:
            s.add(SalesRep(tenant_id=tid, email=email, display_name=name, commission_pct=pct))
        for opp, contact, member, rep, pay in (
                ("o1", "c1", "Member One", "rep-a@x.com", "PIF"),
                ("o2", "c2", "Member Two", "rep-b@x.com", "Monthly")):
            s.add(MetricRecord(tenant_id=tid, business_id=biz.id, source="ghl",
                               kind="bc_launch_opp", external_id=opp, name=member, status="won",
                               meta={"launch_id": str(launch.id), "group": "enrolled",
                                     "contact_id": contact, "payment_type": "pif"}))
            s.add(SalesCall(tenant_id=tid, launch_id=launch.id, opportunity_id=opp,
                            booking_id=f"b{opp}", contact_id=contact, contact_name=member,
                            rep_email=rep, payment_type=pay, is_current=True,
                            call_time_utc=dt.datetime(2026, 8, 20, 15, tzinfo=U)))
        from app.services.payables_vendor import normalize_name
        v = Vendor(tenant_id=tid, legal_name="Empire Builders LLC",
                   name_norm=normalize_name("Empire Builders LLC"),
                   display_name="Empire Builders")
        s.add(v)
        await s.flush()
        out = (tid, launch.id, v.id)
        await s.commit()
        return out


async def _launch(lid):
    async with SessionLocal() as s:
        return (await s.execute(select(Launch).where(Launch.id == lid))).scalar_one()


async def _actor(tid):
    async with SessionLocal() as s:
        return (await s.execute(select(User).where(User.tenant_id == tid))).scalars().first()


def _patch_txns(monkeypatch, rows):
    async def fake(token, location_id, **kw):
        return rows
    monkeypatch.setattr(cm.ghl, "ghl_transactions", fake)


async def _sync(tid, launch):
    async with SessionLocal() as s:
        return await cm.sync_commission_payments(s, tid, launch, "tok", "loc")


async def _report(tid, launch, period):
    async with SessionLocal() as s:
        return await cm.month_report(s, tid, launch, period)


# ── guarantee 1: recorded once ────────────────────────────────────────────────────────────

async def test_resyncing_never_records_a_payment_twice(monkeypatch):
    tid, lid, _v = await _world()
    launch = await _launch(lid)
    rows = [_txn("t1", "c1", 12000, dt.datetime(2026, 9, 10, 18, tzinfo=U))]
    _patch_txns(monkeypatch, rows)
    assert (await _sync(tid, launch)).get("recorded") == 1
    for _ in range(3):
        await _sync(tid, launch)
    async with SessionLocal() as s:
        got = (await s.execute(select(CommissionPayment))).scalars().all()
    assert len(got) == 1 and got[0].amount == Decimal("12000.00")


async def test_only_money_that_actually_arrived_is_recorded(monkeypatch):
    tid, lid, _v = await _world()
    launch = await _launch(lid)
    when = dt.datetime(2026, 9, 10, 18, tzinfo=U)
    _patch_txns(monkeypatch, [
        _txn("ok", "c1", 12000, when),
        _txn("nope", "c1", 12000, when, status="failed"),      # a failed charge is not cash
        _txn("refunded", "c1", 500, when, refunded=500),       # fully refunded nets to zero
        _txn("part", "c2", 1200, when, refunded=200),          # partial refund nets down
        _txn("stranger", "c999", 9999, when),                  # not a member of this cohort
    ])
    await _sync(tid, launch)
    async with SessionLocal() as s:
        got = {p.external_id: p.amount for p in
               (await s.execute(select(CommissionPayment))).scalars()}
    assert got == {"ok": Decimal("12000.00"), "part": Decimal("1000.00")}


# ── guarantee 2: paid once ────────────────────────────────────────────────────────────────

async def test_closing_locks_the_month_and_freezes_the_rate(monkeypatch):
    tid, lid, vid = await _world()
    launch, actor = await _launch(lid), await _actor(tid)
    _patch_txns(monkeypatch, [_txn("t1", "c1", 12000, dt.datetime(2026, 9, 10, 18, tzinfo=U)),
                              _txn("t2", "c2", 1200, dt.datetime(2026, 9, 20, 18, tzinfo=U))])
    await _sync(tid, launch)
    async with SessionLocal() as s:
        out = await cm.close_month(s, tid, launch, "2026-09", vid, actor)
    assert out["payments_locked"] == 2
    assert out["total_cash"] == 13200.0
    assert out["total_commission"] == 1200.0 + 240.0        # 10% of 12000, 20% of 1200

    # The rate is now history. Changing it must not restate an invoice already sent.
    async with SessionLocal() as s:
        rep = (await s.execute(select(SalesRep).where(SalesRep.email == "rep-a@x.com"))).scalar_one()
        rep.commission_pct = Decimal("50")
        await s.commit()
    rep_row = next(r for r in (await _report(tid, launch, "2026-09"))["rows"]
                   if r["rep_email"] == "rep-a@x.com")
    assert rep_row["rate_pct"] == 10.0 and rep_row["commission"] == 1200.0


async def test_a_locked_payment_survives_the_opportunity_changing_underneath_it(monkeypatch):
    """The reason statement_id exists. Somebody re-assigning the rep in November must not
    move October's money onto a different invoice."""
    tid, lid, vid = await _world()
    launch, actor = await _launch(lid), await _actor(tid)
    rows = [_txn("t1", "c1", 12000, dt.datetime(2026, 9, 10, 18, tzinfo=U))]
    _patch_txns(monkeypatch, rows)
    await _sync(tid, launch)
    async with SessionLocal() as s:
        await cm.close_month(s, tid, launch, "2026-09", vid, actor)

    async with SessionLocal() as s:                     # the rep changes, and the amount
        call = (await s.execute(select(SalesCall).where(SalesCall.contact_id == "c1"))).scalar_one()
        call.rep_email = "rep-b@x.com"
        await s.commit()
    rows[0]["amount"] = 999
    await _sync(tid, launch)

    async with SessionLocal() as s:
        p = (await s.execute(select(CommissionPayment).where(
            CommissionPayment.external_id == "t1"))).scalar_one()
    assert p.rep_email == "rep-a@x.com", "a settled payment must not change hands"
    assert p.amount == Decimal("12000.00"), "a settled payment must not change amount"
    assert p.commission_amount == Decimal("1200.00")


async def test_a_closed_month_cannot_be_closed_again(monkeypatch):
    tid, lid, vid = await _world()
    launch, actor = await _launch(lid), await _actor(tid)
    _patch_txns(monkeypatch, [_txn("t1", "c1", 12000, dt.datetime(2026, 9, 10, 18, tzinfo=U))])
    await _sync(tid, launch)
    async with SessionLocal() as s:
        await cm.close_month(s, tid, launch, "2026-09", vid, actor)
    with pytest.raises(ValueError, match="already closed"):
        async with SessionLocal() as s:
            await cm.close_month(s, tid, launch, "2026-09", vid, actor)


async def test_settled_cash_never_reappears_in_a_later_month(monkeypatch):
    """The double-pay scenario in one test: close September, then ask for October."""
    tid, lid, vid = await _world()
    launch, actor = await _launch(lid), await _actor(tid)
    _patch_txns(monkeypatch, [_txn("sep", "c1", 12000, dt.datetime(2026, 9, 10, 18, tzinfo=U)),
                              _txn("oct", "c2", 1200, dt.datetime(2026, 10, 10, 18, tzinfo=U))])
    await _sync(tid, launch)
    async with SessionLocal() as s:
        await cm.close_month(s, tid, launch, "2026-09", vid, actor)
    octo = await _report(tid, launch, "2026-10")
    assert [r["member"] for r in octo["rows"]] == ["Member Two"]
    assert octo["total_cash"] == 1200.0
    async with SessionLocal() as s:
        out = await cm.close_month(s, tid, launch, "2026-10", vid, actor)
    assert out["payments_locked"] == 1 and out["total_cash"] == 1200.0


# ── the bill ──────────────────────────────────────────────────────────────────────────────

async def test_closing_drafts_one_bill_for_the_agency(monkeypatch):
    tid, lid, vid = await _world()
    launch, actor = await _launch(lid), await _actor(tid)
    _patch_txns(monkeypatch, [_txn("t1", "c1", 12000, dt.datetime(2026, 9, 10, 18, tzinfo=U))])
    await _sync(tid, launch)
    async with SessionLocal() as s:
        out = await cm.close_month(s, tid, launch, "2026-09", vid, actor)
    assert out["invoice_number"] == "BC-COMM-2026-09"
    async with SessionLocal() as s:
        bills = (await s.execute(select(Payable).where(
            Payable.invoice_number == "BC-COMM-2026-09"))).scalars().all()
    assert len(bills) == 1 and bills[0].amount == Decimal("1200.00")


async def test_a_close_that_already_wrote_its_bill_can_be_repeated(monkeypatch):
    """A close interrupted after the bill was drafted must not be permanently unrepeatable —
    create_payable refuses a duplicate invoice number, so the retry has to adopt it."""
    tid, lid, vid = await _world()
    launch, actor = await _launch(lid), await _actor(tid)
    _patch_txns(monkeypatch, [_txn("t1", "c1", 12000, dt.datetime(2026, 9, 10, 18, tzinfo=U))])
    await _sync(tid, launch)
    from app.services.payables import create_payable
    async with SessionLocal() as s:                     # the bill, written by a failed close
        await create_payable(s, tid, actor, {
            "vendor_id": str(vid), "invoice_number": "BC-COMM-2026-09", "amount": "1200.00"})
    async with SessionLocal() as s:
        out = await cm.close_month(s, tid, launch, "2026-09", vid, actor)
    assert out["payments_locked"] == 1
    async with SessionLocal() as s:
        bills = (await s.execute(select(Payable).where(
            Payable.invoice_number == "BC-COMM-2026-09"))).scalars().all()
    assert len(bills) == 1, "the retry adopted the existing bill instead of colliding"


# ── what cannot be priced ─────────────────────────────────────────────────────────────────

async def test_cash_with_no_rep_or_no_rate_stays_open_and_visible(monkeypatch):
    """Closing it at zero would write off somebody's pay; blocking the month on it would hold
    everyone else's. It stays open, and it is named."""
    tid, lid, vid = await _world(rates=(("rep-a@x.com", "Rep A", Decimal("10")),
                                        ("rep-b@x.com", "Rep B", None)))
    launch, actor = await _launch(lid), await _actor(tid)
    async with SessionLocal() as s:                     # Member One loses their rep
        call = (await s.execute(select(SalesCall).where(SalesCall.contact_id == "c1"))).scalar_one()
        call.rep_email = None
        await s.commit()
    _patch_txns(monkeypatch, [_txn("t1", "c1", 12000, dt.datetime(2026, 9, 10, 18, tzinfo=U)),
                              _txn("t2", "c2", 1200, dt.datetime(2026, 9, 11, 18, tzinfo=U))])
    await _sync(tid, launch)

    rep = await _report(tid, launch, "2026-09")
    issues = {x["member"]: x["issue"] for x in rep["unresolved"]}
    assert "no sales rep" in issues["Member One"]
    assert "no commission rate" in issues["Member Two"]
    assert rep["total_cash"] == 13200.0, "unpriced cash still counts as cash collected"
    assert rep["total_commission"] == 0.0

    with pytest.raises(ValueError, match="nothing to close"):
        async with SessionLocal() as s:
            await cm.close_month(s, tid, launch, "2026-09", vid, actor)

    async with SessionLocal() as s:                     # give Rep B a rate; only theirs closes
        r = (await s.execute(select(SalesRep).where(SalesRep.email == "rep-b@x.com"))).scalar_one()
        r.commission_pct = Decimal("20")
        await s.commit()
    async with SessionLocal() as s:
        out = await cm.close_month(s, tid, launch, "2026-09", vid, actor)
    assert out["payments_locked"] == 1
    assert [x["member"] for x in out["left_open"]] == ["Member One"]
    assert (await _report(tid, launch, "2026-09"))["unresolved"], "still flagged after the close"


# ── periods ───────────────────────────────────────────────────────────────────────────────

async def test_a_payment_belongs_to_the_month_it_was_made_locally(monkeypatch):
    """6pm Mountain on 30 September is already 1 October in UTC. Bucketing on UTC would move
    that dollar onto the next invoice."""
    tid, lid, _v = await _world()
    launch = await _launch(lid)
    _patch_txns(monkeypatch, [_txn("late", "c1", 500, dt.datetime(2026, 10, 1, 0, 30, tzinfo=U))])
    await _sync(tid, launch)
    assert (await _report(tid, launch, "2026-09"))["total_cash"] == 500.0
    assert (await _report(tid, launch, "2026-10"))["total_cash"] == 0.0


async def test_reopening_unlocks_the_payments_but_leaves_the_bill(monkeypatch):
    tid, lid, vid = await _world()
    launch, actor = await _launch(lid), await _actor(tid)
    _patch_txns(monkeypatch, [_txn("t1", "c1", 12000, dt.datetime(2026, 9, 10, 18, tzinfo=U))])
    await _sync(tid, launch)
    async with SessionLocal() as s:
        await cm.close_month(s, tid, launch, "2026-09", vid, actor)
    async with SessionLocal() as s:
        out = await cm.reopen_month(s, tid, launch, "2026-09")
    assert out["payments_unlocked"] == 1
    async with SessionLocal() as s:
        p = (await s.execute(select(CommissionPayment))).scalars().one()
        assert p.statement_id is None and p.rate_pct is None and p.commission_amount is None
        bills = (await s.execute(select(Payable).where(
            Payable.invoice_number == "BC-COMM-2026-09"))).scalars().all()
    assert len(bills) == 1, "the drafted bill is left for a person to void"


# ── the spreadsheet ───────────────────────────────────────────────────────────────────────

async def test_the_csv_carries_the_rows_the_totals_and_the_caveat(monkeypatch):
    tid, lid, _v = await _world()
    launch = await _launch(lid)
    _patch_txns(monkeypatch, [_txn("t1", "c1", 12000, dt.datetime(2026, 9, 10, 18, tzinfo=U)),
                              _txn("t2", "c2", 1200, dt.datetime(2026, 9, 20, 18, tzinfo=U))])
    await _sync(tid, launch)
    body = cm.report_csv(await _report(tid, launch, "2026-09"))
    assert "Member One" in body and "Rep A" in body
    assert "1200.00" in body and "13200.00" in body
    assert "TOTAL" in body
    # Nobody should have to be told separately that cash taken outside GHL is missing.
    assert "outside it does not appear" in body


def test_member_names_lose_the_phone_number_glued_to_them():
    """GHL names an opportunity "Amy Dolan - +14095546223". That CSV goes to an outside
    agency, so the phone number has no business travelling with it."""
    assert cm.clean_name("Amy Dolan - +14095546223") == "Amy Dolan"
    assert cm.clean_name("Zer Her Realty - (414) 758-6352") == "Zer Her Realty"
    assert cm.clean_name("Elisha O'Brien  - +15092164394") == "Elisha O'Brien"
    assert cm.clean_name("Barbara Rose") == "Barbara Rose"      # nothing to strip
    assert cm.clean_name("Mary-Jane Watson") == "Mary-Jane Watson"   # a hyphen is not a phone
    assert cm.clean_name(None) is None and cm.clean_name("   ") is None
