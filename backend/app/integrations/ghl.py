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

import asyncio
import time

import httpx

GHL_BASE = "https://services.leadconnectorhq.com"
GHL_VERSION = "2021-07-28"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Version": GHL_VERSION, "Accept": "application/json"}

# ── One request path (RECRUITING-SPEC §5.3) ────────────────────────────────────────────────────
#
# Every call in this module goes through `ghl_request`. That is worth the churn for three
# reasons, none of them tidiness:
#
#   1. SYNC AND WRITES SHARE ONE BUDGET. HighLevel's limit is per app per LOCATION, so a sync
#      paging through contacts and a recruiter pressing Send are spending the same allowance.
#      Without a shared bucket the sync wins by volume and the human gets the 429.
#   2. WRITES TAKE PRIORITY. A sync can wait; a text to a recruit that the user just authorised
#      cannot. Sync backs off at 20 remaining, writes go down to 5.
#   3. IT IS WHERE THE WRITE PATH WILL LIVE. Phase 4 adds POST/PUT here and nowhere else, so
#      "which code can write to a customer's CRM" stays a question with one answer.
#
# THE HEADERS MAY NOT EXIST. HighLevel documents `X-RateLimit-*` for OAuth apps; whether a
# Private Integration Token gets them is Phase 0's V1 and is not settled. So the bucket is
# header-driven WHEN headers arrive and falls back to a local count of 100 per 10 seconds when
# they do not. That is not a hedge -- a client that only works when the server volunteers its
# accounting is a client that breaks silently the day the server stops.

_RATE_WINDOW_S = 10.0
_RATE_BURST = 100                      # HighLevel: 100 requests / 10 s / app / location
_SYNC_FLOOR = 20                       # a sync stops here, leaving room for a person
_WRITE_FLOOR = 5                       # a write goes this far and no further


class _Bucket:
    """Per-location request budget. Local count until the server tells us otherwise."""

    def __init__(self) -> None:
        self.stamps: list[float] = []          # local: monotonic times of recent requests
        self.remaining: int | None = None      # server: X-RateLimit-Remaining, when sent
        self.daily_remaining: int | None = None
        self.seen_headers = False
        self.lock = asyncio.Lock()

    def _local_remaining(self, now: float) -> int:
        self.stamps = [t for t in self.stamps if now - t < _RATE_WINDOW_S]
        return _RATE_BURST - len(self.stamps)

    async def take(self, floor: int) -> None:
        """Wait until at least `floor` of the budget would still be left after this request."""
        while True:
            async with self.lock:
                now = time.monotonic()
                local = self._local_remaining(now)
                # The server's number wins when we have one: it accounts for every other client
                # on this location, which our local count cannot see.
                budget = self.remaining if self.seen_headers and self.remaining is not None else local
                if budget > floor:
                    self.stamps.append(now)
                    if self.remaining is not None:
                        self.remaining -= 1
                        return
                    return
                oldest = min(self.stamps) if self.stamps else now
                wait = max(0.05, _RATE_WINDOW_S - (now - oldest))
            await asyncio.sleep(min(wait, _RATE_WINDOW_S))

    def observe(self, headers) -> None:
        rem = headers.get("X-RateLimit-Remaining")
        if rem is not None:
            try:
                self.remaining = int(rem)
                self.seen_headers = True
            except (TypeError, ValueError):
                pass
        day = headers.get("X-RateLimit-Daily-Remaining")
        if day is not None:
            try:
                self.daily_remaining = int(day)
            except (TypeError, ValueError):
                pass


_BUCKETS: dict[str, _Bucket] = {}


def _bucket(location_id: str) -> _Bucket:
    b = _BUCKETS.get(location_id or "-")
    if b is None:
        b = _BUCKETS[location_id or "-"] = _Bucket()
    return b


class GhlError(Exception):
    """A GHL call that failed in a way the caller should surface rather than swallow.

    `status` and `body` are carried so §5.7's error table can map them to something a person can
    act on -- "this connection is missing conversations/message.write" rather than "403".
    """

    def __init__(self, status: int | None, body: str = "", *, path: str = ""):
        super().__init__(f"GHL {status} on {path}: {body[:200]}")
        self.status = status
        self.body = body
        self.path = path


