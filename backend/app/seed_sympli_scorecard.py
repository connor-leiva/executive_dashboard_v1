"""The Sympli Utah lending board: seed.

Idempotent and additive, like the Spring B seed: it creates whatever groups, rows, goals and gates in
the JSON don't exist yet and leaves everything that does alone, so re-running never wipes entered
values or a goal somebody changed in Settings. The business is resolved by KIND (commission_jv), so a
second market is a second workspace with its own copy of the office and loan-officer lists.

    python -m app.seed_sympli_scorecard [tenant_slug]     # default: springb

On a live workspace use scripts/setup_sympli_scorecard.py, which also creates the period, syncs the
timelines in and backfills the rows.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
from decimal import Decimal

from sqlalchemy import select

from .db import SessionLocal
from .models import ScorecardGate, ScorecardGoal, ScorecardGroup, ScorecardMetric, Tenant
from .services import roles
from .services.scorecard import quarter_of

_DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "sympli_scorecard_seed.json")


def load_data() -> dict:
    with open(_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def board_groups(data: dict) -> list[dict]:
    """The groups in board order, with each loan officer expanded into a group of the standard rows."""
    out = []
    for g in data["groups"]:
        if not g.get("loan_officers"):
            out.append(g)
            continue
        for lo in data["loan_officers"]:
            out.append({"key": lo["key"], "name": lo["name"], "owner": lo["name"], "lo_email": lo["email"],
                        "metrics": [{**m, "owner": lo["initials"]} for m in data["loan_officer_rows"]]})
    return out


async def load_sympli_scorecard(s, tenant_id) -> dict:
    """Returns what it created: {"rows": n, "groups": n, "gates": n, "goals": n}."""
    data = load_data()
    biz = await roles.commission_jv(s, tenant_id)
    if not biz:
        raise RuntimeError("no lending (commission_jv) business for this tenant")
    tenant = await s.get(Tenant, tenant_id)
    periods = {p.get("key"): p for p in ((tenant.config or {}).get("fiscal_quarters") or [])}
    made = {"rows": 0, "groups": 0, "gates": 0, "goals": 0}

    existing = {g.key: g for g in (await s.execute(select(ScorecardGroup).where(
        ScorecardGroup.tenant_id == tenant_id, ScorecardGroup.business_id == biz.id))).scalars().all()}
    for gi, g in enumerate(board_groups(data)):
        group = existing.get(g["key"])
        if group is None:
            group = ScorecardGroup(tenant_id=tenant_id, business_id=biz.id, key=g["key"], name=g["name"],
                                   owner_name=g.get("owner"), is_team_room=False, sort_order=gi,
                                   sisu_group_id=g.get("sisu_group_id"), lo_email=g.get("lo_email"))
            s.add(group)
            await s.flush()
            made["groups"] += 1
        have = {m.name: m for m in (await s.execute(select(ScorecardMetric).where(
            ScorecardMetric.group_id == group.id))).scalars().all()}
        for mi, m in enumerate(g["metrics"]):
            metric = have.get(m["name"])
            if metric is None:
                metric = ScorecardMetric(
                    tenant_id=tenant_id, group_id=group.id, name=m["name"], note=m.get("note"),
                    goal=Decimal(str(m.get("goal", 0))), direction=m.get("direction", "gte"), type=m["type"],
                    source=m.get("source", "manual"), resolver_key=m.get("resolver_key"),
                    owner_initials=m.get("owner"), sort_order=mi, active=True)
                s.add(metric)
                await s.flush()
                made["rows"] += 1
            for key, pg in (m.get("period_goals") or {}).items():
                if key not in periods:
                    continue
                row = (await s.execute(select(ScorecardGoal).where(
                    ScorecardGoal.metric_id == metric.id, ScorecardGoal.period_key == key))).scalar_one_or_none()
                if row is None:
                    # The weekly goal over the period as this workspace actually set it. A total
                    # assuming 13 weeks against an 11-week quarter asks for more than the weekly goal.
                    cum = None
                    if pg.get("total_from_weekly"):
                        p = periods[key]
                        weeks = quarter_of([p], dt.date.fromisoformat(p["start"]))["weeks_total"]
                        cum = round(pg["goal"] * weeks)
                    s.add(ScorecardGoal(tenant_id=tenant_id, metric_id=metric.id, period_key=key,
                                        goal=Decimal(str(pg["goal"])),
                                        cumulative_goal=Decimal(str(cum)) if cum is not None else None))
                    made["goals"] += 1

    gates = {(g.due_date, g.label) for g in (await s.execute(select(ScorecardGate).where(
        ScorecardGate.tenant_id == tenant_id, ScorecardGate.business_id == biz.id))).scalars().all()}
    for i, g in enumerate(data.get("gates") or []):
        due = dt.date.fromisoformat(g["due"])
        if (due, g["label"]) not in gates:
            s.add(ScorecardGate(tenant_id=tenant_id, business_id=biz.id, due_date=due, label=g["label"],
                                owner_name=g.get("owner"), sort_order=i))
            made["gates"] += 1
    await s.commit()
    return made


async def _main(slug: str = "springb"):
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if not t:
            print(f"[seed_sympli_scorecard] no tenant '{slug}'")
            return
        print(f"[seed_sympli_scorecard] created {await load_sympli_scorecard(s, t.id)} for '{slug}'")


if __name__ == "__main__":
    _args = [a for a in sys.argv[1:] if not a.startswith("-")]
    asyncio.run(_main(_args[0] if _args else "springb"))
