import datetime as dt
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

from app.db import SessionLocal
from app.main import app
from app.models import (
    AuditLog,
    Domain,
    IntranetCapability,
    IntranetContentGap,
    IntranetCourse,
    IntranetIntegration,
    IntranetLesson,
    IntranetMarketingRequest,
    IntranetMember,
    IntranetPendingChange,
    IntranetPermission,
    IntranetPublishBatch,
    IntranetRole,
    IntranetSop,
    IntranetSopCategory,
    IntranetSopVersion,
    IntranetWorkspace,
    Tenant,
    User,
)
from app.security import hash_pw, make_token
from app.services import binder_storage

TRANSPORT = ASGITransport(app=app)


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token: str, host: str) -> dict:
    return {"Authorization": f"Bearer {token}", "x-tenant-host": host}


@pytest.fixture(scope="module")
async def ctx():
    from app.seed import seed
    from scripts.seed_intranet import seed_intranet

    await seed()
    await seed_intranet("console-a")
    await seed_intranet("console-b")
    return {
        "a": await _wire_console_user("console-a"),
        "b": await _wire_console_user("console-b"),
    }


async def _wire_console_user(slug: str) -> dict:
    async with SessionLocal() as s:
        tenant = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one()
        host = (await s.execute(select(Domain.hostname).where(
            Domain.tenant_id == tenant.id).limit(1))).scalar_one()
        roles = {r.key: r for r in (await s.execute(select(IntranetRole).where(
            IntranetRole.tenant_id == tenant.id))).scalars().all()}
        leader = (await s.execute(select(IntranetMember).where(
            IntranetMember.tenant_id == tenant.id,
            IntranetMember.role_id == roles["team_leader"].id,
        ).limit(1))).scalar_one()
        buyer = (await s.execute(select(IntranetMember).where(
            IntranetMember.tenant_id == tenant.id,
            IntranetMember.role_id == roles["buyer_agent"].id,
        ).limit(1))).scalar_one()

        async def user_for(member: IntranetMember, role: str) -> User:
            user = (await s.execute(select(User).where(
                User.tenant_id == tenant.id,
                User.email == member.email,
            ))).scalar_one_or_none()
            if user is None:
                user = User(
                    tenant_id=tenant.id,
                    email=member.email,
                    name=member.full_name,
                    password_hash=hash_pw("password123"),
                    role=role,
                    status="active",
                    token_version=0,
                )
                s.add(user)
                await s.flush()
            member.user_id = user.id
            return user

        admin_user = await user_for(leader, "admin")
        agent_user = await user_for(buyer, "member")

        courses = (await s.execute(select(IntranetCourse).where(
            IntranetCourse.tenant_id == tenant.id).order_by(IntranetCourse.sort))).scalars().all()
        course = courses[0]
        archive_course = courses[1]
        lessons = (await s.execute(select(IntranetLesson).where(
            IntranetLesson.tenant_id == tenant.id,
            IntranetLesson.course_id == course.id,
        ).order_by(IntranetLesson.sort))).scalars().all()
        lesson = lessons[0]
        delete_lesson = lessons[-1]
        category = (await s.execute(select(IntranetSopCategory).where(
            IntranetSopCategory.tenant_id == tenant.id).order_by(IntranetSopCategory.sort))).scalars().first()
        sops = (await s.execute(select(IntranetSop).where(
            IntranetSop.tenant_id == tenant.id).order_by(IntranetSop.title))).scalars().all()
        sop = sops[0]
        delete_sop = sops[-1]
        delete_member = (await s.execute(select(IntranetMember).where(
            IntranetMember.tenant_id == tenant.id,
            IntranetMember.id.not_in([leader.id, buyer.id]),
        ).limit(1))).scalar_one()
        role_rows = (await s.execute(select(IntranetRole).where(
            IntranetRole.tenant_id == tenant.id).order_by(IntranetRole.sort))).scalars().all()
        cap_rows = (await s.execute(select(IntranetCapability).where(
            IntranetCapability.tenant_id == tenant.id).order_by(IntranetCapability.sort))).scalars().all()
        perm_rows = (await s.execute(select(IntranetPermission).where(
            IntranetPermission.tenant_id == tenant.id))).scalars().all()

        delete_category = IntranetSopCategory(
            tenant_id=tenant.id, name=f"Delete Category {slug}", sort=900)
        gap = IntranetContentGap(
            tenant_id=tenant.id, question=f"What should {slug} document next?",
            ask_count=3, status="Open", first_asked_at=dt.datetime.now(dt.timezone.utc),
            last_asked_at=dt.datetime.now(dt.timezone.utc))
        batch = IntranetPublishBatch(
            tenant_id=tenant.id, published_by=leader.id, note="Existing publish batch",
            snapshot={})
        marketing_request = IntranetMarketingRequest(
            tenant_id=tenant.id, requester_member_id=leader.id,
            requester_label=leader.full_name, title=f"Listing flyer for {slug}",
            priority="Normal", status="New")
        s.add_all([delete_category, gap, batch, marketing_request])
        await s.flush()

        ids = {
            "role": str(role_rows[0].id),
            "marketing_request": str(marketing_request.id),
            "role_ids": [str(r.id) for r in role_rows],
            "preview_role": "team_leader",
            "capability": str(cap_rows[0].id),
            "member": str(leader.id),
            "delete_member": str(delete_member.id),
            "course": str(course.id),
            "archive_course": str(archive_course.id),
            "lesson": str(lesson.id),
            "lesson_ids": [str(row.id) for row in lessons],
            "delete_lesson": str(delete_lesson.id),
            "category": str(category.id),
            "delete_category": str(delete_category.id),
            "sop": str(sop.id),
            "delete_sop": str(delete_sop.id),
            "wtd": None,
            "wtd_ids": [],
            "tile": None,
            "tile_ids": [],
            "calendar": None,
            "integration": None,
            "ai_source": None,
            "content_gap": str(gap.id),
            "setup_key": None,
            "batch": str(batch.id),
            "permissions": [
                {"capability_id": str(p.capability_id), "role_id": str(p.role_id), "level": p.level}
                for p in perm_rows
            ],
        }
        for table_name, key, order_col in (
            ("intranet_wtd_list", "wtd", "position"),
            ("intranet_launchpad_tile", "tile", "sort"),
            ("intranet_calendar_category", "calendar", "sort"),
            # NOTE: `integration` is picked by display_name below and then narrowed -- the
            # connect route now refuses providers the dashboard owns, so the shared harness has
            # to exercise one the portal actually owns or its coverage breaks on a rename.
            ("intranet_integration", "integration", "display_name"),
            ("intranet_ai_source", "ai_source", "sort"),
        ):
            rows = (await s.execute(
                text(f"SELECT id FROM {table_name} WHERE tenant_id = :tid ORDER BY {order_col}"),
                {"tid": str(tenant.id)},
            )).all()
            ids[key] = rows[0][0]
            if key == "wtd":
                ids["wtd_ids"] = [r[0] for r in rows]
            if key == "tile":
                ids["tile_ids"] = [r[0] for r in rows]
        setup_key = (await s.execute(
            text("SELECT key FROM intranet_setup_task WHERE tenant_id = :tid ORDER BY sort LIMIT 1"),
            {"tid": str(tenant.id)},
        )).scalar_one()
        ids["setup_key"] = setup_key

        # Explicit rather than "whichever sorts first": one provider the PORTAL owns (connect
        # works) and one the DASHBOARD owns (connect is refused). Alphabetical order picked a
        # portal-owned provider by luck, which would have flipped on any display-name change.
        for provider_key, id_key in (("slack", "integration"),
                                     ("sisu", "inherited_integration")):
            row = (await s.execute(
                text("SELECT id FROM intranet_integration "
                     "WHERE tenant_id = :tid AND provider_key = :pk"),
                {"tid": str(tenant.id), "pk": provider_key},
            )).first()
            if row:
                ids[id_key] = row[0]

        await s.commit()
        return {
            "tenant_id": str(tenant.id),
            "host": host,
            "admin": make_token(admin_user.id, tenant.id, int(admin_user.token_version or 0)),
            "agent": make_token(agent_user.id, tenant.id, int(agent_user.token_version or 0)),
            "ids": ids,
        }


