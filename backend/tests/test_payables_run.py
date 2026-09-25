"""Payables Phase 3 — runs, release and the matcher (SPEC-payables §4.8).

Every test here is a way money could leave the building wrongly: a bill swept into a run a week
early, a payment sent to banking that changed last night, a run released while a line was held,
one person approving and releasing the same batch, a hold waved through by the person who set it
up, a payment matched to the wrong bill — or a QuickBooks sync quietly erasing the record that a
human approved the thing at all.
"""
import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.config import settings
from app.db import SessionLocal
from app.models import (AuditLog, BookTxn, Business, Payable, PayableApproval, PayableEvent,
                        PaymentRun, Tenant, User, Vendor, VendorBankAccount)
from app.seed import seed
from app.security import hash_pw
from app.services import payables as pay
from app.services import payables_run as run
from app.services import payables_vendor as pv
from app.services import books_scan, books_sync
from app.services.payables_match import match_payments


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _ctx():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = (await s.execute(select(User).where(User.tenant_id == t.id))).scalars().first()
        b = (await s.execute(select(Business).where(
            Business.tenant_id == t.id, Business.key == "ulrg"))).scalar_one()
        return t.id, u, b.id


async def _user(tenant_id, email, name):
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(
            User.tenant_id == tenant_id, User.email == email))).scalar_one_or_none()
        if u is None:
            u = User(tenant_id=tenant_id, email=email, name=name,
                     password_hash=hash_pw("x"), role="admin")
            s.add(u)
            await s.commit()
        return u


async def _reset(tenant_id):
    async with SessionLocal() as s:
        for model in (PayableEvent, PayableApproval):
            for r in (await s.execute(select(model).where(
                    model.tenant_id == tenant_id))).scalars().all():
                await s.delete(r)
        await s.commit()
        for r in (await s.execute(select(BookTxn).where(
                BookTxn.tenant_id == tenant_id))).scalars().all():
            await s.delete(r)
        for r in (await s.execute(select(Payable).where(
                Payable.tenant_id == tenant_id))).scalars().all():
            await s.delete(r)
        await s.commit()
        for r in (await s.execute(select(PaymentRun).where(
                PaymentRun.tenant_id == tenant_id))).scalars().all():
            await s.delete(r)
        for r in (await s.execute(select(VendorBankAccount).where(
                VendorBankAccount.tenant_id == tenant_id))).scalars().all():
            await s.delete(r)
        for r in (await s.execute(select(Vendor).where(
                Vendor.tenant_id == tenant_id))).scalars().all():
            await s.delete(r)
        await s.commit()


async def _vendor(tenant_id, actor, name="Acme Landscaping LLC", bank_age_hours=None):
    """A vendor cleared to be paid. `bank_age_hours` back-dates the banking row, because the
    cooldown is the one hold that depends on wall-clock time."""
    async with SessionLocal() as s:
        v = await pv.create_vendor(s, tenant_id, actor, {"legal_name": name, "terms_days": 30})
        v = await pv.update_vendor(s, tenant_id, actor, v["id"],
                                   {"w9_document_id": uuid.uuid4()})
        v = await pv.add_bank_account(s, tenant_id, actor, v["id"],
                                      {"routing_last4": "1111", "account_last4": "2222",
                                       "verified": True})
        if bank_age_hours is not None:
            row = (await s.execute(select(VendorBankAccount).where(
                VendorBankAccount.vendor_id == uuid.UUID(v["id"]),
                VendorBankAccount.active.is_(True)))).scalars().first()
            row.created_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=bank_age_hours)
            await s.commit()
    return v


