"""Google sign-in: per-workspace credentials, and a door that only opens for invited people.

The two properties worth guarding here are both about what this must NOT do.

MATCHING, NEVER PROVISIONING. A verified Google address at an allowed domain is not an
invitation. If a successful sign-in could create a user, then "who is in this workspace" would be
controlled by whoever administers that email domain rather than by the workspace's own admin --
and domain membership changes without us being told.

CREDENTIALS ARE PER WORKSPACE. Not one Acumyn Google app in env: each team registers their own,
so their staff see their own name on the consent screen and one revoked app cannot sign out
every customer at once.
"""
import datetime as dt
import uuid

import pytest
from httpx import AsyncClient, ASGITransport
from jose import jwt
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import IntranetIntegration, Tenant, User
from app.security import enc, make_capability, read_token
from app.seed import seed
from app.services import google_auth

TRANSPORT = ASGITransport(app=app)
CLIENT_ID = "1234.apps.googleusercontent.com"


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _id_token(**over) -> str:
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID,
              "email": "agent@utahlife.com", "email_verified": True, "name": "A Agent",
              "exp": int((dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).timestamp())}
    claims.update(over)
    # Signed with a throwaway key on purpose: read_id_token does not check the signature, and
    # its docstring explains why (provenance is TLS from Google's own token endpoint). If that
    # ever changes, this starts failing and the reasoning gets revisited rather than bypassed.
    return jwt.encode(claims, "irrelevant", algorithm="HS256")


async def _tenant_id() -> uuid.UUID:
    async with SessionLocal() as s:
        return (await s.execute(select(Tenant.id).where(Tenant.slug == "springb"))).scalar_one()


async def _configure(*, enabled=True, domains=None, secret="shh"):
    tid = await _tenant_id()
    async with SessionLocal() as s:
        row = (await s.execute(select(IntranetIntegration).where(
            IntranetIntegration.tenant_id == tid,
            IntranetIntegration.provider_key == google_auth.PROVIDER_KEY))).scalar_one_or_none()
        if row is None:
            row = IntranetIntegration(tenant_id=tid, provider_key=google_auth.PROVIDER_KEY,
                                      display_name="Google Workspace", role_label="Sign-in",
                                      status="Not Connected", config={})
            s.add(row)
        row.config = {"client_id": CLIENT_ID, "enabled": enabled,
                      "allowed_domains": domains or []}
        row.credential_ref = enc(secret) if secret else None
        await s.commit()
    return tid


# -- the claim checks, which is where a forged or misdirected token is caught ---------------
def test_an_id_token_for_a_different_google_app_is_refused():
    """The confused-deputy case: a token Google really did mint, for somebody else's client."""
    with pytest.raises(google_auth.GoogleAuthError, match="audience"):
        google_auth.read_id_token(_id_token(aud="someone-elses-client"), CLIENT_ID)


def test_an_unverified_address_is_refused():
    """An unverified address is one somebody typed, not one they proved they hold -- accepting
    it would let any Google account claim any invited address."""
    with pytest.raises(google_auth.GoogleAuthError, match="verified"):
        google_auth.read_id_token(_id_token(email_verified=False), CLIENT_ID)


def test_a_stale_or_foreign_token_is_refused():
    past = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)).timestamp())
    with pytest.raises(google_auth.GoogleAuthError, match="expired"):
        google_auth.read_id_token(_id_token(exp=past), CLIENT_ID)
    with pytest.raises(google_auth.GoogleAuthError, match="issuer"):
        google_auth.read_id_token(_id_token(iss="https://evil.example"), CLIENT_ID)


def test_the_domain_restriction_prefers_googles_own_assertion():
    allowed = ["utahlife.com"]
    assert google_auth.domain_allowed({"email": "a@utahlife.com"}, allowed)
    assert google_auth.domain_allowed({"email": "a@x.com", "hd": "utahlife.com"}, allowed)
    assert not google_auth.domain_allowed({"email": "a@utahlife.com.evil.com"}, allowed)
    assert not google_auth.domain_allowed({"email": "a@x.com"}, allowed)
    # No list means no restriction, which is what a team on ordinary gmail accounts has.
    assert google_auth.domain_allowed({"email": "a@anything.com"}, [])


# -- the state token, and the trap QuickBooks fell into ------------------------------------
async def test_the_oauth_state_is_not_usable_as_a_session_token():
    """This string goes to Google, sits in their logs and comes back in a URL, so it must not be
    a credential here.

    Asserted against the API rather than against read_token: read_token is a bare jwt.decode and
    happily decodes a capability, which is exactly how the QuickBooks state stayed a working
    session token for so long. The guard that matters is in current_user -- see
    test_auth_hardening.test_an_oauth_state_cannot_be_used_as_a_session_token."""
    tid = await _tenant_id()
    state = make_capability("google_signin", minutes=15, tid=str(tid))
    assert read_token(state)["cap"] == "google_signin", "decodes -- which is the point"
    async with _client() as c:
        r = await c.get("/api/v1/me", headers={"Authorization": f"Bearer {state}"})
    assert r.status_code == 401


