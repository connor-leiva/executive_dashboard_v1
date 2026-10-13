"""Seed the Spring B scorecard's Q4 2026 goals — the agreed set — plus the fixes they depend on.

Agreed 2026-10-13, worked back from 8 new Forum members in Q4 (the 2026 average; Q3's event cycle ran
12 VIP-guest applications -> 10 guests -> 4 joined, i.e. ~83% show and ~40% close):

  Forum         Members Added                      track-only weekly, 8 for the quarter
                VIP Guests Added                   2 / week, 24
                Appointments Set                   2 / week, 24
                Appointments Met                   1.5 / week, 20
                Member Referrals Generated         1 / week, 13
                Members at Risk                    at most 3 (weekly snapshot)
  Spring B      Email Open Rate Floor              at least 35%
                Content Assets Shipped vs. Planned at least 80%   (becomes a percentage line)
  beCollective  Mighty Network Growth              3 / week, 40
  Every other line stays track-only (goal 0) until it has a baseline or a definition.

It also:
  * creates the 2026Q4 period, Oct 31 - Jan 31 (13 weeks; the first scored week is Mon Nov 2). It starts
    the day after Q3 ends because the board resolves the CURRENT period by today's date, and a gap would
    fall back to the calendar quarter for those days. Periods are TENANT-WIDE: the ULRG board gets the
    same Q4 (its own Q4 goals are set separately, in its Settings).
  * makes Members at Risk and Agents at 0 "lower is better". They were ">=", which scores them green
    when HIGH, and Settings can't change a line's direction once it exists.
  * makes Content Assets Shipped vs. Planned a percentage (rate) line — the week's shipped / planned.

Dry-run by default; --apply writes. Idempotent: run it again and it changes nothing. A period or goal
that already holds DIFFERENT values is reported and left alone unless --force.

  railway ssh --service executive_dashboard_v1 "python -m scripts.seed_springb_goals"
  railway ssh --service executive_dashboard_v1 "python -m scripts.seed_springb_goals --apply"
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal

from sqlalchemy import select, update

from app.config import settings
from app.db import SessionLocal
from app.models import Business, ScorecardGoal, ScorecardGroup, ScorecardMetric, Tenant

PERIOD = {"key": "2026Q4", "start": "2026-10-31", "end": "2027-01-31"}

# (group key, line name) -> changes to the line itself
LINE_FIXES = {
    ("forum", "Members at Risk"): {"direction": "lte"},
    ("activated", "Agents at 0"): {"direction": "lte"},
    ("spring_b", "Content Assets Shipped vs. Planned"): {
        "type": "rate",
        "note": "Share of the week's planned content assets that shipped (shipped / planned)."},
}

# (group key, line name) -> (weekly goal, quarter total or None). Quarter totals are flow lines only.
GOALS = {
    ("forum", "Members Added"): (0, 8),
    ("forum", "VIP Guests Added"): (2, 24),
    ("forum", "Appointments Set"): (2, 24),
    ("forum", "Appointments Met"): (1.5, 20),
    ("forum", "Member Referrals Generated"): (1, 13),
    ("forum", "Members at Risk"): (3, None),
    ("spring_b", "Email Open Rate Floor"): (35, None),
    ("spring_b", "Content Assets Shipped vs. Planned"): (80, None),
    ("becollective", "Mighty Network Growth"): (3, 40),
}


def _db_target() -> str:
    url = settings.DATABASE_URL
    if url.startswith("sqlite"):
        return f"LOCAL SQLite ({url.rsplit('/', 1)[-1]})  <-- NOT production"
    try:
        creds, hostpart = url.split("://", 1)[1].split("@", 1)
        return f"Postgres {creds.split(':', 1)[0]}@{hostpart}"
    except (IndexError, ValueError):
        return "Postgres (could not parse the URL)"


def _num(v):
    if v is None:
        return None
    f = float(v)
    return int(f) if f.is_integer() else f


def _overlaps(a: dict, b: dict) -> bool:
    return not (a["end"] < b["start"] or b["end"] < a["start"])


async def main() -> int:
    ap = argparse.ArgumentParser(description="Seed the Spring B scorecard's Q4 2026 goals.")
    ap.add_argument("--tenant", default="springb", help="tenant slug (default: springb)")
    ap.add_argument("--business", default="springb", help="the board's business key (default: springb)")
    ap.add_argument("--apply", action="store_true", help="write (otherwise dry-run)")
    ap.add_argument("--force", action="store_true", help="overwrite a period/goal that holds different values")
    args = ap.parse_args()

    print(f"DB target: {_db_target()}")
    print(f"{'APPLY' if args.apply else 'DRY-RUN'}   tenant={args.tenant!r}  business={args.business!r}\n")

    async with SessionLocal() as s:
        row = (await s.execute(select(Business.id, Business.tenant_id).join(
            Tenant, Business.tenant_id == Tenant.id).where(
            Tenant.slug == args.tenant, Business.key == args.business))).first()
        if not row:
            print(f"No business {args.business!r} for tenant {args.tenant!r}.")
            return 2
        bid, tid = row
        tenant = await s.get(Tenant, tid)
        writes = 0

        # 1) the period
        fq = list((tenant.config or {}).get("fiscal_quarters") or [])
        have = next((p for p in fq if p.get("key") == PERIOD["key"]), None)
        clash = [p["key"] for p in fq if p.get("key") != PERIOD["key"] and _overlaps(p, PERIOD)]
        new_fq = None
        if clash:
            print(f"  STOP      period {PERIOD['key']} would overlap {clash} — fix the periods in Settings first")
            return 2
        if have is None:
            print(f"  CREATE    period {PERIOD['key']}: {PERIOD['start']} -> {PERIOD['end']} (tenant-wide: ULRG too)")
            new_fq = fq + [dict(PERIOD)]
        elif (have.get("start"), have.get("end")) == (PERIOD["start"], PERIOD["end"]):
            print(f"  DONE      period {PERIOD['key']}: {PERIOD['start']} -> {PERIOD['end']}")
        elif args.force:
            print(f"  REPLACE   period {PERIOD['key']}: {have.get('start')} -> {have.get('end')}  =>  "
                  f"{PERIOD['start']} -> {PERIOD['end']}")
            new_fq = [dict(PERIOD) if p.get("key") == PERIOD["key"] else p for p in fq]
        else:
            print(f"  KEEP      period {PERIOD['key']} already exists as {have.get('start')} -> {have.get('end')}"
                  f" (goals still attach to it; --force to use {PERIOD['start']} -> {PERIOD['end']})")

        async def line(gkey, name):
            return (await s.execute(select(ScorecardMetric).join(
                ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id).where(
                ScorecardMetric.tenant_id == tid, ScorecardGroup.business_id == bid,
                ScorecardGroup.key == gkey, ScorecardMetric.name == name,
                ScorecardMetric.active.is_(True)))).scalar_one_or_none()

        # 2) the line fixes
        line_updates = []          # (metric id, {field: value})
        for (gkey, name), fix in LINE_FIXES.items():
            m = await line(gkey, name)
            if m is None:
                print(f"  MISSING   {gkey} / {name} — not on the board; skipped")
                continue
            todo = {k: v for k, v in fix.items() if getattr(m, k) != v}
            shown = ", ".join(f"{k} {getattr(m, k)!r} -> {v!r}" for k, v in todo.items() if k != "note")
            if todo:
                print(f"  FIX       {gkey} / {name}: {shown or 'note'}")
                line_updates.append((m.id, todo))
            else:
                print(f"  DONE      {gkey} / {name}: {', '.join(f'{k}={v!r}' for k, v in fix.items() if k != 'note')}")

        # 3) the Q4 goals
        goal_writes = []           # (metric id, existing ScorecardGoal or None, weekly, total)
        for (gkey, name), (weekly, total) in GOALS.items():
            m = await line(gkey, name)
            if m is None:
                print(f"  MISSING   {gkey} / {name} — not on the board; skipped")
                continue
            g = (await s.execute(select(ScorecardGoal).where(
                ScorecardGoal.metric_id == m.id, ScorecardGoal.period_key == PERIOD["key"]))).scalar_one_or_none()
            want = f"weekly {weekly}" + (f", quarter {total}" if total is not None else "")
            if g is not None and (_num(g.goal), _num(g.cumulative_goal)) == (_num(weekly), _num(total)):
                print(f"  DONE      {gkey} / {name}: {want}")
                continue
            if g is not None and not args.force:
                print(f"  KEEP      {gkey} / {name}: already weekly {_num(g.goal)}, quarter {_num(g.cumulative_goal)}"
                      f" (--force to set {want})")
                continue
            was = "" if g is None else f" (was weekly {_num(g.goal)}, quarter {_num(g.cumulative_goal)})"
            print(f"  SET       {gkey} / {name}: {want}{was}")
            goal_writes.append((m.id, g, weekly, total))

        changes = (1 if new_fq is not None else 0) + len(line_updates) + len(goal_writes)
        if not args.apply:
            print(f"\nDRY-RUN: {changes} change(s). Re-run with --apply.")
            return 0
        if not changes:
            print("\nNothing to change.")
            return 0

        if new_fq is not None:
            cfg = dict(tenant.config or {})      # a COPY: mutating the loaded dict skips the UPDATE
            cfg["fiscal_quarters"] = new_fq
            await s.execute(update(Tenant).where(Tenant.id == tid).values(config=cfg))
        for mid, todo in line_updates:
            await s.execute(update(ScorecardMetric).where(ScorecardMetric.id == mid).values(**todo))
        for mid, g, weekly, total in goal_writes:
            cum = Decimal(str(total)) if total is not None else None
            if g is None:
                s.add(ScorecardGoal(tenant_id=tid, metric_id=mid, period_key=PERIOD["key"],
                                    goal=Decimal(str(weekly)), cumulative_goal=cum))
            else:
                g.goal, g.cumulative_goal = Decimal(str(weekly)), cum
        await s.commit()
        writes = changes
    print(f"\nAPPLIED {writes} change(s). Q4 goals show on the board from Mon Nov 2; Settings -> Goals per "
          f"period -> {PERIOD['key']} shows them now.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
