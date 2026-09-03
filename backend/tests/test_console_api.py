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
    IntranetLesson,
    IntranetMember,
    IntranetPendingChange,
    IntranetPermission,
    IntranetPublishBatch,
    IntranetRole,
    IntranetSop,
    IntranetSopCategory,
    Tenant,
    User,
)
from app.security import hash_pw, make_token

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
        s.add_all([delete_category, gap, batch])
        await s.flush()

        ids = {
            "role": str(role_rows[0].id),
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
        "integrations": 9,
        "ai_sources": 7,
        "setup_tasks": 11,
        "pending_changes": 0,
    }


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
            AuditLog.tenant_id == uuid.UUID(ctx["b"]["tenant_id"])
        ).order_by(AuditLog.created_at.desc()).limit(1))).scalar_one()
    assert latest.action == "access.permissions.updated"
    assert cap["name"] in latest.summary
    assert role["name"] in latest.summary
    assert old["level"] in latest.summary
    assert new_level in latest.summary


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
