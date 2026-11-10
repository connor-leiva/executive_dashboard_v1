"""ULRG L10 Scorecard — resolver registry + runner (SPEC-ulrg-scorecard Part 3.1, Step 5).

A resolver maps `(session, tenant_id, business_id, week_start, week_end, group)` to a single
`numeric | None` (`group` carries the scorecard team's context — `sisu_group_id` — for per-team
resolvers; business-wide ones ignore it). Register one with `@resolver(key)`; a metric opts in by
setting its `resolver_key`. The worker (`app.worker.scorecard_tick`) runs every active
resolver-backed metric once a day over the trailing look-back (the open week + the 2 before it, so
a late sync self-heals), writing `scorecard_value` with `source="resolver"`.

null ≠ zero (SPEC 0.2 / 2.3): a live source that genuinely counts nothing returns `0`, a real
datum. So a count resolver must NOT coalesce a real zero into None (the acceptance grep forbids
that idiom in this file); it guards on whether the feed is live and otherwise reports the true
count, zero included.

THREE outcomes, not two — the missing third one cost weeks of collected history:
  - a number      → the answer.
  - `UNAVAILABLE` → could not look: the feed is disconnected, the roster has not synced, the
                    metric is not wired. Says nothing about the week.
  - `None`        → looked, and this week genuinely has no value (e.g. an attach rate with an
                    empty denominator).

`UNAVAILABLE` and `None` were both spelled `None` until a credentials change disconnected Sisu.
Every Sisu-backed resolver started returning it, `_write` read it as "this week is empty", and the
runner — which re-resolves the trailing look-back every night — erased a fresh week each day while
the window slid forward. Cumulative totals collapsed with the weekly cells and every measurable
read as far off pace. When a source is unreachable, the number already stored is the best
information anyone has.

Overwrite rules (SPEC 3.1 / 3.3):
  - resolver raises            → caught, logged, the stored value is left untouched.
  - resolver returns a number  → written as `source="resolver"`; if it displaces a hand-entered
                                 value we log a warning so the conflict is visible, not mysterious.
  - resolver returns UNAVAILABLE → nothing is written or cleared.
  - resolver returns None      → clears a stale *resolver* value, but never erases a *manual* one
                                 (a transient feed gap must not delete someone's typed number).
"""
from __future__ import annotations

import datetime as dt
import functools
import logging
from collections.abc import Awaitable, Callable
from decimal import Decimal

from sqlalchemy import func, or_, select

from ..models import (Agent, Integration, ScorecardGroup, ScorecardMetric, ScorecardValue, Transaction)

log = logging.getLogger("app")

Resolver = Callable[..., Awaitable[float | None]]


class _ResolverRegistry(dict):
    """The resolver registry, plus PARAMETERISED keys: a metric's resolver_key "name:arg" resolves to
    the resolver registered as "name:*", called with arg=arg. One resolver then serves many rows that
    differ only in which entity they read (e.g. each company's profit on the Portfolio board), and the
    argument lives on the metric row as data, not in code. Every lookup — .get, [], `in` — handles it,
    so the runner, the drill and the scripts need no change."""

    def get(self, key, default=None):
        fn = dict.get(self, key)
        if fn is None and isinstance(key, str) and ":" in key:
            base, arg = key.split(":", 1)
            pfn = dict.get(self, base + ":*")
            if pfn is not None and arg:
                fn = functools.partial(pfn, arg=arg)
        return default if fn is None else fn

    def __getitem__(self, key):
        fn = self.get(key)
        if fn is None:
            raise KeyError(key)
        return fn

    def __contains__(self, key):
        return self.get(key) is not None


RESOLVERS: dict[str, Resolver] = _ResolverRegistry()


class _Unavailable:
    """Returned by a resolver that COULD NOT LOOK, as distinct from one that looked and found
    nothing. `_write` leaves the stored value alone for this; only a real None clears it.

    These two were both spelled None once, and it cost weeks of collected scorecard history. A
    live week with no closings returns a real 0.0, so None from a Sisu-backed resolver never
    meant "no data for this week" — it only ever meant the feed was unreachable. Clearing on it
    is backwards: when the source is down, the number already on the row is the best information
    anyone has. The runner re-resolves the trailing weeks daily, so a permanently disconnected
    feed erased a fresh week every day and the hole grew as the window slid."""
    __slots__ = ()

    def __repr__(self) -> str:                       # so log lines say why, not "<object ...>"
        return "UNAVAILABLE"

    def __bool__(self) -> bool:                      # never let `if val:` treat it as a number
        return False


UNAVAILABLE = _Unavailable()


def resolver(key: str):
    """Register a resolver under `key`; a metric wires to it via `resolver_key`."""
    def wrap(fn: Resolver) -> Resolver:
        RESOLVERS[key] = fn
        return fn
    return wrap


async def _sisu_live(s, tenant_id, business_id) -> bool:
    """True only if Sisu is a connected, at-least-once-synced feed for THIS business (or a
    tenant-wide feed with no business set). This is the null≠zero gate, and it is scoped to the
    same business the count uses — otherwise one business's live feed could make another's genuine
    collection gap read as a real `0`."""
    row = await s.scalar(select(Integration.id).where(
        Integration.tenant_id == tenant_id, Integration.provider == "sisu",
        Integration.status == "connected", Integration.last_synced_at.is_not(None),
        or_(Integration.business_id == business_id, Integration.business_id.is_(None))).limit(1))
    return row is not None


def _window_filters(key: str, week_start: dt.date, week_end: dt.date):
    """The per-deal WHERE clauses + the anchoring date column behind a resolver key. Shared by the
    COUNT resolver and the drill-down RECORD list so the two can never drift (the list a user clicks
    into is exactly the deals the count counted). Office restriction is applied separately. Returns
    (clauses, date_col) or (None, None) for an unknown key."""
    if key in ("ulrg_homes_closed", "ulrg_team_homes_closed"):
        return ([Transaction.status == "closed", Transaction.sale_price > 0,   # real sales; drops $0 referrals
                 Transaction.close_date >= week_start, Transaction.close_date <= week_end],
                Transaction.close_date)
    if key == "ulrg_team_under_contract":
        return ([Transaction.sale_price > 0,
                 Transaction.contract_date >= week_start, Transaction.contract_date <= week_end],
                Transaction.contract_date)
    if key == "ulrg_team_appts_met":
        return ([Transaction.appt_met_date >= week_start, Transaction.appt_met_date <= week_end],
                Transaction.appt_met_date)
    if key == "ulrg_team_signed":
        return ([Transaction.signed_date >= week_start, Transaction.signed_date <= week_end],
                Transaction.signed_date)
    return None, None


@resolver("ulrg_homes_closed")
async def homes_closed(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Homes closed in the week — count of Sisu `Transaction`s with status=closed and a real sale
    (sale_price > 0) whose `close_date` falls in [week_start, week_end], across the whole business.

    `sale_price > 0` matches the rest of the dashboard's units-closed accounting (services/metrics
    `require_sale=True`) and Sisu's own Closed count: it drops $0 outbound-referral "closings",
    which otherwise inflate the count ~7%. Business-wide (ignores `group`): powers the Overall
    "ULRG Q2 – 250 Homes" row — includes every office, even out-of-state agents. Returns None only
    when Sisu isn't a live feed; a live week with no closings is a real 0."""
    if not await _sisu_live(s, tenant_id, business_id):
        return UNAVAILABLE
    clauses, _ = _window_filters("ulrg_homes_closed", week_start, week_end)
    n = await s.scalar(select(func.count()).select_from(Transaction).where(
        Transaction.tenant_id == tenant_id, Transaction.business_id == business_id, *clauses))
    return float(n)   # func.count() is never None; a live week with 0 closings stays a real 0


async def _office_agent_ids(s, tenant_id, sisu_group_id: int) -> set | None:
    """Agent ids whose live Sisu memberships include this office group. None when the roster hasn't
    been synced yet (no agent carries memberships) — a collection gap, not an empty office. The JSON
    array membership is filtered in Python (cross-dialect + the roster is ~140 agents, tiny)."""
    rows = (await s.execute(select(Agent.id, Agent.sisu_group_ids).where(
        Agent.tenant_id == tenant_id, Agent.source == "sisu",
        Agent.sisu_group_ids.is_not(None)))).all()
    if not rows:
        return None
    return {aid for aid, groups in rows if isinstance(groups, list) and sisu_group_id in groups}


async def _team_count(s, tenant_id, business_id, group, extra):
    """Shared per-team body: None if the team isn't mapped to a Sisu office, if Sisu isn't live, or
    if the roster isn't synced (all gaps); otherwise the real count (0 included) of the team's deals
    matching `extra` (a list of WHERE clauses). Attribution is via the deal's agent's office. A
    synced-but-empty office (no agents assigned to it) is a real 0, not a gap — see null≠zero."""
    sgid = (group or {}).get("sisu_group_id")
    if sgid is None:
        return UNAVAILABLE                              # e.g. 'overall' — not a per-team row
    if not await _sisu_live(s, tenant_id, business_id):
        return UNAVAILABLE
    ids = await _office_agent_ids(s, tenant_id, sgid)
    if ids is None:                                     # roster not synced yet → couldn't look
        return UNAVAILABLE
    n = await s.scalar(select(func.count()).select_from(Transaction).where(
        Transaction.tenant_id == tenant_id, Transaction.business_id == business_id,
        Transaction.agent_id.in_(ids), *extra))         # empty office → IN () → real 0
    return float(n)


