"""Lead sources: the groups, their QuickBooks spend mapping, and the reports built on them.

Every route is scoped to one BUSINESS of the signed-in tenant, by key, the way the rest of
`/businesses/{key}/...` is. Reading needs the business's tab (owners and admins pass); changing a
group, its spend rules or its digest needs owner or admin. Nothing here calls Follow Up Boss, Sisu
or QuickBooks: the reports read what the syncs stored (docs/specs/ulrg-reporting-dashboard-port.md).
"""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import assert_tab, current_user, require_role
from ..models import Business, LeadSourceCostShare, LeadSourceGroup, LeadSourceSpendRule, User
from ..services import lead_source_spend as spend
from ..services import lead_sources as ls
from ..services.audit import audit

router = APIRouter(tags=["lead_sources"])

ADMIN_ROLES = ("owner", "admin")


# ── lookups ──────────────────────────────────────────────────────────────────────────────────

def tab_of(b: Business) -> str:
    """The nav tab a business's page lives on -- what a member must hold to read its reports."""
    return b.display_tab or b.key


async def business_for(s, user: User, key: str) -> Business:
    b = (await s.execute(select(Business).where(
        Business.tenant_id == user.tenant_id, Business.key == key))).scalar_one_or_none()
    if b is None:
        raise HTTPException(404, "Unknown business")
    await assert_tab(user, s, tab_of(b))
    return b


async def group_for(s, user: User, b: Business, gkey: str, *, active_only: bool) -> LeadSourceGroup:
    g = (await s.execute(select(LeadSourceGroup).where(
        LeadSourceGroup.tenant_id == user.tenant_id, LeadSourceGroup.business_id == b.id,
        LeadSourceGroup.key == gkey))).scalar_one_or_none()
    if g is None or (active_only and not g.is_active):
        raise HTTPException(404, "Unknown or inactive lead source")
    return g


def _bad(e: Exception):
    raise HTTPException(400, str(e))


async def _businesses(s, tenant_id) -> list[Business]:
    return list((await s.execute(select(Business).where(Business.tenant_id == tenant_id)
                                 .order_by(Business.sort_order))).scalars().all())


# ── groups ───────────────────────────────────────────────────────────────────────────────────

class GroupIn(BaseModel):
    key: str | None = None
    display_name: str | None = None
    variants: list[str] | None = None
    match_mode: str | None = None
    program_start: dt.date | None = None
    is_active: bool | None = None
    display_order: int | None = None
    exp_risk_team_default: float | None = None
    exp_risk_agent_default: float | None = None


