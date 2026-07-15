"""Acumyn Books — Part 2 sync unit tests (SPEC Part 8 `test_books_sync.py`).

Pure-function coverage, matching how test_pl_parser.py tests parse_pl: the P&L detail
parser and the QBO-object -> BookTxn row mapping. The pg_insert upsert path targets
Postgres and is exercised live by validate_books.py (the Part 10 Step 2 go/no-go),
not under SQLite here.

The parse_pl_lines fixture is a REAL ULRG report captured live via the Intuit API Explorer
(personal names anonymized, amounts intact — see the fixture's _fixture_note), so these
assertions pin the parser to how the actual books are shaped, tie included.
"""
import json
import pathlib
import uuid
from types import SimpleNamespace

from app.integrations import qbo
from app.services.books_sync import _txn_to_row, _cdc_split

FIX = pathlib.Path(__file__).parent / "fixtures" / "qbo_pl_detail_sample.json"
TENANT = uuid.uuid4()
BUS = uuid.uuid4()
INTEG = SimpleNamespace(business_id=BUS, realm_id="realm-1", config=None)


def _row(entity_type, obj):
    return _txn_to_row(TENANT, INTEG, entity_type, obj)


# ── parse_pl_lines ────────────────────────────────────────────────────────────

def test_parse_pl_lines_shape_and_sections():
    lines = qbo.parse_pl_lines(json.loads(FIX.read_text()))
    assert len(lines) == 76
    by_section = {}
    for ln in lines:
        by_section.setdefault(ln["section"], []).append(ln)
    assert len(by_section["income"]) == 3
    assert len(by_section["cogs"]) == 8
    assert len(by_section["expense"]) == 58
    assert len(by_section["other"]) == 7      # Other Income + Other Expenses (plural group)


def test_parse_pl_lines_parent_paths_and_order():
    lines = qbo.parse_pl_lines(json.loads(FIX.read_text()))
    by_label = {ln["label"]: ln for ln in lines}

    assert by_label["Transaction Fee"]["parent"] is None                 # direct under Income
    assert by_label["41100 Listing Income"]["parent"] == "41000 Gross Commission Income"
    # depth-5 nesting joins the whole header path
    assert by_label["Staff A Bonus"]["parent"] == \
        "61000 Compensation: 61100 Salaries: 61120 Administration: Staff A"

    # report row order preserved, income first
    assert lines[0]["label"] == "41100 Listing Income"
    assert [ln["position"] for ln in lines] == sorted(ln["position"] for ln in lines)


def test_parse_pl_lines_captures_parent_direct_postings():
    # The bug the live report exposed: a parent account that ALSO posts to itself must
    # emit its own line, or sum(lines) no longer ties. Verify a few, and the tie.
    lines = qbo.parse_pl_lines(json.loads(FIX.read_text()))
    by_label = {ln["label"]: ln for ln in lines}
    occ = by_label["63000 Occupancy"]
    assert occ["section"] == "expense" and occ["parent"] is None and occ["amount"] == 12261.33
    assert by_label["62100 General Prospecting & Marketing"]["amount"] == 268.13
    assert by_label["51600 Referral COS"]["amount"] == 14275.77       # a COGS parent-direct


def test_parse_pl_lines_maps_other_expenses_plural_group():
    # QBO names it "OtherExpenses" (plural) while OtherIncome is singular; both -> other.
    lines = qbo.parse_pl_lines(json.loads(FIX.read_text()))
    other = {ln["label"] for ln in lines if ln["section"] == "other"}
    assert "86000 Consulting" in other and "Place Income Transfer" in other


def test_parse_pl_lines_ties_to_summary_totals():
    # Part 7 invariant #2 on the REAL report: sum(section) == the summary group total.
    report = json.loads(FIX.read_text())
    lines = qbo.parse_pl_lines(report)
    summary = qbo.parse_pl(report)
    total = lambda sec: round(sum(ln["amount"] for ln in lines if ln["section"] == sec), 2)
    assert total("income") == 1094887.82 == round(float(summary["revenue"]), 2)
    assert total("cogs") == 723709.55 == round(float(summary["cogs"]), 2)
    assert total("expense") == 204654.80 == round(float(summary["opex"]), 2)


def test_parse_pl_lines_is_tolerant():
    assert qbo.parse_pl_lines({}) == []
    assert qbo.parse_pl_lines({"Rows": {"Row": [{"group": "Income"}]}}) == []   # group, no Rows
    # missing value column -> amount 0, label still captured
    weird = {"Rows": {"Row": [{"group": "Income", "Rows": {"Row": [
        {"ColData": [{"value": "Odd Line"}]}]}}]}}
    out = qbo.parse_pl_lines(weird)
    assert out == [{"section": "income", "parent": None, "label": "Odd Line",
                    "amount": 0.0, "position": 0}]


# ── _txn_to_row mapping across entity types ────────────────────────────────────

