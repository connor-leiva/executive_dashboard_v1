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
import contextvars
import datetime as dt
import json
import random
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl

import httpx

from ..config import settings

# Throttle codes. No documented Retry-After header accompanies them.
THROTTLE_CODES = {17, 80000, 80003}
# "Please reduce the amount of data you're asking for, then retry your request." Meta's answer
# when a result set is too large for one page - most often the /ads edge with a wide creative{}
# expansion. It is NOT a rate limit and NOT a permission problem, and retrying the identical
# request forever will never work: the remedy is a smaller page.
REDUCE_DATA_CODE = 1
REDUCE_DATA_TEXT = "reduce the amount of data"
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4
# Above this, waiting in-process is the wrong move. Meta reports the wait for a spent quota in
# tens of minutes; sleeping that inside a request holds a worker hostage, and the retry that
# follows arrives at the same wall having spent one more call to find it there. Past this
# threshold the client raises with the wait attached and lets the caller park the account.
MAX_RETRY_WAIT = 120.0

# Currencies with no minor unit. Budgets arrive in minor units and assuming /100 for these
# inflates every budget by a hundred times.
ZERO_DECIMAL = {"JPY", "KRW", "VND", "CLP", "ISK", "HUF", "TWD", "UGX", "XAF", "XOF", "XPF"}


class MetaError(RuntimeError):
    """A Meta `error` object, carried with its message and code.

    Raised rather than swallowed so a caller can tell a rate-limited pull from a paused account.
    Returning [] on failure is the specific mistake that makes an outage look like a quiet month.
    """

    def __init__(self, message: str, code: int | None = None, subcode: int | None = None,
                 retry_after_s: int | None = None, tier: str | None = None):
        super().__init__(message)
        self.code, self.subcode = code, subcode
        # The app's Marketing API access tier as Meta reported it on the failing response.
        self.tier = tier
        # How long Meta said to wait, in seconds, when it said so. Carried on the exception so the
        # SYNC can park the account for that long instead of rediscovering the wall every tick.
        self.retry_after_s = retry_after_s

    @property
    def is_throttle(self) -> bool:
        return self.code in THROTTLE_CODES

    @property
    def wants_smaller_page(self) -> bool:
        """Meta asking for a smaller request rather than a slower one."""
        return REDUCE_DATA_TEXT in str(self).lower()


def _base() -> str:
    """The ONLY place a version string appears."""
    return f"https://graph.facebook.com/{settings.META_GRAPH_VERSION}"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


# The last access tier any response disclosed. Meta sends `ads_api_access_tier` on /campaigns
# and NOT on /ads, so the call that fails is routinely the one that cannot say what tier it is
# on. The tier is a per-app constant, so remembering the last one seen costs nothing and turns a
# hedged error message into a definite one. Stale or absent degrades to "unknown", never to a
# wrong claim.
# ContextVars, not module globals. Two workspaces can sync concurrently, each against its own
# Meta app with its own tier and its own allowance; a shared global would let one tenant's tier
# appear in another's error message and let two syncs decrement the same budget. ContextVars are
# task-local in asyncio, so each sync gets its own.
_TIER: contextvars.ContextVar[str | None] = contextvars.ContextVar("meta_tier", default=None)


def last_seen_tier() -> str | None:
    return _TIER.get()


class BudgetExhausted(MetaError):
    """This sync hit its own request ceiling. NOT a Meta refusal - Meta never saw the call - so
    it must not park the integration or be reported as a rate limit."""


_SPENT: contextvars.ContextVar[int] = contextvars.ContextVar("meta_spent", default=0)
_CAP: contextvars.ContextVar[int] = contextvars.ContextVar("meta_cap", default=0)


def start_budget(n: int) -> None:
    _CAP.set(int(n))
    _SPENT.set(0)


def budget_spent() -> int:
    return _SPENT.get()