@resolver("ulrg_team_homes_closed")
async def team_homes_closed(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Per-team Homes Sold — closed real sales in the week whose agent is in this team's Sisu office
    (group.sisu_group_id). Same real-sale rule as the Overall row; scoped to the office's agents."""
    return await _team_count(s, tenant_id, business_id, group,
                             _window_filters("ulrg_team_homes_closed", week_start, week_end)[0])


@resolver("ulrg_team_under_contract")
async def team_under_contract(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Per-team Under Contract — deals that WENT under contract in the week (contract_date in the
    window) whose agent is in this team's office. A weekly flow of new contracts; sale_price > 0
    drops $0 referrals (contract price)."""
    return await _team_count(s, tenant_id, business_id, group,
                             _window_filters("ulrg_team_under_contract", week_start, week_end)[0])


@resolver("ulrg_team_appts_met")
async def team_appts_met(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Per-team Appointments Met — deals whose appointment was HELD in the week (Sisu `appt_dt` →
    `appt_met_date` in the window) whose agent is in this team's office. Counts the dated event, not
    the current pipeline stage, so a deal that has since progressed still counts for its appt week.
    No sale_price gate — an appointment isn't a sale."""
    return await _team_count(s, tenant_id, business_id, group,
                             _window_filters("ulrg_team_appts_met", week_start, week_end)[0])


@resolver("ulrg_team_signed")
async def team_signed(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Per-team Clients Signed — buyer/listing agreements signed in the week (Sisu `signed_dt` →
    `signed_date` in the window) whose agent is in this team's office. Dated event, not current stage."""
    return await _team_count(s, tenant_id, business_id, group,
                             _window_filters("ulrg_team_signed", week_start, week_end)[0])


async def _agent_names(s, tenant_id, ids: set) -> dict:
    if not ids:
        return {}
    return {aid: nm for aid, nm in (await s.execute(
        select(Agent.id, Agent.name).where(Agent.id.in_(ids)))).all()}


async def _sympli_records(s, tenant_id, week_start, week_end, group, limit):
    """Drill for the Sympli attach rate: the FINANCEABLE buyer closings that formed the denominator
    (via the shared flywheel capture), each flagged `captured` = it used Sympli (the numerator). So
    `captured of total` reads back exactly as the rate. Overall (no office) → all agents."""
    from .metrics import build_sympli_ctx, sympli_capture
    ctx = await build_sympli_ctx(s, tenant_id)
    if ctx is None:
        return []
    sgid = (group or {}).get("sisu_group_id")
    if sgid is None:
        agent_ids = None                                # Overall → all agents = the org flywheel set
    else:
        agent_ids = await _office_agent_ids(s, tenant_id, sgid)
        if not agent_ids:                               # unmapped office / roster not synced
            return []
    cap = await sympli_capture(ctx, s, tenant_id, week_start, week_end, agent_ids=agent_ids)
    cap_ids = cap["cap_ids"]
    fin = sorted(cap["fin"], key=lambda t: (t.close_date or dt.date.min), reverse=True)[:limit]
    names = await _agent_names(s, tenant_id, {t.agent_id for t in fin if t.agent_id})
    return [{"id": t.external_id, "client": t.buyer_name, "agent": names.get(t.agent_id),
             "date": t.close_date.isoformat() if t.close_date else None, "address": t.address, "side": t.side,
             "sale_price": float(t.sale_price) if t.sale_price is not None else None,
             "captured": t.id in cap_ids} for t in fin]


async def _meraki_records(s, tenant_id, business_id, week_start, week_end, group, limit):
    """Drill for the Meraki (title) attach rate: the office's closings that RECORDED a title company
    (the denominator), each flagged `captured` = title_vid ∈ meraki_title_vids (the numerator)."""
    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.provider == "sisu"))).scalars().first()
    meraki = {int(v) for v in ((integ.config or {}).get("meraki_title_vids") or [])} if integ else set()
    q = (select(Transaction, Agent.name).outerjoin(Agent, Transaction.agent_id == Agent.id)
         .where(Transaction.tenant_id == tenant_id, Transaction.business_id == business_id,
                Transaction.status == "closed", Transaction.title_vid.is_not(None),
                Transaction.close_date >= week_start, Transaction.close_date <= week_end))
    sgid = (group or {}).get("sisu_group_id")
    if sgid is not None:
        ids = await _office_agent_ids(s, tenant_id, sgid)
        if not ids:
            return []
        q = q.where(Transaction.agent_id.in_(ids))
    rows = (await s.execute(q.order_by(Transaction.close_date.desc()).limit(limit))).all()
    return [{"id": t.external_id, "client": t.buyer_name, "agent": nm,
             "date": t.close_date.isoformat() if t.close_date else None, "address": t.address, "side": t.side,
             "sale_price": float(t.sale_price) if t.sale_price is not None else None,
             "captured": t.title_vid in meraki} for t, nm in rows]


async def _forum_member_records(s, tenant_id, business_id, week_start, week_end, limit):
    """Drill for Forum · Members Added — exactly the members forum_members_added counted."""
    from ..models import MetricRecord
    from .forum import _pdate
    rows = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "member",
        MetricRecord.status == "active"))).scalars().all()
    out = []
    for r in rows:
        d = _pdate(((r.meta or {}).get("membership") or {}).get("enrollment_date"))
        if d and week_start <= d <= week_end:
            out.append({"id": r.external_id, "client": r.name, "agent": None, "date": d.isoformat(),
                        "address": None, "side": None, "sale_price": None, "detail": r.segment})
    return sorted(out, key=lambda x: x["date"], reverse=True)[:limit]


async def _forum_vip_records(s, tenant_id, business_id, week_start, week_end, limit):
    """Drill for Forum · VIP Guests Added — the stage-change events into the VIP Guest group."""
    from ..models import MetricRecord
    rows = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "forum_stage_event",
        MetricRecord.segment == "vip_guest",
        MetricRecord.occurred_on >= week_start, MetricRecord.occurred_on <= week_end)
        .order_by(MetricRecord.occurred_on.desc()).limit(limit))).scalars().all()
    return [{"id": r.external_id, "client": r.name, "agent": None,
             "date": r.occurred_on.isoformat() if r.occurred_on else None, "address": None,
             "side": None, "sale_price": None, "detail": (r.meta or {}).get("to_stage")} for r in rows]


def _drill_row(rid, client, date, detail, *, side=None, sale_price=None, company=None,
               direction=None, excluded=False, status=None):
    when = date.isoformat() if hasattr(date, "isoformat") else date
    return {"id": None if rid is None else str(rid), "client": client or "—", "agent": None,
            "date": when, "address": None, "side": side, "sale_price": sale_price, "detail": detail,
            "company": company, "direction": direction, "excluded": excluded, "status": status}


_DRILL_KEYS = frozenset({
    "forum_vip_guests_seats", "forum_vip_guests_spring", "forum_vip_guests_sisu",
    "sb_cost_per_qualified_lead", "sb_cash_collected_rate", "sb_edge_paid_added",
    "sb_renewals_confirmed", "sb_spring_break_tickets", "sb_net_cash",
    "sb_active_members", "sb_decisions_open", "sb_failed_payments_open",
    "sb_renewal_dates_current",
})


async def springb_drill(s, tenant_id, business_id, key: str, week_start: dt.date, week_end: dt.date,
                        group=None, limit: int = 500):
    """The rows behind a Spring B cell. None when this key isn't one of those resolvers.

    The list is the same filter as the number: a drawer that says there are no records
    while the cell shows 3 is a bug. Journal entries and transfers come back under
    `excluded` so they stay visible without being added into the total."""
    if key not in _DRILL_KEYS:
        return None
    from ..models import MetricRecord
    if key == "forum_vip_guests_seats":
        return await _seat_drill(s, tenant_id, business_id, week_start, week_end, limit)
    if key in ("forum_vip_guests_spring", "forum_vip_guests_sisu"):
        split = await _vip_split(s, tenant_id, business_id, week_start, week_end)
        channel = "sisu" if key.endswith("sisu") else "spring"
        if split is UNAVAILABLE:
            return {"records": [], "summary": "GHL hasn't synced this week, so these leads can't be listed.",
                    "source_label": "GHL"}
        _counts, recs = split
        rows = recs[channel][:limit]
        return {"records": rows, "summary": f"{len(rows)} qualified lead{'s' if len(rows) != 1 else ''}.",
                "source_label": "GHL"}
    if key == "sb_cost_per_qualified_lead":
        return await _cpl_drill(s, tenant_id, business_id, week_start, week_end, group, limit)
    if key == "sb_cash_collected_rate":
        return await _cash_drill(s, tenant_id, business_id, week_start, week_end, limit)
    if key == "sb_edge_paid_added":
        return await _edge_drill(s, tenant_id, business_id, week_start, week_end, limit)
    if key == "sb_renewals_confirmed":
        return await _renewals_drill(s, tenant_id, business_id, week_start, week_end, limit)
    if key == "sb_spring_break_tickets":
        return await _tickets_drill(s, tenant_id, business_id, week_end, limit)
    if key == "sb_net_cash":
        return await _net_cash_drill(s, tenant_id, business_id, week_start, week_end, limit)
    if key == "sb_active_members":
        return await _active_drill(s, tenant_id, business_id, week_start, week_end, limit)
    if key == "sb_decisions_open":
        return await _decisions_drill(s, tenant_id, business_id, week_end, limit)
    if key == "sb_failed_payments_open":
        return await _failed_drill(s, tenant_id, business_id, week_end, limit)
    if key == "sb_renewal_dates_current":
        return await _renewal_dates_drill(s, tenant_id, business_id, week_start, week_end, limit)
    return None


async def _seat_drill(s, tenant_id, business_id, week_start, week_end, limit):
    from ..models import MetricRecord
    events = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "forum_stage_event",
        MetricRecord.segment == "vip_guest_seat",
        MetricRecord.occurred_on >= week_start, MetricRecord.occurred_on <= week_end)
        .order_by(MetricRecord.occurred_on.desc()).limit(limit))).scalars().all()
    snaps = {ext: (meta or {}) for ext, meta in (await s.execute(select(
        MetricRecord.external_id, MetricRecord.meta).where(
            MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
            MetricRecord.source == "ghl", MetricRecord.kind == "recruiting"))).all()}
    rows = []
    for ev in events:
        meta = {**(snaps.get((ev.meta or {}).get("opp_id")) or {}), **(ev.meta or {})}
        stage = meta.get("to_stage") or meta.get("stage") or "seat"
        source = meta.get("opp_source") or meta.get("page_url") or "no source"
        proxy = bool(meta.get("proxy"))
        who = ev.name or meta.get("contact_name") or "Opportunity"
        rows.append(_drill_row(
            ev.external_id, who, ev.occurred_on,
            f"{stage} · {source} · {'proxy' if proxy else 'observed'}",
            side=stage, status="proxy" if proxy else "observed"))
    return {"records": rows,
            "summary": f"{len(rows)} seat{'s' if len(rows) != 1 else ''} entered this week.",
            "source_label": "GHL"}


