"""Go High Level (HighLevel) client — Spring B (beCollective + The Forum).

Auth: HighLevel API v2 with a **Private Integration Token** (static bearer;
the v1 API keys reached end-of-support Dec 31 2025). Base
https://services.leadconnectorhq.com, and every request needs a
`Version: 2021-07-28` header.

We pull the whole location's contacts (paginated) and filter membership by TAG
client-side using the per-integration config — so the exact server-side tag-filter
syntax (which shifts in v2) doesn't matter, only the stable contacts-list endpoint.
Confirm endpoint shape against highlevel.stoplight.io if a pull returns nothing.
"""
from __future__ import annotations

import httpx

GHL_BASE = "https://services.leadconnectorhq.com"
GHL_VERSION = "2021-07-28"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Version": GHL_VERSION, "Accept": "application/json"}


async def get_contacts(token: str, location_id: str, max_pages: int | None = None) -> list[dict]:
    """All contacts for the location, following cursor pagination."""
    out: list[dict] = []
    params: dict = {"locationId": location_id, "limit": 100}
    page = 0
    async with httpx.AsyncClient(timeout=45) as c:
        while True:
            r = await c.get(f"{GHL_BASE}/contacts/", headers=_headers(token), params=params)
            r.raise_for_status()
            data = r.json()
            rows = data.get("contacts") or []
            out.extend(rows)
            meta = data.get("meta") or {}
            start_after_id = meta.get("startAfterId")
            start_after = meta.get("startAfter")
            page += 1
            if not rows or not start_after_id or (max_pages and page >= max_pages):
                break
            params["startAfterId"] = start_after_id
            if start_after:
                params["startAfter"] = start_after
    return out


def contact_tags(c: dict) -> list[str]:
    return [str(t).strip().lower() for t in (c.get("tags") or [])]


def contact_name(c: dict) -> str:
    name = " ".join(p for p in [c.get("firstName"), c.get("lastName")] if p).strip()
    return name or c.get("contactName") or c.get("name") or c.get("email") or str(c.get("id"))


def contact_url(location_id: str, contact_id) -> str:
    return f"https://app.gohighlevel.com/v2/location/{location_id}/contacts/detail/{contact_id}"


def classify_member(tags: list[str], member_tags: set[str], forum_tags: set[str],
                    becollective_tags: set[str]) -> tuple[bool, str | None]:
    """Is this contact an active member, and which segment? Tag-driven + configurable."""
    tset = set(tags)
    if not (tset & member_tags):
        return False, None
    if tset & forum_tags:
        return True, "forum"
    if tset & becollective_tags:
        return True, "becollective"
    return True, None
