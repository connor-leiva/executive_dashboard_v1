"""Acumyn Binder — jurisdiction rules seed (SPEC-binder-module Part 4).

The rules engine turns an anchor (formation date, entity type, fiscal year) into a
compliance schedule. These rows are *reference data*, not tenant data: a seeded rule
carries ``tenant_id = None`` (a shared system default); a tenant may later add its own
override rows. This is the one Binder table that is deliberately not tenant-scoped,
because jurisdiction rules are not tenant-specific.

Scope is hard-limited to what Spring actually has today — Utah + Arizona + the federal
entity-type tax calendar + BOI (SPEC "v1 seed rules"). Every rule is stamped
``last_verified = today`` with a ``source_note``. That freshness stamp is not decoration:
BOI's filing requirement was legally turbulent through 2024-2025, which is the canonical
reason a human must be able to see when a rule was last checked and re-verify it. Any rule
older than ``BINDER_RULE_STALE_MONTHS`` surfaces in an admin "stale rules" view.

IMPORTANT — this seed populates ``JurisdictionRule`` ONLY. It never creates a
``LegalEntity`` row: legal entities are tenant data that users create through the frontend
(Part 5.0), one at a time, never seeded or shipped in a migration.

Note on one deliberate deviation from the SPEC's shorthand: the Arizona *corporation*
annual report is seeded with derivation ``manual`` rather than ``fixed_date``. Arizona
assigns each corporation's annual-report due date from its incorporation anniversary (via
the Corporation Commission), so there is no single fixed calendar date to derive — a
fabricated one would silently misfire, exactly what ``last_verified``/``source_note`` exist
to prevent. A human sets the ACC-assigned date per entity; gap detection flags a corp with
none on file. This is flagged in that rule's ``source_note``.
"""
from __future__ import annotations

import calendar
import datetime as dt

from sqlalchemy import select, or_

from ..models import JurisdictionRule


# Standard federal estimated-payment due dates (Q4 lands the following January).
_ESTIMATED_QUARTERS = [
    {"month": 4, "day": 15},
    {"month": 6, "day": 15},
    {"month": 9, "day": 15},
    {"month": 1, "day": 15, "year_offset": 1},
]

# The federal income-tax calendar keyed to tax classification (SPEC Part 4): S-corp and
# partnership returns are due month 3 day 15; C-corp and individual (sole-prop / disregarded
# single-member LLC) due month 4 day 15; a filed extension adds six months. The lookup engine
# (a later step) maps an entity's type + election onto one of these classifications.
_FEDERAL_CALENDAR = {
    "s_corp": {"month": 3, "day": 15},
    "partnership": {"month": 3, "day": 15},
    "c_corp": {"month": 4, "day": 15},
    "individual": {"month": 4, "day": 15},
}
_TODAY_SOURCE = "IRS filing-deadline calendar (Form 1120/1120-S/1065/1040)."


