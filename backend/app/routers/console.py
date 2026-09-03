from __future__ import annotations

import datetime as dt
import uuid
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import ConsolePrincipal, require_console_access
from ..models import (
    AuditLog,
    IntranetAiSetting,
    IntranetAiSource,
    IntranetCalendarCategory,
    IntranetCalendarCategoryRole,
    IntranetCapability,
    IntranetContentGap,
    IntranetCourse,
    IntranetCourseRole,
    IntranetIntegration,
    IntranetLaunchpadTile,
    IntranetLaunchpadTileRole,
    IntranetLesson,
    IntranetMember,
    IntranetPendingChange,
    IntranetPermission,
    IntranetPublishBatch,
    IntranetRole,
    IntranetSetupTask,
    IntranetSop,
    IntranetSopAcknowledgement,
    IntranetSopCategory,
    IntranetSopVersion,
    IntranetWtdList,
    IntranetWorkspace,
)
from ..services.audit import audit
from ..services import binder_storage

router = APIRouter(prefix="/console", tags=["console"])

LEVELS = {"Full", "View", "Limited", "None"}
COURSE_STATES = {"Draft", "Live", "Needs Review"}
SOP_STATES = {"Draft", "Live", "Needs Review", "Archived"}
MEMBER_STATUSES = {"Active", "Invited", "Removed"}
MEMBER_FILTERS = {"active", "pending", "guests", "leadership", "everyone"}
AUTH_SOURCES = {"SSO", "Guest", "Manual"}
LESSON_SOURCE_TYPES = {"LOOM", "SKOOL", "HERE", "PDF", "EXP", "PLACE"}
TILE_AUTH_TYPES = {"SSO", "Deeplink", "Invite", "Link"}
INTEGRATION_STATUSES = {"Connected", "Action Needed", "Not Connected"}
GAP_STATUSES = {"Open", "Assigned", "Resolved", "No Action"}
LOGO_KINDS = {"light": "logo_light_key", "dark": "logo_dark_key", "mark": "logo_mark_key"}
LOGO_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}
PALETTE_KEYS = {"ink", "brand", "accent", "canvas", "gold"}
MAX_LOGO_BYTES = 2 * 1024 * 1024


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def _id(value: Any) -> str | None:
    return str(value) if value is not None else None


def _list(items: list[dict], total: int | None = None) -> dict:
    return {"items": items, "total": len(items) if total is None else int(total), "cursor": None}


def _unprocessable(field: str, message: str) -> None:
    raise HTTPException(422, {"errors": [{"field": field, "message": message}]})


def _body(body: Any) -> dict:
    if not isinstance(body, dict):
        _unprocessable("body", "Expected a JSON object.")
    return body


def _unknown(body: dict, allowed: set[str]) -> None:
    extra = sorted(set(body) - allowed)
    if extra:
        _unprocessable(extra[0], "Unknown field.")


def _text(body: dict, field: str, *, required: bool = False,
          max_len: int | None = None, nullable: bool = False) -> str | None:
    if field not in body:
        if required:
            _unprocessable(field, "Required.")
        return None
    raw = body.get(field)
    if raw is None and nullable:
        return None
    value = str(raw or "").strip()
    if required and not value:
        _unprocessable(field, "Required.")
    if max_len is not None:
        value = value[:max_len]
    return value


def _bool(body: dict, field: str, default: bool | None = None) -> bool | None:
    if field not in body:
        return default
    if not isinstance(body[field], bool):
        _unprocessable(field, "Expected a boolean.")
    return bool(body[field])


def _int(body: dict, field: str, *, default: int | None = None,
         min_value: int | None = None, max_value: int | None = None) -> int | None:
    if field not in body:
        return default
    if body[field] is None:
        return None
    try:
        value = int(body[field])
    except (TypeError, ValueError):
        _unprocessable(field, "Expected an integer.")
    if min_value is not None and value < min_value:
        _unprocessable(field, f"Must be at least {min_value}.")
    if max_value is not None and value > max_value:
        _unprocessable(field, f"Must be at most {max_value}.")
    return value


def _enum(body: dict, field: str, allowed: set[str], default: str | None = None) -> str | None:
    if field not in body:
        return default
    value = str(body.get(field) or "").strip()
    if value not in allowed:
        _unprocessable(field, f"Must be one of: {', '.join(sorted(allowed))}.")
    return value


def _uuid_value(body: dict, field: str, *, required: bool = False,
                nullable: bool = False) -> uuid.UUID | None:
    if field not in body:
        if required:
            _unprocessable(field, "Required.")
        return None
    if body[field] is None and nullable:
        return None
    try:
        return uuid.UUID(str(body[field]))
    except (TypeError, ValueError):
        _unprocessable(field, "Expected a UUID.")


def _date_value(body: dict, field: str) -> dt.date | None:
    if field not in body:
        return None
    if body[field] in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(body[field]))
    except ValueError:
        _unprocessable(field, "Expected an ISO date.")


def _json_object(body: dict, field: str, default: dict | None = None) -> dict | None:
    if field not in body:
        return default
    if not isinstance(body[field], dict):
        _unprocessable(field, "Expected an object.")
    return dict(body[field])


def _hex_color(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 7
        and value.startswith("#")
        and all(ch in "0123456789abcdefABCDEF" for ch in value[1:])
    )


def _palette(body: dict) -> dict:
    palette = _json_object(body, "palette") or {}
    clean = {}
    for key, value in palette.items():
        if key not in PALETTE_KEYS:
            _unprocessable("palette", f"Unknown swatch {key}.")
        if not _hex_color(value):
            _unprocessable(f"palette.{key}", "Expected #RRGGBB.")
        clean[key] = value.upper()
    return clean


def _https_url(body: dict, field: str, *, required: bool = False) -> str | None:
    raw = _text(body, field, required=required)
    if not raw:
        return None
    value = raw.strip()
    parsed = urlparse(value)
    if not parsed.scheme:
        value = f"https://{value}"
        parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme in {"javascript", "data"} or scheme not in {"http", "https"} or not parsed.netloc:
        _unprocessable(field, "Expected a valid http or https URL.")
    if scheme == "http":
        value = "https://" + value[len("http://"):]
    return value


async def _pending_count(s: AsyncSession, tenant_id) -> int:
    return int((await s.execute(select(func.count()).select_from(IntranetPendingChange).where(
        IntranetPendingChange.tenant_id == tenant_id,
        IntranetPendingChange.publish_batch_id.is_(None),
    ))).scalar_one())


async def _count(s: AsyncSession, model, tenant_id, *where) -> int:
    stmt = select(func.count()).select_from(model).where(model.tenant_id == tenant_id, *where)
    return int((await s.execute(stmt)).scalar_one())


