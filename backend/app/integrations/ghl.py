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


# ── membership segmentation (the official 70 = union of member_tags) ──
def member_segment(tags: set[str], forum_tags: set[str], ic_tags: set[str]) -> str:
    """Which program a tagged member belongs to. Forum takes precedence when a
    contact carries both (matches the team's reconciliation)."""
    if tags & forum_tags:
        return "forum"
    if tags & ic_tags:
        return "inner_circle"
    return "member"


# ── opportunities (renewals pipeline = ARR; sales funnel = new members) ──
async def get_pipelines(token: str, location_id: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=45) as c:
        r = await c.get(f"{GHL_BASE}/opportunities/pipelines",
                        headers=_headers(token), params={"locationId": location_id})
        r.raise_for_status()
        return (r.json() or {}).get("pipelines") or []


async def get_opportunities(token: str, location_id: str, max_pages: int | None = None) -> list[dict]:
    """All opportunities, following the search cursor (startAfterId/startAfter)."""
    out: list[dict] = []
    params: dict = {"location_id": location_id, "limit": 100}
    page = 0
    async with httpx.AsyncClient(timeout=60) as c:
        while True:
            r = await c.get(f"{GHL_BASE}/opportunities/search", headers=_headers(token), params=params)
            r.raise_for_status()
            data = r.json()
            rows = data.get("opportunities") or []
            out.extend(rows)
            meta = data.get("meta") or {}
            sai = meta.get("startAfterId")
            page += 1
            if not rows or not sai or (max_pages and page >= max_pages):
                break
            params["startAfterId"] = sai
            if meta.get("startAfter"):
                params["startAfter"] = meta["startAfter"]
    return out


def opp_name(o: dict) -> str:
    return o.get("name") or ((o.get("contact") or {}).get("name")) or o.get("contactId") or "Member"


# ── subscriptions (MRR) — GHL Payments API. NOTE: the payments endpoints reject
# `locationId`; they require altId + altType=location (else HTTP 422). ──
async def get_subscriptions(token: str, location_id: str, max_pages: int | None = None) -> list[dict]:
    out: list[dict] = []
    params: dict = {"altId": location_id, "altType": "location", "limit": 100}
    page = 0
    async with httpx.AsyncClient(timeout=45) as c:
        while True:
            r = await c.get(f"{GHL_BASE}/payments/subscriptions", headers=_headers(token), params=params)
            r.raise_for_status()
            data = r.json()
            rows = data.get("data") or data.get("subscriptions") or []
            out.extend(rows)
            page += 1
            if len(rows) < 100 or (max_pages and page >= max_pages):
                break
            params["offset"] = len(out)
    return out


async def ghl_transactions(token: str, location_id: str, max_pages: int | None = None) -> list[dict]:
    """All GHL/Stripe transactions (charges) for the location — the cash/failures
    ledger. Same altId + altType=location contract as subscriptions (probe-verified)."""
    out: list[dict] = []
    params: dict = {"altId": location_id, "altType": "location", "limit": 100}
    page = 0
    async with httpx.AsyncClient(timeout=45) as c:
        while True:
            r = await c.get(f"{GHL_BASE}/payments/transactions", headers=_headers(token), params=params)
            r.raise_for_status()
            data = r.json()
            rows = data.get("data") or data.get("transactions") or []
            out.extend(rows)
            page += 1
            if len(rows) < 100 or (max_pages and page >= max_pages):
                break
            params["offset"] = len(out)
    return out


async def ghl_subscription_detail(token: str, location_id: str, sub_id: str) -> dict:
    """A single subscription's detail — carries the recurring product (interval),
    end date, and (via its snapshot) the next-payment date/amount the list omits."""
    params = {"altId": location_id, "altType": "location"}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{GHL_BASE}/payments/subscriptions/{sub_id}", headers=_headers(token), params=params)
        if r.status_code != 200:
            return {}
        return r.json() or {}


def txn_status(t: dict) -> str:
    """Normalize a transaction to succeeded | failed | refunded (full)."""
    s = (t.get("status") or "").lower()
    if s in ("succeeded", "success", "paid"):
        return "succeeded"
    if s in ("failed", "declined"):
        return "failed"
    if s in ("refunded", "reversed"):
        return "refunded"
    return s or "unknown"


def sub_interval(detail: dict) -> str | None:
    """Recurring interval (month|year) from a subscription detail's recurringProduct."""
    rp = detail.get("recurringProduct") or {}
    if isinstance(rp, dict):
        price = rp.get("price") if isinstance(rp.get("price"), dict) else {}
        return (rp.get("interval") or rp.get("recurringInterval")
                or price.get("interval") or price.get("recurringInterval"))
    return None


def sub_is_active(sub: dict) -> bool:
    return (sub.get("status") or "").lower() in ("active", "trialing")


def sub_monthly_amount(sub: dict) -> float:
    """Monthly amount of a subscription. The Forum's GHL returns amounts in whole
    dollars (verified against transactions), so no cents conversion; yearly
    intervals are normalised to monthly."""
    raw = sub.get("amount", sub.get("priceAmount", sub.get("price", 0)))
    try:
        amt = float(raw)
    except (TypeError, ValueError):
        amt = 0.0
    interval = str(sub.get("interval") or sub.get("recurringInterval") or "month").lower()
    if "year" in interval or "annual" in interval:
        amt /= 12.0
    return round(amt, 2)


# Events are NOT modelled in GHL calendars for The Forum (that calendar is empty);
# they're tracked by per-event TAGS (e.g. "the forum q3 2026"). "Registered" is
# therefore a contact-tag count, handled in the contacts pass of the sync.
