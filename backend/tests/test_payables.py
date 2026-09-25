"""Payables Phase 2 — bills and approvals (SPEC-payables §3.6).

These assert controls, not features. Each one is a way money could leave without the right
person having agreed to it: paying the same invoice twice, an amount that falls in no band, one
signature standing in for two, or a payee nobody verified.
"""
import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.seed import seed
from app.db import SessionLocal
from app.models import (ApprovalPolicy, AuditLog, Payable, PayableApproval, PayableEvent,
                        Tenant, User)
from app.services import payables as pay
from app.services import payables_vendor as pv
from app.security import hash_pw


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _ctx():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = (await s.execute(select(User).where(User.tenant_id == t.id))).scalars().first()
        return t.id, u


async def _second_user(tenant_id):
    """A real second person. Two-signature bands cannot be tested with one user."""
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(
            User.tenant_id == tenant_id, User.email == "second@payables.test"))
            ).scalar_one_or_none()
        if u is None:
            u = User(tenant_id=tenant_id, email="second@payables.test", name="Second Approver",
                     password_hash=hash_pw("x"), role="admin")
            s.add(u)
            await s.commit()
        return u


async def _reset(tenant_id):
    async with SessionLocal() as s:
        for model in (PayableEvent, PayableApproval):
            for r in (await s.execute(select(model).where(
                    model.tenant_id == tenant_id))).scalars().all():
                await s.delete(r)
        await s.commit()
        for r in (await s.execute(select(Payable).where(
                Payable.tenant_id == tenant_id))).scalars().all():
            await s.delete(r)
        for r in (await s.execute(select(ApprovalPolicy).where(
                ApprovalPolicy.tenant_id == tenant_id))).scalars().all():
            await s.delete(r)
        await s.commit()


async def _active_vendor(tenant_id, actor, name="Acme Landscaping LLC", terms=30):
    """A vendor that has actually earned `active` — W-9, banking, verified callback."""
    async with SessionLocal() as s:
        existing = await pv.list_vendors(s, tenant_id, q=name)
        v = existing[0] if existing else await pv.create_vendor(
            s, tenant_id, actor, {"legal_name": name, "terms_days": terms})
        v = await pv.update_vendor(s, tenant_id, actor, v["id"],
                                   {"w9_document_id": uuid.uuid4(), "terms_days": terms})
        v = await pv.add_bank_account(s, tenant_id, actor, v["id"],
                                      {"routing_last4": "1111", "account_last4": "2222",
                                       "verified": True})
    assert v["status"] == "active"
    return v


async def _bands(tenant_id, actor, bands):
    async with SessionLocal() as s:
        await pay.replace_policies(s, tenant_id, actor, bands)


# ── the duplicate-payment control ─────────────────────────────────────────────────────────

async def test_the_same_invoice_twice_is_refused_and_the_error_names_the_first_one():
    tid, actor = await _ctx()
    await _reset(tid)
    v = await _active_vendor(tid, actor)
    async with SessionLocal() as s:
        await pay.create_payable(s, tid, actor, {
            "vendor_id": uuid.UUID(v["id"]), "invoice_number": "4471", "amount": 2450,
            "invoice_date": dt.date(2026, 9, 15)})
        with pytest.raises(ValueError) as e:
            await pay.create_payable(s, tid, actor, {
                "vendor_id": uuid.UUID(v["id"]), "invoice_number": "4471", "amount": 2450})
    msg = str(e.value)
    assert "4471" in msg and "Acme Landscaping LLC" in msg
    # It must say something about the one already there, or the reader cannot act on it.
    assert "2450" in msg.replace(",", "") or "2450.00" in msg


async def test_the_same_invoice_number_is_fine_for_a_different_vendor():
    tid, actor = await _ctx()
    await _reset(tid)
    a = await _active_vendor(tid, actor, "Acme Landscaping LLC")
    b = await _active_vendor(tid, actor, "Brightpath Creative")
    async with SessionLocal() as s:
        await pay.create_payable(s, tid, actor, {
            "vendor_id": uuid.UUID(a["id"]), "invoice_number": "100", "amount": 10})
        made = await pay.create_payable(s, tid, actor, {
            "vendor_id": uuid.UUID(b["id"]), "invoice_number": "100", "amount": 20})
    assert made["invoice_number"] == "100"


# ── the approval matrix ───────────────────────────────────────────────────────────────────

