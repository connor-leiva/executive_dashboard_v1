from __future__ import annotations

import datetime as dt
import hashlib
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
from ..models import (IntranetCourse, IntranetLaunchpadTile, IntranetLaunchpadTileRole,
                      IntranetLesson, IntranetMarketingAttachment, IntranetMarketingRequest,
                      IntranetMarketingSetting, IntranetMember, IntranetRole, IntranetSop,
                      IntranetCourseRole, IntranetSopAcknowledgement, IntranetSopVersion,
                      IntranetPage, IntranetPageRole, IntranetPageSection,
                      IntranetIntegration, IntranetSopCategory, IntranetUserState,
                      IntranetWorkspace,
                      IntranetWtdList, Tenant, User)
from ..services import binder_storage
from ..services.inheritance import dashboard_connections
from ..services.intranet_permissions import allows, capability_levels
from ..services.audit import audit

def _iso(value) -> str | None:
    """A timestamp the browser can parse, or nothing. Never a naive string."""
    return value.isoformat() if value is not None else None


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


def _marketing_out(row: IntranetMarketingSetting | None, role_name: str | None,
                   permitted: bool = True) -> dict:
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
    # A role the matrix denies gets the same answer as a workspace with it switched off: no
    # form. Three different reasons, one honest outcome -- and the endpoint refuses independently,
    # because a hidden form is not a permission.
    complete = bool(permitted and row.enabled and row.destination_type != "none"
                    and (row.destination or "").strip())
    return {
        "available": complete,
        "required_fields": list(row.required_fields or []),
        "assigned_role": role_name,
        # Was hardcoded True while nothing delivered. Now it means what it says: a configured
        # destination is delivered to, and the only pending case left is a form that is open with
        # nowhere to send -- which `available` already refuses, so this is False whenever the form
        # is actually shown. Kept in the payload because the intranet reads it, and because a
        # future destination type that cannot deliver would need it again.
        "delivery_pending": not complete,
    }


