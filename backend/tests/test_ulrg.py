"""ULRG L10 Scorecard API (SPEC-ulrg-scorecard Part 5.1) against the seeded Spring data."""
import datetime as dt
from decimal import Decimal

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant, ScorecardGroup, ScorecardMetric, ScorecardValue, User
from app.security import hash_pw, make_token
from app.seed_ulrg_scorecard import load_ulrg_scorecard

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        await load_ulrg_scorecard(s, t.id)


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


async def _owner_token():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


def _H(t):
    return {"Authorization": f"Bearer {t}"}


async def _mk_user(email, role="member", tabs=None):
    """Create a user directly (test setup) and return a bearer token."""
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = User(tenant_id=t.id, email=email.lower(), name=email.split("@")[0],
                 password_hash=hash_pw("password123"), role=role, status="active",
                 tab_access=tabs, token_version=0)
        s.add(u); await s.commit()
        return make_token(u.id, t.id, 0)


async def _plant_davis(weekly: dict, weeks: list) -> None:
    """Write one value per (Davis measurable, week) for the weeks given. UPSERTS: a week the seed
    already carries is updated, never doubled — a second row for the same (metric, week) would break
    `test_manual_value_entry_and_role_gate`'s scalar_one, and which seeded weeks a today-anchored
    window overlaps changes as the clock moves."""
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        g = (await s.execute(select(ScorecardGroup).where(
            ScorecardGroup.tenant_id == t.id, ScorecardGroup.key == "davis"))).scalars().first()
        for name, val in weekly.items():
            m = (await s.execute(select(ScorecardMetric).where(
                ScorecardMetric.tenant_id == t.id, ScorecardMetric.group_id == g.id,
                ScorecardMetric.name == name))).scalars().one()
            existing = {v.week_start: v for v in (await s.execute(select(ScorecardValue).where(
                ScorecardValue.tenant_id == t.id, ScorecardValue.metric_id == m.id))).scalars()}
            for ws in weeks:
                row = existing.get(ws)
                if row is None:
                    s.add(ScorecardValue(tenant_id=t.id, metric_id=m.id, week_start=ws,
                                         value=Decimal(val), source="manual"))
                else:
                    row.value = Decimal(val)
        await s.commit()


async def _set_periods(periods: list) -> None:
    """Point tenant.config.fiscal_quarters at the given measurement periods. Rebind the dict instead
    of mutating the loaded one — SQLAlchemy compares a JSON column by identity and would skip the
    UPDATE in silence. Left in place for the rest of the module: nothing else here asserts on the
    periods, and an anchored one keeps quarter_of off its no-quarter-contains-today fallback."""
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        cfg = dict(t.config or {})
        cfg["fiscal_quarters"] = periods
        t.config = cfg
        await s.commit()


async def test_scorecard_payload_shape_and_snapshot_rule():
    # ANCHORED TO TODAY. Every number this test reads — the 13-week cumulative, the quarter's age,
    # the Move constraint — is computed from the LIVE clock, and it comes in over HTTP, which has no
    # `today=` seam (build_scorecard takes one, the route asks the server for the date). Leaning on
    # the seed's July weeks, it was going to raise TypeError in the week of Mon 2026-10-26: the last
    # seeded week (7/27) leaves the 13-week window, `cumulative["w13"]` goes None, and `["attain"]`
    # subscripts it. It had already been thinning for a month — 4 counted weeks on 9/28, 1 on 10/19.
    # So the test plants every CLOSED week of that window itself and owns the period too.
    monday = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())   # this week, in progress
    closed = [monday - dt.timedelta(weeks=w) for w in range(1, 13)]   # the other 12 weeks of the window
    # A period exactly one CLOSED week old, on whatever weekday this runs: (today - start).days is
    # 7–13, so weeks_closed is always 1. 90 days Mon→Sun is 13 whole weeks, so weeks_total is 13 and
    # that 1 is never clamped.
    cur_start, prev_end = monday - dt.timedelta(weeks=1), monday - dt.timedelta(weeks=1, days=1)
    await _set_periods([
        {"key": "PREV", "start": (prev_end - dt.timedelta(days=90)).isoformat(),
         "end": prev_end.isoformat()},
        {"key": "CUR", "start": cur_start.isoformat(),
         "end": (cur_start + dt.timedelta(days=90)).isoformat()}])
    # 18 of a weekly 30 and 12 of a weekly 20 — both 60%, so the Move ranking below has to choose on
    # funnel position rather than on which row is worse.
    await _plant_davis({"Appointments Met": 18, "Clients Signed": 12}, closed)

    owner = await _owner_token()
    async with _client() as c:
        r = await c.get("/api/v1/ulrg/scorecard?weeks=13", headers=_H(owner))
        assert r.status_code == 200
        d = r.json()
    assert len(d["groups"]) == 4 and d["default_window"] == 13
    assert d["quarter"]["key"] and d["windows"] == [4, 13, "qtd"]
    assert len(d["weeks"]) == 13 and d["weeks"][-1]["start"] == monday.isoformat()

    rows = [row for g in d["groups"] for row in g["rows"]]
    snaps = [row for row in rows if row["type"] == "snapshot"]
    # Part 7 acceptance: snapshot rows never carry a cumulative block (the three named in the sheet)
    assert {row["measurable"] for row in snaps} == {
        "ULRG Met to Signed Ratio YTD", "Database HealthScore", "QTD Agents Recruited"}
    assert all(row["cumulative"] is None for row in snaps)     # non-vacuous: the set above pins 3 rows

    davis = next(g for g in d["groups"] if g["key"] == "davis")
    appts = next(row for row in davis["rows"] if row["measurable"] == "Appointments Met")
    assert isinstance(appts["values"], list) and len(appts["values"]) == 13
    w13 = appts["cumulative"]["w13"]
    # the arithmetic, not just "not None" — 12 closed weeks at 18 against a weekly 30. Pinned this
    # way the window cannot quietly shrink to a couple of leftover weeks and still pass.
    assert (w13["n"], w13["actual"], w13["target"], w13["attain"]) == (12, 216, 360, 60.0)

    # qtd is null because the period is younger than two CLOSED weeks (Part 4.7). The period does
    # hold a closed week carrying data, so that null is the rule firing — not the empty-window
    # accident it had degraded into, which no clock could ever have made fail.
    assert d["quarter"]["weeks_closed"] == 1
    assert d["weeks"][-2]["start"] == d["quarter"]["start"] and d["weeks"][-2]["complete"] is True
    assert appts["values"][-2] == 18
    assert appts["cumulative"]["qtd"] is None

    # the Move constraint is the EARLIEST funnel stage below 100, not the worst one: Appointments Met
    # (stage 1) and Clients Signed (stage 2) both sit at 60%, so only the ranking picks a winner.
    signed = next(row for row in davis["rows"] if row["measurable"] == "Clients Signed")
    assert signed["cumulative"]["w13"]["attain"] == 60.0
    assert davis["move"]["constraint_metric_id"] == appts["id"]


