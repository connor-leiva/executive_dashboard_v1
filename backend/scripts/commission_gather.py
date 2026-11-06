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


def rep_from_db(email: str) -> dict:
    """A rep's display name and rate from the product. Refuses rather than assume a rate.

    The rate is deliberately NOT a command-line flag. A statement is what somebody gets paid;
    a mistyped rate is a mispayment that looks exactly like a correct one, and `sales_rep`
    already carries the answer. A rep with a NULL rate is refused for the same reason the probe
    reports NULL rather than 0: no rate set is not a 0% rate.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from scripts.commission_probe import database_url
    import psycopg

    url = database_url().replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url, connect_timeout=30) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute("SELECT display_name, commission_pct, is_active FROM sales_rep"
                        " WHERE lower(email) = %(e)s", {"e": email.strip().lower()})
            row = cur.fetchone()
            if row is None:
                cur.execute("SELECT email FROM sales_rep WHERE commission_pct IS NOT NULL"
                            " ORDER BY email")
                known = [r[0] for r in cur.fetchall()]
                sys.exit(f"No sales rep with email {email!r}.\n"
                         f"  reps with a rate: {', '.join(known) or '(none)'}")
    name, pct, active = row
    if pct is None:
        sys.exit(f"{name} ({email}) has no commission rate in the product. A missing rate is "
                 f"not a 0% rate -- set it before building a statement.")
    if not active:
        print(f"NOTE: {name} is marked inactive in the product.", flush=True)
    return {"payee": name, "email": email.strip().lower(), "rate_pct": float(pct) / 100.0}


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


def rep_matches(sheet_rep: str | None, payee: str) -> bool:
    """Does the tracker's "Rep Name" mean this payee?

    The sheet writes "Aimee S" where the product says "Aimee Stephens", so this compares the
    first name plus the initial of the surname. Deliberately not a substring test: "Alli" would
    match both "Allison Hare" and an "Alli Smith", and the cost of a loose match here is paying
    one rep for another's sale.
    """
    if not sheet_rep:
        return False
    def key(s):
        parts = [p for p in str(s).replace(".", " ").split() if p]
        if not parts:
            return None
        return (parts[0].lower(), parts[-1][0].lower() if len(parts) > 1 else "")
    a, b = key(sheet_rep), key(payee)
    return a is not None and a == b


def cohort1_cash_from_stripe(period: str, emails: list[str]) -> tuple[dict, list[str]]:
    """Each cohort-1 member's succeeded beCollective cash for the period, keyed by email.

    kind='payment' ONLY. The dropped_offroster diagnostic also holds this money today, but it is
    temporary and reading it would make the statement depend on a debugging artefact. The
    consequence is real: until the roster override has been through a sync, charges it rescued
    are still filed as dropped and this undercounts. The caller says so on the statement.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from scripts.commission_probe import database_url
    import psycopg

    url = database_url().replace("postgresql+psycopg://", "postgresql://")
    want = [e for e in emails if e]
    with psycopg.connect(url, connect_timeout=30) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                # Net of refunds, for the same reason as the gap query: a refunded
                # instalment is not cash the rep earned on.
                "SELECT lower(email), coalesce(sum(amount"
                "   - coalesce((meta ->> 'amount_refunded')::numeric, 0)), 0)"
                " FROM metric_record"
                " WHERE source = 'stripe_bc' AND kind = 'payment' AND status = 'succeeded'"
                " AND lower(email) = ANY(%(em)s)"
                " AND to_char(occurred_on, 'YYYY-MM') = %(p)s GROUP BY 1",
                {"em": want, "p": period})
            cash = {e: float(v) for e, v in cur.fetchall()}
            cur.execute(
                "SELECT count(*) FROM metric_record WHERE source = 'stripe_bc'"
                " AND kind = 'dropped_offroster' AND status = 'succeeded'"
                " AND lower(email) = ANY(%(em)s)"
                " AND to_char(occurred_on, 'YYYY-MM') = %(p)s",
                {"em": want, "p": period})
            still_dropped = cur.fetchone()[0]
    notes = []
    if still_dropped:
        notes.append(
            f"{still_dropped} cohort-1 charge(s) this period are still filed as dropped by the "
            f"roster filter, so this UNDERCOUNTS. The override is configured but has not been "
            f"through a sync yet. Re-gather after the next beCollective sync.")
    return cash, notes


