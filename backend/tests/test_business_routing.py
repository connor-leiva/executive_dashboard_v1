"""QBO entity routing — Business financial-entity model + page routing.

Stage 0: the new Business columns (kind / display_tab / include_in_portfolio) exist,
default correctly, and the seed backfills the existing three entities to reproduce
today's behavior. Later stages add read-layer / financials / endpoint tests here.
"""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select, delete

from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant, Business, PLSnapshot, User, BookTxn, PLLine, ICLink


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _biz():
    async with SessionLocal() as s:
        rows = (await s.execute(select(Business))).scalars().all()
        return {b.key: b for b in rows}


async def test_new_columns_default_and_backfill():
    b = await _biz()
    # ulrg keeps the real_estate default; display_tab NULL means "use own key"
    assert b["ulrg"].kind == "real_estate"
    assert b["ulrg"].display_tab is None
    # explicit backfills reproduce current behavior
    assert b["sympli"].kind == "commission_jv"
    assert b["springb"].kind == "membership"
    assert b["springb"].display_tab == "forum"   # springb's P&L renders on the forum tab today
    # nothing is excluded from the portfolio yet (springb flips only at the Stage 6 cutover)
    assert all(x.include_in_portfolio for x in b.values())


async def test_display_tab_routing_and_portfolio_gating():
    """A financial entity: routes its P&L to a chosen page, merges into an existing
    page, and — when include_in_portfolio is False — shows on its own page but stays
    out of the portfolio totals. tenant_tabs + biz_tab_map surface the new pages."""
    from app.services.metrics import build_dashboard, _pl_period
    from app.services.tabs import tenant_tabs, biz_tab_map

    ps, pe = _pl_period("mtd")
    specs = [   # key, display_tab, include_in_portfolio, revenue, noi, sort_order
        ("coaching", "coaching", True, 50000, 10000, 10),   # → a brand-new page
        ("extra_forum", "forum", True, 7000, 1000, 11),     # → merges into the forum page
        ("holdco", "holdco", False, 99999, 99999, 12),      # shown, but out of portfolio
    ]
    added = []
    async with SessionLocal() as s:
        tid = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id
        for key, disp, inc, rev, noi, so in specs:
            b = Business(tenant_id=tid, key=key, name=key.title(), tag="x", kind="membership",
                         display_tab=disp, include_in_portfolio=inc, sort_order=so, config={})
            s.add(b)
            await s.flush()
            s.add(PLSnapshot(tenant_id=tid, business_id=b.id, period_start=ps, period_end=pe,
                             source="qbo", revenue=Decimal(rev), noi=Decimal(noi),
                             gross_profit=Decimal(rev), opex=Decimal(rev - noi)))
            added.append(b.id)
        await s.commit()
    try:
        async with SessionLocal() as s:
            tabs = await tenant_tabs(s, tid)
            assert "coaching" in tabs and "holdco" in tabs
            assert (await biz_tab_map(s, tid))["extra_forum"] == "forum"

            d = (await build_dashboard(s, tid, "mtd")).model_dump()
            areas = d["areas"]
            assert areas["coaching"]["revenue"] == 50000               # routed to a new page
            assert areas["forum"]["revenue"] == 75000                  # springb 68000 + 7000 merged
            assert areas["holdco"]["revenue"] == 99999                 # shown on its own page
            assert d["portfolio"]["revenue"] == 697000                 # 640000 + 50000 + 7000 (holdco excluded)
            comp = {c["key"]: c["revenue"] for c in d["portfolio"]["composition"]}
            assert comp.get("forum") == 75000 and "holdco" not in comp  # merged once, excluded stays out
    finally:
        async with SessionLocal() as s:
            for bid in added:
                await s.execute(delete(PLSnapshot).where(PLSnapshot.business_id == bid))
                await s.execute(delete(Business).where(Business.id == bid))
            await s.commit()


