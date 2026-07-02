"""Validate the corrected commission math against live Sisu for the current month.

Live  = closed deals with close_date in the month (to today).
Projection = Live + pending with projected close (closed_dt) in the month.
Net GCI = team_income (from commission-info); agent commission = GCI − team_income.
Read-only. Creds from env or backend/.probe.env.
"""
from __future__ import annotations
import os, sys, asyncio, datetime as dt
from email.utils import parsedate_to_datetime
import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://api.sisu.co/api"


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


def _num(v):
    if v in (None, "", "null"):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace("$", "").replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def team_income(ci):
    if not ci:
        return None
    if ci.get("team_income") is not None:
        return _num(ci.get("team_income"))
    finals = (ci.get("summaries") or {}).get("final") or {}
    vals = [_num((v or {}).get("value")) for v in finals.values() if (v or {}).get("external_type") == 1]
    return sum(vals) if vals else None


async def main():
    u, t = conf()
    if not (u and t):
        sys.exit("No Sisu creds.")
    today = dt.date.today()
    mstart = today.replace(day=1)
    import calendar
    mend = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    rows = []
    async with httpx.AsyncClient(auth=(u, t), timeout=120, headers={"accept": "application/json"}) as c:
        async def page(pg):
            r = await c.post(f"{BASE}/v1/team/get-team-clients", json={"page": pg, "per_page": 1000})
            r.raise_for_status(); return r.json()
        first = await page(1)
        pages = int((first.get("pagination") or {}).get("pages") or 1)
        rows.extend(first.get("clients") or [])
        sem = asyncio.Semaphore(8)
        async def w(pg):
            async with sem:
                d = await page(pg)
            rows.extend(d.get("clients") or [])
        await asyncio.gather(*(w(p) for p in range(2, pages + 1)))

        def status(r):
            cl = pdate(r.get("closed_dt"))
            if cl and cl <= today:
                return "closed"
            if r.get("archive_ts") or r.get("lost_reason_id") or r.get("status_code") == "LOSTT":
                return "dead"
            if pdate(r.get("uc_dt")):
                return "pending"
            return "active"

        closed = [r for r in rows if status(r) == "closed" and mstart <= (pdate(r.get("closed_dt")) or dt.date(1, 1, 1)) <= today
                  and _num(r.get("gross_commission_amt")) > 0]
        pending = [r for r in rows if status(r) == "pending" and mstart <= (pdate(r.get("closed_dt")) or dt.date(1, 1, 1)) <= mend]
        print(f"{today}: closed MTD={len(closed)}  pending(proj close in month)={len(pending)}", flush=True)

        sem2 = asyncio.Semaphore(10)
        async def ti(r):
            async with sem2:
                resp = await c.get(f"{BASE}/v1/client/commission-info/{r.get('client_id')}")
            try:
                ci = (resp.json() or {}).get("commission_info") or {}
            except Exception:
                ci = {}
            return team_income(ci)

        async def agg(deals, label):
            tis = await asyncio.gather(*(ti(r) for r in deals))
            gross = sum(_num(r.get("gross_commission_amt")) for r in deals)
            net = sum(x for x in tis if x is not None)
            missing = sum(1 for x in tis if x is None)
            comm = gross - net
            print(f"  {label}: units={len(deals)} gross_gci=${gross:,.0f} commissions=${comm:,.0f} "
                  f"net_gci(company$)=${net:,.0f}  (missing team_income: {missing})", flush=True)
            return gross, comm, net

        print("LIVE (closed MTD):")
        lg, lc, ln = await agg(closed, "live")
        print("PROJECTION (closed + pending):")
        pg, pc, pn = await agg(closed + pending, "projection")
        print(f"\nCheck: projected commissions ${pc:,.0f} >= live commissions ${lc:,.0f}? "
              f"{'YES' if pc + 0.5 >= lc else 'NO'}")


if __name__ == "__main__":
    asyncio.run(main())
