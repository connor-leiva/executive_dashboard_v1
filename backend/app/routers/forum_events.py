"""The Forum's quarterly VIP events — read + CRUD. FORUM-EVENT-SPEC.md §5.

Reads are gated on the FORUM tab, not a tab of their own: the Event sub-tab lives inside the
Forum and is granted with it. Deliberately NOT reusing `_launch_tab` from launches.py, which
hardcodes `return "becollective" if (tabs and "becollective" in tabs) else b.key` — copying it
would gate Forum events behind the beCollective tab for every workspace that has both.

Writes are owner/admin and audited, like every other write in this codebase.

A 404 from `/current` is the designed signal that the sub-tab is ABSENT, not that anything is
broken. The frontend hook treats it that way, exactly as useLaunch does.
"""
import datetime as dt
import uuid
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import current_user, require_role, assert_tab
from ..models import Business, ForumEvent, User
from ..schemas import ForumEventUpsert
from ..services import roles
from ..services.audit import audit
from ..services.forum_event import (
    DEFAULT_STAGE_MAP, active_event, compute_event, config_out, drill_event,
)

router = APIRouter(tags=["forum-events"])

_DATE_FIELDS = {"starts_on", "ends_on", "window_start", "window_end"}
_NUM_FIELDS = {"vip_price", "pace_tolerance"}
_REQUIRED = {"name", "slug"}


def _event_tab(b: Business) -> str:
    """The nav tab an event renders under. Events belong to the MEMBERSHIP business, and the
    membership tab is resolved by KIND rather than by a key literal — `Business.key == "springb"`
    is the thing roles.py exists to stamp out."""
    return b.key if b.kind != roles.MEMBERSHIP else "forum"


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


def _apply(ev: ForumEvent, fields: dict) -> None:
    """Coerce in ONE declarative place. A new date field that is not added to `_DATE_FIELDS`
    is stored as a raw string and compares wrong forever — the same trap launches.py documents."""
    for name, val in fields.items():
        if name in _DATE_FIELDS:
            val = _pdate(val)
        elif name in _NUM_FIELDS and val is not None:
            try:
                val = Decimal(str(val))
            except (InvalidOperation, ValueError):
                raise HTTPException(400, f"{name} must be a number")
        setattr(ev, name, val)


async def _find(s: AsyncSession, tenant_id, b: Business, event_id: str) -> ForumEvent:
    """Re-resolve the event from the PATH business on every sub-route. Checking the tab on one
    business and then reading a child row by id alone is how a tab grant on one business reached
    another's data once already (launches.py:204)."""
    try:
        eid = uuid.UUID(str(event_id))
    except ValueError:
        raise HTTPException(404, "No such event")
    ev = (await s.execute(select(ForumEvent).where(
        ForumEvent.id == eid,
        ForumEvent.tenant_id == tenant_id,
        ForumEvent.business_id == b.id))).scalar_one_or_none()
    if ev is None:
        raise HTTPException(404, "No such event")
    return ev


@router.get("/businesses/{key}/events")
async def list_events(key: str, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session)):
    b = await _biz(s, user.tenant_id, key)
    await assert_tab(user, s, _event_tab(b))
    rows = (await s.execute(select(ForumEvent).where(
        ForumEvent.tenant_id == user.tenant_id,
        ForumEvent.business_id == b.id).order_by(
            ForumEvent.starts_on.desc().nullslast()))).scalars().all()
    return [config_out(e) for e in rows]


@router.get("/businesses/{key}/events/current")
async def current_event(key: str, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session)):
    """404 when no event is configured. That is the sub-tab being ABSENT, not an error —
    never return an empty shell, which renders as a tab full of zeroes."""
    b = await _biz(s, user.tenant_id, key)
    await assert_tab(user, s, _event_tab(b))
    ev = await active_event(s, user.tenant_id, b.id)
    if ev is None:
        raise HTTPException(404, "No active event")
    return await compute_event(s, user.tenant_id, ev)


@router.get("/businesses/{key}/events/{event_id}")
async def one_event(key: str, event_id: str, user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session)):
    b = await _biz(s, user.tenant_id, key)
    await assert_tab(user, s, _event_tab(b))
    ev = await _find(s, user.tenant_id, b, event_id)
    return await compute_event(s, user.tenant_id, ev)


@router.get("/businesses/{key}/events/{event_id}/drill/{metric}")
async def drill(key: str, event_id: str, metric: str, user: User = Depends(current_user),
                s: AsyncSession = Depends(get_session)):
    """The people behind one figure. 404 on an unknown metric rather than an empty drawer -
    `drill_launch` returns a soft "no drill-down defined yet", which turns a typo in the
    frontend into a figure that opens onto nothing and looks like missing data."""
    b = await _biz(s, user.tenant_id, key)
    await assert_tab(user, s, _event_tab(b))
    ev = await _find(s, user.tenant_id, b, event_id)
    try:
        return await drill_event(s, user.tenant_id, ev, metric)
    except KeyError:
        raise HTTPException(404, f"No drill-down for {metric!r}")


@router.post("/businesses/{key}/events", status_code=201)
async def create_event(key: str, body: ForumEventUpsert,
                       user: User = Depends(require_role("owner", "admin")),
                       s: AsyncSession = Depends(get_session)):
    b = await _biz(s, user.tenant_id, key)
    await assert_tab(user, s, _event_tab(b))
    fields = body.model_dump(exclude_unset=True)
    missing = _REQUIRED - set(fields)
    if missing:
        raise HTTPException(400, f"Missing required: {sorted(missing)}")
    ev = ForumEvent(tenant_id=user.tenant_id, business_id=b.id,
                    stage_map=fields.pop("stage_map", None) or DEFAULT_STAGE_MAP)
    _apply(ev, fields)
    s.add(ev)
    await s.flush()
    audit(s, user.tenant_id, user.id, "forum_event.create", "forum_event", ev.id,
          {"name": ev.name, "slug": ev.slug})
    await s.commit()
    return await compute_event(s, user.tenant_id, ev)


@router.put("/businesses/{key}/events/{event_id}")
async def update_event(key: str, event_id: str, body: ForumEventUpsert,
                       user: User = Depends(require_role("owner", "admin")),
                       s: AsyncSession = Depends(get_session)):
    """A partial patch. JSON columns are REPLACED whole — the settings drawer merges before it
    sends, which is how it can render a subset of a map without blanking the rest."""
    b = await _biz(s, user.tenant_id, key)
    await assert_tab(user, s, _event_tab(b))
    ev = await _find(s, user.tenant_id, b, event_id)
    fields = body.model_dump(exclude_unset=True)
    _apply(ev, fields)
    audit(s, user.tenant_id, user.id, "forum_event.update", "forum_event", ev.id,
          {"fields": sorted(fields)})
    await s.commit()
    return await compute_event(s, user.tenant_id, ev)
