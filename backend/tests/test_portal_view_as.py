"""An Axcion operator viewing a workspace's portal as one of its people: support access, extended.

The operator console's support access opens a time-boxed, read-only account in a workspace, with a
reason, an email to its owners and a line in both audit trails. This extends it into the portal:
inside an open session, the operator opens the portal AS a roster member and sees what they would
-- their home, their numbers, their follow-ups -- while the member is told nothing and nothing about
them changes. These hold the extension to its precedent: it cannot exist outside a support session,
cannot write, cannot leave the portal, and ends when the session does.
"""
import datetime as dt
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import (Agent, AuditLog, Business, Domain, Integration, IntranetMember,
                        IntranetRole, IntranetUserState, Lead, PlatformAudit, PlatformUser,
                        Tenant, User)
from app.security import enc, hash_pw, make_view_token
from app.seed import seed
from app.services import mailer
from app.services.intranet_bootstrap import bootstrap_intranet

ASGI = httpx.ASGITransport(app=app)
OP_EMAIL = "viewas-operator@platform.test"
OP_PASSWORD = "viewas-operator-password-1"


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()
    async with SessionLocal() as s:
        if (await s.execute(select(PlatformUser).where(
                PlatformUser.email == OP_EMAIL))).scalar_one_or_none() is None:
            s.add(PlatformUser(email=OP_EMAIL, name="View Operator",
                               password_hash=hash_pw(OP_PASSWORD)))
            await s.commit()


def _client():
    return httpx.AsyncClient(transport=ASGI, base_url="http://testserver")


def _H(token, host=None):
    h = {"Authorization": f"Bearer {token}"}
    if host:
        h["x-tenant-host"] = host
    return h


def _capture_mail(monkeypatch):
    sent = []

    async def fake_post(payload, headers):
        sent.append(payload)
        return 200, "{}"
    monkeypatch.setattr(mailer, "_post", fake_post)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(settings, "MAIL_FROM", "Axcion <hello@mail.axcion.io>")
    monkeypatch.setattr(settings, "MAIL_REPLY_TO", "")
    return sent


