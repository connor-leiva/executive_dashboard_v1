import datetime as dt
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Business, Domain, Tenant, User
from app.security import hash_pw, make_token

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token, host):
    return {"Authorization": f"Bearer {token}", "x-tenant-host": host}


async def _tenant(slug: str, *, intranet: bool):
    host = f"{slug}.localhost"
    async with SessionLocal() as s:
        existing = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if existing:
            users = (await s.execute(select(User).where(User.tenant_id == existing.id))).scalars().all()
            return host, existing, {u.email.split("@")[0]: make_token(u.id, existing.id, 0) for u in users}
        t = Tenant(
            slug=slug,
            name=slug.title(),
            status="active",
            config={"features": {"intranet": True}} if intranet else {},
        )
        s.add(t)
        await s.flush()
        s.add(Domain(tenant_id=t.id, hostname=host, is_primary=True))
        s.add(Business(tenant_id=t.id, key="main", name="Main", tag="Business", sort_order=0))
        users = [
            User(tenant_id=t.id, email=f"owner@{slug}.test", name="Owner",
                 password_hash=hash_pw("password123"), role="owner", status="active", token_version=0),
            User(tenant_id=t.id, email=f"admin@{slug}.test", name="Admin",
                 password_hash=hash_pw("password123"), role="admin", status="active", token_version=0),
            User(tenant_id=t.id, email=f"member@{slug}.test", name="Member",
                 password_hash=hash_pw("password123"), role="member", status="active",
                 tab_access=[], token_version=0),
        ]
        s.add_all(users)
        await s.commit()
        return host, t, {u.email.split("@")[0]: make_token(u.id, t.id, 0) for u in users}


async def test_intranet_is_a_tenant_module_not_a_tab_grant():
    """Module access is not a per-tab grant: a member with NO dashboard tabs still gets the
    portal, because the workspace has it and they work there.

    The app LINK additionally requires the portal to exist. This test bootstraps one, which is
    what provisioning does for a real workspace -- see
    test_the_portal_link_appears_only_once_the_portal_exists for why entitlement alone is not
    enough to show somebody a link.
    """
    from app.services.intranet_bootstrap import bootstrap_intranet

    host, tenant, tokens = await _tenant("intraon", intranet=True)
    off_host, _, off_tokens = await _tenant("intraoff", intranet=False)

    async with SessionLocal() as s:
        # Entitled but not set up: the module is reachable, and the LINK is deliberately not.
        me_before = None
        async with _client() as c:
            me_before = (await c.get("/api/v1/me", headers=_H(tokens["member"], host))).json()
        assert "intranet" not in {a["id"] for a in me_before["apps"]}, (
            "an entitled workspace with no portal was offered a link to one")

        await bootstrap_intranet(s, tenant.id, workspace_name=tenant.name, subdomain="intraon")
        await s.commit()

    async with _client() as c:
        member = await c.get("/api/v1/intranet/config", headers=_H(tokens["member"], host))
        assert member.status_code == 200, member.text
        me = (await c.get("/api/v1/me", headers=_H(tokens["member"], host))).json()
        assert "intranet" in {a["id"] for a in me["apps"]}
        assert me["tabs"] == []

        disabled = await c.get("/api/v1/intranet/config", headers=_H(off_tokens["owner"], off_host))
        assert disabled.status_code == 403
        me_off = (await c.get("/api/v1/me", headers=_H(off_tokens["owner"], off_host))).json()
        assert "intranet" not in {a["id"] for a in me_off["apps"]}


async def test_intranet_config_is_admin_only_and_tenant_scoped():
    host, _, tokens = await _tenant("intracfg", intranet=True)
    async with _client() as c:
        patch = {
            "calendar": {"google_calendar_url": "https://calendar.google.com/calendar/embed?src=test"},
            "marketing_requests": {"url": "https://forms.example.com/request", "label": "Request form"},
        }
        assert (await c.patch("/api/v1/intranet/config", headers=_H(tokens["member"], host),
                              json=patch)).status_code == 403
        saved = await c.patch("/api/v1/intranet/config", headers=_H(tokens["admin"], host),
                              json=patch)
        assert saved.status_code == 200, saved.text
        cfg = (await c.get("/api/v1/intranet/config", headers=_H(tokens["member"], host))).json()["config"]
        assert cfg["calendar"]["google_calendar_url"].startswith("https://calendar.google.com/")
        assert cfg["marketing_requests"]["url"] == "https://forms.example.com/request"

        bad = await c.patch("/api/v1/intranet/config", headers=_H(tokens["admin"], host),
                            json={"calendar": {"google_calendar_url": "https://example.com/calendar"}})
        assert bad.status_code == 400


async def test_win_the_day_state_resets_by_user_and_local_date():
    host, _, tokens = await _tenant("intrastate", intranet=True)
    other_host, _, other_tokens = await _tenant("intrastateb", intranet=True)
    payload = {
        "state_key": "2026-09-02",
        "timezone": "America/Chicago",
        "value": {"checked": {"power:Review priorities": True}, "tallies": {"calls": 7}},
    }
    async with _client() as c:
        saved = await c.put("/api/v1/intranet/state/wtd", headers=_H(tokens["member"], host),
                            json=payload)
        assert saved.status_code == 200, saved.text
        same = (await c.get("/api/v1/intranet/state/wtd?state_key=2026-09-02",
                            headers=_H(tokens["member"], host))).json()
        assert same["value"]["tallies"]["calls"] == 7

        next_day = (await c.get("/api/v1/intranet/state/wtd?state_key=2026-09-03",
                               headers=_H(tokens["member"], host))).json()
        assert next_day["value"] == {}
        same_tenant_other_user = (await c.get("/api/v1/intranet/state/wtd?state_key=2026-09-02",
                                             headers=_H(tokens["owner"], host))).json()
        assert same_tenant_other_user["value"] == {}
        other_tenant = (await c.get("/api/v1/intranet/state/wtd?state_key=2026-09-02",
                                   headers=_H(other_tokens["member"], other_host))).json()
        assert other_tenant["value"] == {}


