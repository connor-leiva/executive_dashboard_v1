"""Payables · the matcher (SPEC-payables §4.7).

Pairs a synced `BookTxn` of type `BillPayment` to the released payable it settled, on
`(vendor, amount, ±5 days)`. Precedent: `books_scan._find_ic_match`.

Two things about this file are deliberate.

**It only pairs when the relationship is one-to-one in both directions.** A payable with two
candidate payments, or a payment two payables could explain, is left alone. That case is two
identical bills from the same vendor for the same amount in the same week — exactly the
duplicate-payment situation the module exists to catch — and guessing which one the money
settled would launder the ambiguity into a record that reads as certain. The pass reports it
instead.

**It does not change a payable's status.** `transition` stays the only writer of a status
change, and reconciliation stays a human act, because the bank is the only thing that knows
the money actually left. What this writes is the link, plus the timeline row saying how the
link was made.

The link is the point. A transaction carrying a `payable_id` skips the review queue, so the
pass converts work Connor would otherwise redo on Friday into a row he has already signed.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import BookTxn, ICLink, Payable, PayableEvent, PaymentRun, Vendor
from .audit import audit
from .books_scan import skip_approved_payable
from .payables_vendor import match_vendor

MATCH_WINDOW_DAYS = 5
# Only BillPayment. A Purchase or a Check that happens to sit near a bill for the same amount is
# how a payable gets marked settled by an unrelated payment; QBO records the settlement of a
# bill as a BillPayment, and anything else is a guess dressed up as a match.
PAYMENT_TYPE = "BillPayment"


def _anchor(payable: Payable, run: PaymentRun | None) -> dt.date | None:
    """The date the window centres on: when the run said to pay, not when the bill was due.

    Terms can put a due date weeks from the payment — a window around it would either miss the
    payment entirely or stretch wide enough to catch the next month's.
    """
    if run is not None and run.run_date is not None:
        return run.run_date
    return payable.due_date


async def _ic_txn_ids(s: AsyncSession, tenant_id) -> set:
    """Transactions already caught up in an intercompany link. Flipping one of those to
    approved would leave its ICLink open forever, and an open link blocks the close — so the
    matcher records the pairing and leaves the review state to whoever resolves the link."""
    rows = (await s.execute(select(ICLink.from_txn_id, ICLink.to_txn_id).where(
        ICLink.tenant_id == tenant_id))).all()
    return {i for pair in rows for i in pair if i is not None}


async def match_payments(s: AsyncSession, tenant_id) -> dict:
    """One pass for one tenant. Returns counts; commits only if it paired something."""
    open_payables = list((await s.execute(select(Payable).where(
        Payable.tenant_id == tenant_id, Payable.status == "released"))).scalars())
    if not open_payables:
        return {"open": 0, "paired": 0, "ambiguous": 0, "queue_skipped": 0}

    claimed = {i for (i,) in (await s.execute(select(BookTxn.payable_id).where(
        BookTxn.tenant_id == tenant_id, BookTxn.payable_id.is_not(None)))).all()}
    open_payables = [p for p in open_payables if p.id not in claimed]
    if not open_payables:
        return {"open": 0, "paired": 0, "ambiguous": 0, "queue_skipped": 0}

    runs = {r.id: r for r in (await s.execute(select(PaymentRun).where(
        PaymentRun.tenant_id == tenant_id))).scalars()}
    vendors = list((await s.execute(select(Vendor).where(
        Vendor.tenant_id == tenant_id))).scalars())

    # Anchor every open bill FIRST, so the transaction query can be bounded by the dates those
    # bills actually occupy. Unbounded, this reads every BillPayment the company has ever made —
    # and since payments that predate this module will never acquire a payable_id, it would
    # re-fuzzy-match that whole history every thirty minutes, forever, to find nothing.
    anchors = {}
    for p in open_payables:
        a = _anchor(p, runs.get(p.payment_run_id))
        if a is not None:
            anchors[p.id] = a
    if not anchors:
        return {"open": len(open_payables), "paired": 0, "ambiguous": 0, "queue_skipped": 0}
    window = dt.timedelta(days=MATCH_WINDOW_DAYS)
    lo, hi = min(anchors.values()) - window, max(anchors.values()) + window

    # EVERY query is tenant-scoped, including this one. A BillPayment in another workspace for
    # the same round number is otherwise a perfect match on amount and date.
    free_txns = list((await s.execute(select(BookTxn).where(
        BookTxn.tenant_id == tenant_id, BookTxn.qbo_type == PAYMENT_TYPE,
        BookTxn.payable_id.is_(None),
        BookTxn.txn_date >= lo, BookTxn.txn_date <= hi))).scalars())

    # Resolve each payee ONCE against the whole vendor list, not against the one vendor being
    # tested. Asked "is this Acme?", a fuzzy matcher says yes to the nearest thing it is shown;
    # asked "who is this?", it can answer that two vendors are too close to call.
    payee_of = {}
    for t in free_txns:
        r = match_vendor(t.payee, vendors)
        payee_of[t.id] = None if r["ambiguous"] else r["vendor_id"]

    by_payable, by_txn = {}, {}
    for p in open_payables:
        anchor = anchors.get(p.id)
        if anchor is None:
            continue
        first, last = anchor - window, anchor + window
        for t in free_txns:
            if t.amount != p.amount or not (first <= t.txn_date <= last):
                continue
            if p.business_id is not None and t.business_id != p.business_id:
                continue                            # runs are per entity; so are the realms
            if payee_of.get(t.id) != str(p.vendor_id):
                continue
            by_payable.setdefault(p.id, []).append(t)
            by_txn.setdefault(t.id, []).append(p)

    ic = await _ic_txn_ids(s, tenant_id)
    paired = ambiguous = skipped = 0
    for p in open_payables:
        cands = by_payable.get(p.id, [])
        if len(cands) != 1:
            if len(cands) > 1:
                ambiguous += 1
            continue
        txn = cands[0]
        if len(by_txn.get(txn.id, [])) != 1:        # that payment could explain another bill too
            ambiguous += 1
            continue
        txn.payable_id = p.id
        paired += 1
        s.add(PayableEvent(
            tenant_id=tenant_id, payable_id=p.id, event="payment_matched", actor_user_id=None,
            payload={"txn_id": str(txn.id), "qbo_type": txn.qbo_type, "qbo_id": txn.qbo_id,
                     "txn_date": txn.txn_date.isoformat(), "amount": str(txn.amount),
                     "payee": txn.payee, "matched_by": "Axcion"}))
        # The scan has usually already run on this row and parked it in the queue. Re-stamp it
        # now, or the approval upstream buys nothing and it still shows up on Friday. Rows a
        # human has already decided, and rows tangled in an intercompany link, are left as they
        # are — this removes duplicated work, it does not overwrite somebody's judgement.
        left_queue = (txn.reviewed_at is None and txn.id not in ic
                      and txn.scan_state not in ("approved", "posted"))
        if left_queue:
            await skip_approved_payable(s, tenant_id, txn)
            skipped += 1
        audit(s, tenant_id, None, "payables.payment_matched",
              target_type="payable", target_id=p.id, category="Payments",
              summary=f"Payment matched to invoice {p.invoice_number}",
              detail={"txn_id": str(txn.id), "amount": str(p.amount),
                      "txn_date": txn.txn_date.isoformat(), "skipped_review": left_queue},
              # No person did this. `actor_type` is not free text: audit_log carries a Postgres
              # CHECK enumerating user | system | integration, so "system" is the established
              # word for it — and defaulting to "user" would file a machine's pairing in the
              # financial audit trail as somebody's decision.
              actor_label="Axcion", actor_type="system")

    if paired:
        await s.commit()
    return {"open": len(open_payables), "paired": paired, "ambiguous": ambiguous,
            "queue_skipped": skipped}
