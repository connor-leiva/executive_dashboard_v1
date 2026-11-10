"""The morning ads note. Built from rows we already have. It does not call Meta.

Spend versus plan is yesterday. Recommendations, fatigue, and cost per seat use
the seven days ending yesterday, because one day is too thin to kill an ad.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Ad, AdAccount, AdCampaign, AdDigest, AdInsightDaily, AdWrite, User
from .ads import ctr
from .ads_brand_funnels import build_brand_funnels
from .ads_rules import FALLBACK_GROUP, classify_campaign, fatigue_signal
from .ads_write import recommendation_items


def account_yesterday(timezone_name: str | None, now: dt.datetime | None = None) -> dt.date:
    tz = ZoneInfo(timezone_name or "America/Denver")
    moment = now or dt.datetime.now(tz)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=tz)
    return moment.astimezone(tz).date() - dt.timedelta(days=1)


async def _fatigue(s: AsyncSession, account: AdAccount, day: dt.date) -> list[str]:
    recent_start = day - dt.timedelta(days=6)
    prior_start = recent_start - dt.timedelta(days=7)
    rows = (await s.execute(select(AdInsightDaily).where(
        AdInsightDaily.tenant_id == account.tenant_id, AdInsightDaily.ad_account_id == account.id,
        AdInsightDaily.level == "ad",
        AdInsightDaily.occurred_on >= prior_start, AdInsightDaily.occurred_on <= day))).scalars()
    ads = {a.id: a for a in (await s.execute(select(Ad).where(
        Ad.tenant_id == account.tenant_id, Ad.ad_account_id == account.id))).scalars()}
    buckets: dict = {}
    for row in rows:
        if row.ad_id is None:
            continue
        slot = "recent" if row.occurred_on >= recent_start else "prior"
        buckets.setdefault(row.ad_id, {"recent": [], "prior": []})[slot].append(row)
    names = []
    for ad_id, slots in buckets.items():
        def _ctr(rs):
            return ctr(sum(x.inline_link_clicks or 0 for x in rs), sum(x.impressions or 0 for x in rs))
        def _freq(rs):
            vals = [float(x.frequency) for x in rs if x.frequency is not None]
            return sum(vals) / len(vals) if vals else None
        if fatigue_signal(_ctr(slots["recent"]), _ctr(slots["prior"]),
                          _freq(slots["recent"]), _freq(slots["prior"])):
            names.append(ads[ad_id].name if ads.get(ad_id) else "an ad")
    return names


async def compose(s: AsyncSession, account: AdAccount, day: dt.date) -> dict:
    window_start = day - dt.timedelta(days=6)
    camps = {c.id: c for c in (await s.execute(select(AdCampaign).where(
        AdCampaign.tenant_id == account.tenant_id, AdCampaign.ad_account_id == account.id))).scalars()}
    yday = (await s.execute(select(AdInsightDaily).where(
        AdInsightDaily.tenant_id == account.tenant_id, AdInsightDaily.ad_account_id == account.id,
        AdInsightDaily.level == "campaign", AdInsightDaily.occurred_on == day))).scalars()
    spend_by: dict[str, float] = {}
    for row in yday:
        camp = camps.get(row.campaign_id)
        group = classify_campaign(camp.name if camp else "", account.group_rules) if camp else FALLBACK_GROUP
        spend_by[group] = spend_by.get(group, 0.0) + float(row.spend or 0)
    plans = account.weekly_budgets or {}
    brands = []
    for name in sorted(set(spend_by) | set(plans), key=lambda n: -spend_by.get(n, 0)):
        plan = plans.get(name)
        daily_plan = None if plan in (None, "") else round(float(plan) / 7.0, 2)
        spent = round(spend_by.get(name, 0.0), 2)
        brands.append({
            "name": name, "spent": spent, "daily_plan": daily_plan,
            "pace": None if not daily_plan else round(spent / daily_plan * 100, 1),
        })

    recs = await recommendation_items(s, account, window_start, day)
    actions = []
    writes = (await s.execute(select(AdWrite).where(
        AdWrite.ad_account_id == account.id, AdWrite.source == "autopilot"))).scalars()
    for row in writes:
        when = row.created_at
        if when is not None and when.date() == day:
            actions.append({
                "name": row.object_name, "action": row.action, "status": row.status,
                "reason": row.reason, "dry_run": row.dry_run,
            })

    funnels = await build_brand_funnels(
        s, account.tenant_id, account.business_id, window_start, day,
        {"spend": sum(spend_by.get(n, 0) for n in ("Forum Dallas", "Forum Scottsdale", "Forum")),
         "clicks": 0, "leads": 0},
        {"spend": spend_by.get("Edge Webinar", 0), "clicks": 0, "leads": 0})
    seats = []
    for key in ("forum", "edge"):
        ladder = funnels[key]
        rung = next((r for r in ladder["rungs"] if r["key"] in ("seat", "purchase")), None)
        seats.append({
            "name": ladder["title"],
            "spend": ladder["spend"],
            "seats": None if not rung or rung.get("missing") else rung.get("n"),
            "cost_per_seat": None if not rung else rung.get("cost_per"),
        })

    return {
        "day": day.isoformat(),
        "account": account.name,
        "brands": brands,
        "recommendations": [
            {"name": r["name"], "verdict": r["verdict"], "reason": r["reason"], "group": r.get("group")}
            for r in recs if r["verdict"] != "keep"
        ],
        "kept": sum(1 for r in recs if r["verdict"] == "keep"),
        "autopilot": actions,
        "fatigue": await _fatigue(s, account, day),
        "seats": seats,
    }


def render_text(body: dict) -> str:
    lines = [f"Ads for {body.get('day')} — {body.get('account')}", ""]
    lines.append("Yesterday vs plan")
    for brand in body.get("brands") or []:
        plan = "no plan" if brand.get("daily_plan") is None else f"${brand['daily_plan']:,.0f}/day"
        lines.append(f"  {brand['name']}: ${brand['spent']:,.0f} against {plan}")
    lines.append("")
    lines.append("Scale / Keep / Kill")
    recs = body.get("recommendations") or []
    if not recs:
        lines.append(f"  Nothing to change. {body.get('kept', 0)} stayed keep.")
    for rec in recs:
        lines.append(f"  {rec['verdict'].title()}: {rec['reason']}")
    lines.append("")
    lines.append("Autopilot")
    if not body.get("autopilot"):
        lines.append("  No autopilot actions.")
    for act in body.get("autopilot") or []:
        lines.append(f"  {act['status']}: {act['action']} {act.get('name') or ''}")
    lines.append("")
    lines.append("Fatigue")
    flags = body.get("fatigue") or []
    lines.append("  " + (", ".join(flags) if flags else "None."))
    lines.append("")
    lines.append("Seats")
    for seat in body.get("seats") or []:
        n = "not recorded" if seat.get("seats") is None else seat["seats"]
        cost = "—" if seat.get("cost_per_seat") is None else f"${seat['cost_per_seat']:,.0f}"
        lines.append(f"  {seat['name']}: {n} at {cost} each")
    return "\n".join(lines)


async def deliver(s: AsyncSession, account: AdAccount, day: dt.date | None = None) -> dict:
    """Store the morning note once, and email it when a mailer is configured."""
    day = day or account_yesterday(account.timezone_name)
    existing = (await s.execute(select(AdDigest).where(
        AdDigest.ad_account_id == account.id, AdDigest.digest_on == day))).scalar_one_or_none()
    if existing is not None:
        return existing.body
    body = await compose(s, account, day)
    emailed = False
    people = (await s.execute(select(User).where(
        User.tenant_id == account.tenant_id, User.status == "active",
        User.role.in_(("owner", "admin"))))).scalars()
    addresses = [u.email for u in people if u.email]
    if addresses:
        from .mailer import send
        text = render_text(body)
        html = "<pre style=\"font-family:sans-serif\">" + text.replace("&", "&amp;").replace("<", "&lt;") + "</pre>"
        emailed = await send(
            addresses, f"Ads {day.isoformat()}", html, text,
            idempotency_key=f"ads-digest-{account.id}-{day.isoformat()}")
    s.add(AdDigest(tenant_id=account.tenant_id, ad_account_id=account.id, digest_on=day,
                   body=body, emailed=bool(emailed)))
    await s.flush()
    return body
