"""The only door a Meta write goes through.

Live writes require three things at once: an admin (or autopilot), the token
having accepted a validate_only call, and the kill switch off. Anything else
is stored as a preview. Meta's answer, including a refusal, is stored on the
row and returned to the page.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations.meta_ads import MetaError
from ..integrations.meta_ads_write import FIX_TAGS, PATH_NOTES, graph_post, is_scope_miss, plan_request
from ..models import Ad, AdAccount, AdCampaign, AdInsightDaily, AdWrite, User
from ..security import dec
from .audit import audit
from . import ads_rules as rules
from .ads_brand_funnels import seats_by_ad
from .ads_recommend import DEFAULT_GUARD, clamp_increase, recommend
from .sync import is_confirmed_seat

RECONNECT = "Reconnect Meta to enable changes"


def scope_block(account: AdAccount, role: str | None = None) -> dict:
    can = account.write_scope == "ads_management" and not account.writes_killed
    if account.writes_killed:
        message = "Ad changes are stopped. Apply shows a preview until the switch is turned back on."
    elif account.write_scope != "ads_management":
        message = RECONNECT
    else:
        message = None
    return {
        "can_write": bool(can),
        "can_manage": role in ("owner", "admin"),
        "scope": account.write_scope,
        "killed": bool(account.writes_killed),
        "reconnect": None if account.write_scope == "ads_management" else RECONNECT,
        "detail": account.write_scope_detail,
        "message": message,
        "autopilot": account.autopilot or {},
        "paths": PATH_NOTES,
    }


def _why_dry(account: AdAccount, requested: bool, source: str) -> str | None:
    if requested:
        return "Preview only. Meta was asked to validate the change and not apply it."
    if account.writes_killed:
        return "Ad changes are stopped, so this stayed a preview."
    if account.write_scope != "ads_management":
        return "Reconnect Meta to enable changes. This stayed a preview."
    if source == "autopilot":
        return None
    return None


async def _token(s: AsyncSession, account: AdAccount) -> str | None:
    from ..models import Integration
    integ = await s.get(Integration, account.integration_id)
    if integ is None or not integ.access_token_enc:
        return None
    try:
        return dec(integ.access_token_enc)
    except Exception:  # noqa: BLE001 - a bad key is "cannot write", not a 500
        return None


async def _object(s: AsyncSession, account: AdAccount, level: str, external_id: str):
    if level == "campaign":
        return (await s.execute(select(AdCampaign).where(
            AdCampaign.tenant_id == account.tenant_id, AdCampaign.ad_account_id == account.id,
            AdCampaign.external_id == external_id))).scalar_one_or_none()
    if level == "ad":
        return (await s.execute(select(Ad).where(
            Ad.tenant_id == account.tenant_id, Ad.ad_account_id == account.id,
            Ad.external_id == external_id))).scalar_one_or_none()
    return None


def _before(obj, level: str) -> dict:
    if obj is None:
        return {}
    if level == "campaign":
        return {
            "status": obj.status,
            "daily_budget": float(obj.daily_budget) if obj.daily_budget is not None else None,
            "lifetime_budget": float(obj.lifetime_budget) if obj.lifetime_budget is not None else None,
            "name": obj.name,
        }
    if level == "ad":
        return {
            "status": obj.status, "name": obj.name,
            "url_tags": obj.url_tags, "creative_id": obj.creative_external_id,
            "adset_id": obj.adset_external_id,
        }
    return {"name": getattr(obj, "name", None)}


def _apply_local(obj, level: str, action: str, params: dict) -> None:
    if obj is None:
        return
    if action == "pause":
        obj.status = "PAUSED"
    elif action == "activate":
        obj.status = "ACTIVE"
    elif action == "set_daily_budget" and level == "campaign":
        obj.daily_budget = Decimal(str(params["daily_budget"]))
    elif action == "set_lifetime_budget" and level == "campaign":
        obj.lifetime_budget = Decimal(str(params["lifetime_budget"]))
    elif action in ("set_url_tags", "fix_tagging") and level == "ad":
        obj.url_tags = params.get("url_tags") or FIX_TAGS


async def execute(s: AsyncSession, account: AdAccount, *, actor: User | None, action: str,
                  level: str, external_id: str, reason: str, params: dict | None = None,
                  dry_run: bool = True, source: str = "user", undo_of=None) -> dict:
    """One change. Returns the row the page renders, including Meta's error."""
    params = dict(params or {})
    forced = False
    if source != "autopilot" and actor is not None and actor.role not in ("owner", "admin"):
        dry_run = True
        forced = True
    if account.writes_killed or account.write_scope != "ads_management":
        if not dry_run:
            forced = True
        dry_run = True
    note_why = _why_dry(account, dry_run and not forced, source)
    if forced and account.writes_killed:
        note_why = "Ad changes are stopped, so this stayed a preview."
    elif forced and account.write_scope != "ads_management":
        note_why = "Reconnect Meta to enable changes. This stayed a preview."
    elif forced:
        note_why = "Only an admin can change ads, so this stayed a preview."

    obj = await _object(s, account, level, external_id) if level != "adset" else None
    before = _before(obj, level)
    creative_id = before.get("creative_id") if level == "ad" else None
    if level == "adset":
        before = {"adset_id": external_id}
    try:
        path, body, kind = plan_request(
            action, external_id, creative_id=creative_id, currency=account.currency or "USD",
            params=params, account_external_id=account.external_id)
    except (KeyError, ValueError) as e:
        return {"ok": False, "dry_run": True, "meta_error": str(e), "action": action}

    token = await _token(s, account)
    validated = False
    meta_error = None
    remote = None
    if token is None:
        meta_error = "No Meta token is stored for this account."
    else:
        try:
            remote = await graph_post(token, path, body, validate_only=dry_run)
            validated = True
        except MetaError as e:
            meta_error = str(e)
            if is_scope_miss(e):
                account.write_scope = "ads_read"
                account.write_scope_detail = str(e)[:500]
                dry_run = True
                note_why = "Meta refused the change because the token lacks ads_management. Reconnect to enable changes."
        except Exception as e:  # noqa: BLE001 - the page has to show the failure
            meta_error = f"{type(e).__name__}: {e}"

    applied = bool(remote) and not dry_run and not meta_error
    if applied:
        _apply_local(obj, level, action, params)
    after = {**body}
    if isinstance(remote, dict):
        for key in ("copied_ad_id", "copied_adset_id", "copied_campaign_id", "id"):
            if remote.get(key):
                after["created_id"] = str(remote[key])
    status = "applied" if applied else ("failed" if meta_error and not dry_run else "preview")
    if meta_error and dry_run:
        status = "preview"
    row = AdWrite(
        tenant_id=account.tenant_id, ad_account_id=account.id,
        actor_user_id=getattr(actor, "id", None),
        actor_label=(getattr(actor, "name", None) or source),
        source=source, action=action, object_level=level, object_external_id=external_id,
        object_name=(before.get("name") or params.get("name")),
        before=before, after=after, reason=reason,
        dry_run=bool(dry_run), validated=validated,
        validation_note=" ".join(p for p in (PATH_NOTES.get(kind), note_why) if p),
        status=status, meta_error=meta_error, undo_of_id=undo_of,
    )
    s.add(row)
    audit(s, account.tenant_id, getattr(actor, "id", None),
          "ads.write", "ad_write", external_id,
          {"action": action, "level": level, "dry_run": dry_run or not applied,
           "status": status, "reason": reason, "before": before, "after": after,
           "meta_error": meta_error},
          category="Integrations",
          summary=f"{'Preview' if status != 'applied' else 'Applied'} {action} on {external_id}",
          actor_label=row.actor_label, actor_type="system" if source == "autopilot" else "user")
    await s.flush()
    return {
        "ok": meta_error is None, "id": str(row.id), "action": action, "level": level,
        "external_id": external_id, "name": row.object_name, "dry_run": status != "applied",
        "forced_dry_run": forced or (status != "applied" and not (dry_run and not forced)),
        "validated": validated, "validation_note": row.validation_note,
        "meta_error": meta_error, "status": status, "before": before, "after": after,
        "reason": reason,
    }


