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
from app.config import settings
from app.models import (Tenant, User, Business, LegalEntity, BinderDocument, Obligation,
                        ClosePeriod)
from app.security import make_token, hash_pw
from app.services import binder_storage, binder, binder_extract
from .conftest import binder_headers

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded(tmp_path_factory):
    # Point blob storage at a throwaway dir so the raw-preview route has real bytes to serve.
    settings.BINDER_STORAGE_BUCKET = str(tmp_path_factory.mktemp("binder_matrix_store"))
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(t):
    return binder_headers(t)          # auth + the Binder step-up grant


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
    assert e["cells"]["annual_report"] == {"status": "not_applicable", "label": "N/A"}


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


async def test_entity_binder_full_skeleton():
    """A fresh entity (no obligations, no documents) still returns the FULL shape: every matrix
    kind as a not-configured stub, and the whole document category tree with empty sections."""
    tid = await _tid()
    eid = await _entity(tid, "Skeleton Co, LLC")
    tok = await _owner_token()
    async with _client() as c:
        body = (await c.get(f"/api/v1/binder/entity/{eid}", headers=_H(tok))).json()
    assert [o["kind"] for o in body["obligations"]] == [
        "annual_report", "registered_agent", "insurance", "boi",
        "federal_tax", "state_tax", "estimated_payments"]
    assert all(o["configured"] is False and o["status"] == "none" and o["id"] is None
               for o in body["obligations"])
    assert [d["category_key"] for d in body["documents"]][:6] == [
        "formation", "registered_agent", "insurance", "tax", "lease", "estate"]
    assert all(d["items"] == [] for d in body["documents"])


async def test_entity_binder_404():
    import uuid
    tok = await _owner_token()
    async with _client() as c:
        r = await c.get(f"/api/v1/binder/entity/{uuid.uuid4()}", headers=_H(tok))
    assert r.status_code == 404


# ── Document preview (raw) ────────────────────────────────────────────────────
async def test_document_raw_streams_inline():
    tok = await _owner_token()
    data = b"%PDF-1.4 preview-bytes\n" + b"p" * 200
    async with _client() as c:
        did = (await c.post("/api/v1/binder/documents", headers=_H(tok),
                            files={"file": ("preview.pdf", data, "application/pdf")})).json()["id"]
        r = await c.get(f"/api/v1/binder/documents/{did}/raw", headers=_H(tok))
    assert r.status_code == 200 and r.content == data
    assert "inline" in r.headers["content-disposition"]
    assert r.headers["content-type"].startswith("application/pdf")


async def test_document_raw_404_unknown_and_tenant_scoped():
    import uuid
    tok = await _owner_token()
    async with SessionLocal() as s:                       # a doc that belongs to another tenant
        other = Tenant(slug="rawother", name="RawOther")
        s.add(other)
        await s.flush()
        d = BinderDocument(tenant_id=other.id, filename="foreign.pdf", content_hash="raw-foreign",
                           uploaded_via="upload")
        s.add(d)
        await s.commit()
        foreign_id = d.id
    async with _client() as c:
        unknown = await c.get(f"/api/v1/binder/documents/{uuid.uuid4()}/raw", headers=_H(tok))
        foreign = await c.get(f"/api/v1/binder/documents/{foreign_id}/raw", headers=_H(tok))
    assert unknown.status_code == 404 and foreign.status_code == 404


async def test_delete_document_removes_row_blob_and_detaches():
    """Deleting a document removes the row + blob, drops its proposals, and detaches it from any
    obligation it backed (the obligation stays, just loses its evidence link)."""
    tid = await _tid()
    eid = await _entity(tid, "Doc Delete Co, LLC")
    tok = await _owner_token()
    data = b"%PDF-1.4 to-be-deleted\n" + b"d" * 80
    async with _client() as c:
        did = (await c.post("/api/v1/binder/documents", headers=_H(tok),
                            files={"file": ("gone.pdf", data, "application/pdf")},
                            data={"entity_id": str(eid)})).json()["id"]
    async with SessionLocal() as s:                       # an obligation backed by this document
        doc = (await s.execute(select(BinderDocument).where(BinderDocument.id == did))).scalar_one()
        ref = doc.storage_ref
        s.add(Obligation(tenant_id=tid, entity_id=eid, kind="insurance", source_document_id=doc.id,
                         confirmed_by=None, lead_days=45))
        await s.commit()
    assert binder_storage.exists(ref)
    async with _client() as c:
        r = await c.delete(f"/api/v1/binder/documents/{did}", headers=_H(tok))
        again = await c.delete(f"/api/v1/binder/documents/{did}", headers=_H(tok))
    assert r.status_code == 200 and again.status_code == 404      # gone, second delete 404s
    assert not binder_storage.exists(ref)                         # blob removed
    async with SessionLocal() as s:
        assert (await s.execute(select(BinderDocument).where(
            BinderDocument.id == did))).scalar_one_or_none() is None
        ob = (await s.execute(select(Obligation).where(
            Obligation.entity_id == eid, Obligation.kind == "insurance"))).scalar_one()
    assert ob.source_document_id is None                          # obligation kept, evidence detached


