"""Commissions for the beCollective Sales Desk.

Commission is owed on cash that ARRIVED, never on contracts signed. A $14,400 monthly plan
pays its rep $1,200 at a time, so the unit here is one payment and a member reappears every
month they keep paying. Reading commission off `monetaryValue` would pay a rep the whole
$14,400 the day the member signed and again every month after.

Two guarantees, and everything else is reporting:

  A payment is recorded ONCE. The provider's transaction id is a unique key, so a re-sync
  cannot create a second row for one charge however many times it runs.

  A payment is PAID ONCE. Closing a month stamps `statement_id` on its payments and freezes
  the rate onto each row. A later sync must not move, re-rate or re-attribute a locked
  payment, however much the opportunity changes afterwards — somebody switching plan or rep
  in GHL in November must not restate the invoice that went out in October.

Money arriving outside the payment provider is invisible here. That is a real gap and the
report says so rather than implying the provider is the whole truth.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations import ghl
from ..models import (CommissionPayment, CommissionStatement, MetricRecord, SalesCall,
                      SalesRep, Vendor)

CENTS = Decimal("0.01")


# GHL opportunity names are "Amy Dolan - +14095546223". The phone is not wanted on a report
# that leaves the building for an agency to invoice from, and the SalesCall contact name is
# not always there to fall back on.
_PHONE_TAIL = re.compile(r"\s*[-–]\s*[+(]?\d[\d()\s.\-]{6,}$")


def clean_name(raw) -> str | None:
    return _PHONE_TAIL.sub("", (raw or "").strip()).strip() or None


def _money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(CENTS, rounding=ROUND_HALF_UP)


def _tz(name: str) -> dt.tzinfo:
    try:
        return ZoneInfo(name or "America/Denver")
    except Exception:                                   # noqa: BLE001 — a bad tz is not fatal
        return ZoneInfo("America/Denver")


def period_of(when: dt.datetime, tz: dt.tzinfo) -> str:
    """The month a payment belongs to, in the launch's own timezone.

    A payment at 6pm Mountain on the 31st is UTC the 1st of the next month. Bucketing on UTC
    would move that dollar into the following invoice.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    local = when.astimezone(tz)
    return f"{local.year:04d}-{local.month:02d}"


def period_bounds(period: str, tz: dt.tzinfo) -> tuple[dt.datetime, dt.datetime]:
    """[start, end) in UTC for a YYYY-MM period in the launch timezone."""
    y, m = (int(x) for x in period.split("-"))
    start = dt.datetime(y, m, 1, tzinfo=tz)
    end = dt.datetime(y + (m == 12), (m % 12) + 1, 1, tzinfo=tz)
    return start.astimezone(dt.timezone.utc), end.astimezone(dt.timezone.utc)


def current_period(launch, now: dt.datetime | None = None) -> str:
    return period_of(now or dt.datetime.now(dt.timezone.utc), _tz(launch.default_tz))


# ── sync ──────────────────────────────────────────────────────────────────────────────────

async def _attribution(s: AsyncSession, tenant_id, launch) -> dict:
    """contact_id -> who this member is and who closed them.

    The Sales Desk event log is the authority on the rep and the payment type; the launch
    opportunity snapshot is what says a contact belongs to THIS cohort at all. Both are
    needed: scoping on the snapshot keeps another programme's payments on the same GHL
    location out of a beCollective invoice.
    """
    opp_meta = {}
    for r in (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == tenant_id, MetricRecord.source == "ghl",
            MetricRecord.kind == "bc_launch_opp"))).scalars():
        meta = r.meta or {}
        if meta.get("launch_id") != str(launch.id):
            continue
        cid = meta.get("contact_id")
        if cid:
            opp_meta[cid] = {"opportunity_id": r.external_id, "member_name": r.name,
                             "group": meta.get("group")}
    calls = {}
    for c in (await s.execute(select(SalesCall).where(
            SalesCall.launch_id == launch.id, SalesCall.is_current.is_(True)))).scalars():
        if c.contact_id:
            calls[c.contact_id] = c
    out = {}
    for cid, m in opp_meta.items():
        c = calls.get(cid)
        out[cid] = {
            "opportunity_id": m["opportunity_id"],
            "member_name": (clean_name(c.contact_name) if c and c.contact_name
                            else m["member_name"]),
            "rep_email": (c.rep_email if c else None),
            "payment_type": (c.payment_type if c else None),
        }
    return out


