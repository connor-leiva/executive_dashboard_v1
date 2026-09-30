"""What the AP Clerk is allowed to know.

`build_context` is shaped around launches, pacing, a roster of watched accounts and a media
library — every bit of which is meaningless to an accounts-payable clerk and all of which would
be spent out of the same prompt budget as the bills it is supposed to read. So finance gets its
own slice, built here.

TWO PROPERTIES THIS MODULE HOLDS, and both are worth stating because the next person to add a
skill will be tempted to relax one:

**It only reads.** Every function below calls list/propose functions and nothing else. It never
imports a payables writer, so there is no path from a skill's context to a bill being coded,
approved or paid — and `payables_actor.require_human` guards those writers anyway, which makes
this belt as well as braces. A test asserts the module imports no writer by name.

**It never silently truncates.** `_fill_prompt` cuts the serialised context at 12000 characters,
which for a workspace with three hundred open bills means the model is handed JSON that stops
mid-object. It will do something with that, and whatever it does will look like an answer. So
every list here is fitted to a character budget and says what it left out — a model told "40 of
312, oldest first" can caveat its summary; a model handed a severed brace cannot.
"""
from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Business, Payable, Vendor
from . import payables, payables_run, payables_vendor

# Budgeted by SERIALISED SIZE, not by row count. Counting rows was the obvious thing and it was
# wrong: a vendor row is three times the width of an account row, so sixty vendors and eighty
# accounts came to 20,924 characters against a 12,000 budget — comfortably inside both caps and
# nearly double the space that exists. Sizes are what the limit is actually made of.
# Each list carries its own share of the 12000 _fill_prompt allows, leaving the rest for the
# scalars, the notes and the fixed keys.
VENDOR_BUDGET = 3500
BILL_BUDGET = 3500
ACCOUNT_BUDGET = 2000
AP_SKILLS = {"ap_intake", "ap_exception_scan", "ap_run_prep", "ap_aging_digest", "ap_1099_check"}


def _fit(rows: list, budget: int, what: str) -> tuple[list, str | None]:
    """As many rows as fit in `budget` characters, and a note naming what was dropped.

    The note is the point. A clerk told "40 of 312 open bills, oldest first" writes a different
    summary from one that believes it saw all 312 — and either of them is better off than one
    handed JSON that stops in the middle of a brace, which is what a silent cut produces.
    """
    out, used = [], 0
    for r in rows:
        size = len(json.dumps(r, default=str, ensure_ascii=False)) + 2   # +2 for ", "
        if used + size > budget:
            break
        out.append(r)
        used += size
    if len(out) == len(rows):
        return out, None
    return out, f"showing {len(out)} of {len(rows)} {what}"


async def _vendors(s: AsyncSession, tenant_id) -> tuple[list, str | None]:
    rows = await payables_vendor.list_vendors(s, tenant_id)
    # Shaped BEFORE fitting, or the budget is measured against a row the clerk never sees.
    shaped = [{"id": v["id"], "name": v["display_name"], "legal_name": v.get("legal_name"),
               "dba": v.get("dba"), "status": v["status"], "terms_days": v.get("terms_days"),
               "is_1099": v.get("is_1099"), "has_w9": bool(v.get("w9")),
               "banking_verified": bool((v.get("bank") or {}).get("verified_at"))}
              for v in rows]
    return _fit(shaped, VENDOR_BUDGET, "vendors")


async def _open_bills(s: AsyncSession, tenant_id) -> tuple[list, str | None]:
    """Bills that are still somebody's problem. `released` and `reconciled` are finished and
    `rejected` is decided, so they are noise in every one of these skills."""
    rows = [p for p in await payables.list_payables(s, tenant_id)
            if p["status"] not in ("released", "reconciled", "rejected")]
    rows.sort(key=lambda p: (p.get("due_date") or "9999-12-31", p["invoice_number"]))
    shaped = [{"invoice": p["invoice_number"], "vendor": p["vendor"], "amount": p["amount"],
               "due_date": p["due_date"], "invoice_date": p["invoice_date"],
               "status": p["status"], "entity": p.get("business_name"),
               "vendor_status": p.get("vendor_status"), "band": p.get("band"),
               "awaiting": p.get("awaiting"),
               "holds": [h["key"] for h in (p.get("holds") or [])]}
              for p in rows]
    return _fit(shaped, BILL_BUDGET, "open bills")