def access_tier(resp: httpx.Response) -> str | None:
    """The app's Marketing API access tier, which Meta reports on EVERY ads response.

    `development_access` is the default and its hourly allowance is roughly a hundred calls -
    fine for a person pressing refresh, far too small for a scheduled sync. That single word
    explains a whole class of failure, and it was sitting in a response header the entire time
    this was being diagnosed as something else. Surfaced so nobody has to guess at it again.
    """
    raw = resp.headers.get("X-Business-Use-Case-Usage") or resp.headers.get(
        "x-business-use-case-usage")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        for entries in data.values():
            for e in entries if isinstance(entries, list) else []:
                if isinstance(e, dict) and e.get("ads_api_access_tier"):
                    return str(e["ads_api_access_tier"])
    except (ValueError, TypeError, AttributeError):
        return None
    return None


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
    cap = _CAP.get()
    spent = _SPENT.get()
    if cap and spent >= cap:
        raise BudgetExhausted(
            f"stopped after {spent} requests, this sync's ceiling (ADS_MAX_CALLS_PER_SYNC). "
            f"Partial data from this run is kept.")
    _SPENT.set(spent + 1)

    delay = 0.0
    for attempt in range(MAX_ATTEMPTS):
        if delay:
            await asyncio.sleep(delay)
        r = await client.get(url, headers=_headers(token), params=params)
        pct = usage_pct(r)
        seen = access_tier(r)
        if seen:
            _TIER.set(seen)

        if r.status_code == 200:
            body = r.json() or {}
            try:
                _raise_for_meta(body)
            except MetaError as e:                  # Meta returns errors on 200 as well as 4xx
                e.tier = e.tier or access_tier(r) or last_seen_tier()
                raise
            # Back off BEFORE the next call rather than after being cut off. Above the configured
            # ceiling the budget is nearly spent, and the next request is the expensive one.
            if pct >= settings.ADS_BUC_BACKOFF_PCT:
                # Say it out loud. BUC figures are PERCENTAGES of the hourly allowance, so this
                # line is the difference between knowing the sync fits under the ceiling and
                # inferring it from whether anything broke.
                print(f"[meta_ads] BUC usage {pct:.0f}% (tier={access_tier(r) or 'unknown'})",
                      flush=True)
                await asyncio.sleep(min(30.0, (pct - settings.ADS_BUC_BACKOFF_PCT) * 0.5 + 1.0))
            return body

        body = {}
        try:
            body = r.json() or {}
        except (ValueError, TypeError):
            pass
        code = ((body.get("error") or {}).get("code"))
        throttled = code in THROTTLE_CODES or r.status_code == 429

        wait = retry_after(r) if throttled else None
        # A quota with a long recovery is not a retry case. Ten scheduled syncs against an
        # exhausted quota, each retrying four times behind a five-minute clamp, is how an account
        # stays throttled indefinitely: every attempt to recover is itself a call it cannot
        # afford. Surface the wait and stop touching Meta.
        if wait and wait > MAX_RETRY_WAIT:
            raise MetaError(
                f"{(body.get('error') or {}).get('message') or 'rate limited'} "
                f"(Meta says {wait // 60} min)", code=code, retry_after_s=wait,
                tier=access_tier(r) or last_seen_tier())

        if attempt < MAX_ATTEMPTS - 1 and (throttled or r.status_code in RETRY_STATUS):
            # Exponential with jitter. The jitter matters when several accounts sync together:
            # without it they retry in lockstep and re-throttle each other.
            delay = float(wait) if wait else (2.0 ** attempt) + random.uniform(0, 0.5)
            delay = min(delay, MAX_RETRY_WAIT)
            continue

        if throttled:
            raise MetaError(
                f"{(body.get('error') or {}).get('message') or 'rate limited'}",
                code=code, retry_after_s=wait, tier=access_tier(r) or last_seen_tier())
        _raise_for_meta(body)
        raise MetaError(f"HTTP {r.status_code} from Meta", code=code)
    raise MetaError("exhausted retries against Meta")


MIN_PAGE = 5


