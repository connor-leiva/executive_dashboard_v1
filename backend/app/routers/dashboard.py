from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import current_user, require_tab, assert_tab
from ..models import User
from ..schemas import DashboardResponse
from ..services.metrics import build_dashboard
from ..services.lineage import metric_detail
from ..services.forum import build_forum
from ..services.becollective import build_becollective
from ..services.edge import build_edge
from ..services.tabs import tenant_tabs, effective_tabs, tab_for_metric, biz_tab_map

router = APIRouter(tags=["dashboard"])


def _filter_dashboard(d: DashboardResponse, tabs: list[str]) -> DashboardResponse:
    """Strip the payload to the user's granted tabs (§2.4). Authorization is enforced
    in the DATA, not just the nav — a member without a tab never receives its numbers."""
    tabset = set(tabs)
    d.areas = {k: v for k, v in d.areas.items() if k in tabset}
    if "portfolio" not in tabset:
        d.portfolio = d.portfolio.model_copy(update={
            "revenue": None, "noi": None, "margin": None, "mom": None, "cash": None, "composition": []})
        d.scorecards = []
    if "flywheel" not in tabset:
        from ..schemas import Flywheel
        d.flywheel = Flywheel(available=False)
    return d


@router.get("/dashboard", response_model=DashboardResponse)
async def dashboard(
    period: str = Query("mtd"),     # mtd | qtd | ytd | last_month
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session),
):
    d = await build_dashboard(s, user.tenant_id, period)
    if user.role in ("owner", "admin"):
        return d
    tabs = effective_tabs(user, await tenant_tabs(s, user.tenant_id))
    return _filter_dashboard(d, tabs)


@router.get("/forum")
async def forum(
    period: str = Query("mtd"),
    user: User = Depends(require_tab("forum")),
    s: AsyncSession = Depends(get_session),
):
    """The Forum focused view — KPIs, deep-dive deck, funnel, renewals, event, cash & billing."""
    return await build_forum(s, user.tenant_id, period)


@router.get("/becollective")
async def becollective(
    period: str = Query("mtd"),
    user: User = Depends(require_tab("becollective")),
    s: AsyncSession = Depends(get_session),
):
    """beCollective focused view — cohort program (mirrors the Forum's shape)."""
    return await build_becollective(s, user.tenant_id, period)


@router.get("/edge")
async def edge(
    period: str = Query("mtd"),
    user: User = Depends(require_tab("edge")),
    s: AsyncSession = Depends(get_session),
):
    """The Edge focused view — Spring + Justin Nelson membership (mirrors the Forum's shape)."""
    return await build_edge(s, user.tenant_id, period)


@router.get("/metrics/{key}/detail")
async def metric_detail_ep(
    key: str,
    period: str = Query("mtd"),
    business: str | None = Query(None),
    agent_id: str | None = Query(None),
    lo: str | None = Query(None),
    stage: str | None = Query(None),
    source: str | None = Query(None),
    stream: str | None = Query(None),
    month: str | None = Query(None),
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session),
):
    """The records behind a KPI + a plain-English 'computed_as' + source links.
    A drill inherits its tile's permission (§2.5) — a member can't reach forum_payments
    detail without the forum tab, even by guessing the URL."""
    await assert_tab(user, s, tab_for_metric(key, business, await biz_tab_map(s, user.tenant_id)))
    return await metric_detail(s, user.tenant_id, key, period, business, agent_id, lo, stage, source, stream, month)
