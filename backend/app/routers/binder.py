"""Acumyn Binder API (SPEC-binder-module Part 8). All routes under /api/v1/binder, gated by
the `binder` tab. Step 2 ships the entity lifecycle; the matrix / review / obligation
mutations land with later steps. Payload shapes mirror the Binder mockups."""
import logging
import mimetypes
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, File, Form, Header, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session, SessionLocal
from ..deps import require_tab, require_role
from ..models import User, Tenant, BinderDocument
from ..services import binder, binder_ingest, binder_storage

log = logging.getLogger("app")
router = APIRouter(prefix="/binder", tags=["binder"])

binder_user = require_tab("binder")     # members with the binder grant + owners/admins

MAX_UPLOAD_BYTES = 25 * 1024 * 1024     # 25 MB — comfortably covers scanned filings/policies


async def _extract_after_upload(tenant_id) -> None:
    """Kick off extraction right after an upload so proposals appear in seconds, not on the next
    30-min worker cycle. Runs in a FRESH session (the request's is closed by now) and never
    raises — the periodic worker is the backstop. No-op without a Claude key."""
    from ..services import binder_extract
    try:
        async with SessionLocal() as s:
            await binder_extract.run_binder_extraction(s, tenant_id)
    except Exception as e:                                    # noqa: BLE001
        log.warning("binder post-upload extraction failed: %s", e)


class EntityIn(BaseModel):
    """Create payload. Only legal_name is required; a name-only entity saves dormant."""
    legal_name: str
    nickname: str | None = None
    description: str | None = None
    entity_type: str | None = None       # llc | s_corp | c_corp | partnership | trust
    jurisdiction: str | None = None      # US state postal code
    formation_date: str | None = None    # ISO YYYY-MM-DD
    ein: str | None = None
    entity_group: str | None = None      # operating | holding (default operating)
    ownership: str | None = None
    business_id: uuid.UUID | None = None


class EntityPatch(BaseModel):
    """Edit payload — every field optional; omitted fields are left unchanged. A blank EIN
    keeps the existing value (EIN is write-only)."""
    legal_name: str | None = None
    nickname: str | None = None
    description: str | None = None
    entity_type: str | None = None
    jurisdiction: str | None = None
    formation_date: str | None = None
    ein: str | None = None
    entity_group: str | None = None
    ownership: str | None = None
    business_id: uuid.UUID | None = None


@router.get("")
async def get_matrix(user: User = Depends(binder_user), s: AsyncSession = Depends(get_session)):
    """The obligations matrix (Part 8)."""
    return await binder.build_matrix(s, user.tenant_id)


@router.get("/entities")
async def list_entities(include_inactive: bool = False, user: User = Depends(binder_user),
                        s: AsyncSession = Depends(get_session)):
    return await binder.list_entities(s, user.tenant_id, include_inactive=include_inactive)


@router.get("/entity/{entity_id}")
async def get_entity_binder(entity_id: uuid.UUID, user: User = Depends(binder_user),
                            s: AsyncSession = Depends(get_session)):
    """One entity's binder: attributes, obligations, documents (Part 8)."""
    res = await binder.build_entity_binder(s, user.tenant_id, entity_id)
    if res is None:
        raise HTTPException(404, "Entity not found")
    return res


@router.post("/entities")
async def create_entity(body: EntityIn, user: User = Depends(binder_user),
                        s: AsyncSession = Depends(get_session)):
    try:
        ent = await binder.create_entity(s, user.tenant_id, user, body.model_dump(exclude_unset=True))
    except binder.EntityError as e:
        raise HTTPException(400, str(e))
    return binder.entity_out(ent)


@router.patch("/entities/{entity_id}")
async def update_entity(entity_id: uuid.UUID, body: EntityPatch, user: User = Depends(binder_user),
                        s: AsyncSession = Depends(get_session)):
    try:
        ent = await binder.update_entity(s, user.tenant_id, user, entity_id,
                                         body.model_dump(exclude_unset=True))
    except binder.EntityError as e:
        raise HTTPException(400, str(e))
    if ent is None:
        raise HTTPException(404, "Entity not found")
    return binder.entity_out(ent)


@router.post("/entities/{entity_id}/deactivate")
async def deactivate_entity(entity_id: uuid.UUID, user: User = Depends(binder_user),
                            s: AsyncSession = Depends(get_session)):
    ent = await binder.deactivate_entity(s, user.tenant_id, user, entity_id)
    if ent is None:
        raise HTTPException(404, "Entity not found")
    return {"ok": True, "id": str(ent.id), "active": ent.active}


