"""ULRG scorecard share links (SPEC 5.3 / Step 8): owner-gated create, token-scoped public read
with no auth, revoked/unknown → 404 (never 403), the ulrg_team scope deferred, and the embeddable
page carrying the ClickUp frame-ancestors CSP."""
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant
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


async def _owner_token():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


def _H(t):
    return {"Authorization": f"Bearer {t}"}


async def test_create_then_public_read_and_csp():
    owner = await _owner_token()
    async with _client() as c:
        r = await c.post("/api/v1/ulrg/share", headers=_H(owner), json={"scope": "ulrg_scorecard"})
        assert r.status_code == 201
        tok, url = r.json()["token"], r.json()["url"]
        assert url.endswith(f"/share/{tok}")

        sc = await c.get(f"/api/v1/share/{tok}/scorecard")           # no auth header — token is the auth
        assert sc.status_code == 200
        assert len(sc.json()["groups"]) == 4
        assert sc.headers.get("cache-control") == "no-store"         # token in URL → never cache the payload

        page = await c.get(f"/share/{tok}")
        assert page.status_code == 200
        csp = page.headers.get("content-security-policy", "")
        assert "frame-ancestors" in csp and "https://*.clickup.com" in csp
        assert "x-frame-options" not in page.headers                 # would block the ClickUp frame


async def test_unknown_and_revoked_read_as_404_not_403():
    async with _client() as c:
        assert (await c.get("/api/v1/share/nope/scorecard")).status_code == 404
        assert (await c.get("/share/nope")).status_code == 404

    owner = await _owner_token()
    async with _client() as c:
        tok = (await c.post("/api/v1/ulrg/share", headers=_H(owner),
                            json={"scope": "ulrg_scorecard"})).json()["token"]
        shares = (await c.get("/api/v1/ulrg/shares", headers=_H(owner))).json()
        sid = next(x["id"] for x in shares if x["url"].endswith(tok))
        assert (await c.delete(f"/api/v1/ulrg/share/{sid}", headers=_H(owner))).status_code == 204
        # a dead link leaks nothing — 404, not 403
        assert (await c.get(f"/api/v1/share/{tok}/scorecard")).status_code == 404
        assert (await c.get(f"/share/{tok}")).status_code == 404


async def test_create_requires_auth_and_team_scope_deferred():
    async with _client() as c:
        assert (await c.post("/api/v1/ulrg/share", json={"scope": "ulrg_scorecard"})).status_code == 401
    owner = await _owner_token()
    async with _client() as c:
        r = await c.post("/api/v1/ulrg/share", headers=_H(owner),
                         json={"scope": "ulrg_team", "scope_ref": "davis"})
        assert r.status_code == 400                                  # team-room sharing is Step 6


async def test_room_endpoint_not_yet_available():
    owner = await _owner_token()
    async with _client() as c:
        tok = (await c.post("/api/v1/ulrg/share", headers=_H(owner),
                            json={"scope": "ulrg_scorecard"})).json()["token"]
        assert (await c.get(f"/api/v1/share/{tok}/room")).status_code == 404
