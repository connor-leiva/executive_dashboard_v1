"""The course builder's own data: the byline, the last editor, and the chips that name a source.

The builder autosaves every field and publishes once, so the things worth asserting here are the
two facts it renders that nothing else produced -- who teaches a lesson, and who touched the course
last -- plus the console-side maps that have to cover the real source vocabulary. That last one is
the same class of mistake as lesson_media's invented "HOSTED" key: a map written from memory
rather than from the enum, failing silently by rendering a raw value.
"""
import datetime as dt
import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import (Business, Domain, IntranetCapability, IntranetCourse, IntranetLesson,
                        IntranetMember, IntranetPermission, IntranetRole, Tenant, User)
from app.routers.console import COURSE_STATES, LESSON_SOURCE_TYPES
from app.security import hash_pw, make_token

TRANSPORT = ASGITransport(app=app)
SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token, host):
    return {"Authorization": f"Bearer {token}", "x-tenant-host": host}


async def _workspace(slug: str):
    """A workspace whose owner can open the console, with one course and one lesson."""
    host = f"{slug}.localhost"
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        t = Tenant(slug=slug, name=slug.title(), status="active", plan="portfolio",
                   config={"features": {"intranet": True}})
        s.add(t)
        await s.flush()
        s.add(Domain(tenant_id=t.id, hostname=host, is_primary=True))
        s.add(Business(tenant_id=t.id, key="main", name="Main", tag="Business", sort_order=0))
        u = User(tenant_id=t.id, email=f"lead@{slug}.test", name="Sharida Hansen",
                 password_hash=hash_pw("password123"), role="owner", status="active",
                 token_version=0)
        s.add(u)
        await s.flush()
        role = IntranetRole(tenant_id=t.id, key="lead", name="Team Leader", sort=0,
                            is_leadership=True, published_at=now)
        s.add(role)
        await s.flush()
        s.add(IntranetMember(tenant_id=t.id, full_name="Sharida Hansen", email=u.email,
                             role_id=role.id, status="Active", auth_source="Manual", user_id=u.id))
        cap = IntranetCapability(tenant_id=t.id, key="console_access", name="Console",
                                 description="", sort=0, published_at=now)
        s.add(cap)
        await s.flush()
        s.add(IntranetPermission(tenant_id=t.id, capability_id=cap.id, role_id=role.id,
                                 level="Full", published_at=now))
        course = IntranetCourse(tenant_id=t.id, title="Listing Mastery", category="Hello Week",
                                state="Live", sort=0, published_at=now)
        s.add(course)
        await s.flush()
        lesson = IntranetLesson(tenant_id=t.id, course_id=course.id, title="Why sellers hire you",
                                source_type="LOOM", source_ref="https://www.loom.com/share/a1",
                                sort=1, published_at=now)
        s.add(lesson)
        await s.commit()
        return {"host": host, "tenant_id": t.id, "course_id": course.id, "lesson_id": lesson.id,
                "token": make_token(u.id, t.id, 0)}


# ── the byline ────────────────────────────────────────────────────────────────────────────

async def test_taught_by_round_trips_and_is_not_source_label():
    """Two fields, not one wearing two hats. "Loom" answers where the file lives and "Sharida
    Hansen" answers who to ask about the content -- collapsing them would put a platform name in
    the byline under a lesson title."""
    ws = await _workspace("cbby")
    async with _client() as c:
        r = await c.patch(
            f"/api/console/courses/{ws['course_id']}/lessons/{ws['lesson_id']}",
            json={"taught_by": "Sharida Hansen", "source_label": "Loom"},
            headers=_H(ws["token"], ws["host"]))
        assert r.status_code == 200, r.text
        got = await c.get(f"/api/console/courses/{ws['course_id']}",
                          headers=_H(ws["token"], ws["host"]))

    lesson = got.json()["lessons"][0]
    assert lesson["taught_by"] == "Sharida Hansen"
    assert lesson["source_label"] == "Loom", "the byline overwrote the source label"


async def test_the_byline_reaches_the_portal():
    """It is rendered under the lesson title for members, so it has to be in their payload."""
    ws = await _workspace("cbbyportal")
    async with SessionLocal() as s:
        row = await s.get(IntranetLesson, ws["lesson_id"])
        row.taught_by = "Justin Nelson"
        await s.commit()
    async with _client() as c:
        r = await c.get("/api/v1/intranet/config", headers=_H(ws["token"], ws["host"]))
    lessons = r.json()["config"]["content"]["courses"][0]["lessons"]
    assert lessons[0]["taught_by"] == "Justin Nelson"