# ── Documents (Part 2 ingestion; upload channel) ──────────────────────────────
@router.post("/documents")
async def upload_document(background: BackgroundTasks, file: UploadFile = File(...),
                          entity_id: uuid.UUID | None = Form(None),
                          category: str | None = Form(None),
                          user: User = Depends(binder_user),
                          s: AsyncSession = Depends(get_session)):
    """Multipart upload. Stores the raw bytes, dedups on content hash, creates a
    BinderDocument, and queues it for extraction. entity_id / category are optional hints;
    extraction proposes the real values."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
    try:
        doc, created = await binder_ingest.ingest_document(
            s, user.tenant_id, user, filename=file.filename or "document", data=data,
            uploaded_via="upload", entity_id=entity_id, category=category)
    except ValueError as e:
        raise HTTPException(400, str(e))
    background.add_task(_extract_after_upload, user.tenant_id)
    return binder_ingest.document_out(doc, deduped=not created)


@router.post("/documents/batch")
async def upload_documents_batch(background: BackgroundTasks, files: list[UploadFile] = File(...),
                                 entity_id: uuid.UUID | None = Form(None),
                                 user: User = Depends(binder_user),
                                 s: AsyncSession = Depends(get_session)):
    """Bulk upload (Part 2 channel 3) — the cold-start population path. Ingests many files under
    one SyncRun, deduping across the batch. Each becomes a pending document for extraction;
    entity_id (optional) links them all to one entity (e.g. bulk-uploading from its binder)."""
    payload = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"{f.filename} exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
        payload.append({"filename": f.filename or "document", "data": data, "entity_id": entity_id})
    if not payload:
        raise HTTPException(400, "No files")
    res = await binder_ingest.ingest_batch(s, user.tenant_id, user, payload, uploaded_via="upload")
    background.add_task(_extract_after_upload, user.tenant_id)
    return res


@router.post("/ingest/email")
async def ingest_email(to: str = Form(...), files: list[UploadFile] = File(...),
                       x_ingest_secret: str = Header(default=""),
                       s: AsyncSession = Depends(get_session)):
    """Inbound-email forwarding channel (Part 2 channel 2). PUBLIC + secret-gated: 404s unless
    BINDER_INGEST_SECRET is set and the X-Ingest-Secret header matches. Resolves the tenant from
    the recipient binder@{slug}.<domain>, ingests attachments as uploaded_via='email' (no user).
    The email provider's inbound-parse routing to this endpoint is external infra to configure."""
    if not settings.BINDER_INGEST_SECRET or x_ingest_secret != settings.BINDER_INGEST_SECRET:
        raise HTTPException(404, "Not found")
    _, _, host = (to or "").partition("@")            # binder@{slug}.acumyn.io -> slug
    slug = host.split(".")[0] if host else ""
    tenant = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(404, "Unknown mailbox")
    payload = []
    for f in files:
        data = await f.read()
        if data and len(data) <= MAX_UPLOAD_BYTES:
            payload.append({"filename": f.filename or "document", "data": data})
    if not payload:
        raise HTTPException(400, "No attachments")
    return await binder_ingest.ingest_batch(s, tenant.id, None, payload, uploaded_via="email")


@router.get("/documents")
async def list_documents(entity_id: uuid.UUID | None = None, user: User = Depends(binder_user),
                         s: AsyncSession = Depends(get_session)):
    return await binder_ingest.list_documents(s, user.tenant_id, entity_id=entity_id)


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: uuid.UUID, user: User = Depends(binder_user),
                          s: AsyncSession = Depends(get_session)):
    """Delete a document (and its blob + derived proposals; detach it from any obligation it
    backed). Tenant-scoped + binder-tab gated."""
    ok = await binder.delete_document(s, user.tenant_id, user, doc_id)
    if not ok:
        raise HTTPException(404, "Document not found")
    return {"ok": True, "id": str(doc_id)}