async def test_membership_entity_financials_are_booked_only():
    """A membership entity's /financials is its QBO Booked P&L alone — no Sisu-shaped
    Live/Projection lenses computed off zero transactions."""
    from app.services.financials import compute_financials
    from app.services.metrics import _pl_period

    ps, pe = _pl_period("mtd")
    async with SessionLocal() as s:
        tid = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id
        b = Business(tenant_id=tid, key="coachco", name="Coaching Co", tag="x", kind="membership",
                     display_tab="coachco", include_in_portfolio=True, sort_order=20, config={})
        s.add(b)
        await s.flush()
        bid = b.id
        s.add(PLSnapshot(tenant_id=tid, business_id=bid, period_start=ps, period_end=pe, source="qbo",
                         revenue=Decimal(30000), cogs=Decimal(5000), gross_profit=Decimal(25000),
                         opex=Decimal(9000), noi=Decimal(16000), books_closed=True))
        await s.commit()
    try:
        async with SessionLocal() as s:
            b = (await s.execute(select(Business).where(Business.id == bid))).scalar_one()
            f = await compute_financials(s, b.tenant_id, b, "mtd")
            assert set(f["lenses"]) == {"booked"}          # no live / projection
            assert f["reconciliation"] is None
            booked = f["lenses"]["booked"]
            assert booked["profit"] == 16000               # noi
            assert next(r for r in booked["rows"] if r["key"] == "revenue")["v"] == 30000.0
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(PLSnapshot).where(PLSnapshot.business_id == bid))
            await s.execute(delete(Business).where(Business.id == bid))
            await s.commit()


async def _owner(s):
    return (await s.execute(select(User).where(User.role == "owner"))).scalars().first()


async def test_qbo_entity_create_reroute_delete_guards_bookkeeping():
    """The self-service lifecycle: create a routed entity, re-route it (display_tab only —
    its BookTxn business_id must NOT move), then delete it (purges ledger + open IC links)."""
    from app.routers.integrations import create_qbo_entity, update_qbo_entity, delete_qbo_entity

    async with SessionLocal() as s:
        owner = await _owner(s)
        tid = owner.tenant_id
        r = await create_qbo_entity(
            {"name": "The Forum QBO", "display_tab": "forum", "kind": "membership", "books_enabled": True}, owner, s)
        key = r["business_key"]
    assert r["display_tab"] == "forum"

    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == key))).scalar_one()
        bid = biz.id
        assert biz.kind == "membership" and biz.display_tab == "forum" and biz.include_in_portfolio is True
        assert biz.config["books_enabled"] is True and biz.config["books_onboarding"] is True
        # simulate a sync having landed a txn + P&L line + an open intercompany escalation
        s.add(BookTxn(tenant_id=tid, business_id=bid, realm_id="R1", qbo_type="Purchase", qbo_id="P1",
                      txn_date=dt.date(2026, 7, 1), amount=Decimal("100")))
        s.add(PLLine(tenant_id=tid, business_id=bid, period_start=dt.date(2026, 7, 1),
                     period_end=dt.date(2026, 7, 31), section="expense", label="Software", amount=Decimal("100")))
        s.add(ICLink(tenant_id=tid, from_business_id=bid, to_business_id=bid, amount=Decimal("100"),
                     occurred_on=dt.date(2026, 7, 1), status="escalated"))
        await s.commit()

    async with SessionLocal() as s:
        await update_qbo_entity(key, {"display_tab": "becollective", "include_in_portfolio": False}, await _owner(s), s)
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == key))).scalar_one()
        assert biz.display_tab == "becollective" and biz.include_in_portfolio is False
        tx = (await s.execute(select(BookTxn).where(BookTxn.business_id == bid))).scalar_one()
        assert tx.business_id == bid and tx.realm_id == "R1"   # reroute never moved the ledger keys

    async with SessionLocal() as s:
        await delete_qbo_entity(key, await _owner(s), s)
    async with SessionLocal() as s:
        assert (await s.execute(select(Business).where(Business.key == key))).scalar_one_or_none() is None
        assert (await s.execute(select(BookTxn).where(BookTxn.business_id == bid))).scalars().first() is None
        assert (await s.execute(select(PLLine).where(PLLine.business_id == bid))).scalars().first() is None
        assert (await s.execute(select(ICLink).where(ICLink.from_business_id == bid))).scalars().first() is None