async def _approved(tenant_id, actor, business_id, vendor, invoice, amount, due,
                    approver=None, invoice_date=None):
    """A bill carried all the way to `approved` the way a person would: coded, submitted, signed.
    Going through the service means these tests cannot pass on a payable the lifecycle would
    have refused."""
    await _bands(tenant_id, actor)
    async with SessionLocal() as s:
        p = await pay.create_payable(s, tenant_id, actor, {
            "vendor_id": uuid.UUID(vendor["id"]), "business_id": business_id,
            "invoice_number": invoice, "amount": amount, "due_date": due,
            "invoice_date": invoice_date or (due - dt.timedelta(days=30)),
            # A coded bill: `received` cannot be submitted, which is itself a control.
            "standard_account_id": uuid.uuid4()})
        await pay.submit_for_approval(s, tenant_id, actor, uuid.UUID(p["id"]))
        await pay.decide(s, tenant_id, approver or actor, uuid.UUID(p["id"]), "approve", "ok")
        row = await s.get(Payable, uuid.UUID(p["id"]))
        assert row.status == "approved", row.status
        return row.id


async def _bands(tenant_id, actor):
    async with SessionLocal() as s:
        if not await pay.list_policies(s, tenant_id):
            await pay.replace_policies(s, tenant_id, actor, [
                {"label": "Any amount", "min_amount": 0, "max_amount": None,
                 "approvals_required": 1}])


# ── selection: the window is the control, so its edges are the test ───────────────────────

async def test_the_run_reaches_six_days_out_and_stops_there():
    """A run that sweeps too far pays bills before the cash to pay them has arrived. `+6` is in
    because a weekly run must cover the whole week; `+7` is next week's run."""
    tid, actor, biz = await _ctx()
    await _reset(tid)
    v = await _vendor(tid, actor)
    today = dt.date(2026, 9, 21)
    await _approved(tid, actor, biz, v, "IN-6", 100, today + dt.timedelta(days=6))
    await _approved(tid, actor, biz, v, "IN-7", 200, today + dt.timedelta(days=7))
    async with SessionLocal() as s:
        proposed = await run.propose_run(s, tid, biz, run_date=today)
    invoices = {l["invoice_number"] for l in proposed["lines"]}
    assert invoices == {"IN-6"}, invoices
    assert proposed["lookahead_days"] == 6


# ── the cooldown: a hold that depends on the clock ────────────────────────────────────────

async def test_banking_changed_last_night_holds_the_line_and_a_day_later_does_not():
    """Vendor impersonation is: change the banking, invoice immediately. The cooldown is the
    only control that catches it, so 23 hours and 25 hours must land on opposite sides."""
    tid, actor, biz = await _ctx()
    await _reset(tid)
    fresh = await _vendor(tid, actor, "Fresh Banking LLC", bank_age_hours=23)
    settled = await _vendor(tid, actor, "Settled Banking LLC", bank_age_hours=25)
    due = dt.date.today() + dt.timedelta(days=3)
    await _approved(tid, actor, biz, fresh, "F-1", 500, due)
    await _approved(tid, actor, biz, settled, "S-1", 600, due)
    async with SessionLocal() as s:
        lines = {l["invoice_number"]: l for l in (await run.propose_run(s, tid, biz))["lines"]}
    assert settings.PAYABLES_BANK_COOLDOWN_HOURS == 24
    assert [h["key"] for h in lines["F-1"]["holds"]] == ["bank_cooldown"]
    assert lines["F-1"]["held"] is True
    assert lines["S-1"]["holds"] == [] and lines["S-1"]["held"] is False


# ── release ───────────────────────────────────────────────────────────────────────────────

async def test_a_run_cannot_be_released_while_one_line_is_held():
    """There is no release-anyway. A held line is cleared by a second person, by name, or it is
    pushed to next week — a button that ignores the holds would make all of them decorative."""
    tid, actor, biz = await _ctx()
    await _reset(tid)
    good = await _vendor(tid, actor, "Good Vendor LLC", bank_age_hours=100)
    bad = await _vendor(tid, actor, "Fresh Bank Vendor LLC", bank_age_hours=1)
    due = dt.date.today() + dt.timedelta(days=2)
    await _approved(tid, actor, biz, good, "G-1", 100, due)
    await _approved(tid, actor, biz, bad, "B-1", 100, due)
    releaser = await _user(tid, "releaser@payables.test", "The Releaser")
    async with SessionLocal() as s:
        made = await run.create_run(s, tid, actor, biz)
        assert made["held_count"] == 1 and made["can_release"] is False
        with pytest.raises(ValueError) as e:
            await run.release_run(s, tid, releaser, uuid.UUID(made["id"]))
    assert "held" in str(e.value)

    # Push the held line to next week and the same run releases.
    async with SessionLocal() as s:
        held = [l for l in made["lines"] if l["held"]][0]
        after = await run.hold_line(s, tid, actor, uuid.UUID(made["id"]),
                                    uuid.UUID(held["payable_id"]), "banking too new")
        assert after["can_release"] is True
        out = await run.release_run(s, tid, releaser, uuid.UUID(made["id"]))
    assert out["status"] == "released"
    # The pushed bill is back to approved and attached to no run — eligible next week.
    async with SessionLocal() as s:
        back = await s.get(Payable, uuid.UUID(held["payable_id"]))
    assert back.status == "approved" and back.payment_run_id is None


