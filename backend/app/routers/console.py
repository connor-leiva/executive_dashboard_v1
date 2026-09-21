from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any
from urllib.parse import urlparse

from fastapi import (APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException,
                     Query, Request, Response, UploadFile)
import httpx
from sqlalchemy import delete as sa_delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..db import get_session
from ..deps import ConsolePrincipal, require_console_access
from ..models import (
    Agent,
    Base,
    AuditLog,
    Integration,
    IntranetAiSetting,
    IntranetAiQuestion,
    IntranetAiSource,
    IntranetCalendarCategory,
    IntranetCalendarCategoryRole,
    IntranetCapability,
    IntranetContentGap,
    IntranetCourse,
    IntranetCourseRole,
    IntranetIntegration,
    IntranetMarketingAttachment,
    IntranetMarketingRequest,
    IntranetMarketingSetting,
    IntranetLaunchpadTile,
    IntranetLaunchpadTileRole,
    IntranetLesson,
    IntranetCourseSection,
    IntranetLessonAttachment,
    IntranetMember,
    IntranetPendingChange,
    IntranetPage,
    IntranetPageRole,
    IntranetPageSection,
    IntranetPermission,
    IntranetPublishBatch,
    IntranetRole,
    IntranetSetupTask,
    IntranetSop,
    IntranetSopAcknowledgement,
    IntranetSopCategory,
    IntranetSopSuggestion,
    IntranetSopVersion,
    IntranetDirectorySetting,
    IntranetWtdList,
    IntranetWtdPlaybook,
    IntranetWtdScript,
    IntranetWorkspace,
    Tenant, User,
)
from ..services.audit import audit
from ..security import enc
from .. import plans
from ..security import new_action_token
from ..integrations import fub
from ..services import (binder_storage, course_sections, follow_ups, google_auth, lesson_media,
                        lesson_richtext, mail_templates,
                        mailer, member_identity, uploads,
                        marketing_delivery, member_numbers, sop_drafting, sop_library,
                        whos_who, wtd_playbook)
from ..services.users import INVITE_DAYS, link_base, primary_host
from ..services.inheritance import (INHERITED_PROVIDERS, dashboard_connections, ensure_rows,
                                    is_inherited)

router = APIRouter(prefix="/console", tags=["console"])

LEVELS = {"Full", "View", "Limited", "None"}
COURSE_STATES = {"Draft", "Live", "Needs Review"}
SOP_SUGGESTION_STATUSES = ("New", "Read", "Done")
SOP_STATES = {"Draft", "Live", "Needs Review", "Archived"}
MEMBER_STATUSES = {"Active", "Invited", "Removed"}
MEMBER_FILTERS = {"active", "pending", "guests", "leadership", "everyone"}
AUTH_SOURCES = {"SSO", "Guest", "Manual"}
LESSON_SOURCE_TYPES = {"LOOM", "SKOOL", "HERE", "PDF", "EXP", "PLACE"}
# WHAT a lesson is, as against LESSON_SOURCE_TYPES, which is WHERE it lives. Kept apart on
# purpose: a reading lesson has no host, and a PDF-hosted video is not a document.
LESSON_KINDS = {"video", "reading", "document"}
GROUPING_SCHEMES = set(course_sections.SCHEMES)
SECTION_RELEASE_RULES = set(course_sections.RELEASE_RULES)
SECTION_DUE_RULES = set(course_sections.DUE_RULES)
MAX_LESSON_IMAGE_BYTES = 5 * 1024 * 1024
LESSON_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
TILE_AUTH_TYPES = {"SSO", "Deeplink", "Invite", "Link"}
INTEGRATION_STATUSES = {"Connected", "Action Needed", "Not Connected"}
GAP_STATUSES = {"Open", "Assigned", "Resolved", "No Action"}
LOGO_KINDS = {"light": "logo_light_key", "dark": "logo_dark_key", "mark": "logo_mark_key"}
LOGO_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}
PALETTE_KEYS = {"ink", "brand", "accent", "canvas", "gold"}
SECRET_CONFIG_PARTS = ("token", "secret", "password", "credential", "api_key", "apikey", "access_key", "private_key")
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


def _luminance(hex_color: str) -> float:
    """WCAG 2.1 relative luminance."""
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    def channel(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = channel(r), channel(g), channel(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# The portal's default canvas, for judging an ink the caller sends without one.
DEFAULT_CANVAS = "#EAE7E6"
MIN_INK_CONTRAST = 4.5          # WCAG AA for body text


def _palette(body: dict) -> dict:
    palette = _json_object(body, "palette") or {}
    clean = {}
    for key, value in palette.items():
        if key not in PALETTE_KEYS:
            _unprocessable("palette", f"Unknown swatch {key}.")
        if not _hex_color(value):
            _unprocessable(f"palette.{key}", "Expected #RRGGBB.")
        clean[key] = value.upper()

    # INK IS BODY TEXT, not just a rail colour -- the portal's --ink drives paragraph copy on the
    # canvas AND the rail behind the navigation. A light ink is therefore not a stylistic choice
    # the design can absorb; it is grey text on a white page and white nav labels on a pale rail.
    # Measured live before this check existed: 1.23:1, which is invisible.
    #
    # Refused at write time rather than worked around at render time, because no amount of
    # deriving shades rescues text that has no contrast with the surface it sits on -- and an
    # admin who picked it deserves to be told, not to have it silently adjusted into something
    # they did not choose.
    ink = clean.get("ink")
    if ink:
        canvas = clean.get("canvas") or DEFAULT_CANVAS
        ratio = _contrast(ink, canvas)
        if ratio < MIN_INK_CONTRAST:
            _unprocessable(
                "palette.ink",
                f"This ink is too light to read on the canvas ({ratio:.1f}:1; needs "
                f"{MIN_INK_CONTRAST:.1f}:1). Ink is the body text colour as well as the "
                f"navigation background, so a pale one makes the portal unreadable.")
    return clean


def _secret_config_key(key: str) -> bool:
    """Whether a config key names a credential, however it is spelt.

    Compared with the separators removed. It matched the raw lowercase key, so `api_key` was
    caught and "API Key" -- the way a person types it into the config editor -- was not: a live
    Follow Up Boss key sat in plaintext in a workspace's portal row and came back in this API's
    responses. `secret_config_key` in migration 0072 is the same rule, for the rows already stored.
    """
    folded = _fold(key)
    return any(_fold(part) in folded for part in SECRET_CONFIG_PARTS)


def _fold(text: str) -> str:
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def _integration_config(body: dict) -> dict:
    config = _json_object(body, "config") or {}
    clean = {}
    for raw_key, raw_value in config.items():
        key = str(raw_key or "").strip()
        if not key:
            _unprocessable("config", "Config keys cannot be blank.")
        if _secret_config_key(key):
            continue
        if isinstance(raw_value, (dict, list)):
            _unprocessable(f"config.{key}", "Expected a text, number, boolean, or empty value.")
        clean[key] = raw_value
    return clean


def _https_url(body: dict, field: str, *, required: bool = False) -> str | None:
    raw = _text(body, field, required=required)
    if not raw:
        return None
    value = raw.strip()
    # A raw space is never valid in a URL, and without this the scheme-prepending below turns
    # "not a url" into "https://not a url", whose netloc parses as "not" and passes every check
    # underneath. That reaches the launchpad tile and AI source validators too, so a tile could be
    # saved pointing at a destination that is not an address. %20 is untouched: this rejects the
    # unescaped character, not an encoded space.
    if any(ch.isspace() for ch in value):
        _unprocessable(field, "Expected a valid http or https URL.")
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


def _not_signin():
    """Google sign-in shares the integration table but is not one of the workspace's data
    connections, and it has its own console panel -- the only place its client secret can be
    set, since the generic integration form strips secret-looking keys by design.

    Returned as a filter rather than written out at each call site, so the list and the count
    that sits next to it cannot drift apart.
    """
    return IntranetIntegration.provider_key != google_auth.PROVIDER_KEY


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


async def _members_by_id(s: AsyncSession, tenant_id) -> dict[uuid.UUID, IntranetMember]:
    """Tenant-scoped, so a label can only ever be resolved from this workspace's own roster."""
    rows = (await s.execute(select(IntranetMember).where(
        IntranetMember.tenant_id == tenant_id))).scalars().all()
    return {r.id: r for r in rows}


async def _role_by_key(s: AsyncSession, tenant_id, key: str) -> IntranetRole:
    row = (await s.execute(select(IntranetRole).where(
        IntranetRole.tenant_id == tenant_id,
        IntranetRole.key == key,
    ))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Role not found.")
    return row


def guest_role_of(roles) -> IntranetRole | None:
    """Which of these roles a guest belongs in: the least privileged one.

    DERIVED, not a hardcoded key. Three places used to look up "jv_partner" -- a role that exists
    in the Utah Life seed and in no other workspace. On any workspace created through provisioning
    that meant the console's DEFAULT invite (auth source "Guest") answered 404 "Role not found.",
    and the Guests stat and filter silently counted zero forever. The first customer worked and
    every one after it did not, which is exactly the failure a multi-tenant product cannot carry.

    Least privileged = the highest `sort` among non-leadership roles. For Utah Life that is still
    JV Partner (sort 5, not leadership), so nothing changes for them; a bootstrapped workspace
    gets Member.
    """
    ranked = sorted(roles, key=lambda r: (bool(r.is_leadership), -int(r.sort or 0)))
    return ranked[0] if ranked else None


async def _guest_role(s: AsyncSession, tenant_id) -> IntranetRole:
    rows = (await s.execute(select(IntranetRole).where(
        IntranetRole.tenant_id == tenant_id))).scalars().all()
    row = guest_role_of(rows)
    if row is None:
        raise HTTPException(404, "This workspace has no roles yet.")
    return row


async def _role_ids_from_body(s: AsyncSession, tenant_id, body: dict, *, default_all: bool = False) -> list[uuid.UUID]:
    role_ids = body.get("role_ids")
    roles = await _roles_by_id(s, tenant_id)
    if role_ids is None and default_all:
        return [role.id for role in sorted(roles.values(), key=lambda item: item.sort)]
    if not isinstance(role_ids, list):
        _unprocessable("role_ids", "Expected a list.")
    parsed: list[uuid.UUID] = []
    for idx, rid in enumerate(role_ids):
        try:
            val = uuid.UUID(str(rid))
        except ValueError:
            _unprocessable(f"role_ids.{idx}", "Expected a UUID.")
        if val not in roles:
            raise HTTPException(404, "Role not found.")
        parsed.append(val)
    return sorted(set(parsed), key=lambda value: roles[value].sort)


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
    """The portal's workspace row. Its appearance is its OWN -- see inheritance.py: the portal and
    the dashboard look different on purpose, so brand is not inherited."""
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


def _invite_state(account: User | None) -> str:
    """Where somebody's way in stands: no account yet, added but not invited (the invite is held
    until Send invite), invited, accepted, or an account the dashboard has disabled."""
    if account is None:
        return "no_account"
    if account.status == "active":
        return "accepted"
    if account.status == "invited":
        return "not_sent" if account.invite_held else "sent"
    return "disabled"


async def _account_of(s: AsyncSession, row: IntranetMember) -> User | None:
    if row.user_id is None:
        return None
    account = await s.get(User, row.user_id)
    return account if account is not None and account.tenant_id == row.tenant_id else None


def _member(row: IntranetMember, roles: dict[uuid.UUID, IntranetRole] | None = None,
            account: User | None = None) -> dict:
    role = roles.get(row.role_id) if roles else None
    return {
        "id": _id(row.id), "user_id": _id(row.user_id), "full_name": row.full_name,
        "email": row.email, "role_id": _id(row.role_id), "role_key": role.key if role else None,
        "role_name": role.name if role else None, "market": row.market,
        "auth_source": row.auth_source, "status": row.status,
        "invited_at": _iso(row.invited_at), "activated_at": _iso(row.activated_at),
        "removed_at": _iso(row.removed_at), "last_synced_at": _iso(row.last_synced_at),
        # Profile. Returned so the console can edit what the directory shows -- an admin editing
        # a colleague's title should see the one already set rather than a blank box.
        "title": row.title, "bio": row.bio, "phone": row.phone, "owns": row.owns,
        "has_photo": bool(row.photo_key),
        "started_on": _iso(row.started_on), "wtd_goals": row.wtd_goals or {},
        # Who's Who: everything the profile drawer edits, as stored.
        "headline": row.headline, "tag": row.tag, "help_line": row.help_line, "quote": row.quote,
        "bring": row.bring or [], "office": row.office, "pronoun": row.pronoun or "they",
        "message_url": row.message_url, "owns_items": row.owns_items or [],
        "photo_focus": row.photo_focus, "directory_placement": row.directory_placement or "auto",
        "directory_order": row.directory_order,
        "invite": _invite_state(account),
    }


async def _member_stats(s: AsyncSession, tenant_id, roles: dict[uuid.UUID, IntranetRole]) -> dict:
    leadership_ids = [role_id for role_id, role in roles.items() if role.is_leadership]
    guest_role = guest_role_of(roles.values())
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
    # COALESCE, not a second SUM: a lesson has one length. Summing duration_minutes alone is what
    # made a course of articles advertise itself as taking no time at all, and summing both would
    # double-count any lesson whose kind an author had switched.
    minutes = func.coalesce(IntranetLesson.duration_minutes, IntranetLesson.read_minutes, 0)
    rows = (await s.execute(
        select(IntranetLesson.course_id, func.count(), func.coalesce(func.sum(minutes), 0))
        .where(IntranetLesson.tenant_id == tenant_id, IntranetLesson.course_id.in_(course_ids))
        .group_by(IntranetLesson.course_id)
    )).all()
    return {row[0]: {"lesson_count": int(row[1]), "total_duration_minutes": int(row[2] or 0)} for row in rows}


async def _last_editor(s: AsyncSession, tenant_id, course_id) -> str | None:
    """Who touched this course last, from the audit trail.

    Lessons are audited against their own ids, so this asks about the course row and the entity
    ids underneath it -- editing a lesson is editing the course as far as the header is concerned,
    and a header that said "Aug 24" while somebody renamed a lesson a minute ago would be wrong in
    the one way a "last edited" line must not be.
    """
    lesson_ids = (await s.execute(select(IntranetLesson.id).where(
        IntranetLesson.tenant_id == tenant_id,
        IntranetLesson.course_id == course_id))).scalars().all()
    targets = [str(course_id)] + [str(i) for i in lesson_ids]
    row = (await s.execute(
        select(AuditLog.actor_label)
        .where(AuditLog.tenant_id == tenant_id,
               AuditLog.target_id.in_(targets),
               AuditLog.category == "Training")
        .order_by(AuditLog.created_at.desc())
        .limit(1))).scalars().first()
    return row or None


def _course(row: IntranetCourse, roles: dict[uuid.UUID, list[str]] | None = None,
            counts: dict[uuid.UUID, dict] | None = None, lessons: list[dict] | None = None,
            last_editor: str | None = None, sections: list[dict] | None = None) -> dict:
    derived = (counts or {}).get(row.id, {})
    return {
        "id": _id(row.id), "title": row.title, "category": row.category,
        "description": row.description, "state": row.state,
        "track_progress": bool(row.track_progress),
        "required_for_onboarding": bool(row.required_for_onboarding),
        "issues_certificate": bool(row.issues_certificate), "sequential": bool(row.sequential),
        # The scheme lives on the COURSE so switching Day -> Module is one write that renames
        # every section. The labels themselves are generated in `sections` below.
        "grouping_scheme": row.grouping_scheme or "none",
        "lock_sections": bool(row.lock_sections),
        # Always a list, never null. The course LIST does not load sections (it does not need
        # them), and a payload where one endpoint says `null` and another says `[]` is a
        # `.length` crash waiting for whichever frontend forgets the guard.
        "sections": sections or [],
        "sort": row.sort, "archived_at": _iso(row.archived_at),
        "updated_at": _iso(row.updated_at),
        # Read off the audit trail rather than stored on the course. Adding an `updated_by` column
        # would be a second record of something already written on every mutation, and the day the
        # two disagreed the header would be the one people read.
        "last_editor": last_editor,
        "published_at": _iso(row.published_at), "draft_dirty": bool(row.draft_dirty),
        "lesson_count": derived.get("lesson_count", len(lessons or [])),
        "total_duration_minutes": derived.get("total_duration_minutes", 0),
        "role_ids": (roles or {}).get(row.id, []),
        "lessons": lessons,
    }


def _lesson_attachment(row: IntranetLessonAttachment) -> dict:
    return {
        "id": _id(row.id), "title": row.title, "kind": row.kind, "note": row.note,
        "url": row.url, "filename": row.filename, "content_type": row.content_type,
        "byte_size": row.byte_size, "sort": row.sort,
        "published_at": _iso(row.published_at), "draft_dirty": bool(row.draft_dirty),
    }


def _section(row: IntranetCourseSection, scheme: str | None, index: int,
             counts: dict | None = None) -> dict:
    """One section for the BUILDER, which is a different view from the portal's.

    No release/due dates and no lock state here: those resolve against a member's enrolment, and
    an author has none. What the builder needs is the rule the author chose, so the form can show
    it back to them.
    """
    return {
        "id": _id(row.id), "course_id": _id(row.course_id), "name": row.name,
        "summary": row.summary, "sort": row.sort,
        # Generated, never stored -- see services/course_sections.
        "label": course_sections.label_for(scheme, index),
        "release_rule": row.release_rule or "immediate", "release_day": row.release_day,
        "release_date": _iso(row.release_date),
        "due_rule": row.due_rule or "none", "due_day": row.due_day,
        "lesson_count": (counts or {}).get(str(row.id), {}).get("count", 0),
        "total_minutes": (counts or {}).get(str(row.id), {}).get("minutes", 0),
        "published_at": _iso(row.published_at), "draft_dirty": bool(row.draft_dirty),
    }


def _lesson(row: IntranetLesson, attachments: list | None = None) -> dict:
    kind = row.kind or "video"
    return {
        "id": _id(row.id), "course_id": _id(row.course_id), "title": row.title,
        "section_id": _id(row.section_id), "kind": kind,
        "source_type": row.source_type, "source_ref": row.source_ref,
        "source_label": row.source_label, "description": row.description,
        "taught_by": row.taught_by, "duration_minutes": row.duration_minutes,
        # Sanitized again on the way out. The write-side allowlist can change, and a body stored
        # under yesterday's rules should not keep whatever yesterday allowed. Then image keys are
        # rewritten to the console's own image route -- an owner who administers the workspace
        # without being in its member directory cannot fetch through the portal's.
        "body_html": lesson_richtext.image_urls(
            lesson_richtext.sanitize(row.body_html, tenant_id=row.tenant_id),
            lesson_richtext.CONSOLE_IMAGE_ROUTE) or None,
        "word_count": row.word_count, "read_minutes": row.read_minutes,
        "page_count": row.page_count,
        "duration": course_sections.duration_of(
            kind, duration_minutes=row.duration_minutes, read_minutes=row.read_minutes,
            page_count=row.page_count),
        "required": bool(row.required), "sort": row.sort,
        # Resolved with the SAME function the portal payload uses, so the console can warn about
        # a source that will not play BEFORE a member finds out by opening it. None for a reading
        # lesson, which has no source and never will.
        "player": lesson_media.resolve(row.source_type, row.source_ref, kind=kind),
        "attachments": [_lesson_attachment(a) for a in (attachments or [])],
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
         version_count: int = 0, acknowledged_count: int = 0,
         open_suggestions: int = 0) -> dict:
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
        # The procedure (services/sop_library). `body` is the draft this page edits;
        # `body_live` says whether what members are reading is what is on screen, because the
        # text -- alone in this console -- waits for Publish.
        "summary": row.summary, "applies_to": row.applies_to,
        "tool_ids": list(row.tool_ids or []), "required": bool(row.required),
        "last_reviewed_on": _iso(row.last_reviewed_on),
        "open_suggestions": open_suggestions,
        "body": row.body or None,
        "body_live": (row.body or None) == (row.published_body or None),
        "has_published_body": sop_library.has_body(row.published_body),
    }


def _wtd(row: IntranetWtdList) -> dict:
    return {
        "id": _id(row.id), "position": row.position, "name": row.name,
        "provider": row.provider, "external_list_id": row.external_list_id,
        "script_name": row.script_name, "daily_target": row.daily_target,
        "group_key": row.group_key, "block_key": row.block_key, "cadence": row.cadence,
        "kind": row.kind or "clear", "description": row.description,
        "script_ids": [str(x) for x in (row.script_ids or [])],
        "active": bool(row.active), "published_at": _iso(row.published_at),
        "draft_dirty": bool(row.draft_dirty),
    }


def _wtd_script(row: IntranetWtdScript) -> dict:
    return {
        "id": _id(row.id), "name": row.name, "chip": row.chip, "url": row.url,
        "description": row.description, "group_key": row.group_key, "position": row.position,
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
    # The dashboard's status and FUB's own address, as the portal itself resolves them: this map
    # read the portal row alone, so a list on a connected Follow Up Boss showed as unlinked here.
    inherited = await dashboard_connections(s, tenant_id)
    details = await _dashboard_details(s, tenant_id)
    out = {}
    for row in rows:
        item = _integration(row, inherited, details)
        if not item["base_url"] and item.get("default_base_url"):
            item["base_url"] = item["default_base_url"]
        out[row.provider_key] = item
    return out


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


def _integration_status(row: IntranetIntegration) -> str:
    if row.last_error:
        return "Action Needed"
    sync_status = str(row.last_sync_status or "").strip().lower()
    if sync_status in {"connected", "ok", "success", "synced"} and row.last_sync_at:
        return "Connected"
    if sync_status or row.credential_ref or row.last_sync_at:
        return "Action Needed"
    return "Not Connected"


def _public_integration_config(config: dict | None) -> dict:
    return {
        str(key): value
        for key, value in (config or {}).items()
        if not _secret_config_key(str(key))
    }


async def _dashboard_details(s: AsyncSession, tenant_id) -> dict:
    """For each provider the dashboard owns: its last sync error, and -- for Follow Up Boss -- the
    account's own smart-list address, which a Win the Day list uses when no base URL is typed."""
    rows = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id,
        Integration.provider.in_(tuple(INHERITED_PROVIDERS.values()))))).scalars().all()
    out: dict = {}
    for portal_key, dash_key in INHERITED_PROVIDERS.items():
        mine = [r for r in rows if r.provider == dash_key]
        if not mine:
            continue
        failing = next((r for r in mine if r.status == "error" and r.last_error), None)
        detail = {"error": failing.last_error[:300] if failing else None, "default_base_url": None}
        if dash_key == "fub":
            live = [r for r in mine if r.status in ("connected", "error")]
            domain = next((d for d in (follow_ups.account_domain(r) for r in live) if d), None)
            if domain:
                detail["default_base_url"] = f"https://{domain}.followupboss.com/2/people/list/"
        out[portal_key] = detail
    return out


