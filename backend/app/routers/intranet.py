from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from .. import plans
from ..db import get_session
from ..deps import current_user, require_role
from ..models import IntranetUserState, Tenant, User
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


def _config_out(tenant: Tenant, user: User) -> dict:
    return {
        "enabled": True,
        "can_configure": user.role in ("owner", "admin"),
        "config": _stored_config(tenant),
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
    return _config_out(tenant, user)


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
