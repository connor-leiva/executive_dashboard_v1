"""What the team portal inherits from the Acumyn dashboard, rather than asking for twice.

A workspace is ONE customer. They connect Sisu once, they have one logo, they picked their
colours once. The portal and the dashboard being separate surfaces is our implementation detail,
and making somebody paste the same API key into two screens -- then keeping the two in step
forever -- is that detail leaking onto the customer.

WHAT WAS ACTUALLY DUPLICATED, found by looking rather than assumed:

  * INTEGRATIONS. `Integration` (the dashboard: sisu, fub, qbo, arive, ghl) and
    `IntranetIntegration` (the portal: sisu, follow_up_boss, google_workspace, slack, skool,
    brivity, skyslope, canva) BOTH cover Sisu and Follow Up Boss. A tenant with Sisu connected on
    the dashboard still saw "Not Connected" in their portal, and the portal's numbers read zero
    next to a dashboard showing live production. The two even spell the states differently --
    `connected` against `Connected` -- so nothing would have matched by accident.

BRAND IS DELIBERATELY NOT INHERITED. The dashboard and the portal are two different-looking
products on purpose -- the portal is a warm, dark-railed team space and the dashboard is an
executive surface -- so pulling the dashboard's palette across would fight the portal's design
rather than unify anything. A workspace sets its portal's appearance in the portal's own console.
Connections are the opposite case: there is only one Sisu account and it either works or it does
not.

THE DASHBOARD WINS for the connections it owns. It is where the integration actually does work --
it syncs, it holds the encrypted tokens, it is what the numbers come from -- so a portal that
disagreed with it would be the one that was wrong. Providers the dashboard has never heard of
(Slack, Skool, Canva) stay the portal's own.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Integration

# Portal provider key -> dashboard provider. Only the genuinely shared ones: a portal provider
# absent here is the portal's own and is configured in its console as before.
INHERITED_PROVIDERS: dict[str, str] = {
    "sisu": "sisu",
    "follow_up_boss": "fub",
}

# The dashboard says `connected` / `disconnected` / `error`; the portal's console says
# `Connected` / `Not Connected`. Translated in one place so neither side has to know the other's
# vocabulary, and so a new dashboard state cannot silently read as connected.
_DASHBOARD_TO_PORTAL = {
    "connected": "Connected",
    "disconnected": "Not Connected",
    "error": "Error",
}


async def dashboard_connections(s: AsyncSession, tenant_id: uuid.UUID) -> dict[str, str]:
    """Portal provider key -> portal-shaped status, for everything the dashboard owns.

    A provider can have several dashboard rows (QuickBooks does, one per company), so a provider
    counts as connected when ANY of its rows is. Reporting the first row's status would make a
    workspace's portal depend on insertion order.
    """
    rows = (await s.execute(select(Integration.provider, Integration.status).where(
        Integration.tenant_id == tenant_id))).all()

    best: dict[str, str] = {}
    for provider, status in rows:
        current = best.get(provider)
        if current == "connected":
            continue
        best[provider] = status or "disconnected"

    return {
        portal_key: _DASHBOARD_TO_PORTAL.get(best[dash_key], "Not Connected")
        for portal_key, dash_key in INHERITED_PROVIDERS.items()
        if dash_key in best
    }


def is_inherited(portal_provider_key: str) -> bool:
    """Whether this provider is configured on the dashboard rather than in the portal's console.

    The console uses this to render those rows read-only and point at where the connection
    actually lives, instead of offering a credential form that would write to a row nothing
    reads.
    """
    return portal_provider_key in INHERITED_PROVIDERS