def test_purchase_categorized():
    r = _row("Purchase", {
        "Id": "P1", "SyncToken": "2", "TxnDate": "2026-07-11", "TotalAmt": 389.00,
        "EntityRef": {"value": "77", "name": "Canva Teams"},
        "AccountRef": {"value": "33", "name": "Ramp Card"},
        "PrivateNote": "Design subscription",
        "Line": [{"Amount": 389.00, "DetailType": "AccountBasedExpenseLineDetail",
                  "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "61010", "name": "Marketing - Software"}}}]})
    assert float(r["amount"]) == 389.00
    assert r["payee"] == "Canva Teams"
    assert r["account_label"] == "Marketing - Software"
    assert r["account_qbo_id"] == "61010"
    assert r["bank_account_label"] == "Ramp Card"
    assert r["came_categorized"] is True
    assert r["flags"] is None
    assert r["sync_token"] == "2"
    assert r["scan_state"] == "pending"
    assert r["qbo_id"] == "P1"


def test_purchase_uncategorized_sets_came_false():
    r = _row("Purchase", {
        "Id": "P2", "TxnDate": "2026-07-11", "TotalAmt": 50.0,
        "AccountRef": {"name": "Ramp Card"},
        "Line": [{"AccountBasedExpenseLineDetail": {"AccountRef": {"value": "9", "name": "Uncategorized Expense"}}}]})
    assert r["came_categorized"] is False
    assert r["account_label"] == "Uncategorized Expense"


def test_purchase_split_flags_multi_line():
    r = _row("Purchase", {
        "Id": "P3", "TxnDate": "2026-07-11", "TotalAmt": 200.0,
        "Line": [
            {"AccountBasedExpenseLineDetail": {"AccountRef": {"value": "1", "name": "Rent"}}},
            {"AccountBasedExpenseLineDetail": {"AccountRef": {"value": "2", "name": "Utilities"}}}]})
    assert r["flags"] == {"multi_line": True}
    assert r["account_label"] == "Rent"          # first line wins for the label


def test_deposit_uses_line_entity_and_deposit_account():
    r = _row("Deposit", {
        "Id": "D1", "SyncToken": "0", "TxnDate": "2026-07-09", "TotalAmt": 5000.0,
        "DepositToAccountRef": {"value": "10", "name": "Operating Checking"},
        "Line": [{"Amount": 5000.0, "DepositLineDetail": {
            "AccountRef": {"value": "41000", "name": "Membership Dues"},
            "Entity": {"value": "88", "name": "New Member LLC", "type": "Customer"}}}]})
    assert float(r["amount"]) == 5000.0
    assert r["payee"] == "New Member LLC"
    assert r["account_label"] == "Membership Dues"
    assert r["bank_account_label"] == "Operating Checking"
    assert r["came_categorized"] is True


def test_journal_entry_amount_is_debit_side():
    r = _row("JournalEntry", {
        "Id": "J1", "SyncToken": "1", "TxnDate": "2026-07-05",
        "Line": [
            {"Amount": 15000.0, "JournalEntryLineDetail": {
                "PostingType": "Debit", "AccountRef": {"value": "62000", "name": "Intercompany Rent"}}},
            {"Amount": 15000.0, "JournalEntryLineDetail": {
                "PostingType": "Credit", "AccountRef": {"value": "20000", "name": "Due to ULRG"}}}]})
    assert float(r["amount"]) == 15000.0          # debit side, not the 30000 gross
    assert r["account_label"] == "Intercompany Rent"
    assert r["flags"] == {"multi_line": True}
    assert r["bank_account_label"] is None


def test_transfer_uses_amount_and_from_account():
    r = _row("Transfer", {
        "Id": "T1", "TxnDate": "2026-07-07", "Amount": 2500.0,
        "FromAccountRef": {"value": "10", "name": "Operating Checking"},
        "ToAccountRef": {"value": "11", "name": "Savings"}})
    assert float(r["amount"]) == 2500.0
    assert r["bank_account_label"] == "Operating Checking"
    assert r["account_label"] is None             # bank-to-bank, no P&L category
    assert r["came_categorized"] is False


def test_bill_uses_vendor_ref():
    r = _row("Bill", {
        "Id": "B1", "SyncToken": "3", "TxnDate": "2026-07-02", "TotalAmt": 1200.0,
        "VendorRef": {"value": "90", "name": "WeWork"},
        "Line": [{"AccountBasedExpenseLineDetail": {"AccountRef": {"value": "60000", "name": "Rent"}}}]})
    assert r["payee"] == "WeWork"
    assert r["account_label"] == "Rent"
    assert r["bank_account_label"] is None         # AP, no bank side
    assert r["came_categorized"] is True


def test_missing_fields_do_not_raise():
    r = _row("Purchase", {"Id": "P9"})
    assert float(r["amount"]) == 0.0
    assert r["payee"] is None
    assert r["account_label"] is None
    assert r["came_categorized"] is False
    assert r["txn_date"] is None


# ── _cdc_split ─────────────────────────────────────────────────────────────────

def test_app_txn_url_by_type():
    assert qbo.app_txn_url("Purchase", "123").endswith("/expense?txnId=123")
    assert qbo.app_txn_url("Bill", "4").endswith("/bill?txnId=4")
    assert qbo.app_txn_url("Transfer", "9").endswith("/transfer?txnId=9")
    assert qbo.app_txn_url("JournalEntry", "5").endswith("/journal?txnId=5")
    assert qbo.app_txn_url("Deposit", None) is None        # no id -> no link
    assert qbo.app_txn_url("MysteryType", "1") is None     # unknown type -> no link


def test_cdc_split_extracts_and_drops_tombstones():
    data = {"CDCResponse": [{"QueryResponse": [
        {"Purchase": [{"Id": "1"}, {"Id": "2", "status": "Deleted"}]},
        {"Deposit": [{"Id": "3"}]},
    ]}]}
    out = _cdc_split(data, ("Purchase", "Deposit", "Bill"))
    assert [o["Id"] for o in out["Purchase"]] == ["1"]     # deleted tombstone dropped
    assert [o["Id"] for o in out["Deposit"]] == ["3"]
    assert out["Bill"] == []