async def test_document_raw_requires_binder_tab():
    import uuid
    no_tab = await _member("noraw@springb.com", ["forum"])
    async with _client() as c:
        r = await c.get(f"/api/v1/binder/documents/{uuid.uuid4()}/raw", headers=_H(no_tab))
    assert r.status_code == 403


# ── Manual obligation configuration (invariant 1: records a user) ─────────────
async def test_manual_obligation_configure_records_user_and_upserts():
    tid = await _tid()
    eid = await _entity(tid, "Manual Ob Co, LLC")
    tok = await _owner_token()
    async with _client() as c:
        r1 = await c.post(f"/api/v1/binder/entities/{eid}/obligations", headers=_H(tok),
                          json={"kind": "registered_agent", "due_date": "2026-09-30", "cadence": "annual"})
        assert r1.status_code == 200, r1.text
        oid = r1.json()["id"]
        assert r1.json()["confirmed_by"] is not None            # manual add still records a user
        r2 = await c.post(f"/api/v1/binder/entities/{eid}/obligations", headers=_H(tok),
                          json={"kind": "registered_agent", "applicable": False})
        assert r2.status_code == 200 and r2.json()["id"] == oid  # upsert, not a duplicate
    async with SessionLocal() as s:
        obs = (await s.execute(select(Obligation).where(
            Obligation.entity_id == eid, Obligation.kind == "registered_agent"))).scalars().all()
    assert len(obs) == 1 and obs[0].applicable is False and obs[0].confirmed_by is not None
    async with _client() as c:
        body = (await c.get(f"/api/v1/binder/entity/{eid}", headers=_H(tok))).json()
    ra = [o for o in body["obligations"] if o["kind"] == "registered_agent"][0]
    assert ra["configured"] is True and ra["applicable"] is False


async def test_manual_obligation_bad_kind_and_unknown_entity():
    import uuid
    tid = await _tid()
    eid = await _entity(tid, "Manual Bad Co, LLC")
    tok = await _owner_token()
    async with _client() as c:
        bad = await c.post(f"/api/v1/binder/entities/{eid}/obligations", headers=_H(tok),
                           json={"kind": "nonsense"})
        missing = await c.post(f"/api/v1/binder/entities/{uuid.uuid4()}/obligations", headers=_H(tok),
                               json={"kind": "insurance"})
    assert bad.status_code == 400 and "kind" in bad.json()["detail"]
    assert missing.status_code == 404


# ── Document lifecycle: Current vs Historical ────────────────────────────────
async def test_document_current_vs_historical_split():
    tid = await _tid()
    eid = await _entity(tid, "Lifecycle Co, LLC")
    TODAY = dt.date(2026, 7, 17)
    async with SessionLocal() as s:
        # insurance: one expired (exp 2020) -> historical; one current-year (filename 2026) -> current
        s.add(BinderDocument(tenant_id=tid, entity_id=eid, filename="COI 2020.pdf", content_hash="lc-1",
                             category="insurance", uploaded_via="upload",
                             extracted={"parsed": {"anchors": {"expiration_date": "2020-06-01"}, "printed_dates": []}}))
        s.add(BinderDocument(tenant_id=tid, entity_id=eid, filename="COI 2026.pdf", content_hash="lc-2",
                             category="insurance", uploaded_via="upload"))
        # tax: prior-year (2019) superseded -> historical; latest (2026) -> current
        s.add(BinderDocument(tenant_id=tid, entity_id=eid, filename="Tax Return (2019).pdf", content_hash="lc-3",
                             category="tax", uploaded_via="upload"))
        s.add(BinderDocument(tenant_id=tid, entity_id=eid, filename="Tax Return (2026).pdf", content_hash="lc-4",
                             category="tax", uploaded_via="upload"))
        # formation is permanent: a prior-year doc stays Current, not Historical
        s.add(BinderDocument(tenant_id=tid, entity_id=eid, filename="Articles (2015).pdf", content_hash="lc-5",
                             category="formation", uploaded_via="upload"))
        await s.commit()
        body = await binder.build_entity_binder(s, tid, eid, today=TODAY)
    bycat = {d["category_key"]: d for d in body["documents"]}
    assert [x["filename"] for x in bycat["insurance"]["current"]] == ["COI 2026.pdf"]
    assert [x["filename"] for x in bycat["insurance"]["historical"]] == ["COI 2020.pdf"]
    assert bycat["insurance"]["historical"][0]["expired"] is True
    assert [x["filename"] for x in bycat["tax"]["current"]] == ["Tax Return (2026).pdf"]
    assert [x["filename"] for x in bycat["tax"]["historical"]] == ["Tax Return (2019).pdf"]
    assert [x["filename"] for x in bycat["formation"]["current"]] == ["Articles (2015).pdf"]
    assert bycat["formation"]["historical"] == []


