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
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (AccountPeriodBalance, AllocationContribution, AllocationRule, BookTxn,
                      Business, CoaMap, StandardAccount)
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

# The month this module became authoritative. Books Cleanup owns 2026 through 31 October and
# this owns 1 November on; the date decides, not the tab somebody opened (SPEC 0.1 cutover).
# The page's month control will not open earlier than this: every live rule is effective from
# November with `reach` forward, so an earlier month renders as zeros and reads as a page
# where nothing works. Here rather than in a route so one date governs every caller.
MODULE_LIVE_FROM = dt.date(2026, 11, 1)

# Why a rule-month resolved to nothing, from the one set (5.6). Phase 4 can reach three of
# these; the driver and traced reasons arrive with Phases 5 and 6.
PENDING_REASONS = (
    "no_driver_values", "driver_subject_missing", "fixed_exceeds_pool",
    "source_not_connected", "source_unmapped_spend", "double_count_risk",
    "allocation_exceeds_pool",
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


async def pools_for_month(s: AsyncSession, tenant_id, live, month, *,
                          today: dt.date) -> tuple[dict, list]:
    """Every live rule's pool for ONE month, as {rule_id: {standard_account_id: amount}}.

    THE ONLY PLACE POOLS ARE COMPUTED, and that is the point. `reconciliation` used to do this
    itself, one rule at a time, which silently dropped the two arbitrations that happen here:

    1. LONGEST PREFIX (5.4). `account_pools` assigns each account to the best-matching rule
       AMONG THE RULES IT IS GIVEN. Handed one rule at a time it cannot arbitrate at all, so
       "Advertising" and "Advertising:Meta" each claimed Meta's spend in full and the same cost
       appeared in two rules' `pool_amount`.
    2. VENDOR CLAIMS FIRST (D6). A specific vendor claim is carved out before account rules see
       the money. Computed per rule there were no claims to carve, so a vendor-claimed
       transaction stayed inside the account rule's pool as well.

    The derivation did both and the report did neither, so the page's "In the books" column
    could disagree with what was actually allocated while Rules and Reconciliation agreed with
    each other. One function, one answer.
    """
    pools: dict = {}
    pending: list = []
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
    return pools, pending


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


class ResolveCtx(NamedTuple):
    """What one rule-month needs beyond its rule, its version and its pool. Read-only.

    `resolve_rule_month` performs no IO, so every read a month needs is hoisted in here by the
    caller ONCE PER MONTH rather than once per rule-month.
    """
    already: frozenset
    drivers: dict
    commits: dict
    commit_values: dict
    spend_by_rule: dict


EMPTY_CTX = ResolveCtx(frozenset(), {}, {}, {}, {})


def resolve_rule_month(rule, v, pool_by_account, month, ctx: ResolveCtx = EMPTY_CTX) -> tuple:
    """THE resolver: one rule, one month, as `(rows, pending)`. Writes nothing, reads nothing.

    `derive_applied` writes the rows this returns. `reconciliation` calls the SAME function for
    a rule-month that wrote no row, to report why rather than printing the method name (U3).
    Two computations of one figure is the worst defect this module could have, and the pool
    figures already come from one place for that reason -- so the reasons come from one too.

    A row is a dict rather than an `AllocationContribution` on purpose: this function is called
    by a reporting path that must not be able to add anything to the session by accident.
    """
    ps, _pe = month
    rows, pending = [], []
    # UUIDs, not the strings `targets` stores. The GUID type coerces a string on the way
    # into a column, so writing worked either way -- but `already` holds UUIDs read back
    # from the database, and `(UUID, str, UUID) in {(UUID, UUID, UUID)}` is always False.
    # The double_count_risk check was dead code until a test asserted it FIRES.
    shares = {uuid.UUID(str(t["business_id"])): Decimal(t["share"])
              for t in (v.targets or [])}
    for std_id, pool in pool_by_account.items():
        if pool == ZERO:
            continue
        if v.method == "driven":
            # A driven rule reads its month's committed numbers. No commit means
            # `no_driver_values` and nothing allocated: a driver that silently reused last
            # month would be indistinguishable on the Statement from one somebody decided.
            driver = ctx.drivers.get(v.driver_id)
            commit = ctx.commits.get((v.driver_id, ps))
            if driver is None or commit is None:
                pending.append({"reason": "no_driver_values", "rule_id": str(rule.id),
                                "standard_account_id": str(std_id)})
                continue
            parts, reason = driven_shares(driver.kind, ctx.commit_values.get(commit.id) or [],
                                          subject=v.driver_subject, pool=pool)
            if reason:
                pending.append({"reason": reason, "rule_id": str(rule.id),
                                "standard_account_id": str(std_id),
                                "subject": v.driver_subject})
                continue
            driver_commit_id = commit.id
        elif v.method == "traced":
            # Meta decides the ratio, the books decide the amount (D13). Spend that matched
            # no map entry counts in the denominator, so its fraction stays on the pool
            # entity and is reported with BOTH figures: what Meta spent unmatched, and the
            # pool money that consequently stayed. One without the other is half an answer
            # -- the first says the map has a hole, the second says what the hole cost.
            traced, unmapped, reason = traced_shares(
                ctx.spend_by_rule.get(rule.id) or {}, v.source_spec or {})
            if reason:
                pending.append({"reason": reason, "rule_id": str(rule.id),
                                "standard_account_id": str(std_id)})
                continue
            parts = split_amounts(pool, traced)
            if unmapped > ZERO:
                pending.append({"reason": "source_unmapped_spend",
                                "rule_id": str(rule.id),
                                "standard_account_id": str(std_id),
                                "meta_spend": unmapped,
                                "amount": retained(pool, parts)})
            driver_commit_id = None
        else:
            parts = split_amounts(pool, shares)
            driver_commit_id = None
        for bid, amount in parts.items():
            if str(bid) == str(rule.source_business_id):
                continue                      # retained, and the CHECK would refuse it
            if amount == ZERO:
                continue
            if (rule.source_business_id, bid, std_id) in ctx.already:
                pending.append({"reason": "double_count_risk", "rule_id": str(rule.id),
                                "target": str(bid), "standard_account_id": str(std_id),
                                "amount": amount})
                continue
            rows.append({"target_business_id": bid, "standard_account_id": std_id,
                         "amount": amount, "driver_commit_id": driver_commit_id})
    return rows, pending


def _keeps_all(v, pool_business_id) -> bool:
    """True when every target of this version IS the pool entity, so the whole pool stays put.

    A Direct rule whose one target is its own entity writes no row: `resolve_rule_month` skips
    the pool entity's own share and `ck_alloc_not_self` would refuse it anyway. Asking "was a
    row written" therefore reported it unresolved every month, forever (U2). It is as resolved
    as a `retained` rule -- somebody decided, and the decision was that nothing moves.

    NON-EMPTY IS REQUIRED. `all()` of an empty list is True, and `targets` is NULL on every
    `driven`, `traced`, `retained` and `ended` version, so without the `bool(targets)` guard an
    uncommitted driver month would report as resolved: the exact opposite of what this view
    exists to show, and green for the wrong reason.
    """
    targets = v.targets or []
    return bool(targets) and all(
        str(t.get("business_id")) == str(pool_business_id) for t in targets)


async def _spend_context(s: AsyncSession, tenant_id, live, month) -> dict:
    """`{rule_id: {campaign: spend}}` for the traced versions live this month.

    Cached by the account set a spec names. Merging every traced version's spend into one dict
    would make each rule's ratio a fraction of the UNION's total rather than of its own
    accounts', so connecting a second ad account would silently shrink every existing traced
    rule's shares. Rules sharing a spec share the fetch; rules with different specs get the
    only denominator correct for each.
    """
    cache: dict = {}
    out: dict = {}
    for tr, tv in live:
        if tv.method != "traced":
            continue
        spec = tv.source_spec or {}
        key = frozenset(str(a) for a in (spec.get("ad_account_ids") or []))
        if key not in cache:
            cache[key] = await meta_spend(s, tenant_id, month, spec)
        out[tr.id] = cache[key]
    return out


async def derive_applied(s: AsyncSession, tenant_id, month, *, rules_and_versions,
                         pools_by_rule, today: dt.date, drivers=None, commits=None,
                         commit_values=None, spend_by_rule=None) -> dict:
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

    ctx = ResolveCtx(await _observed_inbound(s, tenant_id, month), drivers or {},
                     commits or {}, commit_values or {}, spend_by_rule or {})
    written, total, pending = 0, ZERO, []

    for rule, v in rules_and_versions:
        if v is None or v.method == "ended":
            continue
        rows, rule_pending = resolve_rule_month(
            rule, v, pools_by_rule.get(rule.id) or {}, month, ctx)
        pending.extend(rule_pending)
        for row in rows:
            s.add(AllocationContribution(
                tenant_id=tenant_id, period_start=ps, period_end=pe,
                source_business_id=rule.source_business_id,
                target_business_id=row["target_business_id"],
                standard_account_id=row["standard_account_id"], amount=row["amount"],
                booking="applied", grain="month", pool_name=rule.pool_name,
                basis=_basis(v), rule_id=rule.id, rule_version_id=v.id,
                driver_commit_id=row["driver_commit_id"], approved_by=v.approved_by))
            written += 1
            total += row["amount"]
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

        pools, vendor_pending = await pools_for_month(s, tenant_id, live, month, today=today)
        pending.extend(vendor_pending)

        # The drivers any live version reads this month, and only this month's commits. Loaded
        # per month rather than once, because `current_commit` is per month by definition and
        # reusing another month's would be the silent carry-forward the design refuses.
        drivers, commits, commit_values = await _driver_context(s, tenant_id, live, month[0])
        spend_by_rule = await _spend_context(s, tenant_id, live, month)

        out = await derive_applied(s, tenant_id, month, rules_and_versions=live,
                                   pools_by_rule=pools, today=today, drivers=drivers,
                                   commits=commits, commit_values=commit_values,
                                   spend_by_rule=spend_by_rule)
        done += 1
        written += out["contributions"]
        total += out["amount"]
        pending.extend({**p, "month": month[0].isoformat()} for p in out["pending"])
    return {"months": done, "contributions": written, "amount": total, "pending": pending}


# ── driven shares (5.4, Phase 5) ──────────────────────────────────────────────────────────

def driven_shares(kind: str, values, *, subject: str | None, pool: Decimal) -> tuple:
    """A driver month's numbers, as money per entity. Returns `(amounts, pending_reason)`.

    `amounts` is {business_id: Decimal}, already money rather than shares, because a driver
    mixes units: a flat amount is not a fraction of anything, and converting it to one would
    divide by a pool that can be zero.

    A COUNT driver is the simple case: each entity's count over the total, converted to a share
    at four decimal places and then through `rate()` like any other share.

    A SPLIT driver, for one subject, applies in the order 5.4 states: fixed `amount` values
    first, then `share` OF THE POOL, then whatever is left to the remainder entity.

      "share of the pool" is read literally, as a fraction of the WHOLE pool rather than of
      what the fixed amounts left. On B2's numbers (a 70% share and a flat 2,500) a 10,000 pool
      gives 7,000 and 2,500 with 500 left, not 2,500 and 5,250 with 2,250 left. The literal
      reading is what a person means by "70% of this contractor's time", and the alternative
      silently reinterprets a number somebody typed. It also means the two can over-commit the
      pool, which is reported rather than absorbed -- see `allocation_exceeds_pool`.
    """
    rows = [v for v in values if (v.subject or None) == (subject or None)] if kind == "split" \
        else list(values)
    if not rows:
        return {}, ("driver_subject_missing" if kind == "split" else "no_driver_values")

    if kind == "count":
        total = sum((Decimal(v.value or 0) for v in rows), Decimal(0))
        if total <= 0:
            return {}, "no_driver_values"
        # NOT quantized to four places, though 5.4 says to. A DERIVED share has to stay exact
        # into `rate()`: three equal counts quantize to 0.3333 each, which sums to 0.9999, so
        # `split_amounts` reconciles to 99.99 of a 100.00 pool and the month is a cent short.
        # Correctly short, which is the problem -- it would be short every month on every such
        # rule and nothing would flag it. Full precision makes the total round to 1 and the
        # residue land where amendment 11 puts it. Four decimals is right for DISPLAYING a
        # derived share and wrong for computing with one.
        shares = {v.business_id: Decimal(v.value or 0) / total for v in rows}
        return split_amounts(pool, shares), None

    fixed = {v.business_id: Decimal(v.value or 0)
             for v in rows if v.value_kind == "amount"}
    shares = {v.business_id: Decimal(v.value or 0)
              for v in rows if v.value_kind == "share"}
    rest = [v.business_id for v in rows if v.value_kind == "remainder"]

    fixed_total = sum(fixed.values(), ZERO)
    if fixed_total > pool and pool >= ZERO:
        return {}, "fixed_exceeds_pool"

    out: dict = {}
    for bid, amount in fixed.items():
        out[bid] = out.get(bid, ZERO) + amount
    for bid, part in split_amounts(pool, shares).items():
        out[bid] = out.get(bid, ZERO) + part

    claimed = sum(out.values(), ZERO)
    if rest:
        left = Decimal(pool) - claimed
        if left < ZERO and pool >= ZERO:
            # The fixed amounts and the shares together claim more than there is. Reported
            # rather than written as a negative remainder, which would allocate money out of an
            # entity that never held it. Not in 5.6's table: added, because 5.4's literal
            # reading makes it reachable and the table only covers fixed amounts alone.
            return {}, "allocation_exceeds_pool"
        out[rest[0]] = out.get(rest[0], ZERO) + left
    return {k: v for k, v in out.items() if v != ZERO}, None


async def _driver_context(s: AsyncSession, tenant_id, live, period: dt.date) -> tuple:
    """The drivers, commits and values the live driven versions need for ONE month.

    Keyed by `(driver_id, month)` so a lookup cannot accidentally reach another month's commit:
    that would be the silent carry-forward this module exists to refuse, and it would look
    exactly like a figure somebody committed.
    """
    from .alloc_drivers import current_commit, values_for
    from ..models import AllocationDriver

    want = {v.driver_id for _r, v in live if v.method == "driven" and v.driver_id}
    if not want:
        return {}, {}, {}
    drivers = {d.id: d for d in (await s.execute(select(AllocationDriver).where(
        AllocationDriver.tenant_id == tenant_id,
        AllocationDriver.id.in_(want)))).scalars()}
    commits, commit_values = {}, {}
    for did in want:
        c = await current_commit(s, tenant_id, did, period)
        if c is None:
            continue
        commits[(did, period)] = c
        commit_values[c.id] = await values_for(s, tenant_id, c.id)
    return drivers, commits, commit_values


# ── traced shares: Meta is the ratio, QuickBooks is the pool (5.4, D13, Phase 6) ──────────

async def meta_spend(s: AsyncSession, tenant_id, month, source_spec: dict) -> dict:
    """{campaign name: spend} for one month, from `AdInsightDaily`.

    `occurred_on` is already in the ad account's own timezone, so the month needs no shifting:
    the ads sync stored it that way precisely so a reader never has to guess which midnight a
    day belongs to.

    CAMPAIGN LEVEL ONLY. Account-level rows carry the same spend again and ad-level rows carry
    it a third time, so mixing grains would multiply the ratio's denominator by a number that
    depends on how much of the account somebody happened to sync.
    """
    from ..models import AdCampaign, AdInsightDaily

    ps, pe = month
    accounts = [str(a) for a in (source_spec.get("ad_account_ids") or [])]
    q = (select(AdCampaign.name, AdInsightDaily.spend)
         .join(AdCampaign, AdCampaign.id == AdInsightDaily.campaign_id)
         .where(AdInsightDaily.tenant_id == tenant_id,
                AdInsightDaily.level == "campaign",
                AdInsightDaily.occurred_on >= ps,
                AdInsightDaily.occurred_on <= pe))
    if accounts:
        q = q.where(AdInsightDaily.ad_account_id.in_(accounts))
    out: dict = {}
    for name, spend in (await s.execute(q)).all():
        if not name:
            continue
        out[name] = out.get(name, ZERO) + Decimal(spend or 0)
    return out


def traced_shares(spend_by_campaign: dict, source_spec: dict) -> tuple:
    """A month's Meta spend as shares of the WHOLE, plus what matched nothing.

    Returns `(shares, unmapped_spend, reason)`. The shares are fractions of TOTAL spend, so they
    deliberately sum to less than 1 whenever some spend matched no map entry: that fraction's
    money stays on the pool entity, exactly as `retained` reports it, and the unmapped figure
    comes back so it can be said out loud rather than absorbed.

    D13: **Meta is the ratio and QuickBooks is the amount.** The pool comes from the books and
    Meta only decides how to divide it. The two totals will differ, because Meta bills on its
    own clock and the card posts when it posts, and that difference is reported rather than
    reconciled away: reconciling it would mean choosing which of two true numbers to overwrite.

    FIRST MATCH WINS, in map order, the same precedence `classify_campaign` gives its rules. A
    campaign matching two entries is the author's ordering decision, not an error.

    Matching goes through `ads_rules.match_name`, which is also what `classify_campaign` calls.
    A copy of `_norm` here would be a second normalization, and the two would drift the first
    time one of them learned about a new kind of dash.
    """
    from .ads_rules import match_name

    total = sum(spend_by_campaign.values(), ZERO)
    if total <= ZERO:
        # No rows at all, or an account connected and spending nothing. Either way there is no
        # ratio to take, and inventing one would divide the pool by a number nobody measured.
        return {}, ZERO, "source_not_connected"

    entries = source_spec.get("map") or []
    by_business: dict = {}
    unmapped = ZERO
    for name, spend in spend_by_campaign.items():
        hit = next((e for e in entries
                    if match_name(name, (e or {}).get("match"), (e or {}).get("value"))), None)
        if hit is None:
            unmapped += spend
            continue
        bid = uuid.UUID(str(hit["business_id"]))
        by_business[bid] = by_business.get(bid, ZERO) + spend

    # Unquantized, for the reason the count driver taught: a derived share rounded to four
    # places sums to less than 1 and leaves the month short by a cent, correctly and silently.
    return {bid: amount / total for bid, amount in by_business.items()}, unmapped, None


# ── exceptions, computed live (5.6, Phase 7) ──────────────────────────────────────────────

# What an exception is made of. Not every P&L section: a shared cost is an EXPENSE, and a queue
# that swept in revenue would invite somebody to allocate income by a staff-time split.
EXPENSE_SECTIONS = ("opex", "cogs", "other_expense")


def _gap_codes(txn, payee_counts: dict) -> list[str]:
    """Why this transaction is hard to place, derived from the row rather than stored.

    `recurring` is the useful one: the same normalized payee three or more times in the window
    is the shape of a subscription somebody should write a vendor rule for, and it is the
    difference between a queue of work and a list of noise.
    """
    from .binder_extract import normalize_name

    out = []
    if not (txn.payee or "").strip():
        out.append("no_payee")
    if not (txn.memo or "").strip():
        out.append("no_memo")
    if (txn.flags or {}).get("multi_line"):
        out.append("multi_line")
    n = normalize_name(txn.payee)
    if n and payee_counts.get(n, 0) >= 3:
        out.append("recurring")
    return out


async def exceptions(s: AsyncSession, tenant_id, period, *, today: dt.date) -> dict:
    """Every shared cost on a pool entity that no effective rule claims.

    NEVER STORED, the same call `useNextRun` makes about payment proposals: a stored queue goes
    stale the moment somebody writes a rule, and a stale queue is read as fact.

    `configured: false` when `alloc_pool_business_ids` is NULL, because "nobody has said which
    entities carry shared cost" and "there is nothing to do" are different answers and an empty
    list would render as the second.

    A multi-line transaction ALWAYS appears (D7), carrying a `multi_line` gap. It is the one
    thing the engine will not claim by payee, so the queue is where it has to surface.
    """
    from .alloc_rules import effective_version, versions_for
    from .binder_extract import normalize_name
    from .coa_balances import get_settings

    ps, pe = period
    cfg = await get_settings(s, tenant_id)
    pools = cfg.alloc_pool_business_ids
    if pools is None:
        return {"configured": False, "rows": [], "total": ZERO, "pool_business_ids": []}
    pool_ids = [uuid.UUID(str(b)) for b in pools]
    if not pool_ids:
        return {"configured": True, "rows": [], "total": ZERO, "pool_business_ids": []}

    # The effective version of every applied rule for the month this period opens in. An
    # account covered by ANY tier is covered, `retained` included: somebody has already decided
    # that cost stays put, and re-asking would be the queue second-guessing a decision.
    rules = list((await s.execute(select(AllocationRule).where(
        AllocationRule.tenant_id == tenant_id,
        AllocationRule.mode == "applied",
        AllocationRule.is_active.is_(True)))).scalars())
    claims_vendor: dict = {}
    covers_account: dict = {}
    for r in rules:
        v = effective_version(await versions_for(s, tenant_id, r.id), ps.replace(day=1))
        if v is None or v.method == "ended":
            continue
        if r.scope_kind == "vendor":
            claims_vendor.setdefault(r.source_business_id, set()).add(normalize_name(r.pattern))
        else:
            # The rule travels with its prefix so a partly covered row can NAME what
            # already claims it. "Covered by something" is not an answer a reader can check.
            covers_account.setdefault(r.source_business_id, []).append(
                (normalize_fqn(r.pattern), r))

    rows = (await s.execute(
        select(BookTxn, CoaMap.standard_account_id, CoaMap.qbo_account_fqn,
               StandardAccount.section, StandardAccount.name)
        .join(CoaMap, (CoaMap.tenant_id == BookTxn.tenant_id) &
                      (CoaMap.business_id == BookTxn.business_id) &
                      (CoaMap.qbo_account_id == BookTxn.account_qbo_id))
        .join(StandardAccount, StandardAccount.id == CoaMap.standard_account_id)
        .where(BookTxn.tenant_id == tenant_id,
               BookTxn.business_id.in_(pool_ids),
               BookTxn.txn_date >= ps,
               BookTxn.txn_date <= min(pe, today),
               CoaMap.is_ignored.is_(False),
               StandardAccount.section.in_(EXPENSE_SECTIONS)))).all()

    # Counted BEFORE filtering, so `recurring` describes the window rather than the leftovers:
    # a payee claimed by a rule should not make its unclaimed siblings look like one-offs.
    payee_counts: dict = {}
    for txn, _std, _fqn, _sec, _name in rows:
        n = normalize_name(txn.payee)
        if n:
            payee_counts[n] = payee_counts.get(n, 0) + 1

    out, total = [], ZERO
    for txn, std_id, fqn, section, acct_name in rows:
        payee_n = normalize_name(txn.payee)
        multi = bool((txn.flags or {}).get("multi_line"))
        norm_fqn = normalize_fqn(fqn or "")
        # Longest prefix wins, as everywhere else this module arbitrates account rules (5.4).
        hits = [(p, r) for p, r in (covers_account.get(txn.business_id) or [])
                if norm_fqn.startswith(p)]
        covering = max(hits, key=lambda h: len(h[0]))[1] if hits else None
        if not multi:
            # A multi-line transaction is never claimed by payee (D7), so a vendor rule does
            # not take it out of the queue however well the name matches.
            if payee_n and payee_n in (claims_vendor.get(txn.business_id) or set()):
                continue
            if covering is not None:
                continue
        # A MULTI-LINE TRANSACTION STILL APPEARS, and is no longer COUNTED as unclaimed when
        # an account rule already pools its main account (U4, D6). D7 is about claiming by
        # PAYEE: the engine cannot know a multi-line charge is all one vendor's. It says
        # nothing about account rules, which read trial balances and therefore already
        # include every line of this transaction -- so counting it here reported cost that
        # was allocated as cost that was not, and the figure at the top of the queue was the
        # sum of the two.
        #
        # 5.6 of the parent spec said "multi-line transactions always appear here" without
        # qualification, which is what the code implemented, so this is an amendment to that
        # sentence rather than a bug fix, and 5.6 is amended with it.
        status = "partly_covered" if (multi and covering is not None) else "unclaimed"
        out.append({
            "txn_id": str(txn.id), "txn_date": txn.txn_date.isoformat(),
            "business_id": str(txn.business_id), "payee": txn.payee, "memo": txn.memo,
            "amount": txn.amount, "qbo_type": txn.qbo_type,
            "account": fqn or acct_name, "standard_account_id": str(std_id),
            # NOT `section`: that key already carries the statement section, and reusing it
            # would make one word mean two things in one row.
            "status": status,
            "covered_by": ({"rule_id": str(covering.id), "pool_name": covering.pool_name}
                           if status == "partly_covered" else None),
            "section": section, "gaps": _gap_codes(txn, payee_counts),
        })
        if status == "unclaimed":
            total += txn.amount
    out.sort(key=lambda r: (-abs(r["amount"]), r["txn_date"], r["txn_id"]))
    groups = await _exception_groups(s, tenant_id, out)
    return {"configured": True, "rows": out, "total": total, "groups": groups,
            "pool_business_ids": [str(b) for b in pool_ids]}


async def _exception_groups(s: AsyncSession, tenant_id, rows) -> list:
    """The unclaimed rows, one group per payee per pool entity (UI spec 6.2, 7.3, D8).

    A RULE IS PER PAYEE, so the queue is too. Rendered per charge, a vendor billing monthly
    filled the screen with its own name and a reader answered the same question once per
    charge. `rows` stays beside this for anything still reading it.

    Partly covered rows are excluded: they are not waiting on a rule, they are waiting on
    somebody to look (U4).
    """
    from ..models import Vendor

    unclaimed = [r for r in rows if r.get("status") != "partly_covered"]
    buckets: dict = {}
    for r in unclaimed:
        payee_n = normalize_name(r["payee"]) if r.get("payee") else ""
        # No payee means no vendor rule can ever claim it, so those group by ACCOUNT: the
        # only scope a rule for them could use (6.2).
        key = (r["business_id"], payee_n or f"acct:{r['account']}")
        g = buckets.setdefault(key, {
            "payee": r["payee"], "business_id": r["business_id"], "account_key": r["account"],
            "count": 0, "total": ZERO, "accounts": [], "latest_date": None,
            "gaps": set(), "txn_ids": [], "has_payee": bool(r.get("payee")),
        })
        g["count"] += 1
        g["total"] += Decimal(str(r["amount"]))
        # The last segment of the FQN, deduplicated: "Education/Dues:Coaching/Consulting"
        # says less on a card than "Coaching/Consulting", and the full path is on the charge.
        leaf = (r["account"] or "").split(":")[-1]
        if leaf and leaf not in g["accounts"]:
            g["accounts"].append(leaf)
        if g["latest_date"] is None or r["txn_date"] > g["latest_date"]:
            g["latest_date"] = r["txn_date"]
        g["gaps"].update(r.get("gaps") or [])
        g["txn_ids"].append(r["txn_id"])

    # A Payables vendor that already has a default entity is a likely Tier 1 rule. SUGGEST,
    # never create: the parent spec's standing item says the same, and a queue that wrote
    # rules on a guess would be deciding the thing it exists to ask about.
    wanted = {normalize_name(g["payee"]) for g in buckets.values() if g["payee"]}
    defaults: dict = {}
    if wanted:
        defaults = {n: str(b) for n, b in (await s.execute(select(
            Vendor.name_norm, Vendor.default_business_id).where(
                Vendor.tenant_id == tenant_id,
                Vendor.name_norm.in_(wanted),
                Vendor.default_business_id.is_not(None)))).all()}

    groups = []
    for g in buckets.values():
        payee_n = normalize_name(g["payee"]) if g["payee"] else ""
        hit = defaults.get(payee_n)
        groups.append({**g,
                       "gaps": sorted(g["gaps"]),
                       "suggestion": ({"tier": 1, "business_id": hit, "source": "payables"}
                                      if hit else None)})
    groups.sort(key=lambda g: (-abs(g["total"]), g["payee"] or g["account_key"] or ""))
    return groups


# ── preview, which writes nothing (5.7, Phase 7) ──────────────────────────────────────────


class DraftRule:
    """The shape the pool functions need from a rule, for a draft that has no row yet.

    Giving a draft a real row so the engine could read it would mean a preview that writes, and
    the one guarantee worth having here is that it cannot. The pool functions only ever read
    `id`, `pattern`, `scope_kind` and `source_business_id`.
    """

    def __init__(self, source_business_id, pattern: str, scope_kind: str):
        self.id = uuid.uuid4()
        self.source_business_id = source_business_id
        self.pattern = pattern
        self.scope_kind = scope_kind


def preview_months(effective_from: dt.date, *, today: dt.date, fy_month: int = 1) -> list:
    """Which months a version would govern: every month of the fiscal year from its own start.

    REACH DOES NOT CHANGE THIS. Section 4.2 is explicit that `reach` is what the author chose,
    recorded for the audit trail and the restatement warning, and `effective_version` resolves
    on `effective_from` alone. A retro version dated April governs April onward just as a
    forward one does; what makes it retroactive is that April has already been reported. Having
    reach alter the month set here would be a second resolution rule, disagreeing with the one
    the engine actually uses.
    """
    from .coa_balances import allocation_months

    start = effective_from.replace(day=1)
    return [m for m in allocation_months(today, fy_month) if m[0] >= start]


async def preview(s: AsyncSession, tenant_id, draft: dict, *, today: dt.date,
                  fy_month: int = 1) -> dict:
    """What a draft rule or draft version WOULD do. Writes nothing.

    The drawer calls this on every change, so it answers the three questions somebody asks
    before saving: how much reaches each entity, what it would take out of the exception queue,
    and which already-reported months it would change.

    Deliberately NOT a dry run of `derive_applied`. Sharing the write path would mean a preview
    that could write.
    """
    from .alloc_rules import validate_version

    known = {str(b.id) for b in (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id))).scalars()}
    terms = validate_version(known_business_ids=known, **{
        k: draft[k] for k in ("method", "effective_from", "reach", "rationale", "tier",
                              "targets", "driver_id", "driver_subject", "source_spec")
        if k in draft})

    pool_bid = uuid.UUID(str(draft["source_business_id"]))
    rule = DraftRule(pool_bid, draft["pattern"], draft["scope_kind"])
    months = preview_months(terms["effective_from"], today=today, fy_month=fy_month)
    shares = {uuid.UUID(t["business_id"]): Decimal(t["share"])
              for t in (terms["targets"] or [])}

    by_target: dict = {}
    retained_total, pool_total = ZERO, ZERO
    pending: list = []
    claimed: list = []

    for month in months:
        if rule.scope_kind == "vendor":
            got = await vendor_pools(s, tenant_id, pool_bid, month, today=today, rules=[rule])
            by_account = got["pools"].get(rule.id) or {}
            pending.extend({**p, "month": month[0].isoformat()} for p in got["pending"])
            claimed.extend(await claimable_txn_ids(s, tenant_id, pool_bid, month,
                                                   rule.pattern, today=today))
        else:
            by_account = (await account_pools(s, tenant_id, pool_bid, month,
                                              [rule])).get(rule.id) or {}

        for pool in by_account.values():
            if pool == ZERO:
                continue
            pool_total += pool
            parts = split_amounts(pool, shares) if shares else {}
            # RETAINED IS WHAT DID NOT LEAVE, not what the shares failed to cover. A split must
            # sum to 1 (amendment 16 makes a short one a refusal), so the way to leave money
            # behind is to list the pool entity among the targets and let its share stay (4.2).
            # Measuring retained as pool minus ALL parts would then report zero while the pool
            # entity kept real money, and that money would appear nowhere in the payload.
            went_out = ZERO
            for bid, amount in parts.items():
                if str(bid) == str(pool_bid):
                    continue                      # retained, never a row
                by_target[bid] = by_target.get(bid, ZERO) + amount
                went_out += amount
            retained_total += pool - went_out

    # Which of those months already hold applied rows for this pool entity. This is the
    # restatement warning: a month somebody has already read would change. Shown for every
    # reach, because a forward version dated last month restates it just as a retro one does.
    covered = {m[0] for m in months}
    already = sorted({start.isoformat() for (start,) in (await s.execute(
        select(AllocationContribution.period_start).where(
            AllocationContribution.tenant_id == tenant_id,
            AllocationContribution.booking == "applied",
            AllocationContribution.grain == "month",
            AllocationContribution.source_business_id == pool_bid))).all()
        if start in covered})

    return {
        "months": [m[0].isoformat() for m in months],
        "pool_amount": pool_total,
        "targets": [{"business_id": str(b), "amount": a} for b, a in sorted(
            by_target.items(), key=lambda kv: (-abs(kv[1]), str(kv[0])))],
        "retained": retained_total,
        "pending": pending,
        "claims": {"count": len(claimed), "txn_ids": claimed},
        "would_restate": already,
        "reach": terms["reach"],
    }


