"""QuickBooks line detail: `book_txn_line`, and what it fixes.

`BookTxn` stores one amount and the FIRST category line's account. A four-line purchase
therefore charges line one's account for the whole transaction and the other three for nothing
— which is why the Books Cleanup tab reports "transactions short by $42,327" against
Advertising on the live books, and why nearly all of it is journal entries (99.7% of them are
multi-line, against 0.8% of purchases).

These rows are the fix, and they cost no extra QuickBooks call: the lines arrived with the
transaction and `books_sync` was throwing them away.

Two halves: `_txn_lines` is pure and gets the shapes, `_write_txn_lines` touches the database
and gets the replace-not-merge behaviour and the id resolution.
"""
import datetime as dt
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import BookTxn, BookTxnLine, Business, Tenant
from app.seed import seed
from app.services.books_sync import _txn_lines, _txn_pairs, _txn_to_row, _write_txn_lines

TENANT = uuid.uuid4()
INTEG = SimpleNamespace(business_id=uuid.uuid4(), realm_id="realm-1", config=None)

# A real shape: one card charge split across three accounts.
#
# The line ids are DELIBERATELY NOT 0, 1, 2. QBO keeps a line's id when its neighbour is
# deleted, so ids go non-contiguous on any transaction anybody has edited. A fixture whose ids
# happen to equal their positions cannot tell whether the code read the position or the id,
# and the first version of this file had one: the mutation that swapped `i` for `ln["Id"]`
# passed every test here.
SPLIT_PURCHASE = {
    "Id": "P100", "SyncToken": "0", "TxnDate": "2026-09-14", "TotalAmt": 1000.00,
    "EntityRef": {"value": "7", "name": "Meta Platforms"},
    "AccountRef": {"value": "10", "name": "Amex"},
    "Line": [
        {"Id": "1", "Amount": 700.00, "Description": "Forum campaign",
         "DetailType": "AccountBasedExpenseLineDetail",
         "AccountBasedExpenseLineDetail": {
             "AccountRef": {"value": "6010", "name": "Advertising"},
             "ClassRef": {"value": "3", "name": "The Forum"}}},
        {"Id": "4", "Amount": 250.00, "DetailType": "AccountBasedExpenseLineDetail",
         "AccountBasedExpenseLineDetail": {
             "AccountRef": {"value": "6020", "name": "Software"}}},
        {"Id": "9", "Amount": 50.00, "DetailType": "AccountBasedExpenseLineDetail",
         "AccountBasedExpenseLineDetail": {
             "AccountRef": {"value": "6030", "name": "Dues and Subscriptions"}}},
    ],
}


# ── _txn_lines, pure ──────────────────────────────────────────────────────────

def test_the_overstatement_this_table_removes():
    """The finding, as a test. The header charges one account for all of it; the lines do not."""
    row = _txn_to_row(TENANT, INTEG, "Purchase", SPLIT_PURCHASE)
    assert row["account_label"] == "Advertising"
    assert row["amount"] == Decimal("1000.0")          # all of it, against line one's account

    lines = _txn_lines(SPLIT_PURCHASE)
    assert [l["account_label"] for l in lines] == ["Advertising", "Software",
                                                   "Dues and Subscriptions"]
    assert [l["amount"] for l in lines] == [Decimal("700.0"), Decimal("250.0"),
                                            Decimal("50.0")]
    assert sum(l["amount"] for l in lines) == row["amount"]
    # Advertising's real share is 700, and the header said 1000. That difference, across a
    # year, is the number on the Cleanup tab.
    assert lines[0]["amount"] < row["amount"]


