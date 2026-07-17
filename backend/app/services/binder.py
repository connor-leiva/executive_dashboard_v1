"""Acumyn Binder — entity management service (SPEC-binder-module Part 5.0 / 5.1).

Step 2 scope: the manual entity lifecycle (create / list / edit / deactivate) plus the
read shapes the management screen renders. Entities are tenant data the user configures
themselves — this layer never seeds one, and it deliberately allows a name-only entity to
save into a *dormant* state (rule-derived filings stay dormant until jurisdiction, entity
type, and formation date are all present). The assisted attribute-fill path and the
confirmation loop (obligations) arrive with extraction in later steps.

Every mutation is audited and the caller-facing functions own their commit, mirroring the
Books service. Reads never expose a raw EIN — only a masked form (Part 8 `ein_masked`);
full document-storage / PII hardening is a multi-tenant concern flagged out of scope for v1
(Part 14) but the masking here is the cheap, correct default to start with.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import (LegalEntity, Business, BinderDocument, Obligation, ProposedObligation,
                      ClosePeriod, Integration, JurisdictionRule)
from .audit import audit
from .binder_status import compute_status, roll_forward, TAX_KINDS, _add_months

# The three fields the rules engine needs before it can derive any obligation (Part 5.0).
# An entity missing any of them is valid but dormant.
TRACKING_FIELDS = ("jurisdiction", "entity_type", "formation_date")

ENTITY_TYPES = {"llc", "s_corp", "c_corp", "partnership", "trust"}
ENTITY_GROUPS = {"operating", "holding"}
# V1 rules cover UT + AZ (Part 14); entities may still be filed in any state (they stay
# dormant for rules), so we validate the code is a real US state to catch typos, not to
# restrict jurisdiction.
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV",
    "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}

# Fields a client may set on create/edit and how each is normalized. business_id is handled
# separately (needs a tenant-scoped existence check).
_TEXT_FIELDS = ("legal_name", "nickname", "description", "ein", "ownership")


class EntityError(ValueError):
    """A validation failure the router surfaces as a 400."""


def _mask_ein(ein: str | None) -> str | None:
    """Show the leading 2 + next 2 digits, bullet the rest (SPEC `ein_masked`: 87-41•••••).
    Reads never leak the full EIN; the edit form treats EIN as write-only."""
    if not ein:
        return None
    digits = [c for c in ein if c.isdigit()]
    if len(digits) < 5:
        return "•" * len(digits) if digits else None
    head = f"{''.join(digits[:2])}-{''.join(digits[2:4])}"
    return head + "•" * (len(digits) - 4)


def _tracking_ready(ent: LegalEntity) -> bool:
    return all(getattr(ent, f) is not None for f in TRACKING_FIELDS)


def _nudge(ent: LegalEntity) -> str | None:
    """The quiet inline prompt shown for a dormant entity. Hyphens/commas only (Part 9.6)."""
    if _tracking_ready(ent):
        return None
    missing = {
        "jurisdiction": "state",
        "entity_type": "entity type",
        "formation_date": "formation date",
    }
    need = [label for f, label in missing.items() if getattr(ent, f) is None]
    return f"Add {', '.join(need)} to start tracking filings."


def entity_out(ent: LegalEntity, business: Business | None = None) -> dict:
    """Read payload for the management screen. EIN is masked; raw value never leaves."""
    return {
        "id": str(ent.id),
        "legal_name": ent.legal_name,
        "nickname": ent.nickname,
        "description": ent.description,
        "entity_type": ent.entity_type,
        "jurisdiction": ent.jurisdiction,
        "formation_date": ent.formation_date.isoformat() if ent.formation_date else None,
        "ein_masked": _mask_ein(ent.ein),
        "has_ein": bool(ent.ein),
        "entity_group": ent.entity_group,
        "ownership": ent.ownership,
        "business_id": str(ent.business_id) if ent.business_id else None,
        "business_key": business.key if business else None,
        "business_name": business.name if business else None,
        "active": ent.active,
        "tracking_ready": _tracking_ready(ent),
        "nudge": _nudge(ent),
    }


async def _biz_by_id(s: AsyncSession, tenant_id) -> dict:
    return {b.id: b for b in (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id))).scalars().all()}


async def list_entities(s: AsyncSession, tenant_id, include_inactive: bool = False) -> dict:
    """All entities for the management screen, operating group first then holding, each
    group alphabetized. A brand-new tenant returns an empty list (the first-run state)."""
    q = select(LegalEntity).where(LegalEntity.tenant_id == tenant_id)
    if not include_inactive:
        q = q.where(LegalEntity.active.is_(True))
    ents = (await s.execute(q)).scalars().all()
    bmap = await _biz_by_id(s, tenant_id)
    ents.sort(key=lambda e: (0 if e.entity_group == "operating" else 1, e.legal_name.lower()))
    rows = [entity_out(e, bmap.get(e.business_id)) for e in ents]
    # The businesses a user may link an entity to (the optional tax-lifecycle tie). Shipped in
    # this binder-tab-gated payload so the entity form can populate its select without the
    # owner/admin-only /businesses call (the bookkeeper is a member).
    businesses = [{"id": str(b.id), "key": b.key, "name": b.name}
                  for b in sorted(bmap.values(), key=lambda b: b.sort_order)]
    return {
        "entities": rows,
        "businesses": businesses,
        "counts": {
            "total": len(rows),
            "operating": sum(1 for r in rows if r["entity_group"] == "operating"),
            "holding": sum(1 for r in rows if r["entity_group"] == "holding"),
            "dormant": sum(1 for r in rows if not r["tracking_ready"]),
        },
    }


# ── Validation + normalization ────────────────────────────────────────────────

def _norm_date(v) -> dt.date | None:
    if v in (None, ""):
        return None
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        raise EntityError("formation_date must be an ISO date (YYYY-MM-DD).")


def _validate_choice(field: str, value, allowed: set[str]) -> str | None:
    if value in (None, ""):
        return None
    v = str(value).strip().lower()
    if v not in allowed:
        raise EntityError(f"{field} must be one of: {', '.join(sorted(allowed))}.")
    return v


def _validate_state(value) -> str | None:
    if value in (None, ""):
        return None
    v = str(value).strip().upper()
    if v not in US_STATES:
        raise EntityError("jurisdiction must be a US state postal code (e.g. UT).")
    return v


async def _apply(s: AsyncSession, tenant_id, ent: LegalEntity, data: dict, *, creating: bool) -> dict:
    """Mutate `ent` from `data`, validating. Returns the dict of {field: new_value} that
    actually changed (for the audit detail). Only keys present in `data` are touched, so a
    PATCH leaves omitted fields alone; EIN blank on edit means 'keep existing'."""
    changed: dict = {}

    def _set(field, value):
        if getattr(ent, field) != value:
            changed[field] = value
        setattr(ent, field, value)

    if "legal_name" in data:
        name = (data["legal_name"] or "").strip()
        if not name:
            raise EntityError("legal_name is required.")
        # Unique per tenant (also DB-enforced) — pre-check for a friendly message.
        dupe = (await s.execute(select(func.count(LegalEntity.id)).where(
            LegalEntity.tenant_id == tenant_id, func.lower(LegalEntity.legal_name) == name.lower(),
            LegalEntity.id != ent.id))).scalar_one()
        if dupe:
            raise EntityError(f"An entity named '{name}' already exists.")
        _set("legal_name", name)
    elif creating:
        raise EntityError("legal_name is required.")

    for f in ("nickname", "description", "ownership"):
        if f in data:
            v = (data[f] or "").strip() or None
            _set(f, v)

    # EIN is write-only: only replace when a non-empty value is supplied.
    if "ein" in data and (data["ein"] or "").strip():
        _set("ein", data["ein"].strip())

    if "entity_type" in data:
        _set("entity_type", _validate_choice("entity_type", data["entity_type"], ENTITY_TYPES))
    if "jurisdiction" in data:
        _set("jurisdiction", _validate_state(data["jurisdiction"]))
    if "entity_group" in data:
        grp = _validate_choice("entity_group", data["entity_group"], ENTITY_GROUPS) or "operating"
        _set("entity_group", grp)
    if "formation_date" in data:
        _set("formation_date", _norm_date(data["formation_date"]))

    if "business_id" in data:
        bid = data["business_id"]
        if bid in (None, ""):
            _set("business_id", None)
        else:
            biz = (await s.execute(select(Business).where(
                Business.tenant_id == tenant_id, Business.id == bid))).scalar_one_or_none()
            if biz is None:
                raise EntityError("business_id does not match a business in this tenant.")
            _set("business_id", biz.id)

    # Audit detail omits raw EIN; record only that it changed.
    if "ein" in changed:
        changed["ein"] = "set"
    if "formation_date" in changed and changed["formation_date"] is not None:
        changed["formation_date"] = changed["formation_date"].isoformat()
    return changed


async def create_entity(s: AsyncSession, tenant_id, user, data: dict) -> LegalEntity:
    ent = LegalEntity(tenant_id=tenant_id, legal_name="", entity_group="operating")
    await _apply(s, tenant_id, ent, {"entity_group": "operating", **data}, creating=True)
    s.add(ent)
    await s.flush()
    audit(s, tenant_id, user.id, "binder.entity_created", "legal_entity", ent.id,
          {"legal_name": ent.legal_name, "tracking_ready": _tracking_ready(ent)})
    await s.commit()
    return ent


async def _get(s: AsyncSession, tenant_id, entity_id) -> LegalEntity | None:
    return (await s.execute(select(LegalEntity).where(
        LegalEntity.tenant_id == tenant_id, LegalEntity.id == entity_id))).scalar_one_or_none()


async def update_entity(s: AsyncSession, tenant_id, user, entity_id, data: dict) -> LegalEntity | None:
    ent = await _get(s, tenant_id, entity_id)
    if ent is None:
        return None
    changed = await _apply(s, tenant_id, ent, data, creating=False)
    if changed:
        audit(s, tenant_id, user.id, "binder.entity_updated", "legal_entity", ent.id,
              {"changed": sorted(changed.keys()), "tracking_ready": _tracking_ready(ent)})
    await s.commit()
    return ent


async def deactivate_entity(s: AsyncSession, tenant_id, user, entity_id) -> LegalEntity | None:
    """Hide from the matrix but retain documents + history (Part 5.0)."""
    ent = await _get(s, tenant_id, entity_id)
    if ent is None:
        return None
    ent.active = False
    audit(s, tenant_id, user.id, "binder.entity_deactivated", "legal_entity", ent.id, None)
    await s.commit()
    return ent


# ── The confirmation loop (SPEC Part 5.1) ─────────────────────────────────────
# The review queue lists pending ProposedObligation rows; a human confirms each into a
# tracked Obligation. This is the ONLY module path that constructs an Obligation, and it
# always stamps confirmed_by (Part 10 invariant 1) — extraction never does.

class ReviewError(ValueError):
    """A confirm/dismiss/complete validation failure the router surfaces as a 400."""


KIND_LABELS = {
    "annual_report": "Annual report", "registered_agent": "Registered agent",
    "insurance": "Insurance", "boi": "BOI / FinCEN", "federal_tax": "Federal tax",
    "state_tax": "State tax", "estimated_payments": "Estimated payments", "lease": "Lease",
}


def _fmt_date(d) -> str | None:
    if not d:
        return None
    if isinstance(d, str):
        d = _norm_date(d)
    return f"{d.strftime('%b')} {d.day}, {d.year}" if d else None


def _as_uuid(v):
    return v if isinstance(v, uuid.UUID) or v is None else uuid.UUID(str(v))


def _entity_conf_label(confidence: float, ambiguous: bool) -> str:
    if ambiguous:
        return "needs review"
    if confidence >= 0.95:
        return "exact name match"
    if confidence >= 0.8:
        return "strong match"
    if confidence >= 0.6:
        return "likely match"
    return "weak match"


def obligation_out(ob: Obligation, status: str) -> dict:
    return {
        "id": str(ob.id), "entity_id": str(ob.entity_id), "kind": ob.kind, "status": status,
        "applicable": ob.applicable, "cadence": ob.cadence, "lead_days": ob.lead_days,
        "due_date": ob.due_date.isoformat() if ob.due_date else None,
        "last_completed": ob.last_completed.isoformat() if ob.last_completed else None,
        "confirmed_by": str(ob.confirmed_by) if ob.confirmed_by else None,
        "source_document_id": str(ob.source_document_id) if ob.source_document_id else None,
        "notes": ob.notes,
    }


async def build_review(s: AsyncSession, tenant_id) -> dict:
    """The review queue (GET /binder/review). Payload mirrors the BinderReview mockup:
    stats + pending proposals + documents filed as evidence with no obligation."""
    props = (await s.execute(select(ProposedObligation).where(
        ProposedObligation.tenant_id == tenant_id)
        .order_by(ProposedObligation.created_at.asc()))).scalars().all()
    ent_map = {e.id: e for e in (await s.execute(select(LegalEntity).where(
        LegalEntity.tenant_id == tenant_id))).scalars().all()}
    docs = (await s.execute(select(BinderDocument).where(
        BinderDocument.tenant_id == tenant_id))).scalars().all()
    doc_map = {d.id: d for d in docs}

    today = dt.date.today()
    pending = [p for p in props if p.state == "pending"]
    proposals = []
    for p in pending:
        proposed = p.proposed or {}
        ent = ent_map.get(p.entity_id)
        doc = doc_map.get(p.document_id)
        ambiguous = bool(proposed.get("ambiguous"))
        proposals.append({
            "id": str(p.id),
            "document": doc.filename if doc else None,
            "via": doc.uploaded_via if doc else None,
            "kind": KIND_LABELS.get(p.kind, p.kind),
            "kind_key": p.kind,
            "entity": ent.legal_name if ent else (p.proposed or {}).get("entity_name_guess"),
            "entity_id": str(p.entity_id) if p.entity_id else None,
            "entity_confidence": _entity_conf_label(p.entity_confidence or 0.0, ambiguous),
            "method": p.method,
            "confidence": round(p.confidence, 2) if p.confidence is not None else None,
            "date": _fmt_date(proposed.get("due_date")),
            "cadence": (proposed.get("cadence") or "").capitalize() or None,
            "basis": p.basis,
            "fields": proposed.get("fields") or [],
            "ambiguous": ambiguous,
            "flavor": p.flavor,
            "candidates": p.entity_candidates or [],
        })

    # Documents filed as evidence that produced no tracked/awaiting obligation.
    live_states = {"pending", "confirmed"}
    doc_has_live = {d.id: False for d in docs}
    for p in props:
        if p.state in live_states:
            doc_has_live[p.document_id] = True
    filed = [{"filename": d.filename,
              "entity": (ent_map.get(d.entity_id).legal_name if ent_map.get(d.entity_id) else None),
              "note": f"Filed under {d.category.replace('_', ' ').title()}."}
             for d in docs if d.extracted is not None and not doc_has_live.get(d.id, False)]

    stats = {
        "awaiting": len(pending),
        "confirmed_this_pass": sum(1 for p in props if p.state == "confirmed"
                                   and p.resolved_at and p.resolved_at.date() == today),
        "entity_unclear": sum(1 for p in proposals if p["ambiguous"]),
        "gaps": sum(1 for p in pending if p.flavor == "gap"),
    }
    return {"stats": stats, "proposals": proposals, "filed_no_obligation": filed}


async def _proposal(s, tenant_id, proposal_id) -> ProposedObligation | None:
    return (await s.execute(select(ProposedObligation).where(
        ProposedObligation.tenant_id == tenant_id,
        ProposedObligation.id == proposal_id))).scalar_one_or_none()


async def _obligation(s, tenant_id, obligation_id) -> Obligation | None:
    return (await s.execute(select(Obligation).where(
        Obligation.tenant_id == tenant_id, Obligation.id == obligation_id))).scalar_one_or_none()


async def confirm_proposal(s: AsyncSession, tenant_id, user, proposal_id,
                           entity_id=None, edits: dict | None = None) -> dict | None:
    """Confirm a proposal into a tracked Obligation. Renewal advances the existing obligation;
    otherwise upserts on (entity, kind). Always sets confirmed_by. An ambiguous proposal
    requires an explicit entity pick. Returns None if the proposal isn't found."""
    p = await _proposal(s, tenant_id, proposal_id)
    if p is None:
        return None
    if p.state != "pending":
        raise ReviewError(f"Proposal already {p.state}.")
    proposed = p.proposed or {}
    edits = edits or {}
    now = dt.datetime.now(dt.timezone.utc)
    today = dt.date.today()

    # Resolve the entity: explicit pick (body/edits) beats the proposal's best guess. An
    # ambiguous proposal cannot be confirmed without an explicit pick (never a silent commit).
    picked = entity_id or edits.get("entity_id")
    if proposed.get("ambiguous") and picked is None:
        raise ReviewError("This proposal's entity is ambiguous; entity_id is required to confirm.")
    eid = _as_uuid(picked or p.entity_id)
    if eid is None:
        raise ReviewError("No entity to attach this obligation to; provide entity_id.")
    ent = (await s.execute(select(LegalEntity).where(
        LegalEntity.tenant_id == tenant_id, LegalEntity.id == eid))).scalar_one_or_none()
    if ent is None:
        raise ReviewError("entity_id does not match an entity in this tenant.")

    kind = edits.get("kind") or p.kind
    due_date = _norm_date(edits["due_date"]) if "due_date" in edits else _norm_date(proposed.get("due_date"))
    lead_days = int(edits.get("lead_days") or proposed.get("lead_days") or 45)
    cadence = edits.get("cadence") or proposed.get("cadence") or "annual"

    ob = None
    if p.flavor == "renewal" and p.renewal_of_id:                 # advance the existing obligation
        ob = await _obligation(s, tenant_id, p.renewal_of_id)
        if ob is not None:
            ob.last_completed = today
            if due_date:
                ob.due_date = due_date
            ob.last_reminded_stage = ob.last_reminded_at = None
            ob.source_document_id = p.document_id
            ob.confirmed_by, ob.last_confirmed_at = user.id, now
    if ob is None:                                                # upsert on (entity, kind)
        ob = (await s.execute(select(Obligation).where(
            Obligation.tenant_id == tenant_id, Obligation.entity_id == eid,
            Obligation.kind == kind))).scalar_one_or_none()
        if ob is None:
            ob = Obligation(tenant_id=tenant_id, entity_id=eid, kind=kind)
            s.add(ob)
        ob.jurisdiction = proposed.get("jurisdiction")
        ob.applicable = bool(proposed.get("applicable", True))
        ob.due_date, ob.cadence, ob.lead_days = due_date, cadence, lead_days
        ob.source_document_id = p.document_id
        ob.rule_id = _as_uuid(proposed.get("rule_id")) if proposed.get("rule_id") else None
        ob.confirmed_by, ob.last_confirmed_at = user.id, now      # invariant 1: always set

    doc = await s.get(BinderDocument, p.document_id)
    if doc is not None and doc.entity_id is None:                 # link evidence to the entity
        doc.entity_id = eid

    p.state, p.resolved_by, p.resolved_at, p.entity_id = "confirmed", user.id, now, eid
    await s.flush()
    status = compute_status(applicable=ob.applicable, kind=ob.kind, due_date=ob.due_date,
                            lead_days=ob.lead_days, today=today)
    audit(s, tenant_id, user.id, "binder.obligation_confirmed", "obligation", ob.id,
          {"kind": ob.kind, "flavor": p.flavor, "status": status})
    await s.commit()
    return {"obligation": ob, "status": status}


