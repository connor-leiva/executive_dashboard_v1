"""Books Cleanup -- the Forum's months ahead, projected from its members' records rather than the audit file.

On the invented three-company file (test_books_cleanup.py), the club is the company the file keeps a dues schedule
for; here it gets five invented members as the CRM and the payment feeds would hold them, on the books' last day
(Mar 31) and a "today" three weeks later:

  * Ada pays in full, $1,200 a year, renewing Jul 1 -- paid last July, so part of it is paid ahead (deferred);
  * Ben pays $100 a month on a subscription, renewing May 15 -- paid through March, and once since;
  * Cy joined Apr 10, after the books, $2,000 paid quarterly -- a contract in hand, counted in full;
  * Di has no renewal date -- left out, and named;
  * Ed's renewal date was never moved on (Feb 1) and nothing was paid toward the term -- flagged;
  * Flo is an add-on seat on Ada's contract, with nothing of her own to earn.

The properties pinned: each member's term is earned day by day, the paid part first; what's owed comes in as it
has and as it's scheduled, never more than is owed; a renewal is counted at the forecast's rate, paid the way that
member pays; The Edge's charges are not the Forum's; and everything the books already do (the partner's share
after the owner's salary, the file's own projected company) is untouched by a second projected company.
"""
import datetime as dt
from types import SimpleNamespace as NS

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import SessionLocal
from app.main import app
from app.models import Business, MetricRecord, Tenant, User
from app.security import hash_pw, make_token
from app.services import books_cleanup as eng
from app.services import cleanup_live as live
from tests.test_books_cleanup import CLUB, COHORT, COMPANIES, dataset, forwarded

TODAY = dt.date(2026, 4, 20)
D = dt.date


def member(cid, name, total, renewal, plan, enrolled=None, kind="primary"):
    mem = {"total_cost": total, "payment": plan, "member_kind": kind}
    if renewal:
        mem["renewal_date"] = renewal
    if enrolled:
        mem["enrollment_date"] = enrolled
    return NS(external_id=cid, name=name, email=f"{name.lower()}@club.test", status="active",
              source_url=f"https://crm.test/{cid}", meta={"membership": mem})


def pay(cid, name, day, amount, what="Forum membership", **meta):
    return NS(external_id=f"{cid}-{day}-{amount}", name=name, email=f"{name.lower()}@club.test", amount=amount,
              status="succeeded", occurred_on=day, meta={"contact_id": cid, "entity_source_name": what,
                                                          "stream": "memberships", **meta})