async def test_the_approver_cannot_also_release_unless_the_workspace_allows_it():
    """Segregation of duties. Where one person genuinely is both — a small company — the
    setting permits it and the audit trail says so; what is not allowed is doing it silently."""
    tid, actor, biz = await _ctx()
    await _reset(tid)
    v = await _vendor(tid, actor, "Solo Vendor LLC", bank_age_hours=100)
    pid = await _approved(tid, actor, biz, v, "SD-1", 750, dt.date.today() + dt.timedelta(days=2))
    async with SessionLocal() as s:
        made = await run.create_run(s, tid, actor, biz)

    was = settings.PAYABLES_ALLOW_SELF_RELEASE
    try:
        settings.PAYABLES_ALLOW_SELF_RELEASE = False
        async with SessionLocal() as s:
            with pytest.raises(ValueError) as e:
                await run.release_run(s, tid, actor, uuid.UUID(made["id"]))
        assert "cannot also release" in str(e.value)

        settings.PAYABLES_ALLOW_SELF_RELEASE = True
        async with SessionLocal() as s:
            out = await run.release_run(s, tid, actor, uuid.UUID(made["id"]))
        assert out["status"] == "released"
        async with SessionLocal() as s:
            rows = (await s.execute(select(AuditLog).where(
                AuditLog.tenant_id == tid,
                AuditLog.action == "payables.self_released"))).scalars().all()
        assert len(rows) == 1 and rows[0].category == "Payments"
    finally:
        settings.PAYABLES_ALLOW_SELF_RELEASE = was
    assert pid is not None


# ── the override ──────────────────────────────────────────────────────────────────────────

async def test_an_override_needs_a_second_person_and_leaves_two_records():
    tid, actor, biz = await _ctx()
    await _reset(tid)
    v = await _vendor(tid, actor, "Override Vendor LLC", bank_age_hours=1)   # cooldown holds it
    await _approved(tid, actor, biz, v, "OV-1", 900, dt.date.today() + dt.timedelta(days=1))
    second = await _user(tid, "override@payables.test", "Second Pair Of Eyes")
    async with SessionLocal() as s:
        made = await run.create_run(s, tid, actor, biz)
        line = made["lines"][0]
        rid, pid = uuid.UUID(made["id"]), uuid.UUID(line["payable_id"])

        with pytest.raises(ValueError) as e:                    # the approver cannot clear it
            await run.override_line(s, tid, actor, rid, pid, "I checked the bank myself")
        assert "second pair of eyes" in str(e.value)

        with pytest.raises(ValueError) as blank:                # and a reason is mandatory
            await run.override_line(s, tid, second, rid, pid, "   ")
        assert "needs a reason" in str(blank.value)

        after = await run.override_line(s, tid, second, rid, pid,
                                        "Called the CFO on a known number")
    assert after["can_release"] is True                          # the line no longer blocks

    async with SessionLocal() as s:
        ev = (await s.execute(select(PayableEvent).where(
            PayableEvent.payable_id == pid, PayableEvent.event == "override"))).scalars().all()
        log = (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == tid,
            AuditLog.action == "payables.hold_overridden"))).scalars().all()
    assert len(ev) == 1 and ev[0].actor_user_id == second.id
    assert len(log) == 1
    # Both people, by name, or the record cannot answer who let this through.
    assert str(second.id) == log[0].detail["overridden_by"]
    assert str(actor.id) in log[0].detail["approved_by"]
    assert "known number" in log[0].detail["note"]


