"""ULRG L10 Scorecard — resolver registry + runner (SPEC-ulrg-scorecard Part 3.1, Step 5).

A resolver maps `(session, tenant_id, business_id, week_start, week_end, group)` to a single
`numeric | None` (`group` carries the scorecard team's context — `sisu_group_id` — for per-team
resolvers; business-wide ones ignore it). Register one with `@resolver(key)`; a metric opts in by
setting its `resolver_key`. The worker (`app.worker.scorecard_tick`) runs every active
resolver-backed metric once a day over the trailing look-back (the open week + the 2 before it, so
a late sync self-heals), writing `scorecard_value` with `source="resolver"`.

null ≠ zero (SPEC 0.2 / 2.3): a resolver returns `None` only when its source is not a live feed —
a *collection gap*. A live source that genuinely counts nothing returns `0`, a real datum. So a
count resolver must NOT coalesce a real zero into None (the acceptance grep forbids that idiom in
this file); it guards on whether the feed is live and otherwise reports the true count, zero
included.

Overwrite rules (SPEC 3.1 / 3.3):
  - resolver raises            → caught, logged, the stored value is left untouched.
  - resolver returns a number  → written as `source="resolver"`; if it displaces a hand-entered
                                 value we log a warning so the conflict is visible, not mysterious.
  - resolver returns None      → clears a stale *resolver* value, but never erases a *manual* one
                                 (a transient feed gap must not delete someone's typed number).
"""
from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Awaitable, Callable
from decimal import Decimal

from sqlalchemy import func, or_, select

from ..models import (Agent, Integration, ScorecardGroup, ScorecardMetric, ScorecardValue, Transaction)

log = logging.getLogger("app")

Resolver = Callable[..., Awaitable[float | None]]
RESOLVERS: dict[str, Resolver] = {}


def resolver(key: str):
    """Register a resolver under `key`; a metric wires to it via `resolver_key`."""
    def wrap(fn: Resolver) -> Resolver:
        RESOLVERS[key] = fn
        return fn
    return wrap


async def _sisu_live(s, tenant_id, business_id) -> bool:
    """True only if Sisu is a connected, at-least-once-synced feed for THIS business (or a
    tenant-wide feed with no business set). This is the null≠zero gate, and it is scoped to the
    same business the count uses — otherwise one business's live feed could make another's genuine
    collection gap read as a real `0`."""
    row = await s.scalar(select(Integration.id).where(
        Integration.tenant_id == tenant_id, Integration.provider == "sisu",
        Integration.status == "connected", Integration.last_synced_at.is_not(None),
        or_(Integration.business_id == business_id, Integration.business_id.is_(None))).limit(1))
    return row is not None


def _window_filters(key: str, week_start: dt.date, week_end: dt.date):
    """The per-deal WHERE clauses + the anchoring date column behind a resolver key. Shared by the
    COUNT resolver and the drill-down RECORD list so the two can never drift (the list a user clicks
    into is exactly the deals the count counted). Office restriction is applied separately. Returns
    (clauses, date_col) or (None, None) for an unknown key."""
    if key in ("ulrg_homes_closed", "ulrg_team_homes_closed"):
        return ([Transaction.status == "closed", Transaction.sale_price > 0,   # real sales; drops $0 referrals
                 Transaction.close_date >= week_start, Transaction.close_date <= week_end],
                Transaction.close_date)
    if key == "ulrg_team_under_contract":
        return ([Transaction.sale_price > 0,
                 Transaction.contract_date >= week_start, Transaction.contract_date <= week_end],
                Transaction.contract_date)
    if key == "ulrg_team_appts_met":
        return ([Transaction.appt_met_date >= week_start, Transaction.appt_met_date <= week_end],
                Transaction.appt_met_date)
    if key == "ulrg_team_signed":
        return ([Transaction.signed_date >= week_start, Transaction.signed_date <= week_end],
                Transaction.signed_date)
    return None, None


@resolver("ulrg_homes_closed")
async def homes_closed(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Homes closed in the week — count of Sisu `Transaction`s with status=closed and a real sale
    (sale_price > 0) whose `close_date` falls in [week_start, week_end], across the whole business.

    `sale_price > 0` matches the rest of the dashboard's units-closed accounting (services/metrics
    `require_sale=True`) and Sisu's own Closed count: it drops $0 outbound-referral "closings",
    which otherwise inflate the count ~7%. Business-wide (ignores `group`): powers the Overall
    "ULRG Q2 – 250 Homes" row — includes every office, even out-of-state agents. Returns None only
    when Sisu isn't a live feed; a live week with no closings is a real 0."""
    if not await _sisu_live(s, tenant_id, business_id):
        return None
    clauses, _ = _window_filters("ulrg_homes_closed", week_start, week_end)
    n = await s.scalar(select(func.count()).select_from(Transaction).where(
        Transaction.tenant_id == tenant_id, Transaction.business_id == business_id, *clauses))
    return float(n)   # func.count() is never None; a live week with 0 closings stays a real 0


