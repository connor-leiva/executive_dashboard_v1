"""Acumyn Books - Part 10 Step 2 go/no-go.

Confirms the QBO transaction pull and, above all, the P&L DETAIL report shape against
ONE live realm BEFORE any Books endpoint is written. The report tree is the only
genuinely unknown part of this build (SPEC 2.2 / Part 8) - this is its gate, exactly as
validate_forum.py gated the stage mappings.

Reads QBO creds from backend/.probe.env (gitignored, never opened by anyone but this
script at runtime):
  QBO_REALM_ID=...        required - the company/realm to validate (e.g. ULRG)
  QBO_ACCESS_TOKEN=...    PREFERRED - a still-valid 60-min access token. No rotation,
                          zero risk to the prod connection. Grab one from Intuit's
                          OAuth Playground for the realm.
  QBO_REFRESH_TOKEN=...   fallback - refreshed on use, which ROTATES the refresh token.
                          QBO then invalidates the old one, so the PROD worker for this
                          realm may need a reconnect afterward. Prefer ACCESS_TOKEN.
  QBO_BUSINESS_KEY=ulrg   optional - which seeded business to attach to (default ulrg)

Modes:
  --report-only   Pull only the ProfitAndLoss detail for the period, parse it, print the
                  summary-vs-detail tie, and DUMP the raw report to the scratch dir so it
                  can become tests/fixtures/qbo_pl_detail_sample.json. Least invasive; it
                  alone answers the open question. RECOMMENDED for the first pass.
  (default)       Also run the live txn pull + PLLine/PLSnapshot syncs into a LOCAL seeded
                  DB and print rail counts + a few sample mapped rows (payee/amount/
                  category only - never memo text).

  --period last_month|mtd|ytd    P&L period to check (default last_month)
  --json PATH                    where --report-only writes the raw report

Nothing here writes production data (the sync targets a local seeded DB). The only live
effect is read-only QBO API calls - plus refresh-token rotation IF you use REFRESH_TOKEN.
"""
import argparse
import asyncio
import datetime as dt
import json
import os
import sys

from sqlalchemy import select, func

from app.db import SessionLocal
from app.models import Business, Integration, BookTxn, PLLine, PLSnapshot
from app.security import enc
from app.seed import seed
from app.integrations import qbo
from app.services.metrics import _pl_period
from app.services.books_sync import sync_qbo_txns, sync_qbo_pl_lines
from app.services.sync import sync_qbo_pl

HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = r"C:\Users\17192\AppData\Local\Temp\claude\C--Users-17192-Desktop-executive-dashboard\7c6f912a-1c05-4ff1-a1e9-feea877b8964\scratchpad"


def load_conf() -> dict:
    c = {}
    p = os.path.join(HERE, ".probe.env")
    if os.path.exists(p):
        for ln in open(p, encoding="utf-8"):
            if "=" in ln and not ln.strip().startswith("#"):
                k, _, v = ln.strip().partition("=")
                c[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("QBO_REALM_ID", "QBO_ACCESS_TOKEN", "QBO_REFRESH_TOKEN", "QBO_BUSINESS_KEY"):
        c.setdefault(k, os.environ.get(k, ""))
    return c


async def _access_token(conf: dict) -> str:
    if conf.get("QBO_ACCESS_TOKEN"):
        print("Using QBO_ACCESS_TOKEN (no token rotation).", flush=True)
        return conf["QBO_ACCESS_TOKEN"]
    if conf.get("QBO_REFRESH_TOKEN"):
        print("WARNING: refreshing via QBO_REFRESH_TOKEN - this ROTATES the token; the "
              "prod worker for this realm may need a reconnect afterward.", flush=True)
        tok = await qbo.refresh(conf["QBO_REFRESH_TOKEN"])
        return tok["access_token"]
    sys.exit("No QBO_ACCESS_TOKEN or QBO_REFRESH_TOKEN in backend/.probe.env.")


def _pl_tie(lines: list[dict], summary: dict) -> list[tuple]:
    """(section, detail_sum, summary_val, |diff|, ok) for the Part 7 #2 invariant."""
    seg = {"income": "revenue", "cogs": "cogs", "expense": "opex"}
    out = []
    for sec, skey in seg.items():
        det = round(sum(l["amount"] for l in lines if l["section"] == sec), 2)
        summ = round(float(summary.get(skey) or 0), 2)
        out.append((sec, det, summ, round(abs(det - summ), 2), abs(det - summ) < 1.00))
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--period", default="last_month", choices=["last_month", "mtd", "ytd"])
    ap.add_argument("--json", default=os.path.join(SCRATCH, "qbo_pl_detail_live.json"))
    args = ap.parse_args()

    conf = load_conf()
    realm = conf.get("QBO_REALM_ID")
    if not realm:
        sys.exit("No QBO_REALM_ID in backend/.probe.env.")
    token = await _access_token(conf)
    ps, pe = _pl_period(args.period)
    fetch_end = min(pe, dt.date.today())

    # ── The report-shape confirmation (the go/no-go core) ──
    print(f"\nPulling ProfitAndLoss {ps} .. {fetch_end} for realm {realm[:6]}... ", flush=True)
    report = await qbo.profit_and_loss_detail(realm, token, ps.isoformat(), fetch_end.isoformat())
    lines = qbo.parse_pl_lines(report)
    summary = qbo.parse_pl(report)
    print(f"parse_pl_lines -> {len(lines)} account-level lines")
    by_sec = {}
    for l in lines:
        by_sec.setdefault(l["section"], 0)
        by_sec[l["section"]] += 1
    print("  by section:", {k: by_sec[k] for k in sorted(by_sec)})
    print("\nSummary-vs-detail tie (Part 7 invariant #2):")
    ties = _pl_tie(lines, summary)
    for sec, det, summ, diff, ok in ties:
        print(f"  {sec:<8} detail={det:>14,.2f}  summary={summ:>14,.2f}  diff={diff:>8,.2f}  {'OK' if ok else 'MISMATCH'}")
    tie_ok = all(t[4] for t in ties)

    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nRaw report saved -> {args.json}")
    print("  If the tie is OK, copy it over tests/fixtures/qbo_pl_detail_sample.json")
    print("  (redact any sensitive labels) and re-run pytest tests/test_books_sync.py.")

    if args.report_only:
        print(f"\nbooks_invariants report_shape ok={tie_ok} lines={len(lines)}")
        sys.exit(0 if tie_ok else 2)

    # ── Full pull into a LOCAL seeded DB (txns + PLLine + PLSnapshot) ──
    await seed()
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(
            Business.key == (conf.get("QBO_BUSINESS_KEY") or "ulrg")))).scalar_one()
        integ = (await s.execute(select(Integration).where(
            Integration.provider == "qbo", Integration.business_id == biz.id))).scalar_one_or_none()
        if integ is None:
            integ = Integration(tenant_id=biz.tenant_id, provider="qbo", business_id=biz.id)
            s.add(integ)
        integ.realm_id = realm
        integ.status = "connected"
        integ.access_token_enc = enc(token)
        integ.token_expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=55)
        integ.config = {**(integ.config or {}), "books_backfill_start": ps.isoformat()}
        await s.commit()

        print("\nRunning live sync_qbo_pl / sync_qbo_pl_lines / sync_qbo_txns (local DB)...", flush=True)
        await sync_qbo_pl(s, biz.tenant_id, integ)
        await sync_qbo_pl_lines(s, biz.tenant_id, integ)
        n = await sync_qbo_txns(s, biz.tenant_id, integ)

        captured = (await s.execute(select(func.count(BookTxn.id)).where(
            BookTxn.tenant_id == biz.tenant_id, BookTxn.realm_id == realm))).scalar_one()
        came = (await s.execute(select(func.count(BookTxn.id)).where(
            BookTxn.tenant_id == biz.tenant_id, BookTxn.realm_id == realm,
            BookTxn.came_categorized.is_(True)))).scalar_one()
        by_type = (await s.execute(select(BookTxn.qbo_type, func.count(BookTxn.id)).where(
            BookTxn.tenant_id == biz.tenant_id, BookTxn.realm_id == realm).group_by(BookTxn.qbo_type))).all()

        print(f"\n=== Books txn pull (realm {realm[:6]}...) ===")
        print(f"captured={captured}  came_categorized={came}  pending(all, pre-scan)={captured}")
        print("  by type:", {t: c for t, c in by_type})

        rows = (await s.execute(select(BookTxn).where(
            BookTxn.tenant_id == biz.tenant_id, BookTxn.realm_id == realm)
            .order_by(BookTxn.amount.desc()).limit(5))).scalars().all()
        print("  five largest (payee / amount / current category - no memo):")
        for r in rows:
            print(f"    {(r.payee or '-')[:28]:<28} {float(r.amount):>12,.2f}  {(r.account_label or '-')[:30]}")

        pl_ct = (await s.execute(select(func.count(PLLine.id)).where(
            PLLine.tenant_id == biz.tenant_id, PLLine.business_id == biz.id,
            PLLine.period_start == ps, PLLine.period_end == pe))).scalar_one()
        snap = (await s.execute(select(PLSnapshot).where(
            PLSnapshot.tenant_id == biz.tenant_id, PLSnapshot.business_id == biz.id,
            PLSnapshot.period_start == ps, PLSnapshot.period_end == pe))).scalar_one_or_none()
        print(f"\nPLLine rows for {args.period}: {pl_ct}; PLSnapshot present: {snap is not None}")
        print(f"\nbooks_invariants report_shape_ok={tie_ok} captured={captured} pl_lines={pl_ct}")
        sys.exit(0 if tie_ok else 2)


if __name__ == "__main__":
    asyncio.run(main())
