"""The mapped statement: trial-balance ingest, sign normalization, the unmapped guard, and the
tie-out invariant (SPEC-coa-mapping-provenance 5.2 to 5.4, Phase 3).

This is where the mapping layer either becomes trustworthy or does not.

**One sign convention, applied once.** Everything stored is debit-positive: assets and expenses
positive, liabilities, equity and revenue negative. QBO hands back mixed conventions depending
on which report you asked for, so the decision is made in `normalize_sign` at ingest and never
re-litigated downstream. Sign bugs in a financial layer are expensive and nearly invisible —
the statement renders, the numbers look like numbers, and the total is wrong.

**Two invariants, both of which refuse to render.** An unmapped account with activity blocks
(5.3), because a statement that quietly omits accounts is worse than no statement: it looks
right. And the mapped total must equal the trial-balance total within tolerance (5.4), which
proves the rollup is faithful to QuickBooks.

**What the tie-out does NOT prove.** It says the map is faithful to QuickBooks. It says nothing
about whether QuickBooks is right. A transaction coded to the wrong account ties perfectly.
That distinction is stated in the API payload, not just here (section 9).

QuickBooks is read-only, here and everywhere in this module.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations import qbo
from ..models import (AccountPeriodBalance, Business, CoaMap, CoaSettings, Integration,
                      StandardAccount, SyncRun)
from .sync import _valid_access_token

ZERO = Decimal("0.00")

# The sources we know how to read a sign from. An unknown one raises rather than passing an
# amount through untouched — silently trusting a convention is the failure this module exists
# to prevent.
SOURCES = ("qbo_tb", "qbo_pl")

# Sections whose accounts are credit-normal: a positive balance in the real world is a CREDIT.
CREDIT_SECTIONS = frozenset({"revenue", "other_income", "liability", "equity"})


def normalize_sign(amount, source: str, *, section: str | None = None) -> Decimal:
    """Return `amount` as **debit-positive**, whatever convention the source used.

    Debit-positive means assets and expenses are positive and liabilities, equity and revenue
    are negative — the convention a trial balance already uses, and the one that makes the
    whole ledger sum to zero. Storing everything this way is what lets a total be taken across
    accounts of different types without a table of special cases at every call site.

    - `qbo_tb`: QBO's TrialBalance gives separate debit and credit columns, and the parser
      already returns `debit - credit`. Debit-positive by construction; nothing to do.
    - `qbo_pl`: the ProfitAndLoss report presents every figure as a positive magnitude —
      revenue of 100 and expense of 100 both arrive as +100. The credit-normal sections have
      to be negated, so `section` is required.

    Render flips the sign back for display on credit-normal accounts; see `for_display`.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown amount source {source!r}; expected one of {SOURCES}")
    value = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    if source == "qbo_tb":
        return value
    if section is None:
        raise ValueError("qbo_pl amounts need a section to know which way is up")
    return -value if section in CREDIT_SECTIONS else value


def for_display(amount: Decimal, section: str | None) -> Decimal:
    """Flip a stored debit-positive amount back to how a reader expects to see it.

    Revenue of 100 is stored as -100 and shown as 100. This is the ONLY place the sign is
    turned around, and it happens at the edge, on the way out.
    """
    return -amount if section in CREDIT_SECTIONS else amount


# ── settings ──────────────────────────────────────────────────────────────────────────────

async def get_settings(s: AsyncSession, tenant_id) -> CoaSettings:
    """The tenant's settings, creating them at the spec's defaults if absent.

    A tenant created after migration 0041 would otherwise have no row, and the guard would
    have to decide what to do about that at the worst possible moment. Defaulting here means
    `block_render_on_unmapped` is never accidentally false.
    """
    row = (await s.execute(select(CoaSettings).where(
        CoaSettings.tenant_id == tenant_id))).scalars().first()
    if row is None:
        row = CoaSettings(tenant_id=tenant_id, ic_flag_threshold_pct=Decimal("5.00"),
                          tie_out_tolerance=Decimal("0.01"), block_render_on_unmapped=True)
        s.add(row)
        await s.commit()
    return row