# ── the matcher ───────────────────────────────────────────────────────────────────────────

async def _released_bill(tid, actor, biz, vendor, invoice, amount, due):
    pid = await _approved(tid, actor, biz, vendor, invoice, amount, due)
    releaser = await _user(tid, "matcher-releaser@payables.test", "Matcher Releaser")
    async with SessionLocal() as s:
        made = await run.create_run(s, tid, actor, biz)
        await run.release_run(s, tid, releaser, uuid.UUID(made["id"]))
        p = await s.get(Payable, pid)
        assert p.status == "released"
        return pid, (await s.get(PaymentRun, uuid.UUID(made["id"]))).run_date


async def _txn(tenant_id, business_id, *, payee, amount, on, qbo_id, qbo_type="BillPayment",
               payable_id=None, scan_state="pending"):
    async with SessionLocal() as s:
        t = BookTxn(tenant_id=tenant_id, business_id=business_id, realm_id="realm-test",
                    qbo_type=qbo_type, qbo_id=qbo_id, txn_date=on, amount=Decimal(str(amount)),
                    payee=payee, account_label="Uncategorized Expense",
                    scan_state=scan_state, payable_id=payable_id)
        s.add(t)
        await s.commit()
        return t.id


async def test_the_matcher_pairs_on_amount_and_window_and_never_across_tenants():
    tid, actor, biz = await _ctx()
    await _reset(tid)
    v = await _vendor(tid, actor, "Brightpath Creative LLC", bank_age_hours=100)
    due = dt.date.today() + dt.timedelta(days=2)
    pid, run_date = await _released_bill(tid, actor, biz, v, "M-1", 1250, due)

    # Same amount, five days out: inside the window.
    inside = await _txn(tid, biz, payee="Brightpath Creative LLC", amount=1250,
                        on=run_date + dt.timedelta(days=5), qbo_id="pay-inside")
    # Same amount, six days out: outside it.
    outside = await _txn(tid, biz, payee="Brightpath Creative LLC", amount=1250,
                         on=run_date + dt.timedelta(days=6), qbo_id="pay-outside")
    # Right window, wrong amount.
    wrong = await _txn(tid, biz, payee="Brightpath Creative LLC", amount=1249,
                       on=run_date, qbo_id="pay-wrong-amount")
    # Right everything, wrong kind of transaction — a bill is settled by a BillPayment.
    kind = await _txn(tid, biz, payee="Brightpath Creative LLC", amount=1250, on=run_date,
                      qbo_id="pay-wrong-type", qbo_type="Purchase")

    async with SessionLocal() as s:
        out = await match_payments(s, tid)
    assert out["paired"] == 1, out
    async with SessionLocal() as s:
        assert (await s.get(BookTxn, inside)).payable_id == pid
        for other in (outside, wrong, kind):
            assert (await s.get(BookTxn, other)).payable_id is None
        ev = (await s.execute(select(PayableEvent).where(
            PayableEvent.payable_id == pid,
            PayableEvent.event == "payment_matched"))).scalars().all()
    assert len(ev) == 1 and ev[0].payload["qbo_id"] == "pay-inside"

    # A second pass must not re-pair or double-log: the bill is claimed.
    async with SessionLocal() as s:
        again = await match_payments(s, tid)
    assert again["paired"] == 0

    # Cross-tenant: a perfect match in another workspace is not a match.
    async with SessionLocal() as s:
        other_t = (await s.execute(select(Tenant).where(
            Tenant.slug == "payables-other"))).scalar_one_or_none()
        if other_t is None:
            other_t = Tenant(slug="payables-other", name="Other Payables")
            s.add(other_t)
            await s.flush()
            s.add(Business(tenant_id=other_t.id, key="other", name="Other Co", tag="Other"))
            await s.commit()
        other_biz = (await s.execute(select(Business).where(
            Business.tenant_id == other_t.id))).scalars().first()
        oid = other_t.id
    await _reset(tid)
    v2 = await _vendor(tid, actor, "Brightpath Creative LLC", bank_age_hours=100)
    pid2, run_date2 = await _released_bill(tid, actor, biz, v2, "M-2", 1250, due)
    stranger = await _txn(oid, other_biz.id, payee="Brightpath Creative LLC", amount=1250,
                          on=run_date2, qbo_id="pay-other-tenant")
    # A second stranger, deliberately mis-parented onto THIS tenant's business. No sync would
    # ever write that row — it exists so that the tenant scope is the only thing left standing
    # between the two workspaces. Without it this assertion passes on the business filter alone,
    # and the tenant filter it claims to test could be deleted unnoticed.
    twin = await _txn(oid, biz, payee="Brightpath Creative LLC", amount=1250,
                      on=run_date2, qbo_id="pay-other-tenant-twin")
    async with SessionLocal() as s:
        assert (await match_payments(s, tid))["paired"] == 0
        assert (await s.get(BookTxn, stranger)).payable_id is None
        assert (await s.get(BookTxn, twin)).payable_id is None
        assert (await s.get(Payable, pid2)).status == "released"


