"""Ads API. SPEC-ads-module.md Part 11.

Every read route is gated on require_tab("ads"), and authorization is enforced IN THE DATA rather
than only in the nav: an account is reachable only through the caller's own tenant, so a member
without the tab gets a 403 and a member of another workspace gets nothing to 403 about.

No step-up. Ad performance is not Binder-grade.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import current_user, get_session, require_role, require_tab
from ..models import Ad, AdAccount, AdCampaign, AdInsightDaily, Business, Integration, User
from ..services import ads as A
from ..services import ads_rules as R
from ..services.ads_funnel import FUNNEL_DEFS
from ..services.audit import audit

router = APIRouter()


async def _account(s: AsyncSession, tenant_id, account_id) -> AdAccount:
    """Resolve an account WITHIN the caller's tenant. The tenant filter is the authorization -
    an id from another workspace simply does not exist here."""
    acct = (await s.execute(select(AdAccount).where(
        AdAccount.tenant_id == tenant_id, AdAccount.id == account_id))).scalar_one_or_none()
    if acct is None:
        raise HTTPException(404, "Unknown ad account")
    return acct


async def _first_account(s: AsyncSession, tenant_id) -> AdAccount | None:
    return (await s.execute(select(AdAccount).where(
        AdAccount.tenant_id == tenant_id, AdAccount.status == "active")
        .order_by(AdAccount.created_at))).scalars().first()


@router.get("/ads/accounts")
async def list_accounts(user: User = Depends(require_tab("ads")),
                        s: AsyncSession = Depends(get_session)):
    rows = list((await s.execute(select(AdAccount).where(
        AdAccount.tenant_id == user.tenant_id).order_by(AdAccount.created_at))).scalars())
    biz = {b.id: b for b in (await s.execute(select(Business).where(
        Business.tenant_id == user.tenant_id))).scalars()}
    out = []
    for a in rows:
        b = biz.get(a.business_id)
        n_camp = (await s.execute(select(func.count()).select_from(AdCampaign).where(
            AdCampaign.tenant_id == user.tenant_id, AdCampaign.ad_account_id == a.id))).scalar_one()
        n_ads = (await s.execute(select(func.count()).select_from(Ad).where(
            Ad.tenant_id == user.tenant_id, Ad.ad_account_id == a.id))).scalar_one()
        span = (await s.execute(select(func.min(AdInsightDaily.occurred_on),
                                       func.max(AdInsightDaily.occurred_on)).where(
            AdInsightDaily.tenant_id == user.tenant_id,
            AdInsightDaily.ad_account_id == a.id))).one()
        archetype = getattr(b, "archetype", None) if b else None
        out.append({
            "id": str(a.id), "name": a.name, "external_id": a.external_id,
            "currency": a.currency, "timezone_name": a.timezone_name,
            "business_key": b.key if b else None, "archetype": archetype,
            # A funnel exists only for archetypes that have one. Everything else gets the click
            # layer and is TOLD so, rather than shown an empty ladder.
            "funnel_available": archetype in ("program", "transactional"),
            "status": a.status, "last_synced_at": a.last_synced_at.isoformat() if a.last_synced_at else None,
            "last_error": a.last_error, "campaigns": n_camp, "ads": n_ads,
            "first_day": span[0].isoformat() if span[0] else None,
            "last_day": span[1].isoformat() if span[1] else None,
        })
    return out


@router.get("/ads")
async def overview(account: str | None = Query(None), period: str = Query("30d"),
                   start: dt.date | None = None, end: dt.date | None = None,
                   basis: str = Query("cohort"), campaign: str | None = Query(None),
                   user: User = Depends(require_tab("ads")),
                   s: AsyncSession = Depends(get_session)):
    acct = await _account(s, user.tenant_id, account) if account else await _first_account(s, user.tenant_id)
    if acct is None:
        # Connect-first empty state. Not an error: a workspace that has never connected Meta is
        # in a normal state, and a 404 here would render as a broken tab.
        return {"connected": False, "accounts": 0,
                "reason": "No ad account is connected. Add one in Settings -> Integrations."}
    camp = await _campaign(s, user.tenant_id, acct, campaign) if campaign else None
    return await build_overview(s, user.tenant_id, acct, period, start, end, basis, camp)


async def _campaign(s: AsyncSession, tenant_id, acct: AdAccount, campaign_id) -> AdCampaign:
    """Resolve a campaign WITHIN this tenant and this account. Same rule as _account: the tenant
    filter is the authorization, so a campaign belonging to another workspace does not exist here
    rather than being found and refused."""
    c = (await s.execute(select(AdCampaign).where(
        AdCampaign.tenant_id == tenant_id, AdCampaign.ad_account_id == acct.id,
        AdCampaign.id == campaign_id))).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, "Unknown campaign")
    return c


async def build_overview(s: AsyncSession, tenant_id, acct: AdAccount, period, start, end,
                         basis: str, campaign: AdCampaign | None = None) -> dict:
    """The click layer. Phase 3 hangs the funnel and the revenue lenses off this same payload.

    EVERY rate here comes from services.ads.rate(), from summed components. Nothing in this
    function divides two numbers directly.
    """
    s_day, e_day, label = A.ads_period(period, start, end, acct.timezone_name)
    rules = acct.group_rules or None
    thresholds = acct.thresholds or None

    q = select(AdInsightDaily).where(
        AdInsightDaily.tenant_id == tenant_id, AdInsightDaily.ad_account_id == acct.id,
        AdInsightDaily.level == "campaign",
        AdInsightDaily.occurred_on >= s_day, AdInsightDaily.occurred_on <= e_day)
    # One campaign, whole page. Spring runs a launch per campaign - "KB - The Shift - August2026"
    # IS the August launch - so narrowing here narrows the headline figures, the funnel and the
    # creative wall together. A funnel filtered to one campaign beside spend for all of them
    # would put a wrong CAC on screen, which is worse than not offering the filter.
    all_rows = list((await s.execute(q)).scalars())     # unfiltered: the picker's own list
    rows = ([r for r in all_rows if r.campaign_id == campaign.id]
            if campaign is not None else all_rows)
    camps = {c.id: c for c in (await s.execute(select(AdCampaign).where(
        AdCampaign.tenant_id == tenant_id, AdCampaign.ad_account_id == acct.id))).scalars()}

    def _sum(field: str, rs=None) -> float:
        return float(sum(getattr(r, field) or 0 for r in (rows if rs is None else rs)))

    per: dict = {}
    for r in rows:
        c = camps.get(r.campaign_id)
        key = c.external_id if c else r.object_external_id
        d = per.setdefault(key, {"name": c.name if c else key, "id": str(c.id) if c else None,
                                 "spend": 0.0, "impressions": 0, "clicks": 0,
                                 "link_clicks": 0, "leads": 0})
        d["spend"] += float(r.spend or 0)
        d["impressions"] += r.impressions or 0
        d["clicks"] += r.clicks or 0
        d["link_clicks"] += r.inline_link_clicks or 0
        d["leads"] += r.leads or 0

    campaigns = []
    for key, d in per.items():
        campaigns.append({**d, "external_id": key,
                          "group": R.classify_campaign(d["name"], rules),
                          "ctr": A.ctr(d["link_clicks"], d["impressions"]),
                          "cpm": A.cpm(d["spend"], d["impressions"]),
                          "cpc": A.cpc(d["spend"], d["link_clicks"]),
                          "cpl": A.cpl(d["spend"], d["leads"])})
    campaigns.sort(key=lambda c: c["spend"], reverse=True)

    groups, unmatched = R.group_campaigns(campaigns, rules)
    spend, impressions = _sum("spend"), _sum("impressions")
    link_clicks, leads = _sum("inline_link_clicks"), _sum("leads")

    totals = {
        "spend": spend, "impressions": int(impressions),
        "clicks": int(_sum("clicks")), "link_clicks": int(link_clicks), "leads": int(leads),
        "ctr": A.ctr(link_clicks, impressions),
        "cpm": A.cpm(spend, impressions),
        "cpc": A.cpc(spend, link_clicks),
        "cpl": A.cpl(spend, leads),
    }
    bands = {k: R.band(k, totals.get(k), thresholds) for k in ("ctr", "cpm", "cpl")}

    # The funnel exists only for archetypes that have one. Everything else gets the click layer
    # and is TOLD so, rather than shown an empty ladder that reads as zero customers.
    biz = await s.get(Business, acct.business_id) if acct.business_id else None
    archetype = getattr(biz, "archetype", None) if biz else None
    _funnel_block = {"funnel": None, "revenue": None, "maturity": None, "funnel_available": False}
    if archetype in FUNNEL_DEFS:
        from ..services.ads_funnel import build_funnel
        f = await build_funnel(s, tenant_id, acct, s_day, e_day, basis,
                               ads_rungs={"impression": totals["impressions"],
                                          "click": totals["link_clicks"],
                                          "lead": totals["leads"], "spend": spend},
                               campaign=(campaign.id if campaign is not None else None))
        _funnel_block = {"funnel": f["rungs"], "revenue": f["revenue"],
                         "maturity": f["maturity"], "cac": f["cac"],
                         "unattributed": f["unattributed"], "funnel_available": True}

    return {
        "connected": True,
        "account": {"id": str(acct.id), "name": acct.name, "external_id": acct.external_id,
                    "currency": acct.currency, "timezone_name": acct.timezone_name},
        "range": {"start": s_day.isoformat(), "end": e_day.isoformat(), "label": label,
                  "days": (e_day - s_day).days + 1},
        # The payload always NAMES its basis. An ads number under the wrong basis label is a
        # wrong business decision, not a cosmetic slip.
        "basis": basis if basis in ("cohort", "period") else "cohort",
        # The payload NAMES its scope for the same reason it names its basis. A funnel narrowed
        # to one launch, rendered under a heading that says the whole account, is a wrong CAC
        # presented as a right one. `campaigns_available` is what the picker offers - every
        # campaign that DELIVERED in this window, so the list cannot offer a scope that would
        # come back empty.
        "scope": ({"kind": "campaign", "id": str(campaign.id), "name": campaign.name}
                  if campaign is not None else {"kind": "account", "id": None,
                                                "name": acct.name}),
        "campaigns_available": _pickable(all_rows, camps),
        "totals": totals, "bands": bands,
        "campaigns": campaigns,
        "groups": [{"name": g, "campaigns": rs,
                    "spend": sum(r["spend"] for r in rs),
                    "link_clicks": sum(r["link_clicks"] for r in rs)}
                   for g, rs in sorted(groups.items(), key=lambda kv: -sum(r["spend"] for r in kv[1]))],
        "unmatched_count": unmatched,
        "archetype": archetype,
        "alerts": _alerts(totals, campaigns, unmatched, thresholds),
        # Phase 3 fills these. Present and explicitly empty so the frontend contract does not
        # change shape when the funnel lands.
        **_funnel_block,
        # THE TWO DENOMINATORS, side by side and never added. Meta's lead count and Acumyn's
        # matched registrations are two systems counting overlapping populations - a large part
        # of the gap is people who DID register and could not be matched (stripped UTM,
        # cross-device, view-through). Summing them double-counts; calling the difference a
        # shortfall blames the funnel for a measurement boundary. So both are shown with the gap
        # named, per Part 4.8.
        "coverage": await _coverage(s, tenant_id, s_day, e_day, int(leads)),
        "freshness": {"last_synced_at": acct.last_synced_at.isoformat() if acct.last_synced_at else None,
                      "last_error": acct.last_error},
    }


async def _coverage(s: AsyncSession, tenant_id, start, end, meta_leads: int) -> dict:
    """Attribution coverage for the window, plus the grade each number is entitled to."""
    from ..services.ads_funnel import attribution_coverage

    cov = await attribution_coverage(s, tenant_id, start, end)
    matched = cov["registrations_matched"]
    return {
        **cov,
        "meta_leads": meta_leads,
        # Deliberately NOT a percentage of one over the other. It is a difference between two
        # measurement systems, and expressing it as a rate invites reading it as a conversion.
        "gap": meta_leads - matched,
        "gap_note": (
            "Meta counts leads it attributes to itself; Acumyn counts registrations it can match "
            "to a campaign. They measure overlapping populations, so the difference is not a "
            "drop-off - much of it is people who did register and could not be matched."),
    }


def _pickable(all_rows: list, camps: dict) -> list[dict]:
    """Every campaign that DELIVERED in this window, whatever the current filter is.

    Built from the UNFILTERED rows on purpose. Deriving it from the displayed campaigns meant
    that selecting one left the picker offering only that one, with no route back - the filter
    would have been a one-way door. Spend-ordered, because that is the order somebody looks for
    a launch in.
    """
    per: dict = {}
    for r in all_rows:
        c = camps.get(r.campaign_id)
        if c is None:
            continue
        d = per.setdefault(str(c.id), {"id": str(c.id), "name": c.name, "spend": 0.0})
        d["spend"] += float(r.spend or 0)
    return sorted(per.values(), key=lambda c: -c["spend"])


def _alerts(totals: dict, campaigns: list[dict], unmatched: int, thresholds) -> list[dict]:
    """Findings, server-side, as {tone, text, metric}. The client colours; it never decides.

    Phase 3 changes the character of this list entirely - a campaign with the best CPL on the
    account and zero enrollments is the finding a click dashboard structurally cannot produce.
    """
    out = []
    if unmatched:
        out.append({"tone": "warn", "metric": "grouping",
                    "text": f"{unmatched} campaign{'s' if unmatched != 1 else ''} fell to "
                            f"{R.FALLBACK_GROUP} - the grouping rules may have drifted from how "
                            f"the account is being named"})
    spending = [c for c in campaigns if c["spend"] > 0]
    floor = (thresholds or R.DEFAULT_THRESHOLDS).get("min_spending_campaigns", 3)
    if len(spending) < floor:
        out.append({"tone": "warn", "metric": "delivery",
                    "text": f"only {len(spending)} campaign{'s' if len(spending) != 1 else ''} "
                            f"delivered in this window"})
    if totals["ctr"] is not None and R.band("ctr", totals["ctr"], thresholds) == "bad":
        out.append({"tone": "bad", "metric": "ctr",
                    "text": f"link CTR is {totals['ctr']:.2f}% across the account"})
    no_leads = [c for c in spending if not c["leads"]]
    if no_leads:
        worst = max(no_leads, key=lambda c: c["spend"])
        out.append({"tone": "bad", "metric": "cpl",
                    "text": f"{worst['name']} spent ${worst['spend']:,.0f} and reported no leads"})
    return out


@router.get("/ads/creatives")
async def creatives(account: str | None = Query(None), period: str = Query("30d"),
                    campaign: str | None = None, sort: str = Query("spend"),
                    limit: int = Query(24, le=96),
                    user: User = Depends(require_tab("ads")),
                    s: AsyncSession = Depends(get_session)):
    acct = await _account(s, user.tenant_id, account) if account else await _first_account(s, user.tenant_id)
    if acct is None:
        return {"connected": False, "ads": [], "revenue_available": False}
    s_day, e_day, _ = A.ads_period(period, None, None, acct.timezone_name)

    rows = list((await s.execute(select(AdInsightDaily).where(
        AdInsightDaily.tenant_id == user.tenant_id, AdInsightDaily.ad_account_id == acct.id,
        AdInsightDaily.level == "ad",
        AdInsightDaily.occurred_on >= s_day, AdInsightDaily.occurred_on <= e_day))).scalars())
    ad_rows = {a.id: a for a in (await s.execute(select(Ad).where(
        Ad.tenant_id == user.tenant_id, Ad.ad_account_id == acct.id))).scalars()}

    agg: dict = {}
    for r in rows:
        a = ad_rows.get(r.ad_id)
        if a is None or (campaign and str(a.campaign_id) != campaign):
            continue
        d = agg.setdefault(a.id, {"id": str(a.id), "name": a.name, "headline": a.headline,
                                  "thumbnail_url": a.thumbnail_url, "spend": 0.0,
                                  "impressions": 0, "link_clicks": 0, "leads": 0,
                                  "url_tags": a.url_tags})
        d["spend"] += float(r.spend or 0)
        d["impressions"] += r.impressions or 0
        d["link_clicks"] += r.inline_link_clicks or 0
        d["leads"] += r.leads or 0

    out = []
    for d in agg.values():
        out.append({**d, "ctr": A.ctr(d["link_clicks"], d["impressions"]),
                    "cpl": A.cpl(d["spend"], d["leads"])})
    key = {"spend": "spend", "ctr": "ctr", "closes": "spend", "revenue": "spend"}.get(sort, "spend")
    out.sort(key=lambda d: (d.get(key) is None, -(d.get(key) or 0)))

    # Ad-level revenue needs utm_content={{ad.id}} on the ads AND a matching custom field in the
    # CRM. Say WHY rather than rendering a blank column - Phase 0 measured this at 0 of 1416.
    from ..integrations.meta_ads import has_ad_level_tagging
    tagged = sum(1 for a in ad_rows.values() if has_ad_level_tagging(a.url_tags))
    return {
        "connected": True, "ads": out[:limit], "total": len(out),
        "revenue_available": False,
        "revenue_reason": (
            f"Ad-level revenue needs utm_content={{{{ad.id}}}} on the ad URLs and a matching "
            f"utm_content field mapped in the CRM. {tagged} of {len(ad_rows)} ads carry it today."),
    }


@router.post("/ads/accounts")
async def attach_account(body: dict, user: User = Depends(require_role("owner", "admin")),
                         s: AsyncSession = Depends(get_session)):
    """Attach one ad account to an existing meta_ads integration. Two steps on purpose: one
    System User token routinely carries several accounts."""
    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == user.tenant_id, Integration.provider == "meta_ads",
        Integration.id == body.get("integration_id")))).scalar_one_or_none()
    if integ is None:
        raise HTTPException(404, "Connect Meta Ads first, then attach an account.")
    ext = str(body.get("external_id") or "").strip()
    if not ext:
        raise HTTPException(400, "external_id is required (act_...)")
    # An account already here is REPAIRED, not refused.
    #
    # Refusing was a dead end: the first live attach left a row with a null business_id and a null
    # timezone, the way to fix it is to attach again, and there is no Remove control in the UI -
    # so the only route out was database surgery. Somebody re-submitting this form is trying to
    # mend the connection, which is exactly when it should work.
    existing = (await s.execute(select(AdAccount).where(
        AdAccount.tenant_id == user.tenant_id, AdAccount.platform == "meta",
        AdAccount.external_id == ext))).scalar_one_or_none()

    # Which entity BOOKS this spend. Its archetype selects the funnel, so an account attached to
    # nothing shows the click layer and says "no funnel for this entity" - which is what the first
    # live connect did, because the form sent no business_key and nothing filled it in.
    #
    # Falls back to the entity the integrations view already resolved by ROLE for this provider,
    # so the default is right for the workspace rather than absent.
    biz = None
    if body.get("business_key"):
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == user.tenant_id,
            Business.key == body["business_key"]))).scalar_one_or_none()
    if biz is None:
        from ..services.integrations_view import CONNECTABLE_KIND
        from ..services import roles
        biz = await roles.primary(s, user.tenant_id, CONNECTABLE_KIND.get("meta_ads"))
    # Ask Meta who this account is, so the name, currency and TIMEZONE are real rather than
    # placeholders. The timezone is not cosmetic: Meta's day boundaries follow the account's
    # reporting zone, and a null one silently falls back to the server's - which puts spend on
    # the wrong day either side of midnight and makes every range disagree with Ads Manager.
    name, currency, tz = str(body.get("name") or ext)[:200], "USD", body.get("timezone_name")
    try:
        from ..integrations import meta_ads as _meta
        from ..security import dec
        info = await _meta.ping(dec(integ.access_token_enc), ext)
        name = str(info.get("name") or name)[:200]
        currency = str(info.get("currency") or currency)[:8]
        tz = info.get("timezone_name") or tz
    except Exception as e:  # noqa: BLE001 - a bad id should fail the attach with Meta's own words
        raise HTTPException(400, f"Meta rejected {ext}: {e}")

    if existing is not None:
        existing.integration_id = integ.id
        existing.name, existing.currency = name, currency
        existing.timezone_name = tz or existing.timezone_name
        # Only fill a missing entity - never move an account somebody deliberately re-pointed.
        if existing.business_id is None and biz is not None:
            existing.business_id = biz.id
        existing.status = "active"
        existing.last_error = None          # the previous failure is not this attempt's news
        audit(s, user.tenant_id, user.id, "ads.account_repaired", "ad_account", existing.id,
              {"external_id": ext, "business": biz.key if biz else None})
        await s.commit()
        return {"id": str(existing.id), "external_id": ext, "repaired": True}

    acct = AdAccount(tenant_id=user.tenant_id, integration_id=integ.id,
                     business_id=biz.id if biz else None, platform="meta", external_id=ext,
                     name=name, currency=currency, timezone_name=tz)
    s.add(acct)
    audit(s, user.tenant_id, user.id, "ads.account_attached", "ad_account", None, {"external_id": ext})
    await s.commit()
    return {"id": str(acct.id), "external_id": acct.external_id, "repaired": False}


@router.get("/ads/drill/{metric}")
async def drill(metric: str, account: str | None = Query(None), period: str = Query("30d"),
                start: dt.date | None = None, end: dt.date | None = None,
                basis: str = Query("cohort"), campaign: str | None = Query(None),
                user: User = Depends(require_tab("ads")),
                s: AsyncSession = Depends(get_session)):
    """Who is behind one funnel rung.

    Tab-gated, exactly like the number it opens - a drill inherits its tile's permission. It
    takes the SAME account, period, basis and campaign the page was showing, because a drill that
    resolves its own window would open a different population than the figure that was clicked.
    """
    acct = await _account(s, user.tenant_id, account) if account else await _first_account(s, user.tenant_id)
    if acct is None:
        raise HTTPException(404, "No ad account is connected")
    camp = await _campaign(s, user.tenant_id, acct, campaign) if campaign else None
    s_day, e_day, _ = A.ads_period(period, start, end, acct.timezone_name)
    from ..services.ads_drill import drill_ads
    out = await drill_ads(s, user.tenant_id, acct, metric, s_day, e_day, basis,
                          campaign=(camp.id if camp is not None else None))
    if out is None:
        raise HTTPException(404, f"Nothing to open for {metric}")
    return out


@router.get("/ads/grouping")
async def grouping(account: str | None = Query(None), period: str = Query("90d"),
                   user: User = Depends(require_tab("ads")),
                   s: AsyncSession = Depends(get_session)):
    """The rules, the shipped defaults, and every campaign with its spend and current group.

    One payload rather than three, because the editor is useless without all of it at once: the
    thing that makes rule-writing tractable is seeing WHICH campaigns land where as you type, and
    that requires the campaign list beside the rules. Read-only and tab-gated; writing is the
    PATCH below and needs owner or admin.
    """
    acct = await _account(s, user.tenant_id, account) if account else await _first_account(s, user.tenant_id)
    if acct is None:
        return {"connected": False, "rules": None, "defaults": R.DEFAULT_GROUP_RULES,
                "campaigns": []}

    s_day, e_day, _ = A.ads_period(period, None, None, acct.timezone_name)
    spend: dict = {}
    rows = await s.execute(
        select(AdInsightDaily.campaign_id, func.sum(AdInsightDaily.spend))
        .where(AdInsightDaily.tenant_id == user.tenant_id,
               AdInsightDaily.ad_account_id == acct.id,
               AdInsightDaily.level == "campaign",
               AdInsightDaily.occurred_on >= s_day, AdInsightDaily.occurred_on <= e_day)
        .group_by(AdInsightDaily.campaign_id))
    for cid, total in rows:
        if cid is not None:
            spend[cid] = float(total or 0)

    camps = list((await s.execute(select(AdCampaign).where(
        AdCampaign.tenant_id == user.tenant_id,
        AdCampaign.ad_account_id == acct.id))).scalars())
    rules = acct.group_rules            # None means "the defaults", which is a real state
    out = [{"id": str(c.id), "name": c.name,
            "spend": spend.get(c.id, 0.0),
            "group": R.classify_campaign(c.name, rules)} for c in camps]
    # Highest spend first: a campaign in the wrong bucket matters in proportion to what it cost.
    out.sort(key=lambda c: -c["spend"])
    return {
        "connected": True,
        "account": str(acct.id),
        "rules": rules,
        "defaults": R.DEFAULT_GROUP_RULES,
        "using_defaults": rules is None,
        "match_kinds": list(R.MATCH_KINDS),
        "fallback": R.FALLBACK_GROUP,
        "campaigns": out,
        "unmatched": sum(1 for c in out if c["group"] == R.FALLBACK_GROUP),
        "period": [s_day.isoformat(), e_day.isoformat()],
    }


@router.post("/ads/grouping/preview")
async def grouping_preview(body: dict, account: str | None = Query(None),
                           period: str = Query("90d"),
                           user: User = Depends(require_tab("ads")),
                           s: AsyncSession = Depends(get_session)):
    """What a PROPOSED rule set would do, without saving it.

    Server-side on purpose. Classifying in the browser would mean two implementations of the same
    rules in two languages, and the one the editor shows would be the one nobody tests - so the
    preview would drift from the answer and quietly stop predicting it. Section 02 already makes
    this argument about findings; it applies harder here, because this is the screen somebody
    uses to DECIDE.

    Writes nothing. Invalid rules come back as problems rather than a 400, because this is called
    on every keystroke and a half-typed rule is not an error yet.
    """
    rules = body.get("rules")
    problems = R.validate_group_rules(rules)
    acct = await _account(s, user.tenant_id, account) if account else await _first_account(s, user.tenant_id)
    if acct is None:
        return {"connected": False, "campaigns": [], "problems": problems}

    s_day, e_day, _ = A.ads_period(period, None, None, acct.timezone_name)
    spend: dict = {}
    for cid, total in await s.execute(
            select(AdInsightDaily.campaign_id, func.sum(AdInsightDaily.spend))
            .where(AdInsightDaily.tenant_id == user.tenant_id,
                   AdInsightDaily.ad_account_id == acct.id,
                   AdInsightDaily.level == "campaign",
                   AdInsightDaily.occurred_on >= s_day, AdInsightDaily.occurred_on <= e_day)
            .group_by(AdInsightDaily.campaign_id)):
        if cid is not None:
            spend[cid] = float(total or 0)

    camps = list((await s.execute(select(AdCampaign).where(
        AdCampaign.tenant_id == user.tenant_id,
        AdCampaign.ad_account_id == acct.id))).scalars())
    # Unusable rules preview as the DEFAULTS rather than as an empty screen, so a half-typed rule
    # does not flash every campaign into Other and read as "you just broke it".
    use = None if problems else rules
    out = [{"id": str(c.id), "name": c.name, "spend": spend.get(c.id, 0.0),
            "group": R.classify_campaign(c.name, use)} for c in camps]
    out.sort(key=lambda c: -c["spend"])
    return {"connected": True, "campaigns": out, "problems": problems,
            "unmatched": sum(1 for c in out if c["group"] == R.FALLBACK_GROUP)}


@router.patch("/ads/accounts/{account_id}")
async def patch_account(account_id: str, body: dict,
                        user: User = Depends(require_role("owner", "admin")),
                        s: AsyncSession = Depends(get_session)):
    acct = await _account(s, user.tenant_id, account_id)
    # Validate BEFORE writing. This endpoint used to setattr whatever JSON it was handed, and
    # classify_campaign is defensive enough not to raise on nonsense - which is worse rather than
    # better, because a malformed rule set silently classifies every campaign as Other and the
    # symptom is indistinguishable from a naming drift somebody would then hunt for in Ads Manager.
    if "group_rules" in body:
        problems = R.validate_group_rules(body["group_rules"])
        if problems:
            raise HTTPException(400, "; ".join(problems))
    for field in ("group_rules", "lead_actions", "thresholds", "funnel_override", "utm_template",
                  "status", "timezone_name", "name"):
        if field in body:
            setattr(acct, field, body[field])
    if "business_key" in body:
        b = (await s.execute(select(Business).where(
            Business.tenant_id == user.tenant_id,
            Business.key == body["business_key"]))).scalar_one_or_none()
        acct.business_id = b.id if b else None
    audit(s, user.tenant_id, user.id, "ads.account_updated", "ad_account", acct.id,
          {"fields": sorted(body)})
    await s.commit()
    return {"ok": True}


@router.delete("/ads/accounts/{account_id}")
async def delete_account(account_id: str, purge: bool = False,
                         user: User = Depends(require_role("owner", "admin")),
                         s: AsyncSession = Depends(get_session)):
    acct = await _account(s, user.tenant_id, account_id)
    ext = acct.external_id
    if not purge:
        acct.status = "archived"
    else:
        await s.delete(acct)          # CASCADE removes campaigns, ads and insight rows
    audit(s, user.tenant_id, user.id, "ads.account_removed", "ad_account", acct.id,
          {"external_id": ext, "purge": purge})
    await s.commit()
    return {"ok": True, "purged": purge}