async def test_manual_value_entry_and_role_gate():
    owner = await _owner_token()
    async with _client() as c:
        mid = (await c.get("/api/v1/ulrg/scorecard", headers=_H(owner))).json()["groups"][0]["rows"][0]["id"]
        r = await c.post("/api/v1/ulrg/scorecard/values", headers=_H(owner),
                         json={"metric_id": mid, "week_start": "2026-07-27", "value": 41})
        assert r.status_code == 201 and r.json()["ok"] is True
    async with SessionLocal() as s:
        v = (await s.execute(select(ScorecardValue).where(
            ScorecardValue.metric_id == mid, ScorecardValue.week_start == dt.date(2026, 7, 27)))).scalar_one()
        assert float(v.value) == 41 and v.source == "manual"


async def test_manual_kpis_are_self_serve_but_auto_rows_stay_admin_only():
    owner = await _owner_token()
    member = await _mk_user("kpi-member@x.com", tabs=["ulrg"])       # can see the scorecard
    outsider = await _mk_user("no-ulrg@x.com", tabs=["forum"])       # cannot
    async with _client() as c:
        rows = [row for g in (await c.get("/api/v1/ulrg/scorecard", headers=_H(owner))).json()["groups"]
                for row in g["rows"]]
        manual = next(r for r in rows if not r["auto"])
        auto = next(r for r in rows if r["auto"])

        # a member who has scorecard access may edit a HAND-ENTERED measurable (the KPI they own)
        r = await c.post("/api/v1/ulrg/scorecard/values", headers=_H(member),
                         json={"metric_id": manual["id"], "week_start": "2026-07-27", "value": 7})
        assert r.status_code == 201
        # …but must NOT hand-override an auto (resolver-sourced) row
        r = await c.post("/api/v1/ulrg/scorecard/values", headers=_H(member),
                         json={"metric_id": auto["id"], "week_start": "2026-07-27", "value": 7})
        assert r.status_code == 403
        # …and someone without ULRG access can't edit at all (require_tab gates the route)
        r = await c.post("/api/v1/ulrg/scorecard/values", headers=_H(outsider),
                         json={"metric_id": manual["id"], "week_start": "2026-07-27", "value": 7})
        assert r.status_code == 403


def test_springb_seed_strings_fit_their_columns():
    """ScorecardMetric.name/note are String(160). SQLite (the test/scratch DB) doesn't enforce a
    varchar length but Postgres (prod) does, so a too-long seed note passes locally then truncates on
    prod. Validate the seed JSON against the real column lengths here so it can't reach prod again."""
    import json
    from app import seed_springb_scorecard as sb
    from app.models import ScorecardMetric, ScorecardGroup
    name_len = ScorecardMetric.__table__.c.name.type.length
    note_len = ScorecardMetric.__table__.c.note.type.length
    gname_len = ScorecardGroup.__table__.c.name.type.length
    data = json.load(open(sb._DATA_PATH, encoding="utf-8"))
    for g in data["groups"]:
        assert len(g["name"]) <= (gname_len or 10**9), g["name"]
        for m in g["metrics"]:
            assert len(m["name"]) <= name_len, m["name"]
            assert len(m.get("note") or "") <= note_len, m["name"]