async def dismiss_proposal(s: AsyncSession, tenant_id, user, proposal_id) -> ProposedObligation | None:
    """Dismiss a proposal. The document stays filed as evidence."""
    p = await _proposal(s, tenant_id, proposal_id)
    if p is None:
        return None
    if p.state != "pending":
        raise ReviewError(f"Proposal already {p.state}.")
    p.state, p.resolved_by, p.resolved_at = "dismissed", user.id, dt.datetime.now(dt.timezone.utc)
    audit(s, tenant_id, user.id, "binder.proposal_dismissed", "proposed_obligation", p.id,
          {"kind": p.kind})
    await s.commit()
    return p


async def complete_obligation(s: AsyncSession, tenant_id, user, obligation_id) -> Obligation | None:
    """Mark done: stamp last_completed, roll due_date forward by cadence, clear reminders."""
    ob = await _obligation(s, tenant_id, obligation_id)
    if ob is None:
        return None
    today = dt.date.today()
    ob.last_completed = today
    ob.due_date = roll_forward(ob.due_date, ob.cadence)
    ob.last_reminded_stage = ob.last_reminded_at = None
    audit(s, tenant_id, user.id, "binder.obligation_completed", "obligation", ob.id,
          {"cadence": ob.cadence, "next_due": ob.due_date.isoformat() if ob.due_date else None})
    await s.commit()
    return ob


