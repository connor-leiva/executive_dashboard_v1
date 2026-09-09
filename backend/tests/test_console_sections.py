"""Sections, lesson kinds and rich text, through the API a browser actually talks to.

The service-level rules are tested in test_course_sections.py and test_lesson_richtext.py. This
file is about the wiring: whether the console can create a section at all, whether deleting one
keeps the lessons, whether a body survives a round trip, and whether the reading lesson that
opens in the portal is the one the author saved.
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import (Domain, IntranetCourseSection, IntranetLesson,
                        IntranetMember, IntranetRole, Tenant, User)
from app.security import hash_pw

TRANSPORT = ASGITransport(app=app)


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token: str, host: str) -> dict:
    return {"Authorization": f"Bearer {token}", "x-tenant-host": host}


@pytest.fixture(scope="module")
async def ctx():
    from app.seed import seed
    from scripts.seed_intranet import seed_intranet
    from app.security import make_token

    await seed()
    await seed_intranet("sections-a")
    async with SessionLocal() as s:
        tenant = (await s.execute(select(Tenant).where(Tenant.slug == "sections-a"))).scalar_one()
        host = (await s.execute(select(Domain.hostname).where(
            Domain.tenant_id == tenant.id).limit(1))).scalar_one()
        roles = {r.key: r for r in (await s.execute(select(IntranetRole).where(
            IntranetRole.tenant_id == tenant.id))).scalars().all()}
        leader = (await s.execute(select(IntranetMember).where(
            IntranetMember.tenant_id == tenant.id,
            IntranetMember.role_id == roles["team_leader"].id).limit(1))).scalar_one()
        user = (await s.execute(select(User).where(
            User.tenant_id == tenant.id, User.email == leader.email))).scalar_one_or_none()
        if user is None:
            user = User(tenant_id=tenant.id, email=leader.email, name=leader.full_name,
                        password_hash=hash_pw("password123"), role="admin", status="active",
                        token_version=0)
            s.add(user)
            await s.flush()
        leader.user_id = user.id
        await s.commit()
        return {"host": host, "tenant_id": str(tenant.id), "user_id": str(user.id),
                "admin": make_token(user.id, tenant.id, int(user.token_version or 0))}


async def _course(c, ctx, **extra):
    body = {"title": "Five Day Onboarding", "category": "Onboarding", "state": "Live"}
    body.update(extra)
    r = await c.post("/api/console/courses", headers=_H(ctx["admin"], ctx["host"]), json=body)
    assert r.status_code == 200, r.text
    return r.json()["item"]["id"]


async def _lesson(c, ctx, course_id, **extra):
    body = {"title": "Lesson", "source_type": "PLACE"}
    body.update(extra)
    r = await c.post(f"/api/console/courses/{course_id}/lessons",
                     headers=_H(ctx["admin"], ctx["host"]), json=body)
    assert r.status_code == 200, r.text
    return r.json()["item"]["id"]


async def _section(c, ctx, course_id, **extra):
    r = await c.post(f"/api/console/courses/{course_id}/sections",
                     headers=_H(ctx["admin"], ctx["host"]), json=dict(extra))
    assert r.status_code == 200, r.text
    return r.json()["item"]


async def _detail(c, ctx, course_id):
    r = await c.get(f"/api/console/courses/{course_id}", headers=_H(ctx["admin"], ctx["host"]))
    assert r.status_code == 200, r.text
    return r.json()


# ── the promise made to courses that already exist ────────────────────────────────────────

async def test_a_course_with_no_sections_looks_exactly_as_it_did(ctx):
    """The first acceptance criterion. Every course on the platform is in this state on deploy."""
    async with _client() as c:
        course_id = await _course(c, ctx)
        await _lesson(c, ctx, course_id, title="One")
        body = await _detail(c, ctx, course_id)
    assert body["sections"] == []
    assert body["grouping_scheme"] == "none"
    assert body["lessons"][0]["section_id"] is None
    assert body["lessons"][0]["kind"] == "video"


# ── sections ──────────────────────────────────────────────────────────────────────────────

async def test_the_label_comes_from_the_scheme_not_from_the_name(ctx):
    async with _client() as c:
        course_id = await _course(c, ctx, grouping_scheme="day")
        await _section(c, ctx, course_id, name="Systems and access")
        await _section(c, ctx, course_id, name="First conversations")
        body = await _detail(c, ctx, course_id)
    assert [x["label"] for x in body["sections"]] == ["Day 1", "Day 2"]
    assert [x["name"] for x in body["sections"]] == ["Systems and access", "First conversations"]


async def test_changing_the_scheme_renames_every_section_and_moves_no_lesson(ctx):
    async with _client() as c:
        course_id = await _course(c, ctx, grouping_scheme="day")
        await _section(c, ctx, course_id, name="A")
        await _section(c, ctx, course_id, name="B")
        lesson_id = await _lesson(c, ctx, course_id, title="Kept")
        before = await _detail(c, ctx, course_id)
        r = await c.patch(f"/api/console/courses/{course_id}",
                          headers=_H(ctx["admin"], ctx["host"]),
                          json={"grouping_scheme": "part"})
        assert r.status_code == 200, r.text
        after = await _detail(c, ctx, course_id)
    assert [x["label"] for x in after["sections"]] == ["Part I", "Part II"]
    assert [x["id"] for x in after["sections"]] == [x["id"] for x in before["sections"]]
    moved = next(le for le in after["lessons"] if le["id"] == lesson_id)
    kept = next(le for le in before["lessons"] if le["id"] == lesson_id)
    assert moved["section_id"] == kept["section_id"]


async def test_the_first_section_adopts_the_lessons_already_in_the_course(ctx):
    """Otherwise adding one produces an empty card above an unlabelled pile, and the author has
    to drag every lesson into the thing they just made."""
    async with _client() as c:
        course_id = await _course(c, ctx, grouping_scheme="module")
        a = await _lesson(c, ctx, course_id, title="Existing one")
        b = await _lesson(c, ctx, course_id, title="Existing two")
        section = await _section(c, ctx, course_id, name="Everything so far")
        body = await _detail(c, ctx, course_id)
    placed = {le["id"]: le["section_id"] for le in body["lessons"]}
    assert placed[a] == section["id"]
    assert placed[b] == section["id"]


async def test_a_second_section_does_not_swallow_the_first_ones_lessons(ctx):
    async with _client() as c:
        course_id = await _course(c, ctx, grouping_scheme="day")
        lesson_id = await _lesson(c, ctx, course_id, title="Day one work")
        first = await _section(c, ctx, course_id, name="Day one")
        await _section(c, ctx, course_id, name="Day two")
        body = await _detail(c, ctx, course_id)
    assert next(le for le in body["lessons"] if le["id"] == lesson_id)["section_id"] == first["id"]


async def test_deleting_a_section_keeps_its_lessons_and_gives_them_the_one_before(ctx):
    """`ON DELETE SET NULL` is the backstop, not the mechanism: leaning on it would drop the
    lessons into the unlabelled group at the top, which reads as data loss."""
    async with _client() as c:
        course_id = await _course(c, ctx, grouping_scheme="day")
        first = await _section(c, ctx, course_id, name="One")
        second = await _section(c, ctx, course_id, name="Two")
        lesson_id = await _lesson(c, ctx, course_id, title="In the second",
                                  section_id=second["id"])
        r = await c.delete(f"/api/console/courses/{course_id}/sections/{second['id']}",
                           headers=_H(ctx["admin"], ctx["host"]))
        assert r.status_code == 200, r.text
        body = await _detail(c, ctx, course_id)
    survivor = next(le for le in body["lessons"] if le["id"] == lesson_id)
    assert survivor["section_id"] == first["id"], "the lesson was orphaned or deleted"
    assert [x["id"] for x in body["sections"]] == [first["id"]]


async def test_deleting_the_first_section_hands_its_lessons_to_the_next(ctx):
    async with _client() as c:
        course_id = await _course(c, ctx, grouping_scheme="day")
        first = await _section(c, ctx, course_id, name="One")
        second = await _section(c, ctx, course_id, name="Two")
        lesson_id = await _lesson(c, ctx, course_id, title="In the first",
                                  section_id=first["id"])
        r = await c.delete(f"/api/console/courses/{course_id}/sections/{first['id']}",
                           headers=_H(ctx["admin"], ctx["host"]))
        assert r.status_code == 200, r.text
        body = await _detail(c, ctx, course_id)
    assert next(le for le in body["lessons"]
                if le["id"] == lesson_id)["section_id"] == second["id"]


async def test_dragging_a_lesson_between_sections_is_one_request(ctx):
    async with _client() as c:
        course_id = await _course(c, ctx, grouping_scheme="day")
        first = await _section(c, ctx, course_id, name="One")
        second = await _section(c, ctx, course_id, name="Two")
        a = await _lesson(c, ctx, course_id, title="A", section_id=first["id"])
        b = await _lesson(c, ctx, course_id, title="B", section_id=first["id"])
        r = await c.put(f"/api/console/courses/{course_id}/lessons/order",
                        headers=_H(ctx["admin"], ctx["host"]),
                        json={"lessons": [{"id": a, "section_id": second["id"], "sort": 0},
                                          {"id": b, "section_id": first["id"], "sort": 0}]})
        assert r.status_code == 200, r.text
        body = await _detail(c, ctx, course_id)
    placed = {le["id"]: le["section_id"] for le in body["lessons"]}
    assert placed[a] == second["id"]
    assert placed[b] == first["id"]


async def test_a_lesson_cannot_be_dragged_into_another_courses_section(ctx):
    """It would leave the course it is filed under and reappear in somebody else's."""
    async with _client() as c:
        mine = await _course(c, ctx, title="Mine")
        theirs = await _course(c, ctx, title="Theirs")
        elsewhere = await _section(c, ctx, theirs, name="Not yours")
        lesson_id = await _lesson(c, ctx, mine, title="Stays")
        r = await c.put(f"/api/console/courses/{mine}/lessons/order",
                        headers=_H(ctx["admin"], ctx["host"]),
                        json={"lessons": [{"id": lesson_id, "section_id": elsewhere["id"],
                                           "sort": 0}]})
    assert r.status_code == 422, r.text


