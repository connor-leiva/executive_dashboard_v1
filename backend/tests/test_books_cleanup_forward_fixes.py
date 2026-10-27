"""Books Cleanup -- the months ahead where they meet the books: the month the books end in.

That month is both. The books hold it as far as they go; the forecast's row for it is the whole month, the books'
part and the rest of it projected. These pin how the two meet, on the invented file (test_books_cleanup.py) with its
books ending half-way through month 3 -- Mar 15, as its own projected company's block already reads them -- and the
club's block built from the invented members (test_books_cleanup_live.py):

  * the Statement's months ahead add only the rest of that month's partner share, so the share through the horizon
    is the forecast's, month for month, not the books' part of that month counted twice;
  * the owner's salary comes off that month's whole profit to the month's end, ahead of a partner whose agreement
    puts it first -- not to the books' last day, which overstated that month's share and understated the next;
  * a block whose cash starts from the books' last day pays its partner, that month, what the books paid and the
    rest of the month at the forecast's pay, as its cash flow does (the file's own block keeps what it had);
  * a bad percentage or number of members says what it should be, not "an amount in dollars".
"""
import pytest

from app.services import books_cleanup as eng
from app.services import cleanup_live as live
from tests.test_books_cleanup import CLUB, COHORT, _l, forwarded
from tests.test_books_cleanup_live import TODAY, block, records

SALARY, RATE = 12000.0, 0.2                 # the owner's, a year, ahead of Pat; Pat's share of the club's profit
PROFIT = {"renew_pct": 100, "new_members": 2, "new_value": 24000}       # the club making money in the months ahead


def mid_month(paid=0.0) -> dict:
    """The invented file with its books ending Mar 15: the owner's salary to date on that day, the club earning
    $3,000 of dues a month (so it makes money past the salary), and Pat paid `paid` in March by then."""
    data = forwarded()
    data["period"]["end"] = "2026-03-15"
    data["period"]["salary_fraction"]["3"] = (2 + 15 / 31) / 12
    data["dues"]["earned"] = {str(m): 3000.0 for m in (1, 2, 3)}
    if paid:
        data["ledger"].append(_l(CLUB, "Club", "partner", None, 3, paid, "Pat payable"))
    return data


def projected(saved=None, paid=0.0):
    """The books, the cohort's months ahead (the file's own block) and the club's (from the members' records)."""
    data = mid_month(paid)
    bk = eng.Books(data, eng.merged_inputs(data, saved))
    own, club = bk.forwards([live.build(data, *records(), TODAY)])
    assert (own["company"], club["company"]) == (COHORT, CLUB)
    return bk, own, club


def by_key(rows):
    return {r["key"]: r for r in rows}


# ── the month the books end in ──────────────────────────────────────────────────────────────────

def test_the_books_end_half_way_through_march():
    bk, own, club = projected()
    assert club["months"][0] == {"key": "2026-03", "label": "Mar 16-31"} and club["months_ahead"] == pytest.approx(16 / 31 + 12, abs=0.01)
    assert club["cash"]["from_books"] and club["cash"]["rows"][0]["key"] == "2026-03"
    # both partners had earned something in March by the 15th, so counting it twice would show
    assert bk.share[(COHORT, 3)] == 1110.0 and bk.share[(CLUB, 3)] > 100


def test_the_statement_counts_the_partner_share_of_the_month_the_books_end_in_once():
    bk, own, club = projected({"forward": {CLUB: PROFIT}})
    for f in (own, club):
        line = next(l for s in f["statement"]["sections"] for l in s["lines"] if l["line"] == "Partner profit shares")
        future = sum(r["share"] for r in f["rows"] if r["projected"])
        # the months ahead: March's row, less what the books already hold for March, and every month after it
        assert f["future_by_line"]["Partner profit shares"] == pytest.approx(future - bk.share[(f["company"], 3)], abs=0.01)
        assert line["ahead"] == f["future_by_line"]["Partner profit shares"]
        # so the share through the horizon is the forecast's, month for month
        assert line["so_far"] == pytest.approx(sum(bk.share[(f["company"], m)] for m in bk.MONTHS), abs=0.01)
        assert line["through"] == pytest.approx(sum(r["share"] for r in f["rows"]), abs=0.05)
        assert line["through"] == pytest.approx(f["summary"]["earned"], abs=0.05)
    assert own["future_by_line"]["Partner profit shares"] == 1000.0      # all of March's 1,210 less the books' 1,110; 450; 450
    assert club["summary"]["earned"] > 1000                               # a real share through the horizon, not just rounding


