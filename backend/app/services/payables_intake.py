"""The one place an AI proposal becomes a row.

THE SHAPE OF THE SAFETY PROPERTY. The clerk never creates anything. It writes an artifact — data
sitting in a run, inert — and a PERSON reads it and accepts it. That person is the actor passed
down into `create_payable`, so `require_human` is satisfied by CONSTRUCTION rather than by an
exemption carved out for this path. There is no "the AI may write here" hole, because the AI is
not the one writing.

Four gates stand between an extraction and a row in the inbox, and all of them already existed:

  1. the artifact must be `approved` — a human clicked that, and `approved_by` records who
  2. shipping is a second, explicit act by a human
  3. `writeback_open(employee)` — the env flag AND that employee's own toggle
  4. `require_human` at `create_payable`, satisfied because the actor is the person shipping

What lands is an `extracted` bill in the ordinary Payables inbox: no account, no submission, no
approval, no run. It sits exactly where a bill somebody typed badly would sit, and it is coded
and submitted by a person through the same screens. The clerk moved it one step — from a PDF
nobody had opened to a row with a name on it — and not one step further.

A vendor is never created here. If the clerk could not match one, that is the end of the road
until a human picks a vendor or creates one having seen a W-9. The alternative is a vendor
invented from the letterhead of whatever arrived in the inbox, which is the first half of every
invoice-fraud story.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AIArtifact, AIRun, Payable, Vendor
from . import payables
from .audit import audit
from .payables_actor import require_human

PROPOSAL_KIND = "ap_bill"


class NotAProposal(ValueError):
    """This artifact is not a bill proposal, so there is nothing to accept."""


class ProposalIncomplete(ValueError):
    """The extraction is missing something a bill cannot be created without."""


def _as_date(v):
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


async def accept_proposal(s: AsyncSession, tenant_id, actor, artifact: AIArtifact) -> dict:
    """Turn an approved `ap_bill` artifact into an `extracted` payable. Does not commit.

    `actor` is the PERSON accepting. That is the whole design: this function has no special
    permission of its own, it simply does what that person asked, under their name.
    """
    require_human(actor, "accepting a proposed bill")

    payload = artifact.payload or {}
    if artifact.kind != PROPOSAL_KIND or payload.get("kind") != PROPOSAL_KIND:
        raise NotAProposal(f"artifact {artifact.id} is a {artifact.kind}, not a bill proposal")

    # Every reason a proposal cannot become a bill, named together rather than one per attempt —
    # somebody fixing this is looking at the artifact, not at a sequence of error messages.
    missing = []
    vendor_id = payload.get("vendor_match")
    if not vendor_id:
        missing.append("a matched vendor (the clerk does not create vendors — pick or add one)")
    if not (payload.get("invoice_number") or "").strip():
        missing.append("an invoice number (it is the duplicate-payment control)")
    if payload.get("amount") in (None, ""):
        missing.append("an amount")
    if missing:
        raise ProposalIncomplete("this proposal is missing " + "; ".join(missing))

    vendor = None
    try:
        vendor = (await s.execute(select(Vendor).where(
            Vendor.tenant_id == tenant_id, Vendor.id == uuid.UUID(str(vendor_id))
        ))).scalar_one_or_none()
    except (ValueError, AttributeError):
        vendor = None
    if vendor is None:
        # Tenant-scoped, and a miss is refused rather than resolved by name. A model that emits a
        # plausible-looking id must not be able to attach a bill to somebody else's vendor.
        raise ProposalIncomplete(
            "the vendor this proposal names is not a vendor in this workspace")

    run = await s.get(AIRun, artifact.run_id)
    bill = await payables.create_payable(s, tenant_id, actor, {
        "vendor_id": vendor.id,
        "invoice_number": str(payload["invoice_number"]).strip(),
        "amount": payload["amount"],
        "invoice_date": _as_date(payload.get("invoice_date")),
        # Only a date the DOCUMENT stated. Absent, create_payable derives it from the vendor's
        # terms, which is what should decide it — a date the clerk calculated would silently
        # override terms somebody agreed to.
        "due_date": _as_date(payload.get("due_date")),
        "currency": payload.get("currency") or "USD",
        "description": payload.get("description"),
        "document_id": (run.context or {}).get("document_id") if run else None,
        # The model's own output, kept. What was proposed, how sure it was, and what it could
        # not read are part of the record of how this bill came to exist — not scaffolding to
        # discard once a row exists.
        "extraction": {**payload, "proposed_by": "ap_clerk",
                       "run_id": str(artifact.run_id), "artifact_id": str(artifact.id)},
    })

    # `extracted`, not `coded`: read off a document and nothing more. It has no account yet, so
    # it cannot be submitted, which is exactly the state it should be in — the next move is a
    # person's.
    p = await s.get(Payable, uuid.UUID(bill["id"]))
    payables.transition(s, p, "extracted", actor,
                        {"source": "ap_clerk", "artifact": str(artifact.id)})

    audit(s, tenant_id, getattr(actor, "id", None), "payables.proposal_accepted",
          target_type="payable", target_id=p.id, category="Payments",
          summary=f"AI-proposed bill accepted: invoice {p.invoice_number}",
          detail={"artifact_id": str(artifact.id), "run_id": str(artifact.run_id),
                  "vendor": vendor.display_name,
                  "confidence": payload.get("confidence"),
                  "unreadable": payload.get("unreadable") or []})
    return await payables.get_payable(s, tenant_id, p.id)