def _integration(row: IntranetIntegration, inherited: dict[str, str] | None = None,
                 details: dict | None = None) -> dict:
    """One provider row, with the dashboard's answer preferred where the dashboard owns it.

    `inherited` is the workspace's dashboard connections. Where a provider appears there, its
    status comes from the dashboard and the console stops offering a credential form -- the
    connection that does the work lives on the other surface, and a second form here would write
    to a row nothing reads while looking like it had done something.
    """
    owned_elsewhere = is_inherited(row.provider_key)
    status = (inherited or {}).get(row.provider_key) if owned_elsewhere else None
    return {
        "id": _id(row.id), "provider_key": row.provider_key,
        "display_name": row.display_name, "role_label": row.role_label,
        "description": row.description,
        "status": status or _integration_status(row),
        "base_url": row.base_url, "config": _public_integration_config(row.config),
        "last_sync_at": _iso(row.last_sync_at),
        "last_sync_status": row.last_sync_status, "last_error": row.last_error,
        "connect_available": False,
        "test_available": False,
        # Told to the console so it can say WHERE the connection lives rather than silently
        # disabling a button.
        "inherited": owned_elsewhere,
        "inherited_from": "Axcion dashboard" if owned_elsewhere else None,
        # The dashboard's own failure, where the connection lives; and the address a Win the Day
        # list opens in when no base URL is set here.
        "dashboard_error": ((details or {}).get(row.provider_key) or {}).get("error")
        if owned_elsewhere else None,
        "default_base_url": ((details or {}).get(row.provider_key) or {}).get("default_base_url")
        if owned_elsewhere else None,
    }


