"""ULRG L10 Scorecard — read-only public share for ClickUp embeds (SPEC Part 5.3 / Step 8).

The web app serves the embed PAGE at /share/{token} (it renders the real read-only Scorecard —
identical look, collapsible cumulative panel — reading from the JSON below), and it carries the
`frame-ancestors 'self' https://*.clickup.com` framing header. This module owns the token-scoped
DATA and keeps old backend /share/{token} URLs alive by redirecting them to the web app:

  GET /api/v1/share/{token}/scorecard  → the same payload as /ulrg/scorecard, no auth
  GET /api/v1/share/{token}/room       → a team room (pending Step 6)
  GET /share/{token}                    → 307 redirect to APP_PUBLIC_URL/share/{token}

Tenant is resolved from the token, never the Host header. A revoked/expired/unknown token returns
404, not 403, so a dead link leaks nothing. The payload is Cache-Control: no-store — the token is
in the URL and a cached copy must not outlive a revoke.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..models import Business, ShareLink
from ..services import scorecard

router = APIRouter(prefix="/share", tags=["share"])          # JSON, mounted under /api/v1
page_router = APIRouter(tags=["share"])                       # /share/{token} at the root


async def _resolve(s: AsyncSession, token: str) -> ShareLink:
    """A live share link, or 404. Revoked/expired both read as 'not found' so a dead link is silent."""
    link = (await s.execute(select(ShareLink).where(ShareLink.token == token))).scalar_one_or_none()
    now = dt.datetime.now(dt.timezone.utc)
    if link is None or link.revoked_at is not None or (link.expires_at is not None and link.expires_at <= now):
        raise HTTPException(404, "Not found")
    return link


async def _ulrg_business(s: AsyncSession, tenant_id) -> Business:
    b = (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id, Business.key == "ulrg"))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Not found")
    return b


@router.get("/{token}/scorecard")
async def shared_scorecard(token: str, response: Response, weeks: int = 13,
                           s: AsyncSession = Depends(get_session)):
    link = await _resolve(s, token)
    b = await _ulrg_business(s, link.tenant_id)
    weeks = max(1, min(52, weeks))
    # never cache the payload: the token is in the URL, and a cached copy could outlive a revoke
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return await scorecard.build_scorecard(s, link.tenant_id, b.id, weeks)


@router.get("/{token}/room")
async def shared_room(token: str, s: AsyncSession = Depends(get_session)):
    await _resolve(s, token)                              # validate the token even while unimplemented
    raise HTTPException(404, "Not found")                 # Team Rooms are Step 6; no room payload yet


@router.get("/{token}/desk")
async def shared_rep_desk(token: str, response: Response, s: AsyncSession = Depends(get_session)):
    """A sales rep's OWN slice of the beCollective Sales Desk (scope 'sd_rep', scope_ref = their
    email). Read-only, no auth — the token IS the credential; revoke kills it everywhere."""
    from ..services.launch import active_launch_for
    from ..services.sales_desk import compute_rep_desk

    link = await _resolve(s, token)
    if link.scope != "sd_rep" or not link.scope_ref:
        raise HTTPException(404, "Not found")
    b = (await s.execute(select(Business).where(
        Business.tenant_id == link.tenant_id, Business.key == "springb"))).scalar_one_or_none()
    launch = b and await active_launch_for(s, link.tenant_id, b.id)
    if not launch:
        raise HTTPException(404, "Not found")             # no active launch → the page goes dark
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return await compute_rep_desk(s, link.tenant_id, launch, link.scope_ref)


@page_router.get("/share/{token}")
async def share_page(token: str, s: AsyncSession = Depends(get_session)):
    """Old backend share URLs → the web-app embed page (which renders the real read-only Scorecard).
    New links (from /ulrg/share) point straight at the web app. 404 a dead token before redirecting."""
    await _resolve(s, token)
    return RedirectResponse(f"{settings.APP_PUBLIC_URL.rstrip('/')}/share/{token}", status_code=307)