async def probe_scope(s: AsyncSession, account: AdAccount, token: str) -> str:
    """One validate_only status post. Success means the token can manage ads."""
    camp = (await s.execute(select(AdCampaign).where(
        AdCampaign.tenant_id == account.tenant_id, AdCampaign.ad_account_id == account.id
    ).limit(1))).scalar_one_or_none()
    if camp is None:
        account.write_scope = account.write_scope or "unknown"
        account.write_scope_detail = "No campaign is synced yet, so the write scope was not checked."
        return account.write_scope
    try:
        await graph_post(token, camp.external_id,
                         {"status": camp.status or "PAUSED"}, validate_only=True)
        account.write_scope = "ads_management"
        account.write_scope_detail = "Meta accepted a validate_only status check."
    except MetaError as e:
        if is_scope_miss(e):
            account.write_scope = "ads_read"
            account.write_scope_detail = str(e)[:500]
        else:
            account.write_scope = "unknown"
            account.write_scope_detail = str(e)[:500]
    except Exception as e:  # noqa: BLE001
        account.write_scope = "unknown"
        account.write_scope_detail = f"{type(e).__name__}: {e}"[:500]
    return account.write_scope


def _guard_for(account: AdAccount, group: str | None) -> dict:
    raw = (account.autopilot or {}).get(group or "") or {}
    return {**DEFAULT_GUARD, **raw} if isinstance(raw, dict) else dict(DEFAULT_GUARD)