@router.get("/businesses/{key}/lead-sources")
async def list_groups(key: str, include_inactive: bool = Query(False),
                      user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """The business's groups. Active only for everyone; inactive too for an admin who asks."""
    b = await business_for(s, user, key)
    admin = user.role in ADMIN_ROLES
    groups = await ls.groups_for_business(s, user.tenant_id, b.id,
                                          include_inactive=include_inactive and admin)
    rules = await spend._rules_by_group(s, user.tenant_id, [g.id for g in groups])
    shares: dict = {}
    for sh in (await s.execute(select(LeadSourceCostShare).where(
            LeadSourceCostShare.tenant_id == user.tenant_id,
            LeadSourceCostShare.lead_source_group_id.in_([g.id for g in groups] or [uuid.uuid4()])
    ))).scalars():
        shares.setdefault(sh.lead_source_group_id, []).append(sh)
    businesses = await _businesses(s, user.tenant_id)
    return {
        "business": {"id": str(b.id), "key": b.key, "name": b.name},
        "can_edit": admin,
        "groups": [ls.group_dict(g, rules=rules.get(g.id, []), shares=shares.get(g.id, []),
                                 businesses=businesses) for g in groups],
    }


def _apply(g: LeadSourceGroup, body: GroupIn, *, creating: bool) -> None:
    mode = ls.clean_mode(body.match_mode if body.match_mode is not None else g.match_mode)
    if body.display_name is not None or creating:
        name = (body.display_name or "").strip()
        if not (1 <= len(name) <= 120):
            raise ls.InvalidGroup("The display name is 1 to 120 characters.")
        g.display_name = name
    if body.variants is not None or body.match_mode is not None or creating:
        g.variants = ls.clean_variants(body.variants if body.variants is not None else g.variants, mode)
        g.match_mode = mode
    if body.program_start is not None or creating:
        g.program_start = body.program_start
    if body.is_active is not None:
        g.is_active = body.is_active
    if body.display_order is not None:
        if not (0 <= body.display_order <= 999):
            raise ls.InvalidGroup("Display order is between 0 and 999.")
        g.display_order = body.display_order
    if body.exp_risk_team_default is not None:
        g.exp_risk_team_default = ls.clean_money(body.exp_risk_team_default, "The team-side EXP risk fee")
    if body.exp_risk_agent_default is not None:
        g.exp_risk_agent_default = ls.clean_money(body.exp_risk_agent_default, "The agent-side EXP risk fee")


@router.post("/businesses/{key}/lead-sources", status_code=201)
async def create_group(key: str, body: GroupIn, user: User = Depends(require_role(*ADMIN_ROLES)),
                       s: AsyncSession = Depends(get_session)):
    b = await business_for(s, user, key)
    try:
        gkey = ls.clean_key(body.key)
        clash = (await s.execute(select(LeadSourceGroup.id).where(
            LeadSourceGroup.tenant_id == user.tenant_id, LeadSourceGroup.business_id == b.id,
            LeadSourceGroup.key == gkey))).first()
        if clash:
            raise ls.InvalidGroup(f"{b.name} already has a lead source with the key {gkey}.")
        g = LeadSourceGroup(id=uuid.uuid4(), tenant_id=user.tenant_id, business_id=b.id, key=gkey,
                            is_active=True if body.is_active is None else body.is_active,
                            display_order=0, variants=[], match_mode="contains")
        _apply(g, body, creating=True)
    except ls.InvalidGroup as e:
        _bad(e)
    s.add(g)
    audit(s, user.tenant_id, user.id, "lead_source.created", "lead_source_group", g.id,
          {"business": b.key, "key": g.key}, category="Config",
          summary=f"Lead source {g.display_name} added to {b.name}")
    await s.commit()
    return ls.group_dict(g, rules=[], shares=[])


@router.patch("/businesses/{key}/lead-sources/{gkey}")
async def update_group(key: str, gkey: str, body: GroupIn,
                       user: User = Depends(require_role(*ADMIN_ROLES)),
                       s: AsyncSession = Depends(get_session)):
    b = await business_for(s, user, key)
    g = await group_for(s, user, b, gkey, active_only=False)
    if body.key is not None and body.key != g.key:
        _bad(ls.InvalidGroup("A lead source's key never changes. Create a new one instead."))
    try:
        _apply(g, body, creating=False)
    except ls.InvalidGroup as e:
        _bad(e)
    audit(s, user.tenant_id, user.id, "lead_source.updated", "lead_source_group", g.id,
          {"business": b.key, "key": g.key,
           "fields": sorted(body.model_dump(exclude_unset=True))}, category="Config",
          summary=f"Lead source {g.display_name} updated")
    await s.commit()
    rules = (await spend._rules_by_group(s, user.tenant_id, [g.id])).get(g.id, [])
    return ls.group_dict(g, rules=rules)


@router.delete("/businesses/{key}/lead-sources/{gkey}")
async def delete_group(key: str, gkey: str, user: User = Depends(require_role(*ADMIN_ROLES)),
                       s: AsyncSession = Depends(get_session)):
    """Soft: inactive, and the key prefixed `deleted_` so a new group can reuse it."""
    b = await business_for(s, user, key)
    g = await group_for(s, user, b, gkey, active_only=False)
    new_key = f"{ls.DELETED_PREFIX}{g.key}"
    n = 2
    while (await s.execute(select(LeadSourceGroup.id).where(
            LeadSourceGroup.tenant_id == user.tenant_id, LeadSourceGroup.business_id == b.id,
            LeadSourceGroup.key == new_key))).first():
        new_key = f"{ls.DELETED_PREFIX}{g.key}_{n}"
        n += 1
    g.key, g.is_active = new_key[:64], False
    audit(s, user.tenant_id, user.id, "lead_source.deleted", "lead_source_group", g.id,
          {"business": b.key, "key": gkey}, category="Config",
          summary=f"Lead source {g.display_name} deleted")
    await s.commit()
    return {"ok": True, "key": g.key}


# ── QuickBooks spend mapping ─────────────────────────────────────────────────────────────────

class SpendIn(BaseModel):
    rules: list[dict] = []
    cost_shares: list[dict] = []


async def _spend_view(s, user: User, b: Business, g: LeadSourceGroup) -> dict:
    businesses = await _businesses(s, user.tenant_id)
    rules = (await spend._rules_by_group(s, user.tenant_id, [g.id])).get(g.id, [])
    shares = await spend.cost_shares(s, user.tenant_id, g)
    m = await spend.monthly_spend(s, user.tenant_id, g)
    companies = sorted({r.business_id for r in rules} | {b.id})
    months = sorted(m["gross_by_month"].items())
    return {
        "group": ls.group_dict(g),
        "rules": [ls.rule_dict(r) for r in rules],
        "cost_shares": [{**sh, "share_pct": float(sh["share_pct"])} for sh in shares],
        "share_pct": float(m["share_pct"]),
        "options": await spend.mapping_options(s, user.tenant_id, companies),
        "businesses": [{"id": str(x.id), "key": x.key, "name": x.name} for x in businesses],
        "quickbooks": {"connected": m["connected"], "synced": m["synced"],
                       "history_starts": m["history_starts"].isoformat() if m["history_starts"] else None},
        # What the mapping catches today, so an admin sees the effect of a rule before trusting it.
        "preview": {
            "lines": len(m["lines"]),
            "gross": float(sum(v for _k, v in months)) if months else 0.0,
            "months": [{"month": k, "gross": float(v),
                        "net": float(spend.net_of(v, m["share_pct"]))} for k, v in months[-12:]],
        },
        "overlap_with_other_groups": m["overlap_with_other_groups"],
        "lines_matching_several_rules": m["lines_matching_several_rules"],
        "can_edit": user.role in ADMIN_ROLES,
    }


@router.get("/businesses/{key}/lead-sources/{gkey}/spend")
async def get_spend(key: str, gkey: str, user: User = Depends(current_user),
                    s: AsyncSession = Depends(get_session)):
    b = await business_for(s, user, key)
    g = await group_for(s, user, b, gkey, active_only=user.role not in ADMIN_ROLES)
    return await _spend_view(s, user, b, g)


@router.put("/businesses/{key}/lead-sources/{gkey}/spend")
async def put_spend(key: str, gkey: str, body: SpendIn,
                    user: User = Depends(require_role(*ADMIN_ROLES)),
                    s: AsyncSession = Depends(get_session)):
    """Replace the group's spend rules and cost shares together. Takes effect on the next report
    load -- there is nothing to deploy and nothing to re-sync."""
    b = await business_for(s, user, key)
    g = await group_for(s, user, b, gkey, active_only=False)
    tenant_biz = {str(x.id): x for x in await _businesses(s, user.tenant_id)}
    try:
        rules, seen = [], set()
        for raw in body.rules:
            r = ls.clean_rule(raw)
            bid = str(raw.get("business_id") or b.id)
            if bid not in tenant_biz:
                raise ls.InvalidGroup("A spend rule names a QuickBooks company outside this workspace.")
            sig = (bid, r["account_qbo_id"], r["vendor_qbo_id"], (r["class_label"] or "").lower())
            if sig in seen:
                continue
            seen.add(sig)
            rules.append({**r, "business_id": tenant_biz[bid].id})
        shares = ls.clean_shares(body.cost_shares)
        for sh in shares:
            if sh["bearer_business_id"] not in tenant_biz:
                raise ls.InvalidGroup("A cost share names a business outside this workspace.")
            if sh["bearer_business_id"] == str(b.id):
                raise ls.InvalidGroup(f"{b.name} cannot bear a share of its own spend.")
    except ls.InvalidGroup as e:
        _bad(e)
    await s.execute(delete(LeadSourceSpendRule).where(
        LeadSourceSpendRule.tenant_id == user.tenant_id,
        LeadSourceSpendRule.lead_source_group_id == g.id))
    await s.execute(delete(LeadSourceCostShare).where(
        LeadSourceCostShare.tenant_id == user.tenant_id,
        LeadSourceCostShare.lead_source_group_id == g.id))
    for r in rules:
        s.add(LeadSourceSpendRule(id=uuid.uuid4(), tenant_id=user.tenant_id,
                                  lead_source_group_id=g.id, **r))
    for sh in shares:
        s.add(LeadSourceCostShare(id=uuid.uuid4(), tenant_id=user.tenant_id,
                                  lead_source_group_id=g.id,
                                  bearer_business_id=tenant_biz[sh["bearer_business_id"]].id,
                                  share_pct=sh["share_pct"]))
    audit(s, user.tenant_id, user.id, "lead_source.spend_mapped", "lead_source_group", g.id,
          {"business": b.key, "key": g.key, "rules": len(rules), "shares": len(shares)},
          category="Config", summary=f"QuickBooks spend mapping for {g.display_name} saved")
    await s.commit()
    return await _spend_view(s, user, b, g)
