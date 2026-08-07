"""ULRG L10 Scorecard + Team Rooms — API (SPEC-ulrg-scorecard Part 5).

Read is gated by the `ulrg` tab grant; all sub-tabs inherit it (team-level scoping is out of
scope, Part 8). Manual entry is owner/admin or the metric's own owner. All math is server-side
(services/scorecard.py) — this router only shapes requests and guards writes.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import current_user, require_tab
from ..models import User, Business, ScorecardMetric, ScorecardValue
from ..services import scorecard
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
