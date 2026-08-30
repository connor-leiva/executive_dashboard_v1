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

from decimal import Decimal

from ..models import (Ad, AdAccount, AdAttribution, AdCampaign, AdConversion, MetricRecord,
                      SalesCall)
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


# ── the funnel (Phase 3) ──────────────────────────────────────────────────────────────
#
# Code default per archetype, per-account override on ad_account.funnel_override. Same shape as
# GROUP_RULES and DEFAULT_STAGE_MAP: a default that ships correct, an override that is data.
#
# `diagnostic` marks a rung that is MEASURED but is not the owned funnel. Meta's lead count is a
# useful cross-check and is never the denominator for anything downstream.
FUNNEL_DEFS = {
    "program": [
        {"key": "impression", "label": "Impressions", "src": "ads", "zone": "meta"},
        {"key": "click", "label": "Link clicks", "src": "ads", "zone": "meta"},
        {"key": "lead", "label": "Leads - Meta", "src": "ads", "zone": "meta", "diagnostic": True},
        {"key": "registered", "label": "Registered", "src": "bc_shift_reg", "zone": "acumyn"},
        {"key": "booked", "label": "Call booked", "src": "sales_call", "zone": "acumyn"},
        {"key": "applied", "label": "Applied", "src": "stage_group", "zone": "acumyn"},
        {"key": "held", "label": "Call held", "src": "sales_call", "zone": "acumyn"},
        {"key": "committed", "label": "Cash received", "src": "stage_group", "zone": "acumyn"},
        {"key": "closed", "label": "Enrolled", "src": "bc_onboarded", "zone": "acumyn",
         "closes": True},
    ],
}

ACUMYN_STAGES = ("registered", "booked", "applied", "held", "committed", "closed")
HELD_OUTCOMES = ("showed", "held", "attended")


def _annualize(amount, payment_type):
    """A rolling monthly membership has no signed annual figure, so twelve months is a MODELLING
    CHOICE and is flagged as one. Reporting it unflagged turns a projection into a fact."""
    if amount is None:
        return None, False
    amt = Decimal(str(amount))
    if str(payment_type or "").lower() in ("monthly", "rolling"):
        return amt * 12, True
    return amt, False


