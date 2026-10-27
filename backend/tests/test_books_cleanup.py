"""Books Cleanup -- the engine's arithmetic, and who may see and change what.

The engine replaced an audit workbook, and was tied out against it cell for cell on the real file
before the tab existed (that file holds a company's books and lives outside the repository). These
tests pin the properties that tie-out depended on, on a small invented file whose every figure can
be worked by hand:

  * a shared cost split between brands moves to the companies those brands belong to, and the
    correcting entries move it the same way, through the companies' intercompany accounts;
  * every entry balances, QuickBooks plus the entries is the corrected P&L, and the balance sheets
    balance, for every company and month, WHATEVER the decisions are;
  * a partner's share accrues on the year to date, and only after the owner's salary when the
    agreement puts it first;
  * the owner's charges, the investment-or-loan switch and the revenue basis do what they say.

And the authorization: the tab exists only where a file is loaded; the decisions are an owner's or
admin's; the work (answers, ticks, posted entry numbers) is anybody's with the tab.
"""
import io
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import AuditLog, CleanupDataset, Tenant, User
from app.security import hash_pw, make_token
from app.services import books_cleanup as eng

TRANSPORT = ASGITransport(app=app)
HUB, CLUB, COHORT = "Hub Co", "Club Co", "Cohort Co"
COMPANIES = [HUB, CLUB, COHORT]
BRANDS = ["Club", "Cohort", "Inside", "Hub"]          # Inside is a brand run inside the hub
REV = ["Membership dues", "Programs and coaching", "Events and tickets", "Sponsorships", "Refunds",
       "Uncategorized income (old books)"]
COSTS = ["Event costs", "Advertising and marketing", "Staff and contractors", "Sales commissions",
         "Coaching and professional fees", "Software and subscriptions", "Payment processing",
         "Travel and meals", "Member gifts and fulfillment", "Office, insurance and other",
         "Not yet identified", "Uncategorized expense (old books)"]
BELOW = ["Other income", "Partner profit shares"]
# A real subscription's name runs past the audit log's 64-character target column.
LONG = "An annual subscription whose name runs well past the audit log's sixty-four characters"


def _l(company, brand, cls, line, m, amount, account, pool=None, item=None):
    return {"company": company, "brand": brand, "cls": cls, "pool": pool, "item": item, "line": line,
            "month": m, "date": f"2026-0{m}-15", "amount": amount, "account": account,
            "corr_account": account, "key": "k"}


def dataset() -> dict:
    """Three companies. The hub pays the shared costs; the club has a dues schedule and a partner who
    earns after the owner's salary; the cohort has a partner who doesn't. Every amount is round."""
    ledger = []
    for m in (1, 2, 3):
        ledger += [
            _l(CLUB, "Club", "revenue", "Membership dues", m, 1000.0, "Club dues"),
            _l(COHORT, "Cohort", "revenue", "Programs and coaching", m, 3000.0, "Cohort programs"),
            _l(HUB, "Inside", "revenue", "Membership dues", m, 500.0, "Inside dues"),
            _l(HUB, None, "cost", "Advertising and marketing", m, 600.0, "Ads", "Advertising", "Advertising"),
            _l(HUB, None, "cost", "Payment processing", m, 100.0, "Fees", "Payment processing", "Payment processing"),
            _l(HUB, None, "cost", "Staff and contractors", m, 1000.0, "Pay:Ann", "Staff and contractors", "Ann"),
            _l(HUB, None, "cost", "Staff and contractors", m, 400.0, "Pay:Bo", "Staff and contractors", "Bo"),
            _l(HUB, None, "cost", "Staff and contractors", m, 100.0, "Taxes", "Staff and contractors", "Taxes"),
            _l(HUB, None, "cost", "Software and subscriptions", m, 90.0, "Zoomish", "Software and subscriptions",
               "Zoomish"),
            _l(HUB, None, "cost", "Software and subscriptions", m, 30.0, "Annual sub", "Software and subscriptions",
               LONG),
            _l(HUB, None, "cost", "Office, insurance and other", m, 300.0, "Insurer", "General overhead", "Insurer"),
            _l(COHORT, "Cohort", "partner", None, m, 800.0, "Jo advances"),
        ]
    # an event the hub paid for that was the club's: QuickBooks has it on the hub, the review puts it on the club
    ledger.append({**_l(HUB, "Club", "cost", "Event costs", 3, 200.0, f"{HUB} Event costs"),
                   "corr_account": f"{CLUB} Event costs"})

    # QuickBooks booked everything where it was paid, plus the owner's charge where it was booked
    by_month = {c: {} for c in COMPANIES}
    for l in ledger:
        if l["cls"] in ("revenue", "cost"):
            bm = by_month[l["company"]].setdefault(str(l["month"]), {})
            bm[l["line"]] = bm.get(l["line"], 0) + l["amount"]
    by_month[HUB]["2"]["Advertising and marketing"] += 250.0
    qbo = {}
    for c in COMPANIES:
        lines = {}
        for bm in by_month[c].values():
            for k, v in bm.items():
                lines[k] = lines.get(k, 0) + v
        qbo[c] = {"lines": lines, "by_month": by_month[c],
                  "net_income": sum(v if k in REV else -v for k, v in lines.items())}

    main = {f"{c}|{l}": f"{c} {l}" for c in COMPANIES for l in REV + COSTS + BELOW}
    acct_line = {f"{c}|{c} {l}": l for c in COMPANIES for l in REV + COSTS + BELOW}
    acct_line.update({f"{HUB}|Ads": "Advertising and marketing", f"{HUB}|Fees": "Payment processing",
                      f"{HUB}|Pay:Ann": "Staff and contractors", f"{HUB}|Pay:Bo": "Staff and contractors",
                      f"{HUB}|Taxes": "Staff and contractors", f"{HUB}|Zoomish": "Software and subscriptions",
                      f"{HUB}|Annual sub": "Software and subscriptions", f"{HUB}|Insurer": "Office, insurance and other",
                      f"{HUB}|Clothes": "Advertising and marketing", f"{HUB}|Inside dues": "Membership dues",
                      f"{CLUB}|Club dues": "Membership dues", f"{COHORT}|Cohort programs": "Programs and coaching"})
    for c in (CLUB, COHORT):
        acct_line.update({f"{c}|Share:Ads": "Advertising and marketing", f"{c}|Share:Pay": "Staff and contractors",
                          f"{c}|Share:Soft": "Software and subscriptions"})
    ic = {f"{a}|{b}": (f"Due from {b}" if a == HUB else f"Due to {b}") for a in COMPANIES for b in COMPANIES if a != b}
    bank = lambda c, extra=0.0: {"account": "Checking", "class": "Asset", "type": "Bank",
                                 "balance": qbo[c]["net_income"] + extra}
    eq = lambda a, v: {"account": a, "class": "Equity", "type": "Equity", "balance": -v}
    bs = {HUB: [bank(HUB, 10000.0), eq("Owner contributions", 10000.0)],
          CLUB: [bank(CLUB)],
          COHORT: [bank(COHORT, 5000.0 - 2400.0), eq("Investment in Subsidiary", 5000.0),
                   {"account": "Jo advances", "class": "Asset", "type": "Other Current Asset", "balance": 2400.0}]}
    split = lambda **kw: {b: float(kw.get(b, 0)) for b in BRANDS}
    return {
        "schema": eng.SCHEMA, "label": "Invented cleanup", "exported_at": "2026-10-19T00:00:00+00:00",
        "period": {"start": "2026-01-01", "end": "2026-03-31", "months": [{"m": m, "label": f"M{m}"} for m in (1, 2, 3)],
                   "accrual_months": 3, "salary_fraction": {str(m): m / 12 for m in range(4)}},
        "companies": COMPANIES, "company_short": {HUB: "Hub", CLUB: "Club", COHORT: "Cohort"},
        "brands": BRANDS, "brand_company": {"Club": CLUB, "Cohort": COHORT, "Inside": HUB, "Hub": HUB},
        "hub": HUB, "hub_brand": "Hub", "inside_brand": "Inside",
        "billback": {"name": "Neighbour", "account": "Due from Neighbour"},
        "lines": {"revenue": REV, "costs": COSTS, "below": BELOW},
        "pools": {"Advertising": {"line": "Advertising and marketing", "share_account": "Share:Ads", "billback": True},
                  "Payment processing": {"line": "Payment processing", "share_account": None, "billback": False},
                  "Staff and contractors": {"line": "Staff and contractors", "share_account": "Share:Pay"},
                  "Software and subscriptions": {"line": "Software and subscriptions", "share_account": "Share:Soft"},
                  "General overhead": {"line": None, "share_account": None}},
        "fees": {"Club": 0.5, "Cohort": 0.2, "Inside": 0.1, "Hub": 0.2, "billback": 0.0},
        "accounts": {"main": main, "acct_line": acct_line, "acct_new": {}, "ic": ic, "owner": "Owner draw",
                     "deferred": {c: f"{c} deferred" for c in COMPANIES},
                     "prepaid": {c: f"{c} prepaid" for c in COMPANIES},
                     "accrued_liab": {c: f"{c} accrued liabilities" for c in COMPANIES},
                     "accrued_revenue": {c: f"{c} accrued" for c in COMPANIES},
                     "cash_to_accrual": "Cash to accrual", "retained_earnings": "Retained earnings",
                     "transfer": "Transfer", "holdco": {"invest": "Investment in Subsidiary", "loan": "Loan from Holdco",
                                                    "dist": "Distributions to Holdco"},
                     "types": {HUB: {f"Due from {b}": ["Asset", "Other Current Asset"] for b in (CLUB, COHORT)}},
                     "new_types": {"Loan from Holdco": ["Liability", "Long Term Liability"],
                                   "Distributions to Holdco": ["Equity", "Equity"]}},
        "partners": [{"company": CLUB, "person": "Pat", "salary": True, "payable_account": "Pat payable"},
                     {"company": COHORT, "person": "Jo", "salary": False, "advance_account": "Jo advances"}],
        "salary_owner": "Owner",
        "dues": {"company": CLUB, "line": "Membership dues", "earned": {"1": 900.0, "2": 900.0, "3": 900.0},
                 "deferred": [0.0, 100.0, 200.0, 300.0], "accrued": [0.0, 0.0, 0.0, 0.0], "in_transit_open": 0.0},
        "transactions": {"k": {"ref": "T1", "who": "Someone", "what": "A line", "note": "", "verdict": "agree",
                               "category": "", "type": ""}},
        "ledger": ledger, "qbo": qbo,
        "review": [{"company": HUB, "account": f"{HUB} Event costs", "component": "moved", "months": [0, 0, 0, -200.0]},
                   {"company": HUB, "account": f"Due from {CLUB}", "component": "moved", "months": [0, 0, 0, 200.0]},
                   {"company": CLUB, "account": f"{CLUB} Event costs", "component": "moved", "months": [0, 0, 0, 200.0]},
                   {"company": CLUB, "account": f"Due to {HUB}", "component": "moved", "months": [0, 0, 0, -200.0]}],
        "timing": [{"company": COHORT, "line": "Programs and coaching", "month": 2, "amount": -1000.0,
                    "what": "Earned next month (rebuilt)", "source": "", "account": "Cohort programs",
                    "side": f"{COHORT} deferred", "mirror": False, "basis": "rebuilt"},
                   {"company": COHORT, "line": "Programs and coaching", "month": 2, "amount": -400.0,
                    "what": "Earned next month (old books)", "source": "", "account": "Cohort programs",
                    "side": f"{COHORT} deferred", "mirror": False, "basis": "old_books"}],
        "people": [{"name": "Ann", "account": "Pay:Ann", "w2": True, "follows_wages": False, "note": ""},
                   {"name": "Bo", "account": "Pay:Bo", "w2": False, "follows_wages": False, "note": ""},
                   {"name": "Taxes", "account": "Taxes", "w2": False, "follows_wages": True, "note": ""}],
        "items": {"software": {"pool": "Software and subscriptions", "rows": [
                      {"name": "Zoomish", "line": "Software and subscriptions", "account": "Zoomish", "note": ""},
                      {"name": LONG, "line": "Software and subscriptions", "account": "Annual sub", "note": ""}]},
                  "other": {"pool": "General overhead", "rows": [
                      {"name": "Insurer", "line": "Office, insurance and other", "account": "Insurer", "note": ""}]}},
        "charges": [{"ref": "C1", "sid": "S1", "date": "2026-02-02", "month": 2, "who": "Boutique", "amount": 250.0,
                     "account": "Clothes", "booked_line": "Advertising and marketing", "what": "A dress"}],
        "charges_brand": "Hub",
        "holdco": [{"company": COHORT, "account": "Investment in Subsidiary", "month": 2, "amount": 5000.0,
                 "date": "2026-02-10", "who": "Holdco", "sid": "B1"}],
        "bs": {c: {"accounts": bs[c], "net_income": qbo[c]["net_income"]} for c in COMPANIES},
        "interco": {"balances": [], "pairs": [
            {"pair": "Hub and Club", "reads": "positive = Club owes Hub",
             "terms": [{"company": HUB, "balance": None, "post": f"Due from {CLUB}", "sign": 1}]}]},
        "inputs": {"ads": {"Club": 0.5, "Cohort": 0.3, "Inside": 0.1, "Hub": 0.0, "billback": 0.1},
                   "rates": {CLUB: 0.2, COHORT: 0.5}, "salary": 12000.0, "holdco": "Investment (equity)",
                   "bc_basis": "rebuilt",
                   "staff": {"Ann": {str(m): split(Club=0.5, Cohort=0.5) for m in (1, 2, 3)},
                             "Bo": {str(m): split(Inside=0.25, Hub=0.75) for m in (1, 2, 3)}},
                   "items": {"software": {"Zoomish": split(Club=1 / 3, Cohort=1 / 3, Inside=1 / 3), LONG: split(Hub=1)},
                             "other": {"Insurer": split(Hub=1)}},
                   "charges": {"C1": {"treat": "own"}}, "answers": {}, "posted": {}, "notes": {}},
        "open_items": [{"ref": "Q1", "company": HUB, "date": "", "who": "", "amount": None, "text": "A question"}],
        "answered": [], "questions": [], "findings": [],
        "checklist": [{"id": "step01", "group": "qbo", "who": "Bookkeeper", "text": "Post the entries."}],
    }


