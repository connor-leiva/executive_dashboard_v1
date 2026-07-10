"""Legacy Stripe (Spring's ORIGINAL account) — read-only charge reader.

The Forum's recurring dues for legacy members still bill in Spring's original
Stripe account (the one wired to the old Spring B GHL location), so those charges
never reach the new Forum sub-account or the dashboard. We read them directly with
a RESTRICTED, READ-ONLY key (Charges: read + Customers: read is enough) and merge
the Forum ones into the billing block — deduped against the CSV-backfilled GHL
copies (see services.billing.merge_payment_sources).

Two gotchas vs the GHL Payments API:
  • Stripe amounts are in CENTS (integers) — everything is /100 to dollars here.
  • `created` is a Unix epoch (seconds), not an ISO string.

Auth is a Bearer token; a restricted key (`rk_live_…`) works exactly like a secret
key for GETs. We never write, so read scopes are all that's ever needed.
"""
from __future__ import annotations

import datetime as dt

import httpx

STRIPE_BASE = "https://api.stripe.com/v1"


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


async def list_charges(key: str, created_gt: int | None = None,
                       max_pages: int | None = None) -> list[dict]:
    """All charges (newest-first from Stripe), following cursor pagination. When
    `created_gt` (Unix seconds) is given, only charges created strictly after it are
    returned — the watermark that keeps the ongoing sync/delta cheap. Customer is
    expanded so we can resolve the member email even when billing_details is blank."""
    out: list[dict] = []
    base = [("limit", "100"), ("expand[]", "data.customer")]
    if created_gt:
        base.append(("created[gt]", str(int(created_gt))))
    starting_after: str | None = None
    page = 0
    async with httpx.AsyncClient(timeout=45) as c:
        while True:
            params = list(base)
            if starting_after:
                params.append(("starting_after", starting_after))
            r = await c.get(f"{STRIPE_BASE}/charges", headers=_headers(key), params=params)
            r.raise_for_status()
            data = r.json()
            rows = data.get("data") or []
            out.extend(rows)
            page += 1
            if not data.get("has_more") or not rows or (max_pages and page >= max_pages):
                break
            starting_after = rows[-1].get("id")
            if not starting_after:
                break
    return out


async def ping(key: str) -> bool:
    """Cheap credential check for the connect flow — one charge, read-only. Raises
    httpx.HTTPStatusError on a bad/again-scoped key so the caller can surface it."""
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{STRIPE_BASE}/charges", headers=_headers(key), params={"limit": 1})
        r.raise_for_status()
        return True


# ── accessors: normalize a Stripe charge to the fields the sync/billing want ──
def _cust(ch: dict) -> dict:
    c = ch.get("customer")
    return c if isinstance(c, dict) else {}


def charge_email(ch: dict) -> str | None:
    """Best available email, lowercased: billing details → receipt → customer."""
    bd = ch.get("billing_details") or {}
    email = bd.get("email") or ch.get("receipt_email") or _cust(ch).get("email")
    return (email or "").strip().lower() or None


def charge_name(ch: dict) -> str | None:
    bd = ch.get("billing_details") or {}
    return (bd.get("name") or _cust(ch).get("name") or "").strip() or None


def charge_amount(ch: dict) -> float:
    """Dollars (Stripe reports cents)."""
    return round((ch.get("amount") or 0) / 100.0, 2)


def charge_refunded(ch: dict) -> float:
    return round((ch.get("amount_refunded") or 0) / 100.0, 2)


def charge_status(ch: dict) -> str:
    """Normalize to succeeded | failed | <raw> — matches ghl.txn_status semantics
    so compute_billing treats both feeds identically."""
    s = (ch.get("status") or "").lower()
    if s == "succeeded":
        return "succeeded"
    if s == "failed":
        return "failed"
    return s or "unknown"


def charge_datetime(ch: dict) -> dt.datetime | None:
    ts = ch.get("created")
    try:
        return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc) if ts else None
    except (ValueError, OverflowError, OSError, TypeError):
        return None


def charge_date(ch: dict) -> dt.date | None:
    d = charge_datetime(ch)
    return d.date() if d else None


def charge_contact(ch: dict) -> dict:
    """Name/phone/address for the GHL delta-import CSV (attaches to the existing
    contact by email; blanks are fine since the backfill already set addresses)."""
    bd = ch.get("billing_details") or {}
    addr = bd.get("address") or {}
    return {"phone": (bd.get("phone") or "").strip() or None,
            "address_line1": (addr.get("line1") or "").strip() or None,
            "city": (addr.get("city") or "").strip() or None,
            "state": (addr.get("state") or "").strip() or None,
            "country": (addr.get("country") or "").strip() or None,
            "postal_code": (addr.get("postal_code") or "").strip() or None}


def charge_description(ch: dict) -> str:
    return (ch.get("description") or "").strip()


def payment_intent(ch: dict) -> str | None:
    pi = ch.get("payment_intent")
    return pi if isinstance(pi, str) else (pi or {}).get("id") if isinstance(pi, dict) else None


def dashboard_url(ch: dict) -> str:
    """Deep link to the payment in the Stripe dashboard (for the audit drawer)."""
    ref = payment_intent(ch) or ch.get("id") or ""
    return f"https://dashboard.stripe.com/payments/{ref}" if ref else "https://dashboard.stripe.com/payments"