async def test_two_identical_bills_and_one_payment_pair_to_neither():
    """This is the duplicate-payment case. One payment that could settle either of two identical
    invoices is exactly what must NOT be resolved by guessing — a link recorded here would read
    as fact and hide the duplicate."""
    tid, actor, biz = await _ctx()
    await _reset(tid)
    v = await _vendor(tid, actor, "Twice Billed LLC", bank_age_hours=100)
    due = dt.date.today() + dt.timedelta(days=2)
    # Within the duplicate window on purpose: same vendor, same amount, invoices two days
    # apart. Dated 90 days apart (as this test first was) no duplicate hold fires at all, and
    # the overrides below clear nothing while still reading as though they did.
    await _approved(tid, actor, biz, v, "D-1", 400, due)
    await _approved(tid, actor, biz, v, "D-2", 400, due, invoice_date=due - dt.timedelta(days=28))
    releaser = await _user(tid, "dupe-releaser@payables.test", "Dupe Releaser")
    async with SessionLocal() as s:
        made = await run.create_run(s, tid, actor, biz)
        assert made["held_count"] == 2, "the duplicate hold did not fire — see the dates above"
        # Both lines flag as possible duplicates; a second person clears them knowingly.
        for line in made["lines"]:
            await run.override_line(s, tid, releaser, uuid.UUID(made["id"]),
                                    uuid.UUID(line["payable_id"]), "two real invoices, checked")
        released = await run.release_run(s, tid, releaser, uuid.UUID(made["id"]))
        rd = dt.date.fromisoformat(released["run_date"])
    await _txn(tid, biz, payee="Twice Billed LLC", amount=400, on=rd, qbo_id="pay-ambiguous")
    async with SessionLocal() as s:
        out = await match_payments(s, tid)
    assert out["paired"] == 0 and out["ambiguous"] == 2, out


# ── the payoff: the review queue shrinks ──────────────────────────────────────────────────

async def test_a_transaction_carrying_a_payable_skips_the_review_queue():
    """The whole point of the module. Approving a bill before paying it has to REPLACE reviewing
    the payment afterwards; if the row still lands in Friday's queue, the work was doubled."""
    tid, actor, biz = await _ctx()
    await _reset(tid)
    v = await _vendor(tid, actor, "Skip The Queue LLC", bank_age_hours=100)
    due = dt.date.today() + dt.timedelta(days=2)
    pid, run_date = await _released_bill(tid, actor, biz, v, "SQ-1", 3200, due)

    linked = await _txn(tid, biz, payee="Skip The Queue LLC", amount=3200, on=run_date,
                        qbo_id="pay-linked", payable_id=pid)
    stray = await _txn(tid, biz, payee="Nobody Approved This", amount=77, on=run_date,
                       qbo_id="pay-stray", qbo_type="Purchase")
    async with SessionLocal() as s:
        tally = await books_scan.run_scan(s, tid)
    assert tally["approved"] == 1, tally

    async with SessionLocal() as s:
        row = await s.get(BookTxn, linked)
        other = await s.get(BookTxn, stray)
    assert row.scan_state == "approved"
    assert row.suggestion["basis"] == books_scan.BASIS_PAYABLE
    # An approved row must name who approved it and what was decided — the books invariant
    # refuses one that does not, and here that name comes from the bill's approver.
    assert row.reviewed_by == actor.id and row.decision["payable_id"] == str(pid)
    assert other.scan_state != "approved"       # only the linked one skips


