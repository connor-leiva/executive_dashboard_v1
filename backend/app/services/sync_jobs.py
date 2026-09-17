"""Running a sync on request, whoever asked for it.

A workspace's own Settings page and the operator console both offer "sync now". They run these two
jobs, so a sync an operator starts is indistinguishable from one the customer starts: the same
SyncRun rows, the same error handling, and the same post-sync steps inside run_all.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from ..db import SessionLocal
from ..models import SyncRun
from .metrics import _period_range
from .sync import run_all, run_one


def syncs_frozen(config: dict | None) -> bool:
    """An operator froze this workspace's syncs: nothing pulls from its sources, scheduled or
    requested, until they are unfrozen. People stay signed in."""
    return bool((config or {}).get("syncs_frozen"))


async def run_all_job(tenant_id, run_id, period: str = "mtd"):
    async with SessionLocal() as s:
        start, end = _period_range(period)
        run = (await s.execute(select(SyncRun).where(SyncRun.id == run_id))).scalar_one()
        try:
            await run_all(s, tenant_id, start.isoformat(), end.isoformat())
            run.status, run.finished_at = "ok", dt.datetime.utcnow()
        except Exception as e:  # noqa: BLE001
            run.status, run.detail, run.finished_at = "error", str(e), dt.datetime.utcnow()
        await s.commit()


async def run_one_job(tenant_id, integ_id, period: str = "mtd"):
    async with SessionLocal() as s:
        start, end = _period_range(period)
        await run_one(s, tenant_id, integ_id, start.isoformat(), end.isoformat())
