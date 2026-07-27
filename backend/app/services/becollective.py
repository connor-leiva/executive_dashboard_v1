"""beCollective focused view — /api/v1/becollective. beCollective is a cohort
program (one-time membership, PIF/Financed) in its OWN GHL instance, so it mirrors
the Forum's structure: members + contract value + recruiting funnel + events.
Reuses the Forum's card builders (funnel/event/deck) pointed at bc_* metric_record
kinds — reuse, not copy. Revenue-quality (MRR) is omitted: there are no GHL
subscriptions for beCollective (revenue is contract value, like the Forum's ARR)."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import MetricRecord, Business, Integration
from .metrics import _period_range
from . import forum as F

# beCollective's own sales-funnel stages (from the live "Be Collective Main Sales
# Funnel" probe) collapsed into a clean funnel; dead/nurture stages → footer.
BC_FUNNEL_GROUPS = [
    ["Applied", ["opt in", "no app", "app submitted"]],
    ["Appointment", ["appointment complete", "needs decision"]],
    ["Payment sent", ["payment sent"]],
    ["Onboarding", ["payment received", "fulfillment"]],
]


async def build_becollective(s: AsyncSession, tenant_id, period: str) -> dict:
    start, end = _period_range(period)
    biz = (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id, Business.key == "springb"))).scalar_one_or_none()
    empty = {"status": "pending", "watch": {"count": 0, "items": []}, "members_total": 0, "pl": None,
             "kpis": [], "deck": [], "funnel": None, "renewals": None, "event": None, "billing": None}
    if not biz:
        return empty
    # beCollective has its own GHL integration row (its own location + token). Prefer
    # it; fall back to the Forum's ghl row for older configs that still hold bc keys.
    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.business_id == biz.id,
        Integration.provider.in_(("ghl_bc", "ghl")))
        .order_by(Integration.provider.desc()))).scalars().first()
    cfg = (integ.config or {}) if integ else {}

    def base(kind):                       # bc_* kinds, distinct from the Forum's
        return (MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == biz.id,
                MetricRecord.source == "ghl", MetricRecord.kind == f"bc_{kind}")

    async def records(kind, *extra):
        return (await s.execute(select(MetricRecord).where(*base(kind), *extra))).scalars().all()

    async def count(kind, *extra):
        return int((await s.execute(select(func.count()).select_from(MetricRecord)
                    .where(*base(kind), *extra))).scalar() or 0)

    members_all = await records("member", MetricRecord.status == "active")   # primary + add-on
    admin_recs = await records("member", MetricRecord.status == "admin")      # staff, not members
    members_total = len(members_all)
    roster = F._roster_summary(members_all, admin_recs, 0, 0)     # no Forum/IC split for beCollective
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
        {"key": "bc_members", "label": "Active Members", "value": str(members_total),
         "sub": f"{roster['primary']} primary · {roster['add_on']} add-on", "drill": "bc_roster"},
        {"key": "bc_arr", "label": "Membership Value", "value": F._usd(arr) if arr else "—",
         "sub": f"{len(memberships)} memberships", "drill": "bc_arr"},
        {"key": "bc_new_members", "label": "New Members", "value": str(new_members),
         "sub": F._period_label(period)},
        {"key": "bc_pipeline", "label": "In Pipeline", "value": str(in_pipeline), "sub": "recruiting"},
        {"key": "bc_registered", "label": "Registered", "value": str(member_regs),
         "sub": cfg.get("event_name") or "next event", "drill": "bc_registered"},
        {"key": "bc_financed", "label": "Financed", "value": str(financed),
         "sub": "payment plans", "drill": "bc_financed"},
    ]

    # Reuse the Forum's card builders with the bc_ base + a beCollective-mapped config.
    # Config lives on beCollective's own GHL row with plain keys (event_name, etc.),
    # so the same Settings › GHL form edits both programs.
    fcfg = dict(cfg)
    fcfg["recruiting_stage_groups"] = cfg.get("recruiting_stage_groups") or BC_FUNNEL_GROUPS
    funnel = await F._funnel(s, base, fcfg)
    renewals = F._renewals(memberships, seg_by_contact)
    ecfg = {"event_date": cfg.get("event_date"), "event_name": cfg.get("event_name"),
            "event_title": cfg.get("event_title"), "event_dates": cfg.get("event_dates"),
            "event_tag": cfg.get("event_tag"), "prior_event_pace": cfg.get("prior_event_pace")}
    event = F._event(ecfg, members_total, member_regs, guests)

    watch_items = []
    if event and event.get("behind_pace"):
        watch_items.append("behind_pace")
    deck = F._deck(funnel, renewals, event, members_total, member_regs)

    # ── Operational Refinement payload (v9) — the same blocks the Forum emits, over the
    # field-driven bc_member roster. beCollective has no GHL subscriptions or payments, so
    # renewal states default to auto, the recover list is empty, and billing stays None.
    today = dt.date.today()
    recruiting = F._recruiting(funnel, guests, fcfg)
    book_total = round(sum(F._num((m.meta or {}).get("membership", {}).get("total_cost")) for m in members_all))
    growth = F._growth(members_all, [], today)                # no churn records yet
    pay_mix = F._pay_mix(members_all, book_total)
    tenure = F._tenure(members_all, today)
    renew = F._renewal_states(members_all, [], today)         # no subscriptions
    calendar = F._calendar(members_all, today)
    recover_list = F._recover([], members_all, today)         # no GHL payments
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
            "event": event, "billing": None,
            "mg": mg, "pulse": pulse, "recruiting": recruiting, "action": action, "recover": recover_list}