def books(saved=None, data=None) -> eng.Books:
    data = data or dataset()
    return eng.Books(data, eng.merged_inputs(data, saved))


def _line(bk, view, month, line):
    return next(r for s in bk.statement(view, month)["sections"] for r in s["lines"] if r["line"] == line)


# ── the arithmetic ───────────────────────────────────────────────────────────────────────────────

def test_a_shared_cost_lands_on_the_companies_its_brands_belong_to():
    bk = books()
    staff = lambda c: _line(bk, c, 1, "Staff and contractors")["corrected"]
    ads = lambda c: _line(bk, c, 1, "Advertising and marketing")["corrected"]
    # Ann is half Club, half Cohort, so her $1,000 leaves the hub, and the payroll taxes follow her.
    # Bo is a quarter Inside (a brand inside the hub) and the rest the hub's own: all of him stays put.
    assert (staff(CLUB), staff(COHORT), staff(HUB)) == (550.0, 550.0, 400.0)
    # the ads: half Club, 30% Cohort, 10% Inside, and 10% billed back to the neighbour
    assert (ads(CLUB), ads(COHORT), ads(HUB)) == (300.0, 180.0, 60.0)
    assert sum(staff(c) for c in COMPANIES) == 1500.0                   # nothing lost between them
    assert sum(ads(c) for c in COMPANIES) == 600.0 - 60.0               # less the billback


def test_payroll_taxes_follow_the_wages():
    # Taxes follow W-2 wages only. Ann is the one W-2 person, so they split as she does.
    assert books().split("Taxes", 2) == (0.5, 0.5, 0.0, 0.0)


def test_a_cost_paid_by_the_wrong_company_moves_to_the_right_one():
    bk = books()
    assert _line(bk, HUB, 3, "Event costs")["qbo"] == 200.0
    assert _line(bk, HUB, 3, "Event costs")["corrected"] == 0.0
    assert _line(bk, CLUB, 3, "Event costs")["corrected"] == 200.0
    why = bk.line_detail(HUB, "Event costs", 3)["why"]
    assert why == [{"component": "moved", "label": eng.COMPONENTS["moved"], "amount": -200.0}]


@pytest.mark.parametrize("saved", [
    None,
    {"staff": {"Ann": {"2": {"Club": 0.0, "Cohort": 0.0, "Inside": 1.0, "Hub": 0.0}}}},
    {"ads": {"Club": 0.0, "Cohort": 0.0, "Inside": 0.0, "Hub": 0.9, "billback": 0.1}},
    {"items": {"other": {"Insurer": {"Club": 0.4, "Cohort": 0.6, "Inside": 0.0, "Hub": 0.0}}}},
    {"charges": {"C1": {"treat": "business", "line": "Event costs"}}, "holdco": "Loan", "salary": 0.0,
     "rates": {CLUB: 0.9, COHORT: 0.1}, "bc_basis": "old_books"},
])
def test_everything_balances_whatever_the_decisions(saved):
    bk = books(saved)
    assert all(ch["ok"] for ch in bk.checks()), [ch for ch in bk.checks() if not ch["ok"]]
    for c in COMPANIES:
        assert bk.corrections(c)["balanced"] and bk.corrections(c)["ties"], c
        assert bk.balance_sheet(c)["balanced"] == {"qbo": True, "corrected": True}, c


