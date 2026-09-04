from __future__ import annotations

import datetime as dt
import json
import re
import uuid

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query, Response,
                     UploadFile)
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from .. import plans
from ..db import get_session
from ..deps import current_user, require_role
from ..models import (IntranetMarketingAttachment, IntranetMarketingRequest,
                      IntranetMarketingSetting, IntranetMember, IntranetRole,
                      IntranetUserState, Tenant, User)
from ..services import binder_storage
from ..services.audit import audit

router = APIRouter(prefix="/intranet", tags=["intranet"])

STATE_SCOPES = {"wtd", "training", "onboarding", "sops"}
MAX_STATE_BYTES = 50_000
GOOGLE_CALENDAR_PREFIX = "https://calendar.google.com/"
URL_RE = re.compile(r"^https?://", re.I)
DATE_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STATE_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class IntranetConfigPatch(BaseModel):
    calendar: dict | None = None
    marketing_requests: dict | None = None
    links: dict | None = None
    brand: dict | None = None


class IntranetStateIn(BaseModel):
    state_key: str = "global"
    timezone: str | None = None
    value: dict = Field(default_factory=dict)


def _default_config() -> dict:
    return {
        "calendar": {"google_calendar_url": ""},
        "marketing_requests": {"url": "", "label": "Marketing requests"},
        "links": {"tools": {}, "fub_lists": {}},
        "brand": {
            "font_mode": "proxy",
            "mark_url": "",
            "display_font": "Playfair Display",
            "text_font": "DM Sans",
            "utility_font": "Archivo",
            "mono_font": "SFMono-Regular, Consolas, Liberation Mono, monospace",
        },
        "numbers": {
            "calls_today": 0,
            "appointments_set": 0,
            "contracts_pending": 0,
            "closed_units": 0,
            "closed_volume": 0,
        },
    }


def _merge_known(defaults: dict, raw: dict | None) -> dict:
    out = json.loads(json.dumps(defaults))
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _merge_known(out[key], value)
        elif key in out:
            out[key] = value
    return out


def _clean_url(value: object, *, google_calendar: bool = False) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    if google_calendar:
        if not url.startswith(GOOGLE_CALENDAR_PREFIX):
            raise HTTPException(400, "Calendar URL must be a Google Calendar URL.")
        return url
    if not URL_RE.match(url):
        raise HTTPException(400, "URL must start with http:// or https://.")
    return url