@router.get("/documents/{doc_id}/raw")
async def document_raw(doc_id: uuid.UUID, user: User = Depends(binder_user),
                       s: AsyncSession = Depends(get_session)):
    """Stream a stored document inline so the frontend can preview it (PDF/image in a viewer).
    Tenant-scoped + binder-tab gated; 404 if the blob isn't on this process's storage."""
    doc = (await s.execute(select(BinderDocument).where(
        BinderDocument.tenant_id == user.tenant_id,
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


# ── Confirmation loop (Part 5.1) ──────────────────────────────────────────────
class ConfirmIn(BaseModel):
    entity_id: uuid.UUID | None = None       # required when the proposal is ambiguous
    edits: dict | None = None                # override due_date / lead_days / cadence / kind / entity_id


class ApplicabilityIn(BaseModel):
    applicable: bool


class ObligationPatch(BaseModel):
    due_date: str | None = None
    lead_days: int | None = None
    cadence: str | None = None
    notes: str | None = None


def _binder400(e):
    raise HTTPException(400, str(e))


@router.get("/review")
async def get_review(user: User = Depends(binder_user), s: AsyncSession = Depends(get_session)):
    return await binder.build_review(s, user.tenant_id)


@router.post("/review/{proposal_id}/confirm")
async def confirm(proposal_id: uuid.UUID, body: ConfirmIn = ConfirmIn(),
                  user: User = Depends(binder_user), s: AsyncSession = Depends(get_session)):
    try:
        res = await binder.confirm_proposal(s, user.tenant_id, user, proposal_id,
                                            entity_id=body.entity_id, edits=body.edits)
    except (binder.ReviewError, binder.EntityError) as e:
        _binder400(e)
    if res is None:
        raise HTTPException(404, "Proposal not found")
    return {"ok": True, **binder.obligation_out(res["obligation"], res["status"])}


@router.post("/review/{proposal_id}/dismiss")
async def dismiss(proposal_id: uuid.UUID, user: User = Depends(binder_user),
                  s: AsyncSession = Depends(get_session)):
    try:
        p = await binder.dismiss_proposal(s, user.tenant_id, user, proposal_id)
    except binder.ReviewError as e:
        _binder400(e)
    if p is None:
        raise HTTPException(404, "Proposal not found")
    return {"ok": True, "id": str(p.id), "state": p.state}


@router.post("/obligations/{obligation_id}/complete")
async def complete(obligation_id: uuid.UUID, user: User = Depends(binder_user),
                   s: AsyncSession = Depends(get_session)):
    ob = await binder.complete_obligation(s, user.tenant_id, user, obligation_id)
    if ob is None:
        raise HTTPException(404, "Obligation not found")
    return {"ok": True, **binder.obligation_out(ob, binder.obligation_status(ob))}


@router.post("/obligations/{obligation_id}/applicability")
async def applicability(obligation_id: uuid.UUID, body: ApplicabilityIn,
                        user: User = Depends(binder_user), s: AsyncSession = Depends(get_session)):
    ob = await binder.set_applicability(s, user.tenant_id, user, obligation_id, body.applicable)
    if ob is None:
        raise HTTPException(404, "Obligation not found")
    return {"ok": True, **binder.obligation_out(ob, binder.obligation_status(ob))}


@router.patch("/obligations/{obligation_id}")
async def edit_obligation(obligation_id: uuid.UUID, body: ObligationPatch,
                          user: User = Depends(binder_user), s: AsyncSession = Depends(get_session)):
    try:
        ob = await binder.edit_obligation(s, user.tenant_id, user, obligation_id,
                                          body.model_dump(exclude_unset=True))
    except binder.EntityError as e:
        _binder400(e)
    if ob is None:
        raise HTTPException(404, "Obligation not found")
    return {"ok": True, **binder.obligation_out(ob, binder.obligation_status(ob))}


@router.post("/obligations/{obligation_id}/explain")
async def explain_obligation(obligation_id: uuid.UUID, user: User = Depends(binder_user),
                             s: AsyncSession = Depends(get_session)):
    """Generate (and cache) a plain-language explanation of an obligation's status. Key-gated:
    400 when no AI key is configured."""
    try:
        res = await binder.explain_obligation(s, user.tenant_id, user, obligation_id)
    except binder.EntityError as e:
        _binder400(e)
    if res is None:
        raise HTTPException(404, "Obligation not found")
    return {"ok": True, **res}


class ObligationUpsertIn(BaseModel):
    kind: str                                # which obligation kind to configure
    due_date: str | None = None
    cadence: str | None = None               # annual | quarterly | biennial | one_time | none
    lead_days: int | None = None
    applicable: bool | None = None           # false -> renders n/a
    notes: str | None = None


@router.post("/entities/{entity_id}/obligations")
async def configure_obligation(entity_id: uuid.UUID, body: ObligationUpsertIn,
                               user: User = Depends(binder_user), s: AsyncSession = Depends(get_session)):
    """Manually configure (create-or-update) an obligation for a kind on an entity — the way a
    user tracks something no document proposed. Records a user, so invariant 1 holds."""
    try:
        ob = await binder.upsert_obligation(s, user.tenant_id, user, entity_id, body.kind,
                                            body.model_dump(exclude={"kind"}, exclude_unset=True))
    except binder.EntityError as e:
        _binder400(e)
    if ob is None:
        raise HTTPException(404, "Entity not found")
    return {"ok": True, **binder.obligation_out(ob, binder.obligation_status(ob))}


# ── Rules engine (Part 4 / 8) ─────────────────────────────────────────────────
@router.get("/rules")
async def get_rules(user: User = Depends(binder_user), s: AsyncSession = Depends(get_session)):
    """The jurisdiction rules that apply to this tenant, with freshness (stale) flags."""
    return await binder.build_rules(s, user.tenant_id)


@router.post("/rules/{rule_id}/verify")
async def verify_rule(rule_id: uuid.UUID, user: User = Depends(require_role("owner", "admin")),
                      s: AsyncSession = Depends(get_session)):
    """Stamp a rule as verified-today (owner/admin only)."""
    r = await binder.verify_rule(s, user.tenant_id, user, rule_id)
    if r is None:
        raise HTTPException(404, "Rule not found")
    return {"ok": True, "id": str(r.id), "last_verified": r.last_verified.isoformat()}