async def claimable_txn_ids(s: AsyncSession, tenant_id, pool_bid, month, pattern: str, *,
                            today: dt.date) -> list:
    """The transactions a vendor rule would claim, so the drawer can say how many rows leave
    the exception queue and the gate can check that count against what actually leaves.

    Multi-line transactions are excluded, because a vendor rule never claims one (D7) and
    promising otherwise would overstate what the save achieves.
    """
    from .binder_extract import normalize_name

    ps, pe = month
    want = normalize_name(pattern)
    if not want:
        return []
    rows = (await s.execute(select(BookTxn.id, BookTxn.payee, BookTxn.flags).where(
        BookTxn.tenant_id == tenant_id, BookTxn.business_id == pool_bid,
        BookTxn.txn_date >= ps, BookTxn.txn_date <= min(pe, today),
        BookTxn.qbo_type.in_(POOL_TXN_TYPES), BookTxn.payee.is_not(None)))).all()
    return [str(tid) for tid, payee, flags in rows
            if normalize_name(payee) == want and not (flags or {}).get("multi_line")]


# ── reconciliation (5.8, Phase 7) ─────────────────────────────────────────────────────────

async def reconciliation(s: AsyncSession, tenant_id, period, *, today: dt.date,
                         fy_month: int = 1) -> dict:
    """Per applied rule: what the books hold, what resolved, and what did not.

    `unresolved_amount` is the point of this view. A rule whose driver month was never committed
    resolves NOTHING while its pool still sits in the books, and a report showing only
    `resolved` would make that rule look finished and the cost look allocated.

    Retained counts as RESOLVED. Money that deliberately stayed on the pool entity was decided,
    not dropped, so a Tier 4 rule reconciles cleanly rather than looking like a permanent gap.
    Variance is `resolved - pool` and is zero on a healthy rule for exactly that reason.
    """
    from .alloc_rules import effective_version, versions_for
    from .coa_balances import allocation_months

    ps, pe = period
    months = [m for m in allocation_months(today, fy_month) if m[0] >= ps and m[1] <= pe]
    rules = list((await s.execute(select(AllocationRule).where(
        AllocationRule.tenant_id == tenant_id,
        AllocationRule.mode == "applied",
        AllocationRule.is_active.is_(True)))).scalars())

    # MONTH FIRST, then rules, so every rule live in a month is handed to `pools_for_month`
    # together. Iterating rules first and asking for one rule's pool at a time is what this
    # function used to do, and it lost both longest-prefix arbitration and the D6 vendor
    # carve-out -- see that function's docstring. The report has to compute pools exactly the
    # way the derivation does or it reconciles against a number nothing ever wrote.
    history = {r.id: await versions_for(s, tenant_id, r.id) for r in rules}
    acc = {r.id: {"pool": ZERO, "resolved": ZERO, "pending": []} for r in rules}
    for month in months:
        live = [(r, effective_version(history[r.id], month[0])) for r in rules]
        live = [(r, v) for r, v in live if v is not None and v.method != "ended"]
        if not live:
            continue
        pools, _vendor_pending = await pools_for_month(s, tenant_id, live, month, today=today)
        # The same context the derivation builds, so the reason this report prints is the
        # reason that stopped the row being written, not a second guess at it (7.2). Built
        # lazily: a month where every rule resolved needs none of it.
        ctx = None
        for rule, v in live:
            month_pool = sum((pools.get(rule.id) or {}).values(), ZERO)
            if month_pool == ZERO:
                continue
            acc[rule.id]["pool"] += month_pool

            wrote = (await s.execute(select(AllocationContribution.id).where(
                AllocationContribution.tenant_id == tenant_id,
                AllocationContribution.rule_id == rule.id,
                AllocationContribution.grain == "month",
                AllocationContribution.booking == "applied",
                AllocationContribution.period_start == month[0]).limit(1))).scalars().first()
            # Retained decided that nothing moves; so did a Direct rule pointing at its own
            # entity, which writes no row and used to report unresolved forever (U2).
            if wrote is not None or v.method == "retained" or _keeps_all(
                    v, rule.source_business_id):
                acc[rule.id]["resolved"] += month_pool
                continue
            if ctx is None:
                drivers, commits, commit_values = await _driver_context(
                    s, tenant_id, live, month[0])
                ctx = ResolveCtx(await _observed_inbound(s, tenant_id, month), drivers,
                                 commits, commit_values,
                                 await _spend_context(s, tenant_id, live, month))
            _rows, why = resolve_rule_month(rule, v, pools.get(rule.id) or {}, month, ctx)
            acc[rule.id]["pending"].append(
                {"month": month[0].isoformat(), "amount": month_pool,
                 "method": v.method, "tier": v.tier,
                 # One reason per rule-month. A rule spanning several accounts can stall on
                 # each of them, but they stall for the same cause in every case this module
                 # can produce, and a row offering two fix links would say neither clearly.
                 "reason": why[0]["reason"] if why else None})

    out = []
    for rule in rules:
        a = acc[rule.id]
        out.append({
            "rule_id": str(rule.id), "pool_name": rule.pool_name,
            "scope_kind": rule.scope_kind, "pattern": rule.pattern,
            "pool_business_id": str(rule.source_business_id),
            "pool_amount": a["pool"], "resolved_amount": a["resolved"],
            "unresolved_amount": a["pool"] - a["resolved"],
            "variance": a["resolved"] - a["pool"], "pending": a["pending"],
        })
    out.sort(key=lambda r: (-abs(r["pool_amount"]), r["pool_name"]))
    # The foot of the ledger. Summed HERE: the page does no money arithmetic, and a total
    # the browser added would be a second computation of a figure the rows already carry.
    totals = {
        "count": len(out),
        "pool_amount": sum((r["pool_amount"] for r in out), ZERO),
        "resolved_amount": sum((r["resolved_amount"] for r in out), ZERO),
        "unresolved_amount": sum((r["unresolved_amount"] for r in out), ZERO),
    }
    return {"rules": out, "months": [m[0].isoformat() for m in months], "totals": totals}


