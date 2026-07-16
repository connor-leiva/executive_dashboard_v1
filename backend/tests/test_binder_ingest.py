"""Acumyn Binder — Step 3 ingestion tests (SPEC Part 2).

The upload channel end to end: a document is stored, hashed, deduped, queued for extraction
(never producing an obligation), and observable via a SyncRun on batches. Covers validation,
tab gating, the audit trail, storage round-trip + path-traversal guard, and tenant scoping.
"""
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func

from app.main import app
from app.seed import seed
from app.db import SessionLocal
from app.config import settings
from app.models import Tenant, User, LegalEntity, BinderDocument, SyncRun, AuditLog, Obligation
from app.security import make_token, hash_pw
from app.services import binder_ingest, binder_storage

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded(tmp_path_factory):
    # Point blob storage at a throwaway dir so nothing lands in the repo.
    settings.BINDER_STORAGE_BUCKET = str(tmp_path_factory.mktemp("binder_store"))
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token):
    return {"Authorization": f"Bearer {token}"}


async def _tid():
    async with SessionLocal() as s:
        return (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id


async def _owner():
    async with SessionLocal() as s:
        return (await s.execute(select(User).where(
            User.email == "spring@springb.com"))).scalar_one()


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


def _upload(c, tok, name, data, **form):
    return c.post("/api/v1/binder/documents", headers=_H(tok),
                  files={"file": (name, data, "application/pdf")}, data=form)


# ── Upload ────────────────────────────────────────────────────────────────────
async def test_upload_stores_hashes_and_queues():
    tok = await _owner_token()
    tid = await _tid()
    data = b"%PDF-1.4 articles of organization\n" + b"x" * 500
    async with _client() as c:
        r = await _upload(c, tok, "Articles_of_Org.pdf", data, category="formation")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["uploaded_via"] == "upload"
    assert body["extraction_pending"] is True and body["deduped"] is False
    assert body["content_hash"] == binder_storage.content_hash(data)
    # The bytes are actually retrievable from storage under the tenant/doc key.
    async with SessionLocal() as s:
        doc = (await s.execute(select(BinderDocument).where(
            BinderDocument.id == body["id"]))).scalar_one()
    assert doc.storage_ref.startswith(f"{tid}/{doc.id}/")
    assert binder_storage.read(doc.storage_ref) == data


async def test_upload_dedups_identical_bytes():
    tok = await _owner_token()
    tid = await _tid()
    data = b"duplicate-policy-bytes-" + b"y" * 300
    async with _client() as c:
        first = (await _upload(c, tok, "policy.pdf", data)).json()
        second = (await _upload(c, tok, "policy-again.pdf", data)).json()
    assert first["deduped"] is False and second["deduped"] is True
    assert first["id"] == second["id"]
    async with SessionLocal() as s:
        n = (await s.execute(select(func.count(BinderDocument.id)).where(
            BinderDocument.tenant_id == tid,
            BinderDocument.content_hash == binder_storage.content_hash(data)))).scalar_one()
    assert n == 1


async def test_empty_and_bad_category_rejected():
    tok = await _owner_token()
    async with _client() as c:
        empty = await _upload(c, tok, "empty.pdf", b"")
        badcat = await _upload(c, tok, "x.pdf", b"data-here", category="nonsense")
    assert empty.status_code == 400
    assert badcat.status_code == 400 and "category" in badcat.json()["detail"]


async def test_entity_hint_must_belong_to_tenant():
    import uuid
    tok = await _owner_token()
    async with _client() as c:
        r = await _upload(c, tok, "x.pdf", b"linked-doc-bytes", entity_id=str(uuid.uuid4()))
    assert r.status_code == 400 and "entity_id" in r.json()["detail"]


async def test_entity_hint_links_document():
    tok = await _owner_token()
    tid = await _tid()
    async with SessionLocal() as s:
        ent = LegalEntity(tenant_id=tid, legal_name="Doc Link Co, LLC")
        s.add(ent)
        await s.commit()
        eid = ent.id
    async with _client() as c:
        r = await _upload(c, tok, "linked.pdf", b"linked-entity-doc", entity_id=str(eid))
    assert r.status_code == 200 and r.json()["entity_id"] == str(eid)


# ── Extraction never produces an obligation at ingest (invariant 1) ───────────
async def test_ingest_creates_no_obligation():
    tok = await _owner_token()
    tid = await _tid()
    async with _client() as c:
        await _upload(c, tok, "should_not_track.pdf", b"boi-report-looking-bytes")
    async with SessionLocal() as s:
        n = (await s.execute(select(func.count(Obligation.id)).where(
            Obligation.tenant_id == tid))).scalar_one()
    assert n == 0


# ── Permissions + audit ───────────────────────────────────────────────────────
async def test_upload_requires_binder_tab():
    no_tab = await _member("nodocs@springb.com", ["forum"])
    async with _client() as c:
        r = await _upload(c, no_tab, "x.pdf", b"nope")
    assert r.status_code == 403


async def test_upload_is_audited():
    tok = await _owner_token()
    tid = await _tid()
    async with _client() as c:
        did = (await _upload(c, tok, "audited.pdf", b"audit-this-doc")).json()["id"]
    async with SessionLocal() as s:
        row = (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == tid, AuditLog.target_id == did,
            AuditLog.action == "binder.document_uploaded"))).scalar_one_or_none()
    assert row is not None


# ── Batch → SyncRun (observability) ───────────────────────────────────────────
async def test_batch_records_sync_run_and_dedups():
    tid = await _tid()
    owner = await _owner()
    files = [
        {"filename": "a.pdf", "data": b"batch-doc-a"},
        {"filename": "b.pdf", "data": b"batch-doc-b"},
        {"filename": "a-dup.pdf", "data": b"batch-doc-a"},   # same bytes as a.pdf
    ]
    async with SessionLocal() as s:
        res = await binder_ingest.ingest_batch(s, tid, owner, files, uploaded_via="folder")
    assert res["created"] == 2 and res["deduped"] == 1 and res["failed"] == 0
    async with SessionLocal() as s:
        run = (await s.execute(select(SyncRun).where(SyncRun.id == res["sync_run_id"]))).scalar_one()
    assert run.provider == "binder_ingest" and run.status == "ok"
    assert run.stats["created"] == 2 and run.stats["deduped"] == 1


# ── Storage safety ────────────────────────────────────────────────────────────
def test_safe_filename_strips_path_separators():
    assert "/" not in binder_storage.safe_filename("../../etc/passwd")
    assert "\\" not in binder_storage.safe_filename("..\\..\\win.ini")
    assert binder_storage.safe_filename("") == "document"


def test_storage_ref_traversal_is_guarded():
    with pytest.raises(ValueError):
        binder_storage.read("../../../etc/passwd")


# ── Tenant isolation ──────────────────────────────────────────────────────────
async def test_documents_are_tenant_scoped():
    tid = await _tid()
    async with SessionLocal() as s:
        other = Tenant(slug="docsother", name="DocsOther")
        s.add(other)
        await s.flush()
        s.add(BinderDocument(tenant_id=other.id, filename="foreign.pdf",
                             content_hash="foreign-hash-xyz", uploaded_via="upload"))
        await s.commit()
    async with SessionLocal() as s:
        listing = await binder_ingest.list_documents(s, tid)
    assert all(d["filename"] != "foreign.pdf" for d in listing["documents"])