async def _published_content(s: AsyncSession, tenant_id, member: IntranetMember | None) -> dict:
    """The workspace's OWN configured content, as the intranet should render it.

    THIS IS THE MULTI-TENANCY FIX AND IT IS NOT COSMETIC. The console has always written roles,
    launchpad tiles, Win the Day lists, courses and SOPs into tenant-scoped tables. The intranet
    never read any of them -- it rendered a compiled-in `constants.js` shaped around the first
    customer, so every workspace on the platform would have seen that customer's navigation,
    their roles, their tool stack and their daily checklists no matter what their own admin had
    configured. The console was configuring tables nothing consumed.

    PUBLISHED ONLY. A row with `published_at` unset has never been published, so the live intranet
    must not show it; that is the draft/live separation the console's publish button exists for.
    Preview is the console's own route and is allowed to see drafts.

    ROLE AUDIENCE IS APPLIED HERE, on the server. Tiles carry a role audience, and filtering that
    in the browser would mean shipping every tile to every agent and hiding some with CSS.
    """
    published = lambda model: model.published_at.is_not(None)   # noqa: E731

    roles = (await s.execute(select(IntranetRole).where(
        IntranetRole.tenant_id == tenant_id, published(IntranetRole),
    ).order_by(IntranetRole.sort))).scalars().all()

    tiles = (await s.execute(select(IntranetLaunchpadTile).where(
        IntranetLaunchpadTile.tenant_id == tenant_id,
        IntranetLaunchpadTile.active.is_(True),
        published(IntranetLaunchpadTile),
    ).order_by(IntranetLaunchpadTile.sort, IntranetLaunchpadTile.name))).scalars().all()

    # A tile with NO audience rows is visible to everyone; one with rows is visible to those roles.
    # Absence means "not restricted", which is what an admin who never opened the audience picker
    # intends -- the alternative silently hides every tile until somebody ticks boxes.
    audience: dict = {}
    if tiles:
        for tile_id, role_id in (await s.execute(select(
            IntranetLaunchpadTileRole.tile_id, IntranetLaunchpadTileRole.role_id,
        ).where(IntranetLaunchpadTileRole.tenant_id == tenant_id,
                IntranetLaunchpadTileRole.tile_id.in_([t.id for t in tiles])))).all():
            audience.setdefault(tile_id, set()).add(role_id)

    my_role = member.role_id if member is not None else None
    # What this role may see. Until now this matrix was authored, published and read in exactly
    # one place (console_access), so every other capability an admin set was decorative.
    levels = await capability_levels(s, tenant_id, my_role)
    visible = [t for t in tiles
               if t.id not in audience or my_role in audience[t.id]]

    wtd = [] if not allows(levels, "wtd") else (await s.execute(select(IntranetWtdList).where(
        IntranetWtdList.tenant_id == tenant_id,
        IntranetWtdList.active.is_(True),
        published(IntranetWtdList),
    ).order_by(IntranetWtdList.position))).scalars().all()

    courses = [] if not allows(levels, "training_library") else (await s.execute(
        select(IntranetCourse).where(
            IntranetCourse.tenant_id == tenant_id,
            IntranetCourse.state == "Live",
            published(IntranetCourse),
        ).order_by(IntranetCourse.sort))).scalars().all()

    lessons_by_course: dict = {}
    if courses:
        for lesson in (await s.execute(select(IntranetLesson).where(
            IntranetLesson.tenant_id == tenant_id,
            IntranetLesson.course_id.in_([c.id for c in courses]),
            # LESSONS WERE THE ONE CONTENT TYPE WITH NO PUBLISHED FILTER. Roles, tiles, lists,
            # courses, SOPs and categories all had one; lessons did not, so a half-written draft
            # was live to the whole team the moment it was saved. The publish button is the
            # promise that does not happen, and for lessons it was not being kept.
            published(IntranetLesson),
        ).order_by(IntranetLesson.sort))).scalars().all():
            lessons_by_course.setdefault(lesson.course_id, []).append(lesson)

    # A course with NO audience rows is visible to everyone; one with rows is visible to those
    # roles. Same rule as launchpad tiles, and for the same reason: absence means "not
    # restricted", which is what an admin who never opened the picker intends. The console has
    # written these rows since it shipped and nothing had ever read them.
    course_audience: dict = {}
    if courses:
        for course_id, role_id in (await s.execute(select(
            IntranetCourseRole.course_id, IntranetCourseRole.role_id,
        ).where(IntranetCourseRole.tenant_id == tenant_id,
                IntranetCourseRole.course_id.in_([c.id for c in courses])))).all():
            course_audience.setdefault(course_id, set()).add(role_id)
    courses = [c for c in courses
               if c.id not in course_audience or my_role in course_audience[c.id]]

    # ── who's who ────────────────────────────────────────────────────────────────────────
    # ACTIVE MEMBERS ONLY. An invited colleague has not arrived and a removed one has left, and
    # a directory listing either is one people stop trusting. Ordered by name, because this is a
    # list somebody scans for a person rather than a ranking.
    #
    # The whole roster is visible to the whole workspace -- that is what a staff directory is --
    # but only the fields a colleague needs in order to work with somebody. Nothing about their
    # account, their status history, or how they sign in.
    directory_rows = (await s.execute(select(IntranetMember).where(
        IntranetMember.tenant_id == tenant_id,
        IntranetMember.status == "Active",
    ).order_by(IntranetMember.full_name))).scalars().all()
    role_names = {r.id: r.name for r in roles}
    leadership = {r.id for r in roles if r.is_leadership}

    # ── the workspace's own pages ────────────────────────────────────────────────────────
    pages = (await s.execute(select(IntranetPage).where(
        IntranetPage.tenant_id == tenant_id,
        IntranetPage.active.is_(True),
        published(IntranetPage),
    ).order_by(IntranetPage.sort, IntranetPage.title))).scalars().all()

    # Same audience rule as tiles and courses: no rows means everyone.
    page_audience: dict = {}
    if pages:
        for page_id, role_id in (await s.execute(select(
            IntranetPageRole.page_id, IntranetPageRole.role_id,
        ).where(IntranetPageRole.tenant_id == tenant_id,
                IntranetPageRole.page_id.in_([x.id for x in pages])))).all():
            page_audience.setdefault(page_id, set()).add(role_id)
    pages = [x for x in pages
             if x.id not in page_audience or my_role in page_audience[x.id]]

    sections_by_page: dict = {}
    if pages:
        for section in (await s.execute(select(IntranetPageSection).where(
            IntranetPageSection.tenant_id == tenant_id,
            IntranetPageSection.page_id.in_([x.id for x in pages]),
            published(IntranetPageSection),
        ).order_by(IntranetPageSection.sort))).scalars().all():
            sections_by_page.setdefault(section.page_id, []).append(section)

    sop_categories = (await s.execute(select(IntranetSopCategory).where(
        IntranetSopCategory.tenant_id == tenant_id, published(IntranetSopCategory),
    ).order_by(IntranetSopCategory.sort, IntranetSopCategory.name))).scalars().all()

    sops = [] if not allows(levels, "sop_library") else (await s.execute(
        select(IntranetSop).where(
            IntranetSop.tenant_id == tenant_id,
            IntranetSop.state == "Live",
            published(IntranetSop),
        ).order_by(IntranetSop.title))).scalars().all()

    category_names = {c.id: c.name for c in sop_categories}

    # The CURRENT version of each SOP, and who owns it. Both were authored by the console from
    # the start and dropped here, which is why the member's SOP library could only ever be a
    # list of titles -- no version to cite, no owner to ask, no file to open.
    versions = {}
    owner_names = {}
    version_ids: list = []
    if sops:
        version_ids = [sop.current_version_id for sop in sops if sop.current_version_id]
        if version_ids:
            versions = {v.id: v for v in (await s.execute(select(IntranetSopVersion).where(
                IntranetSopVersion.tenant_id == tenant_id,
                IntranetSopVersion.id.in_(version_ids)))).scalars().all()}
        owner_ids = [sop.owner_member_id for sop in sops if sop.owner_member_id]
        if owner_ids:
            owner_names = {m.id: m.full_name for m in (await s.execute(select(IntranetMember).where(
                IntranetMember.tenant_id == tenant_id,
                IntranetMember.id.in_(owner_ids)))).scalars().all()}

    # What THIS member has already acknowledged. The console has counted these since it shipped
    # and the table had never had a row inserted, because no member route wrote one -- the
    # portal's tick box was writing to a local state blob nobody else could see.
    acknowledged: dict = {}
    if version_ids and member is not None:
        for version_id, at in (await s.execute(select(
            IntranetSopAcknowledgement.sop_version_id,
            IntranetSopAcknowledgement.acknowledged_at,
        ).where(IntranetSopAcknowledgement.tenant_id == tenant_id,
                IntranetSopAcknowledgement.member_id == member.id,
                IntranetSopAcknowledgement.sop_version_id.in_(version_ids)))).all():
            acknowledged[version_id] = at


    def _sop_out(sop) -> dict:
        version = versions.get(sop.current_version_id)
        # Per VERSION, not per SOP: acknowledging v2 says nothing about v3, and the table is
        # keyed that way on purpose. Republishing a procedure correctly asks everybody again.
        acked = acknowledged.get(sop.current_version_id)
        return {
            "id": str(sop.id),
            "title": sop.title,
            "category": category_names.get(sop.category_id),
            "owner": owner_names.get(sop.owner_member_id),
            "updated_at": _iso(sop.updated_at),
            "review_due_on": sop.review_due_on.isoformat() if sop.review_due_on else None,
            "version": version.version_label if version else None,
            "filename": version.filename if version else None,
            "byte_size": int(version.byte_size) if version else None,
            # A path under the API base, not a storage URL: the bytes are proxied so that a
            # link cannot outlive the reader's access to the workspace.
            "file_url": f"/intranet/sops/{sop.id}/file" if version else None,
            "acknowledged_at": _iso(acked),
        }

    # Which providers this workspace has actually connected. Keys and status only -- no
    # credentials, no base URLs, nothing an agent has any reason to see. Vendor-specific surfaces
    # (the Sunburst panel is one) are driven by this rather than being compiled in, because a
    # coaching product one customer buys is not a feature of the platform.
    integrations = {
        row.provider_key: row.status
        for row in (await s.execute(select(IntranetIntegration).where(
            IntranetIntegration.tenant_id == tenant_id))).scalars().all()
    }
    # Sisu and Follow Up Boss are connected on the DASHBOARD, and that connection wins. A
    # workspace with live production numbers on one surface and "not connected" on the other is
    # the same customer being asked the same question twice and getting two answers.
    integrations.update(await dashboard_connections(s, tenant_id))

    # THE LIST'S OWN LINK, resolved here. The console authors a provider plus an
    # external_list_id per list; the portal was reading an older `config.links.fub_lists` map
    # keyed by hardcoded list names, so a workspace could fill in every list id in the console
    # and the portal would still show "No URL configured". Two mechanisms for one thing, and the
    # one the console writes was the one nothing read.
    #
    # Joined server-side rather than handing over provider base URLs: the integrations map stays
    # keys-and-status, and the browser gets a finished link or nothing.
    provider_bases = {
        row.provider_key: (row.base_url or "").strip()
        for row in (await s.execute(select(IntranetIntegration).where(
            IntranetIntegration.tenant_id == tenant_id))).scalars().all()
        if (row.base_url or "").strip()
    }

    def _list_url(item) -> str | None:
        base = provider_bases.get(item.provider or "")
        ref = (item.external_list_id or "").strip()
        if not base or not ref:
            return None
        return f"{base.rstrip('/')}/{ref}"

    # Grouped exactly as the launchpad renders them, so the browser does no grouping of its own.
    groups: dict = {}
    for tile in visible:
        groups.setdefault(tile.tile_group or "Tools", []).append(
            {"key": str(tile.id), "name": tile.name, "url": tile.url,
             "auth_type": tile.auth_type})

    return {
        "roles": [{"key": r.key, "name": r.name, "is_leadership": bool(r.is_leadership)}
                  for r in roles],
        "my_role": next((r.key for r in roles if member is not None and r.id == member.role_id),
                        None),
        "tool_groups": [{"id": name.lower().replace(" ", "_"), "label": name, "tools": items}
                        for name, items in groups.items()],
        "wtd_lists": [{"id": str(w.id), "name": w.name, "script_name": w.script_name,
                       "daily_target": w.daily_target, "provider": w.provider,
                       "url": _list_url(w)}
                      for w in wtd],
        # WIDER THAN A TITLE, because a title is not a course. Everything below is already
        # authored in the console and was being dropped here, which is why the portal fell back
        # to a compiled-in list: there was nothing in the payload to render. `source_type` and
        # `source_ref` are what let a lesson actually play.
        "courses": [{"id": str(c.id), "title": c.title, "category": c.category,
                     "description": c.description,
                     "required_for_onboarding": bool(c.required_for_onboarding),
                     "sequential": bool(c.sequential),
                     "track_progress": bool(c.track_progress),
                     "issues_certificate": bool(c.issues_certificate),
                     "lessons": [{"id": str(le.id), "title": le.title,
                                  "source_type": le.source_type, "source_ref": le.source_ref,
                                  "source_label": le.source_label,
                                  "duration_minutes": le.duration_minutes,
                                  "required": bool(le.required)}
                                 for le in lessons_by_course.get(c.id, [])]}
                    for c in courses],
        "sops": [_sop_out(sop) for sop in sops],
        "directory": [{"id": str(m.id), "name": m.full_name, "title": m.title,
                       "role": role_names.get(m.role_id), "market": m.market,
                       "email": m.email, "phone": m.phone, "bio": m.bio, "owns": m.owns,
                       "is_leadership": m.role_id in leadership,
                       "photo_url": (f"/intranet/directory/{m.id}/photo" if m.photo_key else None)}
                      for m in directory_rows],
        # Pages this workspace wrote for itself. The rail builds its own entries from these, so
        # a page that is unpublished, inactive, or not for this role never reaches the browser
        # at all rather than being hidden once it gets there.
        "pages": [{"key": x.key, "title": x.title, "subtitle": x.subtitle,
                   "nav_group": x.nav_group, "sort": x.sort,
                   "sections": [{"id": str(sec.id), "heading": sec.heading, "body": sec.body,
                                 "links": list(sec.links or [])}
                                for sec in sections_by_page.get(x.id, [])]}
                  for x in pages],
        "integrations": integrations,
        # Reported as well as enforced. The server is the gate -- everything above is already
        # filtered -- but a rail that offers Win the Day to somebody it will then refuse is a
        # worse experience than one that does not show it, and only the client can hide a link.
        "capabilities": levels,
    }


