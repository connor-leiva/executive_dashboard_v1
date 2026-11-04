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

THE FOUR WHOSE SPLIT MOVES become a tier 3 driven rule against one `staff_time` driver, each
naming its own person as the driver subject, which is what `driver_subject` is for. A fixed
percentage would have been the easy load and the wrong one: it would state that Sarah
Ostermiller was 55/15 in a month the workbook has her at 80/40, and nothing downstream would
flag it. `--with-history` additionally commits the workbook's January to October figures, which
drive nothing when the rules start in November but give the Drivers tab a month to prefill the
next one from. It is off by default, because D8 says a driver month is committed by a person
and ten months appearing at once is not that.
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


DRIVER_KEY = "staff_time"
DRIVER_LABEL = "Staff time commitment"

# A reason that is still a question rather than an answer. Loading one of these as `retained`
# is the trap: `exceptions()` counts an account or vendor covered by ANY tier as covered,
# `retained` included, on the grounds that "somebody has already decided that cost stays put".
# Nobody has. The cost would stop arriving in the one queue built to surface it, and the open
# question would survive only as prose on a rule that looks settled.
OPEN_QUESTION = re.compile(
    r"find out|check wh|nobody knows|probably|make sure|audit the|\?|is checking"
    r"|needs your input", re.I)


def plan_driven(staff):
    """The people whose split moves: one driven rule each, against the shared staff driver."""
    out = []
    for p in staff:
        if not p.get("varies"):
            continue
        paid = [float(v or 0) for v in p["lines"].get("Paid", [])]
        months, fixed = [], []
        for m in range(10):
            if not (m < len(paid) and paid[m]):
                continue
            rows = {}
            for b in BRANDS:
                vals = p["lines"].get(f"{b} %") or []
                v = as_share(vals[m]) if m < len(vals) else Decimal(0)
                if v > 0:
                    rows[b] = v
            if rows:
                # The workbook derives some of these (payroll taxes are worked out FROM the
                # other people's splits), so a month can land on 1.000001. `validate_version`
                # refuses a rule split that misses 1, but `validate_values` does NOT check a
                # driver month's sum: it allows a `remainder` row, so there is nothing to
                # compare against. The residue would be stored and that month would allocate
                # 100.0001 percent of the cost, which no total on any page would contradict.
                # Settled here, on the largest share, which is D5's rounding rule.
                total = sum(rows.values())
                if total != Decimal(1):
                    big = max(rows, key=lambda b: rows[b])
                    rows[big] = rows[big] + (Decimal(1) - total)
                    fixed.append((m + 1, big, total))
                months.append((m + 1, rows))
        skip = []
        if not p["account"]:
            skip.append("no QuickBooks account on the staff row")
        if not p["rationale"]:
            skip.append("no rationale in the workbook")
        out.append({"item": p["item"], "sheet": p["sheet"], "scope_kind": "account",
                    "pattern": p["account"], "method": "driven", "months": months,
                    "rationale": p["rationale"], "ytd": p["ytd"], "skip": skip,
                    "rounded": fixed})
    return out


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

