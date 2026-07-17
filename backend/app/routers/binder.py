"""Acumyn Binder API (SPEC-binder-module Part 8). All routes under /api/v1/binder, gated by
the `binder` tab. Step 2 ships the entity lifecycle; the matrix / review / obligation
mutations land with later steps. Payload shapes mirror the Binder mockups."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import require_tab
from ..models import User
from ..services import binder, binder_ingest

router = APIRouter(prefix="/binder", tags=["binder"])

binder_user = require_tab("binder")     # members with the binder grant + owners/admins

MAX_UPLOAD_BYTES = 25 * 1024 * 1024     # 25 MB — comfortably covers scanned filings/policies


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


@router.get("/entities")
async def list_entities(include_inactive: bool = False, user: User = Depends(binder_user),
                        s: AsyncSession = Depends(get_session)):
    return await binder.list_entities(s, user.tenant_id, include_inactive=include_inactive)


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
async def upload_document(file: UploadFile = File(...),
                          entity_id: uuid.UUID | None = Form(None),
                          category: str | None = Form(None),
                          user: User = Depends(binder_user),
                          s: AsyncSession = Depends(get_session)):
    """Multipart upload. Stores the raw bytes, dedups on content hash, creates a
    BinderDocument, and queues it for extraction. entity_id / category are optional hints;
    extraction (Step 4) proposes the real values."""
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
    return binder_ingest.document_out(doc, deduped=not created)


@router.get("/documents")
async def list_documents(entity_id: uuid.UUID | None = None, user: User = Depends(binder_user),
                         s: AsyncSession = Depends(get_session)):
    return await binder_ingest.list_documents(s, user.tenant_id, entity_id=entity_id)


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