async def set_applicability(s: AsyncSession, tenant_id, user, obligation_id, applicable: bool) -> Obligation | None:
    ob = await _obligation(s, tenant_id, obligation_id)
    if ob is None:
        return None
    ob.applicable = bool(applicable)
    audit(s, tenant_id, user.id, "binder.obligation_applicability", "obligation", ob.id,
          {"applicable": ob.applicable})
    await s.commit()
    return ob


async def edit_obligation(s: AsyncSession, tenant_id, user, obligation_id, fields: dict) -> Obligation | None:
    ob = await _obligation(s, tenant_id, obligation_id)
    if ob is None:
        return None
    changed = []
    if "due_date" in fields:
        ob.due_date = _norm_date(fields["due_date"]); changed.append("due_date")
    if "lead_days" in fields and fields["lead_days"] is not None:
        ob.lead_days = int(fields["lead_days"]); changed.append("lead_days")
    if "cadence" in fields and fields["cadence"]:
        ob.cadence = fields["cadence"]; changed.append("cadence")
    if "notes" in fields:
        ob.notes = (fields["notes"] or "").strip() or None; changed.append("notes")
    if changed:
        audit(s, tenant_id, user.id, "binder.obligation_edited", "obligation", ob.id,
              {"changed": changed})
    await s.commit()
    return ob


