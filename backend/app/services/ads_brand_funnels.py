"""Forum VIP and Edge ladders beside the Shift chain.

The Shift ladder is an ad cohort. These two are the people already in the CRM and in Stripe
for the same dates. A source that has never been written is missing, not zero: a zero would
read as a leak in a step nobody records.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Integration, MetricRecord
from .ads import conversion, cost_per
from .ads_rules import is_forum_group
from .scorecard_resolvers import vip_channel
from .sync import is_confirmed_seat

_ATTENDED = frozenset({"attended", "showed", "held"})


def _person(row: MetricRecord, detail: str | None) -> dict:
    return {
        "name": row.name,
        "email": row.email,
        "reached": row.occurred_on.isoformat() if row.occurred_on else "undated",
        "detail": detail or "",
    }


def _dated(row: MetricRecord, start: dt.date, end: dt.date) -> bool | None:
    """True in the window, False outside it, None when the row has no date."""
    if row.occurred_on is None:
        return None
    return start <= row.occurred_on <= end


def assemble(title: str, spend: float, note: str, steps: list[dict]) -> dict:
    """steps: key, label, zone, n (None = not recorded), people, diagnostic, note."""
    rungs = []
    prev = None
    for step in steps:
        n = step.get("n")
        missing = n is None
        people = step.get("people") or []
        rungs.append({
            "key": step["key"],
            "label": step["label"],
            "zone": step["zone"],
            "diagnostic": bool(step.get("diagnostic")),
            "missing": missing,
            "note": step.get("note"),
            "n": None if missing else int(n),
            "people": people,
            "prev": prev,
            "conversion": None if missing or prev is None else conversion(n, prev),
            "cost_per": None if missing or not n else cost_per(spend, n),
            "dated": step.get("dated", 0 if missing else int(n)),
            "undated": step.get("undated", 0),
        })
        if not missing:
            prev = int(n)
    return {"title": title, "spend": spend, "note": note, "rungs": rungs}


def _window_people(rows, start, end, detail_of) -> tuple[list[dict], int, int, bool]:
    """People in the window. Returns people, dated, undated, and whether the source exists.

    Undated rows are the current snapshot. They count, and they are marked undated, because
    dropping them would show a recorded list as zero.
    """
    if not rows:
        return [], 0, 0, False
    people, dated, undated = [], 0, 0
    seen = set()
    for row in rows:
        flag = _dated(row, start, end)
        if flag is False:
            continue
        key = row.external_id or row.name
        if key in seen:
            continue
        seen.add(key)
        people.append(_person(row, detail_of(row)))
        if flag is None:
            undated += 1
        else:
            dated += 1
    return people, dated, undated, True


async def _kind(s: AsyncSession, tenant_id, business_id, kind: str, source: str | None = None):
    q = select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id,
        MetricRecord.business_id == business_id,
        MetricRecord.kind == kind)
    if source:
        q = q.where(MetricRecord.source == source)
    return list((await s.execute(q)).scalars())


def _step(key, label, zone, people, dated, undated, exists, *, diagnostic=False, empty_note=None):
    if not exists:
        return {"key": key, "label": label, "zone": zone, "n": None, "diagnostic": diagnostic,
                "note": empty_note or "Not recorded.", "people": []}
    return {"key": key, "label": label, "zone": zone, "n": len(people), "people": people,
            "dated": dated, "undated": undated, "diagnostic": diagnostic}


async def build_brand_funnels(s: AsyncSession, tenant_id, business_id,
                              start: dt.date, end: dt.date,
                              forum_ads: dict, edge_ads: dict) -> dict:
    """Two ladders. Meta rungs are the brand's own spend. Later rungs are named people."""
    forum_events = await _kind(s, tenant_id, business_id, "forum_stage_event", "ghl") if business_id else []
    qualified_rows = []
    seat_rows = []
    for row in forum_events:
        flag = _dated(row, start, end)
        if flag is False:
            continue
        meta = row.meta or {}
        if row.segment == "vip_guest" and vip_channel(meta) == "spring":
            qualified_rows.append(row)
        elif row.segment == "vip_guest_seat" and is_confirmed_seat(meta.get("to_stage"), meta.get("stage_id")):
            seat_rows.append(row)
    # Dedup a guest who entered twice.
    def _dedup(rows):
        seen, out = set(), []
        for row in rows:
            key = (row.meta or {}).get("opp_id") or row.external_id
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out
    qualified_rows, seat_rows = _dedup(qualified_rows), _dedup(seat_rows)
    events_exist = bool(forum_events)
    q_people = [_person(r, (r.meta or {}).get("opp_source") or "VIP page") for r in qualified_rows]
    s_people = [_person(r, (r.meta or {}).get("to_stage") or "Confirmed") for r in seat_rows]

    members = await _kind(s, tenant_id, business_id, "onboarded", "ghl") if business_id else []
    m_people, m_dated, m_undated, m_exists = _window_people(
        members, start, end, lambda r: (r.meta or {}).get("stage") or "Onboarded")

    forum = assemble(
        "Forum VIP",
        float(forum_ads.get("spend") or 0),
        "Qualified guests are Spring's VIP page and guest application. A seat is Confirmed "
        "or Dallas VIP guest confirmed. Cost per seat and cost per member use Forum spend only.",
        [
            {"key": "click", "label": "Link clicks", "zone": "meta",
             "n": int(forum_ads.get("clicks") or 0), "people": []},
            {"key": "lead", "label": "Leads · Meta", "zone": "meta", "diagnostic": True,
             "n": int(forum_ads.get("leads") or 0), "people": []},
            _step("qualified", "Qualified guest lead", "axcion", q_people,
                  len(q_people), 0, events_exist,
                  empty_note="Forum stage events are not synced, so a qualified lead cannot be counted."),
            _step("seat", "VIP guest seat", "axcion", s_people,
                  len(s_people), 0, events_exist,
                  empty_note="Forum stage events are not synced, so a seat cannot be counted."),
            _step("member", "Forum member", "axcion", m_people, m_dated, m_undated, m_exists,
                  empty_note="No onboarded stage is synced, so a new Forum member cannot be dated."),
        ],
    )

    regs = await _kind(s, tenant_id, business_id, "edge_registration", "ghl") if business_id else []
    reg_people, reg_dated, reg_undated, reg_exists = _window_people(
        regs, start, end, lambda r: "Webinar registration")
    attended_rows = [r for r in regs if str(r.status or "").strip().lower() in _ATTENDED
                     or (r.meta or {}).get("attended")]
    if attended_rows:
        att_people, att_dated, att_undated, att_exists = _window_people(
            attended_rows, start, end, lambda r: r.status or "Attended")
    else:
        att_people, att_dated, att_undated, att_exists = [], 0, 0, False

    subs = await _kind(s, tenant_id, business_id, "edge_subscription", "stripe_legacy") if business_id else []
    purchases = []
    for row in subs:
        meta = row.meta or {}
        raw = meta.get("start_date")
        start_on = None
        if raw:
            try:
                start_on = dt.date.fromisoformat(str(raw)[:10])
            except ValueError:
                start_on = None
        if start_on is None or not (start <= start_on <= end):
            continue
        if row.amount is not None and float(row.amount) <= 0:
            continue
        if "comp" in str(meta.get("plan_name") or "").lower():
            continue
        if (row.status or "") not in ("active", "trialing", "past_due"):
            continue
        purchases.append(_person(row, meta.get("plan_name") or "Edge"))
    stripe_live = False
    if business_id:
        stripe_live = await s.scalar(select(Integration.id).where(
            Integration.tenant_id == tenant_id, Integration.provider == "stripe_legacy",
            Integration.status == "connected").limit(1)) is not None
    buy_exists = bool(subs) or stripe_live

    edge = assemble(
        "Edge webinar",
        float(edge_ads.get("spend") or 0),
        "Registration is the Edge event list. Attendance is shown only when a registration "
        "is marked attended. A purchase is a paid Edge subscription that started in this window.",
        [
            {"key": "click", "label": "Link clicks", "zone": "meta",
             "n": int(edge_ads.get("clicks") or 0), "people": []},
            {"key": "lead", "label": "Leads · Meta", "zone": "meta", "diagnostic": True,
             "n": int(edge_ads.get("leads") or 0), "people": []},
            _step("registered", "Webinar registration", "axcion", reg_people,
                  reg_dated, reg_undated, reg_exists,
                  empty_note="No Edge registration list is synced."),
            _step("attended", "Attended", "axcion", att_people, att_dated, att_undated, att_exists,
                  empty_note="Attendance is not on the Edge registration, so this rung is blank."),
            _step("purchase", "Edge purchase", "axcion", purchases,
                  len(purchases), 0, buy_exists,
                  empty_note="Edge subscriptions are not synced."),
        ],
    )
    return {"forum": forum, "edge": edge}


def ad_bucket(campaigns: list[dict], *, forum: bool) -> dict:
    rows = [c for c in campaigns if (is_forum_group(c.get("group")) if forum
                                     else c.get("group") == "Edge Webinar")]
    return {
        "spend": sum(c.get("spend") or 0 for c in rows),
        "clicks": sum(c.get("link_clicks") or 0 for c in rows),
        "leads": sum(c.get("leads") or 0 for c in rows),
    }


def seats_by_ad(seat_rows: list, ad_external_ids: set[str]) -> dict | None:
    """Seats whose utm_content is an ad id. None when no seat carries utm_content at all."""
    tagged = [r for r in seat_rows if str((r.meta or {}).get("utm_content") or "").strip()]
    if not tagged:
        return None
    counts = {ext: 0 for ext in ad_external_ids}
    for row in tagged:
        content = str((row.meta or {}).get("utm_content") or "").strip()
        if content in counts:
            counts[content] += 1
    return counts
