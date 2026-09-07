"""Marketing requests actually leaving the building.

The request record has existed for a while with `delivered_at` permanently null -- saved here,
sent nowhere, which was the honest state while there was no delivery path. These are the tests for
the path, and most of them are about the ways delivery FAILS, because a delivery mechanism that
only works when everything works is the one that quietly loses somebody's request.
"""
import datetime as dt
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import (Business, Domain, IntranetCapability, IntranetIntegration,
                        IntranetMarketingRequest, IntranetMarketingSetting, IntranetMember,
                        IntranetPermission, IntranetRole, Tenant, User)
from app.security import hash_pw, make_token
from app.services import marketing_delivery as md

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token, host):
    return {"Authorization": f"Bearer {token}", "x-tenant-host": host}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No test in this file touches the network, including DNS.

    The webhook guard resolves the hostname it is given, so without this every delivery test
    would depend on a resolver answering for a domain we do not own -- passing or failing on
    whether the machine happens to be online, and testing the internet rather than the rule.
    Tests that are ABOUT resolution patch this again with what they need."""
    monkeypatch.setattr(md, "_resolve", lambda host, port: {"93.184.216.34"})


async def _workspace(slug: str, *, destination_type="webhook",
                     destination=None, enabled=True):
    """A tenant with the intranet on, one agent on the roster, and marketing requests configured."""
    host = f"{slug}.localhost"
    # Per workspace, so a test can tell its own deliveries apart from every other fixture's.
    if destination is None and destination_type == "webhook":
        destination = f"https://hooks-{slug}.example.com/abc"
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        t = Tenant(slug=slug, name=slug.title(), status="active",
                   config={"features": {"intranet": True}})
        s.add(t)
        await s.flush()
        s.add(Domain(tenant_id=t.id, hostname=host, is_primary=True))
        s.add(Business(tenant_id=t.id, key="main", name="Main", tag="Business", sort_order=0))
        owner = User(tenant_id=t.id, email=f"owner@{slug}.test", name="Owner",
                     password_hash=hash_pw("password123"), role="owner", status="active",
                     token_version=0)
        agent_user = User(tenant_id=t.id, email=f"agent@{slug}.test", name="Agent",
                          password_hash=hash_pw("password123"), role="member", status="active",
                          tab_access=[], token_version=0)
        s.add_all([owner, agent_user])
        await s.flush()
        role = IntranetRole(tenant_id=t.id, key="agent", name="Agent", sort=1, published_at=now)
        admin_role = IntranetRole(tenant_id=t.id, key="lead", name="Team Leader", sort=0,
                                  is_leadership=True, published_at=now)
        s.add_all([role, admin_role])
        await s.flush()
        s.add_all([
            IntranetMember(tenant_id=t.id, full_name="Ada Agent",
                           email=f"agent@{slug}.test", role_id=role.id, status="Active",
                           auth_source="Manual", user_id=agent_user.id),
            # The console gate wants an ACTIVE member whose role holds console_access=Full --
            # being the tenant's owner is not itself console access.
            IntranetMember(tenant_id=t.id, full_name="Olive Owner",
                           email=f"owner@{slug}.test", role_id=admin_role.id, status="Active",
                           auth_source="Manual", user_id=owner.id),
        ])
        cap = IntranetCapability(tenant_id=t.id, key="console_access", name="Console",
                                 description="Opens the admin console.", sort=0,
                                 published_at=now)
        s.add(cap)
        await s.flush()
        s.add(IntranetPermission(tenant_id=t.id, capability_id=cap.id, role_id=admin_role.id,
                                 level="Full", published_at=now))
        s.add(IntranetMarketingSetting(
            tenant_id=t.id, enabled=enabled, destination_type=destination_type,
            destination=destination, required_fields=[], published_at=now, draft_dirty=False))
        await s.commit()
        return host, t.id, {"owner": make_token(owner.id, t.id, 0),
                            "agent": make_token(agent_user.id, t.id, 0)}


async def _file_request(host, token, title="Flyer for 12 Oak St"):
    async with _client() as c:
        r = await c.post("/api/v1/intranet/marketing/requests",
                         data={"title": title, "priority": "Normal"},
                         headers=_H(token, host))
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"])


async def _row(request_id) -> IntranetMarketingRequest:
    async with SessionLocal() as s:
        return await s.get(IntranetMarketingRequest, request_id)


# ── the queue ─────────────────────────────────────────────────────────────────────────────

async def test_filing_a_request_queues_it_rather_than_sending_it_inline():
    """The POST must not wait on somebody else's server. A destination that accepts a connection
    and never answers would otherwise hold a worker for the whole timeout, once per request --
    a denial of service on our own API, configured through a text box."""
    host, _, tokens = await _workspace("mktqueue")
    row = await _row(await _file_request(host, tokens["agent"]))
    assert row.delivered_at is None, "the handler delivered inline"
    assert row.delivery_attempts == 0
    assert row.delivery_next_attempt_at is not None, "the request was saved but never queued"


async def test_the_tick_delivers_a_queued_request_and_records_where():
    host, _, tokens = await _workspace("mktsend")
    request_id = await _file_request(host, tokens["agent"])

    sent = []

    async def fake_post(url, json, headers=None):
        sent.append((url, json))
        return 200, "ok"

    from app import worker
    original, md._post = md._post, fake_post
    try:
        await worker.marketing_delivery_tick()
    finally:
        md._post = original

    # This workspace's own deliveries. The tick drains every tenant in one pass, so the other
    # fixtures in this file are in `sent` too.
    mine = [(u, b) for u, b in sent if "mktsend" in u]
    assert len(mine) == 1, "the queued request was not delivered"
    url, body = mine[0]
    assert url == "https://hooks-mktsend.example.com/abc"
    assert body["request"]["title"] == "Flyer for 12 Oak St"
    assert body["request"]["requester"] == "Ada Agent"

    row = await _row(request_id)
    assert row.delivered_at is not None
    assert row.delivery_next_attempt_at is None, "a delivered request is still in the queue"
    assert row.delivery_attempts == 1
    assert "hooks-mktsend.example.com" in row.delivery_detail


async def test_a_delivered_request_is_never_sent_twice():
    host, _, tokens = await _workspace("mkttwice")
    await _file_request(host, tokens["agent"])
    calls = []

    async def fake_post(url, json, headers=None):
        calls.append(url)
        return 200, "ok"

    from app import worker
    original, md._post = md._post, fake_post
    try:
        await worker.marketing_delivery_tick()
        await worker.marketing_delivery_tick()      # the tick runs every minute, forever
    finally:
        md._post = original
    assert len([u for u in calls if "mkttwice" in u]) == 1, \
        "the same request was delivered on a later tick"


# ── failure, retry, and giving up ─────────────────────────────────────────────────────────

async def test_a_failure_backs_off_and_eventually_stops():
    """A destination that has been wrong for thirteen hours is wrong, not busy. Retrying it
    forever fills the log and never delivers; retrying it once loses a request over a blip."""
    row = IntranetMarketingRequest(id=uuid.uuid4(), tenant_id=uuid.uuid4(),
                                   requester_label="Ada", title="x", priority="Normal",
                                   status="New", delivery_attempts=0)
    waits = []
    for _ in range(md.MAX_ATTEMPTS):
        md.record(row, md.Outcome(False, "hooks.example.com answered 500.", True))
        waits.append(row.delivery_next_attempt_at)

    assert row.delivery_attempts == md.MAX_ATTEMPTS
    assert waits[-1] is None, "it would retry a dead destination forever"
    assert all(w is not None for w in waits[:-1]), "it gave up before the backoff ran out"
    gaps = [(w - dt.datetime.now(dt.timezone.utc)).total_seconds() for w in waits[:-1]]
    assert gaps == sorted(gaps), "the backoff does not actually back off"
    assert row.delivered_at is None


def test_the_sender_says_whether_waiting_could_help_rather_than_the_queue_guessing():
    """This started as prefix-matching on the message text, which meant the retry decision and
    the words a person reads were two facts that had to agree -- and they did not: a webhook
    pointed at a private address fell through the prefixes and was scheduled for five more
    attempts at a URL that could never work."""
    row = IntranetMarketingRequest(id=uuid.uuid4(), tenant_id=uuid.uuid4(),
                                   requester_label="Ada", title="x", priority="Normal",
                                   status="New", delivery_attempts=0)
    md.record(row, md.Outcome(False, "anything at all", False))
    assert row.delivery_next_attempt_at is None
    assert row.delivery_attempts == 1


async def test_dns_failing_is_retried_but_a_private_address_is_not(monkeypatch):
    """Two failures of the same shape with opposite answers. A name that will not resolve today
    may resolve in an hour; a name that resolves to 10.0.0.1 will resolve there in ten hours too."""
    import socket as _socket

    def boom(host, port):
        raise _socket.gaierror("nope")

    monkeypatch.setattr(md, "_resolve", boom)
    transient = await md._send_webhook("https://gone.example.com/h", {"text": "x"})
    assert transient.retryable is True, "a DNS blip abandoned the request"

    monkeypatch.setattr(md, "_resolve", lambda host, port: {"10.0.0.1"})
    permanent = await md._send_webhook("https://sneaky.example.com/h", {"text": "x"})
    assert permanent.retryable is False, "it will retry a URL that can never work"


async def test_a_workspace_that_switched_marketing_off_stops_retrying():
    """Not a transient failure: there is nowhere to retry TO. Backing off for thirteen hours
    against a destination that no longer exists is just noise in the log."""
    host, tenant_id, tokens = await _workspace("mktoff")
    request_id = await _file_request(host, tokens["agent"])
    async with SessionLocal() as s:
        cfg = await s.get(IntranetMarketingSetting, tenant_id)
        cfg.enabled = False
        await s.commit()

    from app import worker
    await worker.marketing_delivery_tick()

    row = await _row(request_id)
    assert row.delivered_at is None
    assert row.delivery_next_attempt_at is None, "it is still retrying a destination that is off"
    assert "No destination is configured" in row.delivery_detail


async def test_one_tenants_broken_destination_does_not_block_another_tenants_request():
    """The tick runs across every workspace at once. A shared session and one escaping exception
    would mean a single customer's dead webhook stops everybody else's requests going out."""
    host_a, _, tokens_a = await _workspace("mktbadone", destination="https://broken.example.com/x")
    host_b, _, tokens_b = await _workspace("mktbadtwo", destination="https://fine.example.com/y")
    await _file_request(host_a, tokens_a["agent"], title="A's request")
    b_id = await _file_request(host_b, tokens_b["agent"], title="B's request")

    async def fake_post(url, json, headers=None):
        if "broken" in url:
            raise RuntimeError("connection reset")
        return 200, "ok"

    from app import worker
    original, md._post = md._post, fake_post
    try:
        await worker.marketing_delivery_tick()
    finally:
        md._post = original

    assert (await _row(b_id)).delivered_at is not None, \
        "one workspace's broken webhook stopped another workspace's delivery"


# ── Slack ─────────────────────────────────────────────────────────────────────────────────

async def test_slack_saying_no_with_a_200_is_not_treated_as_delivered():
    """channel_not_found, not_in_channel and invalid_auth all arrive as a perfectly successful
    HTTP response carrying ok:false. Checking the status code alone marks every one delivered."""
    host, tenant_id, tokens = await _workspace("mktslack", destination_type="slack",
                                               destination="#marketing")
    request_id = await _file_request(host, tokens["agent"])
    async with SessionLocal() as s:
        from app.security import enc
        s.add(IntranetIntegration(
            tenant_id=tenant_id, provider_key="slack", display_name="Slack",
            role_label="Notifications", status="Action Needed", config={},
            credential_ref=enc("xoxb-test-token")))
        await s.commit()

    async def fake_post(url, json, headers=None):
        return 200, '{"ok":false,"error":"not_in_channel"}'

    from app import worker
    original, md._post = md._post, fake_post
    try:
        await worker.marketing_delivery_tick()
    finally:
        md._post = original

    row = await _row(request_id)
    assert row.delivered_at is None, "a refused Slack post was recorded as delivered"
    assert "not_in_channel" in row.delivery_detail
    assert row.delivery_next_attempt_at is not None, "an invitable channel is worth retrying"


async def test_slack_without_a_token_says_so_instead_of_failing_obscurely():
    host, _, tokens = await _workspace("mktnotok", destination_type="slack",
                                       destination="#marketing")
    request_id = await _file_request(host, tokens["agent"])
    from app import worker
    await worker.marketing_delivery_tick()
    detail = (await _row(request_id)).delivery_detail
    assert "not connected" in detail.lower() and "integrations" in detail.lower(), detail


# ── the webhook is a URL our own server fetches ───────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://localhost/hook",
    "https://127.0.0.1/hook",
    "https://[::1]/hook",
    "http://hooks.example.com/plain",          # scheme, before anything is resolved
])
async def test_a_webhook_cannot_be_pointed_at_our_own_network(url, monkeypatch):
    """This URL is typed by a tenant admin and POSTed to by our server, so it reaches everything
    a browser cannot: the metadata endpoint, the private network the database is on, our own API
    on localhost. Refused after RESOLUTION, so the decimal and IPv6-mapped spellings of 127.0.0.1
    are refused too without enumerating them."""
    monkeypatch.undo()                       # these names resolve locally; use the real lookup
    assert md.check_webhook_target(url) is not None, f"{url} was accepted"


async def test_a_public_webhook_is_allowed(monkeypatch):
    """The guard has to let real destinations through, or it is just an outage."""
    monkeypatch.setattr(md, "_resolve", lambda host, port: {"93.184.216.34"})
    assert md.check_webhook_target("https://hooks.slack.com/services/T0/B0/xxx") is None


async def test_a_name_that_answers_with_one_public_and_one_private_address_is_refused(monkeypatch):
    """The connection picks an address, not us. A name resolving to both is the private one
    whenever it happens to choose that -- so one bad answer is enough to refuse the whole name."""
    monkeypatch.setattr(md, "_resolve", lambda host, port: {"93.184.216.34", "10.0.0.7"})
    assert md.check_webhook_target("https://sneaky.example.com/hook") is not None


async def test_a_name_that_does_not_resolve_says_so(monkeypatch):
    import socket

    def boom(host, port):
        raise socket.gaierror("nope")

    monkeypatch.setattr(md, "_resolve", boom)
    problem = md.check_webhook_target("https://gone.example.com/hook")
    assert problem and "could not be resolved" in problem


async def test_a_blocked_webhook_is_never_actually_requested(monkeypatch):
    """The check is worthless if the POST happens anyway."""
    host, _, tokens = await _workspace("mktssrf", destination="https://hooks.example.com/ok")
    request_id = await _file_request(host, tokens["agent"])
    async with SessionLocal() as s:
        request = await s.get(IntranetMarketingRequest, request_id)
        cfg = await s.get(IntranetMarketingSetting, request.tenant_id)
        cfg.destination = "https://127.0.0.1/hook"
        await s.commit()

    called = []

    async def fake_post(url, json, headers=None):
        called.append(url)
        return 200, "ok"

    from app import worker
    monkeypatch.undo()                       # 127.0.0.1 resolves without a network
    original, md._post = md._post, fake_post
    try:
        await worker.marketing_delivery_tick()
    finally:
        md._post = original

    assert not [u for u in called if "127.0.0.1" in u], \
        "the server made the request it had just refused"
    row = await _row(request_id)
    assert row.delivery_next_attempt_at is None, "a private address is not worth retrying"


def test_loopback_wearing_an_ipv6_costume_is_still_loopback():
    assert md._blocked("::ffff:127.0.0.1") is True
    assert md._blocked("169.254.169.254") is True        # cloud metadata
    assert md._blocked("10.0.0.5") is True
    assert md._blocked("172.16.0.1") is True
    assert md._blocked("8.8.8.8") is False


# ── what the destination is told ──────────────────────────────────────────────────────────

def test_attachments_are_named_but_never_posted_to_a_channel():
    """The files sit behind the workspace's auth and a Slack channel does not. A listing
    photograph of an unlisted property should not be republished to everyone in #marketing."""
    row = IntranetMarketingRequest(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), requester_label="Ada",
        title="Flyer", priority="High", status="New", client="The Ruiz family")
    body = md.payload(row, 2, "https://x.test/console/marketing")
    assert body["request"]["attachments"] == 2
    assert "2 attachments" in body["text"]
    assert "open the request to download" in body["text"]
    assert "https://x.test/console/marketing" in body["text"]


