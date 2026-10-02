"""The Escalations list holds everything waiting on the CFO, not just the intercompany half.

WHAT WENT WRONG. Two different things were called "escalated": a BookTxn.scan_state, set by the
Queue's "Escalate to Connor" button, and an ICLink.status, set by the scanner. The Escalations
tab was built from the links alone, so every transaction a person escalated by hand went into a
state that nothing on any screen displayed. On the live books that was 33 transactions and about
$205,000, the oldest sitting there since March, while the tab said "No escalations —
intercompany is clean."

The two tiles disagreed and neither was lying: Books home counted transactions (72) and the
Queue counted links (39), both labelled "escalated".
"""
import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Business, BookTxn, ICLink, Tenant, User
from app.seed import seed
from app.services import books

TODAY = dt.date(2026, 6, 30)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


@pytest.fixture(autouse=True)
async def _clean():
    """These assert on WHOLE lists, so seeded escalations would make every count ambiguous."""
    async with SessionLocal() as s:
        await s.execute(delete(ICLink))
        await s.execute(delete(BookTxn))
        await s.commit()
    yield


async def _ctx():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        biz = {b.key: b.id for b in (await s.execute(
            select(Business).where(Business.tenant_id == t.id))).scalars().all()}
        u = (await s.execute(select(User).where(User.tenant_id == t.id))).scalars().first()
        return t.id, biz, u


async def _txn(tid, bid, *, amount, date=TODAY, payee=None, state="escalated",
               decision=None, reviewed_by=None, flags=None, account=None, suggestion=None):
    async with SessionLocal() as s:
        t = BookTxn(tenant_id=tid, business_id=bid, realm_id="r1", qbo_type="Purchase",
                    qbo_id=f"q-{uuid.uuid4().hex[:10]}", txn_date=date,
                    amount=Decimal(str(amount)), payee=payee, account_label=account,
                    scan_state=state, decision=decision, reviewed_by=reviewed_by,
                    flags=flags, suggestion=suggestion,
                    reviewed_at=dt.datetime.now(dt.timezone.utc) if reviewed_by else None)
        s.add(t)
        await s.commit()
        return t.id


async def _link(tid, *, frm, to, from_txn=None, to_txn=None, status="escalated", amount=100):
    async with SessionLocal() as s:
        l = ICLink(tenant_id=tid, from_business_id=frm, to_business_id=to,
                   from_txn_id=from_txn, to_txn_id=to_txn, amount=Decimal(str(amount)),
                   occurred_on=TODAY, status=status)
        s.add(l)
        await s.commit()
        return l.id


async def _queue(tid):
    async with SessionLocal() as s:
        return await books.build_books_queue(s, tid, period="ytd", state="all")


# ══ the bug ═══════════════════════════════════════════════════════════════════════════════

async def test_a_hand_escalated_transaction_is_on_the_list():
    """The whole report in one test. Before the fix this list was empty."""
    tid, biz, user = await _ctx()
    await _txn(tid, biz["ulrg"], amount=18592.76, payee="Zions Bank",
               decision={"action": "escalate", "note": None}, reviewed_by=user.id)
    q = await _queue(tid)
    assert len(q["escalations"]) == 1, q["escalations"]
    row = q["escalations"][0]
    assert row["kind"] == "txn"
    assert row["label"] == "Zions Bank"
    assert row["amount"] == 18592.76
    assert user.name in row["reason"] or user.email in row["reason"], row["reason"]
    assert row["escalated_by"]
    assert row["txns"] and row["txns"][0]["payee"] == "Zions Bank"


async def test_the_count_stops_disagreeing_with_itself():
    """Books home counts transactions, the Queue counted links, both said "escalated". The
    Queue's number now covers the same ground, and breaks out which is which."""
    tid, biz, user = await _ctx()
    await _txn(tid, biz["ulrg"], amount=500, payee="By hand",
               decision={"action": "escalate"}, reviewed_by=user.id)
    await _link(tid, frm=biz["ulrg"], to=biz["sympli"], status="escalated")
    await _link(tid, frm=biz["ulrg"], to=biz["ulrg"], status="unmatched")
    q = await _queue(tid)
    assert q["stats"]["escalated"] == 3, q["stats"]
    assert q["stats"]["escalated_ic"] == 2
    assert q["stats"]["escalated_txn"] == 1
    assert len(q["escalations"]) == 3