READ_ROUTES = [
    ("config.get", "GET", lambda ids: "/api/console/config", {}),
    ("overview.get", "GET", lambda ids: "/api/console/overview", {}),
    ("workspace.get", "GET", lambda ids: "/api/console/workspace", {}),
    ("roles.get", "GET", lambda ids: "/api/console/roles", {}),
    ("permissions.get", "GET", lambda ids: "/api/console/permissions", {}),
    ("members.get", "GET", lambda ids: "/api/console/members", {}),
    ("courses.get", "GET", lambda ids: "/api/console/courses", {}),
    ("course.get", "GET", lambda ids: f"/api/console/courses/{ids['course']}", {}),
    ("sop_categories.get", "GET", lambda ids: "/api/console/sop-categories", {}),
    ("sops.get", "GET", lambda ids: "/api/console/sops", {}),
    ("sop.get", "GET", lambda ids: f"/api/console/sops/{ids['sop']}", {}),
    ("sop_versions.get", "GET", lambda ids: f"/api/console/sops/{ids['sop']}/versions", {}),
    ("wtd.get", "GET", lambda ids: "/api/console/wtd-lists", {}),
    ("tiles.get", "GET", lambda ids: "/api/console/tiles", {}),
    ("calendar.get", "GET", lambda ids: "/api/console/calendar-categories", {}),
    ("integrations.get", "GET", lambda ids: "/api/console/integrations", {}),
    ("marketing.get", "GET", lambda ids: "/api/console/marketing", {}),
    ("marketing_requests.get", "GET", lambda ids: "/api/console/marketing/requests", {}),
    ("ai.get", "GET", lambda ids: "/api/console/ai", {}),
    ("content_gaps.get", "GET", lambda ids: "/api/console/content-gaps", {}),
    ("setup.get", "GET", lambda ids: "/api/console/setup-tasks", {}),
    ("pending.get", "GET", lambda ids: "/api/console/publish/pending", {}),
    ("batches.get", "GET", lambda ids: "/api/console/publish/batches", {}),
    ("audit.get", "GET", lambda ids: "/api/console/audit", {}),
    ("preview.get", "GET", lambda ids: f"/api/console/preview?role={ids['preview_role']}", {}),
]


MUTATION_ROUTES = [
    "workspace.patch",
    "workspace.logo",
    "role.patch",
    "permissions.put",
    "member.invite",
    "member.patch",
    "member.delete",
    "member.sync",
    "course.post",
    "course.patch",
    "course.delete",
    "course.roles",
    "lesson.order",
    "lesson.post",
    "lesson.patch",
    "lesson.delete",
    "sop_category.post",
    "sop_category.patch",
    "sop_category.delete",
    "sop.post",
    "sop.patch",
    "sop.delete",
    "sop_version.post",
    "wtd.patch",
    "wtd.order",
    "tile.order",
    "tile.post",
    "tile.patch",
    "tile.delete",
    "tile.roles",
    "calendar.post",
    "calendar.patch",
    "calendar.delete",
    "integration.patch",
    "integration.connect",
    "integration.test",
    "marketing.patch",
    "marketing_request.patch",
    "ai_settings.patch",
    "ai_source.patch",
    "content_gap.patch",
    "setup.patch",
    "publish.post",
    "publish.discard",
    "publish.rollback",
]


def _mutation_request(name: str, ids: dict):
    uniq = uuid.uuid4().hex[:8]
    if name == "workspace.patch":
        return "PATCH", "/api/console/workspace", {"json": {"tagline": f"Configured {uniq}"}}
    if name == "workspace.logo":
        return "POST", "/api/console/workspace/logo", {
            "data": {"kind": "mark"},
            "files": {"file": ("mark.png", b"png", "image/png")},
        }
    if name == "role.patch":
        return "PATCH", f"/api/console/roles/{ids['role']}", {"json": {"name": f"Leader {uniq}"}}
    if name == "permissions.put":
        return "PUT", "/api/console/permissions", {"json": {"items": ids["permissions"]}}
    if name == "member.invite":
        return "POST", "/api/console/members/invite", {
            "json": {"full_name": f"Invited {uniq}", "email": f"invite-{uniq}@example.test",
                     "role_id": ids["role_ids"][0]}
        }
    if name == "member.patch":
        return "PATCH", f"/api/console/members/{ids['member']}", {"json": {"market": "Wasatch Front"}}
    if name == "member.delete":
        return "DELETE", f"/api/console/members/{ids['delete_member']}", {}
    if name == "member.sync":
        return "POST", "/api/console/members/sync", {}
    if name == "course.post":
        return "POST", "/api/console/courses", {
            "json": {"title": f"New Course {uniq}", "category": "Scratch", "state": "Draft"}
        }
    if name == "course.patch":
        return "PATCH", f"/api/console/courses/{ids['course']}", {"json": {"description": f"Updated {uniq}"}}
    if name == "course.delete":
        return "DELETE", f"/api/console/courses/{ids['archive_course']}", {}
    if name == "course.roles":
        return "PUT", f"/api/console/courses/{ids['course']}/roles", {"json": {"role_ids": ids["role_ids"]}}
    if name == "lesson.order":
        return "PUT", f"/api/console/courses/{ids['course']}/lessons/order", {
            "json": {"ids": ids["lesson_ids"]}
        }
    if name == "lesson.post":
        return "POST", f"/api/console/courses/{ids['course']}/lessons", {
            "json": {"title": f"New Lesson {uniq}", "source_type": "PLACE", "duration_minutes": 3}
        }
    if name == "lesson.patch":
        return "PATCH", f"/api/console/courses/{ids['course']}/lessons/{ids['lesson']}", {
            "json": {"duration_minutes": 4}
        }
    if name == "lesson.delete":
        return "DELETE", f"/api/console/courses/{ids['course']}/lessons/{ids['delete_lesson']}", {}
    if name == "sop_category.post":
        return "POST", "/api/console/sop-categories", {"json": {"name": f"New Category {uniq}"}}
    if name == "sop_category.patch":
        return "PATCH", f"/api/console/sop-categories/{ids['category']}", {"json": {"sort": 2}}
    if name == "sop_category.delete":
        return "DELETE", f"/api/console/sop-categories/{ids['delete_category']}", {}
    if name == "sop.post":
        return "POST", "/api/console/sops", {
            "json": {"title": f"New SOP {uniq}", "category_id": ids["category"], "state": "Draft"}
        }
    if name == "sop.patch":
        return "PATCH", f"/api/console/sops/{ids['sop']}", {"json": {"state": "Needs Review"}}
    if name == "sop.delete":
        return "DELETE", f"/api/console/sops/{ids['delete_sop']}", {}
    if name == "sop_version.post":
        return "POST", f"/api/console/sops/{ids['sop']}/versions", {
            "data": {"version_label": f"v-{uniq}"},
            "files": {"file": ("sop.pdf", b"%PDF-1.4", "application/pdf")},
        }
    if name == "wtd.patch":
        return "PATCH", f"/api/console/wtd-lists/{ids['wtd']}", {"json": {"daily_target": 12}}
    if name == "wtd.order":
        return "PUT", "/api/console/wtd-lists/order", {"json": {"ids": ids["wtd_ids"]}}
    if name == "tile.post":
        return "POST", "/api/console/tiles", {
            "json": {"name": f"Tool {uniq}", "url": "https://example.test/tool", "auth_type": "Link"}
        }
    if name == "tile.patch":
        return "PATCH", f"/api/console/tiles/{ids['tile']}", {"json": {"active": True}}
    if name == "tile.delete":
        return "DELETE", f"/api/console/tiles/{ids['tile']}", {}
    if name == "tile.roles":
        return "PUT", f"/api/console/tiles/{ids['tile']}/roles", {"json": {"role_ids": ids["role_ids"]}}
    if name == "tile.order":
        return "PUT", "/api/console/tiles/order", {"json": {"ids": ids["tile_ids"]}}
    if name == "calendar.post":
        return "POST", "/api/console/calendar-categories", {
            "json": {"name": f"Calendar {uniq}", "color": "#C9A227"}
        }
    if name == "calendar.patch":
        return "PATCH", f"/api/console/calendar-categories/{ids['calendar']}", {
            "json": {"calendar_address": "training@example.test"}
        }
    if name == "calendar.delete":
        return "DELETE", f"/api/console/calendar-categories/{ids['calendar']}", {}
    if name == "integration.patch":
        return "PATCH", f"/api/console/integrations/{ids['integration']}", {
            "json": {"config": {"calendar_id": "configurable-later"}}
        }
    if name == "integration.connect":
        return "POST", f"/api/console/integrations/{ids['integration']}/connect", {
            "json": {"config": {"account": "placeholder", "api_key": "must-not-persist"}}
        }
    if name == "integration.test":
        return "POST", f"/api/console/integrations/{ids['integration']}/test", {}
    if name == "marketing.patch":
        return "PATCH", "/api/console/marketing", {"json": {"notify": f"ops-{uniq}"}}
    if name == "marketing_request.patch":
        return "PATCH", f"/api/console/marketing/requests/{ids['marketing_request']}", {
            "json": {"status": "In Progress"}}
    if name == "ai_settings.patch":
        return "PATCH", "/api/console/ai/settings", {"json": {"always_cite": True}}
    if name == "ai_source.patch":
        return "PATCH", f"/api/console/ai/sources/{ids['ai_source']}", {"json": {"enabled": True}}
    if name == "content_gap.patch":
        return "PATCH", f"/api/console/content-gaps/{ids['content_gap']}", {"json": {"status": "Assigned"}}
    if name == "setup.patch":
        return "PATCH", f"/api/console/setup-tasks/{ids['setup_key']}", {"json": {"completed": True}}
    if name == "publish.post":
        return "POST", "/api/console/publish", {"json": {"note": f"Publish {uniq}"}}
    if name == "publish.discard":
        return "POST", "/api/console/publish/discard", {}
    if name == "publish.rollback":
        return "POST", f"/api/console/publish/batches/{ids['batch']}/rollback", {}
    raise AssertionError(name)