async def test_the_flat_id_list_still_works(ctx):
    """The console sends it for a course with no sections, and every existing caller sends it."""
    async with _client() as c:
        course_id = await _course(c, ctx)
        a = await _lesson(c, ctx, course_id, title="A")
        b = await _lesson(c, ctx, course_id, title="B")
        r = await c.put(f"/api/console/courses/{course_id}/lessons/order",
                        headers=_H(ctx["admin"], ctx["host"]), json={"ids": [b, a]})
        assert r.status_code == 200, r.text
        body = await _detail(c, ctx, course_id)
    assert [le["id"] for le in body["lessons"]] == [b, a]


async def test_sections_order_by_their_position_not_by_their_id(ctx):
    """Ordering lessons by `section_id` would order the sections by random hex. Reversing the
    section order must reverse the lesson order."""
    async with _client() as c:
        course_id = await _course(c, ctx, grouping_scheme="day")
        first = await _section(c, ctx, course_id, name="One")
        second = await _section(c, ctx, course_id, name="Two")
        a = await _lesson(c, ctx, course_id, title="A", section_id=first["id"])
        b = await _lesson(c, ctx, course_id, title="B", section_id=second["id"])
        assert [le["id"] for le in (await _detail(c, ctx, course_id))["lessons"]] == [a, b]
        r = await c.put(f"/api/console/courses/{course_id}/sections/order",
                        headers=_H(ctx["admin"], ctx["host"]),
                        json={"ids": [second["id"], first["id"]]})
        assert r.status_code == 200, r.text
        body = await _detail(c, ctx, course_id)
    assert [le["id"] for le in body["lessons"]] == [b, a]
    assert [x["label"] for x in body["sections"]] == ["Day 1", "Day 2"]


