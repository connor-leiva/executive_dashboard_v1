"""Tab registry — the authorization vocabulary. Tabs are data-driven per tenant
(fixed portfolio/flywheel + each business.key, with program views replacing their
business), and every lineage metric key maps to the tab that owns it so a drill can
never leak data a member's tiles wouldn't show. See SPEC-platform §2.1 / §2.5.
"""
from __future__ import annotations

from sqlalchemy import select

from ..config import settings
from ..models import Business
from . import roles
from .. import plans

# Operational multi-view businesses expose more than one nav tab: springb runs both
# The Forum and beCollective from one GHL location. Financial-only entities (a QBO
# entity routed to a page) instead contribute a single tab via their `display_tab`.
# A business's nav tabs resolve as:
#   config["program_tabs"] (explicit)  ->  PROGRAM_TABS[key]  ->  [display_tab or key]
PROGRAM_TABS = {"springb": ["forum", "becollective", "edge"]}

# Presentation for the platform's own tabs — the ones every tenant has regardless of what
# businesses it runs. Nav order is: portfolio, the tenant's business/program tabs, then these.
PLATFORM_TABS = {
    "portfolio": {"label": "Portfolio", "accent": "#F4F1E8"},
    "flywheel": {"label": "Referral Flywheel", "accent": "#FA8069"},
    "books": {"label": "Books", "accent": "#C9D3CE"},
    "binder": {"label": "Binder", "accent": "#227175"},
    # Portfolio-level, NOT filed under a business. The tab registry derives tabs from businesses,
    # which would put Ads under whichever entity books the spend - but one Meta account routinely
    # runs campaigns for several programmes at once, which is exactly what campaign grouping
    # exists to untangle. Filing it under one business would make the feature contradict its own
    # placement.
    "ads": {"label": "Ads", "accent": "#B26248"},
    "ai_employees": {"label": "AI Employees", "accent": "#61835E"},
}

# Presentation for named PROGRAM tabs — several views run off one membership entity's GHL
# location. A tenant that names one of these in `program_tabs` gets this label and colour
# unless it supplies its own (see _business_tabs). These are defaults for a program somebody
# opted into by name, not an assumption about every tenant.
PROGRAM_META = {
    "forum": {"label": "The Forum", "accent": "#FFDD1F"},
    "becollective": {"label": "beCollective", "accent": "#FFBA9F"},
    "edge": {"label": "The Edge", "accent": "#B26248"},
}


def _program_entries(b) -> list[dict]:
    """This business's nav tabs as {key, label, accent} — the single place that decides both
    WHICH tabs a business contributes and how each is presented.

    `config["program_tabs"]` accepts either shape:
        ["forum", "becollective"]                       keys, presented from PROGRAM_META
        [{"key": "guild", "label": "The Guild", "accent": "#8899AA"}]   fully self-describing
    The second is what a tenant with its own programs uses; the first is what Spring has.
    """
    cfg = b.config or {}
    raw = cfg.get("program_tabs") or PROGRAM_TABS.get(b.key)
    if not raw:
        key = b.display_tab or b.key
        return [{"key": key, "label": b.name, "accent": b.accent}]
    out = []
    for item in raw:
        if isinstance(item, dict):
            key = item.get("key")
            if not key:
                continue
            meta = PROGRAM_META.get(key, {})
            out.append({"key": key,
                        "label": item.get("label") or meta.get("label") or key.replace("_", " ").title(),
                        "accent": item.get("accent") or meta.get("accent") or b.accent})
        else:
            meta = PROGRAM_META.get(item, {})
            out.append({"key": item,
                        "label": meta.get("label") or item.replace("_", " ").title(),
                        "accent": meta.get("accent") or b.accent})
    return out


def _business_tabs(b) -> list[str]:
    return [e["key"] for e in _program_entries(b)]


