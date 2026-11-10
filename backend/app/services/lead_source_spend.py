"""A lead-source group's spend, read from the QuickBooks lines Books already stores (spec §4.7).

No QuickBooks call happens here. `books_sync.sync_qbo_txns` has already pulled every Purchase, Bill
and journal entry into `book_txn` / `book_txn_line`; this reads those rows through the group's
spend rules.

THE RULES, in the order they are applied:

  1. A line is a candidate when its transaction belongs to a rule's QuickBooks company (the rule's
     `business_id`) and is not a BillPayment or a Transfer -- a bill and the payment that settles
     it are one cost, and a transfer moves money between the company's own accounts.
  2. A rule matches a line when every field the rule sets matches: the LINE's expense account
     (never the header card), the HEADER vendor, and a class substring.
  3. A line matching any of a group's rules is that group's, once, however many rules it matches.
  4. A line matching two groups belongs to the one with the lower `display_order` only.
  5. Amount: QuickBooks' line amount is a magnitude. A credit posting is subtracted. A Purchase
     QuickBooks marks `Credit` is a refund and is subtracted. A Deposit line on an expense account
     is money coming back and is subtracted. Everything else is added.
  6. The month is the books date's `YYYY-MM`. Not amortized, not spread.

Then gross × (1 − Σ cost shares / 100) is net. The share is an agreement between two businesses,
not a second pull: nothing in the first company's books records the other's half.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, or_, select

from ..models import (BookTxn, BookTxnLine, Business, CoaMap, Integration, LeadSourceCostShare,
                      LeadSourceGroup, LeadSourceSpendRule)

IGNORED_TYPES = ("BillPayment", "Transfer")
CENT = Decimal("0.01")


def _cents(v) -> Decimal:
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


def rule_matches(rule, line: dict) -> bool:
    """Every field the rule sets must match. A rule that sets nothing matches nothing."""
    if rule.business_id != line["business_id"]:
        return False
    set_any = False
    if rule.account_qbo_id:
        set_any = True
        if (line.get("account_qbo_id") or "") != rule.account_qbo_id:
            return False
    if rule.vendor_qbo_id:
        set_any = True
        if (line.get("vendor_qbo_id") or "") != rule.vendor_qbo_id:
            return False
    if rule.class_label:
        set_any = True
        if rule.class_label.lower() not in (line.get("class_label") or "").lower():
            return False
    return set_any


def signed_amount(line: dict) -> Decimal:
    amt = Decimal(line["amount"] or 0)
    if line.get("posting_type") == "credit":
        return -amt
    if line.get("qbo_type") == "Purchase" and line.get("purchase_credit"):
        return -amt
    if line.get("qbo_type") == "Deposit":
        return -amt
    return amt


async def _rules_by_group(s, tenant_id, group_ids) -> dict:
    out: dict = defaultdict(list)
    if not group_ids:
        return out
    for r in (await s.execute(select(LeadSourceSpendRule).where(
            LeadSourceSpendRule.tenant_id == tenant_id,
            LeadSourceSpendRule.lead_source_group_id.in_(list(group_ids))))).scalars():
        out[r.lead_source_group_id].append(r)
    return out


async def _candidate_lines(s, tenant_id, rules) -> list[dict]:
    """Every stored line any of `rules` could match, as plain dicts. One query."""
    if not rules:
        return []
    businesses = {r.business_id for r in rules}
    accounts = {r.account_qbo_id for r in rules if r.account_qbo_id}
    vendors = {r.vendor_qbo_id for r in rules if r.vendor_qbo_id}
    has_class = any(r.class_label for r in rules)
    clauses = []
    if accounts:
        clauses.append(BookTxnLine.account_qbo_id.in_(accounts))
    if vendors:
        clauses.append(BookTxn.vendor_qbo_id.in_(vendors))
    if has_class:
        clauses.append(BookTxnLine.class_label.is_not(None))
    q = (select(BookTxnLine.id, BookTxnLine.amount, BookTxnLine.posting_type,
                BookTxnLine.account_qbo_id, BookTxnLine.account_label, BookTxnLine.class_label,
                BookTxnLine.description, BookTxn.id, BookTxn.business_id, BookTxn.qbo_type,
                BookTxn.qbo_id, BookTxn.txn_date, BookTxn.vendor_qbo_id, BookTxn.payee,
                BookTxn.purchase_credit)
         .join(BookTxn, BookTxn.id == BookTxnLine.book_txn_id)
         .where(BookTxnLine.tenant_id == tenant_id, BookTxn.tenant_id == tenant_id,
                BookTxn.business_id.in_(businesses), BookTxn.qbo_type.notin_(IGNORED_TYPES),
                or_(*clauses)))
    rows = []
    for (lid, amt, posting, acct, acct_label, klass, desc, tid, bid, qtype, qid, tdate, vendor,
         payee, credit) in (await s.execute(q)).all():
        rows.append({"line_id": lid, "amount": amt, "posting_type": posting,
                     "account_qbo_id": acct, "account_label": acct_label, "class_label": klass,
                     "description": desc, "txn_id": tid, "business_id": bid, "qbo_type": qtype,
                     "qbo_id": qid, "txn_date": tdate, "vendor_qbo_id": vendor, "payee": payee,
                     "purchase_credit": credit})
    return rows


async def competing_groups(s, tenant_id, group: LeadSourceGroup) -> list[LeadSourceGroup]:
    """The group itself and every ACTIVE group of the same business that could claim a line first.
    An inactive group claims nothing: switching Realty.com off must not move Realtor.com's spend."""
    rows = (await s.execute(select(LeadSourceGroup).where(
        LeadSourceGroup.tenant_id == tenant_id, LeadSourceGroup.business_id == group.business_id,
        or_(LeadSourceGroup.is_active.is_(True), LeadSourceGroup.id == group.id)))).scalars().all()
    return sorted(rows, key=lambda g: (g.display_order, g.display_name, str(g.id)))


