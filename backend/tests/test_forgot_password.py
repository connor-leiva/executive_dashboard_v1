"""Self-service password reset — the public endpoint behind "Forgot password?".

This is the only write endpoint in the product that an unauthenticated stranger can reach with
an arbitrary email address, so nearly every test here is about what it must NOT reveal or allow.

The single property everything else hangs off: THE RESPONSE NEVER VARIES. Same status, same
body, whether the address is real, disabled, invited or invented. A sign-in page is public, and
these are work addresses at a named brokerage — an endpoint that answers differently for a real
one hands over the customer list a guess at a time.
"""
import datetime as dt

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import AuditLog, Tenant, User
from app.seed import seed
from app.security import hash_pw
from app.services import mailer

TRANSPORT = ASGITransport(app=app)
OK = {"ok": True}


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
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
    monkeypatch.setattr(settings, "MAIL_FROM", "Acumyn <hello@mail.acumyn.io>")
    monkeypatch.setattr(settings, "MAIL_REPLY_TO", "")
    return calls


async def _tenant_id(s):
    return (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id


async def _mk(email, *, status="active", pw="password123", tabs=None):
    """A user in whatever state the test is about."""
    async with SessionLocal() as s:
        tid = await _tenant_id(s)
        u = User(tenant_id=tid, email=email.lower(), name=email.split("@")[0],
                 password_hash=(hash_pw(pw) if pw else None), role="member", status=status,
                 tab_access=(tabs or ["ulrg"]), token_version=0)
        s.add(u)
        await s.commit()
        return u.id


async def _forgot(c, email):
    return await c.post("/api/v1/auth/forgot-password", json={"email": email})


async def _reload(uid):
    async with SessionLocal() as s:
        return (await s.execute(select(User).where(User.id == uid))).scalar_one()


# ── the property the whole endpoint exists to hold ──────────────────
async def test_the_answer_is_identical_for_a_real_and_an_invented_address(monkeypatch):
    """The enumeration guard. If these two ever diverge — status, body, or shape — the sign-in
    page becomes a way to test whether somebody works at this brokerage."""
    _capture(monkeypatch)
    await _mk("real.person@x.com")
    async with _client() as c:
        real = await _forgot(c, "real.person@x.com")
        fake = await _forgot(c, "nobody.at.all@x.com")
    assert real.status_code == fake.status_code == 200
    assert real.json() == fake.json() == OK


async def test_a_disabled_account_gets_the_same_answer_and_no_email(monkeypatch):
    """An administrator turned this account off. A public form must not be able to hand it a way
    back in, and must not admit that it exists either."""
    calls = _capture(monkeypatch)
    await _mk("disabled@x.com", status="disabled")
    async with _client() as c:
        r = await _forgot(c, "disabled@x.com")
    assert r.status_code == 200 and r.json() == OK
    assert calls == [], "a disabled account was sent a way back in"


async def test_an_invented_address_sends_nothing(monkeypatch):
    calls = _capture(monkeypatch)
    async with _client() as c:
        assert (await _forgot(c, "ghost@x.com")).status_code == 200
    assert calls == []


# ── what actually happens behind the identical answer ───────────────
async def test_an_active_user_is_emailed_a_working_reset_link(monkeypatch):
    calls = _capture(monkeypatch)
    uid = await _mk("resetme@x.com")
    async with _client() as c:
        assert (await _forgot(c, "resetme@x.com")).status_code == 200
        assert len(calls) == 1
        body = calls[0]["text"]
        assert "reset-password?token=" in body
        token = body.split("reset-password?token=")[1].split()[0]
        # End to end: the emailed link is one the reset endpoint accepts.
        done = await c.post("/api/v1/auth/reset-password",
                            json={"token": token, "new_password": "a-new-password-1"})
    assert done.status_code == 200 and done.json()["token"]
    assert (await _reload(uid)).status == "active"


async def test_an_invited_user_gets_their_invite_again_not_a_reset(monkeypatch):
    """They have no password to reset. A reset link would work — it sets one — but it skips the
    name the invite screen asks for, and it means two links doing one job."""
    calls = _capture(monkeypatch)
    uid = await _mk("neveraccepted@x.com", status="invited", pw=None)
    async with _client() as c:
        assert (await _forgot(c, "neveraccepted@x.com")).status_code == 200
    assert len(calls) == 1
    assert "accept-invite?token=" in calls[0]["text"]
    assert (await _reload(uid)).action_token_purpose == "invite"


async def test_the_email_names_the_workspace(monkeypatch):
    calls = _capture(monkeypatch)
    await _mk("named@x.com")
    async with _client() as c:
        await _forgot(c, "named@x.com")
    async with SessionLocal() as s:
        name = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().name
    assert name in calls[0]["text"]


async def test_the_address_is_matched_case_insensitively(monkeypatch):
    calls = _capture(monkeypatch)
    await _mk("mixedcase@x.com")
    async with _client() as c:
        await _forgot(c, "  MixedCase@X.com  ")
    assert len(calls) == 1, "a capitalised address found nobody"


# ── throttling ──────────────────────────────────────────────────────
async def test_a_second_request_within_the_window_sends_nothing(monkeypatch):
    """Anybody who knows an address can ask for a reset on it — that is what makes the feature
    work. Without a throttle it is also a button for flooding somebody's inbox."""
    calls = _capture(monkeypatch)
    await _mk("throttled@x.com")
    async with _client() as c:
        first = await _forgot(c, "throttled@x.com")
        second = await _forgot(c, "throttled@x.com")
    assert first.json() == second.json() == OK, "the throttle must not be visible in the answer"
    assert len(calls) == 1


async def test_the_window_expires_so_a_genuine_resend_works(monkeypatch):
    """The Resend button on the check-your-email screen has to actually resend."""
    calls = _capture(monkeypatch)
    uid = await _mk("resendable@x.com")
    async with _client() as c:
        await _forgot(c, "resendable@x.com")
        async with SessionLocal() as s:                      # age the token past the window
            u = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            u.action_token_expires = u.action_token_expires - dt.timedelta(minutes=5)
            await s.commit()
        await _forgot(c, "resendable@x.com")
    assert len(calls) == 2


async def test_a_new_link_replaces_the_previous_one(monkeypatch):
    """Two live reset links for one account is one more than anybody needs."""
    calls = _capture(monkeypatch)
    uid = await _mk("replaced@x.com")
    async with _client() as c:
        await _forgot(c, "replaced@x.com")
        first = calls[0]["text"].split("reset-password?token=")[1].split()[0]
        async with SessionLocal() as s:
            u = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            u.action_token_expires = u.action_token_expires - dt.timedelta(minutes=5)
            await s.commit()
        await _forgot(c, "replaced@x.com")
        dead = await c.post("/api/v1/auth/reset-password",
                            json={"token": first, "new_password": "another-password-1"})
    assert dead.status_code == 400


# ── the workspace ───────────────────────────────────────────────────
async def test_a_suspended_workspace_sends_nothing(monkeypatch):
    """Same reasoning as login: nobody in a suspended workspace can get in, so nothing should
    offer them a route."""
    calls = _capture(monkeypatch)
    await _mk("suspended.user@x.com")
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        t.status = "suspended"
        await s.commit()
    try:
        async with _client() as c:
            r = await _forgot(c, "suspended.user@x.com")
        assert r.status_code == 200 and r.json() == OK
        assert calls == []
    finally:
        async with SessionLocal() as s:
            t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
            t.status = "active"
            await s.commit()


# ── the trail ───────────────────────────────────────────────────────
async def test_both_outcomes_leave_an_audit_row(monkeypatch):
    """A spray against this endpoint leaves no other trace — there is no failed-login counter to
    tick and no session to notice. Writing a row on BOTH paths also keeps the work done inside
    the request comparable, so the response time says less about whether the address was real."""
    _capture(monkeypatch)
    await _mk("audited@x.com")
    async with _client() as c:
        await _forgot(c, "audited@x.com")
        await _forgot(c, "not.a.user@x.com")
    async with SessionLocal() as s:
        rows = (await s.execute(select(AuditLog).where(
            AuditLog.action == "auth.forgot_password"))).scalars().all()
    results = [(r.detail or {}).get("result") for r in rows]
    assert "sent" in results and "no_such_login" in results


async def test_no_key_configured_still_answers_normally(monkeypatch):
    """The mailer is inert without a key and must stay inert rather than raising — the token is
    already committed by the time the send is attempted."""
    calls = _capture(monkeypatch)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    await _mk("nokeyreset@x.com")
    async with _client() as c:
        r = await _forgot(c, "nokeyreset@x.com")
    assert r.status_code == 200 and r.json() == OK
    assert calls == []
