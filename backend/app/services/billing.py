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
from calendar import monthrange
from collections import Counter, defaultdict

_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

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


# Offerings a Forum member may ALSO buy through the same legacy Stripe account, but
# which are NOT Forum revenue. Tunable via config['non_forum_keywords'].
_NON_FORUM_DEFAULT = [
    "spring break",                          # a separate company/event, not the Forum (Connor)
    "the edge", "va in 30", "virtual assistant", "becollective", "be collective",
    "collective", "bootcamp", "playbook", "vault", "buyer mastery", "agent attraction",
    "operator", "blueprint", "abundance", "shadow", "just in time", "justintime", "justin time",
]


def forum_offering(description, config=None, is_subscription=False,
                   amount=0.0, recurring=False) -> tuple[bool, str | None]:
    """Is a legacy Stripe charge/subscription a FORUM or INNER CIRCLE offering — vs a
    Forum member's OTHER purchases through the same account?

    Connor's rule: 'Forum' shows up in the description; a membership/dues charge WITHOUT
    it is Inner Circle; small event tickets ($7) and non-membership products (courses,
    The Edge, beCollective) are not the Forum. Returns (include, segment).

    Descriptions on legacy charges are often thin ("Subscription update"), so a charge
    from a roster member that is RECURRING (subscription-linked) or membership-SIZED
    (≥ membership_min_amount, default $500) is treated as a membership — only a small
    one-off with no membership signal is dropped. A denylisted product is never Forum,
    whatever the amount. Tunable: config['non_forum_keywords'], ['forum_keywords'],
    ['membership_min_amount']."""
    config = config or {}
    d = (description or "").lower()
    if any(k.lower() in d for k in (config.get("non_forum_keywords") or _NON_FORUM_DEFAULT)):
        return (False, None)
    forum = ("forum" in d) or any(k.lower() in d for k in (config.get("forum_keywords") or []))
    ic = ("inner circle" in d) or ("innercircle" in d)
    if any(w in d for w in ("ticket", "rsvp", "vip", " guest")) and not forum:
        return (False, None)                       # the $7 (non-Forum) event tickets
    if forum:
        return (True, "forum")
    if ic:
        return (True, "inner_circle")
    if any(w in d for w in ("membership", "subscription for", "dues", "financed", "pif",
                            "payment plan", "2 pay", "3 pay", "4 pay", "installment")):
        return (True, "inner_circle")              # a membership charge with no 'Forum'
    min_amt = config.get("membership_min_amount")
    min_amt = 500.0 if min_amt is None else float(min_amt)
    if is_subscription or recurring or float(amount or 0) >= min_amt:
        return (True, "inner_circle")              # recurring / membership-sized → a membership
    return (False, None)                           # small ambiguous one-off → not Forum revenue


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


def _interval_months(interval: str | None) -> int:
    return 12 if ("year" in (interval or "").lower() or "annual" in (interval or "").lower()) else 1


def _add_months(d: dt.date, n: int) -> dt.date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return dt.date(y, m, min(d.day, monthrange(y, m)[1]))


def next_charge_date(start_date, interval, after: dt.date) -> dt.date | None:
    """Next recurring charge strictly after `after`, from `start_date` at the sub's
    cadence (monthly/annual anniversary). None if the start date is unknown."""
    start = _pdate(start_date)
    if not start:
        return None
    step = _interval_months(interval)
    d, guard = start, 0
    while d <= after and guard < 600:
        d = _add_months(d, step)
        guard += 1
    return d


def project_charges(subs, today: dt.date, until: dt.date) -> list[dict]:
    """Projected future charges for ACTIVE subs, today→`until`, at each sub's cadence
    — capped by end_date and remaining installments. Prefers a stored next_payment_date,
    else derives the schedule from start_date + interval. Pure + testable."""
    out: list[dict] = []
    for x in subs:
        if x.status != "active":
            continue
        meta = x.meta or {}
        step = _interval_months(meta.get("interval"))
        per_charge = round(sub_monthly(x) * (12 if step == 12 else 1), 2)
        if per_charge <= 0:
            continue
        end = _pdate(meta.get("end_date"))
        remaining = None
        if not is_perpetual(x) and meta.get("installments_total"):
            remaining = max(0, int(meta["installments_total"]) - int(meta.get("installments_collected") or 0))
        d = _pdate(meta.get("next_payment_date"))
        if not d or d <= today:
            d = next_charge_date(meta.get("start_date"), meta.get("interval"), today)
        kind = "installment" if not is_perpetual(x) else "subscription"
        count = 0
        while d and d <= until:
            if end and d > end:
                break
            if remaining is not None and count >= remaining:
                break
            out.append({"date": d.isoformat(), "amount": per_charge, "who": x.name, "note": kind,
                        "source_url": getattr(x, "source_url", None)})
            d = _add_months(d, step)
            count += 1
    out.sort(key=lambda r: r["date"])
    return out


