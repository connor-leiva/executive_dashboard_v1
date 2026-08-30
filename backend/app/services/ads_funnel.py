"""The attribution spine. SPEC-ads-module.md Parts 4.3, 4.4 and 9.2.

WRITE ONCE, AND THIS IS THE WHOLE POINT OF THE TABLE.

GHL records are written by _ghl_snapshot, which REPLACES the row set every sync, so a contact's
UTM is CURRENT STATE and not history. Re-deriving attribution on each sync would move closed
revenue between campaigns weeks after the fact, and nobody would be able to say why last month's
report changed. An identity that already has a row gets its last_seen_on moved and nothing else -
ever.

The Sales Desk hit exactly this and answered it the same way: its own append-only log, with every
rate computed from that log rather than from live GHL fields.

MATCH QUALITY IS A FIRST-CLASS FIELD. Three grades, and they must never be presented as one
thing. A campaign-grade match may not grant ad-level revenue, and a channel-grade match may not
grant campaign-level revenue. The read service enforces the grain rather than trusting callers,
and `ad_id` is NULL unless match_method == "ad".

MULTI-TENANT. Every query filters on tenant_id, campaigns and ads are resolved only within the
tenant's own accounts, and the uniqueness key is (tenant_id, identity_kind, identity_key) - so
two workspaces holding the same GHL contact id are two independent rows.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Ad, AdAccount, AdAttribution, AdCampaign, MetricRecord
from .launch import SHIFT_SRC_CHANNEL

# Sources that mean Meta. Shared with classify_shift_source's vocabulary deliberately: one
# definition of "this came from Meta", so the ads tab and the launch tab cannot disagree.
META_SOURCES = {k for k, v in SHIFT_SRC_CHANNEL.items() if v == "Meta"} or {
    "meta", "facebook", "instagram", "fb", "ig"}

# Grades, strictest first. The order IS the resolution order.
MATCH_AD, MATCH_CAMPAIGN, MATCH_CHANNEL = "ad", "campaign", "channel"


def _norm(s: str | None) -> str:
    return " ".join(str(s or "").lower().split())


def resolve_match(utm: dict, ads_by_ext: dict, campaigns_by_name: dict) -> dict | None:
    """The grade this identity's UTM entitles it to, strictest first. None when it entitles
    nothing - and an unattributed identity gets NO ROW, so the read service counts it from the
    source population rather than from a placeholder that would need explaining forever.

    Pure, so the whole resolution order is testable without a database.
    """
    content = str(utm.get("utm_content") or "").strip()
    campaign = _norm(utm.get("utm_campaign"))
    source = str(utm.get("utm_source") or "").strip().lower()

    # 1. utm_content resolves to a known ad -> ad grade. Both FKs set; this is the only grade
    #    that may set ad_id at all.
    if content and content in ads_by_ext:
        ad = ads_by_ext[content]
        return {"match_method": MATCH_AD, "confidence": "exact",
                "ad_id": ad.id, "campaign_id": ad.campaign_id, "ad_account_id": ad.ad_account_id}

    # 2. utm_campaign matches a campaign name -> campaign grade. ad_id stays NULL, and there is
    #    a test asserting the read service never infers an ad from this.
    if campaign and campaign in campaigns_by_name:
        c = campaigns_by_name[campaign]
        return {"match_method": MATCH_CAMPAIGN, "confidence": "probable",
                "ad_id": None, "campaign_id": c.id, "ad_account_id": c.ad_account_id}

    # 3. A Meta source and nothing finer -> channel grade. Both FKs NULL: this identity may be
    #    counted in channel totals and must never appear in a campaign's revenue.
    if source in META_SOURCES:
        return {"match_method": MATCH_CHANNEL, "confidence": "channel",
                "ad_id": None, "campaign_id": None, "ad_account_id": None}

    return None


async def sync_ad_attribution(s: AsyncSession, tenant_id) -> dict:
    """Write first-touch rows for identities that arrived carrying ad-bearing UTM.

    INSERT ONLY. The single update ever made to an existing row is last_seen_on.
    """
    accounts = list((await s.execute(select(AdAccount).where(
        AdAccount.tenant_id == tenant_id))).scalars())
    if not accounts:
        return {"skipped": "no ad account connected"}
    acct_ids = {a.id for a in accounts}
    default_biz = next((a.business_id for a in accounts if a.business_id), None)

    ads_by_ext = {a.external_id: a for a in (await s.execute(select(Ad).where(
        Ad.tenant_id == tenant_id, Ad.ad_account_id.in_(acct_ids)))).scalars()}
    campaigns_by_name = {_norm(c.name): c for c in (await s.execute(select(AdCampaign).where(
        AdCampaign.tenant_id == tenant_id, AdCampaign.ad_account_id.in_(acct_ids)))).scalars()}

    regs = list((await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id,
        MetricRecord.kind == "bc_shift_reg"))).scalars())

    existing = {a.identity_key: a for a in (await s.execute(select(AdAttribution).where(
        AdAttribution.tenant_id == tenant_id,
        AdAttribution.identity_kind == "ghl_contact"))).scalars()}

    stats = {"registrations": len(regs), "written": 0, "touched": 0,
             "undated": 0, "unattributed": 0, "by_grade": {}}

    # First touch means EARLIEST, so process oldest first. Without the sort, whichever row the
    # database happened to return first would become the frozen answer.
    for r in sorted(regs, key=lambda x: (x.occurred_on or dt.date.max)):
        meta = r.meta or {}
        cid = str(meta.get("contact_id") or "").strip()
        if not cid:
            continue

        day = r.occurred_on
        if day is None:
            # A registration with no date has no cohort day, and first_seen_on IS the cohort day.
            # Counted and skipped rather than dated with today's date, which would put the
            # identity in whatever cohort the worker happened to tick in. Self-correcting: the
            # next sync rewrites these rows carrying dateAdded.
            stats["undated"] += 1
            continue

        row = existing.get(cid)
        if row is not None:
            # THE WRITE-ONCE RULE. Only last_seen_on moves. Re-deriving the campaign here is what
            # would silently relocate closed revenue weeks after the fact.
            if day > row.last_seen_on:
                row.last_seen_on = day
                stats["touched"] += 1
            continue

        utm = {"utm_source": meta.get("utm_source"), "utm_medium": meta.get("utm_medium"),
               "utm_campaign": meta.get("utm_campaign"), "utm_content": meta.get("utm_content")}
        match = resolve_match(utm, ads_by_ext, campaigns_by_name)
        if match is None:
            stats["unattributed"] += 1
            continue

        row = AdAttribution(
            tenant_id=tenant_id, identity_kind="ghl_contact", identity_key=cid,
            email_norm=(str(r.email).strip().lower() if r.email else None),
            business_id=r.business_id or default_biz,
            ad_account_id=match["ad_account_id"], campaign_id=match["campaign_id"],
            ad_id=match["ad_id"],
            utm_source=meta.get("utm_source"), utm_medium=meta.get("utm_medium"),
            utm_campaign=meta.get("utm_campaign"), utm_content=meta.get("utm_content"),
            channel=str(meta.get("channel") or "Meta")[:24],
            match_method=match["match_method"], confidence=match["confidence"],
            first_seen_on=day, last_seen_on=day)
        s.add(row)
        existing[cid] = row
        stats["written"] += 1
        stats["by_grade"][match["match_method"]] = stats["by_grade"].get(match["match_method"], 0) + 1

    await s.commit()
    print(f"[ads_attr] {stats['written']} written, {stats['touched']} touched, "
          f"{stats['unattributed']} unattributed, {stats['undated']} undated "
          f"(grades: {stats['by_grade']})", flush=True)
    return stats


async def attribution_coverage(s: AsyncSession, tenant_id, start: dt.date, end: dt.date) -> dict:
    """How many registrations and closes sit at each grade, for the window.

    Reported explicitly because a rising channel-only share is the leading indicator that the
    tagging is degrading - the moment before the whole number stops being trustworthy. Also
    carries the two denominators side by side: Meta's own lead count and Acumyn's matched
    registrations are two systems counting overlapping populations, and adding them is forbidden
    (Part 4.8). The UI shows the gap and names it rather than implying one is a shortfall of the
    other.
    """
    attrs = list((await s.execute(select(AdAttribution).where(
        AdAttribution.tenant_id == tenant_id,
        AdAttribution.first_seen_on >= start,
        AdAttribution.first_seen_on <= end))).scalars())

    regs = list((await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.kind == "bc_shift_reg",
        MetricRecord.occurred_on >= start, MetricRecord.occurred_on <= end))).scalars())

    by_grade: dict[str, int] = {}
    for a in attrs:
        by_grade[a.match_method] = by_grade.get(a.match_method, 0) + 1

    matched = len(attrs)
    total = len(regs)
    return {
        "registrations_total": total,
        "registrations_matched": matched,
        "registrations_unattributed": max(0, total - matched),
        "by_grade": by_grade,
        # The ceiling on what any campaign-level number can cover. Named, because a reader who
        # does not know it will read the gap as a failure rather than a structural limit.
        "campaign_grade_or_better": by_grade.get(MATCH_AD, 0) + by_grade.get(MATCH_CAMPAIGN, 0),
        "ad_grade": by_grade.get(MATCH_AD, 0),
    }