# Each entry is a system JurisdictionRule (tenant_id=None). last_verified is stamped at
# seed time. The natural identity used for idempotency is (jurisdiction, entity_type, kind).
SYSTEM_RULES: list[dict] = [
    # ── Annual reports ──────────────────────────────────────────────────────
    {
        "jurisdiction": "UT", "entity_type": "llc", "kind": "annual_report",
        "cadence": "annual", "derivation": "anniversary_month_end", "params": None,
        "source_note": ("Utah LLC annual report renews by the last day of the entity's "
                        "formation-anniversary month (Utah Div. of Corporations)."),
    },
    {
        "jurisdiction": "AZ", "entity_type": "llc", "kind": "annual_report",
        "cadence": "none", "derivation": "not_applicable", "params": None,
        "source_note": ("Arizona does NOT require LLC annual reports (A.R.S. Title 29). "
                        "Seeded explicitly so the engine renders 'n/a' rather than a "
                        "false obligation — the reason jurisdiction nuance matters."),
    },
    {
        # jurisdiction=AZ, entity_type=None is the AZ non-LLC fallback (the AZ-LLC rule
        # above is the specific override). manual, not fixed_date — see module docstring.
        "jurisdiction": "AZ", "entity_type": None, "kind": "annual_report",
        "cadence": "annual", "derivation": "manual", "params": None,
        "source_note": ("Arizona corporations file an annual report due on the ACC-assigned "
                        "anniversary of incorporation, so the date is set per entity and "
                        "confirmed by a human (no reliable derived date). Confirm S-corp vs "
                        "C-corp specifics with CPA."),
    },
    # ── Income tax (federal + state mirror) ─────────────────────────────────
    {
        "jurisdiction": None, "entity_type": None, "kind": "federal_tax",
        "cadence": "annual", "derivation": "entity_type_calendar",
        "params": {"calendar": _FEDERAL_CALENDAR, "extension_months": 6},
        "source_note": _TODAY_SOURCE,
    },
    {
        "jurisdiction": "UT", "entity_type": None, "kind": "state_tax",
        "cadence": "annual", "derivation": "entity_type_calendar",
        "params": {"calendar": _FEDERAL_CALENDAR, "extension_months": 6},
        "source_note": ("Utah income-tax filing mirrors the federal cadence for v1; "
                        "flagged for CPA confirmation of Utah-specific due dates."),
    },
    {
        "jurisdiction": "AZ", "entity_type": None, "kind": "state_tax",
        "cadence": "annual", "derivation": "entity_type_calendar",
        "params": {"calendar": _FEDERAL_CALENDAR, "extension_months": 6},
        "source_note": ("Arizona income-tax filing mirrors the federal cadence for v1; "
                        "flagged for CPA confirmation of Arizona-specific due dates."),
    },
    # ── Estimated payments ──────────────────────────────────────────────────
    {
        # Applicability is per entity (operating entities on; no-income holding entities
        # get a not_applicable obligation) — decided at confirmation, not in the rule.
        "jurisdiction": None, "entity_type": None, "kind": "estimated_payments",
        "cadence": "quarterly", "derivation": "fixed_date",
        "params": {"quarters": _ESTIMATED_QUARTERS},
        "source_note": ("Federal quarterly estimated-tax due dates (Apr 15 / Jun 15 / "
                        "Sep 15 / Jan 15). Applicable to income-earning entities; "
                        "not applicable to no-income holding entities."),
    },
    # ── BOI / FinCEN ────────────────────────────────────────────────────────
    {
        "jurisdiction": None, "entity_type": None, "kind": "boi",
        "cadence": "one_time", "derivation": "manual", "params": None,
        "source_note": ("BOI / FinCEN beneficial-ownership report — one-time, human-set (no "
                        "reliable derived date). The filing requirement was legally turbulent "
                        "through 2024-2025 (enjoined, reinstated, then narrowed to foreign "
                        "entities), which is the canonical reason last_verified exists: "
                        "RE-VERIFY before relying on this. Gap detection flags entities with "
                        "no BOI document on file; the obligation itself is human-confirmed."),
    },
]


async def seed_jurisdiction_rules(session) -> int:
    """Idempotently populate the shared system JurisdictionRule rows.

    Matches on (jurisdiction, entity_type, kind) among rows with tenant_id IS NULL, so
    re-running never duplicates and never touches a tenant's own override rows. Returns the
    number of rules inserted this call (0 on a re-run). Does NOT commit — the caller owns the
    transaction (seed() commits once at the end; tests can roll back).
    """
    existing = (await session.execute(
        select(JurisdictionRule.jurisdiction, JurisdictionRule.entity_type, JurisdictionRule.kind)
        .where(JurisdictionRule.tenant_id.is_(None))
    )).all()
    have = {(j, e, k) for (j, e, k) in existing}

    today = dt.date.today()
    inserted = 0
    for rule in SYSTEM_RULES:
        key = (rule["jurisdiction"], rule["entity_type"], rule["kind"])
        if key in have:
            continue
        session.add(JurisdictionRule(tenant_id=None, last_verified=today, active=True, **rule))
        inserted += 1
    if inserted:
        await session.flush()
    return inserted


