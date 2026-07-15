"""Acumyn Books — Part 3.1-3.2 deterministic scan tests (SPEC Part 8 test_books_scan.py).

The scan reads/writes through the ORM, so it runs under SQLite: seed rows with the ORM,
run_scan, assert states + ICLinks. Distinct payees/amounts/realms per test keep the
shared session isolated. (Pass 3 / Claude is Step 4, not covered here.)
"""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant, Business, BookTxn, ICLink
from app.services.books_scan import run_scan, create_ic_rule, seed_ic_rules

TODAY = dt.date(2026, 6, 30)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _ctx():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        biz = {b.key: b.id for b in (await s.execute(
            select(Business).where(Business.tenant_id == t.id))).scalars().all()}
        return t.id, biz


def _txn(tid, bid, realm, qtype, qid, amount, date, payee=None, account_label=None,
         came=False, scan_state="pending", flags=None):
    return BookTxn(tenant_id=tid, business_id=bid, realm_id=realm, qbo_type=qtype, qbo_id=qid,
                   txn_date=date, amount=Decimal(str(amount)), payee=payee,
                   account_label=account_label, came_categorized=came,
                   scan_state=scan_state, flags=flags)


async def _get(qid):
    async with SessionLocal() as s:
        return (await s.execute(select(BookTxn).where(BookTxn.qbo_id == qid))).scalars().first()


async def test_history_rule_clears_known_vendor():
    tid, biz = await _ctx()
    async with SessionLocal() as s:
        for i in range(4):                              # 4 prior, consistently categorized
            s.add(_txn(tid, biz["ulrg"], "r-ulrg", "Purchase", f"canva{i}", 389.00,
                       dt.date(2026, 2 + i, 10), payee="Canva Teams",
                       account_label="Marketing - Software", came=True, scan_state="approved"))
        s.add(_txn(tid, biz["ulrg"], "r-ulrg", "Purchase", "canva_new", 389.00,
                   dt.date(2026, 6, 11), payee="Canva Teams", account_label="Marketing - Software"))
        await s.commit()
    await run_scan_ctx()
    row = await _get("canva_new")
    assert row.scan_state == "cleared"
    assert row.suggestion["category"] == "Marketing - Software"
    assert "4 prior" in row.suggestion["reason"]


async def test_known_vendor_over_band_needs_approval():
    tid, biz = await _ctx()
    async with SessionLocal() as s:
        for i in range(4):
            s.add(_txn(tid, biz["ulrg"], "r-ulrg", "Purchase", f"adobe{i}", 200.00,
                       dt.date(2026, 2 + i, 8), payee="Adobe Inc",
                       account_label="Marketing - Software", came=True, scan_state="approved"))
        s.add(_txn(tid, biz["ulrg"], "r-ulrg", "Purchase", "adobe_spike", 700.00,   # 3.5x band
                   dt.date(2026, 6, 9), payee="Adobe Inc", account_label="Marketing - Software"))
        await s.commit()
    await run_scan_ctx()
    row = await _get("adobe_spike")
    assert row.scan_state == "needs_approval"
    assert row.flags.get("over_band") is True
    assert row.suggestion["category"] == "Marketing - Software"


async def test_multi_line_forced_needs_approval():
    tid, biz = await _ctx()
    async with SessionLocal() as s:
        s.add(_txn(tid, biz["ulrg"], "r-ulrg", "Purchase", "split1", 900.00,
                   dt.date(2026, 6, 12), payee="Costco", account_label="Office Supplies",
                   flags={"multi_line": True}))
        await s.commit()
    await run_scan_ctx()
    row = await _get("split1")
    assert row.scan_state == "needs_approval"
    assert row.suggestion is not None


async def test_intercompany_matched_no_rule_escalates():
    tid, biz = await _ctx()
    async with SessionLocal() as s:                     # springb -> ulrg, no covering rule
        s.add(_txn(tid, biz["springb"], "r-springb", "Transfer", "ic_nr_t", 4201.00,
                   dt.date(2026, 6, 10)))
        s.add(_txn(tid, biz["ulrg"], "r-ulrg", "Deposit", "ic_nr_d", 4201.00, dt.date(2026, 6, 11)))
        await s.commit()
    await run_scan_ctx()
    t, d = await _get("ic_nr_t"), await _get("ic_nr_d")
    assert t.scan_state == "escalated" and d.scan_state == "escalated"
    assert t.flags.get("intercompany") is True
    async with SessionLocal() as s:
        link = (await s.execute(select(ICLink).where(ICLink.from_txn_id == t.id))).scalar_one()
        assert link.status == "escalated" and link.to_txn_id == d.id


async def test_intercompany_matched_rule_auto_ties():
    tid, biz = await _ctx()
    async with SessionLocal() as s:
        await create_ic_rule(s, tid, "Office rent ULRG->Sympli", "rent",
                             from_business_id=biz["ulrg"], to_business_id=biz["sympli"])
        s.add(_txn(tid, biz["ulrg"], "r-ulrg", "Transfer", "ic_ok_t", 3101.00, dt.date(2026, 6, 12)))
        s.add(_txn(tid, biz["sympli"], "r-sympli", "Deposit", "ic_ok_d", 3101.00, dt.date(2026, 6, 13)))
        await s.commit()
    await run_scan_ctx()
    t, d = await _get("ic_ok_t"), await _get("ic_ok_d")
    assert t.scan_state == "cleared" and d.scan_state == "cleared"
    async with SessionLocal() as s:
        link = (await s.execute(select(ICLink).where(ICLink.from_txn_id == t.id))).scalar_one()
        assert link.status == "auto_tied" and link.characterization == "rent" and link.rule_id


async def test_intercompany_monthly_cap_escalates_second():
    tid, biz = await _ctx()
    async with SessionLocal() as s:                     # sympli -> springb, cap $5,000/mo
        await create_ic_rule(s, tid, "Co-op cap", "shared_expense",
                             from_business_id=biz["sympli"], to_business_id=biz["springb"],
                             monthly_cap=Decimal("5000"))
        s.add(_txn(tid, biz["sympli"], "r-sympli", "Transfer", "cap_t1", 3050.00, dt.date(2026, 6, 3)))
        s.add(_txn(tid, biz["springb"], "r-springb", "Deposit", "cap_d1", 3050.00, dt.date(2026, 6, 4)))
        s.add(_txn(tid, biz["sympli"], "r-sympli", "Transfer", "cap_t2", 3050.00, dt.date(2026, 6, 18)))
        s.add(_txn(tid, biz["springb"], "r-springb", "Deposit", "cap_d2", 3050.00, dt.date(2026, 6, 19)))
        await s.commit()
    await run_scan_ctx()
    assert (await _get("cap_t1")).scan_state == "cleared"        # first within cap
    assert (await _get("cap_t2")).scan_state == "escalated"      # second pushes over $5,000


async def test_seed_ic_rules_is_inactive_and_idempotent():
    tid, _ = await _ctx()
    async with SessionLocal() as s:
        first = await seed_ic_rules(s, tid)
        assert len(first) == 3 and all(r.active is False for r in first)   # placeholders don't gate
    async with SessionLocal() as s:
        again = await seed_ic_rules(s, tid)
        assert again == []                                                  # idempotent by label


# run_scan pinned to a deterministic "today" so the 12-month history window is stable
async def run_scan_ctx():
    tid, _ = await _ctx()
    async with SessionLocal() as s:
        await run_scan(s, tid, today=TODAY)
