"""QuickBooks Online — OAuth 2.0 (Authorization Code) + ProfitAndLoss summary.

Verified facts (current as of June 2026), encoded exactly:
- OAuth 2.0 Authorization Code flow ONLY. No API keys, no basic auth for the API.
- Access token lifetime: 60 minutes. Refresh tokens rotate every 24-26h
  (store the newest on every call), max 5-year lifetime.
- Report: GET /reports/ProfitAndLoss?start_date=&end_date=&minorversion=75.
- Rate limits: 500 req/min/realm; report endpoints 200/min -> back off on 429
  and honor Retry-After.
- Per entity = per realm = one Integration row.
- Modernized Reports cutover (~June 30, 2026): use the SUMMARY report (group
  totals), pin the minor version, smoke-test the six totals.
"""
import asyncio
import base64
import re

import httpx

from ..config import settings

AUTHORIZE_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
SCOPE = "com.intuit.quickbooks.accounting"
API_BASE = (
    "https://sandbox-quickbooks.api.intuit.com"
    if settings.QBO_ENV == "sandbox"
    else "https://quickbooks.api.intuit.com"
)


def authorize_url(state: str) -> str:
    from urllib.parse import urlencode

    q = urlencode({
        "client_id": settings.QBO_CLIENT_ID,
        "response_type": "code",
        "scope": SCOPE,
        "redirect_uri": settings.QBO_REDIRECT_URI,
        "state": state,
    })
    return f"{AUTHORIZE_URL}?{q}"


def _basic() -> str:
    raw = f"{settings.QBO_CLIENT_ID}:{settings.QBO_CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            TOKEN_URL,
            headers={
                "Authorization": _basic(),
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.QBO_REDIRECT_URI,
            },
        )
        r.raise_for_status()
        return r.json()  # access_token, refresh_token, expires_in, x_refresh_token_expires_in


async def refresh(refresh_token: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            TOKEN_URL,
            headers={
                "Authorization": _basic(),
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        )
        r.raise_for_status()
        return r.json()  # ALWAYS persist the new refresh_token from here


async def profit_and_loss(realm_id: str, access_token: str, start: str, end: str) -> dict:
    url = f"{API_BASE}/v3/company/{realm_id}/reports/ProfitAndLoss"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    params = {"start_date": start, "end_date": end, "minorversion": "75"}
    # Back off on 429, honoring Retry-After (up to 3 attempts).
    for attempt in range(3):
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.get(url, headers=headers, params=params)
            if r.status_code == 429 and attempt < 2:
                wait = float(r.headers.get("Retry-After", "5"))
                await asyncio.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
    # Should not reach here, but keep the type contract.
    r.raise_for_status()
    return r.json()


# ── Deposit-level detail behind the Booked revenue (the audit drawer) ──
async def query(realm_id: str, access_token: str, sql: str) -> dict:
    """QBO SQL-ish Query API → raw QueryResponse."""
    url = f"{API_BASE}/v3/company/{realm_id}/query"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    for attempt in range(3):
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.get(url, headers=headers, params={"query": sql, "minorversion": "75"})
            if r.status_code == 429 and attempt < 2:
                await asyncio.sleep(float(r.headers.get("Retry-After", "5")))
                continue
            r.raise_for_status()
            return r.json()
    r.raise_for_status()
    return r.json()


async def deposits(realm_id: str, access_token: str, start: str, end: str) -> list[dict]:
    sql = (f"SELECT * FROM Deposit WHERE TxnDate >= '{start}' AND TxnDate <= '{end}' "
           f"ORDERBY TxnDate DESC MAXRESULTS 1000")
    data = await query(realm_id, access_token, sql)
    return ((data.get("QueryResponse") or {}).get("Deposit")) or []


def _ref_name(ref) -> str:
    return (ref or {}).get("name") or ""


def _clean_agent(entity_name: str) -> str:
    """QBO customer:job on the commission line, e.g. "Pablo Negrete (c):5769 S Hillside"
    → "Pablo Negrete"."""
    name = (entity_name or "").split(":")[0].strip()
    return re.sub(r"\s*\([a-z]\)\s*$", "", name).strip()


def deposits_to_deals(deposits: list) -> list[dict]:
    """Reconstruct real-estate deals from commission deposits. Each deposit line has
    an account, an entity (customer/agent), a class (property) and an amount. Group a
    deposit's lines by class → one deal: GCI = the gross-commission INCOME line(s);
    agent = the entity on the COMMISSION-PAID line; property = the class; date = the
    deposit date. (Mapped from ULRG's chart: 41xxx income, 51xxx commission paid.)"""
    deals = []
    for dep in deposits:
        date, dep_id = dep.get("TxnDate"), dep.get("Id")
        by_class: dict[str, list] = {}
        for ln in (dep.get("Line") or []):
            det = ln.get("DepositLineDetail") or {}
            by_class.setdefault(_ref_name(det.get("ClassRef")) or "—", []).append((ln, det))
        for klass, lines in by_class.items():
            gci = 0.0
            agent, agent_mag = "", 0.0                            # agent = the LARGEST commission payout
            for ln, det in lines:
                acct = _ref_name(det.get("AccountRef")).lower()
                amt = float(ln.get("Amount") or 0)
                if "income" in acct or "gross commission" in acct:
                    gci += amt
                elif "commission" in acct and amt < 0:            # commission paid out → the agent
                    ent = _clean_agent(_ref_name(det.get("Entity")))
                    if ent and abs(amt) > agent_mag:
                        agent, agent_mag = ent, abs(amt)
            if gci or agent:
                deals.append({
                    "id": f"{dep_id}:{klass}", "deposit_id": dep_id,
                    "gci": round(gci, 2), "agent": agent or None,
                    "property": None if klass == "—" else klass, "date": date,
                })
    deals.sort(key=lambda d: (d.get("date") or ""), reverse=True)
    return deals


# ── Parse the summary ProfitAndLoss into our six numbers ──
# Top-level report rows carry a `group` key. We read each group's Summary total
# (the last ColData value). Group keys: Income, COGS, GrossProfit, Expenses,
# NetOperatingIncome, NetIncome. Verify against a live response on first run.
def parse_pl(report: dict) -> dict:
    wanted = {
        "Income": "revenue",
        "COGS": "cogs",
        "GrossProfit": "gross_profit",
        "Expenses": "opex",
        "NetOperatingIncome": "noi",
        "NetIncome": "net_income",
    }
    out = {v: 0.0 for v in wanted.values()}
    rows = (report.get("Rows") or {}).get("Row", [])
    for row in rows:
        grp = row.get("group")
        if grp in wanted:
            summary = (row.get("Summary") or {}).get("ColData", [])
            if summary:
                try:
                    out[wanted[grp]] = float(summary[-1].get("value") or 0)
                except (TypeError, ValueError):
                    out[wanted[grp]] = 0.0
    return out
