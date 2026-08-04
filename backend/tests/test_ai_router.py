"""AI Employees — Step 4 router: flag gate, role/tab guards, and the approval lifecycle
(draft → approve → ship), including the 409 on shipping an unapproved artifact.
"""
import uuid

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.seed import seed
from app.config import settings
from app.db import SessionLocal
from app.models import Tenant, User, AIEmployee, AIRun, AIArtifact
from app.security import hash_pw, make_token

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded_enabled():
    prev = settings.AI_EMPLOYEES_ENABLED
    settings.AI_EMPLOYEES_ENABLED = True          # the whole surface is flag-gated
    await seed()
    yield
    settings.AI_EMPLOYEES_ENABLED = prev


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token):
    return {"Authorization": f"Bearer {token}"}


async def _tenant_id():
    async with SessionLocal() as s:
        return (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id


async def _owner_token():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


async def _mk_member(email, tabs):
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = User(tenant_id=t.id, email=email.lower(), name=email.split("@")[0],
                 password_hash=hash_pw("password123"), role="member", status="active",
                 tab_access=tabs, token_version=0)
        s.add(u)
        await s.commit()
        return make_token(u.id, t.id, 0)


async def _seed_run_with_draft(emp_id, tenant_id, state="draft"):
    async with SessionLocal() as s:
        run = AIRun(tenant_id=tenant_id, employee_id=uuid.UUID(emp_id), skill_key="strategy",
                    trigger="manual", status="awaiting_approval", summary="pivot", reads=["r1"])
        s.add(run)
        await s.flush()
        art = AIArtifact(tenant_id=tenant_id, run_id=run.id, kind="strategy", lane="Strategy",
                         title="Strategy pivot", dest_label="Strategy memo",
                         payload={"kind": "strategy"}, state=state)
        s.add(art)
        await s.commit()
        return str(run.id), str(art.id)


# ── flag gate ─────────────────────────────────────────────────────────────────
async def test_flag_off_returns_404():
    owner = await _owner_token()
    settings.AI_EMPLOYEES_ENABLED = False
    try:
        async with _client() as c:
            assert (await c.get("/api/v1/ai/employees", headers=_H(owner))).status_code == 404
    finally:
        settings.AI_EMPLOYEES_ENABLED = True


# ── role / tab guards ─────────────────────────────────────────────────────────
async def test_member_without_grant_403_and_with_grant_reads():
    owner = await _owner_token()
    async with _client() as c:
        emp = (await c.post("/api/v1/ai/employees", headers=_H(owner),
                            json={"name": "Nova", "role_title": "Social Media Manager"})).json()
    no_grant = await _mk_member("ainogrant@x.com", tabs=["forum"])
    with_grant = await _mk_member("aigrant@x.com", tabs=["ai_employees"])
    async with _client() as c:
        assert (await c.get("/api/v1/ai/employees", headers=_H(no_grant))).status_code == 403
        # member WITH the grant reads, but cannot mutate
        assert (await c.get("/api/v1/ai/employees", headers=_H(with_grant))).status_code == 200
        assert (await c.post("/api/v1/ai/employees", headers=_H(with_grant),
                             json={"name": "X"})).status_code == 403
        assert (await c.post(f"/api/v1/ai/employees/{emp['id']}/skills/strategy/run",
                             headers=_H(with_grant), json={})).status_code == 403


# ── lifecycle: draft → approve → ship (+ the 409s) ────────────────────────────
async def test_lifecycle_draft_approve_ship():
    owner = await _owner_token()
    tid = await _tenant_id()
    async with _client() as c:
        emp = (await c.post("/api/v1/ai/employees", headers=_H(owner),
                            json={"name": "Aria"})).json()
        eid = emp["id"]
        run_id, art_id = await _seed_run_with_draft(eid, tid)

        # the run shows one draft artifact
        got = (await c.get(f"/api/v1/ai/runs/{run_id}", headers=_H(owner))).json()
        assert len(got["artifacts"]) == 1 and got["artifacts"][0]["state"] == "draft"

        # approve with writeback gates CLOSED → approved, not shipped
        settings.AI_EMPLOYEES_WRITEBACK_ENABLED = False
        appr = (await c.post(f"/api/v1/ai/runs/{run_id}/approve", headers=_H(owner))).json()
        assert appr["approved"] == 1 and appr["shipped"] == 0 and appr["writeback_open"] is False
        assert appr["run_status"] == "approved"

        # shipping while the gate is closed is a 409 (writeback disabled)
        r = await c.post(f"/api/v1/ai/artifacts/{art_id}/ship", headers=_H(owner))
        assert r.status_code == 409 and "disabled" in r.json()["detail"].lower()

        # open BOTH gates, then ship succeeds
        settings.AI_EMPLOYEES_WRITEBACK_ENABLED = True
        await c.patch(f"/api/v1/ai/employees/{eid}", headers=_H(owner),
                      json={"writeback_enabled": True})
        shipped = (await c.post(f"/api/v1/ai/artifacts/{art_id}/ship", headers=_H(owner))).json()
        assert shipped["state"] == "shipped" and shipped["shipped_at"]
    settings.AI_EMPLOYEES_WRITEBACK_ENABLED = False


async def test_ship_unapproved_returns_409():
    owner = await _owner_token()
    tid = await _tenant_id()
    async with _client() as c:
        emp = (await c.post("/api/v1/ai/employees", headers=_H(owner),
                            json={"name": "Iris"})).json()
        _, art_id = await _seed_run_with_draft(emp["id"], tid, state="draft")
        r = await c.post(f"/api/v1/ai/artifacts/{art_id}/ship", headers=_H(owner))
        assert r.status_code == 409 and "approved" in r.json()["detail"].lower()


async def test_create_seeds_six_skills_and_awaiting_badge():
    owner = await _owner_token()
    tid = await _tenant_id()
    async with _client() as c:
        emp = (await c.post("/api/v1/ai/employees", headers=_H(owner),
                            json={"name": "Wren"})).json()
        skills = (await c.get(f"/api/v1/ai/employees/{emp['id']}/skills",
                              headers=_H(owner))).json()["skills"]
        assert {s["key"] for s in skills} == {
            "audit", "trend_brief", "strategy", "design_carousel", "reel_script", "measure"}
        await _seed_run_with_draft(emp["id"], tid)
        lst = (await c.get("/api/v1/ai/employees", headers=_H(owner))).json()
        mine = next(e for e in lst["employees"] if e["id"] == emp["id"])
        assert mine["awaiting_approval"] == 1 and lst["awaiting_total"] >= 1
