import uuid
import json
import datetime as dt

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session, SessionLocal
from ..deps import current_user, require_role
from ..models import User, Integration, Business, SyncRun, MetricRecord
from ..services.audit import audit
from ..security import enc, dec, make_token, read_token
from ..integrations import qbo, stripe_legacy
from ..services import legacy_export
from ..services.sync import run_all, run_one
from ..services.metrics import _period_range
from ..services.integrations_view import build_integrations_view
from ..schemas import IntegrationsOut

router = APIRouter(tags=["integrations"])


@router.get("/settings/integrations", response_model=IntegrationsOut)
async def settings_integrations(user: User = Depends(require_role("owner", "admin")), s: AsyncSession = Depends(get_session)):
    """Grouped, status-aware source list for the Settings › Integrations page."""
    return await build_integrations_view(s, user.tenant_id)


@router.get("/integrations/qbo/connect")
async def qbo_connect(business_key: str, user: User = Depends(require_role("owner", "admin")),
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
                   user: User = Depends(require_role("owner", "admin")), s: AsyncSession = Depends(get_session)):
    """Refresh every source. Returns immediately; poll GET /sync/status/{job_id}."""
    run = SyncRun(tenant_id=user.tenant_id, provider="all", status="running")
    s.add(run)
    await s.commit()
    bg.add_task(_run_all_job, user.tenant_id, run.id, period)
    return {"job_id": str(run.id)}


@router.get("/sync/status/{job_id}")
async def sync_status(job_id: uuid.UUID, user: User = Depends(require_role("owner", "admin")),
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
                   user: User = Depends(require_role("owner", "admin")), s: AsyncSession = Depends(get_session)):
    integ = (await s.execute(select(Integration).where(
        Integration.id == integ_id, Integration.tenant_id == user.tenant_id))).scalar_one_or_none()
    if not integ:
        raise HTTPException(404, "Unknown integration")
    bg.add_task(_run_one_job, user.tenant_id, integ_id, period)
    return {"ok": True}


@router.post("/integrations")
async def create_integration(body: dict, user: User = Depends(require_role("owner", "admin")),
                             s: AsyncSession = Depends(get_session)):
    """Create/update a token-based integration (Go High Level, Arive). Body:
    {provider, business_key, token, config}. Token is encrypted at rest."""
    provider = (body.get("provider") or "").strip()
    if provider not in ("ghl", "ghl_bc", "arive", "stripe_legacy"):
        raise HTTPException(400, "Unsupported provider")
    biz = (await s.execute(select(Business).where(
        Business.tenant_id == user.tenant_id, Business.key == body.get("business_key")))).scalar_one_or_none()
    if not biz:
        raise HTTPException(404, "Unknown business")
    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == user.tenant_id, Integration.provider == provider,
        Integration.business_id == biz.id))).scalar_one_or_none()

    # Arive uses three credentials (Client ID + Secret Key + API Key) stored as one
    # encrypted blob. On edit, blank fields keep the current value.
    if provider == "arive":
        existing = {}
        if integ and integ.access_token_enc:
            try:
                existing = json.loads(dec(integ.access_token_enc))
            except Exception:  # noqa: BLE001
                existing = {}
        creds = {
            "client_id": (body.get("client_id") or existing.get("client_id") or "").strip(),
            "secret": (body.get("secret") or existing.get("secret") or "").strip(),
            "api_key": (body.get("api_key") or existing.get("api_key") or "").strip(),
        }
        if not all(creds.values()):
            raise HTTPException(400, "Arive needs a Client ID, Secret Key, and API Key.")
        if integ is None:
            integ = Integration(tenant_id=user.tenant_id, provider="arive", business_id=biz.id)
        integ.access_token_enc = enc(json.dumps(creds))
        if body.get("config") is not None:
            integ.config = body["config"]
        integ.status, integ.last_error = "connected", None
        if integ.id is None:
            s.add(integ)
        await s.commit()
        return {"id": str(integ.id)}

    # Legacy Stripe: validate the read-only key up front (a bad/mis-scoped key should
    # fail the connect, not silently no-op at the next sync).
    if provider == "stripe_legacy" and body.get("token"):
        try:
            await stripe_legacy.ping(body["token"].strip())
        except Exception:  # noqa: BLE001 — surface as a clean 400
            raise HTTPException(400, "Stripe rejected that key. Use a read-only restricted "
                                     "key (Charges: read, Customers: read, Subscriptions: read).")

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
    await s.flush()
    audit(s, user.tenant_id, user.id, "integration.connected", "integration", integ.id,
          {"provider": provider, "business": biz.key})
    await s.commit()
    return {"id": str(integ.id)}


