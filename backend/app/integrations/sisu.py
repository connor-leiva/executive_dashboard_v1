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


async def get_team_clients(max_pages: int | None = None) -> list[dict]:
    """Pull every client/transaction for the team, following pagination."""
    if max_pages is None:
        max_pages = settings.SISU_MAX_PAGES or None
    out: list[dict] = []
    page = 1
    url = f"{settings.SISU_BASE_URL}{GET_TEAM_CLIENTS}"
    async with httpx.AsyncClient(auth=_auth(), timeout=90,
                                 headers={"accept": "application/json"}) as c:
        while True:
            r = await c.get(url, params={"page": page, "per_page": 1000})
            r.raise_for_status()
            payload = r.json()
            rows = payload.get("clients") or []
            out.extend(rows)
            pg = payload.get("pagination") or {}
            if not pg.get("has_next"):
                break
            if max_pages and page >= max_pages:
                break
            page = pg.get("next_num") or (page + 1)
    return out


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
        "external_id": str(aid),
        "name": name or f"Agent {aid}",
        "email": ag.get("email"),
        "is_active": (ag.get("status") or "N") == "N",
    }


def map_client(c: dict) -> dict:
    """Map a Sisu client record to our Transaction contract."""
    buyer_names = c.get("buyer_names")
    seller_names = c.get("seller_names")
    person = " ".join(p for p in [c.get("first_name"), c.get("last_name")] if p).strip()
    aid = (c.get("agent") or {}).get("agent_id") or c.get("agent_id")
    return {
        "external_id": str(c.get("client_id") or c.get("transaction_id")),
        "side": SIDE.get(c.get("type_id")),
        "status": classify_status(c),
        "gci": _money(c.get("gross_commission_amt")) or _money(c.get("commission_amt")),
        "sale_price": _money(c.get("trans_amt")) or _money(c.get("closed_volume_amt")),
        "address": c.get("address_1"),
        "buyer_name": buyer_names or seller_names or person or None,
        "buyer_email": c.get("email"),
        "agent_external_id": str(aid) if aid else None,
        "contract_date": parse_dt(c.get("uc_dt")),
        "close_date": parse_dt(c.get("closed_dt")),
        "appt_set_date": parse_dt(c.get("appt_set_dt")),
        "lead_date": parse_dt(c.get("lead_dt")),
        "sisu_status_code": c.get("status_code"),
    }
