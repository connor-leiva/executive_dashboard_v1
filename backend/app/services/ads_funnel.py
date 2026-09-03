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

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from decimal import Decimal

from ..models import (Ad, AdAccount, AdAttribution, AdCampaign, AdConversion, MetricRecord,
                      SalesCall)
from .launch import (PRICE_GHL, PRICE_NONE, SHIFT_SRC_CHANNEL, contract_prices)

# Sources that mean Meta. Shared with classify_shift_source's vocabulary deliberately: one
# definition of "this came from Meta", so the ads tab and the launch tab cannot disagree.
META_SOURCES = {k for k, v in SHIFT_SRC_CHANNEL.items() if v == "Meta"} or {
    "meta", "facebook", "instagram", "fb", "ig"}

# Grades, strictest first. The order IS the resolution order.
MATCH_AD, MATCH_CAMPAIGN, MATCH_CHANNEL = "ad", "campaign", "channel"


def _norm(s: str | None) -> str:
    return " ".join(str(s or "").lower().split())


def resolve_match(utm: dict, ads_by_ext: dict, campaigns_by_name: dict,
                  channel: str | None = None,
                  campaigns_by_ext: dict | None = None) -> dict | None:
    """The grade this identity's UTM entitles it to, strictest first. None when it entitles
    nothing - and an unattributed identity gets NO ROW, so the read service counts it from the
    source population rather than from a placeholder that would need explaining forever.

    `channel` is the channel the LAUNCH TAB already classified this person into, and it is a veto
    on the channel rung, never a promotion. Without it this granted Meta credit on utm_source
    alone: a registrant carrying utm_source=ig, utm_medium=social and no campaign is somebody who
    clicked a link in an Instagram bio, and 48 of the 51 channel-grade rows on the live account
    were exactly that - organic traffic counted against paid spend. The launch classifier already
    knew, and stored "Organic / Existing" on the very same row this called Meta.

    Pure, so the whole resolution order is testable without a database.
    """
    content = str(utm.get("utm_content") or "").strip()
    campaign = _norm(utm.get("utm_campaign"))
    source = str(utm.get("utm_source") or "").strip().lower()
    raw_campaign = str(utm.get("utm_campaign") or "").strip()

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

    # 2b. Some accounts template the campaign ID rather than the name. That is a real ad click
    #     wearing an unreadable label, and the id is one we already hold.
    if raw_campaign and campaigns_by_ext and raw_campaign in campaigns_by_ext:
        c = campaigns_by_ext[raw_campaign]
        return {"match_method": MATCH_CAMPAIGN, "confidence": "probable",
                "ad_id": None, "campaign_id": c.id, "ad_account_id": c.ad_account_id}

    # 3. A Meta source and nothing finer -> channel grade, UNLESS the launch classifier placed
    #    this person outside Meta. Source alone cannot tell a paid click from an organic one.
    if source in META_SOURCES and (channel is None or channel == "Meta"):
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
    _camps = list((await s.execute(select(AdCampaign).where(
        AdCampaign.tenant_id == tenant_id, AdCampaign.ad_account_id.in_(acct_ids)))).scalars())
    campaigns_by_name = {_norm(c.name): c for c in _camps}
    campaigns_by_ext = {str(c.external_id): c for c in _camps if c.external_id}

    regs = list((await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id,
        MetricRecord.kind == "bc_shift_reg"))).scalars())

    # ONE-TIME RE-DERIVATION, BEFORE the snapshot below reads the table.
    #
    # Channel grade used to be granted on utm_source alone, so organic Instagram traffic -
    # utm_source=ig, utm_medium=social, no campaign - was credited against paid spend. Those rows
    # are not stale, they are wrong: the launch classifier had already placed the same person
    # outside Meta on the same row. Attribution is otherwise first-touch-frozen and insert-only,
    # and rightly so; a row that should never have existed is a different thing from a row whose
    # cohort day somebody wants to move.
    #
    # Their conversions go too. AdConversion points at the attribution, and leaving those behind
    # would orphan rows that the funnel still counts.
    #
    # Ordering is not incidental: doing this AFTER the snapshot is exactly the bug that deleted
    # four closes and never rebuilt them, because the cache still held what the table no longer
    # did.
    doomed = [a.id for a in (await s.execute(select(AdAttribution).where(
        AdAttribution.tenant_id == tenant_id,
        AdAttribution.match_method == MATCH_CHANNEL,
        AdAttribution.channel != "Meta"))).scalars()]
    if doomed:
        await s.execute(sa_delete(AdConversion).where(
            AdConversion.attribution_id.in_(doomed)))
        await s.execute(sa_delete(AdAttribution).where(AdAttribution.id.in_(doomed)))
        await s.commit()
        print(f"[ads_attr] dropped {len(doomed)} channel-grade rows the launch classifier "
              f"places outside Meta", flush=True)

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
        match = resolve_match(utm, ads_by_ext, campaigns_by_name,
                              channel=(meta.get("channel") or None),
                              campaigns_by_ext=campaigns_by_ext)
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
        # `implied_by` NAMES THE STAGES WHOSE MEMBERS MUST HAVE PASSED THROUGH THIS ONE, and it
        # exists because two of these rungs are a different KIND of fact from the other four.
        #
        # registered, booked and held come from event logs, and an event never un-happens - those
        # rungs are cumulative for free. committed and closed come from the launch's stage GROUP,
        # and classify_stage puts a person in exactly ONE current group: signing MOVES her out of
        # committed and into closed. So the raw group count answers "who is sitting here", while
        # every other rung answers "who has reached here", and a ladder cannot mix the two.
        #
        # Mixed, it produced a funnel that REFILLS: 34 held, 1 at cash received, 6 enrolled. Every
        # figure derived from that was then wrong in a way that looked entirely plausible - a 600%
        # conversion, a cost-per of the whole ad spend divided by one person, and a "biggest leak"
        # callout blaming a step nobody had dropped at. The six enrolled members had all paid; they
        # had simply stopped sitting at the rung that counts payment.
        {"key": "committed", "label": "Cash received", "src": "stage_group", "zone": "acumyn",
         "implied_by": ("closed",)},
        {"key": "closed", "label": "Enrolled", "src": "stage_group", "zone": "acumyn",
         "closes": True},
    ],
}