async def sync_commission_payments(s: AsyncSession, tenant_id, launch, token: str,
                                   location_id: str) -> dict:
    """Record every succeeded payment from a cohort member, once.

    Locked payments are never rewritten. An unlocked one is refreshed, because an opportunity
    that gains its Sales Rep on Tuesday should attribute Monday's cash without anyone
    re-entering it.
    """
    try:
        txns = await ghl.ghl_transactions(token, location_id)
    except Exception as e:                              # noqa: BLE001 — never break the sync
        print(f"[commissions] could not read transactions: {type(e).__name__}: {e}", flush=True)
        return {}
    who = await _attribution(s, tenant_id, launch)
    existing = {p.external_id: p for p in (await s.execute(select(CommissionPayment).where(
        CommissionPayment.tenant_id == tenant_id,
        CommissionPayment.launch_id == launch.id))).scalars()}

    stat: dict = {}
    for t in txns:
        if (t.get("status") or "").lower() != "succeeded":
            continue                                    # a failed charge is not cash
        cid = t.get("contactId")
        member = who.get(cid)
        if not member:
            continue                                    # not a member of this cohort
        net = _money(t.get("amount")) - _money(t.get("amountRefunded"))
        if net <= 0:
            continue                                    # fully refunded, or a $0 test row
        ext = str(t.get("_id") or t.get("id") or "")
        if not ext:
            stat["no_transaction_id"] = stat.get("no_transaction_id", 0) + 1
            continue
        paid_at = _parse_dt(t.get("createdAt"))
        if paid_at is None:
            stat["no_paid_at"] = stat.get("no_paid_at", 0) + 1
            continue
        row = existing.get(ext)
        if row is None:
            s.add(CommissionPayment(
                tenant_id=tenant_id, launch_id=launch.id, external_id=ext, provider="ghl",
                contact_id=cid, opportunity_id=member["opportunity_id"],
                member_name=member["member_name"] or t.get("contactName"),
                rep_email=member["rep_email"], payment_type=member["payment_type"],
                amount=net, paid_at=paid_at))
            stat["recorded"] = stat.get("recorded", 0) + 1
            continue
        if row.statement_id is not None:
            continue                                    # closed and invoiced — frozen
        changed = False
        for field, value in (("rep_email", member["rep_email"]),
                             ("payment_type", member["payment_type"]),
                             ("member_name", member["member_name"] or t.get("contactName")),
                             ("opportunity_id", member["opportunity_id"]),
                             ("amount", net), ("paid_at", paid_at)):
            if value is not None and getattr(row, field) != value:
                setattr(row, field, value)
                changed = True
        if changed:
            stat["updated"] = stat.get("updated", 0) + 1
    if stat:
        await s.commit()
    return stat


def _parse_dt(v):
    if isinstance(v, dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)
    try:
        d = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


# ── report ────────────────────────────────────────────────────────────────────────────────

async def _rates(s: AsyncSession, tenant_id) -> tuple[dict, dict]:
    rows = (await s.execute(select(SalesRep).where(SalesRep.tenant_id == tenant_id))).scalars()
    rate, name = {}, {}
    for r in rows:
        key = (r.email or "").lower()
        rate[key] = r.commission_pct
        name[key] = r.display_name
    return rate, name


def _commission(amount: Decimal, pct) -> Decimal | None:
    if pct is None:
        return None
    return _money(Decimal(str(amount)) * Decimal(str(pct)) / Decimal("100"))


