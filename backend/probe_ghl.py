"""One-off Go High Level API audit (summary-JSON first).

Reads the Private Integration Token from backend/.probe.env (git-ignored; Claude
never opens it) or the GHL_TOKEN env var, probes a broad set of v2 endpoints, and
writes an AGGREGATE audit to backend/ghl_audit_summary.json — schema (custom
fields + options), tag distributions, pipelines/stages, calendars, products,
subscriptions, transactions, etc. PII (emails/phones) is masked; no raw contact
records are stored. A raw first-page dump goes to ghl_audit_full.json.

Run:  cd backend && ./.venv/Scripts/python.exe probe_ghl.py
"""
from __future__ import annotations

import os
import re
import sys
import json
import time
from collections import Counter

import httpx

try:                                    # keep Windows console from crashing on unicode
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

BASE = "https://services.leadconnectorhq.com"
VERSION = "2021-07-28"
HERE = os.path.dirname(os.path.abspath(__file__))
CONTACT_CAP = 25000


def load_conf():
    conf = {}
    path = os.path.join(HERE, ".probe.env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                conf[k.strip()] = v.strip().strip('"').strip("'")
    token = conf.get("GHL_TOKEN") or os.environ.get("GHL_TOKEN")
    loc = (conf.get("GHL_LOCATION_ID") or os.environ.get("GHL_LOCATION_ID")
           or "IT2T9rc5U89rz8YqPT1E")
    return token, loc


TOKEN, LOC = load_conf()
if not TOKEN:
    sys.exit("No token found. Create backend/.probe.env with GHL_TOKEN=... (git-ignored).")

H = {"Authorization": f"Bearer {TOKEN}", "Version": VERSION, "Accept": "application/json"}
client = httpx.Client(timeout=60, headers=H)
SUMMARY: dict = {"location_id": LOC, "generated_at": time.strftime("%Y-%m-%d %H:%M")}
FULL: dict = {}


def mask(v):
    if not isinstance(v, str):
        return v
    if "@" in v:
        u, _, d = v.partition("@")
        return u[:2] + "***@" + d
    digits = re.sub(r"\D", "", v)
    return ("***" + digits[-4:]) if len(digits) >= 7 else v


def get(path, params=None):
    try:
        r = client.get(f"{BASE}{path}", params=params or {})
        ct = r.headers.get("content-type", "")
        return r.status_code, (r.json() if "application/json" in ct else r.text)
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def find_list(j):
    if isinstance(j, list):
        return j, None
    if not isinstance(j, dict):
        return [], None
    best_key, best = None, []
    for k, v in j.items():
        if isinstance(v, list) and len(v) >= len(best):
            best_key, best = k, v
    return best, best_key


def keyset(items, cap=800):
    c = Counter()
    for it in items[:cap]:
        if isinstance(it, dict):
            c.update(it.keys())
    return sorted(c.keys())


def hit(name, path, params=None, list_hint=None):
    """Generic list endpoint → returns (status, items, raw). Records to SUMMARY/FULL."""
    st, body = get(path, params)
    FULL[name] = {"path": path, "params": params, "status": st,
                  "body": body if isinstance(body, (dict, list)) else str(body)[:1500]}
    items, key = ([], None)
    if st == 200:
        items, key = find_list(body)
    rec = {"status": st, "count": len(items), "list_key": key}
    if st == 200 and items:
        rec["fields"] = keyset(items)
    elif st != 200:
        rec["error"] = str(body)[:200]
    SUMMARY[name] = rec
    print(f"[{st}] {name}: {len(items)} rows", flush=True)
    return st, items, body


def main():
    print(f"GHL audit - location {LOC}", flush=True)

    # 1) location ---------------------------------------------------------
    st, loc = get(f"/locations/{LOC}")
    locd = (loc.get("location", loc) if isinstance(loc, dict) else {})
    SUMMARY["location"] = {"status": st, "name": locd.get("name"),
                           "business": locd.get("business"), "timezone": locd.get("timezone")}
    print(f"[{st}] location: {locd.get('name')}", flush=True)

    # 2) custom fields (with picklist options) ----------------------------
    st, cfs, _ = hit("custom_fields", f"/locations/{LOC}/customFields")
    cf_names = {}
    if cfs:
        SUMMARY["custom_fields"]["defs"] = []
        for f in cfs:
            cf_names[f.get("id")] = f.get("name") or f.get("fieldKey")
            opts = [o.get("name") if isinstance(o, dict) else o
                    for o in (f.get("picklistOptions") or [])]
            SUMMARY["custom_fields"]["defs"].append({
                "name": f.get("name"), "dataType": f.get("dataType"),
                "model": f.get("model"), "fieldKey": f.get("fieldKey"),
                "options": opts or None})

    # 3) custom values + tag catalog -------------------------------------
    st, cvs, _ = hit("custom_values", f"/locations/{LOC}/customValues")
    if cvs:
        SUMMARY["custom_values"]["items"] = [{"name": c.get("name"), "key": c.get("fieldKey")}
                                             for c in cvs]
    st, tags, _ = hit("tags_defined", f"/locations/{LOC}/tags")
    if tags:
        SUMMARY["tags_defined"]["names"] = sorted((t.get("name") or "") for t in tags)

    # 4) contacts (full) + analysis --------------------------------------
    out, params, page, total_meta = [], {"locationId": LOC, "limit": 100}, 0, None
    while True:
        stt, body = get("/contacts/", params)
        if stt != 200 or not isinstance(body, dict):
            break
        rows = body.get("contacts") or []
        out.extend(rows)
        meta = body.get("meta") or {}
        if page == 0:
            total_meta = meta.get("total")
            FULL["contacts_meta"] = meta
        page += 1
        sai = meta.get("startAfterId")
        if not rows or not sai or len(out) >= CONTACT_CAP:
            break
        params["startAfterId"] = sai
        if meta.get("startAfter"):
            params["startAfter"] = meta["startAfter"]

    tagc, cfc, srcc, typec = Counter(), Counter(), Counter(), Counter()
    dnd = 0
    for c in out:
        for t in (c.get("tags") or []):
            tagc[str(t).strip().lower()] += 1
        for f in (c.get("customFields") or []):
            if f.get("value") not in (None, "", []):
                cfc[cf_names.get(f.get("id"), f.get("id"))] += 1
        srcc[c.get("source") or "-"] += 1
        typec[c.get("type") or "-"] += 1
        if c.get("dnd"):
            dnd += 1
    SUMMARY["contacts"] = {
        "total_meta": total_meta, "pulled": len(out), "dnd_count": dnd,
        "fields": keyset(out),
        "tag_usage": dict(tagc.most_common()),
        "custom_field_fill": dict(cfc.most_common()),
        "source_dist": dict(srcc.most_common(25)),
        "type_dist": dict(typec.most_common()),
    }
    print(f"[200] contacts: pulled {len(out)} (meta.total={total_meta}), {len(tagc)} tags in use", flush=True)

    # 5) pipelines + opportunities ---------------------------------------
    st, pls, _ = hit("pipelines", "/opportunities/pipelines", {"locationId": LOC})
    if pls:
        SUMMARY["pipelines"]["defs"] = [
            {"name": p.get("name"), "stages": [s.get("name") for s in (p.get("stages") or [])]}
            for p in pls]
    st, opps, obody = hit("opportunities", "/opportunities/search", {"location_id": LOC, "limit": 100})
    if isinstance(obody, dict):
        SUMMARY["opportunities"]["total_meta"] = (obody.get("meta") or {}).get("total")
    if opps:
        SUMMARY["opportunities"]["status_split"] = dict(Counter(o.get("status") or "-" for o in opps))
        vals = [float(o.get("monetaryValue") or 0) for o in opps]
        SUMMARY["opportunities"]["monetary_sum_page"] = round(sum(vals), 2)
        SUMMARY["opportunities"]["stage_split"] = dict(Counter(o.get("pipelineStageId") or "-" for o in opps))

    # 6) calendars + events ----------------------------------------------
    st, cals, _ = hit("calendars", "/calendars/", {"locationId": LOC})
    if isinstance(cals, list) and cals:
        SUMMARY["calendars"]["defs"] = [{"id": c.get("id"), "name": c.get("name"),
                                         "type": c.get("calendarType") or c.get("widgetType")}
                                        for c in cals]
        now = int(time.time() * 1000)
        yr = 365 * 24 * 3600 * 1000
        ev_summary = []
        for c in cals[:8]:
            stev, ebody = get("/calendars/events", {"locationId": LOC, "calendarId": c.get("id"),
                                                    "startTime": now - yr, "endTime": now + yr})
            evs, _ = find_list(ebody) if stev == 200 else ([], None)
            future = 0
            for e in evs:
                sti = e.get("startTime") or e.get("startAt")
                try:
                    fut = str(sti).isdigit() and int(sti) > now
                except Exception:  # noqa: BLE001
                    fut = False
                future += 1 if fut else 0
            ev_summary.append({"calendar": c.get("name"), "id": c.get("id"),
                               "status": stev, "events_2yr": len(evs), "future": future,
                               "fields": keyset(evs) if evs else None})
        SUMMARY["calendar_events"] = ev_summary
    hit("calendar_groups", "/calendars/groups", {"locationId": LOC})

    # 7) commerce ---------------------------------------------------------
    st, prods, _ = hit("products", "/products/", {"locationId": LOC})
    if prods:
        SUMMARY["products"]["items"] = [{"name": p.get("name"), "id": p.get("_id") or p.get("id"),
                                         "type": p.get("productType")} for p in prods[:100]]
    st, subs, _ = hit("subscriptions", "/payments/subscriptions", {"locationId": LOC, "limit": 100})
    if subs:
        SUMMARY["subscriptions"]["status_split"] = dict(Counter(s.get("status") or "-" for s in subs))
        SUMMARY["subscriptions"]["samples"] = [
            {"amount": s.get("amount"), "interval": s.get("interval") or s.get("recurringInterval"),
             "status": s.get("status"), "currency": s.get("currency")} for s in subs[:15]]
    st, trans, _ = hit("transactions", "/payments/transactions", {"locationId": LOC, "limit": 100})
    if trans:
        amts = [float(t.get("amount") or 0) for t in trans]
        SUMMARY["transactions"]["amount_sum_page"] = round(sum(amts), 2)
        SUMMARY["transactions"]["status_split"] = dict(Counter(t.get("status") or "-" for t in trans))
    hit("orders", "/payments/orders", {"locationId": LOC, "altId": LOC, "altType": "location", "limit": 100})

    # 8) team + automation ------------------------------------------------
    st, users, _ = hit("users", "/users/", {"locationId": LOC})
    if users:
        SUMMARY["users"]["people"] = [{"name": u.get("name"), "email": mask(u.get("email")),
                                       "roles": u.get("roles")} for u in users]
    for nm, pth in [("campaigns", "/campaigns/"), ("workflows", "/workflows/"),
                    ("forms", "/forms/"), ("funnels", "/funnels/funnel/list")]:
        hit(nm, pth, {"locationId": LOC})

    with open(os.path.join(HERE, "ghl_audit_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(SUMMARY, fh, default=str, indent=2)
    with open(os.path.join(HERE, "ghl_audit_full.json"), "w", encoding="utf-8") as fh:
        json.dump(FULL, fh, default=str, indent=2)
    print("\nWrote ghl_audit_summary.json (safe: aggregates only) + ghl_audit_full.json (raw).", flush=True)
    client.close()


if __name__ == "__main__":
    main()
