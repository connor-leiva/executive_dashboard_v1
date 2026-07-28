"""beCollective Launch section — read + CRUD (SPEC-becollective-launch §8 + §11).

View is gated on the program tab (becollective); edits are owner/admin and audited.
Reroute/pricing edits never touch synced opp data — they only reprice on the next read."""
import datetime as dt
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import current_user, require_role, assert_tab
from ..models import User, Business, Launch
from ..schemas import LaunchResponse, LaunchUpsert
from ..services.audit import audit
from ..services.launch import (
    compute_launch, active_launch_for, config_out,
    DEFAULT_STAGE_MAP, DEFAULT_PAYMENT_PLAN_MAP,
)
from ..services.tabs import PROGRAM_TABS

router = APIRouter(tags=["launches"])

_DATE_FIELDS = {"event_start", "event_end", "window_start", "window_end"}
_NUM_FIELDS = {"goal_arr", "ticket_pif", "ticket_plan", "mix_pif", "pace_tolerance"}
_REQUIRED = {"name", "window_start", "window_end", "goal_arr",
             "ticket_pif", "ticket_plan", "pipeline_match"}


def _launch_tab(b: Business) -> str:
    """The nav tab this business's launch renders under (beCollective, else own key)."""
    tabs = PROGRAM_TABS.get(b.key)
    return "becollective" if (tabs and "becollective" in tabs) else b.key


async def _biz(s: AsyncSession, tenant_id, key: str) -> Business:
    b = (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id, Business.key == key))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Unknown business")
    return b


def _pdate(v):
    if v in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        raise HTTPException(400, f"Bad date: {v!r}")


def _apply(launch: Launch, fields: dict) -> None:
    for name, val in fields.items():
        if name in _DATE_FIELDS:
            val = _pdate(val)
        elif name in _NUM_FIELDS and val is not None:
            try:
                val = Decimal(str(val))
            except (InvalidOperation, ValueError):
                raise HTTPException(400, f"{name} must be a number")
        setattr(launch, name, val)


async def _find_launch(s, tenant_id, b: Business, launch_id: str) -> Launch:
    launch = (await s.execute(select(Launch).where(   # GUID() coerces the string id
        Launch.tenant_id == tenant_id, Launch.business_id == b.id,
        Launch.id == launch_id))).scalar_one_or_none()
    if not launch:
        raise HTTPException(404, "Unknown launch")
    return launch


@router.get("/businesses/{key}/launches/active", response_model=LaunchResponse)
async def active_launch(key: str, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session)):
    """The one launch to render in the section. 404 when none is active (section absent)."""
    b = await _biz(s, user.tenant_id, key)
    await assert_tab(user, s, _launch_tab(b))
    launch = await active_launch_for(s, user.tenant_id, b.id)
    if not launch:
        raise HTTPException(404, "No active launch")
    return await compute_launch(s, user.tenant_id, launch)


@router.get("/businesses/{key}/launches")
async def list_launches(key: str, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session)):
    b = await _biz(s, user.tenant_id, key)
    await assert_tab(user, s, _launch_tab(b))
    rows = (await s.execute(select(Launch).where(
        Launch.tenant_id == user.tenant_id, Launch.business_id == b.id)
        .order_by(Launch.window_start.desc()))).scalars().all()
    return [{"id": str(l.id), "is_active": l.is_active, **config_out(l)} for l in rows]


@router.post("/businesses/{key}/launches", response_model=LaunchResponse, status_code=201)
async def create_launch(key: str, body: LaunchUpsert,
                        user: User = Depends(require_role("owner", "admin")),
                        s: AsyncSession = Depends(get_session)):
    b = await _biz(s, user.tenant_id, key)
    fields = body.model_dump(exclude_unset=True)
    missing = _REQUIRED - set(fields)
    if missing:
        raise HTTPException(400, f"Missing required: {sorted(missing)}")
    launch = Launch(
        tenant_id=user.tenant_id, business_id=b.id,
        stage_map=fields.pop("stage_map", None) or DEFAULT_STAGE_MAP,
        payment_plan_map=fields.pop("payment_plan_map", None) or DEFAULT_PAYMENT_PLAN_MAP,
    )
    _apply(launch, fields)
    s.add(launch)
    await s.flush()
    audit(s, user.tenant_id, user.id, "launch.create", "launch", launch.id, {"name": launch.name})
    await s.commit()
    return await compute_launch(s, user.tenant_id, launch)


@router.put("/businesses/{key}/launches/{launch_id}", response_model=LaunchResponse)
async def update_launch(key: str, launch_id: str, body: LaunchUpsert,
                        user: User = Depends(require_role("owner", "admin")),
                        s: AsyncSession = Depends(get_session)):
    b = await _biz(s, user.tenant_id, key)
    launch = await _find_launch(s, user.tenant_id, b, launch_id)
    fields = body.model_dump(exclude_unset=True)
    _apply(launch, fields)
    audit(s, user.tenant_id, user.id, "launch.update", "launch", launch.id, {"fields": sorted(fields)})
    await s.commit()
    return await compute_launch(s, user.tenant_id, launch)