def mrr_of(subs) -> float:
    """True MRR = active perpetual subscriptions only (installments excluded).
    THE single MRR source — top-row tile and billing block both call this."""
    return round(sum(sub_monthly(x) for x in subs
                     if x.status == "active" and is_perpetual(x)), 2)


def normalize_payment_plan(v) -> str | None:
    """A GHL 'payment plan' custom-field value → pif | monthly | financed | None.
    PIF renews as an annual lump; monthly/financed bill via a subscription."""
    d = str(v or "").strip().lower()
    if not d:
        return None
    if "pif" in d or "paid in full" in d or d == "full" or "one time" in d or "one-time" in d:
        return "pif"
    if "financ" in d:
        return "financed"
    if "month" in d:
        return "monthly"
    return None


def project_renewals(members, today: dt.date, until: dt.date) -> list[dict]:
    """Projected annual renewal lump sums for PIF members — the paid-in-full members
    who have NO monthly subscription, so `project_charges` can't see them. Driven by
    the GHL membership fields (renewal date + total cost, populated from ClickUp).
    `members` are MetricRecord-like objects whose `meta['membership']` carries
    {payment, renewal_date, total_cost}. Recurs on the yearly anniversary; a past
    renewal date rolls forward to the next one. The caller passes only members not
    already covered by an active subscription (no double-count). Pure + testable."""
    out: list[dict] = []
    for m in members:
        mem = (getattr(m, "meta", None) or {}).get("membership") or {}
        if mem.get("payment") != "pif":
            continue
        amt = _num(mem.get("total_cost"))
        d = _pdate(mem.get("renewal_date"))
        if amt <= 0 or not d:
            continue
        guard = 0
        while d <= today and guard < 25:          # roll a past renewal to the next anniversary
            d = _add_months(d, 12)
            guard += 1
        while d and d <= until:
            out.append({"date": d.isoformat(), "amount": round(amt, 2),
                        "who": getattr(m, "name", None) or "Member", "note": "renewal",
                        "source_url": getattr(m, "source_url", None)})
            d = _add_months(d, 12)
    out.sort(key=lambda r: r["date"])
    return out


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _pay_key(p) -> tuple:
    """Identity of a charge for cross-source dedupe: (email, whole-dollar amount,
    date). Whole dollars because the GHL CSV import rounded, and a same-day same-
    amount same-payer collision is the money we mean to fold together."""
    email = (getattr(p, "email", None) or "").strip().lower()
    amt = round(_num(getattr(p, "amount", 0)))
    d = p.occurred_on.isoformat() if getattr(p, "occurred_on", None) else ""
    return (email, amt, d)


def _is_imported(p) -> bool:
    """A CSV-backfilled GHL transaction (entitySourceSubType='imported_csv'). Its real
    payment date is read from fulfilledAt at sync time, so it can be matched on date."""
    return bool((getattr(p, "meta", None) or {}).get("imported"))


def merge_payment_sources(ghl_payments, legacy_payments) -> tuple[list, int]:
    """Merge the GHL payment feed with the legacy-Stripe feed, keeping each real charge
    once — WITHOUT assuming legacy Stripe is the complete history.

    Legacy Stripe is authoritative for the ORIGINAL account's charges. The CSV backfill
    copied them into GHL (marked 'imported'); now that each imported row's REAL date is
    read from fulfilledAt, an imported row that matches a legacy charge on (payer,
    whole-$ amount, DATE) is that same charge — drop it, keep legacy's (one-to-one,
    Counter-budgeted). An imported row with NO legacy twin is KEPT (a charge legacy
    isn't pulling), and every NON-imported row — native new-account charges (charge id)
    and genuine manual GHL entries — is always kept. Everything stays date-based."""
    budget = Counter(_pay_key(p) for p in legacy_payments)
    merged = list(legacy_payments)
    suppressed = 0
    for g in ghl_payments:
        if _is_imported(g):
            k = _pay_key(g)
            if budget.get(k, 0) > 0:
                budget[k] -= 1
                suppressed += 1
                continue
        merged.append(g)
    return merged, suppressed