ALL_ROUTE_NAMES = [r[0] for r in READ_ROUTES] + MUTATION_ROUTES


async def _request(client: AsyncClient, method: str, path: str, headers: dict | None, kwargs: dict):
    return await client.request(method, path, headers=headers, **kwargs)


@pytest.mark.parametrize("name,method,path_fn,kwargs", READ_ROUTES, ids=[r[0] for r in READ_ROUTES])
async def test_console_read_endpoints_are_backed_by_seeded_data(ctx, name, method, path_fn, kwargs):
    ids = ctx["a"]["ids"]
    async with _client() as c:
        r = await _request(c, method, path_fn(ids), _H(ctx["a"]["admin"], ctx["a"]["host"]), kwargs)
    assert r.status_code == 200, f"{name}: {r.status_code} {r.text}"
    body = r.json()
    if "items" in body:
        assert "total" in body and "cursor" in body


async def test_console_overview_counts_are_real_seed_counts(ctx):
    async with _client() as c:
        r = await c.get("/api/console/overview", headers=_H(ctx["a"]["admin"], ctx["a"]["host"]))
    assert r.status_code == 200, r.text
    assert r.json()["counts"] == {
        "courses": 6,
        "lessons": 21,
        "sops": 7,
        "members": 8,
        "wtd_lists": 13,
        "tiles": 13,
        "permissions": 50,
        # 8, not the 9 rows the seed creates: google_workspace is the workspace's sign-in — the
        # seed already calls it "Identity + calendar / SSO" — and it now has its own console
        # panel, the only place its client secret can be set. It is filtered out of the
        # connections list and this count together (console._not_signin), so the overview tile
        # agrees with the page it links to.
        "integrations": 8,
        "ai_sources": 7,
        "setup_tasks": 11,
        "pending_changes": 0,
    }


async def test_workspace_brand_update_and_logo_upload_use_spec_audit_actions(ctx):
    palette = {
        "ink": "#101820",
        "brand": "#395262",
        "accent": "#AECBD4",
        "canvas": "#F4F1ED",
        "gold": "#C9A227",
    }
    async with _client() as c:
        update = await c.patch(
            "/api/console/workspace",
            headers=_H(ctx["a"]["admin"], ctx["a"]["host"]),
            json={
                "portal_name": "Utah Life Test Intranet",
                "tagline": "Configured for the team",
                "palette": palette,
            },
        )
        upload = await c.post(
            "/api/console/workspace/logo",
            headers=_H(ctx["a"]["admin"], ctx["a"]["host"]),
            data={"kind": "mark"},
            files={"file": ("mark.png", b"png-bytes", "image/png")},
        )
        refreshed = await c.get("/api/console/workspace", headers=_H(ctx["a"]["admin"], ctx["a"]["host"]))
    assert update.status_code == 200, update.text
    assert upload.status_code == 200, upload.text
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["portal_name"] == "Utah Life Test Intranet"
    assert refreshed.json()["palette"] == palette
    key = upload.json()["item"]["logo_mark_key"]
    assert key.startswith(f"intranet/{ctx['a']['tenant_id']}/workspace/mark-")
    assert not key.startswith("data:")
    assert "\\" not in key
    assert binder_storage.exists(key)
    assert binder_storage.read(key) == b"png-bytes"
    async with SessionLocal() as s:
        actions = (await s.execute(select(AuditLog.action).where(
            AuditLog.tenant_id == uuid.UUID(ctx["a"]["tenant_id"]),
            AuditLog.action.in_(["config.brand.updated", "config.brand.logo_uploaded"]),
        ))).scalars().all()
    assert "config.brand.updated" in actions
    assert "config.brand.logo_uploaded" in actions


async def test_workspace_logo_rejects_non_image_upload(ctx):
    async with _client() as c:
        r = await c.post(
            "/api/console/workspace/logo",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            data={"kind": "light"},
            files={"file": ("logo.txt", b"not an image", "text/plain")},
        )
    assert r.status_code == 422, r.text


async def test_workspace_palette_rejects_unknown_or_invalid_swatch(ctx):
    async with _client() as c:
        unknown = await c.patch(
            "/api/console/workspace",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={"palette": {"brand": "#395262", "shadow": "#000000"}},
        )
        invalid = await c.patch(
            "/api/console/workspace",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={"palette": {"brand": "blue"}},
        )
    assert unknown.status_code == 422, unknown.text
    assert invalid.status_code == 422, invalid.text


async def test_calendar_category_roles_and_workspace_defaults_persist(ctx):
    async with _client() as c:
        roles = await c.get("/api/console/roles", headers=_H(ctx["a"]["admin"], ctx["a"]["host"]))
        assert roles.status_code == 200, roles.text
        role_ids = [item["id"] for item in roles.json()["items"][:2]]
        created = await c.post(
            "/api/console/calendar-categories",
            headers=_H(ctx["a"]["admin"], ctx["a"]["host"]),
            json={
                "name": "Listings Calendar",
                "color": "#aecbd4",
                "calendar_address": "listings@example.test",
                "role_ids": role_ids,
                "active": True,
            },
        )
        defaults = await c.patch(
            "/api/console/workspace",
            headers=_H(ctx["a"]["admin"], ctx["a"]["host"]),
            json={
                "timezone": "America/Chicago",
                "week_starts_on": 0,
                "default_calendar_view": "agenda",
            },
        )
        refreshed_categories = await c.get("/api/console/calendar-categories", headers=_H(ctx["a"]["admin"], ctx["a"]["host"]))
        refreshed_workspace = await c.get("/api/console/workspace", headers=_H(ctx["a"]["admin"], ctx["a"]["host"]))
    assert created.status_code == 200, created.text
    item = created.json()["item"]
    assert item["color"] == "#AECBD4"
    assert item["role_ids"] == role_ids
    assert defaults.status_code == 200, defaults.text
    assert refreshed_categories.status_code == 200, refreshed_categories.text
    got = next(row for row in refreshed_categories.json()["items"] if row["id"] == item["id"])
    assert got["calendar_address"] == "listings@example.test"
    assert got["role_ids"] == role_ids
    assert refreshed_workspace.status_code == 200, refreshed_workspace.text
    assert refreshed_workspace.json()["timezone"] == "America/Chicago"
    assert refreshed_workspace.json()["week_starts_on"] == 0
    assert refreshed_workspace.json()["default_calendar_view"] == "agenda"
    async with SessionLocal() as s:
        actions = (await s.execute(select(AuditLog.action).where(
            AuditLog.tenant_id == uuid.UUID(ctx["a"]["tenant_id"]),
            AuditLog.action.in_(["config.calendar.created", "config.calendar.updated"]),
        ))).scalars().all()
    assert "config.calendar.created" in actions
    assert "config.calendar.updated" in actions


async def test_calendar_category_rejects_invalid_color_or_role_list(ctx):
    async with _client() as c:
        bad_color = await c.post(
            "/api/console/calendar-categories",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={"name": "Bad Color", "color": "gold"},
        )
        bad_roles = await c.post(
            "/api/console/calendar-categories",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={"name": "Bad Roles", "role_ids": "everyone"},
        )
    assert bad_color.status_code == 422, bad_color.text
    assert bad_roles.status_code == 422, bad_roles.text


async def test_integrations_scrub_secret_config_and_derive_status(ctx):
    integration_id = ctx["a"]["ids"]["integration"]
    async with _client() as c:
        patched = await c.patch(
            f"/api/console/integrations/{integration_id}",
            headers=_H(ctx["a"]["admin"], ctx["a"]["host"]),
            json={
                "base_url": "example.test/app",
                "config": {
                    "calendar_id": "team-calendar",
                    "marketing_request_destination": "ops-channel",
                    "api_key": "must-not-persist",
                    "client_secret": "must-not-persist",
                },
            },
        )
        refreshed = await c.get("/api/console/integrations", headers=_H(ctx["a"]["admin"], ctx["a"]["host"]))
    assert patched.status_code == 200, patched.text
    item = patched.json()["item"]
    assert item["base_url"] == "https://example.test/app"
    assert item["config"] == {
        "calendar_id": "team-calendar",
        "marketing_request_destination": "ops-channel",
    }
    assert "must-not-persist" not in patched.text
    assert item["status"] == "Not Connected"
    assert refreshed.status_code == 200, refreshed.text
    async with SessionLocal() as s:
        row = await s.get(IntranetIntegration, uuid.UUID(integration_id))
        assert row.config == item["config"]
        row.last_error = "Forced failure"
        await s.commit()
    async with _client() as c:
        failed = await c.get("/api/console/integrations", headers=_H(ctx["a"]["admin"], ctx["a"]["host"]))
    failed_item = next(row for row in failed.json()["items"] if row["id"] == integration_id)
    assert failed_item["status"] == "Action Needed"
    async with SessionLocal() as s:
        row = await s.get(IntranetIntegration, uuid.UUID(integration_id))
        row.last_error = None
        row.last_sync_status = "ok"
        row.last_sync_at = dt.datetime.now(dt.timezone.utc)
        await s.commit()
    async with _client() as c:
        synced = await c.get("/api/console/integrations", headers=_H(ctx["a"]["admin"], ctx["a"]["host"]))
    synced_item = next(row for row in synced.json()["items"] if row["id"] == integration_id)
    assert synced_item["status"] == "Connected"


