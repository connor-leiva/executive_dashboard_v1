"""Payables API (SPEC-payables §2.3, §3.4). Vendor master, bills, and approvals.

Gated on `require_tab("books")`. Payables is a Books sub-surface, and `tabs.py` already resolves
any `books_*` grant to the books tab, so a `books_payables` grant works with no resolver change.

Release (Phase 3) is the only route that will carry the step-up variant. Nothing here moves
money, so nothing here demands a second factor — asking for one on vendor edits would train
people to type the code without reading why, which is how the factor stops working.
"""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import require_tab
from ..models import User
from ..services import binder_ingest, payables, payables_vendor

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


# ── Bills and approvals (SPEC-payables §3.4) ──────────────────────────────────
# ROUTE ORDER MATTERS. `/policies` is declared before `/{payable_id}`, or FastAPI tries to
# parse "policies" as a UUID and answers 422 — a 422 on a working endpoint is a bad afternoon.

class PayableIn(BaseModel):
    vendor_id: uuid.UUID
    invoice_number: str
    amount: float
    invoice_date: date | None = None
    due_date: date | None = None                 # an override; derived from terms when omitted
    currency: str = "USD"
    description: str | None = None
    business_id: uuid.UUID | None = None
    legal_entity_id: uuid.UUID | None = None
    standard_account_id: uuid.UUID | None = None
    class_key: str | None = None
    location_key: str | None = None
    document_id: uuid.UUID | None = None


class PayablePatch(BaseModel):
    invoice_date: date | None = None
    due_date: date | None = None
    description: str | None = None
    business_id: uuid.UUID | None = None
    legal_entity_id: uuid.UUID | None = None
    standard_account_id: uuid.UUID | None = None
    class_key: str | None = None
    location_key: str | None = None
    service_period_start: date | None = None
    service_period_end: date | None = None
    is_exception: bool | None = None
    exception_reason: str | None = None


class DecideIn(BaseModel):
    decision: str                                 # approve | reject | request_info
    note: str | None = None


class PolicyIn(BaseModel):
    label: str
    business_id: uuid.UUID | None = None
    min_amount: float = 0
    max_amount: float | None = None               # null = no ceiling; the top band must be open
    required_role: str | None = None
    required_user_ids: list[uuid.UUID] = []
    requires_second_approver: bool = False
    active: bool = True


class PoliciesIn(BaseModel):
    bands: list[PolicyIn]


@router.get("")
async def list_payables(status: str | None = None, mine: int = 0,
                        user: User = Depends(payables_user),
                        s: AsyncSession = Depends(get_session)):
    """`mine=1` is the Approvals screen's default. An approver who has to read everyone else's
    list to find their three stops approving."""
    rows = await payables.list_payables(s, user.tenant_id, status=status,
                                        assigned_to=(user.id if mine else None))
    return {"payables": rows}


@router.post("")
async def create_payable(body: PayableIn, user: User = Depends(payables_user),
                         s: AsyncSession = Depends(get_session)):
    try:
        return await payables.create_payable(
            s, user.tenant_id, user, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/upload")
async def upload_payable(file: UploadFile = File(...), user: User = Depends(payables_user),
                         s: AsyncSession = Depends(get_session)):
    """Store an invoice document and hand back the reference. Phase 2 stops here: extraction is
    Phase 4, so a person creates the payable against this document rather than the machine."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
    try:
        doc, created = await binder_ingest.ingest_document(
            s, user.tenant_id, user, filename=file.filename or "invoice.pdf", data=data,
            uploaded_via="upload", entity_id=None, category="other")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"document_id": str(doc.id), "filename": doc.filename, "deduped": not created}


@router.get("/policies")
async def get_policies(user: User = Depends(payables_user),
                       s: AsyncSession = Depends(get_session)):
    return {"bands": await payables.list_policies(s, user.tenant_id)}


@router.put("/policies")
async def put_policies(body: PoliciesIn, user: User = Depends(payables_user),
                       s: AsyncSession = Depends(get_session)):
    """The matrix is replaced whole. Editing band by band invites a moment where two overlap or
    a gap opens, and the gap is what lets an invoice through with nobody required."""
    try:
        bands = await payables.replace_policies(
            s, user.tenant_id, user,
            [b.model_dump() for b in body.bands])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"bands": bands}


@router.get("/{payable_id}")
async def get_payable(payable_id: uuid.UUID, user: User = Depends(payables_user),
                      s: AsyncSession = Depends(get_session)):
    row = await payables.get_payable(s, user.tenant_id, payable_id)
    if row is None:
        raise HTTPException(404, "Payable not found")
    return {**row, "timeline": await payables.payable_timeline(s, user.tenant_id, payable_id)}


@router.patch("/{payable_id}")
async def update_coding(payable_id: uuid.UUID, body: PayablePatch,
                        user: User = Depends(payables_user),
                        s: AsyncSession = Depends(get_session)):
    try:
        row = await payables.update_coding(
            s, user.tenant_id, user, payable_id, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if row is None:
        raise HTTPException(404, "Payable not found")
    return row


@router.post("/{payable_id}/submit")
async def submit(payable_id: uuid.UUID, user: User = Depends(payables_user),
                 s: AsyncSession = Depends(get_session)):
    try:
        row = await payables.submit_for_approval(s, user.tenant_id, user, payable_id)
    except ValueError as e:
        # The message names what the vendor is missing, or which band is absent. The screen
        # shows it verbatim — this is the sentence that tells somebody what to do next.
        raise HTTPException(400, str(e))
    if row is None:
        raise HTTPException(404, "Payable not found")
    return row


@router.post("/{payable_id}/decide")
async def decide(payable_id: uuid.UUID, body: DecideIn, user: User = Depends(payables_user),
                 s: AsyncSession = Depends(get_session)):
    try:
        row = await payables.decide(s, user.tenant_id, user, payable_id, body.decision, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if row is None:
        raise HTTPException(404, "Payable not found")
    return row