async def month_report(s: AsyncSession, tenant_id, launch, period: str) -> dict:
    """Every payment that landed in `period`, with who gets paid for it and whether it is settled."""
    tz = _tz(launch.default_tz)
    start, end = period_bounds(period, tz)
    payments = (await s.execute(select(CommissionPayment).where(
        CommissionPayment.tenant_id == tenant_id,
        CommissionPayment.launch_id == launch.id,
        CommissionPayment.paid_at >= start,
        CommissionPayment.paid_at < end).order_by(CommissionPayment.paid_at))).scalars().all()
    stmt = (await s.execute(select(CommissionStatement).where(
        CommissionStatement.tenant_id == tenant_id,
        CommissionStatement.launch_id == launch.id,
        CommissionStatement.period == period))).scalar_one_or_none()
    rate_by, name_by = await _rates(s, tenant_id)

    rows, exceptions = [], []
    for p in payments:
        key = (p.rep_email or "").lower()
        locked = p.statement_id is not None
        # A locked row reports the rate it was INVOICED at, not today's. That is the whole
        # point of freezing it: editing a rate must not restate an invoice already sent.
        pct = p.rate_pct if locked else rate_by.get(key)
        amount = _money(p.amount)
        owed = p.commission_amount if locked else _commission(amount, pct)
        if not p.rep_email:
            exceptions.append({"member": p.member_name, "issue": "no sales rep on the opportunity",
                               "amount": float(amount)})
        elif pct is None:
            exceptions.append({"member": p.member_name,
                               "issue": f"no commission rate set for {p.rep_email}",
                               "amount": float(amount)})
        rows.append({
            "payment_id": str(p.id), "member": p.member_name,
            "rep_email": p.rep_email, "rep": name_by.get(key) or p.rep_email,
            "payment_type": p.payment_type, "cash": float(amount),
            "paid_on": p.paid_at.astimezone(tz).date().isoformat(),
            "rate_pct": float(pct) if pct is not None else None,
            "commission": float(owed) if owed is not None else None,
            "locked": locked,
        })

    by_rep: dict = {}
    for r in rows:
        k = r["rep_email"] or "(unassigned)"
        b = by_rep.setdefault(k, {"rep_email": r["rep_email"], "rep": r["rep"] or "(unassigned)",
                                  "members": set(), "payments": 0, "cash": 0.0,
                                  "commission": 0.0, "rates": set()})
        b["members"].add(r["member"])
        b["payments"] += 1
        b["cash"] += r["cash"]
        b["commission"] += r["commission"] or 0.0
        b["rates"].add(r["rate_pct"])

    def _rate_of(b):
        """One rate, or none to report. A rep can legitimately hold two in a month — rows
        locked at the old rate beside open ones at the new — and showing either as THE rate
        would misstate half of them."""
        rates = {x for x in b["rates"] if x is not None}
        return (rates.pop() if len(rates) == 1 else None), len(rates) > 1

    reps = []
    for b in by_rep.values():
        rate, mixed = _rate_of(b)
        reps.append({k: v for k, v in b.items() if k != "rates"} |
                    {"members": len(b["members"]), "cash": round(b["cash"], 2),
                     "commission": round(b["commission"], 2),
                     "rate_pct": rate, "rate_mixed": mixed})
    reps.sort(key=lambda r: -r["cash"])

    payable_cash = sum(r["cash"] for r in rows if r["commission"] is not None)
    return {
        "period": period, "timezone": launch.default_tz or "America/Denver",
        "rows": rows, "by_rep": reps,
        "total_cash": round(sum(r["cash"] for r in rows), 2),
        "total_commission": round(sum(r["commission"] or 0.0 for r in rows), 2),
        "payable_cash": round(payable_cash, 2),
        "unresolved": exceptions,
        "closed": stmt is not None,
        "closed_at": stmt.closed_at.isoformat() if stmt and stmt.closed_at else None,
        "payable_id": str(stmt.payable_id) if stmt and stmt.payable_id else None,
        "locked_payments": sum(1 for r in rows if r["locked"]),
        # Said out loud rather than implied: this is what the payment provider saw.
        "source_note": "Succeeded payments in the GHL payments account, net of refunds. "
                       "Money collected outside it does not appear here.",
    }


async def periods_with_activity(s: AsyncSession, tenant_id, launch) -> list[str]:
    """Months that have any payment, newest first — what the month picker offers."""
    tz = _tz(launch.default_tz)
    rows = (await s.execute(select(CommissionPayment.paid_at).where(
        CommissionPayment.tenant_id == tenant_id,
        CommissionPayment.launch_id == launch.id))).scalars().all()
    return sorted({period_of(d, tz) for d in rows}, reverse=True)


# ── close ─────────────────────────────────────────────────────────────────────────────────

COMMISSION_INVOICE_PREFIX = "BC-COMM"


