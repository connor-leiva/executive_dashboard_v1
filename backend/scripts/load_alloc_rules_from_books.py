"""Turn the audited-books allocation decisions into applied allocation rules.

    python scripts/load_alloc_rules_from_books.py "<workbook>.xlsx"            # plan only
    python scripts/load_alloc_rules_from_books.py "<workbook>.xlsx" --wire     # create them

Needs `openpyxl`, which is NOT in pyproject's dependencies on purpose: this runs on a laptop
against a workbook that never leaves one, and adding it to the runtime would put a spreadsheet
reader in the Railway image for a script the server never calls. `pip install openpyxl` locally.
Wiring reads TENANT_ID and ACTOR_EMAIL from the environment and refuses to guess either.

The workbook (`Spring B 2026 books audited v12.xlsx`) already holds everything a rule needs:
the scope, the entities, the shares, the QuickBooks account, and, in the "Oct 14 meeting"
column, the reason in Spring's own words. This reads those sheets and writes them through
`alloc_rules.create_applied_rule`, which is the same path the drawer uses, so every refusal the
product would make is made here too.

WHAT IT DELIBERATELY DOES NOT LOAD, and why each one is a judgement rather than an oversight:

  * Rows with no rationale. `validate_version` refuses them, by design: the rationale is the
    column the Statement shows when a partner asks why a cost reached their entity. Loading a
    rule with an invented reason would put words in Spring's mouth on a partner's statement.

  * Items whose cost sits under a payee that another Item also uses. "Google Workspace" is one
    payee covering three domains with two different answers (the SP domain thirds across the
    brands, the SH and UP domains wholly SB Coaching). A vendor rule matches the payee, so it
    cannot tell them apart, and the one that happened to be written first would claim all
    three. These need a discriminator in QuickBooks before any rule can be right.

  * One-off travel, meals and gifts. A rule says "from now on, this vendor's cost splits this
    way". A single dinner is not that. The workbook settled those charge by charge, which is
    what the exception queue is for: they should arrive there and be decided, not be frozen
    into a standing rule because they happened once.

  * Advertising. Its payee in the books is the Amex card, not the campaign, so no vendor rule
    can see the brand mix. It is a tier 2 traced rule off Meta, which needs the campaign map
    (section 11 item 4) settled first.

STAFF ARE ACCOUNT-SCOPED, NOT VENDOR-SCOPED. Nine of the fifteen people share the single payee
"Payroll Pay", so a vendor rule would claim the whole payroll for whoever was written first.
Each person does have their own QuickBooks account, so that is what the rule matches on.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import os
import pathlib
import re
import sys
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import openpyxl  # noqa: E402

BRANDS = ["The Forum", "beCollective", "The Edge", "SB Coaching"]

# The workbook's brand names, against the `Business.name` they correspond to. SB Coaching is the
# pool entity: section 11 item 9 asks whether its display name changes, so both spellings map.
ALIASES = {
    "The Forum": ["The Forum"],
    "beCollective": ["beCollective", "be Collective"],
    "The Edge": ["The Edge"],
    "SB Coaching": ["SB Coaching", "Spring B", "Spring B Coaching"],
}

# P&L lines that are settled transaction by transaction, never by a standing rule.
ONE_OFF_LINES = {"Travel", "Meals and entertainment", "Member gifts and fulfillment"}
ONE_OFF_ACCOUNTS = {"64300 Meals and Entertainment", "Travel", "66500 Postage and Delivery",
                    "Member gifts and fulfillment", "Not yet identified"}


def norm_space(v) -> str:
    return " ".join(str(v or "").split())


def as_share(v):
    try:
        return Decimal(str(round(float(v), 6)))
    except (TypeError, ValueError):
        return Decimal(0)


# ---- reading the workbook ---------------------------------------------------------------

def headers(rows, r):
    return {norm_space(h): i for i, h in enumerate(rows[r - 1]) if h}


def read_items(wb, sheet):
    rows = list(wb[sheet].iter_rows(values_only=True))
    h = headers(rows, 5)
    out = []
    for r in rows[5:]:
        name = norm_space(r[0]) if r and r[0] else ""
        if not name or name.lower() == "month number":
            continue
        shares = {}
        for b in BRANDS:
            c = h.get(f"{b} %")
            shares[b] = as_share(r[c]) if c is not None and c < len(r) else Decimal(0)
        if sum(shares.values()) == 0:
            continue
        out.append({
            "item": name,
            "sheet": sheet,
            "shares": shares,
            "ytd": r[h["Year to date"]] if "Year to date" in h else None,
            "pl_line": norm_space(r[h["P&L line"]]) if "P&L line" in h else "",
            "account": norm_space(r[h["QuickBooks account"]]) if "QuickBooks account" in h else "",
            "rationale": norm_space(r[h["Oct 14 meeting"]]) if "Oct 14 meeting" in h else "",
        })
    return out


def read_staff(wb):
    rows = list(wb["32 Staff by Month"].iter_rows(values_only=True))
    people = collections.OrderedDict()
    for r in rows[5:]:
        person, line = norm_space(r[0]) if r[0] else "", norm_space(r[1]) if r[1] else ""
        if not person or not line:
            continue
        p = people.setdefault(person, {"item": person, "sheet": "32 Staff by Month",
                                       "lines": {}, "account": "", "rationale": ""})
        p["lines"][line] = [r[i] for i in range(2, 12)]
        if len(r) > 14 and r[14]:
            p["account"] = norm_space(r[14])
        if len(r) > 13 and r[13] and not p["rationale"]:
            p["rationale"] = norm_space(r[13])
    out = []
    for p in people.values():
        paid = [float(v or 0) for v in p["lines"].get("Paid", [])]
        shares, varies = {}, False
        for b in BRANDS:
            vals = p["lines"].get(f"{b} %") or []
            live = {round(float(v or 0), 6) for v, pay in zip(vals, paid) if pay}
            if len(live) > 1:
                varies = True
            shares[b] = as_share(next(iter(live), 0) if len(live) == 1 else 0)
        p.update({"shares": shares, "varies": varies, "ytd": round(sum(paid), 2),
                  "pl_line": "Staff and contractors"})
        out.append(p)
    return out


def payee_index(wb):
    """Item -> the real payees behind it, from Ledger Lines joined to All Transactions."""
    ll = list(wb["53 Ledger Lines"].iter_rows(values_only=True))
    lh = headers(ll, 1)
    at = list(wb["52 All Transactions"].iter_rows(values_only=True))
    ah = headers(at, 4)

    payee_of = {}
    for r in at[4:]:
        if len(r) > max(ah["Transaction key"], ah["Who"]) and r[ah["Transaction key"]]:
            payee_of[str(r[ah["Transaction key"]])] = norm_space(r[ah["Who"]])

    per_item = collections.defaultdict(collections.Counter)
    i_c, k_c = lh["Item (shared costs)"], lh["Transaction key"]
    for r in ll[1:]:
        if len(r) <= max(i_c, k_c) or not r[i_c]:
            continue
        p = payee_of.get(str(r[k_c]) if r[k_c] else None)
        if p:
            per_item[norm_space(r[i_c])][p] += 1
    return per_item


# ---- turning a row into a rule ----------------------------------------------------------

def classify(shares):
    """direct (one entity, not the pool) | retained (all on the pool) | split."""
    live = {b: v for b, v in shares.items() if v > 0}
    if len(live) == 1:
        only = next(iter(live))
        return ("retained", None) if only == "SB Coaching" else ("direct", only)
    return ("split", None)


def plan_row(row, payees, *, is_staff):
    """One row -> a rule spec, or a reason it is not loadable."""
    skip = []
    if not row["rationale"]:
        skip.append("no rationale in the workbook (the engine refuses a rule without one)")
    if not is_staff and (row["pl_line"] in ONE_OFF_LINES or row["account"] in ONE_OFF_ACCOUNTS):
        skip.append(f"settled item by item ({row['pl_line'] or row['account']}), "
                    f"belongs in the exception queue")
    if row["item"].lower().startswith("advertising"):
        skip.append("advertising is tier 2, traced off Meta, not a vendor rule")

    if is_staff:
        scope_kind, pattern = "account", row["account"]
        if not pattern:
            skip.append("no QuickBooks account on the staff row")
        if row.get("varies"):
            skip.append("the split moves month to month, so it needs a driver or a version "
                        "per change, not one fixed split")
    else:
        scope_kind = "vendor"
        cands = payees.get(row["item"], {})
        real = [p for p in cands if p and p != "(blank payee)"]
        if len(real) == 1:
            pattern = real[0]
        elif not real:
            pattern = row["item"]
            skip.append("no transaction carries a payee for this item, so nothing would match")
        else:
            pattern = max(real, key=lambda p: cands[p])
            skip.append(f"its cost sits under {len(real)} payees ({', '.join(real[:3])}), "
                        f"so one vendor rule cannot claim them all")

    method, only = classify(row["shares"])
    return {"item": row["item"], "sheet": row["sheet"], "scope_kind": scope_kind,
            "pattern": pattern, "method": method, "direct_to": only,
            "shares": row["shares"], "rationale": row["rationale"],
            "account": row["account"], "ytd": row["ytd"], "skip": skip}


def detect_shared_payees(plans):
    """A payee two rules would both claim is a collision, whichever was written first."""
    by_pat = collections.defaultdict(list)
    for p in plans:
        if p["scope_kind"] == "vendor":
            by_pat[p["pattern"].lower()].append(p)
    for pat, group in by_pat.items():
        if len(group) > 1:
            names = ", ".join(g["item"] for g in group)
            for g in group:
                g["skip"].append(f"the payee {g['pattern']!r} is shared with: {names}")


# ---- output -----------------------------------------------------------------------------

def show(plans):
    ok = [p for p in plans if not p["skip"]]
    bad = [p for p in plans if p["skip"]]
    by_method = collections.Counter(p["method"] for p in ok)

    print(f"\n{'=' * 94}\nLOADABLE: {len(ok)} rules "
          f"({', '.join(f'{v} {k}' for k, v in sorted(by_method.items()))})\n{'=' * 94}")
    for p in sorted(ok, key=lambda x: (x["sheet"], x["item"])):
        tgt = ("-> " + p["direct_to"]) if p["method"] == "direct" else (
            "stays put" if p["method"] == "retained" else
            " ".join(f"{b}={v}" for b, v in p["shares"].items() if v > 0))
        print(f"  [{p['method']:8}] {p['scope_kind']:7} {p['pattern'][:30]:30} {tgt}")
        print(f"             why: {p['rationale'][:78]}")

    print(f"\n{'=' * 94}\nNOT LOADED: {len(bad)}\n{'=' * 94}")
    groups = collections.Counter()
    for p in bad:
        groups[p["skip"][0].split("(")[0].strip()] += 1
    for reason, n in groups.most_common():
        print(f"  {n:>3}  {reason}")
    print()
    for p in sorted(bad, key=lambda x: x["item"])[:40]:
        print(f"  {p['item'][:34]:34} {p['skip'][0][:72]}")
    return ok


async def wire(ok, *, effective_from, reach, pool_name_default):
    from app.db import SessionLocal
    from app.models import Business, User
    from app.services import alloc_rules

    tenant = os.environ.get("TENANT_ID")
    actor_email = os.environ.get("ACTOR_EMAIL")
    if not tenant or not actor_email:
        sys.exit("Set TENANT_ID and ACTOR_EMAIL to wire. Refusing to guess which workspace.")

    from sqlalchemy import select
    async with SessionLocal() as s:
        biz = {b.name: b for b in (await s.execute(
            select(Business).where(Business.tenant_id == tenant))).scalars()}
        actor = (await s.execute(select(User).where(
            User.tenant_id == tenant, User.email == actor_email))).scalars().first()
        if actor is None:
            sys.exit(f"No user {actor_email!r} in that workspace.")

        def find(brand):
            for want in ALIASES[brand]:
                for name, b in biz.items():
                    if name.strip().lower() == want.lower():
                        return b
            return None

        missing = [b for b in BRANDS if find(b) is None]
        if missing:
            sys.exit(f"These workbook brands match no entity in the workspace: {missing}. "
                     f"Present: {sorted(biz)}")
        pool = find("SB Coaching")

        made, failed = 0, []
        for p in ok:
            kw = {"method": p["method"], "effective_from": effective_from, "reach": reach,
                  "rationale": p["rationale"]}
            if p["method"] == "direct":
                kw["targets"] = [{"business_id": str(find(p["direct_to"]).id), "share": "1"}]
            elif p["method"] == "split":
                kw["targets"] = [{"business_id": str(find(b).id), "share": str(v)}
                                 for b, v in p["shares"].items() if v > 0]
            try:
                await alloc_rules.create_applied_rule(
                    s, tenant, actor, scope_kind=p["scope_kind"], pattern=p["pattern"],
                    pool_name=p["item"][:80] or pool_name_default,
                    source_business_id=pool.id, **kw)
                made += 1
            except Exception as e:                       # the product's own refusal, surfaced
                failed.append((p["item"], str(e)))
        print(f"\ncreated {made} rules; {len(failed)} refused")
        for item, err in failed:
            print(f"  {item[:34]:34} {err[:80]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook")
    ap.add_argument("--wire", action="store_true", help="actually create the rules")
    ap.add_argument("--from", dest="month", default=None, help="effective month, YYYY-MM")
    ap.add_argument("--reach", default="forward", choices=("forward", "from_date", "retro"))
    a = ap.parse_args()

    wb = openpyxl.load_workbook(a.workbook, read_only=True, data_only=True)
    payees = payee_index(wb)
    plans = [plan_row(r, payees, is_staff=False)
             for r in read_items(wb, "33 Software") + read_items(wb, "34 Other Shared Costs")]
    plans += [plan_row(r, payees, is_staff=True) for r in read_staff(wb)]
    detect_shared_payees(plans)
    ok = show(plans)

    if not a.wire:
        print(f"\nPlan only. Re-run with --wire to create the {len(ok)} loadable rules.")
        return
    if not a.month:
        sys.exit("--wire needs --from YYYY-MM: a rule has to say which month it starts in.")
    import datetime as dt
    y, m = (int(x) for x in a.month.split("-"))
    asyncio.run(wire(ok, effective_from=dt.date(y, m, 1), reach=a.reach,
                     pool_name_default="Shared cost"))


if __name__ == "__main__":
    main()
