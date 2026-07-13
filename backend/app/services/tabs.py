"""Tab registry — the authorization vocabulary. Tabs are data-driven per tenant
(fixed portfolio/flywheel + each business.key, with program views replacing their
business), and every lineage metric key maps to the tab that owns it so a drill can
never leak data a member's tiles wouldn't show. See SPEC-platform §2.1 / §2.5.
"""
from __future__ import annotations

from sqlalchemy import select

from ..models import Business

# Program views replace their business key in the nav (springb → forum + becollective).
PROGRAM_TABS = {"springb": ["forum", "becollective"]}


async def tenant_tabs(s, tenant_id) -> list[str]:
    """Ordered nav-tab keys for a tenant, derived from its businesses (sort_order)."""
    biz = (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id).order_by(Business.sort_order))).scalars().all()
    out = ["portfolio"]
    for b in biz:
        out.extend(PROGRAM_TABS.get(b.key, [b.key]))
    out.append("flywheel")
    # de-dupe while preserving order (defensive against config quirks)
    seen, ordered = set(), []
    for t in out:
        if t not in seen:
            seen.add(t); ordered.append(t)
    return ordered


def effective_tabs(user, all_tabs: list[str]) -> list[str]:
    """The tabs a user actually sees. Owners/admins get all; members get grants."""
    if user.role in ("owner", "admin"):
        return list(all_tabs)
    granted = set(user.tab_access or [])
    return [t for t in all_tabs if t in granted]        # preserve nav order, drop stale keys


# ── lineage metric key → owning tab (drill-down enforcement, §2.5) ──
_ULRG = {"units_closed", "gci", "volume", "avg_price", "pending", "active_listings",
         "agents_producing", "fin_closed", "fin_projected", "fin_expenses"}
_FORUM = {"active_members", "forum_roster", "forum_arr", "renewals_due", "new_members",
          "registered", "mrr", "renewal_book", "monthly", "pastdue", "unregistered",
          "forum_payments", "forum_failed_payments", "forum_mrr_subs",
          "forum_installments", "forum_next30", "forum_streams"}
_BC = {"bc_members", "bc_arr", "bc_registered", "bc_financed", "bc_monthly"}
_SYMPLI = {"funded_loans", "loan_volume", "preapprovals", "in_underwriting",
           "sympli_commission", "loan_stage"}
_FINANCIAL = {"revenue", "noi", "gross_profit", "opex", "cogs", "combined_profit"}
# business.key → the tab a financial drill for that business belongs to
_BIZ_TAB = {"ulrg": "ulrg", "sympli": "sympli", "springb": "forum"}


def tab_for_metric(key: str, business: str | None = None) -> str:
    """The tab that owns a lineage key — the drill inherits its tile's permission."""
    if key.startswith("flywheel_"):
        return "flywheel"
    if key.startswith("forum_") or key in _FORUM:
        return "forum"
    if key.startswith("bc_") or key in _BC:
        return "becollective"
    if key in _SYMPLI:
        return "sympli"
    if key in _ULRG:
        return "ulrg"
    if key == "combined_profit":
        return "portfolio"
    if key in _FINANCIAL and business:
        return _BIZ_TAB.get(business, business)
    return "portfolio"