# ── ingest ────────────────────────────────────────────────────────────────────────────────

def balance_periods() -> list[tuple[dt.date, dt.date]]:
    """The (start, calendar-end) keys to pull, matching the ones `pl_line` already uses so a
    mapped statement and the existing P&L detail always describe the same window."""
    from .books_sync import _pl_line_periods
    return _pl_line_periods()


async def sync_trial_balances(s: AsyncSession, tenant_id, integ: Integration) -> int:
    """Pull the trial balance for each period into `account_period_balance` (SPEC 5.1 step 2).

    Delete-then-insert per period rather than upsert: an account that stops appearing in the
    trial balance has gone to zero, and an upsert would leave its old balance sitting there
    forever. A stale non-zero balance is the kind of error that ties out perfectly.
    """
    token = await _valid_access_token(s, integ)
    now = dt.datetime.now(dt.timezone.utc)
    written = 0
    for ps, pe in balance_periods():
        fetch_end = min(pe, dt.date.today())          # actuals through today for an open period
        report = await qbo.trial_balance(integ.realm_id, token, ps.isoformat(),
                                         fetch_end.isoformat())
        rows = qbo.parse_trial_balance(report)
        await s.execute(delete(AccountPeriodBalance).where(
            AccountPeriodBalance.tenant_id == tenant_id,
            AccountPeriodBalance.business_id == integ.business_id,
            AccountPeriodBalance.period_start == ps,
            AccountPeriodBalance.period_end == pe))
        for r in rows:
            if not r.get("qbo_account_id"):
                # The TrialBalance report can emit a row with no account id (a subtotal that
                # slipped the walk). Dropping it is right — it has no identity to map — but it
                # must not be silent, because a dropped row breaks the tie-out by its amount.
                print(f"[coa_balances] realm={integ.realm_id} {ps}..{pe}: skipped a row with "
                      f"no account id: {r.get('account')!r} {r.get('amount')}", flush=True)
                continue
            s.add(AccountPeriodBalance(
                tenant_id=tenant_id, business_id=integ.business_id,
                period_start=ps, period_end=pe, qbo_account_id=r["qbo_account_id"],
                amount=normalize_sign(r["amount"], "qbo_tb"), source="qbo_tb", synced_at=now))
            written += 1
    await s.commit()
    print(f"[coa_balances] realm={integ.realm_id} periods={len(balance_periods())} "
          f"rows={written}", flush=True)
    return written


async def run_balance_sync(s: AsyncSession, tenant_id, integ: Integration) -> None:
    """Worker entry point, on its own SyncRun so a trial-balance failure is recorded and
    surfaced without taking the chart sync or the dashboard's P&L snapshot down with it."""
    run = SyncRun(tenant_id=tenant_id, provider="qbo_tb")
    s.add(run)
    await s.commit()
    started = dt.datetime.utcnow()
    try:
        records = await sync_trial_balances(s, tenant_id, integ)
        run.status, run.stats = "ok", {
            "records": records,
            "seconds": round((dt.datetime.utcnow() - started).total_seconds(), 1)}
    except Exception as e:  # noqa: BLE001 — record + surface, never abort the pass
        run.status, run.detail = "error", str(e)
        run.stats = {"records": None,
                     "seconds": round((dt.datetime.utcnow() - started).total_seconds(), 1)}
        print(f"[coa_balances] failed: {e}", flush=True)
    run.finished_at = dt.datetime.utcnow()
    await s.commit()


# ── the guard ─────────────────────────────────────────────────────────────────────────────

class UnmappedAccountsError(Exception):
    """Raised instead of rendering a statement that would quietly leave accounts out (5.3).

    Carries the offending accounts so the API can answer with something actionable rather than
    a bare failure — the frontend renders them as a list with a link to the mapping screen.
    """

    def __init__(self, business_id, accounts: list[dict]):
        self.business_id = business_id
        self.accounts = accounts
        total = sum(abs(Decimal(str(a["amount"]))) for a in accounts)
        super().__init__(
            f"{len(accounts)} account(s) with activity are unmapped on this entity "
            f"({total:,.2f} unaccounted for). Map them before this statement can render.")


