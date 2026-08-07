"""ULRG L10 Scorecard — Spring seed + backfill (SPEC-ulrg-scorecard Part 6, Steps 1 + 2).

============================ TENANT SEED — NOT PRODUCT DATA ============================
Acumyn ships an EMPTY ULRG-shaped scorecard for a new tenant (Part 0.5); Spring's groups,
measurables, and history are THIS tenant's data. So — like the AI demo fixture — Spring/Davis/SLC
live here, never in the product code (services/frontend), which is what the acceptance greps
enforce. Load it explicitly:

    python -m app.seed_ulrg_scorecard [tenant_slug]     # default: springb

Data mirrors the mockup (`frontend/mockups/ulrg-team-command-v6.jsx`), which the spec designates
as the reference for the sheet's structure. Values are representative — reconcile goals + the real
week history against the live Google Sheet export. Every metric seeds `source="manual"` (Step 1);
resolvers (Step 5) flip individual rows once validated. `null` weeks stay null (uncollected ≠ 0).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
from decimal import Decimal

from sqlalchemy import select, delete

from .db import SessionLocal
from .models import (Tenant, Business, ScorecardGroup, ScorecardMetric, ScorecardValue)

# Week Mondays (ascending, oldest first) — index-aligned to every `v` array below.
WEEK_STARTS = [dt.date(2026, 6, 8), dt.date(2026, 6, 15), dt.date(2026, 6, 22), dt.date(2026, 6, 29),
               dt.date(2026, 7, 6), dt.date(2026, 7, 13), dt.date(2026, 7, 20), dt.date(2026, 7, 27)]

# (key, name, owner_name|None, is_team_room, read, [metrics])
# metric = (name, goal, type, stage|None, lever|None, initials|None, note|None, values[])
_GROUPS = [
    ("davis", "Davis", "Justin", True,
     "Every stage is falling, including closings. The 102% on Homes Sold is the last of the May "
     "pipeline arriving. Last four weeks it is already down to 84.", [
         ("Appointments Met", 30, "flow", 1, "volume", "JT", None, [34, 20, 25, 19, 20, 18, 18, 33]),
         ("Clients Signed", 20, "flow", 2, "volume", "JT", None, [20, 18, 15, 13, 11, 11, 13, 20]),
         ("Under Contract", 10, "flow", 3, "volume", "JT", None, [9, 12, 13, 6, 6, 9, 4, 11]),
         ("Homes Sold", 8, "flow", 4, "volume", "JT", "130 Q2 pace", [11, 11, 6, 10, 7, 10, 7, 3]),
         ("Recruiting Appts Met", 10, "flow", None, "behavior", "JT", None, [10, 10, 11, 12, 8, 11, 0, 10]),
         ("Sympli Attach Rate", 40, "rate", None, "behavior", "JT", None, [43.0, 27.3, 0, 28.6, 0, 10, 0, 66.7]),
         ("Meraki Attach Rate", 60, "rate", None, "behavior", "JT", None, [54.6, 54.6, 28.6, 50, 55.6, 70, 62.5, 33.3]),
     ]),
    ("slc", "SLC", None, True,
     "Behind on the cumulative but climbing. Appointments are up 9 points over the last four weeks. "
     "This is a hole being filled, not one being dug.", [
         ("Appointments Met", 24, "flow", 1, "volume", None, None, [28, 12, 16, 21, 24, 19, 17, 26]),
         ("Signed Units", 15, "flow", 2, "volume", None, None, [24, 11, 13, 10, 15, 8, 16, 15]),
         ("Under Contract", 6, "flow", 3, "volume", None, None, [9, 8, 8, 7, 8, 6, 5, 7]),
         ("Homes Sold", 6, "flow", 4, "volume", None, "100 Q2 pace", [8, 4, 12, 7, 7, 3, 7, 8]),
         ("Recruiting Appts Met", 5, "flow", None, "behavior", None, None, [4, 5, 5, 5, 7, 4, 0, 5]),
         ("Sympli Attach Rate", 40, "rate", None, "behavior", None, None, [0, 50, 25, 0, 50, 0, 40, 20]),
         ("Meraki Attach Rate", 60, "rate", None, "behavior", None, None, [67, 50, 33, 40, 71.4, 66.7, 75, 58]),
     ]),
    ("utco", "Utah County", None, True,
     "Appointments recovering fast, up 20 points. Conversion is the block. On this volume the attach "
     "goals cannot be reached by arithmetic and need resetting.", [
         ("Appointments Met", 5, "flow", 1, "volume", None, None, [5, 2, 6, 2, 3, 3, 6, 7]),
         ("Signed Units", 4, "flow", 2, "volume", None, None, [4, 1, 4, 2, 3, 4, 1, 2]),
         ("Under Contract", 3, "flow", 3, "volume", None, None, [4, 0, 3, 2, 2, 1, 2, 2]),
         ("Homes Sold", 2, "flow", 4, "volume", None, "20 Q2 pace", [0, 1, 0, 4, 2, 1, 1, 5]),
         ("Meraki Attach Rate", 60, "rate", None, "behavior", None, None, [0, 100, 0, 0, 0, 0, 0, 25]),
         ("Sympli Attach Rate", 40, "rate", None, "behavior", None, None, [0, 0, 0, 50, 0, 0, 0, 0]),
     ]),
    ("overall", "Overall", "Spring", False,
     "Three rows here are running totals, not weekly counts. They carry no cumulative because "
     "summing them double counts.", [
         ("ZHL Referral Rate", 10, "rate", None, "behavior", "SB", None, [None, 12.1, 11.9, 8.7, 9.0, 11.5, None, 9.7]),
         ("New Recruitment Leads", 9, "flow", None, "volume", "SB", None, [None, 14, 24, 12, 3, 16, 4, 3]),
         ("Mastermind RSVPs", 0, "flow", None, "volume", "SB", None, [None, None, None, None, 62, 0, 3, 0]),
         ("Met to Signed Ratio YTD", 60, "snapshot", None, None, "SB", None, [None, 55.7, 55.4, 56.1, 56.3, 56.0, None, 56.6]),
         ("Meraki Attach, Utah Life", 60, "rate", None, "behavior", "SB", None, [None, 50.0, 26.3, 40.9, 61.1, 57.1, 61.1, 37.5]),
         ("Sympli Attach, Utah Life", 40, "rate", None, "behavior", "SB", None, [None, 26.0, 0.0, 15.4, 11.1, 9.1, 16.7, 18.8]),
         ("Database HealthScore", 65, "snapshot", None, None, "SB", None, [None, 65, 66, 65, 67, 65, 66, 65]),
         ("Q2 Homes, 250 target", 20, "flow", None, "volume", "SB", None, [None, 16, 19, 21, 18, 14, 14, 16]),
         ("QTD Agents Recruited", 4, "snapshot", None, None, "SB", None, [None, 5, 5, 1, None, 13, None, 20]),
     ]),
]

# ULRG's custom (non-calendar) fiscal quarters — the sheet runs "Q2 = Apr 13 → end of July".
# CONFIRM the real boundaries with Connor (Part 9.2); these match the spec's 5.1 example.
_FISCAL_QUARTERS = [
    {"key": "2026Q2", "start": "2026-04-13", "end": "2026-08-02"},
    {"key": "2026Q3", "start": "2026-08-03", "end": "2026-11-01"},
]


async def load_ulrg_scorecard(s, tenant_id) -> int:
    biz = (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id, Business.key == "ulrg"))).scalar_one_or_none()
    if not biz:
        raise RuntimeError("no ULRG business for this tenant")

    # idempotent: clear this business's scorecard (values cascade from metrics/groups)
    old = (await s.execute(select(ScorecardGroup).where(
        ScorecardGroup.tenant_id == tenant_id, ScorecardGroup.business_id == biz.id))).scalars().all()
    for g in old:
        await s.execute(delete(ScorecardGroup).where(ScorecardGroup.id == g.id))

    n_values = 0   # owner_user_id left null; display uses owner_name / owner_initials
    for gi, (key, name, owner, is_room, read, metrics) in enumerate(_GROUPS):
        group = ScorecardGroup(tenant_id=tenant_id, business_id=biz.id, key=key, name=name,
                               owner_name=owner, is_team_room=is_room, read=read, sort_order=gi)
        s.add(group)
        await s.flush()
        for mi, (mname, goal, mtype, stage, lever, initials, note, values) in enumerate(metrics):
            metric = ScorecardMetric(
                tenant_id=tenant_id, group_id=group.id, name=mname, goal=Decimal(str(goal)),
                direction="gte", type=mtype, stage=stage, lever=lever, source="manual",
                owner_initials=initials, note=note, sort_order=mi, active=True)
            s.add(metric)
            await s.flush()
            for wi, v in enumerate(values):            # backfill (Step 2) — null stays null
                s.add(ScorecardValue(
                    tenant_id=tenant_id, metric_id=metric.id, week_start=WEEK_STARTS[wi],
                    value=None if v is None else Decimal(str(v)), source="manual"))
                n_values += 1

    # fiscal quarters onto tenant.config (Part 4.7) — the JSON field already exists
    tenant = await s.get(Tenant, tenant_id)
    cfg = dict(tenant.config or {})
    cfg.setdefault("fiscal_quarters", _FISCAL_QUARTERS)
    tenant.config = cfg
    await s.commit()
    return n_values


async def _main(slug: str):
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if not t:
            print(f"[seed_ulrg_scorecard] no tenant '{slug}'"); return
        n = await load_ulrg_scorecard(s, t.id)
        print(f"[seed_ulrg_scorecard] seeded 4 groups + metrics + {n} weekly values for '{slug}' "
              f"(SEED DATA — reconcile goals/history with the live sheet)")


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1] if len(sys.argv) > 1 else "springb"))
