"""Transactional email via Resend.

httpx directly rather than the SDK: every other integration here does the same, and one more
dependency for a single POST is not worth it.

Two rules this module exists to enforce.

  NEVER RAISES. An invite is already committed by the time we try to email it. Letting a
  provider outage 500 the request would show the admin a failure for a user that WAS created,
  and their retry would hit a 409.

  NEVER THE ONLY COPY. Callers keep returning their link. Email is the convenience; the
  copy-paste path stays as the fallback for a bounce or a mistyped address.
"""
from __future__ import annotations

import logging

import httpx

from ..config import settings

log = logging.getLogger("app")
_API = "https://api.resend.com/emails"


def api_key() -> str:
    """The key, stripped.

    A pasted secret carries a trailing newline more often than anyone expects — through
    `railway variables --set`, through a copied line from a terminal, through a heredoc. The
    header it builds is then malformed and Resend answers "API key is invalid", which reads as a
    wrong key rather than a whitespace problem and sends you back to the dashboard to reissue a
    key that was fine. Stripping costs nothing and removes that whole class of afternoon.

    It also makes a whitespace-only value count as absent, which is what a human setting the
    variable to a space means.
    """
    return (settings.RESEND_API_KEY or "").strip()


async def _post(payload: dict, headers: dict) -> tuple[int, str]:
    """The network seam. Tests monkeypatch THIS, so no test ever reaches Resend."""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(_API, json=payload, headers=headers)
    return r.status_code, r.text


async def send(to: str | list[str], subject: str, html: str, text: str,
               reply_to: str | None = None, idempotency_key: str | None = None) -> bool:
    """Send one email. True if Resend accepted it. Never raises."""
    key = api_key()
    if not key:
        log.info("mail (no RESEND_API_KEY, not sent) -> %s | %s", to, subject)
        return False

    payload = {
        "from": settings.MAIL_FROM.strip(),
        "to": [to] if isinstance(to, str) else list(to),
        "subject": subject,
        "html": html,
        "text": text,          # both, always — text clients and spam scoring each want it
    }
    rt = reply_to or settings.MAIL_REPLY_TO
    if rt:
        payload["reply_to"] = rt

    headers = {"Authorization": f"Bearer {key}"}
    if idempotency_key:
        # Resend dedups on this, so a double-clicked "resend invite" sends once.
        headers["Idempotency-Key"] = idempotency_key

    try:
        status, body = await _post(payload, headers)
        if status >= 300:
            log.warning("mail failed %s -> %s | %s", status, to, body[:300])
            return False
        return True
    except Exception as e:                              # noqa: BLE001 - see module docstring
        log.warning("mail error -> %s | %s", to, e)
        return False
