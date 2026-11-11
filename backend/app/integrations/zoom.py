"""Zoom Server-to-Server OAuth.

Token: POST https://zoom.us/oauth/token?grant_type=account_credentials&account_id=...
with Basic auth of client_id:client_secret. Cached for 55 minutes. The access token
itself lasts an hour; refreshing five minutes early avoids a call that dies mid-sync.

Participants come from the report endpoints first (duration, join, leave). A 404
or a meeting the report will not serve falls back to the past-meeting list.
"""
from __future__ import annotations

import base64
import datetime as dt
import time
from datetime import date

import httpx

from ..config import settings

_TOKEN: dict = {"value": None, "until": 0.0}
_API = "https://api.zoom.us/v2"


class ZoomError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def configured() -> bool:
    return bool(settings.ZOOM_ACCOUNT_ID and settings.ZOOM_CLIENT_ID and settings.ZOOM_CLIENT_SECRET)


def clear_token() -> None:
    _TOKEN["value"] = None
    _TOKEN["until"] = 0.0


async def access_token() -> str:
    now = time.time()
    if _TOKEN["value"] and now < _TOKEN["until"]:
        return _TOKEN["value"]
    if not configured():
        raise ZoomError("Zoom credentials are not set")
    basic = base64.b64encode(
        f"{settings.ZOOM_CLIENT_ID}:{settings.ZOOM_CLIENT_SECRET}".encode()).decode()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://zoom.us/oauth/token",
            params={"grant_type": "account_credentials", "account_id": settings.ZOOM_ACCOUNT_ID},
            headers={"Authorization": f"Basic {basic}"},
        )
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code != 200 or not body.get("access_token"):
        raise ZoomError(str(body.get("reason") or body.get("error") or f"HTTP {response.status_code}"),
                        response.status_code)
    _TOKEN["value"] = body["access_token"]
    # 55 minutes, or five minutes before Zoom's own expiry, whichever is sooner.
    ttl = min(55 * 60, max(60, int(body.get("expires_in") or 3600) - 300))
    _TOKEN["until"] = now + ttl
    return _TOKEN["value"]


async def _get(path: str, params: dict | None = None) -> dict:
    token = await access_token()
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.get(
            f"{_API}{path}", params=params or {},
            headers={"Authorization": f"Bearer {token}"},
        )
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code == 404:
        raise ZoomError(str(body.get("message") or "not found"), 404)
    if response.status_code != 200:
        raise ZoomError(str(body.get("message") or f"HTTP {response.status_code}"), response.status_code)
    return body


async def _pages(path: str, params: dict, key: str) -> list[dict]:
    out: list[dict] = []
    token = ""
    while True:
        query = {**params, "page_size": 300}
        if token:
            query["next_page_token"] = token
        body = await _get(path, query)
        out.extend(body.get(key) or [])
        token = body.get("next_page_token") or ""
        if not token:
            return out


def _chunks(start: date, end: date):
    """Report endpoints refuse a window longer than a month."""
    cursor = start
    while cursor <= end:
        month_end = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
        stop = min(end, month_end)
        yield cursor, stop
        cursor = stop + dt.timedelta(days=1)


async def users() -> list[dict]:
    return await _pages("/users", {"status": "active"}, "users")


def _unique(rows: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for row in rows:
        key = str(row.get("id") or row.get("uuid") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


async def _optional(path: str, params: dict, key: str) -> list[dict]:
    """A 400/404 means this user has nothing on that edge, not that the sync failed."""
    try:
        return await _pages(path, params, key)
    except ZoomError as err:
        if err.status in (400, 404):
            return []
        raise


async def meetings(user_id: str, start: date, end: date) -> list[dict]:
    rows = []
    for frm, to in _chunks(start, end):
        rows.extend(await _optional(
            f"/report/users/{user_id}/meetings",
            {"from": frm.isoformat(), "to": to.isoformat()}, "meetings"))
    # The report is sometimes empty for a meeting the user list still has.
    rows.extend(await _optional(
        f"/users/{user_id}/meetings", {"type": "previous_meetings"}, "meetings"))
    return _unique(rows)


async def webinars(user_id: str, start: date, end: date) -> list[dict]:
    rows = []
    for frm, to in _chunks(start, end):
        rows.extend(await _optional(
            f"/report/users/{user_id}/webinars",
            {"from": frm.isoformat(), "to": to.isoformat()}, "webinars"))
    rows.extend(await _optional(
        f"/users/{user_id}/webinars", {"type": "past"}, "webinars"))
    return _unique(rows)


async def participants(zoom_id: str, kind: str) -> tuple[list[dict], str]:
    """Report first. Past-meeting list when the report will not serve this one."""
    report = "/report/meetings" if kind == "meeting" else "/report/webinars"
    past = "/past_meetings" if kind == "meeting" else "/past_webinars"
    try:
        return await _pages(f"{report}/{zoom_id}/participants", {}, "participants"), "report"
    except ZoomError as err:
        if err.status not in (400, 404):
            raise
    return await _pages(f"{past}/{zoom_id}/participants", {}, "participants"), "past"
