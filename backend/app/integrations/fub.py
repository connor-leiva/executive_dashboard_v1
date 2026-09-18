"""Follow Up Boss client.

REST, Basic auth: the API key as the username, empty password. Base https://api.followupboss.com/v1.

PAGED WITH FUB'S `next` CURSOR, NOT OFFSET. FUB asks for `next` everywhere and enforces it deep
into a result set. The offset walk this replaced took eight minutes a run on the first live
account, every thirty minutes, and read every field of every person to use four of them -- so
`fields` asks for only what the app stores.

RATE LIMITS. 125 requests per 10 seconds without a registered system key. A 429 comes back with
Retry-After; the page is retried after it rather than failing a sync halfway through a walk.

DATES ARE DATES. FUB sends ISO timestamps in UTC. Everything here returns `datetime`/`date`
objects, never the string: a string in a Date column is exactly what asyncpg refused on every
production run until this was rewritten, while the test suite -- which never ran the sync -- saw
nothing.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass

import httpx

from ..config import settings

# Installed by tests (an httpx.MockTransport). None means the real network.
TRANSPORT: httpx.AsyncBaseTransport | None = None

PAGE = 100
MAX_RETRIES = 5
# The wait between retries. A name of its own so a test can skip the waiting.
_sleep = asyncio.sleep

# Only what the app keeps. `name` is FUB's display name; first/last are the fallback when it is
# blank. No emails, phones or addresses: a follow-up row opens the person in FUB, where they are.
PERSON_FIELDS = ",".join((
    "id", "name", "firstName", "lastName", "stage", "source", "contacted", "assignedUserId",
    "created", "updated", "lastActivity",
))


@dataclass(frozen=True)
class FubCreds:
    """One tenant's Follow Up Boss account.

    This used to be a module-level tuple built from settings at IMPORT time, so every tenant
    shared one API key — and the key could not even be changed without a restart. Credentials
    now travel with the call.
    """
    api_key: str
    base_url: str = ""

    @property
    def auth(self) -> tuple[str, str]:
        # FUB uses Basic auth: the API key as the username, empty password.
        return (self.api_key, "")

    @property
    def base(self) -> str:
        return (self.base_url or settings.FUB_API_BASE).rstrip("/")


def client(creds: FubCreds) -> httpx.AsyncClient:
    """One connection for a whole walk, rather than a new client per page."""
    return httpx.AsyncClient(base_url=creds.base, auth=creds.auth, timeout=30,
                             transport=TRANSPORT)


async def get(c: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    """GET one page, waiting out a 429 rather than failing the walk it belongs to."""
    for attempt in range(MAX_RETRIES + 1):
        r = await c.get(path, params=params)
        if r.status_code == 429 and attempt < MAX_RETRIES:
            try:
                wait = float(r.headers.get("Retry-After") or 0)
            except ValueError:
                wait = 0
            await _sleep(min(max(wait, 1.0), 30.0))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("unreachable")  # pragma: no cover -- the loop returns or raises


async def pages(c: httpx.AsyncClient, path: str, key: str, params: dict | None = None,
                *, start: str | None = None, max_pages: int | None = None):
    """Yield (rows, next_token) page by page, following `_metadata.next`.

    `start` resumes a walk from a saved token; `max_pages` stops early, and the token yielded with
    the last page is where to resume. A token of None means the walk is complete.
    """
    base = {"limit": PAGE, **(params or {})}
    token, n = start, 0
    while True:
        query = {**base, "next": token} if token else base
        data = await get(c, path, query)
        rows = data.get(key) or []
        token = (data.get("_metadata") or {}).get("next") or None
        n += 1
        if not rows:
            token = None
        yield rows, token
        if token is None or (max_pages is not None and n >= max_pages):
            return


async def collect(creds: FubCreds, path: str, key: str, params: dict | None = None) -> list[dict]:
    out: list[dict] = []
    async with client(creds) as c:
        async for rows, _ in pages(c, path, key, params):
            out.extend(rows)
    return out


async def fub_users(creds: FubCreds) -> list[dict]:
    """Every user on the account. This stopped at the first 100 -- exactly 100 were stored for a
    team with more -- so nobody after them could ever be matched to a portal member."""
    return await collect(creds, "/users", "users")


async def fub_people(creds: FubCreds, **filters) -> list[dict]:
    """People, with only the fields the app keeps. `filters` are FUB's own query parameters."""
    return await collect(creds, "/people", "people", {"fields": PERSON_FIELDS, **filters})


