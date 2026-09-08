"""The lesson player: what plays, what cannot, and who may read a handout.

The portal's course screen was a checklist whose titles opened loom.com in a new tab. The design
it was meant to be is a player -- the video in the page, the lesson's own copy under it, its
handouts beside it. Two things had nowhere to live (a description, and attachments) and one thing
was decided wrongly: whether a source can be embedded at all.

The embed question is the interesting half. Skool, PLACE and eXp are logged-in products that send
X-Frame-Options and refuse to be framed, so an iframe at one renders a blank rectangle -- a player
that looks broken, which is worse than the link it replaced. These pin that down.
"""
import datetime as dt
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import (Business, Domain, IntranetCapability, IntranetCourse, IntranetCourseRole,
                        IntranetLesson, IntranetLessonAttachment, IntranetMember,
                        IntranetPermission, IntranetRole, Tenant, User)
from app.security import hash_pw, make_token
from app.services import binder_storage, lesson_media

TRANSPORT = ASGITransport(app=app)
PNG = bytes.fromhex("89504e470d0a1a0a") + b"fake image bytes"


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token, host):
    return {"Authorization": f"Bearer {token}", "x-tenant-host": host}


async def _workspace(slug: str, *, source_type="LOOM",
                     source_ref="https://www.loom.com/share/abc123"):
    host = f"{slug}.localhost"
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        t = Tenant(slug=slug, name=slug.title(), status="active", plan="portfolio",
                   config={"features": {"intranet": True}})
        s.add(t)
        await s.flush()
        s.add(Domain(tenant_id=t.id, hostname=host, is_primary=True))
        s.add(Business(tenant_id=t.id, key="main", name="Main", tag="Business", sort_order=0))
        u = User(tenant_id=t.id, email=f"agent@{slug}.test", name="Agent",
                 password_hash=hash_pw("password123"), role="member", status="active",
                 tab_access=[], token_version=0)
        other = User(tenant_id=t.id, email=f"other@{slug}.test", name="Other",
                     password_hash=hash_pw("password123"), role="member", status="active",
                     tab_access=[], token_version=0)
        s.add_all([u, other])
        await s.flush()
        agent = IntranetRole(tenant_id=t.id, key="agent", name="Agent", sort=1, published_at=now)
        lead = IntranetRole(tenant_id=t.id, key="lead", name="Lead", sort=0, published_at=now)
        s.add_all([agent, lead])
        await s.flush()
        s.add_all([
            IntranetMember(tenant_id=t.id, full_name="Ada", email=u.email, role_id=agent.id,
                           status="Active", auth_source="Manual", user_id=u.id),
            IntranetMember(tenant_id=t.id, full_name="Lee", email=other.email, role_id=lead.id,
                           status="Active", auth_source="Manual", user_id=other.id),
        ])
        course = IntranetCourse(tenant_id=t.id, title="Listing Mastery", category="Sales",
                                state="Live", sort=1, published_at=now)
        s.add(course)
        await s.flush()
        lesson = IntranetLesson(
            tenant_id=t.id, course_id=course.id, title="Handling the commission conversation",
            source_type=source_type, source_ref=source_ref, sort=1,
            description="Sellers ask about your fee because they do not yet understand what "
                        "they are buying.",
            duration_minutes=9, required=True, published_at=now)
        s.add(lesson)
        await s.flush()
        await s.commit()
        return {"host": host, "tenant_id": t.id, "course_id": course.id, "lesson_id": lesson.id,
                "agent_role": agent.id, "lead_role": lead.id,
                "token": make_token(u.id, t.id, 0), "other": make_token(other.id, t.id, 0)}


async def _lesson(ws, token=None):
    async with _client() as c:
        r = await c.get("/api/v1/intranet/config",
                        headers=_H(token or ws["token"], ws["host"]))
    assert r.status_code == 200, r.text
    courses = r.json()["config"]["content"]["courses"]
    if not courses:
        return None
    return courses[0]["lessons"][0]


async def _attach(ws, *, kind="file", published=True, title="Commission One-pager"):
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        row = IntranetLessonAttachment(
            tenant_id=ws["tenant_id"], lesson_id=ws["lesson_id"], title=title, kind=kind,
            note="2 pages", sort=0, published_at=now if published else None,
            draft_dirty=not published)
        if kind == "file":
            row.id = uuid.uuid4()
            key = f"intranet/{ws['tenant_id']}/lessons/{ws['lesson_id']}/{row.id}-a.png"
            binder_storage.put(key, PNG, "image/png")
            row.storage_key, row.filename = key, "a.png"
            row.content_type, row.byte_size = "image/png", len(PNG)
        else:
            row.url = "https://drive.example.com/cma-template"
        s.add(row)
        await s.commit()
        return row.id


