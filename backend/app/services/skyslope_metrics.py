"""The SkySlope numbers a brokerage sees: docs/skyslope-integration-spec.md, "Metrics" (Phase 5).

Computed from the skyslope_* tables directly, per business, for one period. The projection into
`transaction` (services/skyslope_project.py) is what lights the existing production tiles; this is
what those tiles cannot say: coverage, the pipeline by state, who is past due, who has gone quiet.

EVERY NUMBER CARRIES A CHIP saying how far to trust it, and the chip is computed, never set by hand:
  live              synced, and the metric is defined
  needs_sync        no key has finished a sync yet, so zero would be a guess
  needs_key_scope   the connected keys cannot see the whole team, so a team total would read low
  needs_definition  nobody has said which field it is (GCI), so there is no number to show
`sample` is separate and overrides nothing: it says the files themselves are invented.

CREDIT (decision 6): a file is a full unit to its primary agent. Co-agent files are counted beside
it, never added to it, so the team total is a count of files rather than of people on them.

A FILE NO KEY CAN SEE ANY MORE (`removed_from_source_at`) counts for nothing. It is kept, because
the API has no deletions feed, but it is no longer a fact about the business.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Integration, SkyslopeFileAgent, SkyslopeListing, SkyslopeSale, SkyslopeUser, SyncRun, Tenant
from . import metrics as period_tools
from .skyslope_normalize import is_past_due
from .skyslope_sample import is_sample

PROVIDER = "skyslope"
NOT_PRODUCING_KEY = "skyslope_not_producing_days"
NOT_PRODUCING_DEFAULT = 60          # decision 7; per workspace in tenant.config
OFFICES_KEY = "skyslope_roster_offices"
UNDER_CONTRACT = ("under_contract", "past_due")


def _day(at: dt.datetime | None) -> dt.date | None:
    return at.astimezone(dt.timezone.utc).date() if at else None


def _in(at: dt.datetime | None, start: dt.date, end: dt.date) -> bool:
    d = _day(at)
    return d is not None and start <= d <= end


def _money(values) -> float:
    return float(sum((v for v in values if v is not None), Decimal(0)))


async def _keys(s: AsyncSession, tenant_id, business_id) -> list[Integration]:
    q = select(Integration).where(Integration.tenant_id == tenant_id, Integration.provider == PROVIDER,
                                  Integration.status.in_(("connected", "error")))
    if business_id is not None:
        q = q.where(Integration.business_id == business_id)
    return list((await s.execute(q)).scalars().all())


async def _scope(s: AsyncSession, tenant_id, keys: list[Integration]) -> str:
    """'team' if any key could list the people in its offices, 'own' if every key was refused that
    (an agent-level key sees its owner's files), 'unknown' before any sync said either."""
    ids = {str(k.id) for k in keys}
    runs = (await s.execute(select(SyncRun.stats).where(
        SyncRun.tenant_id == tenant_id, SyncRun.provider == PROVIDER, SyncRun.status == "ok")
        .order_by(SyncRun.started_at.desc()).limit(50))).scalars().all()
    seen: dict[str, object] = {}
    for stats in runs:
        sky = (stats or {}).get(PROVIDER) or {}
        iid = sky.get("integration_id")
        if iid in ids and iid not in seen:
            seen[iid] = sky.get("users")
    if any(isinstance(v, int) for v in seen.values()):
        return "team"
    if seen and all(v == "denied" for v in seen.values()):
        return "own"
    return "unknown"


async def overview(s: AsyncSession, tenant_id, business_id=None, period: str | None = None,
                   today: dt.date | None = None) -> dict:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    period = period_tools.canonical_period(period)
    start, end = period_tools._period_range(period, today)
    cfg = (await s.execute(select(Tenant.config).where(Tenant.id == tenant_id))).scalar_one_or_none() or {}
    quiet_days = int(cfg.get(NOT_PRODUCING_KEY) or NOT_PRODUCING_DEFAULT)
    roster_offices = {str(o).lower() for o in (cfg.get(OFFICES_KEY) or [])}

    keys = await _keys(s, tenant_id, business_id)
    synced = [k.last_synced_at for k in keys if k.last_synced_at]
    sample = any(is_sample(k) for k in keys)
    scope = await _scope(s, tenant_id, keys) if keys else "unknown"

    def files(model):
        q = select(model).where(model.tenant_id == tenant_id, model.removed_from_source_at.is_(None))
        return q.where(model.business_id == business_id) if business_id is not None else q

    listings = (await s.execute(files(SkyslopeListing))).scalars().all()
    sales = (await s.execute(files(SkyslopeSale))).scalars().all()
    users = {u.user_guid: u for u in (await s.execute(select(SkyslopeUser).where(
        SkyslopeUser.tenant_id == tenant_id))).scalars().all()}
    co = defaultdict(set)      # user -> files they are on as somebody other than the primary agent
    guids = {x.listing_guid for x in listings} | {x.sale_guid for x in sales}
    for fa in (await s.execute(select(SkyslopeFileAgent).where(
            SkyslopeFileAgent.tenant_id == tenant_id, SkyslopeFileAgent.role.in_(("co_agent", "co_listing"))))).scalars():
        if fa.file_guid in guids:
            co[fa.user_guid].add(fa.file_guid)

    # ── the roster: the people in the configured offices, plus anybody credited on a file ──
    def in_roster(u: SkyslopeUser) -> bool:
        if u.deactivated or (u.user_type or "").lower() not in ("agent", ""):
            return False
        return not roster_offices or bool(roster_offices & {str(o).lower() for o in (u.office_guids or [])})

    roster = {g for g, u in users.items() if in_roster(u)}
    roster |= {f.agent_guid for f in [*listings, *sales] if f.agent_guid and not (
        users.get(f.agent_guid) and users[f.agent_guid].deactivated)}

    # ── team metrics ──
    active = [x for x in listings if x.status == "active"]
    # SkySlope's own Expired (`past_due`) is a contract that missed its closing date: still under
    # contract, and counted so, as the Pending Pipeline tile counts it. Flagged as past due beside it.
    under = [x for x in sales if x.status in UNDER_CONTRACT]
    past_due = [x for x in sales if is_past_due(x.status, x.expected_close_at, today)]
    pre = [x for x in sales if x.status == "pre_contract"]
    new_contracts = [x for x in sales if x.status not in ("pre_contract", "unmapped") and _in(x.contract_accepted_at, start, end)]
    closed = [x for x in sales if x.status == "closed" and _in(x.closed_at, start, end)]
    canceled = [x for x in [*listings, *sales] if x.status == "canceled" and _in(x.canceled_at, start, end)]
    unmapped = sorted({f"{'listing' if isinstance(x, SkyslopeListing) else 'sale'}: {x.status_raw or '(blank)'}"
                       for x in [*listings, *sales] if x.status in (None, "unmapped")})

    if not keys or not synced:
        chip = "needs_sync"
    elif scope == "own":
        chip = "needs_key_scope"
    else:
        chip = "live"

    def metric(key, label, items, *, volume_of=None, note=None, as_of_now=False):
        return {"key": key, "label": label, "count": len(items),
                "volume": _money(volume_of(x) for x in items) if volume_of else None,
                "chip": chip, "note": note, "as_of_now": as_of_now}

    team = [
        metric("active_listings", "Active listings", active, volume_of=lambda x: x.list_price, as_of_now=True),
        metric("under_contract", "Under contract", under, volume_of=lambda x: x.sale_price, as_of_now=True,
               note=f"{len(past_due)} past due" if past_due else None),
        metric("new_contracts", "New contracts", new_contracts, volume_of=lambda x: x.sale_price),
        metric("closed", "Closed", closed, volume_of=lambda x: x.sale_price),
        metric("canceled", "Canceled", canceled),
    ]

    # ── per agent ──
    cutoff = today - dt.timedelta(days=quiet_days)
    rows = []
    for guid in roster:
        u = users.get(guid)
        mine_l = [x for x in listings if x.agent_guid == guid]
        mine_s = [x for x in sales if x.agent_guid == guid]
        starts = [d for d in ([_day(x.listed_at) for x in mine_l] + [_day(x.contract_accepted_at) for x in mine_s]) if d]
        last = max(starts) if starts else None
        rows.append({
            "user_guid": guid,
            "name": (u.name if u and u.name else f"Unknown agent ····{guid[-4:]}"),
            "known": bool(u),
            "active_listings": sum(x.status == "active" for x in mine_l),
            "under_contract": sum(x.status in UNDER_CONTRACT for x in mine_s),
            "past_due": sum(is_past_due(x.status, x.expected_close_at, today) for x in mine_s),
            "new_contracts": sum(x in new_contracts for x in mine_s),
            "closed": sum(x in closed for x in mine_s),
            "closed_volume": _money(x.sale_price for x in mine_s if x in closed),
            "co_agent_files": len(co.get(guid, ())),
            "last_activity": last.isoformat() if last else None,
            "not_producing": last is None or last < cutoff,
        })
    rows.sort(key=lambda r: (-r["closed_volume"], -r["closed"], -r["under_contract"], r["name"]))
    quiet = [r for r in rows if r["not_producing"]]

    n = len(roster)
    if scope == "own":
        coverage = {"state": "partial", "covered": len({f.agent_guid for f in [*listings, *sales] if f.agent_guid}),
                    "roster": None, "label": "Partial: these keys see only their owners' own files"}
    else:
        coverage = {"state": "full" if keys and synced else "unknown", "covered": n, "roster": n,
                    "label": f"All {n} agents" if keys and synced else "Not synced yet"}

    return {
        "period": period, "period_label": period_tools.period_label(period),
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "sample": sample,
        "connection": {"keys": len(keys), "last_synced_at": max(synced).isoformat() if synced else None,
                       "scope": scope},
        "coverage": coverage,
        "metrics": team,
        "pre_contract": len(pre),
        "past_due": [{"user_guid": x.agent_guid, "name": (users[x.agent_guid].name if x.agent_guid in users else None),
                      "expected_close": _day(x.expected_close_at).isoformat() if x.expected_close_at else None,
                      "sale_price": float(x.sale_price) if x.sale_price is not None else None,
                      "city": x.city, "status_raw": x.status_raw}
                     for x in sorted(past_due, key=lambda x: x.expected_close_at or dt.datetime.min.replace(tzinfo=dt.timezone.utc))],
        "not_producing": {"days": quiet_days, "count": len(quiet), "agents": [r["name"] for r in quiet],
                          "chip": chip},
        "gci": {"chip": "needs_definition",
                "note": "No SkySlope field is named GCI yet. It stays blank, not $0, until one is chosen."},
        "agents": rows,
        "unmapped_statuses": unmapped,
    }
