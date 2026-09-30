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
import mimetypes
import secrets
import uuid
from datetime import date

from fastapi import (APIRouter, Depends, File, Form, Header, HTTPException, UploadFile)
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..deps import require_role, require_tab, require_tab_with_step_up
from ..models import BinderDocument, Tenant, User
from ..services import (binder_ingest, binder_storage, payables, payables_intake, payables_run,
                        payables_vendor)
from ..services.audit import audit

router = APIRouter(prefix="/payables", tags=["payables"])

payables_user = require_tab("books")
# Release is the one action here that turns a batch into a payment instruction, so it carries
# the second factor. One symbol, per the docstring on require_tab_with_step_up: no route can be
# added to this section that quietly forgets it.
payables_releaser = require_tab_with_step_up("books", "payments")
# Changing the address that accepts documents into this pipeline is administration, not
# bookkeeping: it decides what the workspace will accept from the outside. A member with a books
# grant can read and file bills; only an owner or admin can move the door.
payables_manager = require_role("owner", "admin")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024        # matches Binder's limit; these are the same scans
MAX_ATTACHMENTS = 20                       # per forwarded message -- see ingest_email


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

    Filed under `vendor_tax` with no entity. A W-9 IS a tax document, but it is the vendor's --
    filing it as plain `tax` put a supplier's W-9 in the middle of the workspace's own tax
    filings, in the section people unlock with a code to read a formation certificate.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
    try:
        doc, _created = await binder_ingest.ingest_document(
            s, user.tenant_id, user, filename=file.filename or "w9.pdf", data=data,
            uploaded_via="upload", entity_id=None, category="vendor_tax")
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
    """Store an invoice document, and queue the AP Clerk to read it.

    The queued run is the whole difference between a skill that exists and a skill that happens.
    Until this, `ap_intake` could only be fired by hand by somebody who already knew a document's
    id -- which is not a workflow anybody would use. Dropping a PDF here IS the trigger.

    It still proposes, and nothing more: the clerk writes a draft, a person approves it, and a
    person ships it. What comes back tells you which of those is next, or why nothing was queued.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
    try:
        return await payables_intake.receive_invoice(
            s, user.tenant_id, user, filename=file.filename or "invoice.pdf", data=data,
            via="upload")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/documents/{doc_id}/raw")
async def document_raw(doc_id: uuid.UUID, user: User = Depends(payables_user),
                       s: AsyncSession = Depends(get_session)):
    """Stream one of Payables' own documents so an invoice can be read on screen.

    Its own route rather than Binder's, and scoped to Payables' categories, because that is the
    other half of taking invoices out of the Binder: Binder's raw route now serves only Binder
    documents, so without this an invoice would be filed where nobody could look at it. Which
    matters most at exactly the moment somebody is deciding whether to accept what the clerk read
    off it.
    """
    doc = (await s.execute(select(BinderDocument).where(
        BinderDocument.tenant_id == user.tenant_id,
        BinderDocument.category.in_(sorted(binder_ingest.PAYABLES_CATEGORIES)),
        BinderDocument.id == doc_id))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(404, "Document not found")
    if not doc.storage_ref or not binder_storage.exists(doc.storage_ref):
        raise HTTPException(404, "Document file is not available")
    try:
        data = binder_storage.read(doc.storage_ref)
    except Exception:
        raise HTTPException(404, "Document file is not available")
    ctype = mimetypes.guess_type(doc.filename)[0] or "application/octet-stream"
    safe = binder_storage.safe_filename(doc.filename)
    return Response(content=data, media_type=ctype,
                    headers={"Content-Disposition": f'inline; filename="{safe}"',
                             "Cache-Control": "private, no-store"})


# ── the forwarding address (Payables' own, not Binder's) ──────────────────────
class EmailIn(BaseModel):
    enabled: bool | None = None
    local_part: str | None = None


@router.get("/email")
async def get_email_channel(user: User = Depends(payables_user),
                            s: AsyncSession = Depends(get_session)):
    """The workspace's AP forwarding address and whether it is open."""
    t = await s.get(Tenant, user.tenant_id)
    if t is None:
        raise HTTPException(404, "Workspace not found")
    return {**payables_intake.email_settings(t),
            "can_manage": user.role in ("owner", "admin")}


@router.put("/email")
async def put_email_channel(body: EmailIn, user: User = Depends(payables_user),
                            _m: User = Depends(payables_manager),
                            s: AsyncSession = Depends(get_session)):
    """Turn the forwarding address on or off, or change what it is called.

    Both gates, deliberately: the books tab AND an owner/admin role. Two dependencies rather than
    one composed guard because this is the only route in the section that needs the second, and a
    third symbol next to `payables_user` and `payables_releaser` would be one more thing to pick
    correctly when the next route is added.
    """
    t = await s.get(Tenant, user.tenant_id)
    if t is None:
        raise HTTPException(404, "Workspace not found")
    before = payables_intake.email_settings(t)
    try:
        out = payables_intake.set_email_settings(
            t, enabled=body.enabled, local_part=body.local_part)
    except payables_intake.AddressRejected as e:
        raise HTTPException(400, str(e))
    audit(s, t.id, user.id, "payables.email_channel", "tenant", t.id,
          {"from": {"enabled": before["enabled"], "local_part": before["local_part"]},
           "to": {"enabled": out["enabled"], "local_part": out["local_part"]}},
          category="Payments",
          summary=("AP forwarding address " + ("opened at " if out["enabled"] else "closed (")
                   + out["address"] + ("" if out["enabled"] else ")")))
    await s.commit()
    return {**out, "can_manage": True}


