"""One-off validation of the ARIVE AEP (REST API) credentials + auth flow, per
Working_with_the_ARIVE_API.docx. Answers two questions in one deterministic run:
  1. Do the credentials authenticate?  (POST /auth/access-token → AccessToken)
  2. What does the pipeline look like + how are responses wrapped?  (GET /loans)

Auth model (from the doc): X-API-KEY goes in the HEADER; clientId/secret/apiKey
go in the JSON BODY; the response token field is `AccessToken` (capital A), then
every call sends `X-API-KEY` + `Authorization: Bearer {AccessToken}`.

Reads ARIVE_CLIENT_ID + ARIVE_SECRET + ARIVE_API_KEY from backend/.probe.env
(git-ignored; Claude never opens it). PII is masked; no raw records are stored.

Run:  cd backend && ./.venv/Scripts/python.exe probe_arive.py
"""
from __future__ import annotations

import os
import sys
import json

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

BASE = "https://gwapiconnect.myarive.com/api"
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
    g = lambda k: conf.get(k) or os.environ.get(k)
    return g("ARIVE_CLIENT_ID"), g("ARIVE_SECRET"), g("ARIVE_API_KEY")


CLIENT_ID, SECRET, API_KEY = load_conf()
missing = [n for n, v in [("ARIVE_CLIENT_ID", CLIENT_ID), ("ARIVE_SECRET", SECRET),
                          ("ARIVE_API_KEY", API_KEY)] if not v]
if missing:
    sys.exit("Missing in backend/.probe.env: " + ", ".join(missing) +
             "\n(The AEP auth needs all three: Client ID, Secret Key, API Key.)")


def mask(v):
    if not v:
        return "—"
    v = str(v)
    return v if len(v) <= 6 else f"{v[:3]}…{v[-2:]}"


def find_list(j):
    """The list of loans hides under one of several wrapper keys (loans/rows/
    responses/data) or is a bare array — grab the longest list found."""
    if isinstance(j, list):
        return j, "(bare array)"
    if isinstance(j, dict):
        best, key = [], None
        for k, v in j.items():
            if isinstance(v, list) and len(v) >= len(best):
                best, key = v, k
        return best, key
    return [], None


def pick(obj, *keys):
    for k in keys:
        v = obj.get(k) if isinstance(obj, dict) else None
        if v not in (None, "", []):
            return v
    return None


