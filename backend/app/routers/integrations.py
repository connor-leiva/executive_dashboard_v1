import uuid
import datetime as dt

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..deps import current_user
from ..models import User, Integration, Business
from ..security import enc, make_token, read_token
from ..integrations import qbo
from ..services.sync import run_all
from ..services.metrics import _period_range

router = APIRouter(tags=["integrations"])


@router.get("/integrations/qbo/connect")
async def qbo_connect(business_id: uuid.UUID, user: User = Depends(current_user)):
    # Pack tenant+business into signed state so the callback (no Host tenant) can resolve.
    state = make_token(user.id, user.tenant_id) + "::" + str(business_id)
    return RedirectResponse(qbo.authorize_url(state))


@router.get("/integrations/qbo/callback")
async def qbo_callback(
    code: str = Query(...), realmId: str = Query(...),
    state: str = Query(...), s: AsyncSession = Depends(get_session),
):
    token_part, _, business_id = state.partition("::")
    payload = read_token(token_part)                 # validates + carries tid
    tenant_id = uuid.UUID(payload["tid"])
    tok = await qbo.exchange_code(code)
    expires = dt.datetime.utcnow() + dt.timedelta(seconds=int(tok["expires_in"]))
    biz = (await s.execute(select(Business).where(
        Business.id == uuid.UUID(business_id), Business.tenant_id == tenant_id))).scalar_one()
    existing = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.provider == "qbo",
        Integration.business_id == biz.id))).scalar_one_or_none()
    obj = existing or Integration(tenant_id=tenant_id, provider="qbo", business_id=biz.id)
    obj.realm_id = realmId
    obj.access_token_enc = enc(tok["access_token"])
    obj.refresh_token_enc = enc(tok["refresh_token"])
    obj.token_expires_at = expires
    obj.status = "connected"
    if not existing:
        s.add(obj)
    await s.commit()
    # Redirect back into the app.
    return RedirectResponse(f"{settings.APP_PUBLIC_URL}/?qbo=connected")


@router.post("/integrations/sync")
async def trigger_sync(
    period: str = Query("mtd"),
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session),
):
    """Manual sync trigger for all connected providers (admin convenience)."""
    start, end = _period_range(period)
    await run_all(s, user.tenant_id, start.isoformat(), end.isoformat())
    return {"ok": True, "period": period}