async def _cpl_drill(s, tenant_id, business_id, week_start, week_end, group, limit):
    from ..models import AdAccount, AdCampaign, AdInsightDaily
    leads = await forum_vip_guests_spring(s, tenant_id, business_id, week_start, week_end, group)
    split = await _vip_split(s, tenant_id, business_id, week_start, week_end)
    lead_rows = [] if split is UNAVAILABLE else split[1]["spring"]
    accts = (await s.execute(select(AdAccount).where(
        AdAccount.tenant_id == tenant_id, AdAccount.platform == "meta"))).scalars().all()
    named = [a for a in accts if (a.external_id or "") in (_META_AD_ACCOUNT, _META_AD_ACCOUNT.removeprefix("act_"))]
    use = named or (accts if len(accts) == 1 else [])
    ids = [a.id for a in use]
    insights = []
    if ids:
        insights = (await s.execute(select(AdInsightDaily).where(
            AdInsightDaily.tenant_id == tenant_id, AdInsightDaily.ad_account_id.in_(ids),
            AdInsightDaily.level == "campaign",
            AdInsightDaily.occurred_on >= week_start, AdInsightDaily.occurred_on <= week_end))).scalars().all()
    names = {c.id: c.name for c in (await s.execute(select(AdCampaign).where(
        AdCampaign.tenant_id == tenant_id))).scalars().all()} if insights else {}
    spend_by = {}
    for row in insights:
        key = row.campaign_id or row.object_external_id
        spend_by.setdefault(key, {"name": names.get(row.campaign_id) or row.object_external_id, "spend": 0.0})
        spend_by[key]["spend"] += float(row.spend or 0)
    records = []
    total = 0.0
    for key, info in sorted(spend_by.items(), key=lambda kv: -kv[1]["spend"]):
        total += info["spend"]
        records.append(_drill_row(key, info["name"], week_start,
                                  "Meta campaign spend", side="Meta", sale_price=round(info["spend"], 2)))
    for lead in lead_rows[:limit]:
        records.append({**lead, "side": "Lead", "detail": lead.get("detail") or "Qualified Guest Lead"})
    n = 0 if leads in (None, UNAVAILABLE) else int(leads)
    if leads is UNAVAILABLE and not records:
        summary = "Meta or GHL hasn't synced, so cost per lead can't be listed."
    elif not n:
        summary = f"Meta spend ${total:,.2f}. No qualified leads this week, so the cell stays blank."
    else:
        summary = f"${total:,.2f} Meta spend ÷ {n} qualified lead{'s' if n != 1 else ''}."
    return {"records": records, "summary": summary, "source_label": "Meta"}


async def _cash_drill(s, tenant_id, business_id, week_start, week_end, limit):
    from ..models import MetricRecord
    from .billing import merge_payment_sources
    ghl_rows = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "payment"))).scalars().all()
    legacy = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "stripe_legacy", MetricRecord.kind == "payment"))).scalars().all()
    if not ghl_rows and not legacy:
        return {"records": [], "summary": "Neither GHL nor Stripe has payment rows yet.",
                "source_label": "Stripe"}
    native = [p for p in ghl_rows if not (p.meta or {}).get("imported")]
    merged, _sup = merge_payment_sources(native, legacy) if legacy else (native, 0)
    week = [p for p in merged if p.occurred_on and week_start <= p.occurred_on <= week_end
            and _forum_cash_row(p)]
    succ = sum(_cash_amount(p) for p in week if p.status == "succeeded")
    fail = sum(_cash_amount(p) for p in week if p.status == "failed")
    records = []
    for p in sorted(week, key=lambda r: r.occurred_on or week_start, reverse=True)[:limit]:
        src = "Stripe" if p.source == "stripe_legacy" else "GHL"
        records.append(_drill_row(
            p.external_id, p.name or p.email or "Member", p.occurred_on,
            f"{src} · {p.status or 'unknown'}", side=src, status=p.status,
            sale_price=round(_cash_amount(p), 2)))
    den = succ + fail
    if den == 0:
        summary = "No Forum attempts this week, so the cell stays blank."
    else:
        summary = f"Succeeded ${succ:,.2f} of ${den:,.2f} attempted."
    return {"records": records, "summary": summary, "source_label": "Stripe"}


async def _edge_drill(s, tenant_id, business_id, week_start, week_end, limit):
    from ..models import MetricRecord
    rows = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "stripe_legacy", MetricRecord.kind == "edge_subscription"))).scalars().all()
    kept = []
    for r in rows:
        meta = r.meta or {}
        start = _parse_day(meta.get("start_date"))
        if not start or not (week_start <= start <= week_end):
            continue
        if r.amount is not None and float(r.amount) <= 0:
            continue
        if "comp" in (meta.get("plan_name") or "").lower():
            continue
        if (r.status or "") not in ("active", "trialing", "past_due"):
            continue
        kept.append((r, start, meta))
    if not rows:
        return {"records": [], "summary": "Stripe hasn't synced Edge subscriptions yet.",
                "source_label": "Stripe"}
    records = [_drill_row(r.external_id, r.name or r.email or "Member", start,
                          meta.get("plan_name") or "Edge", side="Stripe", status=r.status,
                          sale_price=float(r.amount) if r.amount is not None else None)
               for r, start, meta in kept[:limit]]
    return {"records": records, "summary": f"{len(kept)} new paid Edge subscription{'s' if len(kept) != 1 else ''}.",
            "source_label": "Stripe"}


async def _renewals_drill(s, tenant_id, business_id, week_start, week_end, limit):
    from ..models import MetricRecord
    from .forum import _pdate
    if not _week_holds_today(week_start, week_end):
        return {"records": [], "summary": "Renewal status is a snapshot of this week only.",
                "source_label": "GHL"}
    members = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "member",
        MetricRecord.status == "active"))).scalars().all()
    end = week_end + dt.timedelta(days=60)
    due = []
    for m in members:
        mem = (m.meta or {}).get("membership") or {}
        if mem.get("member_kind") == "admin":
            continue
        rd = _pdate(mem.get("renewal_date"))
        if rd and week_end < rd <= end:
            due.append((m, rd, mem))
    records = [_drill_row(m.external_id, m.name or m.email or "Member", rd,
                          f"Renewal {rd.isoformat()} · {(mem.get('renewal_status') or 'blank')}",
                          side="GHL", status=mem.get("renewal_status") or "blank")
               for m, rd, mem in due[:limit]]
    confirmed = sum(1 for _m, _rd, mem in due if (mem.get("renewal_status") or "").strip().lower() == "confirmed")
    if not due:
        summary = "Nobody is due in the next 60 days, so the cell stays blank."
    else:
        summary = f"{confirmed} confirmed of {len(due)} due."
    return {"records": records, "summary": summary, "source_label": "GHL"}


async def _tickets_drill(s, tenant_id, business_id, week_end, limit):
    from ..models import MetricRecord
    if week_end < _SPRING_BREAK_ON:
        return {"records": [], "summary": "Spring Break tickets stay blank until Nov 27.",
                "source_label": "Stripe"}
    rows = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "stripe_legacy", MetricRecord.kind == "spring_break_payment",
        MetricRecord.status == "succeeded"))).scalars().all()
    kept = [r for r in rows if r.occurred_on and r.occurred_on <= week_end]
    kept.sort(key=lambda r: r.occurred_on, reverse=True)
    records = [_drill_row(r.external_id, r.name or r.email or "Charge", r.occurred_on,
                          f"Spring Break · {r.segment or 'ticket'}", side="Stripe", status=r.status,
                          sale_price=float(r.amount) if r.amount is not None else None)
               for r in kept[:limit]]
    return {"records": records, "summary": f"{len(kept)} ticket{'s' if len(kept) != 1 else ''} sold through this week.",
            "source_label": "Stripe"}


def _book_signed(t):
    kind = (t.qbo_type or "").replace(" ", "").lower()
    amt = float(t.amount or 0)
    if kind in _CASH_IN:
        return "in", amt
    if kind in _CASH_OUT:
        return "out", -amt
    return None, amt


async def _net_cash_books(s, tenant_id, business_id):
    from ..models import Business
    books = [b for b in (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id).order_by(Business.sort_order))).scalars().all()
             if _spring_book(b, business_id)]
    return books or []


async def _net_cash_drill(s, tenant_id, business_id, week_start, week_end, limit):
    from ..models import BookTxn
    books = await _net_cash_books(s, tenant_id, business_id)
    ids = [b.id for b in books] or [business_id]
    names = {b.id: b.name for b in books}
    txns = (await s.execute(select(BookTxn).where(
        BookTxn.tenant_id == tenant_id, BookTxn.business_id.in_(ids),
        BookTxn.txn_date >= week_start, BookTxn.txn_date <= week_end)
        .order_by(BookTxn.txn_date.desc()))).scalars().all()
    included, excluded = [], []
    for t in txns:
        direction, signed = _book_signed(t)
        company = names.get(t.business_id) or "Books"
        row = _drill_row(t.qbo_id, t.payee or t.qbo_type, t.txn_date, t.qbo_type,
                         side=t.qbo_type, sale_price=round(signed, 2), company=company,
                         direction=direction or "excluded", excluded=direction is None,
                         status=t.realm_id)
        if direction is None:
            excluded.append(row)
        else:
            included.append(row)
    groups = []
    targets = books or []
    for b in targets:
        rows = [r for r in included if r["company"] == b.name]
        groups.append({
            "company": b.name,
            "in": round(sum(r["sale_price"] for r in rows if r["direction"] == "in"), 2),
            "out": round(sum(-r["sale_price"] for r in rows if r["direction"] == "out"), 2),
            "net": round(sum(r["sale_price"] for r in rows), 2),
            "records": rows[:limit],
        })
    net = round(sum(g["net"] for g in groups), 2)
    names_l = ", ".join(g["company"] for g in groups) or "the three books"
    sign = "−" if net < 0 else ""
    summary = f"Net {sign}${abs(net):,.2f} across {names_l}."
    return {"records": included[:limit], "excluded": excluded[:limit], "groups": groups,
            "summary": summary, "source_label": "Books"}


