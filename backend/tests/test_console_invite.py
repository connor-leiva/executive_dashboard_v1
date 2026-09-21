"""Inviting somebody to the portal has to give them a way in.

LIVE GAP, found in an external repo audit and confirmed against the source: POST
/console/members/invite created an IntranetMember row and stopped. The invited person appeared
in the directory, counted on the overview and could be assigned work -- with no account, no
invite link and no email. There was no way for them to sign in, ever. The roster was decorative,
and every workspace had exactly one usable login: whoever provisioned it.

Two things this must get right beyond "create a user":

  * A portal member carries NO dashboard tabs. They are a buyer agent, not an executive, and the
    dashboard's numbers are not theirs. `_tenant_apps` then leaves the Dashboard out entirely so
    they are not handed an app whose every screen is empty.
  * Somebody who already has a dashboard account is LINKED, not refused. An account and a roster
    entry are different things, and an owner adding themselves to their own roster should not be
    told they already exist.
"""
import uuid

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Domain, IntranetMember, IntranetRole, Tenant, User
from app.security import hash_pw
from app.services import mailer
from app.seed import seed
from app.services.intranet_bootstrap import bootstrap_intranet
from app.services.provisioning import provision_tenant

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    # Creates the tables; this module provisions its own tenant rather than using the seeded one.
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _capture(monkeypatch):
    calls = []

    async def fake_post(payload, headers):
        calls.append(payload)
        return 200, "{}"
    monkeypatch.setattr(mailer, "_post", fake_post)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(settings, "MAIL_FROM", "Axcion <hello@mail.axcion.io>")
    monkeypatch.setattr(settings, "MAIL_REPLY_TO", "")
    return calls


@pytest.fixture(scope="module")
async def ws():
    """A provisioned workspace whose owner can open the console."""
    slug = f"inviteco{uuid.uuid4().hex[:6]}"
    async with SessionLocal() as s:
        result = await provision_tenant(
            s, slug=slug, name="Invite Co", owner_email=f"owner@{slug}.test",
            hostname=f"{slug}.internal", plan="portfolio", seed_catalogs=False)
        await s.commit()
        tid = result.tenant_id if hasattr(result, "tenant_id") else result.tenant.id

    async with SessionLocal() as s:
        tenant = await s.get(Tenant, tid)
        await bootstrap_intranet(s, tid, workspace_name="Invite Co", subdomain=slug,
                                 owner_email=f"owner@{slug}.test")
        owner = (await s.execute(select(User).where(
            User.tenant_id == tid, User.role == "owner"))).scalars().first()
        owner.password_hash = hash_pw("password12345")
        owner.status = "active"
        member = (await s.execute(select(IntranetMember).where(
            IntranetMember.tenant_id == tid))).scalars().first()
        if member is not None and member.user_id is None:
            member.user_id = owner.id
        host = (await s.execute(select(Domain.hostname).where(
            Domain.tenant_id == tid))).scalars().first()
        roles = {r.key: r.id for r in (await s.execute(select(IntranetRole).where(
            IntranetRole.tenant_id == tid))).scalars().all()}
        await s.commit()
        owner_email = owner.email

    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": owner_email, "password": "password12345"},
                         headers={"x-tenant-host": host})
        assert r.status_code == 200, r.text
    return {"tid": tid, "host": host, "token": r.json()["token"],
            "roles": roles, "owner_email": owner_email}


def _H(ws):
    return {"Authorization": f"Bearer {ws['token']}", "x-tenant-host": ws["host"]}


async def _invite(ws, email, name="New Agent"):
    role_id = str(next(iter(ws["roles"].values())))
    async with _client() as c:
        return await c.post("/api/console/members/invite", headers=_H(ws),
                            json={"full_name": name, "email": email, "role_id": role_id})


async def _send(ws, member_id):
    async with _client() as c:
        return await c.post(f"/api/console/members/{member_id}/send-invite", headers=_H(ws))


