"""Find + profile Sisu's agent-entered 'Mortgage Company' field, to use as a direct
attribution signal for the ULRG->Sympli attachment flywheel (better than matching
borrower emails). Read-only. Creds from backend/.probe.env (SISU_USERNAME/SISU_API_TOKEN).

Run:  cd backend && ./.venv/Scripts/python.exe probe_sisu_mortgage.py
"""
from __future__ import annotations
import os, sys, re, asyncio, datetime as dt
from collections import Counter
from email.utils import parsedate_to_datetime
import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://api.sisu.co/api"
MAX_PAGES = 40                         # full team is ~33 pages


def conf():
    c = {}
    p = os.path.join(HERE, ".probe.env")
    if os.path.exists(p):
        for ln in open(p, encoding="utf-8"):
            s = ln.strip()
            if "=" in s and not s.startswith("#"):
                k, _, v = s.partition("="); c[k.strip()] = v.strip().strip('"').strip("'")
    return (os.environ.get("SISU_USERNAME") or c.get("SISU_USERNAME"),
            os.environ.get("SISU_API_TOKEN") or c.get("SISU_API_TOKEN"))


def pdate(v):
    if not v:
        return None
    try:
        return parsedate_to_datetime(str(v)).date()
    except Exception:
        try:
            return dt.date.fromisoformat(str(v)[:10])
        except Exception:
            return None


KEY_RE = re.compile(r"mortgage|lender|loan|vendor|company|title|escrow|financ|preferred", re.I)


async def main():
    u, t = conf()
    if not (u and t):
        sys.exit("No Sisu creds in backend/.probe.env (SISU_USERNAME / SISU_API_TOKEN).")
    today = dt.date.today()
    rows = []
    async with httpx.AsyncClient(auth=(u, t), timeout=120, headers={"accept": "application/json"}) as c:
        async def page(pg):
            r = await c.post(f"{BASE}/v1/team/get-team-clients", json={"page": pg, "per_page": 1000})
            r.raise_for_status(); return r.json()
        first = await page(1)
        pages = min(int((first.get("pagination") or {}).get("pages") or 1), MAX_PAGES)
        rows.extend(first.get("clients") or [])
        sem = asyncio.Semaphore(8)
        async def w(pg):
            async with sem:
                d = await page(pg)
            rows.extend(d.get("clients") or [])
        await asyncio.gather(*(w(p) for p in range(2, pages + 1)))
    print(f"Pulled {len(rows)} clients across {pages} pages.\n", flush=True)

    # 1) Which field names could hold the mortgage company?
    keys = set()
    for r in rows[:2000]:
        keys.update(r.keys())
    cand = sorted(k for k in keys if KEY_RE.search(k))
    print("── Candidate field names (mortgage/lender/company/title/…):")
    for k in cand:
        # population rate + a couple non-empty sample values
        vals = [r.get(k) for r in rows if r.get(k) not in (None, "", 0, "0")]
        samp = []
        for v in vals[:3]:
            samp.append(str(v)[:48] if not isinstance(v, dict) else "{" + ",".join(list(v)[:4]) + "}")
        print(f"   {k:34} populated {len(vals):5}/{len(rows)}   e.g. {samp}", flush=True)

    # 2) Pick the most likely mortgage field(s) and profile on CLOSED BUY-side deals.
    def status(r):
        cl = pdate(r.get("closed_dt"))
        if cl and cl <= today:
            return "closed"
        if r.get("archive_ts") or r.get("lost_reason_id") or r.get("status_code") == "LOSTT":
            return "dead"
        return "pending" if pdate(r.get("uc_dt")) else "active"

    buys = [r for r in rows if r.get("type_id") == "b" and status(r) == "closed"]
    print(f"\n── Closed BUY-side deals in sample: {len(buys)}", flush=True)

    mort_keys = [k for k in cand if re.search(r"mortgage|lender", k, re.I)] or cand
    for k in mort_keys:
        def val(r):
            v = r.get(k)
            if isinstance(v, dict):
                v = v.get("name") or v.get("company") or v.get("label") or v.get("value")
            return (str(v).strip() if v not in (None, "", 0, "0") else "")
        populated = [r for r in buys if val(r)]
        sympli = [r for r in populated if re.search(r"sympli", val(r), re.I)]
        with_email = [r for r in buys if (r.get("email") or "").strip()]
        sympli_and_email = [r for r in sympli if (r.get("email") or "").strip()]
        print(f"\n  FIELD '{k}' on closed buy-side:")
        print(f"     populated: {len(populated)}/{len(buys)} ({round(100*len(populated)/max(len(buys),1))}%)"
              f"  · matches /sympli/: {len(sympli)}"
              f"  · buys with borrower email: {len(with_email)}"
              f"  · sympli-tagged AND has email: {len(sympli_and_email)}")
        top = Counter(val(r) for r in populated).most_common(12)
        print(f"     top values:")
        for name, n in top:
            mark = "  <-- SYMPLI" if re.search(r"sympli", name, re.I) else ""
            print(f"        {n:4}  {name[:60]}{mark}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
