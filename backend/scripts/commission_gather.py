"""Gather one month's commission facts into a reviewable JSON, and list what needs a person.

    python scripts/commission_gather.py --period 2026-10

GATHER IS NOT RENDER. September taught that a commission statement is two different things
stacked: what the data says, and what somebody decided. Sandy Schoettle's $12,000 was in the
data turned out not to be that rep's sale; one instalment was in the tracker and not in the
payment processor. Both were settled by a person, and both would be settled the same wrong way
by any amount of re-running. So this writes the FACTS and the EXCEPTIONS to a file a person
edits, and the renderer turns that file into the PDF. The judgments live in the file, under
review, instead of being re-derived each month and quietly coming out different.

THE TWO COHORTS COME FROM DIFFERENT PLACES, and that is not an accident to be tidied away:

  First cohort   the sales tracker named in config.json, one column per month. That
                 sheet IS the record for cohort 1. The dashboard's Stripe feed carries only 21
                 of its 38 members, so it cannot audit the cohort and must not restate it.

  Aug-2026 cohort  the product: succeeded payments in `commission_payment`, net of refunds,
                 bucketed in the launch's own timezone.

If the tracker has no column for the period, this REFUSES. A statement missing cohort 1 would
have underpaid September by 70 percent and still looked like a complete document.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys

import openpyxl

OUTDIR = pathlib.Path(__file__).resolve().parents[1] / "commission_out"
CONFIG = OUTDIR / "config.json"

# WHO AND WHERE ARE CONFIGURATION, NOT CODE. A rep's name, their email and the path to a
# sheet of their pay are not facts about this product, and a repository is the wrong place
# to learn them. They live in commission_out/config.json, gitignored along with every
# statement built from it. It also means the script serves the other reps who have a rate
# rather than exactly one.
CONFIG_EXAMPLE = {
    "payee": "Firstname Lastname",
    "email": "rep@example.com",
    "rate_pct": 0.10,
    "tracker": "C:/path/to/Sales Tracker.xlsx",
    "tracker_sheet": "Payout",
}


def load_config() -> dict:
    if not CONFIG.exists():
        OUTDIR.mkdir(exist_ok=True)
        CONFIG.write_text(json.dumps(CONFIG_EXAMPLE, indent=2), encoding="utf-8")
        sys.exit(f"Wrote a template to {CONFIG}. Fill it in and run again.")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    missing = [k for k in CONFIG_EXAMPLE if k not in cfg]
    if missing:
        sys.exit(f"{CONFIG} is missing: {missing}")
    cfg["tracker"] = pathlib.Path(cfg["tracker"])
    if not cfg["tracker"].exists():
        sys.exit(f"Tracker not found: {cfg['tracker']}  (set in {CONFIG})")
    return cfg

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


def money(v) -> float:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0


def tracker_column(ws, period: str, tracker) -> tuple[str, str]:
    """The Payout sheet column holding that month's cash, or a refusal naming what is there.

    Matched on the month NAME in the header, not on a column letter: the sheet grows a column
    each month, so a letter pinned today is a silently wrong column in March.
    """
    want = MONTHS[int(period.split("-")[1]) - 1].lower()
    have = []
    for c in range(1, ws.max_column + 1):
        h = ws.cell(1, c).value
        if not h:
            continue
        have.append(str(h).strip())
        if want in str(h).strip().lower():
            return openpyxl.utils.get_column_letter(c), str(h).strip()
    sys.exit(
        f"The tracker has no column for {want.title()}.\n"
        f"  file:    {tracker}\n"
        f"  columns: {', '.join(have)}\n"
        f"Cohort 1 was 70 percent of September's commission, so a statement without it would be\n"
        f"wrong and would not look wrong. Add the month's column to the Payout sheet first.")


def cohort1(period: str, cfg: dict) -> tuple[list[dict], dict]:
    ws = openpyxl.load_workbook(cfg["tracker"], data_only=True)[cfg["tracker_sheet"]]
    col, header = tracker_column(ws, period, cfg["tracker"])

    rows, skipped = [], 0
    for r in range(2, ws.max_row + 1):
        first, last = ws[f"A{r}"].value, ws[f"B{r}"].value
        if not (first or last):
            continue
        cash = money(ws[f"{col}{r}"].value)
        name = " ".join(str(x).strip() for x in (first, last) if x)
        entry = {"member": name,
                 "email": (str(ws[f"C{r}"].value or "").strip().lower() or None),
                 "plan": str(ws[f"D{r}"].value or "").strip() or None,
                 "rep": str(ws[f"E{r}"].value or "").strip() or None,
                 "cash": cash,
                 "commission": round(cash * cfg["rate_pct"], 2)}
        if cash <= 0:
            skipped += 1
            continue
        rows.append(entry)
    meta = {"source": str(cfg["tracker"]), "sheet": cfg["tracker_sheet"],
            "column": col, "header": header,
            "rows_with_cash": len(rows), "rows_without_cash": skipped}
    return rows, meta


def cohort2(period: str, cfg: dict) -> tuple[list[dict], list[str]]:
    """Succeeded payments from the product. Empty, with a reason, when prod is unreachable."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    try:
        from scripts.commission_probe import TZ, database_url
        import psycopg
    except Exception as e:                                   # noqa: BLE001
        return [], [f"Could not load the database helpers ({e})."]

    try:
        url = database_url().replace("postgresql+psycopg://", "postgresql://")
    except SystemExit as e:
        return [], [f"No database URL: {e}"]
    except Exception as e:                                   # noqa: BLE001
        # FileNotFoundError when the Railway CLI is not on PATH, among others. Cohort 2 going
        # missing has to be a reported gap, never a traceback: on the 3rd of the month nobody
        # is watching, and a gather that dies says less than one that says what it could not
        # reach.
        return [], [f"Could not obtain the database URL ({type(e).__name__}: {e}). "
                    f"Set DATABASE_PUBLIC_URL, or run `railway link` first."]

    sql = (f"SELECT to_char(cp.paid_at AT TIME ZONE '{TZ}', 'YYYY-MM-DD') AS paid,"
           " cp.member_name, cp.amount, cp.statement_id IS NOT NULL AS billed,"
           " cp.opportunity_id,"
           " (SELECT sum(x.amount) FROM commission_payment x"
           "    WHERE x.contact_id = cp.contact_id) AS collected,"
           " mr.meta ->> 'stage' AS stage, mr.amount AS contract"
           " FROM commission_payment cp"
           " LEFT JOIN metric_record mr ON mr.external_id = cp.opportunity_id"
           "   AND mr.source = 'ghl' AND mr.kind = 'bc_launch_opp'"
           " WHERE cp.rep_email = %(rep)s"
           f" AND to_char(cp.paid_at AT TIME ZONE '{TZ}', 'YYYY-MM') = %(period)s"
           " ORDER BY paid, cp.member_name")
    try:
        with psycopg.connect(url, connect_timeout=30) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(sql, {"rep": cfg["email"], "period": period})
                got = cur.fetchall()
    except Exception as e:                                   # noqa: BLE001
        return [], [f"Could not read the product database ({type(e).__name__}). "
                    f"Cohort 2 is missing from this gather."]

    out = []
    for paid, member, amount, billed, opp, collected, stage, contract in got:
        out.append({"paid": paid, "member": member, "cash": money(amount),
                    "already_billed": bool(billed),
                    "opportunity_id": opp,
                    "collected_to_date": money(collected),
                    "stage": stage,
                    "contract": money(contract) or None,
                    # What the member still owes on the deal. Only when the contract is known:
                    # a remaining balance computed against a missing contract would read as
                    # the whole collected amount still being due.
                    "remaining": (round(money(contract) - money(collected), 2)
                                  if contract else None),
                    "commission": None if billed else
                    round(money(amount) * cfg["rate_pct"], 2)})
    missing = [r["member"] for r in out if not r.get("stage")]
    notes = ([f"No stage for: {missing}"] if missing else []) + [
        "Contract value and remaining balance are not shown: the sync does not store "
        "monetaryValue on the opportunity snapshot, and the membership snapshot carries "
        "0.00. They are not guessed from the ticket price, which does not match what the "
        "signed statement shows for a monthly plan."]
    return out, notes


