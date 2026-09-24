"""ULRG Recruiting — the optional GHL Workflow webhook (RECRUITING-SPEC §5.6, Phase 6b).

Private Integration Tokens have no native webhooks, so a location admin builds a GHL *Workflow*
whose action is a Webhook pointing here: trigger on Customer Replied, Appointment Status or
Pipeline Stage Changed. It is a LATENCY optimisation on top of the five-minute poll, never a
replacement — if it is never configured, or if GHL stops sending, the poll still catches
everything on its own schedule.

THE BODY IS A HINT, AND NOTHING ELSE. This endpoint does not read a stage, a status or a message
out of the payload and write it down. It notes that something changed and runs the ordinary poll,
which re-reads the truth from the API with our own token. That is the whole security design: a
forged or replayed body can, at worst, make us ask GHL a question we were going to ask anyway.
There is no field in this payload that can change a row.

Three properties it borrows from routers/recall.py:
  * 404 when no workspace has a secret configured, so an unconfigured deploy exposes nothing.
  * 401 for a bad secret -- the endpoint exists and the admin should see the failure in GHL's own
    logs rather than watch it retry against what looks like a missing route.
  * 200 for anything well-formed and authentic, including a workspace we cannot match. A webhook
    that 500s gets retried, and a retry storm against a CRM integration is its own outage.
"""
import hmac

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select

from ..db import SessionLocal
from ..models import Integration, Tenant
from ..security import dec

router = APIRouter(tags=["recruiting"])

# A webhook can fire several times for one change (a reply, then a status, then a stage move).
# Polling once per burst is enough, and it keeps a chatty Workflow from spending the location's
# shared rate budget that the sync also draws on.
DEBOUNCE_SECONDS = 20


@router.post("/webhooks/ghl-recruiting")
async def ghl_recruiting_webhook(request: Request,
                                 x_axcion_secret: str | None = Header(None)):
    """Told that something changed; goes and looks.

    Matching the secret is also how the workspace is identified -- the body is not trusted to say
    which location it is from, because that would let anybody who learned one workspace's secret
    aim it at another.
    """
    if not x_axcion_secret:
        raise HTTPException(401, "Missing secret")

    async with SessionLocal() as s:
        rows = list((await s.execute(select(Integration).where(
            Integration.provider == "ghl_recruiting"))).scalars().all())
        configured = [r for r in rows if (r.config or {}).get("webhook_secret_enc")]
        if not configured:
            # Nothing has a secret: the feature is off everywhere, and the route should look
            # like it does not exist.
            raise HTTPException(404, "Not found")

        match = None
        for row in configured:
            try:
                secret = dec((row.config or {})["webhook_secret_enc"])
            except Exception:  # noqa: BLE001 - a corrupt secret must not 500 the endpoint
                continue
            # Constant-time, and every candidate is checked rather than breaking on the first
            # match: an early return leaks which workspace a guess was close to, through timing.
            if hmac.compare_digest(secret, x_axcion_secret):
                match = row
        if match is None:
            raise HTTPException(401, "Bad secret")

        tenant = await s.get(Tenant, match.tenant_id)
        if tenant is None or (tenant.status or "") == "suspended" or (
                (tenant.config or {}).get("syncs_frozen")):
            # Suspended or frozen means stop touching this customer's CRM. A webhook is not an
            # exception to that; it is exactly the kind of traffic freezing exists to stop.
            return {"ok": True, "polled": False, "reason": "workspace is not syncing"}

        import datetime as dt
        cfg = match.config or {}
        now = dt.datetime.now(dt.timezone.utc)
        last = cfg.get("activity_polled_at")
        if last:
            try:
                since = (now - dt.datetime.fromisoformat(last)).total_seconds()
                if since < DEBOUNCE_SECONDS:
                    return {"ok": True, "polled": False, "reason": "debounced"}
            except ValueError:
                pass

        from ..services.recruiting_poll import poll_activity
        try:
            out = await poll_activity(s, match.tenant_id)
        except Exception as exc:  # noqa: BLE001
            # Still 200. GHL retrying a Workflow webhook because our poll had a bad minute would
            # turn one failure into a queue of them, and the five-minute tick will catch up.
            print(f"[ghl_recruiting_webhook] {match.tenant_id}: {exc}", flush=True)
            return {"ok": True, "polled": False, "reason": "poll failed, the tick will catch up"}

    # Deliberately says nothing about what was found. The reply goes into a log in somebody
    # else's system, and "3 new messages for Sunny Kaur" does not belong there.
    return {"ok": True, "polled": True}
