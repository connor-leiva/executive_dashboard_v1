"""Phase 0 probe for the Meta Ads module (READ-ONLY). SPEC-ads-module.md Part 17.

WHY THIS EXISTS
    The spec had to reason around eleven facts it could not measure. Six of them (items 6 to 11)
    decide the SCHEMA, and every one has a wrong answer that is expensive to discover after the
    tables exist. "Do not write the funnel until this report exists."

    Items 1 to 5 are the ads side and need a Meta token. Items 6 to 11 are the funnel side and
    need only the database, so they run today, against whichever deployment you point this at.

WHAT IT ANSWERS
    ADS SIDE (needs META_ADS_TOKEN + META_ADS_ACCOUNT in backend/.probe.env)
      1  which action_type values actually appear          -> settles lead_actions
      2  how far clicks and inline_link_clicks diverge     -> sizes the CTR correction
      3  campaign / ad counts and pull duration            -> sync vs async backfill
      4  BUC usage after a full pull                       -> the app's access tier
      5  every distinct campaign name vs DEFAULT_GROUP_RULES

    FUNNEL SIDE (database only)
      6  UTM coverage on registrations                     -> the ceiling on everything
      7  does utm_content exist in GHL at all              -> gates ad-level attribution
      8  does bc_launch_opp carry contact_id               -> a Phase 2 prerequisite
      9  median and p90 lag registration -> close          -> ADS_ATTRIBUTION_WINDOW_DAYS
     10  attributable share of closes                      -> the structural ceiling, measured
     11  which stages are reliably dated                   -> sets `dated` per stage

MULTI-TENANT
    Everything is scoped by --tenant and every query filters on tenant_id. A probe that assumes
    one customer is the habit this whole module is being built to avoid, and a probe that reports
    one tenant's numbers under another's name is worse than no probe.

USAGE
    python -m ads_probe --tenant springb                 # funnel side only
    python -m ads_probe --tenant springb --ads           # both, needs .probe.env

    In production:
      railway ssh --service executive_dashboard_v1 "python -m ads_probe --tenant springb"

OUTPUT
    Prints a report. Writes nothing unless --out is given (ads_probe_out/, git-ignored).
    Never prints a token, a contact's email, or a person's name.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import statistics
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Business, Integration, MetricRecord, Tenant

OUT_DIR = Path(__file__).parent / "ads_probe_out"
META_SOURCES = {"meta", "facebook", "instagram", "fb", "ig"}


def h(title: str) -> None:
    print(f"\n{'=' * 78}\n  {title}\n{'=' * 78}")


def pct(n: int, d: int) -> str:
    return "n/a" if not d else f"{n / d * 100:5.1f}%  ({n}/{d})"


async def _tenant(s, slug: str) -> Tenant:
    t = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if t is None:
        have = (await s.execute(select(Tenant.slug))).scalars().all()
        raise SystemExit(f"[error] no tenant {slug!r}. Have: {', '.join(sorted(have)) or 'none'}")
    return t


async def _records(s, tid, kind: str) -> list[MetricRecord]:
    return list((await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tid, MetricRecord.kind == kind))).scalars().all())


# ── 6. UTM coverage on registrations ──────────────────────────────────────────────────
async def probe_utm_coverage(s, tid) -> dict:
    h("6. UTM COVERAGE ON REGISTRATIONS  (the ceiling on everything downstream)")
    regs = await _records(s, tid, "bc_shift_reg")
    if not regs:
        print("  no bc_shift_reg rows. Either no launch defines shift tags, or no sync has run.")
        return {"registrations": 0}

    have_src = sum(1 for r in regs if (r.meta or {}).get("utm_source"))
    have_cmp = sum(1 for r in regs if (r.meta or {}).get("utm_campaign"))
    have_med = sum(1 for r in regs if (r.meta or {}).get("utm_medium"))
    have_cid = sum(1 for r in regs if (r.meta or {}).get("contact_id"))
    meta_src = sum(1 for r in regs
                   if str((r.meta or {}).get("utm_source") or "").lower() in META_SOURCES)

    print(f"  registrations        {len(regs)}")
    print(f"  carry utm_source     {pct(have_src, len(regs))}")
    print(f"  carry utm_medium     {pct(have_med, len(regs))}")
    print(f"  carry utm_campaign   {pct(have_cmp, len(regs))}   <- the CAMPAIGN-grain ceiling")
    print(f"  carry contact_id     {pct(have_cid, len(regs))}   <- the identity spine")
    print(f"  utm_source is Meta   {pct(meta_src, len(regs))}")
    print(f"\n  channels: {dict(Counter((r.meta or {}).get('channel') for r in regs).most_common())}")
    print("\n  READ THIS AS: campaign-grain attribution can cover at most the utm_campaign share.")
    return {"registrations": len(regs), "utm_campaign": have_cmp, "meta_source": meta_src,
            "contact_id": have_cid}


# ── 7. does utm_content exist anywhere ────────────────────────────────────────────────
async def probe_utm_content(s, tid) -> dict:
    h("7. IS utm_content PRESENT?  (gates AD-level attribution entirely)")
    regs = await _records(s, tid, "bc_shift_reg")
    in_meta = sum(1 for r in regs if (r.meta or {}).get("utm_content"))
    print(f"  bc_shift_reg rows carrying utm_content: {in_meta} of {len(regs)}")
    if not in_meta:
        print("  -> NOT SYNCED. sync_becollective_ghl writes source/medium/campaign only.")
        print("     Ad-level revenue is impossible until BOTH of these land:")
        print("       a) a utm_content custom field in GHL, mapped into the sync's utm_ids")
        print("       b) utm_content={{ad.id}} written into the Meta ad URLs")
        print("     This is a CONFIGURATION project with a lead time, not a code task.")
        print("     Phase 5 slips until it lands. The creative wall must SAY this, not blank.")
    return {"utm_content_rows": in_meta}


# ── 8. does bc_launch_opp carry contact_id ────────────────────────────────────────────
async def probe_launch_opp_identity(s, tid) -> dict:
    h("8. DOES bc_launch_opp CARRY contact_id?  (Phase 2 prerequisite)")
    out = {}
    for kind in ("bc_launch_opp", "bc_recruiting", "bc_onboarded", "bc_membership"):
        rows = await _records(s, tid, kind)
        with_cid = sum(1 for r in rows if (r.meta or {}).get("contact_id"))
        out[kind] = {"rows": len(rows), "with_contact_id": with_cid}
        verdict = "OK" if rows and with_cid == len(rows) else (
            "MISSING - cannot be joined to an identity" if rows else "no rows")
        print(f"  {kind:16} {len(rows):>5} rows, {with_cid:>5} with contact_id   {verdict}")
    print("\n  Any kind showing MISSING cannot be attributed. Adding contact_id to that")
    print("  snapshot dict in sync_becollective_ghl is a one-line change.")
    return out


# ── 9. lag from registration to close ─────────────────────────────────────────────────
async def probe_close_lag(s, tid) -> dict:
    h("9. LAG: REGISTRATION -> CLOSE  (sets ADS_ATTRIBUTION_WINDOW_DAYS and the curve shape)")
    regs = await _records(s, tid, "bc_shift_reg")
    closes = await _records(s, tid, "bc_onboarded")
    reg_day = {}
    for r in regs:
        cid = (r.meta or {}).get("contact_id")
        if cid and r.occurred_on and (cid not in reg_day or r.occurred_on < reg_day[cid]):
            reg_day[cid] = r.occurred_on          # FIRST touch, matching the attribution rule

    lags = []
    for c in closes:
        cid = (c.meta or {}).get("contact_id")
        if cid and c.occurred_on and cid in reg_day:
            lags.append((c.occurred_on - reg_day[cid]).days)

    dated_regs = sum(1 for r in regs if r.occurred_on)
    print(f"  closes {len(closes)}, of which {len(lags)} have a DATED registration to measure from")
    if not lags:
        print("  -> NO MEASURABLE LAG.")
        if regs and not dated_regs:
            print(f"     CAUSE: none of the {len(regs)} registrations carry occurred_on. The")
            print("     contact `base` dict in sync_becollective_ghl sets no date, so bc_shift_reg")
            print("     rows are written undated. GHL contacts do carry `dateAdded`, and")
            print("     _parse_ghl_dt already exists - so this is a small change, but WHICH date")
            print("     is a real decision: dateAdded is when the identity first appeared, which")
            print("     is what a COHORT wants, but for someone already on the list it lands them")
            print("     in an ancient cohort rather than this one.")
        print("     Until it is fixed: no cohort day, no curve, no maturity, no projection.")
        print("     Phases 1-3 are unaffected. Phase 4 is blocked.")
        return {"lags": 0, "dated_registrations": dated_regs}
    lags.sort()
    p = lambda q: lags[min(len(lags) - 1, int(len(lags) * q))]   # noqa: E731
    print(f"  min {lags[0]}d   median {statistics.median(lags):.0f}d   "
          f"p90 {p(0.9)}d   max {lags[-1]}d")
    print(f"  negative lags (closed before registering): {sum(1 for l in lags if l < 0)}")
    print(f"\n  ADS_ATTRIBUTION_WINDOW_DAYS defaults to 90. Measured p90 is {p(0.9)}d.")
    if p(0.9) > 90:
        print("  -> p90 EXCEEDS the default. Widen the window or accept uncredited closes.")
        print("     That is a decision (Part 18 Q3), not a default.")
    return {"lags": len(lags), "median": statistics.median(lags), "p90": p(0.9)}


# ── 10. attributable share of closes ──────────────────────────────────────────────────
async def probe_attributable_ceiling(s, tid) -> dict:
    h("10. STRUCTURAL CEILING: WHAT SHARE OF CLOSES CAN EVER BE ATTRIBUTED?")
    regs = await _records(s, tid, "bc_shift_reg")
    closes = await _records(s, tid, "bc_onboarded")
    by_cid = {}
    for r in regs:
        cid = (r.meta or {}).get("contact_id")
        if cid:
            by_cid.setdefault(cid, r)

    n = len(closes)
    has_reg = sum(1 for c in closes if (c.meta or {}).get("contact_id") in by_cid)
    has_utm = sum(1 for c in closes
                  if (by_cid.get((c.meta or {}).get("contact_id")) or
                      type("x", (), {"meta": {}})()).meta.get("utm_campaign"))
    is_meta = sum(1 for c in closes
                  if str((by_cid.get((c.meta or {}).get("contact_id")) or
                          type("x", (), {"meta": {}})()).meta.get("utm_source") or "").lower()
                  in META_SOURCES)
    print(f"  closes                       {n}")
    print(f"  ...with a registration       {pct(has_reg, n)}")
    print(f"  ...whose reg has a campaign  {pct(has_utm, n)}   <- ATTRIBUTED CAC denominator")
    print(f"  ...whose reg is Meta-sourced {pct(is_meta, n)}   <- the ads number's real ceiling")
    print("\n  The remainder is word of mouth, the list, referrals, cross-device. It is a")
    print("  STRUCTURAL ceiling, not a failure - name it in the UI so nobody reads it as one.")
    return {"closes": n, "with_registration": has_reg, "with_campaign": has_utm, "meta": is_meta}


# ── 11. which stages are reliably dated ───────────────────────────────────────────────
async def probe_stage_dating(s, tid) -> dict:
    h("11. WHICH STAGES ARE RELIABLY DATED?  (sets `dated` per stage; gates every duration)")
    from app.models import SalesCall
    out = {}
    for label, kind in (("registered", "bc_shift_reg"), ("closed", "bc_onboarded"),
                        ("committed/applied (opps)", "bc_launch_opp")):
        rows = await _records(s, tid, kind)
        dated = sum(1 for r in rows if r.occurred_on)
        out[label] = {"rows": len(rows), "dated": dated}
        print(f"  {label:26} {pct(dated, len(rows))}")

    calls = list((await s.execute(select(SalesCall).where(
        SalesCall.tenant_id == tid))).scalars().all())
    booked = sum(1 for c in calls if c.call_time_utc)
    held = sum(1 for c in calls if c.outcome_at)
    print(f"  {'booked (SalesCall.call_time)':26} {pct(booked, len(calls))}")
    print(f"  {'held (SalesCall.outcome_at)':26} {pct(held, len(calls))}")
    print("\n  A stage below ~95% dated gets dated=False: COUNTED in the funnel, excluded from")
    print("  every duration and from the curve fit. Counting it is honest; timing it is not.")
    out["booked"] = {"rows": len(calls), "dated": booked}
    out["held"] = {"rows": len(calls), "dated": held}
    return out


async def run(slug: str, do_ads: bool, out: bool) -> None:
    async with SessionLocal() as s:
        t = await _tenant(s, slug)
        biz = list((await s.execute(select(Business).where(
            Business.tenant_id == t.id).order_by(Business.sort_order))).scalars().all())
        integs = list((await s.execute(select(Integration).where(
            Integration.tenant_id == t.id))).scalars().all())

        h(f"ADS MODULE PHASE 0 PROBE  -  tenant {t.slug} / {t.name}")
        url = os.environ.get("DATABASE_URL", "")
        print(f"  database    {'SQLite (local)' if url.startswith('sqlite') or not url else 'Postgres'}")
        print(f"  businesses  {', '.join(f'{b.key}[{b.kind}]' for b in biz) or 'none'}")
        print(f"  providers   {', '.join(sorted({i.provider for i in integs})) or 'none'}")

        report = {"tenant": t.slug}
        report["utm_coverage"] = await probe_utm_coverage(s, t.id)
        report["utm_content"] = await probe_utm_content(s, t.id)
        report["identity"] = await probe_launch_opp_identity(s, t.id)
        report["lag"] = await probe_close_lag(s, t.id)
        report["ceiling"] = await probe_attributable_ceiling(s, t.id)
        report["dating"] = await probe_stage_dating(s, t.id)

        if do_ads:
            h("1-5. ADS SIDE")
            if not os.environ.get("META_ADS_TOKEN"):
                print("  META_ADS_TOKEN not set. Put it in backend/.probe.env (git-ignored).")
                print("  Skipping items 1 to 5; the funnel side above is complete and is the")
                print("  half that decides the schema.")
            else:
                print("  [not yet implemented - lands with the meta_ads client in Phase 1]")

        h("WHAT THIS DECIDES")
        print("  6/10 -> the honest ceiling on attributed CAC. Put it in the UI, not a footnote.")
        print("  7    -> whether Phase 5 (creative revenue) is buildable at all yet.")
        print("  8    -> whether a one-line sync change blocks Phase 2.")
        print("  9    -> ADS_ATTRIBUTION_WINDOW_DAYS, and whether 90 is right for THIS tenant.")
        print("  11   -> which stages may carry a duration. Everything else is counted only.")

        if out:
            OUT_DIR.mkdir(exist_ok=True)
            p = OUT_DIR / f"probe_{t.slug}.json"
            p.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
            print(f"\n  written: {p}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tenant", required=True, help="tenant slug - this probe is never global")
    ap.add_argument("--ads", action="store_true", help="also run items 1-5 (needs a Meta token)")
    ap.add_argument("--out", action="store_true", help=f"write JSON to {OUT_DIR.name}/")
    a = ap.parse_args()
    asyncio.run(run(a.tenant, a.ads, a.out))


if __name__ == "__main__":
    main()