async def test_a_match_landing_after_the_scan_still_pulls_the_row_out_of_the_queue():
    """The sync runs, the scan parks the payment in the queue, and the matcher arrives half an
    hour later. Unless it re-stamps the row, the link is recorded and the duplicated work
    happens anyway."""
    tid, actor, biz = await _ctx()
    await _reset(tid)
    v = await _vendor(tid, actor, "Late Match LLC", bank_age_hours=100)
    due = dt.date.today() + dt.timedelta(days=2)
    pid, run_date = await _released_bill(tid, actor, biz, v, "LM-1", 880, due)
    txn = await _txn(tid, biz, payee="Late Match LLC", amount=880, on=run_date, qbo_id="pay-late")
    async with SessionLocal() as s:                       # the scan gets there first
        await books_scan.run_scan(s, tid)
    async with SessionLocal() as s:
        before = await s.get(BookTxn, txn)
        assert before.scan_state != "approved"
        out = await match_payments(s, tid)
    assert out["paired"] == 1 and out["queue_skipped"] == 1, out
    async with SessionLocal() as s:
        after = await s.get(BookTxn, txn)
    assert after.scan_state == "approved" and after.payable_id == pid


async def test_a_resync_cannot_clear_the_link_that_says_a_human_approved_this():
    """Asserted against the statement that actually runs, not against the list beside it.

    If a later change adds `payable_id` to the overwritten set, the only symptom in production
    would be already-approved payments quietly reappearing for review — with nothing pointing
    at the sync that erased the link.
    """
    stmt = books_sync.txn_upsert([{
        "id": uuid.uuid4(), "tenant_id": uuid.uuid4(), "business_id": uuid.uuid4(),
        "realm_id": "r", "qbo_type": "BillPayment", "qbo_id": "1",
        "txn_date": dt.date.today(), "amount": Decimal("1.00")}])
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    overwritten = sql.split("DO UPDATE SET")[1]
    assert "payable_id" not in overwritten
    # The absence has to be meaningful: it IS a column, and the list around it is real.
    cols = {c.name for c in BookTxn.__table__.columns}
    assert "payable_id" in cols
    assert set(books_sync._TXN_SOURCE_KEYS) <= cols
    # …and the same protection for everything else review owns.
    for owned in ("scan_state", "suggestion", "reviewed_by", "reviewed_at", "decision", "flags"):
        assert owned not in overwritten, owned
# ── the screen reads these exact names ────────────────────────────────────────────────────

async def test_the_run_payloads_carry_every_field_the_runs_screen_reads():
    """BooksPayables' Runs view renders from these keys, and its offline sample is a hand-written
    copy of them. A rename here would leave the screen working against the sample and blank
    against the server — the one failure a browser check in sample mode cannot see.

    Listed explicitly rather than derived, because a list derived from the payload would agree
    with any payload.
    """
    tid, actor, biz = await _ctx()
    await _reset(tid)
    v = await _vendor(tid, actor, "Payload Shape LLC", bank_age_hours=100)
    await _approved(tid, actor, biz, v, "PS-1", 100, dt.date.today() + dt.timedelta(days=2))

    async with SessionLocal() as s:
        proposed = await run.propose_run(s, tid, biz)
    assert {"run_date", "horizon", "lookahead_days", "lines", "held_count",
            "releasable_count", "total"} <= set(proposed)

    line_keys = {"payable_id", "vendor", "vendor_legal_name", "invoice_number", "amount",
                 "due_date", "terms", "status", "holds", "held", "overridden", "override_reason"}
    assert line_keys <= set(proposed["lines"][0])
    hold_sample = {"key", "label", "hold", "why"}

    async with SessionLocal() as s:
        made = await run.create_run(s, tid, actor, biz)
    assert {"id", "business_id", "run_date", "status", "released_by", "released_at",
            "export_ref", "lines", "held_count", "releasable_total", "can_release"} <= set(made)
    assert line_keys <= set(made["lines"][0])

    async with SessionLocal() as s:
        listed = await run.list_runs(s, tid, biz)
    assert {"id", "business_id", "business_name", "run_date", "status", "item_count",
            "total_amount", "released_by", "released_at", "export_ref"} <= set(listed[0])

    # And a hold, whose four keys the line chip and the drawer both read.
    held_v = await _vendor(tid, actor, "Fresh Bank Shape LLC", bank_age_hours=1)
    await _approved(tid, actor, biz, held_v, "PS-2", 100, dt.date.today() + dt.timedelta(days=2))
    async with SessionLocal() as s:
        again = await run.propose_run(s, tid, biz)
    holds = [h for l in again["lines"] for h in l["holds"]]
    assert holds and all(hold_sample <= set(h) for h in holds)