async def _marketing_ready(tenant_id, *, required=None, enabled=True):
    """Configure the workspace the way the console would, and put the submitter on the roster."""
    import uuid as _uuid

    from app.models import IntranetMarketingSetting, IntranetMember, IntranetRole

    async with SessionLocal() as s:
        role = (await s.execute(select(IntranetRole).where(
            IntranetRole.tenant_id == tenant_id))).scalars().first()
        if role is None:
            role = IntranetRole(tenant_id=tenant_id, key="agent", name="Agent", sort=0)
            s.add(role)
            await s.flush()
        user = (await s.execute(select(User).where(
            User.tenant_id == tenant_id, User.role == "member"))).scalar_one()
        member = (await s.execute(select(IntranetMember).where(
            IntranetMember.tenant_id == tenant_id,
            IntranetMember.email == user.email))).scalar_one_or_none()
        if member is None:
            member = IntranetMember(tenant_id=tenant_id, role_id=role.id, full_name="Member One",
                                    email=user.email, status="Active", auth_source="Manual")
            s.add(member)
        cfg = await s.get(IntranetMarketingSetting, tenant_id)
        if cfg is None:
            cfg = IntranetMarketingSetting(tenant_id=tenant_id)
            s.add(cfg)
        cfg.enabled = enabled
        cfg.destination_type = "slack"
        cfg.destination = "#marketing"
        cfg.required_fields = list(required or [])
        await s.commit()


async def test_a_request_is_saved_even_though_nothing_delivers_it_yet():
    """The handoff's own acceptance criterion: a disconnected destination must keep requests and
    must not drop user input.

    That is why the record ships before delivery does. A request typed up and lost because nothing
    was listening is worse than no form at all, and the agent who wrote it is the one who pays.
    `delivered_at` stays null, which is the truth rather than a placeholder.
    """
    host, tenant, tokens = await _tenant("intrareq", intranet=True)
    await _marketing_ready(tenant.id)
    async with _client() as c:
        created = await c.post("/api/v1/intranet/marketing/requests",
                               headers=_H(tokens["member"], host),
                               data={"title": "Listing flyer for 12 Oak St",
                                     "listing": "12 Oak St", "priority": "High"})
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["delivered_at"] is None, "nothing delivered it; this must not claim otherwise"

        mine = await c.get("/api/v1/intranet/marketing/requests",
                           headers=_H(tokens["member"], host))
    assert mine.status_code == 200
    assert [r["title"] for r in mine.json()["items"]] == ["Listing flyer for 12 Oak St"]


async def test_required_fields_are_enforced_by_the_server_not_only_the_form():
    """A required field checked only in the browser is a suggestion. The console's setting has to
    mean something to anything that posts."""
    host, tenant, tokens = await _tenant("intrareqd", intranet=True)
    await _marketing_ready(tenant.id, required=["listing", "due_date"])
    async with _client() as c:
        h = _H(tokens["member"], host)
        missing = await c.post("/api/v1/intranet/marketing/requests", headers=h,
                               data={"title": "No listing given"})
        assert missing.status_code == 422, missing.text
        assert "listing" in missing.text and "due_date" in missing.text

        ok = await c.post("/api/v1/intranet/marketing/requests", headers=h,
                          data={"title": "Complete", "listing": "9 Elm", "due_date": "2026-10-01"})
    assert ok.status_code == 201, ok.text


async def test_requests_are_refused_while_the_workspace_has_them_switched_off():
    """Accepting into an unconfigured feature would collect work nobody is watching for, which is
    the exact failure this phase exists to avoid."""
    host, tenant, tokens = await _tenant("intrareqoff", intranet=True)
    await _marketing_ready(tenant.id, enabled=False)
    async with _client() as c:
        r = await c.post("/api/v1/intranet/marketing/requests",
                         headers=_H(tokens["member"], host), data={"title": "Should not stick"})
    assert r.status_code == 409, r.text


async def test_an_agent_sees_only_their_own_requests():
    """`/marketing/requests` is MY requests, not the workspace queue -- that is the console's, and
    an agent has no reason to read what colleagues have asked for."""
    from app.models import IntranetMarketingRequest, IntranetMember, IntranetRole

    host, tenant, tokens = await _tenant("intrareqmine", intranet=True)
    await _marketing_ready(tenant.id)
    async with SessionLocal() as s:
        role_id = (await s.execute(select(IntranetRole.id).where(
            IntranetRole.tenant_id == tenant.id))).scalars().first()
        other = IntranetMember(tenant_id=tenant.id, role_id=role_id, full_name="Someone Else",
                               email="else@intrareqmine.test", status="Active",
                               auth_source="Manual")
        s.add(other)
        await s.flush()
        s.add(IntranetMarketingRequest(tenant_id=tenant.id, requester_member_id=other.id,
                                       requester_label="Someone Else",
                                       title="Not mine", priority="Normal", status="New"))
        await s.commit()

    async with _client() as c:
        h = _H(tokens["member"], host)
        await c.post("/api/v1/intranet/marketing/requests", headers=h, data={"title": "Mine"})
        mine = (await c.get("/api/v1/intranet/marketing/requests", headers=h)).json()
    titles = [r["title"] for r in mine["items"]]
    assert titles == ["Mine"], f"another member's request leaked into my list: {titles}"


# Real magic numbers, written as hex so no shell or editor can eat an escape.
PNG_BYTES = bytes.fromhex("89504e470d0a1a0a") + b"fake image body"
HTML_BYTES = b"<html><script>alert(document.cookie)</script></html>"


async def test_a_request_and_its_files_arrive_together():
    """Submission is atomic. For a listing flyer the photograph often IS the request, and an
    upload-after-create flow whose second step fails leaves a record that reads as complete with
    the point of it missing -- silently, and on the agent who did the work."""
    host, tenant, tokens = await _tenant("intraatt", intranet=True)
    await _marketing_ready(tenant.id)
    async with _client() as c:
        r = await c.post("/api/v1/intranet/marketing/requests",
                         headers=_H(tokens["member"], host),
                         data={"title": "Flyer with photo"},
                         files=[("files", ("shot.png", PNG_BYTES, "image/png"))])
        assert r.status_code == 201, r.text
        assert r.json()["attachment_count"] == 1

        mine = (await c.get("/api/v1/intranet/marketing/requests",
                            headers=_H(tokens["member"], host))).json()
    assert mine["items"][0]["attachment_count"] == 1


async def test_a_file_is_judged_by_its_bytes_not_its_label():
    """A browser will send whatever Content-Type it is told to. If a download echoes that back,
    an HTML file labelled image/png becomes stored XSS against the next person who opens it."""
    host, tenant, tokens = await _tenant("intrasniff", intranet=True)
    await _marketing_ready(tenant.id)
    async with _client() as c:
        r = await c.post("/api/v1/intranet/marketing/requests",
                         headers=_H(tokens["member"], host),
                         data={"title": "Nice try"},
                         files=[("files", ("photo.png", HTML_BYTES, "image/png"))])
    assert r.status_code == 422, f"HTML was accepted as a PNG: {r.status_code} {r.text}"


