"""Seed the Utah Life intranet/admin-console starting configuration.

Usage from the repo root:
  python -m backend.scripts.seed_intranet --tenant utah-life

Also works from backend/:
  python -m scripts.seed_intranet --tenant utah-life

This is tenant data, not a frontend fixture. It is additive/idempotent: rerunning it upserts the
same logical rows for the same tenant and leaves unrelated tenant data alone.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import re
import sys
import uuid
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if "DATABASE_URL" not in os.environ and Path.cwd() != BACKEND_ROOT:
    os.chdir(BACKEND_ROOT)

from sqlalchemy import func, select

from app import plans
from app.config import settings
from app.db import SessionLocal, engine
from app.models import (
    Base,
    Domain,
    IntranetAiSetting,
    IntranetAiSource,
    IntranetCalendarCategory,
    IntranetCalendarCategoryRole,
    IntranetCapability,
    IntranetCourse,
    IntranetCourseRole,
    IntranetIntegration,
    IntranetLaunchpadTile,
    IntranetLaunchpadTileRole,
    IntranetLesson,
    IntranetMember,
    IntranetPermission,
    IntranetRole,
    IntranetSetupTask,
    IntranetSop,
    IntranetSopCategory,
    IntranetSopVersion,
    IntranetWorkspace,
    IntranetWtdList,
    Tenant,
    User,
)

UTC = dt.timezone.utc
SEED_AT = dt.datetime(2026, 9, 2, 17, 0, tzinfo=UTC)
UTAH_LIFE_NAME = "Utah Life Real Estate Group"
ROLE_ORDER = ["Buyer Agent", "Listing Agent", "Ops / Admin", "Team Leader", "JV Partner"]


ROLE_ROWS = [
    ("buyer_agent", "Buyer Agent", 1, False),
    ("listing_agent", "Listing Agent", 2, False),
    ("ops_admin", "Ops / Admin", 3, True),
    ("team_leader", "Team Leader", 4, True),
    ("jv_partner", "JV Partner", 5, False),
]

CAPABILITY_ROWS = [
    ("training_library", "Training Library", "Courses, lessons and progress",
     ["Full", "Full", "Full", "Full", "None"]),
    ("sop_library", "SOP Library", "All published SOPs",
     ["Full", "Full", "Full", "Full", "Limited"]),
    # TODO(seed-conflict): intranet mockup has a different WTD naming/id map; see SEED_CONFLICTS.
    ("wtd", "Win the Day", "Daily run and the 13 lists",
     ["Full", "Full", "View", "Full", "None"]),
    ("own_numbers", "Own Production Numbers", "Their own Sisu dashboard",
     ["Full", "Full", "Full", "Full", "None"]),
    ("team_production", "Team Production", "Everyone else's numbers",
     ["None", "None", "Full", "Full", "None"]),
    ("commission_splits", "Commission & Splits", "Disbursement and the agent P&L",
     ["None", "None", "Full", "Full", "None"]),
    ("team_calendar", "Team Calendar", "Sales calls, trainings, events",
     ["View", "View", "Full", "Full", "Limited"]),
    ("marketing_requests", "Marketing Requests", "Submit and track requests",
     ["Full", "Full", "Full", "Full", "Limited"]),
    ("jv_partner_space", "JV Partner Space", "Sympli and Meraki resources",
     ["View", "View", "Full", "Full", "Full"]),
    ("console_access", "Console Access", "This admin console",
     ["None", "None", "Full", "Full", "None"]),
]

ROSTER_ROWS = [
    ("Justin Nelson", "justin@utahliferealestate.com", "Team Leader", "Salt Lake", "SSO", "Active"),
    ("Jace Gillies", "jace@utahliferealestate.com", "Team Leader", "Davis County", "SSO", "Active"),
    ("Lauren Griner", "lauren@utahliferealestate.com", "Team Leader", "Utah County", "SSO", "Active"),
    ("Jordan Hale", "jordan@utahliferealestate.com", "Buyer Agent", "Davis County", "SSO", "Active"),
    ("Marisol Vega", "marisol@utahliferealestate.com", "Listing Agent", "Salt Lake", "SSO", "Active"),
    ("Dan Whitaker", "dan@utahliferealestate.com", "Buyer Agent", "Utah County", "SSO", "Invited"),
    ("Priya Raman", "priya@symplimortgage.com", "JV Partner", "Sympli Mortgage", "Guest", "Active"),
    ("Cole Barrett", "cole@utahliferealestate.com", "Buyer Agent", "Salt Lake", "SSO", "Removed"),
]

COURSE_ROWS = [
    {
        "title": "Your First 30 Days",
        "category": "Onboarding",
        "description": "The path every new agent runs before they take a lead. Hosted in Skool, mirrored here for progress tracking.",
        "state": "Live",
        "required_for_onboarding": True,
        "issues_certificate": True,
        "lessons": [
            ("Welcome from Spring", "Loom - 4 min", "LOOM", "4m", True),
            ("How Utah Life Works", "Skool - Module 1", "SKOOL", "18m", True),
            ("Setting Up Follow Up Boss", "Hosted - Walkthrough", "HERE", "22m", True),
            ("The Win the Day Playbook", "PDF - Agent Playbook v4", "PDF", "15m", True),
            ("eXp Onboarding Checklist", "eXp Enterprise - External", "EXP", "30m", False),
        ],
    },
    {
        "title": "Listing Mastery",
        "category": "Listings",
        "description": "Pricing, presentation and the listing appointment, start to finish.",
        "state": "Live",
        "lessons": [
            ("Pricing a Home With a Finished Basement", "Loom - Justin Nelson", "LOOM", "16m", False),
            ("The Pre-Listing Packet", "PDF - Template", "PDF", "8m", False),
            ("Running the Listing Appointment", "Hosted - Roleplay", "HERE", "34m", True),
            ("Price Reduction Conversations", "PLACE - Script library", "PLACE", "12m", False),
        ],
    },
    {
        "title": "Lead Conversion",
        "category": "Lead Conversion",
        "description": "LPMAMA, speed to lead, and what to do with a Zillow lead who will not answer.",
        "state": "Draft",
        "lessons": [
            ("LPMAMA, Line by Line", "PLACE - Script + video", "PLACE", "19m", True),
            ("Speed to Lead: the First 5 Minutes", "Loom - 11 min", "LOOM", "11m", True),
            ("Building an Action Plan in FUB", "Hosted - Walkthrough", "HERE", "26m", False),
        ],
    },
    {
        "title": "Buyer Consultation",
        "category": "Buyers",
        "description": "The consultation that gets a signed agreement before the first showing.",
        "state": "Live",
        "lessons": [
            ("Why the Consultation Comes First", "Loom - Lauren Griner", "LOOM", "9m", True),
            ("Buyer Agreement Walkthrough", "Hosted - Forms", "HERE", "24m", True),
            ("Financing Conversations With Sympli", "Loom - Partner", "LOOM", "17m", False),
        ],
    },
    {
        "title": "Sisu and Your Numbers",
        "category": "Systems & Tools",
        "description": "Logging activity, reading your dashboard, and running a week with Sunburst.",
        "state": "Live",
        "lessons": [
            ("Logging Activity Correctly", "Hosted - Walkthrough", "HERE", "14m", True),
            ("Reading Your Dashboard", "Loom - 12 min", "LOOM", "12m", False),
            ("Your First Sunburst Session", "Hosted - Guided", "HERE", "20m", False),
        ],
    },
    {
        "title": "Open Houses That Produce",
        "category": "Listings",
        "description": "Setup, sign strategy, and the follow-up sequence that turns visitors into appointments.",
        "state": "Needs Review",
        "lessons": [
            ("Choosing the Right Listing", "Loom - Jace Gillies", "LOOM", "10m", False),
            ("The Sign-In Conversation", "PLACE - Script", "PLACE", "7m", False),
            ("Same-Day Follow-Up", "Hosted - Sequence", "HERE", "13m", True),
        ],
    },
]

SOP_CATEGORY_ROWS = [
    "Listings",
    "Buyers",
    "Transactions",
    "Lead Handling",
    "Marketing",
    "Admin & Finance",
    "Leadership",
]

SOP_ROWS = [
    # TODO(seed-conflict): intranet mockup names this "New Listing Intake, Start to Close" v3.1.
    ("New Listing Intake", "new-listing-intake-v3.2.pdf", "Listings", "v3.2", "Spring B.", "Mar 2027", "Draft"),
    ("Under Contract: the First 24 Hours", "under-contract-24h-v2.2.pdf", "Transactions", "v2.2", "Ops", "Jan 2027", "Live"),
    ("File Requirements Before Commission Release", "file-requirements-v1.9.pdf", "Admin & Finance", "v1.9", "Ops", "Sep 14", "Live"),
    ("Commission Disbursement and Splits", "commission-splits-v4.0.pdf", "Leadership", "v4.0", "Spring B.", "Dec 2026", "Live"),
    ("Buyer Consultation Standard", "buyer-consultation-v2.4.pdf", "Buyers", "v2.4", "Lauren G.", "Aug 18", "Needs Review"),
    ("Price Reduction Protocol", "price-reduction-v1.6.pdf", "Listings", "v1.6", "Justin N.", "Aug 22", "Needs Review"),
    ("Low Appraisal Response", "low-appraisal-draft.docx", "Transactions", "v0.1", "Jace G.", "Unassigned", "Draft"),
]

WTD_ROWS = [
    # TODO(seed-conflict): intranet mockup says list 35 is "Recently Active Leads".
    ("New Leads", "35", "LPMAMA Script", 10, True),
    # TODO(seed-conflict): intranet mockup says list 34 is "Zillow High Intent Buyers / Sellers".
    ("Recently Active", "41", "Returning Client to the Website", 15, True),
    ("Zillow High Intent", "22", "Zillow Speed to Lead", 10, True),
    ("Nurture 90+ Days", "57", "Long-Term Follow-Up", 20, True),
    ("Sphere / Past Clients", "18", "Past Client Check-In", 10, True),
    ("Purchase Anniversaries", "63", "Anniversary Call", 5, True),
    ("Open House Sign-Ins", "29", "Open House Follow-Up", 12, True),
    ("Under Contract", "44", "Client Update Cadence", None, True),
    ("Buyer Consults Pending", "51", "Buyer Consultation Set", 6, True),
    ("Expired / Withdrawn", "38", "Expired Listing Approach", 8, True),
    ("Price Reduction Watch", "47", "Price Reduction Conversation", 4, True),
    ("Referral Partners", "12", "Ask for a Referral", 3, True),
    ("Do Not Contact Review", "9", None, None, False),
]

TILE_ROWS = [
    ("Sisu", "sisu", "Production", "https://utahlife.sisu.co", "SSO", "All roles"),
    ("Sunburst", "sunburst", "Coaching", "https://sisu.co/sunburst?ctx=week", "Deeplink", "All roles"),
    ("Follow Up Boss", "follow-up-boss", "CRM", "https://liveutah1.followupboss.com", "SSO", "All roles"),
    ("Brivity", "brivity", "Marketing", "https://utahlife.brivity.com", "SSO", "All roles"),
    ("Skool", "skool", "Onboarding", "https://skool.com/utah-life", "Invite", "New agents"),
    ("SkySlope", "skyslope", "Transactions", "https://app.skyslope.com", "SSO", "Agents, Ops"),
    ("Slack", "slack", "Comms", "https://utahlife.slack.com", "SSO", "All roles"),
    ("Canva", "canva", "Marketing", "https://canva.com/brand/utah-life", "Link", "All roles"),
    ("eXp Enterprise", "exp-enterprise", "Brokerage", "https://enterprise.exprealty.com", "Link", "All roles"),
    ("eXp World Campus", "exp-world-campus", "Brokerage", "https://expworldcampus.com", "Link", "All roles"),
    ("Sympli Mortgage", "sympli-mortgage", "JV Partner", "https://symplimortgage.com/partners", "Link", "All + JV"),
    ("Meraki Title", None, "JV Partner", "https://merakititle.com/orders", "Link", "All + JV"),
    ("Sympli Homes", None, "JV Partner", "https://symplihomes.com/partners", "Link", "All + JV"),
]

CALENDAR_ROWS = [
    ("Team Sales Call", "#395262", "All agents", True),
    ("Training & Roleplay", "#6B4E9E", "All agents", True),
    ("Leadership 1:1s", "#C9A227", "Leaders only", False),
    ("Onboarding Cohort", "#2F6444", "New agents", True),
    ("Market Events", "#A44A33", "Everyone", True),
]

INTEGRATION_ROWS = [
    ("sisu", "Sisu", "Production data", "Numbers, goals, leaderboards and the Sunburst handoff.", None),
    ("follow_up_boss", "Follow Up Boss", "CRM + smart lists", "Smart list counts, lead assignment and the Win the Day run.", "https://liveutah1.followupboss.com/2/people/list/"),
    ("google_workspace", "Google Workspace", "Identity + calendar", "SSO, roster sync from the utahlife-agents group, and calendars.", None),
    ("slack", "Slack", "Notifications", "Publish announcements and route marketing requests to the configured channel.", None),
    ("skool", "Skool", "Onboarding content", "Course mirroring and progress webhooks.", None),
    ("brivity", "Brivity", "Listing marketing", "Listing feeds and single property sites for the launch sequence.", None),
    ("skyslope", "SkySlope", "Transactions", "File status for the under-contract checklist and commission gate.", None),
    ("canva", "Canva", "Brand templates", "Brand kit folder surfaced in the marketing section.", None),
    ("meraki_title", "Meraki Title", "JV partner", "Title order status. No API yet, so partner tiles link out instead.", None),
]

AI_SOURCE_ROWS = [
    ("SOP Library", "All SOP documents, current version only", "sops", "Everyone", True),
    ("Training Library", "Lesson transcripts and descriptions", "training", "Everyone", True),
    ("Win the Day + Scripts", "The playbook and every paired script", "wtd", "Everyone", True),
    ("Marketing & Brand Kit", "Brand rules, launch sequence, turnaround times", "brand", "Everyone", True),
    ("Team Directory", "Who owns what, leader profiles, markets", "directory", "Everyone", True),
    ("Commission & Splits", "Disbursement SOPs and the agent P&L", "custom", "Leadership only", True),
    ("Recruiting & Hiring", "Pipeline notes and offer templates", "custom", "Leadership only", False),
]

SETUP_ROWS = [
    ("brand", "Brand and logo set", "brand"),
    ("roster", "SSO and roster sync", "roster"),
    ("perms", "Roles and permissions", "perms"),
    ("training", "Training library imported", "training"),
    ("sops", "SOPs uploaded", "sops"),
    ("wtd", "Win the Day lists mapped", "wtd"),
    ("launchpad", "Launchpad tiles", "launchpad"),
    ("calendar", "Calendars connected", "calendar"),
    ("assistant", "AI sources reviewed", "assistant"),
    ("onboarding_path", "Onboarding path assigned", "training"),
    ("announcement_channel", "Announcement channel", "integrations"),
]

SEED_CONFLICTS = [
    ("WTD list 01", "Recently Active Leads, list 35", "New Leads, list 35"),
    ("WTD list 02", "Zillow High Intent Buyers / Sellers, list 34", "Recently Active, list 41"),
    ("SOP", "New Listing Intake, Start to Close v3.1", "New Listing Intake v3.2"),
    ("SOP set", "9 SOPs, older naming scheme", "7 SOPs from admin console"),
    ("Courses", "9 courses from portal mockup", "6 courses from admin console"),
    ("Roster", "9 agents, 3 leaders", "8 people including a JV guest"),
    ("Lessons", "Spec prose says 22 lessons", "Admin console artifact contains 21 concrete lessons"),
]


def _seed_id(tenant_id: uuid.UUID, *parts: object) -> uuid.UUID:
    raw = "intranet:" + ":".join(str(p).strip().lower() for p in parts)
    return uuid.uuid5(tenant_id, raw)


def _slug(value: str) -> str:
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower())
    return re.sub(r"_+", "_", value).strip("_")


def _published() -> dict:
    return {"published_at": SEED_AT, "draft_dirty": False}


def _minutes(value: str) -> int | None:
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else None


def _review_due(value: str) -> dt.date | None:
    if value == "Unassigned":
        return None
    if value == "Sep 14":
        return dt.date(2026, 9, 14)
    if value == "Aug 18":
        return dt.date(2026, 8, 18)
    if value == "Aug 22":
        return dt.date(2026, 8, 22)
    if value == "Dec 2026":
        return dt.date(2026, 12, 31)
    if value == "Jan 2027":
        return dt.date(2027, 1, 31)
    if value == "Mar 2027":
        return dt.date(2027, 3, 31)
    raise ValueError(f"Unhandled SOP review date: {value}")


def _content_type(filename: str) -> str:
    if filename.endswith(".pdf"):
        return "application/pdf"
    if filename.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "application/octet-stream"


def _owner_key(owner: str) -> str | None:
    return {
        "Lauren G.": "lauren@utahliferealestate.com",
        "Justin N.": "justin@utahliferealestate.com",
        "Jace G.": "jace@utahliferealestate.com",
    }.get(owner)


def _roles_for_visibility(label: str, roles: dict[str, uuid.UUID]) -> list[uuid.UUID]:
    if label == "All + JV" or label == "Everyone":
        return [roles[name] for name in ROLE_ORDER]
    if label == "All roles":
        return [roles[name] for name in ROLE_ORDER if name != "JV Partner"]
    if label == "Agents, Ops":
        return [roles[name] for name in ("Buyer Agent", "Listing Agent", "Ops / Admin")]
    if label == "Leaders only":
        return [roles[name] for name in ("Ops / Admin", "Team Leader")]
    if label == "New agents" or label == "All agents":
        return [roles[name] for name in ("Buyer Agent", "Listing Agent")]
    raise ValueError(f"Unhandled visibility label: {label}")


async def _merge(s, obj):
    return await s.merge(obj)


async def _load_or_create_tenant(s, slug: str) -> Tenant:
    tenant = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(
            slug=slug,
            name=UTAH_LIFE_NAME,
            plan=plans.BUSINESS,
            config={"features": {"intranet": True}},
        )
        s.add(tenant)
        await s.flush()
    else:
        cfg = dict(tenant.config or {})
        features = cfg.get("features") or {}
        features = dict(features) if isinstance(features, dict) else {}
        features["intranet"] = True
        cfg["features"] = features
        tenant.config = cfg

    hostname = f"{slug}.{settings.PLATFORM_DOMAIN}".lower()
    existing_domain = (await s.execute(select(Domain).where(
        Domain.tenant_id == tenant.id,
        Domain.hostname == hostname,
    ))).scalar_one_or_none()
    if existing_domain is None:
        s.add(Domain(tenant_id=tenant.id, hostname=hostname, is_primary=True))
    return tenant


async def seed_intranet(tenant_slug: str) -> dict[str, int]:
    if settings.is_sqlite:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as s:
        tenant = await _load_or_create_tenant(s, tenant_slug)
        tid = tenant.id

        workspace_id = _seed_id(tid, "workspace")
        await _merge(s, IntranetWorkspace(
            id=workspace_id,
            tenant_id=tid,
            portal_name="Utah Life Team Intranet",
            tagline="Everything you need, one place.",
            subdomain="utahlife",
            custom_domain="intranet.utahliferealestate.com",
            custom_domain_verified_at=None,
            palette={
                "ink": "#171E22",
                "brand": "#395262",
                "accent": "#AECBD4",
                "canvas": "#EAE7E6",
                "gold": "#C9A227",
            },
            logo_light_key="utah-life/logo/wordmark-light.png",
            logo_dark_key="utah-life/logo/wordmark-dark.png",
            logo_mark_key="utah-life/logo/mark-color.png",
            timezone="America/Denver",
            week_starts_on=1,
            default_calendar_view="week",
            **_published(),
        ))

        roles: dict[str, uuid.UUID] = {}
        for key, name, sort, leadership in ROLE_ROWS:
            role_id = _seed_id(tid, "role", key)
            roles[name] = role_id
            await _merge(s, IntranetRole(
                id=role_id,
                tenant_id=tid,
                key=key,
                name=name,
                sort=sort,
                is_leadership=leadership,
                **_published(),
            ))

        capabilities: dict[str, uuid.UUID] = {}
        for sort, (key, name, description, levels) in enumerate(CAPABILITY_ROWS, start=1):
            cap_id = _seed_id(tid, "capability", key)
            capabilities[key] = cap_id
            await _merge(s, IntranetCapability(
                id=cap_id,
                tenant_id=tid,
                key=key,
                name=name,
                description=description,
                sort=sort,
                **_published(),
            ))
            for role_name, level in zip(ROLE_ORDER, levels):
                await _merge(s, IntranetPermission(
                    id=_seed_id(tid, "permission", key, role_name),
                    tenant_id=tid,
                    capability_id=cap_id,
                    role_id=roles[role_name],
                    level=level,
                    **_published(),
                ))

        members: dict[str, uuid.UUID] = {}
        users = {
            email: user_id
            for email, user_id in (await s.execute(select(User.email, User.id).where(
                User.tenant_id == tid
            ))).all()
        }
        for full_name, email, role_name, market, auth_source, status in ROSTER_ROWS:
            member_id = _seed_id(tid, "member", email.lower())
            members[email.lower()] = member_id
            await _merge(s, IntranetMember(
                id=member_id,
                tenant_id=tid,
                user_id=users.get(email.lower()),
                full_name=full_name,
                email=email.lower(),
                role_id=roles[role_name],
                market=market,
                auth_source=auth_source,
                status=status,
                invited_at=SEED_AT if status == "Invited" else None,
                activated_at=SEED_AT if status == "Active" else None,
                removed_at=SEED_AT if status == "Removed" else None,
                last_synced_at=None,
            ))

        for sort, name in enumerate(SOP_CATEGORY_ROWS, start=1):
            await _merge(s, IntranetSopCategory(
                id=_seed_id(tid, "sop_category", name),
                tenant_id=tid,
                name=name,
                sort=sort,
                **_published(),
            ))

        for sort, course in enumerate(COURSE_ROWS, start=1):
            course_id = _seed_id(tid, "course", course["title"])
            await _merge(s, IntranetCourse(
                id=course_id,
                tenant_id=tid,
                title=course["title"],
                category=course["category"],
                description=course["description"],
                state=course["state"],
                track_progress=True,
                required_for_onboarding=bool(course.get("required_for_onboarding")),
                issues_certificate=bool(course.get("issues_certificate")),
                sequential=False,
                sort=sort,
                archived_at=None,
                **_published(),
            ))
            for role_name in ("Buyer Agent", "Listing Agent", "Ops / Admin", "Team Leader"):
                await _merge(s, IntranetCourseRole(
                    tenant_id=tid,
                    course_id=course_id,
                    role_id=roles[role_name],
                    **_published(),
                ))
            for lesson_sort, (title, label, source_type, duration, required) in enumerate(
                course["lessons"], start=1
            ):
                await _merge(s, IntranetLesson(
                    id=_seed_id(tid, "lesson", course["title"], title),
                    tenant_id=tid,
                    course_id=course_id,
                    title=title,
                    source_type=source_type,
                    source_ref=None,
                    source_label=label,
                    duration_minutes=_minutes(duration),
                    required=required,
                    sort=lesson_sort,
                    **_published(),
                ))

        await s.flush()

        sop_objects: dict[str, IntranetSop] = {}
        for title, filename, category, version, owner, due, state in SOP_ROWS:
            owner_email = _owner_key(owner)
            sop = await _merge(s, IntranetSop(
                id=_seed_id(tid, "sop", title),
                tenant_id=tid,
                title=title,
                category_id=_seed_id(tid, "sop_category", category),
                owner_member_id=members.get(owner_email) if owner_email else None,
                state=state,
                review_due_on=_review_due(due),
                current_version_id=None,
                archived_at=None,
                **_published(),
            ))
            sop_objects[title] = sop

        await s.flush()

        for title, filename, _category, version, _owner, _due, _state in SOP_ROWS:
            sop_id = _seed_id(tid, "sop", title)
            version_id = _seed_id(tid, "sop_version", title, version)
            await _merge(s, IntranetSopVersion(
                id=version_id,
                tenant_id=tid,
                sop_id=sop_id,
                version_label=version,
                filename=filename,
                storage_key=f"utah-life/sops/{filename}",
                content_type=_content_type(filename),
                byte_size=0,
                uploaded_by=None,
                uploaded_at=SEED_AT,
                **_published(),
            ))
            sop_objects[title].current_version_id = version_id

        for position, (name, external_id, script_name, target, active) in enumerate(WTD_ROWS, start=1):
            await _merge(s, IntranetWtdList(
                id=_seed_id(tid, "wtd", position),
                tenant_id=tid,
                position=position,
                name=name,
                provider="follow_up_boss",
                external_list_id=external_id,
                script_name=script_name,
                daily_target=target,
                active=active,
                **_published(),
            ))

        for sort, (name, logo, group, url, auth_type, visibility) in enumerate(TILE_ROWS, start=1):
            tile_id = _seed_id(tid, "tile", name)
            await _merge(s, IntranetLaunchpadTile(
                id=tile_id,
                tenant_id=tid,
                name=name,
                logo_key=f"utah-life/logos/{logo}.png" if logo else None,
                tile_group=group,
                url=url,
                auth_type=auth_type,
                sort=sort,
                active=True,
                **_published(),
            ))
            for role_id in _roles_for_visibility(visibility, roles):
                await _merge(s, IntranetLaunchpadTileRole(
                    tenant_id=tid,
                    tile_id=tile_id,
                    role_id=role_id,
                    **_published(),
                ))

        for sort, (name, color, visibility, active) in enumerate(CALENDAR_ROWS, start=1):
            category_id = _seed_id(tid, "calendar", name)
            await _merge(s, IntranetCalendarCategory(
                id=category_id,
                tenant_id=tid,
                name=name,
                color=color,
                # Google Calendar IDs are configured in the UI; seed leaves them empty.
                calendar_address=None,
                sort=sort,
                active=active,
                **_published(),
            ))
            for role_id in _roles_for_visibility(visibility, roles):
                await _merge(s, IntranetCalendarCategoryRole(
                    tenant_id=tid,
                    category_id=category_id,
                    role_id=role_id,
                    **_published(),
                ))

        for provider_key, display_name, role_label, description, base_url in INTEGRATION_ROWS:
            await _merge(s, IntranetIntegration(
                id=_seed_id(tid, "integration", provider_key),
                tenant_id=tid,
                provider_key=provider_key,
                display_name=display_name,
                role_label=role_label,
                description=description,
                status="Not Connected",
                base_url=base_url,
                config={},
                credential_ref=None,
                last_sync_at=None,
                last_sync_status=None,
                last_error=None,
            ))

        for sort, (name, description, source_kind, floor, enabled) in enumerate(AI_SOURCE_ROWS, start=1):
            await _merge(s, IntranetAiSource(
                id=_seed_id(tid, "ai_source", name),
                tenant_id=tid,
                name=name,
                description=description,
                source_kind=source_kind,
                min_role_id=roles["Team Leader"] if floor == "Leadership only" else None,
                enabled=enabled,
                last_crawled_at=None,
                indexed_item_count=0,
                sort=sort,
                **_published(),
            ))

        await _merge(s, IntranetAiSetting(
            tenant_id=tid,
            always_cite=True,
            refuse_without_source=True,
            offer_escalation=True,
            learn_from_corrections=False,
            escalation_channel=None,
            **_published(),
        ))

        for sort, (key, label, destination) in enumerate(SETUP_ROWS, start=1):
            await _merge(s, IntranetSetupTask(
                id=_seed_id(tid, "setup", key),
                tenant_id=tid,
                key=key,
                label=label,
                destination=destination,
                sort=sort,
                completed_at=None,
                completed_by=None,
            ))

        await s.commit()

        models = {
            "courses": IntranetCourse,
            "lessons": IntranetLesson,
            "sops": IntranetSop,
            "members": IntranetMember,
            "wtd": IntranetWtdList,
            "tiles": IntranetLaunchpadTile,
            "perms": IntranetPermission,
            "integrations": IntranetIntegration,
            "ai_sources": IntranetAiSource,
            "setup": IntranetSetupTask,
        }
        counts = {}
        for label, model in models.items():
            counts[label] = (await s.execute(select(func.count()).select_from(model).where(
                model.tenant_id == tid
            ))).scalar_one()
        return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tenant", required=True, help="tenant slug, e.g. utah-life")
    args = ap.parse_args()

    counts = asyncio.run(seed_intranet(args.tenant.strip().lower()))
    print(f"[ok] seeded intranet for tenant '{args.tenant.strip().lower()}'")
    for key in sorted(counts):
        print(f"{key}: {counts[key]}")
    print("\nseed conflicts to confirm:")
    for entity, intranet_value, console_value in SEED_CONFLICTS:
        print(f"- {entity}: intranet={intranet_value}; console_seeded={console_value}")


if __name__ == "__main__":
    main()