# ── the override is the control; everything that could route around it ────────────────────

async def test_a_hold_cannot_be_cleared_by_editing_the_bill():
    """THE control. `is_exception` is what tells line_holds a second person cleared a hold, so
    if the ordinary coding edit can set it, one person clears every hold on their own invoice
    with no second pair of eyes, no written reason and no audit row — and release_run, which
    reads the same list, lets the payment through."""
    tid, actor, biz = await _ctx()
    await _reset(tid)
    v = await _vendor(tid, actor, "Patch The Hold LLC", bank_age_hours=1)   # cooldown holds it
    pid = await _approved(tid, actor, biz, v, "PH-1", 400, dt.date.today() + dt.timedelta(days=2))
    async with SessionLocal() as s:
        made = await run.create_run(s, tid, actor, biz)
        assert made["can_release"] is False and made["held_count"] == 1

        # The coding route must simply not carry these fields.
        assert "is_exception" not in pay._CODING
        assert "exception_reason" not in pay._CODING
        await pay.update_coding(s, tid, actor, pid,
                                {"is_exception": True, "exception_reason": "I say so",
                                 "class_key": "ops"})
        row = await s.get(Payable, pid)
        assert row.is_exception is False, "a coding edit set the override flag"
        assert row.class_key == "ops"          # the rest of the edit still applies
        after = await run.get_run(s, tid, uuid.UUID(made["id"]))
    assert after["can_release"] is False and after["held_count"] == 1
    # And the API surface cannot carry it either.
    from app.routers.payables import PayablePatch
    assert "is_exception" not in PayablePatch.model_fields
    assert "exception_reason" not in PayablePatch.model_fields


async def test_an_override_clears_only_the_holds_it_was_given_for():
    """An override is permission to pay past the risks that were on the screen — not a standing
    exemption. A vendor whose banking is replaced AFTER the override is the exact impersonation
    the cooldown exists to catch, and last week's note about a duplicate must not wave it
    through."""
    tid, actor, biz = await _ctx()
    await _reset(tid)
    v = await _vendor(tid, actor, "Standing Exemption LLC", bank_age_hours=100)
    due = dt.date.today() + dt.timedelta(days=2)
    await _approved(tid, actor, biz, v, "SE-1", 700, due)
    await _approved(tid, actor, biz, v, "SE-2", 700, due, invoice_date=due - dt.timedelta(days=28))
    second = await _user(tid, "scope@payables.test", "Second Scope")
    async with SessionLocal() as s:
        made = await run.create_run(s, tid, actor, biz)
        rid = uuid.UUID(made["id"])
        held = [l for l in made["lines"] if l["held"]]
        assert held, "expected the duplicate hold"
        target = uuid.UUID(held[0]["payable_id"])
        after = await run.override_line(s, tid, second, rid, target, "different jobs, checked")
        line = [l for l in after["lines"] if l["payable_id"] == str(target)][0]
        assert line["held"] is False and line["overridden"] is True
        row = await s.get(Payable, target)
        assert row.exception_holds["keys"] == ["possible_duplicate"]
        assert row.exception_holds["run"] == str(rid)

    # Now the vendor's banking is replaced — a hold nobody has seen.
    async with SessionLocal() as s:
        bank = (await s.execute(select(VendorBankAccount).where(
            VendorBankAccount.vendor_id == uuid.UUID(v["id"]),
            VendorBankAccount.active.is_(True)))).scalars().first()
        bank.created_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
        await s.commit()
    async with SessionLocal() as s:
        now = await run.get_run(s, tid, rid)
        line = [l for l in now["lines"] if l["payable_id"] == str(target)][0]
    keys = {h["key"]: h["hold"] for h in line["holds"]}
    assert keys["possible_duplicate"] is False          # still cleared, as granted
    assert keys["bank_cooldown"] is True, "a brand-new hold was waved through by an old override"
    assert line["held"] is True
    async with SessionLocal() as s:
        with pytest.raises(ValueError):
            await run.release_run(s, tid, second, rid)


