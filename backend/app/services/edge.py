"""The Edge focused view — /api/v1/edge. "The Edge" is a Spring + Justin Nelson membership
product that runs as a SEGMENT of the existing Forum GoHighLevel + Stripe (not its own account):
its members are tagged in the same GHL location, and its payments carry "The Edge" in the
description on the same legacy Stripe account. It mirrors the Forum's structure exactly — reusing
the Forum's card builders pointed at edge_* metric_record kinds (reuse, not copy) — in its own
edge_ data namespace so nothing leaks between programs. Steady membership; no cohort launch."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import MetricRecord, Business, Integration
from .metrics import _period_range
from . import roles
from . import forum as F

# The Edge's sales-funnel stages (tenant-editable via config['recruiting_stage_groups']).
# A sensible membership-funnel default until Spring maps The Edge's real GHL stages.
EDGE_FUNNEL_GROUPS = [
    ["Applied", ["opt in", "application", "app submitted", "no app"]],
    ["Appointment", ["appointment", "needs decision"]],
    ["Committed", ["payment sent", "committed"]],
    ["Onboarding", ["payment received", "onboarded", "fulfillment"]],
]


async def build_edge(s: AsyncSession, tenant_id, period: str) -> dict:
    start, end = _period_range(period)
    # The membership business, by kind — these program views are all segments of ONE
    # membership entity's GHL location, whatever that entity happens to be called.
    biz = await roles.membership(s, tenant_id)
    empty = {"status": "pending", "watch": {"count": 0, "items": []}, "members_total": 0, "pl": None,
             "kpis": [], "deck": [], "funnel": None, "renewals": None, "event": None, "billing": None}
    if not biz:
        return empty
    # The Edge lives on the Forum's GHL integration (same location); its edge_* config keys
    # (edge_member_tags, edge_event_tag, edge_pipeline_match, event_name, …) live on that row.
    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.business_id == biz.id,
        Integration.provider == "ghl"))).scalars().first()
    cfg = (integ.config or {}) if integ else {}

    def base(kind):                       # edge_* kinds, distinct from Forum / beCollective
        return (MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == biz.id,
                MetricRecord.source == "ghl", MetricRecord.kind == f"edge_{kind}")

    async def records(kind, *extra):
        return (await s.execute(select(MetricRecord).where(*base(kind), *extra))).scalars().all()

    async def count(kind, *extra):
        return int((await s.execute(select(func.count()).select_from(MetricRecord)
                    .where(*base(kind), *extra))).scalar() or 0)

    members_all = await records("member", MetricRecord.status == "active")
    admin_recs = await records("member", MetricRecord.status == "admin")
    members_total = len(members_all)
    roster = F._roster_summary(members_all, admin_recs, 0, 0)     # no Forum/IC split
    seg_by_contact = {m.external_id: m.segment for m in members_all}
    memberships = await records("membership")
    arr = sum(float(m.amount or 0) for m in memberships)
    new_members = await count("onboarded", MetricRecord.occurred_on >= start, MetricRecord.occurred_on <= end)
    in_pipeline = await count("recruiting", MetricRecord.status == "open")
    financed = sum(1 for m in memberships
                   if (m.meta or {}).get("payment") in ("monthly", "quarterly", "installments"))

    all_regs = await records("registration")
    guests = sum(1 for r in all_regs if (r.meta or {}).get("guest"))
    member_regs = len(all_regs) - guests

    kpis = [
        {"key": "edge_members", "label": "Active Members", "value": str(members_total),
         "sub": f"{roster['primary']} primary · {roster['add_on']} add-on", "drill": "edge_roster"},
        {"key": "edge_arr", "label": "Membership Value", "value": F._usd(arr) if arr else "—",
         "sub": f"{len(memberships)} memberships", "drill": "edge_arr"},
        {"key": "edge_new_members", "label": "New Members", "value": str(new_members),
         "sub": F._period_label(period)},
        {"key": "edge_pipeline", "label": "In Pipeline", "value": str(in_pipeline), "sub": "recruiting"},
        {"key": "edge_registered", "label": "Registered", "value": str(member_regs),
         "sub": cfg.get("edge_event_name") or "next event", "drill": "edge_registered"},
        {"key": "edge_financed", "label": "Financed", "value": str(financed),
         "sub": "payment plans", "drill": "edge_financed"},
    ]

    fcfg = dict(cfg)
    fcfg["recruiting_stage_groups"] = cfg.get("edge_recruiting_stage_groups") or EDGE_FUNNEL_GROUPS
    funnel = await F._funnel(s, base, fcfg)
    renewals = F._renewals(memberships, seg_by_contact)
    ecfg = {"event_date": cfg.get("edge_event_date"), "event_name": cfg.get("edge_event_name"),
            "event_title": cfg.get("edge_event_title"), "event_dates": cfg.get("edge_event_dates"),
            "event_tag": cfg.get("edge_event_tag"), "prior_event_pace": cfg.get("edge_prior_event_pace")}
    event = F._event(ecfg, members_total, member_regs, guests)
    today = dt.date.today()

    # ── Cash & Billing — The Edge's charges live on the SAME legacy Stripe account as the
    # Forum (source='stripe_legacy'), classified out to kind='edge_payment' by product name.
    from .billing import compute_billing, project_renewals

    async def stripe_recs(kind):
        return (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == biz.id,
            MetricRecord.source == "stripe_legacy", MetricRecord.kind == f"edge_{kind}"))).scalars().all()

    payments = await stripe_recs("payment")
    subs_all = list(await stripe_recs("subscription"))
    active_sub_emails = {(x.email or "").lower() for x in subs_all if x.status == "active" and x.email}
    renewal_members = [m for m in members_all if (m.email or "").lower() not in active_sub_emails]
    extra = project_renewals(renewal_members, today, dt.date(today.year, 12, 31))
    billing = compute_billing(payments, subs_all, arr, start, end, today, extra_projected=extra)

    watch_items = []
    if billing.get("available") and (billing.get("failed_count") or billing.get("past_due")):
        watch_items.append("payments")
    if event and event.get("behind_pace"):
        watch_items.append("behind_pace")
    deck = F._deck(funnel, renewals, event, members_total, member_regs)

    # ── Operational Refinement payload (v9) — the same blocks the Forum emits. ──
    recruiting = F._recruiting(funnel, guests, fcfg)
    book_total = round(sum(F._num((m.meta or {}).get("membership", {}).get("total_cost")) for m in members_all))
    growth = F._growth(members_all, [], today)
    pay_mix = F._pay_mix(members_all, book_total)
    tenure = F._tenure(members_all, today)
    renew = F._renewal_states(members_all, subs_all, today)
    calendar = F._calendar(members_all, today)
    recover_list = F._recover(payments, members_all, today)
    action = {"failed": len(recover_list), "recover": round(sum(r["amt"] for r in recover_list))}
    mg = {"active": members_total, "primary": roster["primary"], "addOn": roster["add_on"],
          "admin": roster["admin"], "book": book_total, "growth": growth, "pay": pay_mix,
          "tenure": tenure, "renewals": renew, "calendar": calendar}
    ev_pct = round(event["registered"] / event["members"] * 100) if (event and event["members"]) else 0
    pulse = {
        "members": {"value": growth["total"][-1], "delta": growth["netMTD"], "spark": growth["total"][-6:]},
        "pipeline": ({"value": recruiting["total"], "stages": [st["n"] for st in recruiting["stages"]]}
                     if recruiting else None),
        "renewals": {"book": renew["book"], "count": renew["count"], "auto": renew["auto"], "needsYou": renew["needsYou"]},
        "event": ({"days": event["days_out"], "pct": ev_pct, "reg": event["registered"], "of": event["members"]} if event else None),
    }

    return {"status": "watch" if watch_items else "healthy",
            "watch": {"count": len(watch_items), "items": watch_items},
            "members_total": members_total, "roster": roster, "pl": None,
            "kpis": kpis, "deck": deck, "funnel": funnel, "renewals": renewals,
            "event": event, "billing": billing,
            "mg": mg, "pulse": pulse, "recruiting": recruiting, "action": action, "recover": recover_list}