async def _one(s: AsyncSession, model, tenant_id, row_id: uuid.UUID):
    row = (await s.execute(select(model).where(model.id == row_id, model.tenant_id == tenant_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Not found.")
    return row


async def _role(s: AsyncSession, tenant_id, role_id: uuid.UUID) -> IntranetRole:
    return await _one(s, IntranetRole, tenant_id, role_id)


async def _roles_by_id(s: AsyncSession, tenant_id) -> dict[uuid.UUID, IntranetRole]:
    rows = (await s.execute(select(IntranetRole).where(
        IntranetRole.tenant_id == tenant_id).order_by(IntranetRole.sort, IntranetRole.name))).scalars().all()
    return {r.id: r for r in rows}


async def _role_by_key(s: AsyncSession, tenant_id, key: str) -> IntranetRole:
    row = (await s.execute(select(IntranetRole).where(
        IntranetRole.tenant_id == tenant_id,
        IntranetRole.key == key,
    ))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Role not found.")
    return row


async def _member_by_id_or_none(s: AsyncSession, tenant_id, member_id: uuid.UUID | None) -> IntranetMember | None:
    if member_id is None:
        return None
    return await _one(s, IntranetMember, tenant_id, member_id)


async def _record_mutation(
    s: AsyncSession,
    p: ConsolePrincipal,
    *,
    action: str,
    category: str,
    summary: str,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    detail: dict | None = None,
    pending: bool = True,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    change_kind: str = "updated",
) -> int:
    if pending:
        s.add(IntranetPendingChange(
            tenant_id=p.user.tenant_id,
            entity_type=entity_type or target_type or "config",
            entity_id=entity_id if isinstance(entity_id, uuid.UUID) else None,
            change_kind=change_kind,
            summary=summary,
            detail=None,
            actor_member_id=p.member.id,
        ))
    audit(
        s,
        p.user.tenant_id,
        p.user.id,
        action,
        target_type,
        str(target_id) if target_id is not None else None,
        detail,
        category=category,
        summary=summary,
        actor_member_id=p.member.id,
        actor_label=p.member.full_name,
        metadata={"console": True},
    )
    await s.flush()
    pending_count = await _pending_count(s, p.user.tenant_id)
    await s.commit()
    return pending_count


def _with_pending(entity: dict, pending_changes: int) -> dict:
    return {"item": entity, "pending_changes": pending_changes}


def _workspace(row: IntranetWorkspace) -> dict:
    return {
        "id": _id(row.id),
        "portal_name": row.portal_name,
        "tagline": row.tagline,
        "subdomain": row.subdomain,
        "custom_domain": row.custom_domain,
        "custom_domain_verified_at": _iso(row.custom_domain_verified_at),
        "palette": row.palette or {},
        "logo_light_key": row.logo_light_key,
        "logo_dark_key": row.logo_dark_key,
        "logo_mark_key": row.logo_mark_key,
        "timezone": row.timezone,
        "week_starts_on": row.week_starts_on,
        "default_calendar_view": row.default_calendar_view,
        "published_at": _iso(row.published_at),
        "draft_dirty": bool(row.draft_dirty),
    }


def _role_out(row: IntranetRole) -> dict:
    return {
        "id": _id(row.id), "key": row.key, "name": row.name, "sort": row.sort,
        "is_leadership": bool(row.is_leadership), "published_at": _iso(row.published_at),
        "draft_dirty": bool(row.draft_dirty),
    }


def _capability(row: IntranetCapability) -> dict:
    return {
        "id": _id(row.id), "key": row.key, "name": row.name,
        "description": row.description, "sort": row.sort,
        "published_at": _iso(row.published_at), "draft_dirty": bool(row.draft_dirty),
    }


def _permission(row: IntranetPermission) -> dict:
    return {
        "id": _id(row.id), "capability_id": _id(row.capability_id),
        "role_id": _id(row.role_id), "level": row.level,
        "published_at": _iso(row.published_at), "draft_dirty": bool(row.draft_dirty),
    }


def _member(row: IntranetMember, roles: dict[uuid.UUID, IntranetRole] | None = None) -> dict:
    role = roles.get(row.role_id) if roles else None
    return {
        "id": _id(row.id), "user_id": _id(row.user_id), "full_name": row.full_name,
        "email": row.email, "role_id": _id(row.role_id), "role_key": role.key if role else None,
        "role_name": role.name if role else None, "market": row.market,
        "auth_source": row.auth_source, "status": row.status,
        "invited_at": _iso(row.invited_at), "activated_at": _iso(row.activated_at),
        "removed_at": _iso(row.removed_at), "last_synced_at": _iso(row.last_synced_at),
    }


async def _member_stats(s: AsyncSession, tenant_id, roles: dict[uuid.UUID, IntranetRole]) -> dict:
    leadership_ids = [role_id for role_id, role in roles.items() if role.is_leadership]
    guest_role = next((role for role in roles.values() if role.key == "jv_partner"), None)
    month_start = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_synced_at = (await s.execute(select(func.max(IntranetMember.last_synced_at)).where(
        IntranetMember.tenant_id == tenant_id,
    ))).scalar_one_or_none()
    return {
        "active": await _count(s, IntranetMember, tenant_id, IntranetMember.status == "Active"),
        "pending": await _count(s, IntranetMember, tenant_id, IntranetMember.status == "Invited"),
        "guests": await _count(
            s,
            IntranetMember,
            tenant_id,
            IntranetMember.role_id == guest_role.id if guest_role else False,
        ),
        "leadership": await _count(
            s,
            IntranetMember,
            tenant_id,
            IntranetMember.role_id.in_(leadership_ids) if leadership_ids else False,
        ),
        "removed_this_month": await _count(
            s,
            IntranetMember,
            tenant_id,
            IntranetMember.status == "Removed",
            IntranetMember.removed_at >= month_start,
        ),
        "last_synced_at": _iso(last_synced_at),
    }


async def _course_roles(s: AsyncSession, tenant_id, course_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    if not course_ids:
        return {}
    rows = (await s.execute(select(IntranetCourseRole).where(
        IntranetCourseRole.tenant_id == tenant_id,
        IntranetCourseRole.course_id.in_(course_ids),
    ))).scalars().all()
    out: dict[uuid.UUID, list[str]] = {}
    for row in rows:
        out.setdefault(row.course_id, []).append(_id(row.role_id))
    return out


async def _lesson_counts(s: AsyncSession, tenant_id, course_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    if not course_ids:
        return {}
    rows = (await s.execute(
        select(IntranetLesson.course_id, func.count(), func.coalesce(func.sum(IntranetLesson.duration_minutes), 0))
        .where(IntranetLesson.tenant_id == tenant_id, IntranetLesson.course_id.in_(course_ids))
        .group_by(IntranetLesson.course_id)
    )).all()
    return {row[0]: {"lesson_count": int(row[1]), "total_duration_minutes": int(row[2] or 0)} for row in rows}


def _course(row: IntranetCourse, roles: dict[uuid.UUID, list[str]] | None = None,
            counts: dict[uuid.UUID, dict] | None = None, lessons: list[dict] | None = None) -> dict:
    derived = (counts or {}).get(row.id, {})
    return {
        "id": _id(row.id), "title": row.title, "category": row.category,
        "description": row.description, "state": row.state,
        "track_progress": bool(row.track_progress),
        "required_for_onboarding": bool(row.required_for_onboarding),
        "issues_certificate": bool(row.issues_certificate), "sequential": bool(row.sequential),
        "sort": row.sort, "archived_at": _iso(row.archived_at),
        "published_at": _iso(row.published_at), "draft_dirty": bool(row.draft_dirty),
        "lesson_count": derived.get("lesson_count", len(lessons or [])),
        "total_duration_minutes": derived.get("total_duration_minutes", 0),
        "role_ids": (roles or {}).get(row.id, []),
        "lessons": lessons,
    }


def _lesson(row: IntranetLesson) -> dict:
    return {
        "id": _id(row.id), "course_id": _id(row.course_id), "title": row.title,
        "source_type": row.source_type, "source_ref": row.source_ref,
        "source_label": row.source_label, "duration_minutes": row.duration_minutes,
        "required": bool(row.required), "sort": row.sort,
        "published_at": _iso(row.published_at), "draft_dirty": bool(row.draft_dirty),
    }


def _sop_category(row: IntranetSopCategory, sop_count: int = 0) -> dict:
    return {
        "id": _id(row.id), "name": row.name, "sort": row.sort,
        "sop_count": sop_count,
        "published_at": _iso(row.published_at), "draft_dirty": bool(row.draft_dirty),
    }


def _sop_version(row: IntranetSopVersion) -> dict:
    return {
        "id": _id(row.id), "sop_id": _id(row.sop_id), "version_label": row.version_label,
        "filename": row.filename, "storage_key": row.storage_key,
        "content_type": row.content_type, "byte_size": row.byte_size,
        "uploaded_by": _id(row.uploaded_by), "uploaded_at": _iso(row.uploaded_at),
        "published_at": _iso(row.published_at), "draft_dirty": bool(row.draft_dirty),
    }


def _sop(row: IntranetSop, category: IntranetSopCategory | None = None,
         owner: IntranetMember | None = None, version: IntranetSopVersion | None = None,
         version_count: int = 0, acknowledged_count: int = 0) -> dict:
    return {
        "id": _id(row.id), "title": row.title, "category_id": _id(row.category_id),
        "category_name": category.name if category else None,
        "owner_member_id": _id(row.owner_member_id), "owner_name": owner.full_name if owner else None,
        "state": row.state, "review_due_on": _iso(row.review_due_on),
        "current_version_id": _id(row.current_version_id),
        "current_version": _sop_version(version) if version else None,
        "version_count": version_count, "acknowledged_count": acknowledged_count,
        "archived_at": _iso(row.archived_at), "published_at": _iso(row.published_at),
        "draft_dirty": bool(row.draft_dirty),
    }


def _wtd(row: IntranetWtdList) -> dict:
    return {
        "id": _id(row.id), "position": row.position, "name": row.name,
        "provider": row.provider, "external_list_id": row.external_list_id,
        "script_name": row.script_name, "daily_target": row.daily_target,
        "active": bool(row.active), "published_at": _iso(row.published_at),
        "draft_dirty": bool(row.draft_dirty),
    }


async def _wtd_stats(s: AsyncSession, tenant_id) -> dict:
    active_filter = (
        IntranetWtdList.tenant_id == tenant_id,
        IntranetWtdList.active.is_(True),
    )
    lists_in_run = await s.scalar(select(func.count()).select_from(IntranetWtdList).where(*active_filter))
    paired_scripts = await s.scalar(
        select(func.count(func.distinct(IntranetWtdList.script_name)))
        .select_from(IntranetWtdList)
        .where(*active_filter, IntranetWtdList.script_name.is_not(None))
    )
    daily_touch_target = await s.scalar(
        select(func.coalesce(func.sum(IntranetWtdList.daily_target), 0))
        .select_from(IntranetWtdList)
        .where(*active_filter)
    )
    return {
        "lists_in_run": int(lists_in_run or 0),
        "paired_scripts": int(paired_scripts or 0),
        "daily_touch_target": int(daily_touch_target or 0),
    }


async def _wtd_integrations(s: AsyncSession, tenant_id, providers: set[str]) -> dict:
    if not providers:
        return {}
    rows = (await s.execute(select(IntranetIntegration).where(
        IntranetIntegration.tenant_id == tenant_id,
        IntranetIntegration.provider_key.in_(providers),
    ))).scalars().all()
    return {row.provider_key: _integration(row) for row in rows}


async def _tile_roles(s: AsyncSession, tenant_id, tile_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    if not tile_ids:
        return {}
    rows = (await s.execute(select(IntranetLaunchpadTileRole).where(
        IntranetLaunchpadTileRole.tenant_id == tenant_id,
        IntranetLaunchpadTileRole.tile_id.in_(tile_ids),
    ))).scalars().all()
    out: dict[uuid.UUID, list[str]] = {}
    for row in rows:
        out.setdefault(row.tile_id, []).append(_id(row.role_id))
    return out


def _tile(row: IntranetLaunchpadTile, roles: dict[uuid.UUID, list[str]] | None = None) -> dict:
    return {
        "id": _id(row.id), "name": row.name, "logo_key": row.logo_key,
        "tile_group": row.tile_group, "url": row.url, "auth_type": row.auth_type,
        "sort": row.sort, "active": bool(row.active),
        "role_ids": (roles or {}).get(row.id, []),
        "published_at": _iso(row.published_at), "draft_dirty": bool(row.draft_dirty),
    }


async def _calendar_roles(s: AsyncSession, tenant_id, category_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    if not category_ids:
        return {}
    rows = (await s.execute(select(IntranetCalendarCategoryRole).where(
        IntranetCalendarCategoryRole.tenant_id == tenant_id,
        IntranetCalendarCategoryRole.category_id.in_(category_ids),
    ))).scalars().all()
    out: dict[uuid.UUID, list[str]] = {}
    for row in rows:
        out.setdefault(row.category_id, []).append(_id(row.role_id))
    return out


def _calendar_category(row: IntranetCalendarCategory,
                       roles: dict[uuid.UUID, list[str]] | None = None) -> dict:
    return {
        "id": _id(row.id), "name": row.name, "color": row.color,
        "calendar_address": row.calendar_address, "sort": row.sort,
        "active": bool(row.active), "role_ids": (roles or {}).get(row.id, []),
        "published_at": _iso(row.published_at), "draft_dirty": bool(row.draft_dirty),
    }


def _integration(row: IntranetIntegration) -> dict:
    return {
        "id": _id(row.id), "provider_key": row.provider_key,
        "display_name": row.display_name, "role_label": row.role_label,
        "description": row.description, "status": row.status,
        "base_url": row.base_url, "config": row.config or {},
        "last_sync_at": _iso(row.last_sync_at),
        "last_sync_status": row.last_sync_status, "last_error": row.last_error,
    }


def _ai_source(row: IntranetAiSource, roles: dict[uuid.UUID, IntranetRole] | None = None) -> dict:
    role = roles.get(row.min_role_id) if roles and row.min_role_id else None
    return {
        "id": _id(row.id), "name": row.name, "description": row.description,
        "source_kind": row.source_kind, "min_role_id": _id(row.min_role_id),
        "min_role_name": role.name if role else None, "enabled": bool(row.enabled),
        "last_crawled_at": _iso(row.last_crawled_at),
        "indexed_item_count": int(row.indexed_item_count or 0), "sort": row.sort,
        "published_at": _iso(row.published_at), "draft_dirty": bool(row.draft_dirty),
    }


def _ai_settings(row: IntranetAiSetting) -> dict:
    return {
        "always_cite": bool(row.always_cite),
        "refuse_without_source": bool(row.refuse_without_source),
        "offer_escalation": bool(row.offer_escalation),
        "learn_from_corrections": bool(row.learn_from_corrections),
        "escalation_channel": row.escalation_channel,
        "published_at": _iso(row.published_at), "draft_dirty": bool(row.draft_dirty),
    }


def _content_gap(row: IntranetContentGap, assignee: IntranetMember | None = None) -> dict:
    return {
        "id": _id(row.id), "question": row.question, "ask_count": int(row.ask_count or 0),
        "status": row.status, "resolution_note": row.resolution_note,
        "assigned_member_id": _id(row.assigned_member_id),
        "assigned_name": assignee.full_name if assignee else None,
        "first_asked_at": _iso(row.first_asked_at), "last_asked_at": _iso(row.last_asked_at),
    }


def _setup_task(row: IntranetSetupTask) -> dict:
    return {
        "id": _id(row.id), "key": row.key, "label": row.label,
        "destination": row.destination, "sort": row.sort,
        "completed_at": _iso(row.completed_at), "completed_by": _id(row.completed_by),
    }


def _pending(row: IntranetPendingChange) -> dict:
    return {
        "id": _id(row.id), "entity_type": row.entity_type, "entity_id": _id(row.entity_id),
        "change_kind": row.change_kind, "summary": row.summary, "detail": row.detail,
        "actor_member_id": _id(row.actor_member_id), "created_at": _iso(row.created_at),
        "publish_batch_id": _id(row.publish_batch_id),
    }


def _batch(row: IntranetPublishBatch) -> dict:
    return {
        "id": _id(row.id), "published_at": _iso(row.published_at),
        "published_by": _id(row.published_by), "note": row.note,
        "rolled_back_at": _iso(row.rolled_back_at), "rolled_back_by": _id(row.rolled_back_by),
    }


def _audit(row: AuditLog) -> dict:
    return {
        "id": _id(row.id), "actor_user_id": _id(row.actor_user_id),
        "actor_member_id": _id(row.actor_member_id), "actor_label": row.actor_label,
        "action": row.action, "category": row.category, "summary": row.summary,
        "target_type": row.target_type, "target_id": row.target_id,
        "detail": row.detail, "metadata": row.event_metadata or {},
        "created_at": _iso(row.created_at),
    }


async def _workspace_row(s: AsyncSession, tenant_id) -> IntranetWorkspace:
    row = (await s.execute(select(IntranetWorkspace).where(
        IntranetWorkspace.tenant_id == tenant_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Workspace is not configured.")
    return row


async def _permissions_bundle(s: AsyncSession, tenant_id) -> dict:
    roles = (await s.execute(select(IntranetRole).where(
        IntranetRole.tenant_id == tenant_id).order_by(IntranetRole.sort, IntranetRole.name))).scalars().all()
    caps = (await s.execute(select(IntranetCapability).where(
        IntranetCapability.tenant_id == tenant_id).order_by(IntranetCapability.sort, IntranetCapability.name))).scalars().all()
    perms = (await s.execute(select(IntranetPermission).where(
        IntranetPermission.tenant_id == tenant_id))).scalars().all()
    return {
        "roles": [_role_out(r) for r in roles],
        "capabilities": [_capability(c) for c in caps],
        "items": [_permission(p) for p in perms],
        "total": len(perms),
        "cursor": None,
    }


async def _courses_bundle(s: AsyncSession, tenant_id, *, include_archived: bool = False) -> dict:
    where = [IntranetCourse.tenant_id == tenant_id]
    if not include_archived:
        where.append(IntranetCourse.archived_at.is_(None))
    rows = (await s.execute(select(IntranetCourse).where(*where).order_by(
        IntranetCourse.sort, IntranetCourse.title))).scalars().all()
    ids = [r.id for r in rows]
    roles = await _course_roles(s, tenant_id, ids)
    counts = await _lesson_counts(s, tenant_id, ids)
    total = await _count(s, IntranetCourse, tenant_id, *([] if include_archived else [IntranetCourse.archived_at.is_(None)]))
    return _list([_course(r, roles, counts) for r in rows], total)


async def _sops_bundle(s: AsyncSession, tenant_id, *, include_archived: bool = False) -> dict:
    where = [IntranetSop.tenant_id == tenant_id]
    if not include_archived:
        where.append(IntranetSop.archived_at.is_(None))
    rows = (await s.execute(select(IntranetSop).where(*where).order_by(
        IntranetSop.updated_at.desc(), IntranetSop.title))).scalars().all()
    category_ids = {r.category_id for r in rows}
    owner_ids = {r.owner_member_id for r in rows if r.owner_member_id}
    version_ids = {r.current_version_id for r in rows if r.current_version_id}
    cats = {r.id: r for r in (await s.execute(select(IntranetSopCategory).where(
        IntranetSopCategory.tenant_id == tenant_id,
        IntranetSopCategory.id.in_(category_ids) if category_ids else False,
    ))).scalars().all()}
    owners = {r.id: r for r in (await s.execute(select(IntranetMember).where(
        IntranetMember.tenant_id == tenant_id,
        IntranetMember.id.in_(owner_ids) if owner_ids else False,
    ))).scalars().all()}
    versions = {r.id: r for r in (await s.execute(select(IntranetSopVersion).where(
        IntranetSopVersion.tenant_id == tenant_id,
        IntranetSopVersion.id.in_(version_ids) if version_ids else False,
    ))).scalars().all()}
    count_rows = (await s.execute(select(IntranetSopVersion.sop_id, func.count()).where(
        IntranetSopVersion.tenant_id == tenant_id,
        IntranetSopVersion.sop_id.in_([r.id for r in rows]) if rows else False,
    ).group_by(IntranetSopVersion.sop_id))).all()
    version_counts = {r[0]: int(r[1]) for r in count_rows}
    ack_rows = (await s.execute(select(IntranetSopVersion.sop_id, func.count()).join(
        IntranetSopAcknowledgement,
        IntranetSopAcknowledgement.sop_version_id == IntranetSopVersion.id,
    ).where(
        IntranetSopVersion.tenant_id == tenant_id,
        IntranetSopAcknowledgement.tenant_id == tenant_id,
        IntranetSopVersion.sop_id.in_([r.id for r in rows]) if rows else False,
    ).group_by(IntranetSopVersion.sop_id))).all()
    ack_counts = {r[0]: int(r[1]) for r in ack_rows}
    total = await _count(s, IntranetSop, tenant_id, *([] if include_archived else [IntranetSop.archived_at.is_(None)]))
    return _list([
        _sop(r, cats.get(r.category_id), owners.get(r.owner_member_id), versions.get(r.current_version_id),
             version_counts.get(r.id, 0), ack_counts.get(r.id, 0))
        for r in rows
    ], total)


async def _sop_category_counts(s: AsyncSession, tenant_id) -> dict[uuid.UUID, int]:
    rows = (await s.execute(select(IntranetSop.category_id, func.count()).where(
        IntranetSop.tenant_id == tenant_id,
        IntranetSop.archived_at.is_(None),
    ).group_by(IntranetSop.category_id))).all()
    return {row[0]: int(row[1]) for row in rows}


async def _sop_health(s: AsyncSession, tenant_id) -> dict:
    today = dt.date.today()
    due_by = today + dt.timedelta(days=30)
    base = (
        IntranetSop.tenant_id == tenant_id,
        IntranetSop.archived_at.is_(None),
    )
    overdue = await _count(s, IntranetSop, tenant_id, IntranetSop.archived_at.is_(None),
                           IntranetSop.review_due_on.is_not(None), IntranetSop.review_due_on < today)
    due_soon = await _count(s, IntranetSop, tenant_id, IntranetSop.archived_at.is_(None),
                            IntranetSop.review_due_on >= today, IntranetSop.review_due_on <= due_by)
    current = int((await s.execute(select(func.count()).select_from(IntranetSop).where(
        *base,
        (IntranetSop.review_due_on.is_(None)) | (IntranetSop.review_due_on > due_by),
    ))).scalar_one())
    return {"current": current, "due_soon": int(due_soon), "overdue": int(overdue)}


async def _config_bundle(s: AsyncSession, tenant_id) -> dict:
    workspace = await _workspace_row(s, tenant_id)
    roles = (await s.execute(select(IntranetRole).where(
        IntranetRole.tenant_id == tenant_id).order_by(IntranetRole.sort))).scalars().all()
    role_map = {r.id: r for r in roles}
    cats = (await s.execute(select(IntranetSopCategory).where(
        IntranetSopCategory.tenant_id == tenant_id).order_by(IntranetSopCategory.sort, IntranetSopCategory.name))).scalars().all()
    wtd = (await s.execute(select(IntranetWtdList).where(
        IntranetWtdList.tenant_id == tenant_id).order_by(IntranetWtdList.position))).scalars().all()
    tiles = (await s.execute(select(IntranetLaunchpadTile).where(
        IntranetLaunchpadTile.tenant_id == tenant_id).order_by(IntranetLaunchpadTile.sort, IntranetLaunchpadTile.name))).scalars().all()
    tile_roles = await _tile_roles(s, tenant_id, [t.id for t in tiles])
    cals = (await s.execute(select(IntranetCalendarCategory).where(
        IntranetCalendarCategory.tenant_id == tenant_id).order_by(IntranetCalendarCategory.sort))).scalars().all()
    cal_roles = await _calendar_roles(s, tenant_id, [c.id for c in cals])
    integrations = (await s.execute(select(IntranetIntegration).where(
        IntranetIntegration.tenant_id == tenant_id).order_by(IntranetIntegration.display_name))).scalars().all()
    ai_sources = (await s.execute(select(IntranetAiSource).where(
        IntranetAiSource.tenant_id == tenant_id).order_by(IntranetAiSource.sort))).scalars().all()
    ai_setting = await _ai_setting_row(s, tenant_id)
    setup = (await s.execute(select(IntranetSetupTask).where(
        IntranetSetupTask.tenant_id == tenant_id).order_by(IntranetSetupTask.sort))).scalars().all()
    gaps = (await s.execute(select(IntranetContentGap).where(
        IntranetContentGap.tenant_id == tenant_id).order_by(IntranetContentGap.last_asked_at.desc().nullslast()))).scalars().all()
    return {
        "workspace": _workspace(workspace),
        "roles": _list([_role_out(r) for r in roles]),
        "permissions": await _permissions_bundle(s, tenant_id),
        "courses": await _courses_bundle(s, tenant_id),
        "sop_categories": _list([_sop_category(c) for c in cats]),
        "sops": await _sops_bundle(s, tenant_id),
        "wtd_lists": _list([_wtd(r) for r in wtd]),
        "tiles": _list([_tile(r, tile_roles) for r in tiles]),
        "calendar_categories": _list([_calendar_category(r, cal_roles) for r in cals]),
        "integrations": _list([_integration(r) for r in integrations]),
        "ai": {"settings": _ai_settings(ai_setting),
               "sources": _list([_ai_source(r, role_map) for r in ai_sources])},
        "content_gaps": _list([_content_gap(r) for r in gaps]),
        "setup_tasks": _list([_setup_task(r) for r in setup]),
        "pending_changes": await _pending_count(s, tenant_id),
    }


async def _ai_setting_row(s: AsyncSession, tenant_id) -> IntranetAiSetting:
    row = await s.get(IntranetAiSetting, tenant_id)
    if row is None:
        row = IntranetAiSetting(tenant_id=tenant_id)
        s.add(row)
        await s.flush()
    return row


@router.get("/config")
async def get_config(p: ConsolePrincipal = Depends(require_console_access),
                     s: AsyncSession = Depends(get_session)):
    return await _config_bundle(s, p.user.tenant_id)


@router.get("/overview")
async def get_overview(p: ConsolePrincipal = Depends(require_console_access),
                       s: AsyncSession = Depends(get_session)):
    tid = p.user.tenant_id
    setup_total = await _count(s, IntranetSetupTask, tid)
    setup_done = await _count(s, IntranetSetupTask, tid, IntranetSetupTask.completed_at.is_not(None))
    counts = {
        "courses": await _count(s, IntranetCourse, tid, IntranetCourse.archived_at.is_(None)),
        "lessons": await _count(s, IntranetLesson, tid),
        "sops": await _count(s, IntranetSop, tid, IntranetSop.archived_at.is_(None)),
        "members": await _count(s, IntranetMember, tid),
        "wtd_lists": await _count(s, IntranetWtdList, tid),
        "tiles": await _count(s, IntranetLaunchpadTile, tid),
        "permissions": await _count(s, IntranetPermission, tid),
        "integrations": await _count(s, IntranetIntegration, tid),
        "ai_sources": await _count(s, IntranetAiSource, tid),
        "setup_tasks": setup_total,
        "pending_changes": await _pending_count(s, tid),
    }
    audits = (await s.execute(select(AuditLog).where(
        AuditLog.tenant_id == tid).order_by(AuditLog.created_at.desc()).limit(10))).scalars().all()
    return {
        "counts": counts,
        "setup": {"completed": setup_done, "total": setup_total,
                  "percent": round((setup_done / setup_total) * 100) if setup_total else 0},
        "recent_activity": [_audit(a) for a in audits],
    }


@router.get("/workspace")
async def get_workspace(p: ConsolePrincipal = Depends(require_console_access),
                        s: AsyncSession = Depends(get_session)):
    return _workspace(await _workspace_row(s, p.user.tenant_id))


@router.patch("/workspace")
async def patch_workspace(body: dict = Body(...),
                          p: ConsolePrincipal = Depends(require_console_access),
                          s: AsyncSession = Depends(get_session)):
    body = _body(body)
    _unknown(body, {"portal_name", "tagline", "subdomain", "custom_domain", "palette",
                    "timezone", "week_starts_on", "default_calendar_view"})
    row = await _workspace_row(s, p.user.tenant_id)
    if "portal_name" in body:
        row.portal_name = _text(body, "portal_name", required=True) or row.portal_name
    if "tagline" in body:
        row.tagline = _text(body, "tagline", nullable=True)
    if "subdomain" in body:
        row.subdomain = _text(body, "subdomain", required=True, max_len=80) or row.subdomain
    if "custom_domain" in body:
        row.custom_domain = _text(body, "custom_domain", nullable=True, max_len=255)
        row.custom_domain_verified_at = None
    if "palette" in body:
        row.palette = _palette(body)
    if "timezone" in body:
        row.timezone = _text(body, "timezone", required=True, max_len=80) or row.timezone
    if "week_starts_on" in body:
        row.week_starts_on = _int(body, "week_starts_on", min_value=0, max_value=6) or 0
    if "default_calendar_view" in body:
        view = _enum(body, "default_calendar_view", {"month", "week", "agenda"})
        row.default_calendar_view = view or row.default_calendar_view
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="config.brand.updated", category="Workspace",
        summary=f"Updated workspace identity for {row.portal_name}",
        target_type="workspace", target_id=row.id, entity_type="workspace", entity_id=row.id)
    return _with_pending(_workspace(row), pending)


@router.post("/workspace/logo")
async def upload_workspace_logo(kind: str = Form(...), file: UploadFile = File(...),
                                p: ConsolePrincipal = Depends(require_console_access),
                                s: AsyncSession = Depends(get_session)):
    kind = (kind or "").strip().lower()
    if kind not in LOGO_KINDS:
        _unprocessable("kind", "Must be light, dark, or mark.")
    row = await _workspace_row(s, p.user.tenant_id)
    data = await file.read()
    if not data:
        _unprocessable("file", "Logo file is empty.")
    if len(data) > MAX_LOGO_BYTES:
        _unprocessable("file", "Logo file must be 2 MB or smaller.")
    content_type = file.content_type or "application/octet-stream"
    if content_type not in LOGO_CONTENT_TYPES:
        _unprocessable("file", "Logo must be PNG, JPEG, WebP, or SVG.")
    name = binder_storage.safe_filename(file.filename or f"{kind}.bin")
    key = f"intranet/{p.user.tenant_id}/workspace/{kind}-{uuid.uuid4()}-{name}"
    binder_storage.put(key, data, content_type)
    setattr(row, LOGO_KINDS[kind], key)
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="config.brand.logo_uploaded", category="Workspace",
        summary=f"Uploaded {kind} workspace logo", target_type="workspace",
        target_id=row.id, detail={"filename": name, "bytes": len(data)},
        entity_type="workspace", entity_id=row.id)
    return _with_pending(_workspace(row), pending)


@router.get("/roles")
async def get_roles(p: ConsolePrincipal = Depends(require_console_access),
                    s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(IntranetRole).where(
        IntranetRole.tenant_id == p.user.tenant_id).order_by(IntranetRole.sort, IntranetRole.name))).scalars().all()
    return _list([_role_out(r) for r in rows], await _count(s, IntranetRole, p.user.tenant_id))


@router.patch("/roles/{role_id}")
async def patch_role(role_id: uuid.UUID, body: dict = Body(...),
                     p: ConsolePrincipal = Depends(require_console_access),
                     s: AsyncSession = Depends(get_session)):
    body = _body(body)
    _unknown(body, {"name", "sort", "is_leadership"})
    row = await _one(s, IntranetRole, p.user.tenant_id, role_id)
    if "name" in body:
        row.name = _text(body, "name", required=True) or row.name
    if "sort" in body:
        row.sort = _int(body, "sort", min_value=0) or 0
    if "is_leadership" in body:
        row.is_leadership = bool(_bool(body, "is_leadership"))
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="console.role.update", category="Roles",
        summary=f"Updated role {row.name}", target_type="role", target_id=row.id,
        entity_type="role", entity_id=row.id)
    return _with_pending(_role_out(row), pending)


@router.get("/permissions")
async def get_permissions(p: ConsolePrincipal = Depends(require_console_access),
                          s: AsyncSession = Depends(get_session)):
    return await _permissions_bundle(s, p.user.tenant_id)


@router.put("/permissions")
async def put_permissions(body: dict = Body(...),
                          p: ConsolePrincipal = Depends(require_console_access),
                          s: AsyncSession = Depends(get_session)):
    body = _body(body)
    items = body.get("items")
    if not isinstance(items, list):
        _unprocessable("items", "Expected a list.")
    roles = await _roles_by_id(s, p.user.tenant_id)
    caps = {c.id: c for c in (await s.execute(select(IntranetCapability).where(
        IntranetCapability.tenant_id == p.user.tenant_id))).scalars().all()}
    expected = len(roles) * len(caps)
    if len(items) != expected:
        _unprocessable("items", f"Expected the full {expected}-permission matrix.")
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    console_full = 0
    changes: list[str] = []
    existing = {(r.capability_id, r.role_id): r for r in (await s.execute(
        select(IntranetPermission).where(IntranetPermission.tenant_id == p.user.tenant_id)
    )).scalars().all()}
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            _unprocessable(f"items.{idx}", "Expected an object.")
        cap_id = _uuid_value(item, "capability_id", required=True)
        role_id = _uuid_value(item, "role_id", required=True)
        level = _enum(item, "level", LEVELS)
        if cap_id not in caps or role_id not in roles:
            raise HTTPException(404, "Permission role or capability not found.")
        key = (cap_id, role_id)
        if key in seen:
            _unprocessable(f"items.{idx}", "Duplicate role/capability pair.")
        seen.add(key)
        cap = caps[cap_id]
        if cap.key == "console_access":
            if level not in {"Full", "None"}:
                _unprocessable(f"items.{idx}.level", "console_access can only be Full or None.")
            if level == "Full":
                console_full += 1
        row = existing.get(key)
        if row is None:
            row = IntranetPermission(
                tenant_id=p.user.tenant_id, capability_id=cap_id, role_id=role_id, level=level)
            s.add(row)
            changes.append(f"{cap.name} for {roles[role_id].name}: None to {level}")
        else:
            if row.level != level:
                changes.append(f"{cap.name} for {roles[role_id].name}: {row.level} to {level}")
            row.level = level
        row.draft_dirty = True
    if console_full == 0:
        _unprocessable("items", "At least one role must keep console_access=Full.")
    if changes:
        first = changes[0]
        extra = f" plus {len(changes) - 1} more" if len(changes) > 1 else ""
        summary = f"Updated permission {first}{extra}"
    else:
        summary = "Reviewed the roles and permissions matrix"
    pending = await _record_mutation(
        s, p, action="access.permissions.updated", category="Roles",
        summary=summary, target_type="permission",
        target_id=None, entity_type="permission", entity_id=None)
    return {**(await _permissions_bundle(s, p.user.tenant_id)), "pending_changes": pending}


@router.get("/members")
async def get_members(status: str | None = Query(None), q: str | None = Query(None),
                      filter: str | None = Query(None),
                      role: str | None = Query(None),
                      p: ConsolePrincipal = Depends(require_console_access),
                      s: AsyncSession = Depends(get_session)):
    roles = await _roles_by_id(s, p.user.tenant_id)
    where = [IntranetMember.tenant_id == p.user.tenant_id]
    if filter:
        value = filter.strip().lower()
        if value not in MEMBER_FILTERS:
            _unprocessable("filter", "Invalid member filter.")
        if value == "active":
            where.append(IntranetMember.status == "Active")
        elif value == "pending":
            where.append(IntranetMember.status == "Invited")
        elif value == "guests":
            guest_role = next((r for r in roles.values() if r.key == "jv_partner"), None)
            where.append(IntranetMember.role_id == guest_role.id if guest_role else False)
        elif value == "leadership":
            leadership_ids = [role_id for role_id, role_row in roles.items() if role_row.is_leadership]
            where.append(IntranetMember.role_id.in_(leadership_ids) if leadership_ids else False)
    if status:
        if status not in MEMBER_STATUSES:
            _unprocessable("status", "Invalid member status.")
        where.append(IntranetMember.status == status)
    if q:
        like = f"%{q.strip().lower()}%"
        where.append(func.lower(IntranetMember.full_name + " " + IntranetMember.email).like(like))
    if role:
        role_row = next((r for r in roles.values() if r.key == role or str(r.id) == role), None)
        if role_row is None:
            raise HTTPException(404, "Role not found.")
        where.append(IntranetMember.role_id == role_row.id)
    rows = (await s.execute(select(IntranetMember).where(*where).order_by(
        IntranetMember.status, IntranetMember.full_name))).scalars().all()
    total = int((await s.execute(select(func.count()).select_from(IntranetMember).where(*where))).scalar_one())
    return {
        **_list([_member(r, roles) for r in rows], total),
        "roles": [_role_out(r) for r in sorted(roles.values(), key=lambda item: item.sort)],
        "stats": await _member_stats(s, p.user.tenant_id, roles),
    }


@router.post("/members/invite")
async def invite_member(body: dict = Body(...),
                        p: ConsolePrincipal = Depends(require_console_access),
                        s: AsyncSession = Depends(get_session)):
    body = _body(body)
    _unknown(body, {"full_name", "email", "role_id", "market", "auth_source"})
    auth_source = _enum(body, "auth_source", AUTH_SOURCES, "Manual") or "Manual"
    role_id = _uuid_value(body, "role_id", required=auth_source != "Guest")
    if role_id is None:
        role_id = (await _role_by_key(s, p.user.tenant_id, "jv_partner")).id
    await _role(s, p.user.tenant_id, role_id)
    email = (_text(body, "email", required=True, max_len=255) or "").lower()
    if "@" not in email:
        _unprocessable("email", "Expected an email address.")
    existing = (await s.execute(select(IntranetMember).where(
        IntranetMember.tenant_id == p.user.tenant_id,
        IntranetMember.email == email,
    ))).scalar_one_or_none()
    if existing is not None:
        _unprocessable("email", "Member already exists.")
    row = IntranetMember(
        tenant_id=p.user.tenant_id,
        full_name=_text(body, "full_name", required=True) or email,
        email=email,
        role_id=role_id,
        market=_text(body, "market", nullable=True),
        auth_source=auth_source,
        status="Invited",
        invited_at=_now(),
    )
    s.add(row)
    pending = await _record_mutation(
        s, p, action="access.member.invited", category="People",
        summary=f"Invited {row.full_name} to the intranet", target_type="member",
        target_id=row.id, pending=False)
    return _with_pending(_member(row, await _roles_by_id(s, p.user.tenant_id)), pending)


@router.patch("/members/{member_id}")
async def patch_member(member_id: uuid.UUID, body: dict = Body(...),
                       p: ConsolePrincipal = Depends(require_console_access),
                       s: AsyncSession = Depends(get_session)):
    body = _body(body)
    _unknown(body, {"full_name", "email", "role_id", "market", "status", "auth_source"})
    row = await _one(s, IntranetMember, p.user.tenant_id, member_id)
    roles = await _roles_by_id(s, p.user.tenant_id)
    old_role = roles.get(row.role_id)
    new_role = old_role
    role_changed = False
    if "full_name" in body:
        row.full_name = _text(body, "full_name", required=True) or row.full_name
    if "email" in body:
        email = (_text(body, "email", required=True, max_len=255) or row.email).lower()
        if "@" not in email:
            _unprocessable("email", "Expected an email address.")
        row.email = email
    if "role_id" in body:
        role_id = _uuid_value(body, "role_id", required=True)
        new_role = await _role(s, p.user.tenant_id, role_id)
        role_changed = role_id != row.role_id
        row.role_id = role_id
    if "market" in body:
        row.market = _text(body, "market", nullable=True)
    if "auth_source" in body:
        row.auth_source = _enum(body, "auth_source", AUTH_SOURCES) or row.auth_source
    if "status" in body:
        status = _enum(body, "status", MEMBER_STATUSES) or row.status
        row.status = status
        if status == "Active" and row.activated_at is None:
            row.activated_at = _now()
        if status == "Removed":
            row.removed_at = _now()
    action = "access.member.role_changed" if role_changed else "access.member.updated"
    if role_changed:
        summary = f"Changed {row.full_name} from {old_role.name if old_role else 'Unknown'} to {new_role.name}"
    else:
        summary = f"Updated roster record for {row.full_name}"
    pending = await _record_mutation(
        s, p, action=action, category="People",
        summary=summary, target_type="member",
        target_id=row.id, pending=False)
    return _with_pending(_member(row, await _roles_by_id(s, p.user.tenant_id)), pending)


@router.delete("/members/{member_id}")
async def remove_member(member_id: uuid.UUID, p: ConsolePrincipal = Depends(require_console_access),
                        s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetMember, p.user.tenant_id, member_id)
    row.status = "Removed"
    row.removed_at = _now()
    pending = await _record_mutation(
        s, p, action="access.member.removed", category="People",
        summary=f"Removed {row.full_name} from the intranet roster", target_type="member",
        target_id=row.id, pending=False)
    return _with_pending(_member(row, await _roles_by_id(s, p.user.tenant_id)), pending)


@router.post("/members/sync")
async def sync_members(p: ConsolePrincipal = Depends(require_console_access),
                       s: AsyncSession = Depends(get_session)):
    pending = await _record_mutation(
        s, p, action="access.roster.synced", category="People",
        summary="Roster sync: no source connected yet", target_type="member",
        target_id=None, pending=False)
    return {"added": 0, "removed": 0, "updated": 0, "pending_changes": pending}


@router.get("/courses")
async def get_courses(p: ConsolePrincipal = Depends(require_console_access),
                      s: AsyncSession = Depends(get_session)):
    return await _courses_bundle(s, p.user.tenant_id)


@router.get("/courses/{course_id}")
async def get_course(course_id: uuid.UUID, p: ConsolePrincipal = Depends(require_console_access),
                     s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    roles = await _course_roles(s, p.user.tenant_id, [row.id])
    lessons = (await s.execute(select(IntranetLesson).where(
        IntranetLesson.tenant_id == p.user.tenant_id,
        IntranetLesson.course_id == row.id,
    ).order_by(IntranetLesson.sort, IntranetLesson.title))).scalars().all()
    return _course(row, roles, {row.id: {"lesson_count": len(lessons),
                                         "total_duration_minutes": sum(l.duration_minutes or 0 for l in lessons)}},
                   [_lesson(l) for l in lessons])


@router.post("/courses")
async def create_course(body: dict = Body(...), p: ConsolePrincipal = Depends(require_console_access),
                        s: AsyncSession = Depends(get_session)):
    body = _body(body)
    _unknown(body, {"title", "category", "description", "state", "track_progress",
                    "required_for_onboarding", "issues_certificate", "sequential", "sort"})
    row = IntranetCourse(
        tenant_id=p.user.tenant_id,
        title=_text(body, "title", required=True) or "",
        category=_text(body, "category", required=True) or "",
        description=_text(body, "description", nullable=True),
        state=_enum(body, "state", COURSE_STATES, "Draft") or "Draft",
        track_progress=_bool(body, "track_progress", True),
        required_for_onboarding=_bool(body, "required_for_onboarding", False),
        issues_certificate=_bool(body, "issues_certificate", False),
        sequential=_bool(body, "sequential", False),
        sort=_int(body, "sort", default=await _count(s, IntranetCourse, p.user.tenant_id), min_value=0) or 0,
    )
    s.add(row)
    pending = await _record_mutation(
        s, p, action="content.course.created", category="Training",
        summary=f"Created course {row.title}", target_type="course", target_id=row.id,
        entity_type="course", entity_id=row.id, change_kind="created")
    return _with_pending(_course(row), pending)


@router.patch("/courses/{course_id}")
async def patch_course(course_id: uuid.UUID, body: dict = Body(...),
                       p: ConsolePrincipal = Depends(require_console_access),
                       s: AsyncSession = Depends(get_session)):
    body = _body(body)
    _unknown(body, {"title", "category", "description", "state", "track_progress",
                    "required_for_onboarding", "issues_certificate", "sequential", "sort"})
    row = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    if "title" in body:
        row.title = _text(body, "title", required=True) or row.title
    if "category" in body:
        row.category = _text(body, "category", required=True) or row.category
    if "description" in body:
        row.description = _text(body, "description", nullable=True)
    if "state" in body:
        row.state = _enum(body, "state", COURSE_STATES) or row.state
    for field in ("track_progress", "required_for_onboarding", "issues_certificate", "sequential"):
        if field in body:
            setattr(row, field, bool(_bool(body, field)))
    if "sort" in body:
        row.sort = _int(body, "sort", min_value=0) or 0
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.course.updated", category="Training",
        summary=f"Updated course {row.title}", target_type="course", target_id=row.id,
        entity_type="course", entity_id=row.id)
    return _with_pending(_course(row), pending)


@router.delete("/courses/{course_id}")
async def delete_course(course_id: uuid.UUID, p: ConsolePrincipal = Depends(require_console_access),
                        s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    row.archived_at = _now()
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.course.archived", category="Training",
        summary=f"Archived course {row.title}", target_type="course", target_id=row.id,
        entity_type="course", entity_id=row.id, change_kind="deleted")
    return _with_pending(_course(row), pending)


@router.put("/courses/{course_id}/roles")
async def put_course_roles(course_id: uuid.UUID, body: dict = Body(...),
                           p: ConsolePrincipal = Depends(require_console_access),
                           s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    items = _body(body).get("role_ids")
    if not isinstance(items, list):
        _unprocessable("role_ids", "Expected a list.")
    roles = await _roles_by_id(s, p.user.tenant_id)
    role_ids = []
    for idx, rid in enumerate(items):
        try:
            parsed = uuid.UUID(str(rid))
        except ValueError:
            _unprocessable(f"role_ids.{idx}", "Expected a UUID.")
        if parsed not in roles:
            raise HTTPException(404, "Role not found.")
        role_ids.append(parsed)
    await s.execute(sa_delete(IntranetCourseRole).where(
        IntranetCourseRole.tenant_id == p.user.tenant_id,
        IntranetCourseRole.course_id == course_id,
    ))
    for rid in sorted(set(role_ids), key=lambda v: roles[v].sort):
        s.add(IntranetCourseRole(tenant_id=p.user.tenant_id, course_id=course_id, role_id=rid))
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.course.roles_updated", category="Training",
        summary=f"Updated role visibility for {row.title}", target_type="course",
        target_id=row.id, entity_type="course", entity_id=row.id)
    return _with_pending(await get_course(course_id, p, s), pending)


@router.put("/courses/{course_id}/lessons/order")
async def put_lesson_order(course_id: uuid.UUID, body: dict = Body(...),
                           p: ConsolePrincipal = Depends(require_console_access),
                           s: AsyncSession = Depends(get_session)):
    course = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    ids = _body(body).get("ids")
    if not isinstance(ids, list):
        _unprocessable("ids", "Expected a full ordered id list.")
    lessons = (await s.execute(select(IntranetLesson).where(
        IntranetLesson.tenant_id == p.user.tenant_id,
        IntranetLesson.course_id == course_id,
    ))).scalars().all()
    by_id = {str(l.id): l for l in lessons}
    if set(map(str, ids)) != set(by_id):
        _unprocessable("ids", "Must include every lesson exactly once.")
    for sort, lesson_id in enumerate(ids):
        by_id[str(lesson_id)].sort = sort
        by_id[str(lesson_id)].draft_dirty = True
    course.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.lesson.reordered", category="Training",
        summary=f"Reordered lessons in {course.title}", target_type="course",
        target_id=course.id, entity_type="lesson", entity_id=course.id)
    return _with_pending(await get_course(course_id, p, s), pending)


@router.post("/courses/{course_id}/lessons")
async def create_lesson(course_id: uuid.UUID, body: dict = Body(...),
                        p: ConsolePrincipal = Depends(require_console_access),
                        s: AsyncSession = Depends(get_session)):
    course = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    body = _body(body)
    _unknown(body, {"title", "source_type", "source_ref", "source_label",
                    "duration_minutes", "required", "sort"})
    row = IntranetLesson(
        tenant_id=p.user.tenant_id,
        course_id=course.id,
        title=_text(body, "title", required=True) or "",
        source_type=_enum(body, "source_type", LESSON_SOURCE_TYPES, "PLACE") or "PLACE",
        source_ref=_text(body, "source_ref", nullable=True),
        source_label=_text(body, "source_label", nullable=True),
        duration_minutes=_int(body, "duration_minutes", min_value=0),
        required=_bool(body, "required", False),
        sort=_int(body, "sort", default=await _count(s, IntranetLesson, p.user.tenant_id,
                                                     IntranetLesson.course_id == course_id), min_value=0) or 0,
    )
    s.add(row)
    course.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.lesson.created", category="Training",
        summary=f"Added lesson {row.title} to {course.title}", target_type="lesson",
        target_id=row.id, entity_type="lesson", entity_id=row.id, change_kind="created")
    return _with_pending(_lesson(row), pending)


@router.patch("/courses/{course_id}/lessons/{lesson_id}")
async def patch_lesson(course_id: uuid.UUID, lesson_id: uuid.UUID, body: dict = Body(...),
                       p: ConsolePrincipal = Depends(require_console_access),
                       s: AsyncSession = Depends(get_session)):
    course = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    row = await _one(s, IntranetLesson, p.user.tenant_id, lesson_id)
    if row.course_id != course_id:
        raise HTTPException(404, "Not found.")
    body = _body(body)
    _unknown(body, {"title", "source_type", "source_ref", "source_label",
                    "duration_minutes", "required", "sort"})
    if "title" in body:
        row.title = _text(body, "title", required=True) or row.title
    if "source_type" in body:
        row.source_type = _enum(body, "source_type", LESSON_SOURCE_TYPES) or row.source_type
    if "source_ref" in body:
        row.source_ref = _text(body, "source_ref", nullable=True)
    if "source_label" in body:
        row.source_label = _text(body, "source_label", nullable=True)
    if "duration_minutes" in body:
        row.duration_minutes = _int(body, "duration_minutes", min_value=0)
    if "required" in body:
        row.required = bool(_bool(body, "required"))
    if "sort" in body:
        row.sort = _int(body, "sort", min_value=0) or 0
    row.draft_dirty = True
    course.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.lesson.updated", category="Training",
        summary=f"Updated lesson {row.title}", target_type="lesson", target_id=row.id,
        entity_type="lesson", entity_id=row.id)
    return _with_pending(_lesson(row), pending)


@router.delete("/courses/{course_id}/lessons/{lesson_id}")
async def delete_lesson(course_id: uuid.UUID, lesson_id: uuid.UUID,
                        p: ConsolePrincipal = Depends(require_console_access),
                        s: AsyncSession = Depends(get_session)):
    course = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    row = await _one(s, IntranetLesson, p.user.tenant_id, lesson_id)
    if row.course_id != course_id:
        raise HTTPException(404, "Not found.")
    out = _lesson(row)
    await s.delete(row)
    course.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.lesson.deleted", category="Training",
        summary=f"Removed lesson {out['title']}", target_type="lesson", target_id=lesson_id,
        entity_type="lesson", entity_id=lesson_id, change_kind="deleted")
    return _with_pending(out, pending)


@router.get("/sop-categories")
async def get_sop_categories(p: ConsolePrincipal = Depends(require_console_access),
                             s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(IntranetSopCategory).where(
        IntranetSopCategory.tenant_id == p.user.tenant_id).order_by(
            IntranetSopCategory.sort, IntranetSopCategory.name))).scalars().all()
    counts = await _sop_category_counts(s, p.user.tenant_id)
    return _list([_sop_category(r, counts.get(r.id, 0)) for r in rows],
                 await _count(s, IntranetSopCategory, p.user.tenant_id))


@router.post("/sop-categories")
async def create_sop_category(body: dict = Body(...), p: ConsolePrincipal = Depends(require_console_access),
                              s: AsyncSession = Depends(get_session)):
    body = _body(body)
    _unknown(body, {"name", "sort"})
    row = IntranetSopCategory(
        tenant_id=p.user.tenant_id,
        name=_text(body, "name", required=True) or "",
        sort=_int(body, "sort", default=await _count(s, IntranetSopCategory, p.user.tenant_id), min_value=0) or 0,
    )
    s.add(row)
    pending = await _record_mutation(
        s, p, action="content.sop_category.created", category="SOPs",
        summary=f"Created SOP category {row.name}", target_type="sop_category",
        target_id=row.id, entity_type="sop_category", entity_id=row.id, change_kind="created")
    return _with_pending(_sop_category(row), pending)


@router.patch("/sop-categories/{category_id}")
async def patch_sop_category(category_id: uuid.UUID, body: dict = Body(...),
                             p: ConsolePrincipal = Depends(require_console_access),
                             s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetSopCategory, p.user.tenant_id, category_id)
    body = _body(body)
    _unknown(body, {"name", "sort"})
    if "name" in body:
        row.name = _text(body, "name", required=True) or row.name
    if "sort" in body:
        row.sort = _int(body, "sort", min_value=0) or 0
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.sop_category.updated", category="SOPs",
        summary=f"Updated SOP category {row.name}", target_type="sop_category",
        target_id=row.id, entity_type="sop_category", entity_id=row.id)
    return _with_pending(_sop_category(row), pending)


@router.delete("/sop-categories/{category_id}")
async def delete_sop_category(category_id: uuid.UUID, p: ConsolePrincipal = Depends(require_console_access),
                              s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetSopCategory, p.user.tenant_id, category_id)
    used = await _count(s, IntranetSop, p.user.tenant_id, IntranetSop.category_id == row.id)
    if used:
        _unprocessable("id", "Move SOPs out of this category before deleting it.")
    out = _sop_category(row)
    await s.delete(row)
    pending = await _record_mutation(
        s, p, action="content.sop_category.deleted", category="SOPs",
        summary=f"Deleted SOP category {out['name']}", target_type="sop_category",
        target_id=category_id, entity_type="sop_category", entity_id=category_id,
        change_kind="deleted")
    return _with_pending(out, pending)


@router.get("/sops")
async def get_sops(p: ConsolePrincipal = Depends(require_console_access),
                   s: AsyncSession = Depends(get_session)):
    out = await _sops_bundle(s, p.user.tenant_id)
    out["health"] = await _sop_health(s, p.user.tenant_id)
    return out


@router.get("/sops/{sop_id}")
async def get_sop(sop_id: uuid.UUID, p: ConsolePrincipal = Depends(require_console_access),
                  s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetSop, p.user.tenant_id, sop_id)
    cat = await _one(s, IntranetSopCategory, p.user.tenant_id, row.category_id)
    owner = await _member_by_id_or_none(s, p.user.tenant_id, row.owner_member_id)
    version = await s.get(IntranetSopVersion, row.current_version_id) if row.current_version_id else None
    version_count = await _count(s, IntranetSopVersion, p.user.tenant_id, IntranetSopVersion.sop_id == row.id)
    return _sop(row, cat, owner, version, version_count, 0)


@router.post("/sops")
async def create_sop(body: dict = Body(...), p: ConsolePrincipal = Depends(require_console_access),
                     s: AsyncSession = Depends(get_session)):
    body = _body(body)
    _unknown(body, {"title", "category_id", "owner_member_id", "state", "review_due_on"})
    category_id = _uuid_value(body, "category_id", required=True)
    await _one(s, IntranetSopCategory, p.user.tenant_id, category_id)
    owner_id = _uuid_value(body, "owner_member_id", nullable=True) if "owner_member_id" in body else None
    await _member_by_id_or_none(s, p.user.tenant_id, owner_id)
    row = IntranetSop(
        tenant_id=p.user.tenant_id,
        title=_text(body, "title", required=True) or "",
        category_id=category_id,
        owner_member_id=owner_id,
        state=_enum(body, "state", SOP_STATES, "Draft") or "Draft",
        review_due_on=_date_value(body, "review_due_on"),
    )
    s.add(row)
    pending = await _record_mutation(
        s, p, action="content.sop.created", category="SOPs",
        summary=f"Created SOP {row.title}", target_type="sop", target_id=row.id,
        entity_type="sop", entity_id=row.id, change_kind="created")
    return _with_pending(await get_sop(row.id, p, s), pending)


@router.patch("/sops/{sop_id}")
async def patch_sop(sop_id: uuid.UUID, body: dict = Body(...),
                    p: ConsolePrincipal = Depends(require_console_access),
                    s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetSop, p.user.tenant_id, sop_id)
    body = _body(body)
    _unknown(body, {"title", "category_id", "owner_member_id", "state", "review_due_on"})
    if "title" in body:
        row.title = _text(body, "title", required=True) or row.title
    if "category_id" in body:
        category_id = _uuid_value(body, "category_id", required=True)
        await _one(s, IntranetSopCategory, p.user.tenant_id, category_id)
        row.category_id = category_id
    if "owner_member_id" in body:
        owner_id = _uuid_value(body, "owner_member_id", nullable=True)
        await _member_by_id_or_none(s, p.user.tenant_id, owner_id)
        row.owner_member_id = owner_id
    if "state" in body:
        old_state = row.state
        row.state = _enum(body, "state", SOP_STATES) or row.state
    else:
        old_state = row.state
    if "review_due_on" in body:
        row.review_due_on = _date_value(body, "review_due_on")
    row.draft_dirty = True
    action = "content.sop.state_changed" if old_state != row.state else "content.sop.updated"
    pending = await _record_mutation(
        s, p, action=action, category="SOPs",
        summary=f"Updated SOP {row.title}", target_type="sop", target_id=row.id,
        entity_type="sop", entity_id=row.id)
    return _with_pending(await get_sop(row.id, p, s), pending)


@router.delete("/sops/{sop_id}")
async def delete_sop(sop_id: uuid.UUID, p: ConsolePrincipal = Depends(require_console_access),
                     s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetSop, p.user.tenant_id, sop_id)
    row.archived_at = _now()
    row.state = "Archived"
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.sop.state_changed", category="SOPs",
        summary=f"Archived SOP {row.title}", target_type="sop", target_id=row.id,
        entity_type="sop", entity_id=row.id, change_kind="deleted")
    return _with_pending(await get_sop(row.id, p, s), pending)


