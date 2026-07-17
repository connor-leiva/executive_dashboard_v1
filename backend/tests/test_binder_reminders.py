"""Acumyn Binder — Step 8 reminder tests (SPEC Part 7): stage computation, fire-each-stage-
once dedup, stage advancement, overdue detection, and the digest cadence gate."""
import datetime as dt

import pytest
from sqlalchemy import select, delete

from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant, LegalEntity, Obligation, SyncRun
from app.services.binder_reminders import compute_stage, run_reminders

T = dt.date(2026, 7, 16)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _tid():
    async with SessionLocal() as s:
        return (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id


async def _entity(tid, name):
    async with SessionLocal() as s:
        e = LegalEntity(tenant_id=tid, legal_name=name, entity_type="llc", jurisdiction="UT")
        s.add(e)
        await s.commit()
        return e.id


async def _obligation(tid, eid, kind, due_date, lead_days=45):
    async with SessionLocal() as s:
        ob = Obligation(tenant_id=tid, entity_id=eid, kind=kind, due_date=due_date,
                        cadence="annual", lead_days=lead_days, confirmed_by=None)
        s.add(ob)
        await s.commit()
        return ob.id


async def _get(ob_id):
    async with SessionLocal() as s:
        return (await s.execute(select(Obligation).where(Obligation.id == ob_id))).scalar_one()


# ── stage math ────────────────────────────────────────────────────────────────
def test_compute_stage():
    assert compute_stage(T - dt.timedelta(days=1), T, 45) == "overdue"
    assert compute_stage(T + dt.timedelta(days=5), T, 45) == "urgent"      # <= 14 days
    assert compute_stage(T + dt.timedelta(days=30), T, 45) == "lead"       # <= lead_days, > 14
    assert compute_stage(T + dt.timedelta(days=100), T, 45) is None        # not yet in a window
    assert compute_stage(None, T, 45) is None


# ── firing + dedup ──────────────────────────────────────────────────────────
async def test_reminder_fires_once_per_stage():
    tid = await _tid()
    eid = await _entity(tid, "Reminder Co, LLC")
    ob_id = await _obligation(tid, eid, "insurance", T + dt.timedelta(days=5))   # urgent
    async with SessionLocal() as s:
        await run_reminders(s, tid, today=T)
    ob = await _get(ob_id)
    assert ob.last_reminded_stage == "urgent" and ob.last_reminded_at is not None
    first_at = ob.last_reminded_at
    # Same stage on the next cycle must NOT re-fire (invariant 5).
    async with SessionLocal() as s:
        await run_reminders(s, tid, today=T)
    ob = await _get(ob_id)
    assert ob.last_reminded_stage == "urgent" and ob.last_reminded_at == first_at


async def test_reminder_stage_advances():
    tid = await _tid()
    eid = await _entity(tid, "Advance Co, LLC")
    ob_id = await _obligation(tid, eid, "annual_report", T + dt.timedelta(days=30))   # lead
    async with SessionLocal() as s:
        await run_reminders(s, tid, today=T)
    assert (await _get(ob_id)).last_reminded_stage == "lead"
    # Move it overdue -> the stage advances and fires again.
    async with SessionLocal() as s:
        ob = (await s.execute(select(Obligation).where(Obligation.id == ob_id))).scalar_one()
        ob.due_date = T - dt.timedelta(days=1)
        await s.commit()
    async with SessionLocal() as s:
        summ = await run_reminders(s, tid, today=T)
    assert (await _get(ob_id)).last_reminded_stage == "overdue"
    assert summ["overdue_new"] >= 1


async def test_not_applicable_obligation_is_skipped():
    tid = await _tid()
    eid = await _entity(tid, "NA Reminder Co, LLC")
    ob_id = await _obligation(tid, eid, "insurance", T - dt.timedelta(days=1))    # overdue by date
    async with SessionLocal() as s:
        ob = (await s.execute(select(Obligation).where(Obligation.id == ob_id))).scalar_one()
        ob.applicable = False
        await s.commit()
    async with SessionLocal() as s:
        await run_reminders(s, tid, today=T)
    assert (await _get(ob_id)).last_reminded_stage is None    # n/a never reminds


# ── digest cadence gate ─────────────────────────────────────────────────────
async def test_digest_delivered_once_per_cadence():
    tid = await _tid()
    async with SessionLocal() as s:
        await s.execute(delete(SyncRun).where(
            SyncRun.tenant_id == tid, SyncRun.provider == "binder_reminders"))
        await s.commit()
    eid = await _entity(tid, "Digest Co, LLC")
    await _obligation(tid, eid, "insurance", T + dt.timedelta(days=3))   # flagged (urgent)
    async with SessionLocal() as s:
        r1 = await run_reminders(s, tid, today=T)
    async with SessionLocal() as s:
        r2 = await run_reminders(s, tid, today=T)
    assert r1["digest_delivered"] is True and r2["digest_delivered"] is False   # once per window
    async with SessionLocal() as s:
        runs = (await s.execute(select(SyncRun).where(
            SyncRun.tenant_id == tid, SyncRun.provider == "binder_reminders"))).scalars().all()
    assert len(runs) == 1
