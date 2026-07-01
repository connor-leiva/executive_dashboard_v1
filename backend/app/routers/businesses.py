from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import current_user
from ..models import User, Business, Integration
from ..schemas import IntegrationStatus

router = APIRouter(tags=["businesses"])


@router.get("/businesses")
async def list_businesses(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(
        select(Business).where(Business.tenant_id == user.tenant_id).order_by(Business.sort_order)
    )).scalars().all()
    return [
        {
            "id": str(b.id), "key": b.key, "name": b.name, "tag": b.tag, "status": b.status,
            "accent": b.accent, "ink": b.ink, "is_jv": b.is_jv, "jv_share": float(b.jv_share),
        }
        for b in rows
    ]


@router.get("/integrations", response_model=list[IntegrationStatus])
async def list_integrations(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    integs = (await s.execute(
        select(Integration).where(Integration.tenant_id == user.tenant_id)
    )).scalars().all()
    biz = {
        b.id: b.key
        for b in (await s.execute(
            select(Business).where(Business.tenant_id == user.tenant_id)
        )).scalars().all()
    }
    return [
        IntegrationStatus(
            id=str(i.id), provider=i.provider,
            business_key=biz.get(i.business_id) if i.business_id else None,
            status=i.status, realm_id=i.realm_id,
            last_synced_at=i.last_synced_at.isoformat() if i.last_synced_at else None,
            last_error=i.last_error,
        )
        for i in integs
    ]
