"""The Forum focused view — the /api/v1/forum payload, built from metric_record +
the GHL integration config. Shaped exactly on the mockup's data objects
(the-forum-view-v2.jsx) so wiring is a drop-in. Every section degrades to a
fallback (None / dimmed) when its GHL inputs are missing — it never errors.

Point-in-time vs period: only `new_members` respects `period`; ARR, MRR, the
funnel, the renewal book, event registration, and payment mix are current-state.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import MetricRecord, Business, Integration
from .metrics import _period_range, _forum_kpis


def _usd(n) -> str:
    n = float(n or 0)
    if abs(n) >= 1_000_000:
        return f"${n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"${round(n / 1000):,}K"
    return f"${round(n):,}"


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _mon3(s: str) -> str:
    return (s or "")[:3].lower()


async def build_forum(s: AsyncSession, tenant_id, period: str) -> dict:
    start, end = _period_range(period)
    biz = (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id, Business.key == "springb"))).scalar_one_or_none()
    if not biz:
        return {"status": "pending", "members_total": 0, "kpis": [], "deck": [],
                "funnel": None, "renewals": None, "event": None, "revq": None,
                "watch": {"count": 0, "items": []}, "pl": None}

    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.business_id == biz.id,
        Integration.provider == "ghl"))).scalar_one_or_none()
    cfg = (integ.config or {}) if integ else {}

    def base(kind):
        return (MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == biz.id,
                MetricRecord.source == "ghl", MetricRecord.kind == kind)

    async def records(kind, *extra):
        return (await s.execute(select(MetricRecord).where(*base(kind), *extra))).scalars().all()

    k = await _forum_kpis(s, tenant_id, biz.id, start, end)
    members_total = k["members"]
    seg = k["segments"]
    forum_n = seg.get("forum", 0) + seg.get("member", 0)
    ic_n = seg.get("inner_circle", 0)
    arr = k["arr"]

    # Registrations split members from guests (guests are additive prospect seats,
    # counted separately) so the "Registered" KPI and the event card agree.
    all_regs = await records("registration")
    guests = sum(1 for r in all_regs if (r.meta or {}).get("guest"))
    member_regs = len(all_regs) - guests

    # ── KPI tiles (mirror the operational panel) ──
    kpis = [
        {"key": "active_members", "label": "Active Members", "value": str(members_total),
         "sub": f"Forum {forum_n} · Inner Circle {ic_n}", "drill": "active_members"},
        {"key": "forum_arr", "label": "Forum ARR", "value": _usd(arr) if arr else "—",
         "sub": f"{k['memberships']} memberships", "drill": "forum_arr"},
        {"key": "new_members", "label": "New Members", "value": str(k["new_members"]),
         "sub": _period_label(period)},
        {"key": "renewals_due", "label": "Renewals Due", "value": str(k["renewals_due"]),
         "sub": _MONTHS[dt.date.today().month - 1], "drill": "renewal_book"},
        {"key": "registered", "label": "Registered", "value": str(member_regs),
         "sub": cfg.get("event_name") or "next event", "drill": "registered"},
        {"key": "mrr", "label": "MRR", "value": _usd(k["mrr"]) if k["mrr"] else "—",
         "sub": "monthly subscriptions", "drill": "monthly"},
    ]

    memberships = await records("membership")
    funnel = await _funnel(s, base, cfg)
    renewals = _renewals(memberships)
    event = _event(cfg, members_total, member_regs, guests)
    revq = await _revq(s, base, memberships, arr, tenant_id, biz.id)

    # ── watch signals ──
    watch_items = []
    if revq and revq.get("past_due") and revq["past_due"]["count"] > 0:
        watch_items.append("pastdue")
    if renewals and renewals["summary"]["mix"].get("risk", 0) > 0:
        watch_items.append("at_risk")
    if event and event.get("behind_pace"):
        watch_items.append("behind_pace")

    # ── deep-dive deck (collapsed summaries) ──
    deck = _deck(funnel, renewals, event, revq, members_total, k["registered"])

    return {
        "status": "watch" if watch_items else "healthy",
        "watch": {"count": len(watch_items), "items": watch_items},
        "members_total": members_total,
        "pl": None,                       # shared springb P&L is rendered by the dashboard payload
        "kpis": kpis, "deck": deck, "funnel": funnel,
        "renewals": renewals, "event": event, "revq": revq,
    }


def _period_label(period: str) -> str:
    return {"mtd": "month to date", "qtd": "quarter to date",
            "ytd": "year to date", "last_month": "last month"}.get(period, period)


async def _funnel(s, base, cfg) -> dict | None:
    recs = (await s.execute(select(MetricRecord).where(*base("recruiting")))).scalars().all()
    if not recs:
        return None
    default_val = float(cfg.get("default_contract_value") or 0)
    by_stage: dict = {}
    for r in recs:
        m = r.meta or {}
        st = m.get("stage") or "—"
        pos = m.get("stage_position", 999)
        g = by_stage.setdefault(st, {"label": st, "pos": pos, "v": 0, "value": 0.0})
        g["v"] += 1
        g["value"] += float(r.amount) if r.amount is not None else default_val
    stages = sorted(by_stage.values(), key=lambda x: x["pos"])
    return {"stages": [{"label": g["label"], "v": g["v"], "value": _usd(g["value"])} for g in stages],
            "footer": cfg.get("funnel_footer")}


def _renewals(memberships) -> dict | None:
    if not memberships:
        return None
    today = dt.date.today()
    window = {_MONTHS[(today.month - 1 + i) % 12] for i in range(3)}   # this + next 2 months
    rows = []
    mix = {"committed": 0, "talking": 0, "risk": 0}
    risk_value = 0.0
    for m in memberships:
        meta = m.meta or {}
        mon = (meta.get("renewal_month") or "")[:3].title()
        if mon not in {w[:3] for w in window}:
            continue
        status = meta.get("renewal_status") or "talking"
        mix[status] = mix.get(status, 0) + 1
        val = float(m.amount or 0)
        if status == "risk":
            risk_value += val
        rows.append({"name": (m.name or "").title() or m.external_id,
                     "seg": "IC" if m.segment == "inner_circle" else "F",
                     "month": mon, "value": _usd(val), "status": status, "_val": val})
    if not rows:
        return None
    order = {"Jan": 0, "Feb": 1, "Mar": 2, "Apr": 3, "May": 4, "Jun": 5,
             "Jul": 6, "Aug": 7, "Sep": 8, "Oct": 9, "Nov": 10, "Dec": 11}
    rows.sort(key=lambda r: (order.get(r["month"], 99), -r["_val"]))
    total = sum(r["_val"] for r in rows)
    return {"rows": [{kk: vv for kk, vv in r.items() if kk != "_val"} for r in rows[:6]],
            "summary": {"count": len(rows), "value": _usd(total), "mix": mix,
                        "risk_value": _usd(risk_value), "retention": None}}


def _event(cfg, members_total, member_regs, guests) -> dict | None:
    # Render whenever a next event is configured (name / tag / title / date).
    # The date only powers the countdown — without it days_out is None but the
    # registration progress + call list still show.
    if not (cfg.get("event_date") or cfg.get("event_name")
            or cfg.get("event_tag") or cfg.get("event_title")):
        return None
    days_out = None
    if cfg.get("event_date"):
        try:
            days_out = (dt.date.fromisoformat(str(cfg["event_date"])) - dt.date.today()).days
        except (ValueError, TypeError):
            days_out = None
    unregistered = max(0, members_total - member_regs)
    # pace: latest reg_count for the prior event vs config fallback
    prior = cfg.get("prior_event_pace")
    behind = bool(prior) and member_regs < int(prior)
    pace_note = (f"{prior} were registered at this point before the prior event"
                 if prior is not None else None)
    return {"title": cfg.get("event_title") or "Next event", "where": cfg.get("event_name") or "",
            "when": cfg.get("event_dates") or "", "days_out": days_out,
            "registered": member_regs, "members": members_total, "guests": guests,
            "unregistered": unregistered, "behind_pace": behind, "pace_note": pace_note}


async def _revq(s, base, memberships, arr, tenant_id, business_id) -> dict | None:
    subs = (await s.execute(select(MetricRecord).where(*base("subscription")))).scalars().all()
    if not subs and not memberships:
        return None
    mrr = sum(float(x.amount or 0) for x in subs if x.status == "active")
    past = [x for x in subs if x.status == "past_due"]
    # payment mix from membership.meta.payment
    monthly = [m for m in memberships if (m.meta or {}).get("payment") == "monthly"]
    monthly_val = sum(float(m.amount or 0) for m in monthly)
    pif = [m for m in memberships if (m.meta or {}).get("payment") != "monthly"]
    pif_val = arr - monthly_val
    out = {
        "pif": {"value": round(pif_val, 2), "count": len(pif)} if memberships else None,
        "monthly": ({"value": round(monthly_val, 2), "count": len(monthly),
                     "sub": f"{_usd(mrr)} MRR annualized"} if subs else None),
        "past_due": ({"count": len(past), "value": _usd(sum(float(x.amount or 0) for x in past))}
                     if subs else None),
    }
    # ARR bridge (YTD): needs onboarded + membership_lost amounts
    year_start = dt.date(dt.date.today().year, 1, 1)
    new_ytd = float((await s.execute(select(func.coalesce(func.sum(MetricRecord.amount), 0)).where(
        *base("onboarded"), MetricRecord.occurred_on >= year_start))).scalar() or 0)
    churn_ytd = float((await s.execute(select(func.coalesce(func.sum(MetricRecord.amount), 0)).where(
        *base("membership_lost"), MetricRecord.occurred_on >= year_start))).scalar() or 0)
    if new_ytd or churn_ytd:
        start_arr = arr - new_ytd + churn_ytd
        out["bridge"] = [
            {"label": "Jan 1", "value": _usd(start_arr)},
            {"label": "New", "value": f"+{_usd(new_ytd)}"},
            {"label": "Churned", "value": f"−{_usd(churn_ytd)}", "soft": True},
            {"label": "Today", "value": _usd(arr), "tot": True},
        ]
    return out


def _deck(funnel, renewals, event, revq, members_total, registered) -> list[dict]:
    deck = []
    if funnel:
        last = funnel["stages"][-1] if funnel["stages"] else {"v": 0, "value": "$0"}
        deck.append({"k": "pipeline", "label": "Recruiting pipeline",
                     "hero": str(sum(x["v"] for x in funnel["stages"])), "hero_sub": "in the pipeline",
                     "salient": f"{last['v']} invited · {last['value']} near-term", "tone": "good"})
    if renewals:
        sm = renewals["summary"]
        deck.append({"k": "renewals", "label": "Renewals · next 90 days",
                     "hero": sm["value"], "hero_sub": f"{sm['count']} renewals",
                     "salient": f"{sm['mix'].get('risk', 0)} at risk · {sm['risk_value']}",
                     "tone": "watch" if sm["mix"].get("risk", 0) else "good"})
    if event:
        pct = round((event["registered"] / event["members"] * 100)) if event["members"] else 0
        deck.append({"k": "event", "label": f"Next event · {event['where']}",
                     "hero": str(event["days_out"]) if event["days_out"] is not None else "—",
                     "hero_sub": "days out",
                     "salient": f"{event['unregistered']} unregistered" + (" · behind pace" if event["behind_pace"] else ""),
                     "tone": "watch" if (pct < 50 and event["unregistered"] > 0) else "good"})
    if revq and revq.get("pif") and revq.get("monthly"):
        total = revq["pif"]["count"] + revq["monthly"]["count"]
        pifpct = round(revq["pif"]["count"] / total * 100) if total else 0
        pd = revq.get("past_due")
        deck.append({"k": "revq", "label": "Revenue quality",
                     "hero": f"{pifpct}%", "hero_sub": "paid in full",
                     "salient": (f"{pd['count']} past due · {pd['value']}" if pd and pd["count"] else "on track"),
                     "tone": "watch" if (pd and pd["count"]) else "good"})
    return deck