async def test_a_rule_that_needs_a_number_and_has_none_is_not_a_rule(ctx):
    """"Day <blank>" must not become a section that never opens."""
    async with _client() as c:
        course_id = await _course(c, ctx, grouping_scheme="day")
        section = await _section(c, ctx, course_id, name="One", release_rule="day_n")
    assert section["release_rule"] == "immediate"


async def test_changing_a_release_rule_clears_the_number_the_old_one_used(ctx):
    """Otherwise the next author to pick "Day N" finds a 3 they never typed."""
    async with _client() as c:
        course_id = await _course(c, ctx, grouping_scheme="day")
        section = await _section(c, ctx, course_id, name="One",
                                 release_rule="day_n", release_day=3)
        assert section["release_day"] == 3
        r = await c.patch(f"/api/console/courses/{course_id}/sections/{section['id']}",
                          headers=_H(ctx["admin"], ctx["host"]),
                          json={"release_rule": "immediate"})
        assert r.status_code == 200, r.text
        body = await _detail(c, ctx, course_id)
    assert body["sections"][0]["release_day"] is None


# ── lesson kinds and rich text ────────────────────────────────────────────────────────────

async def test_a_reading_lesson_round_trips_its_body(ctx):
    async with _client() as c:
        course_id = await _course(c, ctx)
        lesson_id = await _lesson(
            c, ctx, course_id, title="The first call", kind="reading",
            body_html="<h2>Before you dial</h2><p>Know the <strong>address</strong>.</p>")
        body = await _detail(c, ctx, course_id)
    lesson = next(le for le in body["lessons"] if le["id"] == lesson_id)
    assert lesson["kind"] == "reading"
    assert lesson["body_html"] == \
        "<h2>Before you dial</h2><p>Know the <strong>address</strong>.</p>"
    assert lesson["word_count"] == 6
    assert lesson["read_minutes"] == 1


