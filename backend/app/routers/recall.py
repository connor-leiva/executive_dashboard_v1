"""Recall.ai status callback — PUBLIC + secret-gated, like the Binder ingest channel.

Recall posts here whenever a bot changes state. That is the only way we learn a bot sat in
a waiting room and was never admitted, which is the failure mode the recorded-vs-booked
report exists to catch.

Recall signs each delivery with the workspace Verification Secret (whsec_...), so we verify
the signature rather than compare a shared header. 404s when the secret isn't configured, so
an unconfigured deploy exposes nothing. Always answers 200 for a well-formed, authentic post
— a webhook that 500s gets retried, and an unknown bot id is not worth retrying (it is
expected for the bots created by scripts/recall_bots.py).
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..services import recall

router = APIRouter(tags=["recall"])


def _dig(d: dict, *path, default=None):
    for k in path:
        d = (d or {}).get(k) if isinstance(d, dict) else None
    return d if d not in (None, "") else default


@router.post("/webhooks/recall")
async def recall_status(request: Request, s: AsyncSession = Depends(get_session)):
    if not settings.RECALL_WEBHOOK_SECRET:
        raise HTTPException(404, "Not found")
    raw = await request.body()
    if not recall.verify_signature(settings.RECALL_WEBHOOK_SECRET, request.headers, raw):
        # 401, not 404: the endpoint exists and Recall should surface the failure in its logs
        # rather than quietly retrying against what looks like a missing route.
        raise HTTPException(401, "Bad signature")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return {"ok": False, "reason": "not json"}

    # Recall has shipped more than one envelope shape over the years, and the payload is not
    # ours to control. Read defensively rather than pinning one layout.
    bot_id = (_dig(body, "data", "bot", "id") or _dig(body, "data", "bot_id")
              or _dig(body, "bot_id") or _dig(body, "data", "id"))
    status = (_dig(body, "data", "status", "code") or _dig(body, "data", "status")
              or _dig(body, "event") or "")
    media = (_dig(body, "data", "recording", "url") or _dig(body, "data", "video_url")
             or _dig(body, "data", "media_url"))
    if not bot_id or not isinstance(status, str):
        return {"ok": False, "reason": "no bot id or status in payload"}

    known = await recall.apply_bot_status(s, str(bot_id), status, media)
    if not known:
        # The backfill bots were created by scripts/recall_bots.py and aren't linked to a
        # SalesCall row, so this is expected for a while. Log, don't fail.
        print(f"[recall] status {status!r} for unknown bot {bot_id}", flush=True)
    return {"ok": True, "matched": known}
