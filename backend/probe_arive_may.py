"""Reconcile Sympli's May QuickBooks P&L against Arive — to reverse-engineer the
LO economic setup. Pulls loans that FUNDED in May 2026 (actual funding date from
loanStatusHistory, not the status-date proxy), with gross/net/comp per LO and by
state, so we can match QBO Mortgage Revenue and read the LO-comp rate off the books.
Read-only. Creds from backend/.probe.env (ARIVE_*).

Run:  cd backend && ./.venv/Scripts/python.exe probe_arive_may.py
"""
from __future__ import annotations
import os, sys, asyncio, datetime as dt
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.integrations import arive   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MAY_START, MAY_END = dt.date(2026, 5, 1), dt.date(2026, 5, 31)


def load_creds():
    c = {}
    p = os.path.join(HERE, ".probe.env")
    if os.path.exists(p):
        for ln in open(p, encoding="utf-8"):
            s = ln.strip()
            if "=" in s and not s.startswith("#"):
                k, _, v = s.partition("="); c[k.strip()] = v.strip().strip('"').strip("'")
    g = lambda k: os.environ.get(k) or c.get(k)
    return g("ARIVE_CLIENT_ID"), g("ARIVE_SECRET"), g("ARIVE_API_KEY")


def num(v):
    try:
        return float(str(v).replace("$", "").replace(",", "")) if v not in (None, "", "null") else 0.0
    except (TypeError, ValueError):
        return 0.0


def pdate(v):
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def funded_date(full):
    """Actual date the loan hit a funded milestone (from the status history)."""
    best = None
    for h in (full.get("loanStatusHistory") or []):
        st = str(h.get("status") or "").upper()
        if st in ("LOAN_FUNDED", "FUNDED", "LOAN_FUNDED_ED"):
            d = pdate(h.get("date"))
            if d and (best is None or d < best):     # first funding event
                best = d
    if best:
        return best
    cls = full.get("currentLoanStatus")              # fallback: current status date if funded
    if isinstance(cls, dict) and arive.is_funded(str(cls.get("status") or "")):
        return pdate(cls.get("date"))
    return None


async def main():
    cid, secret, api_key = load_creds()
    if not all((cid, secret, api_key)):
        sys.exit("Missing ARIVE_* in backend/.probe.env")

    loans = await arive.get_loans(cid, secret, api_key, max_loans=2000)
    funded_ids = [arive.loan_display_id(l) for l in loans if arive.is_funded(arive.loan_status(l))]
    print(f"Listed {len(loans)} loans · {len(funded_ids)} currently funded. Pulling detail…", flush=True)
    details = await arive.get_loans_detail(funded_ids, cid, secret, api_key, concurrency=8)

    may = []
    hist_missing = 0
    for d in details.values():
        fd = funded_date(d)
        if fd is None:
            hist_missing += 1
            continue
        if MAY_START <= fd <= MAY_END:
            may.append(d)

    def agg(rows):
        gross = sum(num(r.get("grossLoanRevenue")) for r in rows)
        net = sum(num(r.get("netLoanRevenue")) for r in rows)
        comp = sum(num(r.get("compensation")) for r in rows)
        reimb = sum(num(r.get("reimbursements")) for r in rows)
        vol = sum(num(r.get("baseLoanAmount")) for r in rows)
        return len(rows), vol, gross, net, comp, reimb

    def is_ut(d):
        sp = d.get("subjectProperty")
        return isinstance(sp, dict) and str(sp.get("state") or "").upper() == "UT"

    may_ut = [d for d in may if is_ut(d)]
    print(f"\nFunded in MAY 2026: {len(may)} loans  ({len(may_ut)} Utah)."
          f"  (detail w/o usable funding date: {hist_missing})\n", flush=True)

    for label, rows in (("ALL states", may), ("Utah only", may_ut)):
        n, vol, gross, net, comp, reimb = agg(rows)
        print(f"── {label}: {n} funded · volume ${vol:,.0f}")
        print(f"     gross commission ${gross:,.2f} · net ${net:,.2f} · compensation ${comp:,.2f} · reimbursements ${reimb:,.2f}", flush=True)

    print("\n── Per-LO commission (May, all states):")
    by = defaultdict(lambda: {"n": 0, "gross": 0.0, "vol": 0.0})
    for d in may:
        lo = (arive.pick(d, "loanOriginatorName") or arive.pick(d, "loanOriginatorEmail") or "—")
        by[lo]["n"] += 1
        by[lo]["gross"] += num(d.get("grossLoanRevenue"))
        by[lo]["vol"] += num(d.get("baseLoanAmount"))
    for lo, r in sorted(by.items(), key=lambda kv: -kv[1]["gross"]):
        print(f"     {str(lo)[:28]:28} {r['n']:>3} loans · ${r['vol']:>12,.0f} vol · ${r['gross']:>11,.2f} commission", flush=True)

    # keyDates field names on one loan (documents the real funding-date source).
    sample = next(iter(details.values()), {})
    kd = sample.get("keyDates")
    if isinstance(kd, dict):
        print("\n── keyDates fields available:", sorted(kd.keys()), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