async def test_integration_test_run_writes_system_audit(ctx):
    integration_id = ctx["b"]["ids"]["integration"]
    async with _client() as c:
        tested = await c.post(
            f"/api/console/integrations/{integration_id}/test",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
        )
    assert tested.status_code == 200, tested.text
    assert tested.json()["item"]["status"] == "Action Needed"
    assert tested.json()["ok"] is False
    async with SessionLocal() as s:
        event = (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == uuid.UUID(ctx["b"]["tenant_id"]),
            AuditLog.action == "config.integration.test_run",
        ).order_by(AuditLog.created_at.desc()).limit(1))).scalar_one()
    assert event.category == "System"
    assert event.summary


async def test_ai_settings_and_sources_persist_without_invented_index_counts(ctx):
    source_id = ctx["a"]["ids"]["ai_source"]
    role_id = ctx["a"]["ids"]["role_ids"][0]
    async with _client() as c:
        settings = await c.patch(
            "/api/console/ai/settings",
            headers=_H(ctx["a"]["admin"], ctx["a"]["host"]),
            json={
                "always_cite": False,
                "refuse_without_source": True,
                "offer_escalation": False,
                "learn_from_corrections": True,
                "escalation_channel": "ops-channel",
            },
        )
        source = await c.patch(
            f"/api/console/ai/sources/{source_id}",
            headers=_H(ctx["a"]["admin"], ctx["a"]["host"]),
            json={"enabled": False, "min_role_id": role_id},
        )
        refreshed = await c.get("/api/console/ai", headers=_H(ctx["a"]["admin"], ctx["a"]["host"]))
    assert settings.status_code == 200, settings.text
    assert settings.json()["item"]["always_cite"] is False
    assert settings.json()["item"]["learn_from_corrections"] is True
    assert source.status_code == 200, source.text
    assert source.json()["item"]["enabled"] is False
    assert source.json()["item"]["min_role_id"] == role_id
    assert source.json()["item"]["indexed_item_count"] == 0
    assert source.json()["item"]["crawl_status"] == "Not yet indexed"
    assert refreshed.status_code == 200, refreshed.text
    items = refreshed.json()["sources"]["items"]
    assert all(item["indexed_item_count"] == 0 for item in items)
    assert all(item["crawl_status"] == "Not yet indexed" for item in items)
    async with SessionLocal() as s:
        actions = (await s.execute(select(AuditLog.action).where(
            AuditLog.tenant_id == uuid.UUID(ctx["a"]["tenant_id"]),
            AuditLog.action.in_(["config.ai.settings_updated", "config.ai.source_updated"]),
        ))).scalars().all()
    assert "config.ai.settings_updated" in actions
    assert "config.ai.source_updated" in actions


async def test_content_gap_assignment_writes_content_gap_assigned(ctx):
    gap_id = ctx["b"]["ids"]["content_gap"]
    member_id = ctx["b"]["ids"]["member"]
    async with _client() as c:
        assigned = await c.patch(
            f"/api/console/content-gaps/{gap_id}",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={"status": "Assigned", "assigned_member_id": member_id},
        )
        refreshed = await c.get("/api/console/content-gaps", headers=_H(ctx["b"]["admin"], ctx["b"]["host"]))
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["item"]["assigned_member_id"] == member_id
    assert refreshed.status_code == 200, refreshed.text
    got = next(row for row in refreshed.json()["items"] if row["id"] == gap_id)
    assert got["assigned_member_id"] == member_id
    assert got["ask_count"] == 3
    async with SessionLocal() as s:
        event = (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == uuid.UUID(ctx["b"]["tenant_id"]),
            AuditLog.action == "content.gap.assigned",
        ).order_by(AuditLog.created_at.desc()).limit(1))).scalar_one()
    assert event.category == "AI"


async def test_audit_filters_and_cursor_pagination_are_server_side(ctx):
    tid = uuid.UUID(ctx["a"]["tenant_id"])
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        s.add_all([
            AuditLog(tenant_id=tid, actor_label="System", action="read.config", category="Read",
                     summary="Audit read marker 1", created_at=now - dt.timedelta(seconds=1)),
            AuditLog(tenant_id=tid, actor_label="System", action="read.preview", category="Read",
                     summary="Audit read marker 2", created_at=now - dt.timedelta(seconds=2)),
            AuditLog(tenant_id=tid, actor_label="System", action="read.audit", category="Read",
                     summary="Audit read marker 3", created_at=now - dt.timedelta(seconds=3)),
            AuditLog(tenant_id=tid, actor_label="System", action="access.member.updated", category="People",
                     summary="Audit access marker", created_at=now - dt.timedelta(seconds=4)),
            AuditLog(tenant_id=tid, actor_label="System", action="content.course.updated", category="Training",
                     summary="Audit content marker", created_at=now - dt.timedelta(seconds=5)),
            AuditLog(tenant_id=tid, actor_label="System", action="console.publish", category="Publish",
                     summary="Audit publish marker", created_at=now - dt.timedelta(seconds=6)),
        ])
        await s.commit()
    async with _client() as c:
        page_one = await c.get("/api/console/audit?category=Read&limit=2", headers=_H(ctx["a"]["admin"], ctx["a"]["host"]))
        page_two = await c.get(
            f"/api/console/audit?category=Read&limit=2&before={page_one.json()['cursor']}",
            headers=_H(ctx["a"]["admin"], ctx["a"]["host"]),
        )
        access = await c.get("/api/console/audit?category=Access&limit=30", headers=_H(ctx["a"]["admin"], ctx["a"]["host"]))
        content = await c.get("/api/console/audit?category=Content&limit=30", headers=_H(ctx["a"]["admin"], ctx["a"]["host"]))
        publish = await c.get("/api/console/audit?category=Publish&limit=30", headers=_H(ctx["a"]["admin"], ctx["a"]["host"]))
    assert page_one.status_code == 200, page_one.text
    assert page_two.status_code == 200, page_two.text
    first_summaries = [row["summary"] for row in page_one.json()["items"]]
    second_summaries = [row["summary"] for row in page_two.json()["items"]]
    assert first_summaries == ["Audit read marker 1", "Audit read marker 2"]
    assert "Audit read marker 3" in second_summaries
    assert "Audit access marker" in [row["summary"] for row in access.json()["items"]]
    assert "Audit content marker" in [row["summary"] for row in content.json()["items"]]
    assert "Audit publish marker" in [row["summary"] for row in publish.json()["items"]]


@pytest.mark.parametrize(
    "filter_name,field,allowed",
    [
        ("active", "status", {"Active"}),
        ("pending", "status", {"Invited"}),
        ("guests", "role_key", {"jv_partner"}),
        ("leadership", "role_key", {"ops_admin", "team_leader"}),
    ],
)
async def test_roster_filters_are_resolved_by_the_server(ctx, filter_name, field, allowed):
    async with _client() as c:
        r = await c.get(
            f"/api/console/members?filter={filter_name}",
            headers=_H(ctx["a"]["admin"], ctx["a"]["host"]),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "stats" in body and "roles" in body
    assert body["items"]
    assert {item[field] for item in body["items"]} <= allowed


async def test_roster_guest_invite_defaults_to_jv_partner(ctx):
    email = f"guest-{uuid.uuid4().hex[:8]}@example.test"
    async with _client() as c:
        r = await c.post(
            "/api/console/members/invite",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={"full_name": "Guest Partner", "email": email, "auth_source": "Guest"},
        )
    assert r.status_code == 200, r.text
    body = r.json()["item"]
    assert body["status"] == "Invited"
    assert body["auth_source"] == "Guest"
    assert body["role_key"] == "jv_partner"


async def test_permissions_reject_zero_console_access_full_roles(ctx):
    async with _client() as c:
        current = await c.get("/api/console/permissions", headers=_H(ctx["b"]["admin"], ctx["b"]["host"]))
        assert current.status_code == 200, current.text
        body = current.json()
        console_cap = next(cap for cap in body["capabilities"] if cap["key"] == "console_access")
        items = [
            {
                "capability_id": item["capability_id"],
                "role_id": item["role_id"],
                "level": "None" if item["capability_id"] == console_cap["id"] else item["level"],
            }
            for item in body["items"]
        ]
        r = await c.put("/api/console/permissions", headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
                        json={"items": items})
    assert r.status_code == 422, r.text
    assert "console_access=Full" in r.text


async def test_permissions_audit_names_changed_cell(ctx):
    async with _client() as c:
        current = await c.get("/api/console/permissions", headers=_H(ctx["b"]["admin"], ctx["b"]["host"]))
        assert current.status_code == 200, current.text
        body = current.json()
        cap = next(cap for cap in body["capabilities"] if cap["key"] != "console_access")
        role = body["roles"][0]
        old = next(item for item in body["items"]
                   if item["capability_id"] == cap["id"] and item["role_id"] == role["id"])
        new_level = "View" if old["level"] != "View" else "Full"
        items = [
            {
                "capability_id": item["capability_id"],
                "role_id": item["role_id"],
                "level": new_level if item["id"] == old["id"] else item["level"],
            }
            for item in body["items"]
        ]
        r = await c.put("/api/console/permissions", headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
                        json={"items": items})
    assert r.status_code == 200, r.text
    async with SessionLocal() as s:
        latest = (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == uuid.UUID(ctx["b"]["tenant_id"]),
            AuditLog.action == "access.permissions.updated",
        ).order_by(AuditLog.created_at.desc()).limit(1))).scalar_one()
    assert latest.action == "access.permissions.updated"
    assert cap["name"] in latest.summary
    assert role["name"] in latest.summary
    assert old["level"] in latest.summary
    assert new_level in latest.summary