async def test_books_enabled_gates_the_sync(monkeypatch):
    """A QBO entity with books_enabled=False still syncs its P&L snapshot but is NOT
    ingested into the Books ledger; flipping it on runs the Books sync."""
    import app.services.sync as syncmod
    import app.services.books_sync as bsmod
    from app.models import Integration

    calls = {"pl": 0, "books": 0}

    async def fake_pl(s, tid, integ):
        calls["pl"] += 1
        return 0

    async def fake_books(s, tid, integ, since=None):
        calls["books"] += 1

    monkeypatch.setattr(syncmod, "sync_qbo_pl", fake_pl)
    monkeypatch.setattr(bsmod, "run_books_syncs", fake_books)

    async with SessionLocal() as s:
        tid = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id
        b = Business(tenant_id=tid, key="gateco", name="Gate Co", tag="x", kind="membership",
                     display_tab="gateco", sort_order=30, config={"books_enabled": False})
        s.add(b)
        await s.flush()
        bid = b.id
        integ = Integration(tenant_id=tid, provider="qbo", business_id=bid, status="connected", realm_id="RG")
        s.add(integ)
        await s.flush()
        iid = integ.id
        await syncmod._sync_integration(s, tid, integ, "2026-07-01", "2026-07-31")
    assert calls == {"pl": 1, "books": 0}                  # disabled → Books skipped

    async with SessionLocal() as s:
        b = (await s.execute(select(Business).where(Business.id == bid))).scalar_one()
        b.config = {"books_enabled": True}
        await s.commit()
    async with SessionLocal() as s:
        integ = (await s.execute(select(Integration).where(Integration.id == iid))).scalar_one()
        await syncmod._sync_integration(s, tid, integ, "2026-07-01", "2026-07-31")
    assert calls == {"pl": 2, "books": 1}                  # enabled → Books ran

    async with SessionLocal() as s:
        await s.execute(delete(Integration).where(Integration.id == iid))
        await s.execute(delete(Business).where(Business.id == bid))
        await s.commit()


async def test_ic_onboarding_surfaces_but_does_not_block_close():
    """An onboarding entity's escalated intercompany link shows in the IC tile but does
    not flip `blocking` — a fresh realm can't freeze the close before rules are set."""
    from app.services.books import build_books_home

    async with SessionLocal() as s:
        tid = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id
        onb = Business(tenant_id=tid, key="onbco", name="Onb Co", tag="x", kind="membership",
                       display_tab="onbco", sort_order=31, config={"books_onboarding": True})
        s.add(onb)
        await s.flush()
        oid = onb.id
        s.add(ICLink(tenant_id=tid, from_business_id=oid, to_business_id=oid, amount=Decimal("500"),
                     occurred_on=dt.date(2026, 7, 1), status="escalated"))
        await s.commit()
    try:
        async with SessionLocal() as s:
            ic = (await build_books_home(s, tid, "mtd"))["tiles"]["ic"]
            assert ic["blocking"] is False and ic["open"] >= 1   # surfaced, not blocking
        # a non-onboarding escalation DOES block
        async with SessionLocal() as s:
            norm = Business(tenant_id=tid, key="normco", name="Norm Co", tag="x", kind="membership",
                            display_tab="normco", sort_order=32, config={})
            s.add(norm)
            await s.flush()
            nid = norm.id
            s.add(ICLink(tenant_id=tid, from_business_id=nid, to_business_id=nid, amount=Decimal("700"),
                         occurred_on=dt.date(2026, 7, 1), status="escalated"))
            await s.commit()
        async with SessionLocal() as s:
            assert (await build_books_home(s, tid, "mtd"))["tiles"]["ic"]["blocking"] is True
    finally:
        async with SessionLocal() as s:
            for k in ("onbco", "normco"):
                row = (await s.execute(select(Business).where(Business.key == k))).scalar_one_or_none()
                if row:
                    await s.execute(delete(ICLink).where(ICLink.from_business_id == row.id))
                    await s.execute(delete(Business).where(Business.id == row.id))
            await s.commit()