# -- the endpoints -------------------------------------------------------------------------
async def test_a_workspace_that_has_not_configured_google_offers_no_button():
    await _configure(enabled=False)
    async with _client() as c:
        r = await c.get("/api/v1/auth/google/config")
        assert r.status_code == 200 and r.json() == {"enabled": False}
        # And starting the flow is not merely hidden in the UI -- it is refused.
        assert (await c.get("/api/v1/auth/google/start")).status_code == 404


async def test_start_hands_back_googles_url_carrying_this_workspaces_client_id():
    await _configure(domains=["utahlife.com"])
    async with _client() as c:
        assert (await c.get("/api/v1/auth/google/config")).json() == {"enabled": True}
        r = await c.get("/api/v1/auth/google/start")
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert CLIENT_ID in url and "prompt=select_account" in url


async def _callback(monkeypatch, *, email, tid):
    async def fake_exchange(client_id, client_secret, code, redirect_uri):
        assert client_secret == "shh", "the workspace's own secret must reach Google"
        return {"id_token": _id_token(email=email)}
    monkeypatch.setattr(google_auth, "exchange_code", fake_exchange)
    state = make_capability("google_signin", minutes=15, tid=str(tid))
    async with _client() as c:
        return await c.get(f"/api/v1/auth/google/callback?code=x&state={state}",
                           follow_redirects=False)


async def test_a_verified_google_account_with_no_invitation_is_refused(monkeypatch):
    """The property that matters most: sign-in matches, it never provisions."""
    tid = await _configure(domains=["utahlife.com"])
    r = await _callback(monkeypatch, email="stranger@utahlife.com", tid=tid)
    assert r.status_code in (302, 307)
    assert "google_error=no_account" in r.headers["location"]
    async with SessionLocal() as s:
        assert (await s.execute(select(User).where(
            User.tenant_id == tid,
            User.email == "stranger@utahlife.com"))).scalar_one_or_none() is None, \
            "sign-in created a user"


async def test_an_address_outside_the_allowed_domains_is_refused(monkeypatch):
    tid = await _configure(domains=["utahlife.com"])
    async with SessionLocal() as s:
        s.add(User(tenant_id=tid, email="outsider@elsewhere.com", name="O",
                   password_hash=None, role="member", status="active", token_version=0))
        await s.commit()
    r = await _callback(monkeypatch, email="outsider@elsewhere.com", tid=tid)
    assert "google_error=domain" in r.headers["location"]


async def test_an_invited_member_is_signed_in_and_their_invite_burned(monkeypatch):
    """Proving control of the invited address IS accepting the invite -- and the emailed link
    must not survive it, or it could be replayed later."""
    tid = await _configure(domains=["utahlife.com"])
    async with SessionLocal() as s:
        s.add(User(tenant_id=tid, email="invited@utahlife.com", name="", password_hash=None,
                   role="member", status="invited", token_version=0,
                   action_token_hash="deadbeef", action_token_purpose="invite",
                   action_token_expires=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7)))
        await s.commit()
    r = await _callback(monkeypatch, email="invited@utahlife.com", tid=tid)
    assert "#google_token=" in r.headers["location"], r.headers["location"]
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(
            User.tenant_id == tid, User.email == "invited@utahlife.com"))).scalar_one()
        assert u.status == "active"
        assert u.action_token_hash is None, "the emailed invite link is still live"
        assert u.name == "A Agent", "the name Google supplied was not taken"


async def test_a_disabled_account_cannot_come_back_in_through_google(monkeypatch):
    tid = await _configure(domains=["utahlife.com"])
    async with SessionLocal() as s:
        s.add(User(tenant_id=tid, email="gone@utahlife.com", name="G", password_hash=None,
                   role="member", status="disabled", token_version=1))
        await s.commit()
    r = await _callback(monkeypatch, email="gone@utahlife.com", tid=tid)
    assert "google_error=no_account" in r.headers["location"]


async def test_the_session_comes_back_in_the_fragment_not_the_query(monkeypatch):
    """A query string lands in access logs and Referer headers; a fragment is never sent to a
    server at all."""
    tid = await _configure(domains=["utahlife.com"])
    async with SessionLocal() as s:
        s.add(User(tenant_id=tid, email="ok@utahlife.com", name="OK", password_hash=None,
                   role="member", status="active", token_version=0))
        await s.commit()
    r = await _callback(monkeypatch, email="ok@utahlife.com", tid=tid)
    location = r.headers["location"]
    assert "#google_token=" in location
    assert "?google_token=" not in location and "&google_token=" not in location
