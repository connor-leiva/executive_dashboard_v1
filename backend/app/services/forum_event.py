"""The Forum's quarterly VIP event. FORUM-EVENT-SPEC.md §7.

The Forum sells seats to its in-person events as $2,500 VIP guest tickets and upsells those
guests into yearly memberships. This computes both halves: are we filling the room, and are the
guests converting.

TWO POPULATIONS ATTEND, and conflating them is the bug this replaces. VIP guests (prospects, a
sales funnel, money attached) carry one tag set; members who have registered carry another. The
Forum derives the second by subtracting the first from an undifferentiated pool, which is why
its two "Registered" numbers disagree. Here each is counted from its own tags and neither is
ever derived from the other.

THE TAG IS THE REGISTRATION RECORD; THE FUNNEL STAGE IS THE SALES STATE. `guests` counts tags.
Measured against Spring's live Q4 data, the tag says 24 and the stage says 18: five of those
people have no opportunity in the funnel at all, and one RSVP'd after being marked unresponsive.
Counting stages loses six people and looks right doing it — and the five with no sale open are
exactly who a tab like this exists to surface. So the hero deliberately disagrees with the
funnel row beneath it, both are drillable, and the gap is reported rather than hidden.

EVERY MONEY FIGURE IS OPTIONAL. Pricing ships unset; `vip_price` and `price_map` are nullable
with no seeded defaults. No count metric depends on a price, and an unpriced revenue figure is
None — rendered as a dash, never $0, because zero reads as a result and a dash reads as an
absence.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ForumEvent, ForumEventGuest, ForumEventWeekly
from .launch import curve_expected, days_between

# The vocabulary, declared ONCE. The launch equivalent is spread across five places that have
# to change together — GROUPS, DEFAULT_STAGE_MAP's keys, _GROUP_ORDER, the frontend's
# STAGE_GROUPS and the settings drawer's merge — so the frontend reads this from the payload
# instead of re-declaring it.
GROUPS = ("leads", "optin", "registered", "attending", "deciding",
          "committed", "converted", "nurture", "lost", "uncategorized")

# Group → ordered display labels and the function that owns the stage. Shipped as data so the
# tab renders the funnel without knowing the vocabulary.
GROUP_LABELS = {
    "leads": ("Leads", "marketing"),
    "optin": ("Opt-in", "marketing"),
    "registered": ("VIP guests", "setters"),
    "attending": ("Attending", "setters"),
    "deciding": ("Contract sent", "closers"),
    "committed": ("Payment received", "payment ops"),
    "converted": ("Members", "onboarding"),
    "nurture": ("Nurture", "marketing"),
    "lost": ("Lost", None),
}
# The funnel shown on the tab, in order. `nurture`, `lost` and `uncategorized` are counted but
# are not steps on the way to a sale, so they are reported beside it rather than inside it.
FUNNEL_ORDER = ("optin", "registered", "attending", "deciding", "committed", "converted")

DEFAULT_STAGE_MAP = {
    "leads": ["leads:"],
    "optin": ["vip page: opt-in", "opt-in", "opt in"],
    "registered": ["vip guest: confirmed", "ticket purchased", "purchased"],
    "attending": ["hot nurture: current vip event", "attended"],
    "deciding": ["sent contract"],
    "committed": ["payment received"],
    "converted": ["won: onboarded", "onboarded", "completed investment"],
    "nurture": ["nurture: upcoming", "invite to next", "previously attended"],
    "lost": ["lost", "dq", "abandon", "no deposit"],
}

# Precedence. A won opportunity must resolve as `converted` even though its stage text also
# matches `nurture`, and "Hot Nurture: Current VIP Event" must resolve as `attending` rather
# than `nurture` — the longest-standing class of bug in stage mapping is a substring matching
# two groups and the wrong one winning.
GROUP_ORDER = ("converted", "committed", "deciding", "attending", "registered",
               "optin", "nurture", "lost", "leads")

# Once confirmed, always a guest: a VIP who converts still counts toward the room, so the
# registration number does not fall when a sale succeeds.
_GUEST_GROUPS = ("registered", "attending", "deciding", "committed", "converted")
# A tag that says "RSVP'd" while the stage says the sale is dead or parked. Reported, not hidden.
_CONFLICT_GROUPS = ("lost", "nurture")


def classify_stage(stage_name: str, stage_map: dict | None) -> str:
    """Raw GHL stage text → group, through the tenant's map. No stage literal reaches the sync."""
    low = (stage_name or "").lower()
    smap = stage_map or DEFAULT_STAGE_MAP
    for g in GROUP_ORDER:
        if any(sub in low for sub in (smap.get(g) or [])):
            return g
    return "uncategorized"


def match_guest_tag(tags, guest_tags) -> bool:
    """Guests match as SUBSTRINGS, so `…guest rsvp` also catches `…guest rsvp paid`."""
    lows = [str(t).strip().lower() for t in (tags or [])]
    return any(g.strip().lower() in t for g in (guest_tags or []) for t in lows)