async def test_one_bad_file_saves_nothing_at_all():
    """The files are validated before anything is written, so a rejected second file cannot leave
    a saved request and one orphaned upload behind."""
    from app.models import IntranetMarketingRequest

    host, tenant, tokens = await _tenant("intraatomic", intranet=True)
    await _marketing_ready(tenant.id)
    async with _client() as c:
        r = await c.post("/api/v1/intranet/marketing/requests",
                         headers=_H(tokens["member"], host),
                         data={"title": "Half a request"},
                         files=[("files", ("ok.png", PNG_BYTES, "image/png")),
                                ("files", ("bad.png", HTML_BYTES, "image/png"))])
    assert r.status_code == 422, r.text
    async with SessionLocal() as s:
        rows = (await s.execute(select(IntranetMarketingRequest).where(
            IntranetMarketingRequest.tenant_id == tenant.id,
            IntranetMarketingRequest.title == "Half a request"))).scalars().all()
    assert rows == [], "a rejected upload left the request behind"


async def test_attachments_can_be_required_now_that_they_arrive_with_the_request():
    """The whole reason submission is multipart: a requirement and the file it demands have to be
    in the same request for the rule to be enforceable at all."""
    host, tenant, tokens = await _tenant("intraattreq", intranet=True)
    await _marketing_ready(tenant.id, required=["attachments"])
    async with _client() as c:
        h = _H(tokens["member"], host)
        without = await c.post("/api/v1/intranet/marketing/requests", headers=h,
                               data={"title": "No file"})
        assert without.status_code == 422, without.text
        assert "attachments" in without.text

        with_file = await c.post("/api/v1/intranet/marketing/requests", headers=h,
                                 data={"title": "With file"},
                                 files=[("files", ("a.png", PNG_BYTES, "image/png"))])
    assert with_file.status_code == 201, with_file.text


async def test_an_attachment_downloads_as_an_attachment_and_only_to_its_owner():
    """Two properties in one place because they fail together: a file another user uploaded must
    never render inline, and it must not be readable by whoever guesses its id."""
    from app.models import IntranetMarketingAttachment

    host, tenant, tokens = await _tenant("intraattdl", intranet=True)
    await _marketing_ready(tenant.id)
    async with _client() as c:
        created = await c.post("/api/v1/intranet/marketing/requests",
                               headers=_H(tokens["member"], host),
                               data={"title": "Downloadable"},
                               files=[("files", ("shot.png", PNG_BYTES, "image/png"))])
        request_id = created.json()["id"]
    async with SessionLocal() as s:
        att = (await s.execute(select(IntranetMarketingAttachment).where(
            IntranetMarketingAttachment.tenant_id == tenant.id))).scalars().first()

    url = f"/api/v1/intranet/marketing/requests/{request_id}/attachments/{att.id}"
    async with _client() as c:
        mine = await c.get(url, headers=_H(tokens["member"], host))
        # The admin user is not the requester, and this route is scoped to the requester's own
        # files -- the workspace queue is the console's job, with its own authorisation.
        other = await c.get(url, headers=_H(tokens["admin"], host))
    assert mine.status_code == 200, mine.text
    assert mine.headers["content-disposition"].startswith("attachment;")
    assert mine.headers["content-type"].startswith("image/png")
    assert other.status_code == 404, f"another member read the file: {other.status_code}"


async def test_a_workspace_gets_its_own_content_not_another_customers():
    """THE MULTI-TENANCY FIX, asserted.

    The console has always written roles, launchpad tiles, Win the Day lists, courses and SOPs
    into tenant-scoped tables. The intranet read none of them -- it rendered a compiled-in
    constants file shaped around the first customer, so every workspace on the platform would
    have seen that customer's navigation, roles and tool stack no matter what their own admin
    configured. The console was configuring tables nothing consumed.
    """
    from app.models import IntranetLaunchpadTile, IntranetRole, IntranetWtdList

    host, tenant, tokens = await _tenant("intraown", intranet=True)
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    async with SessionLocal() as s:
        s.add_all([
            IntranetRole(tenant_id=tenant.id, key="stylist", name="Stylist", sort=0,
                         published_at=now),
            IntranetLaunchpadTile(tenant_id=tenant.id, name="Acme Booking", tile_group="Daily",
                                  url="https://booking.example.test", auth_type="Link", sort=0,
                                  active=True, published_at=now),
            IntranetWtdList(tenant_id=tenant.id, position=0, name="Chair turns",
                            provider="manual", active=True, published_at=now),
        ])
        await s.commit()

    async with _client() as c:
        cfg = (await c.get("/api/v1/intranet/config",
                           headers=_H(tokens["member"], host))).json()["config"]

    content = cfg["content"]
    assert [r["name"] for r in content["roles"]] == ["Stylist"], content["roles"]
    assert [w["name"] for w in content["wtd_lists"]] == ["Chair turns"]
    tools = [t["name"] for g in content["tool_groups"] for t in g["tools"]]
    assert tools == ["Acme Booking"], tools
    # And nothing from the customer the constants file was shaped around.
    blob = str(cfg)
    for leaked in ("Sunburst", "Follow Up Boss", "Sisu", "Buyer Agent"):
        assert leaked not in blob, f"another customer's content leaked in: {leaked}"


async def test_an_unpublished_row_is_not_live_yet():
    """The live intranet shows published state only. A tile created and never published has not
    been released to the workspace, and showing it would make the console's publish button a
    decoration."""
    from app.models import IntranetLaunchpadTile

    host, tenant, tokens = await _tenant("intradraft", intranet=True)
    async with SessionLocal() as s:
        s.add(IntranetLaunchpadTile(
            tenant_id=tenant.id, name="Not Published Yet", tile_group="Daily",
            url="https://draft.example.test", auth_type="Link", sort=0, active=True,
            published_at=None))
        await s.commit()

    async with _client() as c:
        cfg = (await c.get("/api/v1/intranet/config",
                           headers=_H(tokens["member"], host))).json()["config"]
    tools = [t["name"] for g in cfg["content"]["tool_groups"] for t in g["tools"]]
    assert "Not Published Yet" not in tools, "a draft tile went live"


async def test_the_workspace_names_itself():
    """The rail wordmark, the page title, the assistant button and the sign-in screen all read
    "Utah Life" from a constant, so every customer's portal wore the first customer's name."""
    host, tenant, tokens = await _tenant("intraname", intranet=True)
    async with _client() as c:
        cfg = (await c.get("/api/v1/intranet/config",
                           headers=_H(tokens["member"], host))).json()["config"]
    assert cfg["workspace"]["name"] == tenant.name
    assert "Utah Life" not in str(cfg["workspace"])