async def _accounts(s: AsyncSession, tenant_id, business_id=None) -> tuple[list, str | None]:
    """The standard chart, by NAME. The clerk suggests a name and a person picks the real
    account — handing it ids would invite it to emit one that looks plausible and is not."""
    from . import coa_map
    chart = await coa_map.standard_chart(s, tenant_id)
    live = [a for a in chart if getattr(a, "is_active", True)]
    shaped = [{"code": a.code, "name": a.name, "bucket": a.bucket} for a in live]
    return _fit(shaped, ACCOUNT_BUDGET, "accounts")


async def build_ap_context(s: AsyncSession, tenant_id, skill_key: str, *,
                           today: dt.date | None = None) -> dict:
    """The context slice for ONE accounts-payable skill. Read-only, capped, and annotated."""
    today = today or dt.date.today()
    ctx: dict = {"today": today.isoformat()}
    notes: list[str] = []

    if skill_key == "ap_intake":
        ctx["vendors"], n = await _vendors(s, tenant_id)
        notes.append(n)
        ctx["accounts"], n = await _accounts(s, tenant_id)
        notes.append(n)
        # Invoice numbers already on file, so the clerk can say "you have seen this one" instead
        # of proposing a bill that is about to collide with the duplicate constraint.
        seen = (await s.execute(select(Payable.invoice_number, Vendor.display_name)
                                .join(Vendor, Vendor.id == Payable.vendor_id)
                                .where(Payable.tenant_id == tenant_id)
                                .order_by(Payable.created_at.desc()).limit(40))).all()
        ctx["invoice_numbers_on_file"] = [{"invoice": i, "vendor": v} for i, v in seen]

    elif skill_key in ("ap_exception_scan", "ap_aging_digest"):
        ctx["open_bills"], n = await _open_bills(s, tenant_id)
        notes.append(n)
        ctx["vendors"], n = await _vendors(s, tenant_id)
        notes.append(n)
        ctx["bank_cooldown_hours"] = payables_run.settings.PAYABLES_BANK_COOLDOWN_HOURS
        ctx["approval_bands"] = [{"label": b["label"], "from": b["min_amount"],
                                  "to": b["max_amount"]}
                                 for b in await payables.list_policies(s, tenant_id)]

    elif skill_key == "ap_run_prep":
        # One proposal per entity, because a run is per entity. The clerk describes what the
        # system computed; it does not assemble a run of its own.
        runs = []
        for b in (await s.execute(select(Business).where(
                Business.tenant_id == tenant_id).order_by(Business.sort_order))).scalars():
            proposed = await payables_run.propose_run(s, tenant_id, b.id, run_date=today)
            if proposed["lines"] or proposed.get("upcoming"):
                runs.append({"entity": b.name, "run_date": proposed["run_date"],
                             "horizon": proposed["horizon"], "total": proposed["total"],
                             "held_count": proposed["held_count"],
                             "lines": proposed["lines"],
                             "waiting": proposed.get("upcoming", [])})
        ctx["proposed_run"] = runs
        ctx["lookahead_days"] = payables_run.RUN_LOOKAHEAD_DAYS

    elif skill_key == "ap_1099_check":
        ctx["vendors"], n = await _vendors(s, tenant_id)
        notes.append(n)
        ctx["threshold"] = 600            # the IRS reporting floor for most 1099-NEC payments
        paid = (await s.execute(select(Vendor.display_name, Payable.amount)
                                .join(Payable, Payable.vendor_id == Vendor.id)
                                .where(Payable.tenant_id == tenant_id,
                                       Payable.status.in_(("released", "reconciled")),
                                       Payable.invoice_date >= dt.date(today.year, 1, 1)))).all()
        totals: dict[str, float] = {}
        for name, amt in paid:
            totals[name] = round(totals.get(name, 0) + float(amt), 2)
        ctx["paid_ytd"] = [{"vendor": k, "paid": v} for k, v in
                           sorted(totals.items(), key=lambda kv: -kv[1])]

    live = [n for n in notes if n]
    if live:
        ctx["truncated"] = live
    return ctx