async def _op_token():
    async with _client() as c:
        r = await c.post("/api/v1/platform/login", json={"email": OP_EMAIL, "password": OP_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def _workspace(slug):
    """A workspace with a portal, an owner, Follow Up Boss synced, and two people on the roster:
    an agent added with no account at all, and a colleague whose account has training progress."""
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        t = Tenant(slug=slug, name=slug.title(), status="active", plan="portfolio",
                   config={"features": {"intranet": True}})
        s.add(t)
        await s.flush()
        s.add(Domain(tenant_id=t.id, hostname=f"{slug}.brokerage.test", is_primary=True))
        b = Business(tenant_id=t.id, key="main", name="Main", tag="Business", sort_order=0)
        s.add(b)
        owner = User(tenant_id=t.id, email=f"owner@{slug}.test", name="Owner", role="owner",
                     status="active", password_hash=hash_pw("pw-owner-123"), token_version=0)
        s.add(owner)
        await s.flush()
        await bootstrap_intranet(s, t.id, workspace_name=slug.title(), subdomain=slug,
                                 owner_email=owner.email)
        roles = {r.key: r.id for r in (await s.execute(select(IntranetRole).where(
            IntranetRole.tenant_id == t.id))).scalars()}
        s.add(Integration(tenant_id=t.id, provider="fub", business_id=b.id, status="connected",
                          access_token_enc=enc("fka_test"), last_synced_at=now,
                          config={"fub_state": {"account_id": 1, "account_domain": "acme"}}))
        agent_member = IntranetMember(tenant_id=t.id, full_name="Ada Agent",
                                      email=f"ada@{slug}.test", role_id=roles["member"],
                                      status="Invited", auth_source="Manual")
        s.add(agent_member)
        colleague = User(tenant_id=t.id, email=f"bo@{slug}.test", name="Bo", role="member",
                         status="active", tab_access=[], token_version=0)
        s.add(colleague)
        await s.flush()
        s.add(IntranetMember(tenant_id=t.id, full_name="Bo Colleague", email=colleague.email,
                             role_id=roles["member"], status="Active", auth_source="Manual",
                             user_id=colleague.id))
        s.add(IntranetUserState(tenant_id=t.id, user_id=colleague.id, scope="training",
                                state_key="global", value={"done": {"lesson-1": True}}))
        fub_agent = Agent(tenant_id=t.id, business_id=b.id, source="fub", external_id="7",
                          name="Ada in FUB", email=f"ada@{slug}.test", is_active=True)
        s.add(fub_agent)
        await s.flush()
        s.add(Lead(tenant_id=t.id, business_id=b.id, source="fub", external_id="501",
                   stage="Lead", agent_id=fub_agent.id, name="Pat Newlead", origin="Zillow",
                   contacted=False, src_created_at=now - dt.timedelta(hours=2),
                   created_at_src=now.date(), last_activity_at=now - dt.timedelta(hours=2)))
        await s.commit()
        members = {m.full_name: m.id for m in (await s.execute(select(IntranetMember).where(
            IntranetMember.tenant_id == t.id))).scalars()}
        return {"tid": t.id, "host": f"{slug}.brokerage.test", "slug": slug, "members": members,
                "owner_id": owner.id, "colleague_id": colleague.id}


async def _open_support(tok, slug, reason="checking what an agent sees"):
    async with _client() as c:
        r = await c.post(f"/api/v1/platform/tenants/{slug}/support-access", headers=_H(tok),
                         json={"reason": reason, "minutes": 30})
    assert r.status_code == 201, r.text
    return r.json()


async def _view(tok, slug, member_id):
    async with _client() as c:
        return await c.post(f"/api/v1/platform/tenants/{slug}/support-access/view-as",
                            headers=_H(tok), json={"member_id": str(member_id)})


def _token(url):
    assert "/intranet/#view-as=" in url, url
    return url.split("#view-as=", 1)[1]


# ─────────────────────────────────────────────────────────────────────────────────────────────

async def test_a_view_is_only_opened_inside_a_support_session(monkeypatch):
    _capture_mail(monkeypatch)
    ws = await _workspace("viewneeds")
    tok = await _op_token()
    before = await _view(tok, ws["slug"], ws["members"]["Ada Agent"])
    assert before.status_code == 409 and "support access" in before.text.lower()
    await _open_support(tok, ws["slug"])
    after = await _view(tok, ws["slug"], ws["members"]["Ada Agent"])
    assert after.status_code == 200, after.text
    assert after.json()["member"] == "Ada Agent"
    _token(after.json()["url"])


async def test_the_portal_reads_as_the_member_and_they_are_told_nothing(monkeypatch):
    sent = _capture_mail(monkeypatch)
    ws = await _workspace("viewreads")
    tok = await _op_token()
    await _open_support(tok, ws["slug"])
    view = _token((await _view(tok, ws["slug"], ws["members"]["Ada Agent"])).json()["url"])
    h = _H(view, ws["host"])
    async with _client() as c:
        me = (await c.get("/api/v1/me", headers=h)).json()
        config = (await c.get("/api/v1/intranet/config", headers=h)).json()
        follow = (await c.get("/api/v1/intranet/follow-ups", headers=h)).json()

    assert me["name"] == "Ada Agent" and me["email"] == f"ada@{ws['slug']}.test"
    assert me["view_as"]["name"] == "Ada Agent"
    assert "(Axcion support)" in me["view_as"]["by"]
    assert {a["id"] for a in me["apps"]} == {"intranet"}, "an agent is not offered the dashboard"

    content = config["config"]["content"]
    assert content["on_roster"] is True and content["my_role"] == "member"
    assert config["can_configure"] is False
    sun = config["config"]["sunburst"]
    assert sun["view_as"] is True and sun["url"] == "" and sun["ask_url"] == "", \
        "a view of somebody's portal must not open their coaching conversation"

    assert follow["own"]["agent"]["name"] == "Ada in FUB"
    assert [i["person"]["name"] for i in follow["own"]["items"]] == ["Pat Newlead"]

    # The owners were told a support session opened. Ada was told nothing.
    assert all(f"ada@{ws['slug']}.test" not in m["to"] for m in sent)


async def test_a_member_with_an_account_shows_their_own_progress(monkeypatch):
    _capture_mail(monkeypatch)
    ws = await _workspace("viewstate")
    tok = await _op_token()
    await _open_support(tok, ws["slug"])
    view = _token((await _view(tok, ws["slug"], ws["members"]["Bo Colleague"])).json()["url"])
    async with _client() as c:
        r = await c.get("/api/v1/intranet/state/training", headers=_H(view, ws["host"]))
    assert r.status_code == 200, r.text
    assert r.json()["value"] == {"done": {"lesson-1": True}}


async def test_the_view_cannot_write_or_leave_the_portal(monkeypatch):
    _capture_mail(monkeypatch)
    ws = await _workspace("viewbounds")
    tok = await _op_token()
    await _open_support(tok, ws["slug"])
    view = _token((await _view(tok, ws["slug"], ws["members"]["Ada Agent"])).json()["url"])
    h = _H(view, ws["host"])
    async with _client() as c:
        write = await c.put("/api/v1/intranet/state/wtd", headers=h,
                            json={"state_key": "2026-09-18", "value": {"checked": {"x": True}}})
        ask = await c.post("/api/v1/intranet/ask", headers=h, json={"question": "hello?"})
        dashboard = await c.get("/api/v1/dashboard?period=mtd", headers=h)
        console = await c.get("/api/console/members", headers=h)
        users = await c.get("/api/v1/users", headers=h)
    assert write.status_code == 403 and ask.status_code == 403
    assert dashboard.status_code == 403 and "only opens the portal" in dashboard.text
    assert console.status_code == 403 and users.status_code == 403
    async with SessionLocal() as s:
        wrote = (await s.execute(select(IntranetUserState).where(
            IntranetUserState.tenant_id == ws["tid"], IntranetUserState.scope == "wtd"))).first()
    assert wrote is None


async def test_the_view_ends_with_the_support_session(monkeypatch):
    _capture_mail(monkeypatch)
    ws = await _workspace("viewends")
    tok = await _op_token()
    await _open_support(tok, ws["slug"])
    view = _token((await _view(tok, ws["slug"], ws["members"]["Ada Agent"])).json()["url"])
    async with _client() as c:
        assert (await c.get("/api/v1/me", headers=_H(view, ws["host"]))).status_code == 200
        ended = await c.delete(f"/api/v1/platform/tenants/{ws['slug']}/support-access",
                               headers=_H(tok))
        assert ended.status_code == 200
        after = await c.get("/api/v1/me", headers=_H(view, ws["host"]))
    assert after.status_code == 401


async def test_only_a_support_account_can_carry_a_view():
    """A view is minted inside support access, so a token naming a member for anybody else --
    here the owner's own account -- is not a session at all."""
    ws = await _workspace("viewforged")
    forged = make_view_token(ws["owner_id"], ws["tid"], 0, ws["members"]["Ada Agent"],
                             dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10))
    async with _client() as c:
        r = await c.get("/api/v1/intranet/config", headers=_H(forged, ws["host"]))
    assert r.status_code == 401


async def test_a_removed_or_foreign_member_cannot_be_viewed(monkeypatch):
    _capture_mail(monkeypatch)
    ws = await _workspace("viewwho")
    other = await _workspace("viewother")
    tok = await _op_token()
    await _open_support(tok, ws["slug"])
    async with SessionLocal() as s:
        m = await s.get(IntranetMember, ws["members"]["Bo Colleague"])
        m.status = "Removed"
        await s.commit()
    assert (await _view(tok, ws["slug"], ws["members"]["Bo Colleague"])).status_code == 409
    assert (await _view(tok, ws["slug"], other["members"]["Ada Agent"])).status_code == 404
    assert (await _view(tok, ws["slug"], "not-a-uuid")).status_code == 404


async def test_a_view_is_written_to_both_trails(monkeypatch):
    _capture_mail(monkeypatch)
    ws = await _workspace("viewtrail")
    tok = await _op_token()
    await _open_support(tok, ws["slug"])
    assert (await _view(tok, ws["slug"], ws["members"]["Ada Agent"])).status_code == 200
    async with SessionLocal() as s:
        mine = (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == ws["tid"], AuditLog.action == "support.viewed_as"))).scalars().all()
        ours = (await s.execute(select(PlatformAudit).where(
            PlatformAudit.tenant_id == ws["tid"],
            PlatformAudit.action == "support.viewed_as"))).scalars().all()
    assert len(mine) == 1 and (mine[0].detail or {}).get("member") == "Ada Agent"
    assert len(ours) == 1
    async with _client() as c:
        history = (await c.get(f"/api/v1/platform/tenants/{ws['slug']}/support-access",
                               headers=_H(tok))).json()["history"]
    assert any(h["action"] == "support.viewed_as" and h["member"] == "Ada Agent" for h in history)


async def test_the_roster_it_chooses_from_is_people_not_numbers(monkeypatch):
    _capture_mail(monkeypatch)
    ws = await _workspace("viewroster")
    tok = await _op_token()
    async with _client() as c:
        r = await c.get(f"/api/v1/platform/tenants/{ws['slug']}/support-access/roster",
                        headers=_H(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["portal"] is True
    assert {m["name"] for m in body["members"]} >= {"Ada Agent", "Bo Colleague"}
    for m in body["members"]:
        assert set(m) == {"id", "name", "email", "role", "status"}, m