# ── what the Allocations page reads (SPEC 6, Phase 9) ────────────────────────────────────

async def rules_payload(s: AsyncSession, tenant_id, period, *, today: dt.date,
                        fy_month: int = 1) -> dict:
    """Every applied rule with its identity, its versions, and its money for the window.

    THE POOL FIGURES COME FROM `reconciliation`, not from a second computation. Rules and
    Reconciliation showing different pool amounts for the same rule would be the worst defect
    this module could have: both would look authoritative, neither would be obviously wrong,
    and the one somebody happened to open first would win the argument. One source, two views.

    `to_partners` is what actually left the pool entity, read from the contributions the engine
    WROTE rather than recomputed from the shares. A version's shares say what should happen;
    the rows say what did. Where a month is pending those differ, and the difference is the
    thing a reader needs to see.
    """
    from .alloc_rules import (TIERS, approver_names, versions_for, version_out, vocabulary)

    recon = await reconciliation(s, tenant_id, period, today=today, fy_month=fy_month)
    by_rule = {r["rule_id"]: r for r in recon["rules"]}

    businesses = [
        {"id": str(b.id), "name": b.name, "accent": b.accent}
        for b in (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id).order_by(Business.sort_order))).scalars()
    ]

    rules = list((await s.execute(select(AllocationRule).where(
        AllocationRule.tenant_id == tenant_id,
        AllocationRule.mode == "applied",
        AllocationRule.is_active.is_(True)).order_by(AllocationRule.pool_name))).scalars())

    # One query for every rule's outbound total, rather than one per rule.
    #
    # BOUNDED BY THE MONTHS RECONCILIATION USED, not by the raw window, and the two are not the
    # same set. `allocation_months` runs from the FISCAL YEAR START through the month containing
    # today, so a window outside the current fiscal year yields no months and every
    # `pool_amount` is zero -- while this query, bounded only by `period_start >= ps` and
    # `period_end <= pe`, happily summed that window's contributions. The page then showed a
    # pool of zero against a non-zero outflow and a NEGATIVE `retained`, because `retained` is
    # derived as `pool - went`. Reading both figures off one month list makes them describe the
    # same thing by construction instead of by coincidence.
    month_starts = [dt.date.fromisoformat(m) for m in recon["months"]]
    out_rows = (await s.execute(
        select(AllocationContribution.rule_id,
               func.sum(AllocationContribution.amount))
        .where(AllocationContribution.tenant_id == tenant_id,
               AllocationContribution.booking == "applied",
               AllocationContribution.grain == "month",
               AllocationContribution.period_start.in_(month_starts))
        .group_by(AllocationContribution.rule_id))).all() if month_starts else []
    to_partners = {rid: amount for rid, amount in out_rows if rid is not None}

    # What each target actually received this period, per rule. The expanded row shows a
    # share beside the money it produced, and the two have to come from the same rows the
    # `to_partners` total does or they will not add up to it.
    tgt_rows = (await s.execute(
        select(AllocationContribution.rule_id,
               AllocationContribution.target_business_id,
               func.sum(AllocationContribution.amount))
        .where(AllocationContribution.tenant_id == tenant_id,
               AllocationContribution.booking == "applied",
               AllocationContribution.grain == "month",
               AllocationContribution.period_start.in_(month_starts))
        .group_by(AllocationContribution.rule_id,
                  AllocationContribution.target_business_id))).all() if month_starts else []
    by_target: dict = {}
    for rid, bid, amount in tgt_rows:
        by_target.setdefault(rid, {})[str(bid)] = amount

    names = {str(b["id"]): b["name"] for b in businesses}
    # Driver labels, so a driven rule can say WHICH driver moves it rather than repeating
    # the same sentence on every driven row.
    from ..models import AllocationDriver
    driver_labels = {str(i): lab for i, lab in (await s.execute(select(
        AllocationDriver.id, AllocationDriver.label).where(
            AllocationDriver.tenant_id == tenant_id))).all()}
    out = []
    tier_totals = {t: {"count": 0, "pool_amount": ZERO, "pending_count": 0} for t in TIERS}
    totals = {"pool_amount": ZERO, "to_partners": ZERO, "retained": ZERO, "unresolved": ZERO}
    all_versions = []
    histories = {}
    for rule in rules:
        histories[rule.id] = await versions_for(s, tenant_id, rule.id)
        all_versions.extend(histories[rule.id])
    approvers = await approver_names(s, tenant_id, all_versions)

    for rule in rules:
        history = histories[rule.id]
        current = next((v for v in reversed(history) if v.is_current), None)
        money = by_rule.get(str(rule.id)) or {}
        pool = money.get("pool_amount", ZERO)
        went = to_partners.get(rule.id, ZERO)
        pending = money.get("pending", [])
        kept = pool - went
        out.append({
            "id": str(rule.id),
            "pool_name": rule.pool_name,
            "scope_kind": rule.scope_kind,
            "pattern": rule.pattern,
            "pool_business_id": str(rule.source_business_id),
            "current": version_out(current, approvers) if current else None,
            "versions": [version_out(v, approvers) for v in reversed(history)],
            "pool_amount": pool,
            "to_partners": went,
            # Derived, so it absorbs nothing: whatever did not leave stayed, including the
            # rounding residue and any month that did not resolve.
            "retained": kept,
            # The ledger orders pending rules first, BY THIS FIGURE. Sent rather than summed
            # from `pending` in the browser, because that is money arithmetic and this page
            # does none (B6, and the module's standing rule).
            "unresolved": money.get("unresolved_amount", ZERO),
            "to_partners_by_target": by_target.get(rule.id, {}),
            # Built here, not in the browser: it is a sentence made of entity names and
            # shares, and principle 4 keeps both on this side of the wire.
            "targets_label": targets_label(current, names, str(rule.source_business_id),
                                           driver_labels),
            "pending": pending,
        })
        if current is not None and current.tier in tier_totals:
            tier_totals[current.tier]["count"] += 1
            tier_totals[current.tier]["pool_amount"] += pool
            if pending:
                tier_totals[current.tier]["pending_count"] += 1
        totals["pool_amount"] += pool
        totals["to_partners"] += went
        totals["retained"] += kept
        totals["unresolved"] += money.get("unresolved_amount", ZERO)

    meta = vocabulary()
    meta["tiers"] = [{"tier": t, "code": f"T{t}", "label": label, "gloss": TIER_GLOSS[t]}
                     for t, label in sorted(TIERS.items())]
    meta["tier_totals"] = {str(t): d for t, d in tier_totals.items()}
    # Summed HERE so the browser does not. The page added these client side, against the
    # module's own standing rule that every displayed figure is the server's (U7).
    meta["totals"] = totals
    return {
        "meta": meta,
        "businesses": businesses,
        "rules": out,
        "months": recon["months"],
    }