def _ai_source(row: IntranetAiSource, roles: dict[uuid.UUID, IntranetRole] | None = None) -> dict:
    role = roles.get(row.min_role_id) if roles and row.min_role_id else None
    return {
        "id": _id(row.id), "name": row.name, "description": row.description,
        "source_kind": row.source_kind, "min_role_id": _id(row.min_role_id),
        "min_role_name": role.name if role else None, "enabled": bool(row.enabled),
        "last_crawled_at": _iso(row.last_crawled_at),
        "indexed_item_count": int(row.indexed_item_count or 0), "sort": row.sort,
        "crawl_status": "Indexed" if row.last_crawled_at else "Not yet indexed",
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


MARKETING_DESTINATIONS = {"none", "slack", "email", "webhook"}

# The fields a tenant may demand of a submitter. A fixed vocabulary rather than free text: these
# keys drive the intranet's form, so an unknown one would be a required field nothing renders.
# `attachments` is back, and only because submission became ATOMIC. It was pulled when the submit
# endpoint took JSON: an admin could require a file the form had no way to send, which makes a
# request impossible to file and impossible to diagnose. Requiring it is only enforceable when
# the requirement and the file arrive in the same request, which multipart submission gives us.
# A test asserts this set stays a subset of what the intranet will accept.
MARKETING_FIELDS = {"listing", "client", "request_type", "due_date", "priority", "description",
                    "attachments"}


def _marketing_ready(row: IntranetMarketingSetting) -> bool:
    """Whether the CONFIG is complete -- not whether delivery works.

    Those are different questions and the second one cannot be answered from this row. A complete
    configuration with a destination nobody has ever delivered to is exactly the state this
    product is in until Phase 10, and the console has to be able to say so without claiming a
    connection."""
    if not row.enabled or row.destination_type == "none":
        return False
    return bool((row.destination or "").strip())


def _delivery_health(row: IntranetMarketingSetting) -> str:
    """Whether requests actually leave the building.

    Deliberately not derived from `enabled` alone. A saved destination proves somebody typed a
    channel name, and the only thing that proves delivery works is a delivery -- so a complete
    configuration nobody has tested says "untested" rather than borrowing the confidence of a
    filled-in form."""
    if not _marketing_ready(row):
        return "off"
    if row.last_test_ok is True:
        return "live"
    if row.last_test_ok is False:
        return "failing"
    return "untested"


def _marketing(row: IntranetMarketingSetting,
               roles: dict[uuid.UUID, IntranetRole] | None = None) -> dict:
    role = (roles or {}).get(row.default_role_id) if row.default_role_id else None
    return {
        "enabled": bool(row.enabled),
        "destination_type": row.destination_type,
        "destination": row.destination,
        "default_role_id": _id(row.default_role_id),
        "default_role_name": role.name if role is not None else None,
        "required_fields": list(row.required_fields or []),
        "notify": row.notify,
        # Configuration completeness and delivery health are reported separately and never
        # collapsed into one "connected" flag, because a saved form proves nothing about a
        # destination. `last_tested_at: null` means nobody has tried, which is the truth today.
        "config_complete": _marketing_ready(row),
        "last_tested_at": _iso(row.last_tested_at),
        "last_test_ok": None if row.last_test_ok is None else bool(row.last_test_ok),
        "last_test_detail": row.last_test_detail,
        # Was "pending_runtime" while nothing delivered. It now says whether requests are
        # actually routed, which is a different question from whether the form is filled in:
        # a complete configuration that has never delivered is "untested", not "live".
        "delivery": _delivery_health(row),
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


async def _setup_evidence(s: AsyncSession, tenant_id) -> dict[str, bool | None]:
    """Whether each checklist item's underlying configuration actually exists.

    The checklist was a row of manual checkboxes: `completed` was whatever somebody ticked, so a
    workspace could show "SOPs uploaded" complete with no SOPs and "Calendars connected" complete
    with no calendar. A setup checklist that can be satisfied by clicking it is a progress bar for
    the person clicking, not a statement about the workspace.

    `None` MEANS "NOT DERIVABLE HERE", and it is a real answer rather than a gap to fill in later.
    Whether somebody has genuinely assigned an onboarding path, or reviewed permissions, is not
    visible in a row count, and inventing a proxy for it would put us back to a checkbox that
    claims more than it knows -- just with extra steps. Those stay manual and say so.
    """
    ws = await _workspace_row(s, tenant_id)

    async def any_of(model, *where) -> bool:
        return await _count(s, model, tenant_id, *where) > 0

    return {
        # A workspace has its identity when a mark has actually been uploaded.
        "brand": bool(ws.logo_light_key or ws.logo_dark_key or ws.logo_mark_key),
        "training": await any_of(IntranetCourse),
        "sops": await any_of(IntranetSop),
        "wtd": await any_of(IntranetWtdList),
        "launchpad": await any_of(IntranetLaunchpadTile),
        # A calendar CATEGORY is not a connected calendar; an address is.
        "calendar": await any_of(IntranetCalendarCategory,
                                 IntranetCalendarCategory.calendar_address.is_not(None)),
        "assistant": await any_of(IntranetAiSource),
        # Somebody arrived through the identity provider, which is what "sync" means here.
        # Members can also be added by hand, and those do not evidence a sync.
        "roster": await any_of(IntranetMember, IntranetMember.auth_source == "SSO"),
        # Not derivable, deliberately -- see the docstring.
        "perms": None,
        "onboarding_path": None,
        "announcement_channel": None,
    }


def _setup_task(row: IntranetSetupTask, satisfied: bool | None = None) -> dict:
    return {
        "id": _id(row.id), "key": row.key, "label": row.label,
        "destination": row.destination, "sort": row.sort,
        "completed_at": _iso(row.completed_at), "completed_by": _id(row.completed_by),
        # Reported next to the tick, never instead of it. `satisfied: false` beside a completed
        # task is the interesting state: somebody ticked it and the configuration is not there.
        "satisfied": satisfied,
        "verifiable": satisfied is not None,
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
    suggestion_rows = (await s.execute(select(
        IntranetSopSuggestion.sop_id, func.count()).where(
            IntranetSopSuggestion.tenant_id == tenant_id,
            IntranetSopSuggestion.status != "Done",
            IntranetSopSuggestion.sop_id.in_([r.id for r in rows]) if rows else False,
        ).group_by(IntranetSopSuggestion.sop_id))).all()
    suggestions = {r[0]: int(r[1]) for r in suggestion_rows}
    total = await _count(s, IntranetSop, tenant_id, *([] if include_archived else [IntranetSop.archived_at.is_(None)]))
    return _list([
        _sop(r, cats.get(r.category_id), owners.get(r.owner_member_id), versions.get(r.current_version_id),
             version_counts.get(r.id, 0), ack_counts.get(r.id, 0), suggestions.get(r.id, 0))
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
    wtd_book = await _playbook_row(s, tenant_id)
    wtd_scripts = (await s.execute(select(IntranetWtdScript).where(
        IntranetWtdScript.tenant_id == tenant_id).order_by(IntranetWtdScript.position))).scalars().all()
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
    marketing = await _marketing_row(s, tenant_id)
    inherited_conn = await dashboard_connections(s, tenant_id)
    evidence = await _setup_evidence(s, tenant_id)
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
        "wtd_playbook": wtd_playbook.current(wtd_book.content) if wtd_book else None,
        "wtd_scripts": _list([_wtd_script(r) for r in wtd_scripts]),
        "tiles": _list([_tile(r, tile_roles) for r in tiles]),
        "calendar_categories": _list([_calendar_category(r, cal_roles) for r in cals]),
        "integrations": _list([_integration(r, inherited_conn) for r in integrations]),
        "ai": {"settings": _ai_settings(ai_setting),
               "sources": _list([_ai_source(r, role_map) for r in ai_sources])},
        "marketing": _marketing(marketing, role_map),
        "content_gaps": _list([_content_gap(r) for r in gaps]),
        "setup_tasks": _list([_setup_task(r, evidence.get(r.key)) for r in setup]),
        "pending_changes": await _pending_count(s, tenant_id),
    }


async def _ai_setting_row(s: AsyncSession, tenant_id) -> IntranetAiSetting:
    row = await s.get(IntranetAiSetting, tenant_id)
    if row is None:
        row = IntranetAiSetting(tenant_id=tenant_id)
        s.add(row)
        await s.flush()
    return row


async def _marketing_row(s: AsyncSession, tenant_id) -> IntranetMarketingSetting:
    row = await s.get(IntranetMarketingSetting, tenant_id)
    if row is None:
        # Created disabled with destination "none": a tenant that has never opened this screen
        # has not chosen a destination, and the row has to say that rather than default to one.
        row = IntranetMarketingSetting(tenant_id=tenant_id)
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
        "integrations": await _count(s, IntranetIntegration, tid, _not_signin()),
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
    fields = set(body)
    calendar_fields = {"timezone", "week_starts_on", "default_calendar_view"}
    calendar_only = bool(fields) and fields <= calendar_fields
    pending = await _record_mutation(
        s, p,
        action="config.calendar.updated" if calendar_only else "config.brand.updated",
        category="Calendar" if calendar_only else "Workspace",
        summary="Updated calendar defaults" if calendar_only else f"Updated workspace identity for {row.portal_name}",
        target_type="workspace", target_id=row.id, entity_type="workspace", entity_id=row.id)
    return _with_pending(_workspace(row), pending)


MARKETING_STATUSES = {"New", "In Progress", "Blocked", "Done", "Cancelled"}


def _delivery_state(row: IntranetMarketingRequest) -> str:
    """Where this request stands, read off the timestamps that record it.

    Derived and not stored. A status column would be a second account of the same facts, and the
    day it drifts from them the console shows "Delivered" over a null delivered_at."""
    if row.delivered_at is not None:
        return "delivered"
    if row.delivery_next_attempt_at is not None:
        return "queued" if not (row.delivery_attempts or 0) else "retrying"
    return "failed" if (row.delivery_attempts or 0) else "not_queued"


def _marketing_request(row: IntranetMarketingRequest,
                       members: dict[uuid.UUID, IntranetMember] | None = None,
                       attachments: list | None = None) -> dict:
    assignee = (members or {}).get(row.assignee_member_id) if row.assignee_member_id else None
    return {
        "id": _id(row.id), "title": row.title, "request_type": row.request_type,
        "listing": row.listing, "client": row.client, "description": row.description,
        "due_date": row.due_date.isoformat() if row.due_date else None,
        "priority": row.priority, "status": row.status,
        "requester_label": row.requester_label,
        "assignee_member_id": _id(row.assignee_member_id),
        "assignee_label": assignee.full_name if assignee is not None else None,
        # Never collapsed into a "sent" boolean, because "not delivered" has three meanings the
        # queue has to keep apart: waiting its turn, retrying after a failure, and given up.
        # `delivery` derives all four states from the timestamps rather than storing a fifth
        # column that could disagree with them.
        "delivered_at": _iso(row.delivered_at),
        "delivery_detail": row.delivery_detail,
        "delivery": _delivery_state(row),
        "delivery_attempts": int(row.delivery_attempts or 0),
        "delivery_next_attempt_at": _iso(row.delivery_next_attempt_at),
        "attachments": [
            {"id": _id(a.id), "filename": a.filename, "content_type": a.content_type,
             "byte_size": a.byte_size}
            for a in (attachments or [])
        ],
        "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at),
    }


# ── Google sign-in ────────────────────────────────────────────────────────────────────────
# Every workspace signs in through ONE Google app, Axcion's -- see services/google_auth for why
# it is not a client each team registers. What is left for a workspace is two choices: whether
# the button is offered, and which email domains may use it.


async def _google_row(s: AsyncSession, tenant_id: uuid.UUID) -> IntranetIntegration:
    row = (await s.execute(select(IntranetIntegration).where(
        IntranetIntegration.tenant_id == tenant_id,
        IntranetIntegration.provider_key == google_auth.PROVIDER_KEY))).scalar_one_or_none()
    if row is None:
        row = IntranetIntegration(
            tenant_id=tenant_id, provider_key=google_auth.PROVIDER_KEY,
            display_name="Google Workspace", role_label="Sign-in",
            description="How your team signs in to the portal.",
            status="Not Connected", config={})
        s.add(row)
        await s.flush()
    return row


def _google_out(row: IntranetIntegration | None) -> dict:
    choices = google_auth.workspace_choices(row)
    available = google_auth.platform_configured()
    return {
        **choices,
        # Whether this deployment has Axcion's Google app at all. A workspace can leave sign-in on
        # while this is false; the button simply does not appear until it is true.
        "available": available,
        "status": "Connected" if available and choices["enabled"] else "Not Connected",
    }


@router.get("/google-signin")
async def get_google_signin(p: ConsolePrincipal = Depends(require_console_access),
                            s: AsyncSession = Depends(get_session)):
    # Read without creating: a workspace that has chosen nothing has the defaults, and a GET that
    # wrote a row to say so would be a write nobody asked for.
    row = (await s.execute(select(IntranetIntegration).where(
        IntranetIntegration.tenant_id == p.user.tenant_id,
        IntranetIntegration.provider_key == google_auth.PROVIDER_KEY))).scalar_one_or_none()
    return {"item": _google_out(row)}


@router.patch("/google-signin")
async def patch_google_signin(body: dict = Body(...),
                              p: ConsolePrincipal = Depends(require_console_access),
                              s: AsyncSession = Depends(get_session)):
    """Whether this workspace offers Google sign-in, and to which email domains.

    NOT publish-aware, and that is deliberate. Everything else in this console is content an
    admin stages and publishes to their members; this is the door, and it either works now or it
    does not.

    THERE ARE NO CREDENTIALS TO SET. The app is Axcion's and every workspace uses it, so a body
    that still carries a client id or secret is refused rather than quietly ignored -- an old form
    must not look as though it saved one.
    """
    body = _body(body)
    _unknown(body, {"allowed_domains", "enabled"})
    row = await _google_row(s, p.user.tenant_id)
    cfg = dict(row.config or {})
    # What the per-workspace design stored, dropped on the first save: a client id nothing reads
    # is noise, and a secret nothing reads should not be kept at all.
    cfg.pop("client_id", None)
    row.credential_ref = None

    if "allowed_domains" in body:
        raw = body.get("allowed_domains") or []
        if not isinstance(raw, list):
            _unprocessable("allowed_domains", "Expected a list of domains.")
        seen, domains = set(), []
        for item in raw:
            d = str(item or "").strip().lower().lstrip("@")
            if not d:
                continue
            if " " in d or "." not in d:
                _unprocessable("allowed_domains", f"{d!r} is not a domain name.")
            if d not in seen:
                seen.add(d)
                domains.append(d)
        cfg["allowed_domains"] = domains
    if "enabled" in body:
        cfg["enabled"] = bool(_bool(body, "enabled"))

    row.config = cfg
    out = _google_out(row)
    row.status = out["status"]
    if "enabled" in body:
        summary = "Google sign-in turned on" if out["enabled"] else "Google sign-in turned off"
    else:
        summary = "Google sign-in domains updated"
    await _record_mutation(
        s, p,
        action="config.google_signin.updated",
        category="Sign-in",
        summary=summary,
        target_type="integration", target_id=row.id,
        detail={"allowed_domains": out["allowed_domains"], "enabled": out["enabled"]},
        pending=False)
    await s.commit()
    return {"item": out}


@router.get("/marketing/requests")
async def list_marketing_requests(status: str | None = None,
                                  p: ConsolePrincipal = Depends(require_console_access),
                                  s: AsyncSession = Depends(get_session)):
    where = [IntranetMarketingRequest.tenant_id == p.user.tenant_id]
    if status:
        if status not in MARKETING_STATUSES:
            _unprocessable("status", f"Expected one of: {', '.join(sorted(MARKETING_STATUSES))}.")
        where.append(IntranetMarketingRequest.status == status)
    rows = (await s.execute(select(IntranetMarketingRequest).where(*where)
                            .order_by(IntranetMarketingRequest.created_at.desc())
                            .limit(200))).scalars().all()
    members = await _members_by_id(s, p.user.tenant_id)
    # One query for every row's attachments, grouped in Python. A per-row query here would be
    # 200 round trips on a busy queue.
    by_request: dict[uuid.UUID, list] = {}
    if rows:
        for att in (await s.execute(select(IntranetMarketingAttachment).where(
            IntranetMarketingAttachment.tenant_id == p.user.tenant_id,
            IntranetMarketingAttachment.request_id.in_([r.id for r in rows]),
        ))).scalars().all():
            by_request.setdefault(att.request_id, []).append(att)
    total = await _count(s, IntranetMarketingRequest, p.user.tenant_id)
    return _list([_marketing_request(r, members, by_request.get(r.id)) for r in rows], total)


@router.get("/marketing/requests/{request_id}/attachments/{attachment_id}")
async def download_marketing_attachment(request_id: uuid.UUID, attachment_id: uuid.UUID,
                                        p: ConsolePrincipal = Depends(require_console_access),
                                        s: AsyncSession = Depends(get_session)):
    """The console reads the WORKSPACE's files, where the intranet route reads only the
    requester's own. Different authorisation, so a separate route rather than a flag.

    Served with the stored (sniffed) type and an attachment disposition, same as the intranet
    side: a file one member uploaded is opened here by another, which is precisely the path
    stored XSS would take.
    """
    row = (await s.execute(select(IntranetMarketingAttachment).where(
        IntranetMarketingAttachment.tenant_id == p.user.tenant_id,
        IntranetMarketingAttachment.id == attachment_id,
        IntranetMarketingAttachment.request_id == request_id,
    ))).scalars().first()
    if row is None or not binder_storage.exists(row.storage_key):
        raise HTTPException(404, "Not found")
    safe = binder_storage.safe_filename(row.filename)
    return Response(
        content=binder_storage.read(row.storage_key),
        media_type=row.content_type,
        headers={"Content-Disposition": 'attachment; filename="' + safe + '"'},
    )


@router.patch("/marketing/requests/{request_id}")
async def patch_marketing_request(request_id: uuid.UUID, body: dict = Body(...),
                                  p: ConsolePrincipal = Depends(require_console_access),
                                  s: AsyncSession = Depends(get_session)):
    """Move a request through the queue.

    NOT publish-aware, deliberately, and this is the one place in the console where that is
    right. Everything else here is CONFIGURATION -- a draft of how the workspace should behave,
    which an admin stages and publishes. A request's status is operational fact: somebody either
    started the work or they did not, and holding that in a draft until a publish would mean an
    agent watching their request sees "New" while it is finished. `pending=False` says so.
    """
    row = await _one(s, IntranetMarketingRequest, p.user.tenant_id, request_id)
    body = _body(body)
    _unknown(body, {"status", "assignee_member_id"})

    changed = []
    was_status = row.status
    if "status" in body:
        status = _enum(body, "status", MARKETING_STATUSES)
        if status and status != row.status:
            changed.append(f"status {row.status} -> {status}")
            row.status = status
    if "assignee_member_id" in body:
        member_id = _uuid_value(body, "assignee_member_id", nullable=True)
        # Resolved against THIS tenant, so a member id from elsewhere 404s rather than being
        # written into a foreign key that would then render somebody else's name.
        member = await _member_by_id_or_none(s, p.user.tenant_id, member_id)
        if member_id is not None and member is None:
            raise HTTPException(404, "Not found")
        row.assignee_member_id = member.id if member is not None else None
        changed.append(f"assigned to {member.full_name if member else 'nobody'}")

    members = await _members_by_id(s, p.user.tenant_id)
    summary = f"{row.title}: {'; '.join(changed) if changed else 'no change'}"
    pending = await _record_mutation(
        s, p, action="marketing.request_updated", category="Marketing", summary=summary,
        target_type="marketing_request", target_id=row.id, pending=False)
    # _record_mutation commits, and `updated_at` carries an onupdate, so the ORM expires it and
    # the next attribute read would lazy-load outside the async greenlet -- a 500, not a warning.
    # Refreshed explicitly rather than serialised before the commit, so the response carries the
    # timestamp the database actually wrote.
    await s.refresh(row)
    attachments = (await s.execute(select(IntranetMarketingAttachment).where(
        IntranetMarketingAttachment.tenant_id == p.user.tenant_id,
        IntranetMarketingAttachment.request_id == row.id,
    ))).scalars().all()
    # TELL THE PERSON WHO ASKED. An agent files a request and then has no way to know anything
    # happened to it -- the portal has no inbox, so without this the only way to find out a flyer
    # is finished is to ask the person who finished it. Only on a real status CHANGE: reassigning
    # a request between two people in marketing is not news to the agent who filed it.
    if row.status != was_status:
        await _notify_requester(s, row, members, was_status)
    return _with_pending(_marketing_request(row, members, list(attachments)), pending)


async def _notify_requester(s: AsyncSession, row: IntranetMarketingRequest,
                            members: dict, was: str) -> None:
    """Email the agent whose request just moved. Best effort, and never in the way.

    After the commit and outside the response's success, on purpose. The status change is
    already saved and correct; a bounced address or a Resend outage must not turn a completed
    console action into an error the admin has to interpret -- mailer.send swallows its own
    failures for the same reason.
    """
    member = (members or {}).get(row.requester_member_id)
    to = (getattr(member, "email", None) or "").strip()
    if not to:
        return                        # removed from the roster, or never had an address
    done = row.status in ("Done", "Cancelled")
    subject = f"Your marketing request is {row.status.lower()}: {row.title}"
    assignee = (members or {}).get(row.assignee_member_id)
    lines = [f"{row.title}", "", f"Status: {was} -> {row.status}"]
    if assignee is not None:
        lines.append(f"With: {assignee.full_name}")
    lines.append("")
    lines.append("Nothing to do -- this is just so you know where it stands."
                 if not done else "That is this one closed out.")
    text = "\n".join(lines)
    await mailer.send(to, subject, "<p>" + text.replace("\n", "<br>") + "</p>", text,
                      # One email per request per status. A double-clicked Done button in the
                      # console must not send the agent the same news twice.
                      idempotency_key=f"marketing-status-{row.id}-{row.status}")


@router.get("/marketing")
async def get_marketing(p: ConsolePrincipal = Depends(require_console_access),
                        s: AsyncSession = Depends(get_session)):
    row = await _marketing_row(s, p.user.tenant_id)
    roles = await _roles_by_id(s, p.user.tenant_id)
    # _marketing_row creates the singleton on first read; commit so a later PATCH updates the
    # same row rather than racing a second insert on the primary key.
    await s.commit()
    return {"item": _marketing(row, roles),
            "field_options": sorted(MARKETING_FIELDS),
            "destination_types": sorted(MARKETING_DESTINATIONS)}


@router.patch("/marketing")
async def patch_marketing(body: dict = Body(...),
                          p: ConsolePrincipal = Depends(require_console_access),
                          s: AsyncSession = Depends(get_session)):
    """Where a marketing request goes, and what a submitter must fill in.

    VALIDATION IS PER DESTINATION TYPE, because "destination" means a different thing in each:
    a Slack channel, an email address, or an https URL. Accepting any string for all three would
    let a workspace save `#marketing` as a webhook and only discover it when a real request
    silently failed to deliver.

    The https requirement on webhooks is not cosmetic. This value is a URL the server will later
    POST to on a user's behalf, which is server-side request forgery surface (handoff Sec 17), so
    the scheme is pinned here and the egress allowlist belongs with the delivery code in Phase 10.
    """
    body = _body(body)
    _unknown(body, {"enabled", "destination_type", "destination", "default_role_id",
                    "required_fields", "notify"})
    row = await _marketing_row(s, p.user.tenant_id)

    if "destination_type" in body:
        kind = _enum(body, "destination_type", MARKETING_DESTINATIONS)
        row.destination_type = kind or row.destination_type
    if "destination" in body:
        raw = _text(body, "destination", nullable=True, max_len=500)
        kind = row.destination_type
        if raw and kind == "webhook":
            # Normalised, not just checked: _https_url adds a missing scheme and upgrades http,
            # so the stored value is the one the server would actually POST to.
            raw = _https_url({"destination": raw}, "destination", required=True)
        elif raw and kind == "email":
            if "@" not in raw or raw.startswith("@") or raw.endswith("@"):
                _unprocessable("destination", "Must be an email address.")
        elif raw and kind == "slack":
            if not raw.startswith("#"):
                _unprocessable("destination", "Must be a channel name beginning with #.")
        elif raw and kind == "none":
            _unprocessable("destination",
                           "Choose a destination type before setting a destination.")
        row.destination = raw
    if "default_role_id" in body:
        role_id = _uuid_value(body, "default_role_id", nullable=True)
        if role_id is not None:
            await _role(s, p.user.tenant_id, role_id)      # 404s a cross-tenant or unknown role
        row.default_role_id = role_id
    if "required_fields" in body:
        raw = body.get("required_fields")
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            _unprocessable("required_fields", "Expected a list of field keys.")
        unknown = [f for f in raw if f not in MARKETING_FIELDS]
        if unknown:
            _unprocessable("required_fields",
                           f"Unknown field(s): {', '.join(sorted(str(u) for u in unknown))}. "
                           f"Expected: {', '.join(sorted(MARKETING_FIELDS))}.")
        # Deduped and ordered by the vocabulary so the stored list is stable to compare.
        row.required_fields = [f for f in sorted(MARKETING_FIELDS) if f in set(raw)]
    if "notify" in body:
        row.notify = _text(body, "notify", nullable=True, max_len=500)
    if "enabled" in body:
        enabled = _bool(body, "enabled")
        # Turning it on with nowhere to send is refused rather than saved: the intranet would
        # show a working request form over a destination that does not exist.
        if enabled and (row.destination_type == "none" or not (row.destination or "").strip()):
            _unprocessable("enabled",
                           "Set a destination type and destination before enabling requests.")
        row.enabled = bool(enabled)

    row.draft_dirty = True
    roles = await _roles_by_id(s, p.user.tenant_id)
    pending = await _record_mutation(
        s, p,
        action="config.marketing.updated",
        category="Marketing",
        summary=("Marketing requests set to "
                 f"{row.destination_type}" if row.enabled else "Marketing requests disabled"),
        target_type="marketing_setting", target_id=p.user.tenant_id,
        entity_type="marketing_setting")
    return _with_pending(_marketing(row, roles), pending)


# ── Slack, and sending a test ─────────────────────────────────────────────────────────────
# The Slack CHANNEL lives on the marketing setting, because it is routing an admin reads back.
# The BOT TOKEN lives here, in intranet_integration, because it is a credential: that table
# Fernet-encrypts it and never returns it, and the marketing setting does neither.


async def _slack_row(s: AsyncSession, tenant_id: uuid.UUID) -> IntranetIntegration:
    row = (await s.execute(select(IntranetIntegration).where(
        IntranetIntegration.tenant_id == tenant_id,
        IntranetIntegration.provider_key == marketing_delivery.SLACK_PROVIDER))).scalar_one_or_none()
    if row is None:
        row = IntranetIntegration(
            tenant_id=tenant_id, provider_key=marketing_delivery.SLACK_PROVIDER,
            display_name="Slack", role_label="Notifications",
            description="Where marketing requests are posted.",
            status="Not Connected", config={})
        s.add(row)
        await s.flush()
    return row


def _slack_out(row: IntranetIntegration) -> dict:
    return {
        # Whether a token is stored, never the token. Same rule as the Google secret: a form
        # that round-trips a credential puts it in a response body, a browser cache and a screen.
        "token_set": bool(row.credential_ref),
        "status": row.status,
        "last_error": row.last_error,
    }


@router.get("/slack")
async def get_slack(p: ConsolePrincipal = Depends(require_console_access),
                    s: AsyncSession = Depends(get_session)):
    row = await _slack_row(s, p.user.tenant_id)
    await s.commit()
    return {"item": _slack_out(row)}


@router.patch("/slack")
async def patch_slack(body: dict = Body(...),
                      p: ConsolePrincipal = Depends(require_console_access),
                      s: AsyncSession = Depends(get_session)):
    """Store or clear this workspace's Slack bot token.

    Not publish-aware, for the same reason Google sign-in is not: this is plumbing an admin
    tests immediately, not content they stage for their members.
    """
    body = _body(body)
    _unknown(body, {"bot_token", "clear"})
    row = await _slack_row(s, p.user.tenant_id)

    if body.get("clear"):
        row.credential_ref = None
        row.status = "Not Connected"
        row.last_error = None
    elif "bot_token" in body:
        raw = (_text(body, "bot_token", nullable=True, max_len=500) or "").strip()
        if raw:
            # xoxb- is the bot token; a user token (xoxp-) or a signing secret pasted here fails
            # later as a permissions error that reads like a channel problem.
            if not raw.startswith("xoxb-"):
                _unprocessable("bot_token", "A Slack bot token starts with xoxb-.")
            row.credential_ref = enc(raw)
            # "Action Needed" and not "Connected": a stored token is a stored token, and only a
            # delivery proves it works. Sending a test is what moves this on.
            row.status = "Action Needed"
            row.last_error = None

    await _record_mutation(
        s, p, action="config.slack.updated", category="Integrations",
        summary=("Slack token removed" if not row.credential_ref else "Slack token saved"),
        target_type="integration", target_id=row.id, pending=False)
    await s.commit()
    return {"item": _slack_out(row)}


@router.post("/marketing/test")
async def send_marketing_test(p: ConsolePrincipal = Depends(require_console_access),
                              s: AsyncSession = Depends(get_session)):
    """Deliver a real test message to the configured destination.

    A REAL DELIVERY, down the same code path a request takes, rather than a shape check on the
    saved values. Validating the form again would confirm only what saving it already confirmed;
    every failure worth catching here -- a channel the bot was never invited to, a revoked token,
    a webhook host that no longer resolves, a typo'd address -- is invisible until something is
    actually sent. That is why last_test_ok exists and why nothing else may write it.
    """
    row = await _marketing_row(s, p.user.tenant_id)
    if not _marketing_ready(row):
        _unprocessable("destination",
                       "Set a destination type and destination, and switch requests on, first.")

    # A stand-in request, never saved. It carries the sender's name so whoever is looking at the
    # channel knows who to ask, and says plainly that it is a test -- a message that reads like a
    # real request is one somebody in marketing will start working on.
    probe = IntranetMarketingRequest(
        id=uuid.uuid4(), tenant_id=p.user.tenant_id,
        requester_label=(p.user.name or p.user.email or "the console"),
        title="Test message from the Axcion console",
        description=("This is a connection test, not a real request -- nothing needs doing. "
                     "If you can read this, marketing requests will arrive here."),
        priority="Normal", status="New", created_at=_now())

    host = await primary_host(s, p.user.tenant_id)
    link = f"https://{host}/console/marketing" if host else None
    outcome = await marketing_delivery.send_one(s, probe, row, link)
    ok, detail = outcome.delivered, outcome.detail

    row.last_tested_at = _now()
    row.last_test_ok = bool(ok)
    row.last_test_detail = detail[:1000]
    if row.destination_type == "slack":
        # The same delivery that proves the destination proves the token, so the integration row
        # stops guessing and says what the last attempt actually did.
        slack = await _slack_row(s, p.user.tenant_id)
        slack.status = "Connected" if ok else "Action Needed"
        slack.last_error = None if ok else detail[:1000]

    await _record_mutation(
        s, p, action="config.marketing.tested", category="Marketing",
        summary=("Marketing delivery test succeeded" if ok else "Marketing delivery test failed"),
        target_type="marketing_setting", target_id=p.user.tenant_id, pending=False)
    await s.commit()
    roles = await _roles_by_id(s, p.user.tenant_id)
    return {"ok": bool(ok), "detail": detail, "item": _marketing(row, roles)}


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
            guest_role = guest_role_of(roles.values())
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
    crm = await _crm_matches(s, p.user.tenant_id, rows)
    user_ids = [r.user_id for r in rows if r.user_id is not None]
    accounts = {u.id: u for u in (await s.execute(select(User).where(
        User.tenant_id == p.user.tenant_id, User.id.in_(user_ids)))).scalars()} if user_ids else {}
    return {
        **_list([{**_member(r, roles, accounts.get(r.user_id)), "crm": crm.get(r.id)}
                 for r in rows], total),
        "roles": [_role_out(r) for r in sorted(roles.values(), key=lambda item: item.sort)],
        "stats": await _member_stats(s, p.user.tenant_id, roles),
    }


@router.post("/members/invite")
async def invite_member(body: dict = Body(...),
                        p: ConsolePrincipal = Depends(require_console_access),
                        s: AsyncSession = Depends(get_session)):
    """Add somebody to the portal: a roster entry and an account -- and send nothing yet.

    ADDED, NOT YET INVITED. The invite goes out when an admin presses Send invite on their row
    (send_member_invite, below), so a team can set people up -- roles, CRM links, a look at what
    they will see -- before telling them. Until then the account is HELD (`user.invite_held`):
    Google sign-in, "forgot password" and the workspace finder all behave as though it did not
    exist, because each of them would reach the person before the admin chose to.

    THIS ONCE CREATED A ROSTER ENTRY AND NOTHING ELSE -- no account, no link, no email, so the
    person could never sign in. The account is still made here, up front: the plan's seats count
    it, and everything that looks a person up finds a real one.

    A portal member gets `role="member"` with NO dashboard tabs. They are a buyer agent, not an
    executive: the dashboard's numbers are not theirs to see, and `_tenant_apps` leaves the
    Dashboard out for anyone whose tab list is empty.

    Somebody who already has a dashboard account is LINKED rather than refused -- and nothing is
    held or sent, because they can already sign in.
    """
    body = _body(body)
    _unknown(body, {"full_name", "email", "role_id", "market", "auth_source"})
    auth_source = _enum(body, "auth_source", AUTH_SOURCES, "Manual") or "Manual"
    role_id = _uuid_value(body, "role_id", required=auth_source != "Guest")
    if role_id is None:
        role_id = (await _guest_role(s, p.user.tenant_id)).id
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
    full_name = _text(body, "full_name", required=True) or email
    row = IntranetMember(
        tenant_id=p.user.tenant_id,
        full_name=full_name,
        email=email,
        role_id=role_id,
        market=_text(body, "market", nullable=True),
        auth_source=auth_source,
        status="Invited",
        invited_at=None,
    )
    s.add(row)

    account = (await s.execute(select(User).where(
        User.tenant_id == p.user.tenant_id, User.email == email))).scalar_one_or_none()
    if account is None:
        account = await _new_member_account(s, p, email, full_name)
    row.user_id = account.id
    summary = (f"Added {row.full_name} to the roster; their invite is not sent yet"
               if account.invite_held else f"Added {row.full_name} to the roster")
    pending = await _record_mutation(
        s, p, action="access.member.added", category="People",
        summary=summary, target_type="member", target_id=row.id, pending=False)
    out = _with_pending(_member(row, await _roles_by_id(s, p.user.tenant_id), account), pending)
    out["invite_url"] = None
    return out


async def _new_member_account(s: AsyncSession, p: ConsolePrincipal, email: str,
                              full_name: str) -> User:
    """A portal member's account, invite held. It counts against the plan exactly as the
    dashboard's own invite does: an account somebody is expected to take up is a seat."""
    tenant = await s.get(Tenant, p.user.tenant_id)
    have = (await s.execute(select(func.count()).select_from(User).where(
        User.tenant_id == p.user.tenant_id, User.status != "disabled"))).scalar_one()
    if plans.over_limit(tenant, "max_users", have):
        lim = plans.limits(tenant)
        _unprocessable("email", f"The {lim['name']} plan includes {lim['max_users']} people "
                                f"and this workspace has {have}. Upgrade to add another.")
    account = User(
        tenant_id=p.user.tenant_id, email=email, name=full_name[:200],
        password_hash=None, role="member", status="invited",
        # Empty, not null: null means "an owner, everything". This person's app is the
        # portal, and their portal permissions come from their intranet role.
        tab_access=[], token_version=0, invited_by=p.user.id, invite_held=True)
    s.add(account)
    await s.flush()
    return account


@router.post("/members/{member_id}/send-invite")
async def send_member_invite(member_id: uuid.UUID, request: Request, bg: BackgroundTasks,
                             p: ConsolePrincipal = Depends(require_console_access),
                             s: AsyncSession = Depends(get_session)):
    """Send somebody on the roster their invite -- the first time, or again.

    Releases the hold (`user.invite_held`), issues a fresh link that works for INVITE_DAYS (an
    earlier one stops working), and emails it. The link comes back too, so an admin can hand it
    over directly: the most common reason an invite "never arrived" is a spam folder, and the
    answer to that should not be to send the same email again.
    """
    row = await _one(s, IntranetMember, p.user.tenant_id, member_id)
    if row.status == "Removed":
        _unprocessable("member", f"{row.full_name} was removed from the roster. Restore them "
                                 f"before inviting them.")
    account = await _account_of(s, row)
    if account is None:
        account = (await s.execute(select(User).where(
            User.tenant_id == p.user.tenant_id, User.email == row.email))).scalar_one_or_none()
    if account is None:
        # A roster entry from before accounts were made up front.
        account = await _new_member_account(s, p, row.email, row.full_name)
    if account.status == "active":
        _unprocessable("member", f"{row.full_name} already has an account and signs in "
                                 f"normally, so there is no invite to send.")
    if account.status != "invited":
        _unprocessable("member", f"{row.full_name}'s account is disabled. Re-enable it on the "
                                 f"dashboard's Team page before inviting them.")
    first = account.invite_held or row.invited_at is None
    raw, token_hash = new_action_token()
    account.action_token_hash, account.action_token_purpose = token_hash, "invite"
    account.action_token_expires = _now() + dt.timedelta(days=INVITE_DAYS)
    account.invite_held = False
    row.user_id = account.id
    row.invited_at = _now()
    base = await link_base(request, s, p.user.tenant_id)
    invite_url = f"{base}/accept-invite?token={raw}"
    pending = await _record_mutation(
        s, p, action="access.member.invited", category="People",
        summary=(f"Invited {row.full_name} to the intranet" if first
                 else f"Sent {row.full_name} a new invite"),
        target_type="member", target_id=row.id, pending=False)

    workspace = await _workspace_row(s, p.user.tenant_id)
    tenant = await s.get(Tenant, p.user.tenant_id)
    # The PORTAL's name, not the company's: this invite is to the team portal, and that is the
    # thing the recipient will recognise in a subject line.
    name = ((workspace.portal_name if workspace is not None else None)
            or (tenant.name if tenant is not None else None) or "your workspace")
    # In the background, after the commit above. Keyed on the link's expiry, so a double-click
    # sends once and a genuinely new link sends again.
    bg.add_task(mailer.send, account.email,
                *mail_templates.invite(invite_url, p.user.name, name, INVITE_DAYS),
                reply_to=p.user.email,
                idempotency_key=f"invite-{account.id}-{account.action_token_expires.isoformat()}")
    out = _with_pending(_member(row, await _roles_by_id(s, p.user.tenant_id), account), pending)
    out["invite_url"] = invite_url
    return out


@router.patch("/members/{member_id}")
async def patch_member(member_id: uuid.UUID, body: dict = Body(...),
                       p: ConsolePrincipal = Depends(require_console_access),
                       s: AsyncSession = Depends(get_session)):
    body = _body(body)
    _unknown(body, {"full_name", "email", "role_id", "market", "status", "auth_source",
                    "title", "bio", "phone", "owns", "agent_links", "started_on", "wtd_goals"}
            | whos_who.PROFILE_FIELDS)
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
    # Profile fields. Optional and nullable: a roster row is useful with none of them, and an
    # admin filling in one person's phone number should not have to supply their bio as well.
    for field in ("title", "bio", "phone", "owns"):
        if field in body:
            setattr(row, field,
                    _text(body, field, nullable=True, max_len=4000 if field == "bio" else 200))
    # Who's Who: the profile the directory and the profile page draw (services/whos_who).
    for field in sorted(whos_who.PROFILE_FIELDS & set(body)):
        try:
            setattr(row, field, whos_who.clean_field(field, body[field]))
        except whos_who.ProfileError as e:
            _unprocessable(e.field, e.message)
        if field in ("bring", "owns_items"):
            flag_modified(row, field)
    # Win the Day, for this person: the on-ramp's day 1 (None takes them off it) and their own
    # targets, which must be ones the playbook's scoreboard counts.
    if "started_on" in body:
        row.started_on = _date_value(body, "started_on")
    if "wtd_goals" in body:
        try:
            row.wtd_goals = wtd_playbook.clean_personal_goals(
                body["wtd_goals"], wtd_playbook.tally_keys(await _draft_content(s, p.user.tenant_id)))
        except wtd_playbook.SectionError as e:
            _unprocessable(e.field, e.message)
        flag_modified(row, "wtd_goals")
    if "agent_links" in body:
        row.agent_links = await _agent_links(s, p.user.tenant_id, body["agent_links"],
                                             row.agent_links)
        flag_modified(row, "agent_links")
        if not role_changed:
            summary = f"Updated the CRM identity of {row.full_name}"

    pending = await _record_mutation(
        s, p, action=action, category="People",
        summary=summary, target_type="member",
        target_id=row.id, pending=False)
    crm = await _crm_matches(s, p.user.tenant_id, [row])
    return _with_pending({**_member(row, await _roles_by_id(s, p.user.tenant_id),
                                    await _account_of(s, row)),
                          "crm": crm.get(row.id)}, pending)


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
    return _with_pending(_member(row, await _roles_by_id(s, p.user.tenant_id),
                                 await _account_of(s, row)), pending)


@router.post("/members/sync")
async def sync_members(p: ConsolePrincipal = Depends(require_console_access),
                       s: AsyncSession = Depends(get_session)):
    pending = await _record_mutation(
        s, p, action="access.roster.synced", category="People",
        summary="Roster sync: no source connected yet", target_type="member",
        target_id=None, pending=False)
    return {"added": 0, "removed": 0, "updated": 0, "pending_changes": pending}


# -- Authored pages ------------------------------------------------------------------------
# One page type instead of four bespoke screens. See models.IntranetPage.

# NOTHING IS RESERVED, because nothing can collide. Authored pages are routed under /p/<key>,
# so a page keyed "training" sits at /p/training and the built-in Training Library keeps
# /training untouched.
#
# This started as a reserved-key list, written for a design where authored pages lived at the
# top level. Namespacing them made that list not merely unnecessary but actively wrong: it
# refused "partners", "listing", "brand" and "phone" -- precisely the four pages this feature
# exists to let a workspace replace. A test caught it.
#
# Only the SHAPE is constrained, so a key cannot carry a slash, a space or a leading hyphen
# into a URL.
PAGE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")


def _page_key(body: dict, *, required: bool = False) -> str | None:
    raw = _text(body, "key", required=required, max_len=50)
    if raw is None:
        return None
    key = raw.strip().lower()
    if not PAGE_KEY_RE.fullmatch(key):
        _unprocessable("key", "Use lowercase letters, numbers and hyphens, starting with a "
                              "letter or number.")
    return key


def _page_links(body: dict) -> list:
    raw = body.get("links")
    if raw is None:
        return []
    if not isinstance(raw, list):
        _unprocessable("links", "Expected a list of links.")
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            _unprocessable(f"links.{i}", "Expected an object with a label and a url.")
        label = str(item.get("label") or "").strip()[:120]
        if not label:
            _unprocessable(f"links.{i}.label", "A link needs a label.")
        # Reuses the shared https rule, so an authored link cannot become a javascript: URL or a
        # bare hostname that resolves somewhere unintended.
        url = _https_url({"url": item.get("url")}, "url", required=True)
        out.append({"label": label, "url": url})
    return out


def _page_out(row, sections=None, role_ids=None) -> dict:
    return {
        "id": _id(row.id), "key": row.key, "title": row.title, "subtitle": row.subtitle,
        "nav_group": row.nav_group, "sort": row.sort, "active": bool(row.active),
        "published_at": _iso(row.published_at), "draft_dirty": bool(row.draft_dirty),
        "role_ids": [_id(r) for r in (role_ids or [])],
        "sections": [
            {"id": _id(x.id), "heading": x.heading, "body": x.body,
             "links": list(x.links or []), "sort": x.sort,
             "published_at": _iso(x.published_at)}
            for x in (sections or [])
        ],
    }


async def _page_sections(s: AsyncSession, tenant_id, page_id) -> list:
    return (await s.execute(select(IntranetPageSection).where(
        IntranetPageSection.tenant_id == tenant_id,
        IntranetPageSection.page_id == page_id,
    ).order_by(IntranetPageSection.sort))).scalars().all()


@router.get("/pages")
async def list_pages(p: ConsolePrincipal = Depends(require_console_access),
                     s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(IntranetPage).where(
        IntranetPage.tenant_id == p.user.tenant_id,
    ).order_by(IntranetPage.nav_group, IntranetPage.sort, IntranetPage.title))).scalars().all()
    audience: dict = {}
    for page_id, role_id in (await s.execute(select(
        IntranetPageRole.page_id, IntranetPageRole.role_id,
    ).where(IntranetPageRole.tenant_id == p.user.tenant_id))).all():
        audience.setdefault(page_id, []).append(role_id)

    # SECTIONS COME WITH THE LIST. They did not, and the authoring screen renders each page's
    # sections inline -- so adding one saved it and then showed nothing, because the list it
    # re-read had never carried them. One query for all of them rather than a fetch per page:
    # this screen shows every page a workspace has, and that would be an N+1 by design.
    sections: dict = {}
    if rows:
        for section in (await s.execute(select(IntranetPageSection).where(
            IntranetPageSection.tenant_id == p.user.tenant_id,
            IntranetPageSection.page_id.in_([r.id for r in rows]),
        ).order_by(IntranetPageSection.sort))).scalars().all():
            sections.setdefault(section.page_id, []).append(section)

    return _list([_page_out(r, sections.get(r.id), audience.get(r.id)) for r in rows])


@router.get("/pages/{page_id}")
async def get_page(page_id: uuid.UUID, p: ConsolePrincipal = Depends(require_console_access),
                   s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetPage, p.user.tenant_id, page_id)
    roles = (await s.execute(select(IntranetPageRole.role_id).where(
        IntranetPageRole.tenant_id == p.user.tenant_id,
        IntranetPageRole.page_id == page_id))).scalars().all()
    return _page_out(row, await _page_sections(s, p.user.tenant_id, page_id), roles)


@router.post("/pages")
async def create_page(body: dict = Body(...),
                      p: ConsolePrincipal = Depends(require_console_access),
                      s: AsyncSession = Depends(get_session)):
    body = _body(body)
    _unknown(body, {"key", "title", "subtitle", "nav_group", "sort", "active"})
    key = _page_key(body, required=True)
    clash = (await s.execute(select(IntranetPage.id).where(
        IntranetPage.tenant_id == p.user.tenant_id, IntranetPage.key == key))).first()
    if clash:
        _unprocessable("key", "A page already uses that address.")
    row = IntranetPage(
        tenant_id=p.user.tenant_id,
        key=key,
        title=_text(body, "title", required=True) or "",
        subtitle=_text(body, "subtitle", nullable=True),
        nav_group=_text(body, "nav_group", nullable=True) or "Workspace",
        sort=_int(body, "sort", default=await _count(s, IntranetPage, p.user.tenant_id),
                  min_value=0) or 0,
        active=bool(_bool(body, "active", True)),
    )
    s.add(row)
    await s.flush()
    pending = await _record_mutation(
        s, p, action="content.page.created", category="Content",
        summary=f"Created page {row.title}", target_type="page", target_id=row.id,
        entity_type="page", entity_id=row.id, change_kind="created")
    await s.commit()
    await s.refresh(row)
    return _with_pending(_page_out(row), pending)


@router.patch("/pages/{page_id}")
async def patch_page(page_id: uuid.UUID, body: dict = Body(...),
                     p: ConsolePrincipal = Depends(require_console_access),
                     s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetPage, p.user.tenant_id, page_id)
    body = _body(body)
    _unknown(body, {"key", "title", "subtitle", "nav_group", "sort", "active", "role_ids"})

    if "key" in body:
        key = _page_key(body, required=True)
        clash = (await s.execute(select(IntranetPage.id).where(
            IntranetPage.tenant_id == p.user.tenant_id, IntranetPage.key == key,
            IntranetPage.id != page_id))).first()
        if clash:
            _unprocessable("key", "A page already uses that address.")
        row.key = key
    if "title" in body:
        row.title = _text(body, "title", required=True) or row.title
    if "subtitle" in body:
        row.subtitle = _text(body, "subtitle", nullable=True)
    if "nav_group" in body:
        row.nav_group = _text(body, "nav_group", nullable=True) or "Workspace"
    if "sort" in body:
        row.sort = _int(body, "sort", min_value=0) or 0
    if "active" in body:
        row.active = bool(_bool(body, "active", True))
    if "role_ids" in body:
        role_ids = await _role_ids_from_body(s, p.user.tenant_id, body)
        await s.execute(sa_delete(IntranetPageRole).where(
            IntranetPageRole.tenant_id == p.user.tenant_id,
            IntranetPageRole.page_id == page_id))
        for role_id in role_ids:
            s.add(IntranetPageRole(tenant_id=p.user.tenant_id, page_id=page_id, role_id=role_id))

    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.page.updated", category="Content",
        summary=f"Updated page {row.title}", target_type="page", target_id=row.id,
        entity_type="page", entity_id=row.id)
    await s.commit()
    await s.refresh(row)
    return _with_pending(_page_out(row, await _page_sections(s, p.user.tenant_id, page_id)),
                         pending)


@router.delete("/pages/{page_id}")
async def delete_page(page_id: uuid.UUID, p: ConsolePrincipal = Depends(require_console_access),
                      s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetPage, p.user.tenant_id, page_id)
    title = row.title
    await s.delete(row)
    pending = await _record_mutation(
        s, p, action="content.page.deleted", category="Content",
        summary=f"Deleted page {title}", target_type="page", target_id=page_id,
        entity_type="page", entity_id=page_id, change_kind="deleted")
    await s.commit()
    return _with_pending({"deleted": True}, pending)


@router.post("/pages/{page_id}/sections")
async def create_page_section(page_id: uuid.UUID, body: dict = Body(...),
                              p: ConsolePrincipal = Depends(require_console_access),
                              s: AsyncSession = Depends(get_session)):
    page = await _one(s, IntranetPage, p.user.tenant_id, page_id)
    body = _body(body)
    _unknown(body, {"heading", "body", "links", "sort"})
    row = IntranetPageSection(
        tenant_id=p.user.tenant_id, page_id=page.id,
        heading=_text(body, "heading", nullable=True),
        body=_text(body, "body", nullable=True, max_len=8000),
        links=_page_links(body),
        sort=_int(body, "sort", default=len(await _page_sections(s, p.user.tenant_id, page_id)),
                  min_value=0) or 0,
    )
    s.add(row)
    page.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.page_section.created", category="Content",
        summary=f"Added a section to {page.title}", target_type="page", target_id=page.id,
        entity_type="page", entity_id=page.id)
    await s.commit()
    return _with_pending(_page_out(page, await _page_sections(s, p.user.tenant_id, page_id)),
                         pending)


@router.patch("/pages/{page_id}/sections/{section_id}")
async def patch_page_section(page_id: uuid.UUID, section_id: uuid.UUID, body: dict = Body(...),
                             p: ConsolePrincipal = Depends(require_console_access),
                             s: AsyncSession = Depends(get_session)):
    page = await _one(s, IntranetPage, p.user.tenant_id, page_id)
    row = await _one(s, IntranetPageSection, p.user.tenant_id, section_id)
    if row.page_id != page.id:
        raise HTTPException(404, "Not found.")
    body = _body(body)
    _unknown(body, {"heading", "body", "links", "sort"})
    if "heading" in body:
        row.heading = _text(body, "heading", nullable=True)
    if "body" in body:
        row.body = _text(body, "body", nullable=True, max_len=8000)
    if "links" in body:
        row.links = _page_links(body)
    if "sort" in body:
        row.sort = _int(body, "sort", min_value=0) or 0
    row.draft_dirty = True
    page.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.page_section.updated", category="Content",
        summary=f"Edited a section of {page.title}", target_type="page", target_id=page.id,
        entity_type="page", entity_id=page.id)
    await s.commit()
    return _with_pending(_page_out(page, await _page_sections(s, p.user.tenant_id, page_id)),
                         pending)