@router.get("/sops/{sop_id}/versions")
async def get_sop_versions(sop_id: uuid.UUID, p: ConsolePrincipal = Depends(require_console_access),
                           s: AsyncSession = Depends(get_session)):
    await _one(s, IntranetSop, p.user.tenant_id, sop_id)
    rows = (await s.execute(select(IntranetSopVersion).where(
        IntranetSopVersion.tenant_id == p.user.tenant_id,
        IntranetSopVersion.sop_id == sop_id,
    ).order_by(IntranetSopVersion.uploaded_at.desc()))).scalars().all()
    total = await _count(s, IntranetSopVersion, p.user.tenant_id, IntranetSopVersion.sop_id == sop_id)
    return _list([_sop_version(r) for r in rows], total)


@router.get("/sops/{sop_id}/versions/{version_id}/download")
async def download_sop_version(sop_id: uuid.UUID, version_id: uuid.UUID,
                               p: ConsolePrincipal = Depends(require_console_access),
                               s: AsyncSession = Depends(get_session)):
    await _one(s, IntranetSop, p.user.tenant_id, sop_id)
    row = await _one(s, IntranetSopVersion, p.user.tenant_id, version_id)
    if row.sop_id != sop_id:
        raise HTTPException(404, "Not found.")
    if not binder_storage.exists(row.storage_key):
        raise HTTPException(404, "Stored SOP version file not found.")
    data = binder_storage.read(row.storage_key)
    filename = binder_storage.safe_filename(row.filename)
    return Response(
        content=data,
        media_type=row.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/sops/{sop_id}/versions")
async def upload_sop_version(sop_id: uuid.UUID, version_label: str = Form(...),
                             file: UploadFile = File(...),
                             p: ConsolePrincipal = Depends(require_console_access),
                             s: AsyncSession = Depends(get_session)):
    sop = await _one(s, IntranetSop, p.user.tenant_id, sop_id)
    label = (version_label or "").strip()
    if not label:
        _unprocessable("version_label", "Required.")
    duplicate = await _count(s, IntranetSopVersion, p.user.tenant_id,
                             IntranetSopVersion.sop_id == sop.id,
                             IntranetSopVersion.version_label == label)
    if duplicate:
        _unprocessable("version_label", "Version label already exists for this SOP.")
    data = await file.read()
    version_id = uuid.uuid4()
    name = binder_storage.safe_filename(file.filename or "sop.bin")
    content_type = file.content_type or "application/octet-stream"
    storage_key = f"intranet/{p.user.tenant_id}/sops/{sop.id}/{version_id}-{name}"
    binder_storage.put(storage_key, data, content_type)
    row = IntranetSopVersion(
        id=version_id,
        tenant_id=p.user.tenant_id,
        sop_id=sop.id,
        version_label=label,
        filename=name,
        storage_key=storage_key,
        content_type=content_type,
        byte_size=len(data),
        uploaded_by=p.member.id,
        uploaded_at=_now(),
    )
    s.add(row)
    await s.flush()
    sop.current_version_id = row.id
    sop.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.sop.version_uploaded", category="SOPs",
        summary=f"Uploaded {label} for SOP {sop.title}", target_type="sop_version",
        target_id=row.id, entity_type="sop", entity_id=sop.id, change_kind="updated")
    return _with_pending(_sop_version(row), pending)