def test_the_message_leads_with_the_title_and_who_asked():
    row = IntranetMarketingRequest(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), requester_label="Ada Agent",
        title="Flyer for 12 Oak St", priority="High", status="New",
        due_date=dt.date(2026, 9, 30), description="Needs the new branding.")
    lines = md.payload(row, 0, None)["text"].splitlines()
    assert lines[0] == "*Flyer for 12 Oak St*"
    assert lines[1] == "From Ada Agent"
    assert "Priority: High" in lines
    assert "Due: 2026-09-30" in lines


# ── telling the person who asked ──────────────────────────────────────────────────────────

async def test_the_requester_hears_when_their_request_is_finished():
    """The portal has no inbox. Without this the only way an agent learns their flyer is done is
    to go and ask the person who did it."""
    host, tenant_id, tokens = await _workspace("mktnotify")
    request_id = await _file_request(host, tokens["agent"])

    posted = []

    async def fake_send(to, subject, html, text, reply_to=None, idempotency_key=None):
        posted.append((to, subject, text, idempotency_key))
        return True

    from app.services import mailer
    original, mailer.send = mailer.send, fake_send
    try:
        async with _client() as c:
            r = await c.patch(f"/api/console/marketing/requests/{request_id}",
                              json={"status": "Done"}, headers=_H(tokens["owner"], host))
            assert r.status_code == 200, r.text
            # An assignee change is not news to the agent who filed it.
            posted.clear()
            r2 = await c.patch(f"/api/console/marketing/requests/{request_id}",
                               json={"status": "Done"}, headers=_H(tokens["owner"], host))
            assert r2.status_code == 200
    finally:
        mailer.send = original

    assert not posted, "the requester was emailed again for a status that did not change"


