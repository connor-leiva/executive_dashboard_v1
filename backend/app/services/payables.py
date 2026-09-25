"""Payables · bills and approvals (SPEC-payables §3).

A payable is a bill that has not been paid. Its lifecycle is deliberately parallel to
`BookTxn.scan_state` and not folded into it — that one describes money which already moved.

Three things carry the weight here:

`transition` is the only writer of `payable_event` for a status CHANGE, and every change leaves
exactly one row — so the timeline is complete by construction rather than by everyone
remembering to log. Creation writes the one event that is not a change, because a bill arriving
has no prior state to move from.

The duplicate check runs in the service as well as in the unique constraint, so the error can
name the invoice it collided with. A 409 tells somebody they are wrong; naming the prior payable
tells them what to do next.

Nothing reaches `approved` on one signature when the band required two, and one person cannot
supply both. That is the whole point of a threshold matrix.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (ApprovalPolicy, Business, Payable, PayableApproval, PayableEvent,
                      User, Vendor, VendorBankAccount)
from .audit import audit
from .payables_vendor import derive_status

# received → extracted → needs_info
#                     ↘  coded → awaiting_approval → approved → scheduled → released → reconciled
#                                       ↓                ↓
#                                   rejected          on_hold
LIFECYCLE: dict[str, tuple[str, ...]] = {
    "received": ("extracted", "coded", "needs_info"),
    "extracted": ("coded", "needs_info"),
    "needs_info": ("coded", "extracted"),
    "coded": ("awaiting_approval", "needs_info"),
    "awaiting_approval": ("approved", "rejected", "needs_info"),
    "approved": ("scheduled", "on_hold", "rejected"),
    "scheduled": ("released", "on_hold"),
    "released": ("reconciled",),
    "on_hold": ("scheduled", "approved"),
    "rejected": (),
    "reconciled": (),
}
DECISIONS = ("approve", "reject", "request_info")


class TransitionError(ValueError):
    """A status change the lifecycle does not permit."""


def transition(s: AsyncSession, payable: Payable, new_status: str, actor,
               payload: dict | None = None) -> PayableEvent:
    """Move a payable and record it. THE ONLY WRITER OF payable_event.

    Does not commit — the caller owns the transaction, as `audit` does. A transition that
    committed on its own would make a multi-step operation partially durable.
    """
    allowed = LIFECYCLE.get(payable.status, ())
    if new_status not in allowed:
        raise TransitionError(
            f"a payable cannot go from {payable.status} to {new_status}"
            + (f" — only {', '.join(allowed)}" if allowed else " — it is final"))
    was, payable.status = payable.status, new_status
    payable.updated_at = dt.datetime.now(dt.timezone.utc)
    ev = PayableEvent(tenant_id=payable.tenant_id, payable_id=payable.id, event=new_status,
                      actor_user_id=getattr(actor, "id", None),
                      payload={"from": was, **(payload or {})})
    s.add(ev)
    return ev


# ── reads ─────────────────────────────────────────────────────────────────────────────────

def _holds(p: Payable, vendor_status: str | None, band: str | None) -> list[dict]:
    """Why this payable cannot be submitted, computed HERE.

    §3.5's rule: a hold carries a flag and every disable reads it. Scattering the conditions
    through the UI is how one screen ends up enforcing a rule another screen has forgotten —
    and the forgotten one is the screen that pays somebody who was never verified.
    """
    out: list[dict] = []
    if vendor_status != "active":
        out.append({"key": "vendor_not_ready", "label": "Vendor not ready", "hold": True,
                    "why": "A W-9 and verified banking are required before a first payment."})
    if p.standard_account_id is None:
        out.append({"key": "uncoded", "label": "Not coded", "hold": True,
                    "why": "An expense account is needed before this can go for approval."})
    if band is None:
        out.append({"key": "no_band", "label": "No approval band", "hold": True,
                    "why": "No policy covers this amount, so nobody would be required to approve it."})
    return out


def _row(p: Payable, vendor: Vendor | None, approvals: list[PayableApproval],
         bizmap: dict, umap: dict, vendor_status: str | None = None,
         band: str | None = None) -> dict:
    open_slots = [a for a in approvals if a.decision is None]
    holds = _holds(p, vendor_status, band)
    return {
        "id": str(p.id), "vendor_id": str(p.vendor_id),
        "vendor": vendor.display_name if vendor else None,
        "invoice_number": p.invoice_number,
        "invoice_date": p.invoice_date.isoformat() if p.invoice_date else None,
        "due_date": p.due_date.isoformat() if p.due_date else None,
        "amount": float(p.amount), "currency": p.currency,
        "description": p.description, "status": p.status,
        "business_id": str(p.business_id) if p.business_id else None,
        "business_name": (bizmap or {}).get(p.business_id),
        "standard_account_id": str(p.standard_account_id) if p.standard_account_id else None,
        "class_key": p.class_key, "location_key": p.location_key,
        "is_exception": bool(p.is_exception), "exception_reason": p.exception_reason,
        "document_id": str(p.document_id) if p.document_id else None,
        "extraction": p.extraction,
        "submitted_at": p.submitted_at.isoformat() if p.submitted_at else None,
        "approvals": [{"id": str(a.id), "approver_user_id": str(a.approver_user_id) if a.approver_user_id else None,
                       "approver": umap.get(a.approver_user_id),
                       "decision": a.decision, "band": a.threshold_band, "note": a.note,
                       "decided_at": a.decided_at.isoformat() if a.decided_at else None}
                      for a in approvals],
        "awaiting": len(open_slots),
        "vendor_status": vendor_status, "band": band,
        "holds": holds,
        # The single boolean every Submit button reads. Both halves matter: the right status AND
        # nothing holding it.
        "can_submit": p.status == "coded" and not any(h["hold"] for h in holds),
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


async def _labels(s: AsyncSession, tenant_id) -> tuple[dict, dict]:
    biz = {b.id: b.name for b in (await s.execute(
        select(Business).where(Business.tenant_id == tenant_id))).scalars()}
    users = {u.id: (u.name or u.email) for u in (await s.execute(
        select(User).where(User.tenant_id == tenant_id))).scalars()}
    return biz, users


async def list_payables(s: AsyncSession, tenant_id, *, status: str | None = None,
                        assigned_to=None, run_id=None) -> list[dict]:
    conds = [Payable.tenant_id == tenant_id]
    if status:
        conds.append(Payable.status == status)
    if run_id is not None:
        conds.append(Payable.payment_run_id == run_id)
    rows = (await s.execute(select(Payable).where(*conds)
                            .order_by(Payable.due_date, Payable.created_at))).scalars().all()
    appr: dict = {}
    for a in (await s.execute(select(PayableApproval).where(
            PayableApproval.tenant_id == tenant_id))).scalars():
        appr.setdefault(a.payable_id, []).append(a)
    if assigned_to is not None:
        # `mine=1` is the DEFAULT on the approvals screen. An approver who has to scan everyone
        # else's list to find their three stops approving, which is the failure this prevents.
        rows = [p for p in rows
                if any(a.approver_user_id == assigned_to and a.decision is None
                       for a in appr.get(p.id, []))]
    vendors = {v.id: v for v in (await s.execute(
        select(Vendor).where(Vendor.tenant_id == tenant_id))).scalars()}
    # Two more queries for the whole list, not two per row: the active bank of every vendor and
    # the policy matrix. Holds are then pure computation.
    banks = {}
    for b in (await s.execute(select(VendorBankAccount).where(
            VendorBankAccount.tenant_id == tenant_id, VendorBankAccount.active.is_(True))
            .order_by(VendorBankAccount.created_at))).scalars():
        banks[b.vendor_id] = b
    policies = list((await s.execute(select(ApprovalPolicy).where(
        ApprovalPolicy.tenant_id == tenant_id))).scalars())
    bizmap, umap = await _labels(s, tenant_id)
    out = []
    for p in rows:
        v = vendors.get(p.vendor_id)
        out.append(_row(p, v, appr.get(p.id, []), bizmap, umap,
                        vendor_status=(derive_status(v, banks.get(p.vendor_id)) if v else None),
                        band=pick_band(policies, p.business_id, p.amount)["band"]))
    return out


async def get_payable(s: AsyncSession, tenant_id, payable_id) -> dict | None:
    p = (await s.execute(select(Payable).where(
        Payable.tenant_id == tenant_id, Payable.id == payable_id))).scalar_one_or_none()
    if p is None:
        return None
    v = (await s.execute(select(Vendor).where(Vendor.id == p.vendor_id))).scalar_one_or_none()
    appr = (await s.execute(select(PayableApproval).where(
        PayableApproval.payable_id == p.id).order_by(PayableApproval.created_at))).scalars().all()
    bizmap, umap = await _labels(s, tenant_id)
    band = (await resolve_band(s, tenant_id, p.business_id, p.amount))["band"]
    status = await _vendor_status(s, tenant_id, v) if v else None
    return _row(p, v, list(appr), bizmap, umap, vendor_status=status, band=band)


async def payable_timeline(s: AsyncSession, tenant_id, payable_id) -> list[dict]:
    _, umap = await _labels(s, tenant_id)
    evs = (await s.execute(select(PayableEvent).where(
        PayableEvent.tenant_id == tenant_id, PayableEvent.payable_id == payable_id)
        .order_by(PayableEvent.created_at))).scalars().all()
    return [{"event": e.event, "actor": umap.get(e.actor_user_id), "payload": e.payload,
             "at": e.created_at.isoformat() if e.created_at else None} for e in evs]


# ── the approval matrix ───────────────────────────────────────────────────────────────────

def pick_band(policies: list, business_id, amount) -> dict:
    """The matching rule, as a pure function over already-loaded policies.

    Split out so a list of payables can resolve every row's band from ONE query instead of one
    per row, and so the rule is testable without a database.

    Both ends inclusive: a policy of 2,500–15,000 covers exactly 2,500 and exactly 15,000. A
    matrix with exclusive edges leaves a value in no band at all, and the value it leaves out is
    always the round number somebody actually invoices.

    Overlaps resolve deterministically — business-specific beats tenant-wide, then the narrower
    band (higher floor) wins, then label, so the answer never depends on row order.
    """
    amt = Decimal(str(amount))
    matches = [p for p in policies if p.active
               if (p.business_id is None or p.business_id == business_id)
               and Decimal(str(p.min_amount)) <= amt
               and (p.max_amount is None or amt <= Decimal(str(p.max_amount)))]
    if not matches:
        return {"band": None, "policy_id": None, "required_user_ids": [],
                "required_role": None, "requires_second_approver": False, "approver_count": 0}
    matches.sort(key=lambda p: (p.business_id is None, -Decimal(str(p.min_amount)), p.label))
    best = matches[0]
    users = [u for u in (best.required_user_ids or []) if u]
    count = max(len(users), 2 if best.requires_second_approver else 1)
    return {"band": best.label, "policy_id": str(best.id), "required_user_ids": users,
            "required_role": best.required_role,
            "requires_second_approver": bool(best.requires_second_approver),
            "approver_count": count}


async def resolve_band(s: AsyncSession, tenant_id, business_id, amount) -> dict:
    """Which band an amount falls in, and who therefore has to sign."""
    policies = (await s.execute(select(ApprovalPolicy).where(
        ApprovalPolicy.tenant_id == tenant_id, ApprovalPolicy.active.is_(True)))).scalars().all()
    return pick_band(list(policies), business_id, amount)


# ── writes ────────────────────────────────────────────────────────────────────────────────

async def _vendor_or_raise(s: AsyncSession, tenant_id, vendor_id) -> Vendor:
    v = (await s.execute(select(Vendor).where(
        Vendor.tenant_id == tenant_id, Vendor.id == vendor_id))).scalar_one_or_none()
    if v is None:
        raise ValueError("that vendor does not exist")
    return v


async def _vendor_status(s: AsyncSession, tenant_id, vendor: Vendor) -> str:
    bank = (await s.execute(select(VendorBankAccount).where(
        VendorBankAccount.tenant_id == tenant_id, VendorBankAccount.vendor_id == vendor.id,
        VendorBankAccount.active.is_(True))
        .order_by(VendorBankAccount.created_at.desc()))).scalars().first()
    return derive_status(vendor, bank)


async def create_payable(s: AsyncSession, tenant_id, actor, payload: dict) -> dict:
    vendor = await _vendor_or_raise(s, tenant_id, payload.get("vendor_id"))
    invoice_number = (payload.get("invoice_number") or "").strip()
    if not invoice_number:
        raise ValueError("an invoice number is required — it is the duplicate-payment control")
    if payload.get("amount") is None:
        raise ValueError("an amount is required")

    prior = (await s.execute(select(Payable).where(
        Payable.tenant_id == tenant_id, Payable.vendor_id == vendor.id,
        Payable.invoice_number == invoice_number))).scalars().first()
    if prior is not None:
        # Name it. "Duplicate invoice" alone sends somebody hunting through a list for a row the
        # error will not show them, and the usual next step is to pay it twice anyway.
        raise ValueError(
            f"invoice {invoice_number} already exists for {vendor.display_name}: "
            f"{prior.amount} dated {prior.invoice_date or 'unknown'}, currently {prior.status}")

    invoice_date = payload.get("invoice_date")
    due_date = payload.get("due_date")
    overridden = due_date is not None
    if due_date is None and invoice_date is not None:
        due_date = invoice_date + dt.timedelta(days=vendor.terms_days or 30)

    p = Payable(
        tenant_id=tenant_id, vendor_id=vendor.id, invoice_number=invoice_number,
        invoice_date=invoice_date, due_date=due_date,
        amount=Decimal(str(payload["amount"])),
        currency=payload.get("currency") or "USD",
        description=payload.get("description"),
        business_id=payload.get("business_id") or vendor.default_business_id,
        legal_entity_id=payload.get("legal_entity_id") or vendor.default_legal_entity_id,
        standard_account_id=(payload.get("standard_account_id")
                             or vendor.default_standard_account_id),
        class_key=payload.get("class_key"), location_key=payload.get("location_key"),
        document_id=payload.get("document_id"), extraction=payload.get("extraction"),
        status="received")
    s.add(p)
    await s.flush()
    # The genesis row. Not a transition — there is no prior state to move from — so it is the
    # one event written outside `transition`, and it is written here so a timeline always opens
    # with how the bill arrived rather than starting mid-story.
    s.add(PayableEvent(tenant_id=tenant_id, payable_id=p.id, event="received",
                       actor_user_id=getattr(actor, "id", None),
                       payload={"source": payload.get("source") or "manual"}))
    if overridden:
        # Paying off-terms is a decision about cash, not a typo. It is audited either way.
        audit(s, tenant_id, getattr(actor, "id", None), "payables.due_date_overridden",
              target_type="payable", target_id=p.id, category="Payments",
              summary=f"Due date set by hand on invoice {invoice_number}",
              detail={"due_date": str(due_date), "terms_days": vendor.terms_days})
    if p.standard_account_id is not None:
        transition(s, p, "coded", actor, {"auto": "vendor default"})
    audit(s, tenant_id, getattr(actor, "id", None), "payables.created",
          target_type="payable", target_id=p.id, category="Payments",
          summary=f"Invoice {invoice_number} from {vendor.display_name}",
          detail={"amount": str(p.amount)})
    await s.commit()
    return await get_payable(s, tenant_id, p.id)


_CODING = ("business_id", "legal_entity_id", "standard_account_id", "class_key", "location_key",
           "description", "invoice_date", "service_period_start", "service_period_end",
           "is_exception", "exception_reason")


async def update_coding(s: AsyncSession, tenant_id, actor, payable_id, payload: dict) -> dict | None:
    p = (await s.execute(select(Payable).where(
        Payable.tenant_id == tenant_id, Payable.id == payable_id))).scalar_one_or_none()
    if p is None:
        return None
    if p.status in ("released", "reconciled", "rejected"):
        raise ValueError(f"a {p.status} payable cannot be recoded")
    changed = {}
    for f in _CODING:
        if f in payload:
            setattr(p, f, payload[f])
            changed[f] = str(payload[f])
    if "due_date" in payload:
        p.due_date = payload["due_date"]
        changed["due_date"] = str(payload["due_date"])
        audit(s, tenant_id, getattr(actor, "id", None), "payables.due_date_overridden",
              target_type="payable", target_id=p.id, category="Payments",
              summary=f"Due date changed on invoice {p.invoice_number}",
              detail={"due_date": str(payload["due_date"])})
    p.updated_at = dt.datetime.now(dt.timezone.utc)
    if p.standard_account_id is not None and p.status in ("received", "extracted", "needs_info"):
        transition(s, p, "coded", actor, {"changed": sorted(changed)})
    audit(s, tenant_id, getattr(actor, "id", None), "payables.recoded",
          target_type="payable", target_id=p.id, category="Payments",
          summary=f"Coding updated on invoice {p.invoice_number}",
          detail={"changed": sorted(changed)})
    await s.commit()
    return await get_payable(s, tenant_id, payable_id)


async def submit_for_approval(s: AsyncSession, tenant_id, actor, payable_id) -> dict | None:
    p = (await s.execute(select(Payable).where(
        Payable.tenant_id == tenant_id, Payable.id == payable_id))).scalar_one_or_none()
    if p is None:
        return None
    vendor = await _vendor_or_raise(s, tenant_id, p.vendor_id)
    status = await _vendor_status(s, tenant_id, vendor)
    if status != "active":
        # The gate from Phase 1, enforced at the moment it matters. Say WHICH condition failed —
        # "vendor not active" leaves somebody clicking around the vendor screen guessing.
        missing = []
        if not vendor.w9_document_id:
            missing.append("a W-9")
        bank = (await s.execute(select(VendorBankAccount).where(
            VendorBankAccount.tenant_id == tenant_id, VendorBankAccount.vendor_id == vendor.id,
            VendorBankAccount.active.is_(True)))).scalars().first()
        if bank is None:
            missing.append("banking on file")
        elif bank.verified_at is None:
            missing.append("a verified callback on that banking")
        if status == "inactive":
            missing = ["reactivation — the vendor was made inactive"]
        raise ValueError(
            f"{vendor.display_name} is not ready to be paid: needs {', '.join(missing)}")

    band = await resolve_band(s, tenant_id, p.business_id, p.amount)
    if not band["band"]:
        # Refusing beats approving with nobody required. A payable above every ceiling is
        # exactly the one that must not slip through.
        raise ValueError(
            f"no approval policy covers {p.amount} — add a band before submitting this")

    approvers = band["required_user_ids"] or []
    slots = approvers or [None] * band["approver_count"]
    if approvers and len(slots) < band["approver_count"]:
        slots = slots + [None] * (band["approver_count"] - len(slots))
    for who in slots:
        s.add(PayableApproval(tenant_id=tenant_id, payable_id=p.id,
                              approver_user_id=who, threshold_band=band["band"]))
    p.submitted_by, p.submitted_at = getattr(actor, "id", None), dt.datetime.now(dt.timezone.utc)
    transition(s, p, "awaiting_approval", actor,
               {"band": band["band"], "approvers": len(slots)})
    audit(s, tenant_id, getattr(actor, "id", None), "payables.submitted",
          target_type="payable", target_id=p.id, category="Payments",
          summary=f"Invoice {p.invoice_number} submitted for approval",
          detail={"band": band["band"], "approvers": len(slots)})
    await s.commit()
    return await get_payable(s, tenant_id, payable_id)


async def decide(s: AsyncSession, tenant_id, actor, payable_id, decision: str,
                 note: str | None = None) -> dict | None:
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(DECISIONS)}")
    p = (await s.execute(select(Payable).where(
        Payable.tenant_id == tenant_id, Payable.id == payable_id))).scalar_one_or_none()
    if p is None:
        return None
    if p.status != "awaiting_approval":
        raise ValueError(f"invoice {p.invoice_number} is {p.status}, not awaiting approval")
    slots = (await s.execute(select(PayableApproval).where(
        PayableApproval.tenant_id == tenant_id,
        PayableApproval.payable_id == p.id).order_by(PayableApproval.created_at))).scalars().all()
    slots = list(slots)
    actor_id = getattr(actor, "id", None)

    # One person cannot supply two signatures. A band that requires two approvers is asking for
    # two people; letting one fill both slots would satisfy the letter and delete the control.
    if any(a.approver_user_id == actor_id and a.decision is not None for a in slots):
        raise ValueError("you have already decided on this invoice")

    mine = next((a for a in slots if a.approver_user_id == actor_id and a.decision is None), None)
    if mine is None:
        mine = next((a for a in slots if a.approver_user_id is None and a.decision is None), None)
    if mine is None:
        raise ValueError("this invoice is not waiting on you")

    mine.approver_user_id = actor_id
    mine.decision, mine.note = decision, note
    mine.decided_at = dt.datetime.now(dt.timezone.utc)

    if decision == "reject":
        transition(s, p, "rejected", actor, {"note": note})
    elif decision == "request_info":
        transition(s, p, "needs_info", actor, {"note": note})
    elif all(a.decision == "approve" for a in slots):
        # Only when EVERY required slot has approved. One signature on a two-signature band
        # leaves the payable exactly where it was.
        transition(s, p, "approved", actor, {"band": mine.threshold_band})

    audit(s, tenant_id, actor_id, f"payables.{decision}d",
          target_type="payable", target_id=p.id, category="Payments",
          summary=f"Invoice {p.invoice_number} {decision}", detail={"note": note})
    await s.commit()
    return await get_payable(s, tenant_id, payable_id)


# ── policies ──────────────────────────────────────────────────────────────────────────────

async def list_policies(s: AsyncSession, tenant_id) -> list[dict]:
    rows = (await s.execute(select(ApprovalPolicy).where(
        ApprovalPolicy.tenant_id == tenant_id).order_by(ApprovalPolicy.min_amount))).scalars().all()
    return [{"id": str(p.id), "label": p.label,
             "business_id": str(p.business_id) if p.business_id else None,
             "min_amount": float(p.min_amount),
             "max_amount": float(p.max_amount) if p.max_amount is not None else None,
             "required_role": p.required_role,
             "required_user_ids": p.required_user_ids or [],
             "requires_second_approver": bool(p.requires_second_approver),
             "active": bool(p.active)} for p in rows]


async def replace_policies(s: AsyncSession, tenant_id, actor, bands: list[dict]) -> list[dict]:
    """The matrix is edited as a whole. Editing bands one at a time invites a moment where two
    overlap or a gap opens, and the gap is what lets an invoice through unapproved."""
    for p in (await s.execute(select(ApprovalPolicy).where(
            ApprovalPolicy.tenant_id == tenant_id))).scalars().all():
        await s.delete(p)
    await s.flush()
    for b in bands:
        if not (b.get("label") or "").strip():
            raise ValueError("every band needs a label — the approver is told which one they are in")
        s.add(ApprovalPolicy(
            tenant_id=tenant_id, label=b["label"].strip(),
            business_id=b.get("business_id"),
            min_amount=Decimal(str(b.get("min_amount") or 0)),
            max_amount=(Decimal(str(b["max_amount"])) if b.get("max_amount") is not None else None),
            required_role=b.get("required_role"),
            required_user_ids=b.get("required_user_ids") or [],
            requires_second_approver=bool(b.get("requires_second_approver")),
            active=bool(b.get("active", True))))
    audit(s, tenant_id, getattr(actor, "id", None), "payables.policies_replaced",
          target_type="tenant", target_id=tenant_id, category="Payments",
          summary="Approval matrix updated", detail={"bands": len(bands)})
    await s.commit()
    return await list_policies(s, tenant_id)