@router.delete("/pages/{page_id}/sections/{section_id}")
async def delete_page_section(page_id: uuid.UUID, section_id: uuid.UUID,
                              p: ConsolePrincipal = Depends(require_console_access),
                              s: AsyncSession = Depends(get_session)):
    page = await _one(s, IntranetPage, p.user.tenant_id, page_id)
    row = await _one(s, IntranetPageSection, p.user.tenant_id, section_id)
    if row.page_id != page.id:
        raise HTTPException(404, "Not found.")
    await s.delete(row)
    page.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.page_section.deleted", category="Content",
        summary=f"Removed a section from {page.title}", target_type="page", target_id=page.id,
        entity_type="page", entity_id=page.id, change_kind="deleted")
    await s.commit()
    return _with_pending(_page_out(page, await _page_sections(s, p.user.tenant_id, page_id)),
                         pending)


@router.get("/courses")
async def get_courses(p: ConsolePrincipal = Depends(require_console_access),
                      s: AsyncSession = Depends(get_session)):
    return await _courses_bundle(s, p.user.tenant_id)


@router.get("/courses/{course_id}")
async def get_course(course_id: uuid.UUID, p: ConsolePrincipal = Depends(require_console_access),
                     s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    roles = await _course_roles(s, p.user.tenant_id, [row.id])
    sections = await _sections_of(s, p.user.tenant_id, row.id)
    lessons = (await s.execute(select(IntranetLesson).where(
        IntranetLesson.tenant_id == p.user.tenant_id,
        IntranetLesson.course_id == row.id,
    ))).scalars().all()
    # Sorted by the SECTION's position, not by section_id -- see course_sections.order_lessons.
    lessons = course_sections.order_lessons(lessons, sections)
    # Handouts for the whole course in one query rather than per lesson. This is the read the
    # lesson editor loads, so without it every lesson shows "No attachments" whatever it has.
    handouts: dict = {}
    if lessons:
        for att in (await s.execute(
            select(IntranetLessonAttachment)
            .where(IntranetLessonAttachment.tenant_id == p.user.tenant_id,
                   IntranetLessonAttachment.lesson_id.in_([le.id for le in lessons]))
            .order_by(IntranetLessonAttachment.sort))).scalars().all():
            handouts.setdefault(att.lesson_id, []).append(att)
    per_section: dict = {}
    for le in lessons:
        bucket = per_section.setdefault(_id(le.section_id) or "", {"count": 0, "minutes": 0})
        bucket["count"] += 1
        bucket["minutes"] += course_sections.lesson_minutes(
            le.kind, le.duration_minutes, le.read_minutes)
    scheme = row.grouping_scheme or "none"
    return _course(row, roles,
                   {row.id: {"lesson_count": len(lessons),
                             "total_duration_minutes": course_sections.total_minutes(lessons)}},
                   [_lesson(le, handouts.get(le.id)) for le in lessons],
                   await _last_editor(s, p.user.tenant_id, row.id),
                   [_section(sec, scheme, i, per_section) for i, sec in enumerate(sections)])


@router.post("/courses")
async def create_course(body: dict = Body(...), p: ConsolePrincipal = Depends(require_console_access),
                        s: AsyncSession = Depends(get_session)):
    body = _body(body)
    _unknown(body, {"title", "category", "description", "state", "track_progress",
                    "required_for_onboarding", "issues_certificate", "sequential", "sort",
                    "grouping_scheme", "lock_sections"})
    row = IntranetCourse(
        tenant_id=p.user.tenant_id,
        title=_text(body, "title", required=True) or "",
        # NOT REQUIRED. It was, and that made the console's own "New course" button impossible to
        # use: it posts a placeholder title and an empty category, and got "Required." back every
        # time. Naming a category before you can start a course is exactly the friction that
        # button exists to remove -- and both the rail and the portal already render an empty one
        # as "Uncategorised", so nothing downstream ever needed it.
        category=_text(body, "category", nullable=True) or "",
        description=_text(body, "description", nullable=True),
        state=_enum(body, "state", COURSE_STATES, "Draft") or "Draft",
        track_progress=_bool(body, "track_progress", True),
        required_for_onboarding=_bool(body, "required_for_onboarding", False),
        issues_certificate=_bool(body, "issues_certificate", False),
        sequential=_bool(body, "sequential", False),
        grouping_scheme=_enum(body, "grouping_scheme", GROUPING_SCHEMES, "none") or "none",
        lock_sections=_bool(body, "lock_sections", False),
        sort=_int(body, "sort", default=await _count(s, IntranetCourse, p.user.tenant_id), min_value=0) or 0,
    )
    s.add(row)
    pending = await _record_mutation(
        s, p, action="content.course.created", category="Training",
        summary=f"Created course {row.title}", target_type="course", target_id=row.id,
        entity_type="course", entity_id=row.id, change_kind="created")
    # REFRESHED BEFORE SERIALISING. _record_mutation commits, `updated_at` carries an onupdate so
    # the ORM expires it, and the next attribute read would lazy-load outside the async greenlet --
    # a 500, not a warning. Same fix and same reason as patch_marketing_request; it only started
    # biting courses when the header began showing when they were last edited.
    await s.refresh(row)
    return _with_pending(_course(row), pending)


@router.patch("/courses/{course_id}")
async def patch_course(course_id: uuid.UUID, body: dict = Body(...),
                       p: ConsolePrincipal = Depends(require_console_access),
                       s: AsyncSession = Depends(get_session)):
    body = _body(body)
    _unknown(body, {"title", "category", "description", "state", "track_progress",
                    "required_for_onboarding", "issues_certificate", "sequential", "sort",
                    "grouping_scheme", "lock_sections"})
    row = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    if "title" in body:
        row.title = _text(body, "title", required=True) or row.title
    if "category" in body:
        # `or row.category` would be a second way to refuse a blank: clearing the field would
        # silently restore the old value. An empty category is a real answer, so it is stored.
        row.category = _text(body, "category", nullable=True) or ""
    if "description" in body:
        row.description = _text(body, "description", nullable=True)
    if "state" in body:
        row.state = _enum(body, "state", COURSE_STATES) or row.state
    if "grouping_scheme" in body:
        # Renames every section at once and moves nothing -- the labels are generated from this
        # plus each section's position, so there is nothing else to update.
        row.grouping_scheme = _enum(body, "grouping_scheme", GROUPING_SCHEMES) or "none"
    for field in ("track_progress", "required_for_onboarding", "issues_certificate", "sequential",
                  "lock_sections"):
        if field in body:
            setattr(row, field, bool(_bool(body, field)))
    if "sort" in body:
        row.sort = _int(body, "sort", min_value=0) or 0
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.course.updated", category="Training",
        summary=f"Updated course {row.title}", target_type="course", target_id=row.id,
        entity_type="course", entity_id=row.id)
    # REFRESHED BEFORE SERIALISING. _record_mutation commits, `updated_at` carries an onupdate so
    # the ORM expires it, and the next attribute read would lazy-load outside the async greenlet --
    # a 500, not a warning. Same fix and same reason as patch_marketing_request; it only started
    # biting courses when the header began showing when they were last edited.
    await s.refresh(row)
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
    # REFRESHED BEFORE SERIALISING. _record_mutation commits, `updated_at` carries an onupdate so
    # the ORM expires it, and the next attribute read would lazy-load outside the async greenlet --
    # a 500, not a warning. Same fix and same reason as patch_marketing_request; it only started
    # biting courses when the header began showing when they were last edited.
    await s.refresh(row)
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


# ── sections ──────────────────────────────────────────────────────────────────────────────
# A course is a list of lessons until an author says otherwise. Sections are opt-in per course:
# every course starts with none, and the portal renders a flat list for those exactly as before.


async def _sections_of(s: AsyncSession, tenant_id, course_id) -> list[IntranetCourseSection]:
    return list((await s.execute(select(IntranetCourseSection).where(
        IntranetCourseSection.tenant_id == tenant_id,
        IntranetCourseSection.course_id == course_id,
    ).order_by(IntranetCourseSection.sort, IntranetCourseSection.name))).scalars().all())


def _section_rules(body: dict, row: IntranetCourseSection) -> None:
    """Apply the release and due rules, clearing the fields the chosen rule does not use.

    Clearing matters: an author who sets "Day 3", changes their mind and picks "Immediate" leaves
    `release_day = 3` behind, and the next author to pick "Day N" finds a 3 they never typed.
    """
    if "release_rule" in body:
        row.release_rule = _enum(body, "release_rule", SECTION_RELEASE_RULES) or "immediate"
    if "release_day" in body:
        row.release_day = _int(body, "release_day", min_value=1, max_value=365)
    if "release_date" in body:
        row.release_date = _date_value(body, "release_date")
    if "due_rule" in body:
        row.due_rule = _enum(body, "due_rule", SECTION_DUE_RULES) or "none"
    if "due_day" in body:
        row.due_day = _int(body, "due_day", min_value=1, max_value=365)

    if row.release_rule != "day_n":
        row.release_day = None
    if row.release_rule != "fixed_date":
        row.release_date = None
    if row.due_rule not in ("end_of_day_n", "end_of_week_n"):
        row.due_day = None
    # A rule that needs a number and has none is not a rule. Falling back to 'immediate' is the
    # honest reading of "Day <blank>", and the alternative is a section that never opens.
    if row.release_rule == "day_n" and not row.release_day:
        row.release_rule = "immediate"
    if row.release_rule == "fixed_date" and not row.release_date:
        row.release_rule = "immediate"
    if row.due_rule in ("end_of_day_n", "end_of_week_n") and not row.due_day:
        row.due_rule = "none"


@router.post("/courses/{course_id}/sections")
async def create_section(course_id: uuid.UUID, body: dict = Body(...),
                         p: ConsolePrincipal = Depends(require_console_access),
                         s: AsyncSession = Depends(get_session)):
    course = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    body = _body(body)
    _unknown(body, {"name", "summary", "release_rule", "release_day", "release_date",
                    "due_rule", "due_day", "sort", "adopt_lessons"})
    existing = await _sections_of(s, p.user.tenant_id, course_id)
    row = IntranetCourseSection(
        tenant_id=p.user.tenant_id,
        course_id=course.id,
        # Not required. The label carries the identity for every scheme but 'custom', and making
        # an author name "Day 1" before they can create it is the friction the New-course button
        # already had removed once.
        name=_text(body, "name", nullable=True, max_len=200) or "",
        summary=_text(body, "summary", nullable=True, max_len=2000),
        sort=_int(body, "sort", default=len(existing), min_value=0) or 0,
    )
    _section_rules(body, row)
    s.add(row)
    await s.flush()

    # THE FIRST SECTION ADOPTS THE COURSE'S EXISTING LESSONS (spec 6.4). Otherwise adding one to a
    # course that already has lessons produces an empty section above an unlabelled pile, and the
    # author has to drag every lesson into the thing they just made.
    if not existing and _bool(body, "adopt_lessons", True):
        for lesson in (await s.execute(select(IntranetLesson).where(
                IntranetLesson.tenant_id == p.user.tenant_id,
                IntranetLesson.course_id == course.id))).scalars().all():
            lesson.section_id = row.id
            lesson.draft_dirty = True

    course.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.section.created", category="Training",
        summary=f"Added a section to {course.title}", target_type="course",
        target_id=course.id, entity_type="course_section", entity_id=row.id,
        change_kind="created")
    # REFRESHED BEFORE SERIALISING, for the same reason every other Training handler does it:
    # _record_mutation commits, `updated_at` carries an onupdate so the ORM expires it, and the
    # next attribute read would lazy-load outside the async greenlet -- a 500, not a warning.
    await s.refresh(row)
    return _with_pending(_section(row, course.grouping_scheme, len(existing)), pending)


@router.patch("/courses/{course_id}/sections/{section_id}")
async def patch_section(course_id: uuid.UUID, section_id: uuid.UUID, body: dict = Body(...),
                        p: ConsolePrincipal = Depends(require_console_access),
                        s: AsyncSession = Depends(get_session)):
    course = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    row = await _one(s, IntranetCourseSection, p.user.tenant_id, section_id)
    if row.course_id != course_id:
        raise HTTPException(404, "Not found.")
    body = _body(body)
    _unknown(body, {"name", "summary", "release_rule", "release_day", "release_date",
                    "due_rule", "due_day", "sort"})
    if "name" in body:
        row.name = _text(body, "name", nullable=True, max_len=200) or ""
    if "summary" in body:
        row.summary = _text(body, "summary", nullable=True, max_len=2000)
    if "sort" in body:
        row.sort = _int(body, "sort", min_value=0) or 0
    _section_rules(body, row)
    row.draft_dirty = True
    course.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.section.updated", category="Training",
        summary=f"Updated a section of {course.title}", target_type="course",
        target_id=course.id, entity_type="course_section", entity_id=row.id)
    await s.refresh(row)
    siblings = await _sections_of(s, p.user.tenant_id, course_id)
    index = next((i for i, x in enumerate(siblings) if x.id == row.id), 0)
    return _with_pending(_section(row, course.grouping_scheme, index), pending)


