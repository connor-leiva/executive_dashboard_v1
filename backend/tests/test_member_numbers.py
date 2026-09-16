"""A member's own numbers, the team's, and which agent a portal member is.

`numbers` in the portal payload was five zeroes in `_default_config`, then half-replaced: the server
began sending real figures while My Numbers and the home cards kept reading names it never sent, so
an agent with a busy year still saw noughts -- and an owner, who has no agent row, saw nothing at
all, although the workspace's permission matrix already said owners see the team. These cover the
replacement: resolving a member to their CRM agent, counting their figures and the team's off the
deals the Sisu sync holds, who is shown the team's, and that the page reads what is sent.

The distinction worth defending is between three empties -- no Sisu in the workspace, no match for
this member, and a genuinely quiet week. Only the last one is about the person reading.
"""
import datetime as dt
import re
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm.attributes import flag_modified

from app.db import SessionLocal
from app.main import app
from app.models import (Agent, Business, Domain, Integration, IntranetCapability, IntranetMember,
                        IntranetPermission, IntranetRole, Tenant, Transaction, User)
from app.security import hash_pw, make_token
from app.services import member_identity, member_numbers as mn

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
# A day whose week sits inside its month, so "this week" and "this month" can disagree.
TODAY = dt.date(2026, 9, 20)


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


async def _workspace(slug: str, *, member_email=None, agent_email="ada@crm.test", override=None,
                     sisu="synced", user_role="member", team_level=None, on_roster=True):
    """One user and one Sisu agent, whose emails the caller decides to match or not.

    `sisu` is "synced", "connected" (never synced) or None (no connection at all). `team_level`
    writes the role's `team_production` permission; None defines no such capability. `on_roster`
    False leaves the user off the roster, as an owner who never added themselves is.
    """
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
                 role=user_role, status="active", tab_access=[], token_version=0)
        s.add(u)
        await s.flush()
        role = IntranetRole(tenant_id=t.id, key="agent", name="Agent", sort=1, published_at=now)
        s.add(role)
        await s.flush()
        if team_level is not None:
            cap = IntranetCapability(tenant_id=t.id, key="team_production",
                                     name="Team Production", description="Everyone else's numbers",
                                     sort=1, published_at=now)
            s.add(cap)
            await s.flush()
            s.add(IntranetPermission(tenant_id=t.id, capability_id=cap.id, role_id=role.id,
                                     level=team_level, published_at=now))
        member = None
        if on_roster:
            member = IntranetMember(tenant_id=t.id, full_name="Ada", email=member_email,
                                    role_id=role.id, status="Active", auth_source="Manual",
                                    user_id=u.id, agent_email=override)
            s.add(member)
        agent = Agent(tenant_id=t.id, business_id=b.id, source="sisu", external_id="A1",
                      name="Ada", email=agent_email, is_active=True)
        s.add(agent)
        if sisu:
            s.add(Integration(tenant_id=t.id, provider="sisu", business_id=b.id, status="connected",
                              last_synced_at=now if sisu == "synced" else None))
        await s.commit()
        return {"tenant_id": t.id, "business_id": b.id, "user_id": u.id, "agent_id": agent.id,
                "member_id": member.id if member is not None else None,
                "host": f"{slug}.localhost"}


async def _agent(ws, name: str, email: str, external: str):
    async with SessionLocal() as s:
        row = Agent(tenant_id=ws["tenant_id"], business_id=ws["business_id"], source="sisu",
                    external_id=external, name=name, email=email, is_active=True)
        s.add(row)
        await s.commit()
        return row.id


async def _deal(ws, external: str, *, agent="self", **fields):
    """A deal for the workspace's own agent, unless `agent` names another -- or is None, for a deal
    Sisu attributed to nobody."""
    async with SessionLocal() as s:
        s.add(Transaction(tenant_id=ws["tenant_id"], business_id=ws["business_id"], source="sisu",
                          external_id=external, status=fields.pop("status", "pending"),
                          agent_id=ws["agent_id"] if agent == "self" else agent, **fields))
        await s.commit()


