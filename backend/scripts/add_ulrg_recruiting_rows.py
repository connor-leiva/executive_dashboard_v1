"""Add the four recruiting-result rows to the ULRG L10 board, per office and company-wide, and backfill.

  Hello Week Entries   flow, hand-entered   4 / week per leader (Overall 12)  -- no GHL source yet
  Agents Onboarded     flow, auto (Sisu)    track-only                        -- Sisu start date in the week
  Agents Offboarded    flow, auto (Sisu)    track-only, lower is better       -- left the Sisu team in the week
  Net Agent Count      snapshot, auto       35 per office (Overall track-only) -- on the team at week end

Office rows attribute by the agent's Sisu office group (the same mapping the Homes Sold rows use);
Overall is the whole Sisu team, out-of-office agents included. Decided with Connor 2026-10-20.

--apply also refreshes the Sisu roster first (the daily roster sync's job: team list, start dates,
departures), then backfills ONLY the new auto rows for the open week and the 4 before it. It never
re-runs any other row's resolver -- the Appointments Met / Clients Signed rows are left exactly as
they are. Dry-run by default; idempotent (a row that already exists is reported and left alone).

  railway ssh --service executive_dashboard_v1 "python -m scripts.add_ulrg_recruiting_rows"
  railway ssh --service executive_dashboard_v1 "python -m scripts.add_ulrg_recruiting_rows --apply"
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from decimal import Decimal

from sqlalchemy import select

from app.db import SessionLocal
from app.models import ScorecardGroup, ScorecardMetric, ScorecardValue, Tenant
from app.services import roles
from app.services.scorecard_resolvers import RESOLVERS, _recent_weeks, _write
from app.services.sync import sync_agent_offices

OFFICE_KEYS = ("davis", "slc", "utco")
BACKFILL_WEEKS = 5                      # the open week + the 4 completed weeks before it


def rows_for(group_key: str) -> list[dict]:
    office = group_key in OFFICE_KEYS
    return [
        dict(name="Hello Week Entries", type="flow", direction="gte", goal=4 if office else 12,
             resolver_key=None, source="manual",
             note="New prospects entering Hello Week this week (typed in; target 4 per leader)."),
        dict(name="Agents Onboarded", type="flow", direction="gte", goal=0,
             resolver_key="ulrg_roster_onboarded", source="sisu",
             note="Agents whose Sisu start date falls in the week."),
        dict(name="Agents Offboarded", type="flow", direction="lte", goal=0,
             resolver_key="ulrg_roster_offboarded", source="sisu",
             note="Agents who left the Sisu team in the week."),
        dict(name="Net Agent Count", type="snapshot", direction="gte", goal=35 if office else 0,
             resolver_key="ulrg_roster_net", source="sisu",
             note="Agents on the Sisu team at the end of the week."),
    ]


def _fmt(v):
    if v is None:
        return "-"
    f = float(v)
    return str(int(f)) if f.is_integer() else f"{f:g}"


async def main() -> int:
    ap = argparse.ArgumentParser(description="Add + backfill the ULRG recruiting-result rows.")
    ap.add_argument("--tenant", default="springb")
    ap.add_argument("--apply", action="store_true", help="write (otherwise dry-run)")
    args = ap.parse_args()
    print(f"{'APPLY' if args.apply else 'DRY-RUN'}   tenant={args.tenant!r}\n")

    async with SessionLocal() as s:
        tid = (await s.execute(select(Tenant.id).where(Tenant.slug == args.tenant))).scalar_one_or_none()
        if tid is None:
            print("No such tenant."); return 2
        biz = await roles.real_estate(s, tid)
        bid = biz.id if biz else None
        if biz is None:
            print("No brokerage business."); return 2
        groups = {g.key: g for g in (await s.execute(select(ScorecardGroup).where(
            ScorecardGroup.tenant_id == tid, ScorecardGroup.business_id == bid))).scalars().all()}
        missing = [k for k in OFFICE_KEYS + ("overall",) if k not in groups]
        if missing:
            print(f"STOP: board has no group(s) {missing}"); return 2

        to_add = []
        for gkey in OFFICE_KEYS + ("overall",):
            g = groups[gkey]
            have = {m.name: m for m in (await s.execute(select(ScorecardMetric).where(
                ScorecardMetric.group_id == g.id))).scalars().all()}
            top = max([m.sort_order or 0 for m in have.values()] or [0])
            for i, row in enumerate(rows_for(gkey), start=1):
                m = have.get(row["name"])
                if m is not None:
                    print(f"  DONE      {gkey:<8} {row['name']}" + ("" if m.active else "  (exists but REMOVED -- restore in Settings)"))
                    continue
                print(f"  ADD       {gkey:<8} {row['name']:<20} {row['type']:<8} {row['direction']} goal {row['goal']}"
                      f"  {'auto: ' + row['resolver_key'] if row['resolver_key'] else 'hand-entered'}")
                to_add.append((g, top + i, row))

        if not args.apply:
            print(f"\nDRY-RUN: {len(to_add)} row(s) to add. --apply also refreshes the Sisu roster and "
                  f"backfills the auto rows for the last {BACKFILL_WEEKS} weeks.")
            return 0

        for g, order, row in to_add:
            s.add(ScorecardMetric(tenant_id=tid, group_id=g.id, name=row["name"], note=row["note"],
                                  goal=Decimal(str(row["goal"])), direction=row["direction"],
                                  type=row["type"], source=row["source"],
                                  resolver_key=row["resolver_key"], sort_order=order, active=True))
        await s.commit()
        print(f"\nAdded {len(to_add)} row(s).")

    # The roster first: the auto rows read its start/leave dates.
    async with SessionLocal() as s:
        n = await sync_agent_offices(s, tid)
        print(f"Roster refreshed ({n} agents).")

    # Backfill ONLY the new auto rows -- never re-run any other row's resolver here.
    async with SessionLocal() as s:
        work = (await s.execute(
            select(ScorecardMetric.id, ScorecardMetric.resolver_key, ScorecardGroup.key,
                   ScorecardGroup.sisu_group_id, ScorecardMetric.name)
            .join(ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id)
            .where(ScorecardMetric.tenant_id == tid, ScorecardGroup.business_id == bid,
                   ScorecardMetric.active.is_(True),
                   ScorecardMetric.resolver_key.in_(("ulrg_roster_onboarded", "ulrg_roster_offboarded",
                                                     "ulrg_roster_net"))))).all()
    weeks = _recent_weeks(dt.date.today(), BACKFILL_WEEKS)
    for mid, key, gkey, sgid, _name in work:
        for ws, we in weeks:
            async with SessionLocal() as s:
                val = await RESOLVERS[key](s, tid, bid, ws, we, group={"key": gkey, "sisu_group_id": sgid})
                await _write(s, tid, mid, ws, val, key)
                await s.commit()

    # Show what landed, oldest week first.
    print("\n" + " " * 30 + "  ".join(f"{ws:%m/%d}" for ws, _ in reversed(weeks)))
    async with SessionLocal() as s:
        for mid, key, gkey, sgid, name in sorted(work, key=lambda w: (w[2], w[1])):
            vals = {v.week_start: v.value for v in (await s.execute(select(ScorecardValue).where(
                ScorecardValue.metric_id == mid))).scalars().all()}
            print(f"  {gkey:<8} {name:<19}" + "  ".join(f"{_fmt(vals.get(ws)):>5}" for ws, _ in reversed(weeks)))
    print("\nDone. The 30-minute scorecard tick keeps the auto rows current; the roster refreshes daily.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
