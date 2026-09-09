"""The portal's side of sections: what a member is served, and when.

The console decides what a course IS. This decides what one person sees of it today, which is a
different answer for every member of the workspace -- Day 3 opens on a different date for an agent
who started last week than for one who started this morning. That is the whole reason the payload
carries resolved sections rather than the rules.
"""
import datetime as dt
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import (Domain, IntranetCourse, IntranetCourseEnrolment, IntranetCourseSection,
                        IntranetLesson, IntranetMember, IntranetRole, IntranetUserState,
                        Tenant, User)
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
    await seed_intranet("portal-sections")
    async with SessionLocal() as s:
        tenant = (await s.execute(select(Tenant).where(
            Tenant.slug == "portal-sections"))).scalar_one()
        host = (await s.execute(select(Domain.hostname).where(
            Domain.tenant_id == tenant.id).limit(1))).scalar_one()
        roles = {r.key: r for r in (await s.execute(select(IntranetRole).where(
            IntranetRole.tenant_id == tenant.id))).scalars().all()}
        member = (await s.execute(select(IntranetMember).where(
            IntranetMember.tenant_id == tenant.id,
            IntranetMember.role_id == roles["team_leader"].id).limit(1))).scalar_one()
        user = (await s.execute(select(User).where(
            User.tenant_id == tenant.id, User.email == member.email))).scalar_one_or_none()
        if user is None:
            user = User(tenant_id=tenant.id, email=member.email, name=member.full_name,
                        password_hash=hash_pw("password123"), role="admin", status="active",
                        token_version=0)
            s.add(user)
            await s.flush()
        member.user_id = user.id
        await s.commit()
        return {"host": host, "tenant_id": tenant.id, "user_id": user.id,
                "token": make_token(user.id, tenant.id, int(user.token_version or 0))}


async def _build(ctx, *, scheme="day", lock=False, sections=(), lessons=(), started=None,
                 done=()):
    """A published course with published sections and lessons, wired for one member.

    Everything is published here because the portal only shows published rows -- a test that
    forgot would be testing the publish filter rather than the thing it names.
    """
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        course = IntranetCourse(
            tenant_id=ctx["tenant_id"], title=f"Course {uuid.uuid4().hex[:6]}",
            category="Onboarding", state="Live", sort=500, grouping_scheme=scheme,
            lock_sections=lock, published_at=now, draft_dirty=False)
        s.add(course)
        await s.flush()

        made = []
        for i, spec in enumerate(sections):
            row = IntranetCourseSection(
                tenant_id=ctx["tenant_id"], course_id=course.id,
                name=spec.get("name", f"Section {i}"), sort=i,
                release_rule=spec.get("release_rule", "immediate"),
                release_day=spec.get("release_day"),
                due_rule=spec.get("due_rule", "none"), due_day=spec.get("due_day"),
                published_at=None if spec.get("draft") else now,
                draft_dirty=bool(spec.get("draft")))
            s.add(row)
            made.append(row)
        await s.flush()

        built = []
        for i, spec in enumerate(lessons):
            row = IntranetLesson(
                tenant_id=ctx["tenant_id"], course_id=course.id,
                section_id=made[spec["section"]].id if spec.get("section") is not None else None,
                title=spec.get("title", f"Lesson {i}"),
                kind=spec.get("kind", "video"),
                source_type=spec.get("source_type", "PLACE"),
                source_ref=spec.get("source_ref"),
                body_html=spec.get("body_html"),
                read_minutes=spec.get("read_minutes"),
                duration_minutes=spec.get("duration_minutes"),
                sort=i, published_at=now, draft_dirty=False)
            s.add(row)
            built.append(row)
        await s.flush()

        if started is not None:
            s.add(IntranetCourseEnrolment(
                tenant_id=ctx["tenant_id"], user_id=ctx["user_id"], course_id=course.id,
                started_at=dt.datetime.combine(started, dt.time(9, 0), dt.timezone.utc)))
        if done:
            marked = {str(built[i].id): True for i in done}
            existing = (await s.execute(select(IntranetUserState).where(
                IntranetUserState.tenant_id == ctx["tenant_id"],
                IntranetUserState.user_id == ctx["user_id"],
                IntranetUserState.scope == "training"))).scalars().first()
            if existing is None:
                s.add(IntranetUserState(
                    tenant_id=ctx["tenant_id"], user_id=ctx["user_id"], scope="training",
                    state_key="global", value={"done": marked}))
            else:
                existing.value = {"done": {**(existing.value or {}).get("done", {}), **marked}}
        await s.commit()
        return str(course.id), [str(x.id) for x in made], [str(x.id) for x in built]