# -- the member content payload ------------------------------------------------------------
# _published_content was the entire member content API and it returned titles. The console
# authored lesson sources, durations, SOP versions and owners correctly; the payload dropped all
# of it, which is why the portal fell back to a compiled-in constants.js -- there was nothing in
# the response to render.

async def _learning(slug: str, *, lesson_published=True, course_roles=None):
    """A workspace with one live course, one lesson, and one live SOP with a file."""
    from app.models import (IntranetCourse, IntranetCourseRole, IntranetLesson, IntranetMember,
                            IntranetRole, IntranetSop, IntranetSopCategory, IntranetSopVersion)
    from app.services import binder_storage

    host, tenant, tokens = await _tenant(slug, intranet=True)
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        agent = IntranetRole(tenant_id=tenant.id, key="agent", name="Agent", sort=1,
                             published_at=now)
        leader = IntranetRole(tenant_id=tenant.id, key="leader", name="Leader", sort=2,
                              is_leadership=True, published_at=now)
        s.add_all([agent, leader])
        await s.flush()
        member = IntranetMember(tenant_id=tenant.id, full_name="A Member",
                                email=f"member@{slug}.test", role_id=agent.id, status="Active",
                                auth_source="Manual")
        s.add(member)
        await s.flush()

        course = IntranetCourse(tenant_id=tenant.id, title="Listing Mastery", category="Sales",
                                description="How we take a listing.", state="Live",
                                required_for_onboarding=True, sequential=True, sort=1,
                                published_at=now)
        s.add(course)
        await s.flush()
        s.add(IntranetLesson(
            tenant_id=tenant.id, course_id=course.id, title="The pre-listing packet",
            source_type="LOOM", source_ref="https://videos.example.test/packet",
            source_label="Loom", duration_minutes=12, required=True, sort=1,
            published_at=now if lesson_published else None))
        for role in (course_roles or []):
            s.add(IntranetCourseRole(tenant_id=tenant.id, course_id=course.id,
                                     role_id=(agent if role == "agent" else leader).id,
                                     published_at=now))

        category = IntranetSopCategory(tenant_id=tenant.id, name="Transactions", sort=1,
                                       published_at=now)
        s.add(category)
        await s.flush()
        sop = IntranetSop(tenant_id=tenant.id, title="Under contract checklist",
                          category_id=category.id, owner_member_id=member.id, state="Live",
                          review_due_on=dt.date(2027, 1, 31), published_at=now)
        s.add(sop)
        await s.flush()
        ref = binder_storage.store(tenant.id, sop.id, "checklist.pdf", b"%PDF-1.4 checklist")
        version = IntranetSopVersion(tenant_id=tenant.id, sop_id=sop.id, version_label="v3",
                                     filename="checklist.pdf", storage_key=ref,
                                     content_type="application/pdf", byte_size=18)
        s.add(version)
        await s.flush()
        sop.current_version_id = version.id
        await s.commit()
        return host, tokens, {"sop_id": str(sop.id), "course_id": str(course.id),
                              "tenant_id": tenant.id}


async def _content(host, token):
    async with _client() as c:
        r = await c.get("/api/v1/intranet/config", headers=_H(token, host))
    assert r.status_code == 200, r.text
    return r.json()["config"]["content"]


async def test_a_draft_lesson_is_not_live_yet():
    """Lessons were the ONE content type with no published filter. Roles, tiles, lists, courses,
    SOPs and categories all had one; lessons did not, so a half-written draft was live to the
    whole team the moment it was saved."""
    host, tokens, _ = await _learning("intralessondraft", lesson_published=False)
    content = await _content(host, tokens["member"])
    titles = [le["title"] for c in content["courses"] for le in c["lessons"]]
    assert titles == [], f"a draft lesson went live: {titles}"


async def test_a_lesson_carries_what_it_takes_to_play_it():
    host, tokens, _ = await _learning("intralesson")
    content = await _content(host, tokens["member"])
    course = next(c for c in content["courses"] if c["title"] == "Listing Mastery")
    assert course["category"] == "Sales"
    assert course["required_for_onboarding"] is True
    assert course["sequential"] is True
    lesson = course["lessons"][0]
    # Without source_type and source_ref there is nothing to open -- which is why the portal read
    # a hardcoded list instead of this payload.
    assert lesson["source_type"] == "LOOM"
    assert lesson["source_ref"] == "https://videos.example.test/packet"
    assert lesson["duration_minutes"] == 12
    assert lesson["required"] is True


async def test_a_course_is_only_offered_to_the_roles_it_names():
    """IntranetCourseRole has been written by the console since it shipped and read by nothing.
    Same rule as launchpad tiles: no rows means everyone, rows mean those roles."""
    # Named for a role the member does not hold.
    host, tokens, _ = await _learning("intracourserole", course_roles=["leader"])
    content = await _content(host, tokens["member"])
    assert [c["title"] for c in content["courses"]] == [], "a restricted course was offered"

    # Named for the role they do hold.
    host2, tokens2, _ = await _learning("intracourseown", course_roles=["agent"])
    assert [c["title"] for c in (await _content(host2, tokens2["member"]))["courses"]] \
        == ["Listing Mastery"]

    # No audience rows at all: visible to everyone.
    host3, tokens3, _ = await _learning("intracourseall")
    assert [c["title"] for c in (await _content(host3, tokens3["member"]))["courses"]] \
        == ["Listing Mastery"]


async def test_an_sop_carries_its_version_owner_and_a_way_to_open_it():
    host, tokens, ids = await _learning("intrasopmeta")
    content = await _content(host, tokens["member"])
    sop = content["sops"][0]
    assert sop["title"] == "Under contract checklist"
    assert sop["category"] == "Transactions"
    assert sop["owner"] == "A Member"
    assert sop["version"] == "v3"
    assert sop["filename"] == "checklist.pdf"
    assert sop["review_due_on"] == "2027-01-31"
    assert sop["updated_at"]
    assert sop["file_url"] == f"/intranet/sops/{ids['sop_id']}/file"