@router.get("/wtd-lists")
async def get_wtd_lists(p: ConsolePrincipal = Depends(require_console_access),
                        s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(IntranetWtdList).where(
        IntranetWtdList.tenant_id == p.user.tenant_id).order_by(IntranetWtdList.position))).scalars().all()
    out = _list([_wtd(r) for r in rows], await _count(s, IntranetWtdList, p.user.tenant_id))
    out["stats"] = await _wtd_stats(s, p.user.tenant_id)
    out["integrations"] = await _wtd_integrations(s, p.user.tenant_id, {r.provider for r in rows})
    return out


@router.patch("/wtd-lists/{list_id}")
async def patch_wtd_list(list_id: uuid.UUID, body: dict = Body(...),
                         p: ConsolePrincipal = Depends(require_console_access),
                         s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetWtdList, p.user.tenant_id, list_id)
    body = _body(body)
    _unknown(body, {"position", "name", "provider", "external_list_id", "script_name", "daily_target", "active"})
    if "position" in body:
        row.position = _int(body, "position", min_value=0) or 0
    if "name" in body:
        row.name = _text(body, "name", required=True) or row.name
    if "provider" in body:
        row.provider = _text(body, "provider", required=True, max_len=80) or row.provider
    if "external_list_id" in body:
        row.external_list_id = _text(body, "external_list_id", nullable=True)
    if "script_name" in body:
        row.script_name = _text(body, "script_name", nullable=True)
    if "daily_target" in body:
        row.daily_target = _int(body, "daily_target", min_value=1)
    if "active" in body:
        row.active = bool(_bool(body, "active"))
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.wtd_list.updated", category="Win the Day",
        summary=f"Updated Win the Day list {row.position}", target_type="wtd_list",
        target_id=row.id, entity_type="wtd_list", entity_id=row.id)
    return _with_pending(_wtd(row), pending)