async def _member(ws):
    async with SessionLocal() as s:
        return await s.get(IntranetMember, ws["member_id"])


async def _numbers(ws, *, team=False, today=TODAY):
    async with SessionLocal() as s:
        member = await s.get(IntranetMember, ws["member_id"]) if ws["member_id"] else None
        return await mn.numbers_for(s, ws["tenant_id"], member, today, team=team)


async def _payload(ws, method="GET", body=None):
    """The portal's own config, as the signed-in user receives it."""
    token = make_token(ws["user_id"], ws["tenant_id"], 0)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        r = await c.request(method, "/api/v1/intranet/config", json=body,
                            headers={"Authorization": f"Bearer {token}",
                                     "x-tenant-host": ws["host"]})
    assert r.status_code == 200, r.text
    return r.json()["config"]


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


# ── the figures ───────────────────────────────────────────────────────────────────────────

async def test_the_week_is_the_last_seven_days_and_ends_today():
    ws = await _workspace("mnweek", member_email="ada@mnweek.test", agent_email="ada@mnweek.test")
    await _deal(ws, "in", appt_set_date=TODAY - dt.timedelta(days=2))
    await _deal(ws, "edge", appt_set_date=TODAY - dt.timedelta(days=7))
    await _deal(ws, "out", appt_set_date=TODAY - dt.timedelta(days=30))
    # Sisu holds a few dates after today, and none of them happened this week.
    await _deal(ws, "future", appt_set_date=TODAY + dt.timedelta(days=3))

    numbers = await _numbers(ws)
    assert numbers["own"]["appointments_set"] == 2, "the window is not the last seven days"
    assert numbers["window_days"] == 7
    assert numbers["since"] == (TODAY - dt.timedelta(days=7)).isoformat()


async def test_each_figure_counts_its_own_date():
    ws = await _workspace("mnfigs", member_email="ada@mnfigs.test", agent_email="ada@mnfigs.test")
    await _deal(ws, "a", appt_set_date=TODAY, appt_met_date=TODAY, contract_date=TODAY)
    await _deal(ws, "b", appt_set_date=TODAY)
    await _deal(ws, "c", appt_met_date=TODAY.replace(day=2))       # this month, not this week

    own = (await _numbers(ws))["own"]
    assert (own["appointments_set"], own["appointments_held"], own["new_contracts"]) == (2, 1, 1)
    assert own["appointments_held_mtd"] == 2


async def test_closed_means_a_closed_sale_this_year():
    """Counted the way the dashboard counts it. Sisu marks $0 referrals closed too, and two surfaces
    in one product disagreeing about "closed" is worse than either answer."""
    ws = await _workspace("mnclosed", member_email="ada@mnclosed.test",
                          agent_email="ada@mnclosed.test")
    await _deal(ws, "c1", status="closed", close_date=dt.date(2026, 2, 1),
                sale_price=Decimal(400000), gci=Decimal(12000))
    await _deal(ws, "c2", status="closed", close_date=dt.date(2026, 8, 1),
                sale_price=Decimal(600000), gci=Decimal(18000))
    await _deal(ws, "referral", status="closed", close_date=dt.date(2026, 5, 1),
                sale_price=Decimal(0), gci=Decimal(2500))
    await _deal(ws, "lastyear", status="closed", close_date=dt.date(2025, 12, 1),
                sale_price=Decimal(500000), gci=Decimal(15000))
    # A pending deal with a close date is a projection, not a closing.
    await _deal(ws, "projected", status="pending", close_date=dt.date(2026, 3, 1),
                sale_price=Decimal(300000))

    own = (await _numbers(ws))["own"]
    assert own["closed_units_ytd"] == 2
    assert own["closed_volume_ytd"] == 1_000_000
    assert own["gci_ytd"] == 30_000


