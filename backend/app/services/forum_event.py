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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ForumEvent, ForumEventGuest, ForumEventWeekly, MetricRecord
from .launch import classify_shift_source, curve_expected, days_between

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
    declined = [r for r in rows if r.kind == "declined"]
    converted = [g for g in guests if g.group == "converted"]

    # The denominator for "how much of the membership is coming". Counted the SAME way the
    # Forum tab counts its own member KPI - kind='member', status='active', scoped to this
    # business (metrics.py:265) - because two different member totals on one screen is the
    # exact failure this build has been avoiding since F11.
    roster_rows = (await s.execute(
        select(MetricRecord).where(
            MetricRecord.tenant_id == tenant_id,
            MetricRecord.business_id == event.business_id,
            MetricRecord.source == "ghl",
            MetricRecord.kind == "member",
            MetricRecord.status == "active"))).scalars().all()
    roster = len(roster_rows)
    # THE CHASE LIST. A member who has neither registered nor declined has not been asked, or
    # was asked and never answered - and until this existed the only way to see them was to
    # subtract two numbers on the tile and wonder. metric_record.external_id IS the contact id
    # (forum.py:103), which is what makes these people nameable rather than merely countable.
    answered = {g.contact_id for g in rows if g.kind in ("member", "declined")}
    unanswered = [m for m in roster_rows if m.external_id not in answered]

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
    # NO both_tags here. A contact carrying both tag sets is written ONCE, as a guest, so it can
    # never also appear as a member row - any count derived from the stored rows is structurally
    # zero and would report a lie. The sync knows the overlap because it sees the tags; it logs
    # the number. Putting it on the tab needs a column, not a derivation.

    warnings = []
    if without_opp:
        warnings.append(f"{len(without_opp)} guest{'' if len(without_opp) == 1 else 's'} "
                        f"with no opportunity in the funnel")
    if conflict:
        warnings.append(f"{len(conflict)} guest{'' if len(conflict) == 1 else 's'} "
                        f"tagged as RSVP'd but parked or lost in the funnel")
    # Only a row that HAS a stage can have an unmapped one. A guest with no opportunity is
    # already reported above; counting them here too warned about the same five people twice.
    unmapped = sum(1 for g in guests if g.group == "uncategorized" and g.stage)
    if unmapped:
        warnings.append(f"{unmapped} unmapped stage{'' if unmapped == 1 else 's'}")
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
            # Counted, not None. The sync has been storing these rows all along (kind =
            # "declined"); this field was hardcoded to None, so a tenant who HAD configured the
            # not-attending tag saw the setting take and the figure never appear.
            "members_declined": len(declined) if event.declined_tags else None,
            # Shares of the membership. None rather than 0 on an empty roster - a dash reads as
            # "we do not know how many members there are", a zero reads as "none of them".
            "members_total": roster or None,
            "members_unanswered": len(unanswered) if roster else None,
            "members_unanswered_pct": round(len(unanswered) / roster, 4) if roster else None,
            "members_registered_pct": round(len(members) / roster, 4) if roster else None,
            "members_declined_pct": (round(len(declined) / roster, 4)
                                     if roster and event.declined_tags else None),
            # The only figure allowed to add the two populations. A contact carrying both tags
            # is written once as a guest, so this cannot double-count.
            "room": len(guests) + len(members),
            "guests_without_opp": len(without_opp),
            "guests_stage_conflict": len(conflict),
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


# ── sync (FORUM-EVENT-SPEC.md Phase 3) ──────────────────────────────────────────────────────
# Opportunity custom fields, resolved BY NAME so no field id is hardcoded. Spring's Forum
# location calls them "Sales Rep" and "Referred By"; another workspace will call them something
# else and configure it, which is the point.
_OPP_FIELDS = {
    "sales_rep": ("sales rep",),
    "referred_by": ("referred by", "invited by"),
}
# Contact field naming the member who brought this guest. Spring's is "Guest Invited By".
_CONTACT_FIELDS = {
    "invited_by": ("guest invited by", "invited by", "referred by"),
}
# The Forum encodes the payment type IN THE STAGE - "Sent Contract: Dual - Monthly" - rather
# than in a Payment Type field the way beCollective does. So the price_map is keyed by what
# comes after the colon, and no inference is needed.
_CONTRACT_PREFIX = "sent contract:"


