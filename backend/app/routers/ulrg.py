"""ULRG L10 Scorecard + Team Rooms — API (SPEC-ulrg-scorecard Part 5).

Read is gated by the `ulrg` tab grant; all sub-tabs inherit it (team-level scoping is out of
scope, Part 8). Manual entry is owner/admin or the metric's own owner. All math is server-side
(services/scorecard.py) — this router only shapes requests and guards writes.
"""
from __future__ import annotations

import uuid

import datetime as dt
import mimetypes
import secrets
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from .. import plans
from ..deps import current_user, require_tab
from ..models import (User, Business, Tenant, ScorecardGroup, ScorecardMetric, ScorecardValue,
                      ScorecardGoal, ShareLink)
from ..services import binder_storage, scorecard
from ..services.audit import audit
from ..tenancy import tenant_app_url
from ..services import roles
from ..services.scorecard_resolvers import resolver_records
from ..services.tabs import effective_tabs, tenant_tabs

router = APIRouter(prefix="/ulrg", tags=["ulrg"])


# ── scorecard scopes ─────────────────────────────────────────────────────────────────────────────
# A scorecard is a BUSINESS (resolved by kind, so it's tenant-agnostic — no hardcoded keys) plus the
# tabs that grant access to it. ULRG lives under the ULRG tab; the Spring B board is company-wide and
# reachable from any of its brand tabs. Adding a scorecard is one row here plus a seed. The routes stay
# under /ulrg for now (ULRG shipped first); `scope` selects which board — default "ulrg" keeps every
# existing ULRG call identical.
SCORECARD_SCOPES = {
    "ulrg":    {"kind": roles.REAL_ESTATE, "tabs": ("ulrg",)},
    "springb": {"kind": roles.MEMBERSHIP,  "tabs": ("forum", "becollective", "edge")},
}


async def _ulrg_business(s, tenant_id):
    b = await roles.real_estate(s, tenant_id)
    if not b:
        raise HTTPException(404, "No ULRG business for this tenant")
    return b


async def _scope_business(s, tenant_id, scope: str) -> Business:
    sc = SCORECARD_SCOPES.get(scope)
    if not sc:
        raise HTTPException(404, "Unknown scorecard")
    b = await roles.primary(s, tenant_id, sc["kind"])
    if not b:
        raise HTTPException(404, "This scorecard has no business for this tenant")
    return b


async def _assert_scope_view(user: User, s, scope: str) -> None:
    """403 unless the user can see the scorecard — i.e. holds ANY of the scope's tabs (owners/admins
    pass implicitly, exactly like require_tab)."""
    sc = SCORECARD_SCOPES.get(scope)
    if not sc:
        raise HTTPException(404, "Unknown scorecard")
    seen = set(effective_tabs(user, await tenant_tabs(s, user.tenant_id),
                              tenant=await s.get(Tenant, user.tenant_id)))
    if not seen & set(sc["tabs"]):
        raise HTTPException(403, "No access to this scorecard")


async def _metric_scope(s, m: ScorecardMetric) -> str | None:
    """The scorecard scope a metric belongs to, via its group's business kind — so a write endpoint
    can gate on the RIGHT board without the client naming it."""
    g = await s.get(ScorecardGroup, m.group_id)
    b = await s.get(Business, g.business_id) if g else None
    if b:
        for scope, sc in SCORECARD_SCOPES.items():
            if b.kind == sc["kind"]:
                return scope
    return None


@router.get("/scorecard")
async def get_scorecard(weeks: int = Query(13, ge=1, le=52), scope: str = "ulrg",
                        user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session)):
    await _assert_scope_view(user, s, scope)
    b = await _scope_business(s, user.tenant_id, scope)
    return await scorecard.build_scorecard(s, user.tenant_id, b.id, weeks)


@router.get("/metric/{metric_id}/records")
async def metric_records(metric_id: str, week_start: str, week_end: str,
                         user: User = Depends(current_user),
                         s: AsyncSession = Depends(get_session)):
    """The underlying Sisu deals behind a figure — the grid drill-down (auth-only; carries client
    names, so it's never exposed on the public embed). Auto/resolver metrics return the deals that
    make up the count for [week_start, week_end]; a manual metric has no source records (the client
    opens the value editor instead). Gated on the metric's own board (ULRG or Spring B)."""
    row = (await s.execute(
        select(ScorecardMetric, ScorecardGroup.business_id, ScorecardGroup.sisu_group_id)
        .join(ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id)
        .where(ScorecardMetric.tenant_id == user.tenant_id, ScorecardMetric.id == metric_id))).first()
    if not row:
        raise HTTPException(404, "Unknown metric")
    m, business_id, sisu_group_id = row
    scope = await _metric_scope(s, m)
    if scope is None:
        raise HTTPException(404, "Unknown metric")
    await _assert_scope_view(user, s, scope)
    if not m.resolver_key:
        return {"source": "manual", "measurable": m.name, "records": []}
    try:
        ws, we = dt.date.fromisoformat(week_start), dt.date.fromisoformat(week_end)
    except ValueError:
        raise HTTPException(400, "week_start / week_end must be ISO dates")
    recs = await resolver_records(s, user.tenant_id, business_id, m.resolver_key, ws, we,
                                  group={"sisu_group_id": sisu_group_id})
    return {"source": "resolver", "measurable": m.name, "records": recs or []}