async def test_bands_are_inclusive_at_both_edges():
    """A matrix with exclusive edges leaves a value in no band, and the value it leaves out is
    always the round number somebody actually invoices."""
    tid, actor = await _ctx()
    await _reset(tid)
    await _bands(tid, actor, [
        {"label": "Up to 2,500", "min_amount": 0, "max_amount": 2500},
        {"label": "2,501 to 15,000", "min_amount": 2501, "max_amount": 15000},
        {"label": "Over 15,000", "min_amount": 15001, "max_amount": None},
    ])
    async with SessionLocal() as s:
        assert (await pay.resolve_band(s, tid, None, 0))["band"] == "Up to 2,500"
        assert (await pay.resolve_band(s, tid, None, 2500))["band"] == "Up to 2,500"
        assert (await pay.resolve_band(s, tid, None, 2501))["band"] == "2,501 to 15,000"
        assert (await pay.resolve_band(s, tid, None, 15000))["band"] == "2,501 to 15,000"
        assert (await pay.resolve_band(s, tid, None, 15001))["band"] == "Over 15,000"
        # The top band has no ceiling, so nothing is ever larger than the matrix.
        assert (await pay.resolve_band(s, tid, None, 10_000_000))["band"] == "Over 15,000"


async def test_overlapping_bands_resolve_the_same_way_every_time():
    tid, actor = await _ctx()
    await _reset(tid)
    await _bands(tid, actor, [
        {"label": "Wide", "min_amount": 0, "max_amount": 50000},
        {"label": "Narrow", "min_amount": 10000, "max_amount": 20000},
    ])
    async with SessionLocal() as s:
        first = await pay.resolve_band(s, tid, None, 15000)
        again = await pay.resolve_band(s, tid, None, 15000)
    # The narrower band wins, and it wins identically on a second call — the answer cannot
    # depend on the order rows came back in.
    assert first["band"] == "Narrow" == again["band"]


async def test_an_amount_in_no_band_refuses_rather_than_sailing_through():
    tid, actor = await _ctx()
    await _reset(tid)
    await _bands(tid, actor, [{"label": "Small only", "min_amount": 0, "max_amount": 100}])
    v = await _active_vendor(tid, actor)
    async with SessionLocal() as s:
        p = await pay.create_payable(s, tid, actor, {
            "vendor_id": uuid.UUID(v["id"]), "invoice_number": "BIG-1", "amount": 99999,
            "standard_account_id": uuid.uuid4()})
        with pytest.raises(ValueError) as e:
            await pay.submit_for_approval(s, tid, actor, p["id"])
    assert "no approval policy" in str(e.value).lower()


# ── signatures ────────────────────────────────────────────────────────────────────────────

async def test_two_required_approvers_means_one_is_not_enough():
    tid, actor = await _ctx()
    second = await _second_user(tid)
    await _reset(tid)
    await _bands(tid, actor, [{"label": "Needs two", "min_amount": 0, "max_amount": None,
                               "required_user_ids": [str(actor.id), str(second.id)]}])
    v = await _active_vendor(tid, actor)
    async with SessionLocal() as s:
        p = await pay.create_payable(s, tid, actor, {
            "vendor_id": uuid.UUID(v["id"]), "invoice_number": "TWO-1", "amount": 9000,
            "standard_account_id": uuid.uuid4()})
        p = await pay.submit_for_approval(s, tid, actor, p["id"])
        assert len(p["approvals"]) == 2
        p = await pay.decide(s, tid, actor, p["id"], "approve")
        assert p["status"] == "awaiting_approval", "one signature is not two"
        p = await pay.decide(s, tid, second, p["id"], "approve")
    assert p["status"] == "approved"


async def test_one_person_cannot_supply_both_signatures():
    tid, actor = await _ctx()
    second = await _second_user(tid)
    await _reset(tid)
    await _bands(tid, actor, [{"label": "Needs two", "min_amount": 0, "max_amount": None,
                               "required_user_ids": [str(actor.id), str(second.id)]}])
    v = await _active_vendor(tid, actor)
    async with SessionLocal() as s:
        p = await pay.create_payable(s, tid, actor, {
            "vendor_id": uuid.UUID(v["id"]), "invoice_number": "SOLO-1", "amount": 9000,
            "standard_account_id": uuid.uuid4()})
        p = await pay.submit_for_approval(s, tid, actor, p["id"])
        await pay.decide(s, tid, actor, p["id"], "approve")
        with pytest.raises(ValueError) as e:
            await pay.decide(s, tid, actor, p["id"], "approve")
    assert "already decided" in str(e.value)