async def test_springb_is_a_separate_board_with_its_own_access_and_no_leak():
    from app.seed_springb_scorecard import load_springb_scorecard
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        n = await load_springb_scorecard(s, t.id)
    assert n == 18                                                      # 15 core + an Email Open Rate on 3 more groups

    owner = await _owner_token()
    forum_member = await _mk_user("sb-forum@x.com", tabs=["forum"])      # can see a Spring B brand tab
    ulrg_member = await _mk_user("sb-ulrg@x.com", tabs=["ulrg"])         # cannot

    async with _client() as c:
        d = (await c.get("/api/v1/ulrg/scorecard?scope=springb", headers=_H(owner))).json()
        assert {g["name"] for g in d["groups"]} == {"Spring B", "beCollective", "Forum", "Activated Agent"}
        rows = [r for g in d["groups"] for r in g["rows"]]
        assert len(rows) == 18 and all(not r["auto"] for r in rows)      # all manual, nothing auto-sourced
        # every brand group tracks an email open rate (Spring B's is "Email Open Rate Floor")
        by_group = {g["key"]: {r["measurable"] for r in g["rows"]} for g in d["groups"]}
        assert all(any("Email Open Rate" in m for m in by_group[k]) for k in ("spring_b", "becollective", "forum", "activated"))
        assert d["weeks"]                                                # week columns exist despite no seeded values

        # re-seeding is idempotent + non-destructive: enter a value, re-run the seed, value survives, nothing added
        first = rows[0]["id"]
        await c.post("/api/v1/ulrg/scorecard/values", headers=_H(owner),
                     json={"metric_id": first, "week_start": d["weeks"][-1]["start"], "value": 5})
        async with SessionLocal() as s:
            assert await load_springb_scorecard(s, t.id) == 0            # everything already present
        d2 = (await c.get("/api/v1/ulrg/scorecard?scope=springb", headers=_H(owner))).json()
        rows2 = [r for g in d2["groups"] for r in g["rows"]]
        assert len(rows2) == 18                                         # no duplicates
        kept = next(r for r in rows2 if r["id"] == first)
        assert 5 in [v for v in kept["values"] if v is not None]        # the entered value wasn't wiped

        # access follows the scope's tabs: a Forum member sees + edits Spring B; a ULRG-only member can't
        assert (await c.get("/api/v1/ulrg/scorecard?scope=springb", headers=_H(forum_member))).status_code == 200
        assert (await c.get("/api/v1/ulrg/scorecard?scope=springb", headers=_H(ulrg_member))).status_code == 403
        mid, wk = rows[0]["id"], d["weeks"][-1]["start"]
        assert (await c.post("/api/v1/ulrg/scorecard/values", headers=_H(forum_member),
                             json={"metric_id": mid, "week_start": wk, "value": 5})).status_code == 201
        assert (await c.post("/api/v1/ulrg/scorecard/values", headers=_H(ulrg_member),
                             json={"metric_id": mid, "week_start": wk, "value": 5})).status_code == 403

        # the two boards' Settings editors never show each other's rows
        sb_goals = (await c.get("/api/v1/ulrg/goals?period=2026Q3&scope=springb", headers=_H(owner))).json()["goals"]
        sb = {x["name"] for x in sb_goals}
        ul = {x["name"] for x in (await c.get("/api/v1/ulrg/goals?period=2026Q3&scope=ulrg", headers=_H(owner))).json()["goals"]}
        assert "Members Added" in sb and "Appointments Met" not in sb and len(sb_goals) == 18
        assert "Appointments Met" in ul and "Members Added" not in ul


async def test_remove_measurable_soft_deletes_and_is_admin_only():
    owner = await _owner_token()
    member = await _mk_user("row-remover@x.com", tabs=["ulrg"])
    async with _client() as c:
        overall = next(g for g in (await c.get("/api/v1/ulrg/scorecard", headers=_H(owner))).json()["groups"]
                       if g["key"] == "overall")
        mid = next(r["id"] for r in overall["rows"] if r["measurable"] == "Database HealthScore")

        # removing a row is a structural change → admin only (unlike editing a manual value)
        r = await c.patch(f"/api/v1/ulrg/metric/{mid}", headers=_H(member), json={"active": False})
        assert r.status_code == 403
        # owner removes it → gone from the scorecard everywhere
        r = await c.patch(f"/api/v1/ulrg/metric/{mid}", headers=_H(owner), json={"active": False})
        assert r.status_code == 200
        ids = {row["id"] for g in (await c.get("/api/v1/ulrg/scorecard", headers=_H(owner))).json()["groups"]
               for row in g["rows"]}
        assert mid not in ids
        # soft delete — restore brings it (and its history) back
        assert (await c.patch(f"/api/v1/ulrg/metric/{mid}", headers=_H(owner), json={"active": True})).status_code == 200
        ids = {row["id"] for g in (await c.get("/api/v1/ulrg/scorecard", headers=_H(owner))).json()["groups"]
               for row in g["rows"]}
        assert mid in ids
