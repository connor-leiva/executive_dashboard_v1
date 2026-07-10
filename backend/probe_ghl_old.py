"""Read-only probe of the OLD Spring B GHL location (the one the legacy Stripe is
wired to) — to confirm we can stitch rich labels onto legacy Stripe charges.

Goal: prove the join  legacy Stripe charge (pi_)  ->  old-GHL transaction (chargeId)
->  its invoice (invoiceItems / name).  Pulls invoices + payment transactions, masks
PII, and reports the join coverage + a few Sponsorship examples. Writes ghl_old_audit.json.

Creds come from backend/.probe.env — add:
    GHL_OLD_TOKEN=<read-only private integration token for the OLD Spring B location>
    GHL_OLD_LOCATION_ID=<that location's id>
Run:  cd backend && ./.venv/Scripts/python.exe probe_ghl_old.py
"""
from __future__ import annotations
import os, sys, json
from collections import Counter

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
                k, _, v = s.partition("=")
                conf[k.strip()] = v.strip().strip('"').strip("'")
    return (conf.get("GHL_OLD_TOKEN") or os.environ.get("GHL_OLD_TOKEN"),
            conf.get("GHL_OLD_LOCATION_ID") or os.environ.get("GHL_OLD_LOCATION_ID"))


TOKEN, LOC = load_conf()
if not TOKEN or not LOC:
    sys.exit("Add GHL_OLD_TOKEN and GHL_OLD_LOCATION_ID to backend/.probe.env")
H = {"Authorization": f"Bearer {TOKEN}", "Version": VERSION, "Accept": "application/json"}
client = httpx.Client(timeout=60, headers=H)
ALT = {"altId": LOC, "altType": "location"}


def mask(v):
    if not isinstance(v, str) or "@" not in v:
        return "•" if isinstance(v, str) and v else v
    a, _, b = v.partition("@")
    return (a[:2] + "…@" + b) if a else v


def page(path, key, extra=None):
    out, offset = [], 0
    while True:
        p = {**ALT, "limit": 100, "offset": offset, **(extra or {})}
        r = client.get(f"{BASE}{path}", params=p)
        if r.status_code != 200:
            print(f"[{r.status_code}] {path} :: {r.text[:200]}", flush=True)
            break
        data = r.json() if r.content else {}
        rows = data.get(key) or data.get("data") or []
        out.extend(rows)
        if len(rows) < 100:
            break
        offset += 100
        if offset > 5000:
            break
    return out


def item_names(inv):
    return [it.get("name") or it.get("description") for it in (inv.get("invoiceItems") or []) if isinstance(it, dict)]


report = {"location": LOC}
print(f"OLD Spring B GHL probe — location {LOC}\n", flush=True)

# ── transactions (do they carry the Stripe charge id + a source label?) ──
txns = page("/payments/transactions", "transactions")
print(f"transactions: {len(txns)}", flush=True)
if txns:
    with_charge = [t for t in txns if str(t.get("chargeId") or "").startswith(("pi_", "ch_"))]
    src_types = Counter(str(t.get("entitySourceType") or "-") for t in txns)
    report["transactions"] = {
        "count": len(txns), "fields": sorted({k for t in txns for k in t.keys()}),
        "with_stripe_charge_id": len(with_charge),
        "source_type_split": dict(src_types.most_common()),
        "sample": [{"amount": t.get("amount"), "chargeId": t.get("chargeId"),
                    "entitySourceType": t.get("entitySourceType"),
                    "entitySourceName": t.get("entitySourceName"),
                    "entitySourceId": t.get("entitySourceId"),
                    "invoiceId": t.get("invoiceId"),
                    "email": mask(t.get("contactEmail"))} for t in txns[:6]],
    }
    print(f"   with Stripe charge id (pi_/ch_): {len(with_charge)}/{len(txns)}", flush=True)

# ── invoices (the rich line-item labels + link back to the transaction) ──
invs = page("/invoices/", "invoices")
print(f"invoices: {len(invs)}", flush=True)
if invs:
    report["invoices"] = {
        "count": len(invs), "fields": sorted({k for i in invs for k in i.keys()}),
        "sample": [{"invoiceNumber": str(i.get("invoiceNumberPrefix") or "") + str(i.get("invoiceNumber") or ""),
                    "name": i.get("name") or i.get("title"),
                    "items": item_names(i), "total": i.get("total"), "status": i.get("status"),
                    "email": mask((i.get("contactDetails") or {}).get("email")),
                    "externalTransactions": i.get("externalTransactions")} for i in invs[:6]],
    }
    # The Shaun-style case: sponsorship invoices — do they carry items + a charge link?
    spon = [i for i in invs if "sponsor" in (json.dumps(item_names(i)) + str(i.get("name") or "")).lower()]
    report["sponsorship_examples"] = [
        {"invoiceNumber": str(i.get("invoiceNumberPrefix") or "") + str(i.get("invoiceNumber") or ""),
         "name": i.get("name") or i.get("title"), "items": item_names(i),
         "total": i.get("total"), "email": mask((i.get("contactDetails") or {}).get("email")),
         "externalTransactions": i.get("externalTransactions")} for i in spon[:5]]
    print(f"   sponsorship-style invoices: {len(spon)}", flush=True)

with open(os.path.join(HERE, "ghl_old_audit.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, indent=1, ensure_ascii=False)
print("\nWrote ghl_old_audit.json (masked).", flush=True)
