"""What each plan includes — in one place, because a limit enforced in fifteen places is fifteen
limits that will disagree.

Every gate in the app reads from here. Nothing else hardcodes a number, and nothing compares
`tenant.plan == "portfolio"` inline: a feature check asks `allows(tenant, "binder")`, so adding a
tier later is an entry in this file rather than a search for every place a tier name was spelled.

THE METER IS ENTITIES, NOT SEATS. Every screen is scoped to a business, the permission model
already works that way, and it is the number that tracks the value delivered — reconciling a
brokerage plus a JV plus a mastermind is three times the work of reconciling one. Seat pricing
would also push a workspace toward shared logins, which quietly destroys the audit trail that
makes the product worth buying.

SOURCES ARE LISTED, NOT COUNTED. "Three integrations" would invite a workspace to disconnect
QuickBooks to stay under a limit, and a pricing rule that encourages someone to make their own
numbers wrong is a bug in the pricing.
"""
from __future__ import annotations

TEAM = "team"
BUSINESS = "business"
PORTFOLIO = "portfolio"

ORDER = (TEAM, BUSINESS, PORTFOLIO)

# Sources every plan can connect: the operational feeds a brokerage runs on.
_CORE_SOURCES = {"sisu", "fub", "ghl", "ghl_bc"}
# Financial and lending feeds — the ones that make Books and the flywheel mean anything.
_FINANCIAL_SOURCES = {"qbo", "stripe_legacy", "stripe_bc", "ghl_legacy", "arive"}

# Tabs a plan unlocks beyond a workspace's own business and programme tabs, which every plan has.
# `portfolio` is not listed because it is not optional — a workspace with no portfolio view has
# no product.
_TEAM_TABS: set[str] = set()
_BUSINESS_TABS = {"books", "flywheel", "ai_employees"}
_PORTFOLIO_TABS = _BUSINESS_TABS | {"binder"}

PLANS: dict[str, dict] = {
    TEAM: {
        "name": "Team",
        "price_monthly": 399,
        "max_businesses": 1,
        "max_users": 5,
        "sources": set(_CORE_SOURCES),
        "extra_tabs": set(_TEAM_TABS),
        "max_share_links": 3,
        "history_months": 12,
        "custom_branding": False,
        "max_ai_employees": 0,
    },
    BUSINESS: {
        "name": "Business",
        "price_monthly": 799,
        "max_businesses": 3,
        "max_users": 20,
        "sources": _CORE_SOURCES | _FINANCIAL_SOURCES,
        "extra_tabs": set(_BUSINESS_TABS),
        "max_share_links": None,          # None means no limit, everywhere in this file
        "history_months": 36,
        "custom_branding": True,
        "max_ai_employees": 1,
    },
    PORTFOLIO: {
        "name": "Portfolio",
        "price_monthly": 1199,
        "max_businesses": None,
        "max_users": None,
        "sources": _CORE_SOURCES | _FINANCIAL_SOURCES,
        "extra_tabs": set(_PORTFOLIO_TABS),
        "max_share_links": None,
        "history_months": None,
        "custom_branding": True,
        "max_ai_employees": None,
    },
}

# Features named as strings so a caller never spells a tier. `allows(tenant, "binder")` reads as
# the question being asked; `tenant.plan == "portfolio"` reads as an implementation detail that
# will be wrong the first time a tier is added between them.
_FEATURE_TABS = {"books": "books", "flywheel": "flywheel", "binder": "binder",
                 "ai_employees": "ai_employees"}


def plan_of(tenant) -> str:
    """A workspace's plan, defaulting to the most generous one.

    Deliberately generous rather than restrictive: an unset plan means data we have not written
    yet, and the failure mode of guessing low is a paying customer losing a tab they were using.
    Guessing high costs revenue we were not collecting anyway, and shows up in the operator
    console rather than in a support ticket.
    """
    plan = (getattr(tenant, "plan", None) or "").strip().lower()
    return plan if plan in PLANS else PORTFOLIO


def limits(tenant) -> dict:
    return PLANS[plan_of(tenant)]


def allows(tenant, feature: str) -> bool:
    """Whether this workspace's plan includes a named feature."""
    lim = limits(tenant)
    if feature in _FEATURE_TABS:
        return _FEATURE_TABS[feature] in lim["extra_tabs"]
    if feature == "custom_branding":
        return bool(lim["custom_branding"])
    return False


def allows_source(tenant, provider: str) -> bool:
    return provider in limits(tenant)["sources"]


def plan_tabs(tenant, all_tabs: list[str]) -> list[str]:
    """`all_tabs` filtered to what this plan includes, preserving nav order.

    A workspace's own business and programme tabs always survive: those are ITS entities, and a
    plan limits how many it may have rather than whether it may look at the ones it has. Only the
    platform modules — Books, Binder, the flywheel, AI employees — are gated.
    """
    gated = set(_PORTFOLIO_TABS)                     # every tab any plan gates
    included = limits(tenant)["extra_tabs"]
    return [t for t in all_tabs if t not in gated or t in included]


def over_limit(tenant, key: str, current: int) -> bool:
    """Whether adding one more would exceed the plan. None means unlimited."""
    cap = limits(tenant).get(key)
    return cap is not None and current >= cap


def describe(tenant) -> dict:
    """The plan as the UI and the operator console show it. No prices in the app itself — those
    belong on the marketing site and in billing, and a stale number in a dashboard is worse than
    no number."""
    lim = limits(tenant)
    return {"plan": plan_of(tenant), "name": lim["name"],
            "max_businesses": lim["max_businesses"], "max_users": lim["max_users"],
            "history_months": lim["history_months"],
            "custom_branding": lim["custom_branding"]}
