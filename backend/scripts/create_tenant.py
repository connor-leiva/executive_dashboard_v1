"""Zero-ops tenant provisioning (SPEC-platform 6.1). Creates a tenant, its
{slug}.PLATFORM_DOMAIN domain, its seeded businesses, the catalogs the app expects to find,
and an INVITED owner — then prints the owner's one-time invite URL. No Railway/DNS touches;
the tenant is live at its subdomain immediately, because tenant-host resolution reads the
domain table and CORS matches the platform domain by regex.

The work itself lives in `app.services.provisioning` so this CLI, `app.seed`, and a future
self-serve signup all take the same path — the reason a tenant used to come up with an empty
Binder and no chart of accounts was that only seed.py knew to wire them.

Usage:
  cd backend && ./.venv/Scripts/python.exe -m scripts.create_tenant \
      --slug acme --name "Acme Co" --owner-email owner@acme.com \
      [--businesses '[{"key":"ulrg","name":"Acme Realty","tag":"Real estate"}]'] \
      [--hostname acme.internal]      # override the derived {slug}.PLATFORM_DOMAIN
"""
from __future__ import annotations

import argparse
import asyncio
import json

from app.config import settings
from app.db import SessionLocal, engine
from app.models import Base
from app.services.provisioning import DEFAULT_BUSINESSES, provision_tenant


async def _run(slug: str, name: str, owner_email: str,
               businesses: list[dict], hostname: str | None):
    if settings.is_sqlite:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        return await provision_tenant(
            s, slug=slug, name=name, owner_email=owner_email,
            businesses=businesses, hostname=hostname)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--owner-email", required=True)
    ap.add_argument("--businesses", default=None, help="JSON list of business dicts")
    ap.add_argument("--hostname", default=None,
                    help=f"override the derived {{slug}}.{settings.PLATFORM_DOMAIN}")
    a = ap.parse_args()
    biz = json.loads(a.businesses) if a.businesses else DEFAULT_BUSINESSES
    try:
        r = asyncio.run(_run(a.slug, a.name, a.owner_email, biz, a.hostname))
    except ValueError as e:
        raise SystemExit(f"[error] {e}")

    chart = r.catalogs.get("standard_chart") or {}
    print(f"\n[ok] Tenant '{r.slug}' created at {r.hostname}")
    print(f"     catalogs: {r.catalogs.get('jurisdiction_rules', 0)} jurisdiction rules, "
          f"{r.catalogs.get('ai_skills', 0)} AI skills, "
          f"{chart.get('total', 0)} standard accounts")
    print(f"\n     Send {r.owner_email} this one-time invite link (7-day expiry):\n")
    print("     " + r.invite_url + "\n")


if __name__ == "__main__":
    main()
