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


def _anchored_periods():
    """Two contiguous 13-week periods planted around TODAY: the current one starts six weeks back,
    mid-window, and the prior one covers the rest of the 13-week board. Returns the prior period's
    last day (the boundary the board straddles) plus the two payload-ready date pairs.

    Pinned to absolute dates (2026-04-13 / 07-27 / 10-30) these rotted twice over, quietly and then
    loudly, because the scorecard reads the LIVE clock — build_scorecard takes a `today=`, but the
    HTTP route asks the server for the date, so a test going through HTTP cannot inject one:

      * 2026-10-26, silently: the trailing 13-week window cleared the old Q2 boundary, every shown
        week fell inside Q3, and the "history isn't recolored" loop stopped reaching its `default`
        branch — still green, no longer testing the thing its test is named after.
      * 2026-10-31, loudly: today left Q3 as well, `quarter_of` warned and fell back to the
        CALENDAR quarter, so `cur_key` became 2026Q4 and NO per-period override resolved at all.

    Anchored, both sides of the boundary are on the board and today is inside the current period on
    any date this is ever run. Monday-aligned (start Mon, end Sun) to match the L10 cadence, and
    13 weeks so `round((end - start).days / 7)` gives a whole quarter.
    """
    monday = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    boundary = monday - dt.timedelta(weeks=6)
    prior_end = boundary - dt.timedelta(days=1)
    return prior_end, {
        "prior": {"start": (boundary - dt.timedelta(weeks=13)).isoformat(), "end": prior_end.isoformat()},
        "current": {"start": boundary.isoformat(),
                    "end": (boundary + dt.timedelta(weeks=13) - dt.timedelta(days=1)).isoformat()}}


