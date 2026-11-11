"""Lead sources: the groups, their QuickBooks spend mapping, and the reports built on them.

Every route is scoped to one BUSINESS of the signed-in tenant, by key, the way the rest of
`/businesses/{key}/...` is. Reading needs the business's tab (owners and admins pass); changing a
group, its spend rules or its digest needs owner or admin. The reports call nothing outside: they
read what the syncs stored (docs/specs/ulrg-reporting-dashboard-port.md). The one route that asks
Follow Up Boss anything is Settings' "Check Follow Up Boss" for a group's source strings, an admin's
explicit action of a dozen one-row requests.
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
from ..models import (Business, Integration, LeadSourceCostShare, LeadSourceGroup, LeadSourceSpendRule,
                      User)
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


# ── reports ──────────────────────────────────────────────────────────────────────────────────

MONTH = r"^\d{4}-(0[1-9]|1[0-2])$"


# ── Follow Up Boss source strings (Settings) ─────────────────────────────────────────────────

async def _fub_for(s, tenant_id, b: Business) -> tuple[Integration | None, str | None]:
    """This business's Follow Up Boss integration, or None and the name of the business the
    tenant's FUB is connected to instead -- the lead-source ingestion only reads people for the
    groups on the business FUB is attached to."""
    rows = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.provider == "fub"))).scalars().all()
    mine = next((i for i in rows if i.business_id == b.id), None)
    if mine is not None or not rows:
        return mine, None
    other = next((i.business_id for i in rows if i.business_id), None)
    name = (await s.get(Business, other)).name if other else None
    return None, name


async def _fub_sources_view(s, user: User, b: Business, g: LeadSourceGroup) -> dict:
    from ..services import fub_sync
    from ..services import lead_source_discovery as disc
    integ, elsewhere = await _fub_for(s, user.tenant_id, b)
    state = fub_sync.state_of(integ) if integ else {}
    synced = await disc.synced_counts(s, user.tenant_id, b.id, g)
    out = disc.view(g, (state.get("lead_source_discovery") or {}).get(g.key), synced)
    run = (state.get("last_run") or {}).get("lead_sources") or {}
    walks = ((state.get("lead_sources") or {}).get(g.key) or {}).get("variants") or {}
    out["fub"] = {
        "state": ("connected" if integ is not None and integ.access_token_enc else
                  "no_key" if integ is not None else "elsewhere" if elsewhere else "missing"),
        "elsewhere": elsewhere,
        "last_run_at": (state.get("last_run") or {}).get("at"),
        "last_run": {k: run.get(k) for k in ("groups", "variants", "pages", "people_fetched",
                                             "people_upserted", "skipped_source", "errors", "note")},
        "updated_after": state.get("lead_sources_updated_after"),
        "variants": {v: {"backfilled": bool(w.get("complete_at")), "walking": bool(w.get("walking")),
                         "complete_at": w.get("complete_at"), "mark": w.get("mark")}
                     for v, w in walks.items()},
    }
    out["program_start"] = g.program_start.isoformat() if g.program_start else None
    out["can_edit"] = user.role in ADMIN_ROLES
    return out


@router.get("/businesses/{key}/lead-sources/{gkey}/fub-sources")
async def get_fub_sources(key: str, gkey: str, user: User = Depends(require_role(*ADMIN_ROLES)),
                          s: AsyncSession = Depends(get_session)):
    """The FUB source strings near this group, what FUB and this business hold of each, whether the
    group matches it today, and how the ingestion of the group's variants stands."""
    b = await business_for(s, user, key)
    g = await group_for(s, user, b, gkey, active_only=False)
    return await _fub_sources_view(s, user, b, g)