async def _increased_today(s, account: AdAccount, external_id: str) -> float:
    today = dt.datetime.now(dt.timezone.utc).date()
    rows = (await s.execute(select(AdWrite).where(
        AdWrite.ad_account_id == account.id, AdWrite.object_external_id == external_id,
        AdWrite.action == "set_daily_budget", AdWrite.status == "applied"))).scalars()
    pct = 0.0
    for row in rows:
        if row.created_at and row.created_at.date() == today:
            before = (row.before or {}).get("daily_budget")
            after = (row.after or {}).get("daily_budget")
            if before and after and float(before):
                pct += (float(after) - float(before)) / float(before) * 100
    return pct


async def recommendation_items(s: AsyncSession, account: AdAccount, start, end) -> list[dict]:
    """Ads and ad sets in the window, each with a verdict."""
    ads = {a.id: a for a in (await s.execute(select(Ad).where(
        Ad.tenant_id == account.tenant_id, Ad.ad_account_id == account.id))).scalars()}
    camps = {c.id: c for c in (await s.execute(select(AdCampaign).where(
        AdCampaign.tenant_id == account.tenant_id, AdCampaign.ad_account_id == account.id))).scalars()}
    insights = (await s.execute(select(AdInsightDaily).where(
        AdInsightDaily.tenant_id == account.tenant_id, AdInsightDaily.ad_account_id == account.id,
        AdInsightDaily.level == "ad",
        AdInsightDaily.occurred_on >= start, AdInsightDaily.occurred_on <= end))).scalars()
    seat_rows = []
    if account.business_id:
        from ..models import MetricRecord
        seat_rows = [r for r in (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == account.tenant_id, MetricRecord.business_id == account.business_id,
            MetricRecord.kind == "forum_stage_event", MetricRecord.segment == "vip_guest_seat",
            MetricRecord.occurred_on >= start, MetricRecord.occurred_on <= end))).scalars()
            if is_confirmed_seat((r.meta or {}).get("to_stage"), (r.meta or {}).get("stage_id"))]
    seat_counts = seats_by_ad(seat_rows, {a.external_id for a in ads.values()})
    from .ads import cpl, ctr
    buckets: dict = {}
    for row in insights:
        ad = ads.get(row.ad_id)
        if ad is None:
            continue
        d = buckets.setdefault(ad.id, {"spend": 0.0, "impressions": 0, "link_clicks": 0, "leads": 0})
        d["spend"] += float(row.spend or 0)
        d["impressions"] += row.impressions or 0
        d["link_clicks"] += row.inline_link_clicks or 0
        d["leads"] += row.leads or 0
    items = []
    adset: dict = {}
    for ad_id, d in buckets.items():
        ad = ads[ad_id]
        camp = camps.get(ad.campaign_id)
        group = rules.classify_campaign(camp.name, account.group_rules) if camp else rules.FALLBACK_GROUP
        seats = None if seat_counts is None else seat_counts.get(ad.external_id, 0)
        row = {
            "level": "ad", "external_id": ad.external_id, "name": ad.name, "group": group,
            "spend": d["spend"], "impressions": d["impressions"], "link_clicks": d["link_clicks"],
            "leads": d["leads"], "ctr": ctr(d["link_clicks"], d["impressions"]),
            "cpl": cpl(d["spend"], d["leads"]),
            "seats": seats,
            "cost_per_seat": (d["spend"] / seats) if seats else None,
            "fatigue": False,
            "daily_budget": float(camp.daily_budget) if camp and camp.daily_budget is not None else None,
            "budget_external_id": camp.external_id if camp and camp.daily_budget is not None else None,
        }
        items.append(recommend(row, account.thresholds, _guard_for(account, group)))
        if ad.adset_external_id:
            g = adset.setdefault(ad.adset_external_id, {
                "level": "adset", "external_id": ad.adset_external_id,
                "name": ad.adset_name or "Ad set", "group": group,
                "spend": 0.0, "impressions": 0, "link_clicks": 0, "leads": 0, "seats": 0 if seats is not None else None,
                "daily_budget": row["daily_budget"],
            })
            g["spend"] += d["spend"]
            g["impressions"] += d["impressions"]
            g["link_clicks"] += d["link_clicks"]
            g["leads"] += d["leads"]
            if seats is not None and g["seats"] is not None:
                g["seats"] += seats
    for g in adset.values():
        g["ctr"] = ctr(g["link_clicks"], g["impressions"])
        g["cpl"] = cpl(g["spend"], g["leads"])
        g["cost_per_seat"] = (g["spend"] / g["seats"]) if g.get("seats") else None
        items.append(recommend(g, account.thresholds, _guard_for(account, g["group"])))
    order = {"kill": 0, "scale": 1, "keep": 2}
    items.sort(key=lambda r: (order.get(r["verdict"], 9), -float(r.get("spend") or 0)))
    return items