async def _active_drill(s, tenant_id, business_id, week_start, week_end, limit):
    from ..models import MetricRecord
    if not _week_holds_today(week_start, week_end):
        return {"records": [], "summary": "Active members is a snapshot of this week only.",
                "source_label": "GHL"}
    members = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "member",
        MetricRecord.status == "active").limit(limit))).scalars().all()
    records = [_drill_row(m.external_id, m.name or m.email or "Member", m.occurred_on,
                          "Active member", side="GHL", status=m.status) for m in members]
    return {"records": records, "summary": f"{len(records)} active member{'s' if len(records) != 1 else ''}.",
            "source_label": "GHL"}


async def _decisions_drill(s, tenant_id, business_id, week_end, limit):
    from ..models import MetricRecord
    from .sync import _forum_stage_group
    rows = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "recruiting",
        MetricRecord.status == "open"))).scalars().all()
    cutoff = week_end - dt.timedelta(days=21)
    stuck = []
    for r in rows:
        meta = r.meta or {}
        if _forum_stage_group(meta.get("stage")) != "vip_guest":
            continue
        changed = _parse_day(meta.get("last_stage_change_at"))
        if changed and changed <= cutoff:
            stuck.append((r, changed, meta))
    records = [_drill_row(r.external_id, r.name or "Opportunity", changed,
                          f"{meta.get('stage') or 'VIP guest'} · quiet since {changed.isoformat()}",
                          side="GHL") for r, changed, meta in stuck[:limit]]
    return {"records": records, "summary": f"{len(stuck)} open decision{'s' if len(stuck) != 1 else ''}.",
            "source_label": "GHL"}


async def _failed_drill(s, tenant_id, business_id, week_end, limit):
    from ..models import MetricRecord
    pays = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "payment"))).scalars().all()
    cutoff = week_end - dt.timedelta(days=7)
    failed, succeeded = [], []
    for p in pays:
        if not p.occurred_on:
            continue
        if p.status == "succeeded":
            succeeded.append(p)
        elif p.status == "failed" and p.occurred_on <= cutoff:
            failed.append(p)

    def _recovered(f) -> bool:
        sub = (f.meta or {}).get("subscription_id")
        em = (f.email or "").strip().lower()
        for ok in succeeded:
            if ok.occurred_on <= f.occurred_on:
                continue
            if sub and (ok.meta or {}).get("subscription_id") == sub:
                return True
            if em and (ok.email or "").strip().lower() == em:
                return True
        return False

    open_ = [f for f in failed if not _recovered(f)]
    records = [_drill_row(p.external_id, p.name or p.email or "Member", p.occurred_on,
                          "Failed, still open", side="GHL", status="failed",
                          sale_price=float(p.amount) if p.amount is not None else None)
               for p in open_[:limit]]
    return {"records": records, "summary": f"{len(open_)} failed payment{'s' if len(open_) != 1 else ''} still open.",
            "source_label": "GHL"}


async def _renewal_dates_drill(s, tenant_id, business_id, week_start, week_end, limit):
    from ..models import MetricRecord
    from .forum import _pdate
    if not _week_holds_today(week_start, week_end):
        return {"records": [], "summary": "Renewal dates are a snapshot of this week only.",
                "source_label": "GHL"}
    members = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "member",
        MetricRecord.status == "active"))).scalars().all()
    dated = []
    for m in members:
        rd = _pdate(((m.meta or {}).get("membership") or {}).get("renewal_date"))
        dated.append((m, rd))
    records = [_drill_row(m.external_id, m.name or m.email or "Member", rd,
                          rd.isoformat() if rd else "No renewal date",
                          side="GHL", status="dated" if rd else "missing")
               for m, rd in dated[:limit]]
    have = sum(1 for _m, rd in dated if rd)
    summary = f"{have} of {len(dated)} active members have a renewal date." if dated else "No active members."
    return {"records": records, "summary": summary, "source_label": "GHL"}


async def resolver_records(s, tenant_id, business_id, key: str, week_start: dt.date, week_end: dt.date,
                           group=None, limit: int = 500):
    """The underlying Sisu deals behind a resolver's figure for a (metric, window) — powers the grid
    drill-down. For COUNT keys, the same WHERE clauses as the count (via `_window_filters`) + the same
    office restriction, so the list is exactly what was counted. For the two RATE keys (attach rates)
    it's the denominator deals, each flagged `captured` (numerator subset). Returns [] for an office
    that maps to no agents (or an unmapped/overall-only key with no records), None for an unknown key."""
    if key in _DRILL_KEYS:
        detail = await springb_drill(s, tenant_id, business_id, key, week_start, week_end, group, limit)
        return (detail or {}).get("records") or []
    if key in ROSTER_KEYS:
        return await _roster_records(key, s, tenant_id, business_id, week_start, week_end, group, limit)
    if key and key.startswith("sympli_"):
        return await scorecard_sympli.records(key, s, tenant_id, business_id, week_start, week_end, group, limit)
    if key == "forum_members_added":
        return await _forum_member_records(s, tenant_id, business_id, week_start, week_end, limit)
    if key == "forum_vip_guests_added":
        return await _forum_vip_records(s, tenant_id, business_id, week_start, week_end, limit)
    if key in ("forum_vip_guests_sisu", "forum_vip_guests_spring"):
        split = await _vip_split(s, tenant_id, business_id, week_start, week_end)
        if split is UNAVAILABLE:
            return []
        _counts, recs = split
        channel = "sisu" if key.endswith("sisu") else "spring"
        return recs[channel][:limit]
    if key == "ulrg_team_sympli_attach":
        return await _sympli_records(s, tenant_id, week_start, week_end, group, limit)
    if key == "ulrg_team_meraki_attach":
        return await _meraki_records(s, tenant_id, business_id, week_start, week_end, group, limit)
    clauses, date_col = _window_filters(key, week_start, week_end)
    if clauses is None:
        return None
    q = (select(Transaction, Agent.name).outerjoin(Agent, Transaction.agent_id == Agent.id)
         .where(Transaction.tenant_id == tenant_id, Transaction.business_id == business_id, *clauses))
    if key.startswith("ulrg_team_"):
        sgid = (group or {}).get("sisu_group_id")
        ids = await _office_agent_ids(s, tenant_id, sgid) if sgid is not None else None
        if not ids:                                     # unmapped office or roster not synced → no rows
            return []
        q = q.where(Transaction.agent_id.in_(ids))
    q = q.order_by(date_col.desc()).limit(limit)
    rows = (await s.execute(q)).all()
    out = []
    for txn, agent_name in rows:
        d = getattr(txn, date_col.key)
        out.append({"id": txn.external_id, "client": txn.buyer_name, "agent": agent_name,
                    "date": d.isoformat() if d else None, "address": txn.address, "side": txn.side,
                    "sale_price": float(txn.sale_price) if txn.sale_price is not None else None})
    return out


@resolver("ulrg_team_sympli_attach")
async def team_sympli_attach(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Sympli attach % for the week — the SAME flywheel capture (signal A: Sympli vid ∪ C: funded-loan
    link, over the financeable buyer-closing denominator), scoped to this office's agents (or ALL agents
    for the Overall row). Reuses metrics.sympli_capture, so the scorecard always reconciles with the org
    flywheel card. Returns None when not wired, the roster isn't synced, or there were no financeable
    buyer closings that week — a rate has no meaning without a denominator (that's a gap, not a 0%)."""
    if not await _sisu_live(s, tenant_id, business_id):
        return UNAVAILABLE
    from .metrics import build_sympli_ctx, sympli_capture
    ctx = await build_sympli_ctx(s, tenant_id)
    if ctx is None:
        return UNAVAILABLE
    sgid = (group or {}).get("sisu_group_id")
    if sgid is None:
        agent_ids = None                                # Overall → all agents = the org flywheel number
    else:
        agent_ids = await _office_agent_ids(s, tenant_id, sgid)
        if not agent_ids:                               # roster not synced, or office has no agents
            return UNAVAILABLE                          # ambiguous → never erase on it
    cap = await sympli_capture(ctx, s, tenant_id, week_start, week_end, agent_ids=agent_ids)
    fin = len(cap["fin"])
    return round(len(cap["cap_ids"]) / fin * 100, 1) if fin else None


@resolver("ulrg_team_meraki_attach")
async def team_meraki_attach(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Meraki (title) attach % for the week. Unlike Sympli, title isn't in the referral flywheel — it's
    a straight vendor-share over the deals themselves: of this office's closings that recorded a title
    company, the share that used Meraki (title_vid ∈ the tenant's meraki_title_vids). Per office, or ALL
    agents for the Overall row. Closings whose title vendor is unknown (Sisu's -1/-2 sentinels land as a
    null title_vid) drop out of the denominator — we don't dilute the rate with deals we can't classify.

    meraki_title_vids is carried on the Sisu integration config (set from the seed via --wire): the Sisu
    vendor directory 404s for this account, so the ids can't be auto-derived like Sympli's are. Returns
    None when not configured, the roster isn't synced, or no classifiable closings that week."""
    if not await _sisu_live(s, tenant_id, business_id):
        return UNAVAILABLE
    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.provider == "sisu"))).scalars().first()
    meraki = {int(v) for v in ((integ.config or {}).get("meraki_title_vids") or [])} if integ else set()
    if not meraki:                                      # not wired → couldn't look
        return UNAVAILABLE
    q = select(Transaction.title_vid).where(
        Transaction.tenant_id == tenant_id, Transaction.business_id == business_id,
        Transaction.status == "closed", Transaction.title_vid.is_not(None),
        Transaction.close_date >= week_start, Transaction.close_date <= week_end)
    sgid = (group or {}).get("sisu_group_id")
    if sgid is not None:                                 # per-office (Overall passes no group → all agents)
        agent_ids = await _office_agent_ids(s, tenant_id, sgid)
        if not agent_ids:                               # roster not synced, or office has no agents
            return UNAVAILABLE                          # ambiguous → never erase on it
        q = q.where(Transaction.agent_id.in_(agent_ids))
    vids = (await s.execute(q)).scalars().all()
    if not vids:                                        # no classifiable closings → a gap, not a 0%
        return None
    return round(sum(1 for v in vids if v in meraki) / len(vids) * 100, 1)