@router.post("/ingest/email")
async def ingest_email(files: list[UploadFile] = File(...),
                       to: str = Form(default=""), recipient: str = Form(default=""),
                       envelope: str = Form(default=""), sender: str = Form(default=""),
                       x_ingest_secret: str = Header(default=""),
                       s: AsyncSession = Depends(get_session)):
    """Inbound-email forwarding for supplier invoices. PUBLIC and secret-gated.

    WHAT THE SECRET PROVES, and what it does not. It authenticates the EMAIL PROVIDER, not the
    sender. Anyone can email a workspace's AP address and the provider will forward it here with
    the secret attached -- that is the point of an AP inbox. So the secret is not a credential
    belonging to whoever sent the invoice, and nothing downstream treats it as one.

    Four things stand between a stranger's email and money leaving:

      1. the platform secret -- without it this route 404s and the channel does not exist
      2. the workspace opted in, and the local part matches the one it configured
      3. what lands is a DOCUMENT. Not a bill, not a vendor, not a payment -- a filed PDF and a
         queued run, both of which a person can throw away
      4. the AP Clerk proposes; require_human means the acceptance is a person's act

    404 everywhere, for the same reason Binder's channel does: an unknown mailbox, a closed one
    and a wrong local part must be indistinguishable, or the response is a directory of which
    workspaces exist and which have the channel open.
    """
    if not settings.PAYABLES_INGEST_SECRET or not secrets.compare_digest(
            x_ingest_secret or "", settings.PAYABLES_INGEST_SECRET):
        raise HTTPException(404, "Not found")      # constant-time; 404 leaks nothing either way
    found = await payables_intake.resolve_recipient(
        s, payables_intake.candidate_recipients(envelope=envelope, recipient=recipient, to=to))
    if found is None:
        raise HTTPException(404, "Unknown mailbox")
    tenant, _cfg = found

    # A real invoice email carries one or two attachments. This route is reachable by anyone who
    # can email the address, so the cap bounds what gets hashed, stored and queued -- a message
    # with five hundred attachments is not an invoice. (It does not bound the request BODY, which
    # Starlette has already spooled by the time this runs; that belongs to the proxy.)
    if len(files) > MAX_ATTACHMENTS:
        raise HTTPException(413, f"More than {MAX_ATTACHMENTS} attachments in one message")
    results, skipped, full = [], 0, False
    for f in files:
        data = await f.read()
        if not data or len(data) > MAX_UPLOAD_BYTES:
            skipped += 1
            continue
        try:
            results.append(await payables_intake.receive_invoice(
                s, tenant.id, None, filename=f.filename or "invoice.pdf", data=data,
                via="email", sender=sender))
        except payables_intake.DayIsFull:
            full = True
            break
        except ValueError:
            skipped += 1
    # Audit FIRST, then refuse. Each attachment commits as it lands, so bailing out of the loop
    # without this left the ones that did land with no record of who sent them -- which is the
    # one thing an inbound document has that nothing else can recover later.
    if results:
        # `sender` is the only record of WHO put this in front of the clerk. It depends on the
        # provider's field mapping, which is why it is optional -- but an invoice with no
        # provenance is an invoice nobody can ask a question about.
        audit(s, tenant.id, None, "payables.email_ingest", "tenant", tenant.id,
              {"from": (sender or "unknown")[:160], "files": len(results), "skipped": skipped,
               "queued": sum(1 for r in results if r["intake"]["queued"]), "day_full": full},
              category="Payments", actor_type="integration", actor_label="Inbound email",
              summary=f"{len(results)} invoice(s) forwarded in by {(sender or 'unknown')[:80]}")
        await s.commit()
    if full:
        # 429 rather than 404, and 429 even when some of the message landed. Whoever is on the
        # other end has already produced a working address, so "later" tells them nothing they
        # did not know; a provider given a 404 may decide the mailbox is dead and stop retrying
        # for good; and a 200 on a partial message means the rest is dropped in silence. On the
        # retry the ones that landed dedup, so nothing is lost and nothing is doubled.
        raise HTTPException(429, "This workspace has taken all the forwarded mail it will "
                                 "take today. Try again tomorrow.")
    if not results:
        raise HTTPException(400, "No usable attachments")
    return {"documents": results, "skipped": skipped}


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