async def apply_recommendations(s: AsyncSession, account: AdAccount, actor: User | None,
                                picks: list[dict], *, dry_run: bool, start, end,
                                source: str = "user") -> dict:
    """Apply the server's current verdict for each picked object. The client does not choose the action."""
    items = await recommendation_items(s, account, start, end)
    by_key = {(i["level"], i["external_id"]): i for i in items}
    results = []
    for pick in picks:
        item = by_key.get((pick.get("level"), pick.get("external_id")))
        if item is None or not item.get("action"):
            results.append({"ok": False, "external_id": pick.get("external_id"),
                            "meta_error": "Nothing to apply for this one.", "dry_run": True})
            continue
        params = dict(item.get("proposed") or {})
        level, ext = item["level"], item["external_id"]
        if item["action"] == "set_daily_budget":
            already = await _increased_today(s, account, item.get("budget_external_id") or item["external_id"])
            weekly = (account.weekly_budgets or {}).get(item.get("group"))
            allowed, why = clamp_increase(
                item.get("daily_budget"), params.get("daily_budget"), weekly_budget=weekly,
                week_spend=item.get("spend"), today_spend=0, already_pct=already,
                guard=_guard_for(account, item.get("group")))
            if allowed is None or not item.get("budget_external_id"):
                results.append({"ok": False, "external_id": item["external_id"], "name": item["name"],
                                "verdict": item["verdict"], "dry_run": True,
                                "meta_error": why or "No campaign budget is stored to raise."})
                continue
            params = {"daily_budget": allowed}
            level, ext = "campaign", item["budget_external_id"]
        if source == "autopilot" and _guard_for(account, item.get("group")).get("pause_only") and item["action"] != "pause":
            results.append({"ok": False, "external_id": item["external_id"], "name": item["name"],
                            "verdict": item["verdict"], "dry_run": True,
                            "meta_error": "Pause-only is on for this brand, so a budget increase was not applied."})
            continue
        results.append(await execute(
            s, account, actor=actor, action=item["action"], level=level, external_id=ext,
            reason=item["reason"], params=params, dry_run=dry_run, source=source))
    live = scope_block(account, getattr(actor, "role", None))
    return {"results": results, "can_write": live["can_write"],
            "forced_dry_run": any(r.get("dry_run") for r in results) and not dry_run,
            "message": live["message"]}


