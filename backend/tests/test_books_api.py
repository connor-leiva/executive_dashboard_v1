"""Acumyn Books API tests (SPEC Part 8 test_books_api.py): rail-conservation invariant,
P&L consolidation + snapshot/lines tie, and queue-action permissions + audit trail.
(Close + review endpoints arrive with Step 7.)
"""
import datetime as dt
from decimal import Decimal

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func

from app.main import app
from app.seed import seed
from app.db import SessionLocal
from app.models import (Tenant, User, Business, BookTxn, PLLine, PLSnapshot, ICLink, AuditLog)
from app.security import make_token, hash_pw
from app.services.metrics import _pl_period
from app.services import books

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token):
    return {"Authorization": f"Bearer {token}"}


async def _ids():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        biz = {b.key: b.id for b in (await s.execute(
            select(Business).where(Business.tenant_id == t.id))).scalars().all()}
        return t.id, biz


async def _owner_token():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


async def _member(email, tabs):
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = User(tenant_id=t.id, email=email, name=email.split("@")[0], password_hash=hash_pw("x"),
                 role="member", status="active", tab_access=tabs, token_version=0)
        s.add(u)
        await s.commit()
        return make_token(u.id, t.id, 0)


def _txn(tid, bid, qid, state, amount=100, came=False, payee="V", label="Cat"):
    return BookTxn(tenant_id=tid, business_id=bid, realm_id="r-api", qbo_type="Purchase",
                   qbo_id=qid, txn_date=dt.date.today(), amount=Decimal(str(amount)),
                   payee=payee, account_label=label, came_categorized=came, scan_state=state)