@router.delete("/courses/{course_id}/sections/{section_id}")
async def delete_section(course_id: uuid.UUID, section_id: uuid.UUID,
                         p: ConsolePrincipal = Depends(require_console_access),
                         s: AsyncSession = Depends(get_session)):
    """Remove a section and keep every lesson that was in it.

    The lessons are REASSIGNED here, before the delete, to the preceding section -- or the
    following one if this was the first. `ON DELETE SET NULL` is the backstop, not the mechanism:
    relying on it would drop the lessons out of every section into the unlabelled group at the
    top of the course, which reads as data loss to the person who pressed the button.
    """
    course = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    row = await _one(s, IntranetCourseSection, p.user.tenant_id, section_id)
    if row.course_id != course_id:
        raise HTTPException(404, "Not found.")

    # POSITION READ OFF THE ORDERED LIST ITSELF, not recomputed from `sort`. Two sections can
    # share a sort value -- nothing stops it, and a reorder that half-applied would leave one --
    # and `_sections_of` breaks that tie on `name`. Recomputing with a different tie-break picked
    # a different neighbour, so the lessons went to the wrong section on exactly the rows where
    # it is hardest to notice.
    ordered = await _sections_of(s, p.user.tenant_id, course_id)
    index = next((i for i, x in enumerate(ordered) if x.id == row.id), 0)
    siblings = [x for x in ordered if x.id != row.id]
    out = _section(row, course.grouping_scheme, index)
    # The one before it, or the one after if this was the first. Never None while a sibling
    # exists: a lesson dropped to no section reappears above the first card, which reads as loss.
    target = siblings[index - 1] if index else (siblings[0] if siblings else None)
    for lesson in (await s.execute(select(IntranetLesson).where(
            IntranetLesson.tenant_id == p.user.tenant_id,
            IntranetLesson.section_id == row.id))).scalars().all():
        lesson.section_id = target.id if target else None
        lesson.draft_dirty = True

    await s.delete(row)
    for order, sibling in enumerate(siblings):
        sibling.sort = order
        sibling.draft_dirty = True
    course.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.section.deleted", category="Training",
        summary=f"Removed a section from {course.title}", target_type="course",
        target_id=course.id, entity_type="course_section", entity_id=section_id,
        change_kind="deleted")
    return _with_pending(out, pending)


@router.put("/courses/{course_id}/sections/order")
async def put_section_order(course_id: uuid.UUID, body: dict = Body(...),
                            p: ConsolePrincipal = Depends(require_console_access),
                            s: AsyncSession = Depends(get_session)):
    course = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    ids = _body(body).get("ids")
    if not isinstance(ids, list):
        _unprocessable("ids", "Expected a full ordered id list.")
    by_id = {str(x.id): x for x in await _sections_of(s, p.user.tenant_id, course_id)}
    if set(map(str, ids)) != set(by_id):
        _unprocessable("ids", "Must include every section exactly once.")
    for sort, section_id in enumerate(ids):
        by_id[str(section_id)].sort = sort
        by_id[str(section_id)].draft_dirty = True
    course.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.section.reordered", category="Training",
        summary=f"Reordered the sections of {course.title}", target_type="course",
        target_id=course.id, entity_type="course_section", entity_id=course.id)
    return _with_pending(await get_course(course_id, p, s), pending)


@router.put("/courses/{course_id}/lessons/order")
async def put_lesson_order(course_id: uuid.UUID, body: dict = Body(...),
                           p: ConsolePrincipal = Depends(require_console_access),
                           s: AsyncSession = Depends(get_session)):
    """Reorder lessons, and move them between sections, in one request.

    TWO ACCEPTED SHAPES. `ids` is the original flat list and still means what it meant. `lessons`
    is `[{id, section_id, sort}]` and is what a drag between two sections sends -- one request,
    because a move is a single act and splitting it into a reorder plus a reassignment leaves a
    window where the lesson is in the new section at the old position.
    """
    course = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    body = _body(body)
    ids, moves = body.get("ids"), body.get("lessons")
    if not isinstance(ids, list) and not isinstance(moves, list):
        _unprocessable("ids", "Expected a full ordered id list.")
    lessons = (await s.execute(select(IntranetLesson).where(
        IntranetLesson.tenant_id == p.user.tenant_id,
        IntranetLesson.course_id == course_id,
    ))).scalars().all()
    by_id = {str(le.id): le for le in lessons}
    section_ids = {str(x.id) for x in await _sections_of(s, p.user.tenant_id, course_id)}

    if isinstance(moves, list):
        if not all(isinstance(m, dict) for m in moves):
            _unprocessable("lessons", "Expected objects of {id, section_id, sort}.")
        if {str(m.get("id")) for m in moves} != set(by_id):
            _unprocessable("lessons", "Must include every lesson exactly once.")
        for move in moves:
            lesson = by_id[str(move.get("id"))]
            target = move.get("section_id")
            target = str(target) if target else None
            if target and target not in section_ids:
                # A section from another course would take the lesson out of this one entirely.
                _unprocessable("section_id", "That section is not part of this course.")
            lesson.section_id = uuid.UUID(target) if target else None
            lesson.sort = _int(move, "sort", default=lesson.sort, min_value=0) or 0
            lesson.draft_dirty = True
    else:
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


# ── lesson handouts ───────────────────────────────────────────────────────────────────────
# A file somebody uploads, or a link into a drive the team already keeps. One collection, because
# to the person reading the lesson they are simply the attachments.


async def _lesson_of(s: AsyncSession, tenant_id, course_id, lesson_id) -> IntranetLesson:
    row = await _one(s, IntranetLesson, tenant_id, lesson_id)
    if row.course_id != course_id:
        raise HTTPException(404, "Not found.")
    return row


async def _lesson_attachments(s: AsyncSession, tenant_id, lesson_id) -> list:
    return (await s.execute(
        select(IntranetLessonAttachment)
        .where(IntranetLessonAttachment.tenant_id == tenant_id,
               IntranetLessonAttachment.lesson_id == lesson_id)
        .order_by(IntranetLessonAttachment.sort))).scalars().all()


@router.post("/courses/{course_id}/lessons/{lesson_id}/attachments")
async def add_lesson_attachment(course_id: uuid.UUID, lesson_id: uuid.UUID,
                                title: str = Form(...),
                                kind: str = Form("file"),
                                note: str | None = Form(None),
                                url: str | None = Form(None),
                                file: UploadFile | None = File(None),
                                p: ConsolePrincipal = Depends(require_console_access),
                                s: AsyncSession = Depends(get_session)):
    """Attach a handout to a lesson.

    MULTIPART FOR BOTH KINDS, even though a link carries no bytes. One endpoint and one form
    shape means the console has a single "add attachment" control with a file/link toggle,
    instead of two controls whose difference the admin has to understand before they can use
    either.

    A link is stored as the author typed it (normalised to https), and only a FILE goes through
    binder_storage. The content type is what the server sniffs, never what the browser declared:
    that label is what a later download echoes back, and a mislabelled HTML file served as a PDF
    is the shape stored XSS takes.
    """
    course = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    lesson = await _lesson_of(s, p.user.tenant_id, course_id, lesson_id)
    kind = (kind or "file").strip().lower()
    if kind not in ("file", "link"):
        _unprocessable("kind", "Expected file or link.")
    title = (title or "").strip()[:200]
    if not title:
        _unprocessable("title", "A title is required.")

    row = IntranetLessonAttachment(
        tenant_id=p.user.tenant_id, lesson_id=lesson.id, title=title, kind=kind,
        note=(note or "").strip()[:100] or None,
        sort=len(await _lesson_attachments(s, p.user.tenant_id, lesson.id)))

    if kind == "link":
        row.url = _https_url({"url": (url or "").strip()}, "url", required=True)
    else:
        if file is None or not (file.filename or "").strip():
            _unprocessable("file", "Choose a file to upload.")
        data = await file.read()
        if not data:
            _unprocessable("file", "That file is empty.")
        if len(data) > uploads.MAX_ATTACHMENT_BYTES:
            _unprocessable(
                "file", f"Larger than {uploads.MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB.")
        sniffed = uploads.sniff_attachment(data)
        if sniffed is None:
            _unprocessable("file", "Handouts must be a PDF, PNG, JPEG or WebP.")
        stem = binder_storage.safe_filename(file.filename or "handout").rsplit(".", 1)[0]
        name = stem + uploads.ATTACHMENT_TYPES[sniffed][1]
        row.id = uuid.uuid4()
        key = (f"intranet/{p.user.tenant_id}/lessons/{lesson.id}/{row.id}-{name}")
        binder_storage.put(key, data, sniffed)
        row.storage_key, row.filename = key, name
        row.content_type, row.byte_size = sniffed, len(data)

    s.add(row)
    lesson.draft_dirty = True
    course.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.lesson.attachment_added", category="Training",
        summary=f"Added {title} to {lesson.title}", target_type="lesson_attachment",
        target_id=row.id, entity_type="lesson_attachment", entity_id=row.id,
        change_kind="created")
    return _with_pending(_lesson_attachment(row), pending)


@router.get("/lessons/{lesson_id}/images/{name}")
async def read_lesson_image(lesson_id: uuid.UUID, name: str,
                            p: ConsolePrincipal = Depends(require_console_access),
                            s: AsyncSession = Depends(get_session)):
    """The same image, for the person editing the lesson.

    A SECOND ROUTE RATHER THAN REUSING THE PORTAL'S, because the two authenticate differently: the
    portal route requires a member row with training_library access, and an owner who administers
    the workspace without being in its directory has neither. They would edit an article whose
    images were all broken.

    No course lookup: the lesson is enough to establish the tenant, and `require_console_access`
    has already established that this caller administers it.
    """
    row = (await s.execute(select(IntranetLesson).where(
        IntranetLesson.tenant_id == p.user.tenant_id,
        IntranetLesson.id == lesson_id))).scalars().first()
    if row is None:
        raise HTTPException(404, "Not found.")
    safe = binder_storage.safe_filename(name or "")
    if not safe or safe != (name or "").strip():
        raise HTTPException(404, "Not found.")
    key = f"intranet/{p.user.tenant_id}/lessons/{lesson_id}/{safe}"
    if not binder_storage.exists(key):
        raise HTTPException(404, "Not found.")
    data = binder_storage.read(key)
    return Response(
        content=data,
        media_type=uploads.sniff_image(data) or "application/octet-stream",
        headers={"Content-Disposition": 'inline; filename="' + safe + '"',
                 "X-Content-Type-Options": "nosniff",
                 "Cache-Control": "private, max-age=300"},
    )


@router.post("/courses/{course_id}/lessons/{lesson_id}/images")
async def add_lesson_image(course_id: uuid.UUID, lesson_id: uuid.UUID,
                           alt: str = Form(...),
                           file: UploadFile = File(...),
                           p: ConsolePrincipal = Depends(require_console_access),
                           s: AsyncSession = Depends(get_session)):
    """Upload one image for a lesson body.

    THE STORAGE KEY GOES IN `src`, NOT A SIGNED URL. A body is stored and served for years; a
    signed URL expires, and the article would fill with broken images at whatever hour the
    signature ran out. Keys resolve to URLs at render, the same way attachments do -- and the
    sanitizer's tenant check is written against key shape, so a signed URL would not survive it.

    `alt` IS REQUIRED HERE AS WELL AS IN THE EDITOR. The editor blocks the insert, which is the
    courtesy; this is the rule. An image with no alt text is invisible to anybody using a screen
    reader, and the person who cannot see it is the person who cannot report it missing.
    """
    course = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    lesson = await _lesson_of(s, p.user.tenant_id, course_id, lesson_id)
    alt = (alt or "").strip()[:300]
    if not alt:
        _unprocessable("alt", "Describe the image for anyone who cannot see it.")

    data = await file.read()
    if not data:
        _unprocessable("file", "That file is empty.")
    if len(data) > uploads.MAX_IMAGE_BYTES:
        _unprocessable("file", f"Larger than {uploads.MAX_IMAGE_BYTES // (1024 * 1024)} MB.")
    sniffed = uploads.sniff_image(data)
    if sniffed is None:
        _unprocessable("file", "Images must be a PNG, JPEG, WebP or GIF.")

    stem = binder_storage.safe_filename(file.filename or "image").rsplit(".", 1)[0]
    name = f"{uuid.uuid4()}-{stem}{uploads.IMAGE_TYPES[sniffed][1]}"
    key = f"intranet/{p.user.tenant_id}/lessons/{lesson.id}/{name}"
    binder_storage.put(key, data, sniffed)
    size = uploads.image_size(data)

    lesson.draft_dirty = True
    course.draft_dirty = True
    await _record_mutation(
        s, p, action="content.lesson.image_added", category="Training",
        summary=f"Added an image to {lesson.title}", target_type="lesson",
        target_id=lesson.id, entity_type="lesson", entity_id=lesson.id)
    return {
        "storage_key": key,
        # WHAT GOES IN THE EDITOR'S <img src>, built here rather than in the browser. The client
        # used to assemble a URL of its own from the key and got a route that does not exist --
        # so the image 404'd in the editor and was then stripped on save, because that shape is
        # neither an owned key nor a URL the sanitizer recognises. One spelling, and the server
        # owns it; `lesson_richtext` folds it back to the key on the way in.
        "url": lesson_richtext.CONSOLE_IMAGE_ROUTE.format(lesson=lesson.id, name=name),
        "alt": alt,
        "content_type": sniffed, "byte_size": len(data),
        "width": size[0] if size else None, "height": size[1] if size else None,
    }


@router.delete("/courses/{course_id}/lessons/{lesson_id}/attachments/{attachment_id}",
               status_code=204)
async def delete_lesson_attachment(course_id: uuid.UUID, lesson_id: uuid.UUID,
                                   attachment_id: uuid.UUID,
                                   p: ConsolePrincipal = Depends(require_console_access),
                                   s: AsyncSession = Depends(get_session)):
    course = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    lesson = await _lesson_of(s, p.user.tenant_id, course_id, lesson_id)
    row = await _one(s, IntranetLessonAttachment, p.user.tenant_id, attachment_id)
    if row.lesson_id != lesson.id:
        raise HTTPException(404, "Not found.")
    title = row.title
    # The row goes; the bytes stay. binder_storage has no delete and inventing one here would be
    # the first place in this product that destroys an upload -- a wrong click should not be
    # unrecoverable, and orphaned objects cost pennies.
    await s.delete(row)
    lesson.draft_dirty = True
    course.draft_dirty = True
    await _record_mutation(
        s, p, action="content.lesson.attachment_removed", category="Training",
        summary=f"Removed {title} from {lesson.title}", target_type="lesson_attachment",
        target_id=attachment_id, entity_type="lesson_attachment", entity_id=attachment_id,
        change_kind="deleted")
    await s.commit()
    return Response(status_code=204)


async def _apply_lesson_section(s: AsyncSession, tenant_id, course_id, body: dict,
                                row: IntranetLesson) -> None:
    """Put the lesson in a section, having checked the section is one of this course's.

    Unchecked, a section id from another course would move the lesson out of the course it is
    filed under -- it would vanish from the builder and reappear in somebody else's.
    """
    if "section_id" not in body:
        return
    raw = body.get("section_id")
    if raw in (None, ""):
        row.section_id = None
        return
    target = _uuid_value(body, "section_id", nullable=True)
    owned = {x.id for x in await _sections_of(s, tenant_id, course_id)}
    if target not in owned:
        _unprocessable("section_id", "That section is not part of this course.")
    row.section_id = target


def _apply_lesson_body(body: dict, row: IntranetLesson, tenant_id) -> None:
    """Sanitize an authored body and derive its numbers.

    SANITIZED HERE, not in the router body, so the tests can reach the rule directly and so the
    create and patch paths cannot drift into two different allowlists.

    `read_minutes` is derived from the body but stays author-overridable: the estimate is a
    reading pace, and an author who knows their audience beats a constant. The override only
    sticks because it is applied after -- an explicit value in the same request wins.
    """
    if "body_html" in body:
        raw = body.get("body_html")
        clean = lesson_richtext.sanitize(raw if isinstance(raw, str) else "", tenant_id=tenant_id)
        row.body_html = clean or None
        row.word_count = lesson_richtext.word_count(clean) or None
        row.read_minutes = lesson_richtext.read_minutes(clean)
    if "read_minutes" in body:
        # A CLEARED BOX MEANS "USE THE DERIVED VALUE", NOT "ERASE IT". The console posts every
        # field whenever any one of them changes, and the Read box shows the derived number only
        # as a placeholder -- so renaming a reading lesson sent read_minutes=null and silently
        # wiped the reading time the article had just earned. Recomputed from the body rather
        # than merely left alone, so clearing an override genuinely returns to derived.
        override = _int(body, "read_minutes", min_value=0)
        row.read_minutes = override or lesson_richtext.read_minutes(row.body_html)


@router.post("/courses/{course_id}/lessons")
async def create_lesson(course_id: uuid.UUID, body: dict = Body(...),
                        p: ConsolePrincipal = Depends(require_console_access),
                        s: AsyncSession = Depends(get_session)):
    course = await _one(s, IntranetCourse, p.user.tenant_id, course_id)
    body = _body(body)
    _unknown(body, {"title", "source_type", "source_ref", "source_label", "description",
                    "taught_by", "duration_minutes", "required", "sort",
                    "section_id", "kind", "body_html", "read_minutes", "page_count"})
    row = IntranetLesson(
        tenant_id=p.user.tenant_id,
        course_id=course.id,
        title=_text(body, "title", required=True) or "",
        source_type=_enum(body, "source_type", LESSON_SOURCE_TYPES, "PLACE") or "PLACE",
        source_ref=_text(body, "source_ref", nullable=True),
        source_label=_text(body, "source_label", nullable=True),
        description=_text(body, "description", nullable=True, max_len=4000),
        taught_by=_text(body, "taught_by", nullable=True, max_len=200),
        duration_minutes=_int(body, "duration_minutes", min_value=0),
        kind=_enum(body, "kind", LESSON_KINDS, "video") or "video",
        page_count=_int(body, "page_count", min_value=0),
        required=_bool(body, "required", False),
        sort=_int(body, "sort", default=await _count(s, IntranetLesson, p.user.tenant_id,
                                                     IntranetLesson.course_id == course_id), min_value=0) or 0,
    )
    await _apply_lesson_section(s, p.user.tenant_id, course_id, body, row)
    # A NEW LESSON IN A SECTIONED COURSE LANDS IN THE LAST SECTION, not in the unlabelled group
    # above every card. "Add lesson" on a Day 5 course means Day 5; anything else makes the author
    # drag it there every time.
    if row.section_id is None and "section_id" not in body:
        sections = await _sections_of(s, p.user.tenant_id, course_id)
        if sections:
            row.section_id = sections[-1].id
    _apply_lesson_body(body, row, p.user.tenant_id)
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
    row = await _lesson_of(s, p.user.tenant_id, course_id, lesson_id)
    body = _body(body)
    _unknown(body, {"title", "source_type", "source_ref", "source_label", "description",
                    "taught_by", "duration_minutes", "required", "sort",
                    "section_id", "kind", "body_html", "read_minutes", "page_count"})
    if "title" in body:
        row.title = _text(body, "title", required=True) or row.title
    if "source_type" in body:
        row.source_type = _enum(body, "source_type", LESSON_SOURCE_TYPES) or row.source_type
    if "source_ref" in body:
        row.source_ref = _text(body, "source_ref", nullable=True)
    if "source_label" in body:
        row.source_label = _text(body, "source_label", nullable=True)
    if "description" in body:
        row.description = _text(body, "description", nullable=True, max_len=4000)
    if "taught_by" in body:
        row.taught_by = _text(body, "taught_by", nullable=True, max_len=200)
    if "duration_minutes" in body:
        row.duration_minutes = _int(body, "duration_minutes", min_value=0)
    if "kind" in body:
        # SWITCHING KIND CLEARS NOTHING. An author who flips Reading -> Video to check something
        # and flips back must find their article still there; purging on switch would destroy a
        # document to answer a question. Only a hard delete removes a body.
        row.kind = _enum(body, "kind", LESSON_KINDS) or row.kind
    if "page_count" in body:
        row.page_count = _int(body, "page_count", min_value=0)
    if "required" in body:
        row.required = bool(_bool(body, "required"))
    if "sort" in body:
        row.sort = _int(body, "sort", min_value=0) or 0
    await _apply_lesson_section(s, p.user.tenant_id, course_id, body, row)
    _apply_lesson_body(body, row, p.user.tenant_id)
    row.draft_dirty = True
    course.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.lesson.updated", category="Training",
        summary=f"Updated lesson {row.title}", target_type="lesson", target_id=row.id,
        entity_type="lesson", entity_id=row.id)
    return _with_pending(
        _lesson(row, await _lesson_attachments(s, p.user.tenant_id, row.id)), pending)


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


def _apply_sop_fields(row: IntranetSop, body: dict) -> None:
    """The procedure's own fields (services/sop_library), checked one at a time so a refusal
    names the box it came from."""
    for field in sorted(sop_library.PROFILE_FIELDS & set(body)):
        try:
            setattr(row, field, sop_library.clean_field(field, body[field]))
        except sop_library.SopError as e:
            _unprocessable(e.field, e.message)
        if field == "tool_ids":
            flag_modified(row, field)


@router.get("/sops")
async def get_sops(include_archived: bool = Query(False),
                   p: ConsolePrincipal = Depends(require_console_access),
                   s: AsyncSession = Depends(get_session)):
    out = await _sops_bundle(s, p.user.tenant_id, include_archived=include_archived)
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
    # Was a literal 0: the console asked how many people had read a procedure and was always
    # told nobody, while the listing beside it counted properly.
    acknowledged = await _count(s, IntranetSopAcknowledgement, p.user.tenant_id,
                                IntranetSopAcknowledgement.sop_version_id == row.current_version_id
                                ) if row.current_version_id else 0
    waiting = await _count(s, IntranetSopSuggestion, p.user.tenant_id,
                           IntranetSopSuggestion.sop_id == row.id,
                           IntranetSopSuggestion.status != "Done")
    return _sop(row, cat, owner, version, version_count, int(acknowledged), int(waiting))


