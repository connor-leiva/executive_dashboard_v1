"""One-off Sisu audit — reconcile "pending" with the team's Sisu view.

Full team pull (POST get-team-clients), then for PENDING deals: inventory every
date field and count how many fall in a target month, to find the field Sisu's
"Status Start/End Date" filter uses (projected close). Also inventories
commission fields on closed deals (for agent_commission). Amounts aren't PII.

Creds from env (SISU_USERNAME / SISU_API_TOKEN) or backend/.probe.env.
Run:  cd backend && SISU_USERNAME=.. SISU_API_TOKEN=.. ./.venv/Scripts/python.exe probe_sisu.py
"""
from __future__ import annotations
import os, sys, asyncio, datetime as dt
from collections import Counter
from email.utils import parsedate_to_datetime
import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://api.sisu.co/api"
ENDPOINT = "/v1/team/get-team-clients"
TARGET_YM = (2026, 7)     # July 2026, to match the screenshot filter


def conf():
    c = {}
    p = os.path.join(HERE, ".probe.env")
    if os.path.exists(p):
        for ln in open(p, encoding="utf-8"):
            if "=" in ln and not ln.strip().startswith("#"):
                k, _, v = ln.strip().partition("=")
                c[k.strip()] = v.strip().strip('"').strip("'")
    u = os.environ.get("SISU_USERNAME") or c.get("SISU_USERNAME")
    t = os.environ.get("SISU_API_TOKEN") or c.get("SISU_API_TOKEN")
    return u, t


def pdate(v):
    if not v:
        return None
    try:
        return parsedate_to_datetime(str(v)).date()
    except (TypeError, ValueError, IndexError):
        try:
            return dt.date.fromisoformat(str(v)[:10])
        except ValueError:
            return None


async def get_page(client, page):
    for attempt in range(4):
        r = await client.post(f"{BASE}{ENDPOINT}", json={"page": page, "per_page": 1000})
        if r.status_code in (429, 500, 502, 503) and attempt < 3:
            await asyncio.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return r.json()


async def main():
    u, t = conf()
    if not (u and t):
        sys.exit("No Sisu creds (env SISU_USERNAME/SISU_API_TOKEN or backend/.probe.env).")
    today = dt.date.today()
    all_rows = []
    async with httpx.AsyncClient(auth=(u, t), timeout=120, headers={"accept": "application/json"}) as c:
        first = await get_page(c, 1)
        pages = int((first.get("pagination") or {}).get("pages") or 1)
        all_rows.extend(first.get("clients") or [])
        sem = asyncio.Semaphore(8)

        async def worker(pg):
            async with sem:
                d = await get_page(c, pg)
            all_rows.extend(d.get("clients") or [])
        await asyncio.gather(*(worker(p) for p in range(2, pages + 1)))
    print(f"pulled {len(all_rows)} rows over {pages} pages", flush=True)

    def status(c):
        cl = pdate(c.get("closed_dt"))
        if cl and cl <= today:
            return "closed"
        if c.get("archive_ts") or c.get("lost_reason_id") or c.get("status_code") == "LOSTT":
            return "dead"
        if pdate(c.get("uc_dt")):
            return "pending"
        return "active"

    st = Counter(status(c) for c in all_rows)
    print("status distribution (our classify):", dict(st))
    print("raw status_code distribution:", dict(Counter(str(c.get('status_code')) for c in all_rows).most_common(12)))

    pend = [c for c in all_rows if status(c) == "pending"]
    print(f"\nPENDING total: {len(pend)}")

    # every *_dt / date-ish field on pending records + how many fall in the target month
    date_fields = Counter()
    for c in pend:
        for k, v in c.items():
            if isinstance(v, str) and (k.endswith("_dt") or "date" in k.lower() or "_ts" in k):
                if pdate(v):
                    date_fields[k] += 1
    print("\ndate-ish fields present on pending (non-null count):")
    for k, n in date_fields.most_common():
        in_month = sum(1 for c in pend if (lambda d: d and (d.year, d.month) == TARGET_YM)(pdate(c.get(k))))
        gci_m = sum(float(c.get("gross_commission_amt") or 0) for c in pend
                    if (lambda d: d and (d.year, d.month) == TARGET_YM)(pdate(c.get(k))))
        print(f"   {k:<24} non-null={n:<5} in {TARGET_YM[0]}-{TARGET_YM[1]:02d}={in_month:<4} GCI≈${gci_m:,.0f}")

    # commission field inventory on closed deals
    closed = [c for c in all_rows if status(c) == "closed"]
    print(f"\nCLOSED total: {len(closed)}")
    money_keys = set()
    for c in closed[:300]:
        for k, v in c.items():
            kl = k.lower()
            if any(w in kl for w in ("comm", "split", "gci", "net", "company", "dollar", "agent_")):
                if isinstance(v, (int, float, str)):
                    money_keys.add(k)
    print("commission-ish fields on closed:", sorted(money_keys))
    sample = next((c for c in closed if float(c.get("gross_commission_amt") or 0) > 0), None)
    if sample:
        print("\nsample closed deal — commission-ish values:")
        for k in sorted(money_keys):
            print(f"   {k} = {sample.get(k)}")


if __name__ == "__main__":
    asyncio.run(main())