async def test_launchpad_tile_url_normalizes_to_https(ctx):
    async with _client() as c:
        r = await c.post(
            "/api/console/tiles",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={"name": "Normalized URL", "url": "example.test/tool", "auth_type": "Link"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["item"]["url"] == "https://example.test/tool"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/plain,test"])
async def test_launchpad_tile_rejects_unsafe_url_schemes(ctx, url):
    async with _client() as c:
        r = await c.post(
            "/api/console/tiles",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={"name": "Unsafe URL", "url": url, "auth_type": "Link"},
        )
    assert r.status_code == 422, r.text


async def test_wtd_lists_include_queried_stats_and_provider_metadata(ctx):
    async with _client() as c:
        r = await c.get("/api/console/wtd-lists", headers=_H(ctx["b"]["admin"], ctx["b"]["host"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stats"] == {
        "lists_in_run": 12,
        "paired_scripts": 12,
        "daily_touch_target": 103,
    }
    fub = body["integrations"]["follow_up_boss"]
    assert fub["status"] == "Not Connected"
    assert fub["base_url"].endswith("/2/people/list/")


async def test_wtd_daily_target_is_positive_or_null(ctx):
    async with _client() as c:
        null_target = await c.patch(
            f"/api/console/wtd-lists/{ctx['b']['ids']['wtd']}",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={"daily_target": None},
        )
        zero_target = await c.patch(
            f"/api/console/wtd-lists/{ctx['b']['ids']['wtd']}",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={"daily_target": 0},
        )
    assert null_target.status_code == 200, null_target.text
    assert null_target.json()["item"]["daily_target"] is None
    assert zero_target.status_code == 422, zero_target.text


async def test_wtd_patch_writes_content_audit_action(ctx):
    async with _client() as c:
        r = await c.patch(
            f"/api/console/wtd-lists/{ctx['b']['ids']['wtd']}",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={"active": False},
        )
    assert r.status_code == 200, r.text
    async with SessionLocal() as s:
        latest = (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == uuid.UUID(ctx["b"]["tenant_id"]),
            AuditLog.action == "content.wtd_list.updated",
        ).order_by(AuditLog.created_at.desc()).limit(1))).scalar_one()
    assert latest.action == "content.wtd_list.updated"


async def test_training_create_course_and_lesson_updates_derived_counts(ctx):
    async with _client() as c:
        course = await c.post(
            "/api/console/courses",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={
                "title": "Contract Writing Lab",
                "category": "Transactions",
                "description": "Practice course for accepted offers.",
                "state": "Draft",
                "track_progress": True,
                "required_for_onboarding": False,
                "issues_certificate": False,
                "sequential": True,
            },
        )
        assert course.status_code == 200, course.text
        course_id = course.json()["item"]["id"]
        lesson = await c.post(
            f"/api/console/courses/{course_id}/lessons",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={
                "title": "Write the Offer",
                "source_type": "HERE",
                "source_ref": "utah-life/training/write-the-offer",
                "source_label": "Hosted lesson",
                "duration_minutes": 17,
                "required": True,
            },
        )
        assert lesson.status_code == 200, lesson.text
        detail = await c.get(f"/api/console/courses/{course_id}", headers=_H(ctx["b"]["admin"], ctx["b"]["host"]))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["lesson_count"] == 1
    assert body["total_duration_minutes"] == 17
    assert body["lessons"][0]["source_type"] == "HERE"


async def test_training_course_toggles_persist(ctx):
    course_id = ctx["b"]["ids"]["course"]
    async with _client() as c:
        patched = await c.patch(
            f"/api/console/courses/{course_id}",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={
                "track_progress": False,
                "required_for_onboarding": True,
                "issues_certificate": True,
                "sequential": True,
            },
        )
        assert patched.status_code == 200, patched.text
        detail = await c.get(f"/api/console/courses/{course_id}", headers=_H(ctx["b"]["admin"], ctx["b"]["host"]))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["track_progress"] is False
    assert body["required_for_onboarding"] is True
    assert body["issues_certificate"] is True
    assert body["sequential"] is True


async def test_training_lesson_order_persists(ctx):
    course_id = ctx["b"]["ids"]["course"]
    async with _client() as c:
        before = await c.get(f"/api/console/courses/{course_id}", headers=_H(ctx["b"]["admin"], ctx["b"]["host"]))
        assert before.status_code == 200, before.text
        ids = [lesson["id"] for lesson in before.json()["lessons"]]
        ordered = list(reversed(ids))
        moved = await c.put(
            f"/api/console/courses/{course_id}/lessons/order",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={"ids": ordered},
        )
        assert moved.status_code == 200, moved.text
        after = await c.get(f"/api/console/courses/{course_id}", headers=_H(ctx["b"]["admin"], ctx["b"]["host"]))
    assert after.status_code == 200, after.text
    assert [lesson["id"] for lesson in after.json()["lessons"]] == ordered


async def test_training_writes_content_audit_actions(ctx):
    async with _client() as c:
        course = await c.post(
            "/api/console/courses",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={"title": "Audit Course", "category": "Ops", "state": "Draft"},
        )
        assert course.status_code == 200, course.text
        lesson = await c.post(
            f"/api/console/courses/{course.json()['item']['id']}/lessons",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={"title": "Audit Lesson", "source_type": "PLACE"},
        )
    assert lesson.status_code == 200, lesson.text
    async with SessionLocal() as s:
        actions = (await s.execute(select(AuditLog.action).where(
            AuditLog.tenant_id == uuid.UUID(ctx["b"]["tenant_id"]),
            AuditLog.action.in_(["content.course.created", "content.lesson.created"]),
        ))).scalars().all()
    assert "content.course.created" in actions
    assert "content.lesson.created" in actions


async def test_sops_include_health_and_category_counts(ctx):
    async with _client() as c:
        sops = await c.get("/api/console/sops", headers=_H(ctx["b"]["admin"], ctx["b"]["host"]))
        categories = await c.get("/api/console/sop-categories", headers=_H(ctx["b"]["admin"], ctx["b"]["host"]))
    assert sops.status_code == 200, sops.text
    assert categories.status_code == 200, categories.text
    health = sops.json()["health"]
    assert set(health) == {"current", "due_soon", "overdue"}
    assert sum(health.values()) == sops.json()["total"]
    assert sum(category["sop_count"] for category in categories.json()["items"]) == sops.json()["total"]


async def test_sop_upload_downloads_byte_identical_and_moves_current_version(ctx):
    sop_id = ctx["b"]["ids"]["sop"]
    first = b"%PDF-1.4\nfirst upload\n"
    second = b"%PDF-1.4\nsecond upload\n"
    label_a = f"v-{uuid.uuid4().hex}"
    label_b = f"v-{uuid.uuid4().hex}"
    async with _client() as c:
        first_upload = await c.post(
            f"/api/console/sops/{sop_id}/versions",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            data={"version_label": label_a},
            files={"file": ("policy.pdf", first, "application/pdf")},
        )
        assert first_upload.status_code == 200, first_upload.text
        first_version = first_upload.json()["item"]["id"]
        second_upload = await c.post(
            f"/api/console/sops/{sop_id}/versions",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            data={"version_label": label_b},
            files={"file": ("policy.pdf", second, "application/pdf")},
        )
        assert second_upload.status_code == 200, second_upload.text
        second_version = second_upload.json()["item"]["id"]
        detail = await c.get(f"/api/console/sops/{sop_id}", headers=_H(ctx["b"]["admin"], ctx["b"]["host"]))
        versions = await c.get(f"/api/console/sops/{sop_id}/versions", headers=_H(ctx["b"]["admin"], ctx["b"]["host"]))
        downloaded = await c.get(
            f"/api/console/sops/{sop_id}/versions/{second_version}/download",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
        )
    assert detail.status_code == 200, detail.text
    assert detail.json()["current_version_id"] == second_version
    assert first_version in {version["id"] for version in versions.json()["items"]}
    assert second_version in {version["id"] for version in versions.json()["items"]}
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == second


async def test_sop_duplicate_version_label_returns_422(ctx):
    sop_id = ctx["b"]["ids"]["sop"]
    label = f"v-{uuid.uuid4().hex}"
    async with _client() as c:
        first = await c.post(
            f"/api/console/sops/{sop_id}/versions",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            data={"version_label": label},
            files={"file": ("dup.pdf", b"%PDF-1.4", "application/pdf")},
        )
        duplicate = await c.post(
            f"/api/console/sops/{sop_id}/versions",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            data={"version_label": label},
            files={"file": ("dup.pdf", b"%PDF-1.4", "application/pdf")},
        )
    assert first.status_code == 200, first.text
    assert duplicate.status_code == 422, duplicate.text


async def test_sop_writes_spec_audit_actions(ctx):
    async with _client() as c:
        created = await c.post(
            "/api/console/sops",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={"title": "Audit SOP", "category_id": ctx["b"]["ids"]["category"], "state": "Draft"},
        )
        assert created.status_code == 200, created.text
        patched = await c.patch(
            f"/api/console/sops/{created.json()['item']['id']}",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            json={"state": "Live"},
        )
        assert patched.status_code == 200, patched.text
        version = await c.post(
            f"/api/console/sops/{created.json()['item']['id']}/versions",
            headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
            data={"version_label": f"v-{uuid.uuid4().hex}"},
            files={"file": ("audit.pdf", b"%PDF-1.4", "application/pdf")},
        )
    assert version.status_code == 200, version.text
    async with SessionLocal() as s:
        actions = (await s.execute(select(AuditLog.action).where(
            AuditLog.tenant_id == uuid.UUID(ctx["b"]["tenant_id"]),
            AuditLog.action.in_([
                "content.sop.created",
                "content.sop.state_changed",
                "content.sop.version_uploaded",
            ]),
        ))).scalars().all()
    assert "content.sop.created" in actions
    assert "content.sop.state_changed" in actions
    assert "content.sop.version_uploaded" in actions


def _request_for_name(name: str, ids: dict):
    for read_name, method, path_fn, kwargs in READ_ROUTES:
        if read_name == name:
            return method, path_fn(ids), kwargs
    return _mutation_request(name, ids)


@pytest.mark.parametrize("name", ALL_ROUTE_NAMES)
async def test_console_endpoints_require_authentication(ctx, name):
    method, path, kwargs = _request_for_name(name, ctx["a"]["ids"])
    async with _client() as c:
        r = await _request(c, method, path, None, kwargs)
    assert r.status_code == 401, f"{name}: {r.status_code} {r.text}"


@pytest.mark.parametrize("name", ALL_ROUTE_NAMES)
async def test_buyer_agent_gets_403_from_every_console_endpoint(ctx, name):
    method, path, kwargs = _request_for_name(name, ctx["a"]["ids"])
    async with _client() as c:
        r = await _request(c, method, path, _H(ctx["a"]["agent"], ctx["a"]["host"]), kwargs)
    assert r.status_code == 403, f"{name}: {r.status_code} {r.text}"


@pytest.mark.parametrize("name", MUTATION_ROUTES)
async def test_mutating_console_endpoints_write_exactly_one_audit_row(ctx, name):
    method, path, kwargs = _mutation_request(name, ctx["a"]["ids"])
    async with SessionLocal() as s:
        before = (await s.execute(select(func.count(AuditLog.id)).where(
            AuditLog.tenant_id == uuid.UUID(ctx["a"]["tenant_id"])
        ))).scalar_one()
    async with _client() as c:
        r = await _request(c, method, path, _H(ctx["a"]["admin"], ctx["a"]["host"]), kwargs)
    assert r.status_code == 200, f"{name}: {r.status_code} {r.text}"
    async with SessionLocal() as s:
        after = (await s.execute(select(func.count(AuditLog.id)).where(
            AuditLog.tenant_id == uuid.UUID(ctx["a"]["tenant_id"])
        ))).scalar_one()
        latest = (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == uuid.UUID(ctx["a"]["tenant_id"])
        ).order_by(AuditLog.created_at.desc()).limit(1))).scalar_one()
    assert after == before + 1, name
    assert latest.summary, name
