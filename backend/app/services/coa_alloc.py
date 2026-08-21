"""Shared-cost allocations: the policy rules, the contributions derived from them, and the
checks that keep them honest (SPEC-coa-mapping-provenance 2.5 and 6.6, Phase 4).

**Observed, not applied.** The spec assumes an allocation is added on top of the books. On this
portfolio it is already in them. The Forum's July shared-service charge of 32,450.07 equals, to
the cent, the increase in its `Due To SB Coaching`: the expense sits on the target, the
receivable sits on the source, and the source never expenses it — correctly, because the cost
belongs to the target. Recording these as amounts to ADD would double-count every one.

So a contribution says *what part of a line was funded by someone else*, and the composition
in Phase 5 reads `booking` to know which way to apply it:

    observed  ->  as_allocated = direct              (the books already include it)
                  as_booked    = direct - in + out   (this entity's own activity)
    applied   ->  as_booked    = direct              (the spec's original world)
                  as_allocated = direct + in - out

One column keeps both worlds honest instead of one silently inverted formula.

**Policy lives on the rule, not the row.** Pool, basis, driver and approver do not vary line by
line, and an allocation is a policy decision — never the bookkeeper's. A person declares the
rule once and every derived contribution inherits it.

QuickBooks is read-only here, as everywhere in this module.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (AccountPeriodBalance, AllocationContribution, AllocationRule, Business,
                      CoaMap, StandardAccount)
from .audit import audit
from .coa_map import normalize_fqn

ZERO = Decimal("0.00")
BOOKINGS = ("observed", "applied")


def match_alloc_rule(fqn: str | None, rules) -> AllocationRule | None:
    """The rule governing this account: the LONGEST matching prefix, exactly as `coa_map_rule`
    resolves. Same mechanism, same reasoning — two different prefixes of equal length cannot
    both be prefixes of one string, so there is never a tie and never a priority column."""
    target = normalize_fqn(fqn)
    if not target:
        return None
    best, best_len = None, -1
    for r in rules:
        if not r.is_active:
            continue
        pat = normalize_fqn(r.pattern)
        if pat and target.startswith(pat) and len(pat) > best_len:
            best, best_len = r, len(pat)
    return best


async def rules_for(s: AsyncSession, tenant_id, business_id) -> list[AllocationRule]:
    """Active rules that can govern this entity as a TARGET: its own, plus tenant-wide ones."""
    return list((await s.execute(select(AllocationRule).where(
        AllocationRule.tenant_id == tenant_id,
        AllocationRule.is_active.is_(True),
        (AllocationRule.target_business_id == business_id) |
        (AllocationRule.target_business_id.is_(None)),
    ))).scalars())


# ── deriving contributions ────────────────────────────────────────────────────────────────

async def sync_allocations(s: AsyncSession, tenant_id,
                           period: tuple[dt.date, dt.date]) -> dict:
    """Rebuild this period's contributions from the rules and the pulled balances.

    Delete-then-derive for the whole period rather than upsert: a rule that stops matching, or
    an account that leaves the subtree, must stop contributing. An upsert would leave the old
    contribution behind, and a stale allocation is the kind of error that reconciles perfectly
    against last month's numbers.

    Amounts are stored debit-positive, as they sit on the target's books. The spec asks for
    "always positive"; signed is kept instead because a credit to a shared pool is a real thing
    — a refund of a shared cost — and abs() would silently flip its effect. Direction between
    entities is still carried by source and target, never by the sign.
    """
    ps, pe = period
    await s.execute(delete(AllocationContribution).where(
        AllocationContribution.tenant_id == tenant_id,
        AllocationContribution.period_start == ps,
        AllocationContribution.period_end == pe))

    businesses = list((await s.execute(select(Business).where(
        Business.tenant_id == tenant_id))).scalars())
    stat = {"contributions": 0, "amount": ZERO, "pairs": set()}

    for biz in businesses:
        rules = await rules_for(s, tenant_id, biz.id)
        if not rules:
            continue
        rows = (await s.execute(
            select(AccountPeriodBalance.amount, CoaMap.qbo_account_fqn,
                   CoaMap.qbo_account_name, CoaMap.standard_account_id)
            .join(CoaMap, (CoaMap.qbo_account_id == AccountPeriodBalance.qbo_account_id) &
                          (CoaMap.business_id == AccountPeriodBalance.business_id) &
                          (CoaMap.tenant_id == AccountPeriodBalance.tenant_id))
            .where(AccountPeriodBalance.tenant_id == tenant_id,
                   AccountPeriodBalance.business_id == biz.id,
                   AccountPeriodBalance.period_start == ps,
                   AccountPeriodBalance.period_end == pe,
                   AccountPeriodBalance.amount != ZERO,
                   CoaMap.is_ignored.is_(False),
                   CoaMap.standard_account_id.is_not(None)))).all()

        # Several QBO accounts under one rule routinely land on one standard account; that
        # merge is the same one the statement makes, so the contribution has to match it or
        # the line and its composition would disagree.
        grouped: dict = {}
        for amount, fqn, name, std_id in rows:
            rule = match_alloc_rule(fqn or name, rules)
            if rule is None or rule.source_business_id == biz.id:
                # A rule pointing an entity at itself is a mis-declaration, not an allocation.
                continue
            key = (rule.id, rule.source_business_id, std_id)
            grouped[key] = grouped.get(key, ZERO) + amount

        by_rule = {r.id: r for r in rules}
        for (rule_id, source_id, std_id), amount in grouped.items():
            if not amount:
                continue
            r = by_rule[rule_id]
            s.add(AllocationContribution(
                tenant_id=tenant_id, period_start=ps, period_end=pe,
                source_business_id=source_id, target_business_id=biz.id,
                standard_account_id=std_id, amount=amount, booking="observed",
                pool_name=r.pool_name, basis=r.basis, driver_source=r.driver_source,
                approved_by=r.approved_by, je_ref=r.je_ref, rule_id=r.id))
            stat["contributions"] += 1
            stat["amount"] += amount
            stat["pairs"].add((str(source_id), str(biz.id)))

    out = {"contributions": stat["contributions"], "amount": float(stat["amount"]),
           "pairs": len(stat["pairs"]), "period": [ps.isoformat(), pe.isoformat()]}
    audit(s, tenant_id, None, "coa.sync_allocations", target_type="tenant",
          target_id=tenant_id, detail=out)
    await s.commit()
    print(f"[coa_alloc] {ps}..{pe} contributions={out['contributions']} "
          f"pairs={out['pairs']} amount={out['amount']:,.2f}", flush=True)
    return out


async def contributions_for(s: AsyncSession, tenant_id, business_id,
                            period: tuple[dt.date, dt.date]) -> dict:
    """This entity's contributions, split into what came IN and what went OUT, keyed by
    standard account. The shape Phase 5's line composition needs."""
    ps, pe = period
    rows = list((await s.execute(select(AllocationContribution).where(
        AllocationContribution.tenant_id == tenant_id,
        AllocationContribution.period_start == ps,
        AllocationContribution.period_end == pe,
        (AllocationContribution.target_business_id == business_id) |
        (AllocationContribution.source_business_id == business_id)))).scalars())
    inbound: dict = {}
    outbound: dict = {}
    for r in rows:
        side = inbound if r.target_business_id == business_id else outbound
        side.setdefault(r.standard_account_id, []).append(r)
    return {"in": inbound, "out": outbound}