# ── rail conservation (Part 7 #1) ─────────────────────────────────────────────
async def test_home_rail_conservation():
    tid, biz = await _ids()
    async with SessionLocal() as s:
        for i, st in enumerate(["cleared", "cleared", "needs_approval", "escalated", "pending"]):
            s.add(_txn(tid, biz["ulrg"], f"rail{i}", st, came=(i < 2)))
        await s.commit()
    tok = await _owner_token()
    async with _client() as c:
        r = await c.get("/api/v1/books?period=mtd", headers=_H(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["invariants_ok"] is True
    # captured partitions exactly into the scan states (pending via the service invariant)
    inv = None
    async with SessionLocal() as s:
        inv = await books.books_invariants(s, tid)
    rail = inv["rail"]
    assert rail["captured"] == rail["cleared"] + rail["needs_approval"] + rail["escalated"] + rail["pending"]
    assert inv["rail_conservation"] is True


# ── P&L consolidation + snapshot/lines tie (SPEC 4.2) ─────────────────────────
async def test_pl_tie_consolidation_and_jv():
    tid, biz = await _ids()
    ps, pe = _pl_period("mtd")
    async with SessionLocal() as s:
        b = Business(tenant_id=tid, key="bkco", name="BK Co", tag="test")
        s.add(b)
        await s.commit()
        s.add(PLSnapshot(tenant_id=tid, business_id=b.id, period_start=ps, period_end=pe,
                         revenue=Decimal("1000"), gross_profit=Decimal("1000"),
                         opex=Decimal("300"), noi=Decimal("700")))
        for lbl, amt, pos in [("Rev A", "600", 0), ("Rev B", "400", 1)]:
            s.add(PLLine(tenant_id=tid, business_id=b.id, period_start=ps, period_end=pe,
                         section="income", label=lbl, amount=Decimal(amt), position=pos))
        s.add(PLLine(tenant_id=tid, business_id=b.id, period_start=ps, period_end=pe,
                     section="expense", parent="60000 Occupancy", label="Rent",
                     amount=Decimal("300"), position=2))
        await s.commit()
    tok = await _owner_token()
    async with _client() as c:
        one = (await c.get("/api/v1/books/pl?business=bkco&period=mtd", headers=_H(tok))).json()
        allb = (await c.get("/api/v1/books/pl?business=all&period=mtd", headers=_H(tok))).json()
        sym = (await c.get("/api/v1/books/pl?business=sympli&period=mtd", headers=_H(tok))).json()

    assert one["totals"]["revenue"] == 1000.0                    # from PLSnapshot
    assert round(sum(r["v"] for r in one["revenue"]), 2) == 1000.0   # lines tie to snapshot
    assert one["opex"][0]["cat"] == "60000 Occupancy"
    assert "jv_share" not in one
    # consolidated total == sum of each entity's total (the merge is faithful)
    _, biz2 = await _ids()
    per = 0.0
    async with _client() as c:
        for key in biz2:
            per += (await c.get(f"/api/v1/books/pl?business={key}&period=mtd",
                                headers=_H(tok))).json()["totals"]["revenue"]
    assert abs(allb["totals"]["revenue"] - per) < 1.0
    assert "eliminations" in allb and "applied" in allb["eliminations"]
    assert "jv_share" in sym                                     # only for sympli


# ── queue actions: permissions + audit (SPEC 4.4 / Part 8) ────────────────────
async def test_queue_actions_permissions_and_audit():
    tid, biz = await _ids()
    async with SessionLocal() as s:
        s.add(_txn(tid, biz["ulrg"], "act_appr", "needs_approval", payee="Canva",
                   label="Marketing - Software"))
        await s.commit()
    member = await _member("bookkeeper@x.com", ["books"])
    outsider = await _member("noboooks@x.com", ["forum"])
    tx_id = await _txn_id("act_appr")

    async with _client() as c:
        # member WITHOUT the books tab is blocked from reads and mutations
        assert (await c.get("/api/v1/books", headers=_H(outsider))).status_code == 403
        assert (await c.post(f"/api/v1/books/txn/{tx_id}/approve", headers=_H(outsider))).status_code == 403
        # the bookkeeper (member + books tab) can approve
        r = await c.post(f"/api/v1/books/txn/{tx_id}/approve", headers=_H(member))
        assert r.status_code == 200 and r.json()["scan_state"] == "approved"

    # the approval is audited
    async with SessionLocal() as s:
        n = (await s.execute(select(func.count(AuditLog.id)).where(
            AuditLog.tenant_id == tid, AuditLog.action == "books.txn_approved"))).scalar_one()
        assert n >= 1


async def test_characterize_requires_cfo_and_flips_both_sides():
    tid, biz = await _ids()
    async with SessionLocal() as s:
        a = _txn(tid, biz["ulrg"], "ic_a", "escalated")
        b = _txn(tid, biz["sympli"], "ic_b", "escalated")
        s.add_all([a, b])
        await s.commit()
        link = ICLink(tenant_id=tid, from_business_id=biz["ulrg"], to_business_id=biz["sympli"],
                      from_txn_id=a.id, to_txn_id=b.id, amount=Decimal("2500"),
                      occurred_on=dt.date.today(), status="escalated")
        s.add(link)
        await s.commit()
        link_id, a_id, b_id = link.id, a.id, b.id

    member = await _member("bkmember@x.com", ["books"])
    owner = await _owner_token()
    body = {"characterization": "loan", "note": "due-to/from"}
    async with _client() as c:
        # a books-tab MEMBER cannot characterize (CFO seat)
        assert (await c.post(f"/api/v1/books/ic/{link_id}/characterize", json=body,
                             headers=_H(member))).status_code == 403
        # owner can, and both linked txns flip to approved
        r = await c.post(f"/api/v1/books/ic/{link_id}/characterize", json=body, headers=_H(owner))
        assert r.status_code == 200 and r.json()["status"] == "characterized"
    async with SessionLocal() as s:
        for tid_ in (a_id, b_id):
            t = (await s.execute(select(BookTxn).where(BookTxn.id == tid_))).scalar_one()
            assert t.scan_state == "approved" and t.decision["action"] == "ic_characterized"


async def _txn_id(qid):
    async with SessionLocal() as s:
        return (await s.execute(select(BookTxn.id).where(BookTxn.qbo_id == qid))).scalar_one()
