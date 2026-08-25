"""Rebuild scorecard weeks that the resolvers should have filled but did not.

The daily tick (`worker.scorecard_tick`) only re-resolves LOOKBACK_WEEKS, so anything damaged
or missed further back stays that way forever — the window slides past it. That is what needs
undoing after a feed outage: while Sisu was disconnected every Sisu-backed resolver reported a
gap, `_write` cleared its own stored numbers, and the hole grew by a week per week.

The figures come from Sisu deals ALREADY IN THIS DATABASE (`homes_closed` and friends count
local `transaction` rows), so recovery does not depend on re-fetching anything from the vendor.
It does depend on the feed being live again: `_sisu_live` gates every resolver, so run this
AFTER the integration is reconnected and has synced once, or every week will simply report
UNAVAILABLE and nothing will change.

Reports what it would write and changes nothing until you pass --apply:

  python -m scripts.scorecard_backfill --tenant springb --weeks 16
  python -m scripts.scorecard_backfill --tenant springb --weeks 16 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import (Integration, ScorecardGroup, ScorecardMetric, ScorecardValue, Tenant)
from app.services import scorecard_resolvers as R


def _db_target() -> str:
    url = settings.DATABASE_URL
    if url.startswith("sqlite"):
        return f"LOCAL SQLite ({url.rsplit('/', 1)[-1]})  <-- NOT production"
    try:
        creds, hostpart = url.split("://", 1)[1].split("@", 1)
        return f"Postgres {creds.split(':', 1)[0]}@{hostpart}"
    except (IndexError, ValueError):
        return "Postgres (could not parse the URL)"


async def _preview(s, tenant_id, weeks: int, today: dt.date):
    """What each (metric, week) would become, without writing. Mirrors run_resolvers' work list."""
    metrics = (await s.execute(
        select(ScorecardMetric.id, ScorecardMetric.name, ScorecardMetric.resolver_key,
               ScorecardGroup.business_id, ScorecardGroup.key, ScorecardGroup.sisu_group_id)
        .join(ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id)
        .where(ScorecardMetric.tenant_id == tenant_id,
               ScorecardMetric.active.is_(True),
               ScorecardMetric.resolver_key.is_not(None)))).all()

    rows = []
    for mid, name, key, business_id, group_key, sgid in metrics:
        fn = R.RESOLVERS.get(key)
        if fn is None:
            rows.append((name, group_key, None, "!", f"resolver {key!r} is not registered"))
            continue
        group = {"key": group_key, "sisu_group_id": sgid}
        for ws, we in R._recent_weeks(today, weeks):
            cur = (await s.execute(select(ScorecardValue).where(
                ScorecardValue.metric_id == mid,
                ScorecardValue.week_start == ws))).scalar_one_or_none()
            try:
                val = await fn(s, tenant_id, business_id, ws, we, group=group)
            except Exception as e:                       # noqa: BLE001 — mirror the runner
                rows.append((name, group_key, ws, "!", f"{type(e).__name__}: {e}"))
                continue
            have = None if cur is None or cur.value is None else float(cur.value)
            if val is R.UNAVAILABLE:
                rows.append((name, group_key, ws, "skip", "source unavailable"))
            elif val is None:
                rows.append((name, group_key, ws, "clear" if have is not None else "skip",
                             "no value for this week"))
            elif cur is not None and cur.source == "manual":
                rows.append((name, group_key, ws, "OVERRIDE",
                             f"manual {have} -> resolver {val}"))
            elif have is None:
                rows.append((name, group_key, ws, "FILL", f"(blank) -> {val}"))
            elif abs(have - float(val)) > 1e-9:
                rows.append((name, group_key, ws, "change", f"{have} -> {val}"))
            else:
                rows.append((name, group_key, ws, "same", f"{have}"))
    return rows


async def _run(args) -> None:
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(
            Tenant.slug == args.tenant))).scalar_one_or_none()
        if t is None:
            raise SystemExit(f"[error] no tenant '{args.tenant}'")

        today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
        print(f"\n  database   {_db_target()}")
        print(f"  tenant     {t.slug} / {t.name}")
        print(f"  window     {args.weeks} weeks back from {today.isoformat()}\n")

        # The feed gate decides everything, so say what it is before listing anything.
        integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == t.id, Integration.provider == "sisu"))).scalars().first()
        if integ is None:
            print("  Sisu       NO INTEGRATION ROW - every resolver will report unavailable.\n")
        else:
            synced = integ.last_synced_at.isoformat() if integ.last_synced_at else "NEVER"
            print(f"  Sisu       status={integ.status}  last_synced={synced}")
            if integ.status != "connected" or integ.last_synced_at is None:
                print("             ^ not a live feed, so nothing can be rebuilt. Reconnect Sisu")
                print("               in Settings -> Integrations and let it sync once first.\n")
            else:
                print()

        rows = await _preview(s, t.id, args.weeks, today)

    counts: dict[str, int] = {}
    for _n, _g, _w, action, _d in rows:
        counts[action] = counts.get(action, 0) + 1
    interesting = [r for r in rows if r[3] in ("FILL", "change", "clear", "OVERRIDE", "!")]
    for name, gkey, ws, action, detail in interesting:
        wk = ws.isoformat() if ws else "-"
        print(f"    {action:<9} {wk}  {gkey:<10} {name[:38]:<38} {detail}")
    if not interesting:
        print("    (nothing to change)")
    print("\n  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())) + "\n")

    if not args.apply:
        print("  Dry run. Re-run with --apply to write.\n")
        return
    written = await R.run_resolvers(SessionLocal, t.id, today, weeks=args.weeks)
    print(f"  [ok] resolver pass complete over {args.weeks} weeks ({written} results committed).\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tenant", required=True, help="tenant slug, e.g. springb")
    ap.add_argument("--weeks", type=int, default=12, help="how many weeks back (default 12)")
    ap.add_argument("--today", help="anchor date YYYY-MM-DD (default: today)")
    ap.add_argument("--apply", action="store_true", help="actually write; otherwise dry run")
    asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    main()