def compute_billing(payments, subs, arr_book: float,
                    period_start: dt.date, period_end: dt.date, today: dt.date,
                    extra_projected: list[dict] | None = None) -> dict:
    """Assemble the Forum billing block from `payment` + enriched `subscription`
    records (Section 4). `extra_projected` are non-subscription future charges (PIF
    renewal lump sums from the GHL membership fields) folded into the forward
    projection only — never MRR. `available` is False when there are no payment rows."""
    if not payments:
        return {"available": False}
    extra_future = [c for c in (extra_projected or [])
                    if dt.date.fromisoformat(c["date"]) > today]

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
    txn_count = len(payments)

    # Monthly cash-flow trend — the FULL calendar year: actual net per month through
    # today, then projected inflow (from active subscriptions) for the months ahead.
    by_month: dict = defaultdict(float)
    for p in payments:
        if not p.occurred_on:
            continue
        key = (p.occurred_on.year, p.occurred_on.month)
        if (p.status or "") == "succeeded":
            by_month[key] += _num(p.amount)
        by_month[key] -= _num((p.meta or {}).get("amount_refunded"))

    active = [x for x in subs if x.status == "active"]
    proj_by_month: dict = defaultdict(float)
    for c in project_charges(active, today, dt.date(today.year, 12, 31)) + extra_future:
        d = dt.date.fromisoformat(c["date"])
        if d <= dt.date(today.year, 12, 31):
            proj_by_month[(d.year, d.month)] += c["amount"]

    # Each month splits into `actual` (cash already collected) + `projected` (charges
    # still scheduled). Past = all actual; future = all projected; the CURRENT month
    # carries BOTH — collected-so-far plus what's still set to bill this month — so its
    # bar reflects the full expected month, not just a mid-month partial.
    monthly = []
    for m in range(1, 13):
        key = (today.year, m)
        if m < today.month:
            actual, projected = round(by_month.get(key, 0), 2), 0.0
        elif m == today.month:
            actual, projected = round(by_month.get(key, 0), 2), round(proj_by_month.get(key, 0), 2)
        else:
            actual, projected = 0.0, round(proj_by_month.get(key, 0), 2)
        monthly.append({
            "month": _MON[m - 1], "ym": f"{today.year}-{m:02d}",
            "actual": actual, "projected": projected, "net": round(actual + projected, 2),
            "mtd": m == today.month, "is_projected": m > today.month,
        })

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

    # Forward billing — projected charges over the next 30 and 90 days, plus the
    # remaining-year total, all derived from each active sub's billing cadence.
    d30, d90 = today + dt.timedelta(days=30), today + dt.timedelta(days=90)
    sched30 = project_charges(active, today, d30) + [c for c in extra_future if dt.date.fromisoformat(c["date"]) <= d30]
    sched90 = project_charges(active, today, d90) + [c for c in extra_future if dt.date.fromisoformat(c["date"]) <= d90]
    sched30.sort(key=lambda r: r["date"])
    sched90.sort(key=lambda r: r["date"])
    next30 = {"amount": round(sum(r["amount"] for r in sched30), 2),
              "charges": len(sched30), "schedule": sched30}
    forecast = {
        "next_30": next30["amount"],
        "next_90": round(sum(r["amount"] for r in sched90), 2),
        "rest_of_year": round(sum(m["projected"] for m in monthly), 2),   # incl. rest of this month
    }

    return {
        "available": True, "basis": "cash",
        "span": {"start": span_start.isoformat(), "end": today.isoformat()},
        "net_cash": net_cash, "gross": gross, "refunded": refunded, "txn_count": txn_count,
        "failed_amount": failed_amount, "failed_count": len(failed), "past_due": past_due,
        "monthly": monthly,
        "mrr": mrr, "perpetual_count": len(perpetual),
        "installments": installments,
        "next30": next30,
        "forecast": forecast,
        "arr_book": round(float(arr_book or 0), 2), "run_rate": round(mrr * 12, 2),
        "streams": streams,
    }