async def test_adding_somebody_makes_their_account_and_sends_nothing(ws, monkeypatch):
    """Add, then Send invite. The account exists from the moment they are added -- the roster,
    the seats and CRM matching all see a real person -- and nothing reaches them until an admin
    chooses: the account is HELD, with no invite link issued at all."""
    calls = _capture(monkeypatch)
    r = await _invite(ws, "held@inviteco.test", name="Held Agent")
    assert r.status_code == 200, r.text
    assert r.json()["invite_url"] is None
    assert r.json()["item"]["invite"] == "not_sent"
    assert not calls, "adding somebody emailed them"
    async with SessionLocal() as s:
        account = (await s.execute(select(User).where(
            User.tenant_id == ws["tid"], User.email == "held@inviteco.test"))).scalar_one()
        member = (await s.execute(select(IntranetMember).where(
            IntranetMember.tenant_id == ws["tid"],
            IntranetMember.email == "held@inviteco.test"))).scalar_one()
    assert account.status == "invited" and account.invite_held is True
    assert account.action_token_hash is None, "a link exists before anybody sent one"
    assert account.role == "member" and account.tab_access == []
    assert member.user_id == account.id and member.invited_at is None

    listed = None
    async with _client() as c:
        items = (await c.get("/api/console/members?filter=everyone", headers=_H(ws))).json()["items"]
        listed = next(i for i in items if i["email"] == "held@inviteco.test")
    assert listed["invite"] == "not_sent"


async def test_an_invited_member_gets_an_account_and_a_working_link(ws, monkeypatch):
    calls = _capture(monkeypatch)
    r = await _invite(ws, "agent@inviteco.test")
    assert r.status_code == 200, r.text
    assert not calls
    sent = await _send(ws, r.json()["item"]["id"])
    assert sent.status_code == 200, sent.text
    body = sent.json()
    url = body["invite_url"]
    assert url and "/accept-invite?token=" in url
    assert body["item"]["invite"] == "sent"

    async with SessionLocal() as s:
        account = (await s.execute(select(User).where(
            User.tenant_id == ws["tid"], User.email == "agent@inviteco.test"))).scalar_one()
        member = (await s.execute(select(IntranetMember).where(
            IntranetMember.tenant_id == ws["tid"],
            IntranetMember.email == "agent@inviteco.test"))).scalar_one()
    assert account.status == "invited" and account.invite_held is False
    assert account.password_hash is None
    assert account.action_token_hash, "no invite token was issued"
    assert account.role == "member"
    # Empty, not null: null would mean an owner with everything.
    assert account.tab_access == [], account.tab_access
    assert member.user_id == account.id, "the roster entry is not linked to the account"
    assert member.invited_at is not None

    # The email actually went, and carries the same link.
    assert calls, "no invite email was sent"
    assert "accept-invite?token=" in calls[0]["html"] + calls[0].get("text", "")

    # ...and the link works: this is the whole point.
    token = url.split("token=", 1)[1]
    async with _client() as c:
        accepted = await c.post("/api/v1/auth/accept-invite",
                                headers={"x-tenant-host": ws["host"]},
                                json={"token": token, "name": "New Agent",
                                      "password": "password12345"})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["token"]


async def test_a_portal_member_sees_the_portal_and_not_the_dashboard(ws, monkeypatch):
    _capture(monkeypatch)
    added = await _invite(ws, "noexec@inviteco.test", name="No Exec")
    await _send(ws, added.json()["item"]["id"])
    async with SessionLocal() as s:
        account = (await s.execute(select(User).where(
            User.tenant_id == ws["tid"], User.email == "noexec@inviteco.test"))).scalar_one()
        account.status = "active"
        account.password_hash = hash_pw("password12345")
        await s.commit()

    async with _client() as c:
        login = await c.post("/api/v1/auth/login",
                             headers={"x-tenant-host": ws["host"]},
                             json={"email": "noexec@inviteco.test",
                                   "password": "password12345"})
        assert login.status_code == 200, login.text
        me = await c.get("/api/v1/me",
                         headers={"Authorization": f"Bearer {login.json()['token']}",
                                  "x-tenant-host": ws["host"]})
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["tabs"] == [], body["tabs"]
    ids = {a["id"] for a in body["apps"]}
    assert ids == {"intranet"}, (
        f"a portal member was offered {ids} -- a dashboard with no tabs is an empty shell")


