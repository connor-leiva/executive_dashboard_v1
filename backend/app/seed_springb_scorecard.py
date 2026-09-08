"""Spring B L10 Scorecard — seed (v1: the 15 always-on measurables as agreed).

Unlike the ULRG board (extracted from a real published sheet with 23 weeks of history), the Spring B
board is BRAND NEW: no history, no goals, no owners yet — the team assigns those over time through the
scorecard's own Settings (goal 0 = track-only until a bar is set). Every line is manual for v1; the
week columns come from build_scorecard's trailing window, not from seeded values, so nothing is seeded
per-week. Run it the same way as the ULRG seed:

    python -m app.seed_springb_scorecard [tenant_slug]     # default: springb

Idempotent — clears this business's scorecard and recreates it (metrics/values cascade from the group).
The business is resolved by KIND (membership), not by key, so it is tenant-agnostic.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from decimal import Decimal

from sqlalchemy import select, delete

from .db import SessionLocal
from .models import Tenant, ScorecardGroup, ScorecardMetric
from .services import roles

_DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "springb_scorecard_seed.json")


def _load_data() -> dict:
    with open(_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


async def load_springb_scorecard(s, tenant_id) -> int:
    """Seed the Spring B (membership) scorecard: 4 groups, 15 track-only manual measurables, no
    history. Returns the number of measurables created."""
    data = _load_data()
    biz = await roles.membership(s, tenant_id)
    if not biz:
        raise RuntimeError("no Spring B (membership) business for this tenant")

    # idempotent: clear this business's scorecard (metrics/values cascade from the group)
    for g in (await s.execute(select(ScorecardGroup).where(
            ScorecardGroup.tenant_id == tenant_id, ScorecardGroup.business_id == biz.id))).scalars().all():
        await s.execute(delete(ScorecardGroup).where(ScorecardGroup.id == g.id))

    n = 0
    for gi, g in enumerate(data["groups"]):
        group = ScorecardGroup(
            tenant_id=tenant_id, business_id=biz.id, key=g["key"], name=g["name"],
            is_team_room=False, sort_order=gi)                 # brand/function groups, not Sisu team rooms
        s.add(group)
        await s.flush()
        for mi, m in enumerate(g["metrics"]):
            note = m.get("note")
            s.add(ScorecardMetric(
                tenant_id=tenant_id, group_id=group.id, name=m["name"][:160],
                goal=Decimal("0"),                             # track-only until the team sets a bar
                direction=m.get("direction", "gte"), type=m["type"], source="manual",
                note=note[:160] if note else None,             # note is String(160) — Postgres enforces it (SQLite doesn't)
                sort_order=mi, active=True, resolver_key=None))
            n += 1
    await s.commit()
    return n


async def _main(slug: str = "springb"):
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if not t:
            print(f"[seed_springb_scorecard] no tenant '{slug}'"); return
        n = await load_springb_scorecard(s, t.id)
        print(f"[seed_springb_scorecard] seeded {n} measurables (4 groups) for '{slug}' — "
              f"blank board, owners/goals/history assigned via Settings over time")


if __name__ == "__main__":
    _args = [a for a in sys.argv[1:] if not a.startswith("-")]
    asyncio.run(_main(_args[0] if _args else "springb"))
