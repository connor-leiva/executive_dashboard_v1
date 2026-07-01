import asyncio
import datetime as dt

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from .db import SessionLocal
from .config import settings
from .models import Tenant
from .services.sync import run_all


def _mtd_range():
    today = dt.date.today()
    return today.replace(day=1).isoformat(), today.isoformat()


async def tick():
    start, end = _mtd_range()
    async with SessionLocal() as s:
        tenants = (await s.execute(select(Tenant))).scalars().all()
        for t in tenants:
            await run_all(s, t.id, start, end)


async def main():
    sched = AsyncIOScheduler()
    sched.add_job(
        tick, "interval", minutes=settings.SYNC_INTERVAL_MINUTES,
        next_run_time=dt.datetime.now(),
    )
    sched.start()
    print(f"[worker] started · interval={settings.SYNC_INTERVAL_MINUTES}m")
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