def field_ids(fields: list[dict], wanted: dict) -> dict:
    """{semantic: field_id} from GHL's custom-field list, matched on NAME."""
    out: dict = {}
    for f in fields or []:
        nm = (f.get("name") or "").strip().lower()
        for sem, names in wanted.items():
            if sem in out:
                continue
            if nm in names or any(n in nm for n in names):
                out[sem] = f.get("id")
    return out


def payment_type_from_stage(stage: str | None) -> str | None:
    """"Sent Contract: Dual - Monthly" -> "Dual - Monthly". None for every other stage.

    A converted member whose contract stage we never saw has no payment type, which is correct
    rather than a gap: compute_event prices them at the blended rate and says how many.
    """
    low = (stage or "").strip().lower()
    if not low.startswith(_CONTRACT_PREFIX):
        return None
    tail = (stage or "").strip()[len(_CONTRACT_PREFIX):].strip()
    return tail or None


def _iso_week_start(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


async def sync_forum_event(s: AsyncSession, tenant_id, event: ForumEvent, token: str,
                           location_id: str, today: dt.date | None = None) -> dict:
    """Pull one event's guests and registered members from GHL and UPSERT them.

    Upsert, never snapshot-delete. `_metric_snapshot` clears by (tenant, business, source, kind)
    before inserting, which is why the Forum can only hold one event at a time - syncing Q4
    destroys Q3's registrations. Nothing here deletes a row it did not match.

    Read-only against GHL: contacts, pipelines, opportunities, custom-field names. No writes.
    """
    from ..integrations import ghl

    today = today or dt.date.today()
    guest_tags = event.guest_tags or []
    member_tags = event.member_tags or []
    declined_tags = event.declined_tags or []
    if not (guest_tags or member_tags):
        return {"skipped": "no tags configured"}

    contacts = await ghl.get_contacts(token, location_id)
    pipelines = await ghl.get_pipelines(token, location_id)
    match = [m.strip().lower() for m in (event.pipeline_match or []) if str(m).strip()]
    pipe_name = {p["id"]: (p.get("name") or "") for p in pipelines}
    stage_name = {st["id"]: (st.get("name") or "")
                  for p in pipelines for st in (p.get("stages") or [])}
    opps = await ghl.get_opportunities(token, location_id)
    if match:
        opps = [o for o in opps
                if any(m in (pipe_name.get(o.get("pipelineId")) or "").lower() for m in match)]
    # One opportunity per contact: the most recently updated, because a contact who was in the
    # funnel last quarter and is back this quarter should read as where they are NOW.
    by_contact: dict = {}
    for o in opps:
        cid = str(o.get("contactId") or "")
        if not cid:
            continue
        cur = by_contact.get(cid)
        if cur is None or str(o.get("updatedAt") or "") > str(cur.get("updatedAt") or ""):
            by_contact[cid] = o

    ofields = field_ids(await ghl.get_custom_fields(token, location_id, model="opportunity"),
                        _OPP_FIELDS)
    cfields = field_ids(await ghl.get_custom_fields(token, location_id, model="contact"),
                        _CONTACT_FIELDS)

    existing = {g.contact_id: g for g in (await s.execute(select(ForumEventGuest).where(
        ForumEventGuest.event_id == event.id))).scalars()}

    seen, stat = set(), Counter()
    for c in contacts:
        cid = str(c.get("id") or "")
        if not cid:
            continue
        tags = ghl.contact_tags(c)
        # Precedence: a contact carrying both tag sets is a GUEST, counted once. The sales
        # population is the one with money attached, and a member being sold a guest seat is a
        # sale. Spring has exactly one of these in Q4.
        if match_guest_tag(tags, guest_tags):
            if match_member_tag(tags, member_tags):
                stat["both_tags"] += 1      # counted once, as a guest; logged so it is visible
            kind = "guest"
        elif match_member_tag(tags, member_tags):
            kind = "member"
        elif declined_tags and match_member_tag(tags, declined_tags):
            kind = "declined"
        else:
            continue
        seen.add(cid)
        stat[kind] += 1

        o = by_contact.get(cid)
        stage = stage_name.get(o.get("pipelineStageId")) if o else None
        cvals = ghl.contact_custom_values(c) if hasattr(ghl, "contact_custom_values") else {}
        ovals = ghl.opp_custom_values(o) if o else {}
        row = existing.get(cid)
        if row is None:
            row = ForumEventGuest(tenant_id=tenant_id, event_id=event.id, contact_id=cid)
            s.add(row)
            existing[cid] = row
            stat["new"] += 1
        row.kind = kind
        row.name = (ghl.contact_name(c) or None)
        row.opportunity_id = str(o.get("id")) if o else None
        row.stage = (stage or None)
        row.group = classify_stage(stage, event.stage_map) if stage else "uncategorized"
        row.is_comped = is_comped(tags, event.comp_tag_match)
        row.channel = classify_shift_source({}, row.is_comped)
        row.invited_by = (cvals.get(cfields.get("invited_by"))
                          or ovals.get(ofields.get("referred_by")) or None)
        row.rep_email = ovals.get(ofields.get("sales_rep")) or None
        row.payment_type = payment_type_from_stage(stage)
        # dateAdded is when the IDENTITY first appeared, not when they RSVP'd - GHL exposes no
        # per-tag timestamp. Same deliberate trade bc_shift_reg makes, documented in the spec.
        added = c.get("dateAdded") or c.get("createdAt")
        if added and not row.registered_on:
            try:
                row.registered_on = dt.datetime.fromisoformat(
                    str(added).replace("Z", "+00:00")).date()
            except (ValueError, TypeError):
                pass
        if row.group == "converted" and not row.converted_on:
            row.converted_on = today
        row.last_seen_at = dt.datetime.now(dt.timezone.utc)

    # A person who lost the tag is no longer registered for THIS event. Removed from this
    # event only - never a blanket delete, which is the whole difference from the snapshot.
    for cid, row in list(existing.items()):
        if cid not in seen:
            await s.delete(row)
            stat["untagged"] += 1

    await s.flush()
    await _upsert_week(s, tenant_id, event, today)
    await s.commit()
    return {"guests": stat["guest"], "members": stat["member"], "declined": stat["declined"],
            "new": stat["new"], "untagged": stat["untagged"], "both_tags": stat["both_tags"]}


async def _upsert_week(s: AsyncSession, tenant_id, event: ForumEvent, today: dt.date) -> None:
    """One row per ISO week per event. metric_record cannot hold this - it is current state by
    construction, and momentum is a question about last week."""
    rows = (await s.execute(select(ForumEventGuest).where(
        ForumEventGuest.event_id == event.id))).scalars().all()
    guests = [r for r in rows if r.kind == "guest"]
    wk = _iso_week_start(today)
    week = (await s.execute(select(ForumEventWeekly).where(
        ForumEventWeekly.event_id == event.id,
        ForumEventWeekly.week_start == wk))).scalar_one_or_none()
    if week is None:
        week = ForumEventWeekly(tenant_id=tenant_id, event_id=event.id, week_start=wk)
        s.add(week)
    week.guests = len(guests)
    week.members_registered = sum(1 for r in rows if r.kind == "member")
    week.converted = sum(1 for g in guests if g.group == "converted")
    week.guests_cum = len(guests)
    week.captured_at = dt.datetime.now(dt.timezone.utc)


async def sync_events_for_integration(s: AsyncSession, tenant_id, integ) -> int:
    """Every active event on this integration's business. Inert when none is configured, so a
    workspace that has never made an event pays nothing for this.

    Isolated per event: one event's bad tag set must not stop the next one syncing, and none of
    them may break the Forum sync they hang off.
    """
    from ..security import dec

    cfg = integ.config or {}
    location_id = cfg.get("location_id")
    token = dec(integ.access_token_enc) if integ.access_token_enc else None
    if not (location_id and token):
        return 0
    events = (await s.execute(select(ForumEvent).where(
        ForumEvent.tenant_id == tenant_id,
        ForumEvent.business_id == integ.business_id,
        ForumEvent.is_active.is_(True)))).scalars().all()
    n = 0
    for ev in events:
        try:
            stat = await sync_forum_event(s, tenant_id, ev, token, location_id)
            n += (stat.get("guests") or 0) + (stat.get("members") or 0)
            print(f"[forum_event] {ev.slug}: {stat}", flush=True)
        except Exception as e:  # noqa: BLE001 - never let one event break the Forum sync
            print(f"[forum_event] {ev.slug} skipped: {type(e).__name__}: {e}", flush=True)
    return n


# ── drill (FORUM-EVENT-SPEC.md §5) ──────────────────────────────────────────────────────────
# Every figure on the tab opens the people behind it. A number nobody can open is a number
# nobody can act on - and the five RSVPs with no sale open are the whole reason this exists.
_DRILL_TITLES = {
    "event_guests": "VIP guests",
    "event_paid": "VIP guests who paid",
    "event_comped": "Comped guests",
    "event_members_registered": "Members registered",
    "event_declined": "Members not attending",
    "event_room": "In the room",
    "event_converted": "Guests who became members",
    "event_without_opp": "RSVPs with no sale open",
    "event_stage_conflict": "RSVP'd, but parked or lost in the funnel",
    "event_unanswered": "Members who have not answered",
}


def _row(g: ForumEventGuest) -> dict:
    return {
        "name": g.name or "—",
        "kind": g.kind,
        "stage": g.stage or "—",
        "group": g.group,
        "comped": g.is_comped,
        "rep": g.rep_email or "—",
        "invited_by": g.invited_by or "—",
        "payment": g.payment_type or "—",
    }


_COLUMNS = ["name", "kind", "stage", "group", "comped", "rep", "invited_by", "payment"]


async def drill_event(s: AsyncSession, tenant_id, event: ForumEvent, metric: str) -> dict:
    """The people behind one figure. Two shapes only - `records` and `calc` - so the existing
    drawer renders it with no new component."""
    rows = (await s.execute(select(ForumEventGuest).where(
        ForumEventGuest.tenant_id == tenant_id,
        ForumEventGuest.event_id == event.id))).scalars().all()
    guests = [r for r in rows if r.kind == "guest"]

    def records(title, subset):
        return {"metric": metric, "type": "records", "title": title,
                "subtitle": f"{len(subset)} | {event.name}",
                "count": len(subset), "columns": _COLUMNS,
                "rows": [_row(g) for g in sorted(subset, key=lambda x: (x.name or "").lower())]}

    title = _DRILL_TITLES.get(metric)
    if metric == "event_guests":
        return records(title, guests)
    if metric == "event_paid":
        return records(title, [g for g in guests if not g.is_comped])
    if metric == "event_comped":
        return records(title, [g for g in guests if g.is_comped])
    if metric == "event_members_registered":
        return records(title, [r for r in rows if r.kind == "member"])
    if metric == "event_declined":
        return records(title, [r for r in rows if r.kind == "declined"])
    if metric == "event_room":
        return records(title, [r for r in rows if r.kind in ("guest", "member")])
    if metric == "event_converted":
        return records(title, [g for g in guests if g.group == "converted"])
    if metric == "event_without_opp":
        return records(title, [g for g in guests if not g.opportunity_id])
    if metric == "event_stage_conflict":
        return records(title, [g for g in guests if g.group in _CONFLICT_GROUPS])
    if metric == "event_unanswered":
        # These people have no forum_event_guest row BY DEFINITION - not registering is not an
        # event they generate. They come from the member roster instead, minus everyone who
        # answered either way, which is why this branch does its own query.
        answered = {r.contact_id for r in rows if r.kind in ("member", "declined")}
        roster = (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == tenant_id,
            MetricRecord.business_id == event.business_id,
            MetricRecord.source == "ghl",
            MetricRecord.kind == "member",
            MetricRecord.status == "active"))).scalars().all()
        people = [m for m in roster if m.external_id not in answered]
        return {"metric": metric, "type": "records", "title": title,
                "subtitle": f"{len(people)} | neither registered nor declined",
                "count": len(people),
                "columns": ["name", "segment", "email"],
                "rows": [{"name": (m.name or "").title() or m.external_id,
                          "segment": (m.segment or "member").replace("_", " ").title(),
                          "email": m.email or "-"}
                         for m in sorted(people, key=lambda x: (x.name or "").lower())]}
    # A funnel row: `event_group_registered`, `event_group_deciding`, ...
    if metric.startswith("event_group_"):
        g = metric[len("event_group_"):]
        if g in GROUPS:
            label = GROUP_LABELS.get(g, (g.title(), None))[0]
            return records(label, [x for x in guests if x.group == g])
    # Unknown, and SAID so. launch.py's drill falls through to a soft "no drill-down defined
    # yet", which turns a typo into a silently empty drawer.
    raise KeyError(metric)
