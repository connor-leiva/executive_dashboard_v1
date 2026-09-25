"""Payables · payment runs (SPEC-payables §4).

RELEASE DOES NOT MOVE MONEY. It closes the batch, records who released it, and produces a file
a person carries to Zions Treasury Internet Banking. Zions publishes no developer API, and
writing this module as though it originates ACH would invite somebody to build against a thing
that does not exist.

Three controls live here:

Selection pays on the last run on or before the due date — never late, and never a month early.

A held line refuses release for the whole run. The only exits are per line: push it to the next
run, or have a second person override it. There is no "release anyway" on the button, because a
checkbox that clears every hold at once is the same as having no holds.

Segregation of duties asserts the releaser did not approve what they are releasing. Where a team
is too small for that, it is allowed by configuration — and then every self-release is audited
by name, because the control has to leave a trace even when it cannot be enforced.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import (Business, Payable, PayableApproval, PayableEvent, PaymentRun,
                      User, Vendor, VendorBankAccount)
from .audit import audit
from .payables import transition
from .payables_vendor import derive_status

# Pay on the last run on or before the due date. Six days ahead of a weekly run means nothing
# is ever late and nothing is paid a week early — effective terms of 24-30 days on Net 30.
# A policy decision, so it is named rather than sitting as a 6 in a query.
RUN_LOOKAHEAD_DAYS = 6

# A vendor invoicing the same amount twice within this window under two invoice numbers is the
# shape of an accidental double entry. The unique constraint cannot see it: the numbers differ.
_DUPLICATE_WINDOW_DAYS = 7


def _cooldown_cutoff(now: dt.datetime | None = None) -> dt.datetime:
    now = now or dt.datetime.now(dt.timezone.utc)
    return now - dt.timedelta(hours=settings.PAYABLES_BANK_COOLDOWN_HOURS)


def line_holds(p: Payable, vendor: Vendor | None, bank: VendorBankAccount | None,
               siblings: list[Payable], now: dt.datetime | None = None) -> list[dict]:
    """Why this line cannot be paid. Pure, so the rule is testable without a database and so the
    Release button and the line chip read the very same list."""
    out: list[dict] = []
    status = derive_status(vendor, bank) if vendor else None
    if not vendor or not vendor.w9_document_id:
        out.append({"key": "no_w9", "label": "No W-9", "hold": True,
                    "why": "A W-9 is required before a first payment."})
    if status != "active":
        out.append({"key": "vendor_not_active", "label": "Vendor not verified", "hold": True,
                    "why": "Banking has not been verified by callback."})
    if bank is not None and bank.created_at is not None:
        created = bank.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=dt.timezone.utc)
        if created > _cooldown_cutoff(now):
            # The window IS the control. Impersonation works by changing the account and
            # invoicing at once; a payment that waits a day is one somebody could notice.
            out.append({"key": "bank_cooldown",
                        "label": f"Bank changed · {settings.PAYABLES_BANK_COOLDOWN_HOURS}h hold",
                        "hold": True,
                        "why": "This vendor's banking changed too recently to pay against."})
    for other in siblings:
        if other.id == p.id or other.status == "rejected":
            continue
        if other.vendor_id != p.vendor_id or Decimal(str(other.amount)) != Decimal(str(p.amount)):
            continue
        if p.invoice_date and other.invoice_date and \
                abs((p.invoice_date - other.invoice_date).days) <= _DUPLICATE_WINDOW_DAYS:
            out.append({"key": "possible_duplicate", "label": "Possible duplicate", "hold": True,
                        "why": f"Invoice {other.invoice_number} is the same amount from the "
                               f"same vendor within {_DUPLICATE_WINDOW_DAYS} days."})
            break
    # A second person cleared this line by name (§4.3). The holds stay VISIBLE — a reader must
    # still see what was waved through — but the ones that were cleared stop blocking. This
    # lives here and not in the release check, because a Release button reading a different
    # rule from the release itself is a button that lies in one direction or the other.
    #
    # ONLY the recorded keys, and ONLY on the run the override was granted for. An override is
    # permission to pay past the risks that were on the screen at the time; a hold raised
    # afterwards — banking replaced last night, a duplicate that has since appeared — has never
    # been seen by anybody and must still stop the payment.
    grant = p.exception_holds or {}
    cleared = set(grant.get("keys") or ())
    same_run = grant.get("run") in (None, str(p.payment_run_id))
    if out and cleared and same_run:
        out = [{**h, "hold": False, "overridden": True} if h["key"] in cleared else h
               for h in out]
        out.append({"key": "overridden", "label": "Hold overridden", "hold": False,
                    "why": p.exception_reason or "Cleared by a second approver."})
    return out


async def _context(s: AsyncSession, tenant_id):
    vendors = {v.id: v for v in (await s.execute(
        select(Vendor).where(Vendor.tenant_id == tenant_id))).scalars()}
    banks: dict = {}
    for b in (await s.execute(select(VendorBankAccount).where(
            VendorBankAccount.tenant_id == tenant_id, VendorBankAccount.active.is_(True))
            .order_by(VendorBankAccount.created_at))).scalars():
        banks[b.vendor_id] = b
    allp = list((await s.execute(select(Payable).where(
        Payable.tenant_id == tenant_id))).scalars())
    return vendors, banks, allp


def _line(p: Payable, vendor: Vendor | None, holds: list[dict]) -> dict:
    return {"payable_id": str(p.id), "vendor": vendor.display_name if vendor else None,
            "vendor_legal_name": vendor.legal_name if vendor else None,
            "invoice_number": p.invoice_number, "amount": float(p.amount),
            "due_date": p.due_date.isoformat() if p.due_date else None,
            "terms": f"Net {vendor.terms_days}" if vendor else None,
            "status": p.status, "holds": holds, "overridden": bool(p.is_exception),
            "override_reason": p.exception_reason,
            "held": any(h["hold"] for h in holds)}


async def propose_run(s: AsyncSession, tenant_id, business_id, run_date=None) -> dict:
    """The next run, computed live. Never stored until somebody creates it — a proposal that
    went stale in a table would be read as fact."""
    run_date = run_date or dt.date.today()
    horizon = run_date + dt.timedelta(days=RUN_LOOKAHEAD_DAYS)
    vendors, banks, allp = await _context(s, tenant_id)
    eligible = [p for p in allp
                if p.status == "approved" and p.business_id == business_id
                and p.due_date is not None and p.due_date <= horizon]
    lines = []
    for p in eligible:
        v = vendors.get(p.vendor_id)
        lines.append(_line(p, v, line_holds(p, v, banks.get(p.vendor_id), allp)))
    payable_total = sum(l["amount"] for l in lines if not l["held"])
    return {"run_date": run_date.isoformat(), "horizon": horizon.isoformat(),
            "business_id": str(business_id), "lines": lines,
            "held_count": sum(1 for l in lines if l["held"]),
            "releasable_count": sum(1 for l in lines if not l["held"]),
            "total": round(payable_total, 2), "lookahead_days": RUN_LOOKAHEAD_DAYS}


async def create_run(s: AsyncSession, tenant_id, actor, business_id, run_date=None,
                     cutoff_at=None) -> dict:
    run_date = run_date or dt.date.today()
    horizon = run_date + dt.timedelta(days=RUN_LOOKAHEAD_DAYS)
    eligible = list((await s.execute(select(Payable).where(
        Payable.tenant_id == tenant_id, Payable.business_id == business_id,
        Payable.status == "approved", Payable.due_date.is_not(None),
        Payable.due_date <= horizon))).scalars())
    if not eligible:
        raise ValueError("nothing is approved and due inside this run's window")
    run = PaymentRun(tenant_id=tenant_id, business_id=business_id, run_date=run_date,
                     cutoff_at=cutoff_at, status="draft",
                     total_amount=Decimal(str(sum(float(p.amount) for p in eligible))),
                     item_count=len(eligible))
    s.add(run)
    await s.flush()
    for p in eligible:
        p.payment_run_id = run.id
        # Defence in depth: an override belongs to the run it was granted on, and hold_line
        # already clears it on the way out. Clearing it on the way IN as well means no path
        # into a run can carry somebody's earlier decision onto this week's holds.
        p.is_exception, p.exception_reason, p.exception_holds = False, None, None
        transition(s, p, "scheduled", actor, {"run": str(run.id)})
    audit(s, tenant_id, getattr(actor, "id", None), "payables.run_created",
          target_type="payment_run", target_id=run.id, category="Payments",
          summary=f"Run {run_date} created with {len(eligible)} lines",
          detail={"items": len(eligible), "total": str(run.total_amount)})
    await s.commit()
    return await get_run(s, tenant_id, run.id)


async def list_runs(s: AsyncSession, tenant_id, business_id=None) -> list[dict]:
    conds = [PaymentRun.tenant_id == tenant_id]
    if business_id is not None:
        conds.append(PaymentRun.business_id == business_id)
    runs = (await s.execute(select(PaymentRun).where(*conds)
                            .order_by(PaymentRun.run_date.desc()))).scalars().all()
    biz = {b.id: b.name for b in (await s.execute(
        select(Business).where(Business.tenant_id == tenant_id))).scalars()}
    users = {u.id: (u.name or u.email) for u in (await s.execute(
        select(User).where(User.tenant_id == tenant_id))).scalars()}
    return [{"id": str(r.id), "business_id": str(r.business_id),
             "business_name": biz.get(r.business_id), "run_date": r.run_date.isoformat(),
             "status": r.status, "item_count": r.item_count,
             "total_amount": float(r.total_amount),
             "released_by": users.get(r.released_by),
             "released_at": r.released_at.isoformat() if r.released_at else None,
             "export_ref": r.export_ref} for r in runs]


async def get_run(s: AsyncSession, tenant_id, run_id) -> dict | None:
    run = (await s.execute(select(PaymentRun).where(
        PaymentRun.tenant_id == tenant_id, PaymentRun.id == run_id))).scalar_one_or_none()
    if run is None:
        return None
    vendors, banks, allp = await _context(s, tenant_id)
    mine = [p for p in allp if p.payment_run_id == run.id]
    lines = []
    for p in mine:
        v = vendors.get(p.vendor_id)
        lines.append(_line(p, v, line_holds(p, v, banks.get(p.vendor_id), allp)))
    users = {u.id: (u.name or u.email) for u in (await s.execute(
        select(User).where(User.tenant_id == tenant_id))).scalars()}
    held = [l for l in lines if l["held"]]
    return {"id": str(run.id), "business_id": str(run.business_id),
            "run_date": run.run_date.isoformat(), "status": run.status,
            "cutoff_at": run.cutoff_at.isoformat() if run.cutoff_at else None,
            "released_by": users.get(run.released_by),
            "released_at": run.released_at.isoformat() if run.released_at else None,
            "export_ref": run.export_ref, "lines": lines,
            "held_count": len(held),
            "releasable_total": round(sum(l["amount"] for l in lines if not l["held"]), 2),
            # The single boolean the Release button reads. Three halves now: the right
            # status, not one line held, and something actually left to pay — release_run
            # refuses an empty run, and a button that offers what the server will refuse is
            # worse than a disabled one.
            "can_release": run.status == "draft" and not held and bool(lines)}


# ── the per-line exits ────────────────────────────────────────────────────────────────────

async def hold_line(s: AsyncSession, tenant_id, actor, run_id, payable_id, reason: str) -> dict | None:
    """Push a line to the next run. It leaves this batch and becomes eligible again on the next
    one, which is what "held" means operationally — not deleted, just not this week."""
    p = (await s.execute(select(Payable).where(
        Payable.tenant_id == tenant_id, Payable.id == payable_id,
        Payable.payment_run_id == run_id))).scalar_one_or_none()
    if p is None:
        return None
    transition(s, p, "on_hold", actor, {"reason": reason, "run": str(run_id)})
    transition(s, p, "approved", actor, {"returned": "eligible for the next run"})
    p.payment_run_id = None
    # The override went with this run. Next week is a new run, a new set of holds and a new
    # decision — carrying the exemption forward would wave through risks nobody has looked at.
    p.is_exception, p.exception_reason, p.exception_holds = False, None, None
    audit(s, tenant_id, getattr(actor, "id", None), "payables.line_held",
          target_type="payable", target_id=p.id, category="Payments",
          summary=f"Invoice {p.invoice_number} pushed to the next run",
          detail={"reason": reason})
    await s.commit()
    return await get_run(s, tenant_id, run_id)


async def override_line(s: AsyncSession, tenant_id, actor, run_id, payable_id,
                        note: str) -> dict | None:
    """A second person clears a hold on one line. Never a switch on the Release button — a
    control that can be turned off for every line at once is not a control."""
    p = (await s.execute(select(Payable).where(
        Payable.tenant_id == tenant_id, Payable.id == payable_id,
        Payable.payment_run_id == run_id))).scalar_one_or_none()
    if p is None:
        return None
    if not (note or "").strip():
        raise ValueError("an override needs a reason — it is the only record of why this was paid")
    vendors, banks, allp = await _context(s, tenant_id)
    blocking = sorted({h["key"] for h in line_holds(p, vendors.get(p.vendor_id),
                                                    banks.get(p.vendor_id), allp) if h["hold"]})
    if not blocking:
        raise ValueError("this line is not held — there is nothing to override")
    approvers = {a.approver_user_id for a in (await s.execute(select(PayableApproval).where(
        PayableApproval.payable_id == p.id, PayableApproval.decision == "approve"))).scalars()}
    actor_id = getattr(actor, "id", None)
    if actor_id in approvers:
        raise ValueError("an override needs a second pair of eyes — you approved this invoice")
    p.is_exception = True
    p.exception_reason = note
    # The grant names the holds it clears and the run it clears them on. Anything else that
    # comes up later is a risk nobody has seen, and it must still stop the payment.
    p.exception_holds = {"keys": blocking, "run": str(run_id)}
    audit(s, tenant_id, actor_id, "payables.hold_overridden",
          target_type="payable", target_id=p.id, category="Payments",
          summary=f"Hold overridden on invoice {p.invoice_number}",
          # Both users, by name, as §4.3 requires: who approved it and who waved it through.
          detail={"note": note, "overridden_by": str(actor_id), "holds": blocking,
                  "approved_by": [str(a) for a in approvers if a]})
    s.add(PayableEvent(tenant_id=tenant_id, payable_id=p.id, event="override",
                       actor_user_id=actor_id,
                       payload={"note": note, "holds": blocking,
                                "approved_by": [str(a) for a in approvers if a]}))
    await s.commit()
    return await get_run(s, tenant_id, run_id)


# ── release ───────────────────────────────────────────────────────────────────────────────

async def release_run(s: AsyncSession, tenant_id, actor, run_id) -> dict | None:
    run = (await s.execute(select(PaymentRun).where(
        PaymentRun.tenant_id == tenant_id, PaymentRun.id == run_id))).scalar_one_or_none()
    if run is None:
        return None
    if run.status != "draft":
        raise ValueError(f"this run is already {run.status}")
    vendors, banks, allp = await _context(s, tenant_id)
    mine = [p for p in allp if p.payment_run_id == run.id]
    if not mine:
        raise ValueError("this run has no lines")

    held = []
    for p in mine:
        # No second check for `is_exception` here: line_holds already accounts for it, and the
        # Release button reads the same list. Two copies of this rule is how a run releases
        # lines the screen still shows as held.
        v = vendors.get(p.vendor_id)
        if any(h["hold"] for h in line_holds(p, v, banks.get(p.vendor_id), allp)):
            held.append(p)
    if held:
        raise ValueError(
            f"{len(held)} line{'s' if len(held) != 1 else ''} held — clear or push each one "
            "before releasing. There is no release-anyway.")

    approvers = {a.approver_user_id for a in (await s.execute(select(PayableApproval).where(
        PayableApproval.tenant_id == tenant_id, PayableApproval.decision == "approve"))).scalars()
        if a.payable_id in {p.id for p in mine}}
    actor_id = getattr(actor, "id", None)
    self_release = actor_id in approvers
    if self_release and not settings.PAYABLES_ALLOW_SELF_RELEASE:
        raise ValueError(
            "the person who approved these bills cannot also release them — "
            "ask a second releaser, or enable self-release for this workspace")
    if self_release:
        # Allowed, never silent. When the assertion cannot be enforced the trace is the control.
        audit(s, tenant_id, actor_id, "payables.self_released",
              target_type="payment_run", target_id=run.id, category="Payments",
              summary="Run released by one of its own approvers",
              detail={"run_date": str(run.run_date), "items": len(mine)})

    now = dt.datetime.now(dt.timezone.utc)
    for p in mine:
        transition(s, p, "released", actor, {"run": str(run.id)})
    run.status, run.released_by, run.released_at = "released", actor_id, now
    run.total_amount = Decimal(str(sum(float(p.amount) for p in mine)))
    run.item_count = len(mine)
    run.export_ref = f"run-{run.run_date}-{str(run.id)[:8]}.csv"
    audit(s, tenant_id, actor_id, "payables.run_released",
          target_type="payment_run", target_id=run.id, category="Payments",
          summary=f"Run {run.run_date} released: {len(mine)} payments",
          detail={"total": str(run.total_amount), "self_release": self_release})
    await s.commit()
    return await get_run(s, tenant_id, run_id)


async def export_run(s: AsyncSession, tenant_id, run_id) -> tuple[str, str] | None:
    """(filename, csv). Enough to key or import into Treasury Internet Banking — deliberately
    not NACHA, which needs the bank's file spec, an origination agreement, and handling for
    returns, NOCs and prenotes before a single byte of it is safe to generate.

    ONLY for a released run. This file is the payment instruction somebody carries to the bank,
    so producing it before release would route around every control release stands for: the
    second factor, the segregation-of-duties check, the refusal while any line is held, and the
    audit row naming who released it. A draft run's CSV would look exactly like an approved
    batch to whoever was handed it.
    """
    run = (await s.execute(select(PaymentRun).where(
        PaymentRun.tenant_id == tenant_id, PaymentRun.id == run_id))).scalar_one_or_none()
    if run is None:
        return None
    if run.status not in ("released", "reconciled"):
        raise ValueError(
            f"this run is {run.status} — a payment file exists only once the run is released")
    vendors, _banks, allp = await _context(s, tenant_id)
    biz = {b.id: b.name for b in (await s.execute(
        select(Business).where(Business.tenant_id == tenant_id))).scalars()}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["vendor_legal_name", "amount", "invoice_number", "entity", "due_date"])
    for p in [p for p in allp if p.payment_run_id == run.id]:
        v = vendors.get(p.vendor_id)
        w.writerow([v.legal_name if v else "", f"{p.amount:.2f}", p.invoice_number,
                    biz.get(p.business_id) or "", p.due_date.isoformat() if p.due_date else ""])
    return (run.export_ref or f"run-{run.run_date}.csv", buf.getvalue())


async def reconcile_run(s: AsyncSession, tenant_id, actor, run_id) -> dict | None:
    """Marked paid after the bank confirms. Separate from release because the bank is the only
    thing that knows the money actually left."""
    run = (await s.execute(select(PaymentRun).where(
        PaymentRun.tenant_id == tenant_id, PaymentRun.id == run_id))).scalar_one_or_none()
    if run is None:
        return None
    if run.status != "released":
        raise ValueError(f"a {run.status} run cannot be reconciled")
    mine = list((await s.execute(select(Payable).where(
        Payable.tenant_id == tenant_id, Payable.payment_run_id == run.id))).scalars())
    for p in mine:
        if p.status == "released":
            transition(s, p, "reconciled", actor, {"run": str(run.id)})
    run.status = "reconciled"
    audit(s, tenant_id, getattr(actor, "id", None), "payables.run_reconciled",
          target_type="payment_run", target_id=run.id, category="Payments",
          summary=f"Run {run.run_date} reconciled", detail={"items": len(mine)})
    await s.commit()
    return await get_run(s, tenant_id, run_id)
