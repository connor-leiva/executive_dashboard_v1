"""Flip a measurable's auto-sync onto its declarative source_spec — or roll it back — parity-gated.

Setting `scorecard_metric.source_spec` makes the routing engine (SCORECARD-CONFIG-SPEC Phase 2) resolve
that metric instead of its hardcoded `resolver_key`. The resolver_key is left in place, so rollback is
just clearing the column. This is the "take it live" switch for one metric at a time.

Why this and not a raw UPDATE: the flip is only safe if the engine produces the SAME number the
resolver did. So before writing, this runs the parity check for the targeted metric(s) over --weeks
real weeks and **REFUSES to flip any metric whose spec disagrees with its resolver on this data**. A
flip that this tool performs cannot change the number on the board — the worker's next resolve
re-derives the identical value.

Dry-run by default; --apply writes. --rollback clears source_spec instead (always allowed). Requires
migration 0092 (scorecard_metric.source_spec).

  # preview (no write) — shows exactly which rows and the parity result
  railway ssh --service executive_dashboard_v1 "python -m scripts.scorecard_route --business ulrg --resolver-key ulrg_homes_closed"
  # take it live
  railway ssh --service executive_dashboard_v1 "python -m scripts.scorecard_route --business ulrg --resolver-key ulrg_homes_closed --apply"
  # roll it back
  railway ssh --service executive_dashboard_v1 "python -m scripts.scorecard_route --business ulrg --resolver-key ulrg_homes_closed --rollback --apply"
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import datetime as dt

from sqlalchemy import inspect, select, text, update

from app.config import settings
from app.db import SessionLocal, engine
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


async def _has_source_spec_column() -> bool:
    async with engine.begin() as conn:
        names = await conn.run_sync(
            lambda c: [col["name"] for col in inspect(c).get_columns("scorecard_metric")])
    return "source_spec" in names


async def _resolve_business(s, business_key: str, tenant_slug: str | None):
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
        print(f"{len(rows)} tenants have a {business_key!r} board — pass --tenant <slug>:")
        for bid, tid, name, slug in rows:
            print(f"  --tenant {slug}   ({name})")
        return None
    bid, tid, name, slug = rows[0]
    return tid, bid, f"{name} [tenant {slug}]"


async def main() -> int:
    ap = argparse.ArgumentParser(description="Flip a measurable onto its source_spec, or roll back (parity-gated).")
    ap.add_argument("--business", default="ulrg", help="business key (default: ulrg)")
    ap.add_argument("--tenant", default=None, help="tenant slug, if more than one has this board")
    ap.add_argument("--resolver-key", required=True, help="which measurables to route (by their resolver_key)")
    ap.add_argument("--weeks", type=int, default=8, help="weeks of parity to require before a flip (default: 8)")
    ap.add_argument("--rollback", action="store_true", help="clear source_spec instead of setting it")
    ap.add_argument("--apply", action="store_true", help="write (otherwise dry-run)")
    args = ap.parse_args()

    print(f"DB target: {_db_target()}")
    mode = "ROLLBACK (clear source_spec)" if args.rollback else "FLIP (set source_spec)"
    print(f"{mode}   business={args.business!r}  resolver_key={args.resolver_key!r}  apply={args.apply}\n")

    if not await _has_source_spec_column():
        print("scorecard_metric.source_spec is not present here — deploy/apply migration 0092 first.")
        return 2

    spec = RESOLVER_SPECS.get(args.resolver_key)
    if not args.rollback:
        if spec is None:
            print(f"No declarative twin for resolver_key={args.resolver_key!r}; nothing to flip onto.")
            return 2
        errs = validate_spec(spec)
        if errs:
            print(f"Spec for {args.resolver_key!r} is invalid: {errs}")
            return 2

    async with SessionLocal() as s:
        found = await _resolve_business(s, args.business, args.tenant)
        if not found:
            return 2
        tid, bid, label = found
        print(f"Board: {label}")

        targets = (await s.execute(
            select(ScorecardMetric.id, ScorecardMetric.name, ScorecardMetric.source_spec,
                   ScorecardGroup.key, ScorecardGroup.name, ScorecardGroup.sisu_group_id)
            .join(ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id)
            .where(ScorecardMetric.tenant_id == tid, ScorecardGroup.business_id == bid,
                   ScorecardMetric.resolver_key == args.resolver_key,
                   ScorecardMetric.active.is_(True))
            .order_by(ScorecardGroup.sort_order))).all()
        if not targets:
            print(f"No active measurable with resolver_key={args.resolver_key!r} on this board.")
            return 2
        print(f"Matched {len(targets)} measurable(s).\n")

        weeks = _weeks(args.weeks, dt.date.today())
        to_write: list[tuple] = []     # (metric_id, name, office, new_value)
        blocked = False

        for mid, name, current, gkey, gname, sgid in targets:
            group = {"key": gkey, "sisu_group_id": sgid}

            if args.rollback:
                if current is None:
                    print(f"  =    {gname:<16} {name[:32]:<32} already on resolver_key (no source_spec) - skip")
                    continue
                print(f"  undo {gname:<16} {name[:32]:<32} will CLEAR source_spec -> resolver_key")
                to_write.append((mid, name, gname, None))
                continue

            # FLIP: parity-gate this metric over the window
            fn = R.RESOLVERS.get(args.resolver_key)
            bad, cells = 0, []
            for ws, we in weeks:
                hard = await fn(s, tid, bid, ws, we, group=group)
                got = await run_spec(spec, s, tid, bid, ws, we, group=group)
                if not _same(hard, got):
                    bad += 1
                cells.append(_show(hard))
            if bad:
                blocked = True
                print(f"  FAIL {gname:<16} {name[:32]:<32} PARITY FAILED ({bad}/{len(weeks)} wks) - will NOT flip")
                continue
            if current == spec:
                print(f"  =    {gname:<16} {name[:32]:<32} already flipped to this spec - skip")
                continue
            print(f"  flip {gname:<16} {name[:32]:<32} parity OK (last{args.weeks}w: {' '.join(cells)}) -> set source_spec")
            to_write.append((mid, name, gname, copy.deepcopy(spec)))

        if blocked:
            print("\nAt least one metric failed parity. Writing NOTHING. Investigate with "
                  "`python -m scripts.scorecard_parity` before flipping.")
            return 1
        if not to_write:
            print("\nNothing to change.")
            return 0
        if not args.apply:
            print(f"\nDRY-RUN: {len(to_write)} row(s) would change. Re-run with --apply to write.")
            return 0

        for mid, name, office, new_value in to_write:
            await s.execute(update(ScorecardMetric)
                            .where(ScorecardMetric.id == mid)
                            .values(source_spec=new_value))     # fresh value, not a mutated load
        await s.commit()

        # read back
        ids = [w[0] for w in to_write]
        back = {m: ss for (m, ss) in (await s.execute(
            select(ScorecardMetric.id, ScorecardMetric.source_spec)
            .where(ScorecardMetric.id.in_(ids)))).all()}
        ok = all((back.get(mid) is None) == (nv is None) and (nv is None or back.get(mid) == nv)
                 for mid, _, _, nv in to_write)
        print(f"\nWROTE {len(to_write)} row(s); read-back {'confirms' if ok else 'DOES NOT match — check!'}.")
        print("The board is unchanged now; the worker's next resolve (or `scripts.scorecard_backfill`) "
              "re-derives the identical number via the engine." if not args.rollback else
              "Rolled back to the resolver_key path.")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