@router.put("/wtd-lists/order")
async def put_wtd_order(body: dict = Body(...), p: ConsolePrincipal = Depends(require_console_access),
                        s: AsyncSession = Depends(get_session)):
    ids = _body(body).get("ids")
    if not isinstance(ids, list):
        _unprocessable("ids", "Expected a full ordered id list.")
    rows = (await s.execute(select(IntranetWtdList).where(
        IntranetWtdList.tenant_id == p.user.tenant_id))).scalars().all()
    by_id = {str(r.id): r for r in rows}
    if set(map(str, ids)) != set(by_id):
        _unprocessable("ids", "Must include every list exactly once.")
    for pos, row_id in enumerate(ids, start=1):
        by_id[str(row_id)].position = pos
        by_id[str(row_id)].draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.wtd_list.reordered", category="Win the Day",
        summary="Reordered Win the Day lists", target_type="wtd_list",
        entity_type="wtd_list")
    return {**(await get_wtd_lists(p, s)), "pending_changes": pending}


@router.get("/tiles")
async def get_tiles(p: ConsolePrincipal = Depends(require_console_access),
                    s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(IntranetLaunchpadTile).where(
        IntranetLaunchpadTile.tenant_id == p.user.tenant_id).order_by(
            IntranetLaunchpadTile.sort, IntranetLaunchpadTile.name))).scalars().all()
    roles = await _tile_roles(s, p.user.tenant_id, [r.id for r in rows])
    return _list([_tile(r, roles) for r in rows], await _count(s, IntranetLaunchpadTile, p.user.tenant_id))