VALID_OBLIGATION_KINDS = set(KIND_LABELS)          # kinds a user may manually configure


async def upsert_obligation(s: AsyncSession, tenant_id, user, entity_id, kind: str,
                            fields: dict) -> Obligation | None:
    """Manually configure an obligation for a kind no document proposed yet (or edit the existing
    one). Upserts on (entity, kind). Records confirmed_by so invariant 1 holds for a manual add
    too; source_document_id stays null (no evidence). Returns None if the entity isn't this
    tenant's."""
    if kind not in VALID_OBLIGATION_KINDS:
        raise EntityError(f"kind must be one of: {', '.join(sorted(VALID_OBLIGATION_KINDS))}")
    ent = await _get(s, tenant_id, entity_id)
    if ent is None:
        return None
    now = dt.datetime.now(dt.timezone.utc)
    ob = (await s.execute(select(Obligation).where(
        Obligation.tenant_id == tenant_id, Obligation.entity_id == entity_id,
        Obligation.kind == kind))).scalar_one_or_none()
    created = ob is None
    if ob is None:
        ob = Obligation(tenant_id=tenant_id, entity_id=entity_id, kind=kind,
                        jurisdiction=ent.jurisdiction)
        s.add(ob)
    if "due_date" in fields:
        ob.due_date = _norm_date(fields["due_date"])
    if fields.get("cadence"):
        ob.cadence = fields["cadence"]
    if fields.get("lead_days") is not None:
        ob.lead_days = int(fields["lead_days"])
    if fields.get("applicable") is not None:
        ob.applicable = bool(fields["applicable"])
    if "notes" in fields:
        ob.notes = (fields["notes"] or "").strip() or None
    ob.confirmed_by, ob.last_confirmed_at = user.id, now       # invariant 1: records a user
    audit(s, tenant_id, user.id, "binder.obligation_manual_set", "obligation", ob.id,
          {"kind": kind, "created": created})
    await s.commit()
    return ob


