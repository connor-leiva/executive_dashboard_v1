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
from .conftest import binder_headers

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded(tmp_path_factory):
    # Point blob storage at a throwaway dir so nothing lands in the repo.
    settings.BINDER_STORAGE_BUCKET = str(tmp_path_factory.mktemp("binder_store"))
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token):
    return binder_headers(token)          # auth + the Binder step-up grant


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


async def test_ingested_document_is_pending_extraction():
    """A freshly ingested doc must be visible to the extraction queue (extracted IS NULL).
    Guards the JSONType none_as_null=True fix: SQLAlchemy's JSON stores Python None as the
    JSON string 'null' by default, so col.is_(None) would never match and the worker would
    silently pick up nothing."""
    tok = await _owner_token()
    tid = await _tid()
    async with _client() as c:
        did = (await _upload(c, tok, "pending_check.pdf", b"pending-extraction-bytes")).json()["id"]
    async with SessionLocal() as s:
        pending = {str(d.id) for d in (await s.execute(select(BinderDocument).where(
            BinderDocument.tenant_id == tid, BinderDocument.extracted.is_(None)))).scalars().all()}
    assert did in pending


async def test_dedup_relinks_to_the_upload_entity():
    """Re-uploading the same file under a different entity dedups the bytes but re-links the
    document to the entity it was uploaded under (so it doesn't silently 'disappear')."""
    from app.models import LegalEntity
    tok = await _owner_token()
    tid = await _tid()
    async with SessionLocal() as s:
        a = LegalEntity(tenant_id=tid, legal_name="Relink A, LLC")
        b = LegalEntity(tenant_id=tid, legal_name="Relink B, LLC")
        s.add_all([a, b])
        await s.commit()
        aid, bid = a.id, b.id
    data = b"relink-test-bytes-unique-xyz"
    async with _client() as c:
        r1 = await _upload(c, tok, "relink.pdf", data, entity_id=str(aid))
        r2 = await _upload(c, tok, "relink.pdf", data, entity_id=str(bid))
    assert r1.json()["deduped"] is False and r2.json()["deduped"] is True
    async with SessionLocal() as s:
        doc = (await s.execute(select(BinderDocument).where(
            BinderDocument.tenant_id == tid,
            BinderDocument.content_hash == binder_storage.content_hash(data)))).scalar_one()
    assert doc.entity_id == bid          # re-linked to the second upload's entity


async def test_dedup_reheals_missing_blob_and_requeues():
    """Re-uploading a doc whose blob evaporated (ephemeral storage across a redeploy) re-stores
    the bytes and re-queues extraction when the prior pass produced nothing — so a re-upload
    RECOVERS a doc that filed empty instead of dedup'ing back to the same dead, blob-less row."""
    tok = await _owner_token()
    tid = await _tid()
    data = b"reheal-test-bytes-" + b"z" * 200
    async with _client() as c:
        did = (await _upload(c, tok, "reheal.pdf", data)).json()["id"]
    # Simulate the worker having filed it empty (extracted set, no proposals) AND the blob going
    # missing after a redeploy to fresh ephemeral storage.
    async with SessionLocal() as s:
        doc = (await s.execute(select(BinderDocument).where(BinderDocument.id == did))).scalar_one()
        binder_storage.delete(doc.storage_ref)
        doc.extracted = {"parsed": {}, "match": {"entity_id": None}}
        await s.commit()
        assert not binder_storage.exists(doc.storage_ref)
    async with _client() as c:
        r = await _upload(c, tok, "reheal.pdf", data)
    assert r.json()["deduped"] is True
    async with SessionLocal() as s:
        doc = (await s.execute(select(BinderDocument).where(BinderDocument.id == did))).scalar_one()
    assert binder_storage.exists(doc.storage_ref)         # blob re-stored from the bytes in hand
    assert doc.extracted is None                          # re-queued for a fresh extraction pass
    assert binder_storage.read(doc.storage_ref) == data


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


