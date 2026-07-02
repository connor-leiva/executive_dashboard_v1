"""Sisu client — team-wide production feed.

Auth: HTTP Basic (SISU_USERNAME : SISU_API_TOKEN). Base https://api.sisu.co/api.

Primary endpoint: GET /api/v1/team/get-team-clients — the whole team's
clients/transactions, paginated (1000/page, follow pagination.has_next). Each
record is a rich real-estate deal; we map the fields the command center needs.

Confirmed against the live schema (team 621):
- type_id "b"|"s"  -> buy|sell side
- status_code "CLOSD"|"LOSTT" (+ date-driven classification below)
- money: gross_commission_amt (GCI), trans_amt (sale price; closed_volume_amt
  is frequently null)
- dates are RFC-2822 strings, e.g. "Wed, 29 Apr 2020 00:00:00 GMT"
- lost is signalled by archive_ts (lost_reason_id is often null)
- each record embeds an `agent` object (agent_id, name, email, status N|D)
"""
from __future__ import annotations

import asyncio
import datetime as dt
from email.utils import parsedate_to_datetime

import httpx

from ..config import settings

GET_TEAM_CLIENTS = "/v1/team/get-team-clients"
SIDE = {"b": "buy", "s": "sell"}


def _auth() -> tuple[str, str]:
    return (settings.SISU_USERNAME, settings.SISU_API_TOKEN)


def parse_dt(value) -> dt.date | None:
    """Parse Sisu's RFC-2822 date strings (or ISO) into a date."""
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return parsedate_to_datetime(str(value)).date()
    except (TypeError, ValueError, IndexError):
        try:
            return dt.date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def _money(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip(value, n: int):
    """Clamp a string to a column's max length (Sisu free-text can be long)."""
    return value[:n] if isinstance(value, str) else value


async def _get_page(client: httpx.AsyncClient, page: int) -> dict:
    """Fetch one page with 429 (rate-limit) + 5xx backoff.

    IMPORTANT: get-team-clients only paginates over **POST** — the GET form
    always returns page 1 (same ~1000 rows), so a GET-based sync silently sees
    a duplicated slice of the 32k+ records. Body: {"page", "per_page": 1000}
    (per_page > 1000 breaks the endpoint). No server-side filter is supported.
    """
    url = f"{settings.SISU_BASE_URL}{GET_TEAM_CLIENTS}"
    for attempt in range(4):
        r = await client.post(url, json={"page": page, "per_page": 1000})
        if r.status_code == 429 and attempt < 3:
            await asyncio.sleep(6 * (attempt + 1))
            continue
        if r.status_code >= 500 and attempt < 3:
            await asyncio.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return r.json()


async def fetch_all_clients(concurrency: int = 8, progress=None):
    """Fetch all pages CONCURRENTLY and map to (transactions, agents).

    Each page is ~9s, so sequential paging over ~33 pages takes minutes; a
    bounded-concurrency fan-out cuts that to roughly one page's worth of
    latency × ceil(pages/concurrency). Raw pages are mapped and discarded as
    they arrive to bound memory.

    Returns (list[transaction dict], dict[agent_external_id -> agent dict]).
    """
    txns: list[dict] = []
    agents: dict[str, dict] = {}

    def absorb(rows):
        for cl in rows:
            a = map_agent(cl)
            if a:
                agents[a["external_id"]] = a
            t = map_client(cl)
            if t["external_id"] and t["external_id"] != "None":
                txns.append(t)

    async with httpx.AsyncClient(auth=_auth(), timeout=120,
                                 headers={"accept": "application/json"}) as c:
        first = await _get_page(c, 1)
        pages = int((first.get("pagination") or {}).get("pages") or 1)
        if settings.SISU_MAX_PAGES:
            pages = min(pages, settings.SISU_MAX_PAGES)
        absorb(first.get("clients") or [])
        done = [1]
        if progress:
            progress(1, pages, len(txns))
        sem = asyncio.Semaphore(concurrency)

        async def worker(pg: int):
            async with sem:
                payload = await _get_page(c, pg)
            absorb(payload.get("clients") or [])
            done[0] += 1
            if progress:
                progress(done[0], pages, len(txns))

        await asyncio.gather(*(worker(pg) for pg in range(2, pages + 1)))
    return txns, agents


def classify_status(c: dict) -> str:
    """Map a Sisu client to our status enum (date/flag-driven).

    closed (closed_dt in the past) > dead (archived/lost) > pending (under
    contract) > active. Team-configured status strings are unreliable, so we
    key off the canonical date fields, with archive_ts as the lost signal.
    """
    today = dt.date.today()
    closed = parse_dt(c.get("closed_dt"))
    if closed and closed <= today:
        return "closed"
    if c.get("archive_ts") or c.get("lost_reason_id") or c.get("status_code") == "LOSTT":
        return "dead"
    if parse_dt(c.get("uc_dt")):
        return "pending"
    return "active"


def map_agent(c: dict) -> dict | None:
    ag = c.get("agent") or {}
    aid = ag.get("agent_id") or c.get("agent_id")
    if not aid:
        return None
    name = " ".join(p for p in [ag.get("first_name"), ag.get("last_name")] if p).strip()
    return {
        "external_id": str(aid)[:64],
        "name": _clip(name or f"Agent {aid}", 200),
        "email": _clip(ag.get("email"), 255),
        "is_active": (ag.get("status") or "N") == "N",
    }


def map_client(c: dict) -> dict:
    """Map a Sisu client record to our Transaction contract."""
    buyer_names = c.get("buyer_names")
    seller_names = c.get("seller_names")
    person = " ".join(p for p in [c.get("first_name"), c.get("last_name")] if p).strip()
    aid = (c.get("agent") or {}).get("agent_id") or c.get("agent_id")
    return {
        "external_id": str(c.get("client_id") or c.get("transaction_id"))[:64],
        "side": SIDE.get(c.get("type_id")),
        "status": classify_status(c),
        "gci": _money(c.get("gross_commission_amt")) or _money(c.get("commission_amt")),
        # Agent commission (cost of sale / company dollar). Field name varies by
        # team config; best-effort across candidates. When absent, compute_financials
        # falls back to gci × business.default_agent_split.
        # agent_commission is filled by enrich_commissions() from the per-deal
        # commission-info endpoint (GCI − company dollar); left None here.
        "agent_commission": None,
        "trans_fee": _money(c.get("trans_fee_amt")),
        "sale_price": _money(c.get("trans_amt")) or _money(c.get("closed_volume_amt")),
        "address": _clip(c.get("address_1"), 300),
        "buyer_name": _clip(buyer_names or seller_names or person or None, 200),
        "buyer_email": _clip(c.get("email"), 255),
        "agent_external_id": str(aid) if aid else None,
        "sisu_status_code": _clip(c.get("status_code"), 16),
        "contract_date": parse_dt(c.get("uc_dt")),
        "close_date": parse_dt(c.get("closed_dt")),
        # Scheduled/estimated close (pending → projection). Sisu keeps the target
        # close in the close-date field until it actually closes; prefer an
        # explicit estimate field when present.
        "expected_close_date": parse_dt(c.get("est_close_dt") or c.get("estimated_close_dt")
                                        or c.get("projected_close_dt") or c.get("closed_dt")),
        "appt_set_date": parse_dt(c.get("appt_set_dt")),
        "lead_date": parse_dt(c.get("lead_dt")),
        "listing_date": parse_dt(c.get("listing_dt")),
    }


# ── Company dollar (net GCI) via the per-deal commission-info endpoint ──
# Ported from the ROI conversion dashboard (validated against Sisu's UI for this
# eXp team). CD = GCI + trans fee − EXP Risk (team) − EXP Risk (agent) − agent
# payment. agent_commission (our "cost of sale") = GCI − CD.
DEFAULT_EXP_RISK_TEAM = 69.0     # observed team-side fee when Sisu omits the adjustment
DEFAULT_EXP_RISK_AGENT = 49.0    # observed agent-side fee


def _exp_risk_fees(ci: dict) -> tuple[float, float]:
    """(team, agent) EXP Risk Management fees from commission_info.adjustments.
    The side designator is unreliable → larger is team, smaller is agent."""
    amounts: list[float] = []
    for bucket in (ci.get("adjustments") or {}).values():
        if not isinstance(bucket, list):
            continue
        for item in bucket:
            if "exp risk management" in (item.get("category") or "").lower():
                amt = _money(item.get("adjustment_value") or item.get("amount")
                             or item.get("value") or item.get("total_amount"))
                if amt and amt > 0:
                    amounts.append(amt)
    if not amounts:
        return 0.0, 0.0
    if len(amounts) == 1:
        return amounts[0], 0.0
    amounts.sort(reverse=True)
    return amounts[0], amounts[1]


def _agent_payment(ci: dict) -> float:
    """Sum summaries.final for recipients with external_type == 2 (agents)."""
    finals = (ci.get("summaries") or {}).get("final") or {}
    return sum(_money((v or {}).get("value")) for v in finals.values()
               if (v or {}).get("external_type") == 2)


def company_dollar(gci: float, trans_fee: float, ci: dict) -> float:
    et, ea = _exp_risk_fees(ci)
    if et == 0.0:
        et = DEFAULT_EXP_RISK_TEAM
    if ea == 0.0:
        ea = DEFAULT_EXP_RISK_AGENT
    return round(gci + (trans_fee or 0.0) - et - ea - _agent_payment(ci), 2)


async def _commission_info(client: httpx.AsyncClient, tid) -> dict:
    """GET /v1/client/commission-info/{tid} → the commission_info object ({} on error)."""
    url = f"{settings.SISU_BASE_URL}/v1/client/commission-info/{tid}"
    for attempt in range(3):
        try:
            r = await client.get(url)
            if r.status_code == 429 and attempt < 2:
                await asyncio.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return (r.json() or {}).get("commission_info") or {}
        except Exception:  # noqa: BLE001 — best-effort; caller falls back to the split
            if attempt >= 2:
                return {}
            await asyncio.sleep(1)
    return {}


async def enrich_commissions(mapped: list[dict], concurrency: int = 10, progress=None) -> int:
    """Populate `agent_commission` (= GCI − company dollar) for the financials-
    relevant subset (recent closed + all pending) via per-deal commission-info.
    Best-effort: a failed/empty lookup leaves it None, and compute_financials
    falls back to the business default_agent_split. Returns the count enriched."""
    today = dt.date.today()
    cutoff = dt.date(today.year, 1, 1) - dt.timedelta(days=31)   # covers YTD + last month

    def relevant(t: dict) -> bool:
        if not (t.get("gci") and t.get("external_id")):
            return False
        if t.get("status") == "pending":
            return True
        return t.get("status") == "closed" and t.get("close_date") and t["close_date"] >= cutoff

    targets = [t for t in mapped if relevant(t)]
    if not targets:
        return 0
    sem = asyncio.Semaphore(concurrency)
    done = [0]
    async with httpx.AsyncClient(auth=_auth(), timeout=45, headers={"accept": "application/json"}) as c:
        async def one(t: dict):
            async with sem:
                ci = await _commission_info(c, t["external_id"])
            if ci:
                cd = company_dollar(float(t["gci"]), float(t.get("trans_fee") or 0), ci)
                t["agent_commission"] = round(float(t["gci"]) - cd, 2)
            done[0] += 1
            if progress and done[0] % 50 == 0:
                progress(done[0], len(targets))
        await asyncio.gather(*(one(t) for t in targets))
    if progress:
        progress(len(targets), len(targets))
    return sum(1 for t in targets if t.get("agent_commission") is not None)