def show(plans, driven=()):
    ok = [p for p in plans if not p["skip"]]
    bad = [p for p in plans if p["skip"]]
    by_method = collections.Counter(p["method"] for p in ok)

    if driven:
        good = [d for d in driven if not d["skip"]]
        print(f"\n{'=' * 94}\nDRIVEN, against the {DRIVER_KEY!r} driver: {len(good)} rules"
              f"\n{'=' * 94}")
        for d in driven:
            mark = "  " if not d["skip"] else "! "
            print(f"{mark}[driven  ] account {d['pattern'][:34]:34} "
                  f"{len(d['months'])} months in the workbook")
            for mn, rows in d["months"]:
                share = " ".join(f"{b}={v}" for b, v in rows.items())
                print(f"             month {mn:>2}: {share}")
            for mn, big, was in d.get("rounded", []):
                # The ADJUSTMENT, not the excess. These are the same magnitude and opposite
                # signs, and printing the excess read as though the largest share went UP when
                # the code had just taken that much off it. A line whose only job is to say
                # what changed is worse than no line when it says the opposite.
                print(f"             month {mn:>2} summed to {was}, not 1: "
                      f"{Decimal(1) - was:+} on {big}, the largest share (D5)")
            if d["skip"]:
                print(f"             NOT LOADED: {d['skip'][0]}")

    print(f"\n{'=' * 94}\nLOADABLE: {len(ok)} rules "
          f"({', '.join(f'{v} {k}' for k, v in sorted(by_method.items()))})\n{'=' * 94}")
    for p in sorted(ok, key=lambda x: (x["sheet"], x["item"])):
        tgt = ("-> " + p["direct_to"]) if p["method"] == "direct" else (
            "stays put" if p["method"] == "retained" else
            " ".join(f"{b}={v}" for b, v in p["shares"].items() if v > 0))
        print(f"  [{p['method']:8}] {p['scope_kind']:7} {p['pattern'][:30]:30} {tgt}")
        print(f"             why: {p['rationale'][:78]}")

    openq = [p for p in ok if OPEN_QUESTION.search(p["rationale"] or "")]
    if openq:
        print(f"\n{'=' * 94}\nSTILL A QUESTION, NOT AN ANSWER: {len(openq)}\n{'=' * 94}")
        print("  Loading these settles them. A rule of any tier, `retained` included, takes "
              "its\n  vendor OUT of the exception queue, so these stop arriving on the one "
              "screen built\n  to ask about them. Pass --skip-items to hold any of them back.\n")
        for p in sorted(openq, key=lambda x: x["item"]):
            print(f"  [{p['method']:8}] {p['item'][:26]:26} {p['rationale'][:54]}")

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