def test_every_entry_balances_to_the_cent():
    """QuickBooks refuses an entry that is a cent out. A subscription split in thirds rounds each line
    on its own -- 33.33 + 33.33 against 66.67 -- so the entry has to carry its own rounding."""
    data = dataset()
    for l in data["ledger"]:
        if l["item"] == "Zoomish":
            l["amount"] = 100.0
    bk = books(data=data)
    for c in COMPANIES:
        cc = bk.corrections(c)
        for k in range(len(cc["months"])):
            cents_ = [round(r["amounts"][k] * 100) for r in cc["accounts"]]
            assert sum(cents_) == 0, (c, k, sum(cents_))


def test_an_entry_that_doesnt_balance_is_not_rounded_into_balancing():
    data = dataset()
    data["review"].append({"company": CLUB, "account": "Club Co Event costs", "component": "moved",
                           "months": [0, 0.04, 0, 0]})              # one-sided: the entry is 4 cents out
    cc = books(data=data).corrections(CLUB)
    # a cent out is out: QuickBooks would refuse it, so the check does too
    assert cc["balance"][1] == 0.04 and cc["balanced"] is False


def test_the_entries_carry_a_split_through_intercompany():
    bk = books()
    hub = {r["account"]: r for r in bk.corrections(HUB)["accounts"]}
    club = {r["account"]: r for r in bk.corrections(CLUB)["accounts"]}
    # month 1: Club's shares (ads 300, Ann 500, taxes 50, fees 50, software 30) are owed to the hub
    assert hub[f"Due from {CLUB}"]["amounts"][1] == pytest.approx(930.0)
    assert club[f"Due to {HUB}"]["amounts"][1] == pytest.approx(-930.0)
    assert club["Share:Pay"]["amounts"][1] == pytest.approx(550.0)
    assert hub["Pay:Ann"]["amounts"][1] == pytest.approx(-1000.0)
    assert hub["Due from Neighbour"]["total"] == pytest.approx(180.0)       # 10% of the ads, three months
    assert bk.intercompany()["pairs"][0]["corrected"] == pytest.approx(3 * 930.0 + 200.0)


def test_a_partner_share_accrues_on_the_year_to_date_after_the_salary():
    bk = books()
    # Club never has an operating profit (900 of dues earned against 930 of costs): Pat accrues nothing.
    assert all(bk.share[(CLUB, m)] == 0 for m in (1, 2, 3))
    # Cohort earns 2,220 a month (1,220 in month 2, when 1,000 of programs moves to month 3's
    # service): Jo's 50% accrues month by month on the year to date.
    assert [round(bk.share[(COHORT, m)], 2) for m in (1, 2, 3)] == [1110.0, 610.0, 1110.0]
    # An owner's salary ahead of the partner (12,000 a year: 1,000 a month) comes off the base first.
    data = dataset()
    data["partners"][1]["salary"] = True
    assert [round(books(data=data).share[(COHORT, m)], 2) for m in (1, 2, 3)] == [610.0, 110.0, 610.0]


def test_a_loss_takes_back_only_what_was_accrued():
    data = dataset()
    for l in data["ledger"]:
        if l["company"] == COHORT and l["cls"] == "revenue" and l["month"] in (2, 3):
            l["amount"] = 0.0            # no programs after month 1: 780 of costs a month, and a loss
    bk = books(data=data)
    # Month 1 accrues 1,110. Month 2 (-780, and -1,000 of timing) leaves the year at +440, so 890
    # comes back. Month 3's -780 takes the year below zero: only the 220 still accrued comes back.
    assert [round(bk.share[(COHORT, m)], 2) for m in (1, 2, 3)] == [1110.0, -890.0, -220.0]
    assert round(sum(bk.share[(COHORT, m)] for m in (1, 2, 3)), 2) == 0.0      # no profit, no share


def test_the_owners_charge_is_hers_or_the_businesss():
    own = books()
    biz = books({"charges": {"C1": {"treat": "business", "line": "Event costs"}}})
    assert _line(own, HUB, 2, "Advertising and marketing")["corrected"] == 60.0   # out of the costs
    assert _line(biz, HUB, 2, "Event costs")["corrected"] == 250.0                # a cost, on the line chosen
    draw = {r["account"]: r for r in own.corrections(HUB)["accounts"]}["Owner draw"]
    assert draw["amounts"][2] == 250.0


def test_investment_or_loan():
    inv = {r["account"]: r["total"] for r in books().corrections(COHORT)["accounts"]}
    loan = {r["account"]: r["total"] for r in books({"holdco": "Loan"}).corrections(COHORT)["accounts"]}
    assert inv["Distributions to Holdco"] == 5000.0 and "Loan from Holdco" not in inv
    assert loan["Loan from Holdco"] == 5000.0 and "Distributions to Holdco" not in loan


def test_the_revenue_basis_switch_picks_its_timing():
    prog = lambda bk: _line(bk, COHORT, 2, "Programs and coaching")["corrected"]
    assert prog(books()) == 2000.0
    assert prog(books({"bc_basis": "old_books"})) == 2600.0
    assert prog(books({"bc_basis": "received"})) == 3000.0


@pytest.mark.parametrize("view", COMPANIES + ["Inside", "Club", "Hub", "all"])
def test_the_detail_adds_up_to_the_line(view):
    bk = books({"charges": {"C1": {"treat": "business", "line": "Event costs"}}})
    for s in bk.statement(view)["sections"]:
        for r in s["lines"]:
            det = bk.line_detail(view, r["line"])
            assert det["rows_total"] == pytest.approx(r["corrected"], abs=0.02), (view, r["line"])


def test_a_transaction_reads_as_one_row_with_its_words():
    data = dataset()
    # one charge the hub paid for its own brand and the brand inside it: in the hub's view, one row
    data["ledger"] += [{**_l(HUB, "Hub", "cost", "Travel and meals", 2, 120.0, "Travel"), "key": "t1"},
                       {**_l(HUB, "Inside", "cost", "Travel and meals", 2, 80.0, "Travel"), "key": "t1"}]
    data["qbo"][HUB]["by_month"]["2"]["Travel and meals"] = 200.0
    data["accounts"]["acct_line"][f"{HUB}|Travel"] = "Travel and meals"
    data["transactions"]["t1"] = {
        "ref": "T2", "who": "AIRLINE INC", "payee": "Airline", "what": "Flights to the retreat", "note": "",
        "changed": "", "status": "duplicate", "paid_from": "Hub · Card ••1234", "card_holder": "Owner",
        "twins": [{"key": "t9", "date": "2026-02-16", "amount": -200.0, "paid_from": "Hub · Card ••1234"}],
        "qbo": {"payee": "AIRLINE INC", "category": "Travel"}, "amount": -200.0, "money_in": False,
        "date": "2026-02-15", "company": HUB}
    det = books(data=data).line_detail(HUB, "Travel and meals", 2)
    (tx,) = [r for r in det["rows"] if r["kind"] == "tx"]
    assert tx["amount"] == 200.0 and sorted(tx["brands"]) == ["Hub", "Inside"]
    assert (tx["payee"], tx["what"], tx["status"], tx["tx_amount"]) == ("Airline", "Flights to the retreat",
                                                                       "duplicate", -200.0)
    assert tx["twins"][0]["key"] == "t9" and tx["qbo"]["category"] == "Travel"
    # a file without the words (every file before them) still reads: its QuickBooks payee, and no flags
    (old,) = [r for r in books().line_detail(COHORT, "Programs and coaching", 1)["rows"] if r["kind"] == "tx"]
    assert (old["payee"], old["status"], old["twins"], old["qbo"]["payee"]) == ("Someone", "", [], "Someone")


def test_a_share_says_what_its_made_of():
    bk = books()
    staff = {r["who"]: r for r in bk.line_detail(CLUB, "Staff and contractors", 1)["rows"] if r.get("src") == "staff"}
    assert set(staff) == {"Ann", "Taxes"}                       # Bo is all the hub's (and the brand inside it)
    assert (staff["Ann"]["pct"], staff["Ann"]["pool_total"], staff["Ann"]["amount"]) == (0.5, 1000.0, 500.0)
    assert staff["Taxes"]["follows_wages"] is True and staff["Taxes"]["amount"] == 50.0
    (ads,) = [r for r in bk.line_detail(CLUB, "Advertising and marketing", 1)["rows"] if r.get("src") == "ads"]
    assert (ads["pct"], ads["pool_total"], ads["amount"]) == (0.5, 600.0, 300.0)
    (soft,) = [r for r in bk.line_detail(CLUB, "Software and subscriptions", 1)["rows"] if r.get("src") == "item"]
    assert (soft["who"], soft["item_kind"]) == ("Zoomish", "software") and soft["amount"] == pytest.approx(30.0)


@pytest.mark.parametrize("view", [CLUB, HUB, "Inside", "all"])
def test_the_staff_line_person_by_person_adds_up_to_it(view):
    bk = books()
    people = bk.line_detail(view, "Staff and contractors")["people"]
    assert sum(p["total"] for p in people) == pytest.approx(_line(bk, view, None, "Staff and contractors")["corrected"])
    if view == CLUB:
        club = {p["name"]: p for p in people}
        assert club["Ann"]["months"] == {"1": 500.0, "2": 500.0, "3": 500.0} and "Bo" not in club


