"""TOTP enrollment + the Binder step-up gate (2026-08-16).

The point of a step-up is that the section is closed until you prove it AGAIN — so these
tests care less about the happy path than about the ways in: no grant, someone else's grant,
a stale grant after a password change, the assistant side door, and lockout on guessing.
"""
import datetime as dt

import pyotp
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.db import SessionLocal
from app.models import User, AuditLog
from app.seed import seed, OWNER_EMAIL, OWNER_PASSWORD
from app.security import dec, hash_pw, make_capability


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _login(c, email=OWNER_EMAIL, password=OWNER_PASSWORD):
    r = await c.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def _enroll(c, H):
    """Run a full enrollment and return (secret, recovery_codes)."""
    start = await c.post("/api/v1/me/totp/start", headers=H)
    assert start.status_code == 200, start.text
    secret = start.json()["secret"]
    assert start.json()["otpauth_uri"].startswith("otpauth://totp/")
    code = pyotp.TOTP(secret).now()
    done = await c.post("/api/v1/me/totp/confirm", json={"code": code}, headers=H)
    assert done.status_code == 200, done.text
    return secret, done.json()["recovery_codes"]


async def _reset_totp(email=OWNER_EMAIL):
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.email == email))).scalar_one()
        u.totp_secret_enc = u.totp_confirmed_at = u.totp_recovery = u.totp_last_used = None
        u.totp_failed, u.totp_locked_until = 0, None
        await s.commit()


async def test_binder_is_locked_until_a_code_is_proven():
    await _reset_totp()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        H = {"Authorization": f"Bearer {await _login(c)}"}

        # 1) tab access alone is no longer enough — 428 tells the client to prompt
        r = await c.get("/api/v1/binder/entities", headers=H)
        assert r.status_code == 428, r.text

        # 2) …and you can't unlock before enrolling
        pre = await c.post("/api/v1/step-up/binder", json={"code": "123456"}, headers=H)
        assert pre.status_code == 409

        secret, _ = await _enroll(c, H)

        # 3) a wrong code doesn't open it
        bad = await c.post("/api/v1/step-up/binder", json={"code": "000000"}, headers=H)
        assert bad.status_code == 400

        # 4) the real code mints a grant, and Binder opens WITH the header
        ok = await c.post("/api/v1/step-up/binder",
                          json={"code": pyotp.TOTP(secret).now()}, headers=H)
        assert ok.status_code == 200, ok.text
        grant = ok.json()["token"]
        assert ok.json()["scope"] == "binder" and ok.json()["expires_in"] > 0

        opened = await c.get("/api/v1/binder/entities", headers={**H, "X-Step-Up": grant})
        assert opened.status_code == 200, opened.text
        # …and still closed without it
        assert (await c.get("/api/v1/binder/entities", headers=H)).status_code == 428


async def test_a_grant_is_bound_to_its_user_and_dies_with_the_password():
    """A grant is not a bearer pass: it only works for the user it was minted for, and a
    credential change invalidates it along with the session."""
    await _reset_totp()
    async with SessionLocal() as s:                      # a second user with binder access
        owner = (await s.execute(select(User).where(User.email == OWNER_EMAIL))).scalar_one()
        s.add(User(tenant_id=owner.tenant_id, email="other@x.com", name="Other",
                   password_hash=hash_pw("password123"), role="admin", status="active"))
        await s.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        H = {"Authorization": f"Bearer {await _login(c)}"}
        secret, _ = await _enroll(c, H)
        grant = (await c.post("/api/v1/step-up/binder",
                              json={"code": pyotp.TOTP(secret).now()}, headers=H)).json()["token"]
        assert (await c.get("/api/v1/binder/entities", headers={**H, "X-Step-Up": grant})).status_code == 200

        # another user cannot ride this grant
        H2 = {"Authorization": f"Bearer {await _login(c, 'other@x.com', 'password123')}"}
        assert (await c.get("/api/v1/binder/entities",
                            headers={**H2, "X-Step-Up": grant})).status_code == 428

        # a forged grant for someone else's id is rejected too
        forged = make_capability("stepup:binder", minutes=20, sub="00000000-0000-0000-0000-000000000000", ver=0)
        assert (await c.get("/api/v1/binder/entities",
                            headers={**H, "X-Step-Up": forged})).status_code == 428

        # changing the password bumps token_version → the outstanding grant stops working
        ch = await c.post("/api/v1/auth/change-password",
                          json={"current_password": OWNER_PASSWORD, "new_password": "newpassword123"},
                          headers=H)
        assert ch.status_code == 200, ch.text
        H3 = {"Authorization": f"Bearer {await _login(c, OWNER_EMAIL, 'newpassword123')}"}
        assert (await c.get("/api/v1/binder/entities",
                            headers={**H3, "X-Step-Up": grant})).status_code == 428
        # restore the seeded password for the other tests in this module
        await c.post("/api/v1/auth/change-password",
                     json={"current_password": "newpassword123", "new_password": OWNER_PASSWORD},
                     headers=H3)


