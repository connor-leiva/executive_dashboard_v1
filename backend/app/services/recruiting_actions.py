"""ULRG Recruiting — writing back to GoHighLevel (RECRUITING-SPEC §5).

THE ONLY MODULE IN THIS PRODUCT THAT WRITES TO A CUSTOMER'S CRM. A guard test holds that: every
non-GET through `ghl_request` originates here. That is worth a test rather than a convention,
because the blast radius is somebody else's recruits receiving somebody else's text message.

Four properties, in the order they matter:

  1. NOTHING IS ON BY DEFAULT. Four gates (§5.2) and every one of them defaults shut: an env
     flag, a workspace flag, a per-seat flag, and the workspace not being suspended or frozen.
     `dry_run` defaults TRUE on top of that, so the first thing this code does in a live
     workspace is record exactly what it WOULD have sent.
  2. THE ROW IS WRITTEN BEFORE THE CALL. If the process dies mid-send there is still a record
     that something was attempted -- the difference between "we do not know" and "nothing
     happened". A text cannot be un-sent, so the system has to be able to tell those apart.
  3. ONE CLICK IS ONE SEND. The client generates `idempotency_key` per click; a double-click, a
     retry or a refresh collapse onto the same row. GHL offers no idempotency for a message, so
     this table is the only thing in the way.
  4. A STUCK SEND IS NOT RETRIED. A row in `sending` for over two minutes goes to `failed` with
     "unknown outcome", because a duplicate text to a recruit is worse than a missing one and
     only a person can tell which happened.

COMPLIANCE IS SERVER-SIDE AND HAS NO OVERRIDE (§5.5). Do-not-contact is read from the synced
candidate and re-read when the row is stale; quiet hours are the LOCATION's 08:00-21:00 and are
not configurable outside that. There is no flag, anywhere, that lets a workspace text somebody
who opted out.
"""
from __future__ import annotations

import datetime as dt
import uuid
import zoneinfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..integrations import ghl
from ..models import (
    Integration, RecruitingAction, RecruitingActivity, RecruitingCandidate, RecruitingQueueItem,
    RecruitingSeat, RecruitingStageEvent, Tenant, User,
)
from ..security import dec

KINDS = ("send_sms", "send_email", "add_note", "upsert_task", "complete_task",
         "move_stage", "book", "log_call")
# Which kinds are a MESSAGE to the candidate. Only these face do-not-contact and quiet hours: a
# note on a contact record is not a communication with the recruit.
MESSAGE_KINDS = {"send_sms": "sms", "send_email": "email"}

RETRY_STATUS = (429, 500, 502, 503, 504)
BACKOFF = (30, 120, 600)                 # 30s, 2m, 10m -- three attempts, then failed
MAX_ATTEMPTS = 3
STUCK_AFTER = dt.timedelta(minutes=2)
INLINE_BUDGET_S = 8.0
RETAIN_DAYS = 365                        # §5.5, in line with RECALL_TRANSCRIPT_RETAIN_DAYS


