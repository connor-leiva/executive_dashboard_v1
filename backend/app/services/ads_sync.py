"""Pull Meta delivery into ad_insight_daily. SPEC-ads-module.md Part 8.

RESTATEMENT IS AN UPDATE. Meta revises recent days as attribution settles, so a sync that only
wrote today would freeze every prior day at its first, understated value. A rolling window is
re-pulled each tick and upserted against uq_ad_insight_day. If you find yourself writing
delete-then-insert here, the constraint is wrong - and delete-then-insert is exactly how the
scorecard erased weeks it had already collected correctly.

TIMEZONE. Every date boundary uses the AD ACCOUNT's reporting timezone, because Meta's day
boundaries do. A naive UTC date puts spend on the wrong day either side of midnight and makes a
range disagree with Ads Manager for reasons nobody can find.

MULTI-TENANT. Every read and write filters on tenant_id, and an account is only ever reached
through its own tenant's integration row. run_all iterates tenants, so this is called once per
workspace with that workspace's token.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..integrations import meta_ads as meta
from ..models import Ad, AdAccount, AdCampaign, AdInsightDaily, Integration
from ..security import dec
from .ads import account_today, resolve_leads

# Meta emits several lead-shaped types. This is the default until Phase 0's item 1 measures which
# ones an account actually produces; it is overridable per account via ad_account.lead_actions.
DEFAULT_LEAD_ACTIONS = ["lead", "offsite_conversion.fb_pixel_lead", "onsite_conversion.lead_grouped"]


def _dec(v) -> Decimal:
    try:
        return Decimal(str(v or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _int(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _day(v) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


async def _upsert_campaigns(s: AsyncSession, tenant_id, acct: AdAccount, rows: list[dict]) -> int:
    existing = {c.external_id: c for c in (await s.execute(select(AdCampaign).where(
        AdCampaign.tenant_id == tenant_id, AdCampaign.ad_account_id == acct.id))).scalars()}
    n = 0
    for r in rows:
        ext = str(r.get("id") or "")
        if not ext:
            continue
        c = existing.get(ext)
        if c is None:
            c = AdCampaign(tenant_id=tenant_id, ad_account_id=acct.id, external_id=ext,
                           name=str(r.get("name") or ext)[:400])
            s.add(c)
            existing[ext] = c
        c.name = str(r.get("name") or c.name)[:400]
        c.objective = (r.get("objective") or None)
        c.status = (r.get("status") or None)
        c.effective_status = (r.get("effective_status") or None)
        c.daily_budget = meta.minor_to_decimal(r.get("daily_budget"), acct.currency)
        c.lifetime_budget = meta.minor_to_decimal(r.get("lifetime_budget"), acct.currency)
        n += 1
    await s.flush()
    return n


async def _upsert_ads(s: AsyncSession, tenant_id, acct: AdAccount, rows: list[dict]) -> int:
    camp_by_ext = {c.external_id: c for c in (await s.execute(select(AdCampaign).where(
        AdCampaign.tenant_id == tenant_id, AdCampaign.ad_account_id == acct.id))).scalars()}
    existing = {a.external_id: a for a in (await s.execute(select(Ad).where(
        Ad.tenant_id == tenant_id, Ad.ad_account_id == acct.id))).scalars()}
    now = dt.datetime.now(dt.timezone.utc)
    n = 0
    for r in rows:
        ext = str(r.get("id") or "")
        if not ext:
            continue
        a = existing.get(ext)
        if a is None:
            a = Ad(tenant_id=tenant_id, ad_account_id=acct.id, external_id=ext,
                   name=str(r.get("name") or ext)[:400])
            s.add(a)
            existing[ext] = a
        creative = r.get("creative") or {}
        adset = r.get("adset") or {}
        a.name = str(r.get("name") or a.name)[:400]
        a.status = (r.get("status") or None)
        a.effective_status = (r.get("effective_status") or None)
        a.adset_external_id = (adset.get("id") or None)
        a.adset_name = (str(adset.get("name"))[:400] if adset.get("name") else None)
        camp_ext = str((r.get("campaign") or {}).get("id") or "")
        if camp_ext and camp_ext in camp_by_ext:
            a.campaign_id = camp_by_ext[camp_ext].id
        a.creative_external_id = (creative.get("id") or None)
        a.headline = meta.creative_headline(creative)
        a.body = (creative.get("body") or None)
        # Signed and short lived: refreshed every sync, never treated as a permalink.
        a.thumbnail_url = meta.creative_thumb(creative)
        a.image_hash = (creative.get("image_hash") or None)
        a.url_tags = (creative.get("url_tags") or None)
        a.creative_fetched_at = now
        n += 1
    await s.flush()
    return n


async def _upsert_insights(s: AsyncSession, tenant_id, acct: AdAccount, level: str,
                           rows: list[dict], lead_actions: list[str]) -> tuple[int, int]:
    """Upsert daily rows. Returns (written, restated).

    Restated is counted and reported because it is the signal that Meta moved a number under
    somebody: a big restatement count is worth knowing about, not worth hiding.
    """
    camp_by_ext = {c.external_id: c for c in (await s.execute(select(AdCampaign).where(
        AdCampaign.tenant_id == tenant_id, AdCampaign.ad_account_id == acct.id))).scalars()}
    ad_by_ext = {a.external_id: a for a in (await s.execute(select(Ad).where(
        Ad.tenant_id == tenant_id, Ad.ad_account_id == acct.id))).scalars()}

    existing = {(r.object_external_id, r.occurred_on): r
                for r in (await s.execute(select(AdInsightDaily).where(
                    AdInsightDaily.tenant_id == tenant_id,
                    AdInsightDaily.ad_account_id == acct.id,
                    AdInsightDaily.level == level))).scalars()}

    now = dt.datetime.now(dt.timezone.utc)
    written = restated = 0
    for r in rows:
        day = _day(r.get("date_start"))
        obj = str(r.get("ad_id") if level == "ad" else r.get("campaign_id") or "")
        if not day or not obj:
            continue
        row = existing.get((obj, day))
        if row is None:
            row = AdInsightDaily(tenant_id=tenant_id, ad_account_id=acct.id, level=level,
                                 object_external_id=obj, occurred_on=day)
            s.add(row)
            existing[(obj, day)] = row
        else:
            restated += 1
        camp_ext = str(r.get("campaign_id") or "")
        row.campaign_id = camp_by_ext[camp_ext].id if camp_ext in camp_by_ext else None
        row.ad_id = ad_by_ext[obj].id if (level == "ad" and obj in ad_by_ext) else None
        row.spend = _dec(r.get("spend"))
        row.impressions = _int(r.get("impressions"))
        # reach and frequency go null past Meta's retention. Null is NOT zero, and storing zero
        # would make an old month look like it reached nobody.
        row.reach = _int(r["reach"]) if r.get("reach") not in (None, "") else None
        row.clicks = _int(r.get("clicks"))
        row.inline_link_clicks = _int(r.get("inline_link_clicks"))
        row.frequency = _dec(r["frequency"]) if r.get("frequency") not in (None, "") else None
        row.actions = r.get("actions")
        row.action_values = r.get("action_values")
        row.leads = resolve_leads(r.get("actions"), lead_actions)
        pr = r.get("purchase_roas")
        row.purchase_roas = _dec(pr[0].get("value")) if isinstance(pr, list) and pr else None
        row.attribution = "account_default"
        row.pulled_at = now
        written += 1
    await s.flush()
    return written, restated


async def _stamp_seen(s: AsyncSession, tenant_id, acct: AdAccount) -> None:
    """first_seen_on / last_seen_on come from INSIGHT rows, not the dimension: a campaign can be
    ACTIVE and have delivered nothing for a month, and "spending" is a delivery question."""
    for model, level, fk in ((AdCampaign, "campaign", AdInsightDaily.campaign_id),
                             (Ad, "ad", AdInsightDaily.ad_id)):
        objs = list((await s.execute(select(model).where(
            model.tenant_id == tenant_id, model.ad_account_id == acct.id))).scalars())
        if not objs:
            continue
        rows = list((await s.execute(select(AdInsightDaily.occurred_on, fk).where(
            AdInsightDaily.tenant_id == tenant_id, AdInsightDaily.ad_account_id == acct.id,
            AdInsightDaily.level == level, AdInsightDaily.spend > 0))).all())
        seen: dict = {}
        for day, oid in rows:
            if oid is None:
                continue
            lo, hi = seen.get(oid, (day, day))
            seen[oid] = (min(lo, day), max(hi, day))
        for o in objs:
            if o.id in seen:
                o.first_seen_on, o.last_seen_on = seen[o.id]
    await s.flush()


async def _refresh_creatives(s: AsyncSession, tenant_id, acct: AdAccount, token: str) -> int:
    """Thumbnails and url_tags for the highest-spending ads, one request each.

    Capped by ADS_MAX_CREATIVE_HOPS and ordered by spend, because the creative wall shows a
    couple of dozen and there is no reason to buy detail for an ad nobody will look at.
    """
    spend_by_ad: dict = {}
    for r in (await s.execute(select(AdInsightDaily).where(
            AdInsightDaily.tenant_id == tenant_id, AdInsightDaily.ad_account_id == acct.id,
            AdInsightDaily.level == "ad"))).scalars():
        if r.ad_id:
            spend_by_ad[r.ad_id] = spend_by_ad.get(r.ad_id, 0) + float(r.spend or 0)

    ads_by_id = {a.id: a for a in (await s.execute(select(Ad).where(
        Ad.tenant_id == tenant_id, Ad.ad_account_id == acct.id))).scalars()}
    ranked = sorted(spend_by_ad, key=lambda k: -spend_by_ad[k])[:settings.ADS_MAX_CREATIVE_HOPS]
    ext_ids = [ads_by_id[i].external_id for i in ranked if i in ads_by_id]
    if not ext_ids:
        return 0

    creatives = await meta.ad_creatives(token, ext_ids)
    now = dt.datetime.now(dt.timezone.utc)
    n = 0
    for a in ads_by_id.values():
        c = creatives.get(a.external_id)
        if not c:
            continue
        a.creative_external_id = c.get("id") or a.creative_external_id
        a.headline = meta.creative_headline(c) or a.headline
        a.body = c.get("body") or a.body
        a.thumbnail_url = meta.creative_thumb(c) or a.thumbnail_url
        a.image_hash = c.get("image_hash") or a.image_hash
        a.url_tags = c.get("url_tags") or a.url_tags
        a.creative_fetched_at = now
        n += 1
    await s.commit()
    print(f"[meta_ads] {acct.external_id}: {n} creatives refreshed", flush=True)
    return n


async def sync_meta_ads(s: AsyncSession, tenant_id, integ: Integration) -> dict:
    """Pull every ad account under one Integration. Returns a per-account summary.

    An account that fails records its own last_error and the others still sync: one revoked
    token on one account should not blank a tenant's whole ads tab.
    """
    token = dec(integ.access_token_enc) if integ.access_token_enc else None
    if not token:
        return {"skipped": "no token"}

    accounts = list((await s.execute(select(AdAccount).where(
        AdAccount.tenant_id == tenant_id, AdAccount.integration_id == integ.id,
        AdAccount.status == "active"))).scalars())
    out: dict = {"accounts": len(accounts), "results": []}

    for acct in accounts:
        today = account_today(acct.timezone_name)
        since = today - dt.timedelta(days=max(1, settings.ADS_REFRESH_DAYS) - 1)
        lead_actions = list(acct.lead_actions or DEFAULT_LEAD_ACTIONS)
        step = "starting"
        try:
            # Dimensions FIRST - insight rows carry foreign keys to these - and COMMITTED before
            # the insight pull. The first live sync failed inside ads(), the handler rolled back,
            # and the campaigns already fetched went with it: the account showed zero campaigns
            # and zero ads, which reads as "nothing is running" rather than "one call failed".
            # Partial progress is worth keeping.
            #
            # `step` is threaded through so last_error NAMES the call that failed. The first live
            # failure read "Please reduce the amount of data you're asking for" with no indication
            # of which of four requests said it, which turned a one-line diagnosis into guesswork.
            step = "campaigns"
            n_camp = await _upsert_campaigns(s, tenant_id, acct, await meta.campaigns(token, acct.external_id))
            await s.commit()
            step = "ads"
            n_ads = await _upsert_ads(s, tenant_id, acct, await meta.ads(token, acct.external_id))
            await s.commit()
            step = "insights(campaign)"
            w_c, r_c = await _upsert_insights(
                s, tenant_id, acct, "campaign",
                await meta.insights(token, acct.external_id, "campaign", since, today), lead_actions)
            step = "insights(ad)"
            w_a, r_a = await _upsert_insights(
                s, tenant_id, acct, "ad",
                await meta.insights(token, acct.external_id, "ad", since, today), lead_actions)
            step = "stamp"
            await _stamp_seen(s, tenant_id, acct)
            await s.commit()

            # Creative enrichment, ISOLATED. Thumbnails and url_tags are worth a bounded number
            # of requests and are worth nothing at the cost of the spend figures, so a failure
            # here is logged and swallowed rather than failing the account.
            try:
                await _refresh_creatives(s, tenant_id, acct, token)
            except Exception as ce:  # noqa: BLE001
                print(f"[meta_ads] {acct.external_id} creatives skipped: "
                      f"{type(ce).__name__}: {ce}", flush=True)
            acct.last_synced_at = dt.datetime.now(dt.timezone.utc)
            acct.last_error = None
            await s.commit()
            out["results"].append({"account": acct.external_id, "campaigns": n_camp, "ads": n_ads,
                                   "insight_rows": w_c + w_a, "restated": r_c + r_a,
                                   "window": [since.isoformat(), today.isoformat()]})
            print(f"[meta_ads] {acct.external_id}: {n_camp} campaigns, {n_ads} ads, "
                  f"{w_c + w_a} day-rows ({r_c + r_a} restated)", flush=True)
        except Exception as e:                      # noqa: BLE001 - one account must not blank the rest
            await s.rollback()
            acct.last_error = f"[{step}] {type(e).__name__}: {e}"[:500]
            await s.commit()
            out["results"].append({"account": acct.external_id, "error": acct.last_error})
            print(f"[meta_ads] {acct.external_id} FAILED: {acct.last_error}", flush=True)
    return out


async def backfill_meta_ads(s: AsyncSession, tenant_id, ad_account_id, since: dt.date,
                            until: dt.date) -> dict:
    """Month by month, OLDEST FIRST, so a partial backfill leaves a contiguous history with a
    known start rather than a hole in the middle that nothing will ever notice."""
    acct = (await s.execute(select(AdAccount).where(
        AdAccount.tenant_id == tenant_id, AdAccount.id == ad_account_id))).scalar_one_or_none()
    if acct is None:
        return {"error": "unknown account"}
    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.id == acct.integration_id))).scalar_one_or_none()
    token = dec(integ.access_token_enc) if (integ and integ.access_token_enc) else None
    if not token:
        return {"error": "no token"}

    lead_actions = list(acct.lead_actions or DEFAULT_LEAD_ACTIONS)
    months, cursor, total = [], since, 0
    while cursor <= until:
        nxt = (cursor.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
        months.append((cursor, min(until, nxt - dt.timedelta(days=1))))
        cursor = nxt
    for m_start, m_end in months:
        for level in ("campaign", "ad"):
            rows = await meta.insights(token, acct.external_id, level, m_start, m_end)
            w, _ = await _upsert_insights(s, tenant_id, acct, level, rows, lead_actions)
            total += w
        await s.commit()
        print(f"[meta_ads backfill] {acct.external_id} {m_start}..{m_end}: {total} rows", flush=True)
    acct.backfill_start = min(acct.backfill_start or since, since)
    await s.commit()
    await _stamp_seen(s, tenant_id, acct)
    await s.commit()
    return {"account": acct.external_id, "rows": total, "months": len(months)}
