"""ULRG L10 Scorecard + Team Rooms — API (SPEC-ulrg-scorecard Part 5).

Read is gated by the `ulrg` tab grant; all sub-tabs inherit it (team-level scoping is out of
scope, Part 8). Manual entry is owner/admin or the metric's own owner. All math is server-side
(services/scorecard.py) — this router only shapes requests and guards writes.
"""
from __future__ import annotations

import datetime as dt
import mimetypes
import secrets

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..deps import current_user, require_tab
from ..models import User, Business, Tenant, ScorecardGroup, ScorecardMetric, ScorecardValue, ShareLink
from ..services import binder_storage, scorecard
from ..services.audit import audit

router = APIRouter(prefix="/ulrg", tags=["ulrg"])


async def _ulrg_business(s, tenant_id):
    b = (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id, Business.key == "ulrg"))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "No ULRG business for this tenant")
    return b


@router.get("/scorecard")
async def get_scorecard(weeks: int = Query(13, ge=1, le=52),
                        user: User = Depends(require_tab("ulrg")),
                        s: AsyncSession = Depends(get_session)):
    b = await _ulrg_business(s, user.tenant_id)
    return await scorecard.build_scorecard(s, user.tenant_id, b.id, weeks)


class ValueIn(BaseModel):
    metric_id: str
    week_start: str                 # ISO date (Monday of the ISO week)
    value: float | None = None      # null ≠ zero — null clears a value


