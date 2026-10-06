"""Parity-before-flip validator for the declarative scorecard routing engine (SCORECARD-CONFIG-SPEC §7).

A measurable can auto-sync one of two ways: the legacy hand-written `resolver_key`, or the Phase 2
declarative `source_spec` the routing engine interprets. Flipping a live metric from the first to the
second is only safe if the engine produces the SAME number the resolver did — not just on the unit-test
fixtures, but on THIS tenant's real deals, across real weeks, including the weeks that come back
UNAVAILABLE (feed down / office unmapped) rather than a misleading 0.

This script proves that. For every active measurable on a board whose `resolver_key` has a declarative
twin (`scorecard_routing.RESOLVER_SPECS`), it runs BOTH the hardcoded resolver and `run_spec` against
the same rows, per the metric's own office, for the last N weeks, and reports every disagreement. Zero
disagreements is the green light to flip that metric onto its twin.

It reads only the stable base scorecard columns (never `source_spec` / `scorecard_group.active`), so it
runs against any DB that has the board — including one that has not taken the Phase 1/2 migrations yet.

It is READ-ONLY: it only calls the resolver functions (which only SELECT), sets the session read-only
on Postgres, and never writes. It changes nothing — running it does not flip anything. Do the flip
deliberately afterwards (set `scorecard_metric.source_spec`), re-run this, then let the worker resolve.

Run it against the data you intend to flip:

  # production (on the container, where the internal DATABASE_URL and the deployed code live)
  railway ssh --service executive_dashboard_v1 "python -m scripts.scorecard_parity --business ulrg"

  # or locally against a prod read-replica URL you export yourself (never commit it)
  DATABASE_URL=<postgres public url> python -m scripts.scorecard_parity --business ulrg --weeks 12

Exit code is 0 on full parity, 1 if any metric disagrees, 2 on a usage/lookup problem.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt

from sqlalchemy import select, text

from app.config import settings
from app.db import SessionLocal
from app.models import Business, ScorecardGroup, ScorecardMetric, Tenant
from app.services import scorecard_resolvers as R
from app.services.scorecard_routing import run_spec, validate_spec, RESOLVER_SPECS


def _db_target() -> str:
    url = settings.DATABASE_URL
    if url.startswith("sqlite"):
        return f"LOCAL SQLite ({url.rsplit('/', 1)[-1]})  <-- NOT production"
    try:
        creds, hostpart = url.split("://", 1)[1].split("@", 1)
        return f"Postgres {creds.split(':', 1)[0]}@{hostpart}"
    except (IndexError, ValueError):
        return "Postgres (could not parse the URL)"


def _show(v) -> str:
    if v is R.UNAVAILABLE:
        return "UNAVAIL"
    if v is None:
        return "None"
    return str(int(v)) if float(v).is_integer() else str(v)


def _same(a, b) -> bool:
    return (a is R.UNAVAILABLE and b is R.UNAVAILABLE) or a == b


def _weeks(n: int, today: dt.date):
    monday = today - dt.timedelta(days=today.weekday())
    return [(monday - dt.timedelta(days=7 * i), monday - dt.timedelta(days=7 * i) + dt.timedelta(days=6))
            for i in range(n)]


async def _resolve_business(s, business_key: str, tenant_slug: str | None):
    """(tenant_id, business_id, label) for the board to check, or None with a message printed."""
    q = select(Business.id, Business.tenant_id, Business.name, Tenant.slug).join(
        Tenant, Business.tenant_id == Tenant.id).where(Business.key == business_key)
    if tenant_slug:
        q = q.where(Tenant.slug == tenant_slug)
    rows = (await s.execute(q)).all()
    if not rows:
        print(f"No business key={business_key!r}"
              + (f" for tenant {tenant_slug!r}" if tenant_slug else "") + " in this database.")
        return None
    if len(rows) > 1:
        print(f"{len(rows)} tenants have a {business_key!r} board — pass --tenant <slug> to pick one:")
        for bid, tid, name, slug in rows:
            print(f"  --tenant {slug}   ({name})")
        return None
    bid, tid, name, slug = rows[0]
    return tid, bid, f"{name} [tenant {slug}]"


async def main() -> int:
    ap = argparse.ArgumentParser(description="Prove the routing engine reproduces the live resolvers (read-only).")
    ap.add_argument("--business", default="ulrg", help="business key whose board to check (default: ulrg)")
    ap.add_argument("--tenant", default=None, help="tenant slug, if more than one has this board")
    ap.add_argument("--weeks", type=int, default=8, help="how many trailing weeks to compare (default: 8)")
    args = ap.parse_args()

    print(f"DB target: {_db_target()}")
    print(f"Board: business={args.business!r}  weeks={args.weeks}\n")

    async with SessionLocal() as s:
        if not settings.DATABASE_URL.startswith("sqlite"):
            await s.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))

        found = await _resolve_business(s, args.business, args.tenant)
        if not found:
            return 2
        tid, bid, label = found
        print(f"Checking: {label}")

        # column-level selects only — never touch source_spec / scorecard_group.active, so this runs
        # against a board on any schema version.
        groups = {gid: {"key": k, "name": n, "sisu_group_id": sg} for (gid, k, n, sg) in (await s.execute(
            select(ScorecardGroup.id, ScorecardGroup.key, ScorecardGroup.name, ScorecardGroup.sisu_group_id)
            .where(ScorecardGroup.tenant_id == tid, ScorecardGroup.business_id == bid))).all()}
        metrics = (await s.execute(
            select(ScorecardMetric.name, ScorecardMetric.resolver_key, ScorecardMetric.group_id)
            .where(ScorecardMetric.tenant_id == tid, ScorecardMetric.active.is_(True))
            .order_by(ScorecardMetric.sort_order))).all()

        weeks = _weeks(args.weeks, dt.date.today())
        checkable = skipped = 0
        total = mismatches = 0
        problems: list[str] = []

        for name, resolver_key, group_id in metrics:
            g = groups.get(group_id)
            spec = RESOLVER_SPECS.get(resolver_key)
            fn = R.RESOLVERS.get(resolver_key)
            office = g["name"] if g else "?"
            if spec is None or fn is None:
                skipped += 1
                why = "no declarative twin yet" if fn else f"no resolver {resolver_key!r}"
                print(f"  - skip   {office:<16} {name[:34]:<34} ({why})")
                continue
            errs = validate_spec(spec)
            if errs:
                problems.append(f"  INVALID SPEC  {office} / {name}: {errs}")
                mismatches += 1
                continue
            checkable += 1
            group = {"key": g["key"], "sisu_group_id": g["sisu_group_id"]} if g else None
            cells, bad = [], 0
            for ws, we in weeks:
                hard = await fn(s, tid, bid, ws, we, group=group)
                got = await run_spec(spec, s, tid, bid, ws, we, group=group)
                total += 1
                if not _same(hard, got):
                    bad += 1
                    problems.append(f"  MISMATCH  {ws} {office} / {name}: resolver={_show(hard)} spec={_show(got)}")
                cells.append(f"{_show(hard)}")
            mismatches += bad
            flag = "OK " if bad == 0 else f"!! {bad}"
            print(f"  {flag} {office:<16} {name[:30]:<30} [twin:{resolver_key}]  last{args.weeks}w: {' '.join(cells)}")

    if problems:
        print("\n" + "\n".join(problems))
    print(f"\nMeasurables checked: {checkable}   skipped: {skipped}")
    print(f"Comparisons: {total}   mismatches: {mismatches}")
    if checkable == 0:
        print("RESULT: nothing to compare (no measurable on this board has a declarative twin yet).")
        return 0
    print("RESULT:", "PASS — the engine reproduces the live resolvers on this data; safe to flip."
          if mismatches == 0 else f"FAIL — {mismatches} disagreement(s); do NOT flip until resolved.")
    return 0 if mismatches == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