def _logo_url(tenant: Tenant, workspace: IntranetWorkspace | None,
              kind: str, field: str) -> str | None:
    """A public URL for one of the workspace's marks, or None if it has not uploaded one.

    Relative to the API base, which the browser already knows -- the same shape as an SOP's
    file_url, rather than a second way of naming the API.

    FINGERPRINTED, because the path is stable while the file behind it is not. The asset route
    answers with a year-long immutable cache -- correct for an image, wrong for a URL that keeps
    pointing at whatever was uploaded last -- so `?v=` changes when the stored key changes and a
    re-uploaded logo appears immediately instead of a year from now.
    """
    key = getattr(workspace, field, None) if workspace is not None else None
    if not key or not tenant or not tenant.slug:
        return None
    version = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"/public/brand/{tenant.slug}/{kind}?v={version}"


def _config_out(tenant: Tenant, user: User,
                marketing: IntranetMarketingSetting | None = None,
                marketing_role: str | None = None,
                content: dict | None = None,
                workspace: IntranetWorkspace | None = None) -> dict:
    config = _stored_config(tenant)
    # The capabilities the content payload already resolved -- not looked up again, so the form
    # and the endpoint cannot disagree about the same role.
    config["marketing"] = _marketing_out(
        marketing, marketing_role,
        permitted=(content or {}).get("capabilities", {}).get("marketing_requests") != "None")
    # The workspace names ITSELF. The intranet had "Utah Life" compiled into its rail, its title,
    # its assistant button and its sign-in screen, so every customer's portal would have worn the
    # first customer's name.
    config["workspace"] = {
        "name": (workspace.portal_name if workspace is not None and workspace.portal_name
                 else tenant.name or "Workspace"),
        "tagline": workspace.tagline if workspace is not None else None,
        # The marks the console has been collecting with nothing to serve them back. Absolute
        # public URLs, because the rail renders them in an <img> and an <img> sends no headers --
        # see auth.public_brand_asset for why that route is shaped the way it is.
        "logo_light_url": _logo_url(tenant, workspace, "portal_light", "logo_light_key"),
        "logo_dark_url": _logo_url(tenant, workspace, "portal_dark", "logo_dark_key"),
        "logo_mark_url": _logo_url(tenant, workspace, "portal_mark", "logo_mark_key"),
        # The five swatches the console authors. Sent as-is; the portal decides which of its
        # variables each one drives, because that mapping is a property of the portal's design
        # and not of the workspace's choice.
        "palette": (workspace.palette or {}) if workspace is not None else {},
    }
    config["content"] = content or {}
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
    member = await _member_for(s, user)
    content = await _published_content(s, user.tenant_id, member)
    workspace = (await s.execute(select(IntranetWorkspace).where(
        IntranetWorkspace.tenant_id == user.tenant_id))).scalars().first()
    return _config_out(tenant, user, marketing, role_name, content, workspace)


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
    # A role whose marketing_requests is None cannot file one. The form is hidden for them too,
    # but hiding a form is not a permission -- this is the endpoint the form posts to.
    if not allows(await capability_levels(s, user.tenant_id, member.role_id),
                  "marketing_requests"):
        raise HTTPException(403, "Your role cannot file marketing requests.")

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
        # QUEUED, not sent. Delivery runs on the worker tick rather than here, so a destination
        # that hangs cannot hold this response open -- see services/marketing_delivery.py. Setting
        # it to now rather than leaving it null is what makes "queued" distinguishable from
        # "given up", without a status column that could contradict the timestamps.
        delivery_next_attempt_at=dt.datetime.now(dt.timezone.utc),
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