def records():
    members = [member("a", "Ada", 1200, "2026-07-01", "pif"),
               member("b", "Ben", 1200, "2026-05-15", "monthly"),
               member("c", "Cy", 2000, "2027-04-10", "quarterly", enrolled="2026-04-10"),
               member("d", "Di", 900, None, "pif"),
               member("e", "Ed", 1000, "2026-02-01", "pif"),
               member("f", "Flo", 0, "2026-07-01", None, kind="add_on")]      # a seat on Ada's contract
    payments = [pay("a", "Ada", D(2025, 7, 1), 1200),
                pay("b", "Ben", D(2026, 3, 2), 50, what="The Edge Monthly"),          # not the Forum's
                pay("b", "Ben", D(2026, 3, 3), 25, what="VIP ticket", stream="event_tickets")]
    payments += [pay("b", "Ben", D(2025 + (4 + i) // 12, (4 + i) % 12 + 1, 15), 100) for i in range(12)]  # May 15 .. Apr 15
    payments += [pay("b", "Ben", D(2025, 4, 15), 100)]                       # the last of his term before
    payments += [pay("c", "Cy", D(2026, 4, 10), 500)]
    subs = [NS(name="Ben", email="ben@club.test", status="active", amount=100,
               meta={"contact_id": "b", "interval": "month", "start_date": "2025-05-15", "next_payment_date": "2026-05-15"})]
    lost = [NS(occurred_on=D(2025, 11, 3)), NS(occurred_on=D(2024, 1, 1))]   # one in the last twelve months
    return members, payments, subs, lost


def block(data=None):
    m, p, s, lost = records()
    return live.build(data or forwarded(), m, p, s, lost, TODAY)


def term_earned(value, start, end, day):
    return value * ((day + dt.timedelta(days=1)) - start).days / (end - start).days


# ── the block, from the records ─────────────────────────────────────────────────────────────────

def test_the_block_is_the_dues_companys_through_a_year_after_the_books():
    b = block()
    assert b["company"] == CLUB and b["source"] == "live" and b["revenue_line"] == "Membership dues"
    assert b["schedule"][0]["key"] == "2026-03" and b["horizon"] == "2027-03"
    assert b["first_fraction"] == 0                                   # the books end on the month's last day
    # a file that projects the dues company itself, or keeps no dues schedule, gets no block
    assert live.build({**forwarded(), "forward": {**forwarded()["forward"], "company": CLUB}}, *records(), TODAY) is None
    assert live.build({**dataset(), "dues": {}}, *records(), TODAY) is None


def test_each_term_is_earned_day_by_day_and_paid_ahead_is_deferred():
    b = block()
    c = b["contracts"]
    end = D(2026, 3, 31)
    ada = term_earned(1200, D(2025, 7, 1), D(2026, 7, 1), end)
    ben = term_earned(1200, D(2025, 5, 15), D(2026, 5, 15), end)
    ed = term_earned(1000, D(2026, 2, 1), D(2027, 2, 1), end)
    assert c["members"] == 4                                          # Di is left out
    assert c["contracted"] == 1200 + 1200 + 2000 + 1000
    assert c["collected"] == 1200 + 1100                              # by the books' last day; Edge and tickets aren't dues
    assert c["still_to_collect"] == 100 + 2000 + 1000
    assert c["earned_so_far"] == pytest.approx(ada + ben + ed, abs=0.02)
    # the part of each term already paid for is earned first
    from_paid = sum(s["from_paid"] for s in b["schedule"])
    assert from_paid == pytest.approx((1200 - ada) + (1100 - ben), abs=0.05)
    # everything in hand is earned by the books' end or in the year after it, but for Cy's last nine days
    earned_ahead = sum(s["from_paid"] + s["from_future"] for s in b["schedule"])
    assert earned_ahead + c["earned_so_far"] + 2000 * 9 / 365 == pytest.approx(c["contracted"], abs=0.05)


def test_whats_owed_comes_in_as_it_has_and_as_its_scheduled():
    coll = block()["collections"]
    # Ben's last $100 came Apr 15 (his subscription keeps billing, but the term is paid); Cy paid $500 Apr 10 and
    # pays a quarter each three months after
    assert coll == {"2026-04": 600.0, "2026-07": 500.0, "2026-10": 500.0, "2027-01": 500.0}


def test_never_more_comes_in_than_is_owed():
    m, p, s, lost = records()
    p = [x if x.meta["contact_id"] != "c" else pay("c", "Cy", D(2026, 4, 10), 600) for x in p]   # $100 over
    coll = live.build(forwarded(), m, p, s, lost, TODAY)["collections"]
    assert coll["2026-04"] == 100 + 600 and coll["2027-01"] == 400.0                # the last quarter is what's left
    cy = next(r for r in live.build(forwarded(), m, p, s, lost, TODAY)["detail"]["members"] if r["name"] == "Cy")
    assert cy["since"] + cy["scheduled"] == 2000


def test_renewals_due_are_counted_at_100_and_paid_the_way_each_member_pays():
    r = block()["renewals"]
    assert (r["2026-05"]["count"], r["2026-05"]["value"]) == (1, 1200.0)                 # Ben
    assert (r["2026-07"]["count"], r["2026-07"]["collected"]) == (1, 1200.0 + 100.0)     # Ada in full on the day; Ben
    assert r["2026-06"]["collected"] == 100.0 and r["2027-03"]["collected"] == 100.0     # Ben, monthly
    assert (r["2027-02"]["count"], r["2027-02"]["collected"]) == (1, 1000.0 + 100.0)     # Ed in full; Ben
    # Ada's next term is earned from Jul 1; what falls in the year ahead
    assert sum(x["earned"] for x in r.values()) == pytest.approx(
        1200 * 274 / 365 + 1200 * (D(2027, 4, 1) - D(2026, 5, 15)).days / 365 + 1000 * 59 / 365, abs=0.05)


def test_a_renewal_already_paid_or_in_the_crm_is_a_contract_in_hand():
    m, p, s, lost = records()
    p.append(pay("a", "Ada", D(2026, 4, 1), 50))                 # unrelated: before her renewal
    paid_early = live.build(forwarded(), m, p, s, lost, D(2026, 7, 5))
    assert paid_early["renewals"].get("2026-07", {}).get("count") == 1               # not paid since Jul 1: still due
    p.append(pay("a", "Ada", D(2026, 7, 2), 1200))
    b = live.build(forwarded(), m, p, s, lost, D(2026, 7, 5))
    assert "2026-07" not in b["renewals"] or b["renewals"]["2026-07"]["count"] == 0
    assert b["contracts"]["contracted"] == 5400 + 1200 and b["detail"]["counts"]["renewed"] == 1
    m[0].meta["membership"]["renewal_date"] = "2027-07-01"        # the CRM moved it on a year: in hand, unpaid yet
    b = live.build(forwarded(), m, records()[1], s, lost, TODAY)
    assert b["contracts"]["contracted"] == 5400 + 1200 and b["collections"]["2026-07"] == 500 + 1200


def test_what_the_records_cant_place_is_named():
    d = block()["detail"]
    assert d["left_out"] == [{"name": "Di", "why": "no renewal date", "source_url": "https://crm.test/d"}]
    ed = next(r for r in d["members"] if r["name"] == "Ed")
    assert ed["flag"] == "Nothing paid toward this term, and nothing scheduled" and d["members"][0] is ed
    assert d["counts"]["nothing"] == 1 and d["counts"]["joined"] == 1 and d["counts"]["due"] == 3
    assert d["counts"]["add_ons"] == 1 and all(r["name"] != "Flo" for r in d["members"])    # not left out either


def test_the_renewal_rate_starts_from_the_last_twelve_months_and_new_members_from_todays():
    b = block()
    assert b["retention"] == {"lost12": 1, "members": 6}
    assert b["assumptions"]["renew_pct"] == round(100 * (1 - 1 / 7))
    assert b["assumptions"]["new_value"] == round((1200 + 1200 + 2000 + 1000) / 4)
    assert b["mix"]["pif"] == 0.5                                 # Ada and Ed of the four with a plan and a value


def test_each_months_bar_is_made_of_its_members():
    """What the chart opens to: every member's part of each month, the paid part and the part still to come, adding
    back to the bar -- the same release of what's paid first, member by member."""
    b = block()
    names = {m["name"] for m in b["detail"]["members"]}
    keys = {s["key"] for s in b["schedule"]}
    e = b["earning"]["members"]
    assert {m["name"] for m in e} <= names and all(set(m["months"]) <= keys for m in e)
    for s in b["schedule"]:
        p = sum(m["months"].get(s["key"], [0, 0])[0] for m in e)
        f = sum(m["months"].get(s["key"], [0, 0])[1] for m in e)
        assert p == pytest.approx(s["from_paid"], abs=0.02) and f == pytest.approx(s["from_future"], abs=0.02), s["key"]
    ada = next(m for m in e if m["name"] == "Ada")                  # paid in full last July: all of it already paid
    assert all(fu == 0 for _, fu in ada["months"].values()) and sum(p for p, _ in ada["months"].values()) > 0
    assert all(m["name"] != "Di" for m in e)                         # left out: no part of any month


# ── what production's records showed (2026-10-26): each case, invented ─────────────────────────────

def paid_from(name, email, day, amount, **meta):
    """A charge on the old Stripe account: no CRM contact, and often another address than the member's own."""
    return NS(external_id=f"{email}-{day}-{amount}", name=name, email=email, amount=amount, status="succeeded",
              occurred_on=day, meta={"entity_source_name": "Inner Circle dues", "stream": "memberships", **meta})


def sub(cid, name, amount, start, nxt, interval="month", **meta):
    return NS(name=name, email=f"{name.lower()}@club.test", status="active", amount=amount,
              meta={"contact_id": cid, "interval": interval, "start_date": start, "next_payment_date": nxt, **meta})


def monthly(cid, name, first, n, amount):
    return [pay(cid, name, live._add(first, i), amount) for i in range(n)]


def scenario(members, payments, subs=(), today=TODAY):
    return live.build(forwarded(), members, payments, list(subs), [], today)


def row(b, name):
    return next(r for r in b["detail"]["members"] if r["name"] == name)


def test_a_payment_from_another_address_is_matched_on_a_name_only_one_member_has():
    gil = member("g", "Gil", 1800, "2026-09-01", "pif")
    hal1, hal2 = member("h1", "Hal", 1000, "2026-09-01", "pif"), member("h2", "Hal", 1000, "2026-10-01", "pif")
    ann, bob = member("an", "Ann", 1500, "2026-09-01", "pif"), member("bo", "Bob", 1500, "2026-09-01", "pif")
    ann.email = bob.email = "home@club.test"                     # one household address on two contacts
    b = scenario([gil, hal1, hal2, ann, bob],
                 [paid_from("Gil", "gil@business.test", D(2025, 9, 1), 1800),     # his business card: his
                  paid_from("Hal", "hal@elsewhere.test", D(2025, 9, 2), 1000),    # two Hals: nobody's
                  paid_from("Ann", "home@club.test", D(2025, 9, 1), 1500)])       # the shared address: by name
    assert row(b, "Gil")["paid_by_end"] == 1800 and row(b, "Gil")["flag"] is None
    assert [row_["paid_by_end"] for row_ in b["detail"]["members"] if row_["name"] == "Hal"] == [0.0, 0.0]
    assert row(b, "Ann")["paid_by_end"] == 1500 and row(b, "Bob")["paid_by_end"] == 0.0
    assert b["contracts"]["collected"] == 1800 + 1500


def test_a_member_with_no_contract_value_is_counted_at_what_they_pay_and_said():
    ivy, jo = member("i", "Ivy", 0, "2026-08-23", None), member("j", "Jo", 0, "2026-08-23", None)
    kit = member("k", "Kit", 0, "2026-08-23", None)
    b = scenario([ivy, jo, kit], [pay("i", "Ivy", D(2025, 8, 23), 2700)],
                 [sub("k", "Kit", 250, "2025-08-23", "2026-04-23")])
    assert row(b, "Ivy")["value"] == 2700 and row(b, "Ivy")["value_from_billing"]
    assert row(b, "Ivy")["flag"].startswith("No contract value in the CRM: counted at $2,700.00 a year")
    assert row(b, "Kit")["value"] == 3000 and row(b, "Kit")["plan"] == "monthly"      # a $250 monthly subscription
    assert b["detail"]["left_out"] == [{"name": "Jo", "why": "no contract value", "source_url": "https://crm.test/j"}]
    assert b["detail"]["counts"]["from_billing"] == 2 and b["contracts"]["contracted"] == 2700 + 3000


def test_a_renewal_paid_early_pays_for_the_term_it_was_paid_ahead_of():
    # paid since the books' end, before the renewal date, the CRM not moved yet: renewed, and the cash is in
    kay = member("k", "Kay", 2400, "2026-08-01", "pif")
    b = scenario([kay], [pay("k", "Kay", D(2025, 8, 1), 2400), pay("k", "Kay", D(2026, 7, 20), 2400)],
                 today=D(2026, 8, 9))
    assert row(b, "Kay")["renewal"]["state"] == "renewed" and row(b, "Kay")["renewal"]["since"] == 2400
    assert b["collections"] == {"2026-07": 2400.0} and b["contracts"]["collected"] == 2400
    # paid before the books' end for a term that opens after it: in the bank already, deferred, not collected again
    lee = member("l", "Lee", 2400, "2027-04-20", "pif")          # the CRM moved it on
    b = scenario([lee], [pay("l", "Lee", D(2025, 4, 20), 2400), pay("l", "Lee", D(2026, 3, 25), 2400)],
                 today=D(2026, 4, 25))
    r = row(b, "Lee")
    assert r["paid_by_end"] == 2400 and r["renewal"]["paid_by_end"] == 2400 and r["flag"] is None
    assert b["collections"] == {} and b["contracts"]["collected"] == 4800
    earned = term_earned(2400, D(2025, 4, 20), D(2026, 4, 20), D(2026, 3, 31))
    assert b["contracts"]["earned_so_far"] == pytest.approx(earned, abs=0.02)        # all of the second is deferred


@pytest.mark.parametrize("day", [1, 10, 15, 17, 18, 25])
def test_a_monthly_members_payments_fall_into_terms_a_year_at_a_time_whatever_day_the_card_bills(day):
    lou = member("lu", "Lou", 2400, "2026-06-18", "monthly")
    pays = monthly("lu", "Lou", D(2025, 5, day), 12, 200)        # May 2025 .. Apr 2026, billed on `day`
    nxt = D(2026, 5, day)
    b = scenario([lou], pays, [sub("lu", "Lou", 200, f"2025-05-{day:02d}", nxt.isoformat())], today=D(2026, 4, 28))
    in_hand = sum(b["collections"].values())
    due = sum(r["collected"] for r in b["renewals"].values())
    # the year after the books (Apr 2026 .. Mar 2027) is twelve of his payments: what's left of this term, then the
    # renewal's, never one too many or too few
    assert in_hand + due == pytest.approx(12 * 200)
    r = row(b, "Lou")
    assert r["paid_by_end"] + r["since"] + r["scheduled"] == 2400 and r["flag"] is None


def test_a_monthly_members_first_charge_of_the_new_term_renews_them_though_it_bills_days_before_the_date():
    lou = member("lu", "Lou", 2400, "2026-06-18", "monthly")
    pays = monthly("lu", "Lou", D(2025, 6, 8), 13, 200)           # billed on the 8th: Jun 8 2026 opens the new term
    b = scenario([lou], pays, [sub("lu", "Lou", 200, "2025-06-08", "2026-07-08")], today=D(2026, 6, 12))
    assert row(b, "Lou")["renewal"]["state"] == "renewed" and row(b, "Lou")["renewal"]["since"] == 200


def test_a_blank_payment_plan_is_read_from_the_members_subscription():
    mo = member("m", "Mo", 3000, "2026-06-10", None)
    b = scenario([mo], monthly("m", "Mo", D(2025, 6, 10), 11, 250), [sub("m", "Mo", 250, "2025-06-10", "2026-05-10")],
                 today=D(2026, 4, 25))
    r = row(b, "Mo")
    assert r["plan"] == "monthly" and r["plan_from_crm"] is False
    assert r["paid_by_end"] == 2500                               # Jun .. Mar, not last May's payment as well
    assert b["renewals"]["2026-06"]["collected"] == 250           # the renewal billed monthly, not one lump
    assert sum(x["collected"] for x in b["renewals"].values()) == pytest.approx(10 * 250)


def test_a_small_charge_after_the_renewal_date_doesnt_renew_a_member_and_a_real_payment_does():
    ned = member("n", "Ned", 5000, "2026-05-01", "pif")
    base = [pay("n", "Ned", D(2025, 5, 1), 5000)]
    for extra in (pay("n", "Ned", D(2026, 5, 3), 75, what="Mastermind Workbook", stream="other"),
                  pay("n", "Ned", D(2026, 5, 3), 0), pay("n", "Ned", D(2026, 5, 4), 300, what="Invoice #88", stream="invoices")):
        b = scenario([ned], base + [extra], today=D(2026, 5, 10))
        assert row(b, "Ned")["renewal"]["state"] == "due" and b["contracts"]["contracted"] == 5000
    b = scenario([ned], base + [pay("n", "Ned", D(2026, 5, 4), 5000)], today=D(2026, 5, 10))
    assert row(b, "Ned")["renewal"]["state"] == "renewed" and b["contracts"]["contracted"] == 10000


def test_a_new_or_renewed_term_with_nothing_paid_is_flagged_and_so_is_whats_owed_with_nothing_scheduled():
    pia = member("p", "Pia", 4000, "2027-04-12", "pif", enrolled="2026-04-12")              # joined, never paid
    roy = member("r", "Roy", 2000, "2027-04-15", "pif")                                      # the CRM moved it on
    sal = member("s", "Sal", 3600, "2026-08-01", "monthly")                                  # card cancelled
    b = scenario([pia, roy, sal], [pay("r", "Roy", D(2025, 4, 15), 2000)] + monthly("s", "Sal", D(2025, 8, 1), 4, 300),
                 today=D(2026, 4, 25))
    assert row(b, "Pia")["flag"] == "Nothing paid toward this term, and nothing scheduled"
    assert row(b, "Roy")["flag"] == "Nothing paid toward the term renewed Apr 15 2026, and nothing scheduled"
    assert row(b, "Sal")["flag"] == "$2,400.00 of this term still owed, and nothing scheduled to collect it"
    c = b["detail"]["counts"]
    assert (c["nothing"], c["nothing_value"], c["owed"], c["owed_value"]) == (2, 6000.0, 1, 3600.0)


# ── the engine, projecting it ───────────────────────────────────────────────────────────────────

def books_with(saved=None):
    data = forwarded()
    return eng.Books(data, eng.merged_inputs(data, saved)), block(data)


def club(saved=None):
    bk, b = books_with(saved)
    f = bk.forwards([b])
    assert [x["company"] for x in f] == [COHORT, CLUB]
    return bk, f[1], b


def test_the_files_own_projection_is_untouched_by_a_second():
    bk, b = books_with()
    assert bk.forwards([b])[0] == bk.forward()
    assert bk.projected([b, {**b}])[1:] == [b]                    # a company is projected once


def test_each_projection_carries_what_its_months_are_made_of():
    _, f, b = club()
    assert f["earning"] == b["earning"] and f["earning"]["members"]
    data = forwarded()
    data["forward"]["earning"] = {"members": [{"name": "A contract", "months": {"2026-04": [10.0, 5.0]}}]}
    bk = eng.Books(data, eng.merged_inputs(data, None))
    assert bk.forward()["earning"] == data["forward"]["earning"]
    assert eng.Books(forwarded(), eng.merged_inputs(forwarded(), None)).forward()["earning"] is None   # a file without it


def test_revenue_ahead_is_the_terms_plus_the_renewals_at_the_rate():
    _, none, b = club({"forward": {CLUB: {"renew_pct": 0}}})
    _, all_, _ = club({"forward": {CLUB: {"renew_pct": 100}}})
    in_hand = sum(s["from_paid"] + s["from_future"] for s in b["schedule"])
    due = sum(r["earned"] for r in b["renewals"].values())
    assert none["future_by_line"]["Membership dues"] == pytest.approx(in_hand, abs=0.05)
    assert all_["future_by_line"]["Membership dues"] == pytest.approx(in_hand + due, abs=0.05)
    assert all_["renewals"]["earned"] == pytest.approx(due, abs=0.05) and all_["renewals"]["count"] == 3


def test_new_members_join_each_month_and_pay_the_way_todays_do():
    _, f, _ = club({"forward": {CLUB: {"renew_pct": 0, "new_members": 2, "new_value": 1200}}})
    _, g, _ = club({"forward": {CLUB: {"renew_pct": 0}}})
    rows, base = {r["key"]: r for r in f["rows"]}, {r["key"]: r for r in g["rows"]}
    assert rows["2026-04"]["rev"] - base["2026-04"]["rev"] == pytest.approx(2 * 100)        # one month's twelfth
    assert rows["2026-06"]["rev"] - base["2026-06"]["rev"] == pytest.approx(3 * 2 * 100)     # three cohorts in
    cash, cash0 = {r["key"]: r for r in f["cash"]["rows"]}, {r["key"]: r for r in g["cash"]["rows"]}
    # half pay in full on joining, half a twelfth a month
    assert cash["2026-04"]["in"] - cash0["2026-04"]["in"] == pytest.approx(2 * 600 + 2 * 50)
    assert f["joins"] == {"members": 24.0, "earned": pytest.approx(sum(2 * 100 * min(12, 13 - i) for i in range(1, 13)))}


def test_deferred_revenue_rolls_from_the_terms_and_sits_beside_the_books():
    _, f, b = club()
    c = b["contracts"]
    d = f["deferred"]
    assert d["start"] == pytest.approx(c["collected"] - c["earned_so_far"], abs=0.01)
    assert d["rows"][-1]["close"] == pytest.approx(d["start"] + sum(r["in"] - r["earned"] for r in d["rows"]), abs=0.05)
    assert d["books"]["deferred_account"] == f"{CLUB} deferred" and d["books"]["as_of"] == "2026-03-31"


def test_cash_starts_from_the_books_bank_on_their_last_day():
    bk, f, _ = club()
    bank = next(r for s in bk.balance_sheet(CLUB)["sections"] for r in s["rows"] if r["type"] == "Bank")
    assert f["cash"]["from_books"] and f["cash"]["balance"] == bank["corrected"]
    # the books end on a month's last day: nothing of March is ahead, so the flow starts in April
    assert f["cash"]["rows"][0]["key"] == "2026-04" and f["cash"]["rows"][0]["open"] == bank["corrected"]


def test_the_partner_after_the_owners_salary_accrues_as_the_books_do():
    bk, f, _ = club({"forward": {CLUB: {"renew_pct": 100, "new_members": 2, "new_value": 24000}}})   # a profit
    rows = {r["key"]: r for r in f["rows"]}
    assert f["partner"] == "Pat" and f["assumptions"]["partner_pay"] == 0.0
    assert [rows[f"2026-{m:02d}"]["share"] for m in bk.MONTHS] == [round(bk.share[(CLUB, m)], 2) for m in bk.MONTHS]
    # each calendar year, 20% of the year's profit once the owner's $12,000 a year (to date) is out of it
    profit = lambda yr: sum(r["profit"] for r in f["rows"] if r["key"].startswith(yr))
    assert profit("2026") > 12000 and profit("2027") > 3000
    assert f["summary"]["earned"] == pytest.approx(0.2 * ((profit("2026") - 12000) + (profit("2027") - 3000)), abs=0.1)


def test_cost_per_member_is_the_plan_over_todays_members():
    _, f, _ = club()
    assert f["members"] == 4 and f["cost_per_member"] == pytest.approx(f["running"] / 4, abs=0.01)


def test_a_second_projected_companys_assumptions_and_plan_are_saved_under_its_name():
    data = {**forwarded(), "forward_live": [block()]}
    assert eng.validate_change(data, ["forward", CLUB, "renew_pct"], 75) == 75
    assert eng.validate_change(data, ["budget", CLUB, "Staff and contractors"], 300) == 300
    assert eng.validate_change(data, ["forward", CLUB, "partner_pay"], 200) == 200      # the club has a partner
    for path, value in ((["forward", CLUB, "renew_pct"], 101), (["forward", CLUB, "commission_pct"], 5),
                        (["forward", CLUB, "nonsense"], 1), (["forward", "Nobody Co", "renew_pct"], 5),
                        (["forward", COHORT, "commission_pct"], 5)):
        with pytest.raises(eng.InputError):
            eng.validate_change(data, path, value)
    with pytest.raises(eng.InputError):                            # without the records' block there's nothing there
        eng.validate_change(forwarded(), ["forward", CLUB, "renew_pct"], 75)
    saved = {}
    for path, v in ((["forward", CLUB, "renew_pct"], 75), (["forward", CLUB, "new_members"], 1),
                    (["budget", CLUB, "Staff and contractors"], 300), (["forward", "commission_pct"], 10)):
        eng.set_path(saved, path, v)
    bk, f, _ = club(saved)
    assert f["assumptions"]["renew_pct"] == 75 and f["assumptions"]["new_members"] == 1
    assert f["plan"]["Staff and contractors"] == 300.0
    assert bk.forward()["assumptions"]["commission_pct"] == 10    # the file's own, still flat
    assert CLUB not in bk.forward()["assumptions"]


# ── the API ──────────────────────────────────────────────────────────────────────────────────────

def _H(token):
    return {"Authorization": f"Bearer {token}", "x-tenant-host": "liveco.localhost"}


@pytest.fixture(scope="module")
async def ctx():
    from app.seed import seed
    await seed()
    async with SessionLocal() as s:
        t = Tenant(slug="liveco", name="Live Co", plan="portfolio")
        s.add(t)
        await s.flush()
        biz = Business(tenant_id=t.id, key="club", name="Club Holdings", tag="Memberships", kind="membership")
        s.add(biz)
        await s.flush()
        m, p, subs, lost = records()
        for src, kind, rows in (("ghl", "member", m), ("ghl", "payment", p), ("ghl", "subscription", subs),
                                ("ghl", "membership_lost", lost)):
            for i, r in enumerate(rows):
                s.add(MetricRecord(tenant_id=t.id, business_id=biz.id, source=src, kind=kind,
                                   external_id=getattr(r, "external_id", None) or f"{kind}{i}",
                                   name=getattr(r, "name", None), email=getattr(r, "email", None),
                                   amount=getattr(r, "amount", None), status=getattr(r, "status", None),
                                   occurred_on=getattr(r, "occurred_on", None), meta=getattr(r, "meta", None) or {}))

        def mk(email, role, tabs=None):
            return User(tenant_id=t.id, email=email, name=email.split("@")[0], role=role,
                        password_hash=hash_pw("x" * 12), status="active", tab_access=tabs)
        owner, keeper = mk("owner@live.test", "owner"), mk("keeper@live.test", "member", ["cleanup"])
        s.add_all([owner, keeper])
        await s.commit()
        return {"owner": make_token(owner.id, t.id, 0), "keeper": make_token(keeper.id, t.id, 0)}


async def test_the_forum_is_projected_beside_the_files_own_and_planned_by_the_owners(ctx, monkeypatch):
    import io
    import json
    monkeypatch.setattr(live, "_today", lambda: TODAY)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        f = {"file": ("cleanup.json", io.BytesIO(json.dumps(forwarded()).encode()), "application/json")}
        assert (await c.post("/api/v1/cleanup/dataset", headers=_H(ctx["owner"]), files=f)).status_code == 200
        ov = (await c.get("/api/v1/cleanup", headers=_H(ctx["keeper"]))).json()
        assert ov["forward_companies"] == [COHORT, CLUB] and ov["forward_company"] == COHORT
        body = (await c.get("/api/v1/cleanup/forward", headers=_H(ctx["keeper"]))).json()
        assert [x["company"] for x in body["forwards"]] == [COHORT, CLUB] and body["live_error"] is None
        forum = body["forwards"][1]
        assert forum["source"] == "live" and forum["contracts"]["contracted"] == 5400.0
        assert forum["paths"] == {"forward": ["forward", CLUB], "budget": ["budget", CLUB]}
        assert body["forward"]["company"] == COHORT and set(body["budgets"]) == set(COMPANIES)
        change = {"changes": [{"path": ["forward", CLUB, "renew_pct"], "value": 50}]}
        assert (await c.put("/api/v1/cleanup/inputs", headers=_H(ctx["keeper"]), json=change)).status_code == 403
        assert (await c.put("/api/v1/cleanup/inputs", headers=_H(ctx["owner"]), json=change)).status_code == 200
        bad = {"changes": [{"path": ["forward", CLUB, "commission_pct"], "value": 5}]}
        assert (await c.put("/api/v1/cleanup/inputs", headers=_H(ctx["owner"]), json=bad)).status_code == 400
        after = (await c.get("/api/v1/cleanup/forward", headers=_H(ctx["owner"]))).json()["forwards"]
        assert after[1]["assumptions"]["renew_pct"] == 50 and after[0] == body["forwards"][0]
