"""Load invented SkySlope data into one workspace, to show what the integration will look like, or
take it out again.

What it loads and why it is safe to show is in app/services/skyslope_sample.py. In short: invented
people and deals, read by the real sync code, marked as sample on every surface that shows them, and
removed automatically when a real key connects.

It refuses a workspace that already has a real SkySlope key, because sample and real data must never
share a workspace. A dry run says what it would do and writes nothing.

Usage:
  cd backend && ./.venv/Scripts/python.exe -m scripts.skyslope_sample --tenant activatedagent --dry-run
  ... --tenant activatedagent            load, or refresh to today's dates
  ... --tenant activatedagent --remove   take it all out

In production it runs inside the container, because DATABASE_URL is Railway-internal:
  railway ssh --service executive_dashboard_v1 -- python -m scripts.skyslope_sample --tenant <slug> --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys

from sqlalchemy import select

from app.db import SessionLocal, engine
from app.models import Business, Integration, Tenant
from app.services import roles, skyslope_purge, skyslope_sample
from app.services.sync import run_one


async def _run(slug: str, business_key: str | None, remove: bool, dry_run: bool) -> int:
    async with SessionLocal() as s:
        tenant = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if tenant is None:
            print(f"No workspace with the slug {slug!r}. Nothing was changed.", file=sys.stderr)
            return 1
        rows = (await s.execute(select(Integration).where(
            Integration.tenant_id == tenant.id, Integration.provider == skyslope_sample.PROVIDER))).scalars().all()
        # Any real key, even a disconnected one: disconnecting keeps the data it pulled, and a purge
        # meant for the sample would destroy that too.
        real = [r for r in rows if not skyslope_sample.is_sample(r)]
        if real:
            print(f"{slug} has {len(real)} real SkySlope connection(s). Sample data is never mixed with real "
                  "data, so nothing was changed.", file=sys.stderr)
            return 1

        if remove:
            counts = await skyslope_purge.counts(s, tenant.id)
            for name, n in counts.items():
                print(f"  {n:>8}  {name}")
            if dry_run:
                print("DRY RUN: nothing was removed.")
                return 0
            await skyslope_purge.purge_tenant(s, tenant.id, actor="skyslope_sample --remove")
            await s.commit()
            print("Sample data removed; recorded in audit_log as skyslope.purged.")
            return 0

        q = select(Business).where(Business.tenant_id == tenant.id, Business.kind == roles.REAL_ESTATE)
        if business_key:
            q = q.where(Business.key == business_key)
        businesses = (await s.execute(q)).scalars().all()
        if len(businesses) != 1:
            print(f"{slug} has {len(businesses)} matching real-estate businesses; name one with --business.",
                  file=sys.stderr)
            return 1
        biz = businesses[0]
        today = dt.datetime.now(dt.timezone.utc).date()
        files, users = skyslope_sample.build(today)
        print(f"{slug} / {biz.key}: {sum(f['objectType'] == 'sale' for f in files)} sales, "
              f"{sum(f['objectType'] == 'listing' for f in files)} listings, {len(users)} people, dated around {today}")
        if dry_run:
            print("DRY RUN: nothing was written.")
            return 0

        integ = next((r for r in rows if skyslope_sample.is_sample(r)), None)
        if integ is None:
            integ = Integration(tenant_id=tenant.id, provider=skyslope_sample.PROVIDER, business_id=biz.id)
            s.add(integ)
        integ.business_id, integ.status, integ.last_error = biz.id, "connected", None
        integ.config = {**(integ.config or {}), "label": skyslope_sample.LABEL, "sample": True,
                        "sample_anchor": today.isoformat()}
        await s.commit()
        month = today.replace(day=1)
        await run_one(s, tenant.id, integ.id, month.isoformat(), today.isoformat())
        await s.refresh(integ)
        print(f"sync: {integ.status}{' - ' + integ.last_error if integ.last_error else ''}")
        return 0 if integ.status == "connected" else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tenant", required=True, help="the workspace's slug")
    ap.add_argument("--business", help="the real-estate business's key, when the workspace has several")
    ap.add_argument("--remove", action="store_true", help="take the sample data out")
    ap.add_argument("--dry-run", action="store_true", help="say what would happen and write nothing")
    args = ap.parse_args()

    async def go() -> int:
        try:
            return await _run(args.tenant, args.business, args.remove, args.dry_run)
        finally:
            await engine.dispose()

    raise SystemExit(asyncio.run(go()))


if __name__ == "__main__":
    main()