async def _live_sop(s: AsyncSession, tenant_id, sop_id: uuid.UUID,
                    member: IntranetMember | None = None) -> IntranetSop:
    """A published, live, unarchived SOP this member may read, or 404.

    Checked here rather than trusted from the listing: a member who kept an id from before an SOP
    was archived must not still reach it -- and, since the SOP library is now gated by the
    permissions matrix, NOR must somebody whose role has sop_library set to None. Filtering the
    listing alone would be cosmetic: the ids are guessable from an old page, a bookmark or a
    colleague, and the file endpoint is what actually hands over the document.

    404 rather than 403, so this cannot be used to discover which SOPs exist.
    """
    if member is not None:
        levels = await capability_levels(s, tenant_id, member.role_id)
        if not allows(levels, "sop_library"):
            raise HTTPException(404, "Not found")
    row = (await s.execute(select(IntranetSop).where(
        IntranetSop.tenant_id == tenant_id,
        IntranetSop.id == sop_id,
        IntranetSop.state == "Live",
        IntranetSop.published_at.is_not(None),
        IntranetSop.archived_at.is_(None),
    ))).scalars().first()
    if row is None:
        raise HTTPException(404, "Not found")
    return row


@router.post("/sops/{sop_id}/acknowledge")
async def acknowledge_sop(sop_id: uuid.UUID, user: User = Depends(current_user),
                          s: AsyncSession = Depends(get_session)):
    """Record that this member has read the current version of this SOP.

    THE FIRST WRITE THIS TABLE HAS EVER HAD. IntranetSopAcknowledgement has been read by the
    console for a count since it shipped, and nothing inserted a row -- the portal's tick box
    wrote to a per-user state blob nobody else could see, so an admin asking "who has read the
    new procedure?" got zero for everybody, forever.

    Against the CURRENT VERSION, which is what makes it mean anything: acknowledging v2 says
    nothing about v3, so republishing a procedure asks everybody again rather than silently
    inheriting an assertion about a document that has since changed.

    One-way and idempotent. There is no un-acknowledge: the table has no revoked_at because
    saying "I have read this" is not a state you toggle, and acknowledging twice is the same
    fact, so a double-click is a no-op rather than an error.
    """
    await _enabled_tenant(s, user)
    member = await _member_for(s, user)
    if member is None:
        raise HTTPException(404, "Not found")
    sop = await _live_sop(s, user.tenant_id, sop_id, member)
    if not sop.current_version_id:
        raise HTTPException(409, "This SOP has no document to acknowledge yet.")

    existing = (await s.execute(select(IntranetSopAcknowledgement).where(
        IntranetSopAcknowledgement.tenant_id == user.tenant_id,
        IntranetSopAcknowledgement.sop_version_id == sop.current_version_id,
        IntranetSopAcknowledgement.member_id == member.id,
    ))).scalars().first()
    if existing is None:
        # acknowledged_at is left to the column's server default, so the timestamp is the
        # database's rather than this process's clock.
        existing = IntranetSopAcknowledgement(
            tenant_id=user.tenant_id, sop_version_id=sop.current_version_id,
            member_id=member.id)
        s.add(existing)
        audit(s, user.tenant_id, user.id, "intranet.sop_acknowledged", "sop", sop.id)
        await s.commit()
        await s.refresh(existing)
    return {"acknowledged_at": _iso(existing.acknowledged_at)}


