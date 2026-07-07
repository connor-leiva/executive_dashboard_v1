from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import current_user
from ..models import User, Business, Integration, PLSnapshot
from ..schemas import IntegrationStatus, BusinessUpdate, FinancialsResponse
from ..services.financials import compute_financials, _period

router = APIRouter(tags=["businesses"])

_STATUSES = {"healthy", "watch", "opportunity"}


def _business_dict(b: Business) -> dict:
    return {
        "id": str(b.id), "key": b.key, "name": b.name, "tag": b.tag, "status": b.status,
        "accent": b.accent, "ink": b.ink, "is_jv": b.is_jv, "jv_share": float(b.jv_share),
        "watch_margin_below": float(b.watch_margin_below) if b.watch_margin_below is not None else None,
        "per_loan_share": float(b.per_loan_share) if b.per_loan_share is not None else None,
        "capture_target": float(b.capture_target) if b.capture_target is not None else None,
        "expense_run_rate_mode": b.expense_run_rate_mode or "trailing_3mo",
        "expense_run_rate_manual": float(b.expense_run_rate_manual) if b.expense_run_rate_manual is not None else None,
        "default_agent_split": float(b.default_agent_split) if b.default_agent_split is not None else None,
        "lo_comp_rate": float(b.lo_comp_rate) if b.lo_comp_rate is not None else None,
        "opex_rate": float(b.opex_rate) if b.opex_rate is not None else None,
    }


@router.get("/businesses")
async def list_businesses(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(
        select(Business).where(Business.tenant_id == user.tenant_id).order_by(Business.sort_order)
    )).scalars().all()
    return [_business_dict(b) for b in rows]


@router.put("/businesses/{key}")
async def update_business(key: str, body: BusinessUpdate,
                          user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Edit a business's brand + health config. Partial: only provided fields change."""
    b = (await s.execute(select(Business).where(
        Business.tenant_id == user.tenant_id, Business.key == key))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Unknown business")

    fields = body.model_dump(exclude_unset=True)
    if "status" in fields and fields["status"] not in _STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(_STATUSES)}")

    _NUMERIC = {"jv_share", "watch_margin_below", "per_loan_share", "capture_target",
                "expense_run_rate_manual", "default_agent_split", "lo_comp_rate", "opex_rate"}
    _FRACTION = {"jv_share", "default_agent_split", "lo_comp_rate", "opex_rate"}
    for name, val in fields.items():
        if name in _NUMERIC and val is not None:
            try:
                val = Decimal(str(val))
            except (InvalidOperation, ValueError):
                raise HTTPException(400, f"{name} must be a number")
            if name in _FRACTION and not (Decimal(0) <= val <= Decimal(1)):
                raise HTTPException(400, f"{name} must be between 0 and 1")
        setattr(b, name, val)
    await s.commit()
    return _business_dict(b)


@router.get("/businesses/{key}/financials", response_model=FinancialsResponse)
async def business_financials(key: str, period: str = Query("mtd"),
                              user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Three-lens financials (Live / Projection / Booked) + reconciliation."""
    b = (await s.execute(select(Business).where(
        Business.tenant_id == user.tenant_id, Business.key == key))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Unknown business")
    return await compute_financials(s, user.tenant_id, b, period)


@router.put("/businesses/{key}/periods/{period}/close")
async def close_books(key: str, period: str,
                      user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Mark the period's books closed — clears the Booked lens's 'close in progress' flag."""
    b = (await s.execute(select(Business).where(
        Business.tenant_id == user.tenant_id, Business.key == key))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Unknown business")
    from ..services.metrics import _pl_period
    start, end = _pl_period(period)
    snap = (await s.execute(select(PLSnapshot).where(
        PLSnapshot.tenant_id == user.tenant_id, PLSnapshot.business_id == b.id,
        PLSnapshot.period_start == start, PLSnapshot.period_end == end))).scalar_one_or_none()
    if not snap:
        raise HTTPException(404, "No QuickBooks snapshot for that period yet")
    snap.books_closed = True
    await s.commit()
    return {"ok": True, "books_closed": True}


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
            last_error=i.last_error, config=i.config,
        )
        for i in integs
    ]
