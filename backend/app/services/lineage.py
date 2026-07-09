"""Metric drill-down: the rows behind each KPI, for the audit drawer.

Every metric key maps to (a) the records it's computed from, (b) a plain-English
"computed_as", and (c) a link out to the source system. Source URLs are computed
from the record's external_id (no stored column needed).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Transaction, Agent, MetricRecord, Business, PLSnapshot, Integration
from .metrics import _period_range


def _sisu_url(external_id) -> str | None:
    if not external_id:
        return None
    try:
        return settings.SISU_TXN_URL.format(id=external_id)
    except (KeyError, IndexError):
        return None


def _fin_row(t: Transaction, kind: str, when) -> dict:
    """A deal behind a three-lens financial row: GCI, agent commission, and net
    GCI (company dollar = GCI − commission) when the commission has been enriched."""
    gci = float(t.gci) if t.gci is not None else 0.0
    comm = float(t.agent_commission) if t.agent_commission is not None else None
    return {
        "id": str(t.id),
        "name": t.buyer_name or t.address or t.external_id,
        "gci": gci,
        "agent_commission": comm,
        "company_dollar": round(gci - comm, 2) if comm is not None else None,
        "side": t.side, "kind": kind,
        "close_date": when.isoformat() if when else None,
        "source_url": _sisu_url(t.external_id),
    }


def _txn_row(t: Transaction) -> dict:
    return {
        "id": str(t.id),
        "name": t.buyer_name or t.address or t.external_id,
        "address": t.address,
        "gci": float(t.gci) if t.gci is not None else None,
        "sale_price": float(t.sale_price) if t.sale_price is not None else None,
        "status": t.status,
        "side": t.side,
        "close_date": t.close_date.isoformat() if t.close_date else None,
        "source_url": _sisu_url(t.external_id),
    }


async def _closed(s, tenant_id, start, end):
    q = select(Transaction).where(
        Transaction.tenant_id == tenant_id, Transaction.status == "closed",
        Transaction.sale_price > 0,
        Transaction.close_date >= start, Transaction.close_date <= end,
    ).order_by(Transaction.close_date.desc())
    return (await s.execute(q)).scalars().all()


_TXN_LABEL = {"units_closed": "Units Closed", "gci": "Total GCI",
              "volume": "Volume", "avg_price": "Avg Sale Price"}
_FINANCIAL = {"combined_profit", "revenue", "noi", "gross_profit", "opex", "cogs"}
_PENDING_SRC = {"funded_loans": ("Arive", "Funded loans reaching the funded stage")}


async def metric_detail(s: AsyncSession, tenant_id, key: str, period: str,
                        business: str | None = None, agent_id: str | None = None,
                        lo: str | None = None, stage: str | None = None,
                        source: str | None = None, stream: str | None = None,
                        month: str | None = None) -> dict:
    start, end = _period_range(period)
    cutoff = dt.date.today() - dt.timedelta(days=settings.SISU_CURRENT_WINDOW_DAYS)

    def span():
        return f"{start.strftime('%b %d')}–{end.strftime('%b %d')}"

    # ── closed-sale metrics (rows = closed transactions this period) ──
    if key in _TXN_LABEL:
        txns = await _closed(s, tenant_id, start, end)
        rows = [_txn_row(t) for t in txns]
        return {
            "label": _TXN_LABEL[key], "source": "Sisu",
            "computed_as": f"Closed sales with a price, {span()} (excludes rentals / $0 referrals)",
            "count": len(rows), "rows": rows,
        }

    if key == "pending":
        q = select(Transaction).where(
            Transaction.tenant_id == tenant_id, Transaction.status == "pending",
            Transaction.contract_date >= cutoff,
        ).order_by(Transaction.contract_date.desc())
        txns = (await s.execute(q)).scalars().all()
        return {"label": "Under Contract", "source": "Sisu",
                "computed_as": f"Deals under contract in the last {settings.SISU_CURRENT_WINDOW_DAYS} days",
                "count": len(txns), "rows": [_txn_row(t) for t in txns]}

    if key == "active_listings":
        q = select(Transaction).where(
            Transaction.tenant_id == tenant_id, Transaction.status == "active",
            Transaction.side == "sell", Transaction.listing_date >= cutoff,
        ).order_by(Transaction.listing_date.desc())
        txns = (await s.execute(q)).scalars().all()
        return {"label": "Active Listings", "source": "Sisu",
                "computed_as": f"Sell-side listings active in the last {settings.SISU_CURRENT_WINDOW_DAYS} days",
                "count": len(txns), "rows": [_txn_row(t) for t in txns]}

    if key == "agents_producing":
        txns = await _closed(s, tenant_id, start, end)
        by_agent: dict = {}
        for t in txns:
            if t.agent_id:
                by_agent[t.agent_id] = by_agent.get(t.agent_id, 0) + 1
        names = {a.id: a.name for a in (await s.execute(
            select(Agent).where(Agent.tenant_id == tenant_id))).scalars().all()}
        rows = [{"id": str(aid), "name": names.get(aid, "Agent"),
                 "status": f"{c} closed", "source_url": None}
                for aid, c in sorted(by_agent.items(), key=lambda x: -x[1])]
        return {"label": "Agents Producing", "source": "Sisu",
                "computed_as": f"Agents with at least one closed sale, {span()}",
                "count": len(rows), "rows": rows}

    # ── The Forum (Go High Level) drill-downs ──
    if key in {"active_members", "forum_arr", "renewals_due", "new_members", "registered",
               "mrr", "renewal_book", "monthly", "pastdue", "unregistered"}:
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id, Business.key == "springb"))).scalar_one_or_none()
        if not biz:
            return {"label": key.replace("_", " ").title(), "source": "Go High Level",
                    "computed_as": "Go High Level isn't connected yet.", "count": 0, "rows": []}

        def q(kind):
            return select(MetricRecord).where(
                MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == biz.id,
                MetricRecord.source == "ghl", MetricRecord.kind == kind)

        def title_name(r):
            return (r.name or "").strip().title() or r.email or r.external_id

        def money(n):
            return f"${float(n or 0):,.0f}"

        SEG_LABEL = {"forum": "The Forum", "inner_circle": "Inner Circle"}

        def seg_of(segment):
            return "IC" if segment == "inner_circle" else "F"

        def renews_of(ms):
            mon = (ms.meta or {}).get("renewal_month") if ms else None
            return (mon or "")[:3].title() or None

        # Small join maps (Forum record counts are tiny): contact → segment /
        # membership, plus the registered-contact set for the event ✓/✗ column.
        members_all = (await s.execute(q("member").where(MetricRecord.status == "active"))).scalars().all()
        seg_by_contact = {m.external_id: m.segment for m in members_all}
        ms_all = (await s.execute(q("membership"))).scalars().all()
        ms_by_contact = {(m.meta or {}).get("contact_id"): m for m in ms_all if (m.meta or {}).get("contact_id")}
        reg_all = (await s.execute(q("registration"))).scalars().all()
        reg_ids = {(r.meta or {}).get("contact_id") for r in reg_all
                   if not (r.meta or {}).get("guest") and (r.meta or {}).get("contact_id")}
        reg_names = {(r.name or "").strip().lower() for r in reg_all if not (r.meta or {}).get("guest")}

        def is_registered(contact_id, name):
            return contact_id in reg_ids or (name or "").strip().lower() in reg_names

        if key == "active_members":
            recs = sorted(members_all, key=lambda m: (m.segment or "", m.name or ""))
            rows = []
            for m in recs:
                ms = ms_by_contact.get(m.external_id)
                pay = (ms.meta or {}).get("payment") if ms else None
                mon = renews_of(ms)
                ev = "✓" if is_registered(m.external_id, m.name) else "✗"
                r2 = " · ".join(x for x in [f"renews {mon}" if mon else None, f"event {ev}"] if x)
                rows.append({"id": str(m.id), "name": title_name(m), "seg": seg_of(m.segment),
                             "l2": {"monthly": "Monthly", "pif": "Paid in full"}.get(pay) or SEG_LABEL.get(m.segment, "member"),
                             "r1": money(ms.amount) if ms and ms.amount is not None else "",
                             "r2": r2, "source_url": m.source_url})
            return {"label": "Active Members", "source": "Go High Level",
                    "computed_as": "Distinct contacts carrying any official membership tag (The Forum + Inner Circle).",
                    "count": len(rows), "rows": rows}

        if key == "forum_arr":
            recs = sorted(ms_all, key=lambda m: -float(m.amount or 0))
            rows = [{"id": str(r.id), "name": title_name(r),
                     "seg": seg_of(seg_by_contact.get((r.meta or {}).get("contact_id"))),
                     "l2": "Renewals pipeline", "r1": money(r.amount),
                     "r2": f"renews {renews_of(r)}" if renews_of(r) else "",
                     "source_url": r.source_url} for r in recs]
            return {"label": "Forum ARR", "source": "Go High Level",
                    "computed_as": "Contract value across open opportunities in the Current Forum Members (renewals) pipeline.",
                    "count": len(rows), "rows": rows}

        if key == "renewals_due":
            mon3 = dt.date.today().strftime("%b").lower()
            recs = [r for r in (await s.execute(q("membership").order_by(MetricRecord.name))).scalars().all()
                    if (r.meta or {}).get("renewal_month", "")[:3].lower() == mon3]
            rows = [{"id": str(r.id), "name": (r.name or r.external_id),
                     "status": (r.meta or {}).get("renewal_month"), "source_url": r.source_url} for r in recs]
            return {"label": "Renewals Due", "source": "Go High Level",
                    "computed_as": f"Forum members whose renewal month is {dt.date.today().strftime('%B')}.",
                    "count": len(rows), "rows": rows}

        if key == "new_members":
            recs = (await s.execute(q("onboarded").where(
                MetricRecord.occurred_on >= start, MetricRecord.occurred_on <= end)
                .order_by(MetricRecord.occurred_on.desc()))).scalars().all()
            rows = [{"id": str(r.id), "name": (r.name or r.external_id),
                     "status": (r.occurred_on.isoformat() if r.occurred_on else "onboarded"),
                     "source_url": r.source_url} for r in recs]
            return {"label": "New Members", "source": "Go High Level",
                    "computed_as": f"Sales-funnel opportunities reaching 'Won: Onboarded', {span()}.",
                    "count": len(rows), "rows": rows}

        if key == "registered":
            recs = sorted((r for r in reg_all if not (r.meta or {}).get("guest")), key=lambda r: (r.name or ""))
            rows = [{"id": str(r.id), "name": title_name(r),
                     "seg": seg_of(seg_by_contact.get((r.meta or {}).get("contact_id"))),
                     "l2": "Registered", "r1": "✓", "source_url": r.source_url} for r in recs]
            return {"label": "Registered", "source": "Go High Level",
                    "computed_as": "Members registered for the next Forum event (guest prospect seats excluded).",
                    "count": len(rows), "rows": rows}

        if key == "mrr":
            recs = (await s.execute(q("subscription").where(MetricRecord.status == "active")
                    .order_by(MetricRecord.amount.desc()))).scalars().all()
            rows = [{"id": str(r.id), "name": title_name(r),
                     "seg": seg_of(seg_by_contact.get((r.meta or {}).get("contact_id"))),
                     "l2": "Monthly subscription",
                     "r1": (f"{money(r.amount)}/mo" if r.amount is not None else "active"),
                     "source_url": r.source_url} for r in recs]
            return {"label": "MRR", "source": "Go High Level",
                    "computed_as": "Active recurring subscriptions (the monthly-paying member subset).",
                    "count": len(rows), "rows": rows}

        if key == "renewal_book":
            _months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            today = dt.date.today()
            window = {_months[(today.month - 1 + i) % 12] for i in range(3)}
            recs = (await s.execute(q("membership"))).scalars().all()
            book = []
            for r in recs:
                meta = r.meta or {}
                mon = (meta.get("renewal_month") or "")[:3].title()
                if mon not in window:
                    continue
                seg = seg_of(seg_by_contact.get(meta.get("contact_id")))
                book.append((mon, -(float(r.amount or 0)), seg, money(r.amount),
                             title_name(r), r.source_url, str(r.id)))
            book.sort(key=lambda x: (_months.index(x[0]) if x[0] in _months else 99, x[1]))
            rows = [{"id": rid, "name": nm, "seg": seg, "l2": f"renews {mon}", "r1": val, "source_url": url}
                    for (mon, _v, seg, val, nm, url, rid) in book]
            return {"label": "Renewal Book · next 90 days", "source": "Go High Level",
                    "computed_as": "Memberships whose renewal month falls in the next 90 days, by month + contract value.",
                    "count": len(rows), "rows": rows}

        if key == "monthly":
            recs = (await s.execute(q("subscription").order_by(MetricRecord.amount.desc()))).scalars().all()
            rows = [{"id": str(r.id), "name": title_name(r),
                     "seg": seg_of(seg_by_contact.get((r.meta or {}).get("contact_id"))),
                     "l2": "Monthly · past due" if r.status == "past_due" else "Monthly · current",
                     "r1": (f"{money(r.amount)}/mo" if r.amount is not None else "monthly"),
                     "tone": "watch" if r.status == "past_due" else None,
                     "source_url": r.source_url} for r in recs]
            return {"label": "Monthly Subscriptions", "source": "Go High Level",
                    "computed_as": "All recurring subscriptions in GHL Payments (active + past due).",
                    "count": len(rows), "rows": rows}

        if key == "pastdue":
            recs = (await s.execute(q("subscription").where(MetricRecord.status == "past_due")
                    .order_by(MetricRecord.amount.desc()))).scalars().all()
            rows = [{"id": str(r.id), "name": title_name(r),
                     "seg": seg_of(seg_by_contact.get((r.meta or {}).get("contact_id"))),
                     "l2": "Card failed", "tone": "watch",
                     "r1": (f"{money(r.amount)}/mo" if r.amount is not None else "past due"),
                     "source_url": r.source_url} for r in recs]
            return {"label": "Subscriptions Past Due", "source": "Go High Level",
                    "computed_as": "Subscriptions whose most-recent charge failed — the recovery list.",
                    "count": len(rows), "rows": rows}

        if key == "unregistered":
            recs = sorted((m for m in members_all if not is_registered(m.external_id, m.name)),
                          key=lambda m: (m.segment or "", m.name or ""))
            rows = [{"id": str(m.id), "name": title_name(m), "seg": seg_of(m.segment),
                     "l2": SEG_LABEL.get(m.segment, "member"), "r1": "call",
                     "source_url": m.source_url} for m in recs]
            return {"label": "Not Yet Registered", "source": "Go High Level",
                    "computed_as": "Active members without a registration for the next event — the call list.",
                    "count": len(rows), "rows": rows}

    # ── beCollective (Go High Level, bc_* kinds) drill-downs ──
    if key in {"bc_members", "bc_arr", "bc_registered", "bc_financed", "bc_monthly"}:
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id, Business.key == "springb"))).scalar_one_or_none()
        if not biz:
            return {"label": key.replace("_", " ").title(), "source": "Go High Level",
                    "computed_as": "beCollective isn't connected yet.", "count": 0, "rows": []}

        def bcq(kind):
            return select(MetricRecord).where(
                MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == biz.id,
                MetricRecord.source == "ghl", MetricRecord.kind == f"bc_{kind}")

        def bc_money(n):
            return f"${float(n or 0):,.0f}"

        def bc_name(r):
            return (r.name or "").strip().title() or r.email or r.external_id

        if key == "bc_members":
            recs = (await s.execute(bcq("member").where(MetricRecord.status == "active")
                    .order_by(MetricRecord.name))).scalars().all()
            rows = [{"id": str(r.id), "name": bc_name(r), "seg": "BC", "l2": "beCollective",
                     "source_url": r.source_url} for r in recs]
            return {"label": "Active Members", "source": "Go High Level",
                    "computed_as": "Contacts carrying a beCollective membership tag.",
                    "count": len(rows), "rows": rows}

        if key in ("bc_arr", "bc_financed"):
            recs = (await s.execute(bcq("membership").order_by(MetricRecord.amount.desc()))).scalars().all()
            if key == "bc_financed":
                recs = [r for r in recs if (r.meta or {}).get("payment") == "monthly"]
            rows = [{"id": str(r.id), "name": bc_name(r), "seg": "BC",
                     "l2": ("Financed" if (r.meta or {}).get("payment") == "monthly" else "Paid in full"),
                     "r1": bc_money(r.amount), "source_url": r.source_url} for r in recs]
            label = "Financed Members" if key == "bc_financed" else "Membership Value"
            how = ("beCollective members on a financed payment plan."
                   if key == "bc_financed" else "Contract value across beCollective memberships.")
            return {"label": label, "source": "Go High Level", "computed_as": how,
                    "count": len(rows), "rows": rows}

        if key == "bc_registered":
            recs = (await s.execute(bcq("registration").order_by(MetricRecord.name))).scalars().all()
            recs = [r for r in recs if not (r.meta or {}).get("guest")]
            rows = [{"id": str(r.id), "name": bc_name(r), "seg": "BC", "l2": "Registered", "r1": "✓",
                     "source_url": r.source_url} for r in recs]
            return {"label": "Registered", "source": "Go High Level",
                    "computed_as": "beCollective members registered for the next event (guests excluded).",
                    "count": len(rows), "rows": rows}

    # ── The Forum · Cash & Billing drills (GHL Payments) ──
    if key in {"forum_payments", "forum_failed_payments", "forum_mrr_subs",
               "forum_installments", "forum_next30", "forum_streams", "forum_cashflow"}:
        from .billing import is_perpetual, sub_monthly, project_charges, STREAM_LABELS
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id, Business.key == "springb"))).scalar_one_or_none()

        async def frecs(kind):
            if not biz:
                return []
            return (await s.execute(select(MetricRecord).where(
                MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == biz.id,
                MetricRecord.source == "ghl", MetricRecord.kind == kind))).scalars().all()

        def money(n):
            return f"${float(n or 0):,.0f}"

        def nm(r):
            return (r.name or "").strip() or r.email or r.external_id

        def dlabel(d):
            return d.strftime("%b %d") if d else None

        if key in ("forum_payments", "forum_streams"):
            pays = await frecs("payment")
            if key == "forum_streams" and stream:
                pays = [p for p in pays if (p.meta or {}).get("stream") == stream]
            pays = [p for p in pays if p.occurred_on]
            pays.sort(key=lambda p: (p.occurred_on or dt.date.min), reverse=True)
            rows = [{"id": str(p.id), "name": nm(p),
                     "tone": "watch" if p.status == "failed" else None,
                     "l2": " · ".join(x for x in [STREAM_LABELS.get((p.meta or {}).get("stream"), (p.meta or {}).get("stream")),
                                                  (p.status if p.status != "succeeded" else None)] if x),
                     "r1": money(p.amount), "r2": dlabel(p.occurred_on),
                     "source_url": p.source_url} for p in pays]
            if key == "forum_streams":
                lbl = STREAM_LABELS.get(stream, (stream or "").title() or "Revenue by stream")
                return {"label": lbl, "source": "GHL Payments",
                        "computed_as": f"Succeeded Stripe charges classified as {lbl} (net of refunds; all recorded payments).",
                        "count": len(rows), "rows": rows}
            return {"label": "All transactions", "source": "GHL Payments",
                    "computed_as": "Every Stripe charge on record — succeeded, failed, refunded.",
                    "count": len(rows), "rows": rows}

        if key == "forum_failed_payments":
            pays = [p for p in await frecs("payment") if p.status == "failed"]
            pays.sort(key=lambda p: float(p.amount or 0), reverse=True)
            rows = [{"id": str(p.id), "name": nm(p), "tone": "watch",
                     "l2": "Failed charge · " + (dlabel(p.occurred_on) or ""),
                     "r1": money(p.amount), "source_url": p.source_url} for p in pays]
            for x in await frecs("subscription"):
                if x.status == "past_due":
                    rows.append({"id": str(x.id), "name": nm(x), "tone": "watch",
                                 "l2": "Subscription past due", "r1": money(x.amount) + "/mo",
                                 "source_url": x.source_url})
            return {"label": "Recovery list", "source": "GHL Payments",
                    "computed_as": "Failed charges + past-due subscriptions to recover.",
                    "count": len(rows), "rows": rows}

        subs = await frecs("subscription")
        active = [x for x in subs if x.status == "active"]
        if key == "forum_mrr_subs":
            perp = sorted([x for x in active if is_perpetual(x)],
                          key=lambda x: sub_monthly(x), reverse=True)
            rows = [{"id": str(x.id), "name": nm(x), "l2": "Monthly subscription",
                     "r1": money(sub_monthly(x)) + "/mo", "source_url": x.source_url} for x in perp]
            return {"label": "Perpetual subscriptions", "source": "GHL Payments",
                    "computed_as": "Active recurring memberships (installment plans excluded) — the MRR base.",
                    "count": len(rows), "rows": rows}

        if key == "forum_installments":
            inst = [x for x in active if not is_perpetual(x)]
            rows = [{"id": str(x.id), "name": (x.meta or {}).get("plan_name") or nm(x),
                     "l2": f"{(x.meta or {}).get('installments_collected', 0)} of "
                           f"{(x.meta or {}).get('installments_total') or '?'} collected",
                     "r1": money(sub_monthly(x)), "r2": f"final {(x.meta or {}).get('end_date') or '—'}",
                     "source_url": x.source_url} for x in inst]
            return {"label": "Installment plans", "source": "GHL Payments",
                    "computed_as": "Finite N-pay plans — collection progress (kept out of MRR).",
                    "count": len(rows), "rows": rows}

        today = dt.date.today()

        # forum_cashflow — a clicked cash-flow month bar. Past/current month → the
        # actual transactions; a future month → the projected charges (same source
        # as the chart, so drawer and bar always agree).
        if key == "forum_cashflow":
            try:
                y, mo = int(str(month)[:4]), int(str(month)[5:7])
            except (TypeError, ValueError):
                y, mo = today.year, today.month
            mname = dt.date(y, mo, 1).strftime("%B %Y")
            if (y, mo) <= (today.year, today.month):
                pays = [p for p in await frecs("payment")
                        if p.occurred_on and (p.occurred_on.year, p.occurred_on.month) == (y, mo)]
                pays.sort(key=lambda p: (p.occurred_on or dt.date.min), reverse=True)
                rows = [{"id": str(p.id), "name": nm(p),
                         "tone": "watch" if p.status == "failed" else None,
                         "l2": " · ".join(x for x in [STREAM_LABELS.get((p.meta or {}).get("stream"), (p.meta or {}).get("stream")),
                                                      (p.status if p.status != "succeeded" else None)] if x),
                         "r1": money(p.amount), "r2": dlabel(p.occurred_on),
                         "source_url": p.source_url} for p in pays]
                return {"label": f"{mname} · cash collected", "source": "GHL Payments",
                        "computed_as": f"Stripe charges recorded in {mname} (net of refunds).",
                        "count": len(rows), "rows": rows}
            last = dt.date(y + mo // 12, mo % 12 + 1, 1) - dt.timedelta(days=1)
            sched = [c for c in project_charges(active, today, last) if c["date"][:7] == f"{y}-{mo:02d}"]
            rows = [{"id": f"p{i}", "name": c["who"], "l2": c["note"] + " · projected",
                     "r1": money(c["amount"]), "r2": dlabel(dt.date.fromisoformat(c["date"])),
                     "source_url": c.get("source_url")} for i, c in enumerate(sched)]
            return {"label": f"{mname} · projected inflow", "source": "GHL Payments",
                    "computed_as": f"Expected charges in {mname}, projected from each active subscription's billing cadence.",
                    "count": len(rows), "rows": rows}

        # forum_next30 — the forward-billing schedule (SAME projection as the chart,
        # so the drawer never disagrees with the cash-flow footer).
        sched = project_charges(active, today, today + dt.timedelta(days=30))
        rows = [{"id": f"n{i}", "name": c["who"], "l2": c["note"], "r1": money(c["amount"]),
                 "r2": dlabel(dt.date.fromisoformat(c["date"])), "source_url": c.get("source_url")}
                for i, c in enumerate(sched)]
        return {"label": "Next 30 days", "source": "GHL Payments",
                "computed_as": "Scheduled charges (subscriptions + installment finals) in the next 30 days, projected from billing cadence.",
                "count": len(rows), "rows": rows}

    # ── Sympli pipeline stage drill (a funnel bar / pivot cell → its loans) ──
    if key == "loan_stage":
        from .metrics import _arive_states, _in_states, _loan_source_map, _ARIVE_FUNNEL
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id, Business.key == "sympli"))).scalar_one_or_none()
        loans = []
        if biz:
            states = await _arive_states(s, tenant_id)
            loans = [l for l in (await s.execute(select(MetricRecord).where(
                MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == biz.id,
                MetricRecord.source == "arive", MetricRecord.kind == "loan"))).scalars().all()
                if _in_states(l, states)]
        status_to_stage = {code: lbl for lbl, codes in _ARIVE_FUNNEL for code in codes}

        def stage_of(l):
            if l.segment == "funded":
                return "Funded" if (l.occurred_on and start <= l.occurred_on <= end) else None
            if l.segment == "pipeline":
                return status_to_stage.get((l.status or "").upper())
            return None

        smap = await _loan_source_map(s, tenant_id, loans)
        recs = [l for l in loans if stage_of(l) == stage]
        if lo:
            recs = [r for r in recs if ((r.meta or {}).get("lo_email") or "").lower() == lo.lower()]
        if source:
            recs = [r for r in recs if smap.get(r.id) == source]
        recs.sort(key=lambda r: (smap.get(r.id) != "ULRG", -float(r.amount or 0)))   # ULRG first, then amount

        def money(n):
            return f"${float(n or 0):,.0f}"

        def lname(r):
            return (r.name or "").strip().title() or r.email or r.external_id

        def sub(r):
            m = r.meta or {}
            who = m.get("lo_name") or ((m.get("lo_email") or "").split("@")[0].replace(".", " ").title()) or None
            what = ((r.status or "").replace("_", " ").title() if r.segment == "pipeline"
                    else (m.get("purpose") or "Funded"))
            return " · ".join(x for x in [who, what] if x)

        rows = [{"id": str(r.id), "name": lname(r),
                 "seg": ("ULRG" if smap.get(r.id) == "ULRG" else "OTHER"),
                 "l2": sub(r), "r1": money(r.amount),
                 "r2": r.occurred_on.strftime("%b %d") if (r.segment == "funded" and r.occurred_on) else None,
                 "source_url": r.source_url} for r in recs]
        scope = " · ".join(x for x in [stage or "Pipeline",
                                       (lo.split("@")[0] if lo else None), source] if x)
        return {"label": scope, "source": "Arive",
                "computed_as": f"Loans at the {stage or 'selected'} stage"
                               + (f" · loan officer {lo}" if lo else "")
                               + (f" · {source}-sourced" if source else "")
                               + f", {span()}.",
                "count": len(rows), "rows": rows}

    # ── Sympli's ARIVE loan pipeline (the loans behind the funded/pipeline KPIs) ──
    if key in {"funded_loans", "loan_volume", "preapprovals", "in_underwriting", "sympli_commission"}:
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id, Business.key == "sympli"))).scalar_one_or_none()
        from .metrics import _arive_states, _in_states
        loans = []
        if biz:
            states = await _arive_states(s, tenant_id)
            loans = (await s.execute(select(MetricRecord).where(
                MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == biz.id,
                MetricRecord.source == "arive", MetricRecord.kind == "loan"))).scalars().all()
            loans = [l for l in loans if _in_states(l, states)]
        if loans:                                  # else fall through to the pending-source note
            def money(n):
                return f"${float(n or 0):,.0f}"

            def lname(r):
                return (r.name or "").strip().title() or r.email or r.external_id

            if key == "sympli_commission":
                recs = [l for l in loans if l.segment == "funded"
                        and l.occurred_on and start <= l.occurred_on <= end]
                if lo:                                  # scope to one loan officer
                    recs = [r for r in recs if ((r.meta or {}).get("lo_email") or "").lower() == lo.lower()]
                recs.sort(key=lambda r: float((r.meta or {}).get("gross_revenue") or 0), reverse=True)
                rows = [{"id": str(r.id), "name": lname(r), "seg": "MTG",
                         "l2": f"loan {money(r.amount)}",
                         "r1": money((r.meta or {}).get("gross_revenue")),
                         "r2": f"net {money((r.meta or {}).get('net_revenue'))}",
                         "source_url": r.source_url} for r in recs]
                who = (recs[0].meta or {}).get("lo_name") if (lo and recs) else None
                return {"label": (f"{who} · commissions" if who else "Commission revenue"), "source": "Arive",
                        "computed_as": f"Lender-paid commission on funded loans, {span()} "
                                       "(gross; net = after direct loan costs).",
                        "count": len(rows), "rows": rows}

            if key in ("funded_loans", "loan_volume"):
                recs = [l for l in loans if l.segment == "funded"
                        and l.occurred_on and start <= l.occurred_on <= end]
                from .metrics import _loan_source_map
                smap = await _loan_source_map(s, tenant_id, recs)   # ULRG vs Other per loan
                recs.sort(key=lambda r: (smap.get(r.id) != "ULRG", -float(r.amount or 0)))   # ULRG first
                rows = [{"id": str(r.id), "name": lname(r),
                         "seg": ("ULRG" if smap.get(r.id) == "ULRG" else "OTHER"),
                         "l2": (r.meta or {}).get("purpose") or "Funded",
                         "r1": money(r.amount),
                         "r2": r.occurred_on.strftime("%b %d") if r.occurred_on else None,
                         "source_url": r.source_url} for r in recs]
                n_ulrg = sum(1 for v in smap.values() if v == "ULRG")
                label = "Loan Volume" if key == "loan_volume" else "Loans Funded"
                return {"label": label, "source": "Arive",
                        "computed_as": f"Loans reaching a funded status, {span()} — "
                                       f"{n_ulrg} ULRG-sourced, {len(recs) - n_ulrg} other "
                                       "(sourced = Utah Life referral or borrower matches a ULRG closing).",
                        "count": len(rows), "rows": rows}

            codes = ({"PREAPPROVED", "QUALIFICATION"} if key == "preapprovals"
                     else {"UNDERWRITING_SUBMITTED", "APPROVED_WITH_CONDITION", "RE_SUBMITTAL",
                           "CLEAR_TO_CLOSE", "DOCS_OUT", "DOCS_SIGNED"})
            recs = [l for l in loans if l.segment == "pipeline" and (l.status or "").upper() in codes]
            recs.sort(key=lambda r: float(r.amount or 0), reverse=True)
            rows = [{"id": str(r.id), "name": lname(r), "seg": "MTG",
                     "l2": (r.status or "").replace("_", " ").title(),
                     "r1": money(r.amount), "source_url": r.source_url} for r in recs]
            label = "Pre-approvals" if key == "preapprovals" else "In Underwriting"
            return {"label": label, "source": "Arive",
                    "computed_as": "Active loans currently at this pipeline stage.",
                    "count": len(rows), "rows": rows}

    # ── the referral flywheel (the deals behind ULRG buyers → Sympli) ──
    if key in {"flywheel_buyers", "flywheel_captured", "flywheel_uncaptured"}:
        bmap = {b.key: b for b in (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id))).scalars().all()}
        ulrg, sympli = bmap.get("ulrg"), bmap.get("sympli")
        if not (ulrg and sympli):
            return {"label": "Referral flywheel", "source": "Arive × Sisu",
                    "computed_as": "Connect Arive and Sisu to itemize the flywheel.", "rows": []}

        sisu_integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == tenant_id, Integration.provider == "sisu"))).scalars().first()
        vcfg = (sisu_integ.config or {}) if sisu_integ else {}
        sympli_vids = set(vcfg.get("sympli_mortgage_vids") or [])
        cash_vids = set(vcfg.get("cash_vids") or [])
        lender_names = vcfg.get("lender_names") or {}

        from .metrics import _arive_states, _in_states
        _fw_states = await _arive_states(s, tenant_id)
        funded = (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == sympli.id,
            MetricRecord.source == "arive", MetricRecord.kind == "loan",
            MetricRecord.segment == "funded"))).scalars().all()
        funded = [f for f in funded if _in_states(f, _fw_states)]
        loan_by_email, loan_by_phone = {}, {}
        for f in funded:
            m = f.meta or {}
            for em in {(f.email or "").lower(), (m.get("borrower_email") or "").lower()}:
                if em:
                    loan_by_email.setdefault(em, f)
            if m.get("borrower_phone"):
                loan_by_phone.setdefault(str(m["borrower_phone"]), f)

        def linked(t):
            for em in {(t.buyer_email or "").lower(), (t.buyer_email2 or "").lower()}:
                if em and em in loan_by_email:
                    return loan_by_email[em]
            if t.buyer_phone and t.buyer_phone in loan_by_phone:
                return loan_by_phone[t.buyer_phone]
            return None

        buys = (await s.execute(select(Transaction).where(
            Transaction.tenant_id == tenant_id, Transaction.business_id == ulrg.id,
            Transaction.status == "closed", Transaction.side == "buy",
            Transaction.sale_price > 0, Transaction.close_date >= start,
            Transaction.close_date <= end, Transaction.buyer_email.isnot(None))
            .order_by(Transaction.sale_price.desc()))).scalars().all()
        financeable = [t for t in buys if t.mortgage_vid not in cash_vids]

        def bname(t):
            return (t.buyer_name or "").strip().title() or t.buyer_email

        def money(n):
            return f"${float(n or 0):,.0f}"

        if key == "flywheel_buyers":
            rows = [{"id": str(t.id), "name": bname(t),
                     "l2": " · ".join(x for x in [t.address, t.close_date.strftime("%b %d") if t.close_date else None] if x),
                     "r1": money(t.sale_price)} for t in financeable]
            return {"label": "ULRG buyer closings", "source": "Sisu",
                    "computed_as": f"Financeable buy-side ULRG closings (cash excluded), {span()}.",
                    "count": len(rows), "rows": rows}

        captured, elsewhere = [], []
        for t in financeable:
            (captured if (t.mortgage_vid in sympli_vids or linked(t)) else elsewhere).append(t)

        if key == "flywheel_captured":
            rows = []
            for t in captured:
                by_vid = t.mortgage_vid in sympli_vids
                ln = linked(t)
                how = "vendor pick" if by_vid else "email match"
                rows.append({"id": str(t.id), "name": bname(t), "seg": "MTG",
                             "l2": f"Financed via Sympli · {how}",
                             "r1": money(ln.amount if ln else t.sale_price),
                             "r2": t.address, "source_url": ln.source_url if ln else None})
            return {"label": "Financed via Sympli", "source": "Arive × Sisu",
                    "computed_as": f"Captured = the ULRG agent picked Sympli, or the buyer matches a "
                                   f"funded Sympli loan, {span()}.",
                    "count": len(rows), "rows": rows}

        # financed elsewhere — name the competing lender from the vendor directory
        rows = [{"id": str(t.id), "name": bname(t), "tone": "watch",
                 "l2": (lender_names.get(str(t.mortgage_vid)) or "Lender not recorded"),
                 "r1": money(t.sale_price), "r2": t.address} for t in elsewhere]
        return {"label": "Financed elsewhere", "source": "Sisu",
                "computed_as": f"Financeable ULRG buyers Sympli didn't win — by lender, {span()}.",
                "count": len(rows), "rows": rows}

    # ── flywheel: the call list (zero-referral agents) + per-agent referral detail ──
    if key in {"flywheel_zero_referrals", "flywheel_agent_referrals"}:
        from .metrics import _arive_states, _in_states
        bmap = {b.key: b for b in (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id))).scalars().all()}
        ulrg, sympli = bmap.get("ulrg"), bmap.get("sympli")
        if not (ulrg and sympli):
            return {"label": "Referral flywheel", "source": "Sisu × Arive", "rows": []}
        sisu_integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == tenant_id, Integration.provider == "sisu"))).scalars().first()
        vcfg = (sisu_integ.config or {}) if sisu_integ else {}
        sympli_vids = set(vcfg.get("sympli_mortgage_vids") or [])
        cash_vids = set(vcfg.get("cash_vids") or [])
        states = await _arive_states(s, tenant_id)
        funded = [f for f in (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == sympli.id,
            MetricRecord.source == "arive", MetricRecord.kind == "loan",
            MetricRecord.segment == "funded"))).scalars().all() if _in_states(f, states)]
        loan_by_email, loan_by_phone = {}, {}
        for f in funded:
            m = f.meta or {}
            for em in {(f.email or "").lower(), (m.get("borrower_email") or "").lower()}:
                if em:
                    loan_by_email.setdefault(em, f)
            if m.get("borrower_phone"):
                loan_by_phone.setdefault(str(m["borrower_phone"]), f)

        def linked(t):
            for em in {(t.buyer_email or "").lower(), (t.buyer_email2 or "").lower()}:
                if em and em in loan_by_email:
                    return loan_by_email[em]
            if t.buyer_phone and t.buyer_phone in loan_by_phone:
                return loan_by_phone[t.buyer_phone]
            return None

        names = {a.id: a.name for a in (await s.execute(select(Agent).where(
            Agent.tenant_id == tenant_id, Agent.business_id == ulrg.id))).scalars().all()}
        buys = (await s.execute(select(Transaction).where(
            Transaction.tenant_id == tenant_id, Transaction.business_id == ulrg.id,
            Transaction.status == "closed", Transaction.side == "buy",
            Transaction.sale_price > 0, Transaction.close_date >= start,
            Transaction.close_date <= end, Transaction.buyer_email.isnot(None)))).scalars().all()

        def money2(n):
            return f"${float(n or 0):,.0f}"

        if key == "flywheel_agent_referrals":
            recs = [(t, linked(t)) for t in buys
                    if str(t.agent_id) == str(agent_id)
                    and (t.mortgage_vid in sympli_vids or linked(t))]
            recs.sort(key=lambda tl: float(tl[0].sale_price or 0), reverse=True)
            rows = [{"id": str(t.id), "name": (t.buyer_name or t.buyer_email or "").title() or "Buyer",
                     "seg": "MTG",
                     "l2": ("vendor pick" if t.mortgage_vid in sympli_vids else "email match")
                     + (f" · {ln.occurred_on.strftime('%b %d')}" if ln and ln.occurred_on else ""),
                     "r1": money2(ln.amount if ln else t.sale_price),
                     "source_url": ln.source_url if ln else None} for t, ln in recs]
            aname = {str(k): v for k, v in names.items()}.get(str(agent_id), "Agent")
            return {"label": f"{aname} · Sympli referrals",
                    "source": "Arive × Sisu",
                    "computed_as": f"This agent's ULRG buyers who financed with Sympli, {span()}.",
                    "count": len(rows), "rows": rows}

        # zero-referral call list: producing agents (closed a deal) who referred nobody.
        caps, closed_ct = {}, {}
        for t in buys:
            if t.mortgage_vid in cash_vids:
                continue
            if t.mortgage_vid in sympli_vids or linked(t):
                caps[t.agent_id] = caps.get(t.agent_id, 0) + 1
        closed = (await s.execute(select(Transaction).where(
            Transaction.tenant_id == tenant_id, Transaction.business_id == ulrg.id,
            Transaction.status == "closed", Transaction.sale_price > 0,
            Transaction.close_date >= start, Transaction.close_date <= end))).scalars().all()
        for t in closed:
            if t.agent_id:
                closed_ct[t.agent_id] = closed_ct.get(t.agent_id, 0) + 1
        zero = [(aid, n) for aid, n in closed_ct.items() if not caps.get(aid)]
        zero.sort(key=lambda an: an[1], reverse=True)
        rows = [{"id": str(aid), "name": names.get(aid) or "House account", "tone": "watch",
                 "l2": f"{n} closed · 0 referred to Sympli", "r1": "0"} for aid, n in zero]
        return {"label": "Zero-referral producing agents", "source": "Sisu × Arive",
                "computed_as": f"Producing agents with no Sympli-financed closings — the call "
                               f"list, {span()}.", "count": len(rows), "rows": rows}

    # ── flywheel cross-check: the loans/deals behind each reconciliation number ──
    if key in {"flywheel_sympli_referred", "flywheel_sympli_linked",
               "flywheel_referral_no_deal", "flywheel_vendor_no_loan"}:
        from .metrics import _arive_states, _in_states
        bmap = {b.key: b for b in (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id))).scalars().all()}
        ulrg, sympli = bmap.get("ulrg"), bmap.get("sympli")
        if not (ulrg and sympli):
            return {"label": "Cross-check", "source": "Arive × Sisu", "rows": []}
        sisu_integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == tenant_id, Integration.provider == "sisu"))).scalars().first()
        vcfg = (sisu_integ.config or {}) if sisu_integ else {}
        sympli_vids = set(vcfg.get("sympli_mortgage_vids") or [])
        cash_vids = set(vcfg.get("cash_vids") or [])
        ref_domains = [d.lower().lstrip("@") for d in (vcfg.get("referral_domains") or ["liveutah.com"])]
        states = await _arive_states(s, tenant_id)
        funded = [f for f in (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == sympli.id,
            MetricRecord.source == "arive", MetricRecord.kind == "loan",
            MetricRecord.segment == "funded"))).scalars().all() if _in_states(f, states)]
        loan_by_email, loan_by_phone = {}, {}
        for f in funded:
            m = f.meta or {}
            for em in {(f.email or "").lower(), (m.get("borrower_email") or "").lower()}:
                if em:
                    loan_by_email.setdefault(em, f)
            if m.get("borrower_phone"):
                loan_by_phone.setdefault(str(m["borrower_phone"]), f)

        def ref_is_ulrg(f):
            m = f.meta or {}
            for e in (m.get("referral_email"), m.get("buyer_agent_email")):
                if e and any(str(e).lower().endswith(d) for d in ref_domains):
                    return True
            return False

        buys = (await s.execute(select(Transaction).where(
            Transaction.tenant_id == tenant_id, Transaction.business_id == ulrg.id,
            Transaction.status == "closed", Transaction.side == "buy",
            Transaction.sale_price > 0, Transaction.close_date >= start,
            Transaction.close_date <= end, Transaction.buyer_email.isnot(None)))).scalars().all()

        def linked(t):
            for em in {(t.buyer_email or "").lower(), (t.buyer_email2 or "").lower()}:
                if em and em in loan_by_email:
                    return loan_by_email[em]
            if t.buyer_phone and t.buyer_phone in loan_by_phone:
                return loan_by_phone[t.buyer_phone]
            return None

        # Which funded loans matched a ULRG closing this period (borrower link).
        matched_ids = {ln.external_id for t in buys if (ln := linked(t))}

        def money(n):
            return f"${float(n or 0):,.0f}"

        def lname(r):
            return (r.name or "").strip().title() or r.email or r.external_id

        # gap: ULRG agents who picked a Sympli vendor in Sisu but no funded loan lines up.
        if key == "flywheel_vendor_no_loan":
            fin = [t for t in buys if t.mortgage_vid not in cash_vids]
            recs = [t for t in fin if t.mortgage_vid in sympli_vids and not linked(t)]
            recs.sort(key=lambda t: float(t.sale_price or 0), reverse=True)
            rows = [{"id": str(t.id), "name": (t.buyer_name or t.buyer_email or "Buyer").title(),
                     "tone": "watch", "l2": "Picked Sympli in Sisu · no funded loan found",
                     "r1": money(t.sale_price), "r2": t.address,
                     "source_url": _sisu_url(t.external_id)} for t in recs]
            return {"label": "Picked Sympli, no loan", "source": "Sisu × Arive",
                    "computed_as": f"ULRG buyers whose agent chose a Sympli vendor, but no funded "
                                   f"Sympli loan matches the borrower, {span()}. Reconcile these.",
                    "count": len(rows), "rows": rows}

        # the three loan-side numbers: referred, linked, and the no-deal gap.
        in_period = [f for f in funded if f.occurred_on and start <= f.occurred_on <= end]
        referred = [f for f in in_period if ref_is_ulrg(f)]
        if key == "flywheel_sympli_linked":
            recs = [f for f in referred if f.external_id in matched_ids]
        elif key == "flywheel_referral_no_deal":
            recs = [f for f in referred if f.external_id not in matched_ids]
        else:
            recs = referred
        recs.sort(key=lambda r: float(r.amount or 0), reverse=True)

        def _reftag(f):
            nm = (f.meta or {}).get("referral_name")
            hit = f.external_id in matched_ids
            base = f"Utah Life: {nm}" if nm else "Utah Life referral"
            return base + (" · matched a ULRG closing" if hit else " · no ULRG closing found")

        warn = key == "flywheel_referral_no_deal"
        rows = [{"id": str(f.id), "name": lname(f), "seg": ("ULRG" if f.external_id in matched_ids else None),
                 "tone": "watch" if warn else None, "l2": _reftag(f),
                 "r1": money(f.amount),
                 "r2": f.occurred_on.strftime("%b %d") if f.occurred_on else None,
                 "source_url": f.source_url} for f in recs]
        label = {"flywheel_sympli_referred": "Credited to Utah Life",
                 "flywheel_sympli_linked": "Utah Life · matched to a ULRG deal",
                 "flywheel_referral_no_deal": "Utah Life referral, no ULRG deal"}[key]
        how = {"flywheel_sympli_referred": "Funded Sympli loans whose Arive referral source is Utah Life",
               "flywheel_sympli_linked": "…of those, the ones whose borrower matches a ULRG closing",
               "flywheel_referral_no_deal": "…and the ones with NO matching ULRG closing — the gap to reconcile"}[key]
        return {"label": label, "source": "Arive × Sisu",
                "computed_as": f"{how}, {span()}.", "count": len(rows), "rows": rows}

    # ── three-lens financials (the Sisu deals behind the P&L rows) ──
    if key in ("fin_closed", "fin_projected"):
        from .financials import _period as _finp, _projection_end
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id, Business.key == "ulrg"))).scalar_one_or_none()
        fstart, fend, is_cur = _finp(period)
        deals: list = []
        if biz:
            closed = (await s.execute(select(Transaction).where(
                Transaction.tenant_id == tenant_id, Transaction.business_id == biz.id,
                Transaction.status == "closed", Transaction.close_date >= fstart,
                Transaction.close_date <= fend).order_by(Transaction.close_date.desc()))).scalars().all()
            deals = [(t, "closed", t.close_date) for t in closed]
            if key == "fin_projected" and is_cur:
                pend_end = _projection_end(period, fstart, fend)
                pend = (await s.execute(select(Transaction).where(
                    Transaction.tenant_id == tenant_id, Transaction.business_id == biz.id,
                    Transaction.status == "pending", Transaction.expected_close_date >= fstart,
                    Transaction.expected_close_date <= pend_end).order_by(Transaction.expected_close_date))).scalars().all()
                deals += [(t, "pending", t.expected_close_date) for t in pend]
        rows = [_fin_row(t, kind, when) for (t, kind, when) in deals]
        label = "Projected GCI" if key == "fin_projected" else "Closed this period"
        return {"label": label, "source": "Sisu",
                "computed_as": ("Net GCI = gross GCI − agent commissions (company dollar, from Sisu "
                                "commission-info). Projection adds pending deals at the default agent split."),
                "count": len(rows), "rows": rows}

    if key == "fin_expenses":
        from .financials import _period as _finp, expense_run_rate, _period_months
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id, Business.key == "ulrg"))).scalar_one_or_none()
        fstart, fend, _ = _finp(period)
        months = _period_months(period, fstart, fend)
        rate, src = (await expense_run_rate(s, tenant_id, biz, fend)) if biz else (0.0, "manual")
        total = rate * months
        snaps = []
        if biz and src != "manual":
            snaps = (await s.execute(select(PLSnapshot).where(
                PLSnapshot.tenant_id == tenant_id, PLSnapshot.business_id == biz.id,
                PLSnapshot.period_end < fend.replace(day=1)).order_by(
                PLSnapshot.period_end.desc()).limit(1 if src == "last_month" else 3))).scalars().all()
        rows = [{"id": str(sn.id), "name": sn.period_end.strftime("%B %Y"),
                 "gci": float(sn.opex), "status": "operating expenses", "source_url": None} for sn in snaps]
        how = {"manual": "a manually-set monthly figure",
               "last_month": "last month's operating expenses",
               "trailing_3mo": "the trailing 3 months' operating expenses"}.get(src, src)
        span = (f"Monthly run-rate ${rate:,.0f} (from {how})" if months == 1
                else f"Monthly run-rate ${rate:,.0f} (from {how}) × {months} months = ${total:,.0f}")
        return {"label": "Est. expenses (run-rate)", "source": "QuickBooks",
                "computed_as": span + ".", "count": len(rows), "rows": rows}

    # ── revenue (QuickBooks) — the actual commission deposits behind the number ──
    if key == "revenue" and business:
        from .metrics import _pl_period
        from .sync import _valid_access_token
        from ..integrations import qbo as _qbo
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id, Business.key == business))).scalar_one_or_none()
        integ = None
        if biz:
            integ = (await s.execute(select(Integration).where(
                Integration.tenant_id == tenant_id, Integration.provider == "qbo",
                Integration.business_id == biz.id, Integration.status == "connected"))).scalars().first()
        rows = []
        if integ and integ.access_token_enc and integ.realm_id:
            try:
                token = await _valid_access_token(s, integ)
                ps, pe = _pl_period(period)
                deals = _qbo.deposits_to_deals(await _qbo.deposits(integ.realm_id, token, ps.isoformat(), pe.isoformat()))
                rows = [{"id": d["id"], "name": d["agent"] or "(unassigned)",
                         "address": d["property"], "gci": d["gci"], "close_date": d["date"],
                         "source_url": f"https://app.qbo.intuit.com/app/deposit?txnId={d['deposit_id']}"}
                        for d in deals]
            except Exception:  # noqa: BLE001 — fall back to the report link below
                rows = []
        if rows:
            return {"label": "Revenue", "source": "QuickBooks",
                    "computed_as": "Commission deposits posted this period — GCI, agent, and property per deal.",
                    "count": len(rows), "rows": rows,
                    "report_url": "https://app.qbo.intuit.com/app/reports"}

    # ── financial (QuickBooks) — itemize once QBO is connected ──
    if key in _FINANCIAL:
        return {"label": key.replace("_", " ").title(), "source": "QuickBooks",
                "computed_as": "QuickBooks Profit & Loss group total for the period.",
                "rows": [], "report_url": "https://app.qbo.intuit.com/app/reports"}

    if key in _PENDING_SRC:
        src, how = _PENDING_SRC[key]
        return {"label": key.replace("_", " ").title(), "source": src,
                "computed_as": f"{how}. Itemizes when {src} is connected.", "rows": []}

    return {"label": key.replace("_", " ").title(), "source": "—",
            "computed_as": "Drill-down for this metric isn't available yet.", "rows": []}