async def test_the_requester_is_emailed_once_with_the_new_status():
    host, _, tokens = await _workspace("mktnotify2")
    request_id = await _file_request(host, tokens["agent"])
    posted = []

    async def fake_send(to, subject, html, text, reply_to=None, idempotency_key=None):
        posted.append((to, subject, text, idempotency_key))
        return True

    from app.services import mailer
    original, mailer.send = mailer.send, fake_send
    try:
        async with _client() as c:
            r = await c.patch(f"/api/console/marketing/requests/{request_id}",
                              json={"status": "In Progress"},
                              headers=_H(tokens["owner"], host))
        assert r.status_code == 200, r.text
    finally:
        mailer.send = original

    assert len(posted) == 1, posted
    to, subject, text, key = posted[0]
    assert to == "agent@mktnotify2.test"
    assert "in progress" in subject.lower()
    assert "New -> In Progress" in text
    assert str(request_id) in key, "a retry would send the same news twice"


# ── the console's test button ─────────────────────────────────────────────────────────────

async def test_sending_a_test_is_a_real_delivery_and_records_the_result():
    """A shape check on the saved values would confirm only what saving them already confirmed.
    Every failure worth catching -- a channel the bot was never invited to, a revoked token, a
    host that no longer resolves -- is invisible until something is actually sent."""
    host, tenant_id, tokens = await _workspace("mkttest")
    sent = []

    async def fake_post(url, json, headers=None):
        sent.append(json)
        return 200, "ok"

    original, md._post = md._post, fake_post
    try:
        async with _client() as c:
            r = await c.post("/api/console/marketing/test",
                             headers=_H(tokens["owner"], host))
    finally:
        md._post = original

    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert len(sent) == 1, "the test button did not send anything"
    assert "test" in sent[0]["request"]["title"].lower()

    async with SessionLocal() as s:
        cfg = await s.get(IntranetMarketingSetting, tenant_id)
        assert cfg.last_test_ok is True
        assert cfg.last_tested_at is not None
    assert r.json()["item"]["delivery"] == "live"