async def _office_agent_ids(s, tenant_id, sisu_group_id: int) -> set | None:
    """Agent ids whose live Sisu memberships include this office group. None when the roster hasn't
    been synced yet (no agent carries memberships) — a collection gap, not an empty office. The JSON
    array membership is filtered in Python (cross-dialect + the roster is ~140 agents, tiny)."""
    rows = (await s.execute(select(Agent.id, Agent.sisu_group_ids).where(
        Agent.tenant_id == tenant_id, Agent.source == "sisu",
        Agent.sisu_group_ids.is_not(None)))).all()
    if not rows:
        return None
    return {aid for aid, groups in rows if isinstance(groups, list) and sisu_group_id in groups}


async def _team_count(s, tenant_id, business_id, group, extra) -> float | None:
    """Shared per-team body: None if the team isn't mapped to a Sisu office, if Sisu isn't live, or
    if the roster isn't synced (all gaps); otherwise the real count (0 included) of the team's deals
    matching `extra` (a list of WHERE clauses). Attribution is via the deal's agent's office. A
    synced-but-empty office (no agents assigned to it) is a real 0, not a gap — see null≠zero."""
    sgid = (group or {}).get("sisu_group_id")
    if sgid is None:
        return None                                     # e.g. 'overall' — not a per-team row
    if not await _sisu_live(s, tenant_id, business_id):
        return None
    ids = await _office_agent_ids(s, tenant_id, sgid)
    if ids is None:                                     # roster not synced yet → gap (not 0)
        return None
    n = await s.scalar(select(func.count()).select_from(Transaction).where(
        Transaction.tenant_id == tenant_id, Transaction.business_id == business_id,
        Transaction.agent_id.in_(ids), *extra))         # empty office → IN () → real 0
    return float(n)