def test_every_transaction_once_with_where_the_books_put_it():
    data = dataset()
    for i, l in enumerate(data["ledger"]):          # one transaction per ledger line, as a real file has them
        l["key"] = f"t{i:02d}"
        data["transactions"][l["key"]] = {"ref": "", "who": "Someone", "what": "A line", "note": "", "verdict": "",
                                          "category": "", "type": ""}
    del data["transactions"]["k"]
    txs = books(data=data).transactions()
    by = {t["key"]: t for t in txs}
    assert len(by) == len(txs) == len(data["ledger"])
    pick = lambda **kw: by[next(l["key"] for l in data["ledger"] if all(l[k] == v for k, v in kw.items()))]
    dues = pick(brand="Club", cls="revenue", month=1)
    assert dues["placement"] == [{"brand": "Club", "line": "Membership dues", "amount": 1000.0}]
    assert (dues["amount"], dues["money_in"]) == (1000.0, True)
    ads = pick(pool="Advertising", month=1)                    # shared: no one brand, the decisions split it
    assert ads["placement"] == [{"brand": None, "line": "Advertising and marketing", "amount": 600.0}]
    assert (ads["amount"], ads["money_in"]) == (-600.0, False)
    assert pick(cls="partner", month=1)["placement"] == []    # a partner's advance: not income, not a cost
    assert [t["date"] for t in txs] == sorted(t["date"] for t in txs)


def test_a_big_read_is_gzipped_when_the_browser_takes_it():
    import gzip
    from starlette.requests import Request
    from app.routers.books_cleanup import _big
    req = lambda enc: Request({"type": "http", "headers": [(b"accept-encoding", enc.encode())]})
    payload = {"rows": [{"what": "x" * 100, "n": i} for i in range(1000)]}
    z = _big(req("gzip, deflate, br"), payload)
    assert z.headers["content-encoding"] == "gzip" and json.loads(gzip.decompress(z.body)) == payload
    plain = _big(req(""), payload)
    assert "content-encoding" not in plain.headers and json.loads(plain.body) == payload


# ── changing one transaction (the tab's Move, Split, Personal, Not real, line and month) ─────────────

def keyed(data=None) -> dict:
    """The invented file with one transaction per ledger line, as a real file has them. "ev" is the event the
    hub paid for the club (QuickBooks has it on the hub, the review on the club), with its bank line."""
    data = data or dataset()
    data["transactions"] = {}
    for i, l in enumerate(data["ledger"]):
        l["key"] = "ev" if l["line"] == "Event costs" else f"t{i:02d}"
        data["transactions"][l["key"]] = {"ref": "", "who": "Someone", "what": "A line", "note": "", "verdict": "",
                                          "category": "", "type": "", "company": l["company"], "date": l["date"],
                                          "cleared": True, "cash": []}
    data["transactions"]["ev"].update({"payee": "Venue", "who": "VENUE LLC", "amount": -200.0, "money_in": False,
                                       "cleared": False, "cash": [{"account": "Checking", "amount": -200.0, "month": 3}]})
    return data


def changed(data, **tx) -> eng.Books:
    """The books with these transactions changed, each change checked the way the API checks it."""
    return books({"tx": {k: eng.validate_change(data, ["tx", k], v) for k, v in tx.items()}}, data=data)


def _ok(bk):
    assert all(c["ok"] for c in bk.checks()), [c for c in bk.checks() if not c["ok"]]
    for c in COMPANIES:
        assert bk.balance_sheet(c)["balanced"] == {"qbo": True, "corrected": True}, c


def _delta(before, after, company, m) -> dict:
    """How one month's correcting entry for a company changed, account by account."""
    a = {r["account"]: r["amounts"][m] for r in before.corrections(company)["accounts"]}
    b = {r["account"]: r["amounts"][m] for r in after.corrections(company)["accounts"]}
    return {k: round(b.get(k, 0) - a.get(k, 0), 2) for k in set(a) | set(b) if abs(b.get(k, 0) - a.get(k, 0)) >= 0.005}


def test_moving_a_charge_moves_its_cost_and_who_owes_whom():
    data = keyed()
    before, after = books(data=data), changed(data, ev={"do": "move", "to": "Cohort"})
    _ok(after)
    ev = lambda bk, c: _line(bk, c, 3, "Event costs")["corrected"]
    assert (ev(before, CLUB), ev(before, COHORT), ev(after, CLUB), ev(after, COHORT)) == (200.0, 0.0, 0.0, 200.0)
    # the hub paid it: the club no longer owes the hub for it, the cohort does
    hub, cohort, club = (_delta(before, after, c, 3) for c in (HUB, COHORT, CLUB))
    assert (hub[f"Due from {CLUB}"], hub[f"Due from {COHORT}"]) == (-200.0, 200.0)
    assert (cohort[f"{COHORT} Event costs"], cohort[f"Due to {HUB}"]) == (200.0, -200.0)
    assert (club[f"{CLUB} Event costs"], club[f"Due to {HUB}"]) == (-200.0, 200.0)


def test_a_split_charge_lands_on_each_company():
    bk = changed(keyed(), ev={"do": "split", "parts": {"Club": 0.25, "Cohort": 0.75}})
    _ok(bk)
    assert (_line(bk, CLUB, 3, "Event costs")["corrected"], _line(bk, COHORT, 3, "Event costs")["corrected"]) == (50.0, 150.0)


def test_a_personal_charge_leaves_every_companys_costs():
    data = keyed()
    before, own = books(data=data), changed(data, ev={"do": "personal", "who": "owner"})
    _ok(own)
    total = lambda bk: sum(_line(bk, c, 3, "Event costs")["corrected"] for c in COMPANIES)
    assert total(before) - total(own) == 200.0
    assert _delta(before, own, HUB, 3)["Owner draw"] == 200.0             # the hub paid it: the owner's draw
    pat = changed(data, ev={"do": "personal", "who": "Pat"})
    _ok(pat)
    assets = next(s for s in pat.balance_sheet(HUB)["sections"] if s["key"] == "Asset")["rows"]
    due = next(r for r in assets if r["account"] == "Due from Pat")       # somebody else's: owed back
    assert due["corrected"] == 200.0 and due["note"].startswith("New")


def test_paid_twice_is_owed_back_and_never_happened_takes_out_the_bank_line():
    data = keyed()
    before = books(data=data)
    twice = changed(data, ev={"do": "remove", "reason": "paid_twice"})
    _ok(twice)
    assert _line(twice, CLUB, 3, "Event costs")["corrected"] == 0.0
    assert _delta(before, twice, HUB, 3)["Due from Venue"] == 200.0          # the payee owes the second one back
    never = changed(data, ev={"do": "remove", "reason": "never"})
    _ok(never)
    assert _delta(before, never, HUB, 3)["Checking"] == 200.0             # and the payment comes back out of the bank
    bank = lambda bk: next(r for r in next(s for s in bk.balance_sheet(HUB)["sections"] if s["key"] == "Asset")["rows"]
                           if r["account"] == "Checking")["corrected"]
    assert bank(never) - bank(before) == 200.0


def test_another_line_or_another_month():
    data = keyed()
    line = changed(data, ev={"do": "line", "line": "Travel and meals"})
    _ok(line)
    assert (_line(line, CLUB, 3, "Event costs")["corrected"], _line(line, CLUB, 3, "Travel and meals")["corrected"]) == (0.0, 200.0)
    early = changed(data, ev={"do": "month", "month": 1})                 # paid in month 3, counted in month 1
    _ok(early)
    assert (_line(early, CLUB, 3, "Event costs")["corrected"], _line(early, CLUB, 1, "Event costs")["corrected"]) == (0.0, 200.0)
    accrued = {r["account"]: r["amounts"] for r in early.corrections(CLUB)["accounts"]}[f"{CLUB} accrued liabilities"]
    assert (accrued[1], accrued[3]) == (-200.0, 200.0)


def test_every_kind_of_change_at_once_still_balances():
    data = keyed()
    pick = lambda **kw: next(l["key"] for l in data["ledger"] if all(l[k] == v for k, v in kw.items()))
    bk = changed(data, ev={"do": "move", "to": "Inside"},
                 **{pick(brand="Cohort", cls="revenue", month=2): {"do": "split", "parts": {"Club": 0.5, "Cohort": 0.5}},
                    pick(pool="Advertising", month=1): {"do": "move", "to": "Club"},
                    pick(pool="Staff and contractors", item="Ann", month=2): {"do": "personal", "who": "owner"},
                    pick(brand="Inside", cls="revenue", month=3): {"do": "month", "month": 1},
                    pick(pool="Software and subscriptions", item="Zoomish", month=3): {"do": "remove", "reason": "paid_twice"}})
    _ok(bk)
    for view in COMPANIES + ["Inside", "all"]:
        for s in bk.statement(view)["sections"]:
            for r in s["lines"]:
                assert bk.line_detail(view, r["line"])["rows_total"] == pytest.approx(r["corrected"], abs=0.02)