async def test_a_reading_lesson_has_no_player(ctx):
    """Without the guard, the empty `source_ref` resolves to a "no source attached yet" card on
    an article that is finished."""
    async with _client() as c:
        course_id = await _course(c, ctx)
        lesson_id = await _lesson(c, ctx, course_id, title="Article", kind="reading",
                                  body_html="<p>Words.</p>")
        body = await _detail(c, ctx, course_id)
    assert next(le for le in body["lessons"] if le["id"] == lesson_id)["player"] is None


async def test_flipping_reading_to_video_and_back_keeps_the_body(ctx):
    """An author checking something must not lose their article to the check."""
    async with _client() as c:
        course_id = await _course(c, ctx)
        lesson_id = await _lesson(c, ctx, course_id, title="Flip", kind="reading",
                                  body_html="<p>Still here.</p>")
        for kind in ("video", "reading"):
            r = await c.patch(f"/api/console/courses/{course_id}/lessons/{lesson_id}",
                              headers=_H(ctx["admin"], ctx["host"]), json={"kind": kind})
            assert r.status_code == 200, r.text
        body = await _detail(c, ctx, course_id)
    lesson = next(le for le in body["lessons"] if le["id"] == lesson_id)
    assert lesson["body_html"] == "<p>Still here.</p>"


async def test_a_dangerous_body_is_sanitized_before_it_is_stored(ctx):
    """Stored, not just rendered clean: the row itself must never hold a script."""
    async with _client() as c:
        course_id = await _course(c, ctx)
        lesson_id = await _lesson(
            c, ctx, course_id, title="Nasty", kind="reading",
            body_html='<p>Read <a href="javascript:alert(1)">this</a></p>'
                      '<script>alert(2)</script>')
        body = await _detail(c, ctx, course_id)
    lesson = next(le for le in body["lessons"] if le["id"] == lesson_id)
    assert "<script" not in lesson["body_html"]
    assert "javascript:" not in lesson["body_html"]
    assert "this" in lesson["body_html"], "the link text should survive"
    async with SessionLocal() as s:
        stored = (await s.execute(select(IntranetLesson.body_html).where(
            IntranetLesson.id == uuid.UUID(lesson_id)))).scalar_one()
    assert "<script" not in stored and "javascript:" not in stored


