"""AI Employees — media library (Stage 1). Summer's b-roll / stock / event-photo repository.

Blobs live in object storage (R2 in prod, filesystem in dev) via the shared `binder_storage`
layer; `AIMediaAsset` is the searchable catalog. The catalog metadata (kind, title, description,
tags) is what a text model references — a later stage auto-captions images and lets the cascade's
carousel/reel steps select assets by them. Keys are tenant-prefixed under an `ai-media/` segment
so media and Binder documents coexist in one bucket without collision.
"""
from __future__ import annotations

from ..models import AIMediaAsset
from . import binder_storage

KINDS = ("broll", "stock", "event", "logo", "other")


def _ref(tenant_id, asset_id, filename: str) -> str:
    return f"{tenant_id}/ai-media/{asset_id}/{binder_storage.safe_filename(filename)}"


async def add_asset(s, tenant_id, employee_id, *, filename: str, content_type: str, data: bytes,
                    kind: str = "stock", title: str = "", description: str | None = None,
                    tags: list | None = None, uploaded_by=None) -> AIMediaAsset:
    """Persist the blob + a catalog row. Row is flushed first for its id, which keys the blob."""
    asset = AIMediaAsset(
        tenant_id=tenant_id, employee_id=employee_id,
        kind=kind if kind in KINDS else "other", title=(title or "")[:160],
        description=description or None, tags=tags or [],
        filename=binder_storage.safe_filename(filename),
        content_type=content_type or "application/octet-stream",
        size_bytes=len(data), uploaded_by=uploaded_by, storage_ref="")
    s.add(asset)
    await s.flush()
    asset.storage_ref = binder_storage.put(_ref(tenant_id, asset.id, filename), data, content_type)
    return asset


async def delete_asset(s, asset: AIMediaAsset) -> None:
    binder_storage.delete(asset.storage_ref)
    await s.delete(asset)


def asset_out(a: AIMediaAsset, token: str | None = None) -> dict:
    return {
        "id": str(a.id), "kind": a.kind, "title": a.title, "description": a.description,
        "tags": a.tags or [], "filename": a.filename, "content_type": a.content_type,
        "size_bytes": a.size_bytes, "is_image": (a.content_type or "").startswith("image/"),
        "created_at": a.created_at.isoformat() if a.created_at else None,
        # a relative URL the browser can load directly (token-gated); the client prepends API base
        "url": f"/ai/media/{a.id}/file?t={token}" if token else None,
    }
