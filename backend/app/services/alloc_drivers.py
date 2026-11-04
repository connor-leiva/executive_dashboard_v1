"""Monthly drivers: the numbers a driven rule reads (ALLOCATION-RULES-SPEC 4.3, 5.4, Phase 5).

A driver is a question somebody answers once a month: how was this person's time split, how many
members did each entity have. A rule of method `driven` reads the answer for its month and
splits its pool by it.

NOTHING CARRIES FORWARD BY ITSELF. A month with no commit resolves to `no_driver_values` and
allocates nothing, rather than quietly reusing last month's answer: a driver that repeats a
figure nobody looked at is a number nobody decided, and it would be indistinguishable on the
Statement from one somebody did decide. `prefill` exists for exactly this and is a FORM
convenience, never a fallback at resolution time, which is the whole point of the distinction.

A commit is append-only, like a rule version: correcting a month writes a new commit and
supersedes the old one, so the figures a closed month was allocated on stay readable.

The working prior art is the Cleanup tab's people-splitter, which does per person, per month,
per entity shares with a sum gate and a wage-weighted derived driver. It has no model behind it,
only a per-tenant JSON payload (F15, amendment 22). This generalizes it.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (AllocationDriver, AllocationDriverCommit, AllocationDriverValue,
                      Business)
from .audit import audit

ONE = Decimal("1")
# Four places, the same precision `alloc_rules` compares shares at: a count driver derives its
# shares by division and will not land on exact halves.
SHARE_PLACES = Decimal("0.0001")
ZERO = Decimal("0.00")

KINDS = ("split", "count")
UNITS = ("percent", "people", "members")
# A `split` driver answers per subject, per entity. A `count` driver answers per entity, and
# each entity's share is its count over the total.
VALUE_KINDS = {"split": ("share", "amount", "remainder"), "count": ("count",)}


def month_start(d: dt.date) -> dt.date:
    """A commit covers a month, never a day."""
    return d.replace(day=1)


def _number(v, what: str) -> Decimal:
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{what} has to be a number.")


def validate_values(kind: str, rows, *, known_business_ids: set) -> list[dict]:
    """Normalize one commit's numbers, or refuse with a sentence. Pure.

    Refusals, not defaults. A stored driver month that cannot resolve is a bug rather than a
    state, the same call `alloc_rules.validate_version` makes about a split that does not add
    up (amendment 16).
    """
    if kind not in KINDS:
        raise ValueError(f"A driver is one of: {', '.join(KINDS)}.")
    allowed = VALUE_KINDS[kind]
    out: list[dict] = []
    seen: set = set()
    per_subject: dict = {}

    for row in (rows or []):
        row = row or {}
        bid = str(row.get("business_id") or "")
        if bid not in known_business_ids:
            raise ValueError("One of those entities is not in this workspace.")
        vk = str(row.get("value_kind") or "")
        if vk not in allowed:
            raise ValueError(f"A {kind} driver takes {', '.join(allowed)}, not {vk or 'nothing'}.")
        subject = (str(row.get("subject") or "").strip() or None)
        if kind == "count" and subject:
            raise ValueError("A count driver has no subject: it answers per entity.")
        if kind == "split" and not subject:
            raise ValueError("A split driver's row needs the person it is about.")
        key = (subject, bid)
        if key in seen:
            raise ValueError("That entity is listed twice for the same person.")
        seen.add(key)

        value = None
        if vk == "remainder":
            # No number by definition: the remainder is whatever the others leave. A value here
            # would be a second answer to the same question.
            if row.get("value") not in (None, ""):
                raise ValueError("A remainder takes no number: it is what the others leave.")
        elif vk == "share":
            # A PERCENT IF THE CALLER SENT ONE. The form types percents now, because the
            # card said "percent" while every box asked for a number between 0 and 1
            # (U14); `pct_to_share` is the single conversion and storage stays a fraction
            # (amendment 12). `value` is still accepted so existing callers and the
            # engine's own fixtures keep working.
            if row.get("pct") not in (None, ""):
                if row.get("value") not in (None, ""):
                    raise ValueError("Send a percent or a share for that entity, not both.")
                from .alloc_rules import pct_to_share
                value = pct_to_share(row.get("pct"))
            else:
                value = _number(row.get("value"), "A share")
                if not (Decimal(0) <= value <= ONE):
                    raise ValueError("A share has to be a number between 0 and 1.")
                value = value.quantize(SHARE_PLACES)
        elif vk == "count":
            value = _number(row.get("value"), "A count")
            if value < 0:
                raise ValueError("A count cannot be negative.")
        else:                                            # amount
            value = _number(row.get("value"), "An amount")

        bucket = per_subject.setdefault(subject, [])
        bucket.append(vk)
        out.append({"subject": subject, "business_id": bid, "value_kind": vk, "value": value})

    if not out:
        raise ValueError("A commit needs at least one number. An empty month is not a commit.")

    for subject, kinds in per_subject.items():
        if kinds.count("remainder") > 1:
            who = f" for {subject}" if subject else ""
            raise ValueError(f"Only one entity can take the remainder{who}.")
    if kind == "count" and all(r["value"] == 0 for r in out):
        # Every count zero gives a zero total, and every share would be a division by zero.
        raise ValueError("Every count is zero, so there is nothing to share out.")
    return out


async def _known(s: AsyncSession, tenant_id) -> set:
    return {str(b.id) for b in (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id))).scalars()}


async def create_driver(s: AsyncSession, tenant_id, actor, *, key: str, label: str,
                        kind: str, unit: str | None = None) -> AllocationDriver:
    key = " ".join((key or "").split()).lower().replace(" ", "_")
    if len(key) < 2:
        raise ValueError("A driver needs a key, so a rule can name it.")
    if not (label or "").strip():
        raise ValueError("A driver needs a label. It is what somebody reads on the form.")
    if kind not in KINDS:
        raise ValueError(f"A driver is one of: {', '.join(KINDS)}.")
    if unit is not None and unit not in UNITS:
        raise ValueError(f"A unit is one of: {', '.join(UNITS)}.")
    existing = (await s.execute(select(AllocationDriver).where(
        AllocationDriver.tenant_id == tenant_id,
        AllocationDriver.key == key[:40]))).scalars().first()
    if existing is not None:
        raise ValueError("A driver with that key already exists.")

    d = AllocationDriver(tenant_id=tenant_id, key=key[:40], label=label.strip()[:80],
                         kind=kind, unit=unit, is_active=True)
    s.add(d)
    await s.flush()
    audit(s, tenant_id, getattr(actor, "id", None), "coa.create_allocation_driver",
          target_type="allocation_driver", target_id=d.id, category="Books",
          detail={"key": d.key, "kind": d.kind, "unit": d.unit})
    await s.commit()
    return d


async def list_drivers(s: AsyncSession, tenant_id) -> list[AllocationDriver]:
    """Every driver in the workspace, by key. In the service rather than the router, because a
    router that builds its own queries is a router that drifts from the service's scoping."""
    return list((await s.execute(select(AllocationDriver).where(
        AllocationDriver.tenant_id == tenant_id)
        .order_by(AllocationDriver.key))).scalars())


