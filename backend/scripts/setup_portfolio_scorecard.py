"""Stand up the Portfolio scorecard — Connor + Spring's Monday board, from the Weekly Priorities Agenda.

A WORKSPACE board (its groups belong to no business), shown on the Portfolio tab to owners and admins
only. One section per company, a few numbers each against its win (agenda draft, Nov 9 2026):

  Utah Life     Units closed + pending (month)   snapshot, auto (Sisu)   floor 60, target 75
                Projected GCI (month)            snapshot, auto (Sisu)   track-only (win: pacing to 60+)
                Projected profit, BEFORE overrides / 5% TL pay / PLACE split
                                                 snapshot, auto (Sisu)   positive
                Agent count                      snapshot, auto (Sisu)   track-only until the count is defined
  Spring B      Forum members                    snapshot, auto (GHL)    75 (by Dec 31; 90 stretch)
                Forum VIP guests registered      flow, auto (GHL)        track-only, vs the commitments below
                VIP guests committed: <leader>   flow, typed Monday      one row per leader
                <company> profit, month to date  snapshot, auto (QBO)    positive, one row per membership company
  Sympli        (numbers to define)
  Activated Agent (numbers to define)
  AI            Foundations series attendance    flow, typed             goal after offer + pricing approval
                December launch enrollment       snapshot, typed         goal after offer + pricing approval

Month figures answer only for the current week (they describe today's pipeline and books), so their
history builds week by week from here; agent count and VIP guests backfill. Decided with Connor 2026-11-09.

Dry-run by default; --apply writes. Re-runnable: a section or row that already exists is left alone.

  railway ssh --service executive_dashboard_v1 "python -m scripts.setup_portfolio_scorecard"
  railway ssh --service executive_dashboard_v1 "python -m scripts.setup_portfolio_scorecard --apply"
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from decimal import Decimal

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Business, ScorecardGroup, ScorecardMetric, ScorecardValue, Tenant
from app.services import roles
from app.services.scorecard_resolvers import RESOLVERS, _recent_weeks, _write

BACKFILL_WEEKS = 5
VIP_LEADERS = ("Justin", "Matt", "Brandon")
LOCK_NOTE = "Committed Monday and locked; a revised number goes next to it, never in place of it."


def board(membership: list[Business]) -> list[tuple[str, str, list[dict]]]:
    def row(name, typ, goal, key=None, source="manual", note=None, direction="gte"):
        return dict(name=name, type=typ, goal=goal, resolver_key=key, source=source, note=note,
                    direction=direction)
    spring_b = [
        row("Forum Members", "snapshot", 75, "portfolio_forum_members", "ghl",
            "Win: 75 by Dec 31 (90 stretch). Active Forum members."),
        row("Forum VIP Guests Registered", "flow", 0, "portfolio_forum_vip", "ghl",
            "VIP guests registered this week, against the leaders' committed numbers below."),
        *[row(f"VIP Guests Committed: {who}", "flow", 0, note=LOCK_NOTE) for who in VIP_LEADERS],
        *[row(f"{b.name} Profit (Month to Date)", "snapshot", 1, f"portfolio_entity_profit:{b.key}", "qbo",
              "Win: positive. QuickBooks net operating income, month to date.") for b in membership],
    ]
    return [
        ("utah_life", "Utah Life", [
            row("Units Closed + Pending (Month)", "snapshot", 60, "portfolio_ulrg_units", "sisu",
                "Floor 60, target 75. Closed plus pending set to close this month."),
            row("Projected GCI (Month)", "snapshot", 0, "portfolio_ulrg_gci", "sisu",
                "Win: pacing to 60+ units. Closed plus pending GCI this month."),
            row("Projected Profit (before overrides, TL pay, PLACE split)", "snapshot", 1,
                "portfolio_ulrg_profit", "sisu",
                "Win: positive. Shown net of overrides, 5% team leader pay and PLACE split once defined."),
            row("Agent Count", "snapshot", 0, "portfolio_ulrg_agents", "sisu",
                "Agents on the Sisu team at week end. Goal set once the count is defined."),
        ]),
        ("spring_b", "Spring B", spring_b),
        ("sympli", "Sympli", []),                       # numbers to define (Spring's markup)
        ("activated", "Activated Agent", []),           # numbers to define
        ("ai", "AI", [
            row("Foundations Series Attendance", "flow", 0, source="manual",
                note="Zoom. Goal set after offer and pricing approval."),
            row("December Launch Enrollment", "snapshot", 0, source="manual",
                note="GHL. Goal set after offer and pricing approval."),
        ]),
    ]


def _fmt(v):
    if v is None:
        return "-"
    f = float(v)
    return f"{f:,.0f}"


async def main() -> int:
    ap = argparse.ArgumentParser(description="Stand up the Portfolio scorecard.")
    ap.add_argument("--tenant", default="springb")
    ap.add_argument("--apply", action="store_true", help="write (otherwise dry-run)")
    args = ap.parse_args()
    print(f"{'APPLY' if args.apply else 'DRY-RUN'}   tenant={args.tenant!r}\n")

    async with SessionLocal() as s:
        tid = (await s.execute(select(Tenant.id).where(Tenant.slug == args.tenant))).scalar_one_or_none()
        if tid is None:
            print("No such tenant."); return 2
        membership = (await s.execute(select(Business).where(
            Business.tenant_id == tid, Business.kind == roles.MEMBERSHIP).order_by(Business.sort_order))).scalars().all()
        groups = {g.key: g for g in (await s.execute(select(ScorecardGroup).where(
            ScorecardGroup.tenant_id == tid, ScorecardGroup.business_id.is_(None)))).scalars().all()}

        new_groups, new_rows = [], []
        for order, (gkey, gname, rows) in enumerate(board(membership)):
            g = groups.get(gkey)
            if g is None:
                print(f"  ADD SECTION  {gname}" + ("   (numbers to define)" if not rows else ""))
                new_groups.append((gkey, gname, order))
                have = {}
            else:
                print(f"  SECTION      {gname} (exists)")
                have = {m.name for m in (await s.execute(select(ScorecardMetric).where(
                    ScorecardMetric.group_id == g.id))).scalars().all()}
            for i, r in enumerate(rows, start=1):
                if r["name"] in have:
                    print(f"     DONE      {r['name']}")
                    continue
                how = f"auto: {r['resolver_key']}" if r["resolver_key"] else "typed in"
                print(f"     ADD       {r['name']:<58} {r['type']:<8} goal {r['goal']:<4} {how}")
                new_rows.append((gkey, i, r))

        if not args.apply:
            print(f"\nDRY-RUN: {len(new_groups)} section(s), {len(new_rows)} row(s) to add.")
            return 0

        for gkey, gname, order in new_groups:
            g = ScorecardGroup(tenant_id=tid, business_id=None, key=gkey, name=gname, sort_order=order,
                               is_team_room=False, active=True)
            s.add(g)
            groups[gkey] = g
        await s.flush()
        for gkey, order, r in new_rows:
            s.add(ScorecardMetric(tenant_id=tid, group_id=groups[gkey].id, name=r["name"], note=r["note"],
                                  goal=Decimal(str(r["goal"])), direction=r["direction"], type=r["type"],
                                  source=r["source"], resolver_key=r["resolver_key"], sort_order=order,
                                  active=True))
        await s.commit()
        print(f"\nAdded {len(new_groups)} section(s) and {len(new_rows)} row(s).")

        work = (await s.execute(
            select(ScorecardMetric.id, ScorecardMetric.resolver_key, ScorecardMetric.name, ScorecardGroup.key)
            .join(ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id)
            .where(ScorecardMetric.tenant_id == tid, ScorecardGroup.business_id.is_(None),
                   ScorecardMetric.active.is_(True), ScorecardMetric.resolver_key.is_not(None))
            .order_by(ScorecardGroup.sort_order, ScorecardMetric.sort_order))).all()

    # Fill what can be filled: the current week for the month figures, the last 5 weeks for the dated ones.
    weeks = _recent_weeks(dt.date.today(), BACKFILL_WEEKS)
    for mid, key, _name, gkey in work:
        fn = RESOLVERS.get(key)
        if fn is None:
            print(f"  ! no resolver registered for {key}"); continue
        for ws, we in weeks:
            async with SessionLocal() as s:
                val = await fn(s, tid, None, ws, we, group={"key": gkey, "sisu_group_id": None})
                await _write(s, tid, mid, ws, val, key)
                await s.commit()

    print("\n" + " " * 62 + "  ".join(f"{ws:%m/%d}".rjust(9) for ws, _ in reversed(weeks)))
    async with SessionLocal() as s:
        for mid, key, name, gkey in work:
            vals = {v.week_start: v.value for v in (await s.execute(select(ScorecardValue).where(
                ScorecardValue.metric_id == mid))).scalars().all()}
            print(f"  {name[:60]:<60}" + "  ".join(f"{_fmt(vals.get(ws)):>9}" for ws, _ in reversed(weeks)))
    print("\nDone. Portfolio tab -> Scorecard (owners and admins). The 30-minute tick keeps the auto rows current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