def test_the_owners_salary_comes_off_the_month_the_books_end_in_to_its_end():
    _, _, club = projected({"forward": {CLUB: PROFIT}})
    rows = by_key(club["rows"])
    base = lambda k: max(0.0, rows[k]["ytd"] - SALARY * int(k[5:7]) / 12)    # the year's profit past the salary to date
    assert base("2026-03") > 0 and base("2026-02") > 0
    # March's row is all of March's profit, so the salary to Mar 31 ($3,000) comes off it, not the salary to the 15th
    # ($2,483.87) -- which put $103 of April's share in March
    for prev, k in (("2026-02", "2026-03"), ("2026-03", "2026-04"), ("2026-04", "2026-05")):
        assert rows[k]["share"] == pytest.approx(RATE * (base(k) - base(prev)), abs=0.01)


def test_a_block_from_the_books_pays_its_partner_the_books_pay_then_the_rest_of_the_month():
    pay = {"forward": {CLUB: {"partner_pay": 300.0, "partner_end": "2027-03"}}}
    rest = 300.0 * 16 / 31                                                # Mar 16-31 at $300 a month
    for paid in (0.0, 250.0):
        _, own, club = projected(pay, paid=paid)
        rows, cash = by_key(club["rows"]), by_key(club["cash"]["rows"])
        assert cash["2026-03"]["partner"] == pytest.approx(rest, abs=0.005)
        # what the books paid by the 15th, then the rest of March as the cash flow pays it
        assert rows["2026-03"]["paid_month"] == pytest.approx(paid + cash["2026-03"]["partner"], abs=0.01)
        assert rows["2026-04"]["paid_month"] == cash["2026-04"]["partner"] == 300.0
        # the file's own block starts its cash from a balance a whole month at a time: its March is what the books paid
        assert by_key(own["rows"])["2026-03"]["paid_month"] == 800.0


# ── what a bad assumption is told ───────────────────────────────────────────────────────────────

def with_club() -> dict:
    return {**forwarded(), "forward_live": [block()]}


@pytest.mark.parametrize("path,value,said", [
    (["forward", CLUB, "renew_pct"], 101, "A percentage from 0 to 100."),
    (["forward", CLUB, "renew_pct"], -1, "A percentage from 0 to 100."),
    (["forward", CLUB, "renew_pct"], "most", "A percentage from 0 to 100."),
    (["forward", CLUB, "renew_pct"], float("nan"), "A percentage from 0 to 100."),
    (["forward", "commission_pct"], 100.5, "A percentage from 0 to 100."),          # the file's own company's
    (["forward", CLUB, "new_members"], 1001, "A number of members, 0 to 1,000."),
    (["forward", CLUB, "new_members"], "a few", "A number of members, 0 to 1,000."),
    (["forward", CLUB, "new_value"], -5, "An amount in dollars, from zero up."),    # dollars still say dollars
    (["forward", CLUB, "partner_pay"], None, "An amount in dollars."),
    (["budget", CLUB, "Staff and contractors"], 10_000_001, "An amount in dollars, from zero up."),
])
def test_a_bad_assumption_says_what_it_should_be(path, value, said):
    with pytest.raises(eng.InputError) as bad:
        eng.validate_change(with_club(), path, value)
    assert str(bad.value) == said


def test_what_an_assumption_accepts_is_what_it_was():
    data = with_club()
    for path, value in ((["forward", CLUB, "renew_pct"], 0), (["forward", CLUB, "renew_pct"], 100),
                        (["forward", CLUB, "renew_pct"], 62.5), (["forward", "commission_pct"], 100),
                        (["forward", CLUB, "new_members"], 0), (["forward", CLUB, "new_members"], 1000),
                        (["forward", CLUB, "new_members"], 2.5)):
        assert eng.validate_change(data, path, value) == value
