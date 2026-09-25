"""Payables · vendor master (SPEC-payables §2.2).

The gate on a first payment. Books is read-only over QuickBooks and treats a payee as free
text; here a payee is a row that has to earn `active` before money can be scheduled against it.

Two rules carry most of the weight:

`derive_status` is computed, never assigned. A status somebody can type is a status somebody
types on a Friday afternoon to unblock a payment, which is precisely the control this module
exists to be.

`add_bank_account` supersedes and never updates. The history of where money was sent is the
evidence; an UPDATE erases it on exactly the occasion anyone would want to read it.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Business, User, Vendor, VendorBankAccount
from .audit import audit
from .binder_extract import match_names, normalize_name

VENDOR_TYPES = ("business", "individual")
# draft is the column default for a row that has not been derived yet; derive_status never
# returns it. `inactive` is the only value a human sets directly.
VENDOR_STATUSES = ("draft", "pending_verification", "active", "inactive")


def derive_status(vendor: Vendor, bank: VendorBankAccount | None) -> str:
    """`active` only when all three hold: a W-9 on file, an active bank row, and that row
    carrying a verification. Anything less is `pending_verification`.

    Deliberately takes the bank row rather than reading it, so the rule is testable as a pure
    function — this is the one piece of logic in the module that decides whether money may move.
    """
    if vendor.status == "inactive":          # a human took them out of service; sticky
        return "inactive"
    if vendor.w9_document_id and bank is not None and bank.verified_at is not None:
        return "active"
    return "pending_verification"


async def _active_bank(s: AsyncSession, tenant_id, vendor_id) -> VendorBankAccount | None:
    return (await s.execute(select(VendorBankAccount).where(
        VendorBankAccount.tenant_id == tenant_id,
        VendorBankAccount.vendor_id == vendor_id,
        VendorBankAccount.active.is_(True))
        .order_by(VendorBankAccount.created_at.desc()))).scalars().first()


async def _labels(s: AsyncSession, tenant_id) -> tuple[dict, dict]:
    """Business keys and reviewer names, resolved once per request.

    The screen shows "Spring B" and "K. Shaw", not two UUIDs. Sending identity WITH the payload
    is the same rule the Books queue follows — a frontend that resolves ids itself needs its own
    copy of who the businesses are, and that copy is what breaks for the second tenant.
    """
    biz = {b.id: {"key": b.key, "name": b.name} for b in (await s.execute(
        select(Business).where(Business.tenant_id == tenant_id))).scalars()}
    users = {u.id: (u.name or u.email) for u in (await s.execute(
        select(User).where(User.tenant_id == tenant_id))).scalars()}
    return biz, users


def _bank_row(b: VendorBankAccount | None, umap: dict) -> dict | None:
    if b is None:
        return None
    return {"id": str(b.id), "routing_last4": b.routing_last4, "account_last4": b.account_last4,
            "verified_at": b.verified_at.isoformat() if b.verified_at else None,
            "verified_by": str(b.verified_by) if b.verified_by else None,
            "verified_by_name": umap.get(b.verified_by),
            "verification_method": b.verification_method,
            "verification_note": b.verification_note,
            "created_at": b.created_at.isoformat() if b.created_at else None}


def _row(v: Vendor, bank: VendorBankAccount | None,
         bizmap: dict | None = None, umap: dict | None = None) -> dict:
    return {
        "id": str(v.id), "legal_name": v.legal_name, "display_name": v.display_name,
        "dba": v.dba, "vendor_type": v.vendor_type, "tin_last4": v.tin_last4,
        "w9_document_id": str(v.w9_document_id) if v.w9_document_id else None,
        "w9": bool(v.w9_document_id),
        "w9_received_at": v.w9_received_at.isoformat() if v.w9_received_at else None,
        "is_1099": bool(v.is_1099), "terms_days": v.terms_days,
        "default_standard_account_id": (str(v.default_standard_account_id)
                                        if v.default_standard_account_id else None),
        "default_business_id": str(v.default_business_id) if v.default_business_id else None,
        "default_business_key": (bizmap or {}).get(v.default_business_id, {}).get("key"),
        "default_business_name": (bizmap or {}).get(v.default_business_id, {}).get("name"),
        "default_legal_entity_id": (str(v.default_legal_entity_id)
                                    if v.default_legal_entity_id else None),
        # Reported from the rule, not from the column, so a stale stored value can never be what
        # the screen shows. The column is kept in step on every write for querying/filtering.
        "status": derive_status(v, bank),
        "notes": v.notes, "bank": _bank_row(bank, umap or {}),
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


async def list_vendors(s: AsyncSession, tenant_id, *, status: str | None = None,
                       q: str | None = None) -> list[dict]:
    conds = [Vendor.tenant_id == tenant_id]
    if q:
        # Matched against the normalized column so "Acme Landscaping, LLC" finds "acme
        # landscaping" — the same normalization the uniqueness constraint uses.
        nq = normalize_name(q)
        if nq:
            conds.append(Vendor.name_norm.contains(nq))
    vendors = (await s.execute(select(Vendor).where(*conds)
                               .order_by(Vendor.display_name))).scalars().all()
    # One query for every active bank row rather than one per vendor. A vendor list is the
    # screen's first paint and it is the place an N+1 would be felt.
    banks = {}
    for b in (await s.execute(select(VendorBankAccount).where(
            VendorBankAccount.tenant_id == tenant_id,
            VendorBankAccount.active.is_(True))
            .order_by(VendorBankAccount.created_at))).scalars():
        banks[b.vendor_id] = b            # later row wins, matching _active_bank's ordering
    bizmap, umap = await _labels(s, tenant_id)
    out = []
    for v in vendors:
        row = _row(v, banks.get(v.id), bizmap, umap)
        if status and row["status"] != status:
            continue                      # filter on the DERIVED status, not the column
        out.append(row)
    return out


async def get_vendor(s: AsyncSession, tenant_id, vendor_id) -> dict | None:
    v = (await s.execute(select(Vendor).where(
        Vendor.tenant_id == tenant_id, Vendor.id == vendor_id))).scalar_one_or_none()
    if v is None:
        return None
    bizmap, umap = await _labels(s, tenant_id)
    return _row(v, await _active_bank(s, tenant_id, v.id), bizmap, umap)


async def _clashing(s: AsyncSession, tenant_id, name_norm: str, exclude_id=None) -> Vendor | None:
    conds = [Vendor.tenant_id == tenant_id, Vendor.name_norm == name_norm]
    if exclude_id is not None:
        conds.append(Vendor.id != exclude_id)
    return (await s.execute(select(Vendor).where(*conds))).scalars().first()


_EDITABLE = ("display_name", "dba", "vendor_type", "tin_last4", "w9_document_id",
             "w9_received_at", "is_1099", "default_standard_account_id", "default_business_id",
             "default_legal_entity_id", "terms_days", "notes")


async def create_vendor(s: AsyncSession, tenant_id, actor, payload: dict) -> dict:
    legal_name = (payload.get("legal_name") or "").strip()
    if not legal_name:
        raise ValueError("legal_name is required — it must read exactly as on the W-9")
    vendor_type = payload.get("vendor_type") or "business"
    if vendor_type not in VENDOR_TYPES:
        raise ValueError(f"vendor_type must be one of {', '.join(VENDOR_TYPES)}")
    name_norm = normalize_name(legal_name)
    clash = await _clashing(s, tenant_id, name_norm)
    if clash is not None:
        # Name the existing vendor. "Duplicate" alone sends someone hunting for a row they
        # cannot see from the error.
        raise ValueError(f"{clash.legal_name} already exists with the same name")

    v = Vendor(tenant_id=tenant_id, legal_name=legal_name, name_norm=name_norm,
               display_name=(payload.get("display_name") or legal_name).strip(),
               vendor_type=vendor_type, status="pending_verification")
    for f in _EDITABLE:
        if f in payload and f not in ("display_name", "vendor_type"):
            setattr(v, f, payload[f])
    s.add(v)
    await s.flush()
    v.status = derive_status(v, None)
    audit(s, tenant_id, getattr(actor, "id", None), "payables.vendor_created",
          target_type="vendor", target_id=v.id, category="Payments",
          summary=f"Vendor {v.display_name} created",
          detail={"legal_name": v.legal_name, "vendor_type": v.vendor_type})
    await s.commit()
    return await get_vendor(s, tenant_id, v.id)


async def update_vendor(s: AsyncSession, tenant_id, actor, vendor_id, payload: dict) -> dict | None:
    v = (await s.execute(select(Vendor).where(
        Vendor.tenant_id == tenant_id, Vendor.id == vendor_id))).scalar_one_or_none()
    if v is None:
        return None
    changed = {}
    if "legal_name" in payload:
        legal_name = (payload["legal_name"] or "").strip()
        if not legal_name:
            raise ValueError("legal_name cannot be blank")
        name_norm = normalize_name(legal_name)
        clash = await _clashing(s, tenant_id, name_norm, exclude_id=v.id)
        if clash is not None:
            raise ValueError(f"{clash.legal_name} already exists with the same name")
        changed["legal_name"] = legal_name
        v.legal_name, v.name_norm = legal_name, name_norm
    if "vendor_type" in payload:
        if payload["vendor_type"] not in VENDOR_TYPES:
            raise ValueError(f"vendor_type must be one of {', '.join(VENDOR_TYPES)}")
        v.vendor_type = payload["vendor_type"]
        changed["vendor_type"] = v.vendor_type
    for f in _EDITABLE:
        if f in payload and f != "vendor_type":
            setattr(v, f, payload[f])
            changed[f] = payload[f]
    # `inactive` is the one status a human sets. Everything else is derived, so an attempt to
    # set `active` directly is ignored rather than honoured.
    if payload.get("status") in ("inactive", "pending_verification"):
        v.status = payload["status"]
        changed["status"] = payload["status"]
    v.updated_at = dt.datetime.now(dt.timezone.utc)
    bank = await _active_bank(s, tenant_id, v.id)
    v.status = derive_status(v, bank)
    audit(s, tenant_id, getattr(actor, "id", None), "payables.vendor_updated",
          target_type="vendor", target_id=v.id, category="Payments",
          summary=f"Vendor {v.display_name} updated", detail={"changed": sorted(changed)})
    await s.commit()
    return await get_vendor(s, tenant_id, v.id)


async def add_bank_account(s: AsyncSession, tenant_id, actor, vendor_id, payload: dict) -> dict | None:
    """A new row that supersedes the prior one. NEVER an UPDATE — see the module docstring."""
    v = (await s.execute(select(Vendor).where(
        Vendor.tenant_id == tenant_id, Vendor.id == vendor_id))).scalar_one_or_none()
    if v is None:
        return None
    routing = (payload.get("routing_last4") or "").strip()
    account = (payload.get("account_last4") or "").strip()
    if len(routing) != 4 or len(account) != 4 or not (routing.isdigit() and account.isdigit()):
        raise ValueError("routing_last4 and account_last4 must each be four digits")

    prior = await _active_bank(s, tenant_id, vendor_id)
    verified_at = None
    if payload.get("verified"):
        verified_at = dt.datetime.now(dt.timezone.utc)
    bank = VendorBankAccount(
        tenant_id=tenant_id, vendor_id=v.id, routing_last4=routing, account_last4=account,
        verification_method=(payload.get("verification_method") or "callback"),
        verification_note=payload.get("verification_note"),
        verified_at=verified_at,
        verified_by=(getattr(actor, "id", None) if verified_at else None))
    s.add(bank)
    await s.flush()
    if prior is not None:
        # The prior row keeps its own verified_at and note. It is the record of a verification
        # that genuinely happened; blanking it would rewrite history to match the present.
        prior.active = False
        prior.superseded_by = bank.id
    v.status = derive_status(v, bank)
    v.updated_at = dt.datetime.now(dt.timezone.utc)
    audit(s, tenant_id, getattr(actor, "id", None), "payables.vendor_bank_added",
          target_type="vendor", target_id=v.id, category="Payments",
          summary=f"Banking updated for {v.display_name}",
          detail={"account_last4": account, "verified": bool(verified_at),
                  "superseded": str(prior.id) if prior else None})
    await s.commit()
    return await get_vendor(s, tenant_id, v.id)


def match_vendor(guess: str | None, vendors: list) -> dict:
    """Fuzzy-match an extracted payee name against known vendors.

    Reuses binder_extract's scoring, so `_AUTO_LINK` is 0.80 here for the same reason it is
    there: one threshold, one place. Legal name, display name and DBA are all candidates —
    an invoice head can carry any of the three.
    """
    r = match_names(guess, [(v.id, v.display_name or v.legal_name, [v.legal_name, v.dba])
                            for v in vendors])
    return {"vendor_id": r["id"], "confidence": r["confidence"], "ambiguous": r["ambiguous"],
            "candidates": [{"vendor_id": c["id"], "name": c["name"], "score": c["score"]}
                           for c in r["candidates"]]}
