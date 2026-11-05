"""SkySlope's files, written into `transaction` and `agent` (SKYSLOPE-SPEC.md §7, decision 2: B).

WHY. Every real-estate business already gets production tiles, drill-downs and financials from
`transaction`. Writing SkySlope's files there lights all of them for a SkySlope brokerage without
changing one reader. The SkySlope panel (services/skyslope_metrics.py) covers what `transaction`
cannot say.

ONE WRITER PER BUSINESS. No reader filters `transaction` by source, so two sources writing the same
business would add up. `production_source` names the one that owns it:
  - `business.config["production_source"]`, when somebody set it ("sisu" | "skyslope");
  - otherwise SkySlope when the business has a live SkySlope connection and no live Sisu one;
  - otherwise Sisu, which is every live workspace today: nothing already working changes.
When SkySlope does not own a business, this REMOVES the rows it wrote there, so a change of owner
never leaves two sources counted at once. services/sync.py's Sisu writer honours an explicit
"skyslope" the same way.

A REBUILD, NOT A LOG. The rows are derived from the skyslope_* tables each time: what should exist is
upserted, and SkySlope-sourced rows that should not exist any more are deleted. It is idempotent, it
settles a status-map change, and a purge (services/skyslope_purge.py) takes these rows with it.

WHAT IS NEVER WRITTEN: addresses, buyer names, emails or phones. `transaction` has columns for them
because Sisu fills them; SkySlope's are on the never-store list. `gci` stays NULL until the GCI field is
chosen (decision 9), and the tiles show that as a chip, not as $0.

THE MAPPING:
  sale closed                      -> closed, close_date = actualClosingDate
  sale under_contract / past_due   -> pending
  sale canceled                    -> dead
  sale pre_contract / unmapped     -> not written (pre-contract is not under contract: decision 4)
  listing active                   -> active, side sell, listing_date = listingDate
  any other listing                -> not written: a converted one is counted as its sale
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (Agent, Business, Integration, SkyslopeListing, SkyslopeSale, SkyslopeUser,
                      Transaction)
from .upsert import upsert

SOURCE = "skyslope"
OWNERS = ("sisu", "skyslope")

_SALE_STATUS = {"closed": "closed", "under_contract": "pending", "past_due": "pending", "canceled": "dead"}
_SIDE = {"buyer": "buy", "buy": "buy", "seller": "sell", "sell": "sell", "listing": "sell", "dual": "sell"}


def _date(at: dt.datetime | None) -> dt.date | None:
    return at.astimezone(dt.timezone.utc).date() if at else None


async def production_source(s: AsyncSession, tenant_id, business: Business) -> str:
    explicit = (business.config or {}).get("production_source")
    if explicit in OWNERS:
        return explicit
    live = set((await s.execute(select(Integration.provider).where(
        Integration.tenant_id == tenant_id, Integration.business_id == business.id,
        Integration.provider.in_(OWNERS), Integration.status.in_(("connected", "error"))))).scalars().all())
    return SOURCE if SOURCE in live and "sisu" not in live else "sisu"


async def _clear(s: AsyncSession, tenant_id, business_id) -> int:
    gone = await s.execute(delete(Transaction).where(
        Transaction.tenant_id == tenant_id, Transaction.business_id == business_id, Transaction.source == SOURCE))
    # Agents go too, after the rows that point at them, except one a SkySlope file in ANOTHER
    # business still credits: deleting that would break the foreign key and fail the whole sync.
    still_credited = select(Transaction.agent_id).where(
        Transaction.tenant_id == tenant_id, Transaction.agent_id.is_not(None))
    await s.execute(delete(Agent).where(
        Agent.tenant_id == tenant_id, Agent.business_id == business_id, Agent.source == SOURCE,
        Agent.id.notin_(still_credited)))
    return gone.rowcount or 0


async def project(s: AsyncSession, tenant_id) -> dict:
    """Rebuild every business's SkySlope-sourced rows. Returns counts per business key."""
    ids = set()
    for model in (SkyslopeListing, SkyslopeSale):
        ids |= set((await s.execute(select(model.business_id).where(
            model.tenant_id == tenant_id, model.business_id.is_not(None)).distinct())).scalars().all())
    ids |= set((await s.execute(select(Transaction.business_id).where(
        Transaction.tenant_id == tenant_id, Transaction.source == SOURCE).distinct())).scalars().all())
    out = {}
    for business in (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id, Business.id.in_(ids)))).scalars().all() if ids else []:
        if await production_source(s, tenant_id, business) != SOURCE:
            out[business.key] = {"owner": "sisu", "removed": await _clear(s, tenant_id, business.id)}
            continue
        out[business.key] = await _project_business(s, tenant_id, business)
    return out