async def _course_payload(ctx, course_id):
    async with _client() as c:
        r = await c.get("/api/v1/intranet/config", headers=_H(ctx["token"], ctx["host"]))
    assert r.status_code == 200, r.text
    courses = r.json()["config"]["content"]["courses"]
    found = next((x for x in courses if x["id"] == course_id), None)
    assert found is not None, "the course is not in the payload at all"
    return found


# ── the course that has no sections ───────────────────────────────────────────────────────

async def test_a_course_without_sections_is_unchanged(ctx):
    """Every course on the platform is in this state the morning after deploy."""
    course_id, _, _ = await _build(ctx, scheme="none",
                                   lessons=[{"title": "Flat one"}, {"title": "Flat two"}])
    course = await _course_payload(ctx, course_id)
    assert course["sections"] == []
    assert course["grouping_scheme"] == "none"
    assert [le["section_id"] for le in course["lessons"]] == [None, None]


# ── labels and ordering ───────────────────────────────────────────────────────────────────

async def test_labels_are_generated_and_lessons_follow_their_section(ctx):
    course_id, _, lesson_ids = await _build(
        ctx, scheme="day",
        sections=[{"name": "Systems"}, {"name": "Calls"}],
        lessons=[{"title": "B", "section": 1}, {"title": "A", "section": 0}])
    course = await _course_payload(ctx, course_id)
    assert [x["label"] for x in course["sections"]] == ["Day 1", "Day 2"]
    # A before B: ordered by the SECTION's position, not by the lesson's own sort and not by
    # section_id, which is a UUID.
    assert [le["title"] for le in course["lessons"]] == ["A", "B"]
    assert lesson_ids


async def test_a_draft_section_is_not_in_the_portal(ctx):
    """The publish button's promise. It was already broken once for lessons."""
    course_id, _, _ = await _build(
        ctx, scheme="day",
        sections=[{"name": "Live one"}, {"name": "Not yet", "draft": True}],
        lessons=[{"title": "One", "section": 0}])
    course = await _course_payload(ctx, course_id)
    assert [x["name"] for x in course["sections"]] == ["Live one"]


# ── release, per member ───────────────────────────────────────────────────────────────────

async def test_a_day_rule_opens_against_this_members_own_start_date(ctx):
    """Enrolled four days ago: Day 1 through Day 4 are open, Day 5 is not."""
    started = dt.date.today() - dt.timedelta(days=3)
    course_id, _, _ = await _build(
        ctx, scheme="day", started=started,
        sections=[{"name": f"D{n}", "release_rule": "day_n", "release_day": n}
                  for n in (1, 3, 5)],
        lessons=[{"title": "a", "section": 0}, {"title": "b", "section": 1},
                 {"title": "c", "section": 2}])
    course = await _course_payload(ctx, course_id)
    assert [x["released"] for x in course["sections"]] == [True, True, False]
    assert course["sections"][2]["state"] == "locked"
    assert course["day_number"] == 4
    assert course["enrolled_on"] == started.isoformat()


async def test_a_member_who_never_started_is_not_locked_out(ctx):
    """No enrolment row means they have never opened it. A locked Day 1 is a course nobody can
    start."""
    course_id, _, _ = await _build(
        ctx, scheme="day",
        sections=[{"name": "D1", "release_rule": "day_n", "release_day": 1}],
        lessons=[{"title": "a", "section": 0}])
    course = await _course_payload(ctx, course_id)
    assert course["sections"][0]["released"] is True
    assert course["enrolled_on"] is None
    assert course["day_number"] is None


async def test_completion_comes_from_the_members_own_progress(ctx):
    course_id, _, _ = await _build(
        ctx, scheme="module",
        sections=[{"name": "One"}, {"name": "Two"}],
        lessons=[{"title": "a", "section": 0}, {"title": "b", "section": 0},
                 {"title": "c", "section": 1}],
        done=(0, 1))
    course = await _course_payload(ctx, course_id)
    first, second = course["sections"]
    assert (first["done_count"], first["lesson_count"]) == (2, 2)
    assert first["state"] == "complete"
    assert second["state"] == "current"
    assert second["is_current"] is True
    assert first["is_current"] is False


async def test_locking_sections_gates_the_next_one_on_the_previous(ctx):
    course_id, _, _ = await _build(
        ctx, scheme="day", lock=True,
        sections=[{"name": "One"}, {"name": "Two"}],
        lessons=[{"title": "a", "section": 0}, {"title": "b", "section": 1}])
    course = await _course_payload(ctx, course_id)
    assert [x["state"] for x in course["sections"]] == ["current", "locked"]


# ── the reading lesson ────────────────────────────────────────────────────────────────────