# ── runner (called by the worker) ────────────────────────────────────────────
LOOKBACK_WEEKS = 3     # re-resolve the open week + the 2 before it every day, so a missed Monday
                       # run or a late Sisu sync self-heals — writes are idempotent upserts.


# ── Recruiting (RECRUITING-SPEC §8) ──────────────────────────────────────────────────────────────
#
# Four figures the L10 board has been typing by hand since it was built. They stay HAND until a
# parallel week matches (the repo's standing rule) -- these only make the flip possible.
#
# UNAVAILABLE vs 0.0 IS THE WHOLE CARE HERE, and the class docstring above explains what
# conflating them cost. A workspace with no recruiting connection, or one whose first sync has
# not landed, CANNOT LOOK: it returns UNAVAILABLE and the hand-entered number on the row stays.
# A connected workspace with a quiet week looked and found nothing: it returns 0.0, and the row
# says zero, which is a fact somebody needs.


async def _recruiting_ready(s, tenant_id) -> bool:
    """Connected, configured and synced at least once. Anything less means we cannot look."""
    from ..models import Integration
    row = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id,
        Integration.provider == "ghl_recruiting"))).scalars().first()
    if row is None or (row.status or "") not in ("connected", "error"):
        return False
    cfg = row.config or {}
    return bool(cfg.get("pipeline_id") and cfg.get("synced_at"))


def _week_bounds(week_start: dt.date, week_end: dt.date):
    """The week as an aware UTC half-open interval, anchored on the business day.

    Business-local rather than UTC: a Monday-keyed week computed in UTC starts on Sunday evening
    in Denver, which moves every figure on the board by one day for six hours a night.
    """
    from ..services.recruiting_rules import business_tz
    tz = business_tz()
    start = dt.datetime.combine(week_start, dt.time.min, tzinfo=tz)
    end = dt.datetime.combine(week_end + dt.timedelta(days=1), dt.time.min, tzinfo=tz)
    return start, end


@resolver("ghl_recruiting_new")
async def recruiting_new_leads(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date,
                               group=None):
    """New Recruitment Leads / Week — candidates that ARRIVED in the week.

    Counted on `created_at_src`, the date GHL says the opportunity was created, not on when we
    first saw it: a workspace connecting mid-quarter would otherwise record its whole back
    catalogue as one enormous week.
    """
    from ..models import RecruitingCandidate
    if not await _recruiting_ready(s, tenant_id):
        return UNAVAILABLE
    start, end = _week_bounds(week_start, week_end)
    rows = (await s.execute(select(func.count(RecruitingCandidate.id)).where(
        RecruitingCandidate.tenant_id == tenant_id,
        RecruitingCandidate.created_at_src >= start,
        RecruitingCandidate.created_at_src < end))).scalar()
    return float(rows or 0)


@resolver("ghl_recruiting_held")
async def recruiting_appts_held(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date,
                                group=None):
    """Recruiting Appointments Met — appointments that were HELD in the week.

    `showed`, on the appointment's own start time. D8 also allows "reached the Met group" as a
    fallback definition; that is a config decision and not this resolver's to make, so this
    counts the thing GHL records rather than inferring it.
    """
    from ..models import RecruitingAppointment
    if not await _recruiting_ready(s, tenant_id):
        return UNAVAILABLE
    start, end = _week_bounds(week_start, week_end)
    rows = (await s.execute(select(func.count(RecruitingAppointment.id)).where(
        RecruitingAppointment.tenant_id == tenant_id,
        func.lower(RecruitingAppointment.status) == "showed",
        RecruitingAppointment.start_at >= start,
        RecruitingAppointment.start_at < end))).scalar()
    return float(rows or 0)


@resolver("ghl_recruiting_booked")
async def recruiting_appts_booked(s, tenant_id, business_id, week_start: dt.date,
                                  week_end: dt.date, group=None):
    """Recruiting Appts Booked (D10's new row) — appointments CREATED in the week.

    Booked and held are different weeks for the same meeting, and the SDR is measured on the
    first while the Team Leader is measured on the second. Counting either one twice would make
    one of them look like the other's work.
    """
    from ..models import RecruitingAppointment
    if not await _recruiting_ready(s, tenant_id):
        return UNAVAILABLE
    start, end = _week_bounds(week_start, week_end)
    rows = (await s.execute(select(func.count(RecruitingAppointment.id)).where(
        RecruitingAppointment.tenant_id == tenant_id,
        RecruitingAppointment.created_at_src >= start,
        RecruitingAppointment.created_at_src < end))).scalar()
    return float(rows or 0)


@resolver("ghl_recruiting_signed_qtd")
async def recruiting_signed_qtd(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date,
                                group=None):
    """QTD Agents Recruited — a SNAPSHOT, already cumulative, and never summed.

    Everything signed since the quarter began, as at the end of this week. The board's own
    snapshot handling keeps it from being added up across weeks; the resolver's job is to make
    each week's value the running total rather than that week's increment.

    Distinct CANDIDATES, not events: somebody moved out of Signed and back has still been
    recruited once.
    """
    from ..models import RecruitingStageEvent
    if not await _recruiting_ready(s, tenant_id):
        return UNAVAILABLE
    quarter_start = dt.date(week_end.year, 3 * ((week_end.month - 1) // 3) + 1, 1)
    start, end = _week_bounds(quarter_start, week_end)
    rows = (await s.execute(select(func.count(func.distinct(RecruitingStageEvent.candidate_id))).where(
        RecruitingStageEvent.tenant_id == tenant_id,
        RecruitingStageEvent.to_group == "Signed",
        RecruitingStageEvent.occurred_at >= start,
        RecruitingStageEvent.occurred_at < end))).scalar()
    return float(rows or 0)


# ── ULRG roster rows: Onboardings / Offboardings / Net agent count ──────────────────────────────
# Source = Sisu's TEAM list (get-team-agents, via sync_agent_offices): start_date is Sisu's start_dt,
# ended_date the day the roster sync first saw the agent gone (Sisu's last-update date). Office rows
# scope by the agent's Sisu office group; Overall is the whole team, out-of-office agents included,
# the same convention as Overall Homes Sold.

ROSTER_KEYS = ("ulrg_roster_onboarded", "ulrg_roster_offboarded", "ulrg_roster_net")


async def _roster_rows(s, tenant_id, business_id, group):
    """The agents in this row's scope, or UNAVAILABLE when Sisu isn't live or the team roster has
    never been read (no agent carries a start date yet) — a collection gap, not an empty office."""
    if not await _sisu_live(s, tenant_id, business_id):
        return UNAVAILABLE
    rows = (await s.execute(select(Agent.id, Agent.name, Agent.is_active, Agent.start_date,
                                   Agent.ended_date, Agent.sisu_group_ids).where(
        Agent.tenant_id == tenant_id, Agent.source == "sisu"))).all()
    if not any(r.start_date for r in rows):
        return UNAVAILABLE
    sgid = (group or {}).get("sisu_group_id")
    if sgid is not None:
        rows = [r for r in rows if isinstance(r.sisu_group_ids, list) and sgid in r.sisu_group_ids]
    return rows


def _roster_match(key: str, r, week_start: dt.date, week_end: dt.date) -> bool:
    if key == "ulrg_roster_onboarded":
        return r.start_date is not None and week_start <= r.start_date <= week_end
    if key == "ulrg_roster_offboarded":
        return r.ended_date is not None and week_start <= r.ended_date <= week_end
    # Net: on the team at the END of the week — joined by then, and either still here or left after.
    # An inactive agent with no ended_date left before tracking began, so is not counted.
    return ((r.start_date is None or r.start_date <= week_end)
            and (r.is_active or (r.ended_date is not None and r.ended_date > week_end)))


async def _roster_count(key, s, tenant_id, business_id, week_start, week_end, group):
    rows = await _roster_rows(s, tenant_id, business_id, group)
    if rows is UNAVAILABLE:
        return UNAVAILABLE
    return float(sum(1 for r in rows if _roster_match(key, r, week_start, week_end)))


@resolver("ulrg_roster_onboarded")
async def roster_onboarded(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Onboardings — agents whose Sisu start date falls in the week."""
    return await _roster_count("ulrg_roster_onboarded", s, tenant_id, business_id, week_start, week_end, group)


@resolver("ulrg_roster_offboarded")
async def roster_offboarded(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Offboardings — agents who left the Sisu team in the week."""
    return await _roster_count("ulrg_roster_offboarded", s, tenant_id, business_id, week_start, week_end, group)


@resolver("ulrg_roster_net")
async def roster_net(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Net agent count — a SNAPSHOT: agents on the team as of the week's last day."""
    return await _roster_count("ulrg_roster_net", s, tenant_id, business_id, week_start, week_end, group)


async def _roster_records(key, s, tenant_id, business_id, week_start, week_end, group, limit):
    rows = await _roster_rows(s, tenant_id, business_id, group)
    if rows is UNAVAILABLE:
        return []
    out = []
    for r in rows:
        if not _roster_match(key, r, week_start, week_end):
            continue
        d = r.ended_date if key == "ulrg_roster_offboarded" else r.start_date
        detail = ("Left the team" if key == "ulrg_roster_offboarded" else
                  "Joined" if key == "ulrg_roster_onboarded" else
                  "On the team" + (f" (left {r.ended_date.isoformat()})" if r.ended_date else ""))
        out.append({"id": str(r.id), "client": r.name, "date": d.isoformat() if d else None,
                    "detail": detail})
    out.sort(key=lambda x: x["client"] or "")
    return out[:limit]


# ── Spring B board (Forum lines) ────────────────────────────────────────────────────────────────

async def _ghl_live(s, tenant_id, business_id) -> bool:
    """The null≠zero gate for lines fed by the Forum's GHL location: connected and synced at least
    once, for THIS business (or tenant-wide). Same contract as _sisu_live."""
    row = await s.scalar(select(Integration.id).where(
        Integration.tenant_id == tenant_id, Integration.provider == "ghl",
        Integration.status == "connected", Integration.last_synced_at.is_not(None),
        or_(Integration.business_id == business_id, Integration.business_id.is_(None))).limit(1))
    return row is not None


@resolver("forum_members_added")
async def forum_members_added(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date,
                              group=None):
    """Forum · Members Added — active members whose GHL enrollment date falls in the week.

    The SAME member set (ghl `member` records, status active: Forum + Inner Circle, primary + add-on)
    and the SAME date reading (forum._pdate of meta.membership.enrollment_date) as the Forum tab's
    growth chart, so a week here reconciles with that chart's 'joined'. A member with no enrollment
    date can't be placed in a week — the Forum tab skips them the same way."""
    from ..models import MetricRecord
    from .forum import _pdate
    if not await _ghl_live(s, tenant_id, business_id):
        return UNAVAILABLE
    metas = (await s.execute(select(MetricRecord.meta).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "member",
        MetricRecord.status == "active"))).scalars().all()
    n = 0
    for meta in metas:
        d = _pdate(((meta or {}).get("membership") or {}).get("enrollment_date"))
        if d and week_start <= d <= week_end:
            n += 1
    return float(n)


@resolver("forum_vip_guests_added")
async def forum_vip_guests_added(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date,
                                 group=None):
    """Forum · VIP Guests Added — sales-funnel opportunities that ENTERED the VIP Guest stage group in
    the week (sync._record_forum_stage_changes; group-level, so Opt-In → Confirmed counts once).

    GHL keeps no stage history, so these events exist only from the day tracking began. A week that
    ended before then was never observed → UNAVAILABLE, never a 0 that would read as "nobody came".
    Until the first sync after deploy has run, there is no marker and every week is UNAVAILABLE."""
    from ..models import MetricRecord
    if not await _ghl_live(s, tenant_id, business_id):
        return UNAVAILABLE
    since = await s.scalar(select(func.min(MetricRecord.occurred_on)).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "forum_stage_tracking"))
    if since is None or week_end < since:
        return UNAVAILABLE
    n = await s.scalar(select(func.count()).select_from(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "forum_stage_event",
        MetricRecord.segment == "vip_guest",
        MetricRecord.occurred_on >= week_start, MetricRecord.occurred_on <= week_end))
    return float(n or 0)


# opportunity.source values from the Nov 10 HighLevel pass. Exact, case-insensitive.
# Anything else (including a blank) is unclassified until a tag or attribution URL says otherwise.
_SISU_SOURCES = frozenset({"sisu", "sisu private mastermind"})
_SPRING_SOURCES = frozenset({"vip page", "forummastermind.com/vip", "forummastermind.com/guest-application"})
_SISU_TAG = "forum vip guest sisu nov 2026"


def vip_channel(meta: dict | None) -> str | None:
    """'sisu' (Justin's registrations) | 'spring' (Spring's page leads) | None.

    Source wins. A blank source falls through to the Sisu tag, then the attribution URL
    (/sisu vs /vip or /guest-application). Unclassified rows stay out of both counts.
    """
    meta = meta or {}
    src = str(meta.get("opp_source") or "").strip().lower()
    if src in _SISU_SOURCES:
        return "sisu"
    if src in _SPRING_SOURCES or "forummastermind.com/vip" in src or "forummastermind.com/guest-application" in src:
        return "spring"
    tags = {str(t).strip().lower() for t in (meta.get("tags") or []) if t}
    if _SISU_TAG in tags or any("sisu" in t and "vip" in t for t in tags):
        return "sisu"
    url = str(meta.get("page_url") or "").strip().lower()
    if "/sisu" in url:
        return "sisu"
    if "/vip" in url or "/guest-application" in url:
        return "spring"
    return None


def _merge_vip_meta(event_meta: dict | None, snap: dict | None) -> dict:
    """Event fields win when present; the current recruiting snapshot fills the blanks."""
    out = dict(snap or {})
    for k, v in (event_meta or {}).items():
        if v not in (None, "", []):
            out[k] = v
    return out


async def _vip_split(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date):
    """(counts, records) for VIP-guest entries in the week, split by channel.

    UNAVAILABLE when GHL is down or the week ended before stage tracking began — same gate as
    forum_vip_guests_added, so a split never invents a zero for a week that was not observed.
    """
    from ..models import MetricRecord
    if not await _ghl_live(s, tenant_id, business_id):
        return UNAVAILABLE
    since = await s.scalar(select(func.min(MetricRecord.occurred_on)).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "forum_stage_tracking"))
    if since is None or week_end < since:
        return UNAVAILABLE
    events = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "forum_stage_event",
        MetricRecord.segment == "vip_guest",
        MetricRecord.occurred_on >= week_start, MetricRecord.occurred_on <= week_end))).scalars().all()
    snaps = {ext: (meta or {}) for ext, meta in (await s.execute(select(
        MetricRecord.external_id, MetricRecord.meta).where(
            MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
            MetricRecord.source == "ghl", MetricRecord.kind == "recruiting"))).all()}
    counts = {"sisu": 0, "spring": 0, "open": 0}
    records = {"sisu": [], "spring": [], "open": []}
    for ev in events:
        meta = _merge_vip_meta(ev.meta, snaps.get((ev.meta or {}).get("opp_id")))
        ch = vip_channel(meta) or "open"
        counts[ch] += 1
        records[ch].append({"id": ev.external_id, "client": ev.name, "agent": None,
                            "date": ev.occurred_on.isoformat() if ev.occurred_on else None,
                            "address": None, "side": None, "sale_price": None,
                            "detail": meta.get("opp_source") or meta.get("page_url") or ch})
    return counts, records