@router.post("/tiles")
async def create_tile(body: dict = Body(...), p: ConsolePrincipal = Depends(require_console_access),
                      s: AsyncSession = Depends(get_session)):
    body = _body(body)
    _unknown(body, {"name", "logo_key", "tile_group", "url", "auth_type", "sort", "active"})
    row = IntranetLaunchpadTile(
        tenant_id=p.user.tenant_id,
        name=_text(body, "name", required=True) or "",
        logo_key=_text(body, "logo_key", nullable=True),
        tile_group=_text(body, "tile_group") or "Tools",
        url=_https_url(body, "url", required=True) or "",
        auth_type=_enum(body, "auth_type", TILE_AUTH_TYPES, "Link") or "Link",
        sort=_int(body, "sort", default=await _count(s, IntranetLaunchpadTile, p.user.tenant_id), min_value=0) or 0,
        active=_bool(body, "active", True),
    )
    s.add(row)
    pending = await _record_mutation(
        s, p, action="config.tile.created", category="Launchpad",
        summary=f"Created launchpad tile {row.name}", target_type="tile",
        target_id=row.id, entity_type="tile", entity_id=row.id, change_kind="created")
    return _with_pending(_tile(row), pending)


@router.patch("/tiles/{tile_id}")
async def patch_tile(tile_id: uuid.UUID, body: dict = Body(...),
                     p: ConsolePrincipal = Depends(require_console_access),
                     s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetLaunchpadTile, p.user.tenant_id, tile_id)
    body = _body(body)
    _unknown(body, {"name", "logo_key", "tile_group", "url", "auth_type", "sort", "active"})
    if "name" in body:
        row.name = _text(body, "name", required=True) or row.name
    if "logo_key" in body:
        row.logo_key = _text(body, "logo_key", nullable=True)
    if "tile_group" in body:
        row.tile_group = _text(body, "tile_group", required=True) or row.tile_group
    if "url" in body:
        row.url = _https_url(body, "url", required=True) or row.url
    if "auth_type" in body:
        row.auth_type = _enum(body, "auth_type", TILE_AUTH_TYPES) or row.auth_type
    if "sort" in body:
        row.sort = _int(body, "sort", min_value=0) or 0
    if "active" in body:
        row.active = bool(_bool(body, "active"))
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="config.tile.updated", category="Launchpad",
        summary=f"Updated launchpad tile {row.name}", target_type="tile",
        target_id=row.id, entity_type="tile", entity_id=row.id)
    return _with_pending(_tile(row), pending)