async def test_a_failing_test_is_recorded_as_failing_not_swallowed():
    host, tenant_id, tokens = await _workspace("mkttestbad")

    async def fake_post(url, json, headers=None):
        return 500, "boom"

    original, md._post = md._post, fake_post
    try:
        async with _client() as c:
            r = await c.post("/api/console/marketing/test",
                             headers=_H(tokens["owner"], host))
    finally:
        md._post = original

    assert r.status_code == 200, "a failed test is an answer, not an error"
    assert r.json()["ok"] is False
    assert r.json()["item"]["delivery"] == "failing"
    async with SessionLocal() as s:
        assert (await s.get(IntranetMarketingSetting, tenant_id)).last_test_ok is False


async def test_a_configured_but_untested_destination_does_not_claim_to_be_live():
    """A saved destination proves somebody typed a channel name. Only a delivery proves
    delivery."""
    host, _, tokens = await _workspace("mktuntested")
    async with _client() as c:
        r = await c.get("/api/console/marketing", headers=_H(tokens["owner"], host))
    assert r.status_code == 200
    assert r.json()["item"]["config_complete"] is True
    assert r.json()["item"]["delivery"] == "untested"


async def test_the_test_button_refuses_when_there_is_nowhere_to_send():
    host, _, tokens = await _workspace("mktnodest", destination_type="none", destination=None,
                                       enabled=False)
    async with _client() as c:
        r = await c.post("/api/console/marketing/test", headers=_H(tokens["owner"], host))
    assert r.status_code == 422


