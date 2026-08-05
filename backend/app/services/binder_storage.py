"""Acumyn Binder — document blob storage (SPEC-binder-module Part 2).

Two interchangeable backends behind one narrow interface (store / read / delete / exists by an
opaque ``storage_ref`` keyed ``{tenant_id}/{document_id}/{filename}``):

- **Cloudflare R2** (S3-compatible object storage) when the four ``R2_*`` settings are present.
  This is the production backend: durable, shared across the api + worker services, and it
  survives redeploys (the local container disk does NOT, which is why filesystem storage lost
  files on every deploy). The same key becomes the object key in the bucket.
- **Filesystem** otherwise — a temp dir (or ``BINDER_STORAGE_BUCKET`` if set). This is the
  dev/test fallback so nothing external is needed to run locally.

SECURITY (Part 14, deferred): encryption at rest, per-tenant isolation guarantees, and access
audit trails are OUT OF SCOPE for v1 and become mission-critical at multi-tenant. The data
model already isolates by ``tenant_id`` and the key is tenant-prefixed, so nothing here makes
that harder to add — but do not treat this store as hardened. Path traversal IS guarded on the
filesystem backend (a crafted filename can never escape the tenant/document prefix); R2 keys
are our own, tenant-prefixed, and every read is tenant-checked at the route.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile

from ..config import settings

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_r2_obj = None


def content_hash(data: bytes) -> str:
    """sha256 hex — the dedup key (BinderDocument.content_hash)."""
    return hashlib.sha256(data).hexdigest()


# ── R2 (object storage) backend ───────────────────────────────────────────────
def _r2_enabled() -> bool:
    return bool(settings.R2_ACCOUNT_ID and settings.R2_BUCKET
                and settings.R2_ACCESS_KEY_ID and settings.R2_SECRET_ACCESS_KEY)


def _r2_client():
    """A cached boto3 S3 client pointed at the R2 endpoint (imported lazily so boto3 is only
    needed when R2 is actually configured)."""
    global _r2_obj
    if _r2_obj is None:
        import boto3
        from botocore.config import Config
        _r2_obj = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto",
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}))
    return _r2_obj


def _base_dir() -> str:
    base = settings.BINDER_STORAGE_BUCKET or os.path.join(tempfile.gettempdir(), "acumyn_binder_storage")
    os.makedirs(base, exist_ok=True)
    return base


def safe_filename(filename: str) -> str:
    """Collapse to a bare, safe basename. Never contains a path separator, so it cannot walk
    out of the {tenant}/{document} prefix."""
    name = os.path.basename(filename or "").strip() or "document"
    name = _SAFE.sub("_", name).strip("._") or "document"
    return name[:200]


def storage_key(tenant_id, document_id, filename: str) -> str:
    return f"{tenant_id}/{document_id}/{safe_filename(filename)}"


def _resolve(storage_ref: str) -> str:
    """Absolute path for a storage_ref, guarded so it can never escape the base dir."""
    base = os.path.realpath(_base_dir())
    full = os.path.realpath(os.path.join(base, storage_ref))
    if full != base and not full.startswith(base + os.sep):
        raise ValueError("storage_ref escapes the storage root")
    return full


def _content_type(filename: str) -> str:
    import mimetypes
    return mimetypes.guess_type(filename or "")[0] or "application/octet-stream"


def put(ref: str, data: bytes, content_type: str | None = None) -> str:
    """Write bytes at an opaque, caller-built ref (R2 object key or filesystem path). Shared by
    the Binder (documents) and AI Employees (Summer's media library) so both use the identical,
    production-proven R2/filesystem path. Returns the ref."""
    if _r2_enabled():
        _r2_client().put_object(Bucket=settings.R2_BUCKET, Key=ref, Body=data,
                                ContentType=content_type or "application/octet-stream")
        return ref
    path = _resolve(ref)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return ref


def store(tenant_id, document_id, filename: str, data: bytes) -> str:
    """Write bytes; return the storage_ref to persist on the BinderDocument."""
    return put(storage_key(tenant_id, document_id, filename), data, _content_type(filename))


def read(storage_ref: str) -> bytes:
    if _r2_enabled():
        obj = _r2_client().get_object(Bucket=settings.R2_BUCKET, Key=storage_ref)
        return obj["Body"].read()
    with open(_resolve(storage_ref), "rb") as f:
        return f.read()


def exists(storage_ref: str) -> bool:
    if _r2_enabled():
        try:
            _r2_client().head_object(Bucket=settings.R2_BUCKET, Key=storage_ref)
            return True
        except Exception:                 # 404 (missing) or any client error -> treat as absent
            return False
    try:
        return os.path.exists(_resolve(storage_ref))
    except ValueError:
        return False


def delete(storage_ref: str) -> None:
    """Best-effort removal of the blob (and, on the filesystem, its now-empty document dir)."""
    if _r2_enabled():
        try:
            _r2_client().delete_object(Bucket=settings.R2_BUCKET, Key=storage_ref)
        except Exception:
            pass
        return
    try:
        path = _resolve(storage_ref)
    except ValueError:
        return
    try:
        os.remove(path)
        os.rmdir(os.path.dirname(path))   # remove the per-document dir if empty
    except OSError:
        pass
