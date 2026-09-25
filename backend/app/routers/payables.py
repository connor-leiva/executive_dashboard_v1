"""Payables API (SPEC-payables §2.3, §3.4, §4.5). Vendors, bills, approvals and payment runs.

Gated on `require_tab("books")`. Payables is a Books sub-surface, and `tabs.py` already resolves
any `books_*` grant to the books tab, so a `books_payables` grant works with no resolver change.

Release is the ONE route carrying the step-up variant. Nothing else here moves money, and
asking for a code on vendor edits would train people to type it without reading why — which is
how a second factor stops being one.

ROUTE ORDER MATTERS in this file. `/policies` and everything under `/runs` are declared before
`/{payable_id}`, and `/runs/next` before `/runs/{run_id}`; FastAPI matches in order, so a
literal path declared after its parameterised sibling is a path that never runs.
"""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import require_tab, require_tab_with_step_up
from ..models import User
from ..services import binder_ingest, payables, payables_run, payables_vendor

router = APIRouter(prefix="/payables", tags=["payables"])

payables_user = require_tab("books")
# Release is the one action here that turns a batch into a payment instruction, so it carries
# the second factor. One symbol, per the docstring on require_tab_with_step_up: no route can be
# added to this section that quietly forgets it.
payables_releaser = require_tab_with_step_up("books", "payments")

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
    # No is_exception / exception_reason. Clearing a hold goes through
    # POST /runs/{id}/override-line, which requires a second person and a written reason.


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


# ── Payment runs (SPEC-payables §4.5) ─────────────────────────────────────────
# Declared BEFORE /{payable_id} for the same reason /policies is: "runs" is not a UUID.

class RunIn(BaseModel):
    business_id: uuid.UUID                      # one run per entity — separate realms, separate banks
    run_date: date | None = None


class HoldLineIn(BaseModel):
    payable_id: uuid.UUID
    reason: str


class OverrideLineIn(BaseModel):
    payable_id: uuid.UUID
    note: str


@router.get("/runs")
async def list_runs(business_id: uuid.UUID | None = None,
                    user: User = Depends(payables_user),
                    s: AsyncSession = Depends(get_session)):
    return {"runs": await payables_run.list_runs(s, user.tenant_id, business_id)}


@router.get("/runs/next")
async def next_run(business_id: uuid.UUID, run_date: date | None = None,
                   user: User = Depends(payables_user),
                   s: AsyncSession = Depends(get_session)):
    """Computed live, never stored. A proposal that went stale in a table would be read as
    fact, and the fact it would be read as is which bills are about to be paid."""
    return await payables_run.propose_run(s, user.tenant_id, business_id, run_date)


@router.post("/runs")
async def create_run(body: RunIn, user: User = Depends(payables_user),
                     s: AsyncSession = Depends(get_session)):
    try:
        return await payables_run.create_run(s, user.tenant_id, user, body.business_id,
                                             body.run_date)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/runs/{run_id}")
async def get_run(run_id: uuid.UUID, user: User = Depends(payables_user),
                  s: AsyncSession = Depends(get_session)):
    row = await payables_run.get_run(s, user.tenant_id, run_id)
    if row is None:
        raise HTTPException(404, "Run not found")
    return row


@router.post("/runs/{run_id}/hold-line")
async def hold_line(run_id: uuid.UUID, body: HoldLineIn, user: User = Depends(payables_user),
                    s: AsyncSession = Depends(get_session)):
    try:
        row = await payables_run.hold_line(s, user.tenant_id, user, run_id, body.payable_id,
                                           body.reason)
    except ValueError as e:                      # a lifecycle refusal is a 400, not a 500
        raise HTTPException(400, str(e))
    if row is None:
        raise HTTPException(404, "That line is not on this run")
    return row


@router.post("/runs/{run_id}/override-line")
async def override_line(run_id: uuid.UUID, body: OverrideLineIn,
                        user: User = Depends(payables_user),
                        s: AsyncSession = Depends(get_session)):
    try:
        row = await payables_run.override_line(s, user.tenant_id, user, run_id,
                                               body.payable_id, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if row is None:
        raise HTTPException(404, "That line is not on this run")
    return row


@router.post("/runs/{run_id}/release")
async def release_run(run_id: uuid.UUID, user: User = Depends(payables_releaser),
                      s: AsyncSession = Depends(get_session)):
    """THE ONLY STEP-UP ROUTE IN BOOKS. Releasing is the moment a batch becomes a payment
    instruction a person carries to the bank, so it asks for the code — and it is one symbol
    (require_tab_with_step_up) precisely so no route can be added here that forgets it."""
    try:
        row = await payables_run.release_run(s, user.tenant_id, user, run_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if row is None:
        raise HTTPException(404, "Run not found")
    return row


@router.get("/runs/{run_id}/export")
async def export_run(run_id: uuid.UUID, user: User = Depends(payables_user),
                     s: AsyncSession = Depends(get_session)):
    try:
        out = await payables_run.export_run(s, user.tenant_id, run_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
    if out is None:
        raise HTTPException(404, "Run not found")
    filename, body = out
    return Response(content=body, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/runs/{run_id}/reconcile")
async def reconcile_run(run_id: uuid.UUID, user: User = Depends(payables_user),
                        s: AsyncSession = Depends(get_session)):
    """Marked paid once the bank confirms — the bank is the only thing that knows."""
    try:
        row = await payables_run.reconcile_run(s, user.tenant_id, user, run_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if row is None:
        raise HTTPException(404, "Run not found")
    return row


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
