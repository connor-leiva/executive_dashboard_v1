"""ULRG L10 Scorecard — resolver registry + runner (SPEC-ulrg-scorecard Part 3.1, Step 5).

A resolver maps `(session, tenant_id, business_id, week_start, week_end)` to a single
`numeric | None`. Register one with `@resolver(key)`; a metric opts in by setting its
`resolver_key`. The worker (`app.worker.scorecard_tick`) runs every active metric that has a
resolver_key once a day for the current open week, and again each Monday for the week that just
closed, writing `scorecard_value` with `source="resolver"`.

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

from ..models import (Integration, ScorecardGroup, ScorecardMetric, ScorecardValue, Transaction)

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


@resolver("ulrg_homes_closed")
async def homes_closed(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date):
    """Homes closed in the week — count of Sisu `Transaction`s with status=closed and a real sale
    (sale_price > 0) whose `close_date` falls in [week_start, week_end], across the whole business.

    `sale_price > 0` matches the rest of the dashboard's units-closed accounting (services/metrics
    `require_sale=True`) and Sisu's own Closed count: it drops $0 outbound-referral "closings",
    which otherwise inflate the count ~7%. Business-wide by necessity: Sisu pulls one team (621),
    and the record's `agent` object carries no Davis/SLC/Utah-County split — so this powers the
    Overall "ULRG Q2 – 250 Homes" row, not the per-team Homes-Sold rows. Returns None only when
    Sisu isn't a live feed; a live week with no closings is a real 0."""
    if not await _sisu_live(s, tenant_id, business_id):
        return None
    n = await s.scalar(select(func.count()).select_from(Transaction).where(
        Transaction.tenant_id == tenant_id,
        Transaction.business_id == business_id,
        Transaction.status == "closed",
        Transaction.sale_price > 0,                     # real sales only (excludes $0 outbound referrals)
        Transaction.close_date >= week_start,
        Transaction.close_date <= week_end))
    return float(n)   # func.count() is never None; a live week with 0 closings stays a real 0


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
            select(ScorecardMetric.id, ScorecardMetric.resolver_key, ScorecardGroup.business_id)
            .join(ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id)
            .where(ScorecardMetric.tenant_id == tenant_id,
                   ScorecardMetric.active.is_(True),
                   ScorecardMetric.resolver_key.is_not(None)))).all()

    written = 0
    for metric_id, key, business_id in metrics:
        fn = RESOLVERS.get(key)
        if fn is None:
            log.warning("scorecard: metric %s names resolver %r, which isn't registered", metric_id, key)
            continue
        for ws, we in weeks:
            async with session_factory() as s:
                try:
                    val = await fn(s, tenant_id, business_id, ws, we)
                    await _write(s, tenant_id, metric_id, ws, val, key)
                    await s.commit()
                    written += 1
                except Exception:                       # isolated: this value is left untouched
                    await s.rollback()
                    log.exception("scorecard: resolver %s failed for metric %s week %s", key, metric_id, ws)
    return written
