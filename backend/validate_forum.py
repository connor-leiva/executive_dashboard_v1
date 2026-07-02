"""Validate the re-architected GHL pipeline end-to-end against LIVE data:
seed a local DB, inject the token (from .probe.env) into the GHL integration, run
the real sync_ghl, then build_dashboard and print the Forum panel. No PII printed."""
import asyncio
import os
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Business, Integration
from app.security import enc
from app.seed import seed
from app.services.sync import sync_ghl
from app.services.metrics import build_dashboard
from app.services.forum import build_forum

HERE = os.path.dirname(os.path.abspath(__file__))


def load_conf():
    c = {}
    p = os.path.join(HERE, ".probe.env")
    if os.path.exists(p):
        for ln in open(p, encoding="utf-8"):
            if "=" in ln and not ln.strip().startswith("#"):
                k, _, v = ln.strip().partition("=")
                c[k.strip()] = v.strip().strip('"').strip("'")
    return (c.get("GHL_TOKEN") or os.environ.get("GHL_TOKEN"),
            c.get("GHL_LOCATION_ID") or os.environ.get("GHL_LOCATION_ID") or "IT2T9rc5U89rz8YqPT1E")


async def main():
    token, loc = load_conf()
    if not token:
        sys.exit("No token (backend/.probe.env).")
    await seed()
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        integ = (await s.execute(select(Integration).where(
            Integration.provider == "ghl", Integration.business_id == biz.id))).scalar_one()
        integ.access_token_enc = enc(token)
        integ.status = "connected"
        cfg = dict(integ.config or {})
        cfg["location_id"] = loc
        integ.config = cfg
        await s.commit()

        print("Running live sync_ghl ...", flush=True)
        await sync_ghl(s, biz.tenant_id, integ)

        # ── The full /api/v1/forum payload (Part 2 sync additions validated live) ──
        f = await build_forum(s, biz.tenant_id, "mtd")
        print("\n=== THE FORUM view (live) ===")
        print(f"status: {f['status']}  watch: {f['watch']}")
        print("KPIs:")
        for k in f["kpis"]:
            print(f"  {k['label']:<16} {k['value']:<9} {k.get('sub') or ''}")

        fu = f.get("funnel")
        print("\nRecruiting funnel:", "(none)" if not fu else "")
        if fu:
            for st in fu["stages"]:
                print(f"  {st['label']:<16} {st['v']}")
            print("  footer:", fu.get("footer"))

        rn = f.get("renewals")
        if rn:
            print(f"\nRenewals next 90d: {rn['summary']['count']} · {rn['summary']['value']} · segments {rn['summary']['segments']}")

        ev = f.get("event")
        if ev:
            print(f"\nEvent: {ev['where']} · days_out {ev['days_out']} · registered {ev['registered']} · "
                  f"guests {ev['guests']} · unregistered {ev['unregistered']} · behind_pace {ev['behind_pace']}")

        rq = f.get("revq")
        if rq:
            print("\nRevenue quality:")
            print(f"  PIF     : {rq.get('pif')}")
            print(f"  Monthly : {rq.get('monthly')}")
            print(f"  Past due: {rq.get('past_due')}")
            print(f"  Bridge  : {[ (b['label'], b['value']) for b in rq.get('bridge', []) ] or '(omitted)'}")

        # Consistency invariants (spec Part 4)
        print("\nInvariants:")
        if rq and rq.get("pif") and rq.get("monthly"):
            pc, mc = rq["pif"]["count"], rq["monthly"]["count"]
            print(f"  pif.count + monthly.count == memberships:  {pc}+{mc}={pc+mc}")
        if ev:
            print(f"  registered + unregistered == members:      {ev['registered']}+{ev['unregistered']}={ev['registered']+ev['unregistered']}  (members {ev['members']})")


if __name__ == "__main__":
    asyncio.run(main())
