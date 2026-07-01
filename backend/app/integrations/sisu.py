"""Sisu client — real estate production data (agents, transactions, pipeline).

PORT the exact client (endpoints + auth header + field mapping) from the
Realtor.com reporting dashboard. The scaffold below names the data contract this
app depends on; align the field mapping to the live Sisu payload on first run.
"""
from .base import get_json
from ..config import settings

SISU_HEADERS = {"Authorization": f"Bearer {settings.SISU_API_TOKEN}"}  # match existing client


async def sisu_transactions(since: str | None = None):
    # TODO: replace path/params with the exact ones used in the Realtor.com dashboard client.
    params = {"updated_since": since} if since else {}
    data = await get_json(
        f"{settings.SISU_API_BASE}/transactions", headers=SISU_HEADERS, params=params
    )
    return data.get("transactions", data if isinstance(data, list) else [])


async def sisu_agents():
    data = await get_json(f"{settings.SISU_API_BASE}/agents", headers=SISU_HEADERS)
    return data.get("agents", data if isinstance(data, list) else [])


# Data contract this app needs from each transaction (map Sisu fields -> these):
#   external_id, side(buy/sell), status(active/pending/closed/dead),
#   gci, sale_price, address, buyer_name, buyer_email, agent_external_id,
#   contract_date(ISO), close_date(ISO)
def map_transaction(t: dict) -> dict:
    return {
        "external_id": str(t.get("external_id") or t.get("id")),
        "side": t.get("side"),
        "status": t.get("status"),
        "gci": t.get("gci"),
        "sale_price": t.get("sale_price"),
        "address": t.get("address"),
        "buyer_name": t.get("buyer_name"),
        "buyer_email": t.get("buyer_email"),
        "agent_external_id": (
            str(t["agent_external_id"]) if t.get("agent_external_id") is not None else None
        ),
        "contract_date": t.get("contract_date"),
        "close_date": t.get("close_date"),
    }


def map_agent(a: dict) -> dict:
    return {
        "external_id": str(a.get("external_id") or a.get("id")),
        "name": a.get("name") or a.get("full_name") or "",
        "email": a.get("email"),
        "is_active": a.get("is_active", True),
    }