RUNG_DEFS = {d["key"]: d for d in FUNNEL_DEFS["program"]}
ACUMYN_STAGES = ("registered", "booked", "applied", "held", "committed", "closed")
HELD_OUTCOMES = ("showed", "held", "attended")


def rung_population(by_stage: dict, d: dict) -> list:
    """Everyone who has REACHED this rung - not everyone currently sitting at it.

    ONE DEFINITION, used by the rung count, the rung's dollars, the hero's cash total and the
    drill. They are four renderings of one population and the moment any of them computes its own
    the four start disagreeing, which is the failure mode this whole module is built against.

    Deduped by attribution, and a LATER stage wins on anyone holding rows at both: it is the more
    recent and stronger fact about the same person.
    """
    best = {c.attribution_id: c for c in by_stage.get(d["key"], [])}
    for later in d.get("implied_by", ()):
        best.update({c.attribution_id: c for c in by_stage.get(later, [])})
    return list(best.values())


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

    Reads bc_shift_reg (registered), SalesCall (booked, held), and the classify_stage groups
    already stored on bc_launch_opp for applied, committed AND CLOSED. The contract VALUE comes
    from the launch's own price sheet (contract_prices); bc_onboarded is the last resort when
    nothing has priced the person, and is never consulted for who counts as enrolled.

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

    # ONE-TIME RE-DERIVATION, and it MUST happen before `existing` is read.
    #
    # `closed` used to be written from bc_onboarded and is now written from the launch stage
    # group. _put is insert-only by design - history cannot silently change under somebody - and
    # that same rule would strand every row the old definition wrote, leaving the rung a UNION of
    # two definitions, which is worse than either. A deliberate change of definition is the one
    # case warranting re-derivation.
    #
    # Doing it AFTER the `existing` snapshot is what broke it live: the cache still held the four
    # deleted rows, so _put took its "already exists" branch and updated objects that were no
    # longer in the database. The nine people who had never closed were inserted correctly and
    # the four who HAD closed lost their row and never got it back - a re-derivation that only
    # deleted. `existing` is a cache of the table, so the table has to be right before it is read.
    stale = (await s.execute(sa_delete(AdConversion).where(
        AdConversion.tenant_id == tenant_id,
        AdConversion.stage_key == "closed",
        AdConversion.source_kind == "bc_onboarded"))).rowcount
    if stale:
        print(f"[ads_conv] re-deriving {stale} closes from the launch stage group", flush=True)
        await s.commit()

    existing = {(c.attribution_id, c.stage_key): c
                for c in (await s.execute(select(AdConversion).where(
                    AdConversion.tenant_id == tenant_id))).scalars()}
    stats = {"identities": len(attrs), "written": 0, "undated": 0, "by_stage": {},
             "priced": {}}

    def _put(attr, stage, day, source_kind, source_ref, contracted=None,
             annualized=False, payment_type=None, upfront=None, value_source=None):
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
        # The money fields are assigned UNCONDITIONALLY, unlike the insert-only stage semantics
        # above. Re-pricing is the whole point: the price sheet is tenant-editable, and an edit
        # that could not reach the rows it prices would be an edit that silently did nothing.
        # WHO is enrolled still never changes under anybody; only what we say they signed for.
        if value_source is not None:
            row.value_contracted = contracted
            row.value_upfront = upfront
            row.value_source = value_source
            row.value_annualized = annualized
            row.payment_type = payment_type

    # registered.
    for r in (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == tenant_id,
            MetricRecord.kind == "bc_shift_reg"))).scalars():
        attr = attrs.get(str((r.meta or {}).get("contact_id") or ""))
        if attr is None or attr.business_id is None:
            continue
        _put(attr, "registered", r.occurred_on, "bc_shift_reg", r.external_id)

    # The contract VALUE for a close, keyed by contact. Only the onboarded record carries an
    # amount - the launch opportunity rows carry none at all - so the value is looked up here
    # while the POPULATION is decided by the stage group below. Two different questions:
    # "is this person enrolled" is the launch's definition, "what did they sign for" is a
    # number that happens to live on another row.
    # The price sheet, resolved per opportunity ONCE. Keyed by the opp id the snapshot wrote,
    # and priced against the launch that opp belongs to rather than whichever launch happens to
    # be active today - an ads window can span two cohorts, and pricing an August enrollment off
    # a November sheet would be a wrong number nobody could see.
    prices = await contract_prices(s, tenant_id)

    onboarded_value: dict[str, MetricRecord] = {}
    for r in (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == tenant_id,
            MetricRecord.kind == "bc_onboarded"))).scalars():
        cid = str((r.meta or {}).get("contact_id") or "")
        if cid:
            onboarded_value[cid] = r

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
        cid = str(meta.get("contact_id") or "")
        attr = attrs.get(cid)
        if attr is None or attr.business_id is None:
            continue
        if meta.get("app_in"):
            _put(attr, "applied", r.occurred_on, "bc_launch_opp", r.external_id)
        if meta.get("group") in ("committed", "enrolled"):
            # ONE DEFINITION OF ENROLLED, and it is the launch tab's. This used to read
            # bc_onboarded, which is the `Won: Onboarded` EVENT - so the ads tab counted 4 of the
            # 13 people the launch tab called enrolled, and the nine sitting at "Onboarding Call
            # Attended" silently never reached the rung. Two definitions of the closing event on
            # two tabs, disagreeing by 2.5x on CAC.
            #
            # `group` is classified by classify_stage against the LAUNCH'S OWN stage_map, so
            # editing that map moves both tabs together and neither can drift.
            #
            # ONE PRICE SHEET, likewise, and for the same reason. The contract value is now the
            # launch's own acv for this person's payment type - the number the Launch tab prints
            # - and only falls back to GHL's free-text opportunity amount when nothing has priced
            # them. `value_source` says which, because a price sheet figure and a typed-in one
            # must never look like the same fact.
            priced = prices.get(str(r.external_id)) or {}
            pay = priced.get("type") or (str(meta.get("payment_type") or "") or None)
            if priced.get("acv") is not None:
                contracted = Decimal(str(priced["acv"]))
                upfront = (Decimal(str(priced["upfront"]))
                           if priced.get("upfront") is not None else None)
                annualized, vsrc = bool(priced.get("annualized")), priced["source"]
            else:
                # Nobody priced this person. GHL's amount is the last thing left, and for a
                # financed member it is the down payment wearing a contract's label - so it is
                # taken, and FLAGGED, rather than quietly promoted.
                onb = onboarded_value.get(cid)
                # None, never 0. A person the pipeline enrolled with no amount recorded anywhere
                # has an UNKNOWN value; writing zero would drag the average contract down and
                # read as a free seat.
                amount = onb.amount if (onb is not None and onb.amount) else None
                contracted, annualized = _annualize(amount, pay)
                upfront = None
                vsrc = PRICE_GHL if contracted is not None else PRICE_NONE
            # Tallied per rung: a bare "3 priced" reads as three enrollments when it is two
            # enrollments and a deposit.
            grp_key = f"{meta.get('group')}:{vsrc}"
            stats["priced"][grp_key] = stats["priced"].get(grp_key, 0) + 1
            if meta.get("group") == "committed":
                # Committed is cash received against an unsigned contract - the rung the funnel
                # labels "Cash received". It carries the upfront, and deliberately NOT the acv:
                # nothing is contracted until it is signed, and a value here would leak into any
                # later sum over contracted revenue.
                _put(attr, "committed", r.occurred_on, "bc_launch_opp", r.external_id,
                     contracted=None, annualized=False, payment_type=pay,
                     upfront=upfront, value_source=vsrc)
            else:
                _put(attr, "closed", r.occurred_on, "bc_launch_opp", r.external_id,
                     contracted=contracted, annualized=annualized, payment_type=pay,
                     upfront=upfront, value_source=vsrc)

    # collected - cash received against those contracts.
    #
    # Joined BY EMAIL, because payment rows carry an email and no contact id. That is a weaker
    # key than the rest of the spine: a member who pays from a different address is missed, and
    # the payload reports the join so nobody reads a low collected figure as a collections
    # problem when it is a matching problem.
    matched_emails = 0
    collected: dict = {}
    # SCOPED TO THE BUSINESS THE ATTRIBUTION BOOKS TO. Without it this summed every succeeded
    # payment row in the workspace against a matching email - so a member who is also on the
    # Forum roster brought her Forum dues into the beCollective ads figure. Same tenant, same
    # email, completely different programme.
    biz_ids = {a.business_id for a in attrs.values() if a.business_id}
    pay_rows = list((await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.kind == "payment",
        MetricRecord.status == "succeeded",
        MetricRecord.business_id.in_(biz_ids)))).scalars()) if biz_ids else []
    for pay_row in pay_rows:
        key = str(pay_row.email or "").strip().lower()
        if not (key and key in by_email):
            continue
        # A refunded charge is not cash received. It is carried on every payment writer and was
        # never subtracted, so a fully-refunded seat counted as collected in full.
        net = Decimal(str(pay_row.amount or 0)) - Decimal(
            str((pay_row.meta or {}).get("amount_refunded") or 0))
        collected[key] = collected.get(key, Decimal("0")) + max(net, Decimal("0"))
    for email, total in collected.items():
        # Cash lands on whichever money rung this person has reached. Previously only `closed`
        # was consulted, so the cash of everybody at "Cash received" - the rung that is BY
        # DEFINITION people who have paid and not yet signed - was computed and then thrown away.
        row = (existing.get((by_email[email].id, "closed"))
               or existing.get((by_email[email].id, "committed")))
        if row is not None:
            row.value_collected = total
            matched_emails += 1

    stats["collected_matched"] = matched_emails
    await s.commit()
    print(f"[ads_conv] {stats['written']} stage rows, {stats['undated']} undated, "
          f"{matched_emails} with collected cash (by stage: {stats['by_stage']}; "
          f"priced: {stats['priced']})", flush=True)
    return stats