def exceptions(c1: list[dict], c2: list[dict], cfg: dict,
               partial: bool = False) -> list[str]:
    """What a person has to look at. Never fixed silently, never guessed."""
    out = []
    payee = cfg["payee"]
    first = payee.split()[0].lower()
    # The tracker abbreviates a rep to a first name and initial, so match on the first name
    # the full one, and report anything that does not look like this rep at all.
    wrong_rep = {r["member"] for r in c1
                 if r["rep"] and not r["rep"].lower().startswith(first)}
    if wrong_rep:
        out.append(f"Tracker rows credited to someone other than {payee}: "
                   f"{sorted(wrong_rep)}")
    if not c1 and not partial:
        out.append("No cohort-1 rows carried cash this period.")
    if not c2:
        out.append("No cohort-2 payments found for this period.")
    billed = [r["member"] for r in c2 if r["already_billed"]]
    if billed:
        out.append(f"Already on an earlier statement, so carrying no commission here: {billed}")
    dupes = {r["member"] for r in c1} & {r["member"] for r in c2}
    if dupes:
        out.append(f"IN BOTH COHORTS - would be paid twice unless resolved: {sorted(dupes)}")
    if not partial:
        out.append("Confirm each cohort-1 instalment actually settled: the tracker is kept by "
                   "hand and a plan being active is not a payment succeeding.")
    if partial:
        out.append("PARTIAL STATEMENT: the first cohort is excluded from this run. That "
                   "cohort was the majority of September's commission and is still kept "
                   "by hand, so a second payment is owed for it. The document must say "
                   "so, and the rep must be told.")
    out.append("Confirm every cohort-2 sale is really this rep's before paying "
               "(Sandy Schoettle, September).")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True, help="YYYY-MM")
    ap.add_argument("--out", default=None)
    ap.add_argument("--cohort2-only", action="store_true",
                    help="settle only the cohort the product tracks, and SAY SO on the "
                         "statement. For while the older cohort is still kept by hand.")
    a = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}", a.period):
        sys.exit("--period wants YYYY-MM")

    cfg = load_config()
    if a.cohort2_only:
        c1, c1meta = [], {"skipped": "excluded by --cohort2-only"}
    else:
        c1, c1meta = cohort1(a.period, cfg)
    c2, c2notes = cohort2(a.period, cfg)

    c1_cash = round(sum(r["cash"] for r in c1), 2)
    c1_due = round(sum(r["commission"] for r in c1), 2)
    c2_billable = round(sum(r["cash"] for r in c2 if not r["already_billed"]), 2)
    c2_prior = round(sum(r["cash"] for r in c2 if r["already_billed"]), 2)
    c2_due = round(sum(r["commission"] or 0 for r in c2), 2)

    doc = {
        "period": a.period,
        "payee": {"name": cfg["payee"], "email": cfg["email"]},
        "rate_pct": cfg["rate_pct"],
        "gathered_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "partial": bool(a.cohort2_only),
        "cohort1": {"meta": c1meta, "rows": c1, "cash": c1_cash, "commission": c1_due},
        "cohort2": {"rows": c2, "billable_cash": c2_billable, "prior_cash": c2_prior,
                    "commission": c2_due, "notes": c2notes},
        "totals": {"cash_collected": round(c1_cash + c2_billable + c2_prior, 2),
                   "billable_cash": round(c1_cash + c2_billable, 2),
                   "commission_due": round(c1_due + c2_due, 2)},
        "exceptions": exceptions(c1, c2, cfg, partial=a.cohort2_only),
        "notes_for_the_statement": [
            "Commission is paid on cash collected only. Remaining balances earn commission as "
            "they are collected in future periods.",
        ],
    }

    OUTDIR.mkdir(exist_ok=True)
    slug = cfg["payee"].split()[0].lower()
    out = pathlib.Path(a.out) if a.out else OUTDIR / f"commission-{a.period}-{slug}.json"
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    if a.cohort2_only:
        print(f"period {a.period}   FIRST COHORT EXCLUDED (--cohort2-only)")
    else:
        print(f"period {a.period}   tracker column {c1meta['column']} "
              f"({c1meta['header']!r})")
    print(f"  cohort 1  {len(c1):>3} members  ${c1_cash:>10,.2f}  -> ${c1_due:,.2f}")
    print(f"  cohort 2  {len(c2):>3} payments ${c2_billable:>10,.2f}  -> ${c2_due:,.2f}"
          f"   (+${c2_prior:,.2f} already billed)")
    print(f"  TOTAL DUE                        ${doc['totals']['commission_due']:,.2f}")
    print(f"\nwrote {out}")
    print("\nNEEDS A PERSON:")
    for e in doc["exceptions"]:
        print(f"  - {e}")
    for n in c2notes:
        print(f"  ! {n}")


if __name__ == "__main__":
    main()
