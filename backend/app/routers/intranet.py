from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import uuid

from fastapi import (APIRouter, Body, Depends, File, Form, HTTPException, Query, Response,
                     UploadFile)
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from .. import plans
from ..db import get_session
from ..deps import current_user, require_role
from ..models import (IntranetCourse, IntranetLaunchpadTile, IntranetLaunchpadTileRole,
                      IntranetLesson, IntranetMarketingAttachment, IntranetMarketingRequest,
                      IntranetMarketingSetting, IntranetMember, IntranetRole, IntranetSop,
                      IntranetCourseRole, IntranetCourseSection, IntranetCourseEnrolment,
                      IntranetSopAcknowledgement, IntranetSopVersion,
                      IntranetPage, IntranetPageRole, IntranetPageSection,
                      IntranetIntegration, IntranetLessonAttachment,
                      IntranetSopCategory, IntranetUserState,
                      IntranetAiQuestion, IntranetContentGap, IntranetWorkspace,
                      IntranetSopSuggestion,
                      IntranetWtdList, IntranetWtdPlaybook, IntranetWtdScript,
                      IntranetDirectorySetting, Tenant, User,
                      Agent)
from ..services import (binder_storage, course_sections, follow_ups, intranet_assistant,
                        lesson_media, lesson_richtext, mailer, member_numbers, sop_library,
                        sunburst, uploads, whos_who, wtd_playbook)
from ..services.inheritance import dashboard_connections
from ..services.intranet_permissions import DENIED, allows, capability_levels
from ..services.users import primary_host
from ..services.audit import audit

def _iso(value) -> str | None:
    """A timestamp the browser can parse, or nothing. Never a naive string."""
    return value.isoformat() if value is not None else None


router = APIRouter(prefix="/intranet", tags=["intranet"])