@router.delete("/tiles/{tile_id}")
async def delete_tile(tile_id: uuid.UUID, p: ConsolePrincipal = Depends(require_console_access),
                      s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetLaunchpadTile, p.user.tenant_id, tile_id)
    row.active = False
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="config.tile.archived", category="Launchpad",
        summary=f"Set launchpad tile {row.name} to inactive", target_type="tile",
        target_id=row.id, entity_type="tile", entity_id=row.id, change_kind="deleted")
    return _with_pending(_tile(row), pending)


@router.put("/tiles/{tile_id}/roles")
async def put_tile_roles(tile_id: uuid.UUID, body: dict = Body(...),
                         p: ConsolePrincipal = Depends(require_console_access),
                         s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetLaunchpadTile, p.user.tenant_id, tile_id)
    role_ids = _body(body).get("role_ids")
    if not isinstance(role_ids, list):
        _unprocessable("role_ids", "Expected a list.")
    roles = await _roles_by_id(s, p.user.tenant_id)
    parsed: list[uuid.UUID] = []
    for idx, rid in enumerate(role_ids):
        try:
            val = uuid.UUID(str(rid))
        except ValueError:
            _unprocessable(f"role_ids.{idx}", "Expected a UUID.")
        if val not in roles:
            raise HTTPException(404, "Role not found.")
        parsed.append(val)
    await s.execute(sa_delete(IntranetLaunchpadTileRole).where(
        IntranetLaunchpadTileRole.tenant_id == p.user.tenant_id,
        IntranetLaunchpadTileRole.tile_id == tile_id))
    for rid in sorted(set(parsed), key=lambda v: roles[v].sort):
        s.add(IntranetLaunchpadTileRole(tenant_id=p.user.tenant_id, tile_id=tile_id, role_id=rid))
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="config.tile.roles_updated", category="Launchpad",
        summary=f"Updated launchpad visibility for {row.name}", target_type="tile",
        target_id=row.id, entity_type="tile", entity_id=row.id)
    return _with_pending(_tile(row, await _tile_roles(s, p.user.tenant_id, [row.id])), pending)


@router.put("/tiles/order")
async def put_tile_order(body: dict = Body(...), p: ConsolePrincipal = Depends(require_console_access),
                         s: AsyncSession = Depends(get_session)):
    ids = _body(body).get("ids")
    if not isinstance(ids, list):
        _unprocessable("ids", "Expected a full ordered id list.")
    rows = (await s.execute(select(IntranetLaunchpadTile).where(
        IntranetLaunchpadTile.tenant_id == p.user.tenant_id))).scalars().all()
    by_id = {str(r.id): r for r in rows}
    if set(map(str, ids)) != set(by_id):
        _unprocessable("ids", "Must include every tile exactly once.")
    for sort, row_id in enumerate(ids):
        by_id[str(row_id)].sort = sort
        by_id[str(row_id)].draft_dirty = True
    pending = await _record_mutation(
        s, p, action="config.tile.reordered", category="Launchpad",
        summary="Reordered launchpad tiles", target_type="tile", entity_type="tile")
    return {**(await get_tiles(p, s)), "pending_changes": pending}


@router.get("/calendar-categories")
async def get_calendar_categories(p: ConsolePrincipal = Depends(require_console_access),
                                  s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(IntranetCalendarCategory).where(
        IntranetCalendarCategory.tenant_id == p.user.tenant_id).order_by(
            IntranetCalendarCategory.sort, IntranetCalendarCategory.name))).scalars().all()
    roles = await _calendar_roles(s, p.user.tenant_id, [r.id for r in rows])
    return _list([_calendar_category(r, roles) for r in rows], await _count(s, IntranetCalendarCategory, p.user.tenant_id))


@router.post("/calendar-categories")
async def create_calendar_category(body: dict = Body(...),
                                   p: ConsolePrincipal = Depends(require_console_access),
                                   s: AsyncSession = Depends(get_session)):
    body = _body(body)
    _unknown(body, {"name", "color", "calendar_address", "sort", "active"})
    row = IntranetCalendarCategory(
        tenant_id=p.user.tenant_id,
        name=_text(body, "name", required=True) or "",
        color=_text(body, "color") or "#C9A227",
        calendar_address=_text(body, "calendar_address", nullable=True),
        sort=_int(body, "sort", default=await _count(s, IntranetCalendarCategory, p.user.tenant_id), min_value=0) or 0,
        active=_bool(body, "active", True),
    )
    s.add(row)
    pending = await _record_mutation(
        s, p, action="console.calendar.create", category="Calendar",
        summary=f"Created calendar category {row.name}", target_type="calendar_category",
        target_id=row.id, entity_type="calendar_category", entity_id=row.id,
        change_kind="created")
    return _with_pending(_calendar_category(row), pending)


@router.patch("/calendar-categories/{category_id}")
async def patch_calendar_category(category_id: uuid.UUID, body: dict = Body(...),
                                  p: ConsolePrincipal = Depends(require_console_access),
                                  s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetCalendarCategory, p.user.tenant_id, category_id)
    body = _body(body)
    _unknown(body, {"name", "color", "calendar_address", "sort", "active"})
    if "name" in body:
        row.name = _text(body, "name", required=True) or row.name
    if "color" in body:
        row.color = _text(body, "color", required=True, max_len=32) or row.color
    if "calendar_address" in body:
        row.calendar_address = _text(body, "calendar_address", nullable=True)
    if "sort" in body:
        row.sort = _int(body, "sort", min_value=0) or 0
    if "active" in body:
        row.active = bool(_bool(body, "active"))
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="console.calendar.update", category="Calendar",
        summary=f"Updated calendar category {row.name}", target_type="calendar_category",
        target_id=row.id, entity_type="calendar_category", entity_id=row.id)
    return _with_pending(_calendar_category(row), pending)


@router.delete("/calendar-categories/{category_id}")
async def delete_calendar_category(category_id: uuid.UUID,
                                   p: ConsolePrincipal = Depends(require_console_access),
                                   s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetCalendarCategory, p.user.tenant_id, category_id)
    row.active = False
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="console.calendar.archive", category="Calendar",
        summary=f"Set calendar category {row.name} to inactive", target_type="calendar_category",
        target_id=row.id, entity_type="calendar_category", entity_id=row.id,
        change_kind="deleted")
    return _with_pending(_calendar_category(row), pending)


@router.get("/integrations")
async def get_integrations(p: ConsolePrincipal = Depends(require_console_access),
                           s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(IntranetIntegration).where(
        IntranetIntegration.tenant_id == p.user.tenant_id).order_by(
            IntranetIntegration.display_name))).scalars().all()
    return _list([_integration(r) for r in rows], await _count(s, IntranetIntegration, p.user.tenant_id))


@router.patch("/integrations/{integration_id}")
async def patch_integration(integration_id: uuid.UUID, body: dict = Body(...),
                            p: ConsolePrincipal = Depends(require_console_access),
                            s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetIntegration, p.user.tenant_id, integration_id)
    body = _body(body)
    _unknown(body, {"display_name", "role_label", "description", "status", "base_url", "config"})
    if "display_name" in body:
        row.display_name = _text(body, "display_name", required=True) or row.display_name
    if "role_label" in body:
        row.role_label = _text(body, "role_label", required=True) or row.role_label
    if "description" in body:
        row.description = _text(body, "description", nullable=True)
    if "status" in body:
        row.status = _enum(body, "status", INTEGRATION_STATUSES) or row.status
    if "base_url" in body:
        row.base_url = _text(body, "base_url", nullable=True)
    if "config" in body:
        row.config = _json_object(body, "config") or {}
    pending = await _record_mutation(
        s, p, action="console.integration.update", category="Integrations",
        summary=f"Updated integration settings for {row.display_name}",
        target_type="integration", target_id=row.id, pending=False)
    return _with_pending(_integration(row), pending)


@router.post("/integrations/{integration_id}/connect")
async def connect_integration(integration_id: uuid.UUID, body: dict = Body(default_factory=dict),
                              p: ConsolePrincipal = Depends(require_console_access),
                              s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetIntegration, p.user.tenant_id, integration_id)
    body = _body(body or {})
    config = {k: v for k, v in (body.get("config") or {}).items()
              if "token" not in k.lower() and "secret" not in k.lower() and "key" not in k.lower()}
    if config:
        row.config = {**(row.config or {}), **config}
    row.status = "Action Needed"
    row.last_sync_status = "Not tested"
    pending = await _record_mutation(
        s, p, action="console.integration.connect", category="Integrations",
        summary=f"Prepared {row.display_name} connection settings",
        target_type="integration", target_id=row.id, pending=False)
    return {"item": _integration(row), "connected": False,
            "message": "Connection details saved; credentials still need configuration.",
            "pending_changes": pending}


@router.post("/integrations/{integration_id}/test")
async def test_integration(integration_id: uuid.UUID, p: ConsolePrincipal = Depends(require_console_access),
                           s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetIntegration, p.user.tenant_id, integration_id)
    row.last_sync_status = "Not connected"
    row.last_error = "Integration credentials are not configured."
    pending = await _record_mutation(
        s, p, action="console.integration.test", category="Integrations",
        summary=f"Tested {row.display_name}: not connected",
        target_type="integration", target_id=row.id, pending=False)
    return {"ok": False, "message": row.last_error, "item": _integration(row),
            "pending_changes": pending}