def obligation_status(ob: Obligation, today: dt.date | None = None) -> str:
    return compute_status(applicable=ob.applicable, kind=ob.kind, due_date=ob.due_date,
                          lead_days=ob.lead_days, today=today or dt.date.today())


# ── The matrix + entity binder (SPEC Part 6 / 8) ─────────────────────────────
# Fixed obligation-kind column set for the matrix (lease is evidence-only, Part 14).
MATRIX_KINDS = ["annual_report", "registered_agent", "insurance", "boi",
                "federal_tax", "state_tax", "estimated_payments"]
CATEGORY_ORDER = ["formation", "insurance", "tax", "lease", "registered_agent", "estate", "other"]
_STATUS_RANK = {"overdue": 4, "due_soon": 3, "in_progress": 2, "current": 1,
                "not_applicable": 0, "none": -1}


def _cat_label(c: str) -> str:
    return c.replace("_", " ").title()


def _cell(status: str, due_date, today: dt.date) -> dict:
    """A matrix cell: status + a short label (Part 8)."""
    if status == "none":
        return {"status": "none", "label": "—"}
    if status == "not_applicable":
        return {"status": "not_applicable", "label": "n/a"}
    if status == "in_progress":
        return {"status": "in_progress", "label": "books"}
    if status == "overdue":
        return {"status": "overdue", "label": "overdue"}
    if status == "due_soon":
        days = (due_date - today).days if due_date else None
        return {"status": "due_soon", "label": (f"{days}d" if days is not None else "soon")}
    return {"status": "current", "label": (due_date.strftime("%b") if due_date else "current")}