async def test_a_reading_lesson_arrives_with_a_body_and_no_player(ctx):
    """The single most likely bug in this change: `resolve` on an empty source_ref returns a
    "no source attached yet" card, which would render as an apology on a finished article."""
    course_id, _, _ = await _build(
        ctx, scheme="none",
        lessons=[{"title": "The first call", "kind": "reading", "read_minutes": 6,
                  "body_html": "<h2>Before you dial</h2><p>Know the address.</p>"}])
    course = await _course_payload(ctx, course_id)
    lesson = course["lessons"][0]
    assert lesson["player"] is None
    assert lesson["kind"] == "reading"
    assert lesson["body_html"] == "<h2>Before you dial</h2><p>Know the address.</p>"
    assert lesson["duration"] == {"value": 6, "short": "6m read", "long": "6 min read"}


async def test_a_video_lesson_still_gets_its_player(ctx):
    course_id, _, _ = await _build(
        ctx, scheme="none",
        lessons=[{"title": "Walkthrough", "source_type": "LOOM",
                  "source_ref": "https://loom.com/share/abc123"}])
    course = await _course_payload(ctx, course_id)
    assert course["lessons"][0]["player"]["mode"] == "iframe"


async def test_a_stored_body_is_sanitized_again_on_the_way_out(ctx):
    """Written straight into the row, bypassing the API, the way a bad migration or an older
    allowlist would have left it. Re-sanitizing on read is what makes that survivable."""
    course_id, _, lesson_ids = await _build(
        ctx, scheme="none", lessons=[{"title": "Legacy", "kind": "reading",
                                      "body_html": "<p>ok</p>"}])
    async with SessionLocal() as s:
        row = (await s.execute(select(IntranetLesson).where(
            IntranetLesson.id == uuid.UUID(lesson_ids[0])))).scalar_one()
        row.body_html = '<p>Hi</p><script>alert(1)</script><p onclick="x()">There</p>'
        await s.commit()
    course = await _course_payload(ctx, course_id)
    body = course["lessons"][0]["body_html"]
    assert "<script" not in body and "onclick" not in body
    assert "Hi" in body and "There" in body


async def test_a_course_of_articles_reports_real_minutes(ctx):
    course_id, _, _ = await _build(
        ctx, scheme="none",
        lessons=[{"title": "a", "kind": "reading", "read_minutes": 6},
                 {"title": "b", "kind": "reading", "read_minutes": 4},
                 {"title": "c", "duration_minutes": 12}])
    course = await _course_payload(ctx, course_id)
    assert course["total_duration_minutes"] == 22
    assert "Reading" in course["media"]


# ── enrolment ─────────────────────────────────────────────────────────────────────────────

async def test_starting_a_course_records_the_date_and_never_moves_it(ctx):
    """A second call must return the first answer -- re-enrolling would put Day 1 after Day 4 for
    somebody who simply reopened the page."""
    course_id, _, _ = await _build(ctx, scheme="day", sections=[{"name": "One"}],
                                   lessons=[{"title": "a", "section": 0}])
    async with _client() as c:
        first = await c.post(f"/api/v1/intranet/courses/{course_id}/start",
                             headers=_H(ctx["token"], ctx["host"]))
        assert first.status_code == 200, first.text
        second = await c.post(f"/api/v1/intranet/courses/{course_id}/start",
                              headers=_H(ctx["token"], ctx["host"]))
        assert second.status_code == 200, second.text
    assert first.json()["started_at"] == second.json()["started_at"]
    async with SessionLocal() as s:
        count = len((await s.execute(select(IntranetCourseEnrolment).where(
            IntranetCourseEnrolment.user_id == ctx["user_id"],
            IntranetCourseEnrolment.course_id == uuid.UUID(course_id)))).scalars().all())
    assert count == 1


async def test_starting_a_course_that_is_not_live_is_a_404(ctx):
    """Enrolment must not be a way to discover drafts."""
    async with SessionLocal() as s:
        draft = IntranetCourse(tenant_id=ctx["tenant_id"], title="Draft only",
                               category="Ops", state="Draft", sort=999)
        s.add(draft)
        await s.commit()
        draft_id = str(draft.id)
    async with _client() as c:
        r = await c.post(f"/api/v1/intranet/courses/{draft_id}/start",
                         headers=_H(ctx["token"], ctx["host"]))
    assert r.status_code == 404, r.text


async def test_starting_a_course_requires_a_session(ctx):
    course_id, _, _ = await _build(ctx, scheme="none", lessons=[{"title": "a"}])
    async with _client() as c:
        r = await c.post(f"/api/v1/intranet/courses/{course_id}/start",
                         headers={"x-tenant-host": ctx["host"]})
    assert r.status_code == 401, r.text
