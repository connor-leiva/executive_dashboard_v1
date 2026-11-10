"""Meta Marketing API writes.

The read client stays read-only. Every mutating call here can be sent as
validate_only, which is Meta's execution_options flag: the same validation as
a real write, and no change to the object when it passes.

Documented on campaign, ad set, and ad updates, on the copies edge, and on ad
creation. Image upload has no validate_only, so a dry run of a new ad never
uploads the file. URL tags live on the creative; that update is sent with
validate_only and Meta often refuses to edit a creative that already exists.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

import httpx

from .meta_ads import ZERO_DECIMAL, MetaError, _base, _headers, _raise_for_meta

# The string Ads Manager calls "URL parameters".
FIX_TAGS = (
    "utm_source={{site_source_name}}&utm_medium=paid"
    "&utm_campaign={{campaign.name}}&utm_content={{ad.id}}"
)

# What a dry run can and cannot ask Meta to validate. Shown in the UI and the report.
PATH_NOTES = {
    "update": "Pause, activate, and budget use validate_only on the campaign, ad set, or ad.",
    "copies": "Duplicate uses validate_only on the copies edge.",
    "url_tags": "URL tags are on the creative. validate_only is sent; Meta often refuses to edit one that already exists.",
    "url_tags_on_ad": "No creative id is synced, so the tag string is sent on the ad. Meta may reject the field.",
    "create_ad": "Creating an ad accepts validate_only. The image file is not uploaded; that call has no validate_only.",
}


def to_minor(dollars, currency: str = "USD") -> int:
    """Dollars on our side, minor units on Meta's. The reverse of minor_to_decimal."""
    raw = Decimal(str(dollars))
    if (currency or "USD").upper() in ZERO_DECIMAL:
        return int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return int((raw * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def plan_request(action: str, external_id: str, *, creative_id: str | None = None,
                 currency: str = "USD", params: dict | None = None,
                 account_external_id: str | None = None) -> tuple[str, dict, str]:
    """The Graph path, JSON body, and which note applies. No network."""
    params = params or {}
    if action == "pause":
        return external_id, {"status": "PAUSED"}, "update"
    if action == "activate":
        return external_id, {"status": "ACTIVE"}, "update"
    if action == "set_daily_budget":
        return external_id, {"daily_budget": to_minor(params["daily_budget"], currency)}, "update"
    if action == "set_lifetime_budget":
        return external_id, {"lifetime_budget": to_minor(params["lifetime_budget"], currency)}, "update"
    if action == "duplicate":
        return f"{external_id}/copies", {"status_option": "PAUSED"}, "copies"
    if action in ("set_url_tags", "fix_tagging"):
        tags = params.get("url_tags") or FIX_TAGS
        if creative_id:
            return creative_id, {"url_tags": tags}, "url_tags"
        return external_id, {"url_tags": tags}, "url_tags_on_ad"
    if action == "create_ad":
        acct = (account_external_id or "").lstrip("/")
        body = {
            "name": (params.get("name") or "New ad")[:400],
            "adset_id": params.get("adset_id") or external_id,
            "status": "PAUSED",
            "creative": params.get("creative") or {
                "object_story_spec": {
                    "link_data": {
                        "message": params.get("body") or "",
                        "name": params.get("headline") or "",
                        "link": params.get("link") or "https://example.com",
                    }
                }
            },
        }
        return f"{acct}/ads", body, "create_ad"
    raise ValueError(f"Unknown ad action {action}")


def is_scope_miss(err: MetaError) -> bool:
    """Meta refusing because the token cannot manage ads."""
    msg = str(err).lower()
    if err.code in (10, 200, 294):
        return True
    return "ads_management" in msg or "permission" in msg or "capability" in msg


async def graph_post(token: str, path: str, body: dict, *, validate_only: bool) -> dict:
    """POST one Graph object. validate_only adds execution_options and changes nothing."""
    payload = dict(body)
    if validate_only:
        payload["execution_options"] = ["validate_only"]
    url = f"{_base()}/{str(path).lstrip('/')}"
    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(
            url, headers={**_headers(token), "Content-Type": "application/json"}, json=payload)
    try:
        data = response.json() or {}
    except (ValueError, TypeError):
        data = {}
    if response.status_code != 200:
        err = data.get("error") or {}
        raise MetaError(str(err.get("message") or f"HTTP {response.status_code} from Meta"),
                        code=err.get("code"), subcode=err.get("error_subcode"))
    _raise_for_meta(data)
    return data