def published(model):
    """Whether a row is visible to members: published at least once, and NOT ARCHIVED.

    Was a lambda local to _published_content, which meant the handout download route -- which has
    to apply exactly the same test, or unpublished means "unlisted" rather than "unavailable" --
    could not see it at all. One definition, so a second read path cannot quietly use a different
    rule.

    ARCHIVED ROWS ARE EXCLUDED HERE, derived from the model rather than listed. Archiving a course
    sets `archived_at` and leaves `state` alone -- courses have no Archived state -- and nothing on
    this side ever read that column. So a course archived in the console stayed Live and
    published: still in the library, in search and in the assistant's corpus, and its files still
    downloadable. SOPs escaped only because their archive ALSO flips `state`. A model that grows an
    `archived_at` is covered the day it does, without anybody remembering to come back here.
    """
    clause = model.published_at.is_not(None)
    archived = getattr(model, "archived_at", None)
    if archived is not None:
        clause = and_(clause, archived.is_(None))
    return clause

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
    # No `sunburst`. It ships with the platform and its link is derived per member -- see
    # services/sunburst. There is nothing for a workspace to set.
    numbers: dict | None = None


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
        # WAS FIVE ZEROES, identical for every workspace and computed from nothing -- so every
        # agent who opened My Numbers saw a page of noughts. The live figures come from
        # services/member_numbers now; what stays here is the one thing a workspace TYPES rather
        # than syncs: the annual unit goal a pace is measured against.
        "numbers": {
            "annual_unit_goal": 0,
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


async def _course_audience(s: AsyncSession, tenant_id, course_ids: list) -> dict:
    """course_id -> the set of roles it is restricted to. Absent means "not restricted"."""
    out: dict = {}
    if not course_ids:
        return out
    for course_id, role_id in (await s.execute(select(
        IntranetCourseRole.course_id, IntranetCourseRole.role_id,
    ).where(IntranetCourseRole.tenant_id == tenant_id,
            IntranetCourseRole.course_id.in_(course_ids)))).all():
        out.setdefault(course_id, set()).add(role_id)
    return out


def _resolved_sections(course, sections_by_course: dict, lessons_by_course: dict,
                       done_lessons: set, started: dict, today) -> list[dict]:
    """This course's sections as THIS member sees them: labels, dates, and what is open.

    Counted from the lessons the portal is actually being shown, not from a stored total: a lesson
    still in draft is not in the payload, and a section reading "2 of 5 done" against three
    lessons nobody can see would be a course that could never be finished.
    """
    sections = sections_by_course.get(course.id) or []
    if not sections:
        return []
    counts: dict = {}
    for lesson in lessons_by_course.get(course.id, []):
        if lesson.section_id is None:
            continue
        done, total = counts.get(str(lesson.section_id), (0, 0))
        counts[str(lesson.section_id)] = (done + (1 if str(lesson.id) in done_lessons else 0),
                                          total + 1)
    return course_sections.resolve(
        sections, scheme=course.grouping_scheme, lock_sections=bool(course.lock_sections),
        started_on=started.get(course.id), today=today, done_counts=counts)


def _audience_allows(audience: dict, course_id, role_id) -> bool:
    """Absence means "not restricted" -- what an admin who never opened the role picker intends.

    Extracted so the LISTING and the handout download decide this the same way. They used to be
    one inline expression and one route that did not ask at all; two copies of a permission rule
    is how a course restricted to Team Leaders ends up with handouts anybody can pull.
    """
    return course_id not in audience or role_id in audience[course_id]


async def _published_content(s: AsyncSession, tenant_id, member: IntranetMember | None,
                             user: User | None = None, *, full: bool = False) -> dict:
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

    wtd_allowed = allows(levels, "wtd")
    wtd = [] if not wtd_allowed else (await s.execute(select(IntranetWtdList).where(
        IntranetWtdList.tenant_id == tenant_id,
        IntranetWtdList.active.is_(True),
        published(IntranetWtdList),
    ).order_by(IntranetWtdList.position))).scalars().all()
    # The playbook around the lists, and the scripts they name: published, like the lists.
    wtd_book = None if not wtd_allowed else (await s.execute(select(IntranetWtdPlaybook).where(
        IntranetWtdPlaybook.tenant_id == tenant_id,
        published(IntranetWtdPlaybook)))).scalars().first()
    wtd_scripts = [] if not wtd_allowed else (await s.execute(select(IntranetWtdScript).where(
        IntranetWtdScript.tenant_id == tenant_id,
        IntranetWtdScript.active.is_(True),
        published(IntranetWtdScript),
    ).order_by(IntranetWtdScript.position, IntranetWtdScript.name))).scalars().all()

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
    course_audience = await _course_audience(s, tenant_id, [c.id for c in courses])
    courses = [c for c in courses if _audience_allows(course_audience, c.id, my_role)]

    # ── sections, and where this member stands in them ───────────────────────────────────
    # Published like every other content type: a section drafted in the console is not a section
    # in the portal until somebody presses Publish.
    sections_by_course: dict = {}
    if courses:
        for section in (await s.execute(select(IntranetCourseSection).where(
            IntranetCourseSection.tenant_id == tenant_id,
            IntranetCourseSection.course_id.in_([c.id for c in courses]),
            published(IntranetCourseSection),
        ).order_by(IntranetCourseSection.sort))).scalars().all():
            sections_by_course.setdefault(section.course_id, []).append(section)

    # WHEN each course started, for this member. Nothing is created here -- this is a GET, and a
    # course a member has never opened has no enrolment. `POST /courses/{id}/start` writes one.
    started: dict = {}
    if courses and user is not None:
        for row in (await s.execute(select(IntranetCourseEnrolment).where(
            IntranetCourseEnrolment.tenant_id == tenant_id,
            IntranetCourseEnrolment.user_id == user.id,
        ))).scalars().all():
            started[row.course_id] = row.started_at.date() if row.started_at else None

    # Completion, from the member's own training state -- the same blob the portal writes when
    # somebody ticks a lesson. Read here so section state is decided ONCE, on the server, rather
    # than by two frontends each reimplementing "is this section done".
    done_lessons: set = set()
    if user is not None:
        state_row = (await s.execute(select(IntranetUserState).where(
            IntranetUserState.tenant_id == tenant_id,
            IntranetUserState.user_id == user.id,
            IntranetUserState.scope == "training",
        ))).scalars().first()
        marked = ((state_row.value if state_row else None) or {}).get("done") or {}
        done_lessons = {str(k) for k, v in marked.items() if v}

    # The workspace's own day. A due date is a promise made in the office's calendar: at 6pm
    # Mountain the UTC date has already turned over, and a member would be told they were overdue
    # during the afternoon they were given.
    ws_row = (await s.execute(select(IntranetWorkspace).where(
        IntranetWorkspace.tenant_id == tenant_id))).scalars().first()
    today = course_sections.workspace_today(ws_row.timezone if ws_row else None)

    # Ungrouped lessons first, then section by section -- by each section's POSITION, never by
    # section_id, which is a UUID and would order the sections by random hex.
    for course_id, group in lessons_by_course.items():
        lessons_by_course[course_id] = course_sections.order_lessons(
            group, sections_by_course.get(course_id, []))

    # ── who's who (services/whos_who) ────────────────────────────────────────────────────
    # EVERYONE ON THE ROSTER, not everyone who has signed in (D1). This was Active only, so a team
    # added quietly with held invites showed nobody -- and a colleague is a colleague before they
    # accept a portal invite. Removed people are gone; Hidden people are left out on purpose.
    #
    # The whole roster is visible to the whole workspace -- that is what a staff directory is --
    # but only the fields a colleague needs in order to work with somebody. Nothing about their
    # account, their status history, or how they sign in. Bios and contact details are on the
    # profile route, not in this payload.
    directory_rows = (await s.execute(select(IntranetMember).where(
        IntranetMember.tenant_id == tenant_id,
        IntranetMember.status != "Removed",
    ).order_by(IntranetMember.full_name))).scalars().all()
    role_names = {r.id: r.name for r in roles}
    leadership = {r.id for r in roles if r.is_leadership}
    dir_setting = (await s.execute(select(IntranetDirectorySetting).where(
        IntranetDirectorySetting.tenant_id == tenant_id))).scalars().first()

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
    owner_cards: dict = {}
    version_ids: list = []
    if sops:
        version_ids = [sop.current_version_id for sop in sops if sop.current_version_id]
        if version_ids:
            versions = {v.id: v for v in (await s.execute(select(IntranetSopVersion).where(
                IntranetSopVersion.tenant_id == tenant_id,
                IntranetSopVersion.id.in_(version_ids)))).scalars().all()}
        owner_ids = [sop.owner_member_id for sop in sops if sop.owner_member_id]
        if owner_ids:
            owner_rows = (await s.execute(select(IntranetMember).where(
                IntranetMember.tenant_id == tenant_id,
                IntranetMember.id.in_(owner_ids)))).scalars().all()
            owner_names = {m.id: m.full_name for m in owner_rows}
            # The owner as Who's Who knows them, so the library can show a face and the
            # procedure can offer Send a Message. Somebody hidden from the directory keeps
            # their name here -- they own the procedure either way -- but no profile to open.
            owner_cards = {
                m.id: {**whos_who.card(m, role_name=role_names.get(m.role_id), you=False),
                       "profile_url": (f"/directory/{m.id}" if whos_who.listed(m, leadership)
                                       else None)}
                for m in owner_rows}

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
        document = bool(version is not None and version.storage_key)
        card = sop_library.card(
            sop, category_name=category_names.get(sop.category_id),
            owner=owner_cards.get(sop.owner_member_id), version=version,
            acknowledged_at=_iso(acked), document=document)
        body = sop.published_body or {}
        return {
            **card,
            # The old shape, still here because the search index, the assistant and anything
            # else that learned these names keeps working.
            "category": card["department"],
            "updated_at": _iso(sop.updated_at),
            "review_due_on": sop.review_due_on.isoformat() if sop.review_due_on else None,
            "filename": version.filename if version else None,
            "byte_size": int(version.byte_size) if version and version.byte_size else None,
            # A path under the API base, not a storage URL: the bytes are proxied so that a
            # link cannot outlive the reader's access to the workspace.
            "file_url": f"/intranet/sops/{sop.id}/file" if document else None,
            # The step TITLES, so searching "order media" finds the procedure that says it. The
            # step text itself is on the procedure's own page, which is what keeps this payload
            # the size of a library rather than the size of the library's contents.
            "steps": [step.get("title") for step in (body.get("steps") or []) if step.get("title")],
            "body_text": sop_library.body_text(body) if full else None,
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
    # Follow Up Boss says which account a key opens (services/fub_sync reads /identity), and a
    # smart list lives at a fixed address inside it -- so a list links without anybody typing a
    # base URL. It had to be typed, and the live workspace's was empty. A URL an admin did set
    # still wins.
    if not provider_bases.get("follow_up_boss"):
        fub_base = await follow_ups.fub_list_base(s, tenant_id)
        if fub_base:
            provider_bases["follow_up_boss"] = fub_base

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

    # Handouts for every lesson in one query rather than per lesson. `published` is applied here
    # like every other content type -- a draft handout on a live lesson would otherwise be visible
    # the moment it was saved, which is the bug draft lessons already had once.
    attachments_by_lesson: dict = {}
    lesson_ids = [le.id for group in lessons_by_course.values() for le in group]
    if lesson_ids:
        for att in (await s.execute(
            select(IntranetLessonAttachment)
            .where(IntranetLessonAttachment.tenant_id == tenant_id,
                   IntranetLessonAttachment.lesson_id.in_(lesson_ids),
                   published(IntranetLessonAttachment))
            .order_by(IntranetLessonAttachment.sort)
        )).scalars().all():
            attachments_by_lesson.setdefault(att.lesson_id, []).append(att)

    sop_cards = [_sop_out(sop) for sop in sops]
    return {
        "roles": [{"key": r.key, "name": r.name, "is_leadership": bool(r.is_leadership)}
                  for r in roles],
        "my_role": next((r.key for r in roles if member is not None and r.id == member.role_id),
                        None),
        # Whether this person has a roster entry at all, and so whether the emptiness they may be
        # looking at is a restriction or an absence. A course or a tile with an audience is hidden
        # from somebody with no role, which is correct and invisible: the portal says which it is
        # rather than showing empty shelves to somebody who was never added.
        "on_roster": member is not None,
        "tool_groups": [{"id": name.lower().replace(" ", "_"), "label": name, "tools": items}
                        for name, items in groups.items()],
        # THE PAGE, READY TO DRAW (services/wtd_playbook): counts filled in, lists numbered,
        # grouped and linked, scripts joined, and this person's targets for today worked out --
        # on the server, so the on-ramp is counted in the office's calendar, not the browser's.
        # None when this role may not see Win the Day, rather than an empty page to hide.
        "wtd": (wtd_playbook.resolve(
            wtd_book.content if wtd_book is not None else None,
            lists=wtd, scripts=wtd_scripts, list_url=_list_url,
            list_base=provider_bases.get("follow_up_boss"),
            started_on=member.started_on if member is not None else None,
            personal=member.wtd_goals if member is not None else None,
            today=today) if wtd_allowed else None),
        # WIDER THAN A TITLE, because a title is not a course. Everything below is already
        # authored in the console and was being dropped here, which is why the portal fell back
        # to a compiled-in list: there was nothing in the payload to render. `source_type` and
        # `source_ref` are what let a lesson actually play.
        # `media` and `total_duration_minutes` are DERIVED here rather than in the browser: both
        # are facts about the course's lessons, the library card and any future screen want the
        # same answer, and the badge vocabulary already lives in lesson_media.
        "courses": [{"id": str(c.id), "title": c.title, "category": c.category,
                     "description": c.description,
                     "media": lesson_media.course_media(
                         [le.source_type for le in lessons_by_course.get(c.id, [])],
                         [le.kind for le in lessons_by_course.get(c.id, [])]),
                     "total_duration_minutes": course_sections.total_minutes(
                         lessons_by_course.get(c.id, [])),
                     "required_for_onboarding": bool(c.required_for_onboarding),
                     "sequential": bool(c.sequential),
                     "track_progress": bool(c.track_progress),
                     "issues_certificate": bool(c.issues_certificate),
                     # SECTIONS ARE RESOLVED PER MEMBER. The same section is open for one agent
                     # and locked for another, because "Day 3" is counted from the day each of
                     # them started -- so this cannot be a property of the course row and cannot
                     # be worked out in the browser.
                     "grouping_scheme": c.grouping_scheme or "none",
                     "lock_sections": bool(c.lock_sections),
                     "sections": _resolved_sections(c, sections_by_course, lessons_by_course,
                                                    done_lessons, started, today),
                     "enrolled_on": started.get(c.id).isoformat()
                                    if started.get(c.id) else None,
                     "day_number": course_sections.day_number(started.get(c.id), today),
                     "lessons": [{"id": str(le.id), "title": le.title,
                                  "section_id": str(le.section_id) if le.section_id else None,
                                  "kind": le.kind or "video",
                                  "source_type": le.source_type, "source_ref": le.source_ref,
                                  "source_label": le.source_label,
                                  "description": le.description,
                                  # The byline the player shows under the title.
                                  "taught_by": le.taught_by,
                                  "duration_minutes": le.duration_minutes,
                                  # Sanitized on the way out as well as on the way in: the
                                  # allowlist can change, and a body stored under yesterday's
                                  # rules must not keep whatever yesterday allowed.
                                  "body_html": (lesson_richtext.image_urls(
                                      lesson_richtext.sanitize(
                                          le.body_html, tenant_id=tenant_id),
                                      lesson_richtext.PORTAL_IMAGE_ROUTE) or None
                                      if (le.kind or "video") == "reading" else None),
                                  "word_count": le.word_count,
                                  "read_minutes": le.read_minutes,
                                  "page_count": le.page_count,
                                  "duration": course_sections.duration_of(
                                      le.kind, duration_minutes=le.duration_minutes,
                                      read_minutes=le.read_minutes, page_count=le.page_count),
                                  "required": bool(le.required),
                                  # Resolved server-side: whether this source can be played in
                                  # the page is a fact about the URL, not a rendering choice, and
                                  # framing a logged-in platform produces a refusal rather than a
                                  # video. See services/lesson_media. None for a reading lesson,
                                  # which has no source -- without that guard the empty
                                  # `source_ref` resolves to a "no source attached yet" card on
                                  # a finished article.
                                  "player": lesson_media.resolve(le.source_type, le.source_ref,
                                                                 kind=le.kind),
                                  "attachments": [
                                      {"id": str(a.id), "title": a.title, "kind": a.kind,
                                       "note": a.note,
                                       # A file is fetched through our own authenticated route;
                                       # only a link is handed over as the author typed it.
                                       "url": (a.url if a.kind == "link"
                                               else f"/intranet/lessons/{le.id}/attachments/{a.id}"),
                                       "content_type": a.content_type,
                                       "byte_size": a.byte_size}
                                      for a in attachments_by_lesson.get(le.id, [])]}
                                 for le in lessons_by_course.get(c.id, [])]}
                    for c in courses],
        "sops": sop_cards,
        # The rail down the left of the library: every department, in the order the console put
        # them in, each with how many procedures are in it.
        "sop_departments": sop_library.departments(
            sop_cards, [c.name for c in sop_categories]) if sop_cards else [],
        "directory": await _directory_payload(s, tenant_id, directory_rows, dir_setting,
                                              role_names, leadership, member, today),
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
                workspace: IntranetWorkspace | None = None,
                numbers: dict | None = None,
                member_id=None) -> dict:
    config = _stored_config(tenant)
    # The capabilities the content payload already resolved -- not looked up again, so the form
    # and the endpoint cannot disagree about the same role.
    # The member's own figures and, where their role allows, the team's -- computed by the caller
    # (this is a serializer, and a serializer that opens queries is one nobody can call from a
    # test). Pace stays the member's own: the stored goal is measured against one agent's
    # closings, and nothing defines a team target yet.
    stored_numbers = config.get("numbers") or {}
    goal = stored_numbers.get("annual_unit_goal") or 0
    own = (numbers or {}).get("own")
    as_of = (numbers or {}).get("as_of")
    config["numbers"] = {
        **stored_numbers,
        **(numbers or {}),
        "annual_unit_goal": goal,
        "pace_percent": (member_numbers.pace(own["closed_units_ytd"], goal,
                                             dt.date.fromisoformat(as_of) if as_of else None)
                         if own else None),
    }
    # Derived per member and handed over ready to use, so the portal never builds a URL and there
    # is nothing for an admin to configure. A member-less viewer (an owner not on the roster) gets
    # no link rather than somebody else's conversation.
    # NOT DURING AN ACUMYN SUPPORT VIEW of somebody's portal. Their link opens their own coaching
    # conversation inside Sisu, which is theirs -- a view of their portal must not be a way into
    # it -- so the links are withheld and the portal says why instead of offering a dead button.
    viewing = bool(getattr(user, "view_as", None))
    config["sunburst"] = {
        "url": (sunburst.link_for(tenant.id, member_id) if member_id and not viewing else ""),
        # Where a question goes. The portal adds the question, because the Ask box sends whatever
        # somebody typed; "" while Sisu's question link is not live on the configured host.
        "ask_url": (sunburst.ask_url() if member_id and not viewing else ""),
        "carries_prompt": sunburst.carries_prompt(),
        "view_as": viewing,
    }
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


def _may_see_team(levels: dict, member: IntranetMember | None, user: User) -> bool:
    """Whether this person is shown the TEAM's production as well as their own.

    The workspace already decided this. `team_production` -- "Everyone else's numbers" -- has been
    in the permissions matrix since the console shipped, Full for Owner and Manager and None for
    Member, and nothing read it: an owner who does not sell opened My Numbers and saw nothing.

    STRICTER THAN `allows()`, deliberately. Elsewhere a missing permission row means permitted,
    because guessing wrong costs somebody a course list. Here it would show every agent everyone
    else's production. Both bootstraps write a level for every role and the console cannot create
    a role, so no real workspace loses anything by requiring one.

    Somebody with no roster entry has no role to ask, so their account decides: the workspace's
    owners and admins see the team and nobody else does. A removed member keeps a role but not
    the view.
    """
    if member is None:
        return user.role in ("owner", "admin")
    if member.status == "Removed":
        return False
    return levels.get("team_production", DENIED) != DENIED


async def _member_config(s: AsyncSession, tenant: Tenant, user: User) -> dict:
    """The portal payload for this person. GET and PATCH both answer with it -- see patch_config."""
    # Read-only: the console owns this row and creates it. A tenant that has never opened the
    # Marketing Requests screen has no row, and None is the honest answer for that -- creating one
    # here would write to the database on a GET.
    marketing = await s.get(IntranetMarketingSetting, user.tenant_id)
    role_name = None
    if marketing is not None and marketing.default_role_id is not None:
        role = await s.get(IntranetRole, marketing.default_role_id)
        role_name = role.name if role is not None and role.tenant_id == user.tenant_id else None
    member = await _member_for(s, user)
    content = await _published_content(s, user.tenant_id, member, user)
    workspace = (await s.execute(select(IntranetWorkspace).where(
        IntranetWorkspace.tenant_id == user.tenant_id))).scalars().first()
    # Per request rather than cached: a stale copy of somebody's own numbers is the kind of wrong
    # that makes people stop trusting the page. Counted to the workspace's own day, as sections
    # are -- at 6pm Mountain the UTC date has already turned over.
    today = course_sections.workspace_today(workspace.timezone if workspace is not None else None)
    numbers = await member_numbers.numbers_for(
        s, user.tenant_id, member, today,
        team=_may_see_team(content.get("capabilities") or {}, member, user))
    return _config_out(tenant, user, marketing, role_name, content, workspace, numbers,
                       member.id if member is not None else None)


@router.get("/config")
async def get_config(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    tenant = await _enabled_tenant(s, user)
    return await _member_config(s, tenant, user)


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
    if "numbers" in fields:
        incoming = fields["numbers"] or {}
        nums = dict(current.get("numbers") or {})
        if "annual_unit_goal" in incoming:
            try:
                goal = int(incoming.get("annual_unit_goal") or 0)
            except (TypeError, ValueError):
                goal = 0
            # The only stored number left. Everything else on this block is computed from the
            # syncs; a goal is the one figure a team decides rather than earns.
            nums["annual_unit_goal"] = max(0, min(goal, 10000))
        current["numbers"] = nums
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
    # The WHOLE payload, as GET sends it. This answered `_config_out(tenant, user)` -- no content,
    # no numbers -- and the portal replaces its config with whatever comes back, so saving the
    # calendar URL emptied every production figure and the permissions the rail filters on until
    # the next reload.
    return await _member_config(s, tenant, user)


# The fields a workspace may demand, mirroring MARKETING_FIELDS in the console router. Kept as
# its own constant rather than imported: this router validates what a SUBMITTER sends, the console
# validates what an ADMIN requires, and the two lists agreeing is asserted by a test rather than
# by a shared import that would let one quietly follow the other.
# UPLOAD POLICY lives in services/uploads now -- the console uploads lesson handouts through the
# same allowlist and the same sniffer, and two copies of an allowlist is how one of them quietly
# gains an extension the other refuses. Re-exported under the old names so nothing that reads
# them from this module has to move.
ATTACHMENT_TYPES = uploads.ATTACHMENT_TYPES
MAX_ATTACHMENT_BYTES = uploads.MAX_ATTACHMENT_BYTES
MAX_ATTACHMENTS = uploads.MAX_ATTACHMENTS
sniff_attachment = uploads.sniff_attachment


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
    # An Acumyn support view of the portal names the roster entry exactly (deps._viewing_as); an
    # address match could find a different row for a re-used address.
    viewing = getattr(user, "view_as_member_id", None)
    if viewing is not None:
        return (await s.execute(select(IntranetMember).where(
            IntranetMember.tenant_id == user.tenant_id,
            IntranetMember.id == viewing))).scalars().first()
    email = (user.email or "").strip().lower()
    return (await s.execute(select(IntranetMember).where(
        IntranetMember.tenant_id == user.tenant_id,
        or_(IntranetMember.user_id == user.id, IntranetMember.email == email),
    ))).scalars().first()


@router.get("/follow-ups")
async def get_follow_ups(agent: str | None = Query(None, max_length=64),
                         user: User = Depends(current_user),
                         s: AsyncSession = Depends(get_session)):
    """Needs You Today and the Follow-ups page: who is waiting on this person, and -- where their
    role allows -- on the team. See services/follow_ups for the rules and the six empties.

    THE PERMISSIONS ARE THE ONES THE WORKSPACE ALREADY SET. A person's own queue is part of the
    daily run, so it follows Win the Day (`wtd`); the team's follows `team_production`, "everyone
    else's numbers", through the same strict check My Numbers uses. `?agent=` opens one agent's
    queue, or "unassigned", and needs the team permission and an agent of THIS workspace.
    """
    tenant = await _enabled_tenant(s, user)
    member = await _member_for(s, user)
    levels = await capability_levels(s, tenant.id, member.role_id if member is not None else None)
    team_ok = _may_see_team(levels, member, user)
    viewing = None
    if agent:
        if not team_ok:
            raise HTTPException(403, "Your role does not include the team's follow-ups.")
        if agent == "unassigned":
            viewing = "unassigned"
        else:
            try:
                agent_id = uuid.UUID(agent)
            except ValueError:
                raise HTTPException(404, "No such agent.")
            viewing = (await s.execute(select(Agent).where(
                Agent.id == agent_id, Agent.tenant_id == tenant.id,
                Agent.source == "fub"))).scalar_one_or_none()
            if viewing is None:
                raise HTTPException(404, "No such agent.")
    workspace = (await s.execute(select(IntranetWorkspace).where(
        IntranetWorkspace.tenant_id == tenant.id))).scalars().first()
    today = course_sections.workspace_today(workspace.timezone if workspace is not None else None)
    own_ok = allows(levels, "wtd") and (member is None or member.status != "Removed")
    return await follow_ups.payload(
        s, tenant, member, today=today, now=dt.datetime.now(dt.timezone.utc),
        own_allowed=own_ok, team_allowed=team_ok,
        show_error=user.role in ("owner", "admin"), viewing=viewing)


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


@router.get("/sops/{sop_id}")
async def read_sop(sop_id: uuid.UUID, user: User = Depends(current_user),
                   s: AsyncSession = Depends(get_session)):
    """One procedure, as its own page draws it (SOP-LIBRARY-SPEC.md 1.2).

    The PUBLISHED body, never the draft, plus the tools it needs, the owner as somebody you can
    message, and how many colleagues have read this revision. Behind `_live_sop`, so an id kept
    from an archived procedure -- or held by a role that may not open the library -- is worth
    nothing.
    """
    await _enabled_tenant(s, user)
    member = await _member_for(s, user)
    if member is None:
        raise HTTPException(404, "Not found")
    sop = await _live_sop(s, user.tenant_id, sop_id, member)

    category = await s.get(IntranetSopCategory, sop.category_id) if sop.category_id else None
    version = (await s.get(IntranetSopVersion, sop.current_version_id)
               if sop.current_version_id else None)
    roles = (await s.execute(select(IntranetRole).where(
        IntranetRole.tenant_id == user.tenant_id, published(IntranetRole)))).scalars().all()
    leadership = {r.id for r in roles if r.is_leadership}
    role_names = {r.id: r.name for r in roles}

    owner = None
    if sop.owner_member_id:
        row = (await s.execute(select(IntranetMember).where(
            IntranetMember.tenant_id == user.tenant_id,
            IntranetMember.id == sop.owner_member_id))).scalars().first()
        if row is not None:
            owner = {**whos_who.card(row, role_name=role_names.get(row.role_id), you=False),
                     "profile_url": (f"/directory/{row.id}" if whos_who.listed(row, leadership)
                                     else None),
                     # Questions on this? goes straight to them, as the mockup's button does.
                     "message_url": row.message_url or (f"mailto:{row.email}" if row.email else None)}

    # THE TOOLS AS TILES THIS MEMBER MAY SEE. A tile with no audience is everyone's; one with an
    # audience belongs to those roles, exactly as the launchpad decides it -- so a restricted
    # tool cannot become a link through the back of a procedure.
    tools: list[dict] = []
    wanted = [str(t) for t in (sop.tool_ids or []) if t]
    if wanted:
        tiles = (await s.execute(select(IntranetLaunchpadTile).where(
            IntranetLaunchpadTile.tenant_id == user.tenant_id,
            IntranetLaunchpadTile.active.is_(True),
            published(IntranetLaunchpadTile)))).scalars().all()
        audience: dict = {}
        if tiles:
            for tile_id, role_id in (await s.execute(select(
                IntranetLaunchpadTileRole.tile_id, IntranetLaunchpadTileRole.role_id,
            ).where(IntranetLaunchpadTileRole.tenant_id == user.tenant_id,
                    IntranetLaunchpadTileRole.tile_id.in_([t.id for t in tiles])))).all():
                audience.setdefault(tile_id, set()).add(role_id)
        by_id = {str(t.id): t for t in tiles
                 if t.id not in audience or member.role_id in audience[t.id]}
        tools = [{"id": t, "name": by_id[t].name, "url": by_id[t].url}
                 for t in wanted if t in by_id]

    acknowledged_at = None
    acknowledged_count = 0
    if sop.current_version_id:
        rows = (await s.execute(select(IntranetSopAcknowledgement).where(
            IntranetSopAcknowledgement.tenant_id == user.tenant_id,
            IntranetSopAcknowledgement.sop_version_id == sop.current_version_id))).scalars().all()
        acknowledged_count = len(rows)
        acknowledged_at = next(
            (_iso(r.acknowledged_at) for r in rows if r.member_id == member.id), None)
    team_size = int((await s.execute(select(func.count()).select_from(IntranetMember).where(
        IntranetMember.tenant_id == user.tenant_id,
        IntranetMember.status != "Removed"))).scalar_one())

    document = None
    if version is not None and version.storage_key:
        document = {
            "filename": version.filename,
            "byte_size": int(version.byte_size) if version.byte_size else None,
            "content_type": version.content_type,
            "url": f"/intranet/sops/{sop.id}/file",
            # A PDF can be read in the page. Anything else is a download.
            "inline": version.content_type == "application/pdf",
        }

    return sop_library.reader(
        sop, category_name=category.name if category is not None else None, owner=owner,
        version=version, acknowledged_at=acknowledged_at, document=document, tools=tools,
        acknowledged_count=acknowledged_count, team_size=team_size)


@router.post("/sops/{sop_id}/suggest")
async def suggest_sop_change(sop_id: uuid.UUID, body: dict = Body(...),
                             user: User = Depends(current_user),
                             s: AsyncSession = Depends(get_session)):
    """"Something out of date? Tell the owner." (D5)

    Queued in the console under the procedure and emailed to whoever owns it. Not a comment
    thread: a procedure has one owner, and the way it changes is that they change it.
    """
    await _enabled_tenant(s, user)
    member = await _member_for(s, user)
    if member is None:
        raise HTTPException(404, "Not found")
    sop = await _live_sop(s, user.tenant_id, sop_id, member)
    text = str((body or {}).get("text") or "").strip()
    if not text:
        raise HTTPException(422, "Say what is out of date.")
    if len(text) > 1500:
        raise HTTPException(422, "At most 1500 characters.")
    row = IntranetSopSuggestion(tenant_id=user.tenant_id, sop_id=sop.id, member_id=member.id,
                                text=text, status="New")
    s.add(row)
    audit(s, user.tenant_id, user.id, "intranet.sop_suggestion", "sop", sop.id)
    await s.commit()

    owner = (await s.get(IntranetMember, sop.owner_member_id)) if sop.owner_member_id else None
    to = (getattr(owner, "email", None) or "").strip()
    if to:
        host = await primary_host(s, user.tenant_id)
        link = f"https://{host}/intranet/sops/{sop.id}"
        lines = [f"{member.full_name} says something in {sop.title} is out of date:", "",
                 text, "", f"The procedure: {link}",
                 "It is in your console under SOP Library as well."]
        note = "\n".join(lines)
        # Never raises and never blocks the member: mailer.send swallows its own failures, and
        # the suggestion is already saved either way.
        await mailer.send(to, f"A suggestion on {sop.title}",
                          "<p>" + note.replace("\n", "<br>") + "</p>", note,
                          reply_to=member.email or None,
                          idempotency_key=f"sop-suggestion-{row.id}")
    return {"filed": True}


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


async def _directory_payload(s: AsyncSession, tenant_id, rows, setting, role_names: dict,
                             leadership: set, viewer: IntranetMember | None,
                             today: dt.date) -> dict:
    """Who's Who, laid out as the page draws it: the featured person, the stats under them, the
    Leadership section in its chosen order, and everyone else by name. The featured person appears
    once, in the band -- not again as a card below it."""
    listed = [m for m in rows if whos_who.listed(m, leadership)]
    you = viewer.id if viewer is not None else None

    def _card(m):
        return whos_who.card(m, role_name=role_names.get(m.role_id), you=m.id == you)

    featured = next((m for m in listed if setting is not None and m.id == setting.featured_member_id),
                    None)
    leaders = sorted((m for m in listed if m is not featured
                      and whos_who.placement(m, leadership) == "leadership"),
                     key=lambda m: (m.directory_order if m.directory_order is not None else 999,
                                    m.full_name.lower()))
    agents = [m for m in listed if m is not featured
              and whos_who.placement(m, leadership) == "agents"]

    stats = list(setting.stats or []) if setting is not None else []
    team = None
    if any((st.get("source") or "") in whos_who.SISU_SOURCES for st in stats):
        # Only when the workspace chose to show a Sisu total (D3), and only if Sisu is there.
        if (await member_numbers.sisu_state(s, tenant_id))["connected"]:
            team = await member_numbers.team_figures(s, tenant_id, today)
    intro = setting.intro if setting is not None and setting.intro else ""
    return {
        "intro": whos_who.fill(intro, len(listed)) if intro else "",
        "team_size": len(listed),
        "featured": ({**_card(featured),
                      "eyebrow": (setting.featured_label or featured.title
                                  or role_names.get(featured.role_id) or "")}
                     if featured is not None else None),
        "stats": whos_who.resolve_stats(stats, team_size=len(listed), team=team),
        "leadership": [_card(m) for m in leaders],
        "agents": [_card(m) for m in agents],
        "preview_count": setting.preview_count if setting is not None else 9,
    }


@router.get("/directory/{member_id}")
async def directory_profile(member_id: uuid.UUID, user: User = Depends(current_user),
                            s: AsyncSession = Depends(get_session)):
    """One person's profile page: the card, and what only the profile shows -- the quote, bio, the
    bring list, how to reach them and what they own. Anyone the directory lists; a hidden or
    removed person answers 404, as if they were never on it.

    WHAT THEY OWN includes the SOPs whose owner they are, read from the SOP library rather than
    typed twice -- and only for a viewer whose role can open the SOP library, or the links would
    be doors to a page that refuses them."""
    await _enabled_tenant(s, user)
    roles = (await s.execute(select(IntranetRole).where(
        IntranetRole.tenant_id == user.tenant_id, published(IntranetRole)))).scalars().all()
    leadership = {r.id for r in roles if r.is_leadership}
    row = (await s.execute(select(IntranetMember).where(
        IntranetMember.tenant_id == user.tenant_id, IntranetMember.id == member_id))).scalars().first()
    if row is None or not whos_who.listed(row, leadership):
        raise HTTPException(404, "Not found")
    viewer = await _member_for(s, user)
    levels = await capability_levels(s, user.tenant_id, viewer.role_id if viewer else None)
    owned: list[dict] = []
    if allows(levels, "sop_library"):
        sops = (await s.execute(select(IntranetSop).where(
            IntranetSop.tenant_id == user.tenant_id, IntranetSop.owner_member_id == row.id,
            IntranetSop.state == "Live", published(IntranetSop)).order_by(IntranetSop.title))).scalars().all()
        version_ids = [x.current_version_id for x in sops if x.current_version_id]
        labels = {}
        if version_ids:
            labels = dict((await s.execute(select(IntranetSopVersion.id, IntranetSopVersion.version_label)
                                           .where(IntranetSopVersion.id.in_(version_ids)))).all())
        owned = [{"id": str(x.id), "title": x.title, "version": labels.get(x.current_version_id)}
                 for x in sops]
    return whos_who.profile(row, role_name={r.id: r.name for r in roles}.get(row.role_id),
                            you=viewer is not None and viewer.id == row.id, owned_sops=owned)


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
    # The directory's own rule (D1): anyone not Removed and not Hidden. It was Active only, which
    # would have 404'd the photo of everybody listed before they signed in.
    row = (await s.execute(select(IntranetMember).where(
        IntranetMember.tenant_id == user.tenant_id,
        IntranetMember.id == member_id,
        IntranetMember.status != "Removed",
        IntranetMember.directory_placement != "hidden",
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


@router.get("/lessons/{lesson_id}/images/{name}")
async def read_lesson_image(lesson_id: uuid.UUID, name: str,
                            user: User = Depends(current_user),
                            s: AsyncSession = Depends(get_session)):
    """An image embedded in a reading lesson's body.

    GATED EXACTLY LIKE A HANDOUT, and for the same reason: the key is in the payload of anybody
    who was ever allowed the course, so filtering the listing alone would be cosmetic. A course
    restricted to Team Leaders has body images restricted to Team Leaders.

    Served INLINE rather than as an attachment, which a handout is not -- an `<img>` that
    downloads is not an image. That is only safe because the upload sniffed the bytes and the
    allowlist is four raster formats: no SVG, so nothing served here is a script host. `nosniff`
    keeps the browser from second-guessing the type we settled at upload.
    """
    await _enabled_tenant(s, user)
    member = await _member_for(s, user)
    if member is None:
        raise HTTPException(404, "Not found")
    if not allows(await capability_levels(s, user.tenant_id, member.role_id), "training_library"):
        raise HTTPException(404, "Not found")

    lesson = (await s.execute(select(IntranetLesson).where(
        IntranetLesson.tenant_id == user.tenant_id, IntranetLesson.id == lesson_id,
        published(IntranetLesson)))).scalars().first()
    if lesson is None:
        raise HTTPException(404, "Not found")
    course = (await s.execute(select(IntranetCourse).where(
        IntranetCourse.tenant_id == user.tenant_id, IntranetCourse.id == lesson.course_id,
        published(IntranetCourse)))).scalars().first()
    if course is None:          # published() excludes archived courses
        raise HTTPException(404, "Not found")
    audience = await _course_audience(s, user.tenant_id, [course.id])
    if not _audience_allows(audience, course.id, member.role_id):
        raise HTTPException(404, "Not found")
    return _lesson_image_response(user.tenant_id, lesson_id, name)


def _lesson_image_response(tenant_id, lesson_id, name: str) -> Response:
    """Read one body image off storage, having already decided the caller may have it.

    THE FILENAME IS REBUILT, never joined from what arrived. `safe_filename` strips the traversal,
    and the key is assembled from the tenant and lesson the caller was authorised against -- so a
    name of `../../another-tenant/secret.png` addresses a file that does not exist rather than one
    that does.
    """
    safe = binder_storage.safe_filename(name or "")
    if not safe or safe != (name or "").strip():
        raise HTTPException(404, "Not found")
    key = f"intranet/{tenant_id}/lessons/{lesson_id}/{safe}"
    if not binder_storage.exists(key):
        raise HTTPException(404, "Not found")
    data = binder_storage.read(key)
    return Response(
        content=data,
        media_type=uploads.sniff_image(data) or "application/octet-stream",
        headers={"Content-Disposition": 'inline; filename="' + safe + '"',
                 "X-Content-Type-Options": "nosniff",
                 "Cache-Control": "private, max-age=300"},
    )


@router.get("/lessons/{lesson_id}/attachments/{attachment_id}")
async def download_lesson_attachment(lesson_id: uuid.UUID, attachment_id: uuid.UUID,
                                     user: User = Depends(current_user),
                                     s: AsyncSession = Depends(get_session)):
    """A lesson handout, for a member who is allowed the lesson.

    THE ROLE AUDIENCE IS RE-CHECKED HERE, not trusted from the payload. Courses carry a role
    audience, so a course restricted to Team Leaders has handouts restricted to Team Leaders --
    and filtering the listing alone would be cosmetic, because the ids are in the payload of
    anybody who was ever allowed the course and an id is guessable besides. Same reasoning as the
    SOP file route.

    Published and live only, and both the lesson and its course must be published: a handout on a
    lesson somebody unpublished must stop being downloadable, not merely stop being listed.
    """
    await _enabled_tenant(s, user)
    member = await _member_for(s, user)
    if member is None:
        raise HTTPException(404, "Not found")
    if not allows(await capability_levels(s, user.tenant_id, member.role_id), "training_library"):
        raise HTTPException(404, "Not found")

    row = (await s.execute(
        select(IntranetLessonAttachment)
        .where(IntranetLessonAttachment.tenant_id == user.tenant_id,
               IntranetLessonAttachment.id == attachment_id,
               IntranetLessonAttachment.lesson_id == lesson_id,
               published(IntranetLessonAttachment))
    )).scalars().first()
    if row is None or row.kind != "file" or not row.storage_key:
        raise HTTPException(404, "Not found")

    lesson = (await s.execute(
        select(IntranetLesson).where(IntranetLesson.tenant_id == user.tenant_id,
                                     IntranetLesson.id == lesson_id,
                                     published(IntranetLesson))
    )).scalars().first()
    if lesson is None:
        raise HTTPException(404, "Not found")

    course = (await s.execute(
        select(IntranetCourse).where(IntranetCourse.tenant_id == user.tenant_id,
                                     IntranetCourse.id == lesson.course_id,
                                     published(IntranetCourse))
    )).scalars().first()
    if course is None:          # published() excludes archived courses
        raise HTTPException(404, "Not found")
    audience = await _course_audience(s, user.tenant_id, [course.id])
    if not _audience_allows(audience, course.id, member.role_id):
        raise HTTPException(404, "Not found")

    if not binder_storage.exists(row.storage_key):
        raise HTTPException(404, "Not found")
    safe = binder_storage.safe_filename(row.filename or row.title)
    return Response(
        content=binder_storage.read(row.storage_key),
        media_type=row.content_type or "application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="' + safe + '"'},
    )


@router.get("/sops/{sop_id}/file")
async def download_sop(sop_id: uuid.UUID, disposition: str = Query("attachment"),
                       user: User = Depends(current_user),
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
    # A revision of a WRITTEN procedure has no file. The procedure is on its own page; there is
    # nothing here to hand over.
    if version is None or not version.storage_key:
        raise HTTPException(404, "Not found")
    if not binder_storage.exists(version.storage_key):
        raise HTTPException(404, "Not found")
    safe = binder_storage.safe_filename(version.filename)
    # INLINE ONLY FOR A PDF, and only when asked: the reader embeds the document in the page, and
    # everything else stays an attachment, as every upload in this product does. A PDF is the one
    # type here a browser renders without running anything, and the bytes were sniffed on upload,
    # so the type is ours rather than the uploader's. nosniff keeps the browser from
    # second-guessing it either way.
    inline = disposition == "inline" and version.content_type == "application/pdf"
    how = "inline" if inline else "attachment"
    return Response(
        content=binder_storage.read(version.storage_key),
        media_type=version.content_type,
        headers={"Content-Disposition": how + '; filename="' + safe + '"',
                 "X-Content-Type-Options": "nosniff"},
    )


# ── the assistant ─────────────────────────────────────────────────────────────────────────

class AskBody(BaseModel):
    question: str = Field(min_length=2, max_length=intranet_assistant.MAX_QUESTION)


def _ask_status(tenant: Tenant, permitted: bool) -> dict:
    """Why the assistant is or is not available, kept as separate reasons.

    Three different noes -- the platform has no key, the plan does not include it, this role is
    denied -- and collapsing them into `available: false` would send a member to ask their admin
    to upgrade a plan that would still not answer, or an admin to check permissions when the key
    is missing.
    """
    return {
        "available": bool(intranet_assistant.available()
                          and plans.allows(tenant, "ai_assistant") and permitted),
        "on_plan": plans.allows(tenant, "ai_assistant"),
        "configured": intranet_assistant.available(),
        "permitted": permitted,
    }


@router.get("/assistant")
async def assistant_status(user: User = Depends(current_user),
                           s: AsyncSession = Depends(get_session)):
    tenant = await _enabled_tenant(s, user)
    member = await _member_for(s, user)
    permitted = allows(await capability_levels(s, user.tenant_id,
                                               member.role_id if member else None),
                       "ai_assistant")
    return _ask_status(tenant, permitted)


@router.post("/ask")
async def ask_assistant(body: AskBody, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session)):
    """Answer a question from this workspace's own content, and record that it was asked.

    THE CORPUS IS THIS MEMBER'S OWN PAYLOAD -- the same _published_content their screens render,
    already filtered by capability and role audience. An assistant built on anything else would be
    a second implementation of those rules, and getting them wrong here is worse than getting them
    wrong in a list, because the answer quotes the document.

    EVERY QUESTION IS STORED, answered or not: the console needs to see what people are asking,
    not only what went unanswered. An unanswered one ALSO opens (or bumps) a content gap, which is
    the admin's to-do list -- deduplicated by question text, because ten people asking the same
    thing is one thing to write, not ten.
    """
    tenant = await _enabled_tenant(s, user)
    member = await _member_for(s, user)
    permitted = allows(await capability_levels(s, user.tenant_id,
                                               member.role_id if member else None),
                       "ai_assistant")
    status = _ask_status(tenant, permitted)
    if not permitted:
        raise HTTPException(403, "Your role does not have access to the assistant.")
    if not status["on_plan"]:
        raise HTTPException(402, "The assistant is not included in this workspace's plan.")
    if not status["configured"]:
        # 503, not 402 or 403: nothing the customer can do about this one, and telling them to
        # upgrade or ask an admin would send them somewhere that cannot help.
        raise HTTPException(503, "The assistant is not available right now.")

    question = body.question.strip()
    if not question:
        raise HTTPException(422, "Ask a question.")

    # `full`: the procedures themselves, not only their titles (D10). Every page load
    # carries the library; the assistant runs once per question and can afford the text.
    content = await _published_content(s, user.tenant_id, member, full=True)
    workspace = await s.get(IntranetWorkspace, user.tenant_id)
    name = (workspace.name if workspace and workspace.name else tenant.name) or "this workspace"
    result = await intranet_assistant.ask(s, user.tenant_id, name, content, question)

    s.add(IntranetAiQuestion(
        tenant_id=user.tenant_id,
        member_id=member.id if member else None,
        asker_label=(member.full_name if member else None) or user.name or user.email or "Unknown",
        question=question[:intranet_assistant.MAX_QUESTION],
        answer=result["answer"] or None,
        answered=bool(result["answered"]),
        citations=result["citations"],
        failure=result["failure"]))

    # A gap only when the assistant WORKED and still could not answer. An outage is our problem,
    # not a hole in the customer's documentation, and filing it as one would fill their to-do list
    # with our incidents.
    if not result["answered"] and not result["failure"]:
        await _record_gap(s, user.tenant_id, question)

    audit(s, user.tenant_id, user.id, "assistant.asked", "ai_question", None,
          {"answered": bool(result["answered"])}, category="AI",
          summary="Asked the assistant: " + question[:120],
          actor_member_id=member.id if member else None,
          actor_label=member.full_name if member else None)
    await s.commit()

    if result["failure"]:
        raise HTTPException(503, "The assistant could not answer just now. Try again shortly.")
    return {"answer": result["answer"], "citations": result["citations"],
            "answered": bool(result["answered"])}


async def _record_gap(s: AsyncSession, tenant_id, question: str) -> None:
    """Open a content gap, or bump the one that already exists.

    Deduplicated on the question text, case-insensitively: ten people asking the same thing is one
    thing for an admin to write, and ten rows would bury it. A gap somebody has already resolved
    is REOPENED when it is asked again -- the resolution said the content now exists, and the
    assistant just said it cannot find it, so one of those is wrong and an admin should look.
    """
    text = question.strip()[:1000]
    now = dt.datetime.now(dt.timezone.utc)
    row = (await s.execute(select(IntranetContentGap).where(
        IntranetContentGap.tenant_id == tenant_id,
        func.lower(IntranetContentGap.question) == text.lower()))).scalars().first()
    if row is None:
        s.add(IntranetContentGap(tenant_id=tenant_id, question=text, ask_count=1,
                                 status="Open", first_asked_at=now, last_asked_at=now))
        return
    row.ask_count = (row.ask_count or 0) + 1
    row.last_asked_at = now
    if row.status == "Resolved":
        row.status = "Open"


@router.post("/courses/{course_id}/start")
async def start_course(course_id: uuid.UUID, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session)):
    """Record the day this member started this course. Idempotent, and never rewinds the clock.

    THE SERVER OWNS THIS DATE. Completion lives in the client-written state blob, which is fine
    for a checklist -- a training list is a prompt, not a permission. An enrolment date is
    different: `release_rule='day_n'` counts from it, so a member who could set their own would
    unlock every section of the course at once by backdating it.

    A second call returns the first answer. Re-enrolling would put Day 1 after Day 4 for somebody
    who simply reopened the page.
    """
    course = (await s.execute(select(IntranetCourse).where(
        IntranetCourse.tenant_id == user.tenant_id,
        IntranetCourse.id == course_id,
        IntranetCourse.state == "Live",
        published(IntranetCourse),
    ))).scalars().first()
    if course is None:
        raise HTTPException(404, "Not found.")

    row = (await s.execute(select(IntranetCourseEnrolment).where(
        IntranetCourseEnrolment.tenant_id == user.tenant_id,
        IntranetCourseEnrolment.user_id == user.id,
        IntranetCourseEnrolment.course_id == course_id,
    ))).scalars().first()
    if row is None:
        row = IntranetCourseEnrolment(tenant_id=user.tenant_id, user_id=user.id,
                                      course_id=course_id,
                                      started_at=dt.datetime.now(dt.timezone.utc))
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return {"course_id": str(course_id), "started_at": _iso(row.started_at)}


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