async def test_pending_is_the_current_pipeline_not_every_contract_that_never_closed():
    ws = await _workspace("mnpending", member_email="ada@mnpending.test",
                          agent_email="ada@mnpending.test")
    await _deal(ws, "recent", contract_date=TODAY - dt.timedelta(days=30),
                sale_price=Decimal(350000))
    await _deal(ws, "stale", contract_date=dt.date(2019, 4, 1), sale_price=Decimal(900000))

    own = (await _numbers(ws))["own"]
    assert (own["pending_units"], own["pending_volume"]) == (1, 350_000)


async def test_conversations_are_absent_rather_than_zero_until_there_is_a_source():
    """Nothing syncs call activity yet. Reporting 0 would say "you spoke to nobody" -- a claim
    about their week rather than about our integrations."""
    ws = await _workspace("mnconv", member_email="ada@mnconv.test", agent_email="ada@mnconv.test")
    numbers = await _numbers(ws, team=True)
    assert numbers["own"]["conversations_logged"] is None
    assert numbers["team"]["conversations_logged"] is None


# ── the three empties ─────────────────────────────────────────────────────────────────────

async def test_an_unmatched_member_gets_no_figures_rather_than_zeroes():
    """Zeroes for a real agent are their week. Zeroes for an unmatched email are a configuration
    problem, and rendered as figures they read as the week."""
    ws = await _workspace("mnunconn", member_email="ops@mnunconn.test", agent_email="a@b.test")
    numbers = await _numbers(ws)
    assert (numbers["connected"], numbers["matched"], numbers["own"]) == (True, False, None)

    quiet = await _workspace("mnquiet", member_email="ada@mnquiet.test",
                             agent_email="ada@mnquiet.test")
    numbers = await _numbers(quiet)
    assert numbers["matched"] is True
    assert numbers["own"]["appointments_set"] == 0


async def test_a_workspace_without_sisu_says_so_rather_than_blaming_the_member():
    """What happened in production (September 2026): the Utah Life portal's workspace had no Sisu
    connection -- Sisu was connected on a different workspace -- and the page told its owner that
    their address could not be matched."""
    ws = await _workspace("mnnosisu", member_email="ada@mnnosisu.test",
                          agent_email="ada@mnnosisu.test", sisu=None)
    await _deal(ws, "stale", status="closed", close_date=dt.date(2026, 2, 1),
                sale_price=Decimal(1))

    numbers = await _numbers(ws, team=True)
    assert numbers["connected"] is False
    assert (numbers["matched"], numbers["own"], numbers["team"]) == (False, None, None)


async def test_a_connection_that_has_never_synced_blames_nobody():
    """The afternoon somebody connects Sisu there are no agent rows yet, and every member would
    otherwise be told their address was wrong."""
    ws = await _workspace("mnnosync", member_email="ada@mnnosync.test",
                          agent_email="ada@mnnosync.test", sisu="connected")
    numbers = await _numbers(ws, team=True)
    assert (numbers["connected"], numbers["synced_at"]) == (True, None)
    assert (numbers["matched"], numbers["own"], numbers["team"]) == (False, None, None)


# ── the team ──────────────────────────────────────────────────────────────────────────────