@router.get("/ai")
async def get_ai(p: ConsolePrincipal = Depends(require_console_access),
                 s: AsyncSession = Depends(get_session)):
    roles = await _roles_by_id(s, p.user.tenant_id)
    sources = (await s.execute(select(IntranetAiSource).where(
        IntranetAiSource.tenant_id == p.user.tenant_id).order_by(IntranetAiSource.sort))).scalars().all()
    settings = await _ai_setting_row(s, p.user.tenant_id)
    return {"settings": _ai_settings(settings),
            "sources": _list([_ai_source(r, roles) for r in sources], len(sources))}


@router.patch("/ai/settings")
async def patch_ai_settings(body: dict = Body(...), p: ConsolePrincipal = Depends(require_console_access),
                            s: AsyncSession = Depends(get_session)):
    row = await _ai_setting_row(s, p.user.tenant_id)
    body = _body(body)
    _unknown(body, {"always_cite", "refuse_without_source", "offer_escalation",
                    "learn_from_corrections", "escalation_channel"})
    for field in ("always_cite", "refuse_without_source", "offer_escalation", "learn_from_corrections"):
        if field in body:
            setattr(row, field, bool(_bool(body, field)))
    if "escalation_channel" in body:
        row.escalation_channel = _text(body, "escalation_channel", nullable=True)
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="console.ai.settings", category="AI",
        summary="Updated Utah Life assistant guardrails", target_type="ai_setting",
        target_id=p.user.tenant_id, entity_type="ai_setting", entity_id=None)
    return _with_pending(_ai_settings(row), pending)


@router.patch("/ai/sources/{source_id}")
async def patch_ai_source(source_id: uuid.UUID, body: dict = Body(...),
                          p: ConsolePrincipal = Depends(require_console_access),
                          s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetAiSource, p.user.tenant_id, source_id)
    body = _body(body)
    _unknown(body, {"name", "description", "source_kind", "min_role_id", "enabled", "sort"})
    if "name" in body:
        row.name = _text(body, "name", required=True) or row.name
    if "description" in body:
        row.description = _text(body, "description", nullable=True)
    if "source_kind" in body:
        row.source_kind = _text(body, "source_kind", required=True, max_len=80) or row.source_kind
    if "min_role_id" in body:
        rid = _uuid_value(body, "min_role_id", nullable=True)
        if rid is not None:
            await _role(s, p.user.tenant_id, rid)
        row.min_role_id = rid
    if "enabled" in body:
        row.enabled = bool(_bool(body, "enabled"))
    if "sort" in body:
        row.sort = _int(body, "sort", min_value=0) or 0
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="console.ai.source", category="AI",
        summary=f"Updated assistant source {row.name}", target_type="ai_source",
        target_id=row.id, entity_type="ai_source", entity_id=row.id)
    return _with_pending(_ai_source(row, await _roles_by_id(s, p.user.tenant_id)), pending)


@router.get("/content-gaps")
async def get_content_gaps(p: ConsolePrincipal = Depends(require_console_access),
                           s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(IntranetContentGap).where(
        IntranetContentGap.tenant_id == p.user.tenant_id).order_by(
            IntranetContentGap.last_asked_at.desc().nullslast(), IntranetContentGap.ask_count.desc()))).scalars().all()
    members = {m.id: m for m in (await s.execute(select(IntranetMember).where(
        IntranetMember.tenant_id == p.user.tenant_id))).scalars().all()}
    return _list([_content_gap(r, members.get(r.assigned_member_id)) for r in rows], len(rows))


@router.patch("/content-gaps/{gap_id}")
async def patch_content_gap(gap_id: uuid.UUID, body: dict = Body(...),
                            p: ConsolePrincipal = Depends(require_console_access),
                            s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetContentGap, p.user.tenant_id, gap_id)
    body = _body(body)
    _unknown(body, {"status", "resolution_note", "assigned_member_id"})
    if "status" in body:
        row.status = _enum(body, "status", GAP_STATUSES) or row.status
    if "resolution_note" in body:
        row.resolution_note = _text(body, "resolution_note", nullable=True)
    if "assigned_member_id" in body:
        mid = _uuid_value(body, "assigned_member_id", nullable=True)
        await _member_by_id_or_none(s, p.user.tenant_id, mid)
        row.assigned_member_id = mid
    pending = await _record_mutation(
        s, p, action="console.content_gap.update", category="AI",
        summary=f"Updated content gap: {row.question[:80]}", target_type="content_gap",
        target_id=row.id, pending=False)
    return _with_pending(_content_gap(row, await _member_by_id_or_none(s, p.user.tenant_id, row.assigned_member_id)), pending)


@router.get("/setup-tasks")
async def get_setup_tasks(p: ConsolePrincipal = Depends(require_console_access),
                          s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(IntranetSetupTask).where(
        IntranetSetupTask.tenant_id == p.user.tenant_id).order_by(IntranetSetupTask.sort))).scalars().all()
    return _list([_setup_task(r) for r in rows], await _count(s, IntranetSetupTask, p.user.tenant_id))


@router.patch("/setup-tasks/{key}")
async def patch_setup_task(key: str, body: dict = Body(...),
                           p: ConsolePrincipal = Depends(require_console_access),
                           s: AsyncSession = Depends(get_session)):
    row = (await s.execute(select(IntranetSetupTask).where(
        IntranetSetupTask.tenant_id == p.user.tenant_id,
        IntranetSetupTask.key == key,
    ))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Setup task not found.")
    body = _body(body)
    _unknown(body, {"completed"})
    completed = _bool(body, "completed")
    row.completed_at = _now() if completed else None
    row.completed_by = p.member.id if completed else None
    pending = await _record_mutation(
        s, p, action="console.setup.update", category="Setup",
        summary=f"{'Completed' if completed else 'Reopened'} setup task {row.label}",
        target_type="setup_task", target_id=row.id, pending=False)
    return _with_pending(_setup_task(row), pending)


@router.get("/publish/pending")
async def get_pending_changes(p: ConsolePrincipal = Depends(require_console_access),
                              s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(IntranetPendingChange).where(
        IntranetPendingChange.tenant_id == p.user.tenant_id,
        IntranetPendingChange.publish_batch_id.is_(None),
    ).order_by(IntranetPendingChange.created_at.desc()))).scalars().all()
    return _list([_pending(r) for r in rows], len(rows))


async def _set_publish_state(s: AsyncSession, tenant_id, when: dt.datetime, dirty: bool) -> None:
    models = (
        IntranetWorkspace, IntranetRole, IntranetCapability, IntranetPermission,
        IntranetCourse, IntranetCourseRole, IntranetLesson, IntranetSopCategory,
        IntranetSop, IntranetSopVersion, IntranetWtdList, IntranetLaunchpadTile,
        IntranetLaunchpadTileRole, IntranetCalendarCategory, IntranetCalendarCategoryRole,
        IntranetAiSource, IntranetAiSetting,
    )
    for model in models:
        rows = (await s.execute(select(model).where(model.tenant_id == tenant_id))).scalars().all()
        for row in rows:
            row.published_at = when
            row.draft_dirty = dirty


@router.post("/publish")
async def publish_changes(body: dict = Body(default_factory=dict),
                          p: ConsolePrincipal = Depends(require_console_access),
                          s: AsyncSession = Depends(get_session)):
    body = _body(body or {})
    when = _now()
    pending_rows = (await s.execute(select(IntranetPendingChange).where(
        IntranetPendingChange.tenant_id == p.user.tenant_id,
        IntranetPendingChange.publish_batch_id.is_(None),
    ))).scalars().all()
    bundle = await _config_bundle(s, p.user.tenant_id)
    batch = IntranetPublishBatch(
        tenant_id=p.user.tenant_id,
        published_at=when,
        published_by=p.member.id,
        note=_text(body, "note", nullable=True),
        snapshot=bundle,
    )
    s.add(batch)
    await s.flush()
    for row in pending_rows:
        row.publish_batch_id = batch.id
    await _set_publish_state(s, p.user.tenant_id, when, False)
    pending = await _record_mutation(
        s, p, action="console.publish", category="Publish",
        summary=f"Published {len(pending_rows)} intranet changes", target_type="publish_batch",
        target_id=batch.id, pending=False)
    return {"batch_id": _id(batch.id), "published_count": len(pending_rows),
            "pending_changes": pending}


@router.post("/publish/discard")
async def discard_changes(p: ConsolePrincipal = Depends(require_console_access),
                          s: AsyncSession = Depends(get_session)):
    count = await _pending_count(s, p.user.tenant_id)
    await s.execute(sa_delete(IntranetPendingChange).where(
        IntranetPendingChange.tenant_id == p.user.tenant_id,
        IntranetPendingChange.publish_batch_id.is_(None),
    ))
    await _set_publish_state(s, p.user.tenant_id, _now(), False)
    pending = await _record_mutation(
        s, p, action="console.discard", category="Publish",
        summary=f"Discarded {count} draft intranet changes", target_type="pending_change",
        pending=False)
    return {"discarded_count": count, "pending_changes": pending}


@router.get("/publish/batches")
async def get_publish_batches(p: ConsolePrincipal = Depends(require_console_access),
                              s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(IntranetPublishBatch).where(
        IntranetPublishBatch.tenant_id == p.user.tenant_id).order_by(
            IntranetPublishBatch.published_at.desc()).limit(50))).scalars().all()
    return _list([_batch(r) for r in rows], len(rows))


@router.post("/publish/batches/{batch_id}/rollback")
async def rollback_publish_batch(batch_id: uuid.UUID, p: ConsolePrincipal = Depends(require_console_access),
                                 s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetPublishBatch, p.user.tenant_id, batch_id)
    row.rolled_back_at = _now()
    row.rolled_back_by = p.member.id
    pending = await _record_mutation(
        s, p, action="console.rollback", category="Publish",
        summary="Rolled back an intranet publish batch", target_type="publish_batch",
        target_id=row.id, pending=False)
    return _with_pending(_batch(row), pending)


@router.get("/audit")
async def get_audit(category: str | None = Query(None), before: dt.datetime | None = Query(None),
                    limit: int = Query(50, ge=1, le=100),
                    p: ConsolePrincipal = Depends(require_console_access),
                    s: AsyncSession = Depends(get_session)):
    where = [AuditLog.tenant_id == p.user.tenant_id]
    if category:
        where.append(AuditLog.category == category)
    if before:
        where.append(AuditLog.created_at < before)
    rows = (await s.execute(select(AuditLog).where(*where).order_by(
        AuditLog.created_at.desc()).limit(limit + 1))).scalars().all()
    next_cursor = _iso(rows[-1].created_at) if len(rows) > limit else None
    rows = rows[:limit]
    return {"items": [_audit(r) for r in rows], "total": len(rows), "cursor": next_cursor}


@router.get("/preview")
async def get_preview(role: str = Query(...), p: ConsolePrincipal = Depends(require_console_access),
                      s: AsyncSession = Depends(get_session)):
    role_row = (await s.execute(select(IntranetRole).where(
        IntranetRole.tenant_id == p.user.tenant_id,
        (IntranetRole.key == role) | (IntranetRole.name == role),
    ))).scalar_one_or_none()
    if role_row is None:
        try:
            role_row = await _one(s, IntranetRole, p.user.tenant_id, uuid.UUID(role))
        except ValueError:
            role_row = None
    if role_row is None:
        raise HTTPException(404, "Role not found.")
    bundle = await _config_bundle(s, p.user.tenant_id)
    role_id = _id(role_row.id)
    bundle["preview_role"] = _role_out(role_row)
    bundle["courses"]["items"] = [item for item in bundle["courses"]["items"]
                                  if role_id in item.get("role_ids", [])]
    bundle["courses"]["total"] = len(bundle["courses"]["items"])
    bundle["tiles"]["items"] = [item for item in bundle["tiles"]["items"]
                                if item["active"] and role_id in item.get("role_ids", [])]
    bundle["tiles"]["total"] = len(bundle["tiles"]["items"])
    bundle["calendar_categories"]["items"] = [
        item for item in bundle["calendar_categories"]["items"]
        if item["active"] and role_id in item.get("role_ids", [])
    ]
    bundle["calendar_categories"]["total"] = len(bundle["calendar_categories"]["items"])
    return bundle
