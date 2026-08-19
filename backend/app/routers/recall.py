"""Recall.ai status callback — PUBLIC + secret-gated, like the Binder ingest channel.

Recall posts here whenever a bot changes state. That is the only way we learn a bot sat in
a waiting room and was never admitted, which is the failure mode the recorded-vs-booked
report exists to catch.

404s unless RECALL_WEBHOOK_SECRET is set and matches, so an unconfigured deploy exposes
nothing. Always answers 200 for a well-formed post — a webhook that 500s gets retried, and
an unknown bot id is not an error worth retrying (it may be the one-off test bot).
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Request
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
async def recall_status(request: Request,
                        x_recall_secret: str = Header(default=""),
                        s: AsyncSession = Depends(get_session)):
    if not settings.RECALL_WEBHOOK_SECRET or x_recall_secret != settings.RECALL_WEBHOOK_SECRET:
        raise HTTPException(404, "Not found")
    body = await request.json()

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
