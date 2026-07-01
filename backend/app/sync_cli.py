"""One-shot sync runner — pulls every connected integration for the tenant.

Run from the Railway api service Console (avoids the HTTP request timeout on the
initial ~33-page Sisu pull):

    python -m app.sync_cli

Syncs whatever integrations are marked `connected` (Sisu now; QBO once OAuth'd).
"""
import asyncio
import datetime as dt

from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .models import Tenant
from .services.sync import run_all


async def main():
    today = dt.date.today()
    start, end = today.replace(day=1).isoformat(), today.isoformat()
    async with SessionLocal() as s:
        tenant = (await s.execute(
            select(Tenant).where(Tenant.slug == settings.DEV_TENANT_SLUG)
        )).scalar_one_or_none()
        if not tenant:
            print(f"No tenant with slug '{settings.DEV_TENANT_SLUG}'. Seed first.")
            return
        print(f"Syncing tenant '{tenant.slug}' for {start} -> {end} ...")
        await run_all(s, tenant.id, start, end)
    print("[ok] sync complete")


if __name__ == "__main__":
    asyncio.run(main())