CROSS_TENANT_ROUTES = [
    ("role", "PATCH", lambda ids: f"/api/console/roles/{ids['role']}", {"json": {"name": "Nope"}}),
    ("permission", "PUT", lambda ids: "/api/console/permissions",
     {"json": {"items": [{"capability_id": "FOREIGN_CAP", "role_id": "FOREIGN_ROLE", "level": "Full"}] * 50}}),
    ("member", "PATCH", lambda ids: f"/api/console/members/{ids['member']}", {"json": {"market": "Nope"}}),
    ("course", "PATCH", lambda ids: f"/api/console/courses/{ids['course']}", {"json": {"title": "Nope"}}),
    ("lesson", "PATCH", lambda ids: f"/api/console/courses/{ids['course']}/lessons/{ids['lesson']}",
     {"json": {"title": "Nope"}}),
    ("sop_category", "PATCH", lambda ids: f"/api/console/sop-categories/{ids['category']}", {"json": {"name": "Nope"}}),
    ("sop", "PATCH", lambda ids: f"/api/console/sops/{ids['sop']}", {"json": {"title": "Nope"}}),
    ("wtd", "PATCH", lambda ids: f"/api/console/wtd-lists/{ids['wtd']}", {"json": {"name": "Nope"}}),
    ("tile", "PATCH", lambda ids: f"/api/console/tiles/{ids['tile']}", {"json": {"name": "Nope"}}),
    ("calendar", "PATCH", lambda ids: f"/api/console/calendar-categories/{ids['calendar']}", {"json": {"name": "Nope"}}),
    ("integration", "PATCH", lambda ids: f"/api/console/integrations/{ids['integration']}", {"json": {"status": "Not Connected"}}),
    ("ai_source", "PATCH", lambda ids: f"/api/console/ai/sources/{ids['ai_source']}", {"json": {"enabled": True}}),
    ("content_gap", "PATCH", lambda ids: f"/api/console/content-gaps/{ids['content_gap']}", {"json": {"status": "Assigned"}}),
    ("batch", "POST", lambda ids: f"/api/console/publish/batches/{ids['batch']}/rollback", {}),
]


@pytest.mark.parametrize("name,method,path_fn,kwargs", CROSS_TENANT_ROUTES, ids=[r[0] for r in CROSS_TENANT_ROUTES])
async def test_cross_tenant_ids_return_404(ctx, name, method, path_fn, kwargs):
    ids = dict(ctx["b"]["ids"])
    if name == "permission":
        body = kwargs["json"]
        body["items"] = [
            {"capability_id": ids["capability"], "role_id": ids["role"], "level": "Full"}
        ] * 50
        kwargs = {"json": body}
    async with _client() as c:
        r = await _request(c, method, path_fn(ids), _H(ctx["a"]["admin"], ctx["a"]["host"]), kwargs)
    assert r.status_code == 404, f"{name}: {r.status_code} {r.text}"


INVALID_ROUTES = [
    ("workspace", "PATCH", "/api/console/workspace", {"json": {"week_starts_on": 9}}),
    ("logo", "POST", "/api/console/workspace/logo",
     {"data": {"kind": "seal"}, "files": {"file": ("x.png", b"x", "image/png")}}),
    ("role", "PATCH", "/api/console/roles/not-a-uuid", {"json": {"name": "Nope"}}),
    ("permissions", "PUT", "/api/console/permissions", {"json": {"items": []}}),
    ("member_invite", "POST", "/api/console/members/invite", {"json": {"email": "not-email"}}),
    ("course", "POST", "/api/console/courses", {"json": {"state": "Published"}}),
    ("lesson", "POST", None, {"json": {"source_type": "VIDEO"}}),
    ("sop_category", "POST", "/api/console/sop-categories", {"json": {"name": ""}}),
    ("sop", "POST", "/api/console/sops", {"json": {"title": ""}}),
    ("wtd", "PATCH", None, {"json": {"daily_target": -1}}),
    ("tile", "POST", "/api/console/tiles", {"json": {"auth_type": "Password"}}),
    ("calendar", "POST", "/api/console/calendar-categories", {"json": {"name": ""}}),
    ("integration", "PATCH", None, {"json": {"status": "Unknown"}}),
    ("ai_settings", "PATCH", "/api/console/ai/settings", {"json": {"always_cite": "yes"}}),
    ("marketing_type", "PATCH", "/api/console/marketing",
     {"json": {"destination_type": "carrier_pigeon"}}),
    ("ai_source", "PATCH", None, {"json": {"enabled": "yes"}}),
    ("content_gap", "PATCH", None, {"json": {"status": "Done"}}),
    ("setup", "PATCH", None, {"json": {"completed": "yes"}}),
    ("audit", "GET", "/api/console/audit?limit=0", {}),
    ("preview", "GET", "/api/console/preview", {}),
    ("rollback", "POST", "/api/console/publish/batches/not-a-uuid/rollback", {}),
]


