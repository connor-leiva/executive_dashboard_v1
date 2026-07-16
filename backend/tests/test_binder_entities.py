"""Acumyn Binder — Step 2 entity-management API tests (SPEC Part 5.0 / 8).

Covers the manual entity lifecycle: name-only saves dormant, full entity is tracking-ready,
validation + duplicate rejection, edit (with EIN write-only), deactivate hides-but-retains,
`binder`-tab permission gating, the audit trail, and tenant-scoped isolation. The empty
first-run state is asserted too (a fresh tenant has zero entities).
"""
import uuid

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant, User, LegalEntity, AuditLog
from app.security import make_token, hash_pw
from app.services import binder

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token):
    return {"Authorization": f"Bearer {token}"}


async def _tid():
    async with SessionLocal() as s:
        return (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id


async def _owner_token():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


async def _member(email, tabs):
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = User(tenant_id=t.id, email=email, name=email.split("@")[0], password_hash=hash_pw("x"),
                 role="member", status="active", tab_access=tabs, token_version=0)
        s.add(u)
        await s.commit()
        return make_token(u.id, t.id, 0)


# ── First-run empty state ─────────────────────────────────────────────────────
async def test_fresh_tenant_has_zero_entities():
    """The Binder never assumes an entity exists — the seed creates none (Step 1 invariant)."""
    tok = await _owner_token()
    async with _client() as c:
        r = await c.get("/api/v1/binder/entities", headers=_H(tok))
    assert r.status_code == 200
    assert r.json()["entities"] == []
    assert r.json()["counts"]["total"] == 0


# ── Create ────────────────────────────────────────────────────────────────────
async def test_create_name_only_is_dormant():
    tok = await _owner_token()
    async with _client() as c:
        r = await c.post("/api/v1/binder/entities", headers=_H(tok),
                         json={"legal_name": "Zenworth Holdings, LLC"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tracking_ready"] is False
    assert body["nudge"] and "state" in body["nudge"] and "formation date" in body["nudge"]
    assert body["entity_group"] == "operating"     # default


async def test_create_full_entity_is_tracking_ready_and_masks_ein():
    tok = await _owner_token()
    async with _client() as c:
        r = await c.post("/api/v1/binder/entities", headers=_H(tok), json={
            "legal_name": "Utah Life Real Estate Group, LLC", "nickname": "The Team",
            "entity_type": "llc", "jurisdiction": "ut", "formation_date": "2019-08-05",
            "ein": "87-4123456", "entity_group": "operating", "ownership": "100%"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tracking_ready"] is True and body["nudge"] is None
    assert body["jurisdiction"] == "UT"            # normalized upper
    assert body["entity_type"] == "llc"
    assert body["ein_masked"] == "87-41•••••" and body["has_ein"] is True
    assert "4123456" not in str(body)              # raw EIN never leaves


@pytest.mark.parametrize("payload,frag", [
    ({"legal_name": ""}, "legal_name"),
    ({"legal_name": "Bad Type Co", "entity_type": "gmbh"}, "entity_type"),
    ({"legal_name": "Bad State Co", "jurisdiction": "ZZ"}, "jurisdiction"),
    ({"legal_name": "Bad Date Co", "formation_date": "not-a-date"}, "formation_date"),
])
async def test_create_validation_errors(payload, frag):
    tok = await _owner_token()
    async with _client() as c:
        r = await c.post("/api/v1/binder/entities", headers=_H(tok), json=payload)
    assert r.status_code == 400
    assert frag in r.json()["detail"]


async def test_duplicate_legal_name_rejected():
    tok = await _owner_token()
    async with _client() as c:
        await c.post("/api/v1/binder/entities", headers=_H(tok), json={"legal_name": "Dup Co, LLC"})
        r = await c.post("/api/v1/binder/entities", headers=_H(tok), json={"legal_name": "dup co, llc"})
    assert r.status_code == 400 and "already exists" in r.json()["detail"]


# ── Edit ──────────────────────────────────────────────────────────────────────
async def test_edit_flips_dormant_to_ready_and_ein_write_only():
    tok = await _owner_token()
    async with _client() as c:
        made = (await c.post("/api/v1/binder/entities", headers=_H(tok),
                             json={"legal_name": "Grow Into It, LLC", "ein": "12-9998888"})).json()
        eid = made["id"]
        assert made["tracking_ready"] is False
        # Fill the deriving fields; send blank EIN -> must keep the existing one.
        r = await c.patch(f"/api/v1/binder/entities/{eid}", headers=_H(tok), json={
            "entity_type": "s_corp", "jurisdiction": "AZ", "formation_date": "2021-01-11", "ein": ""})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tracking_ready"] is True and body["nudge"] is None
    assert body["has_ein"] is True and body["ein_masked"] == "12-99•••••"   # unchanged


async def test_edit_missing_entity_404():
    tok = await _owner_token()
    async with _client() as c:
        r = await c.patch(f"/api/v1/binder/entities/{uuid.uuid4()}", headers=_H(tok),
                          json={"nickname": "x"})
    assert r.status_code == 404


# ── Deactivate ────────────────────────────────────────────────────────────────
async def test_deactivate_hides_but_retains():
    tok = await _owner_token()
    async with _client() as c:
        eid = (await c.post("/api/v1/binder/entities", headers=_H(tok),
                            json={"legal_name": "Retire Me, LLC"})).json()["id"]
        r = await c.post(f"/api/v1/binder/entities/{eid}/deactivate", headers=_H(tok))
        assert r.status_code == 200 and r.json()["active"] is False
        default = (await c.get("/api/v1/binder/entities", headers=_H(tok))).json()
        withall = (await c.get("/api/v1/binder/entities?include_inactive=true",
                               headers=_H(tok))).json()
    ids_default = {e["id"] for e in default["entities"]}
    ids_all = {e["id"] for e in withall["entities"]}
    assert eid not in ids_default        # hidden from the matrix/default list
    assert eid in ids_all                # but retained


# ── Permissions (Part 9.5) ────────────────────────────────────────────────────
async def test_binder_tab_gates_access():
    no_tab = await _member("nobinder@springb.com", ["forum"])
    with_tab = await _member("hasbinder@springb.com", ["binder"])
    async with _client() as c:
        r_deny = await c.get("/api/v1/binder/entities", headers=_H(no_tab))
        r_allow = await c.get("/api/v1/binder/entities", headers=_H(with_tab))
    assert r_deny.status_code == 403
    assert r_allow.status_code == 200


# ── Audit (Part 5.1: every mutation audited) ──────────────────────────────────
async def test_mutations_are_audited():
    tok = await _owner_token()
    tid = await _tid()
    async with _client() as c:
        eid = (await c.post("/api/v1/binder/entities", headers=_H(tok),
                            json={"legal_name": "Audit Trail, LLC"})).json()["id"]
        await c.patch(f"/api/v1/binder/entities/{eid}", headers=_H(tok), json={"nickname": "AT"})
        await c.post(f"/api/v1/binder/entities/{eid}/deactivate", headers=_H(tok))
    async with SessionLocal() as s:
        actions = {a.action for a in (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == tid, AuditLog.target_id == eid))).scalars().all()}
    assert {"binder.entity_created", "binder.entity_updated", "binder.entity_deactivated"} <= actions


# ── Tenant isolation (service-level query scoping) ────────────────────────────
async def test_entities_are_tenant_scoped():
    """An entity under another tenant is invisible to this one, and cannot be edited."""
    springb = await _tid()
    async with SessionLocal() as s:
        other = Tenant(slug="othertenant", name="Other")
        s.add(other)
        await s.flush()
        ou = User(tenant_id=other.id, email="o@other.com", name="o", password_hash=hash_pw("x"),
                  role="owner", status="active")
        s.add(ou)
        oe = LegalEntity(tenant_id=other.id, legal_name="Foreign Co, LLC")
        s.add(oe)
        await s.commit()
        other_eid = oe.id

    async with SessionLocal() as s:
        listing = await binder.list_entities(s, springb)
        assert all(e["legal_name"] != "Foreign Co, LLC" for e in listing["entities"])
        # A springb actor cannot reach the other tenant's entity.
        springb_owner = (await s.execute(select(User).where(
            User.tenant_id == springb, User.role == "owner"))).scalars().first()
        got = await binder.update_entity(s, springb, springb_owner, other_eid, {"nickname": "hax"})
        assert got is None
