"""Payables API (SPEC-payables §2.3). Phase 1 — the vendor master.

Gated on `require_tab("books")`. Payables is a Books sub-surface, and `tabs.py` already resolves
any `books_*` grant to the books tab, so a `books_payables` grant works with no resolver change.

Release (Phase 3) is the only route that will carry the step-up variant. Nothing here moves
money, so nothing here demands a second factor — asking for one on vendor edits would train
people to type the code without reading why.
"""
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import require_tab
from ..models import User
from ..services import binder_ingest, payables_vendor

router = APIRouter(prefix="/payables", tags=["payables"])

payables_user = require_tab("books")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024        # matches Binder's limit; these are the same scans


class VendorIn(BaseModel):
    legal_name: str
    display_name: str | None = None
    dba: str | None = None
    vendor_type: str = "business"
    tin_last4: str | None = None
    is_1099: bool = False
    terms_days: int = 30
    default_standard_account_id: uuid.UUID | None = None
    default_business_id: uuid.UUID | None = None
    default_legal_entity_id: uuid.UUID | None = None
    notes: str | None = None


class VendorPatch(BaseModel):
    legal_name: str | None = None
    display_name: str | None = None
    dba: str | None = None
    vendor_type: str | None = None
    tin_last4: str | None = None
    is_1099: bool | None = None
    terms_days: int | None = None
    default_standard_account_id: uuid.UUID | None = None
    default_business_id: uuid.UUID | None = None
    default_legal_entity_id: uuid.UUID | None = None
    notes: str | None = None
    status: str | None = None              # only `inactive` / `pending_verification` are honoured


class BankIn(BaseModel):
    routing_last4: str
    account_last4: str
    verified: bool = False
    verification_method: str = "callback"
    verification_note: str | None = None


@router.get("/vendors")
async def list_vendors(status: str | None = None, q: str | None = None,
                       user: User = Depends(payables_user),
                       s: AsyncSession = Depends(get_session)):
    return {"vendors": await payables_vendor.list_vendors(s, user.tenant_id, status=status, q=q)}


@router.get("/vendors/{vendor_id}")
async def get_vendor(vendor_id: uuid.UUID, user: User = Depends(payables_user),
                     s: AsyncSession = Depends(get_session)):
    row = await payables_vendor.get_vendor(s, user.tenant_id, vendor_id)
    if row is None:
        raise HTTPException(404, "Vendor not found")
    return row


@router.post("/vendors")
async def create_vendor(body: VendorIn, user: User = Depends(payables_user),
                        s: AsyncSession = Depends(get_session)):
    try:
        return await payables_vendor.create_vendor(
            s, user.tenant_id, user, body.model_dump(exclude_none=True))
    except ValueError as e:
        # 400 with the message, not 409 with a code: the service names the colliding vendor and
        # the screen should be able to show that sentence verbatim.
        raise HTTPException(400, str(e))


@router.patch("/vendors/{vendor_id}")
async def update_vendor(vendor_id: uuid.UUID, body: VendorPatch,
                        user: User = Depends(payables_user),
                        s: AsyncSession = Depends(get_session)):
    try:
        row = await payables_vendor.update_vendor(
            s, user.tenant_id, user, vendor_id, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if row is None:
        raise HTTPException(404, "Vendor not found")
    return row


@router.post("/vendors/{vendor_id}/bank")
async def add_bank(vendor_id: uuid.UUID, body: BankIn, user: User = Depends(payables_user),
                   s: AsyncSession = Depends(get_session)):
    """Adds a NEW banking row and supersedes the prior one. There is no edit-banking route,
    because an edit is what would destroy the record of where money used to go."""
    try:
        row = await payables_vendor.add_bank_account(
            s, user.tenant_id, user, vendor_id, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    if row is None:
        raise HTTPException(404, "Vendor not found")
    return row


@router.post("/vendors/{vendor_id}/documents")
async def upload_w9(vendor_id: uuid.UUID, file: UploadFile = File(...),
                    user: User = Depends(payables_user),
                    s: AsyncSession = Depends(get_session)):
    """Attach a W-9. Stored through binder_ingest — the same blob store, hashing and dedup the
    Binder uses, rather than a second document path that would need its own retention story.

    The document is filed under the `tax` category with no entity: a W-9 belongs to the vendor,
    not to one of our legal entities.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
    try:
        doc, _created = await binder_ingest.ingest_document(
            s, user.tenant_id, user, filename=file.filename or "w9.pdf", data=data,
            uploaded_via="upload", entity_id=None, category="tax")
    except ValueError as e:
        raise HTTPException(400, str(e))
    row = await payables_vendor.update_vendor(
        s, user.tenant_id, user, vendor_id, {"w9_document_id": doc.id})
    if row is None:
        raise HTTPException(404, "Vendor not found")
    return row