async def matched_lines(s, tenant_id, group: LeadSourceGroup) -> dict:
    """The lines that are this group's spend, and what the admin should know about the mapping.

    {"rules": n, "lines": [...], "overlap_with_other_groups": {group_key: n_lines_lost_to_it},
     "lines_matching_several_rules": n}
    """
    groups = await competing_groups(s, tenant_id, group)
    rules_by = await _rules_by_group(s, tenant_id, [g.id for g in groups])
    own = rules_by.get(group.id, [])
    if not own:
        return {"rules": 0, "lines": [], "overlap_with_other_groups": {},
                "lines_matching_several_rules": 0}
    everything = [r for rs in rules_by.values() for r in rs]
    lines = await _candidate_lines(s, tenant_id, everything)
    rank = {g.id: i for i, g in enumerate(groups)}
    key_of = {g.id: g.key for g in groups}
    mine, lost, several = [], defaultdict(int), 0
    for ln in lines:
        own_hits = sum(1 for r in own if rule_matches(r, ln))
        if not own_hits:
            continue
        if own_hits > 1:
            several += 1
        claimants = [gid for gid, rs in rules_by.items() if any(rule_matches(r, ln) for r in rs)]
        winner = min(claimants, key=lambda gid: rank.get(gid, 1_000_000))
        if winner != group.id:
            lost[key_of.get(winner, "other")] += 1
            continue
        mine.append({**ln, "signed": signed_amount(ln)})
    return {"rules": len(own), "lines": mine, "overlap_with_other_groups": dict(lost),
            "lines_matching_several_rules": several}


async def cost_shares(s, tenant_id, group: LeadSourceGroup) -> list[dict]:
    from .roles import short_name
    rows = (await s.execute(select(LeadSourceCostShare, Business)
                            .join(Business, Business.id == LeadSourceCostShare.bearer_business_id)
                            .where(LeadSourceCostShare.tenant_id == tenant_id,
                                   LeadSourceCostShare.lead_source_group_id == group.id))).all()
    return [{"bearer_business_id": str(sh.bearer_business_id), "bearer_name": b.name,
             "bearer_short": short_name(b), "bearer_key": b.key, "share_pct": Decimal(sh.share_pct)}
            for sh, b in rows]


async def qbo_synced(s, tenant_id, business_ids) -> dict:
    """Per QuickBooks company: has a sync completed, and where its stored history starts."""
    out = {}
    for bid in business_ids:
        # Every QuickBooks row on the business, not the first: a company that was reconnected has
        # an old disconnected row beside the live one, and the old one must not hide the sync.
        integs = (await s.execute(select(Integration).where(
            Integration.tenant_id == tenant_id, Integration.provider == "qbo",
            Integration.business_id == bid))).scalars().all()
        first = (await s.execute(select(func.min(BookTxn.txn_date)).where(
            BookTxn.tenant_id == tenant_id, BookTxn.business_id == bid))).scalar()
        out[bid] = {"connected": any(i.status in ("connected", "error") for i in integs),
                    "synced": any(i.last_synced_at is not None for i in integs),
                    "history_starts": first}
    return out