def test_r2_backend_roundtrip(monkeypatch):
    """With the four R2_* settings present, store/read/exists/delete route through the
    object-storage client instead of the filesystem. A fake S3 client keeps it offline."""
    import io
    from app.services import binder_storage as bs
    blobs = {}

    class FakeR2:
        def put_object(self, Bucket, Key, Body, **kw): blobs[(Bucket, Key)] = Body
        def get_object(self, Bucket, Key):
            if (Bucket, Key) not in blobs:
                raise KeyError("NoSuchKey")
            return {"Body": io.BytesIO(blobs[(Bucket, Key)])}
        def head_object(self, Bucket, Key):
            if (Bucket, Key) not in blobs:
                raise KeyError("404")
            return {}
        def delete_object(self, Bucket, Key): blobs.pop((Bucket, Key), None)

    monkeypatch.setattr(settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(settings, "R2_BUCKET", "acumyn-binder")
    monkeypatch.setattr(settings, "R2_ACCESS_KEY_ID", "key")
    monkeypatch.setattr(settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(bs, "_r2_client", lambda: FakeR2())

    ref = bs.store("tenant-x", "doc-y", "policy.pdf", b"pdf-bytes")
    assert ref == "tenant-x/doc-y/policy.pdf"
    assert ("acumyn-binder", ref) in blobs          # went to the bucket, not the local disk
    assert bs.exists(ref) is True and bs.read(ref) == b"pdf-bytes"
    assert bs.exists("tenant-x/doc-y/missing.pdf") is False
    bs.delete(ref)
    assert bs.exists(ref) is False


# ── Additional ingest channels (Step 9) ──────────────────────────────────────
async def test_bulk_batch_upload():
    tok = await _owner_token()
    async with _client() as c:
        r = await c.post("/api/v1/binder/documents/batch", headers=_H(tok),
                         files=[("files", ("b1.pdf", b"batch file one", "application/pdf")),
                                ("files", ("b2.pdf", b"batch file two", "application/pdf")),
                                ("files", ("b3.pdf", b"batch file three", "application/pdf"))])
    assert r.status_code == 200 and r.json()["created"] == 3


async def test_email_webhook_disabled_without_secret(monkeypatch):
    monkeypatch.setattr(settings, "BINDER_INGEST_SECRET", "")     # channel off
    async with _client() as c:
        r = await c.post("/api/v1/binder/ingest/email",
                         data={"to": "binder@springb.acumyn.io"},
                         files=[("files", ("x.pdf", b"data", "application/pdf"))],
                         headers={"X-Ingest-Secret": "whatever"})
    assert r.status_code == 404


async def test_email_webhook_is_closed_until_the_tenant_opts_in(monkeypatch):
    """The secret authenticates the email PROVIDER, not the sender — anyone can email
    binder@{slug}.<domain> and the provider forwards it here with the secret attached. So one
    platform-wide secret plus a caller-named tenant let a stranger push documents into any
    tenant's Binder. The channel is now opt-in, and a closed tenant 404s like an unknown
    mailbox so probing cannot tell the two apart."""
    monkeypatch.setattr(settings, "BINDER_INGEST_SECRET", "s3cret")
    tid = await _tid()
    async with SessionLocal() as s:                       # explicitly NOT enabled
        t = await s.get(Tenant, tid)
        t.config = {k: v for k, v in (t.config or {}).items() if k != "binder_email_ingest"}
        await s.commit()
    async with _client() as c:
        r = await c.post("/api/v1/binder/ingest/email",
                         data={"to": "binder@springb.acumyn.io"},
                         files=[("files", ("x.pdf", b"unsolicited", "application/pdf"))],
                         headers={"X-Ingest-Secret": "s3cret"})
    assert r.status_code == 404, r.text
    async with _client() as c:                            # and a bad secret is 404 too
        r = await c.post("/api/v1/binder/ingest/email",
                         data={"to": "binder@springb.acumyn.io"},
                         files=[("files", ("x.pdf", b"unsolicited", "application/pdf"))],
                         headers={"X-Ingest-Secret": "wrong"})
    assert r.status_code == 404


async def test_email_webhook_ingests_with_secret(monkeypatch):
    monkeypatch.setattr(settings, "BINDER_INGEST_SECRET", "s3cret")
    tid = await _tid()
    async with SessionLocal() as s:
        t = await s.get(Tenant, tid)
        t.config = {**(t.config or {}), "binder_email_ingest": True}
        await s.commit()
    async with _client() as c:
        r = await c.post("/api/v1/binder/ingest/email",
                         data={"to": "binder@springb.acumyn.io"},
                         files=[("files", ("policy_email.pdf", b"emailed policy bytes", "application/pdf"))],
                         headers={"X-Ingest-Secret": "s3cret"})
    assert r.status_code == 200 and r.json()["created"] >= 1
    async with SessionLocal() as s:
        doc = (await s.execute(select(BinderDocument).where(
            BinderDocument.tenant_id == tid, BinderDocument.uploaded_via == "email"))).scalars().first()
    assert doc is not None


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