# ── what can actually be embedded ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("source_type,url,mode", [
    # Providers whose embed URL exists precisely to be framed.
    ("LOOM", "https://www.loom.com/share/9f2c1", "iframe"),
    ("HERE", "https://youtu.be/dQw4w9WgXcQ", "iframe"),
    ("HERE", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "iframe"),
    ("HERE", "https://vimeo.com/76979871", "iframe"),
    # A file the browser plays itself.
    ("HERE", "https://cdn.example.com/lesson.mp4", "video"),
    ("PDF", "https://cdn.example.com/handbook.pdf", "iframe"),
    # Logged-in products. Framing these produces a refusal, not a video.
    ("SKOOL", "https://www.skool.com/utah-life/classroom/abc", "link"),
    ("PLACE", "https://place.com/training/123", "link"),
    ("EXP", "https://exp.world/course/7", "link"),
])
def test_each_source_resolves_to_a_mode_that_actually_works(source_type, url, mode):
    assert lesson_media.resolve(source_type, url)["mode"] == mode


def test_a_walled_source_says_where_the_lesson_lives():
    """"Open in Skool" is a working instruction. A grey box where a video should be is a bug
    report."""
    out = lesson_media.resolve("SKOOL", "https://www.skool.com/x/classroom/1")
    assert out["mode"] == "link"
    assert "Skool" in out["reason"]
    assert out["url"] == "https://www.skool.com/x/classroom/1"


def test_the_url_is_believed_over_the_declared_type():
    """A team pastes a YouTube link into a lesson typed "Hosted" because Hosted is the first
    option in the dropdown. `<video src="https://youtube.com/watch?v=...">` plays nothing."""
    assert lesson_media.resolve("HERE", "https://youtu.be/abc")["mode"] == "iframe"
    # ...and the reverse: an mp4 filed under a walled platform is still an mp4.
    assert lesson_media.resolve("SKOOL", "https://cdn.example.com/x.mp4")["mode"] == "video"


def test_a_loom_share_link_becomes_its_embed_url():
    """loom.com/share/<id> in an iframe renders Loom's page furniture, not the player."""
    assert lesson_media.resolve("LOOM", "https://www.loom.com/share/9f2c1")["url"] \
        == "https://www.loom.com/embed/9f2c1"


def test_a_lesson_with_no_source_says_so_rather_than_framing_nothing():
    out = lesson_media.resolve("LOOM", None)
    assert out["mode"] == "none" and out["url"] is None and out["reason"]


@pytest.mark.parametrize("source_type,url", [
    ("LOOM", "https://www.loom.com/share/a1"), ("HERE", "https://youtu.be/a1"),
    ("HERE", "https://vimeo.com/1"), ("HERE", "https://x.test/a.mp4"),
    ("PDF", "https://x.test/a.pdf"), ("SKOOL", "https://skool.com/a"),
    ("PLACE", "https://place.com/a"), ("EXP", "https://exp.world/a"),
    ("HERE", "https://x.test/page"), ("LOOM", None), ("LOOM", ""),
])
def test_every_branch_returns_the_same_four_keys(source_type, url):
    """The portal reads `label` to name a launch button. One branch returning it and another not
    means a consumer has to check -- and the one that forgot shipped a button reading "Open
    SKOOL". Asserting the SHAPE catches the next branch too, which naming the cases would not."""
    out = lesson_media.resolve(source_type, url)
    assert set(out) == {"mode", "url", "label", "reason"}, f"{source_type} {url}: {sorted(out)}"
    assert out["label"], "a branch returned an empty label"


def test_a_source_is_named_the_way_it_names_itself():
    assert lesson_media.source_name("EXP") == "eXp"
    assert lesson_media.source_name("SKOOL") == "Skool"