class WriteRefused(Exception):
    """A write that must not happen. `code` is the HTTP status the router should return: 409 for
    a gate (the workspace could open it) and 422 for compliance (it could not, and should not)."""

    def __init__(self, reason: str, *, code: int = 409, gate: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.code = code
        self.gate = gate


# ── gates (§5.2) ─────────────────────────────────────────────────────────────────────────────

async def writeback_state(s: AsyncSession, tenant_id, integ: Integration | None,
                          seat: RecruitingSeat | None) -> dict:
    """Which gate is shut, for the payload's `connection.writeback`.

    Named rather than boolean, so the drawer's button can say "Sending is off for this workspace"
    instead of being mysteriously dead -- the difference between a setting somebody can find and
    a bug they report.
    """
    if not settings.RECRUITING_WRITEBACK_ENABLED:
        return {"open": False, "gate": "platform",
                "reason": "Sending from Axcion is switched off for this deployment."}
    tenant = await s.get(Tenant, tenant_id)
    if tenant is None or (tenant.status or "") == "suspended":
        return {"open": False, "gate": "suspended", "reason": "This workspace is suspended."}
    if ((tenant.config or {}).get("syncs_frozen")):
        # Freezing is how a compromised credential stops being used. A write is exactly what it
        # is meant to stop.
        return {"open": False, "gate": "frozen",
                "reason": "Syncing and sending are frozen for this workspace."}
    if integ is None:
        return {"open": False, "gate": "not_connected", "reason": "No recruiting location is connected."}
    cfg = integ.config or {}
    if not cfg.get("writeback_enabled"):
        return {"open": False, "gate": "workspace",
                "reason": "Sending is off for this workspace. An owner can turn it on in "
                          "Settings › Recruiting."}
    if seat is None:
        return {"open": False, "gate": "seat", "reason": "You do not hold a recruiting seat."}
    if not seat.writeback_enabled:
        return {"open": False, "gate": "seat",
                "reason": f"Sending is not switched on for {seat.display_name} yet."}
    return {"open": True, "gate": None, "reason": None,
            "dry_run": bool(cfg.get("dry_run", True))}


# ── compliance (§5.5) ────────────────────────────────────────────────────────────────────────

def _tz_for(integ: Integration) -> zoneinfo.ZoneInfo:
    """The LOCATION's timezone. A recruit's own is unknown, so quiet hours follow the brokerage
    -- which is the honest approximation and the one the spec picks."""
    name = (integ.config or {}).get("location_timezone") or settings.BILLING_TIMEZONE
    try:
        return zoneinfo.ZoneInfo(name or "America/Denver")
    except Exception:  # noqa: BLE001
        return zoneinfo.ZoneInfo("America/Denver")


QUIET_FLOOR, QUIET_CEILING = 8, 21


def _clock(when: dt.datetime, tz) -> str:
    """"8 am", built by hand. strftime's %-I is a POSIX extension: it renders on Railway and
    raises on Windows, where the tests run."""
    local = when.astimezone(tz)
    hour = local.hour % 12 or 12
    suffix = "am" if local.hour < 12 else "pm"
    return f"{hour}{':' + f'{local.minute:02d}' if local.minute else ''} {suffix}"


def quiet_hours(integ: Integration) -> tuple[int, int]:
    qh = (integ.config or {}).get("quiet_hours") or {}
    try:
        start, end = int(qh.get("start", QUIET_FLOOR)), int(qh.get("end", QUIET_CEILING))
    except (TypeError, ValueError):
        return QUIET_FLOOR, QUIET_CEILING
    # Clamped here as well as in Settings. A stored value that predates the bound, or one written
    # by a migration, must not be able to text somebody at 6am.
    return max(QUIET_FLOOR, min(start, QUIET_CEILING)), max(QUIET_FLOOR, min(end, QUIET_CEILING))


def check_compliance(kind: str, cand: RecruitingCandidate, integ: Integration,
                     now: dt.datetime) -> dict:
    """Refuse, or hold until morning. Returns {} to proceed, or {"scheduled_at": ...} to hold.

    NO OVERRIDE EXISTS. Not a flag, not an admin bypass, not a "send anyway" in the UI. A
    workspace that could override this would eventually override it.
    """
    channel = MESSAGE_KINDS.get(kind)
    if channel is None:
        return {}

    dnd = cand.dnd or {}
    if dnd.get(channel) or dnd.get("all"):
        first = (cand.name or "This candidate").split(" ")[0]
        word = "texts" if channel == "sms" else "emails"
        raise WriteRefused(f"{first} has opted out of {word}.", code=422)

    start, end = quiet_hours(integ)
    local = now.astimezone(_tz_for(integ))
    if start <= local.hour < end:
        return {}
    # Outside the window the send is HELD, not refused: the work is legitimate and the timing is
    # not. Refusing would push people to do it by hand at 11pm instead, which is the same text at
    # the same hour with none of the logging.
    target = local.replace(hour=start, minute=0, second=0, microsecond=0)
    if local.hour >= end:
        target += dt.timedelta(days=1)
    return {"scheduled_at": target.astimezone(dt.timezone.utc)}


# ── the outbox ───────────────────────────────────────────────────────────────────────────────

def _redact(payload: dict) -> dict:
    """What goes in `request`. The message body is KEPT -- it is the audit trail, and it is what
    somebody needs when a recruit asks what they were sent. Credentials never appear: this strips
    anything that looks like one rather than trusting callers not to pass it."""
    out = {}
    for key, value in (payload or {}).items():
        if key.lower() in ("token", "authorization", "api_key", "apikey", "secret", "password"):
            continue
        out[key] = value
    return out


async def submit(s: AsyncSession, tenant_id, user: User, body: dict) -> dict:
    """The endpoint's worker: authorise, gate, check, record, send.

    Order is deliberate and matches §5.3. Authorisation before gates so a stranger cannot learn
    which gates a workspace has open; compliance before the row so a refusal is recorded as a
    refusal rather than a failure.
    """
    kind = (body.get("kind") or "").strip()
    if kind not in KINDS:
        raise WriteRefused(f"Unknown action {kind!r}.", code=422)
    key = (body.get("idempotency_key") or "").strip()
    if not key:
        raise WriteRefused("Every write needs an idempotency key.", code=422)

    cand = (await s.execute(select(RecruitingCandidate).where(
        RecruitingCandidate.tenant_id == tenant_id,
        RecruitingCandidate.id == body.get("candidate_id")))).scalars().first()
    if cand is None:
        raise WriteRefused("No such candidate.", code=404)

    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id,
        Integration.provider == "ghl_recruiting"))).scalars().first()
    seat = (await s.execute(select(RecruitingSeat).where(
        RecruitingSeat.tenant_id == tenant_id, RecruitingSeat.user_id == user.id,
        RecruitingSeat.active.is_(True)))).scalars().first()

    state = await writeback_state(s, tenant_id, integ, seat)
    if not state["open"]:
        raise WriteRefused(state["reason"], code=409, gate=state["gate"])

    now = dt.datetime.now(dt.timezone.utc)
    payload = _redact(body.get("payload") or {})

    # An existing row for this key IS the answer. Returned before anything else happens, so a
    # retried request cannot send a second message even while the first is still in flight.
    prior = (await s.execute(select(RecruitingAction).where(
        RecruitingAction.tenant_id == tenant_id,
        RecruitingAction.idempotency_key == key))).scalars().first()
    if prior is not None:
        return _result(prior)

    try:
        hold = check_compliance(kind, cand, integ, now)
    except WriteRefused as refusal:
        s.add(RecruitingAction(
            tenant_id=tenant_id, idempotency_key=key, seat_id=seat.id if seat else None,
            actor_user_id=user.id, candidate_id=cand.id, kind=kind, request=payload,
            status="refused", error=refusal.reason[:400]))
        await s.commit()
        raise

    action = RecruitingAction(
        tenant_id=tenant_id, idempotency_key=key, seat_id=seat.id if seat else None,
        actor_user_id=user.id, candidate_id=cand.id,
        queue_item_id=body.get("queue_item_id") or None, kind=kind,
        request={**payload, **({"scheduled_at": hold["scheduled_at"].isoformat()} if hold else {})},
        status="dry_run" if state.get("dry_run") else "queued")
    s.add(action)
    from .audit import audit
    audit(s, tenant_id, user.id, f"recruiting.{kind}", category="Recruiting",
          target_type="recruiting_candidate", target_id=str(cand.id),
          summary=f"{kind} to {cand.name or 'a candidate'}"
                  + (" (dry run)" if state.get("dry_run") else ""))
    try:
        await s.commit()                      # the row exists BEFORE the call
    except IntegrityError:
        # Two clicks raced to insert. The loser re-reads the winner's row rather than sending.
        await s.rollback()
        prior = (await s.execute(select(RecruitingAction).where(
            RecruitingAction.tenant_id == tenant_id,
            RecruitingAction.idempotency_key == key))).scalars().first()
        return _result(prior)

    if state.get("dry_run"):
        # The whole pipeline ran and the request body is on the row. Nothing was sent.
        return _result(action, note="Dry run: recorded, not sent.")
    if hold:
        # Held, not sent: outside quiet hours GHL takes `scheduledTimestamp` and releases it
        # itself. The drawer says when, in the location's own clock.
        return _result(action, note=f"Outside texting hours — held until "
                                    f"{_clock(hold['scheduled_at'], _tz_for(integ))}.")

    await _attempt(s, action, cand, integ, seat)
    return _result(action)


