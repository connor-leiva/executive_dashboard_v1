"""Destroy every SkySlope-sourced row for one workspace, or for all of them.

SkySlope's API terms require this on termination or on request. See services/skyslope_purge.py for
what it covers and why the record it writes survives it.

It deletes access keys and sync state along with the data, so a workspace that carries on has to
reconnect. There is no undo. A dry run says what would go and writes nothing.

Usage:
  cd backend && ./.venv/Scripts/python.exe -m scripts.skyslope_purge --tenant activatedagent --dry-run
  ... --tenant activatedagent
  ... --all

In production it has to run inside the container, because DATABASE_URL is Railway-internal:
  railway ssh --service executive_dashboard_v1 -- python -m scripts.skyslope_purge --tenant <slug> --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.db import SessionLocal, engine
from app.models import Tenant
from app.services import skyslope_purge


def _print(tenant: Tenant, removed: dict[str, int]) -> None:
    print(f"{tenant.slug}:")
    for name, n in removed.items():
        print(f"  {n:>8}  {name}")


async def _run(slug: str | None, everyone: bool, dry_run: bool) -> int:
    async with SessionLocal() as s:
        query = select(Tenant).order_by(Tenant.slug)
        if not everyone:
            query = query.where(Tenant.slug == slug)
        tenants = (await s.execute(query)).scalars().all()
        if not tenants:
            print(f"No workspace with the slug {slug!r}. Nothing was changed.", file=sys.stderr)
            return 1
        for tenant in tenants:
            if dry_run:
                _print(tenant, await skyslope_purge.counts(s, tenant.id))
                continue
            done = await skyslope_purge.purge_tenant(s, tenant.id)
            await s.commit()
            _print(tenant, done["removed"])
            print(f"  purged at {done['at'].isoformat()}; recorded in audit_log as skyslope.purged")
    print("DRY RUN: nothing was changed." if dry_run else "Done.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--tenant", help="the workspace's slug")
    which.add_argument("--all", action="store_true", help="every workspace")
    ap.add_argument("--dry-run", action="store_true", help="say what would go and write nothing")
    args = ap.parse_args()

    async def go() -> int:
        try:
            return await _run(args.tenant, args.all, args.dry_run)
        finally:
            await engine.dispose()

    raise SystemExit(asyncio.run(go()))


if __name__ == "__main__":
    main()
