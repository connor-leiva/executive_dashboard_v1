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
import re
import datetime as dt
from email.utils import parsedate_to_datetime

import httpx

from ..config import settings

GET_TEAM_CLIENTS = "/v1/team/get-team-clients"
GET_TEAM_VENDORS = "/v1/team/get-team-vendors"
SIDE = {"b": "buy", "s": "sell"}
_CASH_RE = re.compile(r"cash|seller finance|no lender", re.I)


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


def _digits10(value) -> str | None:
    """Last 10 digits of a phone (for cross-system matching), else None."""
    if not value:
        return None
    d = "".join(ch for ch in str(value) if ch.isdigit())
    return d[-10:] if len(d) >= 10 else None


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


async def _agent_group_ids(client: httpx.AsyncClient, agent_id) -> list[int] | None:
    """One agent's CURRENT Sisu group_ids via GET /v1/agent/edit-agent/{id} (agent.agent_groups,
    is_included). The client feed carries no sub-team, but this does — it's the live source for
    per-team scorecard attribution (office/pod/tier group ids). None on any failure or app-error, so
    a transient blip leaves the stored roster untouched rather than wiping it."""
    url = f"{settings.SISU_BASE_URL}/v1/agent/edit-agent/{agent_id}"
    for attempt in range(3):                            # same 429/5xx backoff as the other Sisu calls
        try:
            r = await client.get(url)
            if r.status_code == 429 and attempt < 2:
                await asyncio.sleep(5 * (attempt + 1))
                continue
            if r.status_code >= 500 and attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            body = r.json() or {}
            if body.get("status_code") not in (0, None) or "agent" not in body:
                return None                             # Sisu app-level error envelope (status_code != 0)
            groups = (body.get("agent") or {}).get("agent_groups") or []
            return sorted({int(g["group_id"]) for g in groups
                           if g.get("is_included") and g.get("group_id") is not None})
        except Exception:  # noqa: BLE001 — best-effort; caller keeps the prior value on failure
            if attempt >= 2:
                return None
            await asyncio.sleep(1)
    return None


async def fetch_agent_groups(agent_external_ids, concurrency: int = 8) -> dict[str, list[int] | None]:
    """Concurrently fetch each agent's Sisu group_ids. Returns {external_id: [group_id,...] | None};
    None means the fetch failed for that agent (leave its stored memberships as-is)."""
    out: dict[str, list[int] | None] = {}
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(auth=_auth(), timeout=60, headers={"accept": "application/json"}) as c:
        async def one(aid):
            async with sem:
                out[str(aid)] = await _agent_group_ids(c, aid)
        await asyncio.gather(*(one(aid) for aid in agent_external_ids))
    return out


async def get_team_vendors() -> list[dict]:
    """The team's vendor directory (mortgage/title/warranty/… companies). Each has
    vendor_id, name, vendor_type (M=mortgage, T=title, W=warranty, H=inspection,
    I=insurance). Used to resolve which mortgage-vendor ids are Sympli."""
    url = f"{settings.SISU_BASE_URL}{GET_TEAM_VENDORS}"
    async with httpx.AsyncClient(auth=_auth(), timeout=60,
                                 headers={"accept": "application/json"}) as c:
        for attempt in range(4):
            r = await c.post(url, json={})
            if r.status_code in (429,) or (r.status_code >= 500 and attempt < 3):
                await asyncio.sleep(2 ** attempt + 1)
                continue
            r.raise_for_status()
            data = r.json()
            return data.get("vendors") or data.get("data") or (data if isinstance(data, list) else [])
    return []


