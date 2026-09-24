"""ULRG Recruiting — the optional GHL Workflow webhook (RECRUITING-SPEC §5.6, Phase 6b).

A public, internet-reachable endpoint on a product that touches customers' CRMs, so the tests
here are mostly about what it refuses and what it cannot be made to do.

The design that makes it safe is that THE BODY IS A HINT. Nothing in the payload is read and
written down; the handler runs the ordinary poll, which re-reads the truth with our own token.
A forged body can at worst make us ask GHL a question we were about to ask anyway. The first
test pins that, because it is the property everything else rests on.
"""
import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Business, Integration, Tenant
from app.security import dec, enc
from app.seed import seed

URL = "/api/v1/webhooks/ghl-recruiting"


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t.example.test")


async def _integ(s, *, secret: str | None = "s3cret", frozen=False, suspended=False):
    biz = (await s.execute(select(Business).where(Business.key == "ulrg"))).scalar_one()
    tenant = await s.get(Tenant, biz.tenant_id)
    tenant.status = "suspended" if suspended else "active"
    cfg = {k: v for k, v in (tenant.config or {}).items() if k != "syncs_frozen"}
    if frozen:
        cfg["syncs_frozen"] = True
    tenant.config = cfg

    row = (await s.execute(select(Integration).where(
        Integration.tenant_id == biz.tenant_id,
        Integration.provider == "ghl_recruiting"))).scalars().first()
    if row is None:
        row = Integration(tenant_id=biz.tenant_id, provider="ghl_recruiting",
                          business_id=biz.id, status="connected")
        s.add(row)
    base = {"location_id": "loc", "pipeline_id": "pipe",
            "synced_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    if secret is not None:
        base["webhook_secret_enc"] = enc(secret)
    row.config = base
    row.access_token_enc = enc("pit-test-token")
    await s.flush()
    await s.commit()
    return biz.tenant_id, row


# ── the property everything rests on ────────────────────────────────────────────────────────

async def test_the_body_cannot_change_anything(monkeypatch):
    """A forged payload claiming a stage move, a signing, anything at all -- must move no row.
    All it may do is cause a poll, which re-reads the truth with our own token."""
    polled = []

    async def _poll(s, tenant_id):
        polled.append(tenant_id)
        return {"written": 0}

    monkeypatch.setattr("app.services.recruiting_poll.poll_activity", _poll)

    async with SessionLocal() as s:
        await _integ(s)

    malicious = {"contactId": "someone-elses", "opportunityId": "x",
                 "pipelineStageId": "st_signed", "status": "won",
                 "locationId": "a-different-location", "signed": True}
    async with _client() as c:
        r = await c.post(URL, json=malicious, headers={"X-Axcion-Secret": "s3cret"})

    assert r.status_code == 200 and r.json()["polled"] is True
    assert len(polled) == 1
    # And the reply says nothing about what was found: it lands in a log in somebody else's
    # system, and a candidate's name does not belong there.
    assert set(r.json()) == {"ok", "polled"}


# ── the gate ────────────────────────────────────────────────────────────────────────────────

async def test_a_wrong_or_missing_secret_is_refused(monkeypatch):
    monkeypatch.setattr("app.services.recruiting_poll.poll_activity",
                        lambda s, t: (_ for _ in ()).throw(AssertionError("polled on a bad secret")))
    async with SessionLocal() as s:
        await _integ(s)
    async with _client() as c:
        assert (await c.post(URL, json={})).status_code == 401
        assert (await c.post(URL, json={}, headers={"X-Axcion-Secret": "wrong"})).status_code == 401
        assert (await c.post(URL, json={}, headers={"X-Axcion-Secret": ""})).status_code == 401


async def test_an_unconfigured_deploy_looks_like_no_route():
    """404, not 401. With the feature off nowhere, the endpoint should not advertise that it
    exists and is merely waiting for the right header."""
    async with SessionLocal() as s:
        await _integ(s, secret=None)
    async with _client() as c:
        r = await c.post(URL, json={}, headers={"X-Axcion-Secret": "anything"})
    assert r.status_code == 404


# ── the workspace's own state still governs ─────────────────────────────────────────────────

async def test_a_frozen_workspace_is_not_polled(monkeypatch):
    """Freezing is how a compromised credential stops being used. A webhook is precisely the
    traffic that is meant to stop -- it must not be a way around it."""
    monkeypatch.setattr("app.services.recruiting_poll.poll_activity",
                        lambda s, t: (_ for _ in ()).throw(AssertionError("polled while frozen")))
    async with SessionLocal() as s:
        await _integ(s, frozen=True)
    async with _client() as c:
        r = await c.post(URL, json={}, headers={"X-Axcion-Secret": "s3cret"})
    assert r.status_code == 200 and r.json()["polled"] is False

    async with SessionLocal() as s:
        await _integ(s, suspended=True)
    async with _client() as c:
        r = await c.post(URL, json={}, headers={"X-Axcion-Secret": "s3cret"})
    assert r.status_code == 200 and r.json()["polled"] is False


async def test_a_burst_polls_once(monkeypatch):
    """One change can fire several Workflow triggers -- a reply, a status, a stage move. Polling
    per delivery would spend the location's shared rate budget that the sync also draws on."""
    calls = []

    async def _poll(s, tenant_id):
        calls.append(tenant_id)
        # The real poll stamps this; the debounce reads it.
        row = (await s.execute(select(Integration).where(
            Integration.tenant_id == tenant_id,
            Integration.provider == "ghl_recruiting"))).scalars().first()
        row.config = {**(row.config or {}),
                      "activity_polled_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        await s.commit()
        return {"written": 0}

    monkeypatch.setattr("app.services.recruiting_poll.poll_activity", _poll)
    async with SessionLocal() as s:
        await _integ(s)
    async with _client() as c:
        first = await c.post(URL, json={}, headers={"X-Axcion-Secret": "s3cret"})
        second = await c.post(URL, json={}, headers={"X-Axcion-Secret": "s3cret"})
        third = await c.post(URL, json={}, headers={"X-Axcion-Secret": "s3cret"})

    assert first.json()["polled"] is True
    assert second.json()["polled"] is False and second.json()["reason"] == "debounced"
    assert third.json()["polled"] is False
    assert len(calls) == 1


async def test_a_failing_poll_still_answers_200(monkeypatch):
    """A 500 here gets the Workflow retried, and a retry storm against a CRM integration is its
    own outage. The five-minute tick catches up regardless."""
    async def _boom(s, tenant_id):
        raise RuntimeError("GHL had a bad minute")

    monkeypatch.setattr("app.services.recruiting_poll.poll_activity", _boom)
    async with SessionLocal() as s:
        await _integ(s)
        row = (await s.execute(select(Integration).where(
            Integration.provider == "ghl_recruiting"))).scalars().first()
        row.config = {k: v for k, v in row.config.items() if k != "activity_polled_at"}
        await s.commit()
    async with _client() as c:
        r = await c.post(URL, json={}, headers={"X-Axcion-Secret": "s3cret"})
    assert r.status_code == 200 and r.json()["polled"] is False


# ── the secret itself ───────────────────────────────────────────────────────────────────────

async def test_the_secret_is_stored_encrypted_and_never_read_back():
    """It is a credential. If the settings screen could echo it, our copy would be as useful as
    the one in GHL and rotating it would stop meaning anything."""
    async with SessionLocal() as s:
        _, row = await _integ(s, secret="plain-text-secret")
        stored = row.config["webhook_secret_enc"]
    assert stored != "plain-text-secret", "the secret is sitting in config in the clear"
    assert dec(stored) == "plain-text-secret"
