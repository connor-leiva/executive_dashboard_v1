"""Transactional email — the mailer's contract, and the two guarantees around it.

Nothing here touches the network. `mailer._post` exists precisely so the seam can be replaced,
the way tests/test_ai_employees.py replaces `_claude_call`.

The two behaviours worth stating plainly, because both are easy to lose in a refactor that
looks like a tidy-up:

  SENDING NEVER BREAKS THE OPERATION THAT TRIGGERED IT. By the time an invite is emailed, the
  user row is committed. An exception escaping the mailer would show the admin a 500 for a user
  that WAS created, and their retry would hit the 409.

  EMAIL IS NEVER THE ONLY COPY. Every endpoint still returns its link. That is the fallback for
  a bounce, a mistyped address or a spam filter, and it is asserted here rather than left as a
  convention that survives until somebody tidies the response shape.
"""
import httpx
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Tenant
from app.seed import seed
from app.services import binder_reminders, mail_templates, mailer

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


async def _owner_token():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


def _capture(monkeypatch):
    """Replace the network seam and record what would have gone to Resend."""
    calls = []

    async def fake_post(payload, headers):
        calls.append((payload, headers))
        return 200, "{}"
    monkeypatch.setattr(mailer, "_post", fake_post)
    return calls


def _enable(monkeypatch, key="re_test", frm="Acumyn <mail@acumyn.io>", reply=""):
    monkeypatch.setattr(settings, "RESEND_API_KEY", key)
    monkeypatch.setattr(settings, "MAIL_FROM", frm)
    monkeypatch.setattr(settings, "MAIL_REPLY_TO", reply)


# ── the mailer itself ───────────────────────────────────────────────
async def test_no_key_means_no_send_and_no_crash(monkeypatch):
    """Unconfigured is the state local dev and this whole suite run in, and it must be inert
    rather than merely tolerated: no network call, no exception, just False."""
    calls = _capture(monkeypatch)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    assert await mailer.send("a@b.com", "Subject", "<p>hi</p>", "hi") is False
    assert calls == [], "a send was attempted with no API key"


async def test_a_successful_send_carries_both_bodies(monkeypatch):
    calls = _capture(monkeypatch)
    _enable(monkeypatch)
    assert await mailer.send("a@b.com", "Subject", "<p>hi</p>", "hi") is True

    payload, headers = calls[0]
    assert payload["from"] == "Acumyn <mail@acumyn.io>"
    assert payload["to"] == ["a@b.com"], "a single recipient must still be a list"
    assert payload["subject"] == "Subject"
    # Both, always. A text part is what non-HTML clients render and what spam scoring expects to
    # find; sending HTML alone is the single easiest way into a Promotions tab.
    assert payload["html"] == "<p>hi</p>" and payload["text"] == "hi"
    assert headers["Authorization"] == "Bearer re_test"


async def test_a_list_of_recipients_is_passed_through(monkeypatch):
    """The Binder digest sends to several people at once."""
    calls = _capture(monkeypatch)
    _enable(monkeypatch)
    await mailer.send(["a@b.com", "c@d.com"], "S", "<p>h</p>", "h")
    assert calls[0][0]["to"] == ["a@b.com", "c@d.com"]


async def test_a_provider_error_returns_false_rather_than_raising(monkeypatch):
    _enable(monkeypatch)

    async def rejected(payload, headers):
        return 422, "bad address"
    monkeypatch.setattr(mailer, "_post", rejected)
    assert await mailer.send("a@b.com", "S", "<p>h</p>", "h") is False


async def test_a_transport_error_returns_false_rather_than_raising(monkeypatch):
    """The outage case. This is the one that would 500 an invite for a user that exists."""
    _enable(monkeypatch)

    async def dead(payload, headers):
        raise httpx.ConnectError("resend unreachable")
    monkeypatch.setattr(mailer, "_post", dead)
    assert await mailer.send("a@b.com", "S", "<p>h</p>", "h") is False


async def test_reply_to_prefers_the_caller_then_the_setting_then_nothing(monkeypatch):
    calls = _capture(monkeypatch)

    _enable(monkeypatch, reply="support@acumyn.io")
    await mailer.send("a@b.com", "S", "<p>h</p>", "h", reply_to="inviter@acme.com")
    assert calls[-1][0]["reply_to"] == "inviter@acme.com", "an explicit reply_to must win"

    await mailer.send("a@b.com", "S", "<p>h</p>", "h")
    assert calls[-1][0]["reply_to"] == "support@acumyn.io", "the setting is the fallback"

    _enable(monkeypatch, reply="")
    await mailer.send("a@b.com", "S", "<p>h</p>", "h")
    assert "reply_to" not in calls[-1][0], "an empty reply_to must be absent, not blank"


async def test_the_idempotency_key_is_sent_only_when_given(monkeypatch):
    """Resend dedups on it, which is what makes a double-clicked resend send once."""
    calls = _capture(monkeypatch)
    _enable(monkeypatch)

    await mailer.send("a@b.com", "S", "<p>h</p>", "h")
    assert "Idempotency-Key" not in calls[-1][1]

    await mailer.send("a@b.com", "S", "<p>h</p>", "h", idempotency_key="invite-7")
    assert calls[-1][1]["Idempotency-Key"] == "invite-7"


# ── copy ────────────────────────────────────────────────────────────
def test_user_supplied_names_are_escaped_in_the_html():
    """A workspace name and a user's name both reach the HTML body, and both are typed by
    somebody. The subject is a mail header rather than markup, so it is not escaped there."""
    _subject, html, _text = mail_templates.invite(
        "https://acme.acumyn.io/accept-invite?token=x", "<script>alert(1)</script>", "A & B")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "A &amp; B" in html and "A & B" not in html