# ── mapping ───────────────────────────────────────────────────────────────────────────────

def parse_ts(value) -> dt.datetime | None:
    """An FUB timestamp as an aware UTC datetime, or None."""
    if not value:
        return None
    try:
        d = dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def clip(value, length: int) -> str | None:
    """Text cut to its column. SQLite ignores a String(n) length and Postgres refuses the row."""
    if value is None:
        return None
    text = str(value).strip()
    return text[:length] if text else None


# Data contract this app needs (FUB fields -> these):
#   agents:  external_id, name, email, is_active
#   leads:   external_id, stage, agent_external_id, created_at_src (a date, in the workspace's zone)
def map_user(u: dict) -> dict:
    name = u.get("name") or f'{u.get("firstName") or ""} {u.get("lastName") or ""}'.strip()
    return {
        "external_id": str(u.get("id")),
        "name": clip(name, 200) or f"FUB user {u.get('id')}",
        "email": clip((u.get("email") or "").lower(), 255),
        "is_active": (u.get("status") or "Active").lower() == "active",
    }


def map_task(t: dict, tz: dt.tzinfo = dt.timezone.utc) -> dict:
    """An open task. `dueDate` is the day it is due; `dueDateTime`, when FUB sends one, the time.
    A task with only a time is placed on the workspace's day for that time."""
    due_at = parse_ts(t.get("dueDateTime"))
    due_on = None
    raw = str(t.get("dueDate") or "")[:10]
    if raw:
        try:
            due_on = dt.date.fromisoformat(raw)
        except ValueError:
            due_on = None
    if due_on is None and due_at is not None:
        due_on = due_at.astimezone(tz).date()
    person = t.get("personId")
    assigned = t.get("assignedUserId")
    return {
        "external_id": str(t.get("id")),
        "person_external_id": str(person) if person not in (None, 0, "0", "") else None,
        "agent_external_id": str(assigned) if assigned not in (None, 0, "0", "") else None,
        "name": clip(t.get("name"), 300),
        "task_type": clip(t.get("type"), 40),
        "due_on": due_on,
        "due_at": due_at,
    }


def map_person(p: dict, tz: dt.tzinfo = dt.timezone.utc) -> dict:
    assigned = p.get("assignedUserId")
    created = parse_ts(p.get("created") or p.get("createdAt"))
    name = p.get("name") or f'{p.get("firstName") or ""} {p.get("lastName") or ""}'.strip()
    contacted = p.get("contacted")
    return {
        "external_id": str(p.get("id")),
        "stage": clip(p.get("stage"), 80),
        # 0 means "nobody" in FUB's payloads as well as null.
        "agent_external_id": str(assigned) if assigned not in (None, 0, "0", "") else None,
        # The day it arrived in the WORKSPACE's calendar, which is what a period filter means: a
        # lead at 11pm Mountain on the 30th belongs to that month, not to the UTC date after it.
        "created_at_src": created.astimezone(tz).date() if created else None,
        "name": clip(name, 200),
        "origin": clip(p.get("source"), 120),
        "contacted": bool(contacted) if contacted is not None else None,
        "src_created_at": created,
        "src_updated_at": parse_ts(p.get("updated")),
        "last_activity_at": parse_ts(p.get("lastActivity")),
    }
