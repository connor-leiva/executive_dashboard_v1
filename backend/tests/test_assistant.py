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


def test_drill_tool_advertises_roster_fields():
    """The roster drawer's per-member fields (Stripe account, brokerage) are only in the
    roster drills; the tool must name them or the model drills active_members and gives up."""
    from app.services.assistant import DRILL_TOOL
    desc = DRILL_TOOL["description"]
    assert "forum_roster" in desc and "stripe_account" in desc


def test_tool_result_cut_by_rows_not_characters():
    import json
    from app.services.assistant import _fit_detail, TOOL_RESULT_CHARS
    rows = [{"name": f"Member {i}", "stripe_account": "Legacy SB Account", "pad": "x" * 400,
             "source_url": "https://app.gohighlevel.com/" + "y" * 200,
             "last_payment": {"date": "2026-01-01", "amount": 1, "url": "https://stripe/" + "z" * 200}}
            for i in range(250)]
    out = _fit_detail({"label": "Roster", "rows": rows})
    s = json.dumps(out)
    json.loads(s)                                                 # still valid JSON
    assert len(s) <= TOOL_RESULT_CHARS + 500
    assert out["rows_truncated_from"] == 250 and "note" in out
    assert "source_url" not in out["rows"][0] and "url" not in out["rows"][0]["last_payment"]
    small = _fit_detail({"rows": rows[:5]})
    assert len(small["rows"]) == 5 and "rows_truncated_from" not in small