async def test_the_team_is_every_deal_and_the_table_is_who_it_came_from():
    ws = await _workspace("mnteam", member_email="lead@mnteam.test", agent_email="ada@mnteam.test")
    bo = await _agent(ws, "Bo", "bo@mnteam.test", "A2")
    await _agent(ws, "Idle", "idle@mnteam.test", "A3")
    await _deal(ws, "a1", status="closed", close_date=dt.date(2026, 3, 1),
                sale_price=Decimal(500000), gci=Decimal(15000))
    await _deal(ws, "b1", agent=bo, status="closed", close_date=dt.date(2026, 4, 1),
                sale_price=Decimal(700000), gci=Decimal(21000))
    await _deal(ws, "b2", agent=bo, status="closed", close_date=dt.date(2026, 6, 1),
                sale_price=Decimal(300000), gci=Decimal(9000))
    await _deal(ws, "b3", agent=bo, contract_date=TODAY - dt.timedelta(days=10),
                sale_price=Decimal(250000))
    await _deal(ws, "loose", agent=None, status="closed", close_date=dt.date(2026, 5, 1),
                sale_price=Decimal(100000), gci=Decimal(3000))

    numbers = await _numbers(ws, team=True)
    # A manager who does not sell: nothing of their own, and the whole team's.
    assert numbers["own"] is None
    team = numbers["team"]
    assert team["closed_units_ytd"] == 4, "a deal with no agent is still the team's"
    assert team["closed_volume_ytd"] == 1_600_000
    assert team["gci_ytd"] == 48_000
    assert team["pending_units"] == 1

    rows = {row["name"]: row for row in team["by_agent"]}
    assert list(rows) == ["Bo", "Ada"], "most closings first, and nobody with nothing this year"
    assert (rows["Bo"]["closed_units_ytd"], rows["Bo"]["pending_units"]) == (2, 1)
    assert set(rows["Bo"]) == {"id", "name", *mn.FIGURES}
    assert team["producing_agents"] == 2


async def test_the_team_is_only_counted_when_the_caller_asks():
    """Who may see it is the router's decision; this module must not hand it to whoever calls."""
    ws = await _workspace("mnnoteam", member_email="ada@mnnoteam.test",
                          agent_email="ada@mnnoteam.test")
    assert (await _numbers(ws, team=False))["team"] is None


# ── who is shown the team ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("level, shown", [("Full", True), ("View", True), ("Limited", True),
                                          ("None", False), (None, False)])
async def test_the_team_production_permission_decides_who_sees_the_team(level, shown):
    """`team_production` was in every workspace's matrix -- Full for Owner and Manager -- and
    nothing read it. A MISSING row denies here, unlike elsewhere: guessing wrong would show every
    agent everyone else's production."""
    slug = f"mnperm{(level or 'absent').lower()}"
    ws = await _workspace(slug, member_email=f"ada@{slug}.test", agent_email=f"ada@{slug}.test",
                          team_level=level)
    numbers = (await _payload(ws))["numbers"]
    assert numbers["own"] is not None, "your own numbers never depend on the team permission"
    assert (numbers["team"] is not None) is shown


@pytest.mark.parametrize("role, shown", [("owner", True), ("admin", True), ("member", False)])
async def test_off_the_roster_only_an_owner_or_admin_sees_the_team(role, shown):
    slug = f"mnoff{role}"
    ws = await _workspace(slug, member_email=f"boss@{slug}.test", user_role=role, on_roster=False)
    numbers = (await _payload(ws))["numbers"]
    assert (numbers["on_roster"], numbers["own"]) == (False, None)
    assert (numbers["team"] is not None) is shown


async def test_a_removed_member_does_not_keep_the_team():
    ws = await _workspace("mnremoved", member_email="ada@mnremoved.test",
                          agent_email="ada@mnremoved.test", team_level="Full")
    async with SessionLocal() as s:
        member = await s.get(IntranetMember, ws["member_id"])
        member.status = "Removed"
        await s.commit()
    assert (await _payload(ws))["numbers"]["team"] is None


# ── pace ──────────────────────────────────────────────────────────────────────────────────

def test_pace_is_against_the_year_elapsed_not_the_goal_achieved():
    """Four by 31 March against twelve is 33% achieved and 135% of pace, and only the second one
    tells somebody whether to change anything this week."""
    assert mn.pace(4, 12, dt.date(2026, 3, 31)) == 135
    assert mn.pace(12, 12, dt.date(2026, 12, 31)) == 100


def test_no_goal_means_no_percentage_rather_than_a_confident_one():
    assert mn.pace(4, None) is None
    assert mn.pace(4, 0) is None


# ── the payload ───────────────────────────────────────────────────────────────────────────

