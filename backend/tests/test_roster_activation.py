"""A roster entry becomes Active the first time its person signs in -- by any route -- and a Removed
one never does.

The account and the roster entry are different records. The account turned active on first sign-in;
the roster entry stayed "Invited" until an admin edited it by hand, so an agent using the portal
every day was missing from Who's Who, which lists Active people only, and an invited co-admin could
not open the console, which requires an Active member.
"""
import ast
import datetime as dt
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import AuditLog, Domain, IntranetMember, IntranetRole, Tenant, User
from app.security import hash_pw, make_capability, new_action_token
from app.services import google_auth

TRANSPORT = ASGITransport(app=app)
PASSWORD = "password12345"
AUTH_ROUTER = Path(__file__).resolve().parents[1] / "app" / "routers" / "auth.py"


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


async def _person(*, account="active", member="Invited", link=True, token_purpose=None):
    """A workspace holding one person: an account in state `account` and a roster entry in state
    `member`. `token_purpose` issues an invite or reset link, whose raw token is returned."""
    slug = f"ros{uuid.uuid4().hex[:8]}"
    email = f"agent@{slug}.test"
    now = dt.datetime.now(dt.timezone.utc)
    raw = None
    async with SessionLocal() as s:
        t = Tenant(slug=slug, name=slug, status="active", plan="portfolio",
                   config={"features": {"intranet": True}})
        s.add(t)
        await s.flush()
        s.add(Domain(tenant_id=t.id, hostname=f"{slug}.localhost", is_primary=True))
        u = User(tenant_id=t.id, email=email, name="Agent", role="member", status=account,
                 password_hash=hash_pw(PASSWORD) if account == "active" else None,
                 tab_access=[], token_version=0)
        if token_purpose:
            raw, hashed = new_action_token()
            u.action_token_hash, u.action_token_purpose = hashed, token_purpose
            u.action_token_expires = now + dt.timedelta(days=1)
        s.add(u)
        role = IntranetRole(tenant_id=t.id, key="agent", name="Agent", sort=1, published_at=now)
        s.add(role)
        await s.flush()
        m = IntranetMember(tenant_id=t.id, full_name="Agent", email=email, role_id=role.id,
                           status=member, auth_source="Manual", invited_at=now,
                           user_id=u.id if link else None)
        s.add(m)
        await s.commit()
        return {"tid": t.id, "uid": u.id, "rid": role.id, "mid": m.id, "email": email,
                "host": f"{slug}.localhost", "raw": raw}


async def _login(p):
    async with _client() as c:
        return await c.post("/api/v1/auth/login", headers={"x-tenant-host": p["host"]},
                            json={"email": p["email"], "password": PASSWORD})


async def _member(p):
    async with SessionLocal() as s:
        return await s.get(IntranetMember, p["mid"])


async def _activations(p):
    async with SessionLocal() as s:
        return (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == p["tid"],
            AuditLog.action == "access.member.activated"))).scalars().all()


# ── every way in ──────────────────────────────────────────────────────────────────────────

async def test_password_sign_in_makes_an_invited_entry_active():
    p = await _person(account="active", member="Invited")
    r = await _login(p)
    assert r.status_code == 200, r.text
    m = await _member(p)
    assert m.status == "Active" and m.activated_at is not None
    assert len(await _activations(p)) == 1, "the activation left no audit trail"


