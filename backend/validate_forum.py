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

        d = await build_dashboard(s, biz.tenant_id, "mtd")
        area = d.areas["springb"]
        print("\n=== THE FORUM panel (live) ===")
        print(f"status: {area.status}")
        for o in area.ops:
            print(f"  {o.label:<16} {o.value:<10} {o.sub or ''}   [key={o.key}]")
        sc = next((x for x in d.scorecards if x.label == "Active Members"), None)
        if sc:
            print(f"\nScorecard Active Members: {sc.value}  ({sc.sub})")


if __name__ == "__main__":
    asyncio.run(main())
