"""Scorecard self-service office config (Phase A): edit owner full name (owner/admin) + upload a
headshot served publicly (for the app AND the read-only embed)."""
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant
from app.seed_ulrg_scorecard import load_ulrg_scorecard

TRANSPORT = ASGITransport(app=app)

# a minimal valid 1x1 PNG
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f5f0000000049454e44ae426082")


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        await load_ulrg_scorecard(s, t.id)


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


async def _owner():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


def _H(t):
    return {"Authorization": f"Bearer {t}"}


async def _davis_id(c, tok):
    d = (await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json()
    return next(g["id"] for g in d["groups"] if g["key"] == "davis")


async def test_edit_owner_name_full():
    tok = await _owner()
    async with _client() as c:
        gid = await _davis_id(c, tok)
        r = await c.patch(f"/api/v1/ulrg/group/{gid}", headers=_H(tok),
                          json={"owner_name": "Jace Gillies"})
        assert r.status_code == 200 and r.json()["owner_name"] == "Jace Gillies"
        d = (await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json()
        davis = next(g for g in d["groups"] if g["key"] == "davis")
        assert davis["owner"]["name"] == "Jace Gillies"


async def test_upload_headshot_then_public_serve():
    tok = await _owner()
    async with _client() as c:
        gid = await _davis_id(c, tok)
        up = await c.post(f"/api/v1/ulrg/group/{gid}/photo", headers=_H(tok),
                          files={"file": ("jace.png", _PNG, "image/png")})
        assert up.status_code == 201
        # payload now advertises a photo_url
        d = (await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json()
        davis = next(g for g in d["groups"] if g["key"] == "davis")
        purl = davis["owner"]["photo_url"]
        assert purl and purl.startswith(f"/ulrg/group/{gid}/photo")
        # served PUBLICLY (no auth) — the embed needs it
        img = await c.get(f"/api/v1/ulrg/group/{gid}/photo")
        assert img.status_code == 200 and img.headers["content-type"].startswith("image/")
        assert img.content == _PNG


async def test_edit_measurement_periods():
    tok = await _owner()
    async with _client() as c:
        r = await c.put("/api/v1/ulrg/periods", headers=_H(tok), json={"periods": [
            {"key": "2026Q4", "start": "2026-11-02", "end": "2027-01-30"},
            {"key": "2026Q3", "start": "2026-07-28", "end": "2026-10-30"}]})   # out of order on purpose
        assert r.status_code == 200
        assert [p["key"] for p in r.json()["periods"]] == ["2026Q3", "2026Q4"]  # sorted by start
        got = (await c.get("/api/v1/ulrg/periods", headers=_H(tok))).json()["periods"]
        assert len(got) == 2
        # bad date → 400
        bad = await c.put("/api/v1/ulrg/periods", headers=_H(tok),
                          json={"periods": [{"key": "X", "start": "nope", "end": "2026-01-01"}]})
        assert bad.status_code == 400
        # end before start → 400
        b2 = await c.put("/api/v1/ulrg/periods", headers=_H(tok),
                         json={"periods": [{"key": "X", "start": "2026-05-01", "end": "2026-04-01"}]})
        assert b2.status_code == 400


async def test_config_requires_admin_auth():
    async with _client() as c:
        gid = await _davis_id(c, await _owner())
        # no token → 401
        assert (await c.patch(f"/api/v1/ulrg/group/{gid}", json={"owner_name": "x"})).status_code == 401
        # non-image upload → 400
        tok = await _owner()
        bad = await c.post(f"/api/v1/ulrg/group/{gid}/photo", headers=_H(tok),
                           files={"file": ("note.txt", b"hi", "text/plain")})
        assert bad.status_code == 400