async def tenant_tabs(s, tenant_id) -> list[str]:
    """Ordered nav-tab keys for a tenant, derived from its businesses (sort_order).
    Each business contributes its operational program tabs plus — for a financial
    entity routed to a brand-new page — its `display_tab`."""
    biz = (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id).order_by(Business.sort_order))).scalars().all()
    out = ["portfolio"]
    for b in biz:
        out.extend(_business_tabs(b))
        if b.display_tab:                               # a new-page routing target is a tab too
            out.append(b.display_tab)
    out.append("flywheel")
    out.append("books")                                 # portfolio-level bookkeeping module
    out.append("binder")                                # portfolio-level entity-compliance module
    out.append("ads")                                   # portfolio-level paid-media module
    if settings.AI_EMPLOYEES_ENABLED:                   # flag-gated top-level rail item
        out.append("ai_employees")
    # de-dupe while preserving order (defensive against config quirks)
    seen, ordered = set(), []
    for t in out:
        if t not in seen:
            seen.add(t); ordered.append(t)
    return ordered


async def tenant_tab_descriptors(s, tenant_id) -> list[dict]:
    """The nav, as data: [{key, label, accent}] in order.

    The SPA used to hold this as a compile-time constant — ten entries carrying one customer's
    business names and brand colours — so every tenant's rail read "ULRG + Team", "Sympli
    Mortgage", "The Forum", whatever their own businesses were called. Derived here instead,
    from the same `_program_entries` the permission vocabulary uses, so the rail and the tab
    grants can never disagree about which tabs exist.
    """
    biz = (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id).order_by(Business.sort_order))).scalars().all()
    entries: list[dict] = [{"key": "portfolio", **PLATFORM_TABS["portfolio"]}]
    for b in biz:
        entries.extend(_program_entries(b))
        if b.display_tab and b.display_tab not in {e["key"] for e in entries}:
            # A financial entity routed onto a brand-new page contributes that page too.
            entries.append({"key": b.display_tab, "label": b.name, "accent": b.accent})
    for key in ("flywheel", "books", "binder", "ads"):
        entries.append({"key": key, **PLATFORM_TABS[key]})
    if settings.AI_EMPLOYEES_ENABLED:
        entries.append({"key": "ai_employees", **PLATFORM_TABS["ai_employees"]})
    seen, ordered = set(), []
    for e in entries:
        if e["key"] not in seen:
            seen.add(e["key"]); ordered.append(e)
    return ordered


def effective_tabs(user, all_tabs: list[str], tenant=None) -> list[str]:
    """The tabs a user actually sees. Owners/admins get all; members get grants.

    The PLAN is applied first and to everybody, including owners. A member carrying a grant for
    Binder from when their workspace was on Portfolio must not keep reaching Binder after it moves
    down — a stale grant is not an entitlement, and a downgrade that leaves a door open is not a
    downgrade. `tenant` is optional so call sites migrate one at a time; without it the plan is
    not applied, which is the old behaviour and correct for anything that has not been passed one.
    """
    tabs = plans.plan_tabs(tenant, all_tabs) if tenant is not None else list(all_tabs)
    if user.role in ("owner", "admin"):
        return list(tabs)
    granted = set(user.tab_access or [])
    return [t for t in tabs if t in granted]            # preserve nav order, drop stale keys


# ── lineage metric key → owning tab (drill-down enforcement, §2.5) ──
_ULRG = {"units_closed", "gci", "volume", "avg_price", "pending", "active_listings",
         "agents_producing", "fin_closed", "fin_projected", "fin_expenses"}
_FORUM = {"active_members", "forum_roster", "forum_arr", "renewals_due", "new_members",
          "registered", "mrr", "renewal_book", "monthly", "pastdue", "unregistered",
          "forum_payments", "forum_failed_payments", "forum_mrr_subs",
          "forum_installments", "forum_next30", "forum_streams"}
_BC = {"bc_members", "bc_arr", "bc_registered", "bc_financed", "bc_monthly"}
_EDGE = {"edge_members", "edge_arr", "edge_registered", "edge_financed", "edge_monthly",
         "edge_roster", "edge_new_members", "edge_pipeline", "edge_payments"}
_SYMPLI = {"funded_loans", "loan_volume", "preapprovals", "in_underwriting",
           "sympli_commission", "loan_stage"}
_FINANCIAL = {"revenue", "noi", "gross_profit", "opex", "cogs", "combined_profit"}
# business.key → the tab a financial drill for that business belongs to.
# Spring's literals, kept ONLY as the fallback when no tenant map was threaded through. Note
# the lookup below already defaults to the business key itself, which is the right answer for
# any tenant whose business is not one of these three.
_BIZ_TAB = {"ulrg": "ulrg", "sympli": "sympli", "springb": "forum"}