def _result(action: RecruitingAction | None, note: str | None = None) -> dict:
    if action is None:
        return {"status": "failed", "error": "The write could not be recorded."}
    return {"id": str(action.id), "status": action.status, "ghl_ref": action.ghl_ref,
            "error": action.error, "note": note,
            "scheduled_at": (action.request or {}).get("scheduled_at")}


async def _attempt(s: AsyncSession, action: RecruitingAction, cand, integ, seat) -> None:
    """One try. Sets the row to `sending` and commits FIRST, so a crash leaves evidence."""
    action.status = "sending"
    action.attempts = (action.attempts or 0) + 1
    await s.commit()

    token = dec(integ.access_token_enc) if integ.access_token_enc else None
    location_id = (integ.config or {}).get("location_id")
    if not token or not location_id:
        action.status, action.error = "failed", "The recruiting connection is incomplete."
        await s.commit()
        return

    try:
        ref, side = await _perform(action, cand, integ, seat, token, location_id)
    except ghl.GhlError as exc:
        _map_error(action, exc)
        if exc.status in RETRY_STATUS and action.attempts < MAX_ATTEMPTS:
            action.status = "queued"
            action.next_attempt_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
                seconds=BACKOFF[min(action.attempts - 1, len(BACKOFF) - 1)])
        else:
            action.status = "failed"
        await s.commit()
        return
    except Exception as exc:  # noqa: BLE001
        action.status, action.error = "failed", f"Unexpected: {type(exc).__name__}"
        await s.commit()
        return

    action.status = "sent"
    action.ghl_ref = (ref or "")[:64] or None
    action.sent_at = dt.datetime.now(dt.timezone.utc)
    await _side_effects(s, action, cand, side)
    await s.commit()