async def close_month(s: AsyncSession, tenant_id, launch, period: str, vendor_id, actor) -> dict:
    """Settle a month: lock its payments, freeze their rates, draft the bill.

    Only payments that can be priced are locked. One with no rep, or a rep with no rate, stays
    OPEN and keeps showing as unresolved — closing it at zero would quietly write off somebody's
    commission, and blocking the whole month on it would hold the other reps' pay hostage to a
    field nobody has filled in.
    """
    from .payables import create_payable

    existing = (await s.execute(select(CommissionStatement).where(
        CommissionStatement.tenant_id == tenant_id,
        CommissionStatement.launch_id == launch.id,
        CommissionStatement.period == period))).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"{period} is already closed — reopen it before closing again")

    tz = _tz(launch.default_tz)
    start, end = period_bounds(period, tz)
    payments = (await s.execute(select(CommissionPayment).where(
        CommissionPayment.tenant_id == tenant_id,
        CommissionPayment.launch_id == launch.id,
        CommissionPayment.statement_id.is_(None),
        CommissionPayment.paid_at >= start,
        CommissionPayment.paid_at < end))).scalars().all()
    rate_by, _ = await _rates(s, tenant_id)

    settle, skipped = [], []
    for p in payments:
        pct = rate_by.get((p.rep_email or "").lower())
        if not p.rep_email or pct is None:
            skipped.append({"member": p.member_name, "cash": float(_money(p.amount)),
                            "reason": ("no sales rep on the opportunity" if not p.rep_email
                                       else f"no commission rate set for {p.rep_email}")})
            continue
        settle.append((p, pct))
    if not settle:
        raise ValueError(f"nothing to close for {period} — no payment has both a rep and a rate")

    total_cash = sum(_money(p.amount) for p, _ in settle)
    total_comm = sum(_commission(_money(p.amount), pct) for p, pct in settle)

    vendor = (await s.execute(select(Vendor).where(
        Vendor.tenant_id == tenant_id, Vendor.id == vendor_id))).scalar_one_or_none()
    if vendor is None:
        raise ValueError("pick the vendor the commission invoice is owed to")
    invoice_number = f"{COMMISSION_INVOICE_PREFIX}-{period}"

    # Reuse rather than collide. A close that failed after the bill was written would otherwise
    # be unrepeatable: create_payable refuses the duplicate invoice number and the month can
    # never be closed.
    from ..models import Payable
    bill = (await s.execute(select(Payable).where(
        Payable.tenant_id == tenant_id, Payable.vendor_id == vendor.id,
        Payable.invoice_number == invoice_number))).scalars().first()
    if bill is None:
        created = await create_payable(s, tenant_id, actor, {
            "vendor_id": str(vendor.id), "invoice_number": invoice_number,
            "invoice_date": end.astimezone(tz).date() - dt.timedelta(days=1),
            "amount": str(total_comm), "currency": "USD",
            "description": (f"beCollective sales commission, {period} — "
                            f"{len(settle)} payments, ${total_cash:,.2f} collected"),
            "source": "commissions",
        })
        bill_id = created["id"] if isinstance(created, dict) else None
    else:
        bill_id = str(bill.id)

    stmt = CommissionStatement(
        tenant_id=tenant_id, launch_id=launch.id, period=period,
        total_cash=total_cash, total_commission=total_comm, payment_count=len(settle),
        vendor_id=vendor.id, payable_id=bill_id,
        closed_by=getattr(actor, "id", None))
    s.add(stmt)
    await s.flush()
    for p, pct in settle:
        p.statement_id = stmt.id
        p.rate_pct = pct
        p.commission_amount = _commission(_money(p.amount), pct)
    await s.commit()
    return {"period": period, "payments_locked": len(settle),
            "total_cash": float(total_cash), "total_commission": float(total_comm),
            "payable_id": str(bill_id) if bill_id else None,
            "invoice_number": invoice_number, "left_open": skipped}


async def reopen_month(s: AsyncSession, tenant_id, launch, period: str) -> dict:
    """Undo a close: unlock the payments and drop the statement.

    The drafted bill is deliberately NOT deleted. Money may already have moved against it, and
    a reconciliation tool that can silently erase a bill is worse than one that cannot reopen.
    """
    stmt = (await s.execute(select(CommissionStatement).where(
        CommissionStatement.tenant_id == tenant_id,
        CommissionStatement.launch_id == launch.id,
        CommissionStatement.period == period))).scalar_one_or_none()
    if stmt is None:
        raise ValueError(f"{period} is not closed")
    rows = (await s.execute(select(CommissionPayment).where(
        CommissionPayment.statement_id == stmt.id))).scalars().all()
    for p in rows:
        p.statement_id, p.rate_pct, p.commission_amount = None, None, None
    payable_id = str(stmt.payable_id) if stmt.payable_id else None
    await s.delete(stmt)
    await s.commit()
    return {"period": period, "payments_unlocked": len(rows), "payable_id": payable_id,
            "note": "the drafted bill was left in Payables — void it there if it is not owed"}


# ── csv ───────────────────────────────────────────────────────────────────────────────────

CSV_COLUMNS = ["Member", "Sales Rep", "Rep Email", "Payment Type", "Paid On",
               "Cash Collected", "Rate %", "Commission Owed", "Status"]


def report_csv(report: dict) -> str:
    """The month as a spreadsheet, including the totals somebody will otherwise re-add by hand."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for r in report["rows"]:
        w.writerow([r["member"], r["rep"] or "(unassigned)", r["rep_email"] or "",
                    r["payment_type"] or "", r["paid_on"], f"{r['cash']:.2f}",
                    "" if r["rate_pct"] is None else f"{r['rate_pct']:g}",
                    "" if r["commission"] is None else f"{r['commission']:.2f}",
                    "Invoiced" if r["locked"] else "Open"])
    w.writerow([])
    w.writerow(["TOTAL", "", "", "", "", f"{report['total_cash']:.2f}", "",
                f"{report['total_commission']:.2f}",
                "Closed" if report["closed"] else "Open"])
    if report["unresolved"]:
        w.writerow([])
        w.writerow(["UNRESOLVED — not included in the commission total"])
        for x in report["unresolved"]:
            w.writerow([x["member"], "", "", "", "", f"{x['amount']:.2f}", "", "", x["issue"]])
    w.writerow([])
    w.writerow([report["source_note"]])
    return buf.getvalue()
