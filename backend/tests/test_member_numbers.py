"""An agent's own week, and which agent a portal member is.

`numbers` in the config payload was five zeroes in `_default_config` -- the same block for every
workspace, computed from nothing, so every agent who opened My Numbers saw a page of noughts. These
cover the two pieces that replace it: resolving a member to their CRM agent rows, and counting their
last seven days off the deals the Sisu sync already holds.

The distinction worth defending is `sisu_connected` vs the counts. A quiet week and an unmatched
email both produce zeroes, and only one of them is a problem somebody can fix.
"""
import datetime as dt

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (Agent, Business, Domain, IntranetMember, IntranetRole, Tenant,
                        Transaction, User)
from app.security import hash_pw
from app.services import member_identity, member_numbers as mn


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


async def _workspace(slug: str, *, member_email=None, agent_email="ada@crm.test",
                     override=None):
    """One member and one Sisu agent, whose emails the caller decides to match or not."""
    now = dt.datetime.now(dt.timezone.utc)
    member_email = member_email or f"ada@{slug}.test"
    async with SessionLocal() as s:
        t = Tenant(slug=slug, name=slug.title(), status="active", plan="portfolio",
                   config={"features": {"intranet": True}})
        s.add(t)
        await s.flush()
        s.add(Domain(tenant_id=t.id, hostname=f"{slug}.localhost", is_primary=True))
        b = Business(tenant_id=t.id, key="main", name="Main", tag="Business", sort_order=0)
        s.add(b)
        u = User(tenant_id=t.id, email=member_email, name="Ada", password_hash=hash_pw("pw"),
                 role="member", status="active", tab_access=[], token_version=0)
        s.add(u)
        await s.flush()
        role = IntranetRole(tenant_id=t.id, key="agent", name="Agent", sort=1, published_at=now)
        s.add(role)
        await s.flush()
        member = IntranetMember(tenant_id=t.id, full_name="Ada", email=member_email,
                                role_id=role.id, status="Active", auth_source="Manual",
                                user_id=u.id, agent_email=override)
        agent = Agent(tenant_id=t.id, business_id=b.id, source="sisu", external_id="A1",
                      name="Ada", email=agent_email, is_active=True)
        s.add_all([member, agent])
        await s.commit()
        return {"tenant_id": t.id, "member_id": member.id, "agent_id": agent.id,
                "business_id": b.id}


async def _deal(ws, **dates):
    async with SessionLocal() as s:
        s.add(Transaction(tenant_id=ws["tenant_id"], business_id=ws["business_id"],
                          source="sisu", external_id=f"T{dates.get('external', '1')}",
                          status=dates.pop("status", "pending"), agent_id=ws["agent_id"],
                          **{k: v for k, v in dates.items() if k != "external"}))
        await s.commit()


async def _member(ws):
    async with SessionLocal() as s:
        return await s.get(IntranetMember, ws["member_id"])


# ── which agent is this member ────────────────────────────────────────────────────────────

async def test_a_member_is_matched_to_their_agent_by_email():
    ws = await _workspace("mnmatch", member_email="ada@mnmatch.test", agent_email="ada@mnmatch.test")
    async with SessionLocal() as s:
        found = await member_identity.agents_for(s, ws["tenant_id"], await _member(ws))
    assert set(found) == {"sisu"}
    assert found["sisu"].id == ws["agent_id"]


async def test_an_unmatched_member_resolves_to_nothing_rather_than_a_guess():
    """An ops admin or a JV partner is not in the CRM at all, and inventing a match for them would
    put somebody else's deals on their page."""
    ws = await _workspace("mnnomatch", member_email="ops@mnnomatch.test",
                          agent_email="someone@else.test")
    async with SessionLocal() as s:
        assert await member_identity.agents_for(s, ws["tenant_id"], await _member(ws)) == {}


async def test_the_override_matches_a_member_whose_crm_email_differs():
    """The reason the column exists: joined under an old address, or the CRM has their personal
    one. Without it they see zeroes on their own numbers and cannot say why."""
    ws = await _workspace("mnoverride", member_email="ada.new@mnoverride.test",
                          agent_email="ada.old@mnoverride.test",
                          override="ada.old@mnoverride.test")
    async with SessionLocal() as s:
        found = await member_identity.agents_for(s, ws["tenant_id"], await _member(ws))
    assert found["sisu"].id == ws["agent_id"]


async def test_a_member_with_no_email_at_all_is_not_matched_to_everyone():
    """The empty string must not match agents whose email is blank -- that would hand one person
    the whole brokerage's numbers."""
    assert member_identity.addresses(None) == []
    ws = await _workspace("mnblank", member_email="x@mnblank.test", agent_email="")
    async with SessionLocal() as s:
        assert await member_identity.agents_for(s, ws["tenant_id"], await _member(ws)) == {}


async def test_matching_never_crosses_a_workspace():
    a = await _workspace("mncrossa", member_email="same@shared.test", agent_email="same@shared.test")
    b = await _workspace("mncrossb", member_email="same@shared.test", agent_email="same@shared.test")
    async with SessionLocal() as s:
        found = await member_identity.agents_for(s, b["tenant_id"], await _member(a))
    assert found["sisu"].id == b["agent_id"], "a member saw another workspace's agent"


# ── the week ──────────────────────────────────────────────────────────────────────────────