def _map_error(action: RecruitingAction, exc: ghl.GhlError) -> None:
    """§5.7, one table. A 403 that reads "Forbidden" sends somebody to the docs for twenty
    minutes; one that names the scope is a two-minute fix by an admin."""
    action.http_status = exc.status
    body = (exc.body or "").lower()
    if exc.status == 401:
        action.error = "The GHL connection needs to be reconnected."
    elif exc.status == 403:
        action.error = ("This connection is missing a write scope. An admin can add it to the "
                        "GHL private integration.")
    elif exc.status == 404:
        action.error = "Not found in GHL. It may have been deleted."
    elif exc.status in (409, 422) and ("slot" in body or "not available" in body):
        action.error = "That time was just taken."
    elif exc.status == 422 and ("10dlc" in body or "a2p" in body or "not registered" in body):
        # D6. Worth its own sentence: it is a registration somebody has to do, not a bug.
        action.error = "This number isn't registered for business texting yet (A2P 10DLC)."
    elif exc.status == 422:
        action.error = "GHL refused this. The details are in the action log."
    elif exc.status == 429:
        action.error = "Queued, will retry."
    else:
        action.error = "Queued, will retry."


# ── the writes themselves ────────────────────────────────────────────────────────────────────

async def _perform(action, cand, integ, seat, token, location_id) -> tuple[str | None, dict]:
    """Make the call. Returns (ghl reference, side-effect description).

    Every request here is shaped to §5.4 and pinned to Version 2021-07-28, which `ghl_request`
    sends. A v3 move is a separate, repo-wide change.
    """
    req = action.request or {}
    kind = action.kind

    if kind in ("send_sms", "send_email"):
        body = {"type": "SMS" if kind == "send_sms" else "Email", "contactId": cand.contact_id}
        if kind == "send_sms":
            body["message"] = req.get("message") or ""
            if seat and seat.from_number:
                body["fromNumber"] = seat.from_number
        else:
            body["subject"] = req.get("subject") or ""
            body["html"] = req.get("html") or f"<p>{(req.get('message') or '')}</p>"
            body["message"] = req.get("message") or ""
        if req.get("scheduled_at"):
            # Held rather than refused, per §5.5. GHL takes UTC seconds.
            body["scheduledTimestamp"] = int(dt.datetime.fromisoformat(
                req["scheduled_at"]).timestamp())
        r = await ghl.ghl_request("POST", "/conversations/messages", token=token,
                                  location_id=location_id, priority="write", json=body,
                                  raise_for_status=True)
        out = r.json() or {}
        ref = out.get("messageId") or out.get("emailMessageId") or out.get("conversationId")
        return ref, {"activity": "sms_out" if kind == "send_sms" else "email_out",
                     "summary": (req.get("message") or "")[:280], "message_id": ref}

    if kind in ("add_note", "log_call"):
        note = req.get("body") or req.get("note") or ""
        body = {"body": note[:2000]}
        if seat and seat.ghl_user_id:
            body["userId"] = seat.ghl_user_id
        r = await ghl.ghl_request("POST", f"/contacts/{cand.contact_id}/notes", token=token,
                                  location_id=location_id, priority="write", json=body,
                                  raise_for_status=True)
        out = (r.json() or {}).get("note") or (r.json() or {})
        return out.get("id"), {"activity": "call_out" if kind == "log_call" else "note",
                               "summary": note[:280], "note_id": out.get("id"),
                               "duration_s": req.get("duration_s")}

    if kind == "upsert_task":
        body = {"title": (req.get("title") or "Recruiting next step")[:200],
                "body": (req.get("body") or "")[:2000], "completed": False}
        if req.get("due_date"):
            body["dueDate"] = req["due_date"]
        if seat and seat.ghl_user_id:
            body["assignedTo"] = seat.ghl_user_id
        if cand.ghl_task_id:
            r = await ghl.ghl_request("PUT", f"/contacts/{cand.contact_id}/tasks/{cand.ghl_task_id}",
                                      token=token, location_id=location_id, priority="write",
                                      json=body, raise_for_status=True)
            return cand.ghl_task_id, {"activity": "task", "summary": body["title"][:280],
                                      "task_id": cand.ghl_task_id}
        r = await ghl.ghl_request("POST", f"/contacts/{cand.contact_id}/tasks", token=token,
                                  location_id=location_id, priority="write", json=body,
                                  raise_for_status=True)
        out = (r.json() or {}).get("task") or (r.json() or {})
        return out.get("id"), {"activity": "task", "summary": body["title"][:280],
                               "task_id": out.get("id"), "set_task": out.get("id")}

    if kind == "complete_task":
        task_id = req.get("task_id") or cand.ghl_task_id
        if not task_id:
            raise WriteRefused("There is no open next step to complete.", code=422)
        await ghl.ghl_request("PUT", f"/contacts/{cand.contact_id}/tasks/{task_id}/completed",
                              token=token, location_id=location_id, priority="write",
                              json={"completed": True}, raise_for_status=True)
        return task_id, {"activity": "task", "summary": "Next step completed",
                         "task_id": task_id, "clear_task": True}

    if kind == "move_stage":
        target = req.get("stage_id")
        if not target:
            raise WriteRefused("Which stage?", code=422)
        # OPTIMISTIC CONCURRENCY (§5.4). Somebody may have moved them in GHL while the drawer was
        # open, and overwriting that silently is how two systems disagree about a person's state.
        current = await ghl.get_opportunity(token, location_id, cand.opportunity_id)
        if current is None:
            raise WriteRefused("That opportunity is no longer in GHL.", code=404)
        live_stage = current.get("pipelineStageId") or current.get("stageId")
        if req.get("expected_stage_id") and live_stage != req["expected_stage_id"]:
            raise WriteRefused("Moved in GHL already. Refresh to see where they are.", code=409)
        body = {"pipelineStageId": target}
        groups = (integ.config or {}).get("recruiting_stage_groups") or []
        label, _role = rules_stage_group(target, groups)
        if label == "Signed":
            body["status"] = "won"
        await ghl.ghl_request("PUT", f"/opportunities/{cand.opportunity_id}", token=token,
                              location_id=location_id, priority="write", json=body,
                              raise_for_status=True)
        return cand.opportunity_id, {"activity": "stage_move", "summary": f"Moved to {label or target}",
                                     "stage": {"from": cand.stage_id, "to": target, "group": label}}

    if kind == "book":
        body = {"calendarId": req.get("calendar_id"), "locationId": location_id,
                "contactId": cand.contact_id, "startTime": req.get("start_time"),
                "endTime": req.get("end_time"),
                "title": (req.get("title") or f"Recruiting · {cand.name or 'candidate'}")[:200],
                "appointmentStatus": "confirmed", "toNotify": True}
        if req.get("assigned_user_id"):
            body["assignedUserId"] = req["assigned_user_id"]
        # NEVER ignoreFreeSlotValidation / ignoreDateRange (§5.4). A slot taken since the list
        # loaded must fail loudly, not double-book a Team Leader.
        r = await ghl.ghl_request("POST", "/calendars/events/appointments", token=token,
                                  location_id=location_id, priority="write", json=body,
                                  raise_for_status=True)
        out = (r.json() or {})
        return out.get("id"), {"activity": "booking", "summary": "Appointment booked",
                               "event_id": out.get("id")}

    raise WriteRefused(f"{kind} is not implemented.", code=422)