@pytest.mark.parametrize("name,method,path,kwargs", INVALID_ROUTES, ids=[r[0] for r in INVALID_ROUTES])
async def test_invalid_console_input_returns_422(ctx, name, method, path, kwargs):
    ids = ctx["a"]["ids"]
    if path is None:
        path = {
            "lesson": f"/api/console/courses/{ids['course']}/lessons",
            "wtd": f"/api/console/wtd-lists/{ids['wtd']}",
            "integration": f"/api/console/integrations/{ids['integration']}",
            "ai_source": f"/api/console/ai/sources/{ids['ai_source']}",
            "content_gap": f"/api/console/content-gaps/{ids['content_gap']}",
            "setup": f"/api/console/setup-tasks/{ids['setup_key']}",
        }[name]
    async with _client() as c:
        r = await _request(c, method, path, _H(ctx["a"]["admin"], ctx["a"]["host"]), kwargs)
    assert r.status_code == 422, f"{name}: {r.status_code} {r.text}"


async def test_marketing_requests_cannot_be_enabled_without_somewhere_to_send(ctx):
    """Enabling the form while the destination is unset is refused, not saved.

    The handoff's rule is that an unconfigured feature renders an honest empty state. A saved
    `enabled: true` over `destination_type: none` is the opposite of that: the intranet would put
    a working request form in front of an agent, take their listing details, and have nowhere to
    deliver them. The failure would land on the person who filled the form in.
    """
    async with _client() as c:
        h = _H(ctx["a"]["admin"], ctx["a"]["host"])
        blocked = await c.patch("/api/console/marketing", headers=h, json={"enabled": True})
        assert blocked.status_code == 422, blocked.text

        ok = await c.patch("/api/console/marketing", headers=h, json={
            "destination_type": "slack", "destination": "#marketing", "enabled": True})
    assert ok.status_code == 200, ok.text
    assert ok.json()["item"]["enabled"] is True


@pytest.mark.parametrize("kind,bad,good", [
    ("slack", "marketing", "#marketing"),
    ("email", "marketing-team", "marketing@example.test"),
    ("webhook", "not a url", "https://hooks.example.test/abc"),
])
async def test_marketing_destination_is_validated_for_the_type_it_claims_to_be(ctx, kind, bad, good):
    """"Destination" means three different things, so one string check for all three is no check.

    Without this a workspace can save `#marketing` as a webhook URL and only find out when a real
    request fails to deliver -- by which time the person who submitted it has gone home.
    """
    async with _client() as c:
        h = _H(ctx["b"]["admin"], ctx["b"]["host"])
        await c.patch("/api/console/marketing", headers=h, json={"destination_type": kind})
        rejected = await c.patch("/api/console/marketing", headers=h, json={"destination": bad})
        accepted = await c.patch("/api/console/marketing", headers=h, json={"destination": good})
    assert rejected.status_code == 422, f"{kind} accepted {bad!r}: {rejected.text}"
    assert accepted.status_code == 200, accepted.text


async def test_a_webhook_destination_is_stored_normalised(ctx):
    """The scheme is pinned because the server will later POST to this value on a user's behalf,
    which is SSRF surface (handoff Sec 17). Storing what the user typed rather than what was
    validated would mean the check and the request disagree."""
    async with _client() as c:
        h = _H(ctx["b"]["admin"], ctx["b"]["host"])
        await c.patch("/api/console/marketing", headers=h, json={"destination_type": "webhook"})
        r = await c.patch("/api/console/marketing", headers=h,
                          json={"destination": "http://hooks.example.test/x"})
    assert r.status_code == 200, r.text
    assert r.json()["item"]["destination"] == "https://hooks.example.test/x"


async def test_marketing_required_fields_reject_keys_nothing_renders(ctx):
    """These keys drive the intranet's form. An unknown one is a required field with no input
    behind it, which makes a request impossible to submit and impossible to diagnose."""
    async with _client() as c:
        h = _H(ctx["a"]["admin"], ctx["a"]["host"])
        bad = await c.patch("/api/console/marketing", headers=h,
                            json={"required_fields": ["listing", "blood_type"]})
        good = await c.patch("/api/console/marketing", headers=h,
                             json={"required_fields": ["due_date", "listing"]})
    assert bad.status_code == 422, bad.text
    assert good.status_code == 200, good.text
    # Stored against the vocabulary's own order, so two equivalent saves compare equal.
    assert good.json()["item"]["required_fields"] == ["due_date", "listing"]


async def test_marketing_default_role_cannot_be_borrowed_from_another_tenant(ctx):
    """A role id is a tenant-scoped object reference reaching the API from a browser."""
    other_role = ctx["b"]["ids"]["role"]
    async with _client() as c:
        r = await c.patch("/api/console/marketing",
                          headers=_H(ctx["a"]["admin"], ctx["a"]["host"]),
                          json={"default_role_id": str(other_role)})
    assert r.status_code in (403, 404), f"cross-tenant role accepted: {r.status_code} {r.text}"


async def test_marketing_reports_configuration_and_delivery_as_separate_facts(ctx):
    """There is no `connected` flag, deliberately.

    A saved form proves the config is complete. It proves nothing about whether anything can be
    delivered to the destination -- nobody has tried. Collapsing the two into one boolean is how a
    console ends up claiming a connection that has never been exercised, which is the state this
    product is genuinely in until the Phase 10 delivery path exists.
    """
    async with _client() as c:
        h = _H(ctx["a"]["admin"], ctx["a"]["host"])
        r = await c.patch("/api/console/marketing", headers=h, json={
            "destination_type": "email", "destination": "marketing@example.test", "enabled": True})
        cfg = await c.get("/api/console/config", headers=h)
    item = r.json()["item"]
    assert item["config_complete"] is True
    assert item["last_tested_at"] is None, "nothing has delivered; this must stay null"
    assert item["delivery"] == "pending_runtime"
    assert "connected" not in item, "config cannot assert a connection it has not made"
    assert cfg.json()["marketing"]["destination"] == "marketing@example.test"


def test_the_publish_cycle_finds_every_publishable_table_by_itself():
    """`_set_publish_state` used to be a hand-written tuple of seventeen model classes.

    A publishable table added without being added to that tuple is never marked published:
    `draft_dirty` stays true for the life of the workspace, the console keeps offering a publish
    that does nothing for it, and the live intranet keeps serving pre-publish state. Nothing
    raises. That is not hypothetical -- intranet_marketing_setting was written and the tuple did
    not know about it, which is what prompted this.

    The three columns are the contract, so this asserts the derivation still agrees with the
    columns rather than with a list somebody has to remember to update.
    """
    from app.models import Base
    from app.routers.console import _publishable_models

    by_columns = {
        mapper.class_.__name__
        for mapper in Base.registry.mappers
        if {"tenant_id", "published_at", "draft_dirty"} <= {c.key for c in mapper.columns}
    }
    found = {model.__name__ for model in _publishable_models()}
    assert found == by_columns, f"publish cycle and schema disagree: {found ^ by_columns}"
    # Guards the derivation itself: if this ever returns nothing, publish silently no-ops.
    assert len(found) > 15, f"suspiciously few publishable models: {sorted(found)}"
    assert "IntranetMarketingSetting" in found


async def test_a_setup_task_cannot_be_ticked_past_its_own_configuration(ctx):
    """The checklist was eleven manual checkboxes: `completed` was whatever somebody clicked.

    A workspace could therefore show "SOPs uploaded" complete with no SOPs. That matters because
    the checklist is what an operator reads to decide whether a workspace is ready for real users
    -- a green row over an empty library makes that read wrong, and whoever trusted it pays.

    THIS TEST MAKES ITS OWN CONDITION rather than hunting for an unconfigured task in the seed.
    The first version looked for any task with `satisfied: false`, which passed alone and failed
    in the full suite: other tests configure this workspace, so by the time it ran there was
    nothing left unconfigured and the search came up empty. A test that depends on how much of
    the workspace its neighbours happen to have filled in is testing the neighbours.
    """
    tenant_id = uuid.UUID(ctx["b"]["tenant_id"])
    async with SessionLocal() as s:
        ws = (await s.execute(select(IntranetWorkspace).where(
            IntranetWorkspace.tenant_id == tenant_id))).scalar_one()
        before = (ws.logo_light_key, ws.logo_dark_key, ws.logo_mark_key)
        ws.logo_light_key = ws.logo_dark_key = ws.logo_mark_key = None
        await s.commit()

    try:
        async with _client() as c:
            h = _H(ctx["b"]["admin"], ctx["b"]["host"])
            tasks = (await c.get("/api/console/setup-tasks", headers=h)).json()["items"]
            brand = next(t for t in tasks if t["key"] == "brand")
            assert brand["verifiable"] is True
            assert brand["satisfied"] is False, "no mark uploaded, so brand is not configured"

            blocked = await c.patch("/api/console/setup-tasks/brand", headers=h,
                                    json={"completed": True})
        assert blocked.status_code == 422, (
            f"brand was marked complete with no mark uploaded: {blocked.status_code}")
    finally:
        async with SessionLocal() as s:
            ws = (await s.execute(select(IntranetWorkspace).where(
                IntranetWorkspace.tenant_id == tenant_id))).scalar_one()
            ws.logo_light_key, ws.logo_dark_key, ws.logo_mark_key = before
            await s.commit()


