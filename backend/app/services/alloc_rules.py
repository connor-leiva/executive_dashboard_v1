"""Applied allocation rules and their versions (ALLOCATION-RULES-SPEC 4.1, 4.2, 5.2, Phase 3).

APPEND-ONLY, and that is the whole point of this module. `coa_alloc.update_rule` assigns every
field the caller sent, in a loop, and `coa_alloc.delete_rule` removes the row outright; both are
fine for an `observed` rule, which describes something the books already did, and both make a
March restatement impossible for an `applied` one. A split is not one number. It is three or
four entities whose shares move, sometimes retroactively, and every closed month has to stay
reconstructable as it stood.

So the rule keeps its identity and the terms are appended. Nothing here is ever UPDATEd except
`is_current` and `superseded_by`, on the version being replaced, which is the same narrow
exception `SalesCall.is_current` and `VendorBankAccount.superseded_by` already use.

The commissions module took the other road and freezes the rate onto each payment row as its
month closes. That is right for one rate on one payment and cannot express this (D2, amendment
25).

Validation refuses rather than defaults. A stored split that cannot resolve is a bug, not a
state, so `split_not_100` is a refusal here and not a pending reason in the engine (amendment
16) -- `books_cleanup.pct` already refuses at save with a plain sentence, and this follows it.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AllocationRule, AllocationRuleVersion, Business
from .audit import audit
from .coa_map import normalize_fqn

ONE = Decimal("1")
# Shares are compared at four decimal places. Count and traced shares are derived by division
# and will not land on exact halves, and a split typed as thirds cannot sum to 1 in decimal at
# any precision -- so the tolerance is the precision, declared once, rather than an epsilon
# sprinkled at each comparison.
SHARE_PLACES = Decimal("0.0001")

# Tier is the reader's vocabulary and `method` is the machine's. Both are carried so colour is
# never the only signal that distinguishes them (D15, amendment 23).
TIERS = {1: "Direct", 2: "Traced", 3: "Driven", 4: "Retained"}

# method -> tier. `ended` has no tier: the rule claims nothing, which is not a kind of claim.
METHOD_TIER: dict[str, int | None] = {
    "direct": 1, "traced": 2, "driven": 3, "split": 3, "retained": 4, "ended": None,
}
SCOPE_KINDS = ("account", "program", "vendor")
REACHES = ("forward", "from_date", "retro")

# Which methods carry which payload. Stated as data so a new method cannot quietly inherit
# another one's validation.
NEEDS_TARGETS = ("direct", "split")
NEEDS_DRIVER = ("driven",)
NEEDS_SOURCE_SPEC = ("traced",)


def month_start(d: dt.date) -> dt.date:
    """A version is effective from a month, never from a day. The month is the grain the whole
    module works in, and a version starting on the 14th would make one month two rules."""
    return d.replace(day=1)


def _share(v) -> Decimal:
    """A share is a number between 0 and 1 -- the house convention, set by `books_cleanup.pct`
    and adopted here by amendment 12. The drawer still lets somebody type 50; converting it is
    the drawer's job, so that this function has exactly one meaning."""
    try:
        d = Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("A share has to be a number between 0 and 1.")
    if not (Decimal(0) <= d <= ONE):
        raise ValueError("A share has to be a number between 0 and 1.")
    return d.quantize(SHARE_PLACES)