async def _paginate(url: str, params: dict, token: str, max_pages: int = 400) -> list[dict]:
    """Follow `paging.next` and return every row. Raises rather than truncating silently.

    HALVES THE PAGE SIZE AND RETRIES when Meta says the request is too large. That refusal is not
    a rate limit and not a permissions problem - it is Meta declining to assemble a result set
    that big, and no amount of waiting or retrying the same call fixes it. The /ads edge with a
    wide creative{} expansion trips it on a real account, which is exactly how this module's
    first live sync failed: campaigns 0, ads 0, and an error message that reads like a quota.

    Restarts the walk from the first page on a resize. Meta's `next` cursors encode the page size
    they were minted with, so continuing from one after shrinking would keep asking for the size
    that just failed.
    """
    limit = int(params.get("limit") or 100)
    while True:
        out: list[dict] = []
        try:
            async with httpx.AsyncClient(timeout=90) as c:
                page = 0
                next_url, next_params = url, {**params, "limit": limit}
                while next_url and page < max_pages:
                    body = await _get(c, next_url, next_params, token)
                    out.extend(body.get("data") or [])
                    page += 1
                    nxt = ((body.get("paging") or {}).get("next"))
                    if not nxt:
                        break
                    # `next` is fully-formed; re-sending params would duplicate them.
                    next_url, next_params = nxt, {}
            return out
        except BudgetExhausted:
            raise
        except MetaError as e:
            if not e.wants_smaller_page or limit <= MIN_PAGE:
                raise
            limit = max(MIN_PAGE, limit // 2)
            print(f"[meta_ads] Meta refused the page size; retrying at limit={limit}", flush=True)


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
    """The ad dimension, WITHOUT the creative expansion.

    The creative used to be nested here - ten fields per ad, in one account-wide pass. It read
    as an efficiency, and on a real account Meta simply refuses to assemble it: the first live
    sync died on this call and took the campaigns already fetched down with it.

    Sarah's standalone dashboard never makes this request at all. It reads campaign_name and
    ad_name straight off the insight rows and then fetches creatives ONE AD AT A TIME for the
    two dozen it actually displays. That is why hers returns data and this did not.

    NO LONGER CALLED BY THE ROUTINE SYNC. Separating the creative out was not enough: this edge
    ignores any date range and returns every ad the account has ever had - 471 live, against 13
    with activity in a given week - across pages _paginate must restart from the beginning
    whenever Meta refuses a size. It failed every live sync. The dimension is now derived from
    the insights report the way Sarah's page does it (ads_sync._derive_dimensions).

    Kept as the only source of status/effective_status, should anything ever need them. Anything
    calling it should work out the page count first.
    """
    return await _paginate(f"{_base()}/{account_id}/ads", {
        "fields": "id,name,status,effective_status,adset{id,name},campaign{id}",
        "limit": 100,
    }, token)


async def ad_creatives(token: str, ad_ids: list[str]) -> dict[str, dict]:
    """Creative detail for specific ads, one request each. ENRICHMENT, never load-bearing.

    Per-ad rather than a bulk expansion because that is the shape Meta reliably serves, and
    capped by the caller because thumbnails are worth a bounded number of requests and no more.
    A failure on one ad costs that ad's thumbnail and nothing else - `url_tags`, which the
    attribution readiness check reads, simply stays unknown for it.
    """
    out: dict[str, dict] = {}
    if not ad_ids:
        return out
    fields = ("creative{id,name,title,body,thumbnail_url,image_hash,image_url,"
              "object_story_spec,effective_object_story_id,url_tags}")
    async with httpx.AsyncClient(timeout=45) as c:
        for aid in ad_ids:
            try:
                body = await _get(c, f"{_base()}/{aid}", {"fields": fields}, token)
                if body.get("creative"):
                    out[str(aid)] = body["creative"]
            except (MetaError, httpx.HTTPError, ValueError):
                continue
    return out


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
    """The best image available for this ad, WITHOUT spending another request.

    `thumbnail_url` is 64x64. Measured, not assumed - every URL Meta returned carried `p64x64`,
    and the creative wall was upscaling that across a 220px tile, which is why the wall shipped
    looking like a fax. Preferring it over `image_url`, as this did, picked the smallest option
    on offer.

    Order, and why each rung exists:

      1. `image_url` - the uploaded asset for a single-image ad. Full resolution.
      2. `object_story_spec.video_data.image_url` - the POSTER for a video ad, which has no
         top-level image_url at all. Measured at 170-300KB against the 64x64 thumbnail, and it
         costs nothing: object_story_spec is already in the fields ad_creatives requests.
      3. `link_data.picture`, then the first child attachment, for link and carousel ads.
      4. `thumbnail_url` last, because 64px beats nothing.

    Two paths were tried and rejected against the live account rather than in the abstract:
    `effective_object_story_id` -> `full_picture` (what the standalone HTML dashboard uses)
    returns "(#100) Missing permissions" for an ads-only System User - it needs Page permissions
    this token does not have and should not need. And `thumbnail_width`/`thumbnail_height` are
    ignored on the AD endpoint, though they do work on /{creative_id}; that would be one extra
    request per ad to obtain something worse than rung 2.
    """
    c = creative or {}
    oss = c.get("object_story_spec") or {}
    video = oss.get("video_data") or {}
    link = oss.get("link_data") or {}
    kids = link.get("child_attachments") or []
    first_kid = kids[0] if (kids and isinstance(kids[0], dict)) else {}
    for candidate in (c.get("image_url"),
                      video.get("image_url"),
                      link.get("picture"),
                      first_kid.get("picture"),
                      c.get("thumbnail_url")):
        if candidate:
            return str(candidate)
    return None


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
