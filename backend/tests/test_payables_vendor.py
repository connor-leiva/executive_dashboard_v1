"""Payables Phase 1 — the vendor master (SPEC-payables §2.6).

The gate on a first payment. Most of these assert a rule that decides whether money may move,
so they are written against the rule itself rather than through the API where a 200 could hide
a wrong status.
"""
import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant, User, Vendor, VendorBankAccount, AuditLog
from app.services import payables_vendor as pv
from app.services.audit import AUDIT_CATEGORIES
from app.services.binder_extract import _AUTO_LINK


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _ctx():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = (await s.execute(select(User).where(User.tenant_id == t.id))).scalars().first()
        return t.id, u


async def _other_tenant():
    """A second tenant, created directly — isolation is only proved against a real neighbour."""
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "payables-neighbour"))
             ).scalar_one_or_none()
        if t is None:
            t = Tenant(slug="payables-neighbour", name="Neighbour Co")
            s.add(t)
            await s.commit()
        return t.id


async def _clean(tenant_id):
    async with SessionLocal() as s:
        for b in (await s.execute(select(VendorBankAccount).where(
                VendorBankAccount.tenant_id == tenant_id))).scalars().all():
            await s.delete(b)
        await s.commit()
        for v in (await s.execute(select(Vendor).where(
                Vendor.tenant_id == tenant_id))).scalars().all():
            await s.delete(v)
        await s.commit()


# ── the audit vocabulary (landmine 1.1) ───────────────────────────────────────────────────

def test_payments_is_a_known_audit_category():
    """The CHECK is Postgres-only and the suite runs SQLite, so this assertion is the only thing
    standing between a missing category and a 500 on every payables mutation in production."""
    assert "Payments" in AUDIT_CATEGORIES


# ── derive_status: the gate, as a pure function ───────────────────────────────────────────

def _vendor(**kw):
    base = dict(legal_name="Acme", name_norm="acme", display_name="Acme", status="draft")
    base.update(kw)
    return Vendor(**base)


def _bank(verified: bool):
    return VendorBankAccount(
        routing_last4="1234", account_last4="5678",
        verified_at=dt.datetime.now(dt.timezone.utc) if verified else None)


def test_no_w9_is_never_active():
    assert pv.derive_status(_vendor(), _bank(True)) == "pending_verification"


def test_w9_without_verified_banking_is_never_active():
    v = _vendor(w9_document_id=uuid.uuid4())
    assert pv.derive_status(v, None) == "pending_verification"
    assert pv.derive_status(v, _bank(False)) == "pending_verification"


def test_active_requires_all_three():
    v = _vendor(w9_document_id=uuid.uuid4())
    assert pv.derive_status(v, _bank(True)) == "active"


def test_inactive_is_sticky_because_a_human_set_it():
    v = _vendor(w9_document_id=uuid.uuid4(), status="inactive")
    assert pv.derive_status(v, _bank(True)) == "inactive"


# ── banking is append-only ────────────────────────────────────────────────────────────────

async def test_adding_banking_supersedes_and_the_prior_row_keeps_its_verification():
    tid, actor = await _ctx()
    await _clean(tid)
    async with SessionLocal() as s:
        v = await pv.create_vendor(s, tid, actor, {"legal_name": "Copperfield Signs"})
        await pv.add_bank_account(s, tid, actor, v["id"],
                                  {"routing_last4": "1111", "account_last4": "2222",
                                   "verified": True, "verification_note": "called the invoice number"})
        after = await pv.add_bank_account(s, tid, actor, v["id"],
                                          {"routing_last4": "3333", "account_last4": "4444",
                                           "verified": True})
    assert after["bank"]["account_last4"] == "4444"
    async with SessionLocal() as s:
        rows = (await s.execute(select(VendorBankAccount).where(
            VendorBankAccount.vendor_id == uuid.UUID(v["id"]))
            .order_by(VendorBankAccount.created_at))).scalars().all()
    assert len(rows) == 2, "a bank change must ADD a row, never update one"
    prior, current = rows
    assert prior.active is False and current.active is True
    assert prior.superseded_by == current.id
    # The prior verification genuinely happened; blanking it would rewrite history.
    assert prior.verified_at is not None
    assert prior.verification_note == "called the invoice number"


async def test_status_flips_to_active_on_its_own_once_the_three_conditions_hold():
    """§2.7's done-when, end to end: no human types `active` anywhere in this test."""
    tid, actor = await _ctx()
    await _clean(tid)
    async with SessionLocal() as s:
        v = await pv.create_vendor(s, tid, actor, {"legal_name": "Brightpath Creative"})
        assert v["status"] == "pending_verification"
        v = await pv.update_vendor(s, tid, actor, v["id"], {"w9_document_id": uuid.uuid4()})
        assert v["status"] == "pending_verification", "a W-9 alone is not enough"
        v = await pv.add_bank_account(s, tid, actor, v["id"],
                                      {"routing_last4": "1111", "account_last4": "2222"})
        assert v["status"] == "pending_verification", "unverified banking is not enough"
        v = await pv.add_bank_account(s, tid, actor, v["id"],
                                      {"routing_last4": "1111", "account_last4": "2222",
                                       "verified": True})
    assert v["status"] == "active"


