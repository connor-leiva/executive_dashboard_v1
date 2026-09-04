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
