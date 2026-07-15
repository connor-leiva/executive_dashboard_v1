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


# ── Deep link to a transaction in the QuickBooks Online UI ──
# The route depends on the entity type; the link opens in the user's currently-active QBO
# company (Intuit's txn URLs carry no realm, so surface the entity name alongside it).
_QBO_TXN_ROUTE = {
    "Purchase": "expense", "Bill": "bill", "BillPayment": "billpayment",
    "Deposit": "deposit", "Transfer": "transfer", "JournalEntry": "journal",
    "Check": "check", "CreditCardCredit": "creditcardcredit", "SalesReceipt": "salesreceipt",
}


def app_txn_url(qbo_type: str, qbo_id: str) -> str | None:
    route = _QBO_TXN_ROUTE.get(qbo_type)
    if not route or not qbo_id:
        return None
    return f"{settings.QBO_APP_BASE.rstrip('/')}/{route}?txnId={qbo_id}"


# ── Books additions (SPEC-books-module Part 2.1): transaction-level pull ──
# Same client style — httpx, 429 backoff honoring Retry-After, minorversion=75 pinned.
async def cdc(realm_id: str, access_token: str, entities: str, changed_since_iso: str) -> dict:
    """Change Data Capture — everything of `entities` changed since a timestamp.
    entities is a comma list, e.g. "Purchase,Deposit,JournalEntry,Transfer,Bill,BillPayment".
    NOTE: the CDC lookback window is 30 days max; older than that, the caller must
    fall back to a full query. Returns the raw CDCResponse envelope."""
    url = f"{API_BASE}/v3/company/{realm_id}/cdc"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    params = {"entities": entities, "changedSince": changed_since_iso, "minorversion": "75"}
    for attempt in range(3):
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.get(url, headers=headers, params=params)
            if r.status_code == 429 and attempt < 2:
                await asyncio.sleep(float(r.headers.get("Retry-After", "5")))
                continue
            r.raise_for_status()
            return r.json()
    r.raise_for_status()
    return r.json()


async def query_all(realm_id: str, access_token: str, entity: str, where: str = "") -> list[dict]:
    """Paginated Query API: SELECT * FROM {entity} {where} ORDER stable by Id, walking
    STARTPOSITION/MAXRESULTS until a page returns < 1000 rows. Used for the initial
    backfill and for the Account list (chart of accounts)."""
    rows: list[dict] = []
    start = 1
    page = 1000
    while True:
        sql = f"SELECT * FROM {entity} {where} STARTPOSITION {start} MAXRESULTS {page}".strip()
        data = await query(realm_id, access_token, sql)
        got = ((data.get("QueryResponse") or {}).get(entity)) or []
        rows.extend(got)
        if len(got) < page:
            break
        start += page
    return rows


async def accounts(realm_id: str, access_token: str) -> list[dict]:
    """The chart of accounts. Feeds the scan prompt and the recategorize picker."""
    return await query_all(realm_id, access_token, "Account")


async def profit_and_loss_detail(realm_id: str, access_token: str, start: str, end: str) -> dict:
    """The same ProfitAndLoss report as `profit_and_loss` — the response already carries
    the full account tree; `parse_pl` reads only the group summaries while
    `parse_pl_lines` walks the whole tree. Kept as a named entry point so the detail
    sync reads clearly (and so a future minor-version/param split has a home)."""
    return await profit_and_loss(realm_id, access_token, start, end)


# ── Parse the FULL ProfitAndLoss row tree into account-level lines (SPEC 2.2) ──
# Top-level rows carry a `group` key (Income, COGS, GrossProfit, Expenses,
# NetOperatingIncome, OtherIncome, OtherExpenses, NetOtherIncome, NetIncome). Real account
# detail lives under Income / COGS / Expenses / Other*; the Gross/Net* rows are computed
# subtotals with no detail we emit (dashboard totals come from PLSnapshot, never from
# re-summing these). Two shapes matter, both confirmed against a live ULRG report:
#   - a leaf data row: ColData is (label, amount).
#   - a PARENT account that ALSO carries its own direct posting: it appears as a section
#     (Header + nested Rows + Summary), and the Header's SECOND ColData holds that direct
#     amount (blank when the parent only groups). We must emit that direct amount as its
#     own line, else sum(lines) drops it and no longer ties to the group total.
# Sub-section Summary rows are skipped (they'd double-count). Emits
# {section, parent, label, amount, position}.
_PL_SECTION_BY_GROUP = {
    "Income": "income", "COGS": "cogs", "Expenses": "expense",
    "OtherIncome": "other", "OtherExpense": "other", "OtherExpenses": "other",
}
_PL_SKIP_GROUPS = {"GrossProfit", "NetOperatingIncome", "NetIncome", "NetOtherIncome"}


def _pl_to_amount(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _pl_header_name(row: dict) -> str:
    cd = ((row.get("Header") or {}).get("ColData")) or []
    return (cd[0].get("value") if cd else "") or ""


def _pl_header_direct(row: dict):
    """A parent account's own posted amount, carried in Header.ColData[1] (blank when the
    account is purely a grouping). Returns a float or None."""
    cd = ((row.get("Header") or {}).get("ColData")) or []
    raw = cd[1].get("value") if len(cd) > 1 else None
    if raw in (None, ""):
        return None
    return _pl_to_amount(raw)


def _pl_walk(section_row: dict, section: str, parent, out: list, pos: list) -> None:
    """Descend a group/sub-group row, emitting each leaf data row and each parent
    account's own direct posting. Sub-section Summary rows are skipped so sum(emitted
    amounts) ties to the snapshot total."""
    for r in (((section_row.get("Rows") or {}).get("Row")) or []):
        if r.get("Rows"):                               # nested sub-account group
            name = _pl_header_name(r)
            direct = _pl_header_direct(r)
            if direct is not None:                      # parent posts to itself AND groups
                out.append({"section": section, "parent": parent, "label": name,
                            "amount": direct, "position": pos[0]})
                pos[0] += 1
            child = name if not parent else f"{parent}: {name}"
            _pl_walk(r, section, child, out, pos)
        else:                                           # leaf data row
            cd = r.get("ColData") or []
            if not cd:
                continue
            label = (cd[0].get("value") or "").strip()
            if not label:
                continue
            amount = _pl_to_amount(cd[-1].get("value") if len(cd) > 1 else None)
            out.append({"section": section, "parent": parent, "label": label,
                        "amount": amount, "position": pos[0]})
            pos[0] += 1


def parse_pl_lines(report: dict) -> list[dict]:
    out: list[dict] = []
    pos = [0]
    for row in (((report.get("Rows") or {}).get("Row")) or []):
        grp = row.get("group")
        if grp in _PL_SKIP_GROUPS:
            continue
        section = _PL_SECTION_BY_GROUP.get(grp)
        if section is None:                             # unknown/computed top group
            continue
        _pl_walk(row, section, None, out, pos)
    return out


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
