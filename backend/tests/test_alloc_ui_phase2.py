"""Phase 2 of ALLOCATIONS-UI-SPEC: the page shell, and the month it opens on.

The month default is the one decision in this build that verification changed (0.1 item 1).
The audit asked for "the last complete month", which on 4 November is October -- a month this
module is not authoritative for, where every live rule is effective later and the whole page
renders as zeros. The rule built instead is "the last complete month, but never earlier than
the month this module went live", and these are the tests that hold it, across the dates where
the two rules disagree.

The default lives on the SERVER for the same reason every compute entry point here takes an
injected `today`: a `new Date()` in a React component is a clock read no test can travel.
"""
import datetime as dt

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Tenant
from app.seed import seed
from app.services import alloc_engine as E
from app.services.coa_balances import allocation_months


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def _default_month(today: dt.date, live_from: dt.date = E.MODULE_LIVE_FROM):
    """The route's rule, extracted so it can be asked about a date that is not today.

    Kept in step with `alloc_summary` by `test_the_route_agrees_with_this_helper` below: a
    helper that drifts from the code it models is worse than no test, because it keeps
    passing.
    """
    series = allocation_months(today, 1)
    if not series:
        return None
    complete = [m for m in series if m[1] < today] or series
    out = max(complete[-1][0], live_from)
    return min(out, series[-1][0])


def test_on_the_fourth_of_november_it_opens_on_november_not_october():
    """The case the audit got wrong. October is complete, and it is also a month Books
    Cleanup owns and every live rule post-dates, so it renders as zeros."""
    assert _default_month(dt.date(2026, 11, 4)) == dt.date(2026, 11, 1)


def test_in_december_it_opens_on_november_which_is_both_complete_and_ours():
    """Where the two rules agree, and the audit's intent takes over: closing a month is the
    job, so once November is complete that is what the page opens on."""
    assert _default_month(dt.date(2026, 12, 9)) == dt.date(2026, 11, 1)


def test_in_january_december_is_not_reachable_at_all_and_that_is_a_gap():
    """A KNOWN HOLE, asserted so it is a decision rather than a surprise.

    On 20 January the month to close is December, and the page cannot open on it or offer
    it: `allocation_months` runs from the FISCAL YEAR START through today (5.2), so in
    January the whole series is one month and December belongs to the previous year.

    Not fixed here on purpose. The same function bounds what `derive_all` and
    `reconciliation` compute, so widening it changes what the ENGINE derives, which is a
    larger decision than a UI phase should take on its own. Recorded in the build record and
    raised with Connor.
    """
    today = dt.date(2027, 1, 20)
    series = allocation_months(today, 1)
    assert [m[0] for m in series] == [dt.date(2027, 1, 1)], \
        "if this list grows, the hole below may be fixed and this test should change"
    assert _default_month(today) == dt.date(2027, 1, 1), \
        "December is unreachable, so the page opens on the month it is"


def test_it_never_opens_before_the_module_went_live():
    """Every month of the fiscal year before the cutover belongs to Books Cleanup. Asked on a
    date where the last complete month is one of those, the page still opens on the first
    month this module can answer for."""
    for day in (dt.date(2026, 3, 2), dt.date(2026, 7, 15), dt.date(2026, 11, 1)):
        got = _default_month(day)
        assert got >= min(E.MODULE_LIVE_FROM, allocation_months(day, 1)[-1][0]), day


def test_it_never_offers_a_month_the_fiscal_series_does_not_have():
    """The clamp at the top. Before the cutover the live-from date is in the FUTURE, and a
    page opening on a month with no balances behind it is the same failure in the other
    direction."""
    today = dt.date(2026, 7, 15)
    series = allocation_months(today, 1)
    assert _default_month(today) <= series[-1][0], "cannot open past the current month"


def test_the_month_before_any_is_complete_falls_back_to_the_one_it_is():
    """1 January: nothing is complete yet, and a page with no month at all is worse than one
    showing four days."""
    today = dt.date(2026, 1, 1)
    assert _default_month(today) == allocation_months(today, 1)[-1][0]


async def test_the_route_agrees_with_this_helper_on_today():
    """Holds the helper above and `alloc_summary` in step. Without this the tests could all
    pass against a rule the product does not implement."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        login = await c.post("/api/v1/auth/login",
                             json={"email": "spring@springb.com", "password": "springtime"})
        token = login.json()["token"]
        r = await c.get("/api/v1/books/allocations/summary",
                        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["default_month"] == _default_month(dt.date.today()).isoformat()
    assert body["months"], "the month control needs options to render"
    assert body["default_month"] in body["months"], \
        "the default has to be selectable, or the control opens on nothing"
    for key in ("unclaimed_total", "unclaimed_count", "payee_count", "partly_covered_count",
                "pending_rules", "pending_amount", "drivers"):
        assert key in body, f"the strip and the badges read {key}"
