"""Read-only questions about commission data, for building and checking a monthly statement.

DELIBERATELY NARROW. This exists so a permission rule can name ONE reviewable script instead of
granting the interpreter, which would be a grant to run anything. Every query here is a SELECT,
the connection is opened read-only, and the database URL is never printed: it carries the
password.

    python scripts/commission_probe.py --list
    python scripts/commission_probe.py closed
    python scripts/commission_probe.py cash --rep someone@example.com

Add a question by adding a SELECT to QUERIES. Anything that is not a SELECT is refused before it
reaches the server, so this file cannot grow a write by accident or by edit.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

TZ = "America/Denver"          # the launch timezone; months bucket in it, not UTC

QUERIES: dict[str, tuple[str, str]] = {
    "closed": (
        "Months closed in the product. A row here means that month is settled and its "
        "rows are frozen.",
        "SELECT period, total_cash, total_commission, payment_count, closed_at "
        "FROM commission_statement ORDER BY period",
    ),
    "reps": (
        "Sales reps and their commission rate. NULL means no rate set, never a silent 0%.",
        "SELECT display_name, email, commission_pct, is_active FROM sales_rep "
        "WHERE commission_pct IS NOT NULL ORDER BY display_name",
    ),
    "cash": (
        "One rep's cash by month, in the launch timezone.",
        f"SELECT to_char(paid_at AT TIME ZONE '{TZ}', 'YYYY-MM') AS month, count(*) AS payments,"
        " sum(amount) AS cash, sum(amount) * 0.10 AS commission_at_10pct"
        " FROM commission_payment WHERE rep_email = %(rep)s"
        " GROUP BY 1 ORDER BY 1",
    ),
    "month": (
        "Every payment for one rep in one month, with whether it is already on a statement.",
        f"SELECT to_char(paid_at AT TIME ZONE '{TZ}', 'YYYY-MM-DD') AS paid,"
        " member_name, amount, rate_pct, commission_amount,"
        " CASE WHEN statement_id IS NULL THEN 'open' ELSE 'BILLED' END AS state"
        f" FROM commission_payment WHERE rep_email = %(rep)s"
        f" AND to_char(paid_at AT TIME ZONE '{TZ}', 'YYYY-MM') = %(period)s"
        " ORDER BY paid, member_name",
    ),
    "stripe_emails": (
        "Every email the Stripe feed has a payment for, with its range and total. Joined "
        "against the tracker locally, this answers whether the feed could replace the "
        "hand-kept sheet for the older cohort.",
        "SELECT lower(email) AS email, count(*) AS payments,"
        " min(occurred_on) AS first, max(occurred_on) AS last, sum(amount) AS cash"
        " FROM metric_record WHERE source = 'stripe_bc' AND kind = 'payment'"
        " AND email IS NOT NULL GROUP BY 1 ORDER BY 1",
    ),
    "stripe_month": (
        "The Stripe feed's payments in one month, by email.",
        "SELECT lower(email) AS email, count(*) AS payments, sum(amount) AS cash"
        " FROM metric_record WHERE source = 'stripe_bc' AND kind = 'payment'"
        " AND email IS NOT NULL AND to_char(occurred_on, 'YYYY-MM') = %(period)s"
        " GROUP BY 1 ORDER BY 1",
    ),
    "kinds": (
        "What the metric feed actually holds, so a join is written against real kinds.",
        "SELECT source, kind, count(*) AS rows, max(occurred_on) AS latest"
        " FROM metric_record GROUP BY 1, 2 ORDER BY 1, 2",
    ),
    "opps": (
        "The opportunity snapshot behind one rep's payments: stage, contract value and what "
        "has been collected against it. Feeds the statement's Stage / Contract / Remaining "
        "columns.",
        "SELECT DISTINCT cp.member_name, mr.meta ->> 'stage' AS stage, mr.amount AS contract,"
        " (SELECT sum(amount) FROM commission_payment x WHERE x.contact_id = cp.contact_id)"
        "   AS collected_to_date, mr.meta ->> 'payment_plan' AS plan"
        " FROM commission_payment cp"
        " LEFT JOIN metric_record mr ON mr.external_id = cp.opportunity_id"
        "   AND mr.source = 'ghl' AND mr.kind = 'bc_launch_opp'"
        " WHERE cp.rep_email = %(rep)s ORDER BY 1",
    ),
    "launch": (
        "The cohort's ticket prices and instalment count: where a contract value comes from "
        "without asking the CRM.",
        "SELECT name, ticket_pif, ticket_plan, plan_installments, is_active"
        " FROM launch ORDER BY created_at DESC",
    ),
    "opp_meta": (
        "What the opportunity snapshot keeps in meta, to see whether the stage name is there.",
        "SELECT external_id, name, status, amount, meta FROM metric_record"
        " WHERE source = 'ghl' AND kind = 'bc_launch_opp' AND meta IS NOT NULL LIMIT 3",
    ),
    "contracts": (
        "Contract value, plan and stage per member for one rep, all from the membership "
        "snapshot. No CRM call: amount is GHL's monetaryValue as the sync stored it.",
        "SELECT DISTINCT cp.member_name, mr.meta ->> 'stage' AS stage,"
        " mr.meta ->> 'payment' AS plan, mr.amount AS contract,"
        " (SELECT sum(amount) FROM commission_payment x WHERE x.contact_id = cp.contact_id)"
        "   AS collected"
        " FROM commission_payment cp"
        " LEFT JOIN metric_record mr ON mr.meta ->> 'contact_id' = cp.contact_id"
        "   AND mr.source = 'ghl' AND mr.kind = 'bc_membership'"
        " WHERE cp.rep_email = %(rep)s ORDER BY 1",
    ),
    "memberships": (
        "The membership snapshot itself: who is in it, with contract value and plan.",
        "SELECT name, amount AS contract, meta ->> 'payment' AS plan,"
        " meta ->> 'stage' AS stage, meta ->> 'contact_id' AS contact_id"
        " FROM metric_record WHERE source = 'ghl' AND kind = 'bc_membership'"
        " ORDER BY name",
    ),
    "all_emails": (
        "Every email any feed has a record for, with the source, kind and date range. "
        "Joined locally against a roster, this says which feed a missing member lives in.",
        "SELECT source, kind, lower(email) AS email, count(*) AS rows,"
        " min(occurred_on) AS first, max(occurred_on) AS last,"
        " coalesce(sum(amount), 0) AS total"
        " FROM metric_record WHERE email IS NOT NULL"
        " GROUP BY 1, 2, 3 ORDER BY 3, 1, 2",
    ),
    "all_names": (
        "Every NAME any feed holds, with its source and money. For finding somebody who is "
        "in a feed under a different email than the roster has.",
        "SELECT source, kind, lower(name) AS name, lower(coalesce(email,'')) AS email,"
        " count(*) AS rows, max(occurred_on) AS last, coalesce(sum(amount),0) AS total"
        " FROM metric_record WHERE name IS NOT NULL"
        " GROUP BY 1,2,3,4 ORDER BY 3",
    ),
    "dropped_summary": (
        "What the roster filter throws away, split by whether the OFFERING filter would have "
        "accepted it. A 'true' row is money the dashboard is not counting and should be.",
        "SELECT source, (meta ->> 'would_be_offering') AS would_be_offering,"
        " count(*) AS charges, coalesce(sum(amount), 0) AS total,"
        " count(DISTINCT lower(coalesce(email, ''))) AS payers,"
        " min(occurred_on) AS first, max(occurred_on) AS last"
        " FROM metric_record WHERE kind = 'dropped_offroster'"
        " GROUP BY 1, 2 ORDER BY 1, 2",
    ),
    "dropped": (
        "Each payer the roster filter refused whose charges the offering filter WOULD have "
        "accepted: the members the GHL-derived roster has never heard of.",
        "SELECT source, lower(coalesce(email, '(no email)')) AS email, max(name) AS name,"
        " max(meta ->> 'segment') AS segment, count(*) AS charges,"
        " coalesce(sum(amount), 0) AS total,"
        " min(occurred_on) AS first, max(occurred_on) AS last"
        " FROM metric_record WHERE kind = 'dropped_offroster'"
        " AND meta ->> 'would_be_offering' = 'true'"
        " GROUP BY 1, 2 ORDER BY 6 DESC",
    ),
    "dropped_why": (
        "Why each refusal happened: no email on the charge at all, or an email the roster "
        "does not carry. Split by whether the offering filter would have accepted it.",
        "SELECT source, (meta ->> 'reason') AS reason, (meta ->> 'labelled') AS had_ghl_label,"
        " (meta ->> 'would_be_offering') AS would_be_offering, count(*) AS charges,"
        " coalesce(sum(amount), 0) AS total"
        " FROM metric_record WHERE kind = 'dropped_offroster'"
        " GROUP BY 1, 2, 3, 4 ORDER BY 1, 4 DESC, 6 DESC",
    ),
    "dropped_status": (
        "Per payer, the dropped would-be-member charges split by Stripe status. Gross amount "
        "ignores status and refunds, so a cluster of identical same-day charges may be retries.",
        "SELECT source, lower(coalesce(email, '(no email)')) AS email, max(name) AS name,"
        " status, count(*) AS charges, coalesce(sum(amount), 0) AS total,"
        " min(occurred_on) AS first, max(occurred_on) AS last"
        " FROM metric_record WHERE kind = 'dropped_offroster'"
        " AND meta ->> 'would_be_offering' = 'true'"
        " GROUP BY 1, 2, 4 HAVING coalesce(sum(amount), 0) >= 500 ORDER BY 6 DESC",
    ),
    "dropped_overlap": (
        "Payers who appear BOTH in the dropped set and in the kept payment feed. Each one is a "
        "double-count waiting to happen if the roster is widened without deduping.",
        "SELECT d.email, d.sources AS dropped_in, d.dropped_charges, d.dropped_total,"
        " k.sources AS kept_in, k.kept_charges, k.kept_total"
        " FROM (SELECT lower(email) AS email, string_agg(DISTINCT source, ',') AS sources,"
        "   count(*) AS dropped_charges, coalesce(sum(amount), 0) AS dropped_total"
        "   FROM metric_record WHERE kind = 'dropped_offroster'"
        "   AND meta ->> 'would_be_offering' = 'true' AND email IS NOT NULL GROUP BY 1) d"
        " JOIN (SELECT lower(email) AS email, string_agg(DISTINCT source, ',') AS sources,"
        "   count(*) AS kept_charges, coalesce(sum(amount), 0) AS kept_total"
        "   FROM metric_record WHERE kind = 'payment' AND email IS NOT NULL GROUP BY 1) k"
        " ON k.email = d.email ORDER BY 4 DESC",
    ),
    "dropped_net": (
        "The dropped would-be-member money by status. Only 'succeeded' is cash that arrived; "
        "refunds are NOT netted out here because the diagnostic does not record them.",
        "SELECT source, status, count(*) AS charges,"
        " count(DISTINCT lower(coalesce(email, '(none)'))) AS payers,"
        " coalesce(sum(amount), 0) AS gross,"
        " coalesce(sum(CASE WHEN amount >= 100 THEN amount ELSE 0 END), 0) AS gross_over_100,"
        " count(DISTINCT CASE WHEN amount >= 100"
        "   THEN lower(coalesce(email, '(none)')) END) AS payers_over_100"
        " FROM metric_record WHERE kind = 'dropped_offroster'"
        " AND meta ->> 'would_be_offering' = 'true'"
        " GROUP BY 1, 2 ORDER BY 1, 5 DESC",
    ),
    "find_name": (
        "Every feed row whose name or email matches a pattern (pass it with --rep, e.g. "
        "--rep '%huffman%'). For checking whether a person exists in ANY feed before saying "
        "their money is recorded nowhere.",
        "SELECT source, kind, lower(name) AS name, lower(coalesce(email, '')) AS email,"
        " count(*) AS rows, coalesce(sum(amount), 0) AS total,"
        " min(occurred_on) AS first, max(occurred_on) AS last"
        " FROM metric_record"
        " WHERE lower(coalesce(name, '')) LIKE %(rep)s"
        " OR lower(coalesce(email, '')) LIKE %(rep)s"
        " GROUP BY 1, 2, 3, 4 ORDER BY 3, 1, 2",
    ),
    "kept_net": (
        "The KEPT payment feed by status, so a comparison against the dropped set is like for "
        "like. Gross of refunds on both sides.",
        "SELECT source, status, count(*) AS charges,"
        " count(DISTINCT lower(coalesce(email, '(none)'))) AS payers,"
        " coalesce(sum(amount), 0) AS gross"
        " FROM metric_record WHERE kind = 'payment'"
        " AND source IN ('stripe_bc', 'stripe_legacy')"
        " GROUP BY 1, 2 ORDER BY 1, 5 DESC",
    ),
    "dropped_desc": (
        "What the dropped would-be-member charges actually SAY. The offering filter accepts on "
        "a program name, a membership word, or merely 'recurring and over $500' - so the "
        "description is what distinguishes a real membership from an unevidenced one-off.",
        "SELECT source, (meta ->> 'description') AS description, count(*) AS charges,"
        " count(DISTINCT lower(coalesce(email, '(none)'))) AS payers,"
        " coalesce(sum(amount), 0) AS total"
        " FROM metric_record WHERE kind = 'dropped_offroster'"
        " AND meta ->> 'would_be_offering' = 'true' AND status = 'succeeded'"
        " GROUP BY 1, 2 ORDER BY 5 DESC",
    ),
    "stripe_excludes": (
        "The exclude_emails / extra_member_emails lists on the Stripe integrations. An "
        "exclude_emails hit is reported by the diagnostic as 'off_roster', so it can look like "
        "a roster gap when it is a deliberate suppression. Named keys only - no secrets.",
        "SELECT provider, config -> 'exclude_emails' AS exclude_emails,"
        " config -> 'extra_member_emails' AS extra_member_emails,"
        " config -> 'membership_min_amount' AS membership_min_amount"
        " FROM integration WHERE provider LIKE 'stripe%%'"
        " ORDER BY provider",
    ),
    "month_all": (
        "Every rep's cash and commission for one month (--period YYYY-MM), with how much is "
        "already on a statement. This is the whole payroll question in one answer.",
        f"SELECT coalesce(rep_email, '(no rep attributed)') AS rep,"
        " count(*) AS payments, sum(amount) AS cash,"
        " sum(CASE WHEN statement_id IS NULL THEN amount ELSE 0 END) AS cash_open,"
        " sum(CASE WHEN statement_id IS NULL THEN 0 ELSE amount END) AS cash_billed"
        " FROM commission_payment"
        f" WHERE to_char(paid_at AT TIME ZONE '{TZ}', 'YYYY-MM') = %(period)s"
        " GROUP BY 1 ORDER BY 3 DESC",
    ),
    "sync_health": (
        "When each integration last synced, and its last error. The first question when a "
        "config change has not taken effect: did a sync even run since the write?",
        "SELECT provider, status, last_synced_at,"
        " left(coalesce(last_error, ''), 90) AS last_error"
        " FROM integration ORDER BY last_synced_at DESC NULLS LAST",
    ),
    "bc_month_emails": (
        "Every beCollective payer with succeeded cash in one month (--period YYYY-MM), whether "
        "the roster kept the charge or the diagnostic recorded it as dropped. Joined against "
        "the cohort-1 tracker locally, this says whether Stripe can supply cohort 1's month.",
        "SELECT lower(email) AS email, max(name) AS name,"
        " string_agg(DISTINCT kind, ',') AS kinds, count(*) AS charges,"
        " coalesce(sum(amount), 0) AS cash"
        " FROM metric_record WHERE source = 'stripe_bc' AND status = 'succeeded'"
        " AND kind IN ('payment', 'dropped_offroster') AND email IS NOT NULL"
        " AND to_char(occurred_on, 'YYYY-MM') = %(period)s"
        " GROUP BY 1 ORDER BY 5 DESC",
    ),
    "unpriceable": (
        "Cash nobody can be paid on: no rep attributed, or a rep with no rate.",
        f"SELECT to_char(cp.paid_at AT TIME ZONE '{TZ}', 'YYYY-MM') AS month,"
        " coalesce(cp.rep_email, '(no rep)') AS rep, count(*) AS payments, sum(cp.amount) AS cash"
        " FROM commission_payment cp"
        " LEFT JOIN sales_rep sr ON sr.email = cp.rep_email"
        " WHERE cp.rep_email IS NULL OR sr.commission_pct IS NULL"
        " GROUP BY 1, 2 ORDER BY 1, 2",
    ),
}


def database_url() -> str:
    """From the environment, or from Railway. Returned, never printed."""
    for var in ("DATABASE_PUBLIC_URL", "DATABASE_URL"):
        if os.environ.get(var):
            return os.environ[var]
    cached = pathlib.Path(__file__).resolve().parents[1] / ".pg_url.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))["DATABASE_PUBLIC_URL"]
    # shutil.which, not a bare name: on Windows the CLI is `railway.cmd`, and subprocess does
    # not apply PATHEXT, so a bare "railway" raises FileNotFoundError and reads as "Railway is
    # broken" when it is on the PATH and working.
    exe = shutil.which("railway")
    if not exe:
        sys.exit("The Railway CLI is not on PATH. Either `npm i -g @railway/cli`, or set "
                 "DATABASE_PUBLIC_URL in the environment and run this again.")
    out = subprocess.run([exe, "variables", "--service", "Postgres", "--json"],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        sys.exit(f"Railway would not give up the variables (exit {out.returncode}). "
                 f"Try `railway link` first, or set DATABASE_PUBLIC_URL.")
    try:
        return json.loads(out.stdout)["DATABASE_PUBLIC_URL"]
    except (json.JSONDecodeError, KeyError):
        sys.exit("Railway's reply had no DATABASE_PUBLIC_URL in it.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", help="which question to ask")
    ap.add_argument("--list", action="store_true", help="list the questions")
    ap.add_argument("--rep", default=None, help="rep email; required by some questions")
    ap.add_argument("--period", default=None, help="YYYY-MM")
    a = ap.parse_args()

    if a.list or not a.query:
        print("questions:")
        for name, (why, _) in QUERIES.items():
            print(f"  {name:<12} {why}")
        return
    if a.query not in QUERIES:
        sys.exit(f"No such question: {a.query!r}. Try --list.")

    why, sql = QUERIES[a.query]
    # Belt and braces: the only statement this file may ever send is a SELECT.
    if not re.match(r"^\s*SELECT\b", sql, re.I) or ";" in sql:
        sys.exit("Refusing: that is not a single SELECT.")

    import psycopg

    url = database_url().replace("postgresql+psycopg://", "postgresql://")
    print(f"# {why}\n")
    with psycopg.connect(url, connect_timeout=30) as conn:
        conn.read_only = True                      # the server refuses a write on this session
        with conn.cursor() as cur:
            cur.execute(sql, {"rep": a.rep, "period": a.period})
            cols = [d.name for d in cur.description]
            rows = cur.fetchall()
    if not rows:
        print("(no rows)")
        return
    width = [max(len(c), *(len(str(r[i])) for r in rows)) for i, c in enumerate(cols)]
    print("  ".join(c.ljust(width[i]) for i, c in enumerate(cols)))
    print("  ".join("-" * w for w in width))
    for r in rows:
        print("  ".join(str(v).ljust(width[i]) for i, v in enumerate(r)))
    print(f"\n{len(rows)} row(s)")


if __name__ == "__main__":
    main()