async def test_both_kinds_are_tellable_apart():
    tid, biz, user = await _ctx()
    await _txn(tid, biz["ulrg"], amount=500, payee="By hand",
               decision={"action": "escalate"}, reviewed_by=user.id)
    await _link(tid, frm=biz["ulrg"], to=biz["sympli"], status="escalated")
    q = await _queue(tid)
    assert {e["kind"] for e in q["escalations"]} == {"txn", "ic"}
    # and the shape is the same either way, so one row component can render both
    for e in q["escalations"]:
        assert {"id", "kind", "date", "amount", "label", "reason", "txns"} <= set(e)


# ══ the trap that would make this silently return nothing ════════════════════════════════

async def test_a_one_sided_link_does_not_empty_the_whole_list():
    """`to_txn_id` is NULL on every one-sided link, and SQL's NOT IN yields NO ROWS when its
    subquery contains a single NULL. Without the IS NOT NULL filters the list comes back empty
    the moment one unmatched link exists — which, on the live books, is always.

    This is the test that would have caught a "working" fix that shipped an empty tab.
    """
    tid, biz, user = await _ctx()
    by_hand = await _txn(tid, biz["ulrg"], amount=500, payee="By hand",
                         decision={"action": "escalate"}, reviewed_by=user.id)
    orphan_side = await _txn(tid, biz["sympli"], amount=900, payee="One sided")
    await _link(tid, frm=biz["sympli"], to=biz["sympli"], from_txn=orphan_side,
                to_txn=None, status="unmatched")          # <- the NULL
    q = await _queue(tid)
    kinds = [e["kind"] for e in q["escalations"]]
    assert "txn" in kinds, "a NULL to_txn_id swallowed the transaction-level escalations"
    assert str(by_hand) in [e["id"] for e in q["escalations"]]


async def test_a_transaction_its_link_already_speaks_for_is_not_listed_twice():
    """The scanner escalates BOTH SIDES of a link as well as creating the link, so without the
    exclusion every intercompany pair would appear three times: once as the link, once per side."""
    tid, biz, _user = await _ctx()
    a = await _txn(tid, biz["ulrg"], amount=5000, payee="Transfer out")
    b = await _txn(tid, biz["sympli"], amount=5000, payee="Transfer in")
    await _link(tid, frm=biz["ulrg"], to=biz["sympli"], from_txn=a, to_txn=b, status="escalated")
    q = await _queue(tid)
    assert len(q["escalations"]) == 1, [e["kind"] for e in q["escalations"]]
    assert q["escalations"][0]["kind"] == "ic"
    assert q["stats"]["escalated_txn"] == 0


# ══ the ones nobody escalated ═════════════════════════════════════════════════════════════

async def test_an_orphaned_intercompany_escalation_is_shown_and_says_why():
    """Nine of these on the live books, including a $50,000 transfer from April: flagged
    intercompany by the scan, which always writes a link too — and the link is gone. Hiding
    them because their paperwork went missing is how that transfer sat untouched for months."""
    tid, biz, _user = await _ctx()
    await _txn(tid, biz["ulrg"], amount=50000, payee="SB Coaching",
               flags={"intercompany": True})             # no decision, no reviewer, no link
    q = await _queue(tid)
    assert len(q["escalations"]) == 1
    row = q["escalations"][0]
    assert row["kind"] == "txn"
    assert "link behind it is gone" in row["reason"], row["reason"]
    assert row["escalated_by"] is None


# ══ ordering and shape ════════════════════════════════════════════════════════════════════

