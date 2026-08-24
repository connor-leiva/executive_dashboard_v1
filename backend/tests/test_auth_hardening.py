"""Security regressions found while auditing the tenant boundary.

None of these are multi-tenancy bugs — every one was live with a single tenant. They are
grouped here because they were found together and share a shape: a credential that was
accepted in a context it was never meant for.
"""
import uuid

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import AuditLog, ShareLink, Tenant, User
from app.security import make_capability, new_action_token, read_token
from app.seed import seed
from app.seed_ulrg_scorecard import load_ulrg_scorecard

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        await load_ulrg_scorecard(s, t.id)


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(t):
    return {"Authorization": f"Bearer {t}"}


async def _owner_token():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


# ── a share token is only valid for the audience it names ─────────────────────────────
async def test_a_rep_desk_token_cannot_open_the_company_scorecard():
    """Live bug: `_resolve` checked only that a token was unrevoked and unexpired, never
    `link.scope`. Every sales rep holds an `sd_rep` link — so every rep could read the full
    company L10 scorecard by swapping /desk for /scorecard in their own URL."""
    owner = await _owner_token()
    async with _client() as c:
        r = await c.post("/api/v1/ulrg/share", headers=_H(owner), json={"scope": "ulrg_scorecard"})
        scorecard_token = r.json()["token"]

    async with SessionLocal() as s:                       # mint a rep link directly
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        rep = ShareLink(tenant_id=t.id, scope="sd_rep", scope_ref="rep@example.com",
                        token=uuid.uuid4().hex)
        s.add(rep)
        await s.commit()
        rep_token = rep.token

    async with _client() as c:
        # The rep's own token, pointed at the scorecard: 404, and 404 rather than 403 so it
        # does not confirm that the endpoint or the token exists.
        assert (await c.get(f"/api/v1/share/{rep_token}/scorecard")).status_code == 404
        # ...and the reverse: a scorecard token cannot read a rep's desk.
        assert (await c.get(f"/api/v1/share/{scorecard_token}/desk")).status_code == 404
        # Each still works for its own scope.
        assert (await c.get(f"/api/v1/share/{scorecard_token}/scorecard")).status_code == 200


# ── the OAuth state is not a session ──────────────────────────────────────────────────
def test_qbo_oauth_state_is_not_usable_as_a_session_token():
    """Live bug: `state` was `make_token(...)`, i.e. a real 7-day session JWT — and the 2-arg
    call defaulted ver=0, which `current_user` reads as matching, so it authenticated as any
    user still on token_version 0 (the seeded owner). It was handed to Intuit, logged there,
    and returned in a URL query string."""
    state = make_capability("qbo_oauth", minutes=30, sub=str(uuid.uuid4()),
                            tid=str(uuid.uuid4()), biz=str(uuid.uuid4()))
    payload = read_token(state)              # it still decodes — same signing key
    # ...but it carries a purpose and NO session identity, so current_user cannot accept it:
    # the tid/sub checks in deps.py read `payload["sub"]`/`["tid"]`, and a session check that
    # matters is `cap`. Assert the distinguishing claim exists and the session claims that
    # current_user requires are not usable as a login.
    assert payload["cap"] == "qbo_oauth"
    assert "ver" not in payload, "a state token must not carry a session version claim"


async def test_qbo_callback_rejects_a_session_token_as_state():
    """The callback is unauthenticated, so it must verify the state's PURPOSE, not just its
    signature — otherwise any leaked session token is a valid callback state."""
    owner = await _owner_token()
    async with _client() as c:
        r = await c.get("/api/v1/integrations/qbo/callback",
                        params={"code": "x", "realmId": "1", "state": owner})
    assert r.status_code == 400, r.text          # not a 500, and not accepted