async def test_pushing_a_line_to_next_week_takes_its_override_with_it():
    tid, actor, biz = await _ctx()
    await _reset(tid)
    v = await _vendor(tid, actor, "Carry Forward LLC", bank_age_hours=1)
    pid = await _approved(tid, actor, biz, v, "CF-1", 300, dt.date.today() + dt.timedelta(days=2))
    second = await _user(tid, "carry@payables.test", "Second Carry")
    async with SessionLocal() as s:
        made = await run.create_run(s, tid, actor, biz)
        rid = uuid.UUID(made["id"])
        await run.override_line(s, tid, second, rid, pid, "spoke to the CFO")
        assert (await s.get(Payable, pid)).is_exception is True
        await run.hold_line(s, tid, actor, rid, pid, "cash is tight")
        row = await s.get(Payable, pid)
    assert row.is_exception is False and row.exception_holds is None and row.exception_reason is None


async def test_overriding_a_line_that_is_not_held_is_refused():
    """An override with nothing to override records a second person's name against a decision
    nobody made, and leaves a standing grant on the row."""
    tid, actor, biz = await _ctx()
    await _reset(tid)
    v = await _vendor(tid, actor, "Nothing To Clear LLC", bank_age_hours=100)
    pid = await _approved(tid, actor, biz, v, "NC-1", 250, dt.date.today() + dt.timedelta(days=2))
    second = await _user(tid, "nothing@payables.test", "Second Nothing")
    async with SessionLocal() as s:
        made = await run.create_run(s, tid, actor, biz)
        with pytest.raises(ValueError) as e:
            await run.override_line(s, tid, second, uuid.UUID(made["id"]), pid, "just in case")
    assert "not held" in str(e.value)


# ── the export IS the payment instruction ─────────────────────────────────────────────────

async def test_there_is_no_bank_file_until_the_run_is_released():
    """The CSV is what somebody keys at the bank. Produced before release it routes around every
    control release stands for - the second factor, the segregation-of-duties check, the refusal
    while a line is held - and the file itself looks identical to an approved batch."""
    tid, actor, biz = await _ctx()
    await _reset(tid)
    good = await _vendor(tid, actor, "Exportable LLC", bank_age_hours=100)
    bad = await _vendor(tid, actor, "Held Vendor LLC", bank_age_hours=1)
    due = dt.date.today() + dt.timedelta(days=2)
    await _approved(tid, actor, biz, good, "EX-1", 1000, due)
    await _approved(tid, actor, biz, bad, "EX-2", 2000, due)
    releaser = await _user(tid, "export@payables.test", "Export Releaser")
    async with SessionLocal() as s:
        made = await run.create_run(s, tid, actor, biz)
        rid = uuid.UUID(made["id"])
        with pytest.raises(ValueError) as e:
            await run.export_run(s, tid, rid)
    assert "released" in str(e.value)

    async with SessionLocal() as s:
        held = [l for l in made["lines"] if l["held"]][0]
        await run.hold_line(s, tid, actor, rid, uuid.UUID(held["payable_id"]), "banking too new")
        await run.release_run(s, tid, releaser, rid)
        name, csv_text = await run.export_run(s, tid, rid)
    assert name.endswith(".csv")
    assert "Exportable LLC" in csv_text
    assert "Held Vendor LLC" not in csv_text      # it never entered the released batch
