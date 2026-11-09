"""The Portfolio scorecard: a WORKSPACE board (groups with no business) for the owners' Monday review.

Isolation (it never leaks onto a per-business board, and theirs never onto it), owner/admin-only access,
the as-of-now month figures answering only for the current week, per-company QuickBooks profit via a
parameterised resolver key, and the setup script."""
import datetime as dt
import sys
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal, engine
from app.main import app
from app.models import (Base, Business, Integration, MetricRecord, PLSnapshot, ScorecardGroup,
                        ScorecardMetric, Tenant, Transaction, User)
from app.security import hash_pw, make_token
from app.seed import seed
from app.seed_ulrg_scorecard import load_ulrg_scorecard
from app.services import roles
from app.services import scorecard_portfolio as P
from app.services import scorecard_resolvers as R

TRANSPORT = ASGITransport(app=app)


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(t):
    return {"Authorization": f"Bearer {t}"}


def _this_week():
    today = P._today()
    ws = today - dt.timedelta(days=today.weekday())
    return ws, ws + dt.timedelta(days=6)


@pytest.fixture
async def env():
    """A fresh tenant: a brokerage with live Sisu, two membership companies with live GHL."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        t = Tenant(slug=f"pf-{uuid.uuid4().hex[:8]}", name="Portfolio Test")
        s.add(t); await s.flush()
        re_ = Business(tenant_id=t.id, key=f"re{uuid.uuid4().hex[:6]}", name="Brokerage", tag="re",
                       kind=roles.REAL_ESTATE, sort_order=0)
        mb = Business(tenant_id=t.id, key=f"mb{uuid.uuid4().hex[:6]}", name="Coaching", tag="mb",
                      kind=roles.MEMBERSHIP, sort_order=1)
        mb2 = Business(tenant_id=t.id, key=f"mc{uuid.uuid4().hex[:6]}", name="Club", tag="mc",
                       kind=roles.MEMBERSHIP, sort_order=2)
        s.add_all([re_, mb, mb2]); await s.flush()
        now = dt.datetime.now(dt.timezone.utc)
        s.add_all([Integration(tenant_id=t.id, provider="sisu", business_id=re_.id, status="connected",
                               last_synced_at=now),
                   Integration(tenant_id=t.id, provider="ghl", business_id=mb.id, status="connected",
                               last_synced_at=now)])
        await s.commit()
        yield {"tid": t.id, "slug": t.slug, "re": re_.id, "mb": mb.id, "mb_key": mb.key, "mb2_key": mb2.key}


async def test_month_figures_answer_only_for_the_current_week(env):
    ws, we = _this_week()
    today = P._today()
    month_start = today.replace(day=1)
    month_end = (month_start + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    async with SessionLocal() as s:
        s.add_all([
            Transaction(tenant_id=env["tid"], business_id=env["re"], source="sisu", external_id="c1",
                        status="closed", close_date=month_start, sale_price=400000, gci=12000),
            Transaction(tenant_id=env["tid"], business_id=env["re"], source="sisu", external_id="p1",
                        status="pending", expected_close_date=month_end, sale_price=300000, gci=9000),
            Transaction(tenant_id=env["tid"], business_id=env["re"], source="sisu", external_id="p2",
                        status="pending", expected_close_date=month_end + dt.timedelta(days=5),
                        sale_price=300000, gci=9000),                       # next month: not counted
        ])
        s.add_all([MetricRecord(tenant_id=env["tid"], business_id=env["mb"], source="ghl", kind="member",
                                status="active", external_id=f"m{i}") for i in range(3)])
        await s.commit()
        assert await P.ulrg_units(s, env["tid"], None, ws, we) == 2.0          # 1 closed + 1 pending
        assert await P.ulrg_gci(s, env["tid"], None, ws, we) == 21000.0
        assert await P.forum_members(s, env["tid"], None, ws, we) == 3.0
        # a closed week can't be re-read from today's pipeline: it is left alone, never overwritten
        last = (ws - dt.timedelta(days=7), we - dt.timedelta(days=7))
        assert await P.ulrg_units(s, env["tid"], None, *last) is R.UNAVAILABLE
        assert await P.forum_members(s, env["tid"], None, *last) is R.UNAVAILABLE


async def test_company_profit_reads_this_months_quickbooks_snapshot(env):
    ws, we = _this_week()
    today = P._today()
    month_start = today.replace(day=1)
    month_end = (month_start + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    key = f"portfolio_entity_profit:{env['mb_key']}"
    assert key in R.RESOLVERS and "portfolio_nope:x" not in R.RESOLVERS
    async with SessionLocal() as s:
        # no snapshot yet this month: unknown, never a $0
        assert await R.RESOLVERS[key](s, env["tid"], None, ws, we) is R.UNAVAILABLE
        s.add(PLSnapshot(tenant_id=env["tid"], business_id=env["mb"], period_start=month_start,
                         period_end=month_end, revenue=Decimal("5000"), opex=Decimal("7500"),
                         noi=Decimal("-2500"), net_income=Decimal("-2500")))
        await s.commit()
        assert await R.RESOLVERS[key](s, env["tid"], None, ws, we) == -2500.0
        # the OTHER company's row reads its own snapshot (none) — the key names the company
        assert await R.RESOLVERS[f"portfolio_entity_profit:{env['mb2_key']}"](s, env["tid"], None, ws, we) is R.UNAVAILABLE


async def test_setup_script_builds_the_board_once(env, monkeypatch):
    from scripts import setup_portfolio_scorecard as S
    monkeypatch.setattr(sys, "argv", ["x", "--tenant", env["slug"], "--apply"])
    assert await S.main() == 0
    assert await S.main() == 0                                          # re-run: nothing added
    async with SessionLocal() as s:
        groups = (await s.execute(select(ScorecardGroup).where(
            ScorecardGroup.tenant_id == env["tid"]))).scalars().all()
        assert [g.key for g in sorted(groups, key=lambda g: g.sort_order)] == \
            ["utah_life", "spring_b", "sympli", "activated", "ai"]
        assert all(g.business_id is None and not g.is_team_room for g in groups)
        rows = (await s.execute(select(ScorecardMetric).where(ScorecardMetric.tenant_id == env["tid"]))).scalars().all()
        names = {m.name: m for m in rows}
        assert len(rows) == 4 + (2 + 3 + 2) + 2                         # ULRG + Spring B (2 companies) + AI
        assert names["Coaching Profit (Month to Date)"].resolver_key == f"portfolio_entity_profit:{env['mb_key']}"
        assert float(names["Units Closed + Pending (Month)"].goal) == 60
        assert names["VIP Guests Committed: Justin"].resolver_key is None
        assert all(m.resolver_key is None or m.resolver_key in R.RESOLVERS for m in rows)


# ── the API: isolation and access, on the seeded workspace ──────────────────────────────────────

@pytest.fixture
async def seeded():
    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        await load_ulrg_scorecard(s, t.id)
        g = ScorecardGroup(tenant_id=t.id, business_id=None, key="utah_life", name="Utah Life",
                           is_team_room=False, sort_order=0, active=True)
        s.add(g); await s.flush()
        m = ScorecardMetric(tenant_id=t.id, group_id=g.id, name="Foundations Series Attendance",
                            type="flow", direction="gte", goal=Decimal("0"), sort_order=1, active=True)
        s.add(m)
        member = User(tenant_id=t.id, email=f"pf-{uuid.uuid4().hex[:6]}@x.com", name="m",
                      password_hash=hash_pw("password123"), role="member", status="active",
                      tab_access=["portfolio", "ulrg"], token_version=0)
        owner = (await s.execute(select(User).where(User.tenant_id == t.id, User.role == "owner"))).scalars().first()
        s.add(member)
        await s.commit()
        yield {"tid": t.id, "metric": str(m.id), "member": make_token(member.id, t.id, 0),
               "owner": make_token(owner.id, t.id, owner.token_version or 0)}


async def test_workspace_board_is_owners_only_and_never_leaks(seeded):
    async with _client() as c:
        pf = await c.get("/api/v1/ulrg/scorecard?scope=portfolio", headers=_H(seeded["owner"]))
        assert pf.status_code == 200 and [g["key"] for g in pf.json()["groups"]] == ["utah_life"]
        ulrg = (await c.get("/api/v1/ulrg/scorecard?scope=ulrg", headers=_H(seeded["owner"]))).json()
        assert "utah_life" not in {g["key"] for g in ulrg["groups"]}          # no leak onto ULRG's board
        # a member who holds the Portfolio tab still can't open, edit or drill the owners' board
        assert (await c.get("/api/v1/ulrg/scorecard?scope=portfolio",
                            headers=_H(seeded["member"]))).status_code == 403
        wk = (dt.date.today() - dt.timedelta(days=dt.date.today().weekday())).isoformat()
        body = {"metric_id": seeded["metric"], "week_start": wk, "value": 12}
        assert (await c.post("/api/v1/ulrg/scorecard/values", headers=_H(seeded["member"]),
                             json=body)).status_code == 403
        assert (await c.post("/api/v1/ulrg/scorecard/values", headers=_H(seeded["owner"]),
                             json=body)).status_code == 201
        # the member's own boards are untouched
        assert (await c.get("/api/v1/ulrg/scorecard?scope=ulrg", headers=_H(seeded["member"]))).status_code == 200