# Whole metric FAMILIES belong to a business ROLE, not to a business named ulrg or sympli.
# `units_closed` is a brokerage metric; which tab owns it depends on which business is this
# tenant's brokerage. Spring's happen to be keyed "ulrg" and "sympli", and hardcoding that meant
# a normally-provisioned tenant 403'd on nearly every drill-down: tab_for_metric handed back a
# tab their nav does not contain, and deps.py correctly refused it — for the owner too.
_KIND_FAMILIES = ((roles.REAL_ESTATE, _ULRG, "ulrg"), (roles.COMMISSION_JV, _SYMPLI, "sympli"))


def kind_tabs(businesses) -> dict[str, str]:
    """business.kind → the tab that owns that role's metrics, for one tenant.

    A business's tabs already come from _program_entries, so this reuses it rather than
    inventing a second opinion — the drill and the rail cannot disagree about where a metric
    lives. The FIRST tab wins for a business that contributes several (Spring's membership
    entity runs The Forum, beCollective and The Edge off one GHL location; its financial drills
    belong to The Forum), and the lowest sort_order wins when a tenant has two of a kind.

    For Spring this returns exactly {real_estate: "ulrg", commission_jv: "sympli",
    membership: "forum"} — the same three literals it replaces. There is a test on that.
    """
    out: dict[str, str] = {}
    for b in sorted(businesses, key=lambda x: (x.sort_order if x.sort_order is not None else 0)):
        if not b.kind or b.kind in out:
            continue
        tabs = _business_tabs(b)
        if tabs:
            out[b.kind] = tabs[0]
    return out


async def tab_maps(s, tenant_id) -> tuple[dict[str, str], dict[str, str]]:
    """(biz_tab, kind_tab) for one tenant, from ONE query.

    Both maps are needed on every drill and both are derived from the same rows, so fetching
    them separately would double the query count on the hottest authorization path in the app.
    """
    biz = (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id).order_by(Business.sort_order))).scalars().all()
    return ({b.key: (b.display_tab or b.key) for b in biz}, kind_tabs(biz))


def tab_for_metric(key: str, business: str | None = None, biz_tab: dict | None = None,
                   kind_tab: dict | None = None) -> str:
    """The tab that owns a lineage key — the drill inherits its tile's permission.

    `biz_tab` maps business.key → its display_tab (a financial drill for a QBO entity routed to
    another page belongs to that page); falls back to the static _BIZ_TAB.

    `kind_tab` maps business.kind → tab, from kind_tabs() for this tenant. Optional so callers
    can be migrated one at a time: omitted, every branch resolves exactly as it did before, which
    is right for Spring and wrong for everybody else."""
    if key.startswith("flywheel_"):
        return "flywheel"
    if key.startswith("books_"):                        # books_queue, books_ic, books_pl_lines
        return "books"
    if key.startswith("binder_"):                       # binder_matrix, binder_review
        return "binder"
    if key.startswith("ads_"):                          # ads_spend, ads_closes, ads_cac, ...
        return "ads"
    # The membership family. Kept as its own branch rather than folded into _KIND_FAMILIES
    # because of the `forum_` PREFIX, which a set-membership loop cannot express.
    #
    # The bc_/edge_ branches below stay below it only incidentally — their keys are distinct, so
    # order does not decide anything today. Keep them separate regardless: they are per-PROGRAMME
    # keys, and for a tenant that emits one, the literal IS that tenant's own declared tab.
    if key.startswith("forum_") or key in _FORUM:
        return (kind_tab or {}).get(roles.MEMBERSHIP, "forum")
    if key.startswith("bc_") or key in _BC:
        return "becollective"
    if key.startswith("edge_") or key in _EDGE:
        return "edge"
    for kind, family, spring_default in _KIND_FAMILIES:
        if key in family:
            return (kind_tab or {}).get(kind, spring_default)
    if key == "combined_profit":
        return "portfolio"
    if key in _FINANCIAL and business:
        return (biz_tab or _BIZ_TAB).get(business, business)
    return "portfolio"


async def biz_tab_map(s, tenant_id) -> dict[str, str]:
    """business.key → the tab its financial area renders on (display_tab or its own key).

    Delegates to tab_maps rather than repeating the query: two functions deriving the same
    mapping from the same rows is exactly the shape that drifts, and this one decides a
    permission.
    """
    return (await tab_maps(s, tenant_id))[0]