# ── Obligation "why" detail ───────────────────────────────────────────────────
async def test_obligation_detail_reason_and_related_docs():
    tid = await _tid()
    eid = await _entity(tid, "Detail Reason Co, LLC")
    TODAY = dt.date(2026, 7, 17)
    async with SessionLocal() as s:
        s.add(BinderDocument(tenant_id=tid, entity_id=eid, filename="EO (2024).pdf", content_hash="dr-1",
                             category="insurance", uploaded_via="upload",
                             extracted={"parsed": {"anchors": {"expiration_date": "2025-06-14"}, "printed_dates": []}}))
        s.add(Obligation(tenant_id=tid, entity_id=eid, kind="insurance", due_date=dt.date(2025, 6, 14),
                         cadence="annual", lead_days=45, confirmed_by=None))
        await s.commit()
        body = await binder.build_entity_binder(s, tid, eid, today=TODAY)
    ins = [o for o in body["obligations"] if o["kind"] == "insurance"][0]
    assert ins["status"] == "overdue" and "Overdue" in ins["status_reason"]
    assert any(d["filename"] == "EO (2024).pdf" and d["expired"] for d in ins["related_documents"])
    ar = [o for o in body["obligations"] if o["kind"] == "annual_report"][0]
    assert ar["configured"] is False and "Not configured" in ar["status_reason"] and ar["ai_summary"] is None


# ── On-demand AI explanation (network seam mocked) ────────────────────────────
async def test_explain_obligation_caches_summary(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key")
    async def fake(client, model, prompt):
        return "The E and O policy expired on Jun 14, 2025, so insurance is Overdue. Upload a current certificate."
    monkeypatch.setattr(binder_extract, "_explain_call", fake)
    monkeypatch.setattr(binder_extract, "_client", lambda: object())
    tid = await _tid()
    eid = await _entity(tid, "Explain Co, LLC")
    async with SessionLocal() as s:
        ob = Obligation(tenant_id=tid, entity_id=eid, kind="insurance", due_date=dt.date(2025, 6, 14),
                        cadence="annual", lead_days=45, confirmed_by=None)
        s.add(ob)
        await s.commit()
        oid = ob.id
    tok = await _owner_token()
    async with _client() as c:
        r = await c.post(f"/api/v1/binder/obligations/{oid}/explain", headers=_H(tok))
    assert r.status_code == 200, r.text
    assert "overdue" in r.json()["ai_summary"].lower()
    async with SessionLocal() as s:
        ob = (await s.execute(select(Obligation).where(Obligation.id == oid))).scalar_one()
    assert ob.ai_summary and ob.ai_summary_at is not None


async def test_explain_obligation_400_without_key(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")     # no AI key -> unavailable
    tid = await _tid()
    eid = await _entity(tid, "No Key Explain Co, LLC")
    async with SessionLocal() as s:
        ob = Obligation(tenant_id=tid, entity_id=eid, kind="boi", confirmed_by=None, lead_days=45)
        s.add(ob)
        await s.commit()
        oid = ob.id
    tok = await _owner_token()
    async with _client() as c:
        r = await c.post(f"/api/v1/binder/obligations/{oid}/explain", headers=_H(tok))
    assert r.status_code == 400


async def test_matrix_requires_binder_tab():
    no_tab = await _member("nomatrix@springb.com", ["forum"])
    async with _client() as c:
        r = await c.get("/api/v1/binder", headers=_H(no_tab))
    assert r.status_code == 403
