"""ARIVE (mortgage LOS) client — Sympli Mortgage's pipeline via the AEP REST API.

Auth (validated live, see reference memory): a two-step model. Exchange the three
credentials (Client ID + Secret Key + API Key) for a short-lived JWT, then send the
JWT + API key on every request. The token endpoint returns **HTTP 201** (not 200)
with the token under `AccessToken` (capital A). The API key goes in BOTH the header
(`X-API-KEY`) and the JSON body.

Field-name chaos is real (camelCase / snake_case / display / UPPER_SNAKE for the
same logical field), and list rows are sparse — always normalize with `pick()` and
fetch `GET /loans/{id}` when full detail is needed.
"""
from __future__ import annotations

import time
import asyncio

import httpx

ARIVE_BASE = "https://gwapiconnect.myarive.com/api"

# Current-status codes at or past the funding milestone. A loan that funded and then
# moved on (broker check, commission) is the SAME funded loan — count it once here,
# never add these together (the doc's "don't double-count funded loans" trap).
FUNDED_STATUSES = {
    "LOAN_FUNDED", "FUNDED", "LOAN_FUNDED_ED", "BROKER_CHECK_RECEIVED",
    "COMMISSION_PAID", "LOAN_FINALIZED", "COMPLETED", "CLOSED",
}
# Terminal-dead: not funded, out of the pipeline.
DEAD_STATUSES = {"ADVERSE", "DENIED", "WITHDRAWN", "LOAN_ARCHIVED", "ARCHIVED"}


def pick(obj: dict, *keys, default=None):
    """First present/non-empty value across alias keys (Arive renames everything)."""
    for k in keys:
        v = obj.get(k) if isinstance(obj, dict) else None
        if v not in (None, "", []):
            return v
    return default


# ── auth (per-credential in-process token cache) ─────────────────────
_token_cache: dict[str, tuple[str, float]] = {}   # cache_key -> (token, expires_at_epoch)


async def get_access_token(client_id: str, secret: str, api_key: str) -> str:
    """Exchange creds for a JWT; cache in-process and refresh at ~80% of TTL."""
    key = f"{client_id}:{api_key}"
    cached = _token_cache.get(key)
    if cached and cached[1] > time.time():
        return cached[0]
    async with httpx.AsyncClient(timeout=45) as c:
        r = await c.post(f"{ARIVE_BASE}/auth/access-token",
                         headers={"X-API-KEY": api_key, "Content-Type": "application/json",
                                  "Accept": "application/json"},
                         json={"clientId": client_id, "secret": secret, "apiKey": api_key})
    if r.status_code not in (200, 201):
        raise ValueError(f"Arive auth failed: {r.status_code} {r.text[:180]}")
    data = r.json()
    token = data.get("AccessToken") or data.get("accessToken")
    if not token:
        raise ValueError("Arive auth returned no AccessToken")
    ttl = float(data.get("ExpiresIn") or 3600)
    _token_cache[key] = (token, time.time() + ttl * 0.8)
    return token


def _headers(token: str, api_key: str) -> dict:
    return {"X-API-KEY": api_key, "Authorization": f"Bearer {token}",
            "Accept": "application/json", "Content-Type": "application/json"}


def _rows(payload) -> list[dict]:
    """Loans hide under one of several wrappers (rows/loans/responses/data) or a bare
    array — return the longest list found."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        best = []
        for v in payload.values():
            if isinstance(v, list) and len(v) >= len(best):
                best = v
        return best
    return []


async def get_loans(client_id: str, secret: str, api_key: str,
                    max_loans: int = 2000) -> list[dict]:
    """All loans, newest-updated first (limit is capped at 100 → paginate by offset).
    Bounded by max_loans so a huge brokerage can't run away."""
    token = await get_access_token(client_id, secret, api_key)
    out: list[dict] = []
    offset = 0
    async with httpx.AsyncClient(timeout=60) as c:
        while len(out) < max_loans:
            r = await c.get(f"{ARIVE_BASE}/loans", headers=_headers(token, api_key),
                            params={"limit": 100, "offset": offset,
                                    "orderBy": "updatedAt", "sort": "DESC"})
            r.raise_for_status()
            rows = _rows(r.json())
            out.extend(rows)
            if len(rows) < 100:
                break
            offset += 100
    return out[:max_loans]


def _detail_body(body):
    """/loans/{id} returns the loan object at the TOP level (has ariveLoanId).
    Older shapes wrapped it under responses/rows — handle both."""
    if isinstance(body, dict):
        if "ariveLoanId" in body:
            return body
        if isinstance(body.get("responses"), dict):
            return body["responses"]
        rows = _rows(body)
        if rows:
            return rows[0]
        return body
    return None


async def get_loan(loan_id: str, client_id: str, secret: str, api_key: str) -> dict | None:
    """Full detail for one loan (list rows are sparse). Accepts display id or GUID."""
    token = await get_access_token(client_id, secret, api_key)
    async with httpx.AsyncClient(timeout=45) as c:
        r = await c.get(f"{ARIVE_BASE}/loans/{loan_id}", headers=_headers(token, api_key))
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return _detail_body(r.json())