async def test_an_author_can_override_the_read_time_the_body_derived(ctx):
    """The estimate is a reading pace; somebody who knows their audience beats a constant."""
    async with _client() as c:
        course_id = await _course(c, ctx)
        lesson_id = await _lesson(c, ctx, course_id, title="Dense", kind="reading",
                                  body_html="<p>" + " ".join(["word"] * 220) + "</p>")
        first = await _detail(c, ctx, course_id)
        assert next(le for le in first["lessons"]
                    if le["id"] == lesson_id)["read_minutes"] == 1
        r = await c.patch(f"/api/console/courses/{course_id}/lessons/{lesson_id}",
                          headers=_H(ctx["admin"], ctx["host"]), json={"read_minutes": 9})
        assert r.status_code == 200, r.text
        body = await _detail(c, ctx, course_id)
    assert next(le for le in body["lessons"] if le["id"] == lesson_id)["read_minutes"] == 9


async def test_a_course_of_articles_reports_its_real_length(ctx):
    """It used to sum `duration_minutes` alone and advertise itself as taking no time."""
    async with _client() as c:
        course_id = await _course(c, ctx)
        await _lesson(c, ctx, course_id, title="A", kind="reading",
                      body_html="<p>" + " ".join(["word"] * 440) + "</p>")
        await _lesson(c, ctx, course_id, title="B", kind="video", duration_minutes=10)
        body = await _detail(c, ctx, course_id)
    assert body["total_duration_minutes"] == 12


async def test_the_duration_string_is_computed_once_on_the_server(ctx):
    """Two frontends, one answer -- 22m in a list and 22 min in a header, from the same call."""
    async with _client() as c:
        course_id = await _course(c, ctx)
        video = await _lesson(c, ctx, course_id, title="V", duration_minutes=22)
        doc = await _lesson(c, ctx, course_id, title="D", kind="document", page_count=9)
        body = await _detail(c, ctx, course_id)
    found = {le["id"]: le["duration"] for le in body["lessons"]}
    assert found[video] == {"value": 22, "short": "22m", "long": "22 min"}
    assert found[doc] == {"value": 9, "short": "9 pp", "long": "9 pages"}


async def test_an_unknown_kind_is_refused(ctx):
    async with _client() as c:
        course_id = await _course(c, ctx)
        r = await c.post(f"/api/console/courses/{course_id}/lessons",
                         headers=_H(ctx["admin"], ctx["host"]),
                         json={"title": "Nope", "source_type": "PLACE", "kind": "podcast"})
    assert r.status_code == 422, r.text


# ── the publish cycle ─────────────────────────────────────────────────────────────────────

async def test_a_new_section_is_a_draft_until_somebody_publishes_it(ctx):
    """It carries tenant_id + published_at + draft_dirty, so `_publishable_models()` picks it up
    without anybody adding it to a list. This is the assertion that the derivation held."""
    async with _client() as c:
        course_id = await _course(c, ctx, grouping_scheme="day")
        section = await _section(c, ctx, course_id, name="Draft day")
    assert section["published_at"] is None
    assert section["draft_dirty"] is True
    async with SessionLocal() as s:
        row = (await s.execute(select(IntranetCourseSection).where(
            IntranetCourseSection.id == uuid.UUID(section["id"])))).scalar_one()
    assert row.published_at is None


# ── body images ───────────────────────────────────────────────────────────────────────────

PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 4 + b"IHDR" \
    + (800).to_bytes(4, "big") + (600).to_bytes(4, "big") + b"rest of the file"