@router.post("/scorecard/values", status_code=201)
async def set_value(body: ValueIn, user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session)):
    """Manual entry (Part 3.3). Owner/admin, or the metric's owner. A manual write to a
    resolver-sourced metric wins until the next resolver run; that conflict is logged (audited)."""
    m = (await s.execute(select(ScorecardMetric).where(
        ScorecardMetric.tenant_id == user.tenant_id, ScorecardMetric.id == body.metric_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(404, "Unknown metric")
    if user.role not in ("owner", "admin") and m.owner_user_id != user.id:
        raise HTTPException(403, "Not allowed to edit this metric")
    try:
        wk = dt.date.fromisoformat(body.week_start)
    except ValueError:
        raise HTTPException(400, "week_start must be an ISO date")
    def _apply(existing) -> bool:
        overrode = bool(existing and existing.source == "resolver")
        if existing:
            existing.value = body.value
            existing.source, existing.entered_by = "manual", user.id
            existing.entered_at = dt.datetime.now(dt.timezone.utc)
        else:
            s.add(ScorecardValue(tenant_id=user.tenant_id, metric_id=m.id, week_start=wk,
                                 value=body.value, source="manual", entered_by=user.id))
        audit(s, user.tenant_id, user.id, "scorecard.value_set", "scorecard_metric", m.id,
              {"week": body.week_start, "overrode_resolver": overrode})
        return overrode

    row = (await s.execute(select(ScorecardValue).where(
        ScorecardValue.metric_id == m.id, ScorecardValue.week_start == wk))).scalar_one_or_none()
    conflict = _apply(row)
    try:
        await s.commit()
    except IntegrityError:                              # a concurrent first-write won the insert race
        await s.rollback()
        row = (await s.execute(select(ScorecardValue).where(
            ScorecardValue.metric_id == m.id, ScorecardValue.week_start == wk))).scalar_one()
        conflict = _apply(row)
        await s.commit()
    return {"ok": True, "overrode_resolver": conflict}


# ── share links (SPEC 5.3 / Step 8) — owner/admin create + manage; the public read is share.py ──
class ShareIn(BaseModel):
    scope: str = "ulrg_scorecard"
    scope_ref: str | None = None


def _require_admin(user: User) -> None:
    if user.role not in ("owner", "admin"):
        raise HTTPException(403, "Owner or admin only")


def _share_url(token: str) -> str:
    # the web app serves the embed page (renders the real read-only Scorecard); APP_PUBLIC_URL is it
    return f"{settings.APP_PUBLIC_URL.rstrip('/')}/share/{token}"


@router.post("/share", status_code=201)
async def create_share(body: ShareIn, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session)):
    """Mint a read-only share token for the scorecard (owner/admin). Returns {token, url}; the url is
    the web-app embed page ClickUp iframes."""
    _require_admin(user)
    if body.scope != "ulrg_scorecard":                   # team-room sharing arrives with Step 6
        raise HTTPException(400, "Only the full scorecard can be shared yet")
    token = secrets.token_urlsafe(32)
    s.add(ShareLink(tenant_id=user.tenant_id, scope=body.scope, scope_ref=body.scope_ref,
                    token=token, created_by=user.id))
    audit(s, user.tenant_id, user.id, "scorecard.share_created", "share_link", None, {"scope": body.scope})
    await s.commit()
    return {"token": token, "url": _share_url(token)}


@router.get("/shares")
async def list_shares(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Live (non-revoked) share links for this tenant, owner/admin."""
    _require_admin(user)
    rows = (await s.execute(select(ShareLink).where(
        ShareLink.tenant_id == user.tenant_id, ShareLink.revoked_at.is_(None))
        .order_by(ShareLink.created_at.desc()))).scalars().all()
    return [{"id": str(link.id), "scope": link.scope, "scope_ref": link.scope_ref,
             "created_at": link.created_at.isoformat() if link.created_at else None,
             "url": _share_url(link.token)} for link in rows]


@router.delete("/share/{share_id}", status_code=204)
async def revoke_share(share_id: str, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session)):
    """Revoke a share link (owner/admin) — the token then reads as 404 everywhere."""
    _require_admin(user)
    link = (await s.execute(select(ShareLink).where(
        ShareLink.tenant_id == user.tenant_id, ShareLink.id == share_id))).scalar_one_or_none()
    if link is None:
        raise HTTPException(404, "Unknown share link")
    if link.revoked_at is None:
        link.revoked_at = dt.datetime.now(dt.timezone.utc)
        audit(s, user.tenant_id, user.id, "scorecard.share_revoked", "share_link", link.id, {})
        await s.commit()


# ── self-service office config (Step: scorecard settings) — owner name + headshot ──────────────
class GroupPatch(BaseModel):
    owner_name: str | None = None


async def _group(s: AsyncSession, tenant_id, group_id: str) -> ScorecardGroup:
    g = (await s.execute(select(ScorecardGroup).where(
        ScorecardGroup.tenant_id == tenant_id, ScorecardGroup.id == group_id))).scalar_one_or_none()
    if g is None:
        raise HTTPException(404, "Unknown group")
    return g


@router.patch("/group/{group_id}")
async def edit_group(group_id: str, body: GroupPatch, user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session)):
    """Rename a team's owner (owner/admin) — e.g. first name → full name, self-service."""
    _require_admin(user)
    g = await _group(s, user.tenant_id, group_id)
    if body.owner_name is not None:
        g.owner_name = body.owner_name.strip() or None
    audit(s, user.tenant_id, user.id, "scorecard.group_edit", "scorecard_group", g.id,
          {"owner_name": g.owner_name})
    await s.commit()
    return {"ok": True, "owner_name": g.owner_name}


@router.post("/group/{group_id}/photo", status_code=201)
async def upload_group_photo(group_id: str, file: UploadFile = File(...),
                             user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Upload an office/owner headshot (owner/admin). Bytes go to the shared object storage."""
    _require_admin(user)
    g = await _group(s, user.tenant_id, group_id)
    ct = file.content_type or ""
    if not ct.startswith("image/"):
        raise HTTPException(400, "Please upload an image")
    data = await file.read()
    if len(data) > 5_000_000:
        raise HTTPException(413, "Image too large (max 5 MB)")
    ref = f"{user.tenant_id}/scorecard-photos/{g.id}/{binder_storage.safe_filename(file.filename or 'photo')}"
    g.owner_photo_ref = binder_storage.put(ref, data, ct)
    audit(s, user.tenant_id, user.id, "scorecard.group_photo", "scorecard_group", g.id, {})
    await s.commit()
    return {"ok": True}


@router.get("/group/{group_id}/photo")
async def group_photo(group_id: str, s: AsyncSession = Depends(get_session)):
    """Serve a headshot — PUBLIC (no auth) so it shows in the app AND the read-only ClickUp embed.
    Not sensitive; the group id is an unguessable UUID."""
    g = (await s.execute(select(ScorecardGroup).where(
        ScorecardGroup.id == group_id))).scalar_one_or_none()
    if g is None or not g.owner_photo_ref:
        raise HTTPException(404, "No photo")
    try:
        data = binder_storage.read(g.owner_photo_ref)
    except Exception:
        raise HTTPException(404, "No photo")
    media_type = mimetypes.guess_type(g.owner_photo_ref)[0] or "image/jpeg"
    return Response(content=data, media_type=media_type, headers={"Cache-Control": "public, max-age=300"})


# ── measurement periods (Phase B) — the fiscal quarters live on tenant.config ──────────────────
class PeriodIn(BaseModel):
    key: str
    start: str          # ISO date (Monday-ish; boundaries drive quarter_of + QTD)
    end: str


class PeriodsIn(BaseModel):
    periods: list[PeriodIn]


@router.get("/periods")
async def get_periods(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """The tenant's measurement periods (fiscal quarters), owner/admin."""
    _require_admin(user)
    t = await s.get(Tenant, user.tenant_id)
    return {"periods": (t.config or {}).get("fiscal_quarters", [])}


@router.put("/periods")
async def set_periods(body: PeriodsIn, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session)):
    """Replace the measurement periods (owner/admin). Validates dates and sorts by start; the
    scorecard resolves the current period + QTD from these, so bad dates would skew every row."""
    _require_admin(user)
    out, seen = [], set()
    for p in body.periods:
        key = p.key.strip()
        if not key:
            raise HTTPException(400, "Every period needs a name")
        if key in seen:
            raise HTTPException(400, f"Duplicate period name: {key}")
        seen.add(key)
        try:
            sd, ed = dt.date.fromisoformat(p.start), dt.date.fromisoformat(p.end)
        except ValueError:
            raise HTTPException(400, f"{key}: dates must be YYYY-MM-DD")
        if ed <= sd:
            raise HTTPException(400, f"{key}: end must be after start")
        out.append({"key": key, "start": p.start, "end": p.end})
    out.sort(key=lambda p: p["start"])
    t = await s.get(Tenant, user.tenant_id)
    cfg = dict(t.config or {})
    cfg["fiscal_quarters"] = out
    t.config = cfg
    audit(s, user.tenant_id, user.id, "scorecard.periods_set", "tenant", user.tenant_id,
          {"count": len(out)})
    await s.commit()
    return {"ok": True, "periods": out}