@router.get("/directory/{member_id}/photo")
async def directory_photo(member_id: uuid.UUID, user: User = Depends(current_user),
                          s: AsyncSession = Depends(get_session)):
    """A colleague's photo, proxied and behind the session.

    NOT on the public asset route the logos use. A workspace's mark is public by nature -- it is
    on their sign-in screen -- and a photograph of a member of staff is not. This one requires a
    session and is scoped to the caller's own workspace, so a photo cannot be pulled by guessing
    an id from somewhere else.
    """
    await _enabled_tenant(s, user)
    if await _member_for(s, user) is None:
        raise HTTPException(404, "Not found")
    row = (await s.execute(select(IntranetMember).where(
        IntranetMember.tenant_id == user.tenant_id,
        IntranetMember.id == member_id,
        IntranetMember.status == "Active",
    ))).scalars().first()
    if row is None or not row.photo_key or not binder_storage.exists(row.photo_key):
        raise HTTPException(404, "Not found")
    data = binder_storage.read(row.photo_key)
    media = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
             "webp": "image/webp"}.get(row.photo_key.rsplit(".", 1)[-1].lower(), "image/jpeg")
    # Inline, unlike the SOP documents: this is displayed in an <img>, and its type comes from
    # the short list of image formats the console accepted on upload rather than from anything
    # the uploader declared.
    return Response(content=data, media_type=media,
                    headers={"Cache-Control": "private, max-age=300"})