async def sync_ad_conversions(s: AsyncSession, tenant_id) -> dict:
    """For each attributed identity, the stages it has reached - from rows that already exist.

    Reads bc_shift_reg (registered), SalesCall (booked, held), the classify_stage groups already
    stored on bc_launch_opp (applied, committed) and bc_onboarded (closed, with contract value).

    A stage whose source carries no usable date is written dated=False: COUNTED in the funnel and
    excluded from every duration and from the curve fit. Counting it is honest; timing it is not.

    NOTHING HERE RE-DERIVES STAGE SEMANTICS. classify_stage and migration 0032 settled what
    committed and closed mean, and the Sales Desk owns what held means. A second definition of
    "closed" living on the ads tab would be worse than not shipping the feature.
    """
    attrs = {a.identity_key: a for a in (await s.execute(select(AdAttribution).where(
        AdAttribution.tenant_id == tenant_id))).scalars()}
    if not attrs:
        return {"skipped": "no attributed identities"}

    by_email = {a.email_norm: a for a in attrs.values() if a.email_norm}
    existing = {(c.attribution_id, c.stage_key): c
                for c in (await s.execute(select(AdConversion).where(
                    AdConversion.tenant_id == tenant_id))).scalars()}
    stats = {"identities": len(attrs), "written": 0, "undated": 0, "by_stage": {}}

    def _put(attr, stage, day, source_kind, source_ref, contracted=None,
             annualized=False, payment_type=None):
        row = existing.get((attr.id, stage))
        if row is None:
            row = AdConversion(tenant_id=tenant_id, attribution_id=attr.id,
                               business_id=attr.business_id, stage_key=stage,
                               source_kind=source_kind, source_ref=str(source_ref)[:64])
            s.add(row)
            existing[(attr.id, stage)] = row
            stats["written"] += 1
            stats["by_stage"][stage] = stats["by_stage"].get(stage, 0) + 1
        row.occurred_on = day
        row.dated = day is not None
        if day is None:
            stats["undated"] += 1
        if contracted is not None:
            row.value_contracted = contracted
        row.value_annualized = annualized
        row.payment_type = payment_type

    # registered and closed - MetricRecord rows that already carry a date and, for closes, value.
    for kind, stage in (("bc_shift_reg", "registered"), ("bc_onboarded", "closed")):
        for r in (await s.execute(select(MetricRecord).where(
                MetricRecord.tenant_id == tenant_id, MetricRecord.kind == kind))).scalars():
            attr = attrs.get(str((r.meta or {}).get("contact_id") or ""))
            if attr is None or attr.business_id is None:
                continue
            if stage == "closed":
                pay = str((r.meta or {}).get("payment") or "") or None
                contracted, annualized = _annualize(r.amount, pay)
                _put(attr, stage, r.occurred_on, kind, r.external_id,
                     contracted=contracted, annualized=annualized, payment_type=pay)
            else:
                _put(attr, stage, r.occurred_on, kind, r.external_id)

    # booked and held - the Sales Desk's log, never a live GHL field. is_current keeps a rebook
    # from erasing the no-show it replaced.
    for c in (await s.execute(select(SalesCall).where(
            SalesCall.tenant_id == tenant_id, SalesCall.is_current.is_(True)))).scalars():
        attr = attrs.get(str(c.contact_id or ""))
        if attr is None or attr.business_id is None:
            continue
        if c.booking_id:
            _put(attr, "booked", c.call_time_utc.date() if c.call_time_utc else None,
                 "sales_call", c.opportunity_id)
        if str(c.outcome or "").strip().lower() in HELD_OUTCOMES:
            # Phase 0 measured outcome_at present on only 80 percent of calls, so this stage is
            # frequently dated=False - counted, never timed.
            _put(attr, "held", c.outcome_at.date() if c.outcome_at else None,
                 "sales_call", c.opportunity_id)

    # applied and committed - the group the launch snapshot already classified.
    for r in (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == tenant_id,
            MetricRecord.kind == "bc_launch_opp"))).scalars():
        meta = r.meta or {}
        attr = attrs.get(str(meta.get("contact_id") or ""))
        if attr is None or attr.business_id is None:
            continue
        if meta.get("app_in"):
            _put(attr, "applied", r.occurred_on, "bc_launch_opp", r.external_id)
        if meta.get("group") == "committed":
            _put(attr, "committed", r.occurred_on, "bc_launch_opp", r.external_id)

    # collected - cash received against those contracts.
    #
    # Joined BY EMAIL, because payment rows carry an email and no contact id. That is a weaker
    # key than the rest of the spine: a member who pays from a different address is missed, and
    # the payload reports the join so nobody reads a low collected figure as a collections
    # problem when it is a matching problem.
    matched_emails = 0
    collected: dict = {}
    for p in (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == tenant_id, MetricRecord.kind == "payment",
            MetricRecord.status == "succeeded"))).scalars():
        key = str(p.email or "").strip().lower()
        if key and key in by_email:
            collected[key] = collected.get(key, Decimal("0")) + Decimal(str(p.amount or 0))
    for email, total in collected.items():
        row = existing.get((by_email[email].id, "closed"))
        if row is not None:
            row.value_collected = total
            matched_emails += 1

    stats["collected_matched"] = matched_emails
    await s.commit()
    print(f"[ads_conv] {stats['written']} stage rows, {stats['undated']} undated, "
          f"{matched_emails} with collected cash (by stage: {stats['by_stage']})", flush=True)
    return stats