async def test_an_uploaded_image_survives_a_save_and_comes_back_fetchable(ctx):
    """THE ROUND TRIP THAT NEARLY BROKE. The editor is handed a URL, so the editor posts a URL
    back; if the sanitizer did not fold it home to the storage key, the second save would strip
    every image as unowned and the article would empty itself."""
    async with _client() as c:
        course_id = await _course(c, ctx)
        lesson_id = await _lesson(c, ctx, course_id, title="Illustrated", kind="reading")

        up = await c.post(f"/api/console/courses/{course_id}/lessons/{lesson_id}/images",
                          headers=_H(ctx["admin"], ctx["host"]),
                          data={"alt": "Last month's calls"},
                          files={"file": ("chart.png", PNG, "image/png")})
        assert up.status_code == 200, up.text
        key = up.json()["storage_key"]
        assert up.json()["width"] == 800 and up.json()["height"] == 600

        # What the editor would send: the SERVED url, not the key.
        served = f"/api/console/lessons/{lesson_id}/images/{key.rsplit('/', 1)[1]}"
        body = f'<p>Look:</p><figure><img src="{served}" alt="Last month\'s calls"></figure>'
        for _ in range(3):                     # every save re-sends what the last one returned
            r = await c.patch(f"/api/console/courses/{course_id}/lessons/{lesson_id}",
                              headers=_H(ctx["admin"], ctx["host"]), json={"body_html": body})
            assert r.status_code == 200, r.text
            body = r.json()["item"]["body_html"]
            assert "<img" in body, "the image was stripped on save"

        fetched = await c.get(served, headers=_H(ctx["admin"], ctx["host"]))
    assert fetched.status_code == 200, fetched.text
    assert fetched.headers["content-type"].startswith("image/png")
    assert fetched.headers["x-content-type-options"] == "nosniff"
    async with SessionLocal() as s:
        stored = (await s.execute(select(IntranetLesson.body_html).where(
            IntranetLesson.id == uuid.UUID(lesson_id)))).scalar_one()
    assert stored.count(f'src="{key}"') == 1, f"the key is not what was stored: {stored}"


async def test_an_image_without_alt_text_is_refused(ctx):
    async with _client() as c:
        course_id = await _course(c, ctx)
        lesson_id = await _lesson(c, ctx, course_id, title="No alt", kind="reading")
        r = await c.post(f"/api/console/courses/{course_id}/lessons/{lesson_id}/images",
                         headers=_H(ctx["admin"], ctx["host"]), data={"alt": "  "},
                         files={"file": ("chart.png", PNG, "image/png")})
    assert r.status_code == 422, r.text


async def test_a_file_that_is_not_an_image_is_refused_whatever_it_is_called(ctx):
    """Sniffed, not trusted: the filename and the browser's Content-Type are both the client's."""
    async with _client() as c:
        course_id = await _course(c, ctx)
        lesson_id = await _lesson(c, ctx, course_id, title="Sneaky", kind="reading")
        r = await c.post(f"/api/console/courses/{course_id}/lessons/{lesson_id}/images",
                         headers=_H(ctx["admin"], ctx["host"]), data={"alt": "not an image"},
                         files={"file": ("chart.png", b"<svg onload=alert(1)>", "image/png")})
    assert r.status_code == 422, r.text


async def test_an_image_path_cannot_climb_out_of_its_lesson(ctx):
    async with _client() as c:
        course_id = await _course(c, ctx)
        lesson_id = await _lesson(c, ctx, course_id, title="Traversal", kind="reading")
        r = await c.get(f"/api/console/lessons/{lesson_id}/images/..%2F..%2Fsecret.png",
                        headers=_H(ctx["admin"], ctx["host"]))
    assert r.status_code == 404, r.text


async def test_deleting_a_section_uses_the_order_the_list_is_actually_in(ctx):
    """Two sections can share a `sort` -- nothing prevents it, and a half-applied reorder leaves
    one. The reassignment used to recompute the position with a different tie-break from the one
    the listing uses, which handed the lessons to the wrong neighbour on exactly those rows."""
    async with _client() as c:
        course_id = await _course(c, ctx, grouping_scheme="day")
        a = await _section(c, ctx, course_id, name="Aaa")
        b = await _section(c, ctx, course_id, name="Bbb")
        lesson_id = await _lesson(c, ctx, course_id, title="In B", section_id=b["id"])
    async with SessionLocal() as s:
        for sid in (a["id"], b["id"]):
            row = (await s.execute(select(IntranetCourseSection).where(
                IntranetCourseSection.id == uuid.UUID(sid)))).scalar_one()
            row.sort = 0                    # a tie, broken by name: Aaa then Bbb
        await s.commit()
    async with _client() as c:
        r = await c.delete(f"/api/console/courses/{course_id}/sections/{b['id']}",
                           headers=_H(ctx["admin"], ctx["host"]))
        assert r.status_code == 200, r.text
        body = await _detail(c, ctx, course_id)
    assert next(le for le in body["lessons"]
                if le["id"] == lesson_id)["section_id"] == a["id"]