def match_member_tag(tags, member_tags) -> bool:
    """Members match EXACTLY. `the forum q4 2026 rsvp` is not a substring of
    `the forum q4 2026 guest rsvp`, so this is safe in both directions — but matching members by
    substring would be one careless tag away from counting every guest as a member."""
    lows = {str(t).strip().lower() for t in (tags or [])}
    return any(m.strip().lower() in lows for m in (member_tags or []))


def is_comped(tags, comp_match: str | None) -> bool:
    needle = (comp_match or "comp").strip().lower()
    return any(needle in str(t).strip().lower() for t in (tags or []))


def _f(x) -> float:
    return float(x or 0)


async def active_event(s: AsyncSession, tenant_id, business_id,
                       today: dt.date | None = None) -> ForumEvent | None:
    """The event the tab shows: the one whose selling window contains today, else the nearest
    upcoming, else the most recent. Unlike `active_launch_for` this is scoped to a business AND
    cannot be confused with anything else, because nothing but the Event tab reads it."""
    today = today or dt.date.today()
    rows = (await s.execute(select(ForumEvent).where(
        ForumEvent.tenant_id == tenant_id,
        ForumEvent.business_id == business_id,
        ForumEvent.is_active.is_(True)))).scalars().all()
    if not rows:
        return None

    def rank(e: ForumEvent):
        ws, we = e.window_start, e.window_end
        if ws and we and ws <= today <= we:
            return (0, abs((e.starts_on - today).days) if e.starts_on else 0)
        if e.starts_on and e.starts_on >= today:
            return (1, (e.starts_on - today).days)
        return (2, -(today - e.starts_on).days if e.starts_on else 0)

    return sorted(rows, key=rank)[0]


def _registration(ev: ForumEvent, guests: list[ForumEventGuest], today: dt.date) -> dict:
    """Guests against the goal, and where the pace curve says they should be."""
    n = len(guests)
    goal = int(ev.guest_goal or 0)
    dte = days_between(today, ev.starts_on) if ev.starts_on else None
    exp_pct = curve_expected(ev.pace_curve or {}, dte) if ev.pace_curve else None
    expected = round(exp_pct * goal) if (exp_pct is not None and goal) else None
    gap = (n - expected) if expected is not None else None
    tol = _f(ev.pace_tolerance or 0.08) * goal
    if dte is None:
        state = "pending"
    elif dte < 0:
        state = "done"
    elif gap is None:
        state = "pending"            # dated, but nobody has configured a curve yet
    elif gap < -tol:
        state = "behind"
    elif gap > tol:
        state = "ahead"
    else:
        state = "onpace"
    ch = Counter(g.channel or "Organic / Existing" for g in guests)
    return {
        "goal": goal or None,
        "guests": n,
        "paid": sum(1 for g in guests if not g.is_comped),
        "comped": sum(1 for g in guests if g.is_comped),
        "pct_to_goal": round(n / goal, 4) if goal else None,
        "days_to_event": dte,
        "expected": expected,
        "expected_pct": round(exp_pct, 4) if exp_pct is not None else None,
        "gap": gap,
        "state": state,
        "curve": [{"d": d, "pct": round(v, 4), "count": round(v * goal)}
                  for d, v in sorted((int(k), float(x)) for k, x in (ev.pace_curve or {}).items())]
                 if (ev.pace_curve and goal) else [],
        "channels": [{"label": k, "count": v} for k, v in ch.most_common()] if guests else [],
    }


def _revenue(ev: ForumEvent, guests: list[ForumEventGuest], converted: list[ForumEventGuest],
             blended: float | None) -> dict:
    """Ticket money and membership money, kept apart. Ticket revenue is NOT membership revenue —
    `classify_stream("The Forum VIP Guest Ticket") == "event_tickets"` is already tested, and
    summing them would inflate the Forum's MRR with one-off ticket sales."""
    paid = sum(1 for g in guests if not g.is_comped)
    ticket = (paid * _f(ev.vip_price)) if ev.vip_price is not None else None
    pm = ev.price_map or {}
    arr, unpriced = None, 0
    if pm:
        arr = 0.0
        for g in converted:
            acv = (pm.get(g.payment_type) or {}).get("acv") if g.payment_type else None
            if acv is None:
                unpriced += 1
            else:
                arr += float(acv)
        # A converted member we cannot price is still a member. Charging them the blended rate
        # keeps the money consistent with the headcount instead of quietly pricing some of it.
        if unpriced and blended:
            arr += unpriced * blended
    return {
        "ticket_booked": round(ticket, 2) if ticket is not None else None,
        "member_arr": round(arr, 2) if arr is not None else None,
        "member_goal": ev.member_goal,
        "members": len(converted),
        "unpriced_members": unpriced,
    }


