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


async def metric_detail(s: AsyncSession, tenant_id, key: str, period: str, business: str | None = None) -> dict:
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
