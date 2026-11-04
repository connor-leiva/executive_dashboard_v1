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
    out = subprocess.run(["railway", "variables", "--service", "Postgres", "--json"],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        sys.exit("Could not read the database URL from Railway. Set DATABASE_PUBLIC_URL "
                 "in the environment instead.")
    return json.loads(out.stdout)["DATABASE_PUBLIC_URL"]


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