async def test_accepting_the_invite_makes_the_entry_active():
    p = await _person(account="invited", member="Invited", token_purpose="invite")
    async with _client() as c:
        r = await c.post("/api/v1/auth/accept-invite", headers={"x-tenant-host": p["host"]},
                         json={"token": p["raw"], "name": "Agent", "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert (await _member(p)).status == "Active"


async def test_a_password_reset_makes_the_entry_active():
    """A reset also completes an account that was mid-invite, so it can be a first sign-in too."""
    p = await _person(account="invited", member="Invited", token_purpose="reset")
    async with _client() as c:
        r = await c.post("/api/v1/auth/reset-password", headers={"x-tenant-host": p["host"]},
                         json={"token": p["raw"], "new_password": PASSWORD})
    assert r.status_code == 200, r.text
    assert (await _member(p)).status == "Active"


async def test_google_sign_in_makes_the_entry_active(monkeypatch):
    """The route a team will actually use: no emailed link and no password, just Google."""
    p = await _person(account="invited", member="Invited")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "acumyn.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "platform-secret")

    async def fake_exchange(client_id, client_secret, code, redirect_uri):
        expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
        claims = {"iss": "https://accounts.google.com", "aud": client_id, "email": p["email"],
                  "email_verified": True, "name": "Agent", "exp": int(expires.timestamp())}
        return {"id_token": jwt.encode(claims, "irrelevant", algorithm="HS256")}

    monkeypatch.setattr(google_auth, "exchange_code", fake_exchange)
    state = make_capability("google_signin", minutes=15, tid=str(p["tid"]))
    async with _client() as c:
        r = await c.get(f"/api/v1/auth/google/callback?code=x&state={state}",
                        follow_redirects=False)
    assert "#google_token=" in r.headers["location"], r.headers["location"]
    assert (await _member(p)).status == "Active"


def test_every_route_that_signs_somebody_in_activates_their_roster_entry():
    """Derived, not listed: any function in the auth router that mints a session must also run the
    activation, so a sign-in route added later cannot quietly skip it. Changing a password is the
    one exception -- that person is already signed in, so one of the others has run."""
    exempt = {"change_password"}
    tree = ast.parse(AUTH_ROUTER.read_text(encoding="utf-8"))

    def calls(fn, name):
        return any(isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name))
            for node in ast.walk(fn))

    minting = [fn for fn in ast.walk(tree)
               if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and calls(fn, "make_token")]
    assert {fn.name for fn in minting} >= {"login", "google_callback", "accept_invite",
                                           "reset_password"}, \
        "the scan no longer sees the sign-in routes -- fix the scan rather than let it pass empty"
    missing = sorted(fn.name for fn in minting
                     if fn.name not in exempt and not calls(fn, "activate_on_sign_in"))
    assert not missing, f"these routes sign somebody in without activating their entry: {missing}"


# ── what must not move ────────────────────────────────────────────────────────────────────

async def test_signing_in_never_brings_back_a_removed_member():
    """Removed is an admin's decision. The account may still exist for the dashboard, and using it
    must not put somebody back on the roster."""
    p = await _person(account="active", member="Removed")
    assert (await _login(p)).status_code == 200
    m = await _member(p)
    assert m.status == "Removed" and m.activated_at is None
    assert await _activations(p) == []


async def test_an_entry_linked_to_a_different_account_is_not_activated_by_address():
    """Somebody else's seat. An admin linked it to another account, so a matching address alone
    is not enough to claim it."""
    p = await _person(account="active", member="Invited")
    async with SessionLocal() as s:
        other = User(tenant_id=p["tid"], email=f"other-{p['email']}", name="Other",
                     role="member", status="active", password_hash=None, tab_access=[],
                     token_version=0)
        s.add(other)
        await s.flush()
        (await s.get(IntranetMember, p["mid"])).user_id = other.id
        await s.commit()
    assert (await _login(p)).status_code == 200
    assert (await _member(p)).status == "Invited"


async def test_an_entry_added_before_its_account_existed_is_found_by_address_and_linked():
    p = await _person(account="active", member="Invited", link=False)
    assert (await _login(p)).status_code == 200
    m = await _member(p)
    assert (m.status, m.user_id) == ("Active", p["uid"])


async def test_a_member_who_is_already_active_is_left_alone():
    p = await _person(account="active", member="Active")
    assert (await _login(p)).status_code == 200
    assert (await _login(p)).status_code == 200
    assert await _activations(p) == [], "an already-active member was re-activated"
