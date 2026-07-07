from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import current_user
from ..models import User
from ..schemas import DashboardResponse
from ..services.metrics import build_dashboard
from ..services.lineage import metric_detail
from ..services.forum import build_forum
from ..services.becollective import build_becollective

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardResponse)
async def dashboard(
    period: str = Query("mtd"),     # mtd | qtd | ytd | last_month
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session),
):
    return await build_dashboard(s, user.tenant_id, period)


@router.get("/forum")
async def forum(
    period: str = Query("mtd"),
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session),
):
    """The Forum focused view — KPIs, deep-dive deck, funnel, renewals, event, revenue quality."""
    return await build_forum(s, user.tenant_id, period)


@router.get("/becollective")
async def becollective(
    period: str = Query("mtd"),
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session),
):
    """beCollective focused view — cohort program (mirrors the Forum's shape)."""
    return await build_becollective(s, user.tenant_id, period)


@router.get("/metrics/{key}/detail")
async def metric_detail_ep(
    key: str,
    period: str = Query("mtd"),
    business: str | None = Query(None),
    agent_id: str | None = Query(None),
    lo: str | None = Query(None),
    stage: str | None = Query(None),
    source: str | None = Query(None),
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session),
):
    """The records behind a KPI + a plain-English 'computed_as' + source links."""
    return await metric_detail(s, user.tenant_id, key, period, business, agent_id, lo, stage, source)
