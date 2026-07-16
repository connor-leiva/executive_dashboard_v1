"""Acumyn Binder API (SPEC-binder-module Part 8). All routes under /api/v1/binder, gated by
the `binder` tab. Step 2 ships the entity lifecycle; the matrix / review / obligation
mutations land with later steps. Payload shapes mirror the Binder mockups."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import require_tab
from ..models import User
from ..services import binder

router = APIRouter(prefix="/binder", tags=["binder"])

binder_user = require_tab("binder")     # members with the binder grant + owners/admins


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