def test_every_source_type_the_console_accepts_has_a_name_and_a_badge():
    """DERIVED FROM THE REAL VOCABULARY, not from a list written here.

    Both maps were first written against "HOSTED", a key this product has never used -- the stored
    value is `HERE`, shown as "Hosted". Nothing failed: a hosted lesson simply fell through to the
    raw enum in a sentence, and dropped out of its course's badge so the card read "Course".
    Invented rather than read, and only a check against the actual enum catches that.
    """
    from app.routers.console import LESSON_SOURCE_TYPES

    missing_name = sorted(k for k in LESSON_SOURCE_TYPES if k not in lesson_media.NAMES)
    missing_badge = sorted(k for k in LESSON_SOURCE_TYPES if k not in lesson_media.BADGES)
    assert not missing_name, f"no display name for {missing_name}"
    assert not missing_badge, f"no library badge for {missing_badge}"

    invented = sorted(set(lesson_media.NAMES) - LESSON_SOURCE_TYPES)
    assert not invented, f"names a source type the console cannot store: {invented}"
    assert not sorted(set(lesson_media.BADGES) - LESSON_SOURCE_TYPES)

    # ...and every badge value is one _BADGE_ORDER can actually order, or it silently vanishes.
    unordered = sorted(set(lesson_media.BADGES.values()) - set(lesson_media._BADGE_ORDER))
    assert not unordered, f"badge(s) missing from _BADGE_ORDER: {unordered}"


def test_a_hosted_course_is_badged_as_video():
    assert lesson_media.course_media(["HERE", "HERE"]) == "Video"
    assert lesson_media.course_media(["HERE", "LOOM"]) == "Video"
    assert lesson_media.course_media(["PDF", "LOOM"]) == "Video + PDF"
    assert lesson_media.course_media(["SKOOL"]) == "Skool"
    assert lesson_media.course_media([]) == "Course"


def test_a_course_of_everything_stops_listing_and_counts():
    """"Video + PDF + Skool + eXp" on a card has stopped telling anybody anything."""
    assert lesson_media.course_media(["LOOM", "PDF", "SKOOL", "EXP"]) == "Video + 3 more"


# ── the payload the player renders ────────────────────────────────────────────────────────

async def test_the_lesson_carries_its_copy_and_a_resolved_player():
    ws = await _workspace("lpplayer")
    lesson = await _lesson(ws)
    assert lesson["description"].startswith("Sellers ask about your fee")
    assert lesson["player"]["mode"] == "iframe"
    assert lesson["player"]["url"] == "https://www.loom.com/embed/abc123"
    assert lesson["duration_minutes"] == 9
    assert lesson["required"] is True


async def test_a_walled_lesson_reaches_the_portal_as_a_link_with_its_reason():
    ws = await _workspace("lpwalled", source_type="SKOOL",
                          source_ref="https://www.skool.com/ul/classroom/2")
    player = (await _lesson(ws))["player"]
    assert player["mode"] == "link"
    assert "Skool" in player["reason"], "the portal has nothing useful to put on the card"


async def test_handouts_are_listed_with_a_route_for_files_and_the_url_for_links():
    """A file must be fetched through our own authenticated route -- a bare href sends no
    Authorization header and 401s. A link is the author's URL and belongs to somebody else."""
    ws = await _workspace("lpattach")
    file_id = await _attach(ws, kind="file")
    await _attach(ws, kind="link", title="CMA Template")

    attachments = {a["title"]: a for a in (await _lesson(ws))["attachments"]}
    assert set(attachments) == {"Commission One-pager", "CMA Template"}
    assert attachments["Commission One-pager"]["url"] \
        == f"/intranet/lessons/{ws['lesson_id']}/attachments/{file_id}"
    assert attachments["CMA Template"]["url"] == "https://drive.example.com/cma-template"
    assert attachments["Commission One-pager"]["note"] == "2 pages"


async def test_a_draft_handout_is_not_live_yet():
    """The bug draft lessons already had once. A handout added to a live lesson would otherwise be
    published the moment it was saved, without anybody pressing Publish."""
    ws = await _workspace("lpdraft")
    await _attach(ws, published=False)
    assert (await _lesson(ws))["attachments"] == []


# ── who may read a handout ────────────────────────────────────────────────────────────────

async def test_a_handout_needs_a_session():
    ws = await _workspace("lpanon")
    att = await _attach(ws)
    async with _client() as c:
        r = await c.get(f"/api/v1/intranet/lessons/{ws['lesson_id']}/attachments/{att}",
                        headers={"x-tenant-host": ws["host"]})
    assert r.status_code in (401, 403)


async def test_another_workspace_cannot_read_this_ones_handout():
    a = await _workspace("lpcrossa")
    b = await _workspace("lpcrossb")
    att = await _attach(a)
    async with _client() as c:
        r = await c.get(f"/api/v1/intranet/lessons/{a['lesson_id']}/attachments/{att}",
                        headers=_H(b["token"], b["host"]))
    assert r.status_code == 404


