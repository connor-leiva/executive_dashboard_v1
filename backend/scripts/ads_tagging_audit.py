r"""Is the tracking chain tagged AT THE SOURCE? READ-ONLY.

Step one of any attribution chain is the ad's own URL. If an ad sends traffic to a page with no
utm_* parameters on it, nothing downstream can recover where that person came from - not the
landing page, not the CRM, not any dashboard. Auditing the CRM first, in that situation, is
auditing the wrong end of a broken wire.

We already sync `ad.url_tags`: the literal URL Parameters string set on each ad in Meta Ads
Manager. So this question is answerable here, now, without touching GHL or Meta.

The comparison is the point. One campaign in this account demonstrably works end to end - its
registrants carry a utm_campaign that resolves to a campaign name - so it is the reference. Any
campaign whose ads are tagged differently is a measurable distance from a thing already known
to work in the same account.

Same guarantees as the other probes: SELECT-only, autocommit, Postgres required, no secrets and
no personal data. url_tags are campaign templates, not personal data.

USAGE (PowerShell, from backend\)
    $env:DATABASE_URL = '<DATABASE_PUBLIC_URL>'
    & .\.venv\Scripts\python.exe scripts\ads_tagging_audit.py --tenant springb --out ads_probe_out\tagging.txt
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import create_engine, text

_SELECT = re.compile(r"^\s*(select|with)\b", re.I)
_UTMS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")


def _normalise(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _q(conn, sql: str, **p):
    if not _SELECT.match(sql):
        raise RuntimeError("refused: SELECT only")
    return conn.execute(text(sql), p).mappings().all()


def _parse(tags):
    if not tags:
        return {}
    try:
        return {k.lower(): v for k, v in parse_qsl(str(tags).lstrip("?&"), keep_blank_values=True)}
    except (ValueError, TypeError):
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()

    raw = os.environ.get("DATABASE_URL", "")
    if not raw or "sqlite" in raw or not raw.startswith(("postgres://", "postgresql://")):
        print("Set DATABASE_URL to the Postgres DATABASE_PUBLIC_URL. Refusing to guess.",
              file=sys.stderr)
        return 2

    out: list[str] = []
    p = out.append
    engine = create_engine(_normalise(raw), isolation_level="AUTOCOMMIT",
                           connect_args={"connect_timeout": 20})
    with engine.connect() as c:
        pr = urlsplit(raw)
        p("AD TAGGING AUDIT — what the ads themselves carry")
        p(f"database: {pr.hostname}:{pr.port or 5432}{pr.path}")
        p("=" * 78)
        tid = _q(c, "select id from tenant where slug = :s", s=a.tenant)[0]["id"]

        ads = _q(c, """
            select coalesce(ac.name, '— no campaign —') as campaign,
                   ad.name, ad.effective_status, ad.url_tags,
                   ad.first_seen_on, ad.last_seen_on
              from ad
              left join ad_campaign ac on ac.id = ad.campaign_id
             where ad.tenant_id = :t
             order by ac.name nulls last, ad.last_seen_on desc nulls last, ad.name
        """, t=tid)

        # ── 1. Per campaign: how many ads carry anything at all ──────────────────────────
        p("\n1. TAGGING COVERAGE per campaign")
        p("-" * 78)
        by_camp: dict[str, list] = {}
        for r in ads:
            by_camp.setdefault(r["campaign"], []).append(r)
        p(f"  {'campaign':<34}{'ads':>5}{'tagged':>8}{'campaign':>10}{'source':>8}{'content':>9}")
        for camp, rows in sorted(by_camp.items(), key=lambda kv: -len(kv[1])):
            parsed = [_parse(r["url_tags"]) for r in rows]
            tagged = sum(1 for d in parsed if d)
            has = {k: sum(1 for d in parsed if (d.get(k) or "").strip()) for k in _UTMS}
            p(f"  {camp[:33]:<34}{len(rows):>5}{tagged:>8}{has['utm_campaign']:>10}"
              f"{has['utm_source']:>8}{has['utm_content']:>9}")
        p("\n  'tagged' = the ad carries ANY URL parameters. The three columns after it are how")
        p("  many carry that specific utm_* key with a non-empty value.")

        # ── 2. The literal strings, which is where the answer actually is ───────────────
        p("\n2. THE DISTINCT URL PARAMETER STRINGS, per campaign")
        p("-" * 78)
        for camp, rows in sorted(by_camp.items()):
            p(f"\n  {camp}")
            seen: dict[str, int] = {}
            for r in rows:
                key = (r["url_tags"] or "— NONE —").strip()
                seen[key] = seen.get(key, 0) + 1
            for tags, n in sorted(seen.items(), key=lambda kv: -kv[1]):
                p(f"    x{n:<3} {tags[:300]}")

        # ── 3. Live ads with nothing on them — the actionable list ──────────────────────
        p("\n3. CURRENTLY ACTIVE ADS CARRYING NO UTM AT ALL")
        p("-" * 78)
        naked = [r for r in ads
                 if (r["effective_status"] or "").upper() == "ACTIVE" and not _parse(r["url_tags"])]
        if not naked:
            p("  none — every active ad carries at least one parameter")
        for r in naked:
            p(f"    {r['campaign'][:30]:<32} {(r['name'] or '')[:44]:<46} "
              f"last seen {r['last_seen_on']}")

        # ── 4. Does the CRM side actually hold what the tags promise? ───────────────────
        p("\n4. WHAT ARRIVED AT THE OTHER END (ad_attribution, by grade)")
        p("-" * 78)
        grades = _q(c, """
            select coalesce(ac.name, '— channel only, no campaign —') as campaign,
                   a.match_method, a.confidence, count(*) as n,
                   count(a.utm_campaign) as with_campaign,
                   count(a.utm_content) as with_content,
                   count(a.utm_source) as with_source
              from ad_attribution a
              left join ad_campaign ac on ac.id = a.campaign_id
             where a.tenant_id = :t
             group by ac.name, a.match_method, a.confidence
             order by count(*) desc
        """, t=tid)
        if not grades:
            p("  no attribution rows at all")
        for r in grades:
            p(f"    {r['n']:>5}  {r['match_method']:<9} {r['campaign'][:40]:<42} "
              f"utm_campaign {r['with_campaign']:>4} · utm_source {r['with_source']:>4} "
              f"· utm_content {r['with_content']:>4}")
        p("\n  These rows are written ONLY from beCollective Shift registrants, so a campaign")
        p("  for any other entity is absent here even when its ads are tagged perfectly. Read")
        p("  this as 'the tags that did survive the whole chain', not as a scoreboard.")

        p("\n" + "=" * 78)
        p("Read-only. Nothing was written.")

    body = "\n".join(out)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(body + "\n")
        print(f"wrote {a.out}")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