class TieOutError(Exception):
    """Raised when the mapped total does not equal the trial-balance total (5.4).

    The single most important failure in the module. If mapped totals do not tie to
    QuickBooks the view is wrong, and a wrong financial view that renders is more dangerous
    than an error message.
    """

    def __init__(self, business_id, delta: Decimal, mapped: Decimal, booked: Decimal,
                 tolerance: Decimal):
        self.business_id, self.delta = business_id, delta
        self.mapped, self.booked, self.tolerance = mapped, booked, tolerance
        self.orphaned: list[str] = []
        super().__init__(
            f"Mapped total {mapped:,.2f} does not tie to the trial balance {booked:,.2f} "
            f"— off by {delta:,.2f}, tolerance {tolerance:,.2f}.")

    def _orphaned(self, stray: list[str]) -> "TieOutError":
        """Same failure, more specific cause: the map points at standard accounts this tenant
        does not have, so those amounts would leave the statement without leaving the total."""
        self.orphaned = stray
        self.args = (f"{len(stray)} mapped account(s) point at a standard account outside this "
                     f"tenant's chart, carrying {self.delta:,.2f}. The statement would be "
                     f"missing those lines while still appearing to tie.",)
        return self


async def get_unmapped_with_activity(s: AsyncSession, tenant_id, business_id,
                                     period: tuple[dt.date, dt.date]) -> list[dict]:
    """Accounts carrying a balance this period that have nowhere to go.

    Activity is what makes an unmapped account dangerous. Dead accounts are common, harmless,
    and would otherwise block every statement forever — 130 of the portfolio's 693 accounts
    have no activity at all.

    An IGNORED account with activity counts too. Ignoring is for dead accounts; ignoring a live
    one removes real money from the statement, which is the same hole under a different name.
    """
    ps, pe = period
    rows = (await s.execute(
        select(AccountPeriodBalance.qbo_account_id, AccountPeriodBalance.amount,
               CoaMap.qbo_account_name, CoaMap.qbo_account_fqn, CoaMap.is_ignored,
               CoaMap.ignore_reason, CoaMap.standard_account_id)
        .join(CoaMap, (CoaMap.qbo_account_id == AccountPeriodBalance.qbo_account_id) &
                      (CoaMap.business_id == AccountPeriodBalance.business_id) &
                      (CoaMap.tenant_id == AccountPeriodBalance.tenant_id), isouter=True)
        .where(AccountPeriodBalance.tenant_id == tenant_id,
               AccountPeriodBalance.business_id == business_id,
               AccountPeriodBalance.period_start == ps,
               AccountPeriodBalance.period_end == pe,
               AccountPeriodBalance.amount != ZERO))).all()
    out = []
    for qid, amount, name, fqn, ignored, reason, std in rows:
        if std is not None and not ignored:
            continue
        out.append({
            "qbo_account_id": qid,
            "name": name or "(not in the chart sync yet)",
            "fqn": fqn or name or qid,
            "amount": amount,
            # An account the trial balance knows about but coa_map does not has never been
            # synced. Different problem, same consequence, so say which it is.
            "reason": ("ignored: " + (reason or "no reason given")) if ignored
                      else ("unmapped" if name else "not yet synced"),
        })
    out.sort(key=lambda a: -abs(a["amount"]))
    return out


# ── the mapped statement ──────────────────────────────────────────────────────────────────