@router.post("/sops")
async def create_sop(body: dict = Body(...), p: ConsolePrincipal = Depends(require_console_access),
                     s: AsyncSession = Depends(get_session)):
    body = _body(body)
    _unknown(body, {"title", "category_id", "owner_member_id", "state", "review_due_on",
                    "last_reviewed_on"} | sop_library.PROFILE_FIELDS)
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
        last_reviewed_on=_date_value(body, "last_reviewed_on"),
    )
    _apply_sop_fields(row, body)
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
    _unknown(body, {"title", "category_id", "owner_member_id", "state", "review_due_on",
                    "last_reviewed_on"} | sop_library.PROFILE_FIELDS)
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
    if "last_reviewed_on" in body:
        row.last_reviewed_on = _date_value(body, "last_reviewed_on")
    _apply_sop_fields(row, body)
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
    if not row.storage_key:
        raise HTTPException(404, "That revision is the written procedure; it has no document.")
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
async def upload_sop_version(sop_id: uuid.UUID, bg: BackgroundTasks,
                             version_label: str = Form(...),
                             file: UploadFile | None = File(None),
                             p: ConsolePrincipal = Depends(require_console_access),
                             s: AsyncSession = Depends(get_session)):
    """A new revision of this procedure: a document, or -- with no file -- the written procedure
    as it now stands.

    A REVISION WITHOUT A FILE is what a written procedure needs. Acknowledgements hang off the
    version, so this is how a rewrite asks the team to read it again; the document ones work
    exactly as they did.

    THE BYTES ARE CHECKED. This was the one upload in the product that took a file on trust: no
    size limit, and the type stored was whatever the browser declared, replayed to members later.
    Handouts have been sniffed since they shipped (services/uploads); so is this now.
    """
    sop = await _one(s, IntranetSop, p.user.tenant_id, sop_id)
    label = (version_label or "").strip()
    if not label:
        _unprocessable("version_label", "Required.")
    duplicate = await _count(s, IntranetSopVersion, p.user.tenant_id,
                             IntranetSopVersion.sop_id == sop.id,
                             IntranetSopVersion.version_label == label)
    if duplicate:
        _unprocessable("version_label", "Version label already exists for this SOP.")

    version_id = uuid.uuid4()
    name = storage_key = content_type = None
    byte_size = None
    if file is not None and (file.filename or "").strip():
        data = await file.read()
        if not data:
            _unprocessable("file", "That file is empty.")
        if len(data) > uploads.MAX_DOCUMENT_BYTES:
            _unprocessable(
                "file", f"Larger than {uploads.MAX_DOCUMENT_BYTES // (1024 * 1024)} MB.")
        sniffed = uploads.sniff_document(data)
        if sniffed is None:
            _unprocessable("file", "A procedure document must be a PDF or a Word file.")
        content_type = sniffed
        stem = binder_storage.safe_filename(file.filename or "procedure").rsplit(".", 1)[0]
        name = stem + uploads.DOCUMENT_TYPES[sniffed]
        byte_size = len(data)
        storage_key = f"intranet/{p.user.tenant_id}/sops/{sop.id}/{version_id}-{name}"
        binder_storage.put(storage_key, data, content_type)
    elif not sop_library.has_body(sop.body):
        _unprocessable("file", "Attach a document, or write the procedure first.")

    row = IntranetSopVersion(
        id=version_id,
        tenant_id=p.user.tenant_id,
        sop_id=sop.id,
        version_label=label,
        filename=name,
        storage_key=storage_key,
        content_type=content_type,
        byte_size=byte_size,
        uploaded_by=p.member.id,
        uploaded_at=_now(),
    )
    s.add(row)
    await s.flush()
    sop.current_version_id = row.id
    sop.draft_dirty = True
    what = "Uploaded" if storage_key else "Revised"
    pending = await _record_mutation(
        s, p, action="content.sop.version_uploaded", category="SOPs",
        summary=f"{what} {label} for SOP {sop.title}", target_type="sop_version",
        target_id=row.id, entity_type="sop", entity_id=sop.id, change_kind="updated")
    await _announce_sop_revision(s, p, sop, label, bg)
    return _with_pending(_sop_version(row), pending)


async def _mail_each(recipients: list[tuple[str, str]], subject: str, text: str) -> None:
    """One email each, after the response has gone (see the caller). `recipients` is
    (address, idempotency key) so a double-clicked upload cannot send the same news twice."""
    html = "<p>" + text.replace("\n", "<br>") + "</p>"
    for to, key in recipients:
        await mailer.send(to, subject, html, text, idempotency_key=key)


async def _announce_sop_revision(s: AsyncSession, p: ConsolePrincipal, sop: IntranetSop,
                                 label: str, bg: BackgroundTasks) -> None:
    """A new revision of a REQUIRED procedure tells the team it is waiting (D7).

    Acknowledging is per revision, so a new one quietly makes everybody's assurance stale. For
    the procedures an admin marked required, saying nothing would mean the team is out of date
    on paper and nobody has been told. Only for a procedure members can actually open -- a Draft
    revised in private announces nothing -- and only to people with an address.

    SENT AFTER THE RESPONSE. A team of eighty-six is eighty-six calls to the mail provider, and
    an admin who pressed Upload should not be watching a spinner while they go out.
    """
    if not sop.required or sop.state != "Live" or sop.published_at is None:
        return
    members = (await s.execute(select(IntranetMember).where(
        IntranetMember.tenant_id == p.user.tenant_id,
        IntranetMember.status == "Active"))).scalars().all()
    recipients = [((m.email or "").strip(), f"sop-revision-{sop.id}-{label}-{m.id}")
                  for m in members if (m.email or "").strip()]
    if not recipients:
        return
    host = await primary_host(s, p.user.tenant_id)
    link = f"https://{host}/intranet/sops/{sop.id}"
    text = (f"{sop.title} has a new version ({label}).\n\n"
            f"It is required reading, so it is asking you to read it again: {link}")
    bg.add_task(_mail_each, recipients, f"New version of {sop.title}", text)


@router.put("/sops/{sop_id}/body")
async def put_sop_body(sop_id: uuid.UUID, body: dict = Body(...),
                       p: ConsolePrincipal = Depends(require_console_access),
                       s: AsyncSession = Depends(get_session)):
    """The written procedure, saved as a DRAFT (D2).

    Alone in this console the text waits for Publish: a title saved is a title live, but a
    procedure halfway through a rewrite is not the procedure the team should be following.
    `published_body` is what members read until then."""
    row = await _one(s, IntranetSop, p.user.tenant_id, sop_id)
    payload = _body(body)
    _unknown(payload, {"body"})
    try:
        clean = sop_library.clean_body(payload.get("body"))
    except sop_library.SopError as e:
        _unprocessable(e.field, e.message)
    row.body = clean
    flag_modified(row, "body")
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.sop.body_saved", category="SOPs",
        summary=f"Saved the procedure for {row.title}", target_type="sop", target_id=row.id,
        entity_type="sop", entity_id=row.id,
        detail={"steps": len((clean or {}).get("steps") or [])})
    return _with_pending(await get_sop(row.id, p, s), pending)


@router.post("/sops/{sop_id}/draft")
async def draft_sop_body(sop_id: uuid.UUID, body: dict = Body(default_factory=dict),
                         p: ConsolePrincipal = Depends(require_console_access),
                         s: AsyncSession = Depends(get_session)):
    """A first draft of the procedure, read from its document or from pasted text (phase 6).

    RETURNED, NOT SAVED. Whoever owns the procedure reads it, fixes it and presses save; nothing
    a model wrote becomes the team's procedure without somebody saying so.
    """
    sop = await _one(s, IntranetSop, p.user.tenant_id, sop_id)
    payload = _body(body or {})
    _unknown(payload, {"text"})
    if not sop_drafting.available():
        raise HTTPException(503, "The assistant is not configured for this platform.")

    text = (str(payload.get("text") or "")).strip()[:sop_drafting.MAX_CHARS]
    source = "pasted text"
    if not text:
        version = (await s.get(IntranetSopVersion, sop.current_version_id)
                   if sop.current_version_id else None)
        if version is None or not version.storage_key or not binder_storage.exists(version.storage_key):
            _unprocessable("text", "Attach a document first, or paste the procedure in.")
        text = sop_drafting.extract(binder_storage.read(version.storage_key), version.content_type)
        source = version.filename or "the document"
        if not text:
            _unprocessable(
                "file", "There are no words to read in that file -- a scan, most likely. "
                        "Paste the procedure in instead.")
    try:
        drafted = await sop_drafting.draft(sop.title, text)
    except sop_library.SopError as e:
        _unprocessable(e.field, e.message)
    except Exception as e:                      # noqa: BLE001 -- an outage is not a 500 for them
        raise HTTPException(503, f"The draft could not be written: {type(e).__name__}")
    await _record_mutation(
        s, p, action="content.sop.body_drafted", category="SOPs",
        summary=f"Drafted the procedure for {sop.title} from {source}", target_type="sop",
        target_id=sop.id, entity_type="sop", entity_id=sop.id, pending=False,
        detail={"steps": len(drafted.get("steps") or []), "source": source})
    return {"body": drafted, "source": source}


@router.post("/sops/{sop_id}/review")
async def review_sop(sop_id: uuid.UUID, body: dict = Body(default_factory=dict),
                     p: ConsolePrincipal = Depends(require_console_access),
                     s: AsyncSession = Depends(get_session)):
    """Somebody checked this procedure is still true today. Records the fact, and moves the next
    review on if a date is given -- the library had only the forward date, so nothing said
    whether the last review ever happened."""
    row = await _one(s, IntranetSop, p.user.tenant_id, sop_id)
    payload = _body(body or {})
    _unknown(payload, {"reviewed_on", "review_due_on"})
    row.last_reviewed_on = _date_value(payload, "reviewed_on") or dt.date.today()
    if "review_due_on" in payload:
        row.review_due_on = _date_value(payload, "review_due_on")
    if row.state == "Needs Review":
        row.state = "Live"
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.sop.reviewed", category="SOPs",
        summary=f"Reviewed SOP {row.title}", target_type="sop", target_id=row.id,
        entity_type="sop", entity_id=row.id)
    return _with_pending(await get_sop(row.id, p, s), pending)


@router.post("/sops/{sop_id}/restore")
async def restore_sop(sop_id: uuid.UUID, p: ConsolePrincipal = Depends(require_console_access),
                      s: AsyncSession = Depends(get_session)):
    """Un-archive. Archiving was a one-way door: nothing cleared `archived_at` and the listing
    hides archived rows, so a procedure archived by mistake was gone from the console for good.
    It comes back as a Draft, so putting it in front of the team again is a deliberate act."""
    row = await _one(s, IntranetSop, p.user.tenant_id, sop_id)
    if row.archived_at is None:
        _unprocessable("sop_id", "That procedure is not archived.")
    row.archived_at = None
    row.state = "Draft"
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.sop.state_changed", category="SOPs",
        summary=f"Restored SOP {row.title}", target_type="sop", target_id=row.id,
        entity_type="sop", entity_id=row.id, change_kind="updated")
    return _with_pending(await get_sop(row.id, p, s), pending)


@router.post("/sops/{sop_id}/versions/{version_id}/current")
async def set_current_sop_version(sop_id: uuid.UUID, version_id: uuid.UUID,
                                  p: ConsolePrincipal = Depends(require_console_access),
                                  s: AsyncSession = Depends(get_session)):
    """Put an earlier revision back in front of the team -- the way back from a bad upload, which
    the console did not have. Acknowledgements follow the version, so whoever acknowledged this
    one still has."""
    sop = await _one(s, IntranetSop, p.user.tenant_id, sop_id)
    version = await _one(s, IntranetSopVersion, p.user.tenant_id, version_id)
    if version.sop_id != sop.id:
        raise HTTPException(404, "Not found.")
    sop.current_version_id = version.id
    sop.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.sop.version_current", category="SOPs",
        summary=f"Made {version.version_label} current for SOP {sop.title}",
        target_type="sop_version", target_id=version.id, entity_type="sop", entity_id=sop.id,
        change_kind="updated")
    return _with_pending(await get_sop(sop.id, p, s), pending)


@router.get("/sops/{sop_id}/acknowledgements")
async def get_sop_acknowledgements(sop_id: uuid.UUID,
                                   p: ConsolePrincipal = Depends(require_console_access),
                                   s: AsyncSession = Depends(get_session)):
    """Who has read the current revision and who has not (D3). The count has been in the listing
    since the table shipped; the names were nowhere, and the names are the half an admin chasing
    a procedure actually needs."""
    sop = await _one(s, IntranetSop, p.user.tenant_id, sop_id)
    members = (await s.execute(select(IntranetMember).where(
        IntranetMember.tenant_id == p.user.tenant_id,
        IntranetMember.status != "Removed").order_by(IntranetMember.full_name))).scalars().all()
    acked: dict = {}
    version_label = None
    if sop.current_version_id:
        current = await s.get(IntranetSopVersion, sop.current_version_id)
        version_label = current.version_label if current else None
        acked = {row.member_id: row.acknowledged_at for row in (await s.execute(
            select(IntranetSopAcknowledgement).where(
                IntranetSopAcknowledgement.tenant_id == p.user.tenant_id,
                IntranetSopAcknowledgement.sop_version_id == sop.current_version_id))).scalars()}
    items = [{"member_id": _id(m.id), "name": m.full_name, "status": m.status,
              "acknowledged_at": _iso(acked.get(m.id))} for m in members]
    return {**_list(items, len(items)), "version": version_label,
            "acknowledged": sum(1 for i in items if i["acknowledged_at"])}


@router.get("/sops/{sop_id}/suggestions")
async def get_sop_suggestions(sop_id: uuid.UUID,
                              p: ConsolePrincipal = Depends(require_console_access),
                              s: AsyncSession = Depends(get_session)):
    """What the team has said about this procedure (D5)."""
    await _one(s, IntranetSop, p.user.tenant_id, sop_id)
    rows = (await s.execute(select(IntranetSopSuggestion).where(
        IntranetSopSuggestion.tenant_id == p.user.tenant_id,
        IntranetSopSuggestion.sop_id == sop_id).order_by(
            IntranetSopSuggestion.created_at.desc()))).scalars().all()
    names = {}
    member_ids = [r.member_id for r in rows if r.member_id]
    if member_ids:
        names = {m.id: m.full_name for m in (await s.execute(select(IntranetMember).where(
            IntranetMember.tenant_id == p.user.tenant_id,
            IntranetMember.id.in_(member_ids)))).scalars()}
    items = [{"id": _id(r.id), "text": r.text, "status": r.status,
              "from": names.get(r.member_id), "created_at": _iso(r.created_at),
              "resolution_note": r.resolution_note, "resolved_at": _iso(r.resolved_at)}
             for r in rows]
    return {**_list(items, len(items)),
            "open": sum(1 for i in items if i["status"] != "Done")}


@router.patch("/sop-suggestions/{suggestion_id}")
async def patch_sop_suggestion(suggestion_id: uuid.UUID, body: dict = Body(...),
                               p: ConsolePrincipal = Depends(require_console_access),
                               s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetSopSuggestion, p.user.tenant_id, suggestion_id)
    payload = _body(body)
    _unknown(payload, {"status", "resolution_note"})
    if "status" in payload:
        row.status = _enum(payload, "status", SOP_SUGGESTION_STATUSES) or row.status
        row.resolved_at = _now() if row.status == "Done" else None
    if "resolution_note" in payload:
        row.resolution_note = _text(payload, "resolution_note", nullable=True, max_len=500)
    sop = await _one(s, IntranetSop, p.user.tenant_id, row.sop_id)
    await _record_mutation(
        s, p, action="content.sop.suggestion_updated", category="SOPs",
        summary=f"{row.status} a suggestion on {sop.title}", target_type="sop_suggestion",
        target_id=row.id, entity_type="sop", entity_id=sop.id, pending=False)
    return {"item": {"id": _id(row.id), "status": row.status,
                     "resolution_note": row.resolution_note, "resolved_at": _iso(row.resolved_at)}}


@router.put("/sop-categories/order")
async def put_sop_category_order(body: dict = Body(...),
                                 p: ConsolePrincipal = Depends(require_console_access),
                                 s: AsyncSession = Depends(get_session)):
    """The order of the departments down the member's rail. `sort` existed and nothing could
    change it, so the rail kept whatever order somebody happened to create them in."""
    ids = _body(body).get("ids")
    if not isinstance(ids, list) or len(ids) > 60:
        _unprocessable("ids", "Expected the departments, in order.")
    try:
        wanted = [uuid.UUID(str(x)) for x in ids]
    except ValueError:
        _unprocessable("ids", "Expected department ids.")
    rows = {c.id: c for c in (await s.execute(select(IntranetSopCategory).where(
        IntranetSopCategory.tenant_id == p.user.tenant_id,
        IntranetSopCategory.id.in_(wanted)))).scalars().all()}
    if len(rows) != len(set(wanted)):
        _unprocessable("ids", "Not all of those are departments of this workspace.")
    for i, category_id in enumerate(wanted):
        rows[category_id].sort = i
        rows[category_id].draft_dirty = True
    await _record_mutation(
        s, p, action="content.sop_category.updated", category="SOPs",
        summary="Reordered the SOP departments", target_type="sop_category")
    return await get_sop_categories(p, s)


# ── CRM identity on the roster ─────────────────────────────────────────────────────────────

CRM_SOURCES = ("fub", "sisu")


async def _crm_matches(s: AsyncSession, tenant_id, members: list[IntranetMember]) -> dict:
    """{member id: {source: match or None}} for a page of the roster, in one read of the agents."""
    found = await member_identity.matches_for_many(s, tenant_id, members)
    out = {}
    for m in members:
        row = {}
        for source in CRM_SOURCES:
            hit = found.get(m.id, {}).get(source)
            row[source] = ({"agent_id": _id(hit[0].id), "external_id": hit[0].external_id,
                            "name": hit[0].name, "email": hit[0].email,
                            "active": bool(hit[0].is_active), "matched_by": hit[1]}
                           if hit else None)
        row["links"] = {k: v for k, v in (m.agent_links or {}).items() if k in CRM_SOURCES and v}
        out[m.id] = row
    return out


@router.get("/crm-agents")
async def get_crm_agents(source: str = Query(...), p: ConsolePrincipal = Depends(require_console_access),
                         s: AsyncSession = Depends(get_session)):
    """The users a member can be linked to, for the roster's picker: FUB users or Sisu agents."""
    if source not in CRM_SOURCES:
        _unprocessable("source", "Expected fub or sisu.")
    rows = (await s.execute(select(Agent).where(
        Agent.tenant_id == p.user.tenant_id, Agent.source == source).order_by(
            Agent.is_active.desc(), Agent.name))).scalars().all()
    return _list([{"external_id": r.external_id, "name": r.name, "email": r.email,
                   "active": bool(r.is_active)} for r in rows], len(rows))


async def _agent_links(s: AsyncSession, tenant_id, raw, current: dict | None) -> dict | None:
    """{source: CRM user id}, validated against the users this workspace's sync holds. A source
    set to null goes back to matching by email; `agent_links: null` clears them all."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        _unprocessable("agent_links", "Expected an object keyed by source.")
    links = dict(current or {})
    for source, value in raw.items():
        if source not in CRM_SOURCES:
            _unprocessable(f"agent_links.{source}", "Expected fub or sisu.")
        if value in (None, ""):
            links.pop(source, None)
            continue
        ext = str(value).strip()[:64]
        exists = (await s.execute(select(Agent.id).where(
            Agent.tenant_id == tenant_id, Agent.source == source,
            Agent.external_id == ext))).first()
        if exists is None:
            _unprocessable(f"agent_links.{source}", "No such user in this workspace's CRM.")
        links[source] = ext
    return links or None


# ── Needs You Today settings ─────────────────────────────────────────────────────────────

async def _follow_up_settings_out(s: AsyncSession, tenant: Tenant) -> dict:
    integ = await follow_ups.fub_integration(s, tenant.id)
    fstate = ((integ.config or {}).get("fub_state") or {}) if integ else {}
    return {
        "settings": follow_ups.settings_for(tenant),
        "defaults": follow_ups.DEFAULTS,
        "bounds": {k: {"min": lo, "max": hi} for k, (lo, hi) in follow_ups.BOUNDS.items()},
        "stages": await follow_ups.stages_in_use(s, tenant.id),
        # How many overdue tasks the window leaves out, across the account, from the last sync.
        "older_overdue": fstate.get("older_overdue"),
        "connection": follow_ups.connection(integ, show_error=True),
        "backfill": fstate.get("backfill"),
    }


@router.get("/follow-ups")
async def get_follow_up_settings(p: ConsolePrincipal = Depends(require_console_access),
                                 s: AsyncSession = Depends(get_session)):
    return await _follow_up_settings_out(s, p.tenant)


@router.patch("/follow-ups")
async def patch_follow_up_settings(body: dict = Body(...),
                                   p: ConsolePrincipal = Depends(require_console_access),
                                   s: AsyncSession = Depends(get_session)):
    body = _body(body)
    _unknown(body, set(follow_ups.DEFAULTS))
    tenant = await s.get(Tenant, p.user.tenant_id)
    current = follow_ups.settings_for(tenant)
    try:
        merged = follow_ups.clean_settings({**current, **body}, strict=True)
    except ValueError as e:
        field = str(e)
        lo_hi = follow_ups.BOUNDS.get(field)
        _unprocessable(field, f"Expected a whole number from {lo_hi[0]} to {lo_hi[1]}."
                       if lo_hi else "Expected a list of stage names.")
    cfg = dict(tenant.config or {})
    intranet = dict(cfg.get("intranet") or {})
    intranet["follow_ups"] = merged
    cfg["intranet"] = intranet
    tenant.config = cfg
    flag_modified(tenant, "config")
    await _record_mutation(
        s, p, action="config.follow_ups.updated", category="Win the Day",
        summary="Updated the Needs You Today rules", target_type="config",
        detail={"from": current, "to": merged}, pending=False)
    return await _follow_up_settings_out(s, tenant)


# ── Win the Day lists: creating one, and the smart lists to pick from ───────────────────────

LIST_FIELDS = {"name", "provider", "external_list_id", "script_name", "daily_target",
               "group_key", "block_key", "cadence", "kind", "description", "script_ids"}


async def _playbook_row(s: AsyncSession, tenant_id, *, create: bool = False
                        ) -> IntranetWtdPlaybook | None:
    row = (await s.execute(select(IntranetWtdPlaybook).where(
        IntranetWtdPlaybook.tenant_id == tenant_id))).scalars().first()
    if row is None and create:
        row = IntranetWtdPlaybook(tenant_id=tenant_id, content=wtd_playbook.empty_content(),
                                  draft_dirty=True)
        s.add(row)
        await s.flush()
    return row


async def _draft_content(s: AsyncSession, tenant_id) -> dict:
    """The playbook as the console is editing it (published or not), read tolerantly."""
    row = await _playbook_row(s, tenant_id)
    return wtd_playbook.current(row.content if row else None)


async def _apply_list_fields(s: AsyncSession, tenant_id, row: IntranetWtdList, body: dict) -> None:
    """The fields that place a list in the playbook. A group or block must be one the playbook
    has -- the console offers them as a choice, so anything else is a stale screen or a typo --
    and scripts must be this workspace's."""
    content = await _draft_content(s, tenant_id)
    for field, keys in (("group_key", {g["key"] for g in content["lists"]["groups"]}),
                        ("block_key", {b["key"] for b in content["run"]["blocks"]})):
        if field in body:
            value = _text(body, field, nullable=True, max_len=32) or None
            if value is not None and value not in keys:
                _unprocessable(field, "Not one of the playbook's "
                                      + ("groups." if field == "group_key" else "blocks."))
            setattr(row, field, value)
    if "cadence" in body:
        row.cadence = _text(body, "cadence", nullable=True, max_len=32) or None
    if "kind" in body:
        row.kind = _enum(body, "kind", wtd_playbook.KIND_KEYS) or row.kind
    if "description" in body:
        row.description = _text(body, "description", nullable=True, max_len=600) or None
    if "script_ids" in body:
        raw = body.get("script_ids")
        if raw is None:
            raw = []
        if not isinstance(raw, list) or len(raw) > 6:
            _unprocessable("script_ids", "Expected up to six script ids.")
        try:
            ids = list(dict.fromkeys(str(uuid.UUID(str(x))) for x in raw))
        except ValueError:
            _unprocessable("script_ids", "Expected script ids.")
        if ids:
            known = {str(x) for x in (await s.execute(select(IntranetWtdScript.id).where(
                IntranetWtdScript.tenant_id == tenant_id,
                IntranetWtdScript.id.in_([uuid.UUID(x) for x in ids])))).scalars()}
            if set(ids) - known:
                _unprocessable("script_ids", "Not one of this workspace's scripts.")
        row.script_ids = ids or None
        flag_modified(row, "script_ids")