_RETRY_STATUS = (429, 500, 502, 503, 504)


async def ghl_request(method: str, path: str, *, token: str, location_id: str = "",
                      client: httpx.AsyncClient | None = None, priority: str = "sync",
                      attempts: int = 4, timeout: float = 30.0,
                      raise_for_status: bool = False, **kw) -> httpx.Response:
    """The single way this module talks to HighLevel.

    `priority` is "sync" or "write"; it picks which floor of the budget the caller may spend
    down to. Retries 429 and 5xx with backoff -- the pattern `get_opportunity` already used,
    now in one place rather than in whichever function happened to need it.

    Returns the RESPONSE, not parsed JSON: callers differ on what a 404 means (a deleted
    opportunity is not an error; a deleted location is), and that judgement belongs to them.
    With `raise_for_status=True` a non-2xx raises GhlError instead, for callers that have no
    sensible answer to failure.
    """
    floor = _WRITE_FLOOR if priority == "write" else _SYNC_FLOOR
    b = _bucket(location_id)
    headers = {**_headers(token), **(kw.pop("headers", None) or {})}
    if method.upper() not in ("GET", "HEAD") and "Content-Type" not in headers:
        # _headers() never sent one, so every write would have needed to remember. (§1.1)
        headers["Content-Type"] = "application/json"

    own = client is None
    c = client or httpx.AsyncClient(timeout=timeout)
    try:
        last: httpx.Response | None = None
        for attempt in range(attempts):
            await b.take(floor)
            try:
                r = await c.request(method, f"{GHL_BASE}{path}", headers=headers, **kw)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < attempts - 1:
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                raise GhlError(None, str(exc), path=path) from exc
            b.observe(r.headers)
            last = r
            if r.status_code in _RETRY_STATUS and attempt < attempts - 1:
                # Respect Retry-After when the server sends one; otherwise 0.4s, 0.8s, 1.2s.
                delay = 0.4 * (attempt + 1)
                ra = r.headers.get("Retry-After")
                if ra:
                    try:
                        delay = min(float(ra), 10.0)
                    except (TypeError, ValueError):
                        pass
                await asyncio.sleep(delay)
                continue
            break
        assert last is not None
        if raise_for_status and last.status_code >= 400:
            raise GhlError(last.status_code, last.text, path=path)
        return last
    finally:
        if own:
            await c.aclose()


async def ghl_json(method: str, path: str, **kw) -> dict:
    """`ghl_request` for the common case: a 2xx JSON body, or {} for anything else."""
    r = await ghl_request(method, path, **kw)
    if r.status_code != 200:
        return {}
    try:
        return r.json() or {}
    except ValueError:
        return {}



async def get_contacts(token: str, location_id: str, max_pages: int | None = None) -> list[dict]:
    """All contacts for the location, following cursor pagination."""
    out: list[dict] = []
    params: dict = {"locationId": location_id, "limit": 100}
    page = 0
    async with httpx.AsyncClient(timeout=45) as c:
        while True:
            r = await ghl_request("GET", "/contacts/", token=token, location_id=location_id,
                                  client=c, params=params, raise_for_status=True)
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


async def get_custom_fields(token: str, location_id: str, model: str | None = None) -> list[dict]:
    """Custom-field DEFINITIONS for the location: [{id, name, dataType, fieldKey}].
    `model="opportunity"` returns opportunity-model fields — the default (no param) endpoint
    returns only contact fields, so the six Sales Desk opp fields need model="opportunity"."""
    body = await ghl_json("GET", f"/locations/{location_id}/customFields", token=token,
                          location_id=location_id,
                          params=({"model": model} if model else None))
    return body.get("customFields") or []


def contact_custom_values(contact: dict) -> dict:
    """{field_id: value} for a contact's populated custom fields (blanks dropped)."""
    out: dict = {}
    for f in (contact.get("customFields") or []):
        v = f.get("value")
        if v not in (None, "", []):
            out[f.get("id")] = v
    return out


def opp_custom_values(opp: dict) -> dict:
    """{field_id: value} for an opportunity's populated custom fields. Opportunities use
    `fieldValue` (contacts use `value`) — handle both. Blanks dropped."""
    out: dict = {}
    for f in (opp.get("customFields") or []):
        v = f.get("fieldValue") if "fieldValue" in f else f.get("value")
        if v not in (None, "", []):
            out[f.get("id")] = v
    return out


