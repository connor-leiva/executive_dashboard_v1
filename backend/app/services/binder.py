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

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import LegalEntity, Business
from .audit import audit

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
