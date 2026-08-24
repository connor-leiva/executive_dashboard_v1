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
from enum import Enum

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



class Provenance(str, Enum):
    """Where a rendered number came from (SPEC 6.3).

    DIRECT is observed from the bank feed. ALLOCATED carries an intercompany component.
    ADJUSTED is defined now and deliberately unused: depreciation, prepaid amortisation and
    deferred revenue recognition are composed rather than observed too, and Connor will want
    them distinguished once the schedules run. Retrofitting a third state onto a boolean is the
    expensive version of this.
    """
    DIRECT = "direct"
    ADJUSTED = "adjusted"          # reserved — v1 never sets it
    ALLOCATED = "allocated"


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


# QBO account types that live on the P&L. These reset to zero at the fiscal year boundary on a
# trial balance; balance-sheet accounts carry across it. Taken from the source data rather than
# from the map, so the ingest never depends on a mapping decision.
PL_TYPES = frozenset({"Income", "Other Income", "Expense", "Other Expense",
                      "Cost of Goods Sold"})


def _fy_start(on: dt.date, fy_month: int) -> dt.date:
    """The start of the fiscal year containing `on`."""
    year = on.year if (on.month >= fy_month) else on.year - 1
    return dt.date(year, fy_month, 1)


async def _as_of(realm_id: str, token: str, on: dt.date, cache: dict) -> dict[str, Decimal]:
    """The trial balance AS OF a date, as {qbo_account_id: debit-positive amount}.

    QuickBooks' TrialBalance ignores `start_date` — confirmed against the live Forum realm,
    where a July-only pull and a January-to-July pull return the identical figure. That is
    correct for what a trial balance IS: an as-of report, where balance-sheet accounts carry
    their cumulative balance and P&L accounts carry fiscal-year-to-date. Only `end_date`
    selects anything, so only `end_date` is passed.

    Cached per date because the period set overlaps heavily: month-to-date, quarter-to-date
    and year-to-date all end today, and one period's opening date is usually another's close.
    """
    if on in cache:
        return cache[on]
    report = await qbo.trial_balance(realm_id, token, on.isoformat(), on.isoformat())
    out: dict[str, Decimal] = {}
    for r in qbo.parse_trial_balance(report):
        qid = r.get("qbo_account_id")
        if not qid:
            # A row with no account id has no identity to map. Dropping it is right, but never
            # silently: a dropped row shifts the tie-out by its own amount.
            print(f"[coa_balances] realm={realm_id} as-of {on}: skipped a row with no account "
                  f"id: {r.get('account')!r} {r.get('amount')}", flush=True)
            continue
        out[qid] = out.get(qid, ZERO) + normalize_sign(r["amount"], "qbo_tb")
    cache[on] = out
    return out