async def wire(ok, driven=(), *, effective_from, reach, pool_name_default, with_history=False):
    # CONFIG BEFORE IMPORTS. These checks need nothing but the environment, and importing the
    # app first meant a missing dependency buried the answer to "which database is this
    # pointing at" under a traceback. Cheap questions get asked first.
    #
    # SAY WHICH DATABASE THIS IS, ALWAYS. The failure that costs an afternoon is not a crash,
    # it is writing 46 rules into the local development database and reading the empty
    # Allocations page in production wondering why. `DATABASE_URL` unset falls back to local
    # SQLite silently, so the fallback is refused outright rather than reported.
    url = os.environ.get("DATABASE_URL") or ""
    shown = re.sub(r"//[^@]*@", "//", url) or "(DATABASE_URL is not set)"
    print(f"database: {shown}")
    if not url or url.startswith("sqlite"):
        sys.exit("That is the local development database, which is several migrations behind "
                 "and is not where your books live. Set DATABASE_URL to the production "
                 "database before wiring.")

    try:
        from sqlalchemy import select

        from app.db import SessionLocal
        from app.models import AllocationDriver, Business, Tenant, User
        from app.services import alloc_drivers, alloc_rules
    except ImportError as e:
        # Almost always the system Python rather than the project's: `python` on PATH has none
        # of the app's dependencies. The traceback names `jose` or `fastapi` and reads like a
        # broken install, so say the actual cause.
        venv = pathlib.Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe"
        sys.exit(f"Could not import the app ({e}).\n"
                 f"This usually means it ran on the system Python instead of the project's. "
                 f"Use:\n  {venv}\ninstead of `python`.")

    tenant = os.environ.get("TENANT_ID")
    actor_email = os.environ.get("ACTOR_EMAIL")
    if not tenant or not actor_email:
        # Rather than send somebody hunting for a UUID, show the ones that are there.
        async with SessionLocal() as s:
            rows = list((await s.execute(select(Tenant))).scalars())
            print("\nWorkspaces in that database:")
            for t in rows:
                print(f"  TENANT_ID={t.id}   {getattr(t, 'slug', '') or ''} "
                      f"{getattr(t, 'name', '') or ''}")
            if tenant:
                users = list((await s.execute(select(User).where(
                    User.tenant_id == tenant))).scalars())
                print("\nUsers in that workspace (pick yours for ACTOR_EMAIL):")
                for u in users[:20]:
                    print(f"  ACTOR_EMAIL={u.email}")
        sys.exit("\nSet TENANT_ID and ACTOR_EMAIL from the list above, then run it again.")
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

        if not driven:
            return

        # One driver for every person whose split moves, reused if it is already there: the key
        # is unique per tenant, so a second run must find it rather than fail on the duplicate.
        drv = (await s.execute(select(AllocationDriver).where(
            AllocationDriver.tenant_id == tenant,
            AllocationDriver.key == DRIVER_KEY))).scalars().first()
        if drv is None:
            drv = await alloc_drivers.create_driver(
                s, tenant, actor, key=DRIVER_KEY, label=DRIVER_LABEL, kind="split",
                unit="percent")
            print(f"created driver {drv.key!r}")
        else:
            print(f"reusing driver {drv.key!r}")

        dmade, dfailed = 0, []
        for d in driven:
            try:
                await alloc_rules.create_applied_rule(
                    s, tenant, actor, scope_kind="account", pattern=d["pattern"],
                    pool_name=d["item"][:80], source_business_id=pool.id,
                    method="driven", driver_id=drv.id, driver_subject=d["item"],
                    effective_from=effective_from, reach=reach, rationale=d["rationale"])
                dmade += 1
            except Exception as e:
                dfailed.append((d["item"], str(e)))
        print(f"created {dmade} driven rules; {len(dfailed)} refused")
        for item, err in dfailed:
            print(f"  {item[:34]:34} {err[:80]}")

        if not with_history:
            print("\nNo driver months committed. The rules are live from "
                  f"{effective_from:%B %Y}, so commit that month on the Drivers tab. "
                  "Re-run with --with-history to load the workbook's Jan-Oct figures first.")
            return

        # Each month is one commit carrying every person's row, because a commit is a month of
        # the DRIVER, not of a person.
        import datetime as dt
        by_month: dict = collections.defaultdict(list)
        for d in driven:
            for mn, rows in d["months"]:
                for brand, share in rows.items():
                    by_month[mn].append({"subject": d["item"],
                                         "business_id": str(find(brand).id),
                                         "value_kind": "share", "value": str(share)})
        for mn in sorted(by_month):
            try:
                await alloc_drivers.commit_month(
                    s, tenant, actor, drv.id, period=dt.date(effective_from.year, mn, 1),
                    rows=by_month[mn],
                    note="From the audited books workbook, decided in the Oct 14 meeting.")
                print(f"  committed month {mn:>2}: {len(by_month[mn])} values")
            except Exception as e:
                print(f"  month {mn:>2} refused: {str(e)[:70]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook")
    ap.add_argument("--wire", action="store_true", help="actually create the rules")
    ap.add_argument("--from", dest="month", default=None, help="effective month, YYYY-MM")
    ap.add_argument("--reach", default="forward", choices=("forward", "from_date", "retro"))
    ap.add_argument("--skip-items", default="",
                    help="comma-separated Item names to hold back, so they keep arriving in "
                         "the exception queue instead of being settled by a rule")
    ap.add_argument("--with-history", action="store_true",
                    help="also commit the workbook's Jan-Oct driver months, so the Drivers tab "
                         "has a month to prefill the next one from")
    a = ap.parse_args()

    wb = openpyxl.load_workbook(a.workbook, read_only=True, data_only=True)
    payees = payee_index(wb)
    staff = read_staff(wb)
    plans = [plan_row(r, payees, is_staff=False)
             for r in read_items(wb, "33 Software") + read_items(wb, "34 Other Shared Costs")]
    # The people whose split holds still become fixed rules; the four that move become driven.
    plans += [plan_row(r, payees, is_staff=True) for r in staff if not r.get("varies")]
    driven = plan_driven(staff)
    detect_shared_payees(plans)

    held = {x.strip().lower() for x in a.skip_items.split(",") if x.strip()}
    if held:
        found = {p["item"].lower() for p in plans + driven}
        unknown = sorted(held - found)
        if unknown:
            sys.exit(f"--skip-items names nothing in the workbook: {unknown}. "
                     f"A typo here silently loads the rule you meant to hold back.")
        for p in plans + driven:
            if p["item"].lower() in held:
                p["skip"].append("held back by --skip-items, so it keeps arriving in the queue")

    ok = show(plans, driven)

    if not a.wire:
        print(f"\nPlan only. Re-run with --wire to create them.")
        return
    if not a.month:
        sys.exit("--wire needs --from YYYY-MM: a rule has to say which month it starts in.")
    import datetime as dt
    y, m = (int(x) for x in a.month.split("-"))
    asyncio.run(wire(ok, [d for d in driven if not d["skip"]],
                     effective_from=dt.date(y, m, 1), reach=a.reach,
                     pool_name_default="Shared cost", with_history=a.with_history))


if __name__ == "__main__":
    main()