# ── the checks ────────────────────────────────────────────────────────────────────────────

async def allocation_check(s: AsyncSession, tenant_id,
                           period: tuple[dt.date, dt.date]) -> dict:
    """Two checks, and only one of them is the spec's (6.6).

    **Zero-sum across the portfolio.** Every contribution is one row producing both sides, so
    the net across all entities is zero by construction. A non-zero result means a row points
    at a business outside this tenant, or one was excluded from the query. It is a cheap
    structural check, and it passing proves less than it looks like it does.

    **Intercompany reconciliation** is the one worth reading. If The Forum was charged
    32,450.07 of shared cost, its intercompany balance should have moved by 32,450.07. Where it
    did not, something ELSE went through the due-to/due-from account — beCollective's July
    movement was 5,000.00 larger than its shared-service charge, which is a cash transfer, not
    an allocation. That difference is invisible to the zero-sum check and is exactly what a
    close checklist should surface.

    Never raises. The point of a check is to show you the break.
    """
    ps, pe = period
    names = {b.id: b for b in (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id))).scalars()}
    contributions = list((await s.execute(select(AllocationContribution).where(
        AllocationContribution.tenant_id == tenant_id,
        AllocationContribution.period_start == ps,
        AllocationContribution.period_end == pe))).scalars())

    net: dict = {}
    pairs: dict = {}
    for c in contributions:
        net[c.target_business_id] = net.get(c.target_business_id, ZERO) + c.amount
        net[c.source_business_id] = net.get(c.source_business_id, ZERO) - c.amount
        key = (c.source_business_id, c.target_business_id)
        pairs[key] = pairs.get(key, ZERO) + c.amount
    portfolio_net = sum(net.values(), ZERO)

    # The movement in each entity's intercompany accounts this period. `is_intercompany_account`
    # is the same flag consolidation eliminates on, so the two never drift apart.
    ic_ids = {a.id for a in (await s.execute(select(StandardAccount).where(
        StandardAccount.tenant_id == tenant_id,
        StandardAccount.is_intercompany_account.is_(True)))).scalars()}
    ic_move: dict = {}
    if ic_ids:
        rows = (await s.execute(
            select(AccountPeriodBalance.business_id, AccountPeriodBalance.amount)
            .join(CoaMap, (CoaMap.qbo_account_id == AccountPeriodBalance.qbo_account_id) &
                          (CoaMap.business_id == AccountPeriodBalance.business_id) &
                          (CoaMap.tenant_id == AccountPeriodBalance.tenant_id))
            .where(AccountPeriodBalance.tenant_id == tenant_id,
                   AccountPeriodBalance.period_start == ps,
                   AccountPeriodBalance.period_end == pe,
                   CoaMap.standard_account_id.in_(ic_ids)))).all()
        for bid, amount in rows:
            ic_move[bid] = ic_move.get(bid, ZERO) + amount

    entities = []
    for bid, biz in sorted(names.items(), key=lambda kv: kv[1].sort_order):
        charged = net.get(bid, ZERO)
        moved = ic_move.get(bid, ZERO)
        if not charged and not moved:
            continue
        # The target is charged an expense (debit, positive) and takes on a payable (credit,
        # negative). Same event, opposite signs, so they reconcile when they SUM to zero.
        entities.append({
            "business": biz.key, "name": biz.name,
            "allocated_net": float(charged),
            "intercompany_movement": float(moved),
            "unexplained": float(charged + moved),
        })

    return {
        "period": {"start": ps.isoformat(), "end": pe.isoformat()},
        "zero_sum": {"net": float(portfolio_net), "balanced": portfolio_net == ZERO,
                     "contributions": len(contributions)},
        "pairs": [{"source": names[sid].name if sid in names else str(sid),
                   "target": names[tid].name if tid in names else str(tid),
                   "amount": float(amount)}
                  for (sid, tid), amount in sorted(pairs.items(), key=lambda kv: -kv[1])],
        "entities": entities,
        "note": ("Zero-sum is structural — one row generates both sides, so it passing proves "
                 "only that no row points outside the tenant. `unexplained` is the one to "
                 "read: it is intercompany movement this period that no allocation accounts "
                 "for."),
    }