async def test_an_sop_downloads_and_an_archived_one_stops_downloading():
    """The documents have been stored since the console shipped and no member route served them
    back, so the SOP library was a list of titles for something nobody could open."""
    from app.models import IntranetSop

    host, tokens, ids = await _learning("intrasopfile")
    async with _client() as c:
        r = await c.get(f"/api/v1/intranet/sops/{ids['sop_id']}/file",
                        headers=_H(tokens["member"], host))
    assert r.status_code == 200, r.text
    assert r.content == b"%PDF-1.4 checklist"
    # Never inline: a document somebody else uploaded, rendered in the tab, is stored XSS.
    assert r.headers["content-disposition"].startswith("attachment;")

    # Archived after the member took a copy of the id: the link must stop working.
    async with SessionLocal() as s:
        sop = await s.get(IntranetSop, uuid.UUID(ids["sop_id"]))
        sop.state = "Archived"
        await s.commit()
    async with _client() as c:
        again = await c.get(f"/api/v1/intranet/sops/{ids['sop_id']}/file",
                            headers=_H(tokens["member"], host))
    assert again.status_code == 404


async def test_one_workspaces_sop_file_is_not_reachable_from_another():
    host_a, tokens_a, ids_a = await _learning("intrasopmine")
    host_b, tokens_b, _ = await _learning("intrasoptheirs")
    async with _client() as c:
        crossed = await c.get(f"/api/v1/intranet/sops/{ids_a['sop_id']}/file",
                              headers=_H(tokens_b["member"], host_b))
    assert crossed.status_code == 404


async def test_acknowledging_an_sop_is_recorded_where_the_console_reads_it():
    """IntranetSopAcknowledgement has been counted by the console since it shipped and had never
    had a row inserted: the portal's tick box wrote to a per-user state blob nobody else could
    see, so an admin asking "who has read the new procedure?" got zero for everybody, forever."""
    from app.models import IntranetSopAcknowledgement

    host, tokens, ids = await _learning("intraack")
    async with _client() as c:
        r = await c.post(f"/api/v1/intranet/sops/{ids['sop_id']}/acknowledge",
                         headers=_H(tokens["member"], host))
    assert r.status_code == 200, r.text
    assert r.json()["acknowledged_at"]

    async with SessionLocal() as s:
        rows = (await s.execute(select(IntranetSopAcknowledgement).where(
            IntranetSopAcknowledgement.tenant_id == ids["tenant_id"]))).scalars().all()
    assert len(rows) == 1, "nothing was recorded"

    # ...and the member's own view now says so, which is what the button reads.
    content = await _content(host, tokens["member"])
    assert content["sops"][0]["acknowledged_at"]


async def test_acknowledging_twice_is_the_same_fact_not_an_error():
    """A double click is not a second assertion, and the unique constraint would otherwise turn
    an impatient click into a 500."""
    from app.models import IntranetSopAcknowledgement

    host, tokens, ids = await _learning("intraacktwice")
    async with _client() as c:
        first = await c.post(f"/api/v1/intranet/sops/{ids['sop_id']}/acknowledge",
                             headers=_H(tokens["member"], host))
        second = await c.post(f"/api/v1/intranet/sops/{ids['sop_id']}/acknowledge",
                              headers=_H(tokens["member"], host))
    assert first.status_code == 200 and second.status_code == 200, second.text
    # Same timestamp: the second call reports the original fact rather than moving it.
    assert first.json()["acknowledged_at"] == second.json()["acknowledged_at"]
    async with SessionLocal() as s:
        rows = (await s.execute(select(IntranetSopAcknowledgement).where(
            IntranetSopAcknowledgement.tenant_id == ids["tenant_id"]))).scalars().all()
    assert len(rows) == 1, f"acknowledging twice wrote {len(rows)} rows"


async def test_a_new_version_asks_everybody_again():
    """The acknowledgement is against a VERSION, not the SOP. Republishing a procedure must not
    silently inherit an assertion about the document it replaced."""
    from app.models import IntranetSop, IntranetSopVersion
    from app.services import binder_storage

    host, tokens, ids = await _learning("intraackversion")
    async with _client() as c:
        assert (await c.post(f"/api/v1/intranet/sops/{ids['sop_id']}/acknowledge",
                             headers=_H(tokens["member"], host))).status_code == 200
    assert (await _content(host, tokens["member"]))["sops"][0]["acknowledged_at"]

    # A new version supersedes it.
    async with SessionLocal() as s:
        sop = await s.get(IntranetSop, uuid.UUID(ids["sop_id"]))
        ref = binder_storage.store(sop.tenant_id, sop.id, "checklist-v4.pdf", b"%PDF-1.4 newer")
        v4 = IntranetSopVersion(tenant_id=sop.tenant_id, sop_id=sop.id, version_label="v4",
                                filename="checklist-v4.pdf", storage_key=ref,
                                content_type="application/pdf", byte_size=14)
        s.add(v4)
        await s.flush()
        sop.current_version_id = v4.id
        await s.commit()

    after = (await _content(host, tokens["member"]))["sops"][0]
    assert after["version"] == "v4"
    assert after["acknowledged_at"] is None, "an old acknowledgement carried over to a new version"


async def test_an_archived_sop_cannot_be_acknowledged():
    from app.models import IntranetSop

    host, tokens, ids = await _learning("intraackarchived")
    async with SessionLocal() as s:
        sop = await s.get(IntranetSop, uuid.UUID(ids["sop_id"]))
        sop.state = "Archived"
        await s.commit()
    async with _client() as c:
        r = await c.post(f"/api/v1/intranet/sops/{ids['sop_id']}/acknowledge",
                         headers=_H(tokens["member"], host))
    assert r.status_code == 404


# -- the permissions matrix ----------------------------------------------------------------
# IntranetPermission.level spans every capability a workspace defines, the console edits it, the
# publish cycle ships it -- and the only place it had ever been read was console_access, to decide
# who may open the console. An admin could set Training Library to None for a role, publish it,
# see it saved, and that role would still see every course.

async def _deny(tenant_id, capability_key: str, role_key: str = "agent"):
    """Set one capability to None for one role, the way the console would."""
    from app.models import IntranetCapability, IntranetPermission, IntranetRole

    async with SessionLocal() as s:
        role = (await s.execute(select(IntranetRole).where(
            IntranetRole.tenant_id == tenant_id, IntranetRole.key == role_key))).scalar_one()
        cap = (await s.execute(select(IntranetCapability).where(
            IntranetCapability.tenant_id == tenant_id,
            IntranetCapability.key == capability_key))).scalar_one_or_none()
        if cap is None:
            cap = IntranetCapability(tenant_id=tenant_id, key=capability_key,
                                     name=capability_key, description="", sort=1,
                                     published_at=dt.datetime.now(dt.timezone.utc))
            s.add(cap)
            await s.flush()
        s.add(IntranetPermission(tenant_id=tenant_id, role_id=role.id, capability_id=cap.id,
                                 level="None",
                                 published_at=dt.datetime.now(dt.timezone.utc)))
        await s.commit()