async def test_the_week_counts_only_the_last_seven_days():
    today = dt.date(2026, 9, 8)
    ws = await _workspace("mnweek", member_email="ada@mnweek.test", agent_email="ada@mnweek.test")
    await _deal(ws, external="in", appt_set_date=today - dt.timedelta(days=2))
    await _deal(ws, external="edge", appt_set_date=today - dt.timedelta(days=7))
    await _deal(ws, external="out", appt_set_date=today - dt.timedelta(days=30))

    async with SessionLocal() as s:
        week = await mn.week_for(s, ws["tenant_id"], await _member(ws), today)
    assert week["appointments_set"] == 2, "the window is not seven days"
    assert week["window_days"] == 7
    assert week["since"] == (today - dt.timedelta(days=7)).isoformat()


async def test_each_figure_counts_its_own_date():
    today = dt.date(2026, 9, 8)
    ws = await _workspace("mnfigs", member_email="ada@mnfigs.test", agent_email="ada@mnfigs.test")
    await _deal(ws, external="a", appt_set_date=today, appt_met_date=today, contract_date=today)
    await _deal(ws, external="b", appt_set_date=today)

    async with SessionLocal() as s:
        week = await mn.week_for(s, ws["tenant_id"], await _member(ws), today)
    assert (week["appointments_set"], week["appointments_held"], week["new_contracts"]) == (2, 1, 1)


async def test_an_unmatched_member_is_reported_as_unconnected_not_as_a_quiet_week():
    """The distinction the panel is built on. Zeroes for a real agent are their week; zeroes for
    an unmatched email are a configuration problem, and only one is worth surfacing."""
    ws = await _workspace("mnunconn", member_email="ops@mnunconn.test", agent_email="a@b.test")
    async with SessionLocal() as s:
        week = await mn.week_for(s, ws["tenant_id"], await _member(ws))
    assert week["sisu_connected"] is False
    assert week["appointments_set"] == 0

    ws2 = await _workspace("mnquiet", member_email="ada@mnquiet.test", agent_email="ada@mnquiet.test")
    async with SessionLocal() as s:
        quiet = await mn.week_for(s, ws2["tenant_id"], await _member(ws2))
    assert quiet["sisu_connected"] is True
    assert quiet["appointments_set"] == 0


async def test_closed_units_are_year_to_date_and_actually_closed():
    """The numerator of pace, so it has to be the year rather than the week -- and it counts the
    way the dashboard counts, or two surfaces in one product disagree about "closed"."""
    today = dt.date(2026, 9, 8)
    ws = await _workspace("mnclosed", member_email="ada@mnclosed.test",
                          agent_email="ada@mnclosed.test")
    await _deal(ws, external="c1", status="closed", close_date=dt.date(2026, 2, 1))
    await _deal(ws, external="c2", status="closed", close_date=dt.date(2026, 8, 1))
    await _deal(ws, external="lastyear", status="closed", close_date=dt.date(2025, 12, 1))
    # A pending deal with a close date is a projection, not a closing.
    await _deal(ws, external="pending", status="pending", close_date=dt.date(2026, 3, 1))

    async with SessionLocal() as s:
        week = await mn.week_for(s, ws["tenant_id"], await _member(ws), today)
    assert week["closed_units_ytd"] == 2


async def test_conversations_are_absent_rather_than_zero_until_there_is_a_source():
    """Nothing syncs call activity yet. Reporting 0 would say "you spoke to nobody" -- a claim
    about their week rather than about our integrations."""
    ws = await _workspace("mnconv", member_email="ada@mnconv.test", agent_email="ada@mnconv.test")
    async with SessionLocal() as s:
        week = await mn.week_for(s, ws["tenant_id"], await _member(ws))
    assert week["conversations_logged"] is None


# ── pace ──────────────────────────────────────────────────────────────────────────────────

def test_pace_is_against_the_year_elapsed_not_the_goal_achieved():
    """Four by 31 March against twelve is 33% achieved and 135% of pace, and only the second one
    tells somebody whether to change anything this week."""
    assert mn.pace(4, 12, dt.date(2026, 3, 31)) == 135
    assert mn.pace(12, 12, dt.date(2026, 12, 31)) == 100


def test_no_goal_means_no_percentage_rather_than_a_confident_one():
    assert mn.pace(4, None) is None
    assert mn.pace(4, 0) is None


async def test_the_portal_payload_carries_the_live_week():
    """The end of the hardcoded zeroes: what an agent's own page actually reads."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.security import make_token

    today = dt.date.today()
    ws = await _workspace("mnpayload", member_email="ada@mnpayload.test",
                          agent_email="ada@mnpayload.test")
    await _deal(ws, external="p", appt_set_date=today, appt_met_date=today)
    async with SessionLocal() as s:
        t = await s.get(Tenant, ws["tenant_id"])
        cfg = dict(t.config or {})
        cfg.setdefault("intranet", {})["numbers"] = {"annual_unit_goal": 12}
        t.config = cfg
        u = (await s.execute(select(User).where(User.tenant_id == t.id))).scalars().first()
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(t, "config")
        await s.commit()
        token = make_token(u.id, t.id, 0)

    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://testserver") as c:
        r = await c.get("/api/v1/intranet/config",
                        headers={"Authorization": f"Bearer {token}",
                                 "x-tenant-host": "mnpayload.localhost"})
    assert r.status_code == 200, r.text
    numbers = r.json()["config"]["numbers"]
    assert numbers["sisu_connected"] is True
    assert numbers["appointments_set"] == 1
    assert numbers["appointments_held"] == 1
    assert numbers["annual_unit_goal"] == 12
    assert "pace_percent" in numbers