def rules_stage_group(stage_id, groups):
    from .recruiting_sync import stage_group_for
    return stage_group_for(stage_id, groups)


async def _side_effects(s: AsyncSession, action, cand, side: dict) -> None:
    """What a successful write changes locally: the activity row, the candidate, the queue item.

    The activity carries the GHL id, which is what stops the Phase 6 poll counting our own text
    a second time when it comes back round.
    """
    now = dt.datetime.now(dt.timezone.utc)
    s.add(RecruitingActivity(
        tenant_id=action.tenant_id, candidate_id=cand.id, seat_id=action.seat_id,
        kind=side.get("activity") or "note", occurred_at=now,
        summary=(side.get("summary") or "")[:280], source="axcion",
        ghl_message_id=(side.get("message_id") or None),
        ghl_note_id=(side.get("note_id") or None),
        ghl_task_id=(side.get("task_id") or None),
        ghl_event_id=(side.get("event_id") or None),
        duration_s=side.get("duration_s")))

    if side.get("activity") in ("sms_out", "email_out", "call_out"):
        cand.last_outbound_at = now
    if side.get("set_task"):
        cand.ghl_task_id = side["set_task"]
    if side.get("clear_task"):
        cand.ghl_task_id = None

    stage = side.get("stage")
    if stage:
        s.add(RecruitingStageEvent(
            tenant_id=action.tenant_id, candidate_id=cand.id, from_stage_id=stage["from"],
            to_stage_id=stage["to"], to_group=stage["group"], occurred_at=now, source="axcion",
            actor_seat_id=action.seat_id))
        cand.stage_id, cand.stage_group, cand.entered_stage_at = stage["to"], stage["group"], now

    if action.queue_item_id:
        item = await s.get(RecruitingQueueItem, action.queue_item_id)
        # `cleared_by="action"` rather than "user": a write cleared it, which is a different
        # sentence from somebody ticking it off, and it is not undoable.
        if item is not None and item.state in ("open", "snoozed"):
            item.state, item.cleared_by = "done", "action"
            item.cleared_at, item.action_id = now, action.id


