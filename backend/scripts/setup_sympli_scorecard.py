"""Stand up the Sympli lending board on a live workspace, and backfill it.

--apply, in order:
  1. Creates the 2026Q4 period if it's missing (tenant-wide), because the quarter goals attach to
     it. If it exists, its dates are kept and the quarter totals are worked out from them.
  2. Seeds the board's groups, rows, goals and gates. Additive: nothing that exists is changed.
  3. Syncs Arive, so every loan in the market carries the status history the rows read.
  4. Backfills ONLY this board's auto rows for the last 13 weeks. No other board's row is re-run.

Safe to re-run. Do it again after the next Sisu sync: the lead-source attach rows stay blank until
Sisu has re-synced lead_type, and the normal tick only reaches back 3 weeks.

Add a new loan officer (a group with the standard rows) with --add-lo "Name,email,XX" and --apply.

  railway ssh --service executive_dashboard_v1 "python -m scripts.setup_sympli_scorecard"
  railway ssh --service executive_dashboard_v1 "python -m scripts.setup_sympli_scorecard --apply"
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import re
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from app.config import settings
from app.db import SessionLocal
from app.models import Integration, ScorecardGroup, ScorecardMetric, ScorecardValue, Tenant
from app.seed_sympli_scorecard import board_groups, load_data, load_sympli_scorecard
from app.services import roles
from app.services.scorecard_resolvers import RESOLVERS, UNAVAILABLE, _recent_weeks, _write
from app.services.sync import sync_arive

BACKFILL_WEEKS = 13


def _overlaps(a: dict, b: dict) -> bool:
    return not (a["end"] < b["start"] or b["end"] < a["start"])


def _fmt(v) -> str:
    if v is None:
        return "-"
    f = float(v)
    return str(int(f)) if f.is_integer() else f"{f:g}"


async def _add_lo(s, tid, bid, spec: str, data: dict) -> int:
    name, email, initials = [x.strip() for x in spec.split(",")]
    siblings = (await s.execute(select(ScorecardGroup).where(
        ScorecardGroup.tenant_id == tid, ScorecardGroup.business_id == bid))).scalars().all()
    key = "lo_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30]
    if any(g.key == key for g in siblings):
        print(f"  DONE      loan officer {name}")
        return 0
    costs = next((g for g in siblings if g.key == "costs"), None)   # LOs sit above Cost Controls
    order = costs.sort_order if costs else max((g.sort_order for g in siblings), default=-1) + 1
    if costs:
        costs.sort_order = order + 1
    g = ScorecardGroup(tenant_id=tid, business_id=bid, key=key, name=name, owner_name=name, is_team_room=False,
                       sort_order=order, lo_email=email.lower())
    s.add(g)
    await s.flush()
    for i, m in enumerate(data["loan_officer_rows"]):
        s.add(ScorecardMetric(tenant_id=tid, group_id=g.id, name=m["name"], note=m.get("note"),
                              goal=Decimal(str(m.get("goal", 0))), direction=m.get("direction", "gte"),
                              type=m["type"], source=m.get("source", "manual"), resolver_key=m.get("resolver_key"),
                              owner_initials=initials[:4], sort_order=i, active=True))
    print(f"  ADD       loan officer {name} <{email}>: {len(data['loan_officer_rows'])} rows")
    return 1


async def main() -> int:
    ap = argparse.ArgumentParser(description="Stand up and backfill the Sympli lending board.")
    ap.add_argument("--tenant", default="springb")
    ap.add_argument("--apply", action="store_true", help="write (otherwise dry-run)")
    ap.add_argument("--no-sync", action="store_true", help="skip the Arive sync (timelines already landed)")
    ap.add_argument("--add-lo", help='add a loan officer: "Name,email,XX"')
    args = ap.parse_args()
    print(f"{'APPLY' if args.apply else 'DRY-RUN'}   tenant={args.tenant!r}\n")
    data = load_data()
    period = data["period"]

    async with SessionLocal() as s:
        tenant = (await s.execute(select(Tenant).where(Tenant.slug == args.tenant))).scalar_one_or_none()
        if tenant is None:
            print("No such tenant.")
            return 2
        tid = tenant.id
        biz = await roles.commission_jv(s, tid)
        if biz is None:
            print("No lending business on this workspace.")
            return 2
        bid = biz.id

        fq = list((tenant.config or {}).get("fiscal_quarters") or [])
        have = next((p for p in fq if p.get("key") == period["key"]), None)
        clash = [p["key"] for p in fq if p.get("key") != period["key"] and _overlaps(p, period)]
        if clash and have is None:
            print(f"STOP: period {period['key']} would overlap {clash}. Fix the periods in Settings first.")
            return 2
        if have:      # the workspace's own dates win; quarter totals are worked out from them
            print(f"  DONE      period {period['key']}: {have.get('start')} -> {have.get('end')} (kept as set)")
        else:
            print(f"  CREATE    period {period['key']}: {period['start']} -> {period['end']} (tenant-wide)")

        existing = {g.key: g.id for g in (await s.execute(select(ScorecardGroup).where(
            ScorecardGroup.tenant_id == tid, ScorecardGroup.business_id == bid))).scalars().all()}
        new_rows = 0
        for g in board_groups(data):
            names = set()
            if g["key"] in existing:
                names = set((await s.execute(select(ScorecardMetric.name).where(
                    ScorecardMetric.group_id == existing[g["key"]]))).scalars().all())
            missing = [m for m in g["metrics"] if m["name"] not in names]
            new_rows += len(missing)
            print(f"  {'ADD  ' if missing else 'DONE '}     {g['name']:<24} {len(missing)} new of {len(g['metrics'])} rows")
        integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == tid, Integration.provider == "arive"))).scalars().first()
        if integ is None or integ.status != "connected":
            print("\nSTOP: Arive isn't connected on this workspace.")
            return 2

        if not args.apply:
            print(f"\nDRY-RUN: {new_rows} row(s) to add. --apply also syncs Arive and backfills {BACKFILL_WEEKS} weeks.")
            return 0

        if have is None:
            cfg = dict(tenant.config or {})      # a COPY: mutating the loaded dict skips the UPDATE
            cfg["fiscal_quarters"] = fq + [dict(period)]
            await s.execute(update(Tenant).where(Tenant.id == tid).values(config=cfg))
            await s.commit()
        print(f"\nSeeded: {await load_sympli_scorecard(s, tid)}")
        if args.add_lo:
            await _add_lo(s, tid, bid, args.add_lo, data)
            await s.commit()

    if not args.no_sync:
        async with SessionLocal() as s:
            integ = (await s.execute(select(Integration).where(
                Integration.tenant_id == tid, Integration.provider == "arive"))).scalars().first()
            print(f"Arive synced: {await sync_arive(s, tid, integ)} loans")

    async with SessionLocal() as s:
        work = (await s.execute(
            select(ScorecardMetric.id, ScorecardMetric.resolver_key, ScorecardGroup.key, ScorecardGroup.sisu_group_id,
                   ScorecardGroup.lo_email, ScorecardMetric.name, ScorecardGroup.sort_order, ScorecardMetric.sort_order)
            .join(ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id)
            .where(ScorecardMetric.tenant_id == tid, ScorecardGroup.business_id == bid,
                   ScorecardMetric.active.is_(True), ScorecardMetric.resolver_key.is_not(None))
            .order_by(ScorecardGroup.sort_order, ScorecardMetric.sort_order))).all()
    today = dt.datetime.now(ZoneInfo(settings.BILLING_TIMEZONE)).date()
    weeks = _recent_weeks(today, BACKFILL_WEEKS)
    unavailable = set()
    for mid, key, gkey, sgid, lo, name, _go, _mo in work:
        for ws, we in weeks:
            async with SessionLocal() as s:
                val = await RESOLVERS[key](s, tid, bid, ws, we,
                                           group={"key": gkey, "sisu_group_id": sgid, "lo_email": lo})
                await _write(s, tid, mid, ws, val, key)
                await s.commit()
                if val is UNAVAILABLE:
                    unavailable.add(f"{gkey} / {name}")

    shown = list(reversed(weeks))[-6:]
    print("\n" + " " * 44 + "  ".join(f"{ws:%m/%d}" for ws, _ in shown))
    async with SessionLocal() as s:
        for mid, _key, gkey, _sgid, _lo, name, _go, _mo in work:
            vals = {v.week_start: v.value for v in (await s.execute(select(ScorecardValue).where(
                ScorecardValue.metric_id == mid))).scalars().all()}
            print(f"  {gkey[:10]:<11}{name[:30]:<31}" + "  ".join(f"{_fmt(vals.get(ws)):>5}" for ws, _ in shown))
    if unavailable:
        print(f"\nCould not look yet (left blank), {len(unavailable)} row(s): " + "; ".join(sorted(unavailable)[:8]))
    print("\nDone. The 30-minute scorecard tick keeps the last 3 weeks of these rows current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
