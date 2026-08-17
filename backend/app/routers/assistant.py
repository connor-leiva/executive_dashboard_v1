import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..deps import current_user, verified_scopes
from ..models import User
from ..services import assistant

router = APIRouter(tags=["assistant"])
log = logging.getLogger("app")


class AskRequest(BaseModel):
    question: str
    period: str = "mtd"
    history: list[dict] | None = None


@router.get("/assistant/status")
async def status(user: User = Depends(current_user)):
    """So the frontend can show/hide the Ask panel without exposing the key."""
    return {"enabled": assistant.enabled(),
            "model": settings.ASSISTANT_MODEL if assistant.enabled() else None}


@router.post("/assistant/ask")
async def ask(body: AskRequest, user: User = Depends(current_user), s: AsyncSession = Depends(get_session),
              x_step_up: str | None = Header(default=None)):
    q = (body.question or "").strip()
    if not q:
        raise HTTPException(400, "Ask a question first.")
    if not assistant.enabled():
        raise HTTPException(503, "The assistant isn't configured. Set ANTHROPIC_API_KEY on the server to enable it.")
    try:
        # Sections behind a second factor are withheld unless this request proves it too.
        return await assistant.ask(s, user, q, body.history, body.period,
                                   step_up=verified_scopes(x_step_up, user))
    except Exception as e:  # bad key, rate limit, model error — keep it legible
        log.exception("assistant.ask failed")
        raise HTTPException(502, f"The assistant couldn't answer right now ({type(e).__name__}). Check the server's ANTHROPIC_API_KEY and model.")