def main():
    print(f"ARIVE AEP probe — base {BASE}", flush=True)
    print(f"  creds: clientId={mask(CLIENT_ID)}  secret={mask(SECRET)}  apiKey={mask(API_KEY)}\n", flush=True)

    # ── Step 1: token exchange ────────────────────────────────────────
    with httpx.Client(timeout=45) as c:
        try:
            r = c.post(f"{BASE}/auth/access-token",
                       headers={"X-API-KEY": API_KEY, "Content-Type": "application/json",
                                "Accept": "application/json"},
                       json={"clientId": CLIENT_ID, "secret": SECRET, "apiKey": API_KEY})
        except Exception as e:  # noqa: BLE001
            sys.exit(f"[network] auth request failed: {type(e).__name__}: {e}")

        print(f"[{r.status_code}] POST /auth/access-token", flush=True)
        if r.status_code not in (200, 201):   # Arive returns 201 Created on token issue
            print("  ✗ AUTH FAILED — response body (first 400 chars):", flush=True)
            print("   ", r.text[:400], flush=True)
            print("\n  Likely causes: wrong/rotated key, key not in header vs body, or "
                  "credential scope. Double-check all three values.", flush=True)
            return

        try:
            data = r.json()
        except Exception:  # noqa: BLE001
            print("  ✗ 200 but non-JSON body:", r.text[:300], flush=True)
            return
        token = data.get("AccessToken") or data.get("accessToken")
        if not token:
            print("  ✗ 200 but no AccessToken field. Keys seen:", list(data.keys()), flush=True)
            return
        print(f"  ✓ AUTHENTICATED — AccessToken {mask(token)}, "
              f"ExpiresIn={data.get('ExpiresIn')}, TokenType={data.get('TokenType')}\n", flush=True)

        H = {"X-API-KEY": API_KEY, "Authorization": f"Bearer {token}",
             "Accept": "application/json", "Content-Type": "application/json"}

        # ── Step 2: list a few loans (scope + wrapper shape) ──────────
        try:
            lr = c.get(f"{BASE}/loans", headers=H,
                       params={"limit": 5, "orderBy": "updatedAt", "sort": "DESC"})
        except Exception as e:  # noqa: BLE001
            print(f"[network] GET /loans failed: {type(e).__name__}: {e}", flush=True)
            return
        print(f"[{lr.status_code}] GET /loans?limit=5&orderBy=updatedAt&sort=DESC", flush=True)
        if lr.status_code not in (200, 201):
            print("  body:", lr.text[:300], flush=True)
            return
        loans, wrapper = find_list(lr.json())
        print(f"  wrapper key: {wrapper!r} · {len(loans)} rows on this page", flush=True)
        for i, ln in enumerate(loans[:5]):
            lid = pick(ln, "ariveDisplayLoanId", "ariveLoanId", "arive_display_loan_id", "id", "loanId")
            status = pick(ln, "currentLoanStatusStatus", "current_loan_status", "loanStatus", "status")
            updated = pick(ln, "modifiedDateTime", "loanUpdatedAt", "updatedAt")
            branch = pick(ln, "orgUnitDisplayName", "branchDisplayName", "orgUnitId")
            print(f"    {i+1}. loan={mask(lid)}  status={status}  updated={updated}  branch={branch}", flush=True)
        if loans:
            print("\n  Field keys on row 1 (for the normalization layer):", flush=True)
            print("   ", sorted(loans[0].keys())[:40] if isinstance(loans[0], dict) else loans[0], flush=True)
        else:
            print("  (no loans returned — either an empty/narrow-scope credential, or a "
                  "different wrapper. If you expected loans, the key is likely LO-scoped.)", flush=True)

        # ── Deep pass: pull a few pages to learn the status vocabulary + shapes ──
        from collections import Counter
        statuses, purposes, mtypes = Counter(), Counter(), Counter()
        amt_present = lo_email_present = borr_email_present = total = 0
        sample_status_obj = None
        for offset in (0, 100, 200):
            try:
                pr = c.get(f"{BASE}/loans", headers=H,
                           params={"limit": 100, "offset": offset, "orderBy": "updatedAt", "sort": "DESC"})
                rows, _ = find_list(pr.json())
            except Exception as e:  # noqa: BLE001
                print(f"  [page offset={offset}] error: {e}", flush=True)
                break
            if not rows:
                break
            for ln in rows:
                total += 1
                cls = ln.get("currentLoanStatus")
                st = None
                if isinstance(cls, dict):
                    st = cls.get("status")
                    if sample_status_obj is None:
                        sample_status_obj = cls
                st = st or pick(ln, "currentLoanStatusStatus", "loanStatus", "status")
                statuses[str(st)] += 1
                purposes[str(pick(ln, "loanPurpose", "loan_purpose"))] += 1
                mtypes[str(pick(ln, "mortgageType", "mortgage_type"))] += 1
                if pick(ln, "baseLoanAmount", "purchasePriceOrEstimatedValue"):
                    amt_present += 1
                if pick(ln, "loanOriginatorEmail", "loanOfficerEmail"):
                    lo_email_present += 1
                bs = ln.get("loanBorrowers")
                if (isinstance(bs, list) and bs and isinstance(bs[0], dict)
                        and pick(bs[0], "emailAddressText", "email", "borrowerEmailAddress")):
                    borr_email_present += 1
            if len(rows) < 100:
                break

        print(f"\n  ── DEEP PASS over {total} loans ──", flush=True)
        print(f"  currentLoanStatus.status distribution:", flush=True)
        for st, n in statuses.most_common():
            print(f"     {n:4}  {st}", flush=True)
        print(f"  loanPurpose: {dict(purposes.most_common())}", flush=True)
        print(f"  mortgageType: {dict(mtypes.most_common())}", flush=True)
        print(f"  amount present: {amt_present}/{total} · LO email: {lo_email_present}/{total} · "
              f"borrower email: {borr_email_present}/{total}", flush=True)
        if sample_status_obj is not None:
            print(f"  sample currentLoanStatus object shape: {list(sample_status_obj.keys())}", flush=True)

    print("\nDone. If auth ✓ and loans list, the credentials + scope are good to wire up.", flush=True)


if __name__ == "__main__":
    main()