def validate_version(*, method: str, targets=None, driver_id=None, driver_subject=None,
                     source_spec=None, effective_from: dt.date, reach: str,
                     rationale: str, tier=None, known_business_ids: set) -> dict:
    """Normalize one version's terms, or refuse with a sentence somebody can act on. Pure.

    Pure on purpose: every refusal here is a judgement about the terms alone, so the whole set
    is testable without a database, and the service wrapper's only job is to supply
    `known_business_ids` and write the row.
    """
    if method not in METHOD_TIER:
        raise ValueError(f"A rule's method has to be one of: "
                         f"{', '.join(sorted(METHOD_TIER))}.")
    want_tier = METHOD_TIER[method]
    if tier is not None and tier != want_tier:
        raise ValueError(f"A {method} rule is tier {want_tier}, not tier {tier}.")

    # The rationale is the column the Statement shows when somebody asks why a cost landed on
    # their entity. An empty one is the whole feature missing, so it is refused, not defaulted.
    rationale = (rationale or "").strip()
    if not rationale:
        raise ValueError("Say why this cost is shared, in your own words. It is what the "
                         "Statement shows when somebody asks why it reached their entity.")

    if reach not in REACHES:
        raise ValueError(f"Reach has to be one of: {', '.join(REACHES)}.")
    if not isinstance(effective_from, dt.date):
        raise ValueError("A version needs a month to be effective from.")

    out: dict = {"method": method, "tier": want_tier, "rationale": rationale, "reach": reach,
                 "effective_from": month_start(effective_from),
                 "targets": None, "driver_id": None, "driver_subject": None,
                 "source_spec": None}

    if method in NEEDS_TARGETS:
        rows = list(targets or [])
        if not rows:
            raise ValueError("Say which entities this is split between.")
        if method == "direct" and len(rows) != 1:
            raise ValueError("A direct rule has exactly one entity. Use a split for more.")
        clean, seen = [], set()
        for row in rows:
            bid = str((row or {}).get("business_id") or "")
            if bid not in known_business_ids:
                raise ValueError("One of those entities is not in this workspace.")
            if bid in seen:
                raise ValueError("The same entity is listed twice.")
            seen.add(bid)
            clean.append({"business_id": bid, "share": str(_share((row or {}).get("share")))})
        total = sum((Decimal(r["share"]) for r in clean), Decimal(0))
        if method == "direct" and total != ONE:
            raise ValueError("A direct rule sends the whole cost to one entity, so its share "
                             "is 1.")
        if method == "split" and total.quantize(SHARE_PLACES) != ONE:
            # Amendment 16: a refusal, not a pending reason. A stored split that cannot resolve
            # is a bug, and the engine would have to invent something to do with it.
            raise ValueError(f"The shares have to add up to 1. These add up to {total}.")
        out["targets"] = clean
    elif targets:
        raise ValueError(f"A {method} rule does not take a list of entities.")

    if method in NEEDS_DRIVER:
        if driver_id is None:
            raise ValueError("A driven rule needs a driver to read its monthly numbers from.")
        out["driver_id"] = driver_id
        subject = (driver_subject or "").strip()
        out["driver_subject"] = subject[:120] or None
    elif driver_id is not None:
        raise ValueError(f"A {method} rule does not read a driver.")

    if method in NEEDS_SOURCE_SPEC:
        spec = source_spec or {}
        if not spec.get("source"):
            raise ValueError("A traced rule needs a source to take its ratio from.")
        if not spec.get("map"):
            raise ValueError("A traced rule needs a map from the source's names to entities.")
        for entry in spec["map"]:
            if str((entry or {}).get("business_id") or "") not in known_business_ids:
                raise ValueError("One of those entities is not in this workspace.")
        out["source_spec"] = spec
    elif source_spec:
        raise ValueError(f"A {method} rule does not read an outside source.")

    return out


def effective_version(versions, month_start_on: dt.date, as_of: dt.datetime | None = None):
    """Which version governed `month_start_on`, as the world looked at `as_of`. Pure.

    Among the versions created on or before `as_of` (all of them, when None) whose
    `effective_from` is on or before the month, the one with the greatest `version`. None when
    nothing qualifies, which means the rule had not started yet.

    No version ever needs an end date, and that is what makes a restatement expressible rather
    than destructive. v1 from January and v2 from April resolve February to v1 and May to v2. A
    retroactive v3 from January resolves every month to v3 -- while `as_of` set before v3 was
    written still resolves February to v1, which is what the closed month was allocated on.
    """
    best = None
    for v in versions:
        if v.effective_from > month_start_on:
            continue
        if as_of is not None and v.created_at is not None:
            created = v.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=dt.timezone.utc)
            cut = as_of if as_of.tzinfo else as_of.replace(tzinfo=dt.timezone.utc)
            if created > cut:
                continue
        if best is None or v.version > best.version:
            best = v
    return best


async def _known(s: AsyncSession, tenant_id) -> set:
    return {str(b.id) for b in (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id))).scalars()}


async def versions_for(s: AsyncSession, tenant_id, rule_id) -> list[AllocationRuleVersion]:
    """Every version of one rule, oldest first. The whole history, because that is what
    `effective_version` reads and what a restatement is reconstructed from."""
    return list((await s.execute(select(AllocationRuleVersion).where(
        AllocationRuleVersion.tenant_id == tenant_id,
        AllocationRuleVersion.rule_id == rule_id)
        .order_by(AllocationRuleVersion.version))).scalars())


async def _applied_rule(s: AsyncSession, tenant_id, rule_id) -> AllocationRule:
    rule = await s.get(AllocationRule, rule_id)
    if rule is None or rule.tenant_id != tenant_id:
        raise ValueError("Unknown rule")
    if rule.mode != "applied":
        raise ValueError("That is an observed rule. Observed rules describe what the books "
                         "already did and are not versioned.")
    return rule