async def undo_last(s: AsyncSession, account: AdAccount, actor: User) -> dict:
    row = (await s.execute(select(AdWrite).where(
        AdWrite.ad_account_id == account.id, AdWrite.status == "applied"
    ).order_by(AdWrite.created_at.desc()).limit(1))).scalar_one_or_none()
    if row is None:
        return {"ok": False, "meta_error": "Nothing to undo."}
    action, params = _inverse(row)
    if action is None:
        return {"ok": False, "meta_error": "That change cannot be undone from here."}
    result = await execute(
        s, account, actor=actor, action=action, level=row.object_level,
        external_id=(row.after or {}).get("created_id") or row.object_external_id,
        reason=f"Undo {row.action}", params=params, dry_run=False, source="undo", undo_of=row.id)
    if result.get("status") == "applied":
        row.status = "undone"
    return result


def _inverse(row: AdWrite) -> tuple[str | None, dict]:
    before = row.before or {}
    if row.action == "pause" and before.get("status") == "ACTIVE":
        return "activate", {}
    if row.action == "activate" and before.get("status") == "PAUSED":
        return "pause", {}
    if row.action == "set_daily_budget" and before.get("daily_budget") is not None:
        return "set_daily_budget", {"daily_budget": before["daily_budget"]}
    if row.action == "set_lifetime_budget" and before.get("lifetime_budget") is not None:
        return "set_lifetime_budget", {"lifetime_budget": before["lifetime_budget"]}
    if row.action in ("set_url_tags", "fix_tagging") and before.get("url_tags") is not None:
        return "set_url_tags", {"url_tags": before["url_tags"]}
    if row.action in ("duplicate", "create_ad") and (row.after or {}).get("created_id"):
        return "pause", {}
    if row.action == "pause":
        return "activate", {}
    if row.action == "activate":
        return "pause", {}
    return None, {}


async def run_autopilot_for(s: AsyncSession, account: AdAccount, start, end) -> list[dict]:
    """Kill and scale for brands that opted in. Off unless the brand says on."""
    if account.writes_killed:
        return []
    brands = [name for name, cfg in (account.autopilot or {}).items()
              if isinstance(cfg, dict) and cfg.get("on")]
    if not brands:
        return []
    items = [i for i in await recommendation_items(s, account, start, end)
             if i.get("group") in brands and i.get("action") and i["verdict"] in ("kill", "scale")]
    return (await apply_recommendations(
        s, account, None, [{"level": i["level"], "external_id": i["external_id"]} for i in items],
        dry_run=False, start=start, end=end, source="autopilot"))["results"]