def test_the_position_is_the_array_index_and_not_quickbooks_own_line_id():
    """`line_num` is the key a re-sync replaces on and half of `uq_book_txn_line`. QBO's own id
    survives its neighbour's deletion, so it goes non-contiguous, is absent on some entities,
    and is not always a number. Any of those three makes it the wrong key."""
    lines = _txn_lines(SPLIT_PURCHASE)
    assert [l["line_num"] for l in lines] == [0, 1, 2]
    assert [l["qbo_line_id"] for l in lines] == ["1", "4", "9"]      # kept, never relied on

    lettered = {"Id": "P4", "Line": [
        {"Id": "L-abc", "Amount": 5.0, "DetailType": "AccountBasedExpenseLineDetail",
         "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "1", "name": "A"}}}]}
    assert _txn_lines(lettered)[0]["line_num"] == 0                  # a non-numeric id is fine
    assert _txn_lines(lettered)[0]["qbo_line_id"] == "L-abc"


def test_it_carries_each_line_s_own_detail():
    first = _txn_lines(SPLIT_PURCHASE)[0]
    assert first["line_num"] == 0
    assert first["qbo_line_id"] == "1"
    assert first["detail_type"] == "AccountBasedExpenseLineDetail"
    assert first["account_qbo_id"] == "6010"
    assert first["description"] == "Forum campaign"
    assert first["class_label"] == "The Forum"
    assert first["posting_type"] is None               # not a journal entry
    assert _txn_lines(SPLIT_PURCHASE)[1]["class_label"] is None


def test_journal_entry_keeps_both_sides_and_their_posting_type():
    je = {"Id": "J1", "TxnDate": "2026-09-30", "Line": [
        {"Amount": 15000.0, "DetailType": "JournalEntryLineDetail",
         "JournalEntryLineDetail": {"PostingType": "Debit",
                                    "AccountRef": {"value": "62000", "name": "Shared Services"}}},
        {"Amount": 15000.0, "DetailType": "JournalEntryLineDetail",
         "JournalEntryLineDetail": {"PostingType": "Credit",
                                    "AccountRef": {"value": "20000", "name": "Due to ULRG"}}}]}
    lines = _txn_lines(je)
    assert [l["posting_type"] for l in lines] == ["debit", "credit"]     # lowered, not as sent
    assert [l["account_qbo_id"] for l in lines] == ["62000", "20000"]
    # BOTH sides are stored. `_txn_to_row` keeps the debit total alone, which is right for the
    # queue and useless for asking what the credit side hit.
    assert len(lines) == 2
    assert _txn_to_row(TENANT, INTEG, "JournalEntry", je)["amount"] == Decimal("15000.0")


def test_an_unexpected_posting_type_is_no_answer_rather_than_a_truncated_one():
    """`posting_type` is String(6). Slicing would store "transf" and look like a real value;
    SQLite would accept it and so would Postgres."""
    odd = {"Id": "J2", "Line": [
        {"Amount": 1.0, "DetailType": "JournalEntryLineDetail",
         "JournalEntryLineDetail": {"PostingType": "Transfer",
                                    "AccountRef": {"value": "1", "name": "X"}}}]}
    assert _txn_lines(odd)[0]["posting_type"] is None


def test_lines_without_money_are_skipped_and_positions_stay_true():
    """A DescriptionOnly line is presentation. Dropping it must not renumber the money lines
    against their own array positions, which is what a re-sync replaces on."""
    obj = {"Id": "P2", "Line": [
        {"Amount": 10.0, "DetailType": "AccountBasedExpenseLineDetail",
         "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "1", "name": "A"}}},
        {"DetailType": "DescriptionOnly", "Description": "— for the quarter —"},
        {"Amount": 20.0, "DetailType": "AccountBasedExpenseLineDetail",
         "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "2", "name": "B"}}}]}
    lines = _txn_lines(obj)
    assert [l["line_num"] for l in lines] == [0, 2]
    assert [l["account_label"] for l in lines] == ["A", "B"]


def test_a_deposit_line_carries_its_own_payee():
    dep = {"Id": "D1", "Line": [
        {"Amount": 500.0, "DetailType": "DepositLineDetail",
         "DepositLineDetail": {"AccountRef": {"value": "41000", "name": "Membership Dues"},
                               "Entity": {"value": "88", "name": "New Member LLC",
                                          "type": "Customer"}}}]}
    assert _txn_lines(dep)[0]["payee"] == "New Member LLC"


def test_nothing_to_store_is_not_an_error():
    assert _txn_lines({"Id": "T1", "Amount": 2500.0}) == []          # a Transfer has no Line
    assert _txn_lines({}) == []
    assert _txn_lines({"Line": [{}]}) == []                          # a line with no Amount


def test_an_older_payload_without_detail_type_still_resolves_its_account():
    obj = {"Id": "P3", "Line": [
        {"Amount": 42.0,
         "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "9", "name": "Legacy"}}}]}
    line = _txn_lines(obj)[0]
    assert line["account_qbo_id"] == "9"
    assert line["detail_type"] is None


def test_every_row_is_paired_with_its_own_lines():
    """The one step with no downstream check. A row paired with another object's lines reads as
    a correctly split transaction posted to somebody else's accounts."""
    def one(i, acct):
        return {"Id": f"X{i}", "TxnDate": "2026-09-14", "TotalAmt": 10.0 * i,
                "Line": [{"Amount": 10.0 * i, "DetailType": "AccountBasedExpenseLineDetail",
                          "AccountBasedExpenseLineDetail": {
                              "AccountRef": {"value": acct, "name": f"Acct {acct}"}}}]}
    by_entity = {"Purchase": [one(1, "A"), one(2, "B")],
                 "Bill": [one(3, "C"), {"TxnDate": "2026-09-01"}],       # no Id: dropped
                 "Transfer": []}
    pairs = _txn_pairs(TENANT, INTEG, by_entity)
    assert len(pairs) == 3
    for row, lines in pairs:
        assert row["amount"] == sum(l["amount"] for l in lines)
        assert row["account_qbo_id"] == lines[0]["account_qbo_id"]
    assert [r["qbo_id"] for r, _ in pairs] == ["X1", "X2", "X3"]


# ── _write_txn_lines, against the database ────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _ctx():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        b = (await s.execute(select(Business).where(Business.tenant_id == t.id)
                             .order_by(Business.sort_order))).scalars().first()
        return t.id, b.id


@pytest.fixture(autouse=True)
async def _clean():
    tenant_id, _ = await _ctx()
    async with SessionLocal() as s:
        await s.execute(delete(BookTxnLine).where(BookTxnLine.tenant_id == tenant_id))
        await s.execute(delete(BookTxn).where(BookTxn.tenant_id == tenant_id))
        await s.commit()
    yield


async def _txn(s, tenant_id, business_id, qbo_type, qbo_id, realm="realm-w"):
    t = BookTxn(tenant_id=tenant_id, business_id=business_id, realm_id=realm,
                qbo_type=qbo_type, qbo_id=qbo_id, txn_date=dt.date(2026, 9, 1),
                amount=Decimal("1000.00"))
    s.add(t)
    await s.flush()
    return t.id


def _pair(qbo_type, qbo_id, lines):
    """What `sync_qbo_txns` hands the writer: the upsert row, and that object's lines."""
    return ({"qbo_type": qbo_type, "qbo_id": qbo_id}, lines)


async def test_it_hangs_the_lines_off_the_transaction_that_is_actually_there():
    tenant_id, business_id = await _ctx()
    integ = SimpleNamespace(business_id=business_id, realm_id="realm-w", config=None)
    async with SessionLocal() as s:
        txn_id = await _txn(s, tenant_id, business_id, "Purchase", "P100")
        n = await _write_txn_lines(s, tenant_id, integ,
                                   [_pair("Purchase", "P100", _txn_lines(SPLIT_PURCHASE))])
        await s.commit()
        assert n == 3
        rows = (await s.execute(select(BookTxnLine).where(
            BookTxnLine.tenant_id == tenant_id).order_by(BookTxnLine.line_num))).scalars().all()
        assert [r.book_txn_id for r in rows] == [txn_id] * 3
        assert sum(r.amount for r in rows) == Decimal("1000.00")


async def test_a_resync_replaces_rather_than_adds():
    """A recategorized line comes back at the same position with a different account. Merging
    would leave both, which is the overstatement this table exists to remove."""
    tenant_id, business_id = await _ctx()
    integ = SimpleNamespace(business_id=business_id, realm_id="realm-w", config=None)
    async with SessionLocal() as s:
        await _txn(s, tenant_id, business_id, "Purchase", "P100")
        await _write_txn_lines(s, tenant_id, integ,
                              [_pair("Purchase", "P100", _txn_lines(SPLIT_PURCHASE))])
        await s.commit()

        moved = {**SPLIT_PURCHASE, "Line": [
            {**SPLIT_PURCHASE["Line"][0],
             "AccountBasedExpenseLineDetail": {
                 "AccountRef": {"value": "6040", "name": "Contract Labor"}}},
            SPLIT_PURCHASE["Line"][1], SPLIT_PURCHASE["Line"][2]]}
        await _write_txn_lines(s, tenant_id, integ,
                              [_pair("Purchase", "P100", _txn_lines(moved))])
        await s.commit()

        rows = (await s.execute(select(BookTxnLine).where(
            BookTxnLine.tenant_id == tenant_id).order_by(BookTxnLine.line_num))).scalars().all()
        assert len(rows) == 3
        assert rows[0].account_label == "Contract Labor"
        assert sum(r.amount for r in rows) == Decimal("1000.00")


async def test_lines_that_went_away_go_away():
    tenant_id, business_id = await _ctx()
    integ = SimpleNamespace(business_id=business_id, realm_id="realm-w", config=None)
    async with SessionLocal() as s:
        await _txn(s, tenant_id, business_id, "Purchase", "P100")
        await _write_txn_lines(s, tenant_id, integ,
                              [_pair("Purchase", "P100", _txn_lines(SPLIT_PURCHASE))])
        await s.commit()
        assert await _write_txn_lines(s, tenant_id, integ,
                                     [_pair("Purchase", "P100", [])]) == 0
        await s.commit()
        assert (await s.execute(select(BookTxnLine).where(
            BookTxnLine.tenant_id == tenant_id))).scalars().all() == []


async def test_one_realm_can_hold_two_types_sharing_an_id():
    """The reason the writer filters on `qbo_id` and pairs the type in Python: a row-value IN is
    not portable, and a Purchase 500 and a Deposit 500 are different transactions."""
    tenant_id, business_id = await _ctx()
    integ = SimpleNamespace(business_id=business_id, realm_id="realm-w", config=None)
    async with SessionLocal() as s:
        purchase = await _txn(s, tenant_id, business_id, "Purchase", "500")
        deposit = await _txn(s, tenant_id, business_id, "Deposit", "500")
        await _write_txn_lines(s, tenant_id, integ, [
            _pair("Purchase", "500", _txn_lines(SPLIT_PURCHASE)),
            _pair("Deposit", "500", _txn_lines({"Line": [
                {"Amount": 99.0, "DetailType": "DepositLineDetail",
                 "DepositLineDetail": {"AccountRef": {"value": "41000", "name": "Dues"}}}]})),
        ])
        await s.commit()
        by_txn = {}
        for r in (await s.execute(select(BookTxnLine).where(
                BookTxnLine.tenant_id == tenant_id))).scalars().all():
            by_txn.setdefault(r.book_txn_id, []).append(r.account_label)
        assert sorted(by_txn[purchase]) == ["Advertising", "Dues and Subscriptions", "Software"]
        assert by_txn[deposit] == ["Dues"]


async def test_writing_one_transactions_lines_leaves_every_other_transactions_alone():
    """The replace must delete only THIS pass's transactions.

    Without this, dropping the `book_txn_id` filter from the delete passes every other test in
    this file: they each use one transaction, or write both in a single call where one
    tenant-wide delete fires before both inserts. In production a CDC pass carries a handful of
    changed objects, so that mutation would empty the tenant's whole line table on every sync
    and refill only what changed, silently recreating the under-reporting this table exists to
    remove. Found by mutating exactly that filter away.
    """
    tenant_id, business_id = await _ctx()
    integ = SimpleNamespace(business_id=business_id, realm_id="realm-w", config=None)
    async with SessionLocal() as s:
        untouched = await _txn(s, tenant_id, business_id, "Purchase", "P-OLD")
        await _write_txn_lines(s, tenant_id, integ,
                              [_pair("Purchase", "P-OLD", _txn_lines(SPLIT_PURCHASE))])
        await s.commit()

        await _txn(s, tenant_id, business_id, "Purchase", "P-NEW")
        await _write_txn_lines(s, tenant_id, integ, [_pair("Purchase", "P-NEW", _txn_lines(
            {"Id": "P-NEW", "Line": [
                {"Amount": 42.0, "DetailType": "AccountBasedExpenseLineDetail",
                 "AccountBasedExpenseLineDetail": {
                     "AccountRef": {"value": "6099", "name": "Other"}}}]}))])
        await s.commit()

        kept = (await s.execute(select(BookTxnLine).where(
            BookTxnLine.book_txn_id == untouched))).scalars().all()
    assert len(kept) == 3, "the earlier transaction's lines were deleted by an unrelated pass"
    assert sum(r.amount for r in kept) == Decimal("1000.00")


async def test_another_realm_s_transactions_are_not_touched():
    tenant_id, business_id = await _ctx()
    integ = SimpleNamespace(business_id=business_id, realm_id="realm-w", config=None)
    async with SessionLocal() as s:
        mine = await _txn(s, tenant_id, business_id, "Purchase", "P100")
        await _txn(s, tenant_id, business_id, "Purchase", "P100", realm="realm-other")
        n = await _write_txn_lines(s, tenant_id, integ,
                                   [_pair("Purchase", "P100", _txn_lines(SPLIT_PURCHASE))])
        await s.commit()
        assert n == 3
        rows = (await s.execute(select(BookTxnLine).where(
            BookTxnLine.tenant_id == tenant_id))).scalars().all()
        assert {r.book_txn_id for r in rows} == {mine}