async def _vip_channel_count(s, tenant_id, business_id, week_start, week_end, channel: str):
    split = await _vip_split(s, tenant_id, business_id, week_start, week_end)
    if split is UNAVAILABLE:
        return UNAVAILABLE
    counts, _recs = split
    return float(counts[channel])


@resolver("forum_vip_guests_sisu")
async def forum_vip_guests_sisu(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date,
                                group=None):
    """Justin's VIP guests — entered the VIP Guest group from a Sisu source (or the Sisu tag / URL)."""
    return await _vip_channel_count(s, tenant_id, business_id, week_start, week_end, "sisu")


@resolver("forum_vip_guests_spring")
async def forum_vip_guests_spring(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date,
                                  group=None):
    """Spring's qualified guest leads — VIP Guest entries from the VIP page or guest application."""
    return await _vip_channel_count(s, tenant_id, business_id, week_start, week_end, "spring")


@resolver("forum_vip_guests_seats")
async def forum_vip_guests_seats(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date,
                                 group=None):
    """Justin's VIP guests added — first entry into a guest seat, every source.

    A seat is VIP Guest: Confirmed, Dallas VIP guest confirmed, or Attended when that
    opportunity never sat in a confirmed stage. Opt-In is Spring's lead, not a seat.
    The same person can be both. Weeks with no seat events are a real 0 once GHL has synced."""
    from ..models import MetricRecord
    any_evt = await s.scalar(select(MetricRecord.id).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "forum_stage_event",
        MetricRecord.segment == "vip_guest_seat").limit(1))
    if any_evt is None and not await _ghl_live(s, tenant_id, business_id):
        return UNAVAILABLE
    n = await s.scalar(select(func.count()).select_from(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "forum_stage_event",
        MetricRecord.segment == "vip_guest_seat",
        MetricRecord.occurred_on >= week_start, MetricRecord.occurred_on <= week_end))
    return float(n or 0)


# Meta reports spend in the ad account's timezone. The scorecard week is Mon–Sun in the
# business calendar. A purchase just after midnight can land in a different week here than
# it does in Ads Manager.
_META_AD_ACCOUNT = "act_587749862890426"


@resolver("sb_cost_per_qualified_lead")
async def sb_cost_per_qualified_lead(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date,
                                     group=None):
    """Meta campaign spend ÷ Spring's qualified guest leads for the week.

    Campaign-level insights only — ad-level rows would double-count the same dollars.
    Blank when there are no leads (a cost per lead of zero is not a number). UNAVAILABLE
    when Meta has never synced."""
    from ..models import AdAccount, AdCampaign, AdInsightDaily, Integration
    from .ads import cpl
    from .ads_rules import classify_campaign, is_forum_group
    leads = await forum_vip_guests_spring(s, tenant_id, business_id, week_start, week_end, group)
    if leads is UNAVAILABLE:
        return UNAVAILABLE
    accts = (await s.execute(select(AdAccount).where(
        AdAccount.tenant_id == tenant_id, AdAccount.platform == "meta"))).scalars().all()
    named = [a for a in accts if (a.external_id or "") in (_META_AD_ACCOUNT, _META_AD_ACCOUNT.removeprefix("act_"))]
    use = named or (accts if len(accts) == 1 else [])
    synced = any(a.last_synced_at for a in use) or await s.scalar(select(Integration.id).where(
        Integration.tenant_id == tenant_id, Integration.provider == "meta_ads",
        Integration.status == "connected", Integration.last_synced_at.is_not(None)).limit(1))
    if not use or not synced:
        any_row = await s.scalar(select(AdInsightDaily.id).where(
            AdInsightDaily.tenant_id == tenant_id, AdInsightDaily.level == "campaign").limit(1)) if use else None
        if any_row is None:
            return UNAVAILABLE
    ids = [a.id for a in use]
    rules_by_account = {a.id: a.group_rules for a in use}
    names = {c.id: (c.name, c.ad_account_id) for c in (await s.execute(select(AdCampaign).where(
        AdCampaign.tenant_id == tenant_id, AdCampaign.ad_account_id.in_(ids)))).scalars()}
    insights = (await s.execute(select(AdInsightDaily).where(
        AdInsightDaily.tenant_id == tenant_id, AdInsightDaily.ad_account_id.in_(ids),
        AdInsightDaily.level == "campaign",
        AdInsightDaily.occurred_on >= week_start, AdInsightDaily.occurred_on <= week_end))).scalars()
    spend = 0.0
    for row in insights:
        name, acct_id = names.get(row.campaign_id, (None, row.ad_account_id))
        if not name or not is_forum_group(classify_campaign(name, rules_by_account.get(acct_id))):
            continue
        spend += float(row.spend or 0)
    if not leads:
        return None
    return cpl(spend, leads)


