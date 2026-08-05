"""AI Employees — media library (Stage 1). Summer's b-roll / stock / event-photo repository.

Blobs live in object storage (R2 in prod, filesystem in dev) via the shared `binder_storage`
layer; `AIMediaAsset` is the searchable catalog. The catalog metadata (kind, title, description,
tags) is what a text model references — a later stage auto-captions images and lets the cascade's
carousel/reel steps select assets by them. Keys are tenant-prefixed under an `ai-media/` segment
so media and Binder documents coexist in one bucket without collision.
"""
from __future__ import annotations

import base64

from sqlalchemy import select

from ..models import AIMediaAsset
from . import binder_storage

KINDS = ("broll", "stock", "event", "logo", "other")
SUPPORTED_IMAGE = {"image/jpeg", "image/png", "image/gif", "image/webp"}   # Claude vision inputs

_CAPTION_PROMPT = (
    "You are cataloging an image for a brand's social-media asset library. In 1-2 plain sentences "
    "describe what's actually in it — subjects, setting, action, mood, lighting, and whether it's "
    "portrait or landscape. Then give 4-8 short lowercase tags. No marketing language — just what a "
    "person would type to find this photo later. Return STRICT JSON only: "
    "{\"description\": str, \"tags\": [str, ...]}.")


async def caption_asset(data: bytes, content_type: str) -> dict | None:
    """Vision pass → {description, tags} for an image. None if captioning is off (no key/flag) or
    the type isn't a supported image. This is what lets a text model 'see' the library."""
    from . import ai_employees as eng          # late import — avoid a circular dependency
    if not eng.enabled() or content_type not in SUPPORTED_IMAGE:
        return None
    block = {"type": "image", "source": {"type": "base64", "media_type": content_type,
             "data": base64.b64encode(data).decode()}}
    try:
        resp = await eng._client().messages.create(
            model=eng._model(), max_tokens=400, thinking={"type": "disabled"},
            messages=[{"role": "user", "content": [block, {"type": "text", "text": _CAPTION_PROMPT}]}])
        obj = eng._parse_json(eng._text_of(resp)) or {}
    except Exception:
        return None
    desc = obj.get("description")
    if not desc:
        return None
    tags = obj.get("tags") if isinstance(obj.get("tags"), list) else []
    return {"description": str(desc)[:800], "tags": [str(t)[:40] for t in tags][:8]}


async def caption_and_store(s, asset: AIMediaAsset) -> bool:
    """(Re)caption one asset from its stored blob and persist. True if it got a description.
    Never overwrites tags the human already set."""
    try:
        data = binder_storage.read(asset.storage_ref)
    except Exception:
        return False
    cap = await caption_asset(data, asset.content_type)
    if not cap:
        return False
    asset.description = cap["description"]
    if cap["tags"] and not (asset.tags or []):
        asset.tags = cap["tags"]
    return True


async def media_catalog(s, employee_id, limit: int = 40) -> list[dict]:
    """The catalog the cascade's content steps select from (by description). Described assets
    are the useful ones; they're what a text model matches against."""
    rows = (await s.execute(select(AIMediaAsset).where(AIMediaAsset.employee_id == employee_id)
            .order_by(AIMediaAsset.created_at.desc()).limit(limit))).scalars().all()
    return [{"id": str(a.id), "kind": a.kind, "title": a.title,
             "description": a.description or "", "tags": a.tags or []}
            for a in rows if a.description]     # only described assets are selectable


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
