"""Meta Marketing API client (READ ONLY). SPEC-ads-module.md Part 7.

Three facts that go stale silently, recorded here because a reader will not otherwise know to
check them:

VERSION. Pinned once, in settings.META_GRAPH_VERSION, and read only by _base(). An EXPIRED
Marketing API version does not error - Meta executes the call as a later version and returns a
200. So the failure mode is a number that changed, not an exception anybody notices. Graph and
Marketing run SEPARATE clocks for the same version number: v24.0 dies on the Marketing clock
2026-10-06 and lives on the Graph clock until 2028.

AUTH. A System User token: static bearer, does not expire unless revoked, scoped `ads_read` only.
This module never writes, so `ads_management` must never be granted. Follows the GHL and Arive
paste-a-token pattern rather than the QuickBooks OAuth dance - there is no callback route.

RATE LIMITS. Business Use Case, per app per ad account, with separate pools per use case.
`X-Business-Use-Case-Usage` is a JSON map of business-object-id to an ARRAY of up to 32 objects.
It is not a flat object and not an integer, and a parser written for `X-App-Usage` will not
survive it. Throttling begins when ANY of call_count / total_cputime / total_time reaches 100 -
steering on call_count alone is a known trap, because a few expensive insights queries can put
total_cputime at 100 while call_count sits in the teens.

MUST NOT: write anything except the async report run; inline a version string outside _base();
or collapse a Meta `error` object into an empty list. An empty result and a failed call have to
stay distinguishable, or a rate-limited pull reads as a paused account.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import random
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl

import httpx

from ..config import settings

# Throttle codes. No documented Retry-After header accompanies them.
THROTTLE_CODES = {17, 80000, 80003}
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4

# Currencies with no minor unit. Budgets arrive in minor units and assuming /100 for these
# inflates every budget by a hundred times.
ZERO_DECIMAL = {"JPY", "KRW", "VND", "CLP", "ISK", "HUF", "TWD", "UGX", "XAF", "XOF", "XPF"}


class MetaError(RuntimeError):
    """A Meta `error` object, carried with its message and code.

    Raised rather than swallowed so a caller can tell a rate-limited pull from a paused account.
    Returning [] on failure is the specific mistake that makes an outage look like a quiet month.
    """

    def __init__(self, message: str, code: int | None = None, subcode: int | None = None):
        super().__init__(message)
        self.code, self.subcode = code, subcode

    @property
    def is_throttle(self) -> bool:
        return self.code in THROTTLE_CODES


def _base() -> str:
    """The ONLY place a version string appears."""
    return f"https://graph.facebook.com/{settings.META_GRAPH_VERSION}"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def usage_pct(resp: httpx.Response) -> float:
    """Highest BUC usage percentage across every object in the header. 0.0 when absent or
    malformed, and it NEVER raises - a header this shape is not worth failing a sync over.

    Shape: {"<business-object-id>": [{"call_count": 12, "total_cputime": 97, "total_time": 40},
    ...]}. The maximum of all three across all objects is the number to steer on, because any one
    of them reaching 100 throttles the app.
    """
    raw = resp.headers.get("X-Business-Use-Case-Usage") or resp.headers.get(
        "x-business-use-case-usage")
    if not raw:
        return 0.0
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return 0.0
        worst = 0.0
        for entries in data.values():
            for e in entries if isinstance(entries, list) else []:
                if not isinstance(e, dict):
                    continue
                for k in ("call_count", "total_cputime", "total_time"):
                    try:
                        worst = max(worst, float(e.get(k) or 0))
                    except (TypeError, ValueError):
                        continue
        return worst
    except (ValueError, TypeError, AttributeError):
        return 0.0


def retry_after(resp: httpx.Response) -> int | None:
    """`estimated_time_to_regain_access`, in seconds, when Meta says how long it will be."""
    raw = resp.headers.get("X-Business-Use-Case-Usage") or resp.headers.get(
        "x-business-use-case-usage")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        best = None
        for entries in (data or {}).values():
            for e in entries if isinstance(entries, list) else []:
                v = (e or {}).get("estimated_time_to_regain_access")
                if v:
                    best = max(best or 0, int(v) * 60)     # Meta reports it in MINUTES
        return best
    except (ValueError, TypeError, AttributeError):
        return None


def _raise_for_meta(payload: dict) -> None:
    err = (payload or {}).get("error")
    if err:
        raise MetaError(str(err.get("message") or "Meta API error"),
                        code=err.get("code"), subcode=err.get("error_subcode"))


async def _get(client: httpx.AsyncClient, url: str, params: dict, token: str) -> dict:
    """One GET, with backoff. Raises MetaError on a Meta error object, never returns a sentinel."""
    delay = 0.0
    for attempt in range(MAX_ATTEMPTS):
        if delay:
            await asyncio.sleep(delay)
        r = await client.get(url, headers=_headers(token), params=params)
        pct = usage_pct(r)

        if r.status_code == 200:
            body = r.json() or {}
            _raise_for_meta(body)
            # Back off BEFORE the next call rather than after being cut off. Above the configured
            # ceiling the budget is nearly spent, and the next request is the expensive one.
            if pct >= settings.ADS_BUC_BACKOFF_PCT:
                await asyncio.sleep(min(30.0, (pct - settings.ADS_BUC_BACKOFF_PCT) * 0.5 + 1.0))
            return body

        body = {}
        try:
            body = r.json() or {}
        except (ValueError, TypeError):
            pass
        code = ((body.get("error") or {}).get("code"))
        throttled = code in THROTTLE_CODES or r.status_code == 429

        if attempt < MAX_ATTEMPTS - 1 and (throttled or r.status_code in RETRY_STATUS):
            wait = retry_after(r) if throttled else None
            # Exponential with jitter. The jitter matters when several accounts sync together:
            # without it they retry in lockstep and re-throttle each other.
            delay = float(wait) if wait else (2.0 ** attempt) + random.uniform(0, 0.5)
            delay = min(delay, 300.0)
            continue

        _raise_for_meta(body)
        raise MetaError(f"HTTP {r.status_code} from Meta", code=code)
    raise MetaError("exhausted retries against Meta")


async def _paginate(url: str, params: dict, token: str, max_pages: int = 200) -> list[dict]:
    """Follow `paging.next` and return every row. Raises rather than truncating silently."""
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=90) as c:
        page, next_url, next_params = 0, url, dict(params)
        while next_url and page < max_pages:
            body = await _get(c, next_url, next_params, token)
            out.extend(body.get("data") or [])
            page += 1
            nxt = ((body.get("paging") or {}).get("next"))
            if not nxt:
                break
            next_url, next_params = nxt, {}      # `next` is fully-formed; re-sending params dupes them
    return out


# ── endpoints ─────────────────────────────────────────────────────────────────────────
async def ping(token: str, account_id: str) -> dict:
    """Connect-time validation and account metadata.

    Called at connect the way the Stripe providers validate their keys, so a bad or mis-scoped
    token fails while somebody is looking at it rather than silently no-opping at 3am.
    """
    async with httpx.AsyncClient(timeout=30) as c:
        return await _get(c, f"{_base()}/{account_id}",
                          {"fields": "name,currency,timezone_name,account_status"}, token)


CAMPAIGN_FIELDS = ("campaign_id,campaign_name,spend,impressions,reach,clicks,inline_link_clicks,"
                   "frequency,actions,action_values,purchase_roas")
AD_FIELDS = ("ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,spend,impressions,"
             "clicks,inline_link_clicks,actions,action_values")


async def insights(token: str, account_id: str, level: str,
                   since: dt.date, until: dt.date) -> list[dict]:
    """Daily insight rows for one level over a window.

    time_increment=1 returns one row per object PER DAY at no extra request cost, and daily grain
    is what makes trend, any-period reads without a new API call, history past Meta's retention,
    and above all cohort analysis possible.

    use_account_attribution_setting=true so the numbers match Ads Manager - the difference
    between a dashboard somebody trusts and one they check against another dashboard.
    """
    params = {
        "level": level,
        "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()}),
        "time_increment": 1,
        "use_account_attribution_setting": "true",
        "fields": CAMPAIGN_FIELDS if level == "campaign" else AD_FIELDS,
        "limit": 500,
    }
    return await _paginate(f"{_base()}/{account_id}/insights", params, token)


async def campaigns(token: str, account_id: str) -> list[dict]:
    """The campaign dimension. Status and budget are not on insight rows."""
    return await _paginate(f"{_base()}/{account_id}/campaigns", {
        "fields": ("id,name,objective,status,effective_status,daily_budget,lifetime_budget,"
                   "start_time,stop_time"),
        "limit": 500,
    }, token)


async def ads(token: str, account_id: str) -> list[dict]:
    """The ad dimension with `creative{}` expanded.

    One account-level pass in a handful of paginated calls returns what the original dashboard
    spent 48 per-ad requests to assemble. `url_tags` on this expansion is what the attribution
    readiness check reads.
    """
    return await _paginate(f"{_base()}/{account_id}/ads", {
        "fields": ("id,name,status,effective_status,adset{id,name},campaign{id},"
                   "creative{id,name,title,body,thumbnail_url,image_hash,image_url,"
                   "object_story_spec,effective_object_story_id,url_tags}"),
        "limit": 200,
    }, token)


async def story_image(token: str, story_id: str) -> str | None:
    """The effective_object_story_id hop for a full-resolution image. None on ANY failure - a
    missing thumbnail is a cosmetic gap and must never fail a sync."""
    if not story_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            body = await _get(c, f"{_base()}/{story_id}",
                              {"fields": "full_picture,picture"}, token)
        return body.get("full_picture") or body.get("picture") or None
    except (MetaError, httpx.HTTPError, ValueError):
        return None


# ── pure helpers ──────────────────────────────────────────────────────────────────────
def action_count(actions: list | dict | None, wanted: set[str] | list[str]) -> int:
    """Sum the values of the named action types. Tolerates both envelope shapes Meta ships."""
    if not actions or not wanted:
        return 0
    rows = actions if isinstance(actions, list) else (actions.get("data") or [])
    keep = {str(w).lower() for w in wanted}
    total = 0
    for a in rows:
        if isinstance(a, dict) and str(a.get("action_type") or "").lower() in keep:
            try:
                total += int(float(a.get("value") or 0))
            except (TypeError, ValueError):
                continue
    return total


def minor_to_decimal(value, currency: str = "USD") -> Decimal | None:
    """Budgets arrive in MINOR units. Do not assume 100 - zero-decimal currencies exist, and
    dividing a yen budget by 100 understates it by two orders of magnitude."""
    if value in (None, ""):
        return None
    try:
        raw = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        # InvalidOperation is an ArithmeticError, NOT a ValueError - catching the obvious two
        # leaves a malformed budget from Meta raising straight out of the sync.
        return None
    return raw if (currency or "USD").upper() in ZERO_DECIMAL else raw / Decimal("100")


def creative_headline(creative: dict | None) -> str | None:
    """The headline, in the order Meta actually populates it."""
    c = creative or {}
    spec = c.get("object_story_spec") or {}
    for path in (("link_data", "name"), ("video_data", "title")):
        node = spec.get(path[0]) or {}
        if node.get(path[1]):
            return str(node[path[1]])[:300]
    return str(c.get("title"))[:300] if c.get("title") else None


def creative_thumb(creative: dict | None) -> str | None:
    c = creative or {}
    return c.get("thumbnail_url") or c.get("image_url") or None


def parse_url_tags(url_tags: str | None) -> dict:
    """The utm_* map an ad carries, for the readiness check.

    Ad-level revenue is impossible without utm_content={{ad.id}}, so this is what tells a tenant
    WHY their creative wall has no revenue column rather than leaving them to guess.
    """
    if not url_tags:
        return {}
    try:
        return {k.lower(): v for k, v in parse_qsl(str(url_tags).lstrip("?&"), keep_blank_values=False)}
    except (ValueError, TypeError):
        return {}


def has_ad_level_tagging(url_tags: str | None) -> bool:
    """True when this ad carries an ad-identifying utm_content. Accepts the macro as written in
    the ad and the resolved id, because a template and a live ad look different."""
    content = (parse_url_tags(url_tags).get("utm_content") or "").strip().lower()
    return bool(content) and ("{{ad.id}}" in content or "ad.id" in content or content.isdigit())