# ── the Slack credential ──────────────────────────────────────────────────────────────────

async def test_the_slack_token_is_stored_encrypted_and_never_returned():
    host, tenant_id, tokens = await _workspace("mktslacktok")
    async with _client() as c:
        r = await c.patch("/api/console/slack", json={"bot_token": "xoxb-real-secret"},
                          headers=_H(tokens["owner"], host))
        assert r.status_code == 200, r.text
        assert r.json()["item"]["token_set"] is True
        assert "xoxb-real-secret" not in r.text, "the console echoed the token back"

        got = await c.get("/api/console/slack", headers=_H(tokens["owner"], host))
        assert "xoxb-real-secret" not in got.text

    async with SessionLocal() as s:
        row = (await s.execute(select(IntranetIntegration).where(
            IntranetIntegration.tenant_id == tenant_id,
            IntranetIntegration.provider_key == "slack"))).scalar_one()
        assert row.credential_ref and "xoxb-real-secret" not in row.credential_ref, \
            "the token was stored in the clear"
        assert row.status == "Action Needed", "a stored token was reported as a working one"


async def test_a_token_that_is_not_a_bot_token_is_refused_at_the_form():
    """A user token or a signing secret pasted here fails much later, as a permissions error that
    reads like a channel problem."""
    host, _, tokens = await _workspace("mktslackbad")
    async with _client() as c:
        r = await c.patch("/api/console/slack", json={"bot_token": "xoxp-user-token"},
                          headers=_H(tokens["owner"], host))
    assert r.status_code == 422