async def _books_pending_map(s: AsyncSession, tenant_id, today: dt.date) -> dict:
    """business_id -> is the tax-year Books close still pending (drives federal/state tax
    in_progress, Part 6 #1). Simplified v1: a business with a QBO/Books integration whose
    books for the current calendar year are NOT yet closed is 'pending'; once it closes a
    period this year, tax obligations fall to pure date math. (Precise tax-year mapping is a
    later refinement.) The one cross-module read: Books' ClosePeriod, read-only."""
    books_biz = {bid for (bid,) in (await s.execute(select(Integration.business_id).where(
        Integration.tenant_id == tenant_id, Integration.provider == "qbo",
        Integration.business_id.isnot(None)))).all()}
    ystart, yend = dt.date(today.year, 1, 1), dt.date(today.year + 1, 1, 1)
    closed = {bid for (bid,) in (await s.execute(select(ClosePeriod.business_id).where(
        ClosePeriod.tenant_id == tenant_id, ClosePeriod.status == "closed",
        ClosePeriod.period >= ystart, ClosePeriod.period < yend))).all()}
    return {bid: (bid not in closed) for bid in books_biz}


async def build_matrix(s: AsyncSession, tenant_id, today: dt.date | None = None) -> dict:
    """The obligations matrix (GET /binder): one cell per (entity, kind), grouped
    operating/holding, plus attention flags."""
    today = today or dt.date.today()
    ents = (await s.execute(select(LegalEntity).where(
        LegalEntity.tenant_id == tenant_id, LegalEntity.active.is_(True)))).scalars().all()
    obs = (await s.execute(select(Obligation).where(Obligation.tenant_id == tenant_id))).scalars().all()
    by_entity: dict = {}
    for ob in obs:
        by_entity.setdefault(ob.entity_id, {})[ob.kind] = ob
    pending_map = await _books_pending_map(s, tenant_id, today)

    biz = await _biz_by_id(s, tenant_id)
    overdue = due_soon = 0
    attention = []
    groups = {"operating": [], "holding": []}
    for e in sorted(ents, key=lambda x: x.legal_name.lower()):
        books_pending = pending_map.get(e.business_id, False) if e.business_id else False
        cells, worst, open_ct = {}, "none", 0
        for kind in MATRIX_KINDS:
            ob = by_entity.get(e.id, {}).get(kind)
            if ob is None:
                cells[kind] = {"status": "none", "label": "—"}
                continue
            st = compute_status(applicable=ob.applicable, kind=ob.kind, due_date=ob.due_date,
                                lead_days=ob.lead_days, today=today,
                                books_pending=(books_pending and ob.kind in TAX_KINDS))
            cells[kind] = _cell(st, ob.due_date, today)
            if st == "overdue":
                overdue += 1; open_ct += 1
            elif st == "due_soon":
                due_soon += 1; open_ct += 1
            if _STATUS_RANK.get(st, -1) > _STATUS_RANK.get(worst, -1):
                worst = st
        business = biz.get(e.business_id) if e.business_id else None
        # The list view (Operating/Holding) renders from these same rows, so carry the display
        # fields + a rollup (worst status + count of open items) the mockup's list needs.
        row = {"id": str(e.id), "name": e.legal_name, "nickname": e.nickname, "cells": cells,
               "worst": worst, "open": open_ct, "ein_masked": _mask_ein(e.ein),
               "ownership": e.ownership, "business_name": business.name if business else None,
               "entity_group": "holding" if e.entity_group == "holding" else "operating",
               "tracking_ready": _tracking_ready(e)}
        groups["holding" if e.entity_group == "holding" else "operating"].append(row)
        if worst in ("overdue", "due_soon"):
            attention.append({"id": str(e.id), "name": e.legal_name, "open": open_ct, "worst": worst})

    attention.sort(key=lambda a: (a["worst"] != "overdue", -a["open"]))
    return {
        "updated_at": today.isoformat(),
        "groups": [{"group": "operating", "entities": groups["operating"]},
                   {"group": "holding", "entities": groups["holding"]}],
        "flags": {"overdue": overdue, "due_soon": due_soon, "attention": attention},
        "kinds": [{"key": k, "label": KIND_LABELS.get(k, k)} for k in MATRIX_KINDS],
    }


