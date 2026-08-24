"""Capture every read-side payload for a tenant, as a stable JSON tree.

Phase 3 rewrites how the compute layer FINDS a business — from literal `Business.key ==
"ulrg"` comparisons to a `Business.kind` lookup. That is a refactor with no intended
behavioural change, in the two files (`metrics.py`, `lineage.py`) that produce the numbers on
the live dashboard. The failure mode is not an exception; it is a subtly wrong figure that
nobody notices for a week.

So: snapshot before, refactor, snapshot after, diff. An empty diff is the whole proof.

  ./.venv/Scripts/python.exe -m scripts.snapshot_payloads before.json
  ...refactor...
  ./.venv/Scripts/python.exe -m scripts.snapshot_payloads after.json
  ./.venv/Scripts/python.exe -m scripts.snapshot_payloads --diff before.json after.json

Not a pytest: it deliberately runs against whatever database DATABASE_URL points at, so the
same script can snapshot a seeded local database or (read-only) a copy of production.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

try:                                   # Windows consoles default to cp1252; payloads carry arrows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
from decimal import Decimal

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Business, Launch, Tenant

# Every period shape the UI can ask for, so a refactor cannot quietly change one window.
PERIODS = ["mtd", "last_month", "qtd", "ytd", "year", "next_month",
           "c:2026-02-10:2026-04-05"]

# Drill keys metric_detail branches on that are not in any tab set (flywheel/binder/cashflow
# families, and the three-lens financial rows).
LINEAGE_EXTRA = [
    "binder_matrix", "binder_review",
    "flywheel_agent_referrals", "flywheel_buyers", "flywheel_captured",
    "flywheel_referral_no_deal", "flywheel_sympli_linked", "flywheel_sympli_referred",
    "flywheel_vendor_no_loan", "flywheel_zero_referrals", "flywheel_lost",
    "forum_cashflow", "bc_cashflow", "bc_failed_payments", "bc_installments",
    "bc_new_members", "bc_pastdue", "bc_renewals_due", "bc_roster", "bc_streams",
    "bc_unregistered", "edge_roster", "edge_new_members", "edge_pipeline", "edge_payments",
    "fin_closed", "fin_projected", "fin_expenses", "monthly", "pastdue", "registered",
    "renewal_book", "unregistered", "sympli_commission", "loan_stage",
    "books_queue", "books_ic", "books_pl_lines",
]


# Fields that carry "when this payload was built". They differ between two runs seconds apart,
# which would drown the signal — the point of the diff is figures, not clocks.
VOLATILE = {"as_of", "generated_at", "built_at", "now", "fetched_at", "last_synced_at",
            "created_at", "updated_at"}


def _plain(v):
    """Make the payload JSON-comparable without losing precision that matters."""
    if isinstance(v, Decimal):
        return f"{v:f}"                       # str, not float — 0.1 must not become 0.1000000001
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {str(k): ("__WHEN__" if str(k) in VOLATILE else _plain(x))
                for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
    if hasattr(v, "model_dump"):              # pydantic response models
        return _plain(v.model_dump())
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


async def _capture(tenant_slug: str) -> dict:
    from app.services import becollective, binder, books, edge, financials, forum, lineage, metrics
    from app.services.launch import active_launch_for, compute_launch
    from app.services.sales_desk import compute_sales_desk
    from app.services.scorecard import build_scorecard
    from app.services.tabs import tenant_tabs

    out: dict = {}
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one_or_none()
        if t is None:
            raise SystemExit(f"no tenant '{tenant_slug}'")
        tid = t.id
        businesses = (await s.execute(select(Business).where(
            Business.tenant_id == tid).order_by(Business.sort_order))).scalars().all()
        out["businesses"] = _plain([
            {"key": b.key, "name": b.name, "kind": b.kind, "display_tab": b.display_tab,
             "include_in_portfolio": b.include_in_portfolio, "archetype": b.archetype,
             "is_jv": b.is_jv, "jv_share": b.jv_share} for b in businesses])
        out["tabs"] = _plain(await tenant_tabs(s, tid))

    # ── the dashboard, every period ──
    for p in PERIODS:
        async with SessionLocal() as s:
            try:
                out[f"dashboard[{p}]"] = _plain(await metrics.build_dashboard(s, tid, p))
            except Exception as e:                          # noqa: BLE001 — record, do not stop
                out[f"dashboard[{p}]"] = f"__ERROR__ {type(e).__name__}: {e}"

    # ── per-business financials, every period ──
    for b in businesses:
        for p in PERIODS:
            async with SessionLocal() as s:
                biz = await s.get(Business, b.id)
                try:
                    out[f"financials[{b.key}][{p}]"] = _plain(
                        await financials.compute_financials(s, tid, biz, p))
                except Exception as e:                      # noqa: BLE001
                    out[f"financials[{b.key}][{p}]"] = f"__ERROR__ {type(e).__name__}: {e}"

    # ── program views ──
    for name, fn in (("forum", forum.build_forum), ("becollective", becollective.build_becollective),
                     ("edge", edge.build_edge)):
        for p in ("mtd", "ytd"):
            async with SessionLocal() as s:
                try:
                    out[f"{name}[{p}]"] = _plain(await fn(s, tid, p))
                except Exception as e:                      # noqa: BLE001
                    out[f"{name}[{p}]"] = f"__ERROR__ {type(e).__name__}: {e}"

    # ── every lineage drill key: this is where the Business.key lookups concentrate ──
    # Derived from the tab registry's own vocabulary plus the keys metric_detail branches on,
    # so the list cannot drift away from what the UI can actually ask for.
    from app.services import tabs as tabs_mod
    keys = sorted(set(tabs_mod._ULRG) | set(tabs_mod._FORUM) | set(tabs_mod._BC)
                  | set(tabs_mod._EDGE) | set(tabs_mod._SYMPLI) | set(tabs_mod._FINANCIAL)
                  | set(LINEAGE_EXTRA))
    out["__lineage_keys__"] = keys
    for k in keys:
        for bkey in [None] + [b.key for b in businesses]:
            async with SessionLocal() as s:
                try:
                    res = await lineage.metric_detail(s, tid, k, "mtd", business=bkey)
                except Exception as e:                      # noqa: BLE001
                    res = f"__ERROR__ {type(e).__name__}: {e}"
                out[f"lineage[{k}][{bkey}]"] = _plain(res)

    # ── books ──
    for name, fn in (("books_home", books.build_books_home), ("books_queue", books.build_books_queue),
                     ("books_ic", books.build_books_ic)):
        async with SessionLocal() as s:
            try:
                out[name] = _plain(await fn(s, tid))
            except Exception as e:                          # noqa: BLE001
                out[name] = f"__ERROR__ {type(e).__name__}: {e}"
    for bkey in ["all"] + [b.key for b in businesses]:
        async with SessionLocal() as s:
            try:
                out[f"books_pl[{bkey}]"] = _plain(await books.build_books_pl(s, tid, bkey, "mtd"))
            except Exception as e:                          # noqa: BLE001
                out[f"books_pl[{bkey}]"] = f"__ERROR__ {type(e).__name__}: {e}"

    # ── binder + scorecard + launch + sales desk ──
    async with SessionLocal() as s:
        for name, fn in (("binder_matrix", binder.build_matrix), ("binder_review", binder.build_review),
                         ("binder_rules", binder.build_rules)):
            try:
                out[name] = _plain(await fn(s, tid))
            except Exception as e:                          # noqa: BLE001
                out[name] = f"__ERROR__ {type(e).__name__}: {e}"

    for b in businesses:
        async with SessionLocal() as s:
            try:
                out[f"scorecard[{b.key}]"] = _plain(await build_scorecard(s, tid, b.id, 13))
            except Exception as e:                          # noqa: BLE001
                out[f"scorecard[{b.key}]"] = f"__ERROR__ {type(e).__name__}: {e}"

    async with SessionLocal() as s:
        launches = (await s.execute(select(Launch).where(Launch.tenant_id == tid))).scalars().all()
        lids = [(l.id, l.name) for l in launches]
    for lid, lname in lids:
        async with SessionLocal() as s:
            L = await s.get(Launch, lid)
            try:
                out[f"launch[{lname}]"] = _plain(await compute_launch(s, tid, L))
            except Exception as e:                          # noqa: BLE001
                out[f"launch[{lname}]"] = f"__ERROR__ {type(e).__name__}: {e}"
            try:
                out[f"sales_desk[{lname}]"] = _plain(await compute_sales_desk(s, tid, L))
            except Exception as e:                          # noqa: BLE001
                out[f"sales_desk[{lname}]"] = f"__ERROR__ {type(e).__name__}: {e}"
    return out


def _paths(v, prefix=""):
    """Flatten a payload to leaf paths, so a diff points at the FIELD that moved rather than
    dumping two 400-character blobs and leaving the reader to spot the difference."""
    if isinstance(v, dict):
        for k, x in v.items():
            yield from _paths(x, f"{prefix}.{k}")
    elif isinstance(v, list):
        for i, x in enumerate(v):
            yield from _paths(x, f"{prefix}[{i}]")
    else:
        yield prefix, v


def _diff(a_path: str, b_path: str) -> int:
    a = json.load(open(a_path, encoding="utf-8"))
    b = json.load(open(b_path, encoding="utf-8"))
    bad = 0
    for k in sorted(set(a) | set(b)):
        if k not in a:
            print(f"  + {k}  (only after)"); bad += 1
        elif k not in b:
            print(f"  - {k}  (only before)"); bad += 1
        elif a[k] != b[k]:
            bad += 1
            print(f"  ~ {k}")
            pa, pb = dict(_paths(a[k])), dict(_paths(b[k]))
            for path in sorted(set(pa) | set(pb)):
                if pa.get(path) != pb.get(path):
                    print(f"      {path}")
                    print(f"        before: {pa.get(path)!r}")
                    print(f"        after : {pb.get(path)!r}")
    print(f"\n{'DIFFERENCES: ' + str(bad) if bad else 'IDENTICAL — no payload changed'}"
          f"   ({len(a)} keys compared)")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", help="write the snapshot here")
    ap.add_argument("--tenant", default="springb")
    ap.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"))
    a = ap.parse_args()
    if a.diff:
        return _diff(*a.diff)
    if not a.out:
        ap.error("give an output path, or --diff BEFORE AFTER")
    data = asyncio.run(_capture(a.tenant))
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True, ensure_ascii=False)
    errs = sum(1 for v in data.values() if isinstance(v, str) and v.startswith("__ERROR__"))
    print(f"[snapshot] {len(data)} payloads -> {a.out}"
          f"{f'  ({errs} raised — recorded, and they must raise identically after)' if errs else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