def cohort1(period: str, cfg: dict, from_stripe: bool = False) -> tuple[list[dict], dict]:
    ws = openpyxl.load_workbook(cfg["tracker"], data_only=True)[cfg["tracker_sheet"]]
    col = header = None
    if not from_stripe:
        col, header = tracker_column(ws, period, cfg["tracker"])

    members, other_reps, unknown_reps = [], {}, {}
    for r in range(2, ws.max_row + 1):
        first, last = ws[f"A{r}"].value, ws[f"B{r}"].value
        if not (first or last):
            continue
        sheet_rep = str(ws[f"E{r}"].value or "").strip() or None
        name = " ".join(str(x).strip() for x in (first, last) if x)
        cash = money(ws[f"{col}{r}"].value) if col else 0.0
        if not rep_matches(sheet_rep, cfg["payee"]):
            # Not this payee's member. Record it so the omission is visible: a silently
            # filtered row is how one rep's sale ends up on nobody's statement.
            bucket = other_reps if sheet_rep else unknown_reps
            key_name = sheet_rep or "(no rep on the row)"
            slot = bucket.setdefault(key_name, {"rows": 0, "cash": 0.0})
            slot["rows"] += 1
            slot["cash"] = round(slot["cash"] + cash, 2)
            continue
        members.append({"member": name,
                        "email": (str(ws[f"C{r}"].value or "").strip().lower() or None),
                        "plan": str(ws[f"D{r}"].value or "").strip() or None,
                        "rep": sheet_rep,
                        "cash": cash})

    stripe_notes = []
    if from_stripe:
        got, stripe_notes = cohort1_cash_from_stripe(
            period, [m["email"] for m in members])
        for m in members:
            m["cash"] = round(got.get(m["email"] or "", 0.0), 2)

    rows, skipped = [], 0
    for m in members:
        if m["cash"] <= 0:
            skipped += 1
            continue
        m["commission"] = round(m["cash"] * cfg["rate_pct"], 2)
        rows.append(m)

    meta = {"source": ("beCollective Stripe (cash) + tracker (roster)" if from_stripe
                       else str(cfg["tracker"])),
            "sheet": cfg["tracker_sheet"],
            "column": col, "header": header,
            "cash_from": "stripe" if from_stripe else "tracker",
            "members_for_this_rep": len(members),
            "rows_with_cash": len(rows), "rows_without_cash": skipped,
            "excluded_other_reps": other_reps,
            "excluded_rows_with_no_rep": unknown_reps,
            "notes": stripe_notes}
    return rows, meta


def _usd(v) -> str:
    return f"${v:,.2f}"


# Finding the member's Stripe address: the registration row first, then the member row,
# either by contact id or by name. On the registration email alone, five members who
# reconcile to the dollar read as having no Stripe history at all -- a false gap that looks
# exactly like a real one.
_EMAIL_LOOKUP = (
    "SELECT lower(r.email) FROM metric_record r WHERE r.source = 'ghl'"
    " AND r.kind IN ('bc_registration', 'bc_member') AND r.email IS NOT NULL"
    " AND (r.meta ->> 'contact_id' = opp.meta ->> 'contact_id'"
    "      OR lower(r.name) = lower(opp.name)) LIMIT 1")


