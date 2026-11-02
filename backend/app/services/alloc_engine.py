"""The applied-allocation engine: pools, splits, and the one place a share becomes money
(ALLOCATION-RULES-SPEC 5.4, 5.5, Phase 4).

`coa_alloc.py` keeps the observed path, which describes allocations the books already made.
This module computes the ones they did not, from a rule's current version, per calendar month.

THE SPLIT HAS TO ADD BACK TO THE POOL. Every month, for every rule, the entities' amounts plus
whatever stays on the pool entity must equal the pool to the cent. Round each share
independently and they will not: three shares of a figure ending in an odd half-cent lose or
gain a cent, every month, on every rule, and the Statement still renders and still ties out
because the tie-out compares mapped against booked rather than against the pool. So the residue
is placed deliberately, once, by `split_amounts`.

`rate()` is the one place in THIS module where a share becomes money (amendment 15).
`books_cleanup._parts` is the other one in the product, in float, and this follows its rounding
rule rather than inventing a second one.
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (AccountPeriodBalance, AllocationContribution, AllocationRule, BookTxn,
                      CoaMap)
from .binder_extract import normalize_name
from .coa_balances import PL_TYPES
from .coa_map import normalize_fqn

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

# The ledger entities a vendor pool draws from. A BillPayment is excluded on purpose: it debits
# accounts payable rather than an expense, so counting it beside the Bill it settles would
# charge the pool twice for one cost. A JournalEntry is excluded because it carries no payee to
# match, and a Transfer because it never touches a P&L account.
POOL_TXN_TYPES = ("Purchase", "Bill")

# Why a rule-month resolved to nothing, from the one set (5.6). Phase 4 can reach three of
# these; the driver and traced reasons arrive with Phases 5 and 6.
PENDING_REASONS = (
    "no_driver_values", "driver_subject_missing", "fixed_exceeds_pool",
    "source_not_connected", "source_unmapped_spend", "double_count_risk",
    "multi_line", "unmapped_account", "vendor_rule_collision",
)


def rate(amount: Decimal, share: Decimal) -> Decimal:
    """A share of an amount, in money. The one place in this module that multiplies by a share.

    `share` is a fraction between 0 and 1 (amendment 12, and the house convention
    `books_cleanup.pct` set). Half-even rather than half-up because a ledger rounds a run of
    half-cents to no net drift, while half-up walks upward on every one of them.
    """
    return (Decimal(amount) * Decimal(share)).quantize(CENT, rounding=ROUND_HALF_EVEN)


def split_amounts(pool: Decimal, shares: dict) -> dict:
    """Each key's money, adding back to the allocated whole to the cent.

    The residue of rounding lands on the LARGEST line by magnitude (amendment 11, superseding
    D5, which put it on the pool entity). Two reasons, both from prior art rather than taste:
    QuickBooks' journal-entry import expects it, and `books_cleanup._parts` already does it, so
    a reader comparing the Cleanup workbook against this module sees one rule and not two.

    THE WHOLE IS `rate(pool, total share)`, NOT THE POOL. The shares do not always sum to 1: a
    `retained` method sends nothing, and a split may deliberately stop short and leave the rest
    on the pool entity. Reconciling to the pool instead would force the entire pool onto the
    largest line and silently delete the retained portion -- which is what the first version of
    this function did, found by running `{forum: 0.6}` over 1000.00 and getting 1000.00 out and
    nothing retained. The arithmetic has to hold for a partial split, not just a total one.

    Largest by MAGNITUDE, so a refund of a shared cost places its residue the same way a charge
    does, and the residue may be negative: 0.7 and 0.3 of 1000.05 round UP to 1000.06 between
    them, so the largest line comes down a cent. Ties broken on the key, because `max` would
    otherwise pick whichever the dict happened to yield first and a rebuild has to reproduce the
    row it replaced: a 50/50 split has two equally large lines and the cent has to go to a
    predictable one.
    """
    out = {k: rate(pool, s) for k, s in shares.items()}
    if not out:
        return out
    whole = rate(pool, sum(Decimal(s) for s in shares.values()))
    big = max(out, key=lambda k: (abs(out[k]), str(k)))
    out[big] = out[big] + whole - sum(out.values())
    return out


def retained(pool: Decimal, allocated: dict) -> Decimal:
    """What stays on the pool entity: the pool, less everything that went out.

    Derived rather than computed from a share, so it absorbs nothing and hides nothing. If the
    shares sum to 1 this is zero, and a non-zero value means the version deliberately left some
    behind -- a `retained` method, a split whose shares stop short of 1, or traced spend that
    matched no map entry.
    """
    return Decimal(pool) - sum(allocated.values(), ZERO)


# ── pools ─────────────────────────────────────────────────────────────────────────────────

async def vendor_pools(s: AsyncSession, tenant_id, pool_business_id, month, *,
                       today: dt.date, rules) -> dict:
    """Every vendor rule's pool on one entity for one month, keyed
    {rule_id: {standard_account_id: amount}}, plus what it had to refuse.

    ONE FETCH FOR EVERY VENDOR RULE ON THE ENTITY, not one per rule. `normalize_name` is a
    regex chain and cannot be expressed in SQL, so the SQL half narrows by tenant, entity, date
    and type, and payee equality happens here. Doing it per rule would re-scan the month's
    transactions once per rule, and D6 needs the union anyway: what an account pool subtracts is
    every vendor claim on that account, not one rule's.

    The join to `CoaMap` is OUTER. The observed path joins inner and also filters
    `standard_account_id.is_not(None)`, which is right for describing allocations the books
    already made; copying it here would let a transaction on an unmapped account silently shrink
    a pool instead of being reported, and the shrunken pool would still split to the cent.
    """
    ps, pe = month
    wanted: dict[str, list] = {}
    for r in rules:
        n = normalize_name(r.pattern)
        if n:                                    # an empty pattern is refused at save (4.1)
            wanted.setdefault(n, []).append(r)
    if not wanted:
        return {"pools": {}, "claimed": {}, "pending": []}

    rows = (await s.execute(
        select(BookTxn.id, BookTxn.amount, BookTxn.payee, BookTxn.flags,
               BookTxn.account_qbo_id, CoaMap.standard_account_id, CoaMap.is_ignored,
               CoaMap.qbo_account_fqn, CoaMap.qbo_account_name)
        .join(CoaMap, (CoaMap.tenant_id == BookTxn.tenant_id) &
                      (CoaMap.business_id == BookTxn.business_id) &
                      (CoaMap.qbo_account_id == BookTxn.account_qbo_id), isouter=True)
        .where(BookTxn.tenant_id == tenant_id,
               BookTxn.business_id == pool_business_id,
               BookTxn.txn_date >= ps,
               # The same upper bound `_write_period` uses, so a vendor pool and an account
               # pool in an open month cover the same days.
               BookTxn.txn_date <= min(pe, today),
               BookTxn.qbo_type.in_(POOL_TXN_TYPES),
               BookTxn.payee.is_not(None)))).all()

    pools: dict = {}
    claimed: dict = {}
    pending: list = []
    for tid, amount, payee, flags, qid, std_id, ignored, fqn, name in rows:
        hits = wanted.get(normalize_name(payee)) or []
        if not hits:
            continue
        # D7 and F9. A multi-line transaction carries ONE account_qbo_id and its header amount,
        # so claiming it by payee would charge line one's account for the whole thing. It goes
        # to the queue instead. Checked here rather than in SQL: `flags` is a JSON column and
        # the key test is not portable between SQLite and Postgres.
        if (flags or {}).get("multi_line"):
            pending.append({"reason": "multi_line", "txn_id": str(tid),
                            "payee": payee, "amount": amount})
            continue
        if len(hits) > 1:
            # Refused at save by `alloc_rules.scope_norm`, so this is the belt: two rules
            # claiming one transaction would double the pool and D6 would subtract it twice,
            # while every split still summed to its own pool to the cent.
            pending.append({"reason": "vendor_rule_collision", "txn_id": str(tid),
                            "payee": payee,
                            "rules": sorted(str(h.id) for h in hits)})
            continue
        if ignored:
            continue                             # a human excluded this account, with a reason
        if std_id is None:
            pending.append({"reason": "unmapped_account", "txn_id": str(tid),
                            "account": fqn or name or qid, "amount": amount})
            continue
        rid = hits[0].id
        by_acct = pools.setdefault(rid, {})
        by_acct[std_id] = by_acct.get(std_id, ZERO) + amount
        claimed.setdefault(rid, set()).add((std_id,))
    return {"pools": pools, "claimed": claimed, "pending": pending}


async def account_pool(s: AsyncSession, tenant_id, pool_business_id, month, prefix: str, *,
                       vendor_claims: dict | None = None) -> dict:
    """One account or program rule's pool for one month, keyed {standard_account_id: amount}.

    The month's `AccountPeriodBalance` rows for every mapped account whose fully qualified name
    starts with the prefix, grouped by standard account, MINUS every vendor claim on those same
    accounts that month (D6: a specific claim beats a general one, and without the carve-out the
    same cost is allocated twice).

    P&L ONLY. A prefix match can reach a balance-sheet account that happens to share a name
    stem, and a pool that swept in a bank balance would be wrong in the way this module exists
    to prevent: it would still split to the cent and the Statement would still tie out, because
    the tie-out compares mapped against booked rather than against the pool.
    """
    ps, pe = month
    norm = normalize_fqn(prefix)
    rows = (await s.execute(
        select(CoaMap.standard_account_id, AccountPeriodBalance.amount,
               CoaMap.qbo_account_fqn, CoaMap.qbo_account_type)
        .join(CoaMap, (CoaMap.tenant_id == AccountPeriodBalance.tenant_id) &
                      (CoaMap.business_id == AccountPeriodBalance.business_id) &
                      (CoaMap.qbo_account_id == AccountPeriodBalance.qbo_account_id))
        .where(AccountPeriodBalance.tenant_id == tenant_id,
               AccountPeriodBalance.business_id == pool_business_id,
               AccountPeriodBalance.period_start == ps,
               AccountPeriodBalance.period_end == pe,
               CoaMap.standard_account_id.is_not(None),
               CoaMap.is_ignored.is_(False)))).all()

    pool: dict = {}
    for std_id, amount, fqn, qtype in rows:
        if (qtype or "") not in PL_TYPES:
            continue
        if not normalize_fqn(fqn or "").startswith(norm):
            continue
        pool[std_id] = pool.get(std_id, ZERO) + amount

    for std_id, claimed in (vendor_claims or {}).items():
        if std_id in pool:
            pool[std_id] = pool[std_id] - claimed
    return {k: v for k, v in pool.items() if v != ZERO}


def longest_prefix_wins(rules, fqn: str | None):
    """Among account and program rules, the one whose prefix is the longest match, as
    `coa_alloc.match_alloc_rule` already does for observed rules. A general rule and a specific
    one both matching is not ambiguity, it is a hierarchy."""
    norm = normalize_fqn(fqn or "")
    best = None
    for r in rules:
        p = normalize_fqn(r.pattern)
        if norm.startswith(p) and (best is None or len(p) > len(normalize_fqn(best.pattern))):
            best = r
    return best


# ── derivation ────────────────────────────────────────────────────────────────────────────

def _basis(v) -> str:
    """A readable summary of the version, for the `basis` column somebody reads on a line."""
    tier = f"Tier {v.tier}" if v.tier else "Ended"
    if v.method in ("direct", "split") and v.targets:
        shares = "/".join(str(int(Decimal(t["share"]) * 100)) for t in v.targets)
        return f"{tier} {v.method.title()}, {shares}"[:200]
    return f"{tier} {v.method.title()}"[:200]


async def _observed_inbound(s: AsyncSession, tenant_id, month) -> set:
    """Which (source, target, standard account) triples already carry an OBSERVED charge this
    month. F7: if an applied split sends a cost from the pool entity to an entity that already
    books it as a shared-service charge, the carrier is charged twice in `as_allocated` and the
    double is invisible, because each half is individually correct."""
    ps, pe = month
    rows = (await s.execute(select(
        AllocationContribution.source_business_id,
        AllocationContribution.target_business_id,
        AllocationContribution.standard_account_id).where(
            AllocationContribution.tenant_id == tenant_id,
            AllocationContribution.booking == "observed",
            AllocationContribution.period_start <= ps,
            AllocationContribution.period_end >= pe))).all()
    return {(a, b, c) for a, b, c in rows}


async def derive_applied(s: AsyncSession, tenant_id, month, *, rules_and_versions,
                         pools_by_rule, today: dt.date) -> dict:
    """Write one month's applied contributions, replacing whatever that month held.

    Deletes `booking == "applied"` AND `grain == "month"` for this month only, then writes one
    row per target. Both predicates matter: without `grain` it would reach the window rows
    `sync_allocations` owns, and without `booking` it would reach the observed ones, which
    describe what the books already did and are not this module's to delete.

    RETAINED IS NEVER A ROW. `ck_alloc_not_self` forbids a contribution from an entity to
    itself, and 5.4 defines the retained amount as `pool - sum(targets)` -- derived, so it
    absorbs nothing and hides nothing. A version may legitimately list the pool entity among
    its targets (4.2), and that share is exactly what stays behind, so it is skipped here rather
    than written and rejected.
    """
    ps, pe = month
    await s.execute(AllocationContribution.__table__.delete().where(
        AllocationContribution.tenant_id == tenant_id,
        AllocationContribution.booking == "applied",
        AllocationContribution.grain == "month",
        AllocationContribution.period_start == ps,
        AllocationContribution.period_end == pe))

    already = await _observed_inbound(s, tenant_id, month)
    written, total, pending = 0, ZERO, []

    for rule, v in rules_and_versions:
        if v is None or v.method == "ended":
            continue
        pool_by_account = pools_by_rule.get(rule.id) or {}
        # UUIDs, not the strings `targets` stores. The GUID type coerces a string on the way
        # into a column, so writing worked either way -- but `already` holds UUIDs read back
        # from the database, and `(UUID, str, UUID) in {(UUID, UUID, UUID)}` is always False.
        # The double_count_risk check was dead code until a test asserted it FIRES.
        shares = {uuid.UUID(str(t["business_id"])): Decimal(t["share"])
                  for t in (v.targets or [])}
        for std_id, pool in pool_by_account.items():
            if pool == ZERO:
                continue
            parts = split_amounts(pool, shares)
            for bid, amount in parts.items():
                if str(bid) == str(rule.source_business_id):
                    continue                      # retained, and the CHECK would refuse it
                if amount == ZERO:
                    continue
                if (rule.source_business_id, bid, std_id) in already:
                    pending.append({"reason": "double_count_risk", "rule_id": str(rule.id),
                                    "target": str(bid), "standard_account_id": str(std_id),
                                    "amount": amount})
                    continue
                s.add(AllocationContribution(
                    tenant_id=tenant_id, period_start=ps, period_end=pe,
                    source_business_id=rule.source_business_id, target_business_id=bid,
                    standard_account_id=std_id, amount=amount,
                    booking="applied", grain="month", pool_name=rule.pool_name,
                    basis=_basis(v), rule_id=rule.id, rule_version_id=v.id,
                    approved_by=v.approved_by))
                written += 1
                total += amount
    return {"contributions": written, "amount": total, "pending": pending}


async def account_pools(s: AsyncSession, tenant_id, pool_business_id, month, rules, *,
                        vendor_claims: dict | None = None) -> dict:
    """Every account and program rule's pool on one entity for one month,
    {rule_id: {standard_account_id: amount}}.

    ASSIGNED PER ACCOUNT, NOT PER RULE, because longest prefix wins (5.4, as
    `coa_alloc.match_alloc_rule` already does for observed rules). Asking each rule for
    everything under its own prefix would let a general rule and a specific one both claim the
    same account, which is not ambiguity, it is a hierarchy: "Advertising" and
    "Advertising:Meta" together mean Meta's spend belongs to the second rule and the rest to
    the first. Claiming it twice would allocate the same cost twice and each rule's split would
    still come to the cent.
    """
    ps, pe = month
    rows = (await s.execute(
        select(CoaMap.standard_account_id, AccountPeriodBalance.amount,
               CoaMap.qbo_account_fqn, CoaMap.qbo_account_type)
        .join(CoaMap, (CoaMap.tenant_id == AccountPeriodBalance.tenant_id) &
                      (CoaMap.business_id == AccountPeriodBalance.business_id) &
                      (CoaMap.qbo_account_id == AccountPeriodBalance.qbo_account_id))
        .where(AccountPeriodBalance.tenant_id == tenant_id,
               AccountPeriodBalance.business_id == pool_business_id,
               AccountPeriodBalance.period_start == ps,
               AccountPeriodBalance.period_end == pe,
               CoaMap.standard_account_id.is_not(None),
               CoaMap.is_ignored.is_(False)))).all()

    out: dict = {}
    for std_id, amount, fqn, qtype in rows:
        if (qtype or "") not in PL_TYPES:
            continue                              # P&L only; see `account_pool`
        winner = longest_prefix_wins(rules, fqn)
        if winner is None:
            continue
        by_acct = out.setdefault(winner.id, {})
        by_acct[std_id] = by_acct.get(std_id, ZERO) + amount

    # D6: a specific claim beats a general one. Subtracted after assignment, so a vendor claim
    # reduces whichever account rule actually holds that account.
    for by_acct in out.values():
        for std_id, claimed in (vendor_claims or {}).items():
            if std_id in by_acct:
                by_acct[std_id] = by_acct[std_id] - claimed
    return {rid: {k: v for k, v in by.items() if v != ZERO} for rid, by in out.items()}


async def derive_all(s: AsyncSession, tenant_id, *, today: dt.date, fy_month: int = 1,
                     months=None) -> dict:
    """Every applied rule, for every month of the fiscal year. What the worker and
    `/allocations/rebuild` both call.

    The version is resolved PER MONTH, which is the whole point of Phase 3: a split that
    changed in April governs April onward and March keeps what March was allocated on.
    """
    from .alloc_rules import effective_version, versions_for
    from .coa_balances import allocation_months

    rules = list((await s.execute(select(AllocationRule).where(
        AllocationRule.tenant_id == tenant_id,
        AllocationRule.mode == "applied",
        AllocationRule.is_active.is_(True)))).scalars())
    if not rules:
        return {"months": 0, "contributions": 0, "amount": ZERO, "pending": []}

    history = {r.id: await versions_for(s, tenant_id, r.id) for r in rules}
    keys = list(months) if months is not None else allocation_months(today, fy_month)

    done, written, total, pending = 0, 0, ZERO, []
    for month in keys:
        live = [(r, effective_version(history[r.id], month[0])) for r in rules]
        live = [(r, v) for r, v in live if v is not None and v.method != "ended"]
        if not live:
            # Still derive: the month may hold rows from a version that has since ended, and
            # leaving them would keep allocating a cost nobody allocates any more.
            await derive_applied(s, tenant_id, month, rules_and_versions=[],
                                 pools_by_rule={}, today=today)
            done += 1
            continue

        pools: dict = {}
        by_entity: dict = {}
        for r, v in live:
            by_entity.setdefault(r.source_business_id, []).append((r, v))
        for pool_bid, group in by_entity.items():
            vend = [r for r, _v in group if r.scope_kind == "vendor"]
            acct = [r for r, _v in group if r.scope_kind in ("account", "program")]
            claims: dict = {}
            if vend:
                vp = await vendor_pools(s, tenant_id, pool_bid, month, today=today, rules=vend)
                pools.update(vp["pools"])
                pending.extend({**p, "month": month[0].isoformat()} for p in vp["pending"])
                for by_acct in vp["pools"].values():
                    for std_id, amt in by_acct.items():
                        claims[std_id] = claims.get(std_id, ZERO) + amt
            if acct:
                pools.update(await account_pools(s, tenant_id, pool_bid, month, acct,
                                                 vendor_claims=claims))

        out = await derive_applied(s, tenant_id, month, rules_and_versions=live,
                                   pools_by_rule=pools, today=today)
        done += 1
        written += out["contributions"]
        total += out["amount"]
        pending.extend({**p, "month": month[0].isoformat()} for p in out["pending"])
    return {"months": done, "contributions": written, "amount": total, "pending": pending}