@pytest.mark.parametrize("key,value", [
    ("nope", {"do": "move", "to": "Club"}),                      # no such transaction
    ("ev", {"do": "move", "to": "Elsewhere"}),
    ("ev", {"do": "split", "parts": {"Club": 0.5, "Cohort": 0.4}}),
    ("ev", {"do": "split", "parts": {"Club": 1.0}}),
    ("ev", {"do": "line", "line": "Membership dues"}),           # a cost onto an income line
    ("ev", {"do": "month", "month": 7}),                         # a month the books don't cover
    ("ev", {"do": "fly"}),
    ("ev", "move"),
])
def test_a_change_the_books_cannot_hold_is_refused(key, value):
    with pytest.raises(eng.InputError):
        eng.validate_change(keyed(), ["tx", key], value)


def test_some_changes_need_the_right_transaction():
    data = keyed()
    pick = lambda **kw: next(l["key"] for l in data["ledger"] if all(l[k] == v for k, v in kw.items()))
    data["transactions"]["ev"]["cleared"] = True
    for key, value in ((pick(cls="partner"), {"do": "move", "to": "Club"}),       # an advance: not income or a cost
                       (pick(cls="revenue"), {"do": "personal"}),                 # money in isn't anybody's spending
                       (pick(pool="Advertising"), {"do": "line", "line": "Event costs"}),  # a shared cost's line is its pool's
                       ("ev", {"do": "remove", "reason": "never"})):              # the bank shows it: it happened
        with pytest.raises(eng.InputError):
            eng.validate_change(data, ["tx", key], value)
    assert eng.validate_change(data, ["tx", "ev"], None) is None                  # undo


def test_a_moved_charge_stays_in_sight():
    data = keyed()
    bk = changed(data, ev={"do": "move", "to": "Cohort"})
    club = bk.line_detail(CLUB, "Event costs", 3)
    (gone,) = [r for r in club["rows"] if r.get("gone")]
    assert (gone["amount"], gone["was"], gone["status"], gone["change"]["to"]) == (0.0, 200.0, "yours", "Cohort")
    assert club["rows_total"] == club["corrected"] == 0.0
    (here,) = [r for r in bk.line_detail(COHORT, "Event costs", 3)["rows"] if r["kind"] == "tx"]
    assert (here["amount"], here["status"]) == (200.0, "yours") and "was" not in here
    t = next(t for t in bk.transactions() if t["key"] == "ev")
    assert t["placement"] == [{"brand": "Cohort", "line": "Event costs", "amount": 200.0}]
    assert t["was"] == [{"brand": "Club", "line": "Event costs", "amount": 200.0}]
    assert eng.describe_change(data, "ev", {"do": "move", "to": "Cohort"}) == "Venue, Mar 15, $200.00: moved to Cohort"


# ── what the companies owe each other, item by item; each entry's line in words ──────────────────────

SPLIT = eng.COMPONENTS["split"]


def test_a_pair_says_why_and_is_made_of_its_items():
    bk = books()
    (pair,) = bk.intercompany()["pairs"]
    # the club's shares of the hub's costs, 930 a month, and the event the review moved onto the club
    assert [(p["component"], p["amount"]) for p in pair["parts"]] == [("split", 2790.0), ("moved", 200.0)]
    dt = bk.interco_detail("Hub and Club")
    assert dt["ties"] and dt["corrections"] == 2990.0 and dt["itemized"] is False
    m1 = {(r["src"], r["who"]): r["amount"] for r in dt["rows"] if r["kind"] == "share" and r["month"] == 1}
    assert m1 == {("ads", "Advertising"): 300.0, ("fees", "Payment processing"): 50.0, ("staff", "Ann"): 500.0,
                  ("staff", "Taxes"): 50.0, ("item", "Zoomish"): 30.0}
    # a file without the review's rows per transaction says what the review did, not which transaction
    (moved,) = [r for r in dt["rows"] if r["component"] == "moved"]
    assert (moved["kind"], moved["amount"], moved["what"]) == ("other", 200.0, eng.COMPONENTS["moved"])
    with pytest.raises(eng.InputError):
        bk.interco_detail("Nobody and Nothing")


def test_a_pair_names_the_transactions_the_review_moved():
    data = keyed()
    data["interco"]["items"] = [{"company": HUB, "account": f"Due from {CLUB}", "key": "ev", "component": "moved",
                                 "sub": None, "month": 3, "amount": 200.0}]
    dt = books(data=data).interco_detail("Hub and Club")
    (ev,) = [r for r in dt["rows"] if r["component"] == "moved"]
    assert (ev["kind"], ev["key"], ev["amount"], ev["tx"]["payee"]) == ("tx", "ev", 200.0, "Venue")
    assert dt["ties"] and dt["itemized"]


def test_a_change_on_the_tab_shows_on_its_pair_in_its_own_words():
    data = keyed()
    bk = changed(data, ev={"do": "move", "to": "Cohort"})
    _ok(bk)
    hub_club = bk.interco_detail("Hub and Club")
    assert hub_club["corrections"] == 2790.0 and hub_club["ties"]            # the club no longer owes for the event
    (ev,) = [r for r in hub_club["rows"] if r["component"] == "yours"]
    assert (ev["kind"], ev["key"], ev["amount"]) == ("tx", "ev", -200.0)
    assert ev["why"] == "Venue, Mar 15, $200.00: moved to Cohort" and ev["tx"]["payee"] == "Venue"


def test_each_line_of_an_entry_says_what_it_is_for():
    bk = books()
    hub = {r["account"]: r for r in bk.corrections(HUB)["accounts"]}
    assert hub["Pay:Ann"]["said"][1] == f"{SPLIT}: Ann's pay"
    due = hub[f"Due from {CLUB}"]["said"]
    assert due[1] == f"{SPLIT}: Ann's pay, the shared ads, Payment processing and 2 more"     # largest first
    assert due[3].endswith(f"; {eng.COMPONENTS['moved']}") and due[0] == ""
    assert hub["Due from Neighbour"]["said"][2] == f"{SPLIT}: ads billed back to Neighbour"
    # a change made on the tab is named in the entry it makes
    moved = changed(keyed(), ev={"do": "move", "to": "Cohort"})
    said = {r["account"]: r["said"] for r in moved.corrections(HUB)["accounts"]}[f"Due from {COHORT}"][3]
    assert f"{eng.COMPONENTS['yours']}: Venue, Mar 15, $200.00: moved to Cohort" in said


# ── the forward view (Forecast, Budget, Cash flow) ───────────────────────────────────────────────────

def forwarded(data=None) -> dict:
    """The invented file projecting the cohort two months past its books (which end half-way through month 3)."""
    data = data or dataset()
    data["forward"] = {
        "company": COHORT, "revenue_line": "Programs and coaching", "as_of": "2026-03-15", "horizon": "2026-05",
        "first_fraction": 0.5,
        "contracts": {"members": 2, "contracted": 9000.0, "collected": 6000.0, "still_to_collect": 3000.0,
                      "earned_so_far": 4000.0, "stopped": {"count": 0, "plan_value": 0.0, "kept": 0.0}},
        "schedule": [{"key": "2026-03", "label": "Mar 16-31", "from_paid": 500.0, "from_future": 0.0},
                     {"key": "2026-04", "label": "Apr 2026", "from_paid": 1000.0, "from_future": 500.0},
                     {"key": "2026-05", "label": "May 2026", "from_paid": 0.0, "from_future": 1500.0},
                     {"key": "2026-06", "label": "Jun 2026", "from_paid": 0.0, "from_future": 1000.0}],
        "collections": {"2026-04": 1000.0, "2026-05": 2000.0},
        "launches": [{"name": "Launch", "window": "Jan – Feb", "months": [1, 2], "members": 2, "contracted": 9000.0}],
        "cash": {"account": "Cohort checking", "as_of": "2026-02-28", "balance": 5000.0},
        "selling": ["Advertising and marketing", "Sales commissions", "Event costs"],
        "plan_from": {"Event costs": "retreats", "Sales commissions": "commissions"},
        "plan_defaults": {"Advertising and marketing": 0}, "notes": {},
        "assumptions": {"commission_pct": 0, "partner_pay": 100.0, "partner_end": "2026-05",
                        "retreats": [{"label": "A retreat", "key": "2026-05", "amount": 300.0, "on": False}],
                        "repay": {"label": "Repay the hub", "key": "2026-04", "amount": 1000.0, "on": False}},
        "meeting": {"label": "The meeting's", "commission_pct": 10, "retreats_on": True}}
    return data


