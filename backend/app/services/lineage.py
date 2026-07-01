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
from ..models import Transaction, Agent, MetricRecord, Business
from .metrics import _period_range


def _sisu_url(external_id) -> str | None:
    if not external_id:
        return None
    try:
        return settings.SISU_TXN_URL.format(id=external_id)
    except (KeyError, IndexError):
        return None


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


async def metric_detail(s: AsyncSession, tenant_id, key: str, period: str) -> dict:
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
        return {"label": "Active listings", "source": "Sisu",
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

    # ── active members (Go High Level) — the real member records ──
    if key == "active_members":
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id, Business.key == "springb"))).scalar_one_or_none()
        recs = []
        if biz:
            recs = (await s.execute(select(MetricRecord).where(
                MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == biz.id,
                MetricRecord.source == "ghl", MetricRecord.kind == "member",
                MetricRecord.status == "active").order_by(MetricRecord.name))).scalars().all()
        rows = [{"id": str(r.id), "name": ((r.name or "").strip().title() or r.email or r.external_id),
                 "status": r.segment or "member", "source_url": r.source_url} for r in recs]
        return {"label": "Active members", "source": "Go High Level",
                "computed_as": "Contacts tagged as active members (beCollective + The Forum).",
                "count": len(rows), "rows": rows}

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