async def monthly_spend(s, tenant_id, group: LeadSourceGroup) -> dict:
    """Gross spend by `YYYY-MM`, the share, and whether the numbers can be trusted at all.

    {"mapped": bool, "synced": bool, "history_starts": date|None, "gross_by_month": {month: Decimal},
     "share_pct": Decimal, "bearers": [...], "lines": [...], ...}

    `mapped` False means the group has no spend rule: every spend figure the report shows is then
    null, never $0, and the screen says the QuickBooks mapping is not set.
    """
    m = await matched_lines(s, tenant_id, group)
    shares = await cost_shares(s, tenant_id, group)
    share_pct = sum((sh["share_pct"] for sh in shares), Decimal(0))
    rules = (await _rules_by_group(s, tenant_id, [group.id])).get(group.id, [])
    status = await qbo_synced(s, tenant_id, {r.business_id for r in rules})
    by_month: dict = defaultdict(Decimal)
    for ln in m["lines"]:
        if ln["txn_date"] is not None:
            by_month[ln["txn_date"].strftime("%Y-%m")] += ln["signed"]
    starts = [v["history_starts"] for v in status.values() if v["history_starts"]]
    return {
        "mapped": bool(rules),
        "synced": bool(status) and all(v["synced"] for v in status.values()),
        "connected": bool(status) and all(v["connected"] for v in status.values()),
        "history_starts": min(starts) if starts else None,
        "gross_by_month": {k: _cents(v) for k, v in by_month.items()},
        "share_pct": share_pct,
        "bearers": shares,
        "lines": m["lines"],
        "overlap_with_other_groups": m["overlap_with_other_groups"],
        "lines_matching_several_rules": m["lines_matching_several_rules"],
    }


def net_of(gross: Decimal | None, share_pct: Decimal) -> Decimal | None:
    if gross is None:
        return None
    return _cents(Decimal(gross) * (Decimal(1) - Decimal(share_pct) / Decimal(100)))


async def mapping_options(s, tenant_id, business_ids) -> dict:
    """What the Settings mapper offers: the company's expense accounts (from the chart Books maps)
    and the vendors already on its transactions, by id. Accounts the chart has not seen yet but
    lines carry are offered too, named by the line."""
    accounts: dict = {}
    vendors: dict = {}
    for bid in business_ids:
        for acc_id, name, fqn, typ in (await s.execute(select(
                CoaMap.qbo_account_id, CoaMap.qbo_account_name, CoaMap.qbo_account_fqn,
                CoaMap.qbo_account_type).where(CoaMap.tenant_id == tenant_id,
                                               CoaMap.business_id == bid))).all():
            accounts[(bid, acc_id)] = {"business_id": str(bid), "id": acc_id, "name": name,
                                       "path": fqn or name, "type": typ}
        for acc_id, label, n in (await s.execute(
                select(BookTxnLine.account_qbo_id, func.max(BookTxnLine.account_label),
                       func.count(BookTxnLine.id))
                .join(BookTxn, BookTxn.id == BookTxnLine.book_txn_id)
                .where(BookTxn.tenant_id == tenant_id, BookTxn.business_id == bid,
                       BookTxnLine.account_qbo_id.is_not(None))
                .group_by(BookTxnLine.account_qbo_id))).all():
            row = accounts.setdefault((bid, acc_id), {"business_id": str(bid), "id": acc_id,
                                                      "name": label or acc_id,
                                                      "path": label or acc_id, "type": None})
            row["lines"] = int(n)
        for vid, payee, n in (await s.execute(
                select(BookTxn.vendor_qbo_id, func.max(BookTxn.payee), func.count(BookTxn.id))
                .where(BookTxn.tenant_id == tenant_id, BookTxn.business_id == bid,
                       BookTxn.vendor_qbo_id.is_not(None))
                .group_by(BookTxn.vendor_qbo_id))).all():
            vendors[(bid, vid)] = {"business_id": str(bid), "id": vid, "name": payee or vid,
                                   "transactions": int(n)}
    expense_types = {"Expense", "Other Expense", "Cost of Goods Sold"}
    acc = sorted(accounts.values(), key=lambda a: (a["type"] not in expense_types and a["type"] is not None,
                                                   (a["path"] or "").lower()))
    return {"accounts": acc,
            "vendors": sorted(vendors.values(), key=lambda v: (v["name"] or "").lower())}


def as_uuid(v) -> uuid.UUID:
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
