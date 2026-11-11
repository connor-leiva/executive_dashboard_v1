"""Session Attendance. Read is any Spring program tab. Sync and the map are admin."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_session, require_role, require_tab
from ..models import AttendancePerson, AttendanceSession, AttendanceSync, User
from ..services.attendance import (DEFAULT_RULES, UNMAPPED, classify_topic, run_for_tenant,
                                   sync_range, validate_rules)
from ..services.audit import audit

router = APIRouter()


async def _program(user: User = Depends(require_tab("edge"))) -> User:
    return user


@router.get("/attendance")
async def overview(user: User = Depends(_program), s: AsyncSession = Depends(get_session)):
    state = (await s.execute(select(AttendanceSync).where(
        AttendanceSync.tenant_id == user.tenant_id))).scalar_one_or_none()
    rules = (state.rules if state and state.rules is not None else None)
    sessions = list((await s.execute(select(AttendanceSession).where(
        AttendanceSession.tenant_id == user.tenant_id).order_by(
            AttendanceSession.started_at.desc()))).scalars())
    counts = {}
    if sessions:
        rows = (await s.execute(select(AttendancePerson.session_id).where(
            AttendancePerson.tenant_id == user.tenant_id))).all()
        for (sid,) in rows:
            counts[sid] = counts.get(sid, 0) + 1
    listed = []
    unmapped = []
    by_type: dict = {}
    for session in sessions:
        label = classify_topic(session.topic, rules)
        n = counts.get(session.id, 0)
        item = {
            "id": str(session.id), "topic": session.topic, "kind": session.kind,
            "type": label, "host": session.host_email,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "duration_minutes": session.duration_minutes, "participants": n,
            "source": session.source,
        }
        listed.append(item)
        bucket = by_type.setdefault(label, {"sessions": 0, "participants": 0})
        bucket["sessions"] += 1
        bucket["participants"] += n
        if label == UNMAPPED and session.topic not in unmapped:
            unmapped.append(session.topic)
    return {
        "connected": True,
        "configured": True,
        "last_synced_at": state.last_synced_at.isoformat() if state and state.last_synced_at else None,
        "last_error": state.last_error if state else None,
        "backfilled": bool(state and state.backfilled_at),
        "rules": rules,
        "defaults": DEFAULT_RULES,
        "using_defaults": rules is None,
        "by_type": [{"name": name, **vals} for name, vals in sorted(by_type.items(), key=lambda kv: -kv[1]["participants"])],
        "unmapped": unmapped,
        "sessions": listed[:200],
        "session_count": len(sessions),
    }


@router.post("/attendance/sync")
async def sync_now(body: dict | None = None, user: User = Depends(require_role("owner", "admin")),
                   s: AsyncSession = Depends(get_session)):
    body = body or {}
    if body.get("backfill"):
        result = await run_for_tenant(s, user.tenant_id, backfill=True)
    else:
        today = dt.date.today()
        result = await sync_range(s, user.tenant_id, today - dt.timedelta(days=14), today)
    audit(s, user.tenant_id, user.id, "attendance.synced", "attendance", user.tenant_id,
          {"backfill": bool(body.get("backfill")), "ok": result.get("ok")},
          category="Integrations", summary="Synced Zoom attendance")
    await s.commit()
    if not result.get("ok"):
        raise HTTPException(502, result.get("error") or "Zoom sync failed")
    return result


@router.patch("/attendance/rules")
async def save_rules(body: dict, user: User = Depends(require_role("owner", "admin")),
                     s: AsyncSession = Depends(get_session)):
    from ..services.attendance import _sync_row
    problems = validate_rules(body.get("rules"))
    if problems:
        raise HTTPException(400, "; ".join(problems))
    state = await _sync_row(s, user.tenant_id)
    state.rules = body.get("rules")
    audit(s, user.tenant_id, user.id, "attendance.rules", "attendance", state.id,
          {"rules": state.rules}, category="Integrations", summary="Updated session type mapping")
    await s.commit()
    return {"ok": True, "using_defaults": state.rules is None}
