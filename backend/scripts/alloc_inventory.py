"""Phase 0 of the allocation-rules build: look before touching anything.

WHY THIS EXISTS. The spec plans a module around entities, realms, vendors and transaction
history that nobody has counted. Several of its decisions only make sense if the data is shaped
the way it assumes, and three of its findings (F10, F14, and the whole of Tier 2) are explicitly
"unverifiable from the repo". This answers them from the database instead of from the code.

It writes NOTHING. Every statement is a SELECT, and it never calls QuickBooks or Meta -- the
fiscal-year month, which the real sync reads from QBO, is a flag here precisely so this stays
offline.

WHAT IT REPORTS
  1. Every Business: key, name, kind, portfolio flag, sort order, accent. F14 asks which rows
     exist and which carries SB Coaching's realm; only production can say.
  2. Every QBO integration with its business and realm, because "per realm per pass" in the
     call-count measurement means per one of these.
  3. Existing AllocationRule rows, so Phase 1's additive migration can be checked against what
     it has to leave alone.
  4. BookTxn coverage per business: first and last transaction date, count, and the share
     carrying flags.multi_line. The first date BOUNDS how far a retroactive rule can reach
     (F10), and the multi_line share sizes the exception queue that D7 sends them to.
  5. Ad accounts and their campaign-name counts, which is what a Tier 2 Meta rule matches on.
  6. CoaSettings as it stands, before Phase 1 adds alloc_pool_business_ids.
  7. The as-of pull count per realm per pass, today versus with monthly grain added. Arithmetic,
     not calls: period activity is the difference of two as-of trial balances and the cache is
     keyed by date, so the cost of a pass is the number of DISTINCT dates it needs.
  8. The BookTxn sign check: for one closed month on one realm, does the sum of BookTxn.amount
     on one expense account match that month's AccountPeriodBalance? Phase 4 computes vendor
     pools from BookTxn and account pools from AccountPeriodBalance, and if their signs
     disagree every vendor carve-out would be backwards.

Usage (reads DATABASE_URL; point it at a read-only replica or a restored copy):
  cd backend && ./.venv/Scripts/python.exe -m scripts.alloc_inventory --tenant springb
  ... --fy-month 1          # fiscal year starts in January (the real value comes from QBO)
  ... --sign-check-months 3 # how many recent closed months to try for check 8
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from decimal import Decimal

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import (AccountPeriodBalance, AdAccount, AdCampaign, AllocationContribution,
                        AllocationRule, BookTxn, Business, CoaMap, CoaSettings, Integration,
                        StandardAccount, Tenant)


def _rule(label: str) -> None:
    print(f"\n{'=' * 78}\n{label}\n{'=' * 78}")


def _money(v) -> str:
    return f"{Decimal(str(v or 0)):,.2f}"


def allocation_months(today: dt.date, fy_month: int) -> list[tuple[dt.date, dt.date]]:
    """Every calendar month from the fiscal year start through the month containing `today`.

    Lifted from the spec's section 5.2 so the call-count projection measures the real thing. It
    is duplicated here rather than imported because Phase 0 ships no product code.
    """
    year = today.year if today.month >= fy_month else today.year - 1
    out, cur = [], dt.date(year, fy_month, 1)
    last = today.replace(day=1)
    while cur <= last:
        nxt = dt.date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
        out.append((cur, nxt - dt.timedelta(days=1)))
        cur = nxt
    return out


def _as_of_dates(periods) -> set[dt.date]:
    """The distinct dates a pass must pull. One per period end, one per day-before-start."""
    dates: set[dt.date] = set()
    for ps, pe in periods:
        dates.add(pe)
        dates.add(ps - dt.timedelta(days=1))
    return dates


async def main(slug: str, fy_month: int, sign_months: int) -> None:
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if t is None:
            raise SystemExit(f"[error] no workspace with slug {slug!r}")
        print(f"workspace: {t.slug}  ({t.name})")
        print(f"today    : {dt.date.today().isoformat()}   fy_month assumed: {fy_month}")

        # ── 1. businesses ─────────────────────────────────────────────────────────────────
        _rule("1. Businesses (F14: which rows exist, and what they are called)")
        biz = (await s.execute(select(Business).where(Business.tenant_id == t.id)
                               .order_by(Business.sort_order, Business.key))).scalars().all()
        print("   %-14s %-26s %-16s %-9s %-6s %s"
              % ("key", "name", "kind", "portfolio", "sort", "accent"))
        for b in biz:
            print("   %-14s %-26s %-16s %-9s %-6s %s"
                  % (b.key, (b.name or "")[:26], b.kind, b.include_in_portfolio,
                     getattr(b, "sort_order", "-"), b.accent))
        print(f"   [{len(biz)} businesses]")

        # ── 2. QBO integrations ───────────────────────────────────────────────────────────
        _rule("2. QBO integrations (one 'realm per pass' each)")
        integs = (await s.execute(select(Integration).where(
            Integration.tenant_id == t.id, Integration.provider == "qbo"))).scalars().all()
        bname = {b.id: f"{b.key} ({b.name})" for b in biz}
        for i in integs:
            print("   realm=%-22s business=%-30s status=%-10s last_sync=%s"
                  % (i.realm_id or "-", bname.get(i.business_id, "(none)"), i.status,
                     i.last_synced_at))
        print(f"   [{len(integs)} QBO integrations]")

        # ── 3. existing allocation rows ───────────────────────────────────────────────────
        _rule("3. Allocation rules and contributions that already exist")
        rules = (await s.execute(select(AllocationRule).where(
            AllocationRule.tenant_id == t.id))).scalars().all()
        for r in rules:
            print("   pool=%-24s pattern=%-34s src=%-16s tgt=%-16s active=%s"
                  % ((r.pool_name or "")[:24], (r.pattern or "")[:34],
                     bname.get(r.source_business_id, "-").split(" ")[0],
                     bname.get(r.target_business_id, "ALL").split(" ")[0], r.is_active))
            print("        basis=%r driver_source=%r je_ref=%r"
                  % (r.basis, r.driver_source, r.je_ref))
        print(f"   [{len(rules)} rules]")
        rows = (await s.execute(select(AllocationContribution.booking, func.count(),
                                       func.sum(AllocationContribution.amount))
                                .where(AllocationContribution.tenant_id == t.id)
                                .group_by(AllocationContribution.booking))).all()
        print("   contributions by booking:", [(b, n, _money(a)) for b, n, a in rows] or "none")

        # ── 4. BookTxn coverage ───────────────────────────────────────────────────────────
        _rule("4. BookTxn coverage per business (F10: how far back a retro rule can reach)")
        print("   %-14s %-8s %-12s %-12s %-10s %s"
              % ("business", "rows", "first", "last", "multi_line", "share"))
        for b in biz:
            n, lo, hi = (await s.execute(select(
                func.count(BookTxn.id), func.min(BookTxn.txn_date), func.max(BookTxn.txn_date)
            ).where(BookTxn.tenant_id == t.id, BookTxn.business_id == b.id))).one()
            if not n:
                continue
            ml = (await s.execute(select(func.count(BookTxn.id)).where(
                BookTxn.tenant_id == t.id, BookTxn.business_id == b.id,
                BookTxn.flags["multi_line"].as_boolean().is_(True)))).scalar_one()
            print("   %-14s %-8s %-12s %-12s %-10s %.1f%%"
                  % (b.key, n, lo, hi, ml, (ml / n * 100) if n else 0))

        # ── 5. ad accounts ────────────────────────────────────────────────────────────────
        _rule("5. Ad accounts and campaign names (what a Tier 2 Meta rule matches on)")
        accounts = (await s.execute(select(AdAccount).where(
            AdAccount.tenant_id == t.id))).scalars().all()
        for a in accounts:
            n = (await s.execute(select(func.count(func.distinct(AdCampaign.name))).where(
                AdCampaign.ad_account_id == a.id))).scalar_one()
            print("   platform=%-7s external=%-26s business=%-22s distinct_campaigns=%s"
                  % (a.platform, a.external_id, bname.get(a.business_id, "(none)")[:22], n))
        print(f"   [{len(accounts)} ad accounts]")

        # ── 6. settings ───────────────────────────────────────────────────────────────────
        _rule("6. CoaSettings as it stands (before Phase 1 adds alloc_pool_business_ids)")
        cs = (await s.execute(select(CoaSettings).where(
            CoaSettings.tenant_id == t.id))).scalars().first()
        if cs is None:
            print("   NO ROW. get_settings() would create one at the spec defaults on first read.")
        else:
            for col in cs.__table__.columns:
                if col.name not in ("id", "tenant_id"):
                    print("   %-28s %s" % (col.name, getattr(cs, col.name)))

        # ── 7. the call-count projection ──────────────────────────────────────────────────
        _rule("7. As-of pulls per realm per pass: today vs with monthly grain")
        from app.services.books_sync import _pl_line_periods
        today = dt.date.today()
        windows = _pl_line_periods()
        months = allocation_months(today, fy_month)
        now_dates = _as_of_dates(windows)
        both_dates = _as_of_dates(list(windows) + months)
        print("   window periods today      : %s" % len(windows))
        for ps, pe in windows:
            print("        %s .. %s" % (ps, pe))
        print("   month periods to be added : %s  (%s .. %s)"
              % (len(months), months[0][0] if months else "-", months[-1][1] if months else "-"))
        print("   distinct as-of dates now  : %s" % len(now_dates))
        print("   distinct as-of dates after: %s" % len(both_dates))
        print("   ADDED pulls per realm/pass: %s  (x %s realms = %s)"
              % (len(both_dates) - len(now_dates), len(integs),
                 (len(both_dates) - len(now_dates)) * len(integs)))
        print("   note: sync_trial_balances already logs its real count as as_of_pulls=; compare")
        print("         this projection against that line in the worker log after Phase 2.")

        # ── 8. the sign check ─────────────────────────────────────────────────────────────
        _rule("8. BookTxn sign vs AccountPeriodBalance (Phase 4 mixes the two)")
        done = 0
        for b in biz:
            if done >= 1:
                break
            # A month that already has balance rows, newest first.
            keys = (await s.execute(select(AccountPeriodBalance.period_start,
                                           AccountPeriodBalance.period_end)
                                    .where(AccountPeriodBalance.tenant_id == t.id,
                                           AccountPeriodBalance.business_id == b.id)
                                    .distinct().order_by(AccountPeriodBalance.period_start.desc())
                                    .limit(sign_months))).all()
            for ps, pe in keys:
                if (pe - ps).days > 40:          # a month, not a quarter or a year
                    continue
                pairs = (await s.execute(
                    select(AccountPeriodBalance.qbo_account_id, AccountPeriodBalance.amount)
                    .where(AccountPeriodBalance.tenant_id == t.id,
                           AccountPeriodBalance.business_id == b.id,
                           AccountPeriodBalance.period_start == ps,
                           AccountPeriodBalance.period_end == pe))).all()
                for acct, apb_amount in pairs:
                    if not apb_amount or abs(Decimal(str(apb_amount))) < 100:
                        continue
                    txn = (await s.execute(select(func.sum(BookTxn.amount)).where(
                        BookTxn.tenant_id == t.id, BookTxn.business_id == b.id,
                        BookTxn.account_qbo_id == acct,
                        BookTxn.txn_date >= ps, BookTxn.txn_date <= pe))).scalar_one()
                    if txn is None:
                        continue
                    apb_d, txn_d = Decimal(str(apb_amount)), Decimal(str(txn))
                    same = (apb_d > 0) == (txn_d > 0)
                    print("   %s %s..%s account=%s" % (b.key, ps, pe, acct))
                    print("        AccountPeriodBalance : %s" % _money(apb_d))
                    print("        sum(BookTxn.amount)  : %s" % _money(txn_d))
                    print("        signs agree          : %s" % same)
                    print("        abs difference       : %s" % _money(abs(abs(apb_d) - abs(txn_d))))
                    print("        READ THIS AS: BookTxn is %s on an expense account."
                          % ("debit-positive, same as APB" if same
                             else "OPPOSITE in sign to APB -- Phase 4 must negate one side"))
                    done += 1
                    break
                if done:
                    break
        if not done:
            print("   NO COMPARABLE MONTH FOUND. Either no monthly AccountPeriodBalance rows")
            print("   exist yet (expected before Phase 2) or no expense account has both a")
            print("   balance over 100 and BookTxn rows in the same month. Re-run after Phase 2,")
            print("   or widen --sign-check-months.")

        _rule("Mapped-account coverage, for context on the exception queue")
        mapped = (await s.execute(select(func.count(CoaMap.id)).where(
            CoaMap.tenant_id == t.id))).scalar_one()
        std = (await s.execute(select(func.count(StandardAccount.id)))).scalar_one()
        print("   coa_map rows: %s    standard accounts: %s" % (mapped, std))
        print("\n[done] nothing was written.")


def cli() -> None:
    ap = argparse.ArgumentParser(description="Phase 0 inventory for the allocation module")
    ap.add_argument("--tenant", required=True, help="workspace slug, e.g. springb")
    ap.add_argument("--fy-month", type=int, default=1,
                    help="fiscal year start month, 1-12 (the sync reads this from QBO)")
    ap.add_argument("--sign-check-months", type=int, default=3,
                    help="how many recent balance periods to try for the sign check")
    a = ap.parse_args()
    if not 1 <= a.fy_month <= 12:
        raise SystemExit("[error] --fy-month must be 1 to 12")
    asyncio.run(main(a.tenant.strip().lower(), a.fy_month, a.sign_check_months))


if __name__ == "__main__":
    cli()
