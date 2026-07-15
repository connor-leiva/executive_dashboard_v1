"""Acumyn Books — Part 1 schema smoke test. Confirms the new tables build via
create_all, the BookTxn source-uniqueness constraint the sync upsert depends on, and
that the new Tenant.config round-trips. (Sync/scan/API tests arrive with later steps.)"""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant, Business, BookTxn, PLLine, ICRule, ICLink, ClosePeriod, BooksReview


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _springb():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        b = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        return t.id, b.id


def _txn(tenant_id, business_id, qbo_id="P1", **kw):
    base = dict(tenant_id=tenant_id, business_id=business_id, realm_id="realm-1",
                qbo_type="Purchase", qbo_id=qbo_id, txn_date=dt.date(2026, 7, 11),
                amount=Decimal("389.00"), payee="Canva Teams")
    base.update(kw)
    return BookTxn(**base)


async def test_all_books_tables_exist():
    # create_all (run in seed) must build every Books table + the new tenant.config
    tid, bid = await _springb()
    async with SessionLocal() as s:
        s.add_all([
            PLLine(tenant_id=tid, business_id=bid, period_start=dt.date(2026, 6, 1),
                   period_end=dt.date(2026, 6, 30), section="income", label="Memberships",
                   amount=Decimal("96000")),
            ICRule(tenant_id=tid, label="Office rent to holding LLC", characterization="rent"),
            ClosePeriod(tenant_id=tid, business_id=bid, period=dt.date(2026, 6, 1),
                        steps={"bank_rec": True, "card_rec": False}),
            BooksReview(tenant_id=tid, period=dt.date(2026, 6, 1), body="June closed clean."),
        ])
        await s.commit()
        assert (await s.execute(select(ICRule).where(ICRule.tenant_id == tid))).scalars().first() is not None


async def test_booktxn_source_uniqueness():
    tid, bid = await _springb()
    async with SessionLocal() as s:
        s.add(_txn(tid, bid, qbo_id="UNIQ1", scan_state="cleared",
                   suggestion={"category": "Marketing - Software", "confidence": 0.99}))
        await s.commit()
    # same (tenant, realm, qbo_type, qbo_id) must collide — the sync upsert relies on it
    async with SessionLocal() as s:
        s.add(_txn(tid, bid, qbo_id="UNIQ1"))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_tenant_config_roundtrips():
    tid, _ = await _springb()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.id == tid))).scalar_one()
        t.config = {"books_elim_accounts": ["Intercompany Rent", "Due to/from"]}
        await s.commit()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.id == tid))).scalar_one()
        assert t.config["books_elim_accounts"] == ["Intercompany Rent", "Due to/from"]