@router.post("/businesses/{key}/lead-sources/{gkey}/fub-sources/check")
async def check_fub_sources(key: str, gkey: str, user: User = Depends(require_role(*ADMIN_ROLES)),
                            s: AsyncSession = Depends(get_session)):
    """Ask Follow Up Boss now which source strings it holds near this group's
    (services/lead_source_discovery): one-row probes, about a dozen requests."""
    import httpx

    from ..integrations import fub
    from ..services import fub_sync
    from ..services import lead_source_discovery as disc
    from ..services.sync import _fub_creds, _workspace_tz
    b = await business_for(s, user, key)
    g = await group_for(s, user, b, gkey, active_only=False)
    integ, elsewhere = await _fub_for(s, user.tenant_id, b)
    if integ is None:
        raise HTTPException(400, f"Follow Up Boss is connected to {elsewhere}, not {b.name}." if elsewhere
                            else f"Connect Follow Up Boss to {b.name} under Integrations first.")
    if not any(str(v).strip() for v in (g.variants or [])):
        raise HTTPException(400, "Add at least one source variant first.")
    if fub_sync.lock_for(integ.id).locked():
        raise HTTPException(409, "A Follow Up Boss sync is running. Try again in a minute.")
    try:
        creds = _fub_creds(integ)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    since = fub_sync._program_start(g, await _workspace_tz(s, user.tenant_id))
    synced = await disc.synced_counts(s, user.tenant_id, b.id, g)
    try:
        async with fub.client(creds) as c:
            found = await disc.discover(c, g, since, synced)
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        raise HTTPException(502, "Follow Up Boss rejected the saved API key." if code in (401, 403)
                            else f"Follow Up Boss answered HTTP {code}. Try again shortly.") from e
    except httpx.HTTPError as e:
        raise HTTPException(502, "Follow Up Boss didn't answer. Try again shortly.") from e
    state = fub_sync.state_of(integ)
    state["lead_source_discovery"] = {**(state.get("lead_source_discovery") or {}), g.key: found}
    fub_sync._save(integ, state)
    await s.commit()
    return await _fub_sources_view(s, user, b, g)


# The app's date-bar period (metrics.KNOWN_PERIODS, or c:YYYY-MM-DD:YYYY-MM-DD). The reports follow
# the bar like every other page; anything unrecognised reads as the default, as it does elsewhere.
PERIOD = r"^[a-z_0-9:-]{1,40}$"


def _span(window: str, period: str | None) -> str:
    from ..services.metrics import canonical_period
    return canonical_period(period) if period else window