@router.post("/wtd-lists")
async def create_wtd_list(body: dict = Body(...), p: ConsolePrincipal = Depends(require_console_access),
                          s: AsyncSession = Depends(get_session)):
    """A new call list. The console could edit and reorder lists but never make one, so a
    workspace with none -- every workspace but the first -- could never have any. It is a draft
    like every other change here, and goes live on publish."""
    body = _body(body)
    _unknown(body, LIST_FIELDS)
    name = _text(body, "name", required=True, max_len=120)
    last = (await s.execute(select(func.max(IntranetWtdList.position)).where(
        IntranetWtdList.tenant_id == p.user.tenant_id))).scalar()
    row = IntranetWtdList(
        tenant_id=p.user.tenant_id, position=int(last or 0) + 1, name=name,
        provider=_text(body, "provider", max_len=80) or "follow_up_boss",
        external_list_id=_text(body, "external_list_id", nullable=True, max_len=64),
        script_name=_text(body, "script_name", nullable=True, max_len=200),
        daily_target=_int(body, "daily_target", min_value=1, max_value=10000),
        kind="clear", active=True, draft_dirty=True)
    await _apply_list_fields(s, p.user.tenant_id, row, body)
    s.add(row)
    await s.flush()
    pending = await _record_mutation(
        s, p, action="content.wtd_list.created", category="Win the Day",
        summary=f"Added Win the Day list {name}", target_type="wtd_list", target_id=row.id,
        entity_type="wtd_list", entity_id=row.id, change_kind="created")
    return _with_pending(_wtd(row), pending)


@router.get("/fub-smart-lists")
async def get_fub_smart_lists(p: ConsolePrincipal = Depends(require_console_access),
                              s: AsyncSession = Depends(get_session)):
    """The account's smart lists, asked of Follow Up Boss with the dashboard's key, so a list is
    picked by name instead of by the number at the end of its address. Read live: an admin
    choosing, rarely, and a stale copy would offer lists that no longer exist."""
    items = await _fub_smart_lists(s, p.user.tenant_id)
    return _list(items, len(items))


async def _fub_smart_lists(s: AsyncSession, tenant_id) -> list[dict]:
    from ..services.sync import _fub_creds
    integ = await follow_ups.fub_integration(s, tenant_id)
    if integ is None or integ.status not in ("connected", "error"):
        raise HTTPException(409, "Follow Up Boss is not connected. Connect it on the Axcion "
                                 "dashboard under Settings, Integrations.")
    items = []
    try:
        async with fub.client(_fub_creds(integ)) as c:
            async for page, _ in fub.pages(c, "/smartLists", "smartlists", {"fub2": "true"},
                                           max_pages=5):
                items.extend({"id": row.get("id"), "name": row.get("name")} for row in page
                             if row.get("id") is not None)
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(502, f"Follow Up Boss did not answer: {str(e)[:160]}")
    items.sort(key=lambda row: str(row["name"] or "").lower())
    return items


@router.get("/wtd-lists")
async def get_wtd_lists(p: ConsolePrincipal = Depends(require_console_access),
                        s: AsyncSession = Depends(get_session)):
    # The provider rows a list links through, created on first read as the Integrations page does:
    # a workspace that never opened that page had none, so every list here read "Disconnected"
    # beside a Follow Up Boss connection that was working.
    await ensure_rows(s, p.user.tenant_id)
    rows = (await s.execute(select(IntranetWtdList).where(
        IntranetWtdList.tenant_id == p.user.tenant_id).order_by(IntranetWtdList.position))).scalars().all()
    out = _list([_wtd(r) for r in rows], await _count(s, IntranetWtdList, p.user.tenant_id))
    out["stats"] = await _wtd_stats(s, p.user.tenant_id)
    out["integrations"] = await _wtd_integrations(s, p.user.tenant_id, {r.provider for r in rows})
    await s.commit()
    return out


@router.patch("/wtd-lists/{list_id}")
async def patch_wtd_list(list_id: uuid.UUID, body: dict = Body(...),
                         p: ConsolePrincipal = Depends(require_console_access),
                         s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetWtdList, p.user.tenant_id, list_id)
    body = _body(body)
    _unknown(body, LIST_FIELDS | {"position", "active"})
    if "position" in body:
        row.position = _int(body, "position", min_value=0) or 0
    if "name" in body:
        row.name = _text(body, "name", required=True, max_len=120) or row.name
    if "provider" in body:
        row.provider = _text(body, "provider", required=True, max_len=80) or row.provider
    if "external_list_id" in body:
        row.external_list_id = _text(body, "external_list_id", nullable=True, max_len=64)
    if "script_name" in body:
        row.script_name = _text(body, "script_name", nullable=True, max_len=200)
    if "daily_target" in body:
        row.daily_target = _int(body, "daily_target", min_value=1)
    if "active" in body:
        row.active = bool(_bool(body, "active"))
    await _apply_list_fields(s, p.user.tenant_id, row, body)
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.wtd_list.updated", category="Win the Day",
        summary=f"Updated Win the Day list {row.position}", target_type="wtd_list",
        target_id=row.id, entity_type="wtd_list", entity_id=row.id)
    return _with_pending(_wtd(row), pending)


@router.delete("/wtd-lists/{list_id}")
async def delete_wtd_list(list_id: uuid.UUID, p: ConsolePrincipal = Depends(require_console_access),
                          s: AsyncSession = Depends(get_session)):
    """Gone, not hidden: turning a list off (`active`) is the way to take it out of the run for a
    while. The rest close up so the numbers agents see stay 01, 02, 03."""
    row = await _one(s, IntranetWtdList, p.user.tenant_id, list_id)
    name = row.name
    await s.delete(row)
    await s.flush()
    await _renumber_lists(s, p.user.tenant_id)
    pending = await _record_mutation(
        s, p, action="content.wtd_list.deleted", category="Win the Day",
        summary=f"Removed Win the Day list {name}", target_type="wtd_list", target_id=list_id,
        entity_type="wtd_list", change_kind="deleted")
    return {"deleted": True, "pending_changes": pending}


async def _renumber_lists(s: AsyncSession, tenant_id, order: list[str] | None = None) -> None:
    """Positions 1..n in `order` (or the current order). Two passes because (tenant, position)
    is unique: every row steps out of the way first, then takes its place."""
    rows = (await s.execute(select(IntranetWtdList).where(
        IntranetWtdList.tenant_id == tenant_id).order_by(IntranetWtdList.position))).scalars().all()
    if order is not None:
        by_id = {str(r.id): r for r in rows}
        rows = [by_id[i] for i in order]
    for i, row in enumerate(rows, start=1):
        row.position = 1000 + i
    await s.flush()
    for i, row in enumerate(rows, start=1):
        if row.position != i:
            row.position = i
            row.draft_dirty = True
    await s.flush()


@router.put("/wtd-lists/order")
async def put_wtd_order(body: dict = Body(...), p: ConsolePrincipal = Depends(require_console_access),
                        s: AsyncSession = Depends(get_session)):
    ids = _body(body).get("ids")
    if not isinstance(ids, list):
        _unprocessable("ids", "Expected a full ordered id list.")
    rows = (await s.execute(select(IntranetWtdList).where(
        IntranetWtdList.tenant_id == p.user.tenant_id))).scalars().all()
    by_id = {str(r.id): r for r in rows}
    if set(map(str, ids)) != set(by_id) or len(ids) != len(by_id):
        _unprocessable("ids", "Must include every list exactly once.")
    await _renumber_lists(s, p.user.tenant_id, [str(i) for i in ids])
    for row in rows:
        row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.wtd_list.reordered", category="Win the Day",
        summary="Reordered Win the Day lists", target_type="wtd_list",
        entity_type="wtd_list")
    return {**(await get_wtd_lists(p, s)), "pending_changes": pending}


# ── the Win the Day playbook (services/wtd_playbook) ──────────────────────────────────────

async def _playbook_out(s: AsyncSession, tenant_id) -> dict:
    row = await _playbook_row(s, tenant_id)
    base = await follow_ups.fub_list_base(s, tenant_id)
    return {
        "content": wtd_playbook.current(row.content if row else None),
        "exists": row is not None,
        "published_at": _iso(row.published_at) if row else None,
        "draft_dirty": bool(row.draft_dirty) if row else False,
        "kinds": list(wtd_playbook.KINDS),
        # What an empty "Open Follow Up Boss" and every list and pond link resolve to.
        "fub": {"list_base": base, "people_url": wtd_playbook.people_url(base)},
    }


@router.get("/wtd/playbook")
async def get_wtd_playbook(p: ConsolePrincipal = Depends(require_console_access),
                           s: AsyncSession = Depends(get_session)):
    return await _playbook_out(s, p.user.tenant_id)


@router.patch("/wtd/playbook")
async def patch_wtd_playbook(body: dict = Body(...),
                             p: ConsolePrincipal = Depends(require_console_access),
                             s: AsyncSession = Depends(get_session)):
    """One or more whole sections, each validated on its own (services/wtd_playbook). A draft like
    every other change here: agents see it when somebody publishes."""
    body = _body(body)
    _unknown(body, set(wtd_playbook.SECTIONS))
    if not body:
        _unprocessable("body", "Send at least one section.")
    cleaned = {}
    for name, value in body.items():
        try:
            cleaned[name] = wtd_playbook.clean_section(name, value)
        except wtd_playbook.SectionError as e:
            _unprocessable(e.field, e.message)
    row = await _playbook_row(s, p.user.tenant_id, create=True)
    content = wtd_playbook.current(row.content)
    content.update(cleaned)
    row.content = content
    flag_modified(row, "content")
    row.draft_dirty = True
    names = ", ".join(sorted(cleaned))
    pending = await _record_mutation(
        s, p, action="content.wtd_playbook.updated", category="Win the Day",
        summary=f"Updated the Win the Day playbook ({names})", target_type="wtd_playbook",
        target_id=row.id, entity_type="wtd_playbook", entity_id=row.id)
    return {**(await _playbook_out(s, p.user.tenant_id)), "pending_changes": pending}


# ── the script library ───────────────────────────────────────────────────────────────────

SCRIPT_FIELDS = {"name", "chip", "url", "description", "group_key", "active"}


async def _apply_script_fields(s: AsyncSession, tenant_id, row: IntranetWtdScript,
                               body: dict) -> None:
    if "name" in body:
        row.name = _text(body, "name", required=True, max_len=120)
    if "chip" in body:
        row.chip = _text(body, "chip", nullable=True, max_len=40) or None
    if "url" in body:
        raw = _text(body, "url", nullable=True, max_len=500) or None
        try:
            row.url = wtd_playbook._link(raw)
        except ValueError as e:
            _unprocessable("url", str(e))
    if "description" in body:
        row.description = _text(body, "description", nullable=True, max_len=400) or None
    if "group_key" in body:
        value = _text(body, "group_key", nullable=True, max_len=32) or None
        if value is not None:
            content = await _draft_content(s, tenant_id)
            if value not in {g["key"] for g in content["scripts"]["groups"]}:
                _unprocessable("group_key", "Not one of the playbook's script groups.")
        row.group_key = value
    if "active" in body:
        row.active = bool(_bool(body, "active"))


@router.get("/wtd/scripts")
async def get_wtd_scripts(p: ConsolePrincipal = Depends(require_console_access),
                          s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(IntranetWtdScript).where(
        IntranetWtdScript.tenant_id == p.user.tenant_id).order_by(
            IntranetWtdScript.position, IntranetWtdScript.name))).scalars().all()
    return _list([_wtd_script(r) for r in rows])


@router.post("/wtd/scripts")
async def create_wtd_script(body: dict = Body(...),
                            p: ConsolePrincipal = Depends(require_console_access),
                            s: AsyncSession = Depends(get_session)):
    body = _body(body)
    _unknown(body, SCRIPT_FIELDS)
    if "name" not in body:
        _unprocessable("name", "Required.")
    last = (await s.execute(select(func.max(IntranetWtdScript.position)).where(
        IntranetWtdScript.tenant_id == p.user.tenant_id))).scalar()
    row = IntranetWtdScript(tenant_id=p.user.tenant_id, name="", position=int(last or 0) + 1,
                            active=True, draft_dirty=True)
    await _apply_script_fields(s, p.user.tenant_id, row, body)
    s.add(row)
    await s.flush()
    pending = await _record_mutation(
        s, p, action="content.wtd_script.created", category="Win the Day",
        summary=f"Added the script {row.name}", target_type="wtd_script", target_id=row.id,
        entity_type="wtd_script", entity_id=row.id, change_kind="created")
    return _with_pending(_wtd_script(row), pending)


@router.patch("/wtd/scripts/{script_id}")
async def patch_wtd_script(script_id: uuid.UUID, body: dict = Body(...),
                           p: ConsolePrincipal = Depends(require_console_access),
                           s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetWtdScript, p.user.tenant_id, script_id)
    body = _body(body)
    _unknown(body, SCRIPT_FIELDS)
    await _apply_script_fields(s, p.user.tenant_id, row, body)
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.wtd_script.updated", category="Win the Day",
        summary=f"Updated the script {row.name}", target_type="wtd_script", target_id=row.id,
        entity_type="wtd_script", entity_id=row.id)
    return _with_pending(_wtd_script(row), pending)


@router.delete("/wtd/scripts/{script_id}")
async def delete_wtd_script(script_id: uuid.UUID,
                            p: ConsolePrincipal = Depends(require_console_access),
                            s: AsyncSession = Depends(get_session)):
    """Gone from the library AND from every list that named it -- a chip pointing at a deleted
    script would be a link to nothing on an agent's screen."""
    row = await _one(s, IntranetWtdScript, p.user.tenant_id, script_id)
    name, sid = row.name, str(row.id)
    for lst in (await s.execute(select(IntranetWtdList).where(
            IntranetWtdList.tenant_id == p.user.tenant_id))).scalars().all():
        if sid in [str(x) for x in (lst.script_ids or [])]:
            lst.script_ids = [x for x in lst.script_ids if str(x) != sid] or None
            flag_modified(lst, "script_ids")
            lst.draft_dirty = True
    await s.delete(row)
    pending = await _record_mutation(
        s, p, action="content.wtd_script.deleted", category="Win the Day",
        summary=f"Removed the script {name}", target_type="wtd_script", target_id=script_id,
        entity_type="wtd_script", change_kind="deleted")
    return {"deleted": True, "pending_changes": pending}


@router.put("/wtd/scripts/order")
async def put_wtd_script_order(body: dict = Body(...),
                               p: ConsolePrincipal = Depends(require_console_access),
                               s: AsyncSession = Depends(get_session)):
    ids = _body(body).get("ids")
    rows = (await s.execute(select(IntranetWtdScript).where(
        IntranetWtdScript.tenant_id == p.user.tenant_id))).scalars().all()
    by_id = {str(r.id): r for r in rows}
    if not isinstance(ids, list) or len(ids) != len(by_id) or set(map(str, ids)) != set(by_id):
        _unprocessable("ids", "Must include every script exactly once.")
    for pos, row_id in enumerate(ids, start=1):
        by_id[str(row_id)].position = pos
        by_id[str(row_id)].draft_dirty = True
    pending = await _record_mutation(
        s, p, action="content.wtd_script.reordered", category="Win the Day",
        summary="Reordered the scripts", target_type="wtd_script", entity_type="wtd_script")
    return {**(await get_wtd_scripts(p, s)), "pending_changes": pending}


# ── checking list ids against the account ────────────────────────────────────────────────

@router.get("/wtd/lists/check")
async def check_wtd_list_ids(p: ConsolePrincipal = Depends(require_console_access),
                             s: AsyncSession = Depends(get_session)):
    """Which list and pond ids are not smart lists in the connected Follow Up Boss account.

    Asked with the stored key, on the server; ids and names are all that come back. This is how
    an imported playbook's ids get checked -- never by reading the key anywhere else."""
    rows = (await s.execute(select(IntranetWtdList).where(
        IntranetWtdList.tenant_id == p.user.tenant_id).order_by(IntranetWtdList.position))).scalars().all()
    content = await _draft_content(s, p.user.tenant_id)
    wanted = [{"kind": "list", "id": str(r.external_list_id).strip(), "name": r.name}
              for r in rows if r.provider == "follow_up_boss" and (r.external_list_id or "").strip()]
    wanted += [{"kind": "pond", "id": link["list_id"], "name": link["label"]}
               for link in content["lists"]["ponds"]["links"]]
    account = {str(item["id"]): item["name"] for item in await _fub_smart_lists(s, p.user.tenant_id)}
    missing = [w for w in wanted if w["id"] not in account]
    return {"checked": len(wanted), "missing": missing,
            "found": [{**w, "account_name": account[w["id"]]} for w in wanted if w["id"] in account]}


# ── moving a playbook between workspaces ─────────────────────────────────────────────────

@router.get("/wtd/export")
async def export_wtd_playbook(p: ConsolePrincipal = Depends(require_console_access),
                              s: AsyncSession = Depends(get_session)):
    row = await _playbook_row(s, p.user.tenant_id)
    lists = (await s.execute(select(IntranetWtdList).where(
        IntranetWtdList.tenant_id == p.user.tenant_id).order_by(IntranetWtdList.position))).scalars().all()
    scripts = (await s.execute(select(IntranetWtdScript).where(
        IntranetWtdScript.tenant_id == p.user.tenant_id).order_by(
            IntranetWtdScript.position, IntranetWtdScript.name))).scalars().all()
    return wtd_playbook.export_bundle(row.content if row else None, lists, scripts)


@router.post("/wtd/import")
async def import_wtd_playbook(body: dict = Body(...),
                              p: ConsolePrincipal = Depends(require_console_access),
                              s: AsyncSession = Depends(get_session)):
    """A playbook file, checked whole before anything is written, then written as a DRAFT.

    It refuses to overwrite: a workspace that already has a playbook, lists or scripts must say
    `replace`, and replacing removes them now -- the imported ones appear when somebody
    publishes."""
    body = _body(body)
    _unknown(body, {"bundle", "replace"})
    try:
        bundle = wtd_playbook.clean_bundle(body.get("bundle"))
    except wtd_playbook.SectionError as e:
        _unprocessable(e.field, e.message)
    tid = p.user.tenant_id
    existing = (await _playbook_row(s, tid) is not None
                or await _count(s, IntranetWtdList, tid) > 0
                or await _count(s, IntranetWtdScript, tid) > 0)
    if existing and not _bool(body, "replace", default=False):
        raise HTTPException(409, "This workspace already has a Win the Day playbook, lists or "
                                 "scripts. Import again with replace to swap them for the file's.")
    await s.execute(sa_delete(IntranetWtdList).where(IntranetWtdList.tenant_id == tid))
    await s.execute(sa_delete(IntranetWtdScript).where(IntranetWtdScript.tenant_id == tid))
    await s.execute(sa_delete(IntranetWtdPlaybook).where(IntranetWtdPlaybook.tenant_id == tid))
    await s.flush()
    playbook = IntranetWtdPlaybook(tenant_id=tid, content=bundle["content"], draft_dirty=True)
    s.add(playbook)
    ids = {}
    for i, item in enumerate(bundle["scripts"], start=1):
        script = IntranetWtdScript(
            tenant_id=tid, name=item["name"], chip=item.get("chip"), url=item.get("url"),
            description=item.get("description"), group_key=item.get("group_key"), position=i,
            active=bool(item.get("active", True)), draft_dirty=True)
        s.add(script)
        await s.flush()
        ids[item["ref"]] = str(script.id)
    for i, item in enumerate(bundle["lists"], start=1):
        s.add(IntranetWtdList(
            tenant_id=tid, position=i, name=item["name"],
            provider=item.get("provider") or "follow_up_boss",
            external_list_id=item.get("external_list_id"), cadence=item.get("cadence"),
            kind=item.get("kind") or "clear", description=item.get("description"),
            group_key=item.get("group_key"), block_key=item.get("block_key"),
            script_ids=[ids[r] for r in item.get("scripts") or []] or None,
            active=bool(item.get("active", True)), draft_dirty=True))
    await s.flush()
    pending = await _record_mutation(
        s, p, action="content.wtd_playbook.imported", category="Win the Day",
        summary=(f"Imported a Win the Day playbook: {len(bundle['lists'])} lists, "
                 f"{len(bundle['scripts'])} scripts"),
        target_type="wtd_playbook", target_id=playbook.id, entity_type="wtd_playbook",
        entity_id=playbook.id, change_kind="created",
        detail={"replaced": existing, "lists": len(bundle["lists"]),
                "scripts": len(bundle["scripts"])})
    return {**(await _playbook_out(s, tid)), "lists": len(bundle["lists"]),
            "scripts": len(bundle["scripts"]), "pending_changes": pending}


# ── each person's targets ────────────────────────────────────────────────────────────────

@router.get("/wtd/people")
async def get_wtd_people(p: ConsolePrincipal = Depends(require_console_access),
                         s: AsyncSession = Depends(get_session)):
    """The roster with each person's on-ramp start and personal targets, and where that puts them
    today -- so an admin setting a start date sees the phase it lands them in."""
    tid = p.user.tenant_id
    content = await _draft_content(s, tid)
    ws = await _workspace_row(s, tid)
    today = course_sections.workspace_today(ws.timezone if ws else None)
    rows = (await s.execute(select(IntranetMember).where(
        IntranetMember.tenant_id == tid, IntranetMember.status != "Removed").order_by(
            IntranetMember.full_name))).scalars().all()
    tallies = [{"key": r["tally"]["key"], "label": r["label"], "goal": r["tally"].get("goal")}
               for r in content["numbers"]["rows"] if r.get("tally")]
    people = []
    for m in rows:
        goals, phase = wtd_playbook.goals_for(content["numbers"], m.started_on, m.wtd_goals, today)
        people.append({"id": _id(m.id), "name": m.full_name, "status": m.status,
                       "started_on": _iso(m.started_on), "wtd_goals": m.wtd_goals or {},
                       "goals_today": goals, "onramp_phase": phase})
    return {"tallies": tallies, "today": today.isoformat(), "items": people}


