"""Scorecard self-service office config (Phase A): edit owner full name (owner/admin) + upload a
headshot served publicly (for the app AND the read-only embed)."""
import datetime as dt
import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant, Business, ScorecardGroup, ScorecardMetric, ScorecardValue
from app.seed_ulrg_scorecard import load_ulrg_scorecard
from app.services.scorecard import build_scorecard

TRANSPORT = ASGITransport(app=app)

# a minimal valid 1x1 PNG
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f5f0000000049454e44ae426082")


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        await load_ulrg_scorecard(s, t.id)


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


async def _owner():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


def _H(t):
    return {"Authorization": f"Bearer {t}"}


async def _davis_id(c, tok):
    d = (await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json()
    return next(g["id"] for g in d["groups"] if g["key"] == "davis")


async def test_edit_owner_name_full():
    tok = await _owner()
    async with _client() as c:
        gid = await _davis_id(c, tok)
        r = await c.patch(f"/api/v1/ulrg/group/{gid}", headers=_H(tok),
                          json={"owner_name": "Jace Gillies"})
        assert r.status_code == 200 and r.json()["owner_name"] == "Jace Gillies"
        d = (await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json()
        davis = next(g for g in d["groups"] if g["key"] == "davis")
        assert davis["owner"]["name"] == "Jace Gillies"


async def test_upload_headshot_then_public_serve():
    tok = await _owner()
    async with _client() as c:
        gid = await _davis_id(c, tok)
        up = await c.post(f"/api/v1/ulrg/group/{gid}/photo", headers=_H(tok),
                          files={"file": ("jace.png", _PNG, "image/png")})
        assert up.status_code == 201
        # payload now advertises a photo_url
        d = (await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json()
        davis = next(g for g in d["groups"] if g["key"] == "davis")
        purl = davis["owner"]["photo_url"]
        assert purl and purl.startswith(f"/ulrg/group/{gid}/photo")
        # served PUBLICLY (no auth) — the embed needs it
        img = await c.get(f"/api/v1/ulrg/group/{gid}/photo")
        assert img.status_code == 200 and img.headers["content-type"].startswith("image/")
        assert img.content == _PNG


async def test_edit_measurement_periods():
    tok = await _owner()
    async with _client() as c:
        r = await c.put("/api/v1/ulrg/periods", headers=_H(tok), json={"periods": [
            {"key": "2026Q4", "start": "2026-11-02", "end": "2027-01-30"},
            {"key": "2026Q3", "start": "2026-07-28", "end": "2026-10-30"}]})   # out of order on purpose
        assert r.status_code == 200
        assert [p["key"] for p in r.json()["periods"]] == ["2026Q3", "2026Q4"]  # sorted by start
        got = (await c.get("/api/v1/ulrg/periods", headers=_H(tok))).json()["periods"]
        assert len(got) == 2
        # bad date → 400
        bad = await c.put("/api/v1/ulrg/periods", headers=_H(tok),
                          json={"periods": [{"key": "X", "start": "nope", "end": "2026-01-01"}]})
        assert bad.status_code == 400
        # end before start → 400
        b2 = await c.put("/api/v1/ulrg/periods", headers=_H(tok),
                         json={"periods": [{"key": "X", "start": "2026-05-01", "end": "2026-04-01"}]})
        assert b2.status_code == 400


async def test_config_requires_admin_auth():
    async with _client() as c:
        gid = await _davis_id(c, await _owner())
        # no token → 401
        assert (await c.patch(f"/api/v1/ulrg/group/{gid}", json={"owner_name": "x"})).status_code == 401
        # non-image upload → 400
        tok = await _owner()
        bad = await c.post(f"/api/v1/ulrg/group/{gid}/photo", headers=_H(tok),
                           files={"file": ("note.txt", b"hi", "text/plain")})
        assert bad.status_code == 400


async def test_per_period_goals_override_and_history():
    tok = await _owner()
    async with _client() as c:
        # known periods: seeded weeks end 7/27 (Q2); today (Aug 2026) is in Q3
        await c.put("/api/v1/ulrg/periods", headers=_H(tok), json={"periods": [
            {"key": "2026Q2", "start": "2026-04-13", "end": "2026-07-27"},
            {"key": "2026Q3", "start": "2026-07-28", "end": "2026-10-30"}]})

        g0 = (await c.get("/api/v1/ulrg/goals?period=2026Q3", headers=_H(tok))).json()["goals"]
        appt = next(x for x in g0 if x["name"] == "Appointments Met" and x["group"] == "Davis")
        assert appt["goal"] == appt["default"] and appt["overridden"] is False   # no override yet
        default = appt["default"]

        # override the CURRENT period (Q3) goal
        r = await c.put("/api/v1/ulrg/goals", headers=_H(tok),
                        json={"period": "2026Q3", "goals": [{"metric_id": appt["metric_id"], "goal": default + 10}]})
        assert r.status_code == 200
        again = next(x for x in (await c.get("/api/v1/ulrg/goals?period=2026Q3", headers=_H(tok))).json()["goals"]
                     if x["metric_id"] == appt["metric_id"])
        assert again["goal"] == default + 10 and again["overridden"] is True

        d = (await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json()
        row = next(rr for gg in d["groups"] if gg["key"] == "davis"
                   for rr in gg["rows"] if rr["measurable"] == "Appointments Met")
        assert row["goal"] == default + 10                                   # current-period goal
        # history isn't recolored: each shown week keeps ITS OWN period's goal. The board now shows
        # through the current week, so Q2 weeks (<= 7/27) stay at the default while the current Q3
        # weeks carry the new override.
        q2_end = dt.date(2026, 7, 27)
        assert row["week_goals"] and len(row["week_goals"]) == len(row["values"])
        for wg, w in zip(row["week_goals"], d["weeks"]):
            assert wg == (default if dt.date.fromisoformat(w["start"]) <= q2_end else default + 10)

        # now override Q2 too → those weeks' goals move, current (Q3) unaffected
        await c.put("/api/v1/ulrg/goals", headers=_H(tok),
                    json={"period": "2026Q2", "goals": [{"metric_id": appt["metric_id"], "goal": default - 5}]})
        d2 = (await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json()
        row2 = next(rr for gg in d2["groups"] if gg["key"] == "davis"
                    for rr in gg["rows"] if rr["measurable"] == "Appointments Met")
        for wg, w in zip(row2["week_goals"], d2["weeks"]):                    # each week uses ITS period's goal
            assert wg == (default - 5 if dt.date.fromisoformat(w["start"]) <= q2_end else default + 10)
        assert row2["goal"] == default + 10                                  # current (Q3) still its own


async def test_cumulative_goal_is_period_scoped_thermometer():
    """A flow metric with a period-total (cumulative) goal becomes a quarter thermometer: it counts
    ONLY the weeks within the current period (not a trailing window that bleeds in the prior period)
    and tracks the running total toward the FULL total. Regression for the '96 of 110 after 1 week'
    bug — the actual must be period-to-date, not a rolling 13-week sum."""
    tok = await _owner()
    async with _client() as c:
        # current period starts 2026-07-01, so only the JULY data weeks count (the seed ends 7/27).
        await c.put("/api/v1/ulrg/periods", headers=_H(tok), json={"periods": [
            {"key": "H1", "start": "2026-01-01", "end": "2026-06-30"},
            {"key": "H2", "start": "2026-07-01", "end": "2026-12-31"}]})

        g = (await c.get("/api/v1/ulrg/goals?period=H2", headers=_H(tok))).json()["goals"]
        homes = next(x for x in g if x["name"].startswith("Total Homes Sold") and x["group"] == "Davis")
        assert homes["supports_cumulative"] is True and homes["cumulative_goal"] is None
        assert next(x for x in g if x["type"] == "rate")["supports_cumulative"] is False

        def _homes(sc):
            row = next(rr for gg in sc["groups"] if gg["key"] == "davis"
                       for rr in gg["rows"] if rr["measurable"].startswith("Total Homes Sold"))
            return row, row["cumulative"]["w13"]

        # baseline: no cumulative goal → rolling window, target = weekly(8) × weeks
        row0, c0 = _homes((await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json())
        assert row0["cumulative_goal"] is None and c0["target"] == round(8 * c0["n"])

        # set the period total to 260 (weekly stays 8)
        r = await c.put("/api/v1/ulrg/goals", headers=_H(tok), json={"period": "H2",
            "goals": [{"metric_id": homes["metric_id"], "goal": 8, "cumulative_goal": 260}]})
        assert r.status_code == 200

        sc = (await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json()
        weeks = sc["weeks"]
        row1, c1 = _homes(sc)
        assert row1["goal"] == 8 and row1["cumulative_goal"] == 260   # weekly unchanged, total carried

        # actual counts ONLY weeks whose start is inside the period (>= 2026-07-01)
        pstart = dt.date(2026, 7, 1)
        pd = [v for v, w in zip(row1["values"], weeks)
              if v is not None and dt.date.fromisoformat(w["start"]) >= pstart]
        trailing = [v for v in row1["values"] if v is not None]
        assert c1["actual"] == round(sum(pd))                 # period-to-date, NOT the rolling sum
        assert sum(pd) < sum(trailing)                        # proves pre-period data is excluded
        assert c1["target"] == 260                            # thermometer shows the FULL total
        assert c1["period"] is True and c1["pace"] == round(260 / 26)   # H2 = 26 weeks → pace 10/wk
        # every window is the same quarter-to-date block (the toggle is a no-op for a thermometer)
        assert row1["cumulative"]["w4"] == c1 and row1["cumulative"]["qtd"] == c1

        # clearing it reverts to the rolling weekly-derived cumulative
        await c.put("/api/v1/ulrg/goals", headers=_H(tok), json={"period": "H2",
            "goals": [{"metric_id": homes["metric_id"], "goal": 8, "cumulative_goal": None}]})
        row2, c2 = _homes((await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json())
        assert row2["cumulative_goal"] is None and c2["target"] == round(8 * c2["n"])


async def test_rename_measurable():
    """Owner/admin can rename a measurable (self-service); renaming does not touch its resolver."""
    tok = await _owner()
    async with _client() as c:
        d = (await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json()
        row = next(rr for gg in d["groups"] if gg["key"] == "slc"          # slc, so davis lookups elsewhere are safe
                   for rr in gg["rows"] if rr["measurable"].startswith("Total Homes Sold"))
        mid, original = row["id"], row["measurable"]
        r = await c.patch(f"/api/v1/ulrg/metric/{mid}", headers=_H(tok), json={"name": "Homes Closed"})
        assert r.status_code == 200 and r.json()["name"] == "Homes Closed"
        d2 = (await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json()
        assert any(rr["measurable"] == "Homes Closed" for gg in d2["groups"] if gg["key"] == "slc"
                   for rr in gg["rows"])
        # auth + validation
        assert (await c.patch(f"/api/v1/ulrg/metric/{mid}", json={"name": "x"})).status_code == 401
        assert (await c.patch(f"/api/v1/ulrg/metric/{mid}", headers=_H(tok), json={"name": "   "})).status_code == 400
        # restore (module-scoped seed is shared across tests)
        await c.patch(f"/api/v1/ulrg/metric/{mid}", headers=_H(tok), json={"name": original})


async def test_in_progress_week_excluded_from_cumulative():
    """A week counts toward the cumulative/pace only once it has fully CLOSED (Connor's L10 cadence:
    the team reviews completed Mon–Sun weeks at the Tuesday meeting). The in-progress week still shows
    as a cell but is excluded from actual/target/pace — so 33+17 reads '50 of 60', never '50 of 90'."""
    async with SessionLocal() as s:
        t = Tenant(slug=f"cw-{uuid.uuid4().hex[:8]}", name="Cadence")
        s.add(t); await s.flush()
        t.config = {"fiscal_quarters": [{"key": "2026Q3", "start": "2026-07-27", "end": "2026-10-30"}]}
        b = Business(tenant_id=t.id, key="ulrg", name="ULRG", tag="re"); s.add(b); await s.flush()
        g = ScorecardGroup(tenant_id=t.id, business_id=b.id, key="davis", name="Davis", is_team_room=True)
        s.add(g); await s.flush()
        m = ScorecardMetric(tenant_id=t.id, group_id=g.id, name="Appointments Met", goal=Decimal("30"),
                            direction="gte", type="flow", active=True)
        s.add(m); await s.flush()
        # two closed weeks (7/27, 8/3) + the current in-progress week (8/10)
        for ws, v in {dt.date(2026, 7, 27): 33, dt.date(2026, 8, 3): 17, dt.date(2026, 8, 10): 0}.items():
            s.add(ScorecardValue(tenant_id=t.id, metric_id=m.id, week_start=ws, value=Decimal(v), source="resolver"))
        await s.commit()
        d = await build_scorecard(s, t.id, b.id, 13, today=dt.date(2026, 8, 11))   # Tue inside the 8/10 week

    c = d["groups"][0]["rows"][0]["cumulative"]["w13"]
    assert c["actual"] == 50 and c["target"] == 60          # 33+17 vs 30×2 — the 8/10 week does NOT count
    assert c["n"] == 2 and round(c["attain"]) == 83         # not 56% (which counting the 0 week would give)
    assert d["weeks"][-1]["start"] == "2026-08-10" and d["weeks"][-1]["complete"] is False   # shows, flagged
    assert d["weeks"][-2]["complete"] is True


async def test_current_period_goal_zero_does_not_crash():
    """A goal of 0 is a valid track-only state (e.g. Mastermind RSVPs). Setting the CURRENT period's
    goal to 0 on a metric with a full history must not 500 the whole scorecard (trend_4v4 regression)."""
    tok = await _owner()
    async with _client() as c:
        await c.put("/api/v1/ulrg/periods", headers=_H(tok), json={"periods": [
            {"key": "2026Q2", "start": "2026-04-13", "end": "2026-07-27"},
            {"key": "2026Q3", "start": "2026-07-28", "end": "2026-10-30"}]})
        g = (await c.get("/api/v1/ulrg/goals?period=2026Q3", headers=_H(tok))).json()["goals"]
        appt = next(x for x in g if x["name"] == "Appointments Met" and x["group"] == "Davis")
        r = await c.put("/api/v1/ulrg/goals", headers=_H(tok),
                        json={"period": "2026Q3", "goals": [{"metric_id": appt["metric_id"], "goal": 0}]})
        assert r.status_code == 200

        sc = await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))
        assert sc.status_code == 200                                          # was 500 before the fix
        row = next(rr for gg in sc.json()["groups"] if gg["key"] == "davis"
                   for rr in gg["rows"] if rr["measurable"] == "Appointments Met")
        assert row["goal"] == 0
        assert row["trend_4v4"] is None                                       # no scoreable goal → no trend
        assert (row["cumulative"] or {}).get("w13") is None                   # goal 0 → not a cumulative row