# ── "Last edited X by Y" ──────────────────────────────────────────────────────────────────

async def test_the_header_names_whoever_edited_last():
    ws = await _workspace("cbedit")
    async with _client() as c:
        await c.patch(f"/api/console/courses/{ws['course_id']}",
                      json={"title": "Listing Mastery II"}, headers=_H(ws["token"], ws["host"]))
        r = await c.get(f"/api/console/courses/{ws['course_id']}",
                        headers=_H(ws["token"], ws["host"]))
    body = r.json()
    assert body["last_editor"] == "Sharida Hansen"
    assert body["updated_at"], "nothing for the header to say when it was"


async def test_editing_a_lesson_counts_as_editing_the_course():
    """Lessons are audited against their own ids. A header that said "Aug 24" while somebody
    renamed a lesson a minute ago is wrong in the one way a "last edited" line must not be."""
    ws = await _workspace("cbeditlesson")
    async with _client() as c:
        await c.patch(f"/api/console/courses/{ws['course_id']}/lessons/{ws['lesson_id']}",
                      json={"title": "Why sellers really hire you"},
                      headers=_H(ws["token"], ws["host"]))
        r = await c.get(f"/api/console/courses/{ws['course_id']}",
                        headers=_H(ws["token"], ws["host"]))
    assert r.json()["last_editor"] == "Sharida Hansen"


async def test_an_untouched_course_says_nothing_rather_than_guessing():
    ws = await _workspace("cbnoedit")
    async with _client() as c:
        r = await c.get(f"/api/console/courses/{ws['course_id']}",
                        headers=_H(ws["token"], ws["host"]))
    assert r.json()["last_editor"] is None


async def test_saving_a_course_does_not_500_on_its_own_timestamp():
    """_record_mutation commits, `updated_at` carries an onupdate so the ORM expires it, and the
    next read lazy-loads outside the async greenlet. Adding updated_at to the serializer turned
    every course PATCH into a 500 until the refresh went in."""
    ws = await _workspace("cbrefresh")
    async with _client() as c:
        for body in ({"title": "Renamed"}, {"state": "Draft"}, {"sequential": True}):
            r = await c.patch(f"/api/console/courses/{ws['course_id']}", json=body,
                              headers=_H(ws["token"], ws["host"]))
            assert r.status_code == 200, r.text
            assert r.json()["item"]["updated_at"]


# ── the console's chip maps cover the real vocabulary ─────────────────────────────────────

def _map_keys(name: str) -> set[str]:
    text = (SRC / "console" / "pages" / "Training.jsx").read_text(encoding="utf-8")
    block = re.search(name + r"\s*=\s*\{(.*?)\n\};", text, re.S)
    assert block, f"{name} not found in Training.jsx"
    return set(re.findall(r'^\s*"?([A-Za-z ]+?)"?:\s*\[', block.group(1), re.M))


@pytest.mark.skipif(not SRC.exists(), reason="frontend not present")
def test_every_source_type_has_a_chip_colour():
    """A missing key falls back to grey and the lesson row stops telling anybody what kind of
    thing it is. Derived from the enum rather than a list written beside it -- the same mistake
    lesson_media made with an invented "HOSTED" key, which failed silently for exactly this
    reason."""
    missing = sorted(LESSON_SOURCE_TYPES - _map_keys("SOURCE_CHIP"))
    assert not missing, f"SOURCE_CHIP has no colour for {missing}"


@pytest.mark.skipif(not SRC.exists(), reason="frontend not present")
def test_every_course_state_has_a_chip_colour():
    missing = sorted(COURSE_STATES - _map_keys("STATE_CHIP"))
    assert not missing, f"STATE_CHIP has no colour for {missing}"


@pytest.mark.skipif(not SRC.exists(), reason="frontend not present")
def test_the_builder_has_no_per_lesson_save_button():
    """One save path. The old screen had a Save on every lesson plus one on the course, so an
    admin who edited three lessons and pressed Save on two silently lost the third -- and the
    unsaved one looked exactly like the saved ones."""
    text = (SRC / "console" / "pages" / "Training.jsx").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    for gone in ("Save Lesson", "trainingSaveLesson", "Save Course", "trainingSaveCourse"):
        assert gone not in code, f"a per-item save button came back: {gone}"
