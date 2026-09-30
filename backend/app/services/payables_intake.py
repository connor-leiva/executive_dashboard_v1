"""How an invoice gets in, and where it stops.

Two doors, at the two ends of the same pipeline.

`receive_invoice` is the front door: a PDF arrives — dropped on the Payables screen, or forwarded
to the workspace's own AP address — and is filed as a Payables document with a run queued against
it for the AP Clerk. That is all it does. Filing a document costs nothing and decides nothing.

`accept_proposal` is the far door, and a person has to open it.

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
import re
import secrets
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import AIArtifact, AIEmployee, AIEmployeeSkill, AIRun, Payable, Tenant, Vendor
from . import binder_ingest, payables
from .audit import audit
from .payables_actor import require_human

PROPOSAL_KIND = "ap_bill"
INTAKE_SKILL = "ap_intake"
DOCUMENT_CATEGORY = "payable"
# A document-triggered run is the one trigger in the system that a STRANGER can pull: anyone who
# knows a workspace's forwarding address can email it, and each attachment would otherwise be a
# Claude call charged to the workspace. The monthly token budget is the money backstop, but it is
# spent by then; this bounds the day. Past the cap the document is still filed and can be read by
# hand -- nothing is lost but the automatic reading.
MAX_INTAKE_PER_DAY = 60


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


# ═══════════════════════════════════════════════════════════════════════════════════════════
# The front door: a document arrives
# ═══════════════════════════════════════════════════════════════════════════════════════════

# ── the forwarding address ─────────────────────────────────────────────────────────────────
# Payables gets its OWN address rather than sharing Binder's. They carry different things to
# different people: a supplier invoice, forwarded by whoever happens to bill you, and the legal
# record of the entities, read behind a second factor. One address for both means a month of
# supplier invoices filed among somebody's formation papers, and one secret whose leak opens both.
CFG_ENABLED = "payables_email_ingest"
CFG_LOCAL = "payables_email_local"
DEFAULT_LOCAL = "ap"
# Lowercase, starts alphanumeric, the characters every mail provider accepts in a local part and
# nothing that needs quoting. Capped at 31 because the whole address has to fit in a mail header
# and in the box on the settings screen.
LOCAL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,30}$")
# `binder@` is a live mailbox on the same host that goes somewhere else entirely. Refusing it here
# is cheaper than working out why forwarded invoices are appearing in the legal binder.
RESERVED_LOCALS = {"binder"}


class AddressRejected(ValueError):
    """The local part offered for the forwarding address cannot be used."""


class DayIsFull(RuntimeError):
    """This workspace has taken as much forwarded mail today as it is willing to."""


def ingest_domain() -> str:
    return (settings.PAYABLES_INGEST_DOMAIN or settings.PLATFORM_DOMAIN).strip().lower()


def email_settings(tenant: Tenant) -> dict:
    """What the settings screen shows: the address, whether it is open, and whether the platform
    side of the channel exists at all.

    `channel_open` is the honest half. A workspace can switch this on and forward its AP mail
    to the address below, and if no PAYABLES_INGEST_SECRET is set on the API service then every
    one of those messages is answered with a 404 and nobody is told. So the screen says which of
    the two halves is missing rather than showing an address and hoping.
    """
    cfg = tenant.config or {}
    local = str(cfg.get(CFG_LOCAL) or DEFAULT_LOCAL).strip().lower()
    if not LOCAL_RE.match(local):            # a hand-edited config should not break the page
        local = DEFAULT_LOCAL
    return {
        "enabled": bool(cfg.get(CFG_ENABLED)),
        "local_part": local,
        "address": f"{local}@{tenant.slug}.{ingest_domain()}",
        "channel_open": bool(settings.PAYABLES_INGEST_SECRET),
    }


def set_email_settings(tenant: Tenant, *, enabled=None, local_part=None) -> dict:
    """Change the forwarding address or turn the channel on/off. Does not commit."""
    # A COPY. Mutating the dict SQLAlchemy loaded leaves it identical to the dict SQLAlchemy is
    # comparing against, so it sees no change and issues no UPDATE -- silently, on a commit that
    # reports success.
    cfg = dict(tenant.config or {})
    if local_part is not None:
        lp = str(local_part or "").strip().lower().lstrip("@")
        if not LOCAL_RE.match(lp):
            raise AddressRejected(
                "An address can use letters, numbers, dots, dashes and underscores, has to start "
                "with a letter or number, and stops at 31 characters.")
        if lp in RESERVED_LOCALS:
            raise AddressRejected(
                f"{lp}@ already belongs to another part of the product. Pick a different one.")
        cfg[CFG_LOCAL] = lp
    if enabled is not None:
        cfg[CFG_ENABLED] = bool(enabled)
    tenant.config = cfg
    return email_settings(tenant)


# A real recipient list is one or two addresses. These bound what a hostile field can cost --
# getaddresses on a megabyte of header, or a lookup per address on a list of a thousand.
MAX_FIELD_CHARS = 2000
MAX_CANDIDATES = 10


def _addresses(raw: str) -> list[str]:
    """Pull addresses out of one provider field, which may be a header list or a JSON envelope."""
    from email.utils import getaddresses
    raw = (raw or "").strip()[:MAX_FIELD_CHARS]
    if not raw:
        return []
    if raw.startswith("{"):                           # SendGrid's envelope: {"to":[...],"from":...}
        import json
        try:
            blob = json.loads(raw)
        except ValueError:
            return []
        got = blob.get("to") or []
        raw = ", ".join(got) if isinstance(got, list) else str(got)
    out = []
    for _name, addr in getaddresses([raw]):
        addr = (addr or "").strip().lower()
        if "@" in addr and addr not in out:
            out.append(addr)
    return out


def candidate_recipients(*, envelope: str = "", recipient: str = "", to: str = "") -> list[str]:
    """Which addresses this message was DELIVERED to, according to the provider.

    The envelope wins outright, and when the provider supplies one the `To:` header is not read
    at all. Two reasons, and the second is the important one:

      * A workspace forwards its existing AP group -- ap@springb.com -- to the address below, and
        a forwarded message still carries the ORIGINAL `To:`. The header answers "who did the
        sender write to", which is not the question being asked.
      * The header is written by the SENDER. This endpoint resolves a workspace from the recipient
        address, so trusting a sender-supplied header would let anyone who can reach the channel
        at all name which workspace their attachment lands in. The envelope recipient is the
        provider's own statement of where it delivered the message, and cannot be forged by the
        person who sent it.

    The header is the last resort, for a provider that forwards only headers. It is weaker, which
    is why the local part is checked as well and the daily cap exists.

    Providers name the envelope differently -- Mailgun `recipient`, SendGrid an `envelope` blob --
    so both are read here and the caller does not have to know which one it is talking to. The
    keyword-only signature is deliberate: the precedence is decided HERE, not by the order a
    router happens to pass three strings in.
    """
    env = _addresses(envelope)
    strong = env + [a for a in _addresses(recipient) if a not in env]
    return (strong or _addresses(to))[:MAX_CANDIDATES]


async def resolve_recipient(s: AsyncSession, addresses: list[str]) -> tuple[Tenant, dict] | None:
    """Which workspace an arriving message belongs to, or None.

    Three things have to line up: the host names a workspace, that workspace has turned the
    channel on, and the local part is the one it configured. The third is what makes "configurable"
    mean something -- anyone can send mail to ap@{slug}, so a workspace that wants the address
    itself to be hard to guess can have that, and a workspace that is happy with `ap` can have
    that too.
    """
    suffix = "." + ingest_domain()
    for addr in addresses:
        local, _, host = addr.partition("@")
        # `ap+anything@` is delivered to `ap@` by every provider, so a plus tag is not a mismatch.
        local = local.split("+", 1)[0].strip()
        # The host has to be the workspace's subdomain of the ingest domain, exactly. Matching on
        # the first label alone would resolve `ap@springb.com` -- a workspace's OWN group address,
        # which is in the To: header of every message it forwards -- as if it were ours.
        if not (host.endswith(suffix) and host.count(".") == suffix.count(".")):
            continue
        slug = host[:-len(suffix)].strip()
        # compare_digest raises on a non-ASCII str, and the local part arrives off the wire.
        if not (local and slug and local.isascii()):
            continue
        tenant = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if tenant is None:
            continue
        cfg = email_settings(tenant)
        if not cfg["enabled"]:
            continue
        # Constant-time: the local part is the only part of this address that is not public.
        if not secrets.compare_digest(local, cfg["local_part"]):
            continue
        return tenant, cfg
    return None


# ── filing it, and putting it in front of the clerk ────────────────────────────────────────

async def _clerk(s: AsyncSession, tenant_id):
    """The active employee whose job is accounts payable, or None. Family, not name: an employee
    called "AP Clerk" that was created without one is a social-media manager wearing the badge."""
    from .ai_employees import employee_family
    from .ai_skills import AP
    emps = (await s.execute(select(AIEmployee).where(
        AIEmployee.tenant_id == tenant_id, AIEmployee.status == "active"))).scalars().all()
    return next((e for e in emps if employee_family(e) == AP), None)


async def _emailed_today(s: AsyncSession, tenant_id) -> int:
    """How many documents were FORWARDED IN today. Separate from the run count below, and
    separate for a reason: a duplicate queues no run but still arrives, and a document is stored
    whether or not anybody reads it. Capping only the readings left the blob store as an open
    door -- 25 MB an attachment, from anyone who knows the address."""
    from ..models import BinderDocument
    from .ai_employees import _now
    day_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    return (await s.execute(select(func.count(BinderDocument.id)).where(
        BinderDocument.tenant_id == tenant_id, BinderDocument.uploaded_via == "email",
        BinderDocument.category.in_(sorted(binder_ingest.PAYABLES_CATEGORIES)),
        BinderDocument.created_at >= day_start))).scalar_one()


async def _intake_runs_today(s: AsyncSession, tenant_id) -> int:
    from .ai_employees import _now
    day_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    return (await s.execute(select(func.count(AIRun.id)).where(
        AIRun.tenant_id == tenant_id, AIRun.skill_key == INTAKE_SKILL,
        AIRun.trigger == "document", AIRun.created_at >= day_start))).scalar_one()


async def queue_intake(s: AsyncSession, tenant_id, doc, *, via: str,
                       sender: str | None = None) -> dict:
    """Queue an ap_intake run against a filed document. Does not commit.

    Returns why it did NOT queue as often as it returns a run, and that is the point: the
    document is filed either way. Every "no" here is a reason a person can act on -- nobody does
    AP yet, the skill is switched off, the budget is spent -- rather than a document that sat in a
    table with nothing watching it.
    """
    from . import ai_employees as eng
    if not eng.enabled():
        return {"queued": False, "reason": "ai_disabled"}
    emp = await _clerk(s, tenant_id)
    if emp is None:
        return {"queued": False, "reason": "no_clerk"}
    es = (await s.execute(select(AIEmployeeSkill).where(
        AIEmployeeSkill.employee_id == emp.id,
        AIEmployeeSkill.skill_key == INTAKE_SKILL))).scalar_one_or_none()
    if es is not None and not es.enabled:
        return {"queued": False, "reason": "skill_off"}
    if await _intake_runs_today(s, tenant_id) >= MAX_INTAKE_PER_DAY:
        return {"queued": False, "reason": "daily_cap"}

    over = await eng._over_budget(s, tenant_id)
    run = await eng._queue_run(
        s, tenant_id, emp, INTAKE_SKILL, "document",
        {"source": "Forwarded email" if via == "email" else "Upload",
         "label": (sender or "")[:160] or None, "title": doc.filename},
        over)
    # `context` is what the seam reads to attach the file (ai_context_ap.document_blocks); the
    # trigger_context above is the EVENT, which is what the run banner shows a person.
    run.context = {"document_id": str(doc.id)}
    return {"queued": True, "run_id": str(run.id), "status": run.status,
            "employee": emp.name, "reason": None}


async def receive_invoice(s: AsyncSession, tenant_id, user, *, filename: str, data: bytes,
                          via: str = "upload", sender: str | None = None) -> dict:
    """File an arriving supplier invoice and put it in front of the AP Clerk. Commits.

    Filed as a PAYABLES document, which keeps it out of the Binder's lists, out of the Binder's
    extractor, and out of the Binder's raw-file route -- see binder_ingest.binder_scope.

    A duplicate queues nothing. `ingest_document` dedups on the content hash, and the same bytes
    arriving twice (forwarded by two people, or a provider retrying) must not become two proposals
    and then two bills. A genuinely different SCAN of the same invoice still gets its own run, and
    is caught one door later: create_payable refuses a second bill with the same invoice number
    against the same vendor.
    """
    if via == "email" and await _emailed_today(s, tenant_id) >= MAX_INTAKE_PER_DAY:
        # Only the forwarded door. An upload came from somebody who signed in.
        raise DayIsFull(f"{tenant_id} has taken {MAX_INTAKE_PER_DAY} forwarded documents today")
    doc, created = await binder_ingest.ingest_document(
        s, tenant_id, user, filename=filename or "invoice.pdf", data=data,
        uploaded_via=via, entity_id=None, category=DOCUMENT_CATEGORY, commit=False)
    intake = ({"queued": False, "reason": "duplicate"} if not created
              else await queue_intake(s, tenant_id, doc, via=via, sender=sender))
    await s.commit()
    return {"document_id": str(doc.id), "filename": doc.filename, "deduped": not created,
            "intake": intake}
