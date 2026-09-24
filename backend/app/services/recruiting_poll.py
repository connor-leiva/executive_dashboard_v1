"""ULRG Recruiting — reading back what happened in GoHighLevel (RECRUITING-SPEC §5.6, Phase 6a).

Private Integration Tokens have no webhooks (§5.1), so this polls. Every five minutes it asks
which conversations have moved, reads the new messages in those, and writes them into
`recruiting_activity` -- which is what finally makes four things real:

  * DIALS AND CONVERSATIONS on the SDR card. Phase 5 already counts them; there was simply
    nothing writing the rows.
  * SPEED TO LEAD, for the same reason.
  * RULES THAT CLEAR THEMSELVES ON A REPLY. `offer_out_stale` and `appt_24h` already read the
    last inbound message, so a recruit answering in GHL clears somebody's Axcion list on the next
    queue tick without anybody touching Axcion. No new code; the rows just start existing.
  * DELIVERY STATUS for what the outbox sent. `POST /conversations/messages` returning 200 means
    QUEUED, not delivered, and the difference matters when somebody asks whether a text arrived.

WHAT THIS FILE IS CAREFUL ABOUT, AND WHY IT LOOKS DEFENSIVE.

V3 in the sourcing doc -- the conversation search parameters and the message `type` values -- is
NOT SETTLED, because the probe needs a token nobody has issued yet. HighLevel's docs show
`TYPE_SMS`/`TYPE_CALL` in places and bare `SMS`/`Call` in others, and the messages endpoint has
shipped both a flat list and one nested under `messages.messages`. So `classify()` accepts every
shape that has been documented, and anything it does not recognise is COUNTED AND REPORTED rather
than dropped: a poll that silently ignores an unknown message type would under-count somebody's
dials forever and look like it was working.

The dedupe is `ghl_message_id`, with a partial unique index behind it. A text the outbox sent
comes back through this poll carrying the same id, and without that it would be counted twice --
once as ours, once as GHL's.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations import ghl
from ..models import (
    Integration, RecruitingAction, RecruitingActivity, RecruitingAppointment,
    RecruitingCandidate, RecruitingSeat,
)
from ..security import dec
from .recruiting_sync import ghl_id

# How far back a first poll reaches. Not "everything": a location with two years of history would
# spend an afternoon paging it, and the tab only ever asks about the current month.
FIRST_RUN_DAYS = 30
MAX_CONVERSATIONS = 100
MAX_MESSAGES_PER_CONVERSATION = 50
CALENDAR_BACK_DAYS, CALENDAR_FORWARD_DAYS = 14, 30

# Every spelling of a message type these endpoints have been documented with, mapped onto the
# kinds recruiting_activity stores. Lower-cased before lookup.
_TYPES = {
    "sms": "sms", "type_sms": "sms", "text": "sms",
    "email": "email", "type_email": "email",
    "call": "call", "type_call": "call", "voicemail": "call", "type_voicemail": "call",
}
_INBOUND = {"inbound", "in", "incoming", "received"}


def classify(message: dict) -> tuple[str | None, bool]:
    """(kind, is_inbound) for one message, or (None, _) if we do not recognise it.

    Returning None rather than guessing is deliberate. A message mapped to the wrong kind becomes
    a dial that never happened or a conversation nobody had, and those numbers end up on a
    scorecard.
    """
    raw = (message.get("messageType") or message.get("type") or "")
    channel = _TYPES.get(str(raw).strip().lower())
    if channel is None:
        return None, False
    direction = str(message.get("direction") or "").strip().lower()
    inbound = direction in _INBOUND
    return f"{channel}_{'in' if inbound else 'out'}", inbound


def _dtm(value):
    """A GHL timestamp as an aware datetime, or None. Same tolerance as the sync's parser: ISO
    with or without Z, or epoch milliseconds."""
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return dt.datetime.fromtimestamp(float(value) / 1000.0, dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.isdigit() and len(text) >= 12:
        return _dtm(int(text))
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _messages_of(body: dict) -> list:
    """The message list, whichever shape this version returns.

    Documented as both a flat `{"messages": [...]}` and a nested
    `{"messages": {"messages": [...]}}`. Handling both is three lines; finding out in production
    which one a location returns is a morning.
    """
    rows = (body or {}).get("messages")
    if isinstance(rows, dict):
        rows = rows.get("messages")
    return rows if isinstance(rows, list) else []


async def poll_activity(s: AsyncSession, tenant_id) -> dict:
    """One workspace's conversations and calendars since the last cursor.

    Returns a summary the tick logs, including anything it could not classify -- which is how V3
    gets answered by production rather than by a probe nobody has run.
    """
    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id,
        Integration.provider == "ghl_recruiting"))).scalars().first()
    cfg = (integ.config or {}) if integ else {}
    if integ is None or not cfg.get("pipeline_id"):
        return {"skipped": "not configured"}
    # Never before the first full sync: without candidates there is nothing to attach a message
    # to, and every row would be discarded after being fetched.
    if not cfg.get("synced_at"):
        return {"skipped": "not synced yet"}

    token = dec(integ.access_token_enc) if integ.access_token_enc else None
    location_id = cfg.get("location_id")
    if not (token and location_id):
        return {"skipped": "incomplete connection"}

    now = dt.datetime.now(dt.timezone.utc)
    cursor = _dtm(cfg.get("activity_cursor")) or (now - dt.timedelta(days=FIRST_RUN_DAYS))

    cands = list((await s.execute(select(RecruitingCandidate).where(
        RecruitingCandidate.tenant_id == tenant_id))).scalars().all())
    by_contact = {c.contact_id: c for c in cands if c.contact_id}
    if not by_contact:
        return {"skipped": "no candidates"}

    seats = list((await s.execute(select(RecruitingSeat).where(
        RecruitingSeat.tenant_id == tenant_id, RecruitingSeat.active.is_(True)))).scalars().all())
    by_ghl_user = {x.ghl_user_id: x for x in seats if x.ghl_user_id}
    by_calendar = {x.calendar_id: x for x in seats if x.calendar_id}

    summary = {"conversations": 0, "messages": 0, "written": 0, "appointments": 0,
               "unknown_types": Counter(), "newest": cursor}

    # ── conversations ───────────────────────────────────────────────────────────────────────
    body = await ghl.ghl_json("GET", "/conversations/search", token=token,
                              location_id=location_id,
                              params={"locationId": location_id, "limit": MAX_CONVERSATIONS,
                                      "sort": "desc", "sortBy": "last_message_date"})
    conversations = (body.get("conversations") or []) if isinstance(body, dict) else []
    for conv in conversations:
        contact_id = ghl_id(conv.get("contactId"))
        cand = by_contact.get(contact_id)
        if cand is None:
            continue                       # not a recruiting contact; this location has others
        last = _dtm(conv.get("lastMessageDate") or conv.get("last_message_date"))
        if last is not None and last <= cursor:
            # Sorted by last message descending, so everything after this is older too.
            break
        summary["conversations"] += 1
        await _ingest_conversation(s, tenant_id, conv, cand, token, location_id,
                                   cursor, by_ghl_user, summary)

    # ── calendars: statuses, and bookings made directly in GHL ──────────────────────────────
    if by_calendar:
        start_ms = int((now - dt.timedelta(days=CALENDAR_BACK_DAYS)).timestamp() * 1000)
        end_ms = int((now + dt.timedelta(days=CALENDAR_FORWARD_DAYS)).timestamp() * 1000)
        for calendar_id, seat in by_calendar.items():
            events = (await ghl.ghl_json(
                "GET", "/calendars/events", token=token, location_id=location_id,
                params={"locationId": location_id, "calendarId": calendar_id,
                        "startTime": start_ms, "endTime": end_ms})).get("events") or []
            summary["appointments"] += await _ingest_events(
                s, tenant_id, events, calendar_id, seat, by_contact, by_ghl_user)

    # The cursor moves to the newest message actually SEEN, not to now. If a page was truncated
    # or a call failed, the next run starts where this one really got to rather than skipping
    # whatever happened in between.
    integ.config = {**cfg, "activity_cursor": summary["newest"].isoformat(),
                    "activity_polled_at": now.isoformat()}
    await s.commit()

    summary["newest"] = summary["newest"].isoformat()
    summary["unknown_types"] = dict(summary["unknown_types"])
    return summary


async def _ingest_conversation(s, tenant_id, conv, cand, token, location_id, cursor,
                               by_ghl_user, summary) -> None:
    body = await ghl.ghl_json("GET", f"/conversations/{conv.get('id')}/messages", token=token,
                              location_id=location_id,
                              params={"limit": MAX_MESSAGES_PER_CONVERSATION})
    known = {a.ghl_message_id for a in (await s.execute(select(RecruitingActivity).where(
        RecruitingActivity.tenant_id == tenant_id,
        RecruitingActivity.candidate_id == cand.id,
        RecruitingActivity.ghl_message_id.isnot(None)))).scalars().all()}

    for message in _messages_of(body):
        if not isinstance(message, dict):
            continue
        summary["messages"] += 1
        message_id = str(message.get("id") or "")[:64]
        when = _dtm(message.get("dateAdded") or message.get("dateUpdated"))
        if when is None or when <= cursor:
            continue
        if when > summary["newest"]:
            summary["newest"] = when

        kind, inbound = classify(message)
        if kind is None:
            # Recorded, not discarded. This is how the unsettled V3 gets answered from real
            # traffic instead of from a guess.
            summary["unknown_types"][str(message.get("messageType") or message.get("type"))] += 1
            continue

        # Our own send, already recorded by the outbox: update its delivery status and move on.
        if message_id and message_id in known:
            await _update_delivery(s, tenant_id, message_id, message)
            continue

        seat = by_ghl_user.get(ghl_id(message.get("userId")))
        duration = message.get("duration") or message.get("callDuration")
        s.add(RecruitingActivity(
            tenant_id=tenant_id, candidate_id=cand.id,
            seat_id=seat.id if seat else None, kind=kind, occurred_at=when,
            ghl_message_id=message_id or None,
            duration_s=int(duration) if isinstance(duration, (int, float)) else None,
            # A DESCRIPTION, never the body (§5.5). What a recruit wrote is not ours to keep.
            summary=_describe(kind, message), source="ghl_poll"))
        summary["written"] += 1

        if inbound:
            cand.last_inbound_at = when
        elif cand.last_outbound_at is None or when > cand.last_outbound_at.replace(
                tzinfo=cand.last_outbound_at.tzinfo or dt.timezone.utc):
            cand.last_outbound_at = when


def _describe(kind: str, message: dict) -> str:
    """One line about a message, with none of its content.

    A call gets its length because that is what separates a dial from a conversation (D9); a text
    or an email gets only its direction. Storing what somebody wrote back would make this table a
    copy of their inbox.
    """
    if kind.startswith("call"):
        seconds = message.get("duration") or message.get("callDuration") or 0
        try:
            seconds = int(seconds)
        except (TypeError, ValueError):
            seconds = 0
        if not seconds:
            return "Call · no answer"
        return f"Call · {seconds // 60}m {seconds % 60}s" if seconds >= 60 else f"Call · {seconds}s"
    channel = "Text" if kind.startswith("sms") else "Email"
    return f"{channel} {'received' if kind.endswith('_in') else 'sent'}"


async def _update_delivery(s, tenant_id, message_id: str, message: dict) -> None:
    """A 200 from the send endpoint meant QUEUED. This is where it becomes delivered or failed.

    Only a FAILURE is written onto the action row. "Delivered" adds nothing a person needs --
    they already saw it send -- while a silent failure is the one thing they would want to know
    and would otherwise never learn.
    """
    status = str(message.get("status") or "").strip().lower()
    if status not in ("failed", "undelivered", "rejected", "error"):
        return
    row = (await s.execute(select(RecruitingAction).where(
        RecruitingAction.tenant_id == tenant_id,
        RecruitingAction.ghl_ref == message_id))).scalars().first()
    if row is None or row.status == "failed":
        return
    row.status = "failed"
    row.error = f"GHL reported the message as {status}."


async def _ingest_events(s, tenant_id, events, calendar_id, seat, by_contact, by_ghl_user) -> int:
    """Appointment statuses, and bookings somebody made in GHL rather than here.

    Upserted by event id, so `confirmed` becoming `showed` updates one row. A booking that never
    went through our outbox still belongs on the SDR's strip and in the held count -- the tab is
    about what happened, not about which screen it happened on.
    """
    if not events:
        return 0
    ids = [str(e.get("id"))[:64] for e in events if e.get("id")]
    known = {a.event_id: a for a in (await s.execute(select(RecruitingAppointment).where(
        RecruitingAppointment.tenant_id == tenant_id,
        RecruitingAppointment.event_id.in_(ids)))).scalars().all()}
    touched = 0
    for event in events:
        event_id = str(event.get("id") or "")[:64]
        if not event_id:
            continue
        appt = known.get(event_id)
        if appt is None:
            appt = RecruitingAppointment(tenant_id=tenant_id, event_id=event_id, source="ghl")
            s.add(appt)
            known[event_id] = appt
        contact_id = ghl_id(event.get("contactId"))
        cand = by_contact.get(contact_id)
        appt.calendar_id = str(calendar_id)[:64]
        appt.seat_id = seat.id
        appt.contact_id = contact_id[:64] if contact_id else None
        if cand is not None:
            appt.candidate_id = cand.id
        appt.start_at = _dtm(event.get("startTime"))
        appt.end_at = _dtm(event.get("endTime"))
        appt.status = str(event.get("appointmentStatus") or "")[:24] or None
        appt.created_at_src = _dtm(event.get("dateAdded") or event.get("createdAt"))
        booked_by = by_ghl_user.get(
            ghl_id(event.get("createdBy")) or ghl_id(event.get("assignedUserId")))
        if booked_by is not None and appt.booked_by_seat_id is None:
            appt.booked_by_seat_id = booked_by.id
        touched += 1
    return touched