async def test_the_list_is_oldest_first_across_both_kinds():
    """What makes one of these urgent is its date, not which kind it is, so they interleave."""
    tid, biz, user = await _ctx()
    await _txn(tid, biz["ulrg"], amount=100, payee="Newer", date=dt.date(2026, 6, 20),
               decision={"action": "escalate"}, reviewed_by=user.id)
    await _txn(tid, biz["ulrg"], amount=100, payee="Older", date=dt.date(2026, 3, 4),
               decision={"action": "escalate"}, reviewed_by=user.id)
    async with SessionLocal() as s:
        l = ICLink(tenant_id=tid, from_business_id=biz["ulrg"], to_business_id=biz["sympli"],
                   amount=Decimal("1"), occurred_on=dt.date(2026, 4, 15), status="escalated")
        s.add(l)
        await s.commit()
    q = await _queue(tid)
    # NOT `dates == sorted(dates)`. _fmt_date renders "Mar 4", so asserting the rendered strings
    # are in order is a tautology when the code sorted on those same strings — it passed happily
    # while the April link sat above the March transaction (Apr < Jun < Mar, alphabetically).
    # Assert the chronology that was meant instead.
    assert [e["kind"] for e in q["escalations"]] == ["txn", "ic", "txn"], \
        [(e["kind"], e["date"]) for e in q["escalations"]]
    assert [e["date"] for e in q["escalations"]] == ["Mar 4", "Apr 15", "Jun 20"]


async def test_the_suggestion_rides_along_so_the_cfo_can_take_it_in_one_click():
    tid, biz, user = await _ctx()
    await _txn(tid, biz["ulrg"], amount=525.04, payee="UserWay",
               account="Uncategorized Expense",
               suggestion={"category": "Subscription Software", "confidence": 0.91},
               decision={"action": "escalate"}, reviewed_by=user.id)
    q = await _queue(tid)
    row = q["escalations"][0]
    assert row["suggest"] == "Subscription Software"
    assert row["current_category"] == "Uncategorized Expense"


async def test_a_note_left_when_escalating_is_what_the_reason_says():
    tid, biz, user = await _ctx()
    await _txn(tid, biz["ulrg"], amount=75, payee="Aldo",
               decision={"action": "escalate", "note": "Is this a client dinner?"},
               reviewed_by=user.id)
    q = await _queue(tid)
    assert q["escalations"][0]["reason"] == "Is this a client dinner?"


# ══ the stage chip named after them ═══════════════════════════════════════════════════════

async def test_the_escalated_stage_shows_the_rows_it_counts():
    """The second half of the same bug, and the one that made the transaction look deleted.

    Escalating stamps reviewed_at, and the row query hid anything carrying that stamp from every
    stage but Approved — so the Escalated chip opened an empty list while the Books home funnel
    said 72. The row never came back on reload either, which is why "it removes it from the list"
    read as "it is gone".

    The product already carved Approved out of that filter for exactly this reason; escalating
    belongs in the carve-out too, because it is the one decision that means NOT finished.
    """
    tid, biz, user = await _ctx()
    await _txn(tid, biz["ulrg"], amount=7606.85, payee="Zions Bank",
               decision={"action": "escalate"}, reviewed_by=user.id)
    async with SessionLocal() as s:
        q = await books.build_books_queue(s, tid, period="ytd", state="escalated")
    assert q["stages"]["escalated"] == 1, q["stages"]
    assert [r["vendor"] for r in q["rows"]] == ["Zions Bank"], q["rows"]
    assert q["rows"][0]["signed_off"] is True, "it is stamped — that was never the question"


async def test_the_chip_and_its_list_cannot_disagree():
    """A property, not a number: for every stage, what the chip counts is what the list holds.
    The two were computed by separate code paths that had drifted apart once already."""
    tid, biz, user = await _ctx()
    await _txn(tid, biz["ulrg"], amount=100, payee="Escalated",
               decision={"action": "escalate"}, reviewed_by=user.id)
    await _txn(tid, biz["ulrg"], amount=100, payee="Approved", state="approved",
               reviewed_by=user.id)
    await _txn(tid, biz["ulrg"], amount=100, payee="Waiting", state="needs_approval")
    await _txn(tid, biz["ulrg"], amount=100, payee="Clear", state="cleared")
    async with SessionLocal() as s:
        for stage in ("escalated", "approved", "needs_approval", "cleared"):
            q = await books.build_books_queue(s, tid, period="ytd", state=stage)
            assert q["stages"][stage] == len(q["rows"]), \
                f"{stage}: chip says {q['stages'][stage]}, list holds {len(q['rows'])}"


async def test_nothing_escalated_means_an_empty_list_not_an_error():
    tid, biz, _user = await _ctx()
    await _txn(tid, biz["ulrg"], amount=10, payee="Fine", state="cleared")
    q = await _queue(tid)
    assert q["escalations"] == []
    assert q["stats"]["escalated"] == 0
