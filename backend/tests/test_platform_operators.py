"""The operator surface, and the wall between it and every tenant.

This is the one router that acts ACROSS tenants, which is the shape three phases of work went
into eliminating everywhere else. So the tests that matter most are not the endpoints — they
are the two directions of the wall: a tenant session must never reach an operator route, and
an operator session must never reach a tenant route. Both have to fail because of what the
tokens ARE, not because a check was remembered.
"""
import uuid

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import PlatformUser, Tenant, User
from app.security import hash_pw, make_platform_token, make_token
from app.seed import seed

TRANSPORT = ASGITransport(app=app)
OP_EMAIL = "operator@platform.test"
OP_PASSWORD = "operator-password-123"


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()
    async with SessionLocal() as s:
        op = (await s.execute(select(PlatformUser).where(
            PlatformUser.email == OP_EMAIL))).scalar_one_or_none()
        if op is None:
            s.add(PlatformUser(email=OP_EMAIL, name="Operator",
                               password_hash=hash_pw(OP_PASSWORD)))
            await s.commit()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(t, host=None):
    h = {"Authorization": f"Bearer {t}"}
    if host:
        h["x-tenant-host"] = host
    return h


async def _op_token():
    async with _client() as c:
        r = await c.post("/api/v1/platform/login",
                         json={"email": OP_EMAIL, "password": OP_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def _tenant_owner_token():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


# ── the wall, both directions ─────────────────────────────────────────────────────────
async def test_a_tenant_session_cannot_reach_the_operator_surface():
    """The failure everything else here depends on. A tenant OWNER — the most privileged
    account inside a tenant — must not be able to list or touch other tenants."""
    owner = await _tenant_owner_token()
    async with _client() as c:
        for method, path in (("get", "/api/v1/platform/tenants"),
                             ("get", "/api/v1/platform/me"),
                             ("get", "/api/v1/platform/tenants/springb"),
                             ("post", "/api/v1/platform/tenants/springb/suspend")):
            r = await getattr(c, method)(path, headers=_H(owner))
            assert r.status_code == 401, f"{path} -> {r.status_code}"


async def test_an_operator_session_cannot_reach_a_tenants_data():
    """And the converse. An operator administers tenants; they are not a user of one, so a
    platform token must not open the dashboard, the drills, or anything else tenant-scoped."""
    tok = await _op_token()
    async with _client() as c:
        for path in ("/api/v1/me", "/api/v1/dashboard?period=mtd", "/api/v1/users",
                     "/api/v1/settings/integrations"):
            r = await c.get(path, headers=_H(tok))
            assert r.status_code == 401, f"{path} -> {r.status_code}"


def test_the_two_token_shapes_are_mutually_unusable_by_construction():
    """Not a route test — the property underneath them. A platform token carries `pu` and no
    `tid`; a tenant token carries `tid` and no `pu`. Neither guard can be satisfied by the
    other's token even if a future route forgets which dependency to use."""
    from app.security import read_token

    plat = read_token(make_platform_token(uuid.uuid4(), 0))
    tenant = read_token(make_token(uuid.uuid4(), uuid.uuid4(), 0))
    assert "pu" in plat and "tid" not in plat
    assert "tid" in tenant and "pu" not in tenant


# ── what the surface is for ───────────────────────────────────────────────────────────
async def test_the_tenant_list_answers_operational_questions_not_business_ones():
    tok = await _op_token()
    async with _client() as c:
        r = await c.get("/api/v1/platform/tenants", headers=_H(tok))
    assert r.status_code == 200, r.text
    rows = {t["slug"]: t for t in r.json()["tenants"]}
    assert "springb" in rows
    row = rows["springb"]
    for k in ("status", "hosts", "businesses", "users", "sources", "sources_in_error",
              "last_synced_at", "sync_failures_7d"):
        assert k in row, k
    # ...and nothing about what the business earns.
    blob = str(row).lower()
    assert "revenue" not in blob and "gci" not in blob and "noi" not in blob


async def test_provisioning_through_the_api_takes_the_same_path_as_the_cli():
    tok = await _op_token()
    async with _client() as c:
        r = await c.post("/api/v1/platform/tenants", headers=_H(tok), json={
            "slug": "opco", "name": "Op Co", "owner_email": "owner@opco.test",
            "hostname": "opco.localhost"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["invite_url"].startswith("http://opco.localhost/accept-invite?token=")
    # provision_tenant's catalogs came with it — the whole point of one shared path.
    assert body["catalogs"]["standard_chart"]["total"] > 100
    async with _client() as c:                         # duplicate slug is a clean 400
        r = await c.post("/api/v1/platform/tenants", headers=_H(tok), json={
            "slug": "opco", "name": "Again", "owner_email": "x@y.z"})
    assert r.status_code == 400


# ── suspension has to bite ────────────────────────────────────────────────────────────
async def test_suspending_a_tenant_stops_new_logins_and_live_sessions():
    """A status field nobody enforces is a label. Suspension must end sessions that already
    exist, or a suspended customer keeps working until they happen to sign out."""
    tok = await _op_token()
    live = await _tenant_owner_token()               # a session issued BEFORE the suspension
    async with _client() as c:
        assert (await c.get("/api/v1/me", headers=_H(live))).status_code == 200

        r = await c.post("/api/v1/platform/tenants/springb/suspend", headers=_H(tok))
        assert r.status_code == 200 and r.json()["status"] == "suspended"

        # the live session stops working — 403, not 401: this is deliberate state, not a
        # credential problem, so the client must not bounce to a login screen
        r = await c.get("/api/v1/me", headers=_H(live))
        assert r.status_code == 403, r.text
        assert "suspended" in r.text.lower()

        # ...and a fresh login is refused too
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
        assert r.status_code == 403

        # resume restores both
        assert (await c.post("/api/v1/platform/tenants/springb/resume",
                             headers=_H(tok))).status_code == 200
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
        assert r.status_code == 200


async def test_an_expired_owner_invite_can_be_reissued():
    """The commonest real onboarding failure is a link that expired before the customer opened
    it, and there was previously no way to send another."""
    tok = await _op_token()
    async with _client() as c:
        await c.post("/api/v1/platform/tenants", headers=_H(tok), json={
            "slug": "reinvite", "name": "Reinvite Co", "owner_email": "owner@reinvite.test",
            "hostname": "reinvite.localhost"})
        r = await c.post("/api/v1/platform/tenants/reinvite/resend-invite", headers=_H(tok))
    assert r.status_code == 200, r.text
    raw = r.json()["invite_url"].split("token=")[1]

    async with _client() as c:                        # the NEW link works
        r = await c.post("/api/v1/auth/accept-invite",
                         headers={"x-tenant-host": "reinvite.localhost"},
                         json={"token": raw, "name": "Owner", "password": "a-good-password-1"})
    assert r.status_code == 200, r.text
    async with _client() as c:                        # ...and cannot be reissued once accepted
        r = await c.post("/api/v1/platform/tenants/reinvite/resend-invite", headers=_H(tok))
    assert r.status_code == 409


async def test_operator_actions_land_in_the_tenants_own_audit_trail():
    """An operator acting on a customer should be visible IN that customer's history, not only
    in a log the customer cannot see."""
    tok = await _op_token()
    async with _client() as c:
        await c.post("/api/v1/platform/tenants/springb/suspend", headers=_H(tok))
        await c.post("/api/v1/platform/tenants/springb/resume", headers=_H(tok))
        r = await c.get("/api/v1/platform/tenants/springb/audit", headers=_H(tok))
    actions = [e["action"] for e in r.json()["events"]]
    assert "tenant.suspended" in actions and "tenant.resumed" in actions
    who = [e for e in r.json()["events"] if e["action"] == "tenant.suspended"][0]
    assert who["detail"]["by"] == OP_EMAIL


async def test_a_disabled_operator_loses_access_immediately():
    tok = await _op_token()
    async with SessionLocal() as s:
        op = (await s.execute(select(PlatformUser).where(
            PlatformUser.email == OP_EMAIL))).scalar_one()
        op.is_active = False
        await s.commit()
    async with _client() as c:
        assert (await c.get("/api/v1/platform/tenants", headers=_H(tok))).status_code == 401
    async with SessionLocal() as s:
        op = (await s.execute(select(PlatformUser).where(
            PlatformUser.email == OP_EMAIL))).scalar_one()
        op.is_active = True
        await s.commit()