async def sync_trial_balances(s: AsyncSession, tenant_id, integ: Integration) -> int:
    """Pull each period into `account_period_balance` (SPEC 5.1 step 2).

    Period activity is the DIFFERENCE of two as-of trial balances — the one at the period end
    minus the one the day before it opened. That is the only way to get true period activity
    out of an as-of report, and it is exact rather than approximate. Both sides sum to zero, so
    the difference does too, and the tie-out invariant is unaffected.

    `balance_end` keeps the as-of figure alongside it, because that is what a balance sheet
    wants and re-deriving it later would mean pulling QuickBooks twice for the same numbers.

    Delete-then-insert per period rather than upsert: an account that stops appearing has gone
    to zero, and an upsert would leave its old balance sitting there forever — a stale number
    that ties out perfectly.
    """
    token = await _valid_access_token(s, integ)
    now = dt.datetime.now(dt.timezone.utc)
    cache: dict = {}
    written = 0
    fy_month = await qbo.fiscal_year_start_month(integ.realm_id, token)
    # Account types come from coa_map, which the chart sync populated moments ago. An account
    # the trial balance names but the chart does not is treated as balance-sheet: differencing
    # is the conservative choice, since zeroing an opening we should have subtracted would
    # invent activity that never happened.
    types = {m.qbo_account_id: (m.qbo_account_type or "") for m in (await s.execute(
        select(CoaMap).where(CoaMap.tenant_id == tenant_id,
                             CoaMap.business_id == integ.business_id))).scalars()}
    for ps, pe in balance_periods():
        fetch_end = min(pe, dt.date.today())          # actuals through today for an open period
        opening = await _as_of(integ.realm_id, token, ps - dt.timedelta(days=1), cache)
        closing = await _as_of(integ.realm_id, token, fetch_end, cache)
        # A trial balance zeroes P&L accounts at the fiscal year boundary, so an opening pull
        # from the previous year carries the WHOLE prior year on those accounts. Differencing
        # across it would subtract a year of trading from a year-to-date figure. Balance-sheet
        # accounts carry across the boundary and difference normally.
        crosses_fy = _fy_start(ps - dt.timedelta(days=1), fy_month) != _fy_start(fetch_end, fy_month)
        await s.execute(delete(AccountPeriodBalance).where(
            AccountPeriodBalance.tenant_id == tenant_id,
            AccountPeriodBalance.business_id == integ.business_id,
            AccountPeriodBalance.period_start == ps,
            AccountPeriodBalance.period_end == pe))
        for qid in set(closing) | set(opening):
            close_amt = closing.get(qid, ZERO)
            open_amt = opening.get(qid, ZERO)
            if crosses_fy and types.get(qid) in PL_TYPES:
                open_amt = ZERO
            activity = close_amt - open_amt
            if not activity and not close_amt:
                continue                              # nothing to say about this account
            s.add(AccountPeriodBalance(
                tenant_id=tenant_id, business_id=integ.business_id,
                period_start=ps, period_end=pe, qbo_account_id=qid,
                amount=activity, balance_end=close_amt, source="qbo_tb", synced_at=now))
            written += 1
    await s.commit()
    print(f"[coa_balances] realm={integ.realm_id} periods={len(balance_periods())} "
          f"as_of_pulls={len(cache)} rows={written} fy_start_month={fy_month}", flush=True)
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
        total = sum(max(abs(Decimal(str(a["amount"]))),
                        abs(Decimal(str(a.get("balance_end") or 0)))) for a in accounts)
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
    """Accounts that carry something this period and have nowhere to go.

    "Carries something" means it MOVED in the period or is HOLDING a balance at the end of it.
    Movement alone is not enough: a bank account that sat still all month still belongs on the
    balance sheet, and leaving it out would be a hole. Both zero means genuinely dead, which
    is common and harmless — 130 of the portfolio's 693 accounts are in that state, and
    blocking on them would mean no statement ever renders.

    An IGNORED account does NOT count, whatever it carries. Ignoring is a deliberate, reasoned
    decision by a person, and blocking on it would make the feature useless for the case it
    exists for — a clearing account has activity by definition, and is the canonical thing to
    leave out. The money is not hidden: every exclusion is itemised on the statement with its
    amount and the reason somebody typed, and the total appears in `tie_out.excluded`. Named
    and visible beats absent, and beats refusing to render.
    """
    ps, pe = period
    rows = (await s.execute(
        select(AccountPeriodBalance.qbo_account_id, AccountPeriodBalance.amount,
               AccountPeriodBalance.balance_end,
               CoaMap.qbo_account_name, CoaMap.qbo_account_fqn, CoaMap.is_ignored,
               CoaMap.ignore_reason, CoaMap.standard_account_id)
        .join(CoaMap, (CoaMap.qbo_account_id == AccountPeriodBalance.qbo_account_id) &
                      (CoaMap.business_id == AccountPeriodBalance.business_id) &
                      (CoaMap.tenant_id == AccountPeriodBalance.tenant_id), isouter=True)
        .where(AccountPeriodBalance.tenant_id == tenant_id,
               AccountPeriodBalance.business_id == business_id,
               AccountPeriodBalance.period_start == ps,
               AccountPeriodBalance.period_end == pe,
               (AccountPeriodBalance.amount != ZERO) |
               (AccountPeriodBalance.balance_end != ZERO)))).all()
    out = []
    for qid, amount, balance_end, name, fqn, ignored, reason, std in rows:
        if ignored or std is not None:
            continue
        out.append({
            "qbo_account_id": qid,
            "name": name or "(not in the chart sync yet)",
            "fqn": fqn or name or qid,
            "amount": amount,
            "balance_end": balance_end,
            # An account the trial balance knows about but coa_map does not has never been
            # synced. Different problem, same consequence, so say which it is.
            "reason": "unmapped" if name else "not yet synced",
        })
    out.sort(key=lambda a: -max(abs(a["amount"]), abs(a["balance_end"])))
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
                                 statement: str = "pl", mode: str = "allocated",
                                 threshold_pct=None) -> dict:
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

    # A P&L wants what MOVED in the period; a balance sheet wants what is HELD at the end of
    # it. Same table, different column, and picking the wrong one is a whole-statement error
    # rather than a line error.
    value = (AccountPeriodBalance.balance_end if statement == "bs"
             else AccountPeriodBalance.amount)
    rows = (await s.execute(select(
        AccountPeriodBalance.qbo_account_id, value,
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
    exclusions: list[dict] = []
    for qid, amount in balances:
        m = maps.get(qid)
        if m is None or m.is_ignored or m.standard_account_id is None:
            # Deliberately left out, or (with the guard off) not yet decided. Either way it is
            # itemised rather than merely absent — a reader can see the money and why it went.
            excluded += amount
            if amount:
                exclusions.append({
                    "qbo_account_id": qid,
                    "fqn": (m.qbo_account_fqn or m.qbo_account_name) if m else qid,
                    "amount": _money(amount),
                    "reason": (m.ignore_reason or "ignored, no reason given") if (m and m.is_ignored)
                              else "not mapped",
                })
            continue
        entry = rolled.setdefault(m.standard_account_id, {"amount": ZERO, "sources": []})
        entry["amount"] += amount
        entry["sources"].append({"qbo_account_id": qid,
                                 "name": m.qbo_account_fqn or m.qbo_account_name,
                                 "amount": _money(amount)})

    mapped_total = sum((e["amount"] for e in rolled.values()), ZERO)
    # A balanced trial balance nets to zero, so `mapped_total` is ~0 on a healthy entity and
    # "delta 0.00 of 0.00" reads as though nothing was checked. `gross` is the magnitude the
    # check actually ranged over, and it is what makes the result legible: 0.00 out of twelve
    # million is a real statement about the rollup, 0.00 out of 0.00 is not.
    gross = sum((abs(e["amount"]) for e in rolled.values()), ZERO)

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

    # ── provenance (SPEC 6.2) ──
    from .coa_alloc import contributions_for                    # local: avoids an import cycle
    contribs = await contributions_for(s, tenant_id, business_id, period)
    threshold = (Decimal(str(threshold_pct)) if threshold_pct is not None
                 else cfg.ic_flag_threshold_pct)
    who = {b.id: b.name for b in (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id))).scalars()}
    approver_ids = {c.approved_by for side in contribs.values() for cs in side.values()
                    for c in cs if c.approved_by}
    approvers = {}
    if approver_ids:
        from ..models import User
        # Tenant-scoped even though approver_ids came from tenant-scoped contribution rows.
        # Defence in depth: an .in_() seeded from another table is exactly the query that
        # becomes a leak the first time someone widens the query that fed it.
        approvers = {u.id: u.name for u in (await s.execute(select(User).where(
            User.id.in_(approver_ids), User.tenant_id == tenant_id))).scalars()}

    wanted = ("bs",) if statement == "bs" else ("pl",)
    sections: dict = {}
    for sid, entry in rolled.items():
        acct = chart[sid]
        if acct.statement not in wanted:
            continue
        direct = for_display(entry["amount"], acct.section)
        inbound = contribs["in"].get(sid, [])
        outbound = contribs["out"].get(sid, [])

        # `observed` contributions are ALREADY in `direct` on the target, and were never on the
        # source's P&L at all — the funder books its payment to a receivable. `applied` ones,
        # if a workbook ever feeds them, sit outside the books on both sides.
        in_observed = sum((c.amount for c in inbound if c.booking == "observed"), ZERO)
        in_applied = sum((c.amount for c in inbound if c.booking == "applied"), ZERO)
        out_applied = sum((c.amount for c in outbound if c.booking == "applied"), ZERO)

        as_allocated = direct + in_applied - out_applied
        # The entity's own activity: strip what somebody else funded. Outbound `observed`
        # contributes nothing, because the funder never carried it on its P&L to begin with —
        # subtracting it would invent a cost this entity never had.
        as_booked = direct - in_observed
        ic_net = (in_observed + in_applied) - out_applied
        # Undefined rather than zero in BOTH the cases where it means nothing: a line that
        # nets to zero has no denominator, and a line with no intercompany at all has nothing
        # to report. "0.0%" on every direct row reads as a measured result and is noise on the
        # rows that need no attention.
        ic_share = (abs(ic_net) / abs(as_allocated) * 100) if (ic_net and as_allocated) else None

        provenance = Provenance.ALLOCATED if ic_net else Provenance.DIRECT
        flagged = bool(
            provenance == Provenance.ALLOCATED
            and ic_share is not None
            and ic_share >= threshold
            and not acct.is_intercompany_account)     # Due To / Due From are all intercompany

        sec = sections.setdefault(acct.section, {})
        buc = sec.setdefault(acct.bucket, [])
        buc.append({
            "standard_account_id": str(acct.id), "code": acct.code, "name": acct.name,
            "sort_order": acct.sort_order,
            # Stored debit-positive; flipped once, here, on the way out.
            "amount": _money(as_allocated if mode == "allocated" else as_booked),
            "as_booked": _money(as_booked),
            "as_allocated": _money(as_allocated),
            "ic_net": _money(ic_net),
            # Undefined, not zero, when the line nets to nothing — rendered blank rather than
            # as "0.0%", which would read as a measured result.
            "ic_share_pct": (round(float(ic_share), 1) if ic_share is not None else None),
            "provenance": provenance.value,
            "flagged": flagged,
            "is_intercompany_account": acct.is_intercompany_account,
            "definition": acct.definition,
            "sources": sorted(entry["sources"], key=lambda x: -abs(x["amount"])),
            # Always present on a flagged line, always empty otherwise: the frontend must never
            # need a second request to expand a row (SPEC 6.5).
            "contributions": ([_contribution(c, "in", who, approvers) for c in inbound] +
                              [_contribution(c, "out", who, approvers) for c in outbound]
                              ) if flagged else [],
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
                            "total_booked": _money(sum(Decimal(str(x["as_booked"])) for x in lines)),
                            "total_allocated": _money(sum(Decimal(str(x["as_allocated"])) for x in lines)),
                            "flagged": any(x["flagged"] for x in lines),
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
        "mode": mode,
        "threshold_pct": float(threshold),
        "sections": out_sections,
        # Every account whose money is NOT in the sections above, with the reason. The tie-out
        # still passes when something is excluded, so this list is the only thing standing
        # between a deliberate omission and an invisible one.
        "exclusions": sorted(exclusions, key=lambda x: -abs(x["amount"])),
        # Totals follow the mode, and both net-income figures ride along so a reader can see
        # what the toggle is worth without flipping it (SPEC 6.5).
        "totals": (_totals(out_sections, "amount") if statement == "pl" else {}),
        "net_income_booked": (_totals(out_sections, "as_booked")["net_income"]
                              if statement == "pl" else None),
        "net_income_allocated": (_totals(out_sections, "as_allocated")["net_income"]
                                 if statement == "pl" else None),
        "tie_out": {"status": "tied", "delta": _money(delta),
                    "mapped": _money(mapped_total), "booked": _money(booked_total - excluded),
                    "tolerance": _money(cfg.tie_out_tolerance),
                    "accounts": len(balances), "gross": _money(gross),
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




def _contribution(c, direction: str, who: dict, approvers: dict) -> dict:
    """One line of the composition panel. Allocated-out renders negative AND says "out", so
    direction never rests on the sign alone (SPEC 7.4)."""
    counterparty = c.source_business_id if direction == "in" else c.target_business_id
    return {
        "direction": direction,
        "counterparty_business": who.get(counterparty, "—"),
        "amount": _money(c.amount if direction == "in" else -c.amount),
        "pool_name": c.pool_name,
        "basis": c.basis,
        "driver_source": c.driver_source,
        "approved_by": approvers.get(c.approved_by),
        "je_ref": c.je_ref,
        "booking": c.booking,
    }


async def _books_closed(s: AsyncSession, tenant_id, business_id, period_start: dt.date) -> bool:
    """Has the month this period sits in been signed off in the close checklist? A quarter or
    a year is only as closed as its opening month, which is the conservative reading."""
    from ..models import ClosePeriod
    row = (await s.execute(select(ClosePeriod.status).where(
        ClosePeriod.tenant_id == tenant_id, ClosePeriod.business_id == business_id,
        ClosePeriod.period == period_start.replace(day=1)))).scalars().first()
    return row == "closed"


def _totals(sections: list[dict], key: str = "amount") -> dict:
    """The calculated lines. Subtotals are never accounts — posting to a subtotal is how the
    old ULRG chart ended up with balances on parents that appeared in none of their children.

    `key` chooses which per-line figure to total, so the same code produces the rendered totals
    and the booked/allocated pair. The mode toggle has to move net income, not just styling.
    """
    by: dict = {}
    for sec in sections:
        by[sec["key"]] = sum(
            (Decimal(str(line[key])) for b in sec["buckets"] for line in b["lines"]), ZERO)
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


# ── drilling into a line ──────────────────────────────────────────────────────────────────

async def line_detail(s: AsyncSession, tenant_id, business_id, standard_account_id,
                      period: tuple[dt.date, dt.date], limit: int = 400) -> dict:
    """The transactions behind one statement line, so a number can be audited rather than
    trusted.

    Two layers, because they come from different places and one is more reliable than the
    other. The ACCOUNTS come from the same trial balance the line is built from, so they always
    add up to it exactly. The TRANSACTIONS come from the ledger sync, and they are evidence
    rather than proof — `reconciliation` says how much of the line they actually explain.

    Why they can disagree, all reported rather than smoothed over:
      - A multi-line transaction is stored against its FIRST category with its FULL header
        amount. Summing by account therefore overstates it here and misses the other accounts
        it touched.
      - The ledger sync backfills from a start date; anything older than that has no rows.
      - Opening balances and QuickBooks-generated entries never existed as a transaction.

    The trial balance is the authority. This list is how you look at what is behind it.
    """
    ps, pe = period
    acct = await s.get(StandardAccount, standard_account_id)
    if acct is None or acct.tenant_id != tenant_id:
        raise ValueError("Unknown standard account")
    biz = await s.get(Business, business_id)
    if biz is None or biz.tenant_id != tenant_id:
        raise ValueError("Unknown business")

    maps = list((await s.execute(select(CoaMap).where(
        CoaMap.tenant_id == tenant_id, CoaMap.business_id == business_id,
        CoaMap.standard_account_id == standard_account_id))).scalars())
    by_qid = {m.qbo_account_id: m for m in maps}
    if not by_qid:
        return {"line": {"code": acct.code, "name": acct.name}, "accounts": [],
                "transactions": [], "reconciliation": {"note": "Nothing maps here yet."}}

    balances = dict((await s.execute(select(
        AccountPeriodBalance.qbo_account_id, AccountPeriodBalance.amount).where(
        AccountPeriodBalance.tenant_id == tenant_id,
        AccountPeriodBalance.business_id == business_id,
        AccountPeriodBalance.period_start == ps, AccountPeriodBalance.period_end == pe,
        AccountPeriodBalance.qbo_account_id.in_(list(by_qid))))).all())
    line_total = for_display(sum(balances.values(), ZERO), acct.section)

    from ..models import BookTxn
    txns = list((await s.execute(
        select(BookTxn)
        .where(BookTxn.tenant_id == tenant_id, BookTxn.business_id == business_id,
               BookTxn.txn_date >= ps, BookTxn.txn_date <= pe,
               BookTxn.account_qbo_id.in_(list(by_qid)))
        .order_by(BookTxn.txn_date.desc()))).scalars())

    per_account: dict = {}
    for t in txns:
        per_account[t.account_qbo_id] = per_account.get(t.account_qbo_id, 0) + 1
    txn_total = sum((t.amount for t in txns), ZERO)
    multi = sum(1 for t in txns if (t.flags or {}).get("multi_line"))
    delta = line_total - txn_total

    reasons = []
    if multi:
        reasons.append(f"{multi} multi-line transaction(s) are counted at their full header "
                       f"amount against their first category, which OVERSTATES them here")
    if not txns and line_total:
        reasons.append("the ledger sync has no transactions in this window — it backfills from "
                       "a start date, and QuickBooks-generated entries never had one")
    if abs(delta) > Decimal("0.01") and not reasons:
        reasons.append("the difference is journal entries, opening balances, or activity "
                       "outside the ledger sync's backfill window")

    return {
        "line": {"standard_account_id": str(acct.id), "code": acct.code, "name": acct.name,
                 "section": acct.section, "amount": _money(line_total),
                 "definition": acct.definition},
        "business": {"id": str(biz.id), "key": biz.key, "name": biz.name},
        "period": {"start": ps.isoformat(), "end": pe.isoformat()},
        # Always ties to the line, because it comes from the same trial balance.
        "accounts": sorted(
            [{"qbo_account_id": qid,
              "fqn": by_qid[qid].qbo_account_fqn or by_qid[qid].qbo_account_name,
              "type": by_qid[qid].qbo_account_type,
              "mapped_via": by_qid[qid].mapped_via,
              "amount": _money(for_display(balances.get(qid, ZERO), acct.section)),
              "transactions": per_account.get(qid, 0)}
             for qid in by_qid],
            key=lambda a: -abs(a["amount"])),
        "transactions": [{
            "id": str(t.id), "date": t.txn_date.isoformat(), "qbo_type": t.qbo_type,
            "payee": t.payee, "memo": t.memo, "amount": _money(t.amount),
            "account": t.account_label, "bank_account": t.bank_account_label,
            "multi_line": bool((t.flags or {}).get("multi_line")),
            "scan_state": t.scan_state,
            "qbo_url": qbo.app_txn_url(t.qbo_type, t.qbo_id),
        } for t in sorted(txns, key=lambda x: -abs(x.amount))[:limit]],
        "truncated": max(len(txns) - limit, 0),
        "reconciliation": {
            "line_total": _money(line_total),
            "transaction_total": _money(txn_total),
            "delta": _money(delta),
            "explained": abs(delta) <= Decimal("0.01"),
            "transactions": len(txns),
            "multi_line": multi,
            "note": ("These transactions account for the whole line."
                     if abs(delta) <= Decimal("0.01") else
                     "These transactions do not add up to the line. "
                     + "; ".join(reasons).capitalize()
                     + ". The trial balance is the authority — this list is evidence."),
        },
    }