# ── Rule lookup + derivation (SPEC Part 4) ───────────────────────────────────
# The engine: given (jurisdiction, entity_type, kind) find the governing rule (most specific
# first, a tenant override beating the system default), then turn an anchor date into a
# schedule. Pure/deterministic — the extraction step feeds anchors in, the confirm loop and
# status math read the results out.

# Return-form -> federal tax classification (the entity_type_calendar key). A document's
# return type is the reliable signal for the tax cadence; an LLC's own type is not (its
# federal classification depends on election), so tax derivation is READ from the return.
RETURN_TYPE_CLASS = {
    "1120-s": "s_corp", "1120s": "s_corp",
    "1065": "partnership",
    "1120": "c_corp",
    "1040": "individual",
}


async def lookup_rule(session, tenant_id, *, jurisdiction, entity_type, kind) -> JurisdictionRule | None:
    """The governing rule for (jurisdiction, entity_type, kind). Considers this tenant's
    overrides plus system defaults (tenant_id NULL), and wildcard rows (NULL jurisdiction /
    entity_type). Ranks a tenant rule over a system one, and a more specific match over a
    wildcard, so the returned rule is the single best fit (or None)."""
    rows = (await session.execute(select(JurisdictionRule).where(
        JurisdictionRule.kind == kind,
        JurisdictionRule.active.is_(True),
        or_(JurisdictionRule.tenant_id == tenant_id, JurisdictionRule.tenant_id.is_(None)),
        or_(JurisdictionRule.jurisdiction == jurisdiction, JurisdictionRule.jurisdiction.is_(None)),
        or_(JurisdictionRule.entity_type == entity_type, JurisdictionRule.entity_type.is_(None)),
    ))).scalars().all()
    if not rows:
        return None
    rows.sort(key=lambda r: (bool(r.tenant_id), r.jurisdiction is not None,
                             r.entity_type is not None), reverse=True)
    return rows[0]


def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _next_on_or_after(today: dt.date, month: int, day: int) -> dt.date:
    """The next occurrence of (month, day) on or after `today` (this year, else next).
    Clamps the day into the month so Feb 29 etc. never raises."""
    for year in (today.year, today.year + 1):
        d = dt.date(year, month, min(day, _last_day(year, month)))
        if d >= today:
            return d
    return dt.date(today.year + 1, month, min(day, _last_day(today.year + 1, month)))


def derive(rule: JurisdictionRule, *, today: dt.date, anchor: dt.date | None = None,
           classification: str | None = None) -> dict:
    """Turn a rule + anchor into a schedule: {due_date, cadence, lead_days, applicable}.

    `anchor` is the formation date (anniversary rules); `classification` is the tax class
    (entity_type_calendar). A rule that cannot derive a date without a missing input returns
    due_date=None (dormant) rather than guessing. `not_applicable` -> applicable False."""
    out = {"due_date": None, "cadence": rule.cadence, "lead_days": rule.lead_days_default,
           "applicable": True}
    p = rule.params or {}
    d = rule.derivation

    if d == "not_applicable":
        out["applicable"] = False
    elif d == "manual":
        pass                                        # human sets the date; no derivation
    elif d == "anniversary_month_end":
        if anchor is not None:
            m = anchor.month
            year = today.year
            due = dt.date(year, m, _last_day(year, m))
            if due < today:
                due = dt.date(year + 1, m, _last_day(year + 1, m))
            out["due_date"] = due
    elif d == "fixed_date":
        quarters = p.get("quarters")
        if quarters:                                # e.g. estimated payments -> next quarter
            cands = [_next_on_or_after(today, q["month"], q["day"]) for q in quarters]
            out["due_date"] = min(cands)
        elif p.get("month"):
            out["due_date"] = _next_on_or_after(today, p["month"], p["day"])
    elif d == "entity_type_calendar":
        entry = (p.get("calendar") or {}).get(classification or "")
        if entry:
            out["due_date"] = _next_on_or_after(today, entry["month"], entry["day"])
    return out