async def build_funnel(s: AsyncSession, tenant_id, account, start, end, basis="cohort",
                       ads_rungs=None) -> dict:
    """The ladder, plus revenue and CAC. SPEC-ads-module.md Part 9.5.

    basis="cohort" (the default): identities FIRST TOUCHED in the window, with their revenue
    whenever it eventually lands. What marketing needs, because this month's revenue came from
    last quarter's spend.

    basis="period": revenue RECOGNISED in the window regardless of when the identity was
    acquired. What accounting wants, and useless for judging an ad.

    Both are correct answers to different questions, and the payload always NAMES which one it
    is. An ads number under the wrong basis label is a wrong business decision.
    """
    from .ads import cac as _cac
    from .ads import conversion, cost_per, rate, roas

    if basis == "period":
        # Recognised in the window: the stage row's own date decides membership.
        convs = list((await s.execute(select(AdConversion).where(
            AdConversion.tenant_id == tenant_id,
            AdConversion.occurred_on >= start,
            AdConversion.occurred_on <= end))).scalars())
        cohort_ids = {c.attribution_id for c in convs}
    else:
        cohort = list((await s.execute(select(AdAttribution).where(
            AdAttribution.tenant_id == tenant_id,
            AdAttribution.first_seen_on >= start,
            AdAttribution.first_seen_on <= end))).scalars())
        cohort_ids = {a.id for a in cohort}
        convs = [c for c in (await s.execute(select(AdConversion).where(
            AdConversion.tenant_id == tenant_id))).scalars() if c.attribution_id in cohort_ids]

    by_stage: dict[str, list] = {}
    for c in convs:
        by_stage.setdefault(c.stage_key, []).append(c)

    spend = float((ads_rungs or {}).get("spend") or 0)
    closes = by_stage.get("closed", [])
    n_closed = len(closes)

    contracted = sum(float(c.value_contracted or 0) for c in closes)
    collected = sum(float(c.value_collected or 0) for c in closes)
    annualized_n = sum(1 for c in closes if c.value_annualized)

    rungs = []
    prev = None
    for d in FUNNEL_DEFS["program"]:
        key = d["key"]
        if d.get("src") == "ads":
            n = int((ads_rungs or {}).get(key) or 0)
        else:
            n = len(by_stage.get(key, []))
        dated = sum(1 for c in by_stage.get(key, []) if c.dated) if d.get("src") != "ads" else n
        rungs.append({
            **d, "n": n, "prev": prev,
            "conversion": conversion(n, prev) if prev is not None else None,
            "cost_per": cost_per(spend, n) if n else None,
            # A stage is only ever TIMED on the rows that carry a date. Reported so the reader
            # can see which counts are safe to build a duration on.
            "dated": dated, "undated": max(0, n - dated),
        })
        prev = n

    # ALL closes in the window, attributed or not. The difference is the structural ceiling from
    # Part 4.7, and naming it prevents the attributed number reading as a failure.
    all_closes = list((await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.kind == "bc_onboarded",
        MetricRecord.occurred_on >= start, MetricRecord.occurred_on <= end))).scalars())

    return {
        "basis": basis,
        "rungs": rungs,
        "revenue": {
            "contracted": contracted,
            "collected": collected,
            # Suppressed until a measured curve exists. A guessed projection is worse than an
            # absent one, because it looks like a number.
            "projected": None,
            "roas_contracted": roas(contracted, spend),
            "roas_collected": roas(collected, spend),
            "roas_projected": None,
            "annualized_closes": annualized_n,
            "collected_join": "email",
        },
        "cac": {
            "attributed": _cac(spend, n_closed),
            "blended": _cac(spend, len(all_closes)),
            "blended_label": "Blended - every enrollment in the window, not only those traced "
                             "to an ad. Always lower, and never the ads number.",
            "attributed_closes": n_closed,
            "all_closes": len(all_closes),
        },
        "unattributed": {
            "closes": max(0, len(all_closes) - n_closed),
            "note": "Enrollments with no attribution row. Counted here and assigned to no "
                    "campaign - word of mouth, the existing list, a referral, someone who saw an "
                    "ad on a phone and registered on a laptop.",
        },
        "maturity": {"fitted": False, "pct": None, "median_lag_days": None,
                     "expected_additional": None,
                     "note": "No cohort curve has been fitted yet, so no projection is shown."},
    }
