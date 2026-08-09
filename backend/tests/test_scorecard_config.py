"""Scorecard self-service office config (Phase A): edit owner full name (owner/admin) + upload a
headshot served publicly (for the app AND the read-only embed)."""
import datetime as dt
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant
from app.seed_ulrg_scorecard import load_ulrg_scorecard

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
        # every shown week is in Q2 (data ends 7/27) → still the default, NOT the new Q3 goal (history kept)
        assert row["week_goals"] and all(wg == default for wg in row["week_goals"])
        assert len(row["week_goals"]) == len(row["values"])

        # now override Q2 too → those weeks' goals move, current (Q3) unaffected
        await c.put("/api/v1/ulrg/goals", headers=_H(tok),
                    json={"period": "2026Q2", "goals": [{"metric_id": appt["metric_id"], "goal": default - 5}]})
        d2 = (await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json()
        row2 = next(rr for gg in d2["groups"] if gg["key"] == "davis"
                    for rr in gg["rows"] if rr["measurable"] == "Appointments Met")
        assert all(wg == default - 5 for wg in row2["week_goals"])            # Q2 weeks use the Q2 goal
        assert row2["goal"] == default + 10                                  # current (Q3) still its own


async def test_cumulative_goal_drives_period_total_tracking():
    """A flow metric can carry a period-total (cumulative) goal separate from its weekly goal, so the
    cumulative block tracks toward the real quarter total (e.g. 130 homes) — not weekly × weeks."""
    tok = await _owner()
    async with _client() as c:
        await c.put("/api/v1/ulrg/periods", headers=_H(tok), json={"periods": [
            {"key": "2026Q2", "start": "2026-04-13", "end": "2026-07-27"},
            {"key": "2026Q3", "start": "2026-07-28", "end": "2026-10-30"}]})

        g = (await c.get("/api/v1/ulrg/goals?period=2026Q3", headers=_H(tok))).json()["goals"]
        homes = next(x for x in g if x["name"].startswith("Total Homes Sold") and x["group"] == "Davis")
        assert homes["supports_cumulative"] is True and homes["cumulative_goal"] is None
        # a rate metric does NOT support a cumulative total
        rate = next(x for x in g if x["type"] == "rate")
        assert rate["supports_cumulative"] is False

        def _homes_cum(sc):
            row = next(rr for gg in sc["groups"] if gg["key"] == "davis"
                       for rr in gg["rows"] if rr["measurable"].startswith("Total Homes Sold"))
            return row, row["cumulative"]["w13"]

        # baseline: no cumulative goal → target is weekly(8) × weeks
        base = (await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json()
        row0, c0 = _homes_cum(base)
        assert row0["cumulative_goal"] is None
        assert c0["target"] == round(8 * c0["n"])

        # set the period total to 130 (weekly stays 8)
        r = await c.put("/api/v1/ulrg/goals", headers=_H(tok), json={"period": "2026Q3",
            "goals": [{"metric_id": homes["metric_id"], "goal": 8, "cumulative_goal": 130}]})
        assert r.status_code == 200
        got = next(x for x in (await c.get("/api/v1/ulrg/goals?period=2026Q3", headers=_H(tok))).json()["goals"]
                   if x["metric_id"] == homes["metric_id"])
        assert got["goal"] == 8 and got["cumulative_goal"] == 130

        sc = (await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json()
        row1, c1 = _homes_cum(sc)
        assert row1["goal"] == 8                          # weekly unchanged (colours the cells)
        assert row1["cumulative_goal"] == 130             # period total carried in the payload
        q3w = max(1, round((dt.date(2026, 10, 30) - dt.date(2026, 7, 28)).days / 7))   # quarter_of's formula
        assert c1["target"] == round((130 / q3w) * c1["n"])   # cumulative now paces toward 130
        assert c1["target"] != c0["target"]                   # and genuinely differs from weekly×weeks

        # clearing it (0 / null) reverts to the weekly-derived target
        await c.put("/api/v1/ulrg/goals", headers=_H(tok), json={"period": "2026Q3",
            "goals": [{"metric_id": homes["metric_id"], "goal": 8, "cumulative_goal": None}]})
        sc2 = (await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json()
        row2, c2 = _homes_cum(sc2)
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