@resolver("sb_ad_spend_vs_budget")
async def sb_ad_spend_vs_budget(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date,
                                group=None):
    """This week's Meta spend ÷ the planned weekly budget, as a percent.

    The plan is the sum of the weekly budgets entered on the Ads tab. No plan means the
    cell stays blank rather than reading as zero."""
    from ..models import AdAccount, AdInsightDaily
    accts = (await s.execute(select(AdAccount).where(
        AdAccount.tenant_id == tenant_id, AdAccount.platform == "meta"))).scalars().all()
    plan = sum(float(v) for a in accts for v in (a.weekly_budgets or {}).values() if v not in (None, ""))
    if not plan:
        return None
    if not accts:
        return UNAVAILABLE
    ids = [a.id for a in accts]
    spend = await s.scalar(select(func.coalesce(func.sum(AdInsightDaily.spend), 0)).where(
        AdInsightDaily.tenant_id == tenant_id, AdInsightDaily.ad_account_id.in_(ids),
        AdInsightDaily.level == "campaign",
        AdInsightDaily.occurred_on >= week_start, AdInsightDaily.occurred_on <= week_end))
    return round(float(spend or 0) / plan * 100, 1)


def _week_holds_today(week_start: dt.date, week_end: dt.date, today: dt.date | None = None) -> bool:
    today = today or dt.date.today()
    return week_start <= today <= week_end


def _parse_day(v):
    if v is None or v == "":
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


@resolver("sb_edge_paid_added")
async def sb_edge_paid_added(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date,
                             group=None):
    """New paid Edge subscriptions whose start date falls in the week. Comped plans (name contains
    'comp') and zero-amount subs are excluded. UNAVAILABLE until the legacy Stripe feed has synced."""
    from ..models import MetricRecord
    rows = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "stripe_legacy", MetricRecord.kind == "edge_subscription"))).scalars().all()
    live = await s.scalar(select(Integration.id).where(
        Integration.tenant_id == tenant_id, Integration.provider == "stripe_legacy",
        Integration.status == "connected",
        or_(Integration.business_id == business_id, Integration.business_id.is_(None))).limit(1))
    if live is None and not rows:
        from ..models import Business
        missing = (await s.execute(select(Business.key).where(
            Business.tenant_id == tenant_id, Business.kind == "membership"))).scalars().all()
        print(f"[sb_edge_paid_added] no stripe_legacy on this business; membership businesses={list(missing)}",
              flush=True)
        return UNAVAILABLE
    n = 0
    for r in rows:
        meta = r.meta or {}
        start = _parse_day(meta.get("start_date"))
        if not start or not (week_start <= start <= week_end):
            continue
        if r.amount is not None and float(r.amount) <= 0:
            continue
        if "comp" in (meta.get("plan_name") or "").lower():
            continue
        if (r.status or "") not in ("active", "trialing", "past_due"):
            continue
        n += 1
    return float(n)


@resolver("sb_active_members")
async def sb_active_members(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date,
                            group=None):
    """Active non-admin Forum members, as of the current week only.

    The roster is a snapshot, so writing it into last month would rewrite history. Weeks that
    don't contain today stay UNAVAILABLE and keep whatever was entered by hand. Comped seats are
    not a flag on this roster — the count is every active member."""
    from ..models import MetricRecord
    if not _week_holds_today(week_start, week_end):
        return UNAVAILABLE
    if not await _ghl_live(s, tenant_id, business_id):
        return UNAVAILABLE
    any_member = await s.scalar(select(MetricRecord.id).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "member").limit(1))
    if any_member is None:
        return UNAVAILABLE
    n = await s.scalar(select(func.count()).select_from(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "member",
        MetricRecord.status == "active"))
    return float(n or 0)


def _cash_amount(p) -> float:
    try:
        return float(p.amount or 0)
    except (TypeError, ValueError):
        return 0.0


def _forum_cash_row(p) -> bool:
    """The same charges the Forum tab bills: Forum/IC segments, plus unlabeled rows on this feed.

    A labeled non-Forum product (Edge, Spring Break) stays out. project_charges is not used —
    that projects future subscription amounts, and this row is money attempted this week."""
    from .billing import forum_offering
    meta = p.meta or {}
    seg = p.segment or meta.get("segment")
    if seg in ("forum", "inner_circle"):
        return True
    desc = meta.get("entity_source_name") or ""
    if not desc:
        return True
    include, _seg = forum_offering(
        desc, amount=_cash_amount(p), recurring=bool(meta.get("subscription_id")))
    return include


@resolver("sb_cash_collected_rate")
async def sb_cash_collected_rate(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date,
                                 group=None):
    """Dollar-weighted collections for the week: succeeded $ ÷ (succeeded $ + failed $).

    Same stitched set as the Forum tab: CSV-imported GHL copies dropped, then
    merge_payment_sources with the legacy Stripe Forum charges. UNAVAILABLE when neither
    feed has synced. None when the week had no attempts."""
    from ..models import Integration, MetricRecord
    from .billing import merge_payment_sources
    ghl_rows = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "payment"))).scalars().all()
    legacy = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "stripe_legacy", MetricRecord.kind == "payment"))).scalars().all()
    ghl_up = await _ghl_live(s, tenant_id, business_id)
    stripe_up = await s.scalar(select(Integration.id).where(
        Integration.tenant_id == tenant_id, Integration.provider == "stripe_legacy",
        Integration.status == "connected",
        or_(Integration.business_id == business_id, Integration.business_id.is_(None))).limit(1))
    if not ghl_rows and not legacy and not (ghl_up or stripe_up):
        return UNAVAILABLE
    if not ghl_rows and not legacy:
        return UNAVAILABLE
    native = [p for p in ghl_rows if not (p.meta or {}).get("imported")]
    merged, _sup = merge_payment_sources(native, legacy) if legacy else (native, 0)
    week = [p for p in merged if p.occurred_on and week_start <= p.occurred_on <= week_end
            and _forum_cash_row(p)]
    succ = sum(_cash_amount(p) for p in week if p.status == "succeeded")
    fail = sum(_cash_amount(p) for p in week if p.status == "failed")
    den = succ + fail
    if den == 0:
        return None
    return round(succ / den * 100, 1)


_CASH_IN = frozenset({"deposit", "salesreceipt", "payment", "creditmemo"})
_CASH_OUT = frozenset({"purchase", "billpayment", "expense", "check", "creditcardcharge"})


def _spring_book(b, scorecard_business_id) -> bool:
    """The Forum, beCollective, and SB Coaching. Not the brokerage or the JV."""
    if b.kind in ("real_estate", "commission_jv"):
        return False
    if b.id == scorecard_business_id:
        return True
    blob = f"{b.name or ''} {b.key or ''}".lower()
    return ("forum" in blob or "becollective" in blob or "be collective" in blob or "coaching" in blob)


_net_cash_logged = False


async def _log_net_cash_census(s, tenant_id, books) -> None:
    """One line per realm, type, and recent week so a deploy log shows what the books actually hold.

    Sales receipts and customer payments were not in the QuickBooks pull, so a week can
    score $0 while the company did take cash. This count is the check."""
    from collections import Counter
    from ..models import BookTxn
    ids = [b.id for b in books]
    names = {b.id: b.name for b in books}
    since = dt.date.today() - dt.timedelta(weeks=16)
    rows = (await s.execute(select(
        BookTxn.business_id, BookTxn.realm_id, BookTxn.qbo_type, BookTxn.txn_date, func.count()
    ).where(BookTxn.tenant_id == tenant_id, BookTxn.business_id.in_(ids), BookTxn.txn_date >= since)
        .group_by(BookTxn.business_id, BookTxn.realm_id, BookTxn.qbo_type, BookTxn.txn_date))).all()
    by_type = Counter()
    by_week = Counter()
    for bid, realm, qbo_type, txn_date, n in rows:
        kind = (qbo_type or "").replace(" ", "").lower()
        bucket = "in" if kind in _CASH_IN else "out" if kind in _CASH_OUT else "excluded"
        week = txn_date - dt.timedelta(days=txn_date.weekday())
        by_type[(names.get(bid), realm, qbo_type, bucket)] += n
        by_week[(week.isoformat(), names.get(bid), qbo_type, bucket)] += n
    for (name, realm, qbo_type, bucket), n in sorted(by_type.items(), key=lambda kv: (str(kv[0][0]), kv[0][2])):
        print(f"[sb_net_cash] realm={realm} business={name} type={qbo_type} {bucket}={n}", flush=True)
    for (week, name, qbo_type, bucket), n in sorted(by_week.items()):
        print(f"[sb_net_cash] week={week} business={name} type={qbo_type} {bucket}={n}", flush=True)