class ValueIn(BaseModel):
    metric_id: str
    week_start: str                 # ISO date (Monday of the ISO week)
    value: float | None = None      # null ≠ zero — null clears a value


@router.post("/scorecard/values", status_code=201)
async def set_value(body: ValueIn, user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session)):
    """Manual entry (Part 3.3). SELF-SERVE: anyone who can see the metric's scorecard may edit a
    hand-entered measurable — these are KPIs individuals own and update themselves, not an admin-only
    task. An AUTO (resolver-sourced) row stays locked to owner/admin (or the metric's owner): a member
    must not hand-override a live feed — that's an audited correction, and the next resolver run would
    revert it anyway. Works for any board (ULRG or Spring B); the gate follows the metric's scope."""
    m = (await s.execute(select(ScorecardMetric).where(
        ScorecardMetric.tenant_id == user.tenant_id, ScorecardMetric.id == body.metric_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(404, "Unknown metric")
    scope = await _metric_scope(s, m)
    if scope is None:
        raise HTTPException(404, "Unknown metric")
    await _assert_scope_view(user, s, scope)          # must be able to see the board this metric is on
    if m.resolver_key and user.role not in ("owner", "admin") and m.owner_user_id != user.id:
        raise HTTPException(403, "This measurable is auto-sourced — only an owner or admin can override it")
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


async def _share_url(s, tenant_id, token: str) -> str:
    # The web app serves the embed page; it lives on the TENANT's origin, so an embed pasted
    # into ClickUp resolves to that customer's app rather than to the platform's first tenant.
    return f"{await tenant_app_url(s, tenant_id)}/share/{token}"


@router.post("/share", status_code=201)
async def create_share(body: ShareIn, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session)):
    """Mint a read-only share token for the scorecard (owner/admin). Returns {token, url}; the url is
    the web-app embed page ClickUp iframes."""
    _require_admin(user)
    if body.scope != "ulrg_scorecard":                   # team-room sharing arrives with Step 6
        raise HTTPException(400, "Only the full scorecard can be shared yet")
    tenant = await s.get(Tenant, user.tenant_id)
    # Counts only LIVE links. A revoked one is not occupying anything, and making somebody delete
    # history to mint a new share would be a limit that punishes tidiness.
    live = (await s.execute(select(func.count()).select_from(ShareLink).where(
        ShareLink.tenant_id == user.tenant_id, ShareLink.revoked_at.is_(None)))).scalar_one()
    if plans.over_limit(tenant, "max_share_links", live):
        lim = plans.limits(tenant)
        raise HTTPException(402, f"The {lim['name']} plan includes {lim['max_share_links']} "
                                 f"active share links and this workspace has {live}. Revoke one "
                                 f"or upgrade.")
    token = secrets.token_urlsafe(32)
    s.add(ShareLink(tenant_id=user.tenant_id, scope=body.scope, scope_ref=body.scope_ref,
                    token=token, created_by=user.id))
    audit(s, user.tenant_id, user.id, "scorecard.share_created", "share_link", None, {"scope": body.scope})
    await s.commit()
    return {"token": token, "url": await _share_url(s, user.tenant_id, token)}


@router.get("/shares")
async def list_shares(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Live (non-revoked) share links for this tenant, owner/admin."""
    _require_admin(user)
    rows = (await s.execute(select(ShareLink).where(
        ShareLink.tenant_id == user.tenant_id, ShareLink.revoked_at.is_(None))
        .order_by(ShareLink.created_at.desc()))).scalars().all()
    return [{"id": str(link.id), "scope": link.scope, "scope_ref": link.scope_ref,
             "created_at": link.created_at.isoformat() if link.created_at else None,
             "url": await _share_url(s, user.tenant_id, link.token)} for link in rows]


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
async def group_photo(group_id: uuid.UUID, s: AsyncSession = Depends(get_session)):
    """Serve a headshot — PUBLIC (no auth) so it shows in the app AND the read-only ClickUp embed.
    Not sensitive; the group id is an unguessable UUID — which is also why the id is typed as
    one. Declared `str`, a malformed id reached the GUID bind, which parses on Postgres but not
    on SQLite: a clean 404 in dev and the whole test suite, an unhandled 500 in production. The
    UUID type makes FastAPI reject it at the boundary with a 422, on both."""
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


# ── measurables (rename / remove) — generic names so a static number never goes stale in the label ─
class MetricPatch(BaseModel):
    name: str | None = None
    active: bool | None = None      # False removes the row from the scorecard (soft-delete; history kept)


@router.patch("/metric/{metric_id}")
async def edit_metric(metric_id: str, body: MetricPatch, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session)):
    """Edit a measurable (owner/admin), self-service. Rename — e.g. '130 Homes Sold Q2' → 'Total Homes
    Sold (Current Quarter)' (display only; auto-sourcing keys off resolver_key, not the name). Remove —
    `active=false` drops the row from the scorecard everywhere (build_scorecard + the goals editor
    filter active); it's a SOFT delete, so the weekly history is kept and an admin can restore it by
    setting active=true. A resolver-backed row simply stops being resolved while inactive."""
    _require_admin(user)
    m = (await s.execute(select(ScorecardMetric).where(
        ScorecardMetric.tenant_id == user.tenant_id, ScorecardMetric.id == metric_id))).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, "Unknown metric")
    changed = False
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Name required")
        m.name = name[:160]
        audit(s, user.tenant_id, user.id, "scorecard.metric_rename", "scorecard_metric", m.id, {"name": m.name})
        changed = True
    if body.active is not None:
        m.active = body.active
        audit(s, user.tenant_id, user.id, "scorecard.metric_active", "scorecard_metric", m.id, {"active": body.active})
        changed = True
    if not changed:
        raise HTTPException(400, "Nothing to update")
    await s.commit()
    return {"ok": True, "name": m.name, "active": m.active}


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


# ── per-period goals (Phase C) — a goal per (metric, period); absent → the metric's default ─────
class GoalIn(BaseModel):
    metric_id: str
    goal: float                                 # weekly goal
    cumulative_goal: float | None = None        # period total (flow only); None clears any override


class GoalsIn(BaseModel):
    period: str
    goals: list[GoalIn]


@router.get("/goals")
async def get_goals(period: str, scope: str = "ulrg", user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session)):
    """Each active measurable's weekly + cumulative goal for a period (override if set, else the
    metric default / no cumulative), grouped for the editor. owner/admin. Scoped to ONE board so the
    ULRG and Spring B measurables editors never show each other's rows."""
    _require_admin(user)
    b = await _scope_business(s, user.tenant_id, scope)
    rows = (await s.execute(
        select(ScorecardMetric, ScorecardGroup.name, ScorecardGroup.sort_order)
        .join(ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id)
        .where(ScorecardMetric.tenant_id == user.tenant_id, ScorecardGroup.business_id == b.id,
               ScorecardMetric.active.is_(True))
        .order_by(ScorecardGroup.sort_order, ScorecardMetric.sort_order))).all()
    over = {str(mid): (float(gv), None if cg is None else float(cg))
            for mid, gv, cg in (await s.execute(select(
                ScorecardGoal.metric_id, ScorecardGoal.goal, ScorecardGoal.cumulative_goal).where(
                ScorecardGoal.tenant_id == user.tenant_id, ScorecardGoal.period_key == period))).all()}
    goals = [{"metric_id": str(m.id), "name": m.name, "group": gname, "type": m.type,
              "default": float(m.goal), "goal": over.get(str(m.id), (float(m.goal), None))[0],
              "cumulative_goal": over.get(str(m.id), (None, None))[1],
              "supports_cumulative": m.type == "flow",   # only flow metrics accumulate toward a total
              "overridden": str(m.id) in over}
             for m, gname, _ in rows]
    return {"period": period, "goals": goals}


@router.put("/goals")
async def set_goals(body: GoalsIn, user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session)):
    """Upsert per-period goals for a period (owner/admin). Only touches this period, so other periods
    keep their goals."""
    _require_admin(user)
    valid = {str(mid) for (mid,) in (await s.execute(select(ScorecardMetric.id).where(
        ScorecardMetric.tenant_id == user.tenant_id))).all()}
    targets = [g for g in body.goals if g.metric_id in valid]

    def _cum(g) -> Decimal | None:
        # a period total ≤ 0 is meaningless → clear it (fall back to weekly × weeks)
        return Decimal(str(g.cumulative_goal)) if (g.cumulative_goal and g.cumulative_goal > 0) else None

    async def _apply() -> int:
        existing = {str(r.metric_id): r for r in (await s.execute(select(ScorecardGoal).where(
            ScorecardGoal.tenant_id == user.tenant_id, ScorecardGoal.period_key == body.period))).scalars()}
        for g in targets:
            row = existing.get(g.metric_id)
            if row is not None:
                row.goal = Decimal(str(g.goal))
                row.cumulative_goal = _cum(g)
            else:
                s.add(ScorecardGoal(tenant_id=user.tenant_id, metric_id=g.metric_id,
                                    period_key=body.period, goal=Decimal(str(g.goal)),
                                    cumulative_goal=_cum(g)))
        return len(targets)

    n = await _apply()
    audit(s, user.tenant_id, user.id, "scorecard.goals_set", "tenant", user.tenant_id,
          {"period": body.period, "count": n})
    try:
        await s.commit()
    except IntegrityError:                              # a concurrent PUT won the insert race for this period
        await s.rollback()
        n = await _apply()
        audit(s, user.tenant_id, user.id, "scorecard.goals_set", "tenant", user.tenant_id,
              {"period": body.period, "count": n})
        await s.commit()
    return {"ok": True, "count": n}