def targets_label(v, names: dict, pool_business_id: str, drivers: dict | None = None) -> str:
    """"The Forum 50%, beCollective 30%" -- what a rule does, as words, for the ledger.

    Replaces a Method column that printed one of six sentences over and over without ever
    saying where the money went (U11, UI spec 6.1). A driven rule names its DRIVER rather
    than the method, because "a monthly number sets the split" is true of every driven rule
    on the page and tells a reader nothing about this one.
    """
    from .alloc_rules import METHOD_LABELS
    if v is None:
        return None
    if v.method == "retained":
        return f"Stays on {names.get(pool_business_id, 'this entity')}"
    if v.method == "driven":
        label = (drivers or {}).get(str(v.driver_id))
        return f"By {label}" if label else METHOD_LABELS["driven"]
    if v.method == "traced":
        src = ((v.source_spec or {}).get("source") or "").replace("_", " ").strip()
        return f"By {src}" if src else METHOD_LABELS["traced"]
    if v.method in ("direct", "split"):
        parts = []
        for t in (v.targets or []):
            bid = str(t.get("business_id"))
            share = Decimal(str(t.get("share") or 0)) * 100
            pct = share.quantize(Decimal("1")) if share == share.to_integral_value() \
                else share.normalize()
            name = names.get(bid, "an entity")
            parts.append(f"{name} {pct}%" if bid != pool_business_id
                         else f"stays on {name} {pct}%")
        return ", ".join(parts) or METHOD_LABELS.get(v.method)
    return METHOD_LABELS.get(v.method)


# The one-line explanation each tier carries on screen. Here rather than in the frontend so
# adding a tier is a server change: acceptance check A10 greps that folder for a constant of
# tier definitions and expects to find none.
TIER_GLOSS = {
    1: "One entity carries the whole cost.",
    2: "An outside source sets the ratio.",
    3: "A monthly number sets the split.",
    4: "The cost stays where it is booked.",
}
