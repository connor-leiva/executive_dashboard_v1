"""Acumyn Binder — Step 7 matrix + entity-binder tests (SPEC Part 6 / 8).

The read side that feeds the matrix: cells per (entity, kind), operating/holding grouping,
attention flags, the Books tax tie (federal/state tax -> in_progress until the linked
business's books close), and the per-entity binder (attributes + obligations + documents).
"""
import datetime as dt

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.seed import seed
from app.db import SessionLocal
from app.models import (Tenant, User, Business, LegalEntity, BinderDocument, Obligation,
                        ClosePeriod)
from app.security import make_token, hash_pw

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(t):
    return {"Authorization": f"Bearer {t}"}


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


async def _entity(tid, name, **kw):
    async with SessionLocal() as s:
        e = LegalEntity(tenant_id=tid, legal_name=name, entity_type="llc", jurisdiction="UT", **kw)
        s.add(e)
        await s.commit()
        return e.id


async def _obligation(tid, eid, kind, **kw):
    kw.setdefault("cadence", "annual")
    async with SessionLocal() as s:
        ob = Obligation(tenant_id=tid, entity_id=eid, kind=kind, confirmed_by=None, **kw)
        s.add(ob)
        await s.commit()
        return ob.id


def _find_entity(matrix, name):
    for g in matrix["groups"]:
        for e in g["entities"]:
            if e["name"] == name:
                return e, g["group"]
    return None, None


# ── Matrix ────────────────────────────────────────────────────────────────────
async def test_matrix_shape_and_none_cells():
    tid = await _tid()
    await _entity(tid, "Matrix Empty Co, LLC", entity_group="holding")
    tok = await _owner_token()
    async with _client() as c:
        r = await c.get("/api/v1/binder", headers=_H(tok))
    assert r.status_code == 200, r.text
    m = r.json()
    assert {"updated_at", "groups", "flags", "kinds"} <= set(m)
    assert [k["key"] for k in m["kinds"]][:2] == ["annual_report", "registered_agent"]
    e, group = _find_entity(m, "Matrix Empty Co, LLC")
    assert group == "holding"
    # An entity with no obligations shows every fixed kind as "none".
    assert all(e["cells"][k]["status"] == "none" for k in [x["key"] for x in m["kinds"]])


async def test_matrix_cell_statuses_and_flags():
    tid = await _tid()
    eid = await _entity(tid, "Matrix Status Co, LLC")
    await _obligation(tid, eid, "insurance", due_date=dt.date.today() - dt.timedelta(days=2),
                      lead_days=45)                                        # overdue
    await _obligation(tid, eid, "annual_report", due_date=dt.date.today() + dt.timedelta(days=10),
                      lead_days=45)                                        # due_soon
    await _obligation(tid, eid, "boi", due_date=dt.date.today() + dt.timedelta(days=400),
                      cadence="one_time", lead_days=45)                    # current
    tok = await _owner_token()
    async with _client() as c:
        m = (await c.get("/api/v1/binder", headers=_H(tok))).json()
    e, _ = _find_entity(m, "Matrix Status Co, LLC")
    assert e["cells"]["insurance"]["status"] == "overdue"
    assert e["cells"]["annual_report"]["status"] == "due_soon" and e["cells"]["annual_report"]["label"] == "10d"
    assert e["cells"]["boi"]["status"] == "current"
    # This entity contributes to the attention list.
    assert any(a["name"] == "Matrix Status Co, LLC" and a["worst"] == "overdue" for a in m["flags"]["attention"])
    assert m["flags"]["overdue"] >= 1 and m["flags"]["due_soon"] >= 1


async def test_matrix_not_applicable_cell():
    tid = await _tid()
    eid = await _entity(tid, "Matrix NA Co, LLC", entity_group="holding")
    await _obligation(tid, eid, "annual_report", applicable=False, lead_days=45)
    tok = await _owner_token()
    async with _client() as c:
        m = (await c.get("/api/v1/binder", headers=_H(tok))).json()
    e, _ = _find_entity(m, "Matrix NA Co, LLC")
    assert e["cells"]["annual_report"] == {"status": "not_applicable", "label": "n/a"}