async def test_a_rejection_stops_the_payable_dead():
    tid, actor = await _ctx()
    await _reset(tid)
    await _bands(tid, actor, [{"label": "Any", "min_amount": 0, "max_amount": None}])
    v = await _active_vendor(tid, actor)
    async with SessionLocal() as s:
        p = await pay.create_payable(s, tid, actor, {
            "vendor_id": uuid.UUID(v["id"]), "invoice_number": "REJ-1", "amount": 500,
            "standard_account_id": uuid.uuid4()})
        p = await pay.submit_for_approval(s, tid, actor, p["id"])
        p = await pay.decide(s, tid, actor, p["id"], "reject", note="not ours")
    assert p["status"] == "rejected"


# ── the vendor gate ───────────────────────────────────────────────────────────────────────

async def test_an_unverified_vendor_blocks_submission_and_says_what_is_missing():
    tid, actor = await _ctx()
    await _reset(tid)
    await _bands(tid, actor, [{"label": "Any", "min_amount": 0, "max_amount": None}])
    async with SessionLocal() as s:
        v = await pv.create_vendor(s, tid, actor, {"legal_name": "Rivera, Marcos"})
        p = await pay.create_payable(s, tid, actor, {
            "vendor_id": uuid.UUID(v["id"]), "invoice_number": "MR-0912", "amount": 3800,
            "standard_account_id": uuid.uuid4()})
        with pytest.raises(ValueError) as e:
            await pay.submit_for_approval(s, tid, actor, p["id"])
    msg = str(e.value)
    assert "Rivera, Marcos" in msg
    # Naming the missing piece is the difference between a blocked user and a guessing one.
    assert "W-9" in msg and "banking" in msg


# ── the timeline ──────────────────────────────────────────────────────────────────────────

async def test_every_transition_writes_exactly_one_event():
    tid, actor = await _ctx()
    await _reset(tid)
    await _bands(tid, actor, [{"label": "Any", "min_amount": 0, "max_amount": None}])
    v = await _active_vendor(tid, actor)
    async with SessionLocal() as s:
        p = await pay.create_payable(s, tid, actor, {
            "vendor_id": uuid.UUID(v["id"]), "invoice_number": "TL-1", "amount": 250,
            "standard_account_id": uuid.uuid4()})
    pid = uuid.UUID(p["id"])

    async def _events():
        async with SessionLocal() as s:
            return (await s.execute(select(PayableEvent).where(
                PayableEvent.payable_id == pid))).scalars().all()

    # received (genesis) + coded (a vendor default was applied at creation)
    assert [e.event for e in await _events()] == ["received", "coded"]
    async with SessionLocal() as s:
        await pay.submit_for_approval(s, tid, actor, p["id"])
    assert [e.event for e in await _events()] == ["received", "coded", "awaiting_approval"]
    async with SessionLocal() as s:
        await pay.decide(s, tid, actor, p["id"], "approve")
    assert [e.event for e in await _events()] == \
        ["received", "coded", "awaiting_approval", "approved"]
    async with SessionLocal() as s:
        timeline = await pay.payable_timeline(s, tid, pid)
    assert [t["event"] for t in timeline][-1] == "approved"
    assert timeline[-1]["payload"]["from"] == "awaiting_approval"


async def test_an_impossible_transition_is_refused():
    tid, actor = await _ctx()
    await _reset(tid)
    v = await _active_vendor(tid, actor)
    async with SessionLocal() as s:
        row = await pay.create_payable(s, tid, actor, {
            "vendor_id": uuid.UUID(v["id"]), "invoice_number": "JUMP-1", "amount": 10})
        p = (await s.execute(select(Payable).where(
            Payable.id == uuid.UUID(row["id"])))).scalar_one()
        with pytest.raises(pay.TransitionError):
            pay.transition(s, p, "released", actor)      # received -> released skips everything


# ── terms ─────────────────────────────────────────────────────────────────────────────────