@router.post("/integrations/{integ_id}/disconnect")
async def disconnect(integ_id: uuid.UUID, user: User = Depends(require_role("owner", "admin")),
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
    audit(s, user.tenant_id, user.id, "integration.disconnected", "integration", integ.id,
          {"provider": integ.provider})
    await s.commit()
    return {"ok": True}


# ── Legacy Stripe → GHL delta CSV (generate-only; GHL has no transaction-write API) ──
async def _stripe_legacy_integ(s: AsyncSession, tenant_id):
    return (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.provider == "stripe_legacy"))).scalar_one_or_none()


def _legacy_watermark(integ: Integration) -> dt.date | None:
    v = (integ.config or {}).get("delta_through")
    try:
        return dt.date.fromisoformat(v) if v else None
    except (ValueError, TypeError):
        return None


async def _legacy_payments(s: AsyncSession, integ: Integration):
    return (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == integ.tenant_id, MetricRecord.business_id == integ.business_id,
        MetricRecord.source == "stripe_legacy", MetricRecord.kind == "payment"))).scalars().all()


@router.get("/integrations/stripe_legacy/delta")
async def legacy_delta_status(user: User = Depends(require_role("owner", "admin")),
                              s: AsyncSession = Depends(get_session)):
    """How many net-new legacy Forum charges are waiting to be imported into GHL —
    powers the Settings reminder."""
    integ = await _stripe_legacy_integ(s, user.tenant_id)
    if not integ:
        raise HTTPException(404, "Legacy Stripe is not connected.")
    recs = await _legacy_payments(s, integ)
    through = _legacy_watermark(integ)
    pend = legacy_export.pending(recs, through)
    return {"connected": integ.status == "connected",
            "total_forum_charges": len(recs),
            "through": through.isoformat() if through else None,
            "pending_count": len(pend),
            "pending_through": (max(r.occurred_on for r in pend).isoformat() if pend else None),
            "last_synced_at": integ.last_synced_at.isoformat() if integ.last_synced_at else None}


@router.get("/integrations/stripe_legacy/delta.csv")
async def legacy_delta_csv(user: User = Depends(require_role("owner", "admin")),
                           s: AsyncSession = Depends(get_session)):
    """Download the ready-to-import GHL CSV of net-new legacy Forum charges. Idempotent
    (does not advance the watermark) — import it in GHL, then POST …/delta/mark-imported."""
    integ = await _stripe_legacy_integ(s, user.tenant_id)
    if not integ:
        raise HTTPException(404, "Legacy Stripe is not connected.")
    recs = await _legacy_payments(s, integ)
    text, n, _ = legacy_export.build_csv(recs, _legacy_watermark(integ))
    fname = f"forum_legacy_delta_{dt.date.today().isoformat()}.csv"
    return Response(content=text, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"', "X-Row-Count": str(n)})


@router.post("/integrations/stripe_legacy/delta/mark-imported")
async def legacy_mark_imported(user: User = Depends(require_role("owner", "admin")),
                               s: AsyncSession = Depends(get_session)):
    """Advance the watermark past the charges just downloaded, so the next delta is
    only what's new after them. Call this only after the GHL upload succeeds."""
    integ = await _stripe_legacy_integ(s, user.tenant_id)
    if not integ:
        raise HTTPException(404, "Legacy Stripe is not connected.")
    recs = await _legacy_payments(s, integ)
    through = _legacy_watermark(integ)
    _, n, new_wm = legacy_export.build_csv(recs, through)
    if new_wm:
        cfg = dict(integ.config or {})
        cfg["delta_through"] = new_wm.isoformat()
        integ.config = cfg
        audit(s, user.tenant_id, user.id, "integration.legacy_delta_imported", "integration", integ.id,
              {"through": new_wm.isoformat(), "rows": n})
        await s.commit()
    return {"ok": True, "marked": n,
            "through": new_wm.isoformat() if new_wm else (through.isoformat() if through else None)}