# ── Who's Who (services/whos_who) ──────────────────────────────────────────────────────────

async def _directory_row(s: AsyncSession, tenant_id, *, create: bool = False
                         ) -> IntranetDirectorySetting | None:
    row = (await s.execute(select(IntranetDirectorySetting).where(
        IntranetDirectorySetting.tenant_id == tenant_id))).scalars().first()
    if row is None and create:
        row = IntranetDirectorySetting(tenant_id=tenant_id, stats=[], preview_count=9)
        s.add(row)
        await s.flush()
    return row


def _directory_settings(row: IntranetDirectorySetting | None) -> dict:
    return {
        "featured_member_id": _id(row.featured_member_id) if row else None,
        "featured_label": row.featured_label if row else None,
        "intro": row.intro if row else None,
        "stats": list(row.stats or []) if row else [],
        "preview_count": row.preview_count if row else 9,
    }


async def _directory_out(s: AsyncSession, tenant_id) -> dict:
    roles = await _roles_by_id(s, tenant_id)
    leadership = {rid for rid, r in roles.items() if r.is_leadership}
    rows = (await s.execute(select(IntranetMember).where(
        IntranetMember.tenant_id == tenant_id, IntranetMember.status != "Removed").order_by(
            IntranetMember.full_name))).scalars().all()
    people = []
    for m in rows:
        people.append({
            "id": _id(m.id), "name": m.full_name, "email": m.email, "status": m.status,
            "role_name": roles[m.role_id].name if m.role_id in roles else None,
            "title": m.title, "market": m.market, "tag": m.tag, "has_photo": bool(m.photo_key),
            "placement": m.directory_placement or "auto",
            "shown_in": whos_who.placement(m, leadership),
            # Where Automatic would put them, for the option's label -- not where they sit now.
            "auto_shown_in": "leadership" if m.role_id in leadership else "agents",
            "order": m.directory_order,
        })
    listed = [p for p in people if p["shown_in"] != "hidden"]
    return {
        "settings": _directory_settings(await _directory_row(s, tenant_id)),
        "people": people,
        "team_size": len(listed),
        "sisu_connected": (await member_numbers.sisu_state(s, tenant_id))["connected"],
        "leadership_roles": sorted(r.name for r in roles.values() if r.is_leadership),
    }


@router.get("/directory")
async def get_directory(p: ConsolePrincipal = Depends(require_console_access),
                        s: AsyncSession = Depends(get_session)):
    return await _directory_out(s, p.user.tenant_id)


@router.patch("/directory")
async def patch_directory(body: dict = Body(...),
                          p: ConsolePrincipal = Depends(require_console_access),
                          s: AsyncSession = Depends(get_session)):
    """The page's settings: who is featured, the intro, three stats, the preview count. Immediate,
    like the roster it shows."""
    body = _body(body)
    _unknown(body, {"intro", "featured_label", "stats", "preview_count", "featured_member_id"})
    tid = p.user.tenant_id
    sisu = (await member_numbers.sisu_state(s, tid))["connected"]
    try:
        clean = whos_who.clean_settings(body, sisu_connected=sisu)
    except whos_who.ProfileError as e:
        _unprocessable(e.field, e.message)
    row = await _directory_row(s, tid, create=True)
    if "featured_member_id" in body:
        member_id = _uuid_value(body, "featured_member_id", nullable=True)
        if member_id is not None:
            member = await _one(s, IntranetMember, tid, member_id)
            roles = await _roles_by_id(s, tid)
            leadership = {rid for rid, r in roles.items() if r.is_leadership}
            if not whos_who.listed(member, leadership):
                _unprocessable("featured_member_id",
                               "That person is hidden or removed. Show them on Who's Who first.")
        row.featured_member_id = member_id
    for field, value in clean.items():
        setattr(row, field, value)
    if "stats" in clean:
        flag_modified(row, "stats")
    await _record_mutation(
        s, p, action="content.directory.updated", category="People",
        summary="Updated the Who's Who page", target_type="directory", target_id=row.id,
        detail={"fields": sorted(body)}, pending=False)
    return await _directory_out(s, tid)


@router.put("/directory/leadership/order")
async def put_leadership_order(body: dict = Body(...),
                               p: ConsolePrincipal = Depends(require_console_access),
                               s: AsyncSession = Depends(get_session)):
    """The order of the Leadership section, as a list of member ids."""
    ids = _body(body).get("ids")
    if not isinstance(ids, list) or len(ids) > 60:
        _unprocessable("ids", "Expected the leadership members, in order.")
    tid = p.user.tenant_id
    try:
        wanted = [uuid.UUID(str(x)) for x in ids]
    except ValueError:
        _unprocessable("ids", "Expected member ids.")
    rows = {m.id: m for m in (await s.execute(select(IntranetMember).where(
        IntranetMember.tenant_id == tid, IntranetMember.id.in_(wanted)))).scalars().all()}
    if len(rows) != len(set(wanted)):
        _unprocessable("ids", "Not all of those are on this workspace's roster.")
    for i, member_id in enumerate(wanted):
        rows[member_id].directory_order = i
    await _record_mutation(
        s, p, action="content.directory.reordered", category="People",
        summary="Reordered Who's Who leadership", target_type="directory", pending=False)
    return await _directory_out(s, tid)


@router.post("/members/{member_id}/photo")
async def upload_member_photo(member_id: uuid.UUID, file: UploadFile = File(...),
                              p: ConsolePrincipal = Depends(require_console_access),
                              s: AsyncSession = Depends(get_session)):
    """A photo for Who's Who, re-encoded before it is stored (services/whos_who.process_photo):
    checked by its bytes, upright, no EXIF (a phone photo's GPS never reaches the portal), at most
    2000px, transparency flattened onto white."""
    row = await _one(s, IntranetMember, p.user.tenant_id, member_id)
    data = await file.read(whos_who.MAX_PHOTO_BYTES + 1)
    try:
        jpeg = whos_who.process_photo(data)
    except whos_who.ProfileError as e:
        _unprocessable("file", e.message)
    old = row.photo_key
    key = binder_storage.store(p.user.tenant_id, row.id,
                               f"photo-{binder_storage.content_hash(jpeg)[:12]}.jpg", jpeg)
    row.photo_key = key
    if old and old != key:
        try:
            binder_storage.delete(old)
        except Exception:        # the new photo is what matters; a stray old file is harmless
            pass
    await _record_mutation(
        s, p, action="access.member.photo_set", category="People",
        summary=f"Set the Who's Who photo for {row.full_name}", target_type="member",
        target_id=row.id, detail={"bytes": len(jpeg)}, pending=False)
    return {"item": _member(row, await _roles_by_id(s, p.user.tenant_id), await _account_of(s, row))}


@router.delete("/members/{member_id}/photo")
async def delete_member_photo(member_id: uuid.UUID,
                              p: ConsolePrincipal = Depends(require_console_access),
                              s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetMember, p.user.tenant_id, member_id)
    old = row.photo_key
    row.photo_key = None
    if old:
        try:
            binder_storage.delete(old)
        except Exception:
            pass
    await _record_mutation(
        s, p, action="access.member.photo_removed", category="People",
        summary=f"Removed the Who's Who photo for {row.full_name}", target_type="member",
        target_id=row.id, pending=False)
    return {"item": _member(row, await _roles_by_id(s, p.user.tenant_id), await _account_of(s, row))}


@router.get("/members/{member_id}/photo")
async def get_member_photo(member_id: uuid.UUID,
                           p: ConsolePrincipal = Depends(require_console_access),
                           s: AsyncSession = Depends(get_session)):
    """The stored photo, for the console's preview. Behind the console's session like the rest.

    Never cached: the address does not change when the photo does, so Replace photo would
    otherwise preview the old one."""
    row = await _one(s, IntranetMember, p.user.tenant_id, member_id)
    if not row.photo_key or not binder_storage.exists(row.photo_key):
        raise HTTPException(404, "No photo")
    return Response(content=binder_storage.read(row.photo_key), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


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
    _unknown(body, {"name", "color", "calendar_address", "sort", "active", "role_ids"})
    color = _text(body, "color") or "#C9A227"
    if not _hex_color(color):
        _unprocessable("color", "Expected #RRGGBB.")
    role_ids = await _role_ids_from_body(s, p.user.tenant_id, body, default_all=True)
    row = IntranetCalendarCategory(
        tenant_id=p.user.tenant_id,
        name=_text(body, "name", required=True) or "",
        color=color.upper(),
        calendar_address=_text(body, "calendar_address", nullable=True),
        sort=_int(body, "sort", default=await _count(s, IntranetCalendarCategory, p.user.tenant_id), min_value=0) or 0,
        active=_bool(body, "active", True),
    )
    s.add(row)
    await s.flush()
    for rid in role_ids:
        s.add(IntranetCalendarCategoryRole(tenant_id=p.user.tenant_id, category_id=row.id, role_id=rid))
    pending = await _record_mutation(
        s, p, action="config.calendar.created", category="Calendar",
        summary=f"Created calendar category {row.name}", target_type="calendar_category",
        target_id=row.id, entity_type="calendar_category", entity_id=row.id,
        change_kind="created")
    return _with_pending(_calendar_category(row, {row.id: [_id(rid) for rid in role_ids]}), pending)


@router.patch("/calendar-categories/{category_id}")
async def patch_calendar_category(category_id: uuid.UUID, body: dict = Body(...),
                                  p: ConsolePrincipal = Depends(require_console_access),
                                  s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetCalendarCategory, p.user.tenant_id, category_id)
    body = _body(body)
    _unknown(body, {"name", "color", "calendar_address", "sort", "active", "role_ids"})
    if "name" in body:
        row.name = _text(body, "name", required=True) or row.name
    if "color" in body:
        color = _text(body, "color", required=True, max_len=32) or row.color
        if not _hex_color(color):
            _unprocessable("color", "Expected #RRGGBB.")
        row.color = color.upper()
    if "calendar_address" in body:
        row.calendar_address = _text(body, "calendar_address", nullable=True)
    if "sort" in body:
        row.sort = _int(body, "sort", min_value=0) or 0
    if "active" in body:
        row.active = bool(_bool(body, "active"))
    if "role_ids" in body:
        role_ids = await _role_ids_from_body(s, p.user.tenant_id, body)
        await s.execute(sa_delete(IntranetCalendarCategoryRole).where(
            IntranetCalendarCategoryRole.tenant_id == p.user.tenant_id,
            IntranetCalendarCategoryRole.category_id == category_id,
        ))
        for rid in role_ids:
            s.add(IntranetCalendarCategoryRole(tenant_id=p.user.tenant_id, category_id=row.id, role_id=rid))
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="config.calendar.updated", category="Calendar",
        summary=f"Updated calendar category {row.name}", target_type="calendar_category",
        target_id=row.id, entity_type="calendar_category", entity_id=row.id)
    return _with_pending(_calendar_category(row, await _calendar_roles(s, p.user.tenant_id, [row.id])), pending)


@router.delete("/calendar-categories/{category_id}")
async def delete_calendar_category(category_id: uuid.UUID,
                                   p: ConsolePrincipal = Depends(require_console_access),
                                   s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetCalendarCategory, p.user.tenant_id, category_id)
    row.active = False
    row.draft_dirty = True
    pending = await _record_mutation(
        s, p, action="config.calendar.archived", category="Calendar",
        summary=f"Set calendar category {row.name} to inactive", target_type="calendar_category",
        target_id=row.id, entity_type="calendar_category", entity_id=row.id,
        change_kind="deleted")
    return _with_pending(_calendar_category(row), pending)


@router.get("/integrations")
async def get_integrations(p: ConsolePrincipal = Depends(require_console_access),
                           s: AsyncSession = Depends(get_session)):
    # Created on first read, the same way the Google sign-in and Slack panels create theirs. A
    # workspace provisioned before this existed has no provider rows at all, and no other route
    # can make one -- so without this its Integrations page stays empty forever.
    await ensure_rows(s, p.user.tenant_id)
    rows = (await s.execute(select(IntranetIntegration).where(
        IntranetIntegration.tenant_id == p.user.tenant_id, _not_signin()).order_by(
            IntranetIntegration.display_name))).scalars().all()
    inherited = await dashboard_connections(s, p.user.tenant_id)
    details = await _dashboard_details(s, p.user.tenant_id)
    payload = _list([_integration(r, inherited, details) for r in rows],
                    await _count(s, IntranetIntegration, p.user.tenant_id, _not_signin()))
    await s.commit()
    return payload


@router.patch("/integrations/{integration_id}")
async def patch_integration(integration_id: uuid.UUID, body: dict = Body(...),
                            p: ConsolePrincipal = Depends(require_console_access),
                            s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetIntegration, p.user.tenant_id, integration_id)
    body = _body(body)
    _unknown(body, {"display_name", "role_label", "description", "base_url", "config"})
    if "display_name" in body:
        row.display_name = _text(body, "display_name", required=True) or row.display_name
    if "role_label" in body:
        row.role_label = _text(body, "role_label", required=True) or row.role_label
    if "description" in body:
        row.description = _text(body, "description", nullable=True)
    if "base_url" in body:
        row.base_url = _https_url(body, "base_url")
    if "config" in body:
        row.config = _integration_config(body)
    pending = await _record_mutation(
        s, p, action="config.integration.updated", category="Integrations",
        summary=f"Updated integration settings for {row.display_name}",
        target_type="integration", target_id=row.id, pending=False)
    return _with_pending(_integration(row, await dashboard_connections(s, p.user.tenant_id),
                                      await _dashboard_details(s, p.user.tenant_id)), pending)


@router.post("/integrations/{integration_id}/connect")
async def connect_integration(integration_id: uuid.UUID, body: dict = Body(default_factory=dict),
                              p: ConsolePrincipal = Depends(require_console_access),
                              s: AsyncSession = Depends(get_session)):
    row = await _one(s, IntranetIntegration, p.user.tenant_id, integration_id)
    # A provider the dashboard owns is not connected from here. Accepting credentials would write
    # them to a row nothing reads while telling the admin they had connected something -- the
    # worst of both, because it looks like it worked.
    if is_inherited(row.provider_key):
        _unprocessable("provider",
                       f"{row.display_name} is connected on the Axcion dashboard, not here. "
                       f"Connect it there and this workspace picks it up.")
    body = _body(body or {})
    _unknown(body, {"config"})
    config = _integration_config(body)
    if config:
        row.config = {**(row.config or {}), **config}
    row.status = "Action Needed"
    row.last_sync_status = "Not yet available"
    row.last_error = "Connection flow is not available for this provider."
    pending = await _record_mutation(
        s, p, action="config.integration.disconnected", category="Integrations",
        summary=f"Connection flow unavailable for {row.display_name}",
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
        s, p, action="config.integration.test_run", category="System",
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
        s, p, action="config.ai.settings_updated", category="AI",
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
        s, p, action="config.ai.source_updated", category="AI",
        summary=f"Updated assistant source {row.name}", target_type="ai_source",
        target_id=row.id, entity_type="ai_source", entity_id=row.id)
    return _with_pending(_ai_source(row, await _roles_by_id(s, p.user.tenant_id)), pending)


@router.get("/ai/questions")
async def get_ai_questions(limit: int = 100,
                           p: ConsolePrincipal = Depends(require_console_access),
                           s: AsyncSession = Depends(get_session)):
    """What people actually asked, newest first.

    The gap list next to this one says what could not be answered; this says what was asked at
    all, which is the signal a gap list cannot carry. An SOP forty people ask about every month is
    worth revising even though the assistant answers it every time.

    IT NAMES THE ASKER, which makes this staff data. That is deliberate -- a gap you cannot
    attribute is a gap you cannot follow up -- and the portal tells members their questions are
    recorded, so this is not something they learn about from an admin quoting one back at them.
    """
    limit = max(1, min(int(limit or 100), 500))
    rows = (await s.execute(select(IntranetAiQuestion)
                            .where(IntranetAiQuestion.tenant_id == p.user.tenant_id)
                            .order_by(IntranetAiQuestion.created_at.desc())
                            .limit(limit))).scalars().all()
    total = await _count(s, IntranetAiQuestion, p.user.tenant_id)
    unanswered = sum(1 for r in rows if not r.answered)
    return {**_list([{
        "id": _id(r.id),
        "question": r.question,
        "answer": r.answer,
        "answered": bool(r.answered),
        "citations": list(r.citations or []),
        # Reported so an outage is not read as a hole in the workspace's documentation.
        "failure": r.failure,
        "asker_label": r.asker_label,
        "asked_at": _iso(r.created_at),
    } for r in rows], total), "unanswered_in_page": unanswered}


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
    old_assignee = row.assigned_member_id
    if "status" in body:
        row.status = _enum(body, "status", GAP_STATUSES) or row.status
    if "resolution_note" in body:
        row.resolution_note = _text(body, "resolution_note", nullable=True)
    if "assigned_member_id" in body:
        mid = _uuid_value(body, "assigned_member_id", nullable=True)
        await _member_by_id_or_none(s, p.user.tenant_id, mid)
        row.assigned_member_id = mid
    assigned = "assigned_member_id" in body and old_assignee != row.assigned_member_id
    pending = await _record_mutation(
        s, p, action="content.gap.assigned" if assigned else "content.gap.updated", category="AI",
        summary=f"Updated content gap: {row.question[:80]}", target_type="content_gap",
        target_id=row.id, pending=False)
    return _with_pending(_content_gap(row, await _member_by_id_or_none(s, p.user.tenant_id, row.assigned_member_id)), pending)


@router.get("/setup-tasks")
async def get_setup_tasks(p: ConsolePrincipal = Depends(require_console_access),
                          s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(IntranetSetupTask).where(
        IntranetSetupTask.tenant_id == p.user.tenant_id).order_by(IntranetSetupTask.sort))).scalars().all()
    evidence = await _setup_evidence(s, p.user.tenant_id)
    return _list([_setup_task(r, evidence.get(r.key)) for r in rows],
                 await _count(s, IntranetSetupTask, p.user.tenant_id))


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
    evidence = await _setup_evidence(s, p.user.tenant_id)
    satisfied = evidence.get(row.key)
    # A VERIFIABLE task cannot be ticked past its own configuration. Not paternalism: the
    # checklist is what an operator reads to decide whether a workspace is ready to hand to real
    # users, and a green row over an empty SOP library makes that read wrong. Tasks whose state
    # cannot be derived (satisfied is None) stay a human judgement and are accepted as given.
    if completed and satisfied is False:
        _unprocessable("completed",
                       f"{row.label} is not configured yet, so it cannot be marked complete. "
                       f"Finish it under {row.destination} first.")
    row.completed_at = _now() if completed else None
    row.completed_by = p.member.id if completed else None
    pending = await _record_mutation(
        s, p, action="console.setup.update", category="Setup",
        summary=f"{'Completed' if completed else 'Reopened'} setup task {row.label}",
        target_type="setup_task", target_id=row.id, pending=False)
    return _with_pending(_setup_task(row, satisfied), pending)


@router.get("/publish/pending")
async def get_pending_changes(p: ConsolePrincipal = Depends(require_console_access),
                              s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(IntranetPendingChange).where(
        IntranetPendingChange.tenant_id == p.user.tenant_id,
        IntranetPendingChange.publish_batch_id.is_(None),
    ).order_by(IntranetPendingChange.created_at.desc()))).scalars().all()
    return _list([_pending(r) for r in rows], len(rows))


def _publishable_models() -> tuple:
    """Every tenant-scoped model that participates in draft/publish, found rather than listed.

    This was a hand-written tuple of seventeen classes, and a publishable table added without
    being added to it would never be marked published: `draft_dirty` stays true forever, and the
    live intranet keeps serving the pre-publish state with nothing raising. That is not a
    hypothetical -- intranet_marketing_setting was written and the tuple did not know about it.

    The three columns ARE the contract. A model carrying tenant_id, published_at and draft_dirty
    is by definition something the publish cycle owns, so asking the mapper registry is both
    shorter and the actual question. Checked against the tuple it replaces: it reproduces all
    seventeen exactly and adds only the new table.
    """
    found = []
    for mapper in Base.registry.mappers:
        columns = {col.key for col in mapper.columns}
        if {"tenant_id", "published_at", "draft_dirty"} <= columns:
            found.append(mapper.class_)
    # Sorted by name so the write order is stable between runs and across processes.
    return tuple(sorted(found, key=lambda m: m.__name__))


async def _stage_sop_bodies(s: AsyncSession, tenant_id, *, revert: bool) -> None:
    """The one piece of content that waits for Publish (D2, services/sop_library).

    Publishing copies each draft procedure into `published_body`, which is what members read;
    discarding copies it back, which makes Discard mean something for SOPs -- everywhere else it
    only clears the queue, because everywhere else the draft was already live.
    """
    rows = (await s.execute(select(IntranetSop).where(
        IntranetSop.tenant_id == tenant_id))).scalars().all()
    for row in rows:
        source, target = ("published_body", "body") if revert else ("body", "published_body")
        value = getattr(row, source)
        if getattr(row, target) != value:
            setattr(row, target, value)
            flag_modified(row, target)


async def _set_publish_state(s: AsyncSession, tenant_id, when: dt.datetime, dirty: bool) -> None:
    for model in _publishable_models():
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
    await _stage_sop_bodies(s, p.user.tenant_id, revert=False)
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
    await _stage_sop_bodies(s, p.user.tenant_id, revert=True)
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
    category_key = str(category or "").strip().lower()
    if category_key in {"", "everything"}:
        pass
    elif category_key == "publish":
        where.append(AuditLog.category == "Publish")
    elif category_key == "access":
        where.append(or_(
            AuditLog.action.like("access.%"),
            AuditLog.action == "console.role.update",
            AuditLog.category.in_(["People", "Roles"]),
        ))
    elif category_key == "content":
        where.append(or_(
            AuditLog.action.like("content.%"),
            AuditLog.action.like("config.%"),
        ))
    elif category_key == "read":
        where.append(or_(
            AuditLog.action.like("read.%"),
            AuditLog.category == "Read",
        ))
    elif category:
        where.append(AuditLog.category == category)
    if before:
        where.append(AuditLog.created_at < before)
    rows = (await s.execute(select(AuditLog).where(*where).order_by(
        AuditLog.created_at.desc()).limit(limit + 1))).scalars().all()
    page = rows[:limit]
    next_cursor = _iso(page[-1].created_at) if len(rows) > limit and page else None
    return {"items": [_audit(r) for r in page], "total": len(page), "cursor": next_cursor}


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