# ── rule CRUD ─────────────────────────────────────────────────────────────────────────────

async def create_rule(s: AsyncSession, tenant_id, actor, *, pattern: str, pool_name: str,
                      source_business_id, target_business_id=None, basis: str | None = None,
                      driver_source: str | None = None, je_ref: str | None = None) -> AllocationRule:
    pattern = " ".join((pattern or "").split())
    if len(pattern) < 3:
        raise ValueError("A rule pattern needs at least three characters")
    if not (pool_name or "").strip():
        raise ValueError("A pool name is required — it is what the reader sees on the line")
    src = await s.get(Business, source_business_id)
    if src is None or src.tenant_id != tenant_id:
        raise ValueError("Unknown source business")
    if target_business_id is not None:
        tgt = await s.get(Business, target_business_id)
        if tgt is None or tgt.tenant_id != tenant_id:
            raise ValueError("Unknown target business")
        if tgt.id == src.id:
            raise ValueError("An entity cannot allocate a cost to itself")
    existing = await rules_for(s, tenant_id, target_business_id)
    if any(normalize_fqn(r.pattern) == normalize_fqn(pattern) and
           r.target_business_id == target_business_id for r in existing):
        raise ValueError("A rule with that pattern already exists for this scope")

    rule = AllocationRule(
        tenant_id=tenant_id, target_business_id=target_business_id,
        source_business_id=src.id, pattern=pattern[:300], pool_name=pool_name.strip()[:80],
        basis=basis, driver_source=driver_source, je_ref=je_ref,
        # The approver is whoever declared it. An allocation is a policy decision, so the row
        # records a person rather than a process.
        approved_by=getattr(actor, "id", None), is_active=True)
    s.add(rule)
    await s.flush()
    audit(s, tenant_id, getattr(actor, "id", None), "coa.create_allocation_rule",
          target_type="allocation_rule", target_id=rule.id,
          detail={"pattern": rule.pattern, "pool": rule.pool_name,
                  "source": src.key, "target": str(target_business_id or "all")})
    await s.commit()
    return rule