async def build_funnel(s: AsyncSession, tenant_id, account, start, end, basis="cohort",
                       ads_rungs=None, campaign=None) -> dict:
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
        q = select(AdAttribution).where(
            AdAttribution.tenant_id == tenant_id,
            AdAttribution.first_seen_on >= start,
            AdAttribution.first_seen_on <= end)
        if campaign is not None:
            q = q.where(AdAttribution.campaign_id == campaign)
        cohort = list((await s.execute(q)).scalars())
        cohort_ids = {a.id for a in cohort}
        convs = [c for c in (await s.execute(select(AdConversion).where(
            AdConversion.tenant_id == tenant_id))).scalars() if c.attribution_id in cohort_ids]

    if campaign is not None and basis == "period":
        # The period branch selects conversions by date, so the campaign filter has to be applied
        # through their attribution rather than in the same query.
        allowed = {a.id for a in (await s.execute(select(AdAttribution).where(
            AdAttribution.tenant_id == tenant_id,
            AdAttribution.campaign_id == campaign))).scalars()}
        convs = [c for c in convs if c.attribution_id in allowed]
        cohort_ids = {c.attribution_id for c in convs}

    by_stage: dict[str, list] = {}
    for c in convs:
        by_stage.setdefault(c.stage_key, []).append(c)

    spend = float((ads_rungs or {}).get("spend") or 0)
    closes = by_stage.get("closed", [])
    n_closed = len(closes)

    contracted = sum(float(c.value_contracted or 0) for c in closes)
    annualized_n = sum(1 for c in closes if c.value_annualized)

    # CASH, over EVERYONE WHO HAS PAID - the same population the Launch tab's §9.4 cash line uses,
    # and for the same reason it states there: "Committed IS paid by definition, so cash must
    # include it." The ads tab summed `closed` only, so the money sitting on the rung LABELLED
    # "Cash received" was excluded from the cash figure. Both rungs, one definition, both tabs.
    # EVERYONE WHO HAS PAID, which is exactly the Cash received rung's population - the same call
    # the rung itself makes, so the hero total and the rung's dollars cannot drift apart. Deduped
    # by person: somebody carrying a committed row AND a closed row is one person, not two.
    paid = rung_population(by_stage, RUNG_DEFS["committed"])

    # PER ROW, NEVER ALL-OR-NOTHING. Preferring the price sheet only when it had priced ANYBODY
    # discarded every matched payment the moment one person was priced: one priced deposit of
    # $5,000 beside twelve people carrying $90,000 of real, succeeded, refund-netted charges
    # would have reported $5,000 and thrown the rest away, with nothing on screen to say so.
    modelled = sum(float(c.value_upfront or 0) for c in paid)
    measured = sum(float(c.value_collected or 0) for c in paid)
    per_row = [(float(c.value_upfront), "upfront") if c.value_upfront is not None
               else (float(c.value_collected or 0), "payments") for c in paid]
    collected = sum(v for v, _ in per_row)
    kinds = {k for v, k in per_row if v}
    collected_source = "mixed" if len(kinds) > 1 else next(iter(kinds), None)
    unpriced_closes = sum(1 for c in closes if c.value_contracted is None)
    ghl_priced = sum(1 for c in closes if c.value_source == "ghl_amount")

    rungs = []
    prev = None
    for d in FUNNEL_DEFS["program"]:
        key = d["key"]
        if d.get("src") == "ads":
            rows_, n = [], int((ads_rungs or {}).get(key) or 0)
        else:
            rows_ = rung_population(by_stage, d)
            n = len(rows_)
        dated = sum(1 for c in rows_ if c.dated) if d.get("src") != "ads" else n
        # The two money rungs carry their dollars. A rung called "Cash received" that shows only
        # a headcount is the reader's job half done, and it is the figure the hero totals.
        value = None
        if key == "closed":
            value = contracted or None
        elif key == "committed":
            value = collected or None
        rungs.append({
            **d, "n": n, "prev": prev, "value": value,
            "conversion": conversion(n, prev) if prev is not None else None,
            "cost_per": cost_per(spend, n) if n else None,
            # A stage is only ever TIMED on the rows that carry a date. Reported so the reader
            # can see which counts are safe to build a duration on.
            "dated": dated, "undated": max(0, n - dated),
        })
        prev = n

    # ALL closes in the window, attributed or not. The difference is the structural ceiling from
    # Part 4.7, and naming it prevents the attributed number reading as a failure.
    #
    # THE SAME DEFINITION as the attributed count above, which is the whole point of blended CAC:
    # it is the attributed figure's denominator widened to everybody, and widening the population
    # AND changing the definition in one step compares two different things. This read
    # bc_onboarded while the rung read the stage group, which on live data was 64 against 13 -
    # a blended CAC five times too flattering, sitting beside the attributed one as its check.
    #
    # And it can legitimately be EMPTY for a past window: bc_launch_opp is delete-then-insert per
    # BUSINESS, not per launch, so the table only ever holds the currently active launch's
    # opportunities. Asking it about last quarter returns nothing, which is a missing denominator
    # and not a zero - the payload says which, so the UI can decline to show a blended figure
    # rather than show a wrong one.
    all_closes = [r for r in (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.kind == "bc_launch_opp",
        MetricRecord.occurred_on >= start, MetricRecord.occurred_on <= end))).scalars()
        if (r.meta or {}).get("group") == "enrolled"]

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
            # How the cash figure was arrived at, and both readings of it. "upfront" is the
            # price sheet's due-at-signing; "payments" is succeeded charges matched by email.
            "collected_source": collected_source,
            "collected_modelled": modelled,
            "collected_measured": measured,
            "cash_people": len(paid),
            "committed_people": len(by_stage.get("committed", [])),
            # Enrollments the price sheet could not price. Surfaced rather than swallowed,
            # because they read as a smaller contracted total and not as missing configuration.
            "unpriced_closes": unpriced_closes,
            "ghl_priced_closes": ghl_priced,
        },
        "cac": {
            "attributed": _cac(spend, n_closed),
            "blended": _cac(spend, len(all_closes)),
            "blended_label": "Blended - every enrollment in the window, not only those traced "
                             "to an ad. Always lower, and never the ads number.",
            "attributed_closes": n_closed,
            "all_closes": len(all_closes),
            # False when the window predates the launch whose opportunities the table holds:
            # there is no population to blend against, which is not the same as nobody enrolling.
            "blended_available": bool(all_closes) or n_closed == 0,
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
