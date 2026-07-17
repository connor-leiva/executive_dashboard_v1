"""Acumyn Binder — document ingestion (SPEC-binder-module Part 2).

Three channels land here (upload, forwarding address, bulk folder). Step 3 builds the
upload path; email/folder reuse the same core and arrive with later steps. Every channel
does the same thing: store the raw bytes, dedup on the sha256 content hash, create a
``BinderDocument``, and enqueue extraction. The document row itself IS the queue — the
extraction worker (Step 4) scans for ``extracted IS NULL`` — so "enqueue" is a marker plus
a log line for now; nothing writes obligations here.

Batches record a ``SyncRun`` (``provider="binder_ingest"``) so ingestion is observable the
same way the other syncs are.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import BinderDocument, LegalEntity, SyncRun
from .audit import audit
from . import binder_storage

log = logging.getLogger("app")

# The document categories the extractor classifies into (SPEC Part 3.1). At ingest time the
# category is unknown unless the caller passed one; it defaults to "other" until extraction
# (Step 4) proposes a real one.
CATEGORIES = {"formation", "insurance", "tax", "lease", "registered_agent", "estate", "other"}


def document_out(doc: BinderDocument, *, deduped: bool = False) -> dict:
    """Read payload for an ingested document."""
    return {
        "id": str(doc.id),
        "filename": doc.filename,
        "category": doc.category,
        "content_hash": doc.content_hash,
        "uploaded_via": doc.uploaded_via,
        "entity_id": str(doc.entity_id) if doc.entity_id else None,
        "extraction_pending": doc.extracted is None,
        "deduped": deduped,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


def enqueue_extraction(doc: BinderDocument) -> None:
    """Mark a document for extraction. The row (extracted IS NULL) is the queue the Step 4
    worker drains; this is a hook + log line until that worker exists. It must NEVER write an
    Obligation — extraction proposes only (invariant 1)."""
    log.info("binder_ingest: queued %s (%s) for extraction", doc.id, doc.filename)


async def list_documents(s: AsyncSession, tenant_id, entity_id=None) -> dict:
    """Ingested documents for a tenant (optionally one entity), newest first. Interim
    observability surface; the entity-detail document tree lands with a later step."""
    q = select(BinderDocument).where(BinderDocument.tenant_id == tenant_id)
    if entity_id is not None:
        q = q.where(BinderDocument.entity_id == entity_id)
    docs = (await s.execute(q.order_by(BinderDocument.created_at.desc()))).scalars().all()
    rows = [document_out(d) for d in docs]
    return {"documents": rows,
            "counts": {"total": len(rows),
                       "pending_extraction": sum(1 for r in rows if r["extraction_pending"])}}


async def _existing(s: AsyncSession, tenant_id, content_hash: str) -> BinderDocument | None:
    return (await s.execute(select(BinderDocument).where(
        BinderDocument.tenant_id == tenant_id,
        BinderDocument.content_hash == content_hash))).scalar_one_or_none()


async def _validate_entity(s: AsyncSession, tenant_id, entity_id) -> None:
    if entity_id is None:
        return
    ok = (await s.execute(select(LegalEntity.id).where(
        LegalEntity.tenant_id == tenant_id, LegalEntity.id == entity_id))).scalar_one_or_none()
    if ok is None:
        raise ValueError("entity_id does not match an entity in this tenant")


async def ingest_document(s: AsyncSession, tenant_id, user, *, filename: str, data: bytes,
                          uploaded_via: str = "upload", entity_id=None, category: str | None = None,
                          commit: bool = True) -> tuple[BinderDocument, bool]:
    """Core ingest: store bytes, dedup, create the row, enqueue extraction. Returns
    (document, created). On a content-hash collision the existing row is returned with
    created=False and nothing is re-stored. Does not commit when commit=False (batch callers
    own the transaction)."""
    if category is not None and category not in CATEGORIES:
        raise ValueError(f"category must be one of: {', '.join(sorted(CATEGORIES))}")
    await _validate_entity(s, tenant_id, entity_id)

    chash = binder_storage.content_hash(data)
    dup = await _existing(s, tenant_id, chash)
    if dup is not None:
        # Same bytes already stored (dedup). If the user is uploading it under a specific entity
        # (they're telling us it belongs there), re-link the existing doc to that entity so it
        # actually shows up — otherwise a re-upload to a new entity silently "disappears".
        if entity_id is not None and dup.entity_id != entity_id:
            dup.entity_id = entity_id
            if commit:
                await s.commit()          # batch callers commit once at the end
        return dup, False

    doc = BinderDocument(
        tenant_id=tenant_id, entity_id=entity_id, filename=(filename or "document")[:300],
        category=category or "other", content_hash=chash, uploaded_via=uploaded_via,
        uploaded_by=getattr(user, "id", None), extracted=None,
    )
    s.add(doc)
    await s.flush()                       # need doc.id for the storage key
    doc.storage_ref = binder_storage.store(tenant_id, doc.id, doc.filename, data)
    enqueue_extraction(doc)
    audit(s, tenant_id, getattr(user, "id", None), "binder.document_uploaded", "binder_document",
          doc.id, {"filename": doc.filename, "via": uploaded_via, "bytes": len(data)})
    if commit:
        await s.commit()
    return doc, True


async def ingest_batch(s: AsyncSession, tenant_id, user, files: list[dict],
                       uploaded_via: str = "folder") -> dict:
    """Ingest many files under one observable SyncRun. `files` is a list of
    {filename, data, entity_id?, category?}. Dedups within and across the batch."""
    run = SyncRun(tenant_id=tenant_id, provider="binder_ingest", status="running")
    s.add(run)
    await s.flush()
    created = deduped = failed = 0
    results = []
    for f in files:
        try:
            doc, was_new = await ingest_document(
                s, tenant_id, user, filename=f.get("filename", "document"), data=f["data"],
                uploaded_via=uploaded_via, entity_id=f.get("entity_id"),
                category=f.get("category"), commit=False)
            created += int(was_new)
            deduped += int(not was_new)
            results.append(document_out(doc, deduped=not was_new))
        except Exception as e:                       # one bad file never sinks the batch
            failed += 1
            results.append({"filename": f.get("filename"), "error": str(e)})
            log.warning("binder_ingest: failed %s: %s", f.get("filename"), e)
    run.status = "ok" if failed == 0 else "error"
    run.finished_at = dt.datetime.now(dt.timezone.utc)
    run.stats = {"files": len(files), "created": created, "deduped": deduped, "failed": failed}
    await s.commit()
    return {"created": created, "deduped": deduped, "failed": failed, "documents": results,
            "sync_run_id": str(run.id)}