async def test_the_due_date_comes_from_the_vendor_terms():
    tid, actor = await _ctx()
    await _reset(tid)
    v = await _active_vendor(tid, actor, "Copperfield Signs", terms=15)
    async with SessionLocal() as s:
        p = await pay.create_payable(s, tid, actor, {
            "vendor_id": uuid.UUID(v["id"]), "invoice_number": "CS-311", "amount": 1875,
            "invoice_date": dt.date(2026, 9, 14)})
    assert p["due_date"] == "2026-09-29"       # 14 Sept + Net 15


async def test_overriding_the_due_date_is_audited():
    """Paying off terms is a decision about cash, not a typo to absorb silently."""
    tid, actor = await _ctx()
    await _reset(tid)
    v = await _active_vendor(tid, actor)
    async with SessionLocal() as s:
        p = await pay.create_payable(s, tid, actor, {
            "vendor_id": uuid.UUID(v["id"]), "invoice_number": "OVR-1", "amount": 100,
            "invoice_date": dt.date(2026, 9, 1), "due_date": dt.date(2026, 9, 3)})
    assert p["due_date"] == "2026-09-03"
    async with SessionLocal() as s:
        rows = (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == tid, AuditLog.target_id == p["id"]))).scalars().all()
    overrides = [r for r in rows if r.action == "payables.due_date_overridden"]
    assert overrides, "an off-terms due date must leave a trail"
    assert {r.category for r in rows} == {"Payments"}


# ── holds: the one list every disable reads (§3.5) ────────────────────────────────────────

async def test_a_ready_payable_says_so_and_a_held_one_says_why():
    tid, actor = await _ctx()
    await _reset(tid)
    await _bands(tid, actor, [{"label": "Any", "min_amount": 0, "max_amount": None}])
    good = await _active_vendor(tid, actor)
    async with SessionLocal() as s:
        bad_vendor = await pv.create_vendor(s, tid, actor, {"legal_name": "Unverified Co"})
        ready = await pay.create_payable(s, tid, actor, {
            "vendor_id": uuid.UUID(good["id"]), "invoice_number": "OK-1", "amount": 100,
            "standard_account_id": uuid.uuid4()})
        uncoded = await pay.create_payable(s, tid, actor, {
            "vendor_id": uuid.UUID(good["id"]), "invoice_number": "RAW-1", "amount": 100})
        unverified = await pay.create_payable(s, tid, actor, {
            "vendor_id": uuid.UUID(bad_vendor["id"]), "invoice_number": "UV-1", "amount": 100,
            "standard_account_id": uuid.uuid4()})

    assert ready["can_submit"] is True and ready["holds"] == []
    assert uncoded["can_submit"] is False
    assert "uncoded" in {h["key"] for h in uncoded["holds"]}
    assert "vendor_not_ready" in {h["key"] for h in unverified["holds"]}
    # Every hold explains itself. A disabled button with no reason is a support ticket.
    assert all(h["why"] for h in uncoded["holds"] + unverified["holds"])


async def test_no_band_shows_as_a_hold_before_anybody_clicks_submit():
    """The refusal in submit_for_approval is the backstop; the hold is what stops the click."""
    tid, actor = await _ctx()
    await _reset(tid)
    await _bands(tid, actor, [{"label": "Small only", "min_amount": 0, "max_amount": 100}])
    v = await _active_vendor(tid, actor)
    async with SessionLocal() as s:
        p = await pay.create_payable(s, tid, actor, {
            "vendor_id": uuid.UUID(v["id"]), "invoice_number": "NB-1", "amount": 50000,
            "standard_account_id": uuid.uuid4()})
    assert p["can_submit"] is False
    assert "no_band" in {h["key"] for h in p["holds"]}


# ── isolation ─────────────────────────────────────────────────────────────────────────────

async def test_payables_never_cross_a_tenant_boundary():
    tid, actor = await _ctx()
    await _reset(tid)
    v = await _active_vendor(tid, actor)
    async with SessionLocal() as s:
        p = await pay.create_payable(s, tid, actor, {
            "vendor_id": uuid.UUID(v["id"]), "invoice_number": "ISO-1", "amount": 42})
    other = uuid.uuid4()
    async with SessionLocal() as s:
        assert await pay.list_payables(s, other) == []
        assert await pay.get_payable(s, other, p["id"]) is None
        assert await pay.update_coding(s, other, actor, p["id"], {"class_key": "x"}) is None
        assert await pay.submit_for_approval(s, other, actor, p["id"]) is None