@resolver("sb_net_cash")
async def sb_net_cash(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Cash in minus cash out for the week across The Forum, beCollective, and SB Coaching.

    Deposits, sales receipts and payments count in. Purchases, expenses and bill payments count
    out. Transfers and journal entries are skipped. UNAVAILABLE only when none of those books
    has a QuickBooks transaction yet."""
    global _net_cash_logged
    from ..models import BookTxn, Business
    books = [b for b in (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id))).scalars().all() if _spring_book(b, business_id)]
    ids = [b.id for b in books] or [business_id]
    any_txn = await s.scalar(select(BookTxn.id).where(
        BookTxn.tenant_id == tenant_id, BookTxn.business_id.in_(ids)).limit(1))
    if any_txn is None:
        return UNAVAILABLE
    if not _net_cash_logged:
        realms = sorted({b.key for b in books})
        print(f"[sb_net_cash] businesses={[b.name for b in books]} keys={realms}", flush=True)
        await _log_net_cash_census(s, tenant_id, books)
        _net_cash_logged = True
    txns = (await s.execute(select(BookTxn).where(
        BookTxn.tenant_id == tenant_id, BookTxn.business_id.in_(ids),
        BookTxn.txn_date >= week_start, BookTxn.txn_date <= week_end))).scalars().all()
    net = 0.0
    for t in txns:
        kind = (t.qbo_type or "").replace(" ", "").lower()
        amt = float(t.amount or 0)
        if kind in _CASH_IN:
            net += amt
        elif kind in _CASH_OUT:
            net -= amt
    return round(net, 2)


_SPRING_BREAK_ON = dt.date(2026, 11, 27)


@resolver("sb_spring_break_tickets")
async def sb_spring_break_tickets(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date,
                                  group=None):
    """Cumulative succeeded Spring Break tickets as of the week's Sunday.

    The row is off until Nov 27, so earlier weeks stay blank instead of reading as zero.
    UNAVAILABLE until the legacy Stripe feed has produced ticket rows or is connected and
    the season has started with an empty result (a real 0)."""
    from ..models import Integration, MetricRecord
    if week_end < _SPRING_BREAK_ON:
        return None
    rows = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "stripe_legacy", MetricRecord.kind == "spring_break_payment",
        MetricRecord.status == "succeeded"))).scalars().all()
    live = await s.scalar(select(Integration.id).where(
        Integration.tenant_id == tenant_id, Integration.provider == "stripe_legacy",
        Integration.status == "connected",
        or_(Integration.business_id == business_id, Integration.business_id.is_(None))).limit(1))
    if not rows and live is None:
        return UNAVAILABLE
    n = sum(1 for r in rows if r.occurred_on and r.occurred_on <= week_end)
    return float(n)


@resolver("sb_renewals_confirmed")
async def sb_renewals_confirmed(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date,
                                group=None):
    """Confirmed ÷ members whose renewal date falls in the 60 days after this week.

    Renewal Status is the GHL field (Confirmed / Not yet / Not renewing). Not yet, Not renewing,
    and blank stay in the denominator only. Current week only — the roster is a snapshot.
    Blank when nobody is due. UNAVAILABLE when GHL has not synced."""
    from ..models import MetricRecord
    from .forum import _pdate
    if not _week_holds_today(week_start, week_end):
        return UNAVAILABLE
    if not await _ghl_live(s, tenant_id, business_id):
        return UNAVAILABLE
    members = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "member",
        MetricRecord.status == "active"))).scalars().all()
    if not members:
        return UNAVAILABLE
    end = week_end + dt.timedelta(days=60)
    due = []
    for m in members:
        mem = (m.meta or {}).get("membership") or {}
        if mem.get("member_kind") == "admin":
            continue
        rd = _pdate(mem.get("renewal_date"))
        if rd and week_end < rd <= end:
            due.append(mem)
    if not due:
        return None
    confirmed = sum(1 for mem in due if (mem.get("renewal_status") or "").strip().lower() == "confirmed")
    return round(confirmed / len(due) * 100, 1)


@resolver("sb_decisions_open")
async def sb_decisions_open(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date,
                            group=None):
    """Open VIP-guest opportunities whose stage hasn't changed in 21 days. Current week only."""
    from ..models import MetricRecord
    if not _week_holds_today(week_start, week_end):
        return UNAVAILABLE
    if not await _ghl_live(s, tenant_id, business_id):
        return UNAVAILABLE
    rows = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "recruiting",
        MetricRecord.status == "open"))).scalars().all()
    any_event = await s.scalar(select(MetricRecord.id).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "forum_stage_event").limit(1))
    if not rows and any_event is None:
        return UNAVAILABLE
    from .sync import _forum_stage_group
    cutoff = week_end - dt.timedelta(days=21)
    n = 0
    for r in rows:
        meta = r.meta or {}
        if _forum_stage_group(meta.get("stage")) != "vip_guest":
            continue
        changed = _parse_day(meta.get("last_stage_change_at"))
        if changed and changed <= cutoff:
            n += 1
    return float(n)


@resolver("sb_failed_payments_open")
async def sb_failed_payments_open(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date,
                                  group=None):
    """Failed GHL payments older than 7 days with no later success on the same subscription or email.
    Current week only — it's a stock, not a flow."""
    from ..models import MetricRecord
    if not _week_holds_today(week_start, week_end):
        return UNAVAILABLE
    if not await _ghl_live(s, tenant_id, business_id):
        return UNAVAILABLE
    pays = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "payment"))).scalars().all()
    if not pays:
        return UNAVAILABLE
    cutoff = week_end - dt.timedelta(days=7)
    failed, succeeded = [], []
    for p in pays:
        if not p.occurred_on:
            continue
        if p.status == "succeeded":
            succeeded.append(p)
        elif p.status == "failed" and p.occurred_on <= cutoff:
            failed.append(p)

    def _recovered(f) -> bool:
        sub = (f.meta or {}).get("subscription_id")
        em = (f.email or "").strip().lower()
        for ok in succeeded:
            if ok.occurred_on <= f.occurred_on:
                continue
            if sub and (ok.meta or {}).get("subscription_id") == sub:
                return True
            if em and (ok.email or "").strip().lower() == em:
                return True
        return False

    return float(sum(1 for f in failed if not _recovered(f)))


@resolver("sb_renewal_dates_current")
async def sb_renewal_dates_current(s, tenant_id, business_id, week_start: dt.date, week_end: dt.date,
                                   group=None):
    """Active members with a renewal date on the GHL membership field ÷ active members, as a percent.
    Current week only. 'Verified' is the field being filled in — there is no second confirmation flag."""
    from ..models import MetricRecord
    from .forum import _pdate
    if not _week_holds_today(week_start, week_end):
        return UNAVAILABLE
    if not await _ghl_live(s, tenant_id, business_id):
        return UNAVAILABLE
    members = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "member",
        MetricRecord.status == "active"))).scalars().all()
    if not members:
        return UNAVAILABLE
    ok = sum(1 for m in members if _pdate(((m.meta or {}).get("membership") or {}).get("renewal_date")))
    return round(ok / len(members) * 100, 1)


def _recent_weeks(today: dt.date, n: int = LOOKBACK_WEEKS) -> list[tuple[dt.date, dt.date]]:
    """The `n` most recent Monday…Sunday weeks (newest first): the open week and the n-1 before it.
    Weeks are keyed by their Monday (SPEC 2.3)."""
    monday = today - dt.timedelta(days=today.weekday())
    weeks = []
    for k in range(n):
        ws = monday - dt.timedelta(days=7 * k)
        weeks.append((ws, ws + dt.timedelta(days=6)))
    return weeks


async def _write(s, tenant_id, metric_id, week_start: dt.date, val, key: str,
                 fill_only: bool = False) -> None:
    """Upsert one resolver result, honoring the overwrite rules above.

    `fill_only` is for RECOVERY: write only where nothing survives, and never touch a value that
    is already there. Repairing a gap and re-deriving months of settled history are different
    operations, and a recovery run that quietly does both is worse than one that refuses. The
    first backfill after the erasure would have rewritten 215 hand-entered figures — several off
    by more than double — on the way to restoring 20 blank weeks."""
    row = (await s.execute(select(ScorecardValue).where(
        ScorecardValue.metric_id == metric_id,
        ScorecardValue.week_start == week_start))).scalar_one_or_none()

    if val is UNAVAILABLE:                              # could not look — leave the row alone
        return
    if fill_only and row is not None and row.value is not None:
        return                                          # something survives here; not ours to redo
    if val is None:                                     # looked; genuinely nothing for this week
        if row is not None and row.source == "resolver":
            row.value = None                            # clear our own stale number
        return                                          # never erase a manual value with a gap

    dec = Decimal(str(val))
    if row is None:
        s.add(ScorecardValue(tenant_id=tenant_id, metric_id=metric_id, week_start=week_start,
                             value=dec, source="resolver"))
        return
    if row.source == "manual":
        log.warning("scorecard: resolver %s overrode a manual value for metric %s week %s",
                    key, metric_id, week_start)
    row.value, row.source, row.entered_by = dec, "resolver", None


async def run_resolvers(session_factory, tenant_id, today: dt.date, weeks: int = LOOKBACK_WEEKS,
                        fill_only: bool = False) -> int:
    """Run every active resolver-backed metric for the recent weeks (the open week plus the trailing
    look-back, so a late sync or a missed daily run self-heals). Returns the count of (metric, week)
    results committed.

    Each (metric, week) runs in its OWN transaction: a resolver that raises — even mid-query, which
    would poison a shared session — is caught, rolled back, and left untouched (SPEC 3.1), while
    every other metric still commits. `session_factory` is the `SessionLocal` maker."""
    # `weeks` widens the window for RECOVERY. The daily tick only reaches LOOKBACK_WEEKS back,
    # so anything damaged or missed further out — the weeks a dark feed erased before the
    # UNAVAILABLE fix — can only be rebuilt by asking for a wider window explicitly.
    week_windows = _recent_weeks(today, weeks)

    async with session_factory() as s:                  # read the work list up front, read-only
        metrics = (await s.execute(
            select(ScorecardMetric.id, ScorecardMetric.resolver_key, ScorecardMetric.source_spec,
                   ScorecardGroup.business_id, ScorecardGroup.key, ScorecardGroup.sisu_group_id,
                   ScorecardGroup.lo_email)
            .join(ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id)
            .where(ScorecardMetric.tenant_id == tenant_id,
                   ScorecardMetric.active.is_(True),
                   or_(ScorecardMetric.resolver_key.is_not(None),
                       ScorecardMetric.source_spec.is_not(None))))).all()

    from .scorecard_routing import run_spec              # lazy import — breaks the module cycle

    written = 0
    for metric_id, key, source_spec, business_id, group_key, sisu_group_id, lo_email in metrics:
        # A declarative source_spec WINS over a legacy resolver_key (SCORECARD-CONFIG-SPEC Phase 2),
        # so flipping a metric onto a spec is a data change, not a code change.
        if not source_spec:
            fn = RESOLVERS.get(key)
            if fn is None:
                log.warning("scorecard: metric %s names resolver %r, which isn't registered", metric_id, key)
                continue
        label = f"spec:{source_spec.get('source')}.{source_spec.get('dataset')}" if source_spec else key
        group = {"key": group_key, "sisu_group_id": sisu_group_id,   # context for per-team resolvers/specs
                 "lo_email": lo_email}
        for ws, we in week_windows:
            async with session_factory() as s:
                try:
                    val = (await run_spec(source_spec, s, tenant_id, business_id, ws, we, group=group)
                           if source_spec else
                           await fn(s, tenant_id, business_id, ws, we, group=group))
                    await _write(s, tenant_id, metric_id, ws, val, label, fill_only=fill_only)
                    await s.commit()
                    written += 1
                except Exception:                       # isolated: this value is left untouched
                    await s.rollback()
                    log.exception("scorecard: resolver %s failed for metric %s week %s", label, metric_id, ws)
    return written


# Registers the lending board's and the Portfolio board's resolvers. Last, because those modules import
# the decorator above.
from . import scorecard_sympli  # noqa: E402
from . import scorecard_portfolio  # noqa: E402,F401