async def update_rule(s: AsyncSession, tenant_id, actor, rule_id, **fields) -> AllocationRule:
    rule = await s.get(AllocationRule, rule_id)
    if rule is None or rule.tenant_id != tenant_id:
        raise ValueError("Unknown rule")
    for k, v in fields.items():
        if v is not None and hasattr(rule, k):
            setattr(rule, k, v)
    audit(s, tenant_id, getattr(actor, "id", None), "coa.update_allocation_rule",
          target_type="allocation_rule", target_id=rule.id, detail=dict(fields))
    await s.commit()
    return rule


async def delete_rule(s: AsyncSession, tenant_id, actor, rule_id) -> None:
    rule = await s.get(AllocationRule, rule_id)
    if rule is None or rule.tenant_id != tenant_id:
        raise ValueError("Unknown rule")
    pattern = rule.pattern
    await s.delete(rule)
    audit(s, tenant_id, getattr(actor, "id", None), "coa.delete_allocation_rule",
          target_type="allocation_rule", target_id=rule_id, detail={"pattern": pattern})
    await s.commit()


async def list_rules(s: AsyncSession, tenant_id) -> list[dict]:
    names = {b.id: b for b in (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id))).scalars()}
    out = []
    for r in (await s.execute(select(AllocationRule).where(
            AllocationRule.tenant_id == tenant_id).order_by(AllocationRule.pattern))).scalars():
        out.append({
            "id": str(r.id), "pattern": r.pattern, "pool_name": r.pool_name,
            "source": names[r.source_business_id].name if r.source_business_id in names else None,
            "source_business_id": str(r.source_business_id),
            "target": (names[r.target_business_id].name
                       if r.target_business_id in names else "every entity"),
            "target_business_id": str(r.target_business_id) if r.target_business_id else None,
            "basis": r.basis, "driver_source": r.driver_source, "je_ref": r.je_ref,
            "is_active": r.is_active,
        })
    return out
