"""Find every row in the database that still spells the old name.

WHY THIS EXISTS RATHER THAN A LIST. A rename is easy to grep for in source and easy to miss
in data, and the tempting move is to write down the tables you think are affected. That list
is wrong the moment somebody adds a column, and it is wrong in the direction that does not
announce itself: the migration runs, reports success, and leaves rows behind.

So this enumerates its own scope. It asks `information_schema` for every text-ish column in
the schema and counts matches in each. Nothing here is hardcoded except the string being
looked for, which means a column added next month is covered without anyone remembering.

Read-only. It never writes.

    python -m scripts.scan_rebrand                          # against DATABASE_URL
    python -m scripts.scan_rebrand --url postgresql://...   # against anything else
    python -m scripts.scan_rebrand --term acumyn --show 3   # print sample values

EXIT CODES: 0 when nothing is found, 1 when something is. So it can gate a cutover step.

EXPECTED LEFTOVERS. Some hits are meant to stay, and the scan cannot know that, so it does
not try -- it reports and you judge. As of AXCION-REBRAND-SPEC.md §A2 those are:

  * `audit_log.actor_label` / `platform_audit` -- history, recorded under the name the
    platform had at the time. Rewriting an audit trail defeats its purpose.

Anything else it finds is a gap in the migration, not a gap in this list.
"""
from __future__ import annotations

import argparse
import sys

TEXTY = ("text", "character varying", "character", "json", "jsonb")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", help="database URL; defaults to the app's DATABASE_URL")
    ap.add_argument("--term", default="acumyn", help="string to look for (default: acumyn)")
    ap.add_argument("--show", type=int, default=0,
                    help="print up to N sample values per hit (default: 0, counts only)")
    args = ap.parse_args()

    url = args.url
    if not url:
        from app.config import settings
        url = settings.sync_database_url

    import sqlalchemy as sa

    engine = sa.create_engine(url)
    with engine.connect() as c:
        if c.dialect.name != "postgresql":
            print(f"[scan] {c.dialect.name} is not supported -- this reads "
                  f"information_schema, which is where the point of the script is.",
                  file=sys.stderr)
            return 2

        cols = c.execute(sa.text("""
            SELECT table_name, column_name, data_type
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND data_type = ANY(:texty)
             ORDER BY table_name, column_name
        """), {"texty": list(TEXTY)}).fetchall()

        hits, scanned, skipped = [], 0, []
        for table, column, dtype in cols:
            expr = f'"{column}"::text' if dtype in ("json", "jsonb") else f'"{column}"'
            try:
                n = c.execute(
                    sa.text(f'SELECT count(*) FROM "{table}" WHERE {expr} ILIKE :pat'),
                    {"pat": f"%{args.term}%"}).scalar_one()
                scanned += 1
            except Exception as exc:                      # a view, a permission, a bad cast
                c.rollback()
                skipped.append(f"{table}.{column} ({type(exc).__name__})")
                continue
            if n:
                samples = []
                if args.show:
                    samples = [r[0] for r in c.execute(
                        sa.text(f'SELECT DISTINCT {expr} FROM "{table}" '
                                f'WHERE {expr} ILIKE :pat LIMIT :lim'),
                        {"pat": f"%{args.term}%", "lim": args.show}).fetchall()]
                hits.append((table, column, dtype, n, samples))

    print(f"[scan] {scanned} text/json columns scanned for {args.term!r}")
    if skipped:
        print(f"[scan] {len(skipped)} could not be read: {', '.join(skipped[:6])}"
              + (" ..." if len(skipped) > 6 else ""))
    if not hits:
        print("[scan] nothing found.")
        return 0

    width = max(len(f"{t}.{c}") for t, c, _d, _n, _s in hits)
    print(f"[scan] {len(hits)} column(s) still contain it:")
    for table, column, dtype, n, samples in hits:
        print(f"    {f'{table}.{column}':<{width}}  {n:>6} row(s)  [{dtype}]")
        for s in samples:
            text = (s or "")[:160].replace("\n", " ")
            print(f"        {text}{'...' if s and len(s) > 160 else ''}")
    print("\n[scan] Check each against AXCION-REBRAND-SPEC.md §A2 (the exception register). "
          "Anything not listed there is a gap in the migration.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