async def test_a_denied_capability_actually_hides_its_content():
    host, tokens, ids = await _learning("permdeny")
    # Visible first, so the test proves the denial did it rather than the content being absent.
    before = await _content(host, tokens["member"])
    assert before["courses"] and before["sops"]

    await _deny(ids["tenant_id"], "training_library")
    after = await _content(host, tokens["member"])
    assert after["courses"] == [], "a role denied Training Library was still shown every course"
    assert after["sops"], "denying one capability took another with it"


async def test_denying_the_sop_library_also_closes_the_document_itself():
    """The listing is the cosmetic half. Ids are guessable from an old page, a bookmark or a
    colleague, and the file endpoint is what actually hands over the document -- so filtering the
    list while leaving the read path open would be a permission in name only."""
    host, tokens, ids = await _learning("permsopfile")
    async with _client() as c:
        assert (await c.get(f"/api/v1/intranet/sops/{ids['sop_id']}/file",
                            headers=_H(tokens["member"], host))).status_code == 200

    await _deny(ids["tenant_id"], "sop_library")
    async with _client() as c:
        blocked = await c.get(f"/api/v1/intranet/sops/{ids['sop_id']}/file",
                              headers=_H(tokens["member"], host))
        ack = await c.post(f"/api/v1/intranet/sops/{ids['sop_id']}/acknowledge",
                           headers=_H(tokens["member"], host))
    # 404, not 403: the answer must not confirm which SOPs exist.
    assert blocked.status_code == 404, "a denied role could still download the document"
    assert ack.status_code == 404, "a denied role could still acknowledge it"
    assert (await _content(host, tokens["member"]))["sops"] == []


async def test_a_capability_nobody_has_set_is_permitted():
    """Absence means permitted, matching how tile audiences already behave. Denying by default
    would black a workspace out the moment it defined a new capability, and denial has to be a
    decision somebody made rather than a row nobody wrote."""
    host, tokens, _ = await _learning("permdefault")
    content = await _content(host, tokens["member"])
    assert content["courses"] and content["sops"]
    # ...and the levels are reported, so the rail can hide what the server would refuse.
    assert content["capabilities"] == {}


async def test_the_matrix_is_reported_so_the_rail_can_match_it():
    host, tokens, ids = await _learning("permreport")
    await _deny(ids["tenant_id"], "wtd")
    content = await _content(host, tokens["member"])
    assert content["capabilities"].get("wtd") == "None"
    assert content["wtd_lists"] == []


async def test_a_denied_role_cannot_file_a_marketing_request():
    """The form is hidden for them, but hiding a form is not a permission -- this is the endpoint
    the form posts to."""
    host, tokens, ids = await _learning("permmarketing")
    await _marketing_ready(ids["tenant_id"])
    await _deny(ids["tenant_id"], "marketing_requests")

    async with _client() as c:
        cfg = (await c.get("/api/v1/intranet/config",
                           headers=_H(tokens["member"], host))).json()["config"]
        posted = await c.post("/api/v1/intranet/marketing/requests",
                              headers=_H(tokens["member"], host),
                              data={"title": "Flyer please"})
    assert cfg["marketing"]["available"] is False, "the form was offered to a denied role"
    assert posted.status_code == 403, "a denied role filed a request anyway"


async def test_a_call_list_carries_the_link_the_console_configured():
    """Two mechanisms existed for one thing and the portal read the wrong one. The console
    authors a provider plus an external_list_id per list; the portal rendered an older
    `config.links.fub_lists` map keyed by hardcoded list names that the console never writes. A
    workspace could fill in every list id and still see "No URL configured" on every card."""
    from app.models import IntranetIntegration, IntranetWtdList

    host, tenant, tokens = await _tenant("wtdlink", intranet=True)
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        s.add(IntranetIntegration(
            tenant_id=tenant.id, provider_key="follow_up_boss", display_name="Follow Up Boss",
            role_label="CRM", status="Connected",
            base_url="https://team.followupboss.com/2/people/list/"))
        s.add(IntranetWtdList(tenant_id=tenant.id, name="New leads", position=1, active=True,
                              provider="follow_up_boss", external_list_id="42",
                              script_name="New lead script", daily_target=15,
                              published_at=now))
        # A list with no id configured yet: it must still appear, just without a link.
        s.add(IntranetWtdList(tenant_id=tenant.id, name="Sphere", position=2, active=True,
                              provider="follow_up_boss", published_at=now))
        await s.commit()

    async with _client() as c:
        cfg = (await c.get("/api/v1/intranet/config",
                           headers=_H(tokens["member"], host))).json()["config"]
    lists = {row["name"]: row for row in cfg["content"]["wtd_lists"]}
    assert lists["New leads"]["url"] == "https://team.followupboss.com/2/people/list/42"
    assert lists["New leads"]["script_name"] == "New lead script"
    assert lists["New leads"]["daily_target"] == 15
    # Present but unlinked, rather than hidden: an admin needs to see the list they have not
    # finished configuring.
    assert lists["Sphere"]["url"] is None


async def test_a_call_list_without_a_connected_provider_has_no_link():
    """No base URL means there is nothing to build a link out of, and half a URL is worse than
    none -- it would 404 on the agent rather than tell the admin something is missing."""
    from app.models import IntranetWtdList

    host, tenant, tokens = await _tenant("wtdnolink", intranet=True)
    async with SessionLocal() as s:
        s.add(IntranetWtdList(tenant_id=tenant.id, name="New leads", position=1, active=True,
                              provider="follow_up_boss", external_list_id="42",
                              published_at=dt.datetime.now(dt.timezone.utc)))
        await s.commit()

    async with _client() as c:
        cfg = (await c.get("/api/v1/intranet/config",
                           headers=_H(tokens["member"], host))).json()["config"]
    assert cfg["content"]["wtd_lists"][0]["url"] is None


# -- the workspace's own colours -----------------------------------------------------------
# IntranetWorkspace.palette has been authored by the console since it shipped and read by
# nothing, so every workspace's portal wore the same five colours whatever their admin picked.

async def test_the_palette_reaches_the_portal():
    from app.models import IntranetWorkspace

    host, tenant, tokens = await _tenant("palettemine", intranet=True)
    async with SessionLocal() as s:
        s.add(IntranetWorkspace(
            tenant_id=tenant.id, portal_name="Mine", subdomain="palettemine",
            palette={"ink": "#2B1B3D", "brand": "#7A4E9B", "accent": "#E0C36B",
                     "canvas": "#F4EFE8"},
            published_at=dt.datetime.now(dt.timezone.utc)))
        await s.commit()

    async with _client() as c:
        cfg = (await c.get("/api/v1/intranet/config",
                           headers=_H(tokens["member"], host))).json()["config"]
    assert cfg["workspace"]["palette"]["ink"] == "#2B1B3D"
    assert cfg["workspace"]["palette"]["brand"] == "#7A4E9B"


