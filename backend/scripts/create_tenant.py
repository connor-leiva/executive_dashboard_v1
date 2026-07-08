"""Zero-ops tenant provisioning (SPEC-platform §6.1). Creates a tenant, its
{slug}.acumyn.io domain, its seeded businesses, and an INVITED owner — then prints
the owner's one-time invite URL. No Railway/DNS touches; the tenant is live at its
subdomain immediately. This is also the seam a future self-serve signup calls.

Usage:
  cd backend && ./.venv/Scripts/python.exe -m scripts.create_tenant \
      --slug acme --name "Acme Co" --owner-email owner@acme.com \
      [--businesses '[{"key":"ulrg","name":"Acme Realty","tag":"Real estate"}]']
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
from decimal import Decimal

from sqlalchemy import select

from app.db import SessionLocal, engine
from app.config import settings
from app.models import Base, Tenant, Domain, Business, User
from app.security import new_action_token
from app.tenancy import RESERVED_SLUGS

DEFAULT_BUSINESSES = [
    {"key": "main", "name": "Main", "tag": "Business", "accent": "#61835E", "ink": "#4D6A4D"},
]


async def create_tenant(slug: str, name: str, owner_email: str, businesses: list[dict]) -> str:
    slug = slug.strip().lower()
    if slug in RESERVED_SLUGS:
        raise SystemExit(f"'{slug}' is a reserved slug")
    if settings.is_sqlite:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        if (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none():
            raise SystemExit(f"Tenant '{slug}' already exists")
        t = Tenant(slug=slug, name=name)
        s.add(t)
        await s.flush()
        s.add(Domain(tenant_id=t.id, hostname=f"{slug}.acumyn.io", is_primary=True))
        for i, b in enumerate(businesses):
            s.add(Business(
                tenant_id=t.id, key=b["key"], name=b.get("name") or b["key"],
                tag=b.get("tag") or "Business", accent=b.get("accent") or "#61835E",
                ink=b.get("ink") or "#4D6A4D", is_jv=bool(b.get("is_jv")),
                jv_share=Decimal(str(b.get("jv_share", 1.0))), sort_order=i))
        raw, th = new_action_token()
        s.add(User(
            tenant_id=t.id, email=owner_email.strip().lower(), name=owner_email.split("@")[0],
            password_hash=None, role="owner", status="invited", token_version=0,
            action_token_hash=th, action_token_purpose="invite",
            action_token_expires=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7)))
        await s.commit()
    return f"https://{slug}.acumyn.io/accept-invite?token={raw}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--owner-email", required=True)
    ap.add_argument("--businesses", default=None, help="JSON list of business dicts")
    a = ap.parse_args()
    biz = json.loads(a.businesses) if a.businesses else DEFAULT_BUSINESSES
    url = asyncio.run(create_tenant(a.slug, a.name, a.owner_email, biz))
    print("\n[ok] Tenant created. Send the owner this one-time invite link (7-day expiry):\n")
    print("   " + url + "\n")


if __name__ == "__main__":
    main()