@resolver("ulrg_team_homes_closed")
async def team_homes_closed(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Per-team Homes Sold — closed real sales in the week whose agent is in this team's Sisu office
    (group.sisu_group_id). Same real-sale rule as the Overall row; scoped to the office's agents."""
    return await _team_count(s, tenant_id, business_id, group,
                             _window_filters("ulrg_team_homes_closed", week_start, week_end)[0])


@resolver("ulrg_team_under_contract")
async def team_under_contract(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Per-team Under Contract — deals that WENT under contract in the week (contract_date in the
    window) whose agent is in this team's office. A weekly flow of new contracts; sale_price > 0
    drops $0 referrals (contract price)."""
    return await _team_count(s, tenant_id, business_id, group,
                             _window_filters("ulrg_team_under_contract", week_start, week_end)[0])


@resolver("ulrg_team_appts_met")
async def team_appts_met(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Per-team Appointments Met — deals whose appointment was HELD in the week (Sisu `appt_dt` →
    `appt_met_date` in the window) whose agent is in this team's office. Counts the dated event, not
    the current pipeline stage, so a deal that has since progressed still counts for its appt week.
    No sale_price gate — an appointment isn't a sale."""
    return await _team_count(s, tenant_id, business_id, group,
                             _window_filters("ulrg_team_appts_met", week_start, week_end)[0])


@resolver("ulrg_team_signed")
async def team_signed(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Per-team Clients Signed — buyer/listing agreements signed in the week (Sisu `signed_dt` →
    `signed_date` in the window) whose agent is in this team's office. Dated event, not current stage."""
    return await _team_count(s, tenant_id, business_id, group,
                             _window_filters("ulrg_team_signed", week_start, week_end)[0])


async def resolver_records(s, tenant_id, business_id, key: str, week_start: dt.date, week_end: dt.date,
                           group=None, limit: int = 500):
    """The underlying Sisu deals behind a resolver's count for a (metric, window) — powers the grid
    drill-down. Same WHERE clauses as the count (via `_window_filters`) + the same office restriction
    for per-team keys, so the list is exactly what was counted. Returns [] for an office that maps to
    no agents (or an unmapped/overall-only key with no records), None for an unknown key."""
    clauses, date_col = _window_filters(key, week_start, week_end)
    if clauses is None:
        return None
    q = (select(Transaction, Agent.name).outerjoin(Agent, Transaction.agent_id == Agent.id)
         .where(Transaction.tenant_id == tenant_id, Transaction.business_id == business_id, *clauses))
    if key.startswith("ulrg_team_"):
        sgid = (group or {}).get("sisu_group_id")
        ids = await _office_agent_ids(s, tenant_id, sgid) if sgid is not None else None
        if not ids:                                     # unmapped office or roster not synced → no rows
            return []
        q = q.where(Transaction.agent_id.in_(ids))
    q = q.order_by(date_col.desc()).limit(limit)
    rows = (await s.execute(q)).all()
    out = []
    for txn, agent_name in rows:
        d = getattr(txn, date_col.key)
        out.append({"id": txn.external_id, "client": txn.buyer_name, "agent": agent_name,
                    "date": d.isoformat() if d else None, "address": txn.address,
                    "sale_price": float(txn.sale_price) if txn.sale_price is not None else None})
    return out


# ── runner (called by the worker) ────────────────────────────────────────────
LOOKBACK_WEEKS = 3     # re-resolve the open week + the 2 before it every day, so a missed Monday
                       # run or a late Sisu sync self-heals — writes are idempotent upserts.


def _recent_weeks(today: dt.date, n: int = LOOKBACK_WEEKS) -> list[tuple[dt.date, dt.date]]:
    """The `n` most recent Monday…Sunday weeks (newest first): the open week and the n-1 before it.
    Weeks are keyed by their Monday (SPEC 2.3)."""
    monday = today - dt.timedelta(days=today.weekday())
    weeks = []
    for k in range(n):
        ws = monday - dt.timedelta(days=7 * k)
        weeks.append((ws, ws + dt.timedelta(days=6)))
    return weeks


async def _write(s, tenant_id, metric_id, week_start: dt.date, val, key: str) -> None:
    """Upsert one resolver result, honoring the overwrite rules above."""
    row = (await s.execute(select(ScorecardValue).where(
        ScorecardValue.metric_id == metric_id,
        ScorecardValue.week_start == week_start))).scalar_one_or_none()

    if val is None:                                     # feed gap
        if row is not None and row.source == "resolver":
            row.value = None                            # clear our own stale number
        return                                          # never erase a manual value with a gap

    dec = Decimal(str(val))
    if row is None:
        s.add(ScorecardValue(tenant_id=tenant_id, metric_id=metric_id, week_start=week_start,
                             value=dec, source="resolver"))
        return
    if row.source == "manual":
        log.warning("scorecard: resolver %s overrode a manual value for metric %s week %s",
                    key, metric_id, week_start)
    row.value, row.source, row.entered_by = dec, "resolver", None


async def run_resolvers(session_factory, tenant_id, today: dt.date) -> int:
    """Run every active resolver-backed metric for the recent weeks (the open week plus the trailing
    look-back, so a late sync or a missed daily run self-heals). Returns the count of (metric, week)
    results committed.

    Each (metric, week) runs in its OWN transaction: a resolver that raises — even mid-query, which
    would poison a shared session — is caught, rolled back, and left untouched (SPEC 3.1), while
    every other metric still commits. `session_factory` is the `SessionLocal` maker."""
    weeks = _recent_weeks(today)

    async with session_factory() as s:                  # read the work list up front, read-only
        metrics = (await s.execute(
            select(ScorecardMetric.id, ScorecardMetric.resolver_key, ScorecardGroup.business_id,
                   ScorecardGroup.key, ScorecardGroup.sisu_group_id)
            .join(ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id)
            .where(ScorecardMetric.tenant_id == tenant_id,
                   ScorecardMetric.active.is_(True),
                   ScorecardMetric.resolver_key.is_not(None)))).all()

    written = 0
    for metric_id, key, business_id, group_key, sisu_group_id in metrics:
        fn = RESOLVERS.get(key)
        if fn is None:
            log.warning("scorecard: metric %s names resolver %r, which isn't registered", metric_id, key)
            continue
        group = {"key": group_key, "sisu_group_id": sisu_group_id}   # context for per-team resolvers
        for ws, we in weeks:
            async with session_factory() as s:
                try:
                    val = await fn(s, tenant_id, business_id, ws, we, group=group)
                    await _write(s, tenant_id, metric_id, ws, val, key)
                    await s.commit()
                    written += 1
                except Exception:                       # isolated: this value is left untouched
                    await s.rollback()
                    log.exception("scorecard: resolver %s failed for metric %s week %s", key, metric_id, ws)
    return written