async def create_applied_rule(s: AsyncSession, tenant_id, actor, *, scope_kind: str,
                              pattern: str, pool_name: str, source_business_id,
                              **version) -> tuple[AllocationRule, AllocationRuleVersion]:
    """A new applied rule and its version 1, in one act. A rule with no version claims nothing
    and would sit in the list looking live, so the two are never separable."""
    if scope_kind not in SCOPE_KINDS:
        raise ValueError(f"Scope has to be one of: {', '.join(SCOPE_KINDS)}.")
    pattern = " ".join((pattern or "").split())
    if len(pattern) < 3:
        raise ValueError("A rule needs at least three characters to match on.")
    if not (pool_name or "").strip():
        raise ValueError("A pool name is required. It is what the reader sees on the line.")
    pool = await s.get(Business, source_business_id)
    if pool is None or pool.tenant_id != tenant_id:
        raise ValueError("Unknown pool entity")

    terms = validate_version(known_business_ids=await _known(s, tenant_id), **version)

    # Uniqueness lives here rather than in a constraint, because a database constraint would
    # have to agree with this normalization and two normalizations drift (4.1).
    norm = normalize_fqn(pattern)
    for r in (await s.execute(select(AllocationRule).where(
            AllocationRule.tenant_id == tenant_id,
            AllocationRule.mode == "applied",
            AllocationRule.scope_kind == scope_kind,
            AllocationRule.source_business_id == pool.id))).scalars():
        if normalize_fqn(r.pattern) == norm:
            raise ValueError("A rule already covers that, on that entity.")

    rule = AllocationRule(
        tenant_id=tenant_id, mode="applied", scope_kind=scope_kind,
        # The pool entity is where the cost is booked and who funds it. Targets live on the
        # version, and there can be several, so this stays NULL (4.1).
        source_business_id=pool.id, target_business_id=None,
        pattern=pattern[:300], pool_name=pool_name.strip()[:80], is_active=True)
    s.add(rule)
    await s.flush()
    v = await _append(s, tenant_id, actor, rule, terms, version_no=1)
    audit(s, tenant_id, getattr(actor, "id", None), "coa.create_applied_rule",
          target_type="allocation_rule", target_id=rule.id, category="Books",
          detail={"pattern": rule.pattern, "pool": rule.pool_name, "scope": scope_kind,
                  "entity": pool.key, "method": terms["method"]})
    await s.commit()
    return rule, v


async def _append(s: AsyncSession, tenant_id, actor, rule: AllocationRule, terms: dict,
                  *, version_no: int) -> AllocationRuleVersion:
    v = AllocationRuleVersion(
        tenant_id=tenant_id, rule_id=rule.id, version=version_no,
        approved_by=getattr(actor, "id", None), is_current=True, **terms)
    s.add(v)
    await s.flush()
    return v


async def supersede(s: AsyncSession, tenant_id, actor, rule_id,
                    **version) -> AllocationRuleVersion:
    """Append the next version and retire the one it replaces.

    The two writes to the old row are the ONLY updates this module performs: `is_current` so a
    reader can find the latest without a subquery, and `superseded_by` so the chain is walkable
    forwards as well as by version number. Its terms are untouched, which is what keeps a closed
    month reconstructable.
    """
    rule = await _applied_rule(s, tenant_id, rule_id)
    terms = validate_version(known_business_ids=await _known(s, tenant_id), **version)
    history = await versions_for(s, tenant_id, rule_id)
    if not history:
        raise ValueError("That rule has no version to supersede.")
    v = await _append(s, tenant_id, actor, rule, terms,
                      version_no=max(h.version for h in history) + 1)
    for prior in history:
        if prior.is_current:
            prior.is_current = False
            prior.superseded_by = v.id
    audit(s, tenant_id, getattr(actor, "id", None), "coa.supersede_allocation_rule",
          target_type="allocation_rule", target_id=rule.id, category="Books",
          detail={"version": v.version, "method": terms["method"],
                  "effective_from": terms["effective_from"].isoformat(),
                  "reach": terms["reach"]})
    await s.commit()
    return v


async def end_rule(s: AsyncSession, tenant_id, actor, rule_id, *,
                   effective_from: dt.date, rationale: str) -> AllocationRuleVersion:
    """Stop a rule claiming anything, from a month, without deleting what it used to claim.

    An `ended` version rather than `is_active = False`, because the question a reader asks is
    "what did this rule say in March", and a flag answers it with the present tense.
    """
    return await supersede(s, tenant_id, actor, rule_id, method="ended",
                           effective_from=effective_from, reach="forward",
                           rationale=rationale)


def version_out(v: AllocationRuleVersion) -> dict:
    return {
        "id": str(v.id), "version": v.version, "tier": v.tier,
        "tier_label": TIERS.get(v.tier), "method": v.method, "targets": v.targets,
        "driver_id": str(v.driver_id) if v.driver_id else None,
        "driver_subject": v.driver_subject, "source_spec": v.source_spec,
        "effective_from": v.effective_from.isoformat(), "reach": v.reach,
        "rationale": v.rationale, "is_current": v.is_current,
        "superseded_by": str(v.superseded_by) if v.superseded_by else None,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }
