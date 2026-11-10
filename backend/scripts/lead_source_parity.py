"""Parity check: the legacy reporting dashboard against Axcion's lead-source reports (spec §11).

READ-ONLY. It reads Axcion's stored rows through the same functions the API serves, reads a payload
you saved from the legacy dashboard, and prints one row per metric: match, or the delta. It writes
nothing to the database and nothing to disk.

    # 1. From the legacy app, within the same hour as step 2 (they will not be identical otherwise):
    #      GET /api/dashboard/cohorts?live=true&sisu=true&source=realtor_com   -> legacy_cohorts.json
    #      GET /api/dashboard/team-pipeline?source=realtor_com                 -> legacy_pipeline.json (optional)
    # 2. Against the production database:
    python -m scripts.lead_source_parity --tenant utah-life --business ulrg --group realtor_com \\
        --legacy-cohorts legacy_cohorts.json [--legacy-pipeline legacy_pipeline.json]

Keep the legacy payloads out of the repository: they hold Utah Life's people.

WHAT IS EXPECTED TO DIFFER, and is reported as such rather than as a failure:
  * Leads near a month boundary. Axcion's cohort month is the workspace's calendar date; the legacy
    report used UTC. A lead at 11pm Mountain on 31 January is January here and February there, so
    adjacent months can trade a lead. A month pair whose deltas cancel is labelled "boundary".
  * Spend, ROI and program cost. The legacy figure is a flat fee times non-empty months; Axcion's is
    QuickBooks lines less the cost share. They are printed for the record and never compared.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Business, LeadSourceGroup, Tenant

EXACT = ["leads_received", "contacted_count", "appts_set_count", "appts_held_count", "dead_count",
         "active_count", "sisu_closed_count", "sisu_under_contract_count", "sisu_matched_count"]
CLOSE = [("avg_time_to_contact_minutes", 0.1), ("contacted_pct", 0.1), ("pct_contacted_5min", 0.1),
         ("pct_contacted_24hr", 0.1), ("appts_set_pct", 0.1), ("appts_held_pct", 0.1)]
MONEY = ["gci", "volume", "trans_fee", "agent_payment", "company_dollar"]


def row(metric, scope, legacy, axcion, ok, note=""):
    mark = "match" if ok else "DELTA"
    print(f"{mark:6} {metric:34} {scope:18} legacy={legacy!s:>14}  axcion={axcion!s:>14}  {note}")
    return ok


def _close(a, b, tol):
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol


def compare_cohorts(legacy: dict, ours: dict) -> tuple[int, int]:
    good = bad = 0
    L = {r["cohort_key"]: r for r in legacy.get("cohort_rows", [])}
    A = {r["cohort_key"]: r for r in ours["cohort_rows"] if not r.get("spend_only")}
    lead_delta = {k: (A.get(k, {}).get("leads_received", 0) - L.get(k, {}).get("leads_received", 0))
                  for k in sorted(set(L) | set(A))}
    keys = sorted(set(L) | set(A))
    for i, k in enumerate(keys):
        lr, ar = L.get(k, {}), A.get(k, {})
        boundary = bool(lead_delta[k]) and any(
            lead_delta.get(n, 0) == -lead_delta[k] for n in (keys[i - 1] if i else None, keys[i + 1] if i + 1 < len(keys) else None))
        for m in EXACT:
            ok = row(m, k, lr.get(m), ar.get(m), lr.get(m) == ar.get(m), "boundary" if boundary and m == "leads_received" else "")
            good, bad = good + ok, bad + (not ok)
        for m, tol in CLOSE:
            ok = row(m, k, lr.get(m), ar.get(m), _close(lr.get(m), ar.get(m), tol), f"±{tol}")
            good, bad = good + ok, bad + (not ok)
    sl = {r["cohort_key"]: r for r in legacy.get("speed_table", [])}
    sa = {r["cohort_key"]: r for r in ours["speed_table"]}
    for k in sorted(set(sl) | set(sa)):
        for b in ours["speed_bucket_order"]:
            lv, av = (sl.get(k, {}).get("buckets") or {}).get(b), (sa.get(k, {}).get("buckets") or {}).get(b)
            ok = row(f"speed:{b}", k, lv, av, lv == av)
            good, bad = good + ok, bad + (not ok)
    lt = {str(t["transaction_id"]): t for t in (legacy.get("sisu_meta") or {}).get("closed_transactions", legacy.get("closed_transactions", []))}
    at = {str(t["transaction_id"]): t for t in ours["closed_transactions"]}
    for tid in sorted(set(lt) & set(at)):
        for m in MONEY:
            ok = row(f"txn:{m}", tid, lt[tid].get(m), at[tid].get(m), _close(lt[tid].get(m), at[tid].get(m), 0.01), "±$0.01")
            good, bad = good + ok, bad + (not ok)
        src_l, src_a = str(lt[tid].get("exp_risk_source") or "").upper(), str(at[tid].get("exp_risk_source") or "").upper()
        ok = row("txn:exp_risk_source", tid, src_l, src_a, src_l == src_a)
        good, bad = good + ok, bad + (not ok)
    for tid in sorted(set(lt) - set(at)):
        bad += 1
        row("txn:present", tid, "yes", "no", False, "closed in the legacy report only")
    for tid in sorted(set(at) - set(lt)):
        bad += 1
        row("txn:present", tid, "no", "yes", False, "closed in Axcion only")
    total = round(sum(r.get("sisu_company_dollar_dollars", 0.0) for r in ours["cohort_rows"]), 2)
    ok = row("header:company_dollar = rows", "program", ours["header"]["company_dollar"], total,
             _close(ours["header"]["company_dollar"], total, 0.01))
    good, bad = good + ok, bad + (not ok)
    print(f"\n-- not compared: legacy program cost {(legacy.get('sisu_meta') or {}).get('program_cost_dollars')} "
          f"(flat fee) vs Axcion gross spend {ours['header']['gross_spend']} / net {ours['header']['net_spend']} (QuickBooks)")
    return good, bad


def compare_pipeline(legacy: dict, ours: dict) -> tuple[int, int]:
    good = bad = 0
    L = {a["agent"]: a for a in legacy.get("agents", [])}
    A = {a["agent"]: a for a in ours["agents"]}
    for name in sorted(set(L) | set(A)):
        la, aa = L.get(name), A.get(name)
        if la is None or aa is None:
            bad += 1
            row("agent:present", name, la is not None, aa is not None, False,
                "names differ? (spec §12.4: assignedTo string vs the Axcion agent name)")
            continue
        for m in ("tier", "stuck_count", "pickup_rate", "predicted_cvr"):
            ok = row(f"agent:{m}", name, la.get(m), aa.get(m),
                     la.get(m) == aa.get(m) if m in ("tier", "stuck_count") else _close(la.get(m), aa.get(m), 0.1))
            good, bad = good + ok, bad + (not ok)
        for st, v in (la.get("stage_counts") or {}).items():
            ok = row(f"agent:stage:{st}", name, v, aa["stage_counts"].get(st), v == aa["stage_counts"].get(st))
            good, bad = good + ok, bad + (not ok)
    return good, bad


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--business", default="ulrg")
    ap.add_argument("--group", default="realtor_com")
    ap.add_argument("--legacy-cohorts", required=True, type=Path)
    ap.add_argument("--legacy-pipeline", type=Path)
    ap.add_argument("--window", default="all_time")
    args = ap.parse_args()

    from app.services.lead_source_pipeline import team_pipeline
    from app.services.lead_source_report import cohort_report
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == args.tenant))).scalar_one()
        b = (await s.execute(select(Business).where(Business.tenant_id == t.id, Business.key == args.business))).scalar_one()
        g = (await s.execute(select(LeadSourceGroup).where(
            LeadSourceGroup.tenant_id == t.id, LeadSourceGroup.business_id == b.id,
            LeadSourceGroup.key == args.group))).scalar_one()
        ours = await cohort_report(s, t.id, b, g)
        pipe = await team_pipeline(s, t.id, b, g, window=args.window) if args.legacy_pipeline else None
        await s.rollback()                           # read-only: nothing this session touched is kept
    print(f"Axcion as of {ours['as_of']}. Compare against a legacy payload taken in the same hour.\n")
    good, bad = compare_cohorts(json.loads(args.legacy_cohorts.read_text()), ours)
    if pipe is not None:
        g2, b2 = compare_pipeline(json.loads(args.legacy_pipeline.read_text()), pipe)
        good, bad = good + g2, bad + b2
    print(f"\n{good} match, {bad} differ. Boundary-month lead counts are expected to differ (spec §11.8).")


if __name__ == "__main__":
    asyncio.run(main())
