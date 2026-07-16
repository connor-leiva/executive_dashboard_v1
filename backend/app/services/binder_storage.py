"""Acumyn Binder — document blob storage (SPEC-binder-module Part 2).

V1 is a simple filesystem store keyed by ``{tenant_id}/{document_id}/{filename}``, which is
all single-tenant Spring needs. In prod ``BINDER_STORAGE_BUCKET`` points at a Railway volume;
unset (dev/test) it falls back to a temp directory so nothing lands in the repo.

The interface (store / read / delete by an opaque ``storage_ref``) is deliberately narrow so
an object bucket can replace the filesystem later without touching callers.

SECURITY (Part 14, deferred): encryption at rest, per-tenant isolation guarantees, and access
audit trails are OUT OF SCOPE for v1 and become mission-critical at multi-tenant. The data
model already isolates by ``tenant_id`` and the key is tenant-prefixed, so nothing here makes
that harder to add — but do not treat this store as hardened. Path traversal IS guarded below
(a crafted filename can never escape the tenant/document prefix).
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile

from ..config import settings

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def content_hash(data: bytes) -> str:
    """sha256 hex — the dedup key (BinderDocument.content_hash)."""
    return hashlib.sha256(data).hexdigest()


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


def store(tenant_id, document_id, filename: str, data: bytes) -> str:
    """Write bytes; return the storage_ref to persist on the BinderDocument."""
    ref = storage_key(tenant_id, document_id, filename)
    path = _resolve(ref)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return ref


def read(storage_ref: str) -> bytes:
    with open(_resolve(storage_ref), "rb") as f:
        return f.read()


def exists(storage_ref: str) -> bool:
    try:
        return os.path.exists(_resolve(storage_ref))
    except ValueError:
        return False


def delete(storage_ref: str) -> None:
    """Best-effort removal of the blob (and its now-empty document dir)."""
    try:
        path = _resolve(storage_ref)
    except ValueError:
        return
    try:
        os.remove(path)
        os.rmdir(os.path.dirname(path))   # remove the per-document dir if empty
    except OSError:
        pass