@router.get("/businesses/{key}/lead-sources/{gkey}/cohorts")
async def cohorts(key: str, gkey: str, start: str | None = Query(None, pattern=MONTH),
                  end: str | None = Query(None, pattern=MONTH),
                  period: str | None = Query(None, pattern=PERIOD),
                  user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """The cohort report: one row per month of arrival, from stored rows only. `period` picks the
    cohort months the date bar's range touches. 404 for a missing or inactive group; 403 without
    the business's tab."""
    from ..services.lead_source_report import cohort_report
    b = await business_for(s, user, key)
    g = await group_for(s, user, b, gkey, active_only=True)
    if start and end and start > end:
        raise HTTPException(400, "The first month comes before the last.")
    if period:
        from ..services.metrics import canonical_period
        return await cohort_report(s, user.tenant_id, b, g, period=canonical_period(period))
    return await cohort_report(s, user.tenant_id, b, g, start=start, end=end)


@router.get("/businesses/{key}/lead-sources/{gkey}/cohorts/leads")
async def cohort_people(key: str, gkey: str, month: str | None = Query(None, pattern=MONTH),
                        user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    from ..services.lead_source_report import cohort_leads
    b = await business_for(s, user, key)
    g = await group_for(s, user, b, gkey, active_only=True)
    return await cohort_leads(s, user.tenant_id, b, g, month=month)


WINDOW = r"^(all_time|rolling_30|rolling_90|last_4mo|ytd)$"


@router.get("/businesses/{key}/lead-sources/{gkey}/pipeline")
async def pipeline(key: str, gkey: str, window: str = Query("all_time", pattern=WINDOW),
                   period: str | None = Query(None, pattern=PERIOD),
                   user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """The team pipeline: the eight-stage funnel, coaching tiers and the agent table."""
    from ..services.lead_source_pipeline import team_pipeline
    b = await business_for(s, user, key)
    g = await group_for(s, user, b, gkey, active_only=True)
    return await team_pipeline(s, user.tenant_id, b, g, window=_span(window, period))


@router.get("/businesses/{key}/lead-sources/{gkey}/pipeline/agents/{agent_key}")
async def agent_pipeline(key: str, gkey: str, agent_key: str,
                         window: str = Query("all_time", pattern=WINDOW),
                         period: str | None = Query(None, pattern=PERIOD),
                         user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """One agent's pipeline. `agent_key` is the Axcion agent id, `fub-…` for an agent FUB names that
    Axcion does not hold (lead_source_report.agent_key), or `unassigned`."""
    from ..services.lead_source_pipeline import agent_pipeline as build
    b = await business_for(s, user, key)
    g = await group_for(s, user, b, gkey, active_only=True)
    out = await build(s, user.tenant_id, b, g, agent_key, window=_span(window, period))
    if out is None:
        raise HTTPException(404, "That agent has no leads from this source in this date range")
    return out


@router.get("/businesses/{key}/lead-sources/{gkey}/whats-working")
async def whats_working(key: str, gkey: str, window: str = Query("all_time", pattern=WINDOW),
                        period: str | None = Query(None, pattern=PERIOD),
                        user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """What's Working: behavior medians by agent, team and outcome, and 72-hour lead journeys."""
    from ..services.lead_source_behavior import whats_working as build
    b = await business_for(s, user, key)
    g = await group_for(s, user, b, gkey, active_only=True)
    return await build(s, user.tenant_id, b, g, window=_span(window, period))


# ── weekly digest (phase 6) ──────────────────────────────────────────────────────────────────

class DigestIn(BaseModel):
    is_active: bool | None = None
    send_weekday: int | None = None
    send_hour: int | None = None
    send_minute: int | None = None
    timezone: str | None = None
    recipients: list[str] | None = None
    max_agents: int | None = None
    min_leads: int | None = None
    include_reinforce: bool | None = None
    benchmarks: dict | None = None
    clickup_list_id: str | None = None
    clickup_assignee: str | None = None
    agent_map: dict | None = None
    model: str | None = None
    clickup_token: str | None = None          # write-only: stored encrypted, never read back


_EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


async def _digest_view(s, user, g):
    from ..models import Integration, LeadSourceDigest
    from ..services import lead_source_digest as ld
    from ..services.sync import _workspace_tz
    d = (await s.execute(select(LeadSourceDigest).where(
        LeadSourceDigest.tenant_id == user.tenant_id, LeadSourceDigest.lead_source_group_id == g.id))).scalars().first()
    token_set = bool((await s.execute(select(Integration.id).where(
        Integration.tenant_id == user.tenant_id, Integration.provider == "clickup",
        Integration.access_token_enc.is_not(None)))).first())
    tz = await _workspace_tz(s, user.tenant_id)
    return d, ld.digest_dict(d, token_set=token_set, default_tz=getattr(tz, "key", str(tz)))


@router.get("/businesses/{key}/lead-sources/{gkey}/digest")
async def get_digest(key: str, gkey: str, user: User = Depends(require_role(*ADMIN_ROLES)),
                     s: AsyncSession = Depends(get_session)):
    b = await business_for(s, user, key)
    g = await group_for(s, user, b, gkey, active_only=False)
    return (await _digest_view(s, user, g))[1]


@router.put("/businesses/{key}/lead-sources/{gkey}/digest")
async def put_digest(key: str, gkey: str, body: DigestIn, user: User = Depends(require_role(*ADMIN_ROLES)),
                     s: AsyncSession = Depends(get_session)):
    import re
    from zoneinfo import ZoneInfo
    from ..models import Integration, LeadSourceDigest
    from ..security import enc
    from ..services import lead_source_digest as ld
    b = await business_for(s, user, key)
    g = await group_for(s, user, b, gkey, active_only=False)
    d, _view = await _digest_view(s, user, g)
    if d is None:
        d = LeadSourceDigest(tenant_id=user.tenant_id, lead_source_group_id=g.id, is_active=False,
                             recipients=[], unsubscribed=[], model=ld.DEFAULT_MODEL)
        s.add(d)
    f = body.model_dump(exclude_unset=True)
    try:
        if "send_weekday" in f and not (0 <= int(f["send_weekday"]) <= 6):
            raise ls.InvalidGroup("The day is Monday to Sunday.")
        if "send_hour" in f and not (0 <= int(f["send_hour"]) <= 23):
            raise ls.InvalidGroup("The hour is 0 to 23.")
        if "send_minute" in f and not (0 <= int(f["send_minute"]) <= 59):
            raise ls.InvalidGroup("The minute is 0 to 59.")
        if f.get("timezone"):
            try:
                ZoneInfo(f["timezone"])
            except Exception:  # noqa: BLE001
                raise ls.InvalidGroup(f"{f['timezone']} is not a time zone name.") from None
        if "max_agents" in f and not (1 <= int(f["max_agents"]) <= 12):
            raise ls.InvalidGroup("Agents per digest is 1 to 12.")
        if "min_leads" in f and not (1 <= int(f["min_leads"]) <= 100):
            raise ls.InvalidGroup("The minimum is 1 to 100 leads.")
        if "recipients" in f:
            emails = []
            for e in f["recipients"] or []:
                e = str(e).strip().lower()
                if not e:
                    continue
                if not re.match(_EMAIL, e):
                    raise ls.InvalidGroup(f"{e} is not an email address.")
                if e not in emails:
                    emails.append(e)
            if len(emails) > 25:
                raise ls.InvalidGroup("At most 25 recipients.")
            f["recipients"] = emails
        if "benchmarks" in f and f["benchmarks"] is not None:
            for k, v in (f["benchmarks"].get("transitions") or {}).items():
                if k not in ld.DEFAULT_BENCHMARKS["transitions"]:
                    raise ls.InvalidGroup(f"Unknown funnel step {k}.")
                for side in ("floor", "target"):
                    if side in v and not (0 < float(v[side]) <= 1):
                        raise ls.InvalidGroup("Each rate is between 0 and 100%.")
        if "model" in f and not (f["model"] or "").strip():
            f["model"] = ld.DEFAULT_MODEL
    except (ls.InvalidGroup, ValueError, TypeError) as e:
        _bad(e)
    token = f.pop("clickup_token", None)
    for name, value in f.items():
        setattr(d, name, value.strip() if isinstance(value, str) else value)
    if token is not None:
        integ = (await s.execute(select(Integration).where(Integration.tenant_id == user.tenant_id,
                                                           Integration.provider == "clickup"))).scalars().first()
        if integ is None:
            integ = Integration(tenant_id=user.tenant_id, provider="clickup", business_id=None, status="connected")
            s.add(integ)
        integ.access_token_enc = enc(token.strip()) if token.strip() else None
        integ.status = "connected" if token.strip() else "disconnected"
    audit(s, user.tenant_id, user.id, "lead_source.digest_saved", "lead_source_group", g.id,
          {"business": b.key, "key": g.key, "fields": sorted(f) + (["clickup_token"] if token is not None else [])},
          category="Config", summary=f"Weekly digest for {g.display_name} saved")
    await s.commit()
    return (await _digest_view(s, user, g))[1]


@router.post("/businesses/{key}/lead-sources/{gkey}/digest/test")
async def test_digest(key: str, gkey: str, user: User = Depends(require_role(*ADMIN_ROLES)),
                      s: AsyncSession = Depends(get_session)):
    """Send this week's digest now, to its recipients, marked as a test. Off or on, it sends."""
    from ..models import LeadSourceDigestDelivery
    from ..services import lead_source_digest as ld
    b = await business_for(s, user, key)
    g = await group_for(s, user, b, gkey, active_only=False)
    d, _v = await _digest_view(s, user, g)
    if d is None or not d.recipients:
        raise HTTPException(400, "Add a recipient and save before sending a test.")
    tz = await ld.tz_of(s, d)
    week = ld.week_ending(dt.datetime.now(dt.timezone.utc).astimezone(tz))
    row = LeadSourceDigestDelivery(tenant_id=user.tenant_id, digest_id=d.id, run_key=f"test:{uuid.uuid4().hex}",
                                   week_ending=week, is_test=True, status="queued")
    s.add(row)
    await s.commit()
    row = await ld.send(s, row)
    return _delivery_dict(row)


@router.post("/businesses/{key}/lead-sources/{gkey}/digest/clickup-test")
async def test_clickup(key: str, gkey: str, user: User = Depends(require_role(*ADMIN_ROLES)),
                       s: AsyncSession = Depends(get_session)):
    from ..services import lead_source_digest as ld
    b = await business_for(s, user, key)
    g = await group_for(s, user, b, gkey, active_only=False)
    d, _v = await _digest_view(s, user, g)
    if d is None or not (d.clickup_list_id or "").strip():
        raise HTTPException(400, "Set a ClickUp list and save before testing it.")
    out = await ld.clickup_tasks(s, d, [{"agent": "Test", "tier": "coach", "headline": f"{g.display_name} digest test",
                                         "body": "A test task from Axcion's weekly digest settings. Safe to delete."}],
                                 dt.date.today())
    if not out or out[0].get("error") or (out[0].get("status") or 500) >= 300:
        raise HTTPException(400, (out[0].get("error") if out else None) or f"ClickUp answered {out[0].get('status')}.")
    return {"ok": True, "task_id": out[0].get("id")}


def _delivery_dict(r) -> dict:
    return {"id": str(r.id), "week_ending": r.week_ending.isoformat(), "is_test": r.is_test, "status": r.status,
            "attempts": r.attempts, "last_error": r.last_error, "recipients": r.recipients or [],
            "subject": r.subject, "model_used": r.model_used, "agents": r.agents or [],
            "clickup_tasks": r.clickup_tasks or [],
            "sent_at": r.sent_at.isoformat() if r.sent_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None}


@router.get("/businesses/{key}/lead-sources/{gkey}/digest/deliveries")
async def digest_deliveries(key: str, gkey: str, user: User = Depends(require_role(*ADMIN_ROLES)),
                            s: AsyncSession = Depends(get_session)):
    from ..models import LeadSourceDigestDelivery
    b = await business_for(s, user, key)
    g = await group_for(s, user, b, gkey, active_only=False)
    d, _v = await _digest_view(s, user, g)
    if d is None:
        return {"deliveries": []}
    rows = (await s.execute(select(LeadSourceDigestDelivery).where(
        LeadSourceDigestDelivery.tenant_id == user.tenant_id, LeadSourceDigestDelivery.digest_id == d.id)
        .order_by(LeadSourceDigestDelivery.created_at.desc()).limit(20))).scalars().all()
    return {"deliveries": [_delivery_dict(r) for r in rows]}


class UnsubIn(BaseModel):
    token: str


@router.post("/public/lead-sources/digest/unsubscribe")
async def digest_unsubscribe(body: UnsubIn, s: AsyncSession = Depends(get_session)):
    """The link in every digest email. No sign-in: the signed token names the digest and address."""
    from ..services import lead_source_digest as ld
    email = await ld.unsubscribe(s, body.token)
    if email is None:
        raise HTTPException(400, "This unsubscribe link has expired or isn't valid.")
    return {"ok": True, "email": email}