# P&L reading order. Operating expenses read Advertising, Sales Promotion, Occupancy, Office,
# Salaries, G&A — proximity to revenue, not magnitude (chart doc, "One Display Consequence").
SECTION_ORDER = ("revenue", "cogs", "opex", "other_income", "other_expense")
SECTION_LABEL = {
    "revenue": "Revenue", "cogs": "Cost of Sale", "opex": "Operating Expenses",
    "other_income": "Other Income", "other_expense": "Other Expense",
    "asset": "Assets", "liability": "Liabilities", "equity": "Equity",
}
BUCKET_LABEL = {
    "revenue_transactional": "Transactional Revenue", "revenue_program": "Program and Membership",
    "revenue_event": "Event Revenue", "revenue_property": "Property Revenue",
    "revenue_partnership": "Partnership and Passive", "revenue_intercompany": "Intercompany Revenue",
    "contra_revenue": "Less: Contra-Revenue",
    "cos_producer": "Producer Compensation", "cos_transaction": "Third-Party Transaction Costs",
    "cos_delivery": "Delivery and Fulfillment", "cos_merchant": "Merchant and Financing",
    "cos_other": "Other Direct Costs",
    "advertising": "Advertising", "sales_promotion": "Sales Promotion", "occupancy": "Occupancy",
    "office_expense": "Office Expenses",
    "salaries_wages": "Salaries, Wages and Contract Labor",
    "general_admin": "General and Administrative",
    "other_income": "Other Income", "interest_expense": "Interest Expense",
    "depreciation_amortization": "Depreciation and Amortization", "income_tax": "Income Tax",
    "other_expense": "Other Expense", "balance_sheet": "Balance Sheet",
}


def _money(v) -> float:
    return float(Decimal(str(v)).quantize(Decimal("0.01")))