# ── a disabled account stays disabled ─────────────────────────────────────────────────
async def test_a_reset_link_cannot_resurrect_a_disabled_account():
    """Live bug: `reset_password` set status='active' with no status guard, and `disable_user`
    never cleared outstanding action tokens. So a reset link minted before an emergency
    disable re-enabled the account AND returned a live session."""
    raw, token_hash = new_action_token()
    import datetime as dt
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = User(tenant_id=t.id, email="revoked@x.com", name="Revoked",
                 password_hash=None, role="member", status="disabled", token_version=1,
                 action_token_hash=token_hash, action_token_purpose="reset",
                 action_token_expires=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=24))
        s.add(u)
        await s.commit()
        uid = u.id

    async with _client() as c:
        r = await c.post("/api/v1/auth/reset-password",
                         json={"token": raw, "new_password": "brand-new-password"})
    assert r.status_code == 400
    async with SessionLocal() as s:
        assert (await s.get(User, uid)).status == "disabled"


async def test_disabling_a_user_also_burns_their_outstanding_links():
    """token_version only revokes issued SESSIONS. An invite or reset link is a separate way
    back in, and it survived the disable."""
    owner = await _owner_token()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        raw, token_hash = new_action_token()
        import datetime as dt
        u = User(tenant_id=t.id, email="tobedisabled@x.com", name="T",
                 password_hash=None, role="member", status="invited", token_version=0,
                 action_token_hash=token_hash, action_token_purpose="invite",
                 action_token_expires=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7))
        s.add(u)
        await s.commit()
        uid = u.id

    async with _client() as c:
        assert (await c.post(f"/api/v1/users/{uid}/disable", headers=_H(owner))).status_code == 200
        # the invite link is now inert
        r = await c.post("/api/v1/auth/accept-invite",
                         json={"token": raw, "name": "T", "password": "password12345"})
    assert r.status_code == 400
    async with SessionLocal() as s:
        assert (await s.get(User, uid)).action_token_hash is None


# ── a password spray leaves a trace ───────────────────────────────────────────────────
async def test_login_writes_an_audit_row_on_success_and_on_failure():
    """Live gap: login audited nothing at all, so a spray across many accounts — which never
    trips the per-user lockout — was completely invisible to the operator."""
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        before = len((await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == t.id, AuditLog.action.like("auth.login%")))).scalars().all())

    async with _client() as c:
        await c.post("/api/v1/auth/login", json={"email": "nobody@nowhere.com", "password": "x"})
        await c.post("/api/v1/auth/login",
                     json={"email": "spring@springb.com", "password": "wrong-password"})
        await c.post("/api/v1/auth/login",
                     json={"email": "spring@springb.com", "password": "springtime"})

    async with SessionLocal() as s:
        rows = (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == t.id, AuditLog.action.like("auth.login%")))).scalars().all()
    assert len(rows) - before >= 3
    actions = {r.action for r in rows}
    assert "auth.login" in actions and "auth.login_failed" in actions
    # An unknown address has no user id to attribute to, so the attempted email is recorded —
    # that is the case most worth being able to see.
    unknown = [r for r in rows if (r.detail or {}).get("reason") == "no_such_login"]
    assert unknown and unknown[-1].detail["email"] == "nobody@nowhere.com"
    assert all("password" not in (r.detail or {}) for r in rows)


# ── dev/prod parity on the unauthenticated surface ────────────────────────────────────
async def test_malformed_ids_on_public_routes_are_rejected_at_the_boundary(monkeypatch):
    """Live bug, invisible to this suite by construction: these ids were typed `str` and bound
    straight into a GUID column. dbtypes.GUID parses to uuid.UUID on POSTGRES ONLY, so a
    malformed id was a clean 404 on SQLite (dev + every test) and an unhandled 500 in prod.
    Typing the param makes FastAPI reject it identically on both."""
    from app.config import settings

    async with _client() as c:
        r = await c.get("/api/v1/ulrg/group/not-a-uuid/photo")
        assert r.status_code == 422, r.text
        # The media route sits behind a feature flag whose 404 would otherwise mask the
        # boundary check entirely — turn it on so this asserts the id validation, not the flag.
        monkeypatch.setattr(settings, "AI_EMPLOYEES_ENABLED", True)
        r = await c.get("/api/v1/ai/media/not-a-uuid/file",
                        params={"t": make_capability("media_read", tid=str(uuid.uuid4()))})
        assert r.status_code == 422, r.text