@router.get("/sops/{sop_id}/file")
async def download_sop(sop_id: uuid.UUID, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session)):
    """The current version of a published SOP, proxied.

    The documents have been uploaded and stored since the console shipped, and no member route
    ever served them back -- so the SOP library was a list of titles for something nobody could
    open. This is that route.

    PUBLISHED AND LIVE ONLY, checked here rather than trusted from the listing: a member who
    kept an id from before an SOP was archived must not still be able to pull the file. Served
    as an attachment with the stored content type, and only ever the CURRENT version, so a stale
    link cannot be used to read a procedure that has since been replaced.
    """
    await _enabled_tenant(s, user)
    member = await _member_for(s, user)
    if member is None:
        raise HTTPException(404, "Not found")
    row = await _live_sop(s, user.tenant_id, sop_id, member)
    if not row.current_version_id:
        raise HTTPException(404, "Not found")
    version = (await s.execute(select(IntranetSopVersion).where(
        IntranetSopVersion.tenant_id == user.tenant_id,
        IntranetSopVersion.id == row.current_version_id,
    ))).scalars().first()
    if version is None or not binder_storage.exists(version.storage_key):
        raise HTTPException(404, "Not found")
    safe = binder_storage.safe_filename(version.filename)
    return Response(
        content=binder_storage.read(version.storage_key),
        media_type=version.content_type,
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