async def build_mapped_statement(s: AsyncSession, tenant_id, business_id,
                                 period: tuple[dt.date, dt.date],
                                 statement: str = "pl") -> dict:
    """The entity's books, rendered through the standard chart (SPEC 5.3 + 5.4).

    Raises `UnmappedAccountsError` before building anything if an account with activity has
    nowhere to go, and `TieOutError` after building if the result does not agree with
    QuickBooks. Neither returns a partial statement.
    """
    ps, pe = period
    biz = await s.get(Business, business_id)
    if biz is None or biz.tenant_id != tenant_id:
        raise ValueError("Unknown business")
    cfg = await get_settings(s, tenant_id)

    unmapped = await get_unmapped_with_activity(s, tenant_id, business_id, period)
    if unmapped and cfg.block_render_on_unmapped:
        raise UnmappedAccountsError(business_id=business_id, accounts=unmapped)

    rows = (await s.execute(select(
        AccountPeriodBalance.qbo_account_id, AccountPeriodBalance.amount,
        AccountPeriodBalance.synced_at).where(
        AccountPeriodBalance.tenant_id == tenant_id,
        AccountPeriodBalance.business_id == business_id,
        AccountPeriodBalance.period_start == ps,
        AccountPeriodBalance.period_end == pe))).all()
    balances = [(qid, amount) for qid, amount, _ in rows]
    synced = [r[2].isoformat() if r[2] else None for r in rows]
    booked_total = sum((a for _, a in balances), ZERO)

    maps = {m.qbo_account_id: m for m in (await s.execute(select(CoaMap).where(
        CoaMap.tenant_id == tenant_id, CoaMap.business_id == business_id))).scalars()}
    chart = {a.id: a for a in (await s.execute(select(StandardAccount).where(
        StandardAccount.tenant_id == tenant_id))).scalars()}

    # Roll the trial balance up onto standard accounts. Several QBO accounts routinely land on
    # one standard account — that merge IS the product.
    rolled: dict = {}
    excluded = ZERO
    for qid, amount in balances:
        m = maps.get(qid)
        if m is None or m.is_ignored or m.standard_account_id is None:
            excluded += amount            # already proven zero-activity by the guard above
            continue
        entry = rolled.setdefault(m.standard_account_id, {"amount": ZERO, "sources": []})
        entry["amount"] += amount
        entry["sources"].append({"qbo_account_id": qid,
                                 "name": m.qbo_account_fqn or m.qbo_account_name,
                                 "amount": _money(amount)})

    mapped_total = sum((e["amount"] for e in rolled.values()), ZERO)

    # THE invariant. Compare against the trial balance minus what was legitimately excluded,
    # so the check is like-for-like: the guard has already proven every exclusion is zero, and
    # anything else showing up here is a rollup that lost or double-counted an account.
    delta = mapped_total - (booked_total - excluded)
    if abs(delta) > cfg.tie_out_tolerance:
        raise TieOutError(business_id, delta, mapped_total, booked_total - excluded,
                          cfg.tie_out_tolerance)

    # A rolled amount whose standard account is not in THIS tenant's chart would count toward
    # mapped_total and then vanish from every section — the tie-out would pass while a line
    # was missing from the statement. The FK cannot catch it (it is not tenant-aware) and the
    # service layer refuses to create one, so reaching here means the map was written around
    # the API. Loud, not silent.
    orphaned = sum((e["amount"] for sid, e in rolled.items() if sid not in chart), ZERO)
    if orphaned or any(sid not in chart for sid in rolled):
        stray = [str(sid) for sid in rolled if sid not in chart]
        raise TieOutError(business_id, orphaned, mapped_total, booked_total - excluded,
                          cfg.tie_out_tolerance)._orphaned(stray)

    wanted = ("bs",) if statement == "bs" else ("pl",)
    sections: dict = {}
    for sid, entry in rolled.items():
        acct = chart[sid]
        if acct.statement not in wanted:
            continue
        sec = sections.setdefault(acct.section, {})
        buc = sec.setdefault(acct.bucket, [])
        buc.append({
            "standard_account_id": str(acct.id), "code": acct.code, "name": acct.name,
            "sort_order": acct.sort_order,
            # Stored debit-positive; flipped once, here, on the way out.
            "amount": _money(for_display(entry["amount"], acct.section)),
            "as_booked": _money(for_display(entry["amount"], acct.section)),
            "is_intercompany_account": acct.is_intercompany_account,
            "definition": acct.definition,
            "sources": sorted(entry["sources"], key=lambda x: -abs(x["amount"])),
        })

    order = SECTION_ORDER if statement == "pl" else ("asset", "liability", "equity")
    out_sections = []
    for key in order:
        if key not in sections:
            continue
        buckets = []
        for bkey, lines in sections[key].items():
            lines.sort(key=lambda x: x["sort_order"])
            buckets.append({"key": bkey, "label": BUCKET_LABEL.get(bkey, bkey),
                            "total": _money(sum(Decimal(str(x["amount"])) for x in lines)),
                            "lines": lines})
        buckets.sort(key=lambda b: min(x["sort_order"] for x in b["lines"]))
        out_sections.append({
            "key": key, "label": SECTION_LABEL.get(key, key),
            "total": _money(sum(Decimal(str(b["total"])) for b in buckets)),
            "buckets": buckets,
        })

    return {
        "business": {"id": str(biz.id), "key": biz.key, "name": biz.name,
                     "archetype": biz.archetype, "gross_profit_label": biz.gross_profit_label},
        # `books_closed` and `synced_at` are a required mitigation, not decoration (section 9):
        # a statement that ties is still a statement built on an open, mid-sync month, and the
        # reader has to be able to see that without being told.
        "period": {"start": ps.isoformat(), "end": pe.isoformat(),
                   "books_closed": await _books_closed(s, tenant_id, business_id, ps),
                   "synced_at": max((r for r in synced if r), default=None)},
        "statement": statement,
        "mode": "booked",          # Phase 5 adds "allocated"
        "sections": out_sections,
        "totals": _totals(out_sections) if statement == "pl" else {},
        "tie_out": {"status": "tied", "delta": _money(delta),
                    "mapped": _money(mapped_total), "booked": _money(booked_total - excluded),
                    "tolerance": _money(cfg.tie_out_tolerance),
                    "accounts": len(balances),
                    # Money the statement leaves out. The guard normally proves this is zero;
                    # it can only be non-zero with block_render_on_unmapped turned off, and
                    # then the tie-out still passes while money is missing. Reported so that
                    # hole is visible rather than inferred from a total that looks fine.
                    "excluded": _money(excluded)},
        # Stated in the payload, not only in the spec (section 9). Mapping normalizes
        # presentation; it does not correct a transaction coded to the wrong account, and a
        # clean-looking statement can make that mess LESS visible.
        "caveat": ("Tied to QuickBooks. That proves the map is faithful to the books — it says "
                   "nothing about whether the books are right."),
    }



