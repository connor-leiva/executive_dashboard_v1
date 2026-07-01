from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import current_user
from ..models import User
from ..schemas import DashboardResponse
from ..services.metrics import build_dashboard

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardResponse)
async def dashboard(
    period: str = Query("mtd"),     # mtd | qtd | ytd | last_month
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session),
):
    return await build_dashboard(s, user.tenant_id, period)
