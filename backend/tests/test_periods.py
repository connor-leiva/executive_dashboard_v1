"""The period vocabulary (2026-08-16): YTD vs full Year, next month, and custom ranges.

The whole product passes ONE period string end to end, so these functions are the single
contract every surface depends on. A custom range is encoded "c:YYYY-MM-DD:YYYY-MM-DD" —
which is why ~20 call sites that merely forward `period` needed no change at all.
"""
import datetime as dt

from app.services.metrics import (
    _period_range, _pl_period, parse_custom, custom_period, period_label,
    has_booked_snapshot, is_forward, _fw_label,
)
from app.services.financials import _period, _projection_end


def test_ytd_is_to_date_and_year_is_the_whole_calendar_year():
    today = dt.date.today()
    ytd_s, ytd_e = _period_range("ytd")
    yr_s, yr_e = _period_range("year")
    assert (ytd_s, ytd_e) == (dt.date(today.year, 1, 1), today)          # unchanged behavior
    assert (yr_s, yr_e) == (dt.date(today.year, 1, 1), dt.date(today.year, 12, 31))
    # both read the SAME QuickBooks snapshot key, so 'year' gets its booked P&L for free
    assert _pl_period("year") == _pl_period("ytd") == (dt.date(today.year, 1, 1),
                                                       dt.date(today.year, 12, 31))
    assert period_label("ytd") == "Year to date"
    assert period_label("year") == f"Full year {today.year}"


def test_next_month_is_the_whole_upcoming_month_and_is_forward():
    today = dt.date.today()
    start, end = _period_range("next_month")
    assert start.day == 1 and start > today                     # first day of the NEXT month
    assert (start.year, start.month) == ((today.year + 1, 1) if today.month == 12
                                         else (today.year, today.month + 1))
    assert end.month == start.month and (end + dt.timedelta(days=1)).month != end.month  # month end
    assert is_forward("next_month") is True
    assert has_booked_snapshot("next_month") is False           # no QBO snapshot for the future
    assert not is_forward("mtd") and not is_forward("last_month")


def test_custom_range_round_trips_and_scopes_every_window():
    p = custom_period(dt.date(2026, 1, 15), dt.date(2026, 3, 20))
    assert p == "c:2026-01-15:2026-03-20"
    assert parse_custom(p) == (dt.date(2026, 1, 15), dt.date(2026, 3, 20))
    assert _period_range(p) == (dt.date(2026, 1, 15), dt.date(2026, 3, 20))
    assert _pl_period(p) == (dt.date(2026, 1, 15), dt.date(2026, 3, 20))
    assert has_booked_snapshot(p) is False                      # arbitrary range has no snapshot
    assert period_label(p) == "Jan 15 – Mar 20, 2026"
    assert "Jan 15" in _fw_label(p)                             # flywheel scope phrase too


def test_malformed_custom_falls_back_to_mtd_instead_of_raising():
    """~20 call sites forward `period` straight through; a bad string must never 500 them.
    The payload echoes the RESOLVED window back, so the UI can't mislabel the fallback."""
    mtd = _period_range("mtd")
    for bad in ("c:garbage", "c:2026-13-01:2026-12-01", "c:2026-06-01:2026-01-01",
                "c:2026-01-01", "", "nonsense", "c:2020-01-01:2099-01-01"):   # last = >5yr span
        assert parse_custom(bad) is None, bad
        assert _period_range(bad) == mtd, bad


def test_financials_period_helpers_match_the_canonical_resolver():
    """financials.py used to duplicate the period math; it now delegates. is_current must
    still reproduce the old per-period booleans exactly."""
    today = dt.date.today()
    for p in ("mtd", "qtd", "ytd"):
        start, end, is_current = _period(p)
        assert (start, end) == _period_range(p) and is_current is True
    lm_start, lm_end, lm_current = _period("last_month")
    assert (lm_start, lm_end) == _period_range("last_month") and lm_current is False
    # a closed custom window is not current; one running through today is
    past = custom_period(dt.date(2026, 1, 1), dt.date(2026, 1, 31))
    assert _period("c:2026-01-01:2026-01-31")[2] is (dt.date(2026, 1, 31) >= today)
    assert _period(custom_period(today - dt.timedelta(days=5), today))[2] is True
    assert _period("next_month")[2] is True          # forward window is open for pending deals
    # the projection window is the period's CALENDAR end
    for p in ("mtd", "qtd", "ytd", "last_month", "next_month", past):
        s, e, _ = _period(p)
        assert _projection_end(p, s, e) == _pl_period(p)[1], p


def test_snapshot_periods_are_exactly_what_qbo_syncs():
    """has_booked_snapshot must not drift from the periods the QBO sync actually stores."""
    from app.services.sync import _QBO_PERIODS
    for p in _QBO_PERIODS:
        assert has_booked_snapshot(p), p
    # 'year' isn't synced separately — it shares ytd's key, which IS synced
    assert has_booked_snapshot("year") and _pl_period("year") == _pl_period("ytd")


# ── end to end: the string survives the whole request path ────────────────────────────────
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.seed import seed, OWNER_EMAIL, OWNER_PASSWORD


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _token(c):
    r = await c.post("/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def test_dashboard_accepts_every_period_and_echoes_the_resolved_window():
    """Proves the wire contract: one ?period= string, and the payload reports back the window
    it actually used (label/start/end/key) so the UI can never mislabel what it rendered."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        H = {"Authorization": f"Bearer {await _token(c)}"}
        seen = {}
        for p in ("mtd", "qtd", "ytd", "year", "last_month", "next_month",
                  "c:2026-01-15:2026-03-20"):
            r = await c.get(f"/api/v1/dashboard?period={p}", headers=H)
            assert r.status_code == 200, (p, r.text)
            per = r.json()["period"]
            assert per["key"] == p, p
            assert (per["start"], per["end"]) == tuple(x.isoformat() for x in _period_range(p)), p
            seen[p] = per

        assert seen["ytd"]["label"] == "Year to date"
        assert seen["year"]["label"].startswith("Full year")
        assert seen["ytd"]["start"] == seen["year"]["start"]          # same Jan 1 …
        assert seen["ytd"]["end"] != seen["year"]["end"]              # … different end
        assert seen["c:2026-01-15:2026-03-20"]["label"] == "Jan 15 – Mar 20, 2026"
        assert seen["next_month"]["forward"] is True
        assert seen["next_month"]["booked_available"] is False
        assert seen["c:2026-01-15:2026-03-20"]["booked_available"] is False
        assert seen["mtd"]["booked_available"] is True

        # a malformed range resolves to MTD and SAYS so — no silent mislabeling
        bad = (await c.get("/api/v1/dashboard?period=c:nope", headers=H)).json()["period"]
        assert bad["label"] == "Month to date"
        assert (bad["start"], bad["end"]) == (seen["mtd"]["start"], seen["mtd"]["end"])


async def test_business_financials_accept_custom_and_forward_periods():
    """The other big period consumer: three-lens financials. A window with no QuickBooks
    snapshot must flag the booked lens rather than report a phantom $0."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        H = {"Authorization": f"Bearer {await _token(c)}"}
        for p in ("mtd", "year", "next_month", "c:2026-02-01:2026-04-30"):
            r = await c.get(f"/api/v1/businesses/ulrg/financials?period={p}", headers=H)
            assert r.status_code == 200, (p, r.text)
            j = r.json()
            assert j["period"]["key"] == p
            booked = j["lenses"]["booked"]
            if p in ("mtd", "year"):
                assert booked.get("flag") != "no_snapshot", p
            else:
                assert booked.get("flag") == "no_snapshot", p     # absence, not a $0 result