async def compute_event(s: AsyncSession, tenant_id, event: ForumEvent,
                        today: dt.date | None = None) -> dict:
    """The whole Event payload. `today` is injectable because every pace figure depends on it
    and a test that cannot move the clock cannot prove a curve."""
    today = today or dt.date.today()
    rows = (await s.execute(select(ForumEventGuest).where(
        ForumEventGuest.tenant_id == tenant_id,
        ForumEventGuest.event_id == event.id))).scalars().all()

    guests = [r for r in rows if r.kind == "guest"]
    members = [r for r in rows if r.kind == "member"]
    converted = [g for g in guests if g.group == "converted"]

    # Blended from what we CAN price, so an unpriced member is charged the going rate rather
    # than nothing. None when nothing is priced at all.
    pm = event.price_map or {}
    priced = [float((pm.get(g.payment_type) or {}).get("acv"))
              for g in converted
              if g.payment_type and (pm.get(g.payment_type) or {}).get("acv") is not None]
    blended = (sum(priced) / len(priced)) if priced else None

    by_group = Counter(g.group for g in guests)
    funnel = [{"key": k, "label": GROUP_LABELS[k][0], "owner": GROUP_LABELS[k][1],
               "count": by_group.get(k, 0)} for k in FUNNEL_ORDER]

    without_opp = [g for g in guests if not g.opportunity_id]
    conflict = [g for g in guests if g.group in _CONFLICT_GROUPS]
    both = [g for g in guests if g.kind == "guest" and g.contact_id in {m.contact_id for m in members}]

    warnings = []
    if without_opp:
        warnings.append(f"{len(without_opp)} guest{'' if len(without_opp) == 1 else 's'} "
                        f"with no opportunity in the funnel")
    if conflict:
        warnings.append(f"{len(conflict)} guest{'' if len(conflict) == 1 else 's'} "
                        f"tagged as RSVP'd but parked or lost in the funnel")
    if by_group.get("uncategorized"):
        warnings.append(f"{by_group['uncategorized']} unmapped stage"
                        f"{'' if by_group['uncategorized'] == 1 else 's'}")
    rev = _revenue(event, guests, converted, blended)
    if rev["unpriced_members"]:
        warnings.append(f"{rev['unpriced_members']} member"
                        f"{'' if rev['unpriced_members'] == 1 else 's'} priced at blended")

    weeks = (await s.execute(select(ForumEventWeekly).where(
        ForumEventWeekly.event_id == event.id)
        .order_by(ForumEventWeekly.week_start.desc()).limit(2))).scalars().all()
    momentum = {
        "guests": {"now": weeks[0].guests if weeks else 0,
                   "was": weeks[1].guests if len(weeks) > 1 else None},
        "converted": {"now": weeks[0].converted if weeks else 0,
                      "was": weeks[1].converted if len(weeks) > 1 else None},
    }

    return {
        "event": config_out(event),
        "registration": {
            **_registration(event, guests, today),
            "members_registered": len(members),
            "members_declined": None,       # set by the sync when declined_tags is configured
            # The only figure allowed to add the two populations. A contact carrying both tags
            # is written once as a guest, so this cannot double-count.
            "room": len(guests) + len(members),
            "guests_without_opp": len(without_opp),
            "guests_stage_conflict": len(conflict),
            "both_tags": len(both),
        },
        "funnel": funnel,
        "aside": [{"key": k, "label": GROUP_LABELS.get(k, (k.title(), None))[0],
                   "count": by_group.get(k, 0)}
                  for k in ("nurture", "lost") if by_group.get(k)],
        "revenue": {**rev,
                    "conversion": {"guests": len(guests), "converted": len(converted),
                                   # None, never 0, on an empty denominator - a dash reads as
                                   # "no data", a zero reads as "nobody converted".
                                   "rate": round(len(converted) / len(guests), 4) if guests else None}},
        "momentum": momentum,
        "groups": list(GROUPS),
        "warnings": warnings,
        "as_of": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def config_out(ev: ForumEvent) -> dict:
    """The editable config, serialised ONCE and reused by every route that echoes it. Every
    field here must also exist on the upsert schema and in the settings drawer — a field that is
    accepted but not emitted is one a future editor silently blanks."""
    return {
        "id": str(ev.id),
        "name": ev.name,
        "slug": ev.slug,
        "status": ev.status,
        "starts_on": ev.starts_on.isoformat() if ev.starts_on else None,
        "ends_on": ev.ends_on.isoformat() if ev.ends_on else None,
        "window_start": ev.window_start.isoformat() if ev.window_start else None,
        "window_end": ev.window_end.isoformat() if ev.window_end else None,
        "venue": ev.venue,
        "default_tz": ev.default_tz,
        "pipeline_match": ev.pipeline_match or [],
        "stage_map": ev.stage_map or DEFAULT_STAGE_MAP,
        "guest_tags": ev.guest_tags or [],
        "member_tags": ev.member_tags or [],
        "declined_tags": ev.declined_tags or [],
        "comp_tag_match": ev.comp_tag_match,
        "guest_goal": ev.guest_goal,
        "member_goal": ev.member_goal,
        "vip_price": float(ev.vip_price) if ev.vip_price is not None else None,
        "price_map": ev.price_map or {},
        "pace_curve": ev.pace_curve or {},
        "pace_tolerance": float(ev.pace_tolerance) if ev.pace_tolerance is not None else None,
        "is_active": ev.is_active,
    }
