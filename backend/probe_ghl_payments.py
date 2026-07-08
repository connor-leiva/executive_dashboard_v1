"""Focused audit of The Forum's GHL Payments API (Stripe-fed) — the source for
revenue / cash flow / MRR / ARR / processed payments / failures.

Pulls ALL transactions + subscriptions (altId + altType=location), fetches a few
subscription + transaction DETAILS to reveal the rich fields the GHL UI shows
(product frequency, upcoming payment, total collected, linked charges), and prints
aggregates keyed to the KPIs. PII masked; writes ghl_payments_audit.json.
Read-only. Creds from backend/.probe.env (GHL_TOKEN / GHL_LOCATION_ID).

Run:  cd backend && ./.venv/Scripts/python.exe probe_ghl_payments.py
"""
from __future__ import annotations
import os, re, sys, json, time
from collections import Counter, defaultdict

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "https://services.leadconnectorhq.com"
VERSION = "2021-07-28"
HERE = os.path.dirname(os.path.abspath(__file__))


def load_conf():
    conf = {}
    p = os.path.join(HERE, ".probe.env")
    if os.path.exists(p):
        for ln in open(p, encoding="utf-8"):
            s = ln.strip()
            if "=" in s and not s.startswith("#"):
                k, _, v = s.partition("="); conf[k.strip()] = v.strip().strip('"').strip("'")
    return (conf.get("GHL_TOKEN") or os.environ.get("GHL_TOKEN"),
            conf.get("GHL_LOCATION_ID") or os.environ.get("GHL_LOCATION_ID") or "IT2T9rc5U89rz8YqPT1E")


TOKEN, LOC = load_conf()
if not TOKEN:
    sys.exit("No token in backend/.probe.env (GHL_TOKEN=...).")
H = {"Authorization": f"Bearer {TOKEN}", "Version": VERSION, "Accept": "application/json"}
client = httpx.Client(timeout=60, headers=H)
ALT = {"altId": LOC, "altType": "location"}


def mask(v):
    if not isinstance(v, str):
        return v
    if "@" in v:
        u, _, d = v.partition("@"); return u[:2] + "***@" + d
    dg = re.sub(r"\D", "", v)
    return ("***" + dg[-4:]) if len(dg) >= 7 else v


def money(n):
    try:
        return float(n)
    except (TypeError, ValueError):
        return 0.0


def get(path, params=None):
    try:
        r = client.get(f"{BASE}{path}", params=params or {})
        ct = r.headers.get("content-type", "")
        return r.status_code, (r.json() if "application/json" in ct else r.text)
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def find_list(j):
    if isinstance(j, list):
        return j
    if isinstance(j, dict):
        for k in ("data", "transactions", "subscriptions", "orders", "invoices", "schedules"):
            if isinstance(j.get(k), list):
                return j[k]
        best = []
        for v in j.values():
            if isinstance(v, list) and len(v) >= len(best):
                best = v
        return best
    return []


def page_all(path, cap=2000):
    """Paginate a payments list endpoint (limit/offset) with altId/altType."""
    out, offset = [], 0
    while len(out) < cap:
        st, body = get(path, {**ALT, "limit": 100, "offset": offset})
        if st != 200:
            return st, out, body
        rows = find_list(body)
        out.extend(rows)
        if len(rows) < 100:
            break
        offset += 100
    return 200, out, None


def dstr(v):
    """Best-effort date from ms-epoch or ISO string → YYYY-MM-DD."""
    if v in (None, "", 0):
        return None
    s = str(v)
    if s.isdigit():
        try:
            return time.strftime("%Y-%m-%d", time.gmtime(int(s) / (1000 if len(s) > 10 else 1)))
        except Exception:  # noqa: BLE001
            return s
    return s[:10]


