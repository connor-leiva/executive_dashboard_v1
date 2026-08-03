"""Ask-the-dashboard assistant — context packing.

The per-business three-lens financials (Live / Projection / Booked) must be in the
context so the assistant can answer Projection/forecast questions about the Financials
view the user is looking at — and must be scoped to the tabs a user is allowed to see.
(The live Claude call in `ask()` isn't exercised here — only the context builder.)
"""
import pytest
from sqlalchemy import select, delete

from app.seed import seed
from app.db import SessionLocal
from app.models import User
from app.services.assistant import _build_context


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _owner(s):
    return (await s.execute(select(User).where(User.role == "owner"))).scalars().first()


async def test_context_includes_three_lens_financials():
    async with SessionLocal() as s:
        ctx, tabs = await _build_context(s, await _owner(s), "mtd")
    fin = ctx["data"].get("financials", {})
    assert "ulrg" in fin and "sympli" in fin
    lenses = fin["ulrg"]["lenses"]
    assert {"live", "projection", "booked"} <= set(lenses)
    assert "profit" in lenses["projection"]                       # the Projected profit number
    keys = {r.get("key") for r in lenses["projection"]["rows"]}
    assert "fin_projected" in keys                                # per-deal GCI is drillable


async def test_member_financials_scoped_to_granted_tabs():
    """A member who can only see the Forum gets the Forum's financials (springb renders
    there) but NOT ulrg/sympli — the assistant can't leak numbers off their tabs."""
    async with SessionLocal() as s:
        tid = (await _owner(s)).tenant_id
        m = User(tenant_id=tid, email="ctxmember@x.com", password_hash="x", name="M",
                 role="member", tab_access=["forum"])
        s.add(m)
        await s.commit()
        mid = m.id
    try:
        async with SessionLocal() as s:
            m = (await s.execute(select(User).where(User.id == mid))).scalar_one()
            fin = (await _build_context(s, m, "mtd"))[0]["data"].get("financials", {})
        assert "springb" in fin                    # Forum's booked P&L (springb → forum tab)
        assert "ulrg" not in fin and "sympli" not in fin
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(User).where(User.id == mid))
            await s.commit()