def test_the_forward_view_accrues_the_partner_share_as_the_books_do():
    f = books(data=forwarded()).forward()
    # the plan: the last three months' pace (staff 550, fees 20, software 30), the file's no ads
    assert f["plan"] == {"Advertising and marketing": 0.0, "Staff and contractors": 550.0, "Payment processing": 20.0,
                         "Software and subscriptions": 30.0} and f["running"] == 600.0
    rows = {r["key"]: r for r in f["rows"]}
    # month 3: the books' 3,000 and 780, plus the rest of the month: 500 of contracts and half a month of plan
    assert (rows["2026-03"]["rev"], rows["2026-03"]["cost"], rows["2026-03"]["projected"]) == (3500.0, 1080.0, True)
    assert (rows["2026-04"]["profit"], rows["2026-05"]["profit"]) == (900.0, 900.0)
    # Jo's 50% on the year so far, month by month: 1,110, 610, 1,210, 450, 450
    assert [rows[k]["share"] for k in sorted(rows)] == [1110.0, 610.0, 1210.0, 450.0, 450.0]
    # paid: 800 a month on the books, then the assumption's 100 (month 3 had a real payment, so it's that)
    assert (f["summary"]["earned"], f["summary"]["paid"], f["summary"]["ahead"]) == (3830.0, 2600.0, -1230.0)
    # what the Statement adds for the months ahead: 2.5 months of plan, the contracts, the projected shares (month 3's
    # 1,210 less the 1,110 the books already hold for it, then 450 and 450)
    assert f["future_by_line"]["Programs and coaching"] == 3500.0
    assert f["future_by_line"]["Staff and contractors"] == 1375.0 and f["future_by_line"]["Partner profit shares"] == 1000.0


def test_the_meetings_assumptions_and_the_budget_move_the_forecast():
    data = forwarded()
    meeting = books({"forward": {"commission_pct": 10, "retreats": [
        {"label": "A retreat", "key": "2026-05", "amount": 300.0, "on": True}]}}, data=data).forward()
    rows = {r["key"]: r for r in meeting["rows"]}
    assert rows["2026-05"]["cost"] == 600.0 + 300.0 + 200.0                 # the plan, the retreat, 10% of 2,000 in
    assert meeting["future_by_line"]["Event costs"] == 300.0 and meeting["future_by_line"]["Sales commissions"] == 300.0
    leaner = books({"budget": {"Staff and contractors": 400.0}}, data=data).forward()
    assert leaner["running"] == 450.0 and leaner["plan"]["Staff and contractors"] == 400.0


def test_cash_flows_from_the_starting_balance():
    data = forwarded()
    cash = books(data=data).forward()["cash"]
    assert [(r["key"], r["close"]) for r in cash["rows"]] == [("2026-03", 4300.0), ("2026-04", 4600.0), ("2026-05", 5900.0)]
    assert cash["runs_out"] is None
    repay = {"forward": {"repay": {"label": "Repay the hub", "key": "2026-04", "amount": 5000.0, "on": True}}}
    cash = books(repay, data=data).forward()["cash"]
    assert [r["close"] for r in cash["rows"]] == [4300.0, -400.0, 900.0] and cash["runs_out"] == "2026-04"


def test_a_company_budget_is_its_lines_so_far_and_at_the_recent_pace():
    b = books(data=forwarded()).budget(COHORT)
    staff = next(l for l in b["lines"] if l["line"] == "Staff and contractors")
    assert (staff["ytd"], staff["recent"]) == (1650.0, 550.0) and staff["pct"] == pytest.approx(1650 / 8000 * 100, abs=0.01)
    assert b["revenue"] == 8000.0 and b["noi"] == 8000.0 - 2340.0


@pytest.mark.parametrize("path,value", [
    (["forward", "commission_pct"], 101),
    (["forward", "partner_end"], "2026-06"),                 # past the horizon
    (["forward", "retreats"], [{"label": "x", "key": "2027-01", "amount": 1, "on": True}]),
    (["forward", "retreats"], [{"label": "x", "key": "2026-04", "amount": 1, "on": True}] * 9),
    (["forward", "nonsense"], 1),
    (["budget", "Event costs"], 100),                        # the forecast plans that line itself
    (["budget", "Staff and contractors"], -5),
])
def test_a_forward_change_the_file_cannot_hold_is_refused(path, value):
    with pytest.raises(eng.InputError):
        eng.validate_change(forwarded(), path, value)


def test_a_file_that_projects_nothing_has_no_forward_view():
    assert books().forward() is None
    with pytest.raises(eng.InputError):
        eng.validate_change(dataset(), ["forward", "commission_pct"], 5)


def test_merged_inputs_change_only_what_was_saved():
    data = dataset()
    m = eng.merged_inputs(data, {"staff": {"Ann": {"2": {"Club": 1.0, "Cohort": 0.0}}}})
    assert m["staff"]["Ann"]["2"] == {"Club": 1.0, "Cohort": 0.0, "Inside": 0.0, "Hub": 0.0}
    assert m["staff"]["Ann"]["1"]["Club"] == 0.5                                 # untouched
    assert data["inputs"]["staff"]["Ann"]["2"]["Club"] == 0.5                    # the file isn't mutated


@pytest.mark.parametrize("path,value", [
    (["staff", "Nobody", "1", "Club"], 0.5),
    (["staff", "Taxes", "1", "Club"], 0.5),            # follows the wages: not a decision
    (["staff", "Ann", "13", "Club"], 0.5),
    (["staff", "Ann", "1", "Club"], 1.5),
    (["staff", "Ann", "1", "Elsewhere"], 0.5),
    (["ads", "Club"], -0.1),
    (["holdco"], "Gift"),
    (["bc_basis"], "vibes"),
    (["charges", "C1", "line"], "Uncategorized expense (old books)"),
    (["charges", "C1", "treat"], "maybe"),
    (["answers", "Q404"], "yes"),
    (["posted", "Nobody Co", "1"], "JE 1"),
    (["salary"], "lots"),
    (["salary"], -1),
    (["checklist", "step01"], True),
    (["anything"], 1),
])
def test_a_change_the_file_cannot_hold_is_refused(path, value):
    with pytest.raises(eng.InputError):
        eng.validate_change(dataset(), path, value)


# ── the API ──────────────────────────────────────────────────────────────────────────────────────

def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token):
    return {"Authorization": f"Bearer {token}", "x-tenant-host": "cleanco.localhost"}


def _file(data=None):
    return {"file": ("cleanup.json", io.BytesIO(json.dumps(data or dataset()).encode()), "application/json")}


@pytest.fixture(scope="module")
async def ctx():
    from app.seed import seed
    await seed()
    async with SessionLocal() as s:
        t = Tenant(slug="cleanco", name="Clean Co", plan="portfolio")
        s.add(t)
        await s.flush()

        def mk(email, role, tabs=None):
            return User(tenant_id=t.id, email=email, name=email.split("@")[0], role=role,
                        password_hash=hash_pw("x" * 12), status="active", tab_access=tabs)
        owner = mk("owner@clean.test", "owner")
        keeper = mk("keeper@clean.test", "member", ["cleanup"])
        other = mk("other@clean.test", "member", ["books"])
        s.add_all([owner, keeper, other])
        await s.commit()
        return {"tenant": t.id, "owner": make_token(owner.id, t.id, 0), "keeper": make_token(keeper.id, t.id, 0),
                "other": make_token(other.id, t.id, 0)}


async def _has_file(ctx) -> bool:
    async with SessionLocal() as s:
        return (await s.execute(select(CleanupDataset.id).where(
            CleanupDataset.tenant_id == ctx["tenant"]).limit(1))).first() is not None


async def _loaded(ctx):
    """Load the invented file once, so each API test also runs on its own."""
    if await _has_file(ctx):
        return
    async with _client() as c:
        r = await c.post("/api/v1/cleanup/dataset", headers=_H(ctx["owner"]), files=_file())
        assert r.status_code == 200, r.text


async def test_the_tab_exists_only_once_a_file_is_loaded(ctx):
    if await _has_file(ctx):
        pytest.skip("the file is already loaded (this test runs first in the module)")
    async with _client() as c:
        me = (await c.get("/api/v1/me", headers=_H(ctx["owner"]))).json()
        assert "cleanup" not in me["tabs"] and not any(n["key"] == "cleanup" for n in me["nav"])
        assert (await c.get("/api/v1/cleanup", headers=_H(ctx["owner"]))).status_code == 403
        # a member, even one granted the tab, does not load the company's books
        assert (await c.post("/api/v1/cleanup/dataset", headers=_H(ctx["keeper"]), files=_file())).status_code == 403
        r = await c.post("/api/v1/cleanup/dataset", headers=_H(ctx["owner"]), files=_file())
        assert r.status_code == 200, r.text
        me = (await c.get("/api/v1/me", headers=_H(ctx["owner"]))).json()
        assert "cleanup" in me["tabs"]
        keys = [n["key"] for n in me["nav"]]
        assert keys.index("cleanup") == keys.index("books") + 1                 # right after Books in the rail
        ov = (await c.get("/api/v1/cleanup", headers=_H(ctx["owner"]))).json()
        assert all(ch["ok"] for ch in ov["checks"]), ov["checks"]
        assert ov["views"] == COMPANIES + ["Inside", "all"] and ov["can_decide"] is True


