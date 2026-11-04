r"""Campaign performance, straight from the database. READ-ONLY.

Written for an exec summary: what did a campaign cost, what did it buy on Meta's side, and -
the part that matters more - what can this workspace HONESTLY say about what happened after
the click.

WHY A SCRIPT RATHER THAN THE TAB. The Ads tab answers this already, but reading it needs a
signed-in browser session. This reads the same rows the tab reads, from the same database, so
the two cannot disagree; anything it prints can be checked against the tab.

READ-ONLY, AND THAT IS ENFORCED, NOT PROMISED. The session is opened in autocommit with no
transaction of its own, every statement goes through `_q` which refuses anything that is not a
SELECT, and nothing here imports app.seed - seed() calls _wipe(), which deletes the workspace.

IT WILL NOT SILENTLY READ THE WRONG DATABASE. With DATABASE_URL unset the project's own helpers
fall back to a local SQLite file and report on data that has nothing to do with production -
numbers that look real and are not. This refuses to run unless the URL is Postgres, and prints
the host it reached so the reader can see which database answered.

NO SECRETS ARE PRINTED. The connection string is read from the environment and never echoed;
only host and database name are shown. Contact names and emails are never selected.

USAGE (PowerShell)
    $env:DATABASE_URL = '<the Postgres service DATABASE_PUBLIC_URL from Railway>'
    & .\.venv\Scripts\python.exe scripts\ads_campaign_report.py --tenant springb --days 90 --out report.txt
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from urllib.parse import urlsplit

from sqlalchemy import create_engine, text


def _normalise(url: str) -> str:
    """SQLAlchemy needs the driver named; Railway hands out a bare postgresql:// URL."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _where(url: str) -> str:
    """Host and database only. Never the user, never the password."""
    p = urlsplit(url)
    return f"{p.hostname}:{p.port or 5432}{p.path}"


_SELECT = re.compile(r"^\s*(select|with)\b", re.I)


def _q(conn, sql: str, **params):
    if not _SELECT.match(sql):
        raise RuntimeError(f"refused: this script only runs SELECT, got {sql.split()[0]!r}")
    return conn.execute(text(sql), params).mappings().all()


def _f(x) -> float:
    return float(x or 0)


def _rate(num, den, scale=1.0):
    num, den = _f(num), _f(den)
    return (num / den) * scale if den else None


def _usd(x):
    return "—" if x is None else f"${x:,.0f}"