# ── Books tax tie (Part 6 #1) ────────────────────────────────────────────────
async def test_books_tie_federal_tax_in_progress_until_close():
    tid = await _tid()
    async with SessionLocal() as s:
        ulrg = (await s.execute(select(Business).where(Business.key == "ulrg"))).scalar_one()
        ulrg_id = ulrg.id
    eid = await _entity(tid, "Books Tie Co, LLC", business_id=ulrg_id)
    await _obligation(tid, eid, "federal_tax", due_date=dt.date.today() + dt.timedelta(days=200), lead_days=45)
    tok = await _owner_token()
    async with _client() as c:
        m1 = (await c.get("/api/v1/binder", headers=_H(tok))).json()
    e1, _ = _find_entity(m1, "Books Tie Co, LLC")
    # ulrg has a QBO integration and no closed period this year -> waiting on Books close.
    assert e1["cells"]["federal_tax"]["status"] == "in_progress"

    # Close this year's books for ulrg -> the tie releases, date math applies.
    async with SessionLocal() as s:
        s.add(ClosePeriod(tenant_id=tid, business_id=ulrg_id,
                          period=dt.date(dt.date.today().year, 1, 1), status="closed"))
        await s.commit()
    async with _client() as c:
        m2 = (await c.get("/api/v1/binder", headers=_H(tok))).json()
    e2, _ = _find_entity(m2, "Books Tie Co, LLC")
    assert e2["cells"]["federal_tax"]["status"] == "current"   # due in 200d, books closed


# ── Entity binder detail ──────────────────────────────────────────────────────
async def test_entity_binder_detail():
    tid = await _tid()
    eid = await _entity(tid, "Detail Co, LLC", nickname="Detail", ownership="100%", ein="87-4123456")
    async with SessionLocal() as s:
        doc = BinderDocument(tenant_id=tid, entity_id=eid, filename="EO_policy.pdf",
                             content_hash="detail-hash-1", category="insurance", uploaded_via="upload")
        s.add(doc)
        await s.flush()
        s.add(Obligation(tenant_id=tid, entity_id=eid, kind="insurance",
                         due_date=dt.date.today() + dt.timedelta(days=20), cadence="annual",
                         source_document_id=doc.id, confirmed_by=None, lead_days=45))
        s.add(BinderDocument(tenant_id=tid, entity_id=eid, filename="articles.pdf",
                             content_hash="detail-hash-2", category="formation", uploaded_via="upload"))
        await s.commit()
    tok = await _owner_token()
    async with _client() as c:
        r = await c.get(f"/api/v1/binder/entity/{eid}", headers=_H(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entity"]["name"] == "Detail Co, LLC" and body["entity"]["ein_masked"] == "87-41•••••"
    ins = [o for o in body["obligations"] if o["kind"] == "insurance"]
    assert ins and ins[0]["status"] == "due_soon" and ins[0]["source_document"] == "EO_policy.pdf"
    cats = {d["category_key"] for d in body["documents"]}
    assert {"insurance", "formation"} <= cats
    # Formation is ordered before insurance (CATEGORY_ORDER).
    assert body["documents"][0]["category_key"] == "formation"


async def test_entity_binder_404():
    import uuid
    tok = await _owner_token()
    async with _client() as c:
        r = await c.get(f"/api/v1/binder/entity/{uuid.uuid4()}", headers=_H(tok))
    assert r.status_code == 404


async def test_matrix_requires_binder_tab():
    no_tab = await _member("nomatrix@springb.com", ["forum"])
    async with _client() as c:
        r = await c.get("/api/v1/binder", headers=_H(no_tab))
    assert r.status_code == 403
