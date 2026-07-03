"""One-off audit of the SEPARATE beCollective Go High Level instance, to learn
its real tags / pipelines / products / subscriptions so we can set the
beCollective config from live data (mirrors probe_ghl.py for the Forum).

Reads GHL_BC_TOKEN + GHL_BC_LOCATION_ID from backend/.probe.env (git-ignored;
Claude never opens it). PII is masked; no raw contact records stored.

Run:  cd backend && ./.venv/Scripts/python.exe probe_becollective.py
"""
from __future__ import annotations

import os
import re
import sys
import json
import time
from collections import Counter

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

BASE = "https://services.leadconnectorhq.com"
VERSION = "2021-07-28"
HERE = os.path.dirname(os.path.abspath(__file__))


def load_conf():
    conf = {}
    path = os.path.join(HERE, ".probe.env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                conf[k.strip()] = v.strip().strip('"').strip("'")
    return (conf.get("GHL_BC_TOKEN") or os.environ.get("GHL_BC_TOKEN"),
            conf.get("GHL_BC_LOCATION_ID") or os.environ.get("GHL_BC_LOCATION_ID"))


TOKEN, LOC = load_conf()
if not TOKEN or not LOC:
    sys.exit("Missing GHL_BC_TOKEN / GHL_BC_LOCATION_ID in backend/.probe.env")

H = {"Authorization": f"Bearer {TOKEN}", "Version": VERSION, "Accept": "application/json"}
c = httpx.Client(timeout=60, headers=H)


def get(path, params=None):
    try:
        r = c.get(f"{BASE}{path}", params=params or {})
        ct = r.headers.get("content-type", "")
        return r.status_code, (r.json() if "application/json" in ct else r.text)
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def find_list(j):
    if isinstance(j, list):
        return j
    if isinstance(j, dict):
        best = []
        for v in j.values():
            if isinstance(v, list) and len(v) >= len(best):
                best = v
        return best
    return []


def main():
    print(f"beCollective GHL audit — location {LOC}", flush=True)
    st, loc = get(f"/locations/{LOC}")
    name = (loc.get("location", loc) if isinstance(loc, dict) else {}).get("name")
    print(f"[{st}] location: {name}\n", flush=True)

    # Contacts → tag usage (the member roster lives in tags).
    out, params, page = [], {"locationId": LOC, "limit": 100}, 0
    while True:
        stt, body = get("/contacts/", params)
        if stt != 200 or not isinstance(body, dict):
            print(f"[{stt}] contacts: {str(body)[:120]}", flush=True)
            break
        rows = body.get("contacts") or []
        out.extend(rows)
        meta = body.get("meta") or {}
        sai = meta.get("startAfterId")
        page += 1
        if not rows or not sai or len(out) >= 20000:
            break
        params["startAfterId"] = sai
        if meta.get("startAfter"):
            params["startAfter"] = meta["startAfter"]
    tagc = Counter()
    for ct_ in out:
        for t in (ct_.get("tags") or []):
            tagc[str(t).strip().lower()] += 1
    print(f"[200] contacts: {len(out)} pulled, {len(tagc)} distinct tags in use")
    print("  TOP 40 TAGS:")
    for k, v in tagc.most_common(40):
        print(f"    {v:>5}  {k}")

    # Pipelines + stages (does beCollective have its own sales pipeline?).
    st, pl = get("/opportunities/pipelines", {"locationId": LOC})
    print(f"\n[{st}] pipelines:")
    for p in find_list(pl):
        print(f"  - {p.get('name')}: {[s.get('name') for s in (p.get('stages') or [])]}")

    # Opportunities status split.
    st, ob = get("/opportunities/search", {"location_id": LOC, "limit": 100})
    opps = find_list(ob)
    if opps:
        print(f"\n[{st}] opportunities (first {len(opps)}): status_split="
              f"{dict(Counter(o.get('status') or '-' for o in opps))}")

    # Products (subscription price/product names → MRR split signal).
    st, pr = get("/products/", {"locationId": LOC})
    prods = find_list(pr)
    print(f"\n[{st}] products ({len(prods)}):")
    for p in prods[:40]:
        print(f"  - {p.get('name')}  [{p.get('productType')}]")

    # Subscriptions (payments API needs altId/altType).
    st, sb = get("/payments/subscriptions", {"altId": LOC, "altType": "location", "limit": 100})
    subs = find_list(sb)
    print(f"\n[{st}] subscriptions ({len(subs)}): status_split="
          f"{dict(Counter(x.get('status') or '-' for x in subs))}")
    for x in subs[:8]:
        print(f"  - amount={x.get('amount')} status={x.get('status')} "
              f"product={x.get('entitySourceName') or x.get('productName')}")

    # Custom fields (renewal/payment fields?).
    st, cf = get(f"/locations/{LOC}/customFields")
    cfs = find_list(cf)
    print(f"\n[{st}] custom fields ({len(cfs)}): "
          f"{[f.get('name') for f in cfs if any(w in (f.get('name') or '').lower() for w in ('renew','payment','plan','tier','join','member'))][:15]}")
    c.close()


if __name__ == "__main__":
    main()