async def test_a_configured_setup_task_can_still_be_completed(ctx):
    """The gate must not make the checklist impossible to finish: once the configuration is
    there, the tick goes through. Asserted because a rule that only ever refuses is easy to
    write and useless."""
    tenant_id = uuid.UUID(ctx["b"]["tenant_id"])
    async with SessionLocal() as s:
        ws = (await s.execute(select(IntranetWorkspace).where(
            IntranetWorkspace.tenant_id == tenant_id))).scalar_one()
        before = ws.logo_mark_key
        ws.logo_mark_key = "intranet/test/mark.png"
        await s.commit()
    try:
        async with _client() as c:
            h = _H(ctx["b"]["admin"], ctx["b"]["host"])
            r = await c.patch("/api/console/setup-tasks/brand", headers=h,
                              json={"completed": True})
        assert r.status_code == 200, r.text
        assert r.json()["item"]["completed_at"] is not None
    finally:
        async with SessionLocal() as s:
            ws = (await s.execute(select(IntranetWorkspace).where(
                IntranetWorkspace.tenant_id == tenant_id))).scalar_one()
            ws.logo_mark_key = before
            await s.commit()


async def test_a_task_that_cannot_be_verified_stays_a_human_judgement(ctx):
    """`satisfied: null` is a real answer, not a gap.

    Whether somebody has actually reviewed permissions is not visible in a row count, and a proxy
    invented for it would be a checkbox claiming more than it knows with extra steps. Those tasks
    must still be tickable, or the checklist becomes impossible to finish.
    """
    async with _client() as c:
        h = _H(ctx["b"]["admin"], ctx["b"]["host"])
        tasks = (await c.get("/api/console/setup-tasks", headers=h)).json()["items"]
        manual = [t for t in tasks if not t["verifiable"]]
        assert manual, "expected some tasks to be non-derivable"
        assert all(t["satisfied"] is None for t in manual)
        r = await c.patch(f"/api/console/setup-tasks/{manual[0]['key']}",
                          headers=h, json={"completed": True})
    assert r.status_code == 200, r.text
    assert r.json()["item"]["completed_at"] is not None


async def test_a_marketing_request_cannot_be_touched_across_tenants(ctx):
    """A request id is a tenant-scoped object reference arriving from a browser. Reading or moving
    another workspace's request would expose what their agents have asked for, and the 404 must
    not distinguish "not yours" from "does not exist"."""
    other_id = ctx["b"]["ids"]["marketing_request"]
    async with _client() as c:
        h = _H(ctx["a"]["admin"], ctx["a"]["host"])
        moved = await c.patch(f"/api/console/marketing/requests/{other_id}", headers=h,
                              json={"status": "Done"})
        listed = await c.get("/api/console/marketing/requests", headers=h)
    assert moved.status_code == 404, f"cross-tenant request accepted: {moved.status_code}"
    ids = {r["id"] for r in listed.json()["items"]}
    assert other_id not in ids, "another tenant's request appeared in this queue"


async def test_an_assignee_cannot_be_borrowed_from_another_tenant(ctx):
    """The assignee is a member id from a browser, and a foreign key written unchecked would then
    render somebody else's name onto this workspace's queue."""
    mine = ctx["a"]["ids"]["marketing_request"]
    theirs = ctx["b"]["ids"]["member"]
    async with _client() as c:
        r = await c.patch(f"/api/console/marketing/requests/{mine}",
                          headers=_H(ctx["a"]["admin"], ctx["a"]["host"]),
                          json={"assignee_member_id": theirs})
    assert r.status_code == 404, f"cross-tenant assignee accepted: {r.status_code} {r.text}"


def test_the_two_marketing_field_vocabularies_agree():
    """The console validates what an ADMIN may REQUIRE; the intranet validates what a SUBMITTER
    may SEND. They are separate constants on purpose -- neither should quietly follow the other --
    but a field the admin can require and the form cannot collect is an unsubmittable request.

    So the submittable set must cover everything requirable. `attachments` is the live example: it
    is absent from both until the form can actually take a file.
    """
    from app.routers.console import MARKETING_FIELDS
    from app.routers.intranet import MARKETING_REQUIRABLE

    unsubmittable = MARKETING_FIELDS - MARKETING_REQUIRABLE
    assert not unsubmittable, (
        f"an admin can require {sorted(unsubmittable)}, which the submit form cannot collect")


async def test_the_console_can_read_a_workspace_attachment_but_not_another_tenants(ctx):
    """The console reads the WORKSPACE's files where the intranet route reads only the
    requester's own -- different authorisation, hence a separate route. What must not differ is
    the tenant boundary, or an id from one workspace fetches bytes from another.
    """
    from app.models import IntranetMarketingAttachment
    from app.services import binder_storage

    tenant_a = uuid.UUID(ctx["a"]["tenant_id"])
    tenant_b = uuid.UUID(ctx["b"]["tenant_id"])
    png = bytes.fromhex("89504e470d0a1a0a") + b"body"

    made = {}
    async with SessionLocal() as s:
        for label, tenant_id, request_id in (
            ("a", tenant_a, uuid.UUID(ctx["a"]["ids"]["marketing_request"])),
            ("b", tenant_b, uuid.UUID(ctx["b"]["ids"]["marketing_request"])),
        ):
            att_id = uuid.uuid4()
            key = f"intranet/{tenant_id}/marketing/{request_id}/{att_id}-probe.png"
            binder_storage.put(key, png, "image/png")
            s.add(IntranetMarketingAttachment(
                id=att_id, tenant_id=tenant_id, request_id=request_id, filename="probe.png",
                storage_key=key, content_type="image/png", byte_size=len(png)))
            made[label] = (request_id, att_id)
        await s.commit()

    h = _H(ctx["a"]["admin"], ctx["a"]["host"])
    own_req, own_att = made["a"]
    other_req, other_att = made["b"]
    async with _client() as c:
        mine = await c.get(f"/api/console/marketing/requests/{own_req}/attachments/{own_att}",
                           headers=h)
        theirs = await c.get(
            f"/api/console/marketing/requests/{other_req}/attachments/{other_att}", headers=h)
    assert mine.status_code == 200, mine.text
    assert mine.headers["content-disposition"].startswith("attachment;")
    assert theirs.status_code == 404, f"read another tenant's file: {theirs.status_code}"


async def test_a_dashboard_connection_shows_up_in_the_portal_without_being_made_twice(ctx):
    """A workspace is ONE customer. They connect Sisu once.

    `Integration` (dashboard) and `IntranetIntegration` (portal) both cover Sisu and Follow Up
    Boss, and the two even spell their states differently -- `connected` against `Connected` --
    so nothing would have matched by accident. A tenant with live production numbers on the
    dashboard saw "Not Connected" in their portal, and the portal's figures read zero beside a
    dashboard showing real ones.
    """
    from app.models import Integration

    tenant_id = uuid.UUID(ctx["a"]["tenant_id"])
    async with SessionLocal() as s:
        s.add(Integration(tenant_id=tenant_id, provider="sisu", status="connected"))
        await s.commit()

    async with _client() as c:
        h = _H(ctx["a"]["admin"], ctx["a"]["host"])
        items = (await c.get("/api/console/integrations", headers=h)).json()["items"]

    sisu = next(i for i in items if i["provider_key"] == "sisu")
    assert sisu["status"] == "Connected", "the portal ignored a dashboard connection"
    assert sisu["inherited"] is True
    assert sisu["inherited_from"] == "Acumyn dashboard"

    # A provider the dashboard has never heard of stays the portal's own.
    slack = next(i for i in items if i["provider_key"] == "slack")
    assert slack["inherited"] is False


async def test_the_console_refuses_credentials_for_a_provider_the_dashboard_owns(ctx):
    """Accepting them would write to a row nothing reads while telling the admin they had
    connected something. That is worse than refusing, because it looks like it worked."""
    inherited_id = ctx["b"]["ids"].get("inherited_integration")
    assert inherited_id, "fixture did not seed an inherited provider"
    async with _client() as c:
        r = await c.post(f"/api/console/integrations/{inherited_id}/connect",
                         headers=_H(ctx["b"]["admin"], ctx["b"]["host"]),
                         json={"config": {"api_key": "must-not-persist"}})
    assert r.status_code == 422, f"credentials accepted for an inherited provider: {r.text}"
    assert "dashboard" in r.text.lower()


async def test_a_provider_with_several_dashboard_rows_counts_as_connected_if_any_is(ctx):
    """QuickBooks has one row per company, so a provider can have several. Reporting the first
    row's status would make a workspace's portal depend on insertion order."""
    from app.models import Integration
    from app.services.inheritance import dashboard_connections

    tenant_id = uuid.UUID(ctx["b"]["tenant_id"])
    async with SessionLocal() as s:
        s.add_all([
            Integration(tenant_id=tenant_id, provider="fub", status="disconnected"),
            Integration(tenant_id=tenant_id, provider="fub", status="connected"),
        ])
        await s.commit()
        conns = await dashboard_connections(s, tenant_id)
    assert conns["follow_up_boss"] == "Connected", conns