async def get_loans_detail(ids, client_id: str, secret: str, api_key: str,
                           concurrency: int = 8) -> dict:
    """Full detail for many loans concurrently → {display_id: detail}. Best-effort
    per loan (a failed detail is skipped, not fatal)."""
    token = await get_access_token(client_id, secret, api_key)
    out: dict = {}
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=45) as c:
        async def one(lid):
            async with sem:
                try:
                    r = await c.get(f"{ARIVE_BASE}/loans/{lid}", headers=_headers(token, api_key))
                    if r.status_code == 200:
                        d = _detail_body(r.json())
                        if isinstance(d, dict):
                            out[str(lid)] = d
                except Exception:  # noqa: BLE001
                    pass
        await asyncio.gather(*(one(i) for i in ids))
    return out


def _money(v):
    try:
        return round(float(str(v).replace("$", "").replace(",", "")), 2) if v not in (None, "", "null") else None
    except (TypeError, ValueError):
        return None


def loan_economics(full: dict) -> dict:
    """Revenue / comp on a funded loan (Lender-Paid comp = the commission Sympli
    earns, not the LO's take-home split). netLoanRevenue = gross − direct loan costs
    (reimbursements, tolerance cures). Used for the calculated Sympli financials."""
    return {
        "gross_revenue": _money(pick(full, "grossLoanRevenue", "totalLoanRevenue")),
        "net_revenue": _money(pick(full, "netLoanRevenue")),
        "compensation": _money(pick(full, "compensation")),
        "comp_type": pick(full, "compensationType"),
    }


def loan_referral(full: dict) -> dict:
    """Referral source + buyer's real-estate agent from full loan detail — how we
    tell a loan came from a partner brokerage (e.g. Utah Life = @liveutah.com)."""
    email = pick(full, "referralContactSourceEmail")
    agent_email = None
    for bc in (full.get("businessContacts") or []):
        if isinstance(bc, dict) and str(bc.get("role") or "").upper() == "REAL_ESTATE_AGENT":
            ae = pick(bc, "emailAddressText", "email")
            if ae and (str(bc.get("subType") or "").upper() == "BUYERS_AGENT" or agent_email is None):
                agent_email = ae
    return {"referral_email": (str(email).lower().strip() if email else None),
            "referral_name": pick(full, "referralContactSourceName"),
            "buyer_agent_email": (str(agent_email).lower().strip() if agent_email else None),
            "lead_source": pick(full, "leadSource")}


# ── normalization ────────────────────────────────────────────────────
def loan_status(loan: dict) -> str | None:
    cls = loan.get("currentLoanStatus")
    if isinstance(cls, dict):
        st = cls.get("status")
        if st:
            return str(st).upper()
    st = pick(loan, "currentLoanStatusStatus", "current_loan_status", "loanStatus", "status")
    return str(st).upper() if st else None


def loan_status_date(loan: dict):
    cls = loan.get("currentLoanStatus")
    if isinstance(cls, dict) and cls.get("date"):
        return cls["date"]
    return pick(loan, "currentLoanStatusDate", "modifiedDateTime", "loanUpdatedAt", "updatedAt")


def loan_display_id(loan: dict) -> str:
    return str(pick(loan, "ariveDisplayLoanId", "ariveLoanId", "arive_display_loan_id",
                    "id", "loanId", "sysGUID", default=""))


def loan_amount(loan: dict) -> float:
    try:
        return float(pick(loan, "baseLoanAmount", "base_loan_amount", default=0) or 0)
    except (TypeError, ValueError):
        return 0.0


def loan_borrower(loan: dict) -> dict:
    """Primary borrower — native AEP nests loanBorrowers[]; Zapier flattens to
    loanBorrower1_*. Return {name, email, phone}."""
    bs = loan.get("loanBorrowers")
    b0 = bs[0] if isinstance(bs, list) and bs and isinstance(bs[0], dict) else {}
    first = pick(b0, "firstName", "first_name") or pick(loan, "loanBorrower1_firstName", "borrowerFirstName")
    last = pick(b0, "lastName", "last_name") or pick(loan, "loanBorrower1_lastName", "borrowerLastName")
    name = " ".join(p for p in [first, last] if p).strip() or None
    email = (pick(b0, "emailAddressText", "email", "borrowerEmailAddress")
             or pick(loan, "loanBorrower1_emailAddressText", "borrowerEmailAddress"))
    phone = (pick(b0, "mobilePhone10digit", "mobilePhone", "cellPhone")
             or pick(loan, "loanBorrower1_mobilePhone10digit", "borrowerCellPhone"))
    return {"name": name, "email": (str(email).strip().lower() if email else None),
            "phone": _digits(phone)}


def _digits(v) -> str | None:
    if not v:
        return None
    d = "".join(ch for ch in str(v) if ch.isdigit())
    return d[-10:] if len(d) >= 10 else (d or None)


def is_funded(status: str | None) -> bool:
    return bool(status) and status.upper() in FUNDED_STATUSES


def is_dead(status: str | None) -> bool:
    return bool(status) and status.upper() in DEAD_STATUSES


def loan_deep_link(loan: dict) -> str | None:
    return pick(loan, "deepLinkURL", "ariveDeepLink", "ARIVE Deep Link")
