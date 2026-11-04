r"""Forum-side context for the KB - Forum campaign. READ-ONLY.

The first report established what Meta did. This answers the question an exec summary actually
turns on: is there ANY evidence of what happened after the click, and how far can it be pushed
before it becomes a guess.

The ads module cannot help here - attribution is written only from beCollective Shift
registrants, so a Forum click never produces an attribution row. The Forum keeps its own
records, and one of them carries an opportunity SOURCE. That is not ad attribution and must
never be presented as it; it is the difference between "we cannot see anything" and "here is
what the Forum's own pipeline did while the ads ran, which is not the same claim".

Same guarantees as ads_campaign_report.py: SELECT-only, autocommit, Postgres required, no
secrets and no personal names printed.

USAGE (PowerShell, from backend\)
    $env:DATABASE_URL = '<DATABASE_PUBLIC_URL>'
    & .\.venv\Scripts\python.exe scripts\ads_forum_followup.py --tenant springb --since 2026-10-01 --out ads_probe_out\forum.txt
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from urllib.parse import urlsplit

from sqlalchemy import create_engine, text

_SELECT = re.compile(r"^\s*(select|with)\b", re.I)


def _normalise(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _q(conn, sql: str, **params):
    if not _SELECT.match(sql):
        raise RuntimeError("refused: SELECT only")
    return conn.execute(text(sql), params).mappings().all()


def _f(x):
    return float(x or 0)


def _usd(x):
    return "—" if x is None else f"${x:,.0f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--since", default="2026-10-01")
    ap.add_argument("--campaign", default="KB - Forum")
    ap.add_argument("--out")
    a = ap.parse_args()

    raw = os.environ.get("DATABASE_URL", "")
    if not raw or "sqlite" in raw or not raw.startswith(("postgres://", "postgresql://")):
        print("Set DATABASE_URL to the Postgres DATABASE_PUBLIC_URL. Refusing to guess.",
              file=sys.stderr)
        return 2

    since = dt.date.fromisoformat(a.since)
    out: list[str] = []
    p = out.append

    engine = create_engine(_normalise(raw), isolation_level="AUTOCOMMIT",
                           connect_args={"connect_timeout": 20})
    with engine.connect() as c:
        pr = urlsplit(raw)
        p(f"FORUM FOLLOW-UP · {a.tenant} · since {since}")
        p(f"database: {pr.hostname}:{pr.port or 5432}{pr.path}")
        p("=" * 78)
        tid = _q(c, "select id from tenant where slug = :s", s=a.tenant)[0]["id"]

        # 1. Is it still running, and how is it pacing day by day?
        p(f"\n1. {a.campaign} — DAILY since {since}")
        p("-" * 78)
        p(f"  {'day':<12}{'spend':>9}{'impr':>9}{'clicks':>8}{'leads':>7}{'CTR':>8}{'CPC':>8}")
        rows = _q(c, """
            select i.occurred_on as d, sum(i.spend) as spend, sum(i.impressions) as impr,
                   sum(i.inline_link_clicks) as lc, sum(i.leads) as leads
              from ad_insight_daily i
              join ad_campaign ac on ac.id = i.campaign_id
             where i.tenant_id = :t and i.level = 'campaign'
               and ac.name = :c and i.occurred_on >= :s
             group by i.occurred_on order by i.occurred_on
        """, t=tid, c=a.campaign, s=since)
        tot = {"spend": 0.0, "impr": 0, "lc": 0, "leads": 0}
        for r in rows:
            ctr = (_f(r["lc"]) / _f(r["impr"]) * 100) if r["impr"] else None
            cpc = (_f(r["spend"]) / _f(r["lc"])) if r["lc"] else None
            p(f"  {str(r['d']):<12}{_usd(_f(r['spend'])):>9}{r['impr'] or 0:>9,}"
              f"{r['lc'] or 0:>8,}{r['leads'] or 0:>7,}"
              f"{('—' if ctr is None else f'{ctr:.2f}%'):>8}"
              f"{('—' if cpc is None else f'${cpc:,.2f}'):>8}")
            tot["spend"] += _f(r["spend"])
            for k in ("impr", "lc", "leads"):
                tot[k] += int(r[{"impr": "impr", "lc": "lc", "leads": "leads"}[k]] or 0)
        if not rows:
            p("  no delivery in this period")
        else:
            ctr = tot["lc"] / tot["impr"] * 100 if tot["impr"] else None
            p(f"  {'TOTAL':<12}{_usd(tot['spend']):>9}{tot['impr']:>9,}{tot['lc']:>8,}"
              f"{tot['leads']:>7,}{('—' if ctr is None else f'{ctr:.2f}%'):>8}")
            p(f"  cost per Meta lead: "
              f"{_usd(tot['spend'] / tot['leads']) if tot['leads'] else '— (no leads)'}")

        # 2. Why the ad-level table does not cover the whole campaign.
        p("\n2. AD-LEVEL COVERAGE — how much of the spend is attributable to a named ad")
        p("-" * 78)
        cov = _q(c, """
            select min(i.occurred_on) as first_day, max(i.occurred_on) as last_day,
                   sum(i.spend) as ad_level_spend
              from ad_insight_daily i where i.tenant_id = :t and i.level = 'ad'
        """, t=tid)[0]
        camp_total = _q(c, """
            select sum(i.spend) as s from ad_insight_daily i
             where i.tenant_id = :t and i.level = 'campaign'
        """, t=tid)[0]["s"]
        p(f"  ad-level rows exist {cov['first_day']} → {cov['last_day']}")
        p(f"  ad-level spend {_usd(_f(cov['ad_level_spend']))} of campaign-level "
          f"{_usd(_f(camp_total))} across the whole account")
        p("  Spend outside that range has no per-ad breakdown and must not be read as $0 per ad.")

        # 3. The Forum's OWN pipeline. Not ad attribution.
        p("\n3. FORUM PIPELINE MOVEMENT (forum_stage_event) — the Forum's own records")
        p("-" * 78)
        ev = _q(c, """
            select m.segment as stage_group,
                   coalesce(m.meta->>'opp_source', '— none recorded —') as opp_source,
                   count(*) as n, min(m.occurred_on) as first_day, max(m.occurred_on) as last_day
              from metric_record m join business b on b.id = m.business_id
             where m.tenant_id = :t and m.kind = 'forum_stage_event' and m.occurred_on >= :s
             group by m.segment, m.meta->>'opp_source'
             order by count(*) desc
        """, t=tid, s=since)
        if not ev:
            p("  none in this period")
        for r in ev:
            p(f"    {r['n']:>4}  {str(r['stage_group']):<22} source={r['opp_source']:<28} "
              f"{r['first_day']} → {r['last_day']}")
        p("\n  `opp_source` is GHL's own field on the opportunity. It is NOT ad attribution:")
        p("  nothing here ties a person to a click, an ad or a campaign.")

        # 4. Current Forum funnel occupancy, for the denominator.
        p("\n4. FORUM SALES FUNNEL — current snapshot (kind='recruiting')")
        p("-" * 78)
        rec = _q(c, """
            select coalesce(m.meta->>'stage', '—') as stage, count(*) as n
              from metric_record m
             where m.tenant_id = :t and m.kind = 'recruiting'
             group by m.meta->>'stage' order by count(*) desc limit 20
        """, t=tid)
        if not rec:
            p("  no snapshot rows")
        for r in rec:
            p(f"    {r['n']:>4}  {r['stage']}")

        # 5. Appointments booked, the Forum's nearest thing to a conversion event.
        p("\n5. FORUM APPOINTMENTS (forum_appt_cal)")
        p("-" * 78)
        appt = _q(c, """
            select m.occurred_on as d, count(*) as n
              from metric_record m
             where m.tenant_id = :t and m.kind = 'forum_appt_cal' and m.occurred_on >= :s
             group by m.occurred_on order by m.occurred_on
        """, t=tid, s=since)
        if not appt:
            p("  none in this period")
        for r in appt:
            p(f"    {r['d']}  {r['n']}")

        p("\n" + "=" * 78)
        p("Read-only. Nothing was written. No names or emails were selected.")

    body = "\n".join(out)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(body + "\n")
        print(f"wrote {a.out}")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
