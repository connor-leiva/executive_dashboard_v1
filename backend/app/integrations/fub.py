"""Follow Up Boss client.

REST, Basic auth: API key as username, empty password.
Base https://api.followupboss.com/v1. Paginated via ?limit=100&offset=.

Reuse note: lift the existing FUB client/field mapping from the Realtor.com
reporting dashboard rather than re-deriving. Keep the same agent/lead field
mappings so the two apps stay consistent.
"""
from .base import get_json
from ..config import settings

# FUB uses Basic auth with the API key as the username and empty password.
FUB_AUTH = (settings.FUB_API_KEY, "")


async def fub_users():
    data = await get_json(
        f"{settings.FUB_API_BASE}/users", auth=FUB_AUTH, params={"limit": 100}
    )
    return data.get("users", [])


async def fub_people(updated_after: str | None = None):
    out, offset = [], 0
    while True:
        params = {"limit": 100, "offset": offset, "sort": "updated"}
        if updated_after:
            params["updatedAfter"] = updated_after
        data = await get_json(
            f"{settings.FUB_API_BASE}/people", auth=FUB_AUTH, params=params
        )
        rows = data.get("people", [])
        out.extend(rows)
        if len(rows) < 100:
            break
        offset += 100
    return out


# Data contract this app needs (map FUB fields -> these):
#   agents:  external_id, name, email, is_active
#   leads:   external_id, stage, agent_external_id, created_at_src (ISO date)
def map_user(u: dict) -> dict:
    return {
        "external_id": str(u.get("id")),
        "name": u.get("name") or f'{u.get("firstName","")} {u.get("lastName","")}'.strip(),
        "email": u.get("email"),
        "is_active": (u.get("status") or "Active").lower() == "active",
    }


def map_person(p: dict) -> dict:
    assigned = p.get("assignedUserId")
    created = p.get("created") or p.get("createdAt")
    return {
        "external_id": str(p.get("id")),
        "stage": p.get("stage"),
        "agent_external_id": str(assigned) if assigned is not None else None,
        "created_at_src": (created or "")[:10] or None,
    }