def _n(x):
    return "—" if x is None else f"{x:,.0f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True, help="workspace slug, e.g. springb")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--out", help="write the report here instead of stdout")
    a = ap.parse_args()

    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        print("DATABASE_URL is not set. Set it to the Postgres service's DATABASE_PUBLIC_URL\n"
              "(NOT DATABASE_URL from Railway: that is postgres.railway.internal and only\n"
              "resolves inside Railway).", file=sys.stderr)
        return 2
    if "sqlite" in raw or not raw.startswith(("postgres://", "postgresql://")):
        print("DATABASE_URL does not point at Postgres. Refusing: a local SQLite fallback would\n"
              "print numbers that look real and describe nothing.", file=sys.stderr)
        return 2

    out: list[str] = []

    def p(s: str = ""):
        out.append(s)

    engine = create_engine(_normalise(raw), isolation_level="AUTOCOMMIT",
                           connect_args={"connect_timeout": 20})
    today = dt.date.today()
    start = today - dt.timedelta(days=a.days - 1)

    with engine.connect() as c:
        p(f"ADS CAMPAIGN REPORT · {a.tenant} · {start} to {today} ({a.days} days)")
        p(f"database: {_where(raw)}")
        p("=" * 78)

        ten = _q(c, "select id, slug, name from tenant where slug = :s", s=a.tenant)
        if not ten:
            p(f"no workspace with slug {a.tenant!r}")
            return _emit(out, a.out)
        tid = ten[0]["id"]
        p(f"workspace: {ten[0]['name']}")
        p()

        # ── 1. Which entity books the spend, and does it have a funnel ────────────────────
        p("1. AD ACCOUNTS — the entity that books the spend decides which funnel applies")
        p("-" * 78)
        accts = _q(c, """
            select aa.id, aa.external_id, aa.name, aa.currency, aa.timezone_name,
                   b.key as biz_key, b.name as biz_name, b.archetype
              from ad_account aa
              left join business b on b.id = aa.business_id
             where aa.tenant_id = :t
             order by aa.name
        """, t=tid)
        if not accts:
            p("  none connected")
        for r in accts:
            funnel = "program funnel" if r["archetype"] == "program" else "CLICK LAYER ONLY"
            p(f"  {r['name']}  ({r['external_id']})  {r['currency'] or ''} {r['timezone_name'] or ''}")
            p(f"    books to: {r['biz_name'] or '— no entity —'} "
              f"[key={r['biz_key'] or '-'} archetype={r['archetype'] or '-'}] -> {funnel}")
        p()

        # ── 2. Every campaign that delivered, so the right one can be named ──────────────
        p(f"2. CAMPAIGNS THAT DELIVERED in the window")
        p("-" * 78)
        camps = _q(c, """
            select ac.id, ac.name, ac.external_id, ac.status, ac.effective_status,
                   aa.name as acct, b.key as biz_key, b.archetype,
                   min(i.occurred_on) as first_day, max(i.occurred_on) as last_day,
                   count(distinct i.occurred_on) as days_live,
                   sum(i.spend) as spend, sum(i.impressions) as impr,
                   sum(i.clicks) as clicks, sum(i.inline_link_clicks) as link_clicks,
                   sum(i.leads) as leads, max(i.reach) as peak_daily_reach
              from ad_insight_daily i
              join ad_campaign ac on ac.id = i.campaign_id
              join ad_account aa on aa.id = i.ad_account_id
              left join business b on b.id = aa.business_id
             where i.tenant_id = :t and i.level = 'campaign'
               and i.occurred_on between :s and :e
             group by ac.id, ac.name, ac.external_id, ac.status, ac.effective_status,
                      aa.name, b.key, b.archetype
             order by sum(i.spend) desc
        """, t=tid, s=start, e=today)
        if not camps:
            p("  nothing delivered in this window")
        for r in camps:
            ctr = _rate(r["link_clicks"], r["impr"], 100)
            cpm = _rate(r["spend"], r["impr"], 1000)
            cpc = _rate(r["spend"], r["link_clicks"])
            cpl = _rate(r["spend"], r["leads"])
            p(f"  {r['name']}")
            p(f"    {r['acct']} · entity={r['biz_key'] or '-'} ({r['archetype'] or '-'}) · "
              f"status={r['effective_status'] or r['status'] or '-'}")
            p(f"    live {r['first_day']} → {r['last_day']}  ({r['days_live']} days with delivery)")
            p(f"    spend {_usd(_f(r['spend']))} · impressions {_n(r['impr'])} · "
              f"link clicks {_n(r['link_clicks'])} · all clicks {_n(r['clicks'])} · "
              f"Meta leads {_n(r['leads'])}")
            p(f"    CTR {'—' if ctr is None else f'{ctr:.2f}%'} · CPM {_usd(cpm)} · "
              f"CPC {'—' if cpc is None else f'${cpc:,.2f}'} · CPL {_usd(cpl)}")
            p()

        # ── 3. Week by week, so the summary can say which way it is going ────────────────
        p("3. WEEKLY TREND per campaign (Mon-anchored)")
        p("-" * 78)
        weekly = _q(c, """
            select ac.name, date_trunc('week', i.occurred_on)::date as wk,
                   sum(i.spend) as spend, sum(i.impressions) as impr,
                   sum(i.inline_link_clicks) as link_clicks, sum(i.leads) as leads
              from ad_insight_daily i
              join ad_campaign ac on ac.id = i.campaign_id
             where i.tenant_id = :t and i.level = 'campaign'
               and i.occurred_on between :s and :e
             group by ac.name, date_trunc('week', i.occurred_on)
             order by ac.name, wk
        """, t=tid, s=start, e=today)
        cur = None
        for r in weekly:
            if r["name"] != cur:
                cur = r["name"]
                p(f"  {cur}")
                p(f"    {'week':<12} {'spend':>10} {'impr':>10} {'clicks':>8} "
                  f"{'leads':>7} {'CPL':>9}")
            cpl = _rate(r["spend"], r["leads"])
            p(f"    {str(r['wk']):<12} {_usd(_f(r['spend'])):>10} {_n(r['impr']):>10} "
              f"{_n(r['link_clicks']):>8} {_n(r['leads']):>7} {_usd(cpl):>9}")
        p()

        # ── 4. Which creative is carrying it ─────────────────────────────────────────────
        p("4. ADS, by spend (top 15)")
        p("-" * 78)
        ads = _q(c, """
            select ad.name, ad.adset_name, ac.name as campaign, ad.effective_status,
                   sum(i.spend) as spend, sum(i.impressions) as impr,
                   sum(i.inline_link_clicks) as link_clicks, sum(i.leads) as leads
              from ad_insight_daily i
              join ad on ad.id = i.ad_id
              left join ad_campaign ac on ac.id = ad.campaign_id
             where i.tenant_id = :t and i.level = 'ad'
               and i.occurred_on between :s and :e
             group by ad.name, ad.adset_name, ac.name, ad.effective_status
             order by sum(i.spend) desc
             limit 15
        """, t=tid, s=start, e=today)
        if not ads:
            p("  no ad-level rows in this window (ad-level insights may not be syncing)")
        for r in ads:
            ctr = _rate(r["link_clicks"], r["impr"], 100)
            cpl = _rate(r["spend"], r["leads"])
            p(f"  {_usd(_f(r['spend'])):>9}  CTR {'—' if ctr is None else f'{ctr:.2f}%':>6}  "
              f"CPL {_usd(cpl):>8}  leads {_n(r['leads']):>5}  | {r['campaign'] or '-'} / "
              f"{(r['name'] or '')[:60]}")
        p()

        # ── 5. What we can say about what happened AFTER the click ───────────────────────
        p("5. AFTER THE CLICK — what this workspace can actually evidence")
        p("-" * 78)
        attr = _q(c, """
            select coalesce(ac.name, '— no campaign (channel-grade) —') as campaign,
                   a.match_method, count(*) as n,
                   min(a.first_seen_on) as first_seen, max(a.first_seen_on) as last_seen
              from ad_attribution a
              left join ad_campaign ac on ac.id = a.campaign_id
             where a.tenant_id = :t
             group by ac.name, a.match_method
             order by n desc
        """, t=tid)
        if not attr:
            p("  NO attribution rows at all in this workspace.")
        for r in attr:
            p(f"    {r['n']:>5}  {r['match_method']:<9} {r['campaign']}  "
              f"({r['first_seen']} → {r['last_seen']})")
        p()
        p("  Attribution is written from ONE source — MetricRecord kind='bc_shift_reg' —")
        p("  so only beCollective Shift registrants ever get a row. A campaign for any other")
        p("  entity has no post-click chain here, however well it is performing on Meta.")
        p()

        # ── 6. The entity's own records, as unattributed context ─────────────────────────
        p("6. RECORD KINDS PER ENTITY written in this window (NOT ad-attributed)")
        p("-" * 78)
        kinds = _q(c, """
            select b.key as biz, m.kind, count(*) as n,
                   min(m.occurred_on) as first_day, max(m.occurred_on) as last_day
              from metric_record m
              join business b on b.id = m.business_id
             where m.tenant_id = :t and m.occurred_on between :s and :e
             group by b.key, m.kind
             order by b.key, count(*) desc
        """, t=tid, s=start, e=today)
        cur = None
        for r in kinds:
            if r["biz"] != cur:
                cur = r["biz"]
                p(f"  {cur}")
            p(f"    {r['n']:>6}  {r['kind']:<22} {r['first_day']} → {r['last_day']}")
        p()
        p("  These are the entity's own records. None of them is tied to an ad by this module;")
        p("  quoting one beside ad spend as though it were caused by the ads is the single")
        p("  easiest way to turn this report into a wrong decision.")

        p()
        p("=" * 78)
        p("Read-only. Nothing was written.")

    return _emit(out, a.out)


def _emit(lines: list[str], path: str | None) -> int:
    body = "\n".join(lines)
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body + "\n")
        print(f"wrote {path} ({len(lines)} lines)")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