def _clean_link_map(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    out = {}
    for key, url in value.items():
        clean_key = str(key or "").strip()[:80]
        if not clean_key:
            continue
        out[clean_key] = _clean_url(url)
    return out


def _stored_config(tenant: Tenant) -> dict:
    cfg = tenant.config or {}
    return _merge_known(_default_config(), cfg.get("intranet") or {})


def _marketing_out(row: IntranetMarketingSetting | None, role_name: str | None) -> dict:
    """What the INTRANET is told about marketing requests.

    Deliberately not the console's view of the same row. The destination -- an ops Slack channel,
    a shared inbox, a webhook -- is internal routing, and there is no reason every agent in the
    workspace can read it off an API response. What an agent needs is whether the form is open,
    what they will be asked for, and who picks it up.

    `available` is the two console facts combined, because from the intranet's side "switched on
    but pointing nowhere" and "switched off" are the same thing: no form. `delivery_pending` is
    reported separately so the UI can say the request will be recorded but not yet routed, rather
    than implying delivery it cannot perform.
    """
    if row is None:
        return {"available": False, "required_fields": [], "assigned_role": None,
                "delivery_pending": True}
    complete = bool(row.enabled and row.destination_type != "none"
                    and (row.destination or "").strip())
    return {
        "available": complete,
        "required_fields": list(row.required_fields or []),
        "assigned_role": role_name,
        # True until the Phase 10 delivery path exists. The intranet says so rather than
        # presenting a form that looks like it sends somewhere.
        "delivery_pending": True,
    }


def _config_out(tenant: Tenant, user: User,
                marketing: IntranetMarketingSetting | None = None,
                marketing_role: str | None = None) -> dict:
    config = _stored_config(tenant)
    config["marketing"] = _marketing_out(marketing, marketing_role)
    return {
        "enabled": True,
        "can_configure": user.role in ("owner", "admin"),
        "config": config,
    }


async def _enabled_tenant(s: AsyncSession, user: User) -> Tenant:
    tenant = await s.get(Tenant, user.tenant_id)
    if tenant is None or not plans.allows(tenant, "intranet"):
        raise HTTPException(403, "Intranet is not enabled for this workspace.")
    return tenant


def _state_key(scope: str, raw: str | None) -> str:
    key = (raw or "global").strip()
    if scope == "wtd":
        if not DATE_KEY_RE.fullmatch(key):
            raise HTTPException(400, "Win the Day state_key must be a local ISO date.")
        return key
    if not STATE_KEY_RE.fullmatch(key):
        raise HTTPException(400, "Invalid state key.")
    return key


def _check_state_value(value: dict) -> dict:
    if not isinstance(value, dict):
        raise HTTPException(400, "State value must be an object.")
    if len(json.dumps(value, separators=(",", ":"))) > MAX_STATE_BYTES:
        raise HTTPException(413, "State value is too large.")
    return value


@router.get("/config")
async def get_config(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    tenant = await _enabled_tenant(s, user)
    # Read-only: the console owns this row and creates it. A tenant that has never opened the
    # Marketing Requests screen has no row, and None is the honest answer for that -- creating one
    # here would write to the database on a GET.
    marketing = await s.get(IntranetMarketingSetting, user.tenant_id)
    role_name = None
    if marketing is not None and marketing.default_role_id is not None:
        role = await s.get(IntranetRole, marketing.default_role_id)
        role_name = role.name if role is not None and role.tenant_id == user.tenant_id else None
    return _config_out(tenant, user, marketing, role_name)


@router.patch("/config")
async def patch_config(body: IntranetConfigPatch,
                       user: User = Depends(require_role("owner", "admin")),
                       s: AsyncSession = Depends(get_session)):
    tenant = await _enabled_tenant(s, user)
    current = _stored_config(tenant)
    fields = body.model_dump(exclude_unset=True)
    if "calendar" in fields:
        cal = dict(current.get("calendar") or {})
        incoming = fields["calendar"] or {}
        if "google_calendar_url" in incoming or "embed_url" in incoming:
            cal["google_calendar_url"] = _clean_url(
                incoming.get("google_calendar_url") or incoming.get("embed_url"),
                google_calendar=True)
        current["calendar"] = cal
    if "marketing_requests" in fields:
        incoming = fields["marketing_requests"] or {}
        prior = current.get("marketing_requests") or {}
        current["marketing_requests"] = {
            "url": _clean_url(incoming["url"]) if "url" in incoming else prior.get("url", ""),
            "label": str(incoming.get("label") or prior.get("label")
                         or "Marketing requests").strip()[:80],
        }
    if "links" in fields:
        incoming = fields["links"] or {}
        prior = current.get("links") or {}
        current["links"] = {
            "tools": _clean_link_map(incoming["tools"]) if "tools" in incoming
            else dict(prior.get("tools") or {}),
            "fub_lists": _clean_link_map(incoming["fub_lists"]) if "fub_lists" in incoming
            else dict(prior.get("fub_lists") or {}),
        }
    if "brand" in fields:
        incoming = fields["brand"] or {}
        brand = dict(current.get("brand") or {})
        if "mark_url" in incoming:
            brand["mark_url"] = _clean_url(incoming.get("mark_url"))
        current["brand"] = brand

    cfg = dict(tenant.config or {})
    cfg["intranet"] = current
    tenant.config = cfg
    flag_modified(tenant, "config")
    audit(s, user.tenant_id, user.id, "intranet.config_changed", "tenant", tenant.id,
          {"fields": sorted(fields)})
    await s.commit()
    return _config_out(tenant, user)


# The fields a workspace may demand, mirroring MARKETING_FIELDS in the console router. Kept as
# its own constant rather than imported: this router validates what a SUBMITTER sends, the console
# validates what an ADMIN requires, and the two lists agreeing is asserted by a test rather than
# by a shared import that would let one quietly follow the other.
# UPLOAD POLICY. Four types a marketing request plausibly carries, and no more.
#
# SVG is absent deliberately even though the logo uploader accepts it: an SVG is a script host,
# and these files are fetched by other people in the workspace. HTML for the same reason. The
# download is served as an attachment with the sniffed type, so nothing here renders inline, but
# the allowlist is the primary control rather than the header.
# Magic numbers as hex, not escapes: a byte literal written through a shell heredoc gets its
# escapes eaten, which is how this arrived as real control characters the first time.
ATTACHMENT_TYPES = {
    "image/png": (bytes.fromhex("89504e470d0a1a0a"), ".png"),
    "image/jpeg": (bytes.fromhex("ffd8ff"), ".jpg"),
    "application/pdf": (b"%PDF-", ".pdf"),
    "image/webp": (None, ".webp"),          # RIFF....WEBP, checked separately below
}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS = 5


def sniff_attachment(data: bytes) -> str | None:
    """The type the BYTES claim, ignoring the filename and the browser's Content-Type entirely.

    A client controls both of those. If a download later echoes a declared type back, an uploaded
    HTML file labelled `image/png` becomes stored XSS against everyone who opens it. Sniffing is
    what makes the allowlist mean anything.
    """
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for content_type, (magic, _ext) in ATTACHMENT_TYPES.items():
        if magic and data.startswith(magic):
            return content_type
    return None


# The TEXT fields a submitter sends. `attachments` is deliberately not among them -- it is not a
# text field -- but it IS requirable, so the vocabulary check below has to know about it.
MARKETING_SUBMIT_FIELDS = {"listing", "client", "request_type", "due_date", "priority",
                          "description"}
# Everything an admin may require, which is the text fields plus the files. Kept separate from
# the console's own list on purpose: this router validates what a SUBMITTER sends and that one
# validates what an ADMIN requires, and a shared import would let one quietly follow the other.
# A test asserts they still agree.
MARKETING_REQUIRABLE = MARKETING_SUBMIT_FIELDS | {"attachments"}
MARKETING_PRIORITIES = {"Low", "Normal", "High"}


async def _member_for(s: AsyncSession, user: User) -> IntranetMember | None:
    email = (user.email or "").strip().lower()
    return (await s.execute(select(IntranetMember).where(
        IntranetMember.tenant_id == user.tenant_id,
        or_(IntranetMember.user_id == user.id, IntranetMember.email == email),
    ))).scalars().first()


def _request_out(row: IntranetMarketingRequest, attachment_count: int = 0) -> dict:
    return {
        "id": str(row.id), "title": row.title, "request_type": row.request_type,
        "listing": row.listing, "client": row.client, "description": row.description,
        "due_date": row.due_date.isoformat() if row.due_date else None,
        "priority": row.priority, "status": row.status,
        "requester_label": row.requester_label,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        # Reported so the submitter is never left guessing whether it went anywhere. Null means
        # it is recorded and has not been sent, which is the truth until delivery ships.
        "delivered_at": row.delivered_at.isoformat() if row.delivered_at else None,
        "attachment_count": attachment_count,
    }


@router.get("/marketing/requests")
async def list_my_requests(user: User = Depends(current_user),
                           s: AsyncSession = Depends(get_session)):
    """The requests THIS person filed. Not the workspace's queue -- that is the console's, and an
    agent has no reason to read what colleagues have asked for."""
    await _enabled_tenant(s, user)
    member = await _member_for(s, user)
    if member is None:
        return {"items": [], "total": 0}
    rows = (await s.execute(select(IntranetMarketingRequest).where(
        IntranetMarketingRequest.tenant_id == user.tenant_id,
        IntranetMarketingRequest.requester_member_id == member.id,
    ).order_by(IntranetMarketingRequest.created_at.desc()).limit(50))).scalars().all()
    # One grouped count rather than a query per row: this list is read on every visit to the page.
    counts = dict((await s.execute(
        select(IntranetMarketingAttachment.request_id, func.count())
        .where(IntranetMarketingAttachment.tenant_id == user.tenant_id,
               IntranetMarketingAttachment.request_id.in_([r.id for r in rows] or [None]))
        .group_by(IntranetMarketingAttachment.request_id))).all())
    return {"items": [_request_out(r, counts.get(r.id, 0)) for r in rows], "total": len(rows)}


@router.post("/marketing/requests", status_code=201)
async def submit_request(
    title: str = Form(...),
    request_type: str | None = Form(None),
    listing: str | None = Form(None),
    client: str | None = Form(None),
    description: str | None = Form(None),
    due_date: str | None = Form(None),
    priority: str = Form("Normal"),
    files: list[UploadFile] = File(default=[]),
    user: User = Depends(current_user),
    s: AsyncSession = Depends(get_session),
):
    """File a request, with its files, in one transaction.

    MULTIPART RATHER THAN JSON, and atomic rather than upload-after-create. For a listing flyer
    the photograph often IS the request; a two-step flow whose second step fails leaves a record
    that reads as complete with the point of it missing, silently, and the agent who did the work
    is the one who loses it. It is also the only shape in which `attachments` can be a REQUIRED
    field, because the requirement and the files arrive together.

    THE WORKSPACE'S REQUIRED FIELDS ARE ENFORCED HERE, not only in the form. A required field
    checked in the browser only is a suggestion, and the console's setting would mean nothing to
    anything that posts directly.
    """
    await _enabled_tenant(s, user)
    cfg = await s.get(IntranetMarketingSetting, user.tenant_id)
    available = bool(cfg and cfg.enabled and cfg.destination_type != "none"
                     and (cfg.destination or "").strip())
    if not available:
        raise HTTPException(409, "Marketing requests are not switched on for this workspace.")

    member = await _member_for(s, user)
    if member is None:
        raise HTTPException(403, "You are not on this workspace's roster.")

    title = (title or "").strip()[:200]
    if not title:
        raise HTTPException(422, "A title is required.")

    values = {
        "request_type": (request_type or "").strip()[:2000] or None,
        "listing": (listing or "").strip()[:2000] or None,
        "client": (client or "").strip()[:2000] or None,
        "description": (description or "").strip()[:2000] or None,
    }

    if due_date and due_date.strip():
        try:
            values["due_date"] = dt.date.fromisoformat(due_date.strip())
        except ValueError:
            raise HTTPException(422, "due_date must be a date like 2026-09-30.")
    else:
        values["due_date"] = None

    priority = (priority or "Normal").strip().title()
    if priority not in MARKETING_PRIORITIES:
        raise HTTPException(
            422, "priority must be one of: " + ", ".join(sorted(MARKETING_PRIORITIES)) + ".")

    # Read and validate EVERY file before writing anything, so a bad third file cannot leave a
    # saved request and two orphaned uploads behind.
    uploads = []
    real_files = [f for f in (files or []) if f is not None and (f.filename or "").strip()]
    if len(real_files) > MAX_ATTACHMENTS:
        raise HTTPException(422, "At most " + str(MAX_ATTACHMENTS) + " files per request.")
    for upload in real_files:
        data = await upload.read()
        if not data:
            raise HTTPException(422, (upload.filename or "file") + " is empty.")
        if len(data) > MAX_ATTACHMENT_BYTES:
            limit = MAX_ATTACHMENT_BYTES // (1024 * 1024)
            raise HTTPException(
                422, (upload.filename or "file") + " is larger than " + str(limit) + " MB.")
        sniffed = sniff_attachment(data)
        if sniffed is None:
            raise HTTPException(
                422, (upload.filename or "file") + " is not a PNG, JPEG, WebP or PDF.")
        # The stored name keeps the user's stem but takes its extension from the SNIFFED type, so
        # a file called photo.html that really is a PNG is stored as a PNG, and nothing
        # downstream is asked to trust a name the client chose.
        stem = binder_storage.safe_filename(upload.filename or "attachment").rsplit(".", 1)[0]
        uploads.append((stem + ATTACHMENT_TYPES[sniffed][1], data, sniffed))

    required = list(cfg.required_fields or [])
    missing = [f for f in required if f in MARKETING_SUBMIT_FIELDS and not values.get(f)]
    if "attachments" in required and not uploads:
        missing.append("attachments")
    if missing:
        raise HTTPException(422, "This workspace requires: " + ", ".join(sorted(missing)) + ".")

    row = IntranetMarketingRequest(
        tenant_id=user.tenant_id,
        requester_member_id=member.id,
        requester_label=member.full_name or member.email or "Unknown",
        title=title, priority=priority, assignee_member_id=None,
        **values,
    )
    s.add(row)
    await s.flush()

    for name, data, content_type in uploads:
        attachment_id = uuid.uuid4()
        key = "intranet/" + str(user.tenant_id) + "/marketing/" + str(row.id) + "/" \
            + str(attachment_id) + "-" + name
        binder_storage.put(key, data, content_type)
        s.add(IntranetMarketingAttachment(
            id=attachment_id, tenant_id=user.tenant_id, request_id=row.id,
            filename=name, storage_key=key, content_type=content_type,
            byte_size=len(data), uploaded_by=member.id))

    audit(s, user.tenant_id, user.id, "marketing.request_submitted",
          "marketing_request", str(row.id),
          {"title": title, "priority": priority, "attachments": len(uploads)},
          category="Marketing", summary="Filed marketing request: " + title,
          actor_member_id=member.id, actor_label=member.full_name)
    await s.commit()
    return _request_out(row, len(uploads))


@router.get("/marketing/requests/{request_id}/attachments/{attachment_id}")
async def download_attachment(request_id: uuid.UUID, attachment_id: uuid.UUID,
                              user: User = Depends(current_user),
                              s: AsyncSession = Depends(get_session)):
    """Proxied, never a direct storage URL, and scoped to the requester's OWN request.

    Served as an attachment with the SNIFFED content type. Both matter: an inline disposition on
    a file somebody else uploaded is how stored XSS reaches the next person to open it, and the
    declared type would have been the uploader's choice rather than the file's.
    """
    await _enabled_tenant(s, user)
    member = await _member_for(s, user)
    if member is None:
        raise HTTPException(404, "Not found")
    row = (await s.execute(select(IntranetMarketingAttachment).join(
        IntranetMarketingRequest,
        IntranetMarketingRequest.id == IntranetMarketingAttachment.request_id,
    ).where(
        IntranetMarketingAttachment.tenant_id == user.tenant_id,
        IntranetMarketingAttachment.id == attachment_id,
        IntranetMarketingAttachment.request_id == request_id,
        # An agent reads their own request's files. The workspace queue is the console's.
        IntranetMarketingRequest.requester_member_id == member.id,
    ))).scalars().first()
    if row is None or not binder_storage.exists(row.storage_key):
        raise HTTPException(404, "Not found")
    safe = binder_storage.safe_filename(row.filename)
    return Response(
        content=binder_storage.read(row.storage_key),
        media_type=row.content_type,
        headers={"Content-Disposition": 'attachment; filename="' + safe + '"'},
    )


@router.get("/state/{scope}")
async def get_state(scope: str, state_key: str = Query("global"),
                    user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session)):
    if scope not in STATE_SCOPES:
        raise HTTPException(404, "Unknown intranet state scope.")
    await _enabled_tenant(s, user)
    key = _state_key(scope, state_key)
    row = (await s.execute(select(IntranetUserState).where(
        IntranetUserState.tenant_id == user.tenant_id,
        IntranetUserState.user_id == user.id,
        IntranetUserState.scope == scope,
        IntranetUserState.state_key == key,
    ))).scalar_one_or_none()
    return {
        "scope": scope,
        "state_key": key,
        "timezone": row.timezone if row else None,
        "value": row.value if row else {},
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
    }


@router.put("/state/{scope}")
async def put_state(scope: str, body: IntranetStateIn,
                    user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session)):
    if scope not in STATE_SCOPES:
        raise HTTPException(404, "Unknown intranet state scope.")
    await _enabled_tenant(s, user)
    key = _state_key(scope, body.state_key)
    value = _check_state_value(body.value)
    timezone = (body.timezone or "").strip()[:64] or None

    async def apply():
        row = (await s.execute(select(IntranetUserState).where(
            IntranetUserState.tenant_id == user.tenant_id,
            IntranetUserState.user_id == user.id,
            IntranetUserState.scope == scope,
            IntranetUserState.state_key == key,
        ))).scalar_one_or_none()
        if row is None:
            row = IntranetUserState(
                tenant_id=user.tenant_id, user_id=user.id,
                scope=scope, state_key=key)
            s.add(row)
        row.timezone = timezone
        row.value = value
        return row

    row = await apply()
    try:
        await s.commit()
    except IntegrityError:
        await s.rollback()
        row = await apply()
        await s.commit()
    return {
        "scope": scope,
        "state_key": key,
        "timezone": row.timezone,
        "value": row.value,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
