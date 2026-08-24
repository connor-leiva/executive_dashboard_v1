"""Finding a business by what it IS, not by what it is called.

The compute layer used to locate businesses with literal comparisons — `Business.key ==
"ulrg"`, `bmap.get("sympli")`, `if b.key == "springb"` — about thirty of them across eleven
files. Those strings are one customer's names for their own companies, so every one of them was
a place a second tenant's dashboard would come up empty: not an error, just a panel with
nothing in it and no explanation.

`Business.kind` is the axis those comparisons were really reaching for, and it already exists.
Migration 0014_business_routing added it precisely to remove "the hardcoded springb→forum/
becollective synthesis" (its own words) and backfilled ulrg→real_estate,
sympli→commission_jv, springb→membership. The column landed; the dispatch was never moved onto
it. This module is that move.

  ulrg    -> real_estate    the brokerage: transactions, agents, GCI, listings
  sympli  -> commission_jv  a joint venture paid per closing, with its own cost-of-sale split
  springb -> membership     programs and cohorts sold as memberships

PRIMARY, not THE. Nothing stops a tenant having two brokerages, and the old code silently
assumed exactly one of each. `primary()` returns the lowest `sort_order` — the same business
the old lookup would have found for Spring, and a defined answer for anyone else. Where a
caller genuinely wants all of them, `of_kind()` returns the list.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Business

REAL_ESTATE = "real_estate"
COMMISSION_JV = "commission_jv"
MEMBERSHIP = "membership"
HOLDING = "holding"

KINDS = (REAL_ESTATE, COMMISSION_JV, MEMBERSHIP, HOLDING)


async def all_businesses(s: AsyncSession, tenant_id) -> list[Business]:
    """Every business for a tenant, in nav order. One query, so callers that need several
    lookups can do them off one list instead of a query per kind."""
    return list((await s.execute(select(Business).where(
        Business.tenant_id == tenant_id).order_by(Business.sort_order))).scalars().all())


def pick(businesses: list[Business], kind: str) -> Business | None:
    """The primary business of `kind` from an already-loaded list, or None."""
    for b in businesses:
        if b.kind == kind:
            return b
    return None


def group(businesses: list[Business]) -> dict[str, list[Business]]:
    out: dict[str, list[Business]] = {}
    for b in businesses:
        out.setdefault(b.kind, []).append(b)
    return out


async def of_kind(s: AsyncSession, tenant_id, kind: str) -> list[Business]:
    return [b for b in await all_businesses(s, tenant_id) if b.kind == kind]


async def primary(s: AsyncSession, tenant_id, kind: str) -> Business | None:
    """The business of `kind` this tenant's dashboard means when it says "the brokerage".

    Returns None when the tenant has none — which is a normal state, not an error: a
    membership-only customer has no brokerage, and the panels that need one should render
    empty rather than raise.
    """
    return (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id, Business.kind == kind)
        .order_by(Business.sort_order).limit(1))).scalars().first()


async def primary_id(s: AsyncSession, tenant_id, kind: str) -> uuid.UUID | None:
    b = await primary(s, tenant_id, kind)
    return b.id if b else None


async def real_estate(s: AsyncSession, tenant_id) -> Business | None:
    return await primary(s, tenant_id, REAL_ESTATE)


async def membership(s: AsyncSession, tenant_id) -> Business | None:
    return await primary(s, tenant_id, MEMBERSHIP)


async def commission_jv(s: AsyncSession, tenant_id) -> Business | None:
    return await primary(s, tenant_id, COMMISSION_JV)


async def flywheel_pair(s: AsyncSession, tenant_id) -> tuple[Business | None, Business | None]:
    """(brokerage, JV) — the referral flywheel's two halves.

    Returned together because the flywheel is meaningless with only one of them, and every
    caller needs to make the same "both or nothing" decision.
    """
    biz = await all_businesses(s, tenant_id)
    return pick(biz, REAL_ESTATE), pick(biz, COMMISSION_JV)