async def _books_closed(s: AsyncSession, tenant_id, business_id, period_start: dt.date) -> bool:
    """Has the month this period sits in been signed off in the close checklist? A quarter or
    a year is only as closed as its opening month, which is the conservative reading."""
    from ..models import ClosePeriod
    row = (await s.execute(select(ClosePeriod.status).where(
        ClosePeriod.tenant_id == tenant_id, ClosePeriod.business_id == business_id,
        ClosePeriod.period == period_start.replace(day=1)))).scalars().first()
    return row == "closed"


def _totals(sections: list[dict]) -> dict:
    """The calculated lines. Subtotals are never accounts — posting to a subtotal is how the
    old ULRG chart ended up with balances on parents that appeared in none of their children."""
    by = {sec["key"]: Decimal(str(sec["total"])) for sec in sections}
    # Contra-revenue needs no special case. Its accounts are debit-normal but sit in the
    # revenue section, so `for_display` renders them negative and the section total is already
    # net of them. Subtracting again would double-count every refund.
    net_revenue = by.get("revenue", ZERO)
    cogs = by.get("cogs", ZERO)
    opex = by.get("opex", ZERO)
    gross = net_revenue - cogs
    noi = gross - opex
    below = by.get("other_income", ZERO) - by.get("other_expense", ZERO)
    return {
        "net_revenue": _money(net_revenue), "cost_of_sale": _money(cogs),
        "gross_profit": _money(gross), "operating_expenses": _money(opex),
        "net_operating_income": _money(noi), "below_the_line": _money(below),
        "net_income": _money(noi + below),
    }


async def tie_out_report(s: AsyncSession, tenant_id,
                         period: tuple[dt.date, dt.date]) -> dict:
    """Run the two invariants across every entity — the Phase 3 gate, and the thing to look at
    before trusting anything downstream. Never raises: the point is to SEE the failures."""
    ps, pe = period
    cfg = await get_settings(s, tenant_id)
    out = []
    for biz in (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id).order_by(Business.sort_order))).scalars():
        have = (await s.execute(select(AccountPeriodBalance.qbo_account_id).where(
            AccountPeriodBalance.tenant_id == tenant_id,
            AccountPeriodBalance.business_id == biz.id,
            AccountPeriodBalance.period_start == ps,
            AccountPeriodBalance.period_end == pe))).scalars().all()
        if not have:
            out.append({"business": biz.key, "name": biz.name, "status": "no_balances",
                        "detail": "no trial balance pulled for this period"})
            continue
        try:
            st = await build_mapped_statement(s, tenant_id, biz.id, period)
            out.append({"business": biz.key, "name": biz.name, "status": "tied",
                        **st["tie_out"]})
        except UnmappedAccountsError as e:
            out.append({"business": biz.key, "name": biz.name, "status": "unmapped",
                        "accounts": len(e.accounts), "detail": str(e),
                        "worst": [{"fqn": a["fqn"], "amount": _money(a["amount"]),
                                   "reason": a["reason"]} for a in e.accounts[:5]]})
        except TieOutError as e:
            out.append({"business": biz.key, "name": biz.name, "status": "broken",
                        "delta": _money(e.delta), "mapped": _money(e.mapped),
                        "booked": _money(e.booked), "detail": str(e)})
    passing = [e for e in out if e["status"] == "tied"]
    return {
        "period": {"start": ps.isoformat(), "end": pe.isoformat()},
        "tolerance": _money(cfg.tie_out_tolerance),
        "entities": out,
        "gate": {"checked": len(out), "tied": len(passing),
                 "passed": bool(out) and len(passing) == len(out)},
    }