async def test_a_human_cannot_simply_declare_a_vendor_active():
    tid, actor = await _ctx()
    await _clean(tid)
    async with SessionLocal() as s:
        v = await pv.create_vendor(s, tid, actor, {"legal_name": "Wishful Thinking LLC"})
        v = await pv.update_vendor(s, tid, actor, v["id"], {"status": "active"})
    assert v["status"] == "pending_verification"


# ── duplicates ────────────────────────────────────────────────────────────────────────────

async def test_duplicate_name_is_rejected_after_normalization_and_names_the_clash():
    tid, actor = await _ctx()
    await _clean(tid)
    async with SessionLocal() as s:
        await pv.create_vendor(s, tid, actor, {"legal_name": "Acme Landscaping, LLC"})
        with pytest.raises(ValueError) as e:
            await pv.create_vendor(s, tid, actor, {"legal_name": "acme landscaping llc"})
    # The message has to identify WHICH vendor collided — "duplicate" alone sends somebody
    # hunting for a row the error will not show them.
    assert "Acme Landscaping, LLC" in str(e.value)


# ── matching ──────────────────────────────────────────────────────────────────────────────

def _vend(name, dba=None):
    return Vendor(id=uuid.uuid4(), legal_name=name, display_name=name, dba=dba,
                  name_norm="", status="active")


def test_match_vendor_auto_links_a_confident_name():
    vendors = [_vend("Acme Landscaping LLC"), _vend("Brightpath Creative"),
               _vend("Northline Title Co")]
    r = pv.match_vendor("Acme Landscaping, LLC", vendors)
    assert r["confidence"] >= _AUTO_LINK
    assert r["ambiguous"] is False
    assert r["vendor_id"] == str(vendors[0].id)


def test_match_vendor_flags_a_guess_it_cannot_stand_behind():
    vendors = [_vend("Acme Landscaping LLC"), _vend("Brightpath Creative")]
    r = pv.match_vendor("Rivera, Marcos", vendors)
    assert r["ambiguous"] is True, "an unknown payee must ask rather than link"
    assert r["candidates"], "the human still needs something to pick from"


def test_match_vendor_reads_the_dba_too():
    vendors = [_vend("Halcyon Systems LLC", dba="Halcyon Cloud")]
    r = pv.match_vendor("Halcyon Cloud", vendors)
    assert r["vendor_id"] == str(vendors[0].id)
    assert r["confidence"] >= _AUTO_LINK


# ── tenant isolation ──────────────────────────────────────────────────────────────────────

async def test_vendors_never_cross_a_tenant_boundary():
    tid, actor = await _ctx()
    other = await _other_tenant()
    await _clean(tid)
    await _clean(other)
    async with SessionLocal() as s:
        mine = await pv.create_vendor(s, tid, actor, {"legal_name": "Northline Title Co"})
    async with SessionLocal() as s:
        assert await pv.list_vendors(s, other) == []
        assert await pv.get_vendor(s, other, mine["id"]) is None
        # And the neighbour cannot mutate it either.
        assert await pv.update_vendor(s, other, actor, mine["id"], {"terms_days": 1}) is None
        assert await pv.add_bank_account(s, other, actor, mine["id"],
                                         {"routing_last4": "9999", "account_last4": "8888"}) is None


async def test_the_same_name_is_allowed_in_two_different_tenants():
    """The uniqueness constraint is per tenant. Two customers may both pay an Acme."""
    tid, actor = await _ctx()
    other = await _other_tenant()
    await _clean(tid)
    await _clean(other)
    async with SessionLocal() as s:
        await pv.create_vendor(s, tid, actor, {"legal_name": "Acme Landscaping LLC"})
        made = await pv.create_vendor(s, other, actor, {"legal_name": "Acme Landscaping LLC"})
    assert made["legal_name"] == "Acme Landscaping LLC"


# ── audit ─────────────────────────────────────────────────────────────────────────────────

async def test_every_vendor_mutation_audits_under_payments():
    tid, actor = await _ctx()
    await _clean(tid)
    async with SessionLocal() as s:
        v = await pv.create_vendor(s, tid, actor, {"legal_name": "Audited Vendor LLC"})
        await pv.update_vendor(s, tid, actor, v["id"], {"terms_days": 15})
        await pv.add_bank_account(s, tid, actor, v["id"],
                                  {"routing_last4": "1111", "account_last4": "2222"})
    async with SessionLocal() as s:
        rows = (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == tid, AuditLog.target_id == v["id"]))).scalars().all()
    actions = {r.action for r in rows}
    assert {"payables.vendor_created", "payables.vendor_updated",
            "payables.vendor_bank_added"} <= actions
    assert {r.category for r in rows} == {"Payments"}
