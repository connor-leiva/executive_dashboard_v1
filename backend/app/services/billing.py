"""The Forum · Cash & Billing — pure classification + aggregation over the GHL
Payments records (kind='payment', enriched 'subscription'). No I/O; every function
is unit-testable. The one computation source for MRR / cash / streams so the top
KPI tile and the Cash & Billing section can never disagree (spec decision 0.7).

Cash basis (v1): every dollar is a Stripe charge that happened. See
spring-command-center-SPEC-forum-billing.md.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

# Stream labels (Section 2.1) — display names live on the frontend.
STREAM_LABELS = {"memberships": "Memberships", "event_tickets": "Event tickets",
                 "sponsorships": "Sponsorships", "invoices": "Invoices", "other": "Other"}

_PAY_RE = re.compile(r"(\d+)\s*[-\s]?pay", re.I)   # "3 pay", "3-pay", "3pay"


def classify_stream(name: str | None, overrides: dict | None = None) -> str:
    """entitySourceName / plan name → revenue stream. Ordered, first match wins;
    an exact-name config override beats everything (Section 2.1)."""
    overrides = overrides or {}
    if name in overrides:
        return overrides[name]
    n = (name or "").lower()
    if "sponsor" in n:
        return "sponsorships"
    if any(x in n for x in ("ticket", "vip", "rsvp", "guest")):
        return "event_tickets"
    if "invoice" in n:
        return "invoices"
    if any(x in n for x in ("membership", "subscription for", "financed", "pif", "pay")):
        return "memberships"
    return "other"


def _pdate(v) -> dt.date | None:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def classify_installment(plan_name: str | None, start_date, end_date,
                         config: dict | None = None) -> tuple[str, int | None]:
    """A subscription is an installment (finite N-pay), not perpetual MRR, when
    (Section 2.2): its name matches /(N) pay/, it has a finite term (end within
    ~13 months of start), or its name is in config.installment_plan_names.
    Returns (sub_type, installments_total)."""
    config = config or {}
    name = plan_name or ""
    m = _PAY_RE.search(name)
    if m:
        return "installment", int(m.group(1))
    if name in (config.get("installment_plan_names") or []):
        return "installment", None
    sd, ed = _pdate(start_date), _pdate(end_date)
    if sd and ed and (ed - sd).days <= 400:            # finite term ~13 months
        return "installment", None
    return "perpetual", None


def sub_monthly(sub) -> float:
    """Monthly amount of a subscription record — normalized to monthly by interval.
    The Forum's amounts are already whole-dollar monthly (verified against charges)."""
    amt = float(sub.amount or 0)
    interval = str((sub.meta or {}).get("interval") or "month").lower()
    return round(amt / 12, 2) if ("year" in interval or "annual" in interval) else amt


def is_perpetual(sub) -> bool:
    return (sub.meta or {}).get("sub_type") != "installment"


def mrr_of(subs) -> float:
    """True MRR = active perpetual subscriptions only (installments excluded).
    THE single MRR source — top-row tile and billing block both call this."""
    return round(sum(sub_monthly(x) for x in subs
                     if x.status == "active" and is_perpetual(x)), 2)


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def compute_billing(payments, subs, arr_book: float,
                    period_start: dt.date, period_end: dt.date, today: dt.date) -> dict:
    """Assemble the Forum billing block from `payment` + enriched `subscription`
    records (Section 4). `available` is False when there are no payment rows."""
    if not payments:
        return {"available": False}

    dated = [p for p in payments if p.occurred_on]
    span_start = min((p.occurred_on for p in dated), default=period_start)
    succ = [p for p in payments if (p.status or "") == "succeeded"]

    # Tile numbers cover the full payments span (the section is a stable cash
    # overview, like MRR/ARR — point-in-time, not period-scoped by the selector).
    gross = round(sum(_num(p.amount) for p in succ), 2)
    refunded = round(sum(_num((p.meta or {}).get("amount_refunded")) for p in payments), 2)
    net_cash = round(gross - refunded, 2)
    failed = [p for p in payments if (p.status or "") == "failed"]
    failed_amount = round(sum(_num(p.amount) for p in failed), 2)

    # Monthly cash-flow trend over the FULL span (net per month), chronological.
    by_month: dict = defaultdict(float)
    for p in payments:
        if not p.occurred_on:
            continue
        key = (p.occurred_on.year, p.occurred_on.month)
        if (p.status or "") == "succeeded":
            by_month[key] += _num(p.amount)
        by_month[key] -= _num((p.meta or {}).get("amount_refunded"))
    _MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    monthly = [{"month": _MON[m - 1], "net": round(v, 2),
                "mtd": (y, m) == (today.year, today.month)}
               for (y, m), v in sorted(by_month.items())]

    # Streams over the same span as `monthly` (net of refunds; Σ == net over span).
    stream_val: dict = defaultdict(float)
    for p in payments:
        st = (p.meta or {}).get("stream") or "other"
        if (p.status or "") == "succeeded":
            stream_val[st] += _num(p.amount)
        stream_val[st] -= _num((p.meta or {}).get("amount_refunded"))
    span_net = round(sum(stream_val.values()), 2)
    order = ["memberships", "event_tickets", "sponsorships", "invoices", "other"]
    streams = [{"key": s, "label": STREAM_LABELS.get(s, s.title()),
                "amount": round(stream_val[s], 2),
                "pct": round(stream_val[s] / span_net * 100) if span_net else 0}
               for s in order if round(stream_val.get(s, 0), 2) != 0]

    # Subscriptions: perpetual MRR (single source) + installment objects.
    active = [x for x in subs if x.status == "active"]
    mrr = mrr_of(active)
    perpetual = [x for x in active if is_perpetual(x)]
    installments = [{
        "name": (x.meta or {}).get("plan_name") or x.name,
        "amount": sub_monthly(x),
        "total": (x.meta or {}).get("installments_total"),
        "collected": (x.meta or {}).get("installments_collected"),
        "final_date": (x.meta or {}).get("end_date") or (x.meta or {}).get("next_payment_date"),
    } for x in active if not is_perpetual(x)]

    past_due = sum(1 for x in subs if x.status == "past_due")

    # Forward billing — next 30 days (both types; installment finals included).
    horizon = today + dt.timedelta(days=30)
    schedule = []
    for x in active:
        d = _pdate((x.meta or {}).get("next_payment_date"))
        amt = _num((x.meta or {}).get("next_payment_amount")) or sub_monthly(x)
        if d and today <= d <= horizon and amt:
            note = "installment" if not is_perpetual(x) else "subscription"
            schedule.append({"date": d.isoformat(), "amount": round(amt, 2),
                             "who": x.name, "note": note})
    schedule.sort(key=lambda r: r["date"])
    next30 = {"amount": round(sum(r["amount"] for r in schedule), 2),
              "charges": len(schedule), "schedule": schedule}

    return {
        "available": True, "basis": "cash",
        "span": {"start": span_start.isoformat(), "end": today.isoformat()},
        "net_cash": net_cash, "gross": gross, "refunded": refunded,
        "failed_amount": failed_amount, "failed_count": len(failed), "past_due": past_due,
        "monthly": monthly,
        "mrr": mrr, "perpetual_count": len(perpetual),
        "installments": installments,
        "next30": next30,
        "arr_book": round(float(arr_book or 0), 2), "run_rate": round(mrr * 12, 2),
        "streams": streams,
    }