# ── the retry tick ───────────────────────────────────────────────────────────────────────────

async def drain(s: AsyncSession, tenant_id) -> dict:
    """Retry what is queued, fail what is stuck, purge what is old. One workspace."""
    now = dt.datetime.now(dt.timezone.utc)
    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id,
        Integration.provider == "ghl_recruiting"))).scalars().first()

    # A row that has been `sending` for over two minutes has an UNKNOWN outcome. It is not
    # retried, ever: the message may well have gone. Only a person can check.
    stuck = list((await s.execute(select(RecruitingAction).where(
        RecruitingAction.tenant_id == tenant_id, RecruitingAction.status == "sending",
        RecruitingAction.created_at < now - STUCK_AFTER))).scalars().all())
    for row in stuck:
        row.status = "failed"
        row.error = "Unknown outcome — check GHL before resending."

    retried = 0
    if integ is not None:
        due = list((await s.execute(select(RecruitingAction).where(
            RecruitingAction.tenant_id == tenant_id, RecruitingAction.status == "queued",
            RecruitingAction.next_attempt_at.isnot(None),
            RecruitingAction.next_attempt_at <= now).limit(25))).scalars().all())
        for row in due:
            cand = await s.get(RecruitingCandidate, row.candidate_id) if row.candidate_id else None
            seat = await s.get(RecruitingSeat, row.seat_id) if row.seat_id else None
            if cand is None:
                row.status, row.error = "failed", "The candidate is gone."
                continue
            await _attempt(s, row, cand, integ, seat)
            retried += 1

    # §5.5: the bodies we sent are kept for a year and then are not.
    old = list((await s.execute(select(RecruitingAction).where(
        RecruitingAction.tenant_id == tenant_id,
        RecruitingAction.created_at < now - dt.timedelta(days=RETAIN_DAYS)))).scalars().all())
    for row in old:
        if row.request:
            row.request = {"purged": True}

    await s.commit()
    return {"stuck_failed": len(stuck), "retried": retried, "purged": len(old)}