def test_an_unreadable_ink_is_refused_rather_than_rendered():
    """Ink is the body text colour as well as the navigation background, so a pale one is not a
    stylistic choice the design absorbs -- it is grey text on a white page. Measured live before
    this check existed: the nav sat at 1.23:1, which is invisible.

    Refused at write time, because no amount of deriving shades rescues text with no contrast
    against the surface it sits on, and an admin who picked it deserves to be told rather than
    have it silently adjusted into something they did not choose.
    """
    from fastapi import HTTPException

    from app.routers.console import MIN_INK_CONTRAST, _contrast, _palette

    # Sanity on the measure itself, so the threshold means what it says.
    assert _contrast("#171E22", "#EAE7E6") > MIN_INK_CONTRAST
    assert _contrast("#EDE7DC", "#EAE7E6") < MIN_INK_CONTRAST

    with pytest.raises(HTTPException) as caught:
        _palette({"palette": {"ink": "#EDE7DC"}})
    assert caught.value.status_code == 422
    # The message has to say WHY, or an admin just tries another pale colour.
    detail = str(caught.value.detail)
    assert "too light to read" in detail and "body text" in detail


def test_a_readable_ink_is_accepted_and_judged_against_its_own_canvas():
    from app.routers.console import _palette

    # Dark ink on the default canvas.
    assert _palette({"palette": {"ink": "#171E22"}})["ink"] == "#171E22"
    # ...and against the canvas the SAME request sets, not the default: a workspace choosing a
    # dark canvas and a mid ink is judged on the pair it actually ships.
    ok = _palette({"palette": {"ink": "#2B1B3D", "canvas": "#F4EFE8"}})
    assert ok["ink"] == "#2B1B3D" and ok["canvas"] == "#F4EFE8"


def test_a_palette_without_an_ink_is_left_alone():
    """Setting only an accent is legitimate and must not be judged against an ink nobody sent."""
    from app.routers.console import _palette

    assert _palette({"palette": {"accent": "#E0C36B"}}) == {"accent": "#E0C36B"}


# -- pages a workspace writes for itself ---------------------------------------------------
# One authored page type replaces four bespoke screens (JV Partners, Listing Marketing,
# Sunburst Coaching, On The Phone). Each of those is one customer's content wearing a route of
# its own, and building them that way means building four more for the next customer.

async def _page(slug: str, *, published=True, active=True, role_key=None, sections=True):
    from app.models import (IntranetMember, IntranetPage, IntranetPageRole,
                            IntranetPageSection, IntranetRole)

    host, tenant, tokens = await _tenant(slug, intranet=True)
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        agent = IntranetRole(tenant_id=tenant.id, key="agent", name="Agent", sort=1,
                             published_at=now)
        other = IntranetRole(tenant_id=tenant.id, key="leader", name="Leader", sort=2,
                             is_leadership=True, published_at=now)
        s.add_all([agent, other])
        await s.flush()
        s.add(IntranetMember(tenant_id=tenant.id, full_name="A Member",
                             email=f"member@{slug}.test", role_id=agent.id, status="Active",
                             auth_source="Manual"))
        page = IntranetPage(tenant_id=tenant.id, key="partners", title="JV Partners",
                            subtitle="Who to call", nav_group="Partners", sort=1,
                            active=active, published_at=now if published else None)
        s.add(page)
        await s.flush()
        if sections:
            s.add(IntranetPageSection(
                tenant_id=tenant.id, page_id=page.id, heading="Title",
                body="First paragraph.\n\nSecond paragraph.",
                links=[{"label": "Sympli", "url": "https://sympli.example.test"}],
                sort=1, published_at=now))
            # A draft section, to prove the publish gate applies here too.
            s.add(IntranetPageSection(tenant_id=tenant.id, page_id=page.id,
                                      heading="Not published", body="draft", links=[],
                                      sort=2, published_at=None))
        if role_key:
            s.add(IntranetPageRole(tenant_id=tenant.id, page_id=page.id,
                                   role_id=(agent if role_key == "agent" else other).id,
                                   published_at=now))
        await s.commit()
        return host, tokens, {"tenant_id": tenant.id, "page_id": page.id}


async def test_an_authored_page_reaches_the_member_with_its_sections():
    host, tokens, _ = await _page("pagemine")
    content = await _content(host, tokens["member"])
    page = next(p for p in content["pages"] if p["key"] == "partners")
    assert page["title"] == "JV Partners"
    assert page["nav_group"] == "Partners", "the rail needs to know which group it joins"
    assert [s["heading"] for s in page["sections"]] == ["Title"], "a draft section went live"
    assert page["sections"][0]["links"][0]["url"] == "https://sympli.example.test"


async def test_an_unpublished_or_inactive_page_is_not_live():
    host, tokens, _ = await _page("pagedraft", published=False)
    assert (await _content(host, tokens["member"]))["pages"] == [], "a draft page went live"

    host2, tokens2, _ = await _page("pageoff", active=False)
    assert (await _content(host2, tokens2["member"]))["pages"] == [], "an inactive page went live"


async def test_a_page_is_only_offered_to_the_roles_it_names():
    """Same rule as tiles and courses: no rows means everyone, rows mean those roles."""
    host, tokens, _ = await _page("pagerole", role_key="leader")
    assert (await _content(host, tokens["member"]))["pages"] == []

    host2, tokens2, _ = await _page("pageown", role_key="agent")
    assert len((await _content(host2, tokens2["member"]))["pages"]) == 1

    host3, tokens3, _ = await _page("pageall")
    assert len((await _content(host3, tokens3["member"]))["pages"]) == 1


def test_a_page_key_is_shaped_for_a_url_and_nothing_is_reserved():
    """Authored pages are routed under /p/<key>, so nothing can collide with a built-in screen
    and nothing needs reserving.

    This began as a reserved-key list written for a design where authored pages lived at the top
    level. Namespacing them made that list not merely unnecessary but wrong: it refused
    "partners", "listing", "brand" and "phone" -- exactly the four pages this feature exists to
    let a workspace replace. The point of the whole thing, blocked by its own guard.
    """
    from fastapi import HTTPException

    from app.routers.console import _page_key

    # The four this feature replaces are all available.
    for key in ("partners", "listing", "brand", "phone", "training", "sops"):
        assert _page_key({"key": key}, required=True) == key

    # Only the shape is enforced, so a key cannot carry a slash or a space into a URL.
    for bad in ("Has Space", "with/slash", "-leading"):
        with pytest.raises(HTTPException):
            _page_key({"key": bad}, required=True)

    # Case is normalised rather than refused: an admin who types "Partners" means `partners`.
    assert _page_key({"key": "Partners"}, required=True) == "partners"