async def _project_business(s: AsyncSession, tenant_id, business: Business) -> dict:
    def files(model):
        return select(model).where(model.tenant_id == tenant_id, model.business_id == business.id,
                                   model.removed_from_source_at.is_(None))

    listings = (await s.execute(files(SkyslopeListing))).scalars().all()
    sales = (await s.execute(files(SkyslopeSale))).scalars().all()
    users = {u.user_guid: u for u in (await s.execute(select(SkyslopeUser).where(
        SkyslopeUser.tenant_id == tenant_id))).scalars().all()}

    # ── agents: everybody credited on a file here, plus the agents the keys can list ──
    people = {f.agent_guid for f in [*listings, *sales] if f.agent_guid}
    people |= {g for g, u in users.items() if (u.user_type or "").lower() == "agent" and not u.deactivated}
    agent_rows = []
    for guid in sorted(people):
        u = users.get(guid)
        agent_rows.append({"tenant_id": tenant_id, "business_id": business.id, "source": SOURCE, "external_id": guid,
                           "name": ((u.name if u and u.name else None) or f"Unknown agent {guid[-4:]}")[:200],
                           "email": (u.email_norm if u else None), "is_active": not (u and u.deactivated)})
    await upsert(s, Agent, agent_rows, keys=["tenant_id", "source", "external_id"],
                 update=["business_id", "name", "email", "is_active"])
    # Somebody no longer credited or listed stays, inactive: transactions elsewhere may name them.
    gone = [i for i, ext in (await s.execute(select(Agent.id, Agent.external_id).where(
        Agent.tenant_id == tenant_id, Agent.business_id == business.id, Agent.source == SOURCE,
        Agent.is_active.is_(True)))).all() if ext not in people]
    for chunk in _chunks(gone):
        await s.execute(update(Agent).where(Agent.id.in_(chunk)).values(is_active=False))
    agent_ids = {a.external_id: a.id for a in (await s.execute(select(Agent).where(
        Agent.tenant_id == tenant_id, Agent.source == SOURCE))).scalars().all()}

    # ── transactions ──
    listed = {x.listing_guid: _date(x.listed_at) for x in listings}
    rows = []
    for x in sales:
        status = _SALE_STATUS.get(x.status or "")
        if status is None:
            continue
        rows.append({
            "tenant_id": tenant_id, "business_id": business.id, "source": SOURCE, "external_id": x.sale_guid,
            "side": _SIDE.get((x.deal_side or "").strip().lower()), "status": status, "gci": None,
            "agent_commission": None, "sale_price": x.sale_price, "agent_id": agent_ids.get(x.agent_guid),
            "contract_date": _date(x.contract_accepted_at),
            "close_date": _date(x.closed_at) if status == "closed" else None,
            "expected_close_date": _date(x.expected_close_at),
            "listing_date": listed.get(x.listing_guid) if x.listing_guid else None})
    for x in listings:
        if x.status != "active":
            continue
        rows.append({
            "tenant_id": tenant_id, "business_id": business.id, "source": SOURCE,
            "external_id": f"listing:{x.listing_guid}", "side": "sell", "status": "active", "gci": None,
            "agent_commission": None, "sale_price": x.list_price, "agent_id": agent_ids.get(x.agent_guid),
            "contract_date": None, "close_date": None, "expected_close_date": None,
            "listing_date": _date(x.listed_at)})
    await upsert(s, Transaction, rows, keys=["tenant_id", "source", "external_id"],
                 update=[c for c in rows[0] if c not in ("tenant_id", "source", "external_id")] if rows else [])
    # What this business holds from SkySlope and should not: a file that closed out of the mapping,
    # was canceled before it was ever pending, or that no key can see any more.
    keep = {r["external_id"] for r in rows}
    held = (await s.execute(select(Transaction.id, Transaction.external_id).where(
        Transaction.tenant_id == tenant_id, Transaction.business_id == business.id,
        Transaction.source == SOURCE))).all()
    stale = [i for i, ext in held if ext not in keep]
    for chunk in _chunks(stale):
        await s.execute(delete(Transaction).where(Transaction.id.in_(chunk)))
    return {"owner": SOURCE, "agents": len(agent_rows), "transactions": len(rows), "removed": len(stale)}


def _chunks(items: list, size: int = 500):
    for i in range(0, len(items), size):
        yield items[i:i + size]
