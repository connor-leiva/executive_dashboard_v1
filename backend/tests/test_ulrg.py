"""ULRG L10 Scorecard API (SPEC-ulrg-scorecard Part 5.1) against the seeded Spring data."""
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant, ScorecardMetric, User
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


async def test_scorecard_payload_shape_and_snapshot_rule():
    owner = await _owner_token()
    async with _client() as c:
        r = await c.get("/api/v1/ulrg/scorecard?weeks=13", headers=_H(owner))
        assert r.status_code == 200
        d = r.json()
    assert len(d["groups"]) == 4 and d["default_window"] == 13
    assert d["quarter"]["key"] and d["windows"] == [4, 13, "qtd"]

    rows = [row for g in d["groups"] for row in g["rows"]]
    snaps = [row for row in rows if row["type"] == "snapshot"]
    # Part 7 acceptance: snapshot rows never carry a cumulative block (the three named in the sheet)
    assert {row["measurable"] for row in snaps} == {
        "ULRG Met to Signed Ratio YTD", "Database HealthScore", "QTD Agents Recruited"}
    assert all(row["cumulative"] is None for row in snaps)

    davis = next(g for g in d["groups"] if g["key"] == "davis")
    appts = next(row for row in davis["rows"] if row["measurable"] == "Appointments Met")
    assert isinstance(appts["values"], list) and appts["cumulative"]["w13"]["attain"] is not None
    assert appts["cumulative"]["qtd"] is None          # quarter < 2 weeks closed → qtd null
    assert davis["move"]["constraint_metric_id"]        # earliest funnel stage below 100


async def test_manual_value_entry_and_role_gate():
    owner = await _owner_token()
    async with _client() as c:
        mid = (await c.get("/api/v1/ulrg/scorecard", headers=_H(owner))).json()["groups"][0]["rows"][0]["id"]
        r = await c.post("/api/v1/ulrg/scorecard/values", headers=_H(owner),
                         json={"metric_id": mid, "week_start": "2026-07-27", "value": 41})
        assert r.status_code == 201 and r.json()["ok"] is True
    async with SessionLocal() as s:
        from app.models import ScorecardValue
        import datetime as dt
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
    assert n == 15

    owner = await _owner_token()
    forum_member = await _mk_user("sb-forum@x.com", tabs=["forum"])      # can see a Spring B brand tab
    ulrg_member = await _mk_user("sb-ulrg@x.com", tabs=["ulrg"])         # cannot

    async with _client() as c:
        d = (await c.get("/api/v1/ulrg/scorecard?scope=springb", headers=_H(owner))).json()
        assert {g["name"] for g in d["groups"]} == {"Spring B", "beCollective", "Forum", "Activated Agent"}
        rows = [r for g in d["groups"] for r in g["rows"]]
        assert len(rows) == 15 and all(not r["auto"] for r in rows)      # all manual, nothing auto-sourced
        assert d["weeks"]                                                # week columns exist despite no seeded values

        # access follows the scope's tabs: a Forum member sees + edits Spring B; a ULRG-only member can't
        assert (await c.get("/api/v1/ulrg/scorecard?scope=springb", headers=_H(forum_member))).status_code == 200
        assert (await c.get("/api/v1/ulrg/scorecard?scope=springb", headers=_H(ulrg_member))).status_code == 403
        mid, wk = rows[0]["id"], d["weeks"][-1]["start"]
        assert (await c.post("/api/v1/ulrg/scorecard/values", headers=_H(forum_member),
                             json={"metric_id": mid, "week_start": wk, "value": 5})).status_code == 201
        assert (await c.post("/api/v1/ulrg/scorecard/values", headers=_H(ulrg_member),
                             json={"metric_id": mid, "week_start": wk, "value": 5})).status_code == 403

        # the two boards' Settings editors never show each other's rows
        sb = {x["name"] for x in (await c.get("/api/v1/ulrg/goals?period=2026Q3&scope=springb", headers=_H(owner))).json()["goals"]}
        ul = {x["name"] for x in (await c.get("/api/v1/ulrg/goals?period=2026Q3&scope=ulrg", headers=_H(owner))).json()["goals"]}
        assert "Members Added" in sb and "Appointments Met" not in sb and len(sb) == 15
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