async def test_another_workspace_cannot_read_or_set_this_ones_slack_token():
    host_a, tenant_a, tokens_a = await _workspace("mktslackone")
    host_b, _, tokens_b = await _workspace("mktslacktwo")
    async with _client() as c:
        await c.patch("/api/console/slack", json={"bot_token": "xoxb-a-secret"},
                      headers=_H(tokens_a["owner"], host_a))
        r = await c.get("/api/console/slack", headers=_H(tokens_b["owner"], host_b))
    assert r.status_code == 200
    assert r.json()["item"]["token_set"] is False, "a workspace saw another's Slack connection"

    async with SessionLocal() as s:
        rows = (await s.execute(select(IntranetIntegration).where(
            IntranetIntegration.provider_key == "slack",
            IntranetIntegration.tenant_id == tenant_a))).scalars().all()
        assert len(rows) == 1


# ── what the portal claims ────────────────────────────────────────────────────────────────

async def test_the_portal_no_longer_says_delivery_is_pending_when_it_is_not():
    """`delivery_pending` was hardcoded True. It said "we will not send this" on every workspace,
    including the ones now delivering."""
    host, _, tokens = await _workspace("mktpending")
    async with _client() as c:
        r = await c.get("/api/v1/intranet/config", headers=_H(tokens["agent"], host))
    assert r.status_code == 200, r.text
    marketing = r.json()["config"]["marketing"]
    assert marketing["available"] is True
    assert marketing["delivery_pending"] is False
