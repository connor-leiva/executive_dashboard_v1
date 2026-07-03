import uuid
import datetime as dt

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session, SessionLocal
from ..deps import current_user
from ..models import User, Integration, Business, SyncRun
from ..security import enc, make_token, read_token
from ..integrations import qbo
from ..services.sync import run_all, run_one
from ..services.metrics import _period_range
from ..services.integrations_view import build_integrations_view
from ..schemas import IntegrationsOut

router = APIRouter(tags=["integrations"])


@router.get("/settings/integrations", response_model=IntegrationsOut)
async def settings_integrations(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Grouped, status-aware source list for the Settings › Integrations page."""
    return await build_integrations_view(s, user.tenant_id)


@router.get("/integrations/qbo/connect")
async def qbo_connect(business_key: str, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session)):
    # Pack tenant+business into signed state so the callback (no Host tenant) can resolve.
    # Return the URL as JSON (not a redirect): the SPA fetches this with the auth
    # header, then navigates the browser to Intuit.
    biz = (await s.execute(select(Business).where(
        Business.tenant_id == user.tenant_id, Business.key == business_key))).scalar_one_or_none()
    if not biz:
        raise HTTPException(404, "Unknown business")
    state = make_token(user.id, user.tenant_id) + "::" + str(biz.id)
    return {"url": qbo.authorize_url(state)}


@router.get("/integrations/qbo/callback")
async def qbo_callback(
    code: str = Query(...), realmId: str = Query(...),
    state: str = Query(...), s: AsyncSession = Depends(get_session),
):
    token_part, _, business_id = state.partition("::")
    payload = read_token(token_part)                 # validates + carries tid
    tenant_id = uuid.UUID(payload["tid"])
    tok = await qbo.exchange_code(code)
    expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=int(tok["expires_in"]))
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


# ── manual refresh (async via BackgroundTasks) ────────────────────
async def _run_all_job(tenant_id, run_id, period):
    async with SessionLocal() as s:
        start, end = _period_range(period)
        run = (await s.execute(select(SyncRun).where(SyncRun.id == run_id))).scalar_one()
        try:
            await run_all(s, tenant_id, start.isoformat(), end.isoformat())
            run.status, run.finished_at = "ok", dt.datetime.utcnow()
        except Exception as e:  # noqa: BLE001
            run.status, run.detail, run.finished_at = "error", str(e), dt.datetime.utcnow()
        await s.commit()


async def _run_one_job(tenant_id, integ_id, period):
    async with SessionLocal() as s:
        start, end = _period_range(period)
        await run_one(s, tenant_id, integ_id, start.isoformat(), end.isoformat())


@router.post("/sync/all")
async def sync_all(bg: BackgroundTasks, period: str = Query("mtd"),
                   user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Refresh every source. Returns immediately; poll GET /sync/status/{job_id}."""
    run = SyncRun(tenant_id=user.tenant_id, provider="all", status="running")
    s.add(run)
    await s.commit()
    bg.add_task(_run_all_job, user.tenant_id, run.id, period)
    return {"job_id": str(run.id)}


@router.get("/sync/status/{job_id}")
async def sync_status(job_id: uuid.UUID, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session)):
    run = (await s.execute(select(SyncRun).where(
        SyncRun.id == job_id, SyncRun.tenant_id == user.tenant_id))).scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Unknown job")
    return {"status": run.status,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "detail": run.detail}


@router.post("/integrations/{integ_id}/sync")
async def sync_one(integ_id: uuid.UUID, bg: BackgroundTasks, period: str = Query("mtd"),
                   user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    integ = (await s.execute(select(Integration).where(
        Integration.id == integ_id, Integration.tenant_id == user.tenant_id))).scalar_one_or_none()
    if not integ:
        raise HTTPException(404, "Unknown integration")
    bg.add_task(_run_one_job, user.tenant_id, integ_id, period)
    return {"ok": True}


@router.post("/integrations")
async def create_integration(body: dict, user: User = Depends(current_user),
                             s: AsyncSession = Depends(get_session)):
    """Create/update a token-based integration (Go High Level, Arive). Body:
    {provider, business_key, token, config}. Token is encrypted at rest."""
    provider = (body.get("provider") or "").strip()
    if provider not in ("ghl", "ghl_bc", "arive"):
        raise HTTPException(400, "Unsupported provider")
    biz = (await s.execute(select(Business).where(
        Business.tenant_id == user.tenant_id, Business.key == body.get("business_key")))).scalar_one_or_none()
    if not biz:
        raise HTTPException(404, "Unknown business")
    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == user.tenant_id, Integration.provider == provider,
        Integration.business_id == biz.id))).scalar_one_or_none()
    new = integ is None or not integ.access_token_enc
    if new and not body.get("token"):
        raise HTTPException(400, "A token is required to connect.")
    if integ is None:
        integ = Integration(tenant_id=user.tenant_id, provider=provider, business_id=biz.id)
    if body.get("token"):                       # blank on edit = keep the current token
        integ.access_token_enc = enc(body["token"])
    if body.get("config") is not None:
        integ.config = body["config"]
    integ.status = "connected"
    integ.last_error = None
    if integ.id is None:
        s.add(integ)
    await s.commit()
    return {"id": str(integ.id)}


@router.post("/integrations/{integ_id}/disconnect")
async def disconnect(integ_id: uuid.UUID, user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session)):
    integ = (await s.execute(select(Integration).where(
        Integration.id == integ_id, Integration.tenant_id == user.tenant_id))).scalar_one_or_none()
    if not integ:
        raise HTTPException(404, "Unknown integration")
    # TODO: for qbo, also call Intuit's token-revocation endpoint before clearing.
    integ.status = "disconnected"
    integ.access_token_enc = None
    integ.refresh_token_enc = None
    integ.last_error = None
    await s.commit()
    return {"ok": True}