async def test_per_period_goals_override_and_history():
    tok = await _owner()
    # ANCHORED TO TODAY, not to July 2026 — see _anchored_periods for what rotted. The period KEYS
    # are unique to this test so its goal rows can never be confused with another test's: goals are
    # stored per (metric, period_key) and several tests here share one seeded tenant.
    prior_end, per = _anchored_periods()
    async with _client() as c:
        await c.put("/api/v1/ulrg/periods", headers=_H(tok), json={"periods": [
            {"key": "HIST", **per["prior"]}, {"key": "LIVE", **per["current"]}]})

        g0 = (await c.get("/api/v1/ulrg/goals?period=LIVE", headers=_H(tok))).json()["goals"]
        appt = next(x for x in g0 if x["name"] == "Appointments Met" and x["group"] == "Davis")
        assert appt["goal"] == appt["default"] and appt["overridden"] is False   # no override yet
        default = appt["default"]

        # override the CURRENT period's goal
        r = await c.put("/api/v1/ulrg/goals", headers=_H(tok),
                        json={"period": "LIVE", "goals": [{"metric_id": appt["metric_id"], "goal": default + 10}]})
        assert r.status_code == 200
        again = next(x for x in (await c.get("/api/v1/ulrg/goals?period=LIVE", headers=_H(tok))).json()["goals"]
                     if x["metric_id"] == appt["metric_id"])
        assert again["goal"] == default + 10 and again["overridden"] is True

        d = (await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json()
        row = next(rr for gg in d["groups"] if gg["key"] == "davis"
                   for rr in gg["rows"] if rr["measurable"] == "Appointments Met")
        assert row["goal"] == default + 10                                   # current-period goal
        # history isn't recolored: each shown week keeps ITS OWN period's goal. The board shows
        # through the current week, so weeks inside HIST stay at the default while the LIVE weeks
        # carry the new override.
        assert row["week_goals"] and len(row["week_goals"]) == len(row["values"])
        shown = [dt.date.fromisoformat(w["start"]) for w in d["weeks"]]
        # BOTH branches of the loop have to be reached, or it proves only the current period's half.
        # This is the check the pinned version lacked: once the window advanced past 7/27 the prior
        # period's count was 0 and the loop went on passing while asserting one thing, thirteen times.
        assert sum(1 for w in shown if w <= prior_end) == 6
        assert sum(1 for w in shown if w > prior_end) == 7
        for wg, w in zip(row["week_goals"], shown):
            assert wg == (default if w <= prior_end else default + 10)

        # now override the prior period too → those weeks' goals move, the current one is unaffected
        await c.put("/api/v1/ulrg/goals", headers=_H(tok),
                    json={"period": "HIST", "goals": [{"metric_id": appt["metric_id"], "goal": default - 5}]})
        d2 = (await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json()
        row2 = next(rr for gg in d2["groups"] if gg["key"] == "davis"
                    for rr in gg["rows"] if rr["measurable"] == "Appointments Met")
        assert [w["start"] for w in d2["weeks"]] == [w.isoformat() for w in shown]   # same board
        for wg, w in zip(row2["week_goals"], shown):                          # each week uses ITS period's goal
            assert wg == (default - 5 if w <= prior_end else default + 10)
        assert row2["goal"] == default + 10                                  # current still its own


async def _plant_weeks(measurable: str, week_values: dict) -> None:
    """Plant values for the Davis measurable whose name starts with `measurable`, at the given week
    starts, for the shared springb tenant. Idempotent: a week already carrying a value is updated,
    not duplicated.

    Davis only — `test_rename_measurable` deliberately works on `slc` so that Davis lookups
    elsewhere stay stable. Plant on MONDAYS INSIDE the trailing window: build_scorecard's week
    columns are the union of every stored week with that window, so a week off-Monday or in the
    future would shift the columns out from under the other tests sharing this tenant.
    """
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        g = (await s.execute(select(ScorecardGroup).where(
            ScorecardGroup.tenant_id == t.id, ScorecardGroup.key == "davis"))).scalars().first()
        m = (await s.execute(select(ScorecardMetric).where(
            ScorecardMetric.tenant_id == t.id, ScorecardMetric.group_id == g.id,
            ScorecardMetric.name.startswith(measurable)))).scalars().first()
        existing = {v.week_start: v for v in (await s.execute(select(ScorecardValue).where(
            ScorecardValue.tenant_id == t.id, ScorecardValue.metric_id == m.id))).scalars()}
        for ws, val in week_values.items():
            row = existing.get(ws)
            if row is None:
                s.add(ScorecardValue(tenant_id=t.id, metric_id=m.id, week_start=ws,
                                     value=Decimal(val), source="resolver"))
            else:
                row.value = Decimal(val)
        await s.commit()


async def _homes_weeks(week_values: dict) -> None:
    """Davis "Total Homes Sold" — see _plant_weeks."""
    await _plant_weeks("Total Homes Sold", week_values)


async def _appt_weeks(week_values: dict) -> None:
    """Davis "Appointments Met" — see _plant_weeks."""
    await _plant_weeks("Appointments Met", week_values)


async def test_cumulative_goal_is_period_scoped_thermometer():
    """A flow metric with a period-total (cumulative) goal becomes a quarter thermometer: it counts
    ONLY the weeks within the current period (not a trailing window that bleeds in the prior period)
    and tracks the running total toward the FULL total. Regression for the '96 of 110 after 1 week'
    bug — the actual must be period-to-date, not a rolling 13-week sum."""
    tok = await _owner()

    # ANCHORED TO TODAY, not to July 2026. The scorecard's trailing window is 13 weeks back from
    # the live clock, and this endpoint has no `today=` seam (build_scorecard takes one, but the
    # HTTP route asks the server for the date). Pinned to absolute weeks, the seeded July data
    # slid out of that window one week at a time: this test went red on the Monday the last
    # pre-period week fell off the end — and before that it had already stopped proving its
    # point, because `pd` and `trailing` had silently converged on the same set of weeks.
    #
    # So the test plants its own data on BOTH sides of the period boundary, inside the window,
    # relative to today. That relationship is now true on any date it is ever run.
    monday = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    boundary = monday - dt.timedelta(weeks=6)          # period starts mid-window, always
    # round((end - start).days / 7) is how the service counts a period's weeks, so 181 days
    # inclusive == 26 weeks, which keeps the pace assertion below at 260/26.
    cur_end = boundary + dt.timedelta(days=181)
    prev_end = boundary - dt.timedelta(days=1)
    prev_start = prev_end - dt.timedelta(days=181)
    pre = {monday - dt.timedelta(weeks=w): 4 for w in (11, 10, 9, 8, 7)}   # before the boundary
    inp = {monday - dt.timedelta(weeks=w): 9 for w in (5, 4, 3, 2, 1)}     # inside the period
    await _homes_weeks({**pre, **inp})

    async with _client() as c:
        await c.put("/api/v1/ulrg/periods", headers=_H(tok), json={"periods": [
            {"key": "PREV", "start": prev_start.isoformat(), "end": prev_end.isoformat()},
            {"key": "CUR", "start": boundary.isoformat(), "end": cur_end.isoformat()}]})

        g = (await c.get("/api/v1/ulrg/goals?period=CUR", headers=_H(tok))).json()["goals"]
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
        r = await c.put("/api/v1/ulrg/goals", headers=_H(tok), json={"period": "CUR",
            "goals": [{"metric_id": homes["metric_id"], "goal": 8, "cumulative_goal": 260}]})
        assert r.status_code == 200

        sc = (await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json()
        weeks = sc["weeks"]
        row1, c1 = _homes(sc)
        assert row1["goal"] == 8 and row1["cumulative_goal"] == 260   # weekly unchanged, total carried

        # actual counts ONLY weeks whose start is inside the period
        pstart = boundary
        pd = [v for v, w in zip(row1["values"], weeks)
              if v is not None and dt.date.fromisoformat(w["start"]) >= pstart]
        trailing = [v for v in row1["values"] if v is not None]
        assert c1["actual"] == round(sum(pd))                 # period-to-date, NOT the rolling sum
        # The whole point: pre-period weeks are inside the 13-week window and must NOT count.
        # If these two ever converge the assertion is vacuous rather than false, so the gap is
        # asserted explicitly instead of relying on `<` alone.
        assert sum(pd) == sum(inp.values())
        assert sum(trailing) >= sum(pd) + sum(pre.values())
        assert sum(pd) < sum(trailing)                        # proves pre-period data is excluded
        assert c1["target"] == 260                            # thermometer shows the FULL total
        assert c1["period"] is True and c1["pace"] == round(260 / 26)   # 26-week period → 10/wk
        # every window is the same quarter-to-date block (the toggle is a no-op for a thermometer)
        assert row1["cumulative"]["w4"] == c1 and row1["cumulative"]["qtd"] == c1

        # clearing it reverts to the rolling weekly-derived cumulative
        await c.put("/api/v1/ulrg/goals", headers=_H(tok), json={"period": "CUR",
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
    # ANCHORED TO TODAY — same rot as the history test, see _anchored_periods: pinned to
    # 2026-07-28/10-30 this asked for an override on a period that stops containing today, the
    # scorecard fell back to the calendar quarter, and `row["goal"] == 0` never saw the 0.
    _, per = _anchored_periods()
    # "a metric with a full history" is the PREMISE of this test, and the seeded weeks (ending 7/27)
    # were sliding out of the trailing 13. That matters because `cumulative` is also None when the
    # counted window holds no values at all: from 2026-10-26 the two `is None` assertions below
    # would have passed against an empty window, proving nothing about a goal of 0. So the history
    # is planted under today, on closed weeks inside the window, and the before-state is asserted.
    monday = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    await _appt_weeks({monday - dt.timedelta(weeks=w): 30 + w for w in range(1, 10)})

    def _appt(payload):
        return next(rr for gg in payload["groups"] if gg["key"] == "davis"
                    for rr in gg["rows"] if rr["measurable"] == "Appointments Met")

    async with _client() as c:
        await c.put("/api/v1/ulrg/periods", headers=_H(tok), json={"periods": [
            {"key": "GZ_HIST", **per["prior"]}, {"key": "GZ_LIVE", **per["current"]}]})
        g = (await c.get("/api/v1/ulrg/goals?period=GZ_LIVE", headers=_H(tok))).json()["goals"]
        appt = next(x for x in g if x["name"] == "Appointments Met" and x["group"] == "Davis")

        # the BEFORE state: on its own goal this row HAS a trend and a cumulative block. Asserting
        # that is what keeps the two `is None` checks below honest — they fail loudly if the data
        # ever leaves the counted window, instead of going quietly true.
        before = _appt((await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))).json())
        assert before["goal"] == appt["default"] and appt["default"] > 0
        assert before["trend_4v4"] is not None and before["cumulative"]["w13"] is not None

        r = await c.put("/api/v1/ulrg/goals", headers=_H(tok),
                        json={"period": "GZ_LIVE", "goals": [{"metric_id": appt["metric_id"], "goal": 0}]})
        assert r.status_code == 200

        sc = await c.get("/api/v1/ulrg/scorecard", headers=_H(tok))
        assert sc.status_code == 200                                          # was 500 before the fix
        row = _appt(sc.json())
        assert row["goal"] == 0
        assert row["values"] == before["values"] and any(v is not None for v in row["values"])
        assert row["trend_4v4"] is None                                       # no scoreable goal → no trend
        assert (row["cumulative"] or {}).get("w13") is None                   # goal 0 → not a cumulative row
