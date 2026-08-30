"""Ads read service. SPEC-ads-module.md Part 10. Pure read - never calls Meta.

THE RATIO LAW LIVES HERE, and it is the most important correctness rule in the module.

A mean of daily CTRs is not the period CTR. A mean of per-campaign CPLs is not the account CPL.
Both mistakes produce a number that looks plausible, sits next to correct numbers, and is wrong
in a direction nobody can predict. The defence is structural rather than careful:

  1. AdInsightDaily stores NO rates. Meta returns ctr, cpm and cpc; none are persisted. There is
     therefore no rate in the table for anyone to accidentally average.
  2. Every ratio in this module - click metrics, funnel conversion, CAC, ROAS - comes out of
     rate(), from SUMMED numerators and denominators.

MULTI-TENANT. Every query here takes tenant_id and filters on it. The ads tables are indexed
(tenant_id, ad_account_id, occurred_on) so a workspace's reads never scan another's rows, and an
ad account is reachable only through its own tenant.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

Number = int | float | Decimal | None


def rate(numerator: Number, denominator: Number, scale: float = 1.0) -> float | None:
    """The ONLY way a ratio is produced in this module.

    Computed from SUMMED components, never averaged from per-row rates.

    Returns None - not 0 - on a zero or missing denominator, because "no closes yet" and "a CAC
    of zero" are different facts and the UI renders them differently. Returning 0 here would make
    an account with no enrollments display the best CAC on the page.
    """
    if not denominator:
        return None
    try:
        return (float(numerator or 0) / float(denominator)) * scale
    except (TypeError, ValueError, ZeroDivisionError):
        return None


# The click layer's rates, each named once so no caller re-derives one slightly differently.
def ctr(link_clicks: Number, impressions: Number) -> float | None:
    """Link-click through rate, as a percentage.

    inline_link_clicks, NOT clicks. Meta's `clicks` counts reactions, comments, shares and
    page-name clicks, so the mockup's "Link clicks" label was measuring something else. Sarah's
    headline CTR drops when this ships - that is a correction, and it needs to reach her as a
    note before she meets it as a number.
    """
    return rate(link_clicks, impressions, 100)


def cpm(spend: Number, impressions: Number) -> float | None:
    return rate(spend, impressions, 1000)


def cpc(spend: Number, link_clicks: Number) -> float | None:
    return rate(spend, link_clicks)


def cpl(spend: Number, leads: Number) -> float | None:
    return rate(spend, leads)


def cost_per(spend: Number, count: Number) -> float | None:
    """Cost per registration, per booked call, per enrollment - one helper, because they are the
    same arithmetic and giving each its own function invites one of them to drift."""
    return rate(spend, count)


def cac(spend: Number, closes: Number) -> float | None:
    """Customer acquisition cost. Deliberately the same call as cost_per; named separately
    because it is the number people quote, and a named function is a place to hang this comment:
    an ATTRIBUTED CAC and a BLENDED CAC are different denominators and must never be shown as one
    figure. See Part 4.7."""
    return rate(spend, closes)


def roas(value: Number, spend: Number) -> float | None:
    """Return on ad spend. Which VALUE is passed decides what this means - contracted, collected
    or projected - and the payload always names the lens. A single unlabelled "ROAS" is the
    lie this module exists to stop telling."""
    return rate(value, spend)


def conversion(stage_n: Number, stage_prior: Number) -> float | None:
    """Conversion from the previous rung, as a percentage. Same law: counts in, ratio out."""
    return rate(stage_n, stage_prior, 100)


def resolve_leads(actions: list | dict | None, wanted: list[str] | None) -> int:
    """Resolve a lead count from a stored `actions` array at READ time.

    `action_type == "lead"` is only one of several lead-shaped types Meta emits, alongside
    offsite_conversion.fb_pixel_lead and onsite_conversion.lead_grouped. Which ones count is a
    per-account decision, so the whole array is stored and the number is resolved from it. That
    means changing the configured list re-answers history without a re-sync, and the UI can show
    which types are being counted next to the number.
    """
    if not actions or not wanted:
        return 0
    rows = actions if isinstance(actions, list) else actions.get("data") or []
    keep = {str(w).lower() for w in wanted}
    total = 0
    for a in rows:
        if not isinstance(a, dict):
            continue
        if str(a.get("action_type") or "").lower() in keep:
            try:
                total += int(float(a.get("value") or 0))
            except (TypeError, ValueError):
                continue
    return total


def account_today(timezone_name: str | None) -> dt.date:
    """Today in the AD ACCOUNT's reporting timezone.

    Meta's day boundaries follow the account's timezone, not UTC and not the tenant's. A naive
    UTC date puts spend on the wrong day either side of midnight and makes a range disagree with
    Ads Manager for reasons nobody can find. scorecard_tick learned this the same way, which is
    why it computes `today` in BILLING_TIMEZONE rather than calling date.today().
    """
    if timezone_name:
        try:
            return dt.datetime.now(ZoneInfo(timezone_name)).date()
        except (ZoneInfoNotFoundError, ValueError):
            pass                                   # an unknown zone is not worth a 500
    return dt.date.today()


# Trailing-day keys this module adds. Everything else delegates to the shared period helper, so
# `mtd` means here exactly what it means on every other tab.
_TRAILING = {"7d": 7, "30d": 30, "60d": 60, "90d": 90}


def ads_period(period: str | None, start: dt.date | None, end: dt.date | None,
               timezone_name: str | None = None) -> tuple[dt.date, dt.date, str]:
    """Resolve a reporting window. Returns (start, end, label).

    Explicit start/end always win. The trailing-day keys resolve locally; anything else is handed
    to metrics._period_range so there is one definition of `mtd` in the platform rather than two
    that drift. Deliberately a local helper rather than a new branch in the shared one.
    """
    today = account_today(timezone_name)
    if start and end:
        return start, end, f"{start.isoformat()} to {end.isoformat()}"
    key = (period or "30d").lower().strip()
    if key in _TRAILING:
        days = _TRAILING[key]
        s = today - dt.timedelta(days=days - 1)
        return s, today, f"Last {days} days"
    from .metrics import _period_range          # local import: shared definition, one source

    # NOTE, and it is a real divergence rather than an oversight: _period_range computes its own
    # `today` from the server clock, not from the ad account's timezone. So a calendar key like
    # `mtd` can disagree with a trailing key by one day either side of midnight in an account
    # whose zone is far from the server's.
    #
    # Accepted deliberately. The alternative is a second definition of `mtd` that means something
    # different on this tab than on every other one, and a number that disagrees with the rest of
    # the platform is worse than a boundary that moves by a day. Trailing keys - which are what
    # the ads tab defaults to and what Ads Manager is compared against - do use the account zone.
    s, e = _period_range(key)
    return s, e, key.upper()