async def test_a_file_that_isnt_one_is_refused(ctx):
    async with _client() as c:
        # Postgres keeps the file as JSONB, which refuses a NaN or a NUL that SQLite stores silently:
        # those are a 400 saying where, not a 500 from production's insert.
        nan, nul = dataset(), dataset()
        nan["ledger"][0]["amount"] = float("nan")
        nul["transactions"]["k"]["what"] = "A line" + chr(0)
        for body in (b"", b"not json", json.dumps({"schema": "other"}).encode(),
                     json.dumps({**dataset(), "ledger": None}).encode(), json.dumps(nan).encode(),
                     json.dumps(nul).encode()):
            files = {"file": ("x.json", io.BytesIO(body), "application/json")}
            r = await c.post("/api/v1/cleanup/dataset", headers=_H(ctx["owner"]), files=files)
            assert r.status_code == 400, (body[:20], r.status_code)
        assert "ledger[0].amount is nan" in r.text or "NUL" in r.text


async def test_who_may_read_and_who_may_decide(ctx):
    await _loaded(ctx)
    async with _client() as c:
        # a member without the grant sees nothing, not even that the tab exists
        assert (await c.get(f"/api/v1/cleanup/statement?view={HUB}", headers=_H(ctx["other"]))).status_code == 403
        assert "cleanup" not in (await c.get("/api/v1/me", headers=_H(ctx["other"]))).json()["tabs"]
        # the bookkeeper reads, answers and ticks...
        assert (await c.get(f"/api/v1/cleanup/statement?view={HUB}", headers=_H(ctx["keeper"]))).status_code == 200
        assert (await c.get("/api/v1/cleanup", headers=_H(ctx["keeper"]))).json()["can_decide"] is False
        r = await c.put("/api/v1/cleanup/inputs", headers=_H(ctx["keeper"]), json={"changes": [
            {"path": ["answers", "Q1"], "value": "Answered. " * 300},
            {"path": ["checklist", "step01"], "value": {"done": True, "note": "JE 101-103"}},
            {"path": ["posted", HUB, "1"], "value": "JE 101"}]})
        assert r.status_code == 200, r.text
        items = (await c.get("/api/v1/cleanup/items", headers=_H(ctx["keeper"]))).json()
        assert items["open"][0]["answer"].startswith("Answered.") and items["checklist"][0]["done"] is True
        posted = (await c.get(f"/api/v1/cleanup/corrections?company={HUB}", headers=_H(ctx["keeper"]))).json()["posted"]
        assert posted == {"1": "JE 101"}
        # ...but does not move money between the companies, even inside an otherwise allowed set
        r = await c.put("/api/v1/cleanup/inputs", headers=_H(ctx["keeper"]), json={"changes": [
            {"path": ["answers", "Q1"], "value": "x"}, {"path": ["staff", "Ann", "1", "Club"], "value": 1.0}]})
        assert r.status_code == 403
        assert (await c.post("/api/v1/cleanup/dataset", headers=_H(ctx["keeper"]), files=_file())).status_code == 403


async def test_an_owners_split_moves_the_numbers_and_is_audited(ctx):
    await _loaded(ctx)
    staff = lambda st: next(x for s in st["sections"] for x in s["lines"] if x["line"] == "Staff and contractors")
    async with _client() as c:
        url = f"/api/v1/cleanup/statement?view={CLUB}&month=1"
        before = (await c.get(url, headers=_H(ctx["owner"]))).json()
        r = await c.put("/api/v1/cleanup/inputs", headers=_H(ctx["owner"]), json={"changes": [
            {"path": ["staff", "Ann", "1", "Club"], "value": 1.0},
            {"path": ["staff", "Ann", "1", "Cohort"], "value": 0.0},
            {"path": ["items", "software", LONG, "Hub"], "value": 0.5},
            {"path": ["items", "software", LONG, "Inside"], "value": 0.5}]})
        assert r.status_code == 200, r.text
        assert all(ch["ok"] for ch in r.json()["checks"])
        after = (await c.get(url, headers=_H(ctx["owner"]))).json()
        assert staff(after)["corrected"] - staff(before)["corrected"] == pytest.approx(500.0 + 50.0)
        # a set with one change the file can't hold is refused whole
        r = await c.put("/api/v1/cleanup/inputs", headers=_H(ctx["owner"]), json={"changes": [
            {"path": ["staff", "Ann", "2", "Club"], "value": 1.0},
            {"path": ["staff", "Nobody", "2", "Club"], "value": 1.0}]})
        assert r.status_code == 400
        dec = (await c.get("/api/v1/cleanup/decisions", headers=_H(ctx["owner"]))).json()
        assert next(p for p in dec["staff"] if p["name"] == "Ann")["split"]["2"]["Club"] == 0.5
        line = (await c.get(f"/api/v1/cleanup/statement/line?view={CLUB}&line=Staff and contractors&month=1",
                            headers=_H(ctx["owner"]))).json()
        assert line["rows_total"] == pytest.approx(staff(after)["corrected"])
        # a month the file doesn't cover, or a view or line it doesn't have, is a 400 rather than a 500
        for bad in (f"/api/v1/cleanup/statement?view={CLUB}&month=4", "/api/v1/cleanup/statement?view=Nowhere",
                    f"/api/v1/cleanup/statement/line?view={CLUB}&line=Staff and contractors&month=7",
                    f"/api/v1/cleanup/statement/line?view={CLUB}&line=Nonsense"):
            assert (await c.get(bad, headers=_H(ctx["owner"]))).status_code == 400, bad
        csv = await c.get(f"/api/v1/cleanup/corrections.csv?company={HUB}", headers=_H(ctx["owner"]))
        assert csv.status_code == 200 and csv.text.startswith("Journal No.,Journal Date,Account Name,Debits,Credits")
        by_entry = {}                                          # each month's entry in the file balances
        for row in csv.text.strip().splitlines()[1:]:
            jno, _, _, dr, cr, _ = row.split(",", 5)
            by_entry[jno] = by_entry.get(jno, 0) + float(dr or 0) - float(cr or 0)
        assert by_entry and all(abs(v) < 0.05 for v in by_entry.values()), by_entry
    async with SessionLocal() as s:
        rows = (await s.execute(select(AuditLog).where(AuditLog.tenant_id == ctx["tenant"],
                                                       AuditLog.action == "cleanup.change"))).scalars().all()
        assert "staff/Ann/1/Club" in {r.target_id for r in rows}
        # Postgres refuses what SQLite stores: keeping to the columns' lengths is the code's job
        assert all(len(r.target_id) <= 64 and len(r.summary) <= 500 for r in rows)
        hub = next(r for r in rows if r.target_id.startswith("items/software/") and r.detail["path"][3] == "Hub")
        assert hub.detail["path"][2] == LONG and hub.detail["from"] == 1.0 and hub.detail["to"] == 0.5


async def test_the_years_transactions_are_the_tabs(ctx):
    await _loaded(ctx)
    async with _client() as c:
        assert (await c.get("/api/v1/cleanup/transactions", headers=_H(ctx["other"]))).status_code == 403
        r = await c.get("/api/v1/cleanup/transactions", headers=_H(ctx["keeper"]))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["rows"] and body["brands"] == BRANDS and body["brand_company"]["Inside"] == HUB
        line = await c.get(f"/api/v1/cleanup/statement/line?view={CLUB}&line=Staff and contractors",
                           headers=_H(ctx["keeper"]))
        assert line.status_code == 200 and line.json()["people"]


async def test_an_owner_moves_a_transaction_and_can_undo_it(ctx):
    await _loaded(ctx)
    async with _client() as c:
        assert (await c.post("/api/v1/cleanup/dataset", headers=_H(ctx["owner"]), files=_file(keyed()))).status_code == 200
        move = {"changes": [{"path": ["tx", "ev"], "value": {"do": "move", "to": "Cohort", "why": "Spring, Oct 19"}}]}
        # it moves money between companies: an owner's or admin's
        assert (await c.put("/api/v1/cleanup/inputs", headers=_H(ctx["keeper"]), json=move)).status_code == 403
        r = await c.put("/api/v1/cleanup/inputs", headers=_H(ctx["owner"]), json=move)
        assert r.status_code == 200, r.text
        assert all(ch["ok"] for ch in r.json()["checks"])
        (ch,) = (await c.get("/api/v1/cleanup/decisions", headers=_H(ctx["owner"]))).json()["tx_changes"]
        assert (ch["key"], ch["change"]["to"], ch["change"]["why"]) == ("ev", "Cohort", "Spring, Oct 19")
        assert ch["change"]["by"] and ch["change"]["at"] and ch["said"] == "Venue, Mar 15, $200.00: moved to Cohort"
        undo = {"changes": [{"path": ["tx", "ev"], "value": None}]}
        assert (await c.put("/api/v1/cleanup/inputs", headers=_H(ctx["owner"]), json=undo)).status_code == 200
        assert (await c.get("/api/v1/cleanup/decisions", headers=_H(ctx["owner"]))).json()["tx_changes"] == []
    async with SessionLocal() as s:
        said = {r.summary for r in (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == ctx["tenant"], AuditLog.target_id == "tx/ev"))).scalars().all()}
        assert said >= {"Books Cleanup: Venue, Mar 15, $200.00: moved to Cohort",
                        "Books Cleanup: Venue, Mar 15, $200.00: back to where the review put it"}