def allocate_unrecorded(payments, commission_ever):
    """Which dollars of a member's Stripe history the commission table never recorded.

    `payments` is [(paid 'YYYY-MM-DD', amount)] oldest first; `commission_ever` is everything
    the commission table holds for that member. Returns [(month, paid, amount)] for the
    uncovered part, oldest first.

    Covers the oldest payments first: the earliest cash is the most likely to have been
    invoiced already, and the rule must give the same answer on every run without remembering
    the last one. The consequence that matters is that the months PARTITION the shortfall --
    gathering August, September and October separately cannot total more than is owed, which a
    per-run cap could not promise because no run can see the others.
    """
    cover = round(float(commission_ever or 0), 2)
    out = []
    for paid, amount in payments:
        amount = round(float(amount), 2)
        if cover >= amount:
            cover = round(cover - amount, 2)
            continue                             # already accounted for
        free = round(amount - cover, 2)          # a single payment can be part covered
        cover = 0.0
        if free > 0:
            out.append((paid[:7], paid, free))
    return out


def cohort2_gaps(period: str, cfg: dict) -> tuple[list[dict], list[str]]:
    """Cash this rep's won members paid that the commission table never recorded.

    Returns rows shaped like cohort2's, each marked source='stripe'. See the module note: the
    shortfall is CUMULATIVE, a member whose email carries more than one payer is refused, and
    every added payment is capped at what is still unrecorded.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    try:
        from scripts.commission_probe import TZ, database_url
        import psycopg
    except Exception as e:                                   # noqa: BLE001
        return [], [f"Could not load the database helpers for the Stripe gap check ({e})."]
    try:
        url = database_url().replace("postgresql+psycopg://", "postgresql://")
    except Exception as e:                                   # noqa: BLE001
        return [], [f"Could not check for unrecorded Stripe cash ({type(e).__name__}). "
                    f"If a member of this rep's pays outside GHL, this statement is SHORT."]

    members_sql = (
        "SELECT * FROM ("
        " SELECT opp.name AS member, opp.external_id AS opportunity_id,"
        "  opp.meta ->> 'stage' AS stage, opp.amount AS contract,"
        "  coalesce((SELECT lower(sc.rep_email) FROM sales_call sc"
        "    WHERE sc.contact_id = opp.meta ->> 'contact_id' AND sc.rep_email IS NOT NULL"
        "    ORDER BY sc.is_current DESC, sc.call_time_utc DESC LIMIT 1), '') AS rep,"
        f"  ({_EMAIL_LOOKUP}) AS email,"
        # NET of refunds. commission_payment stores `amount - amountRefunded`, so summing
        # gross here invented a shortfall out of a refund and paid commission on returned
        # money. The refund lives in meta.amount_refunded (see sync.py and billing.py).
        "  coalesce((SELECT sum(sp.amount"
        "      - coalesce((sp.meta ->> 'amount_refunded')::numeric, 0))"
        "    FROM metric_record sp"
        "    WHERE sp.source = 'stripe_bc' AND sp.kind = 'payment'"
        "    AND sp.status = 'succeeded'"
        f"    AND lower(sp.email) = ({_EMAIL_LOOKUP})), 0) AS stripe_ever,"
        "  (SELECT string_agg(DISTINCT sp2.name, ' | ') FROM metric_record sp2"
        "    WHERE sp2.source = 'stripe_bc' AND sp2.kind = 'payment'"
        "    AND sp2.status = 'succeeded'"
        f"    AND lower(sp2.email) = ({_EMAIL_LOOKUP})) AS payers,"
        "  coalesce((SELECT sum(c.amount) FROM commission_payment c"
        "    WHERE c.contact_id = opp.meta ->> 'contact_id'), 0) AS commission_ever"
        " FROM metric_record opp"
        " WHERE opp.source = 'ghl' AND opp.kind = 'bc_launch_opp' AND opp.status = 'won'"
        "  AND opp.meta ->> 'contact_id' IS NOT NULL) q"
        " WHERE q.rep = lower(%(rep)s) AND q.stripe_ever > q.commission_ever"
        " ORDER BY q.member")

    # What the query above cannot see, counted so it is stated rather than assumed away: an
    # opportunity that is not yet 'won' but is already collecting, or one that has lost its
    # contact id, is outside the gap check entirely.
    blind_sql = (
        "SELECT count(*) FILTER (WHERE status <> 'won') AS not_won,"
        " count(*) FILTER (WHERE meta ->> 'contact_id' IS NULL) AS no_contact"
        " FROM metric_record WHERE source = 'ghl' AND kind = 'bc_launch_opp'")

    # EVERY payment, not just this period's. FIFO cannot decide what is uncovered in October
    # without knowing what August and September collected. occurred_on then amount then name
    # so the order is total and the same on every run: two charges on one day must not swap
    # places between gathers, or which of them is "covered" changes with the wind.
    pays_sql = (
        "SELECT lower(email) AS email,"
        " to_char(occurred_on, 'YYYY-MM-DD') AS paid,"
        " (amount - coalesce((meta ->> 'amount_refunded')::numeric, 0)) AS net, name"
        " FROM metric_record WHERE source = 'stripe_bc' AND kind = 'payment'"
        " AND status = 'succeeded' AND lower(email) = ANY(%(em)s)"
        " AND (amount - coalesce((meta ->> 'amount_refunded')::numeric, 0)) > 0"
        " ORDER BY occurred_on, 3, name")

    try:
        with psycopg.connect(url, connect_timeout=30) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(members_sql, {"rep": cfg["email"]})
                gapped = cur.fetchall()
                cur.execute(blind_sql)
                not_won, no_contact = cur.fetchone()
                emails = [g[5] for g in gapped if g[5]]
                pays = []
                if emails:
                    cur.execute(pays_sql, {"em": emails})
                    pays = cur.fetchall()
    except Exception as e:                                   # noqa: BLE001
        return [], [f"Could not check for unrecorded Stripe cash ({type(e).__name__}). "
                    f"If a member of this rep's pays outside GHL, this statement is SHORT."]

    by_email = {}
    for email, paid, amount, name in pays:
        by_email.setdefault(email, []).append((paid, money(amount), name))

    # ONE entry per member, not per opportunity. members_sql returns a row per won
    # bc_launch_opp; a member with two of them (a re-created or upgraded opp, or two duplicate
    # GHL contacts on one email) would otherwise have the same uncovered payments appended
    # once per row and their whole gap paid twice on one statement. Keyed on the email the
    # payments are matched by, since that is what decides which payments get claimed; the
    # product's own attribution dedupes at exactly this point.
    seen_key, collapsed, merged = set(), [], {}
    for g in gapped:
        key = (g[5] or "").strip().lower() or f"opp:{g[1]}"
        if key in seen_key:
            merged.setdefault(g[0], []).append(g[1])
            continue
        seen_key.add(key)
        collapsed.append(g)
    gapped = collapsed

    rows, notes = [], []
    for member, opps in merged.items():
        notes.append(
            f"{member} has more than one won opportunity in the CRM ({len(opps) + 1} of them). "
            f"Their collected cash is counted ONCE here, against the first. Tidy the duplicate "
            f"opportunities in GHL -- an extra one is also an extra contract value in every "
            f"other report.")
    if not_won or no_contact:
        notes.append(
            f"The unrecorded-cash check only looks at won opportunities carrying a contact id. "
            f"Outside it: {not_won} opportunit(ies) not marked won and {no_contact} with no "
            f"contact id. A member already paying before their opportunity is marked won would "
            f"not be found by it.")
    for member, opp_id, stage, contract, _rep, email, stripe_ever, payers, commission_ever             in gapped:
        shortfall = round(money(stripe_ever) - money(commission_ever), 2)
        if shortfall <= 0:                       # cannot happen given the WHERE; belt and braces
            continue
        if not email:
            notes.append(f"{member} has cash the commission table has not recorded, but no "
                         f"email to match it on. NOT included -- check by hand.")
            continue
        who = [p.strip() for p in (payers or "").split("|") if p.strip()]
        if len(who) > 1:
            notes.append(f"{member}: {email} carries charges under more than one payer "
                         f"({', '.join(who)}), so whose money it is cannot be told from the "
                         f"feed. NOT included -- resolve the address before paying on it.")
            continue
        uncovered = allocate_unrecorded(
            [(paid, amount) for paid, amount, _name in by_email.get(email, [])],
            money(commission_ever))

        by_month = {}
        for m, _paid, amt in uncovered:
            by_month[m] = round(by_month.get(m, 0.0) + amt, 2)

        for m, paid, amount in uncovered:
            if m != period:
                continue
            rows.append({
                "paid": paid, "member": member, "cash": amount,
                "already_billed": False, "opportunity_id": opp_id,
                "collected_to_date": money(stripe_ever), "stage": stage,
                "contract": money(contract) or None,
                "remaining": (round(money(contract) - money(stripe_ever), 2)
                              if contract else None),
                "commission": round(amount * cfg["rate_pct"], 2),
                "source": "stripe",
            })

        added = by_month.get(period, 0.0)
        allocated = round(sum(by_month.values()), 2)
        spread = ", ".join(f"{m} {_usd(v)}" for m, v in sorted(by_month.items()))
        # The breakdown goes on EVERY one of this member's statements, not just the month in
        # hand. A rep reading one month cannot otherwise tell whether the rest was settled or
        # forgotten, and in this case it was forgotten: the whole history had never been
        # invoiced to anybody.
        notes.append(
            f"{member}: {_usd(allocated)} collected that the commission table never recorded. "
            f"By month: {spread}. This statement carries the {period} part, {_usd(added)}."
            + ("" if allocated == added else
               " The other months are owed too and need their own statements -- confirm "
               "whether any of it was already paid by hand before paying twice."))
        if money(commission_ever) > 0:
            # Part recorded, part not, and a GHL transaction and a Stripe charge share no key
            # to match them one to one. FIFO makes the split deterministic and makes the
            # months add up to the shortfall exactly -- it does not make it EVIDENCE of which
            # dollar is which, and on a document somebody pays from that distinction matters.
            notes.append(
                f"{member}'s cash appears in BOTH records "
                f"({_usd(money(commission_ever))} recorded of {_usd(money(stripe_ever))} "
                f"collected). The unrecorded part is assigned to months oldest-first, which "
                f"is a rule, not a matched payment trail. Check this one before paying.")
    return rows, notes


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
    unpriced = [r["member"] for r in out if not r.get("contract")]
    notes = ([f"No stage for: {missing}"] if missing else [])
    if unpriced:
        # Only worth saying when it is true. The sync keeps monetaryValue now, so this is a
        # deal nobody priced in the CRM rather than a gap in what is stored.
        notes.append(f"No contract value in the CRM, so no remaining balance, for: {unpriced}")

    # Cash the commission table never saw. Checked for EVERY rep on EVERY run rather than
    # behind a flag: the reason Brianna Wood was missing from October is that nobody knew to
    # ask, and a check you have to remember is one that gets forgotten the month it matters.
    gaps, gap_notes = cohort2_gaps(period, cfg)
    if gaps:
        out.extend(gaps)
        out.sort(key=lambda r: (r["paid"], r["member"] or ""))
    notes.extend(gap_notes)
    return out, notes


def exceptions(c1: list[dict], c2: list[dict], cfg: dict,
               partial: bool = False, c1meta: dict | None = None) -> list[str]:
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
    meta = c1meta or {}
    if not c1 and not partial:
        # Two different facts, and the difference decides whether anybody needs to act.
        if meta.get("members_for_this_rep") == 0:
            out.append(f"{payee} has no members in the first cohort, so there is nothing to "
                       f"settle for it. This statement is complete, not partial.")
        else:
            out.append(f"{payee} has {meta.get('members_for_this_rep')} first-cohort members "
                       f"but none of them paid this period. Worth a glance: a whole cohort "
                       f"going quiet for a month is unusual.")
    other = meta.get("excluded_other_reps") or {}
    def describe(bucket):
        # Only quote cash the tracker actually supplied. Reading it from Stripe means the
        # tracker column was never opened, so every excluded row's cash is 0 by construction
        # and printing it would assert something false.
        show_cash = meta.get("cash_from") == "tracker"
        return ", ".join(
            f"{name} ({v['rows']} rows" + (f", ${v['cash']:,.2f})" if show_cash else ")")
            for name, v in sorted(bucket.items()))
    if other:
        out.append("First-cohort rows belonging to other reps were excluded from this "
                   f"statement: {describe(other)}. They are somebody else's to settle, "
                   f"not missing.")
    orphan = meta.get("excluded_rows_with_no_rep") or {}
    if orphan:
        out.append(f"First-cohort tracker rows with NO rep name, excluded and owed to nobody: "
                   f"{describe(orphan)}. Somebody has to say whose these are.")
    for n in (meta.get("notes") or []):
        out.append(n)
    if not c2:
        out.append("No cohort-2 payments found for this period.")
    billed = [r["member"] for r in c2 if r["already_billed"]]
    if billed:
        out.append(f"Already on an earlier statement, so carrying no commission here: {billed}")
    dupes = {r["member"] for r in c1} & {r["member"] for r in c2}
    if dupes:
        out.append(f"IN BOTH COHORTS - would be paid twice unless resolved: {sorted(dupes)}")
    if not partial and meta.get("cash_from") == "tracker":
        out.append("Confirm each cohort-1 instalment actually settled: the tracker is kept by "
                   "hand and a plan being active is not a payment succeeding.")
    if meta.get("cash_from") == "stripe":
        # NOT gated on c1 being non-empty. Gated, the sentence disappeared exactly when Stripe
        # found nothing -- the case where the undercount is total and the reader most needs it.
        out.append("Cohort-1 cash here is what the beCollective Stripe account actually "
                   "collected, not what the tracker says. A member who paid by some other "
                   "route this period would be missing from it, so this is a floor."
                   + ("" if c1 else " Stripe shows NO cohort-1 cash for this rep this period, "
                                    "which is not the same as none having been collected."))
    if partial:
        out.append("PARTIAL STATEMENT: the first cohort is excluded from this run. That "
                   "cohort was the majority of September's commission and is still kept "
                   "by hand, so a second payment is owed for it. The document must say "
                   "so, and the rep must be told.")
    # Deliberately no name here. One rep's disputed row in one month is not a fact about
    # this product -- it belongs in that statement's reviewed JSON, with a reason. Baked in
    # here it printed "(Sandy Schoettle, September)" on a different rep's October statement.
    out.append("Confirm every cohort-2 sale is really this rep's before paying. Attribution "
               "comes from the Sales Rep field on the GHL opportunity, and a wrong one there "
               "is a wrong payment here.")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True, help="YYYY-MM")
    ap.add_argument("--out", default=None)
    ap.add_argument("--rep", default=None, metavar="EMAIL",
                    help="build for this rep instead of the one in config.json. Their name and "
                         "rate come from the product's sales_rep table, never from a flag.")
    ap.add_argument("--cohort1-from-stripe", action="store_true",
                    help="take cohort 1's CASH from the beCollective Stripe feed, using the "
                         "tracker only as the roster. For months the tracker has no column for.")
    ap.add_argument("--cohort2-only", action="store_true",
                    help="settle only the cohort the product tracks, and SAY SO on the "
                         "statement. For while the older cohort is still kept by hand.")
    a = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}", a.period):
        sys.exit("--period wants YYYY-MM")

    cfg = load_config()
    if a.rep:
        cfg.update(rep_from_db(a.rep))
    if a.cohort2_only:
        c1, c1meta = [], {"skipped": "excluded by --cohort2-only"}
    else:
        c1, c1meta = cohort1(a.period, cfg, from_stripe=a.cohort1_from_stripe)
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
        "exceptions": exceptions(c1, c2, cfg, partial=a.cohort2_only, c1meta=c1meta),
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
    elif c1meta.get("cash_from") == "stripe":
        print(f"period {a.period}   cohort-1 cash from Stripe "
              f"({c1meta['members_for_this_rep']} members on the tracker roster)")
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