# Documents in the entity detail always show these category sections (even when empty), so a
# fresh entity shows the full shape of what it should collect, not a blank card. "other" is
# appended only when it actually holds something.
DETAIL_DOC_CATEGORIES = ["formation", "registered_agent", "insurance", "tax", "lease", "estate"]


async def build_entity_binder(s: AsyncSession, tenant_id, entity_id, today: dt.date | None = None) -> dict | None:
    """One entity's binder (GET /binder/entity/{id}): attributes, the FULL obligation set (every
    MATRIX_KIND, with a 'not configured' placeholder where nothing is tracked yet), and documents
    grouped into the full category tree (empty sections included). The placeholders are what let
    a user configure an obligation for a kind no document has proposed yet (invariant 1 still
    holds: creating one records a user)."""
    ent = await _get(s, tenant_id, entity_id)
    if ent is None:
        return None
    today = today or dt.date.today()
    business = (await _biz_by_id(s, tenant_id)).get(ent.business_id) if ent.business_id else None
    books_pending = (await _books_pending_map(s, tenant_id, today)).get(ent.business_id, False) if ent.business_id else False
    obs = (await s.execute(select(Obligation).where(
        Obligation.tenant_id == tenant_id, Obligation.entity_id == entity_id))).scalars().all()
    docs = (await s.execute(select(BinderDocument).where(
        BinderDocument.tenant_id == tenant_id, BinderDocument.entity_id == entity_id)
        .order_by(BinderDocument.created_at.desc()))).scalars().all()
    doc_by_id = {d.id: d for d in docs}
    ob_by_kind = {ob.kind: ob for ob in obs}

    obligations = []
    for kind in MATRIX_KINDS:
        ob = ob_by_kind.get(kind)
        base = {"kind": kind, "kind_label": KIND_LABELS.get(kind, kind)}
        if ob is None:                                   # not configured yet — an editable stub
            obligations.append({**base, "id": None, "configured": False, "status": "none",
                                "label": "not configured", "due_date": None, "cadence": None,
                                "lead_days": None, "applicable": None, "source_document": None,
                                "source_document_id": None, "notes": None})
            continue
        st = compute_status(applicable=ob.applicable, kind=ob.kind, due_date=ob.due_date,
                            lead_days=ob.lead_days, today=today,
                            books_pending=(books_pending and ob.kind in TAX_KINDS))
        src = doc_by_id.get(ob.source_document_id)
        obligations.append({
            **base, "id": str(ob.id), "configured": True,
            "status": st, "label": _cell(st, ob.due_date, today)["label"],
            "due_date": ob.due_date.isoformat() if ob.due_date else None,
            "cadence": ob.cadence, "lead_days": ob.lead_days, "applicable": ob.applicable,
            "source_document": src.filename if src else None,
            "source_document_id": str(ob.source_document_id) if ob.source_document_id else None,
            "notes": ob.notes,
        })

    by_cat: dict = {}
    for d in docs:
        by_cat.setdefault(d.category, []).append(
            {"id": str(d.id), "filename": d.filename, "category_key": d.category,
             "can_preview": bool(d.storage_ref), "alert": False})
    cats = DETAIL_DOC_CATEGORIES + [c for c in CATEGORY_ORDER
                                    if c not in DETAIL_DOC_CATEGORIES and c in by_cat]
    documents = [{"category": _cat_label(c), "category_key": c, "items": by_cat.get(c, [])}
                 for c in cats]
    return {
        "entity": {"id": str(ent.id), "name": ent.legal_name, "nickname": ent.nickname,
                   "type": ent.entity_type, "jurisdiction": ent.jurisdiction,
                   "ownership": ent.ownership, "ein_masked": _mask_ein(ent.ein),
                   "has_ein": bool(ent.ein),
                   "formation_date": ent.formation_date.isoformat() if ent.formation_date else None,
                   "description": ent.description,
                   "entity_group": ent.entity_group,
                   "business_id": str(ent.business_id) if ent.business_id else None,
                   "business_name": business.name if business else None,
                   "tracking_ready": _tracking_ready(ent)},
        "obligations": obligations,
        "documents": documents,
    }