def test_every_template_returns_subject_html_and_text():
    """The tuple is unpacked positionally into mailer.send, so the shape IS the contract."""
    for built in (mail_templates.invite("https://x/y", "Ann", "Acme"),
                  mail_templates.reset("https://x/y", "Acme"),
                  mail_templates.owner_invite("https://x/y", "Acme"),
                  mail_templates.binder_digest("Binder: 2 obligations", "Acme LLC:")):
        subject, html, text = built
        assert subject and html and text


def test_the_invite_reads_differently_with_and_without_an_inviter():
    named, _, _ = mail_templates.invite("https://x/y", "Ann", "Acme")
    anon, _, _ = mail_templates.invite("https://x/y", None, "Acme")
    assert named == "Ann invited you to Acme"
    assert "None" not in anon


# ── the endpoints ───────────────────────────────────────────────────
async def test_an_invite_still_returns_its_link_with_no_key_configured(monkeypatch):
    """The no-email-is-not-the-only-copy guarantee, as a test rather than a convention. Email is
    the convenience; the link is the fallback for a bounce or a mistyped address, and removing
    it would trade one onboarding failure mode for a worse one."""
    calls = _capture(monkeypatch)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    token = await _owner_token()
    async with _client() as c:
        r = await c.post("/api/v1/users/invite",
                         headers={"Authorization": f"Bearer {token}"},
                         json={"email": "nokey@x.com", "role": "member", "tab_access": ["ulrg"]})
    assert r.status_code == 200
    assert "accept-invite?token=" in r.json()["invite_url"]
    assert calls == []


async def test_inviting_sends_the_mail_and_replies_to_the_inviter(monkeypatch):
    """The wiring, not just the mailer: an endpoint that builds a perfect email and never hands
    it to a background task looks identical from the response body."""
    calls = _capture(monkeypatch)
    _enable(monkeypatch)
    token = await _owner_token()
    async with _client() as c:
        r = await c.post("/api/v1/users/invite",
                         headers={"Authorization": f"Bearer {token}"},
                         json={"email": "sent@x.com", "role": "member", "tab_access": ["ulrg"]})
    assert r.status_code == 200
    assert len(calls) == 1, "the invite email was never handed to a background task"
    payload, _headers = calls[0]
    assert payload["to"] == ["sent@x.com"]
    # The inviter, not a support queue: a reply to "what is this?" should reach the colleague
    # who sent it, at the moment the recipient is deciding whether to trust the link.
    assert payload["reply_to"] == "spring@springb.com"
    assert r.json()["invite_url"] in payload["text"]

    async with SessionLocal() as s:
        name = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().name
    assert name in payload["subject"], "the email names the workspace, not the platform"


async def test_a_reset_link_is_emailed_without_hijacking_the_reply(monkeypatch):
    """A reply to a password reset should reach a monitored inbox, not whichever admin happened
    to click the button."""
    calls = _capture(monkeypatch)
    _enable(monkeypatch)
    token = await _owner_token()
    async with _client() as c:
        h = {"Authorization": f"Bearer {token}"}
        inv = (await c.post("/api/v1/users/invite", headers=h,
                            json={"email": "resetme@x.com", "role": "member",
                                  "tab_access": ["ulrg"]})).json()
        uid = inv["user"]["id"]
        calls.clear()
        r = await c.post(f"/api/v1/users/{uid}/reset-link", headers=h)
    assert "reset-password?token=" in r.json()["reset_url"]
    assert len(calls) == 1
    assert "reply_to" not in calls[0][0]


async def test_resending_an_invite_carries_an_idempotency_key(monkeypatch):
    """So a double-clicked button sends once. The key is scoped to the token's expiry, so a
    genuinely reissued invite is a different key and does send."""
    calls = _capture(monkeypatch)
    _enable(monkeypatch)
    token = await _owner_token()
    async with _client() as c:
        h = {"Authorization": f"Bearer {token}"}
        uid = (await c.post("/api/v1/users/invite", headers=h,
                            json={"email": "again@x.com", "role": "member",
                                  "tab_access": ["ulrg"]})).json()["user"]["id"]
        calls.clear()
        await c.post(f"/api/v1/users/{uid}/resend-invite", headers=h)
    assert len(calls) == 1
    assert calls[0][1]["Idempotency-Key"].startswith(f"invite-{uid}-")


# ── the worker ──────────────────────────────────────────────────────
async def test_the_binder_digest_goes_through_the_mailer(monkeypatch):
    """Reminders run in the WORKER, which is a different Railway service with its own
    environment. This is the assertion that the digest is wired to a transport at all."""
    calls = _capture(monkeypatch)
    _enable(monkeypatch)
    await binder_reminders._deliver(["a@b.com", "c@d.com"], "Binder: 2 need attention",
                                    "Acme LLC:  - Annual report (overdue, due 2026-01-01)")
    assert len(calls) == 1
    payload, _ = calls[0]
    assert payload["to"] == ["a@b.com", "c@d.com"]
    assert payload["subject"] == "Binder: 2 need attention"
    assert "Annual report" in payload["text"] and "Annual report" in payload["html"]


async def test_a_digest_with_nobody_to_send_to_is_not_sent(monkeypatch):
    calls = _capture(monkeypatch)
    _enable(monkeypatch)
    await binder_reminders._deliver([], "Binder: something", "body")
    assert calls == []