async def test_the_forward_view_is_the_tabs_to_read_and_the_owners_to_plan(ctx):
    await _loaded(ctx)
    async with _client() as c:
        assert (await c.post("/api/v1/cleanup/dataset", headers=_H(ctx["owner"]), files=_file(forwarded()))).status_code == 200
        r = await c.get("/api/v1/cleanup/forward", headers=_H(ctx["keeper"]))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["forward"]["company"] == COHORT and set(body["budgets"]) == set(COMPANIES)
        assert body["forward"]["statement"]["totals"]["through"]["total_revenue"] == pytest.approx(8000.0 + 3500.0)
        plan = {"changes": [{"path": ["budget", "Staff and contractors"], "value": 400}]}
        assert (await c.put("/api/v1/cleanup/inputs", headers=_H(ctx["keeper"]), json=plan)).status_code == 403
        assert (await c.put("/api/v1/cleanup/inputs", headers=_H(ctx["owner"]), json=plan)).status_code == 200
        assert (await c.get("/api/v1/cleanup/forward", headers=_H(ctx["owner"]))).json()["forward"]["running"] == 450.0


async def test_a_new_file_keeps_the_decisions(ctx):
    await _loaded(ctx)
    async with _client() as c:
        r = await c.put("/api/v1/cleanup/inputs", headers=_H(ctx["owner"]), json={"changes": [
            {"path": ["staff", "Bo", "3", "Hub"], "value": 0.5}, {"path": ["staff", "Bo", "3", "Inside"], "value": 0.5}]})
        assert r.status_code == 200, r.text
        data = dataset()
        data["label"] = "Invented cleanup, second upload"
        assert (await c.post("/api/v1/cleanup/dataset", headers=_H(ctx["owner"]), files=_file(data))).status_code == 200
        dec = (await c.get("/api/v1/cleanup/decisions", headers=_H(ctx["owner"]))).json()
        assert next(p for p in dec["staff"] if p["name"] == "Bo")["split"]["3"]["Inside"] == 0.5
        assert (await c.get("/api/v1/cleanup", headers=_H(ctx["owner"]))).json()["label"] == data["label"]
    async with SessionLocal() as s:
        n = len((await s.execute(select(CleanupDataset.id).where(CleanupDataset.tenant_id == ctx["tenant"]))).all())
        assert n >= 2                                                            # kept, not replaced


async def test_a_pair_item_by_item_and_the_entries_in_words(ctx):
    await _loaded(ctx)
    url = "/api/v1/cleanup/intercompany/pair?pair=Hub and Club"
    async with _client() as c:
        assert (await c.get(url, headers=_H(ctx["other"]))).status_code == 403
        r = await c.get(url, headers=_H(ctx["keeper"]))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ties"] and sum(x["amount"] for x in body["rows"]) == pytest.approx(body["corrections"], abs=1)
        assert (await c.get("/api/v1/cleanup/intercompany/pair?pair=Nobody", headers=_H(ctx["keeper"]))).status_code == 400
        assert (await c.get("/api/v1/cleanup/intercompany", headers=_H(ctx["keeper"]))).json()["pairs"][0]["parts"]
        # the import file: each line says what it's for
        csv = await c.get(f"/api/v1/cleanup/corrections.csv?company={HUB}", headers=_H(ctx["owner"]))
        assert f"{SPLIT}: " in csv.text and "Ann's pay" in csv.text


async def test_ask_about_this(ctx, monkeypatch):
    from app.config import settings
    from app.services import cleanup_ask
    await _loaded(ctx)
    q = {"about": {"kind": "line", "view": CLUB, "line": "Staff and contractors", "month": 2},
         "question": "Why is half of Ann's pay on the club?"}
    ask = lambda who, body: c.post("/api/v1/cleanup/ask", headers=_H(ctx[who]), json=body)
    seen = []

    async def fake(system, messages):
        seen.append((system, messages))
        return "Half of Ann's pay is the club's and half the cohort's."

    async def broken(system, messages):
        raise RuntimeError("the network")

    async with _client() as c:
        assert (await c.post("/api/v1/cleanup/dataset", headers=_H(ctx["owner"]), files=_file(keyed()))).status_code == 200
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
        assert (await c.get("/api/v1/cleanup", headers=_H(ctx["keeper"]))).json()["can_ask"] is False
        assert (await ask("keeper", q)).status_code == 503                   # no key on the server: not offered
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(cleanup_ask, "_claude_call", fake)
        assert (await c.get("/api/v1/cleanup", headers=_H(ctx["keeper"]))).json()["can_ask"] is True
        # a read: the bookkeeper may ask; a member without the tab may not
        assert (await ask("other", q)).status_code == 403
        r = await ask("keeper", {**q, "history": [{"role": "user", "content": "What is this line?"},
                                                  {"role": "assistant", "content": "The hub's staff."}]})
        assert r.status_code == 200, r.text
        assert r.json()["answer"].startswith("Half of Ann's pay")
        system, messages = seen[-1]
        assert '"line":"Staff and contractors"' in system and '"who":"Ann"' in system      # the line, as the tab has it
        assert [m["role"] for m in messages] == ["user", "assistant", "user"] and messages[-1]["content"] == q["question"]
        # a transaction, and a pair
        assert (await ask("keeper", {**q, "about": {"kind": "tx", "key": "ev"}})).status_code == 200
        assert '"payee":"Venue"' in seen[-1][0]
        assert (await ask("keeper", {**q, "about": {"kind": "pair", "pair": "Hub and Club"}})).status_code == 200
        assert '"pair":"Hub and Club"' in seen[-1][0]
        for bad in ({"kind": "tx", "key": "nope"}, {"kind": "line", "view": HUB, "line": "Nonsense"},
                    {"kind": "line", "view": HUB, "line": "Staff and contractors", "month": 9},
                    {"kind": "line", "view": HUB, "line": "Staff and contractors", "month": "March"},
                    {"kind": "line", "view": "Nowhere", "line": "Staff and contractors"},
                    {"kind": "pair", "pair": "Nobody"}, {"kind": "weather"}):
            assert (await ask("keeper", {**q, "about": bad})).status_code == 400, bad
        monkeypatch.setattr(cleanup_ask, "_claude_call", broken)
        assert (await ask("keeper", q)).status_code == 502                   # a clean error, not a 500


async def test_the_saved_changes_download_rebuilds_the_tab(ctx):
    await _loaded(ctx)
    url = "/api/v1/cleanup/saved.json"
    async with _client() as c:
        r = await c.put("/api/v1/cleanup/inputs", headers=_H(ctx["owner"]), json={"changes": [
            {"path": ["staff", "Ann", "2", "Club"], "value": 0.7}, {"path": ["staff", "Ann", "2", "Cohort"], "value": 0.3}]})
        assert r.status_code == 200, r.text
        # the decisions move money between companies: an owner's or admin's, like changing them
        assert (await c.get(url, headers=_H(ctx["other"]))).status_code == 403
        assert (await c.get(url, headers=_H(ctx["keeper"]))).status_code == 403
        r = await c.get(url, headers=_H(ctx["owner"]))
        assert r.status_code == 200, r.text
        assert r.headers["content-disposition"].startswith('attachment; filename="books-cleanup-saved-changes-v')
        body = r.json()
        assert body["schema"] == "axcion.books_cleanup_saved/1" and body["version"] >= 1
        assert body["inputs"]["staff"]["Ann"]["2"]["Club"] == 0.7
        ov = (await c.get("/api/v1/cleanup", headers=_H(ctx["owner"]))).json()
        assert body["file"]["label"] == ov["label"] and body["version"] == ov["version"]
    # the file it names, plus these changes, gives the tab's own figures
    async with SessionLocal() as s:
        payload = (await s.execute(select(CleanupDataset.payload).where(
            CleanupDataset.id == __import__("uuid").UUID(body["file"]["id"])))).scalar_one()
        rows = (await s.execute(select(AuditLog).where(AuditLog.tenant_id == ctx["tenant"],
                                                       AuditLog.action == "cleanup.export"))).scalars().all()
    bk = eng.Books(payload, eng.merged_inputs(payload, body["inputs"]))
    assert {c: bk.net_income(c) for c in bk.companies} == ov["headline"]["net_income"]
    assert rows and rows[-1].summary.startswith("Books Cleanup: downloaded the saved changes")