async def get_opportunity(token: str, location_id: str, opp_id: str) -> dict | None:
    """A single opportunity's DETAIL — carries the opportunity custom-field VALUES that the
    /opportunities/search list omits (Sales Rep, Booking ID, Call Time, Call Outcome, …).

    Retries transient failures (429 rate-limit / 5xx) with backoff. Returns None on a
    persistent failure — distinct from a real (populated) opp — so callers don't mistake a
    rate-limited fetch for a blank opportunity and silently drop it."""
    # The 429/5xx backoff that used to be written out here is ghl_request's, unchanged:
    # four attempts, 0.4s / 0.8s / 1.2s. Returning None on a persistent failure still matters
    # more than the retry does -- a rate-limited fetch must not read as a blank opportunity.
    try:
        r = await ghl_request("GET", f"/opportunities/{opp_id}", token=token,
                              location_id=location_id)
    except GhlError:
        return None
    if r.status_code != 200:
        return None
    body = r.json() or {}
    return body.get("opportunity") or body


async def get_users(token: str, location_id: str) -> list[dict]:
    """Location users (sales reps): [{id, name, email, …}] — seeds the rep-roster display names."""
    body = await ghl_json("GET", "/users/", token=token, location_id=location_id,
                          params={"locationId": location_id})
    return body.get("users") or []


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
        r = await ghl_request("GET", "/opportunities/pipelines", token=token,
                              location_id=location_id, client=c,
                              params={"locationId": location_id}, raise_for_status=True)
        return (r.json() or {}).get("pipelines") or []


async def get_opportunities(token: str, location_id: str, max_pages: int | None = None) -> list[dict]:
    """All opportunities, following the search cursor (startAfterId/startAfter)."""
    out: list[dict] = []
    params: dict = {"location_id": location_id, "limit": 100}
    page = 0
    async with httpx.AsyncClient(timeout=60) as c:
        while True:
            r = await ghl_request("GET", "/opportunities/search", token=token,
                                  location_id=location_id, client=c, params=params,
                                  raise_for_status=True)
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
            r = await ghl_request("GET", "/payments/subscriptions", token=token,
                                  location_id=location_id, client=c, params=params,
                                  raise_for_status=True)
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
            r = await ghl_request("GET", "/payments/transactions", token=token,
                                  location_id=location_id, client=c, params=params,
                                  raise_for_status=True)
            data = r.json()
            rows = data.get("data") or data.get("transactions") or []
            out.extend(rows)
            page += 1
            if len(rows) < 100 or (max_pages and page >= max_pages):
                break
            params["offset"] = len(out)
    return out


async def get_invoices(token: str, location_id: str, max_pages: int | None = None) -> list[dict]:
    """All invoices for the location (altId/altType) — carry `invoiceItems` (the real
    line-item labels) that a transaction references by entitySourceId. Read-only."""
    out: list[dict] = []
    params: dict = {"altId": location_id, "altType": "location", "limit": 100, "offset": 0}
    page = 0
    async with httpx.AsyncClient(timeout=45) as c:
        while True:
            r = await ghl_request("GET", "/invoices/", token=token, location_id=location_id,
                                  client=c, params=params, raise_for_status=True)
            data = r.json()
            rows = data.get("invoices") or data.get("data") or []
            out.extend(rows)
            page += 1
            if len(rows) < 100 or (max_pages and page >= max_pages):
                break
            params["offset"] += 100
    return out


def invoice_items(inv: dict) -> list[str]:
    return [it.get("name") or it.get("description") for it in (inv.get("invoiceItems") or [])
            if isinstance(it, dict) and (it.get("name") or it.get("description"))]


async def ghl_subscription_detail(token: str, location_id: str, sub_id: str) -> dict:
    """A single subscription's detail — carries the recurring product (interval),
    end date, and (via its snapshot) the next-payment date/amount the list omits."""
    params = {"altId": location_id, "altType": "location"}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await ghl_request("GET", f"/payments/subscriptions/{sub_id}", token=token,
                              location_id=location_id, client=c, params=params)
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
