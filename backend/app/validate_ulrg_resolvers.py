r"""ULRG scorecard resolver validation (SPEC Step 5: "switch manual→resolver only after a parallel
week matches"). For each row we intend to auto-source, prints the resolver's computed count next to
the hand-entered value for the most recent weeks — so we only flip a metric's resolver_key once the
numbers line up.

Refreshes the agent→office roster (writes agent.sisu_group_ids) when Sisu creds are present, so the
per-team rows can be computed; it NEVER changes scorecard values and NEVER flips a metric. Run it
against a live DB the same way as the seed:

    $env:DATABASE_URL="<prod public url>"; .\.venv\Scripts\python -m app.validate_ulrg_resolvers springb

If SISU_USERNAME/SISU_API_TOKEN are also set it refreshes the roster first; otherwise it uses the
roster already in the DB (populated by the daily worker.roster_tick after deploy) — the Overall row
needs no roster, the per-team rows do.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows consoles default to cp1252
except Exception:  # noqa: BLE001
    pass

from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .models import Business, ScorecardGroup, ScorecardMetric, ScorecardValue, Tenant
from .services import scorecard_resolvers as R
from .services.sync import sync_agent_offices


def _resolver_for(group_key: str, name: str) -> str | None:
    """Map a live metric to the resolver we intend to flip it to (metrics don't set resolver_key
    until validated). Overall homes → business-wide; each team's Homes-Sold / Under-Contract /
    Appointments-Met / Clients-Signed → per-team."""
    n = name.lower()
    if "sympli" in n and ("attach" in n or "mortgage" in n):
        return "ulrg_team_sympli_attach"                    # per-office AND overall (resolver handles both)
    if "meraki" in n:
        return "ulrg_team_meraki_attach"                    # title attach — per-office AND overall
    if group_key == "overall" and "homes" in n:
        return "ulrg_homes_closed"
    if group_key in ("davis", "slc", "utco"):
        if "homes sold" in n:
            return "ulrg_team_homes_closed"
        if "under contract" in n:
            return "ulrg_team_under_contract"
        if "appointments met" in n and "recruiting" not in n:   # not "Recruiting Appointments Met"
            return "ulrg_team_appts_met"
        if "signed" in n:                                        # "Clients Signed" / "Signed Units"
            return "ulrg_team_signed"
    return None


def _fmt(v) -> str:
    return "  -" if v is None else f"{v:>3.0f}"


async def _main(slug: str, weeks_back: int = 6) -> None:
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if not t:
            print(f"[validate] no tenant '{slug}'"); return
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == t.id, Business.key == "ulrg"))).scalar_one_or_none()
        if not biz:
            print("[validate] no ULRG business for this tenant"); return

    # refresh the roster so per-team attribution is populated (best-effort; needs Sisu creds)
    if settings.SISU_USERNAME and settings.SISU_API_TOKEN:
        async with SessionLocal() as s:
            n = await sync_agent_offices(s, t.id)
        print(f"[validate] roster refreshed: {n} agents have an office\n")
    else:
        print("[validate] SISU creds not set — using the roster already in the DB "
              "(per-team rows need worker.roster_tick to have run in prod)\n")

    async with SessionLocal() as s:
        groups = {g.id: g for g in (await s.execute(select(ScorecardGroup).where(
            ScorecardGroup.tenant_id == t.id, ScorecardGroup.business_id == biz.id))).scalars()}
        metrics = (await s.execute(select(ScorecardMetric).where(
            ScorecardMetric.tenant_id == t.id, ScorecardMetric.active.is_(True)))).scalars().all()
        weeks = sorted({v.week_start for v in (await s.execute(select(ScorecardValue).where(
            ScorecardValue.tenant_id == t.id))).scalars()})[-weeks_back:]

    if not weeks:
        print("[validate] no stored weeks to compare against"); return
    print(f"Comparing resolver vs hand-entered for the last {len(weeks)} weeks "
          f"({weeks[0]} to {weeks[-1]}). 'match' = within 0.5.\n")

    totals = {"match": 0, "diff": 0, "gap": 0}
    for m in sorted(metrics, key=lambda mm: (groups[mm.group_id].sort_order, mm.sort_order)):
        g = groups[m.group_id]
        key = _resolver_for(g.key, m.name)
        if not key:
            continue
        fn = R.RESOLVERS[key]
        ctx = {"key": g.key, "sisu_group_id": g.sisu_group_id}
        print(f"-- {g.name} | {m.name}   [{key}]")
        print(f"   {'week':<12}{'hand':>6}{'resolver':>10}   result")
        for ws in weeks:
            we = ws + dt.timedelta(days=6)
            async with SessionLocal() as s:
                rv = await fn(s, t.id, biz.id, ws, we, group=ctx)
                stored = (await s.execute(select(ScorecardValue.value).where(
                    ScorecardValue.metric_id == m.id, ScorecardValue.week_start == ws))).scalar_one_or_none()
            hv = None if stored is None else float(stored)
            rvf = None if rv is None else float(rv)
            if hv is None or rvf is None:
                res, bucket = "n/a", "gap"
            elif abs(hv - rvf) < 0.5:
                res, bucket = "match", "match"
            else:
                res, bucket = f"DIFF ({rvf - hv:+.0f})", "diff"
            totals[bucket] += 1
            print(f"   {ws.isoformat():<12}{_fmt(hv):>6}{_fmt(rvf):>10}   {res}")
        print()

    print(f"summary: {totals['match']} match · {totals['diff']} differ · {totals['gap']} n/a "
          f"(no hand value or resolver returned a gap)")
    print("Flip a metric's resolver_key only for rows whose recent weeks match.")


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1] if len(sys.argv) > 1 else "springb"))
