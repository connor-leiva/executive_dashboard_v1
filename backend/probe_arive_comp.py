"""Profile ARIVE loan-level revenue/compensation fields, grouped by loan officer —
feasibility for (a) a per-LO performance section and (b) a calculated Sympli
revenue / cost-of-sale / profit breakdown (like the Sisu three-lens) to compare
against QuickBooks booked. Read-only. Creds from backend/.probe.env (ARIVE_*).

Run:  cd backend && ./.venv/Scripts/python.exe probe_arive_comp.py
"""
from __future__ import annotations
import os, sys, asyncio, json
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.integrations import arive   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DETAIL_CAP = 220                      # funded UT loans to pull full detail on

# Every field that might carry revenue / cost / comp on a loan.
MONEY_FIELDS = ["grossLoanRevenue", "netLoanRevenue", "brokerFee", "compensation",
                "compensationType", "discountPoints", "financedFees", "reimbursements",
                "toleranceCures", "lenderCredit", "originationFee", "totalLoanRevenue"]


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
        return float(str(v).replace("$", "").replace(",", "")) if v not in (None, "", "null") else None
    except (TypeError, ValueError):
        return None


async def main():
    cid, secret, api_key = load_creds()
    if not all((cid, secret, api_key)):
        sys.exit("Missing ARIVE_* in backend/.probe.env")

    loans = await arive.get_loans(cid, secret, api_key, max_loans=2000)

    def is_ut(l):
        sp = l.get("subjectProperty")
        return isinstance(sp, dict) and str(sp.get("state") or "").upper() == "UT"

    funded_ut = [l for l in loans if arive.is_funded(arive.loan_status(l)) and is_ut(l)]
    print(f"Listed {len(loans)} loans · {len(funded_ut)} funded + Utah. "
          f"Pulling detail on {min(DETAIL_CAP, len(funded_ut))}…\n", flush=True)
    ids = [arive.loan_display_id(l) for l in funded_ut][:DETAIL_CAP]
    details = await arive.get_loans_detail(ids, cid, secret, api_key, concurrency=8)
    dl = list(details.values())
    n = len(dl)
    if not n:
        sys.exit("No detail returned.")

    # 1) Which money fields are populated, and their totals?
    print("── Loan revenue / cost / comp fields (funded UT):")
    for f in MONEY_FIELDS:
        vals = [num(d.get(f)) for d in dl]
        present = [v for v in vals if v is not None]
        strs = [str(d.get(f)) for d in dl if d.get(f) not in (None, "", "null")]
        if not present and not strs:
            print(f"   {f:22} —  (absent)")
            continue
        if all(num(x) is not None for x in strs):    # numeric
            tot = sum(present)
            print(f"   {f:22} {len(present):3}/{n}   total ${tot:,.0f}   avg ${tot/max(len(present),1):,.0f}")
        else:                                          # categorical (e.g. compensationType)
            from collections import Counter
            print(f"   {f:22} {len(strs):3}/{n}   values {Counter(strs).most_common(5)}")

    # 2) Per-LO rollup: count, volume, gross, net, comp.
    by_lo = defaultdict(lambda: {"n": 0, "vol": 0.0, "gross": 0.0, "net": 0.0, "comp": 0.0})
    for d in dl:
        lo = (arive.pick(d, "loanOriginatorEmail", "loanOfficerEmail") or "—").lower()
        r = by_lo[lo]
        r["n"] += 1
        r["vol"] += arive.loan_amount(d)
        for k, fld in (("gross", "grossLoanRevenue"), ("net", "netLoanRevenue"), ("comp", "compensation")):
            r[k] += num(d.get(fld)) or 0
    print(f"\n── Per-LO rollup ({n} funded UT loans):")
    print(f"   {'loan officer':32} {'#':>3} {'volume':>13} {'gross rev':>12} {'net rev':>11} {'comp':>10}")
    for lo, r in sorted(by_lo.items(), key=lambda kv: -kv[1]["gross"]):
        print(f"   {lo[:32]:32} {r['n']:>3} ${r['vol']:>12,.0f} ${r['gross']:>11,.0f} "
              f"${r['net']:>10,.0f} ${r['comp']:>9,.0f}", flush=True)

    # 3) One loan's full economics (so we see how the pieces relate).
    sample = max(dl, key=lambda d: num(d.get("grossLoanRevenue")) or 0)
    print("\n── Sample loan economics (highest gross):")
    for f in ["ariveLoanId", "loanOriginatorName", "baseLoanAmount", "noteRate", "compensation",
              "compensationType"] + MONEY_FIELDS:
        if sample.get(f) not in (None, "", "null"):
            print(f"   {f:22} {sample.get(f)}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