async def subjects_by_driver(s: AsyncSession, tenant_id) -> dict:
    """Who each split driver is about, from the rules that read it: {driver_id: [subject]}.

    The first month of a driver cannot be typed without this. `prefill` answers "what did last
    month say", which is nothing when there is no last month, and a form with no rows is a dead
    end: it was one, and a brand-new driver could never have its first month committed.

    Read from the CURRENT version of each driven rule rather than from past commits, so a person
    added to a rule this month appears on the form before they have ever been committed for.
    In the service because a router that builds its own queries drifts from the service's
    scoping, the same reason `list_drivers` lives here.
    """
    from ..models import AllocationRuleVersion

    rows = (await s.execute(
        select(AllocationRuleVersion.driver_id, AllocationRuleVersion.driver_subject)
        .where(AllocationRuleVersion.tenant_id == tenant_id,
               AllocationRuleVersion.is_current.is_(True),
               AllocationRuleVersion.method == "driven",
               AllocationRuleVersion.driver_id.is_not(None))
        .distinct())).all()
    out: dict = {}
    for driver_id, subject in rows:
        if subject:
            out.setdefault(str(driver_id), set()).add(subject)
    return {k: sorted(v) for k, v in out.items()}


async def subjects_for_month(s: AsyncSession, tenant_id, driver_id, period: dt.date) -> list:
    """The subjects this driver answers for in ONE month, with what each one feeds (7.6).

    RESOLVED PER MONTH, not from `is_current`. `subjects_by_driver` reads the current version
    of every rule, which is right for "who is on the form today" and wrong for a month in the
    past: it would show this month's roster beside last month's committed numbers, and a
    person added in November would appear on September's form as an unanswered row. The page
    gained a month control, so that month is now reachable.

    Each subject carries the rules that read it and what their pools hold, because that is
    the dollars at stake and the reason to get the number right. The form said nothing about
    what any row moved.
    """
    from ..models import AllocationRule
    from .alloc_rules import effective_version, versions_for
    from .alloc_engine import pools_for_month

    month = month_start(period)
    rules = list((await s.execute(select(AllocationRule).where(
        AllocationRule.tenant_id == tenant_id,
        AllocationRule.mode == "applied",
        AllocationRule.is_active.is_(True)))).scalars())

    live = []
    for r in rules:
        v = effective_version(await versions_for(s, tenant_id, r.id), month)
        if v is None or v.method != "driven" or str(v.driver_id) != str(driver_id):
            continue
        live.append((r, v))
    if not live:
        return []

    end = (month.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    pools, _vp = await pools_for_month(s, tenant_id, live, (month, end),
                                       today=dt.date.today())

    by_subject: dict = {}
    for r, v in live:
        if not v.driver_subject:
            continue
        amount = sum((pools.get(r.id) or {}).values(), Decimal("0.00"))
        entry = by_subject.setdefault(v.driver_subject, {"name": v.driver_subject,
                                                         "rules": [], "pool_amount": ZERO})
        entry["rules"].append({"id": str(r.id), "pool_name": r.pool_name,
                               "pool_amount": amount})
        entry["pool_amount"] += amount
    return [by_subject[k] for k in sorted(by_subject)]


async def current_commit(s: AsyncSession, tenant_id, driver_id, period: dt.date):
    """The commit in force for a month, or None. `is_current` rather than the greatest
    `committed_at`, so a correction that supersedes is found by the same read a rule uses."""
    return (await s.execute(select(AllocationDriverCommit).where(
        AllocationDriverCommit.tenant_id == tenant_id,
        AllocationDriverCommit.driver_id == driver_id,
        AllocationDriverCommit.period_month == month_start(period),
        AllocationDriverCommit.is_current.is_(True)))).scalars().first()


async def values_for(s: AsyncSession, tenant_id, commit_id) -> list[AllocationDriverValue]:
    return list((await s.execute(select(AllocationDriverValue).where(
        AllocationDriverValue.tenant_id == tenant_id,
        AllocationDriverValue.commit_id == commit_id)
        .order_by(AllocationDriverValue.subject,
                  AllocationDriverValue.business_id))).scalars())


async def commit_month(s: AsyncSession, tenant_id, actor, driver_id, *, period: dt.date,
                       rows, note: str | None = None) -> AllocationDriverCommit:
    """Commit one month's numbers, superseding whatever that month held.

    Append-only: the prior commit keeps its values and gains `superseded_by`, so a month that
    was already allocated can still say what it was allocated on.
    """
    driver = await s.get(AllocationDriver, driver_id)
    if driver is None or driver.tenant_id != tenant_id:
        raise ValueError("Unknown driver")
    clean = validate_values(driver.kind, rows, known_business_ids=await _known(s, tenant_id))
    period = month_start(period)

    c = AllocationDriverCommit(tenant_id=tenant_id, driver_id=driver.id, period_month=period,
                               committed_by=getattr(actor, "id", None), note=note,
                               is_current=True)
    s.add(c)
    await s.flush()
    for row in clean:
        s.add(AllocationDriverValue(
            tenant_id=tenant_id, commit_id=c.id, subject=row["subject"],
            business_id=row["business_id"], value_kind=row["value_kind"],
            value=row["value"]))

    prior = (await s.execute(select(AllocationDriverCommit).where(
        AllocationDriverCommit.tenant_id == tenant_id,
        AllocationDriverCommit.driver_id == driver.id,
        AllocationDriverCommit.period_month == period,
        AllocationDriverCommit.is_current.is_(True),
        AllocationDriverCommit.id != c.id))).scalars().all()
    for p in prior:
        p.is_current = False
        p.superseded_by = c.id

    audit(s, tenant_id, getattr(actor, "id", None), "coa.commit_allocation_driver",
          target_type="allocation_driver", target_id=driver.id, category="Books",
          detail={"key": driver.key, "month": period.isoformat(), "values": len(clean),
                  "superseded": len(prior)})
    await s.commit()
    return c


async def prefill(s: AsyncSession, tenant_id, driver_id, period: dt.date) -> dict:
    """Last committed month's numbers, for the FORM to start from.

    Not a fallback. Resolution never calls this: a month with no commit of its own reports
    `no_driver_values` and allocates nothing. The difference is the whole design -- somebody
    has to look at the numbers and press commit, and this only saves them retyping.
    """
    period = month_start(period)
    prior = (await s.execute(select(AllocationDriverCommit).where(
        AllocationDriverCommit.tenant_id == tenant_id,
        AllocationDriverCommit.driver_id == driver_id,
        AllocationDriverCommit.period_month < period,
        AllocationDriverCommit.is_current.is_(True))
        .order_by(AllocationDriverCommit.period_month.desc()).limit(1))).scalars().first()
    if prior is None:
        return {"from_month": None, "rows": []}
    rows = await values_for(s, tenant_id, prior.id)
    return {"from_month": prior.period_month.isoformat(),
            "rows": [{"subject": v.subject, "business_id": str(v.business_id),
                      "value_kind": v.value_kind,
                      "value": (str(v.value) if v.value is not None else None)}
                     for v in rows]}


def driver_out(d: AllocationDriver) -> dict:
    return {"id": str(d.id), "key": d.key, "label": d.label, "kind": d.kind,
            "unit": d.unit, "is_active": d.is_active,
            "value_kinds": list(VALUE_KINDS[d.kind])}


def commit_out(c: AllocationDriverCommit, values, *, who: str | None = None) -> dict:
    """One committed month. `pct` rides BESIDE `value` on a share row, never instead of it.

    The stored figure is still the fraction the engine reads; `pct` is the same number in
    the units the form types and the page shows (7.6, and the parent spec's rule that the
    server formats and the browser does not compute).
    """
    from .alloc_rules import share_to_pct
    return {
        "id": str(c.id), "period_month": c.period_month.isoformat(),
        "committed_at": c.committed_at.isoformat() if c.committed_at else None,
        "committed_by": ({"id": str(c.committed_by), "name": who}
                         if c.committed_by else None),
        "note": c.note, "is_current": c.is_current,
        "superseded_by": str(c.superseded_by) if c.superseded_by else None,
        "values": [{"subject": v.subject, "business_id": str(v.business_id),
                    "value_kind": v.value_kind,
                    "value": (str(v.value) if v.value is not None else None),
                    "pct": (share_to_pct(v.value) if v.value_kind == "share" else None)}
                   for v in values],
    }


async def committer_name(s: AsyncSession, tenant_id, commit) -> str | None:
    """Who committed a month. The Drivers card says "Committed Nov 3 by ..." and a date with
    no name is half an audit trail."""
    from ..models import User
    if commit is None or not commit.committed_by:
        return None
    return (await s.execute(select(User.name).where(
        User.tenant_id == tenant_id, User.id == commit.committed_by))).scalars().first()