def resolve_vendor_config(vendors: list[dict], sympli_match: str = "sympli") -> dict:
    """From the vendor directory, derive the ids the flywheel needs:
      sympli_mortgage_vids — mortgage vendors whose name is Sympli (auto-maintained
                             as LOs are added), so a ULRG deal picking one = captured
      cash_vids            — cash / seller-finance mortgage rows (never financeable →
                             excluded from the attach-rate denominator)
      lender_names         — {vid: name} for EVERY mortgage vendor (competitor breakdown)
    """
    sympli, cash, lender_names = [], [], {}
    for v in vendors:
        vid = v.get("vendor_id")
        if vid is None:
            continue
        name = (v.get("name") or "").strip()
        low = name.lower()
        is_mortgage = str(v.get("vendor_type") or "") == "M" or "mortgage" in low
        if not is_mortgage:
            continue
        lender_names[str(vid)] = name
        if sympli_match in low:
            sympli.append(vid)
        if _CASH_RE.search(low):
            cash.append(vid)
    return {"sympli_mortgage_vids": sorted(set(sympli)),
            "cash_vids": sorted(set(cash)),
            "lender_names": lender_names}


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
        # Attachment-flywheel signals: the mortgage vendor the agent selected (the
        # authoritative capture signal), the co-borrower email, and the phone — extra
        # keys to match a ULRG buyer to a funded Sympli loan.
        "mortgage_vid": (int(c["mortgage_company_vid"])
                         if str(c.get("mortgage_company_vid") or "").isdigit() else None),
        # title vendor the deal used — drives the Meraki (title) attach rate. isdigit() is False
        # for Sisu's negative sentinels (-1 "none"/-2 "unknown"), so those land as None (not a vendor).
        "title_vid": (int(c["title_company_vid"])
                      if str(c.get("title_company_vid") or "").isdigit() else None),
        "buyer_email2": _clip(c.get("second_contact_email"), 255),
        "buyer_phone": _digits10(c.get("mobile_phone")),
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
        "appt_met_date": parse_dt(c.get("appt_dt")),        # the appointment (held) date → Appointments Met
        "signed_date": parse_dt(c.get("signed_dt")),        # buyer/listing agreement signed → Clients Signed
        "lead_date": parse_dt(c.get("lead_dt")),
        "listing_date": parse_dt(c.get("listing_dt")),
    }


# ── Net GCI (company dollar) via the per-deal commission-info endpoint ──
# Sisu's commission-info exposes each recipient's cut. NET GCI (what the team
# keeps) = `team_income` — the external_type==1 recipient's take. It is populated
# for BOTH closed (final) and PENDING deals (the projected split), verified live:
#   pending 6614734 GCI 22,625 → team_income 6,787.50 (agent 15,837.50, 70/30)
#   pending 6613469 GCI 17,100 → team_income 3,420    (20% team split)
# So agent_commission (our "cost of sale") = GCI − team_income, and we enrich
# closed AND pending. The flat default_agent_split is only a last-resort fallback
# for the rare deal whose commission-info can't be read.
def _num(value) -> float:
    """Money → float, tolerant of "$1,234.50" / "null" / None. 0.0 when absent."""
    if value in (None, "", "null"):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def team_income(ci: dict) -> float | None:
    """Company dollar = the team's take (external_type==1) from commission-info.
    Prefer the top-level `team_income`; else sum the external_type==1 recipients in
    summaries.final. None when commission-info is absent/unreadable."""
    if not ci:
        return None
    if ci.get("team_income") is not None:
        return _num(ci.get("team_income"))
    finals = (ci.get("summaries") or {}).get("final") or {}
    vals = [_num((v or {}).get("value")) for v in finals.values() if (v or {}).get("external_type") == 1]
    return sum(vals) if vals else None


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
    """Populate `agent_commission` (= GCI − team_income) from Sisu commission-info,
    for recently-CLOSED and ALL PENDING deals (both carry a team_income; pending's
    is the projected split). Best-effort per deal; returns the count enriched."""
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
            ti = team_income(ci)
            if ti is not None:
                t["agent_commission"] = round(float(t["gci"]) - ti, 2)
            done[0] += 1
            if progress and done[0] % 50 == 0:
                progress(done[0], len(targets))
        await asyncio.gather(*(one(t) for t in targets))
    if progress:
        progress(len(targets), len(targets))
    return sum(1 for t in targets if t.get("agent_commission") is not None)