async def test_a_role_outside_the_courses_audience_cannot_pull_its_handouts():
    """Filtering the LISTING alone would be cosmetic: the id is in the payload of anybody who was
    ever allowed the course, and guessable besides. This is the same lesson the SOP file route
    already taught -- a course restricted to leadership has handouts restricted to leadership."""
    ws = await _workspace("lpaudience")
    att = await _attach(ws)
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        s.add(IntranetCourseRole(tenant_id=ws["tenant_id"], course_id=ws["course_id"],
                                 role_id=ws["lead_role"], published_at=now))
        await s.commit()

    async with _client() as c:
        denied = await c.get(f"/api/v1/intranet/lessons/{ws['lesson_id']}/attachments/{att}",
                             headers=_H(ws["token"], ws["host"]))          # Ada is an Agent
        allowed = await c.get(f"/api/v1/intranet/lessons/{ws['lesson_id']}/attachments/{att}",
                              headers=_H(ws["other"], ws["host"]))         # Lee is a Lead
    assert denied.status_code == 404, "a course restricted to leadership leaked its handout"
    assert allowed.status_code == 200
    assert allowed.content == PNG

    # ...and the listing agrees with the route, rather than the two disagreeing.
    assert await _lesson(ws) is None or "Listing Mastery" not in str(await _lesson(ws))


async def test_a_role_denied_the_training_library_cannot_pull_a_handout():
    ws = await _workspace("lpcap")
    att = await _attach(ws)
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        cap = IntranetCapability(tenant_id=ws["tenant_id"], key="training_library",
                                 name="Training", description="", sort=0, published_at=now)
        s.add(cap)
        await s.flush()
        s.add(IntranetPermission(tenant_id=ws["tenant_id"], capability_id=cap.id,
                                 role_id=ws["agent_role"], level="None", published_at=now))
        await s.commit()
    async with _client() as c:
        r = await c.get(f"/api/v1/intranet/lessons/{ws['lesson_id']}/attachments/{att}",
                        headers=_H(ws["token"], ws["host"]))
    assert r.status_code == 404


async def test_a_draft_handout_cannot_be_downloaded_either():
    """Unpublished means unpublished, not merely unlisted."""
    ws = await _workspace("lpdraftdl")
    att = await _attach(ws, published=False)
    async with _client() as c:
        r = await c.get(f"/api/v1/intranet/lessons/{ws['lesson_id']}/attachments/{att}",
                        headers=_H(ws["token"], ws["host"]))
    assert r.status_code == 404


async def test_a_link_attachment_is_not_downloadable_through_the_file_route():
    """It has no bytes. Serving an empty 200 would look like a corrupt file rather than a
    mistake."""
    ws = await _workspace("lplink")
    att = await _attach(ws, kind="link")
    async with _client() as c:
        r = await c.get(f"/api/v1/intranet/lessons/{ws['lesson_id']}/attachments/{att}",
                        headers=_H(ws["token"], ws["host"]))
    assert r.status_code == 404


# ── the console side ──────────────────────────────────────────────────────────────────────

async def test_the_console_course_read_carries_each_lessons_handouts():
    """The read the lesson editor loads. Without them every lesson showed "No attachments" no
    matter what was on it -- found by opening the screen, not by reading the endpoint."""
    ws = await _workspace("lpconsole")
    await _attach(ws, kind="file")
    await _attach(ws, kind="link", title="CMA Template")

    # The agent's own user is not a console user, so grant this workspace's role console access.
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        cap = IntranetCapability(tenant_id=ws["tenant_id"], key="console_access", name="Console",
                                 description="", sort=0, published_at=now)
        s.add(cap)
        await s.flush()
        s.add(IntranetPermission(tenant_id=ws["tenant_id"], capability_id=cap.id,
                                 role_id=ws["agent_role"], level="Full", published_at=now))
        await s.commit()

    async with _client() as c:
        r = await c.get(f"/api/console/courses/{ws['course_id']}",
                        headers=_H(ws["token"], ws["host"]))
    assert r.status_code == 200, r.text
    lesson = r.json()["lessons"][0]
    assert {a["title"] for a in lesson["attachments"]} == {"Commission One-pager", "CMA Template"}
    # ...and the console sees the same player verdict the portal does, so it can warn first.
    assert lesson["player"]["mode"] == "iframe"
    assert lesson["description"].startswith("Sellers ask")


async def test_handouts_are_publishable_so_they_wait_for_publish():
    """Not symmetry: tenant_id + published_at + draft_dirty are what `_publishable_models()`
    matches on, so these three columns ARE what makes a draft handout stay out of the portal."""
    from app.routers.console import _publishable_models
    assert IntranetLessonAttachment in _publishable_models()
