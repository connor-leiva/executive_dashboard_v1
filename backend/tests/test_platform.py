"""Multi-user platform — the authorization security payload (SPEC-platform §7):
authz matrix, dashboard filtering, lineage enforcement, invite/reset e2e,
token_version revocation, management invariants, lockout, and cross-tenant isolation.
"""
import datetime as dt
import uuid

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant, User, Domain, Business
from app.security import hash_pw, make_token

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token, host=None):
    h = {"Authorization": f"Bearer {token}"}
    if host:
        h["x-tenant-host"] = host
    return h


async def _springb():
    async with SessionLocal() as s:
        return (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()


async def _owner_token():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login", json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


async def _mk_user(email, role="member", tabs=None, status="active", pw="password123", tenant=None):
    """Create a user directly (test setup) and return (id, token)."""
    async with SessionLocal() as s:
        t = tenant or (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = User(tenant_id=t.id, email=email.lower(), name=email.split("@")[0],
                 password_hash=(hash_pw(pw) if pw else None), role=role, status=status,
                 tab_access=tabs, token_version=0)
        s.add(u)
        await s.commit()
        return u.id, make_token(u.id, t.id, 0)


# ── migration back-compat (test 9) ──────────────────────────────────
async def test_seeded_owner_logs_in_and_me():
    tok = await _owner_token()
    async with _client() as c:
        me = (await c.get("/api/v1/me", headers=_H(tok))).json()
    assert me["role"] == "owner" and me["status"] == "active"
    assert me["tabs"] == ["portfolio", "ulrg", "forum", "becollective", "edge", "sympli", "flywheel", "books", "binder"]


# ── authz matrix (test 1) ───────────────────────────────────────────
async def test_role_matrix_on_management_routes():
    _, member = await _mk_user("mmatrix@x.com", tabs=["forum"])
    owner = await _owner_token()
    async with _client() as c:
        # members are 403 on management + integration routes
        for path in ("/api/v1/users", "/api/v1/settings/integrations", "/api/v1/businesses"):
            assert (await c.get(path, headers=_H(member))).status_code == 403
        assert (await c.post("/api/v1/sync/all", headers=_H(member))).status_code == 403
        # owner passes
        assert (await c.get("/api/v1/users", headers=_H(owner))).status_code == 200


async def test_tab_routes_gate_by_grant():
    _, forum_only = await _mk_user("forumonly@x.com", tabs=["forum"])
    async with _client() as c:
        assert (await c.get("/api/v1/forum", headers=_H(forum_only))).status_code == 200
        assert (await c.get("/api/v1/becollective", headers=_H(forum_only))).status_code == 403
        # sympli financials require the sympli tab
        assert (await c.get("/api/v1/businesses/sympli/financials", headers=_H(forum_only))).status_code == 403


# ── dashboard filtering (test 2) ────────────────────────────────────
async def test_dashboard_filtered_to_grants():
    _, ulrg_only = await _mk_user("ulrgonly@x.com", tabs=["ulrg"])
    async with _client() as c:
        d = (await c.get("/api/v1/dashboard", headers=_H(ulrg_only))).json()
    assert set(d["areas"].keys()) == {"ulrg"}                 # no forum/becollective/sympli
    assert d["portfolio"]["revenue"] is None and d["scorecards"] == []
    assert d["flywheel"]["available"] is False


# ── lineage enforcement (test 3) ────────────────────────────────────
async def test_lineage_inherits_tab_permission():
    _, ulrg_only = await _mk_user("ulrglin@x.com", tabs=["ulrg"])
    async with _client() as c:
        # forum drill denied
        assert (await c.get("/api/v1/metrics/forum_payments/detail?business=springb",
                            headers=_H(ulrg_only))).status_code == 403
        # own-tab drill allowed
        assert (await c.get("/api/v1/metrics/units_closed/detail",
                            headers=_H(ulrg_only))).status_code == 200


# ── invite / accept / reset e2e (tests 4, 5) ────────────────────────
async def test_invite_accept_login_flow():
    owner = await _owner_token()
    async with _client() as c:
        r = await c.post("/api/v1/users/invite", headers=_H(owner),
                         json={"email": "newmember@x.com", "role": "member", "tab_access": ["forum"]})
        assert r.status_code == 200
        url = r.json()["invite_url"]
        token = url.split("token=")[1]
        # accept
        acc = await c.post("/api/v1/auth/accept-invite",
                           json={"token": token, "name": "New Member", "password": "password123"})
        assert acc.status_code == 200 and acc.json()["token"]
        # token is single-use
        again = await c.post("/api/v1/auth/accept-invite",
                             json={"token": token, "name": "X", "password": "password123"})
        assert again.status_code == 400
        # the new member can log in and only sees forum
        login = await c.post("/api/v1/auth/login", json={"email": "newmember@x.com", "password": "password123"})
        me = (await c.get("/api/v1/me", headers=_H(login.json()["token"]))).json()
    assert me["role"] == "member" and me["tabs"] == ["forum"]


async def test_password_change_revokes_old_sessions():
    uid, tok = await _mk_user("rotate@x.com", tabs=["forum"])
    async with _client() as c:
        assert (await c.get("/api/v1/me", headers=_H(tok))).status_code == 200
        ch = await c.post("/api/v1/auth/change-password", headers=_H(tok),
                          json={"current_password": "password123", "new_password": "newpassword456"})
        assert ch.status_code == 200
        # old token now 401 (token_version bumped); fresh token works
        assert (await c.get("/api/v1/me", headers=_H(tok))).status_code == 401
        assert (await c.get("/api/v1/me", headers=_H(ch.json()["token"]))).status_code == 200


# ── management invariants (test 6) ──────────────────────────────────
async def test_last_owner_and_self_invariants():
    owner = await _owner_token()
    async with SessionLocal() as s:
        me = (await s.execute(select(User).where(User.email == "spring@springb.com"))).scalar_one()
        owner_id = str(me.id)
    _, admin_tok = await _mk_user("admin2@x.com", role="admin")
    admin2_id = None
    async with SessionLocal() as s:
        admin2_id = str((await s.execute(select(User).where(User.email == "admin2@x.com"))).scalar_one().id)
    async with _client() as c:
        # last owner cannot be demoted/disabled
        assert (await c.patch(f"/api/v1/users/{owner_id}", headers=_H(owner),
                              json={"role": "member"})).status_code in (403, 409)
        assert (await c.post(f"/api/v1/users/{owner_id}/disable", headers=_H(owner))).status_code in (403, 409)
        # admin cannot manage another admin
        assert (await c.post(f"/api/v1/users/{admin2_id}/disable", headers=_H(admin_tok))).status_code == 403


# ── lockout (test 7) ────────────────────────────────────────────────
async def test_login_lockout():
    await _mk_user("locked@x.com", tabs=["forum"], pw="rightpassword1")
    async with _client() as c:
        for _ in range(10):
            await c.post("/api/v1/auth/login", json={"email": "locked@x.com", "password": "wrong"})
        r = await c.post("/api/v1/auth/login", json={"email": "locked@x.com", "password": "rightpassword1"})
    assert r.status_code == 423


# ── cross-tenant isolation (test 8) ─────────────────────────────────
async def _make_tenant_b():
    async with SessionLocal() as s:
        existing = (await s.execute(select(Tenant).where(Tenant.slug == "tenantb"))).scalar_one_or_none()
        if existing:
            return existing.id
        t = Tenant(slug="tenantb", name="Tenant B")
        s.add(t)
        await s.flush()
        s.add(Domain(tenant_id=t.id, hostname="tenantb.testhost", is_primary=True))
        # Distinct key so tests with unscoped Business.key lookups don't collide with B.
        s.add(Business(tenant_id=t.id, key="bmain", name="B Real Estate", tag="Real estate", sort_order=0))
        await s.commit()
        return t.id


async def test_cross_tenant_token_and_object_isolation():
    tb = await _make_tenant_b()
    async with SessionLocal() as s:
        bu = (await s.execute(select(User).where(
            User.tenant_id == tb, User.email == "b_owner@x.com"))).scalar_one_or_none()
        if not bu:
            bu = User(tenant_id=tb, email="b_owner@x.com", name="B", password_hash=hash_pw("password123"),
                      role="owner", status="active")
            s.add(bu)
            await s.commit()
        b_token = make_token(bu.id, tb, 0)
    owner = await _owner_token()   # springb owner
    async with _client() as c:
        # A springb token used against tenant B's host → 401 (invalid session for
        # this realm; the client should re-authenticate, not read it as a 403).
        r = await c.get("/api/v1/me", headers=_H(owner, host="tenantb.testhost"))
        assert r.status_code == 401
        # B's own token on B's host works
        assert (await c.get("/api/v1/me", headers=_H(b_token, host="tenantb.testhost"))).status_code == 200
        # springb owner probing a tenant-B user id → 404 (no existence leak)
        async with SessionLocal() as s:
            b_user_id = str((await s.execute(select(User).where(User.email == "b_owner@x.com"))).scalar_one().id)
        assert (await c.post(f"/api/v1/users/{b_user_id}/disable", headers=_H(owner))).status_code == 404


# ── regression: invite link points at the host the admin is using ───
async def test_invite_link_uses_request_origin():
    """The invite/reset link must be built from the admin's Origin (a live host),
    not a stored primary-domain row that may not be serving the app yet (a dead
    custom domain there just loads a broken page for the invitee)."""
    owner = await _owner_token()
    async with _client() as c:
        r = await c.post("/api/v1/users/invite",
                         headers={**_H(owner), "origin": "https://springb.acumyn.io"},
                         json={"email": "originlink@x.com", "role": "member", "tab_access": ["forum"]})
    assert r.status_code == 200
    assert r.json()["invite_url"].startswith("https://springb.acumyn.io/accept-invite?token=")


# ── regression: invite must survive >1 primary domain (prod) ─────────
async def test_invite_survives_multiple_primary_domains():
    """A tenant with several domains flagged primary (e.g. an app host + an api
    host) must not 500 the invite. _primary_host used scalar_one_or_none(), which
    raised MultipleResultsFound — after the user was already committed — and the
    bare exception bypassed CORS so the browser only saw a generic failure."""
    from app.routers.users import _primary_host
    sb = await _springb()
    async with SessionLocal() as s:
        s.add(Domain(tenant_id=sb.id, hostname="api.springb.test", is_primary=True))
        await s.commit()
        host = await _primary_host(s, sb.id)   # must not raise
        # clean up so the extra domain can't perturb host resolution elsewhere
        dup = (await s.execute(select(Domain).where(Domain.hostname == "api.springb.test"))).scalar_one()
        await s.delete(dup)
        await s.commit()
    assert host and "." in host
