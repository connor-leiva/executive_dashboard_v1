"""ULRG L10 Scorecard — Spring seed + backfill (SPEC-ulrg-scorecard Part 6, Steps 1 + 2).

============================ TENANT SEED — NOT PRODUCT DATA ============================
Acumyn ships an EMPTY ULRG-shaped scorecard for a new tenant (Part 0.5); Spring's groups,
measurables, and history are THIS tenant's data. So — like the AI demo fixture — Spring/Davis/SLC
live here (and in the sibling data file), never in the product code (services/frontend), which is
what the acceptance greps enforce. Load it explicitly:

    python -m app.seed_ulrg_scorecard [tenant_slug]     # default: springb

The data in `app/data/ulrg_scorecard_seed.json` is extracted from the real published sheet
(ULRG_Team_Scoreboard, pulled 6 Aug 2026): 23 weeks (W8–W30), rates stored ×100 to match the
display scale. Blank cells stay null (uncollected ≠ 0). Every metric seeds `source="manual"`
(Step 1); resolvers (Step 5) flip individual rows once validated. Fiscal-quarter boundaries are
best-effort (Q2 = Apr 13 → Aug 2, per the sheet's "Apr 13 – end of July") — CONFIRM with Connor
(the sheet's "weeks left" was a placeholder of 5).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select, delete

from .config import settings
from .db import SessionLocal
from .models import Tenant, Business, ScorecardGroup, ScorecardMetric, ScorecardValue

_DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "ulrg_scorecard_seed.json")


def _load_data() -> dict:
    with open(_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


async def load_ulrg_scorecard(s, tenant_id) -> int:
    data = _load_data()
    week_starts = [dt.date.fromisoformat(w) for w in data["week_starts"]]
    biz = (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id, Business.key == "ulrg"))).scalar_one_or_none()
    if not biz:
        raise RuntimeError("no ULRG business for this tenant")

    # idempotent: clear this business's scorecard (metrics/values cascade from the group)
    for g in (await s.execute(select(ScorecardGroup).where(
            ScorecardGroup.tenant_id == tenant_id, ScorecardGroup.business_id == biz.id))).scalars().all():
        await s.execute(delete(ScorecardGroup).where(ScorecardGroup.id == g.id))

    n_values = 0
    for gi, g in enumerate(data["groups"]):
        group = ScorecardGroup(
            tenant_id=tenant_id, business_id=biz.id, key=g["key"], name=g["name"],
            owner_name=g.get("owner"), is_team_room=g.get("is_team_room", True),
            read=g.get("read"), sort_order=gi, sisu_group_id=g.get("sisu_group_id"))
        s.add(group)
        await s.flush()
        for mi, m in enumerate(g["metrics"]):
            metric = ScorecardMetric(
                tenant_id=tenant_id, group_id=group.id, name=m["name"], goal=Decimal(str(m["goal"])),
                direction=m.get("direction", "gte"), type=m["type"], stage=m.get("stage"),
                lever=m.get("lever"), source="manual", owner_initials=m.get("initials"),
                note=m.get("note"), sort_order=mi, active=True,
                resolver_key=m.get("resolver_key"))    # auto-sourced rows carry a resolver_key (Step 5)
            s.add(metric)
            await s.flush()
            for wi, v in enumerate(m["values"]):            # backfill (Step 2) — null stays null
                s.add(ScorecardValue(
                    tenant_id=tenant_id, metric_id=metric.id, week_start=week_starts[wi],
                    value=None if v is None else Decimal(str(v)), source="manual"))
                n_values += 1

    tenant = await s.get(Tenant, tenant_id)
    cfg = dict(tenant.config or {})
    cfg["fiscal_quarters"] = data["fiscal_quarters"]   # the scorecard seed owns these — overwrite so a reseed corrects them
    tenant.config = cfg
    await s.commit()
    return n_values


async def wire_resolvers(s, tenant_id) -> tuple[int, int]:
    """Apply the resolver config from the seed JSON to an ALREADY-seeded tenant (prod) WITHOUT
    clearing metrics/values: each group's sisu_group_id (office mapping) and each metric's
    resolver_key (which rows are auto-sourced). Non-destructive — only those two fields are written,
    and resolver_key is only SET where the JSON marks it (unmarked rows are left alone). Returns
    (groups_mapped, metrics_wired)."""
    data = _load_data()
    biz = (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id, Business.key == "ulrg"))).scalar_one_or_none()
    if not biz:
        raise RuntimeError("no ULRG business for this tenant")
    by_key = {g["key"]: g for g in data["groups"]}
    groups = (await s.execute(select(ScorecardGroup).where(
        ScorecardGroup.tenant_id == tenant_id, ScorecardGroup.business_id == biz.id))).scalars().all()
    g_mapped, m_wired = 0, 0
    for grp in groups:
        jg = by_key.get(grp.key)
        if jg is None:
            continue
        grp.sisu_group_id = jg.get("sisu_group_id")
        g_mapped += 1
        rk_by_name = {m["name"]: m.get("resolver_key") for m in jg.get("metrics", [])}
        mets = (await s.execute(select(ScorecardMetric).where(
            ScorecardMetric.tenant_id == tenant_id, ScorecardMetric.group_id == grp.id))).scalars().all()
        for met in mets:
            rk = rk_by_name.get(met.name)
            if rk is not None:                           # only flip the rows the JSON marks
                met.resolver_key = rk
                m_wired += 1
    await s.commit()
    return g_mapped, m_wired


async def _main(slug: str, wire_only: bool = False):
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if not t:
            print(f"[seed_ulrg_scorecard] no tenant '{slug}'"); return
        tid = t.id
        if not wire_only:
            n = await load_ulrg_scorecard(s, tid)
            print(f"[seed_ulrg_scorecard] seeded groups + metrics + {n} weekly values for '{slug}' "
                  f"from the real sheet (confirm fiscal-quarter boundaries with Connor)")
            return
        g, m = await wire_resolvers(s, tid)              # --wire: flip the config (non-destructive)
        print(f"[seed_ulrg_scorecard] wired resolvers for '{slug}': {g} groups mapped to a Sisu "
              f"office, {m} metrics set to auto-source")

    # then run the resolvers once so the flip shows immediately (else it waits for the daily worker
    # tick). Writes only the trailing look-back weeks; older weeks are untouched.
    from .services.scorecard_resolvers import run_resolvers
    today = dt.datetime.now(ZoneInfo(settings.BILLING_TIMEZONE)).date()
    written = await run_resolvers(SessionLocal, tid, today)
    print(f"[seed_ulrg_scorecard] ran the resolvers: {written} (metric, week) values written "
          f"for the recent weeks")


if __name__ == "__main__":
    _args = [a for a in sys.argv[1:] if not a.startswith("-")]
    _wire = "--wire" in sys.argv or "--scopes" in sys.argv   # --scopes kept as an alias
    asyncio.run(_main(_args[0] if _args else "springb", wire_only=_wire))
