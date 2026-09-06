"""The invite and reset screens must name the account they belong to.

Live failure, on the first workspace provisioned through the new flow. The owner opened their
invite, chose a name and a password, landed in the product — and could not sign in again the
next day. Nothing was broken: the account was active, with the right address on it. But the
setup screen asked for a name and a password and never showed the email, so the only copy of
the address was in an email nobody re-reads, and the owner had two addresses to choose between.
The sign-in page cannot rescue that — it answers "Invalid email or password" for every guess on
purpose, so it must not confirm which addresses exist.

The second half of the same defect is quieter and is why "just remember it" is not the fix: a
password form with no username field gives the browser's password manager nothing to file the
credential under, so autofill has nothing to offer on the next visit either.
"""
import datetime as dt
import re
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Tenant, User
from app.security import new_action_token
from app.seed import seed

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


async def _invited(email: str, purpose: str = "invite", *, name="",
                   expires_in=dt.timedelta(days=7), status="invited") -> str:
    """A user holding a live action token. Returns the raw token."""
    raw, token_hash = new_action_token()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        s.add(User(tenant_id=t.id, email=email, name=name, password_hash=None, role="member",
                   status=status, token_version=0, action_token_hash=token_hash,
                   action_token_purpose=purpose,
                   action_token_expires=dt.datetime.now(dt.timezone.utc) + expires_in))
        await s.commit()
    return raw


async def test_an_invite_link_names_the_account_it_belongs_to():
    raw = await _invited("named@x.com", name="Pat Invited")
    async with _client() as c:
        r = await c.post("/api/v1/auth/link-info", json={"token": raw, "purpose": "invite"})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "named@x.com"
    assert body["name"] == "Pat Invited"
    # The workspace too: somebody invited to two of them needs to know which one this is.
    assert body["workspace"]


async def test_reading_a_link_does_not_spend_it():
    """The whole point is that the screen can ask BEFORE the person fills the form in. If the
    lookup consumed the token, showing the address would break accepting the invite — the
    screen would name the account and then refuse to set it up."""
    raw = await _invited("notspent@x.com")
    async with _client() as c:
        assert (await c.post("/api/v1/auth/link-info",
                             json={"token": raw, "purpose": "invite"})).status_code == 200
        # asked twice, because a page reload is a second ask
        assert (await c.post("/api/v1/auth/link-info",
                             json={"token": raw, "purpose": "invite"})).status_code == 200
        accepted = await c.post("/api/v1/auth/accept-invite",
                                json={"token": raw, "name": "N", "password": "password12345"})
    assert accepted.status_code == 200, accepted.text


async def test_a_token_cannot_be_read_under_the_wrong_purpose():
    """Purpose is checked here exactly as the accepting endpoints check it. A reset token read
    as an invite would let one screen's link light up another screen."""
    raw = await _invited("wrongpurpose@x.com", purpose="reset", status="active")
    async with _client() as c:
        assert (await c.post("/api/v1/auth/link-info",
                             json={"token": raw, "purpose": "invite"})).status_code == 400
        assert (await c.post("/api/v1/auth/link-info",
                             json={"token": raw, "purpose": "reset"})).status_code == 200


async def test_an_unknown_purpose_is_refused_rather_than_looked_up():
    raw = await _invited("otherpurpose@x.com")
    async with _client() as c:
        r = await c.post("/api/v1/auth/link-info", json={"token": raw, "purpose": "totp"})
    assert r.status_code == 400


async def test_an_expired_or_unknown_link_answers_the_same_as_it_would_on_submit():
    stale = await _invited("stale@x.com", expires_in=-dt.timedelta(days=1))
    async with _client() as c:
        assert (await c.post("/api/v1/auth/link-info",
                             json={"token": stale, "purpose": "invite"})).status_code == 400
        r = await c.post("/api/v1/auth/link-info",
                         json={"token": "not-a-real-token", "purpose": "invite"})
    assert r.status_code == 400
    # Neutral: it must not distinguish "no such link" from "expired", or it becomes an oracle
    # for whether a guessed token ever existed.
    assert r.json()["detail"] == "This link is invalid or expired. Ask your admin to resend it."


# ── the screens themselves ────────────────────────────────────────────────────────────────
# The frontend has no test runner, and the password-manager half of this defect is invisible in
# the backend: the API can be perfect while the form still saves a password against no username.
SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
PUBLIC_AUTH = SRC / "PublicAuth.jsx"


@pytest.mark.skipif(not PUBLIC_AUTH.exists(), reason="frontend not present")
def test_both_setup_screens_show_the_address_and_offer_it_to_the_password_manager():
    text = PUBLIC_AUTH.read_text(encoding="utf-8")
    assert 'autoComplete="username"' in text, (
        "the password fields have no username beside them, so a saved credential cannot be "
        "offered back on the sign-in page")

    # Derived from the file rather than listed here: a third screen added later is covered
    # without anybody remembering to extend this test.
    bodies = re.split(r"^export function ", text, flags=re.M)[1:]
    screens = {b.split("(")[0].strip(): b for b in bodies}
    assert {"AcceptInvite", "ResetPassword"} <= set(screens), screens.keys()
    for name, body in screens.items():
        assert "<SignInAs" in body, f"{name} never tells you which account it is setting up"