# ── Rules engine surface (SPEC Part 4 / 8; the Step-5 codeable bit) ───────────
# The rules are reference data with a last_verified stamp. A rule older than
# BINDER_RULE_STALE_MONTHS surfaces here so a changed rule (BOI is the cautionary tale)
# gets re-checked rather than silently misfiring.

async def build_rules(s: AsyncSession, tenant_id, today: dt.date | None = None) -> dict:
    """List the jurisdiction rules that apply to this tenant (shared system rules + any tenant
    overrides), each with its freshness (stale = last_verified older than the configured window)."""
    today = today or dt.date.today()
    rows = (await s.execute(select(JurisdictionRule).where(
        or_(JurisdictionRule.tenant_id == tenant_id, JurisdictionRule.tenant_id.is_(None)))
    )).scalars().all()
    threshold = _add_months(today, -settings.BINDER_RULE_STALE_MONTHS)
    out = []
    for r in sorted(rows, key=lambda x: (x.kind, x.jurisdiction or "", x.entity_type or "")):
        stale = (r.last_verified is None) or (r.last_verified < threshold)
        out.append({
            "id": str(r.id), "kind": r.kind, "kind_label": KIND_LABELS.get(r.kind, r.kind),
            "jurisdiction": r.jurisdiction, "entity_type": r.entity_type,
            "cadence": r.cadence, "derivation": r.derivation,
            "last_verified": r.last_verified.isoformat() if r.last_verified else None,
            "stale": stale, "source_note": r.source_note, "active": r.active,
            "scope": "tenant" if r.tenant_id else "system",
        })
    return {"rules": out, "stale_count": sum(1 for x in out if x["stale"]),
            "stale_after_months": settings.BINDER_RULE_STALE_MONTHS}


async def verify_rule(s: AsyncSession, tenant_id, user, rule_id, today: dt.date | None = None) -> JurisdictionRule | None:
    """Stamp last_verified=today on a rule (owner/admin action). A tenant may verify a shared
    system rule (single-tenant Spring for now) or one of its own overrides — never another
    tenant's rule."""
    today = today or dt.date.today()
    r = (await s.execute(select(JurisdictionRule).where(
        JurisdictionRule.id == rule_id))).scalar_one_or_none()
    if r is None or (r.tenant_id is not None and r.tenant_id != tenant_id):
        return None
    r.last_verified = today
    audit(s, tenant_id, user.id, "binder.rule_verified", "jurisdiction_rule", r.id,
          {"kind": r.kind, "jurisdiction": r.jurisdiction})
    await s.commit()
    return r


# ── Assistant context (SPEC Part 9.5) ─────────────────────────────────────────
async def build_assistant_summary(s: AsyncSession, tenant_id, today: dt.date | None = None) -> dict:
    """Compact Binder overview for the 'Ask' panel: matrix flags, review counts, and the
    flagged obligations — so 'what's overdue in the Binder' is answerable from the summary, with
    drill keys for the records behind it."""
    today = today or dt.date.today()
    m = await build_matrix(s, tenant_id, today)
    rv = await build_review(s, tenant_id)
    flagged = []
    for g in m["groups"]:
        for e in g["entities"]:
            for kind, cell in e["cells"].items():
                if cell["status"] in ("overdue", "due_soon"):
                    flagged.append({"entity": e["name"], "obligation": KIND_LABELS.get(kind, kind),
                                    "status": cell["status"], "label": cell["label"]})
    return {"flags": m["flags"], "review": rv["stats"], "flagged_obligations": flagged,
            "drill": {"matrix": "binder_matrix", "review": "binder_review"}}