async def test_the_portal_payload_carries_live_figures_and_pace():
    today = dt.datetime.now(dt.timezone.utc).date()
    ws = await _workspace("mnpayload", member_email="ada@mnpayload.test",
                          agent_email="ada@mnpayload.test")
    await _deal(ws, "p", appt_set_date=today, appt_met_date=today)
    async with SessionLocal() as s:
        t = await s.get(Tenant, ws["tenant_id"])
        cfg = dict(t.config or {})
        cfg.setdefault("intranet", {})["numbers"] = {"annual_unit_goal": 12}
        t.config = cfg
        flag_modified(t, "config")
        await s.commit()

    numbers = (await _payload(ws))["numbers"]
    assert (numbers["connected"], numbers["matched"]) == (True, True)
    assert (numbers["own"]["appointments_set"], numbers["own"]["appointments_held"]) == (1, 1)
    assert numbers["annual_unit_goal"] == 12
    assert numbers["pace_percent"] == 0, "no closings against a goal of twelve is 0% of pace"


async def test_saving_the_config_answers_with_the_whole_payload():
    """PATCH answered with a partial payload -- no content, no numbers -- and the portal replaces
    its config with the answer. Saving the calendar URL blanked every figure, and the permissions
    the rail filters on, until the next reload."""
    ws = await _workspace("mnpatch", member_email="ada@mnpatch.test",
                          agent_email="ada@mnpatch.test", user_role="owner")
    config = await _payload(ws, "PATCH", {"calendar": {"google_calendar_url": ""}})
    assert config["numbers"]["own"] is not None
    assert "capabilities" in config["content"]


# ── what the page reads ───────────────────────────────────────────────────────────────────

# `numbers.x`, `own?.x`, `team.x`... Zero-width, so `numbers.team.by_agent` yields both
# `numbers.team` and `team.by_agent`.
READ = re.compile(r"(?=\b(numbers|own|team|figures|producer)\??\.([a-z_]+))")


def _reads(text: str) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for name, key in READ.findall(text):
        found.setdefault(name, set()).add(key)
    return found


def test_the_scan_sees_through_chains_and_optional_reads():
    """A scan that quietly matches nothing passes forever. Pinned on synthetic source."""
    assert _reads("numbers.team.by_agent.map((producer) => producer.name); own?.gci_ytd;") == {
        "numbers": {"team"}, "team": {"by_agent"}, "producer": {"name"}, "own": {"gci_ytd"}}
    assert _reads("member_numbers.pace; their own. Then the team.") == {}


@pytest.mark.skipif(not SRC.exists(), reason="frontend not present")
async def test_every_number_the_portal_reads_is_one_the_server_sends():
    """THE BUG THAT SHIPPED. The Sunburst commit replaced the zeroes with real figures under new
    names, and My Numbers, the home cards and the goal card went on reading `closed_units`,
    `gci_ytd` and `team_units_ytd` -- names nothing sends -- so they showed 0 to every agent and
    nothing complained. Derived rather than listed: every read in the page is checked against a
    real payload, including reads added after this was written."""
    today = dt.datetime.now(dt.timezone.utc).date()
    ws = await _workspace("mnreads", member_email="ada@mnreads.test",
                          agent_email="ada@mnreads.test", team_level="Full")
    await _deal(ws, "r", appt_set_date=today)
    numbers = (await _payload(ws))["numbers"]
    assert numbers["own"] and numbers["team"] and numbers["team"]["by_agent"]

    sent = {"numbers": set(numbers), "own": set(numbers["own"]), "team": set(numbers["team"]),
            # The cards render either one, so they may only read what both carry.
            "figures": set(numbers["own"]) & set(numbers["team"]),
            "producer": set(numbers["team"]["by_agent"][0])}
    reads = _reads((SRC / "intranet" / "IntranetApp.jsx").read_text(encoding="utf-8"))
    for name in sent:
        assert reads.get(name), (f"the page no longer reads anything off `{name}` -- if it was "
                                 "renamed, rename it here rather than letting this pass empty")
    unsent = sorted(f"{name}.{key}" for name, keys in reads.items() for key in keys - sent[name])
    assert not unsent, f"the portal reads names the server does not send: {unsent}"