async def test_assistant_cannot_be_used_as_a_side_door_into_binder():
    """The Ask panel loads Binder data for anyone with the tab — which would walk straight
    around the lock. It must withhold that section unless the request proves the step-up."""
    from app.services.assistant import _build_context
    await _reset_totp()
    async with SessionLocal() as s:
        user = (await s.execute(select(User).where(User.email == OWNER_EMAIL))).scalar_one()
        ctx_locked, tabs = await _build_context(s, user, "mtd")                     # no step-up
        ctx_open, _ = await _build_context(s, user, "mtd", step_up={"binder"})      # proved it
    assert "binder" in tabs                                  # the owner does have the tab …
    assert "binder_detail" not in ctx_locked["data"]         # … but the data is withheld
    assert "binder_detail" in ctx_open["data"]


async def test_recovery_code_works_once_and_guessing_gets_locked_out():
    await _reset_totp()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        H = {"Authorization": f"Bearer {await _login(c)}"}
        secret, recovery = await _enroll(c, H)
        assert len(recovery) == 10

        first = await c.post("/api/v1/step-up/binder", json={"code": recovery[0]}, headers=H)
        assert first.status_code == 200 and first.json()["used_recovery"] is True
        assert first.json()["recovery_remaining"] == 9
        assert (await c.get("/api/v1/binder/entities",
                            headers={**H, "X-Step-Up": first.json()["token"]})).status_code == 200

        again = await c.post("/api/v1/step-up/binder", json={"code": recovery[0]}, headers=H)
        assert again.status_code == 400          # single use

        for _ in range(5):                       # repeated wrong codes → cooldown
            await c.post("/api/v1/step-up/binder", json={"code": "000000"}, headers=H)
        locked = await c.post("/api/v1/step-up/binder",
                              json={"code": pyotp.TOTP(secret).now()}, headers=H)
        assert locked.status_code == 429         # even a VALID code is refused while cooling off

    async with SessionLocal() as s:
        actions = {a.action for a in (await s.execute(select(AuditLog))).scalars()}
    assert {"totp.enabled", "step_up.granted", "step_up.failed"} <= actions


async def test_secret_is_encrypted_at_rest_and_never_returned_twice():
    await _reset_totp()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        H = {"Authorization": f"Bearer {await _login(c)}"}
        secret, _ = await _enroll(c, H)
        status = (await c.get("/api/v1/me/totp", headers=H)).json()
        assert status["enabled"] is True and status["recovery_remaining"] == 10
        assert "secret" not in status and "otpauth_uri" not in status      # never handed back
        # re-enrolling over a live authenticator is refused
        assert (await c.post("/api/v1/me/totp/start", headers=H)).status_code == 400

    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.email == OWNER_EMAIL))).scalar_one()
    assert u.totp_secret_enc and secret not in u.totp_secret_enc     # stored ciphertext…
    assert dec(u.totp_secret_enc) == secret                          # …that decrypts back
    assert all(len(h) == 64 for h in u.totp_recovery)                # recovery stored hashed