async def test_somebody_who_already_has_an_account_is_linked_not_refused(ws, monkeypatch):
    """An account and a roster entry are different things.

    A dashboard user added to the portal roster should be linked to the account they already
    have -- not refused as a duplicate, and not given a second account. They can already sign in,
    so there is no invite to send either.
    """
    calls = _capture(monkeypatch)
    async with SessionLocal() as s:
        s.add(User(tenant_id=ws["tid"], email="existing@inviteco.test", name="Already Here",
                   password_hash=hash_pw("password12345"), role="member", status="active",
                   tab_access=["portfolio"], token_version=0))
        await s.commit()
        before = len((await s.execute(select(User).where(
            User.tenant_id == ws["tid"]))).scalars().all())

    r = await _invite(ws, "existing@inviteco.test", name="Already Here")
    assert r.status_code == 200, r.text
    # No invite link and no email: they already have a way in.
    assert r.json()["invite_url"] is None
    assert not calls, "an invite was emailed to somebody who already has an account"

    async with SessionLocal() as s:
        accounts = (await s.execute(select(User).where(
            User.tenant_id == ws["tid"], User.email == "existing@inviteco.test"))).scalars().all()
        member = (await s.execute(select(IntranetMember).where(
            IntranetMember.tenant_id == ws["tid"],
            IntranetMember.email == "existing@inviteco.test"))).scalar_one()
        after = len((await s.execute(select(User).where(
            User.tenant_id == ws["tid"]))).scalars().all())
    assert len(accounts) == 1, "a second account was created for the same address"
    assert after == before, "the roster entry created an extra account"
    assert member.user_id == accounts[0].id
    # Their existing dashboard access is untouched -- being added to a roster is not a demotion.
    assert accounts[0].tab_access == ["portfolio"]
    assert accounts[0].status == "active"


async def test_inviting_the_same_person_twice_is_refused(ws, monkeypatch):
    _capture(monkeypatch)
    assert (await _invite(ws, "twice@inviteco.test")).status_code == 200
    again = await _invite(ws, "twice@inviteco.test")
    assert again.status_code == 422


# ── the held invite, and sending it ────────────────────────────────────────────────────────

async def test_a_held_invite_does_not_answer_forgot_password(ws, monkeypatch):
    """For an invited account, "forgot password" emails the INVITE. Held, it sends nothing --
    that email would be how they found out -- and answers exactly as for an unknown address."""
    calls = _capture(monkeypatch)
    await _invite(ws, "quiet@inviteco.test", name="Quiet Agent")
    async with _client() as c:
        r = await c.post("/api/v1/auth/forgot-password", headers={"x-tenant-host": ws["host"]},
                         json={"email": "quiet@inviteco.test"})
        control = await c.post("/api/v1/auth/forgot-password",
                               headers={"x-tenant-host": ws["host"]},
                               json={"email": "nobody-at-all@inviteco.test"})
    assert r.status_code == control.status_code == 200 and r.json() == control.json()
    assert not calls, "a held account was emailed"


async def test_sending_again_issues_a_new_link_and_retires_the_old_one(ws, monkeypatch):
    calls = _capture(monkeypatch)
    member_id = (await _invite(ws, "again@inviteco.test", name="Again")).json()["item"]["id"]
    first = (await _send(ws, member_id)).json()["invite_url"]
    second = (await _send(ws, member_id)).json()["invite_url"]
    assert first != second and len(calls) == 2
    async with _client() as c:
        stale = await c.post("/api/v1/auth/accept-invite", headers={"x-tenant-host": ws["host"]},
                             json={"token": first.split("token=", 1)[1], "name": "Again",
                                   "password": "password12345"})
        fresh = await c.post("/api/v1/auth/accept-invite", headers={"x-tenant-host": ws["host"]},
                             json={"token": second.split("token=", 1)[1], "name": "Again",
                                   "password": "password12345"})
    assert stale.status_code == 400 and fresh.status_code == 200, (stale.text, fresh.text)
    # Accepted: there is nothing left to send.
    again = await _send(ws, member_id)
    assert again.status_code == 422 and "already has an account" in again.text


async def test_a_removed_member_is_not_sent_an_invite(ws, monkeypatch):
    calls = _capture(monkeypatch)
    member_id = (await _invite(ws, "gone@inviteco.test", name="Gone")).json()["item"]["id"]
    async with _client() as c:
        assert (await c.delete(f"/api/console/members/{member_id}", headers=_H(ws))).status_code == 200
    r = await _send(ws, member_id)
    assert r.status_code == 422 and "removed" in r.text
    assert not calls


async def test_the_dashboard_resend_releases_a_held_invite(ws, monkeypatch):
    """Any path that sends the invite releases the hold -- or Google sign-in and "forgot password"
    would go on refusing somebody who has been told to use them."""
    _capture(monkeypatch)
    await _invite(ws, "viadash@inviteco.test", name="Via Dash")
    async with SessionLocal() as s:
        account = (await s.execute(select(User).where(
            User.tenant_id == ws["tid"], User.email == "viadash@inviteco.test"))).scalar_one()
    async with _client() as c:
        r = await c.post(f"/api/v1/users/{account.id}/resend-invite", headers=_H(ws))
    assert r.status_code == 200, r.text
    async with SessionLocal() as s:
        account = await s.get(User, account.id)
    assert account.invite_held is False and account.action_token_hash