def test_an_authored_link_cannot_be_a_javascript_url():
    """These links are written by an admin and clicked by their whole team, so they go through
    the same https rule every other stored URL in this product does."""
    from fastapi import HTTPException

    from app.routers.console import _page_links

    ok = _page_links({"links": [{"label": "Sympli", "url": "https://sympli.example.test"}]})
    assert ok == [{"label": "Sympli", "url": "https://sympli.example.test"}]

    for bad in ("javascript:alert(1)", "not a url", ""):
        with pytest.raises(HTTPException):
            _page_links({"links": [{"label": "x", "url": bad}]})
    # A link with no label is a button nobody can read.
    with pytest.raises(HTTPException):
        _page_links({"links": [{"label": "  ", "url": "https://ok.example.test"}]})


def test_pages_joined_the_publish_cycle_without_being_told_to():
    """_publishable_models derives its list from the mapper registry, so a new publishable table
    is picked up by shape rather than by somebody remembering to add it. That is the whole point
    of deriving it, and this pins it."""
    from app.routers.console import _publishable_models

    names = {m.__name__ for m in _publishable_models()}
    assert {"IntranetPage", "IntranetPageSection", "IntranetPageRole"} <= names


# -- Who's Who -----------------------------------------------------------------------------
# The roster was already there. This screen said "Directory is empty" while intranet_member held
# the whole team, admin-only, with no member-facing read -- the first thing a new starter looks
# for, telling them their workspace had nobody in it.

async def _roster(slug: str):
    from app.models import IntranetMember, IntranetRole

    host, tenant, tokens = await _tenant(slug, intranet=True)
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        agent = IntranetRole(tenant_id=tenant.id, key="agent", name="Agent", sort=1,
                             published_at=now)
        leader = IntranetRole(tenant_id=tenant.id, key="leader", name="Team Leader", sort=2,
                              is_leadership=True, published_at=now)
        s.add_all([agent, leader])
        await s.flush()
        s.add_all([
            IntranetMember(tenant_id=tenant.id, full_name="Zoe Agent",
                           email=f"member@{slug}.test", role_id=agent.id, status="Active",
                           auth_source="Manual", title="Buyer Agent", market="Salt Lake",
                           phone="801-555-0100", owns="Buyer pipeline",
                           bio="Ten years in residential."),
            IntranetMember(tenant_id=tenant.id, full_name="Alice Leader",
                           email=f"lead@{slug}.test", role_id=leader.id, status="Active",
                           auth_source="Manual", title="Team Leader"),
            # Neither of these has arrived, or has left.
            IntranetMember(tenant_id=tenant.id, full_name="Invited Person",
                           email=f"invited@{slug}.test", role_id=agent.id, status="Invited",
                           auth_source="Manual"),
            IntranetMember(tenant_id=tenant.id, full_name="Gone Person",
                           email=f"gone@{slug}.test", role_id=agent.id, status="Removed",
                           auth_source="Manual"),
        ])
        await s.commit()
    return host, tokens, tenant


async def test_the_directory_lists_the_workspaces_own_people():
    host, tokens, _ = await _roster("dirmine")
    people = (await _content(host, tokens["member"]))["directory"]
    by_name = {p["name"]: p for p in people}

    assert set(by_name) == {"Zoe Agent", "Alice Leader"}, (
        "an invited or removed colleague was listed as if they were here")
    zoe = by_name["Zoe Agent"]
    assert zoe["title"] == "Buyer Agent" and zoe["role"] == "Agent"
    assert zoe["market"] == "Salt Lake"
    assert zoe["phone"] == "801-555-0100"
    assert zoe["owns"] == "Buyer pipeline"
    assert zoe["bio"].startswith("Ten years")
    assert by_name["Alice Leader"]["is_leadership"] is True
    assert zoe["is_leadership"] is False


async def test_the_directory_says_nothing_about_how_somebody_signs_in():
    """A staff directory is the whole roster shown to the whole team, so what it carries matters.
    Account state -- auth_source, status history, the linked user id -- is the console's business
    and nobody else's."""
    host, tokens, _ = await _roster("dirfields")
    people = (await _content(host, tokens["member"]))["directory"]
    leaked = {k for p in people for k in p} & {
        "auth_source", "status", "user_id", "invited_at", "activated_at", "removed_at",
        "last_synced_at", "photo_key", "role_id",
    }
    assert not leaked, f"the directory exposed account fields: {sorted(leaked)}"


async def test_a_photo_needs_a_session_and_belongs_to_one_workspace():
    """Unlike a workspace's logo, a photograph of a member of staff is not public."""
    from app.models import IntranetMember
    from app.services import binder_storage

    host_a, tokens_a, tenant_a = await _roster("dirphotoa")
    host_b, tokens_b, _ = await _roster("dirphotob")
    async with SessionLocal() as s:
        member = (await s.execute(select(IntranetMember).where(
            IntranetMember.tenant_id == tenant_a.id,
            IntranetMember.full_name == "Zoe Agent"))).scalar_one()
        member.photo_key = binder_storage.store(tenant_a.id, member.id, "zoe.png", b"\x89PNG fake")
        await s.commit()
        member_id = member.id

    async with _client() as c:
        mine = await c.get(f"/api/v1/intranet/directory/{member_id}/photo",
                           headers=_H(tokens_a["member"], host_a))
        crossed = await c.get(f"/api/v1/intranet/directory/{member_id}/photo",
                              headers=_H(tokens_b["member"], host_b))
        anonymous = await c.get(f"/api/v1/intranet/directory/{member_id}/photo",
                                headers={"x-tenant-host": host_a})
    assert mine.status_code == 200 and mine.content == b"\x89PNG fake"
    assert crossed.status_code == 404, "another workspace could read a colleague's photo"
    assert anonymous.status_code in (401, 403), "a photo was served without a session"

    # ...and it is offered in the payload only once there is one to serve.
    people = {p["name"]: p for p in (await _content(host_a, tokens_a["member"]))["directory"]}
    assert people["Zoe Agent"]["photo_url"] == f"/intranet/directory/{member_id}/photo"
    assert people["Alice Leader"]["photo_url"] is None