def main():
    print(f"GHL Payments audit — location {LOC}\n", flush=True)
    report = {"location": LOC, "generated_at": time.strftime("%Y-%m-%d %H:%M")}

    # ── TRANSACTIONS (cash / processed payments / failures) ──────────────
    st, txns, err = page_all("/payments/transactions")
    print(f"[{st}] transactions: {len(txns)}", flush=True)
    if st == 200 and txns:
        keys = Counter()
        for t in txns:
            keys.update(t.keys())
        status = Counter(str(t.get("status") or "-").lower() for t in txns)
        # entitySourceType tells subscription vs membership vs manual
        src = Counter(str(t.get("entitySourceType") or t.get("sourceType")
                          or (t.get("entityType")) or "-") for t in txns)
        prov = Counter(str(t.get("paymentProviderType") or t.get("provider") or "-") for t in txns)
        succ = [t for t in txns if str(t.get("status") or "").lower() in ("succeeded", "success", "paid")]
        failed = [t for t in txns if str(t.get("status") or "").lower() in ("failed", "declined", "canceled", "cancelled")]
        plan_names = Counter(str(t.get("entitySourceName") or "-") for t in succ)
        refunds = round(sum(money(t.get("amountRefunded")) for t in txns), 2)
        # PIF question: one-time charges (no subscriptionId) vs recurring installments.
        onetime = [t for t in succ if not t.get("subscriptionId")]
        recurring = [t for t in succ if t.get("subscriptionId")]
        top_charges = sorted(
            [{"amount": money(t.get("amount")), "plan": t.get("entitySourceName"),
              "recurring": bool(t.get("subscriptionId")),
              "date": dstr(t.get("createdAt"))} for t in succ],
            key=lambda x: -x["amount"])[:15]
        by_month = defaultdict(float)
        for t in succ:
            d = dstr(t.get("createdAt") or t.get("created") or t.get("date"))
            if d:
                by_month[d[:7]] += money(t.get("amount"))
        report["transactions"] = {
            "count": len(txns), "fields": sorted(keys),
            "status_split": dict(status), "source_type_split": dict(src.most_common()),
            "provider_split": dict(prov),
            "amount_sum_succeeded": round(sum(money(t.get("amount")) for t in succ), 2),
            "amount_sum_failed": round(sum(money(t.get("amount")) for t in failed), 2),
            "refunds_total": refunds,
            "net_cash": round(sum(money(t.get("amount")) for t in succ) - refunds, 2),
            "onetime_count": len(onetime),
            "onetime_sum": round(sum(money(t.get("amount")) for t in onetime), 2),
            "recurring_count": len(recurring),
            "recurring_sum": round(sum(money(t.get("amount")) for t in recurring), 2),
            "top_charges": top_charges,
            "plan_name_split": dict(plan_names.most_common()),
            "cash_by_month": {k: round(v, 2) for k, v in sorted(by_month.items())},
            "currency": Counter(str(t.get("currency") or "-") for t in txns).most_common(3),
            "sample": [{k: (mask(v) if k in ("email", "contactName") else v)
                        for k, v in t.items() if k in
                        ("amount", "status", "entitySourceType", "entityType",
                         "paymentProviderType", "createdAt", "subscriptionId", "chargeId", "currency")}
                       for t in txns[:5]],
        }
        print(f"    succeeded {len(succ)} · failed {len(failed)} · "
              f"cash ${report['transactions']['amount_sum_succeeded']:,.0f}", flush=True)
    elif st != 200:
        report["transactions"] = {"status": st, "error": str(err)[:200]}

    # ── SUBSCRIPTIONS (MRR) ──────────────────────────────────────────────
    st, subs, err = page_all("/payments/subscriptions")
    print(f"[{st}] subscriptions: {len(subs)}", flush=True)
    if st == 200 and subs:
        keys = Counter()
        for sdoc in subs:
            keys.update(sdoc.keys())
        status = Counter(str(sdoc.get("status") or "-").lower() for sdoc in subs)
        active = [sdoc for sdoc in subs if str(sdoc.get("status") or "").lower() == "active"]

        def interval(sdoc):
            rp = sdoc.get("recurringProduct") or {}
            if isinstance(rp, dict):
                return (rp.get("interval") or rp.get("recurringInterval")
                        or (rp.get("price") or {}).get("interval") if isinstance(rp.get("price"), dict) else None)
            return None

        # MRR estimate (assume month unless a year interval shows up)
        def monthly(sdoc):
            amt = money(sdoc.get("amount"))
            iv = str(interval(sdoc) or "month").lower()
            return amt / 12 if ("year" in iv or "annual" in iv) else amt

        report["subscriptions"] = {
            "count": len(subs), "active": len(active), "fields": sorted(keys),
            "status_split": dict(status),
            "mrr_estimate": round(sum(monthly(sdoc) for sdoc in active), 2),
            "active_detail": [{
                "amount": sdoc.get("amount"), "interval": interval(sdoc),
                "plan": sdoc.get("entitySourceName"),
                "start": (sdoc.get("subscriptionStartDate") or sdoc.get("createdAt") or "")[:10],
                "end": (sdoc.get("subscriptionEndDate") or "") if sdoc.get("subscriptionEndDate") else None,
                "recurringProduct": sdoc.get("recurringProduct"),
            } for sdoc in active],
        }
        print(f"    active {len(active)} · MRR est ${report['subscriptions']['mrr_estimate']:,.0f}", flush=True)

    # ── DETAILS (the rich per-record shape the UI shows) ─────────────────
    def detail(kind, _id):
        for path in (f"/payments/{kind}/{_id}", f"/payments/{kind}/{_id}/"):
            st, body = get(path, ALT)
            if st == 200:
                return body
        return {"_status": st}

    sub_id = next((s.get("_id") or s.get("id") or s.get("subscriptionId") for s in subs), None) if subs else None
    if sub_id:
        sd = detail("subscriptions", sub_id)
        report["subscription_detail_fields"] = sorted(sd.keys()) if isinstance(sd, dict) else None
        # surface the KPI-critical bits without PII
        if isinstance(sd, dict):
            report["subscription_detail_shape"] = {
                k: sd.get(k) for k in ("status", "amount", "interval", "recurringInterval",
                                       "nextChargeDate", "totalPaymentsCollected", "startDate",
                                       "createdAt", "product", "products", "priceSnapshot", "schedule")
                if k in sd}
        print(f"    subscription detail keys: {report.get('subscription_detail_fields')}", flush=True)

    txn_id = next((t.get("_id") or t.get("id") for t in txns), None) if txns else None
    if txn_id:
        td = detail("transactions", txn_id)
        report["transaction_detail_fields"] = sorted(td.keys()) if isinstance(td, dict) else None
        print(f"    transaction detail keys: {report.get('transaction_detail_fields')}", flush=True)

    # ── ORDERS (one per purchase; links product ↔ contact ↔ txns) ────────
    st, orders, err = page_all("/payments/orders")
    print(f"[{st}] orders: {len(orders)}", flush=True)
    if st == 200 and orders:
        keys = Counter()
        for o in orders:
            keys.update(o.keys())
        report["orders"] = {
            "count": len(orders), "fields": sorted(keys),
            "status_split": dict(Counter(str(o.get("status") or "-") for o in orders)),
            "amount_sum": round(sum(money(o.get("amount")) for o in orders), 2),
            "detail": sorted([{
                "amount": money(o.get("amount")), "source": o.get("sourceName"),
                "status": o.get("status"), "onetime_n": o.get("onetimeProducts"),
                "recurring_n": o.get("recurringProducts"), "total_n": o.get("totalProducts"),
            } for o in orders], key=lambda x: -x["amount"]),
        }

    # ── INVOICES (where PIF hides — a member paying full via a single invoice) ──
    st, invs, err = page_all("/invoices/")
    print(f"[{st}] invoices: {len(invs)}", flush=True)
    if st == 200 and invs:
        keys = Counter()
        for iv in invs:
            keys.update(iv.keys())

        def inum(iv, *ks):
            for k in ks:
                if iv.get(k) not in (None, ""):
                    return money(iv.get(k))
            return 0.0

        def items(iv):
            return [it.get("name") for it in (iv.get("invoiceItems") or iv.get("items") or [])
                    if isinstance(it, dict)]

        status = Counter(str(iv.get("status") or "-").lower() for iv in invs)
        paid = [iv for iv in invs if str(iv.get("status") or "").lower() in ("paid", "partially_paid")]
        report["invoices"] = {
            "count": len(invs), "fields": sorted(keys), "status_split": dict(status),
            "total_sum": round(sum(inum(iv, "total", "amount") for iv in invs), 2),
            "paid_sum": round(sum(inum(iv, "amountPaid", "amountPaidToDate") for iv in invs), 2),
            "paid_count": len(paid),
            "detail": sorted([{
                "total": inum(iv, "total", "amount"), "paid": inum(iv, "amountPaid", "amountPaidToDate"),
                "status": iv.get("status"), "title": iv.get("name") or iv.get("title"),
                "items": items(iv), "date": dstr(iv.get("issueDate") or iv.get("createdAt")),
            } for iv in invs], key=lambda x: -x["total"])[:30],
        }
        print(f"    invoices: {len(paid)} paid · total billed ${report['invoices']['total_sum']:,.0f} "
              f"· collected ${report['invoices']['paid_sum']:,.0f}", flush=True)
    elif st != 200:
        report["invoices"] = {"status": st, "error": str(err)[:200]}

    with open(os.path.join(HERE, "ghl_payments_audit.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, default=str, indent=2)
    print("\nWrote ghl_payments_audit.json (masked aggregates).", flush=True)
    client.close()


if __name__ == "__main__":
    main()
