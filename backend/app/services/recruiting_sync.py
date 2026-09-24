"""ULRG Recruiting — the read path from GoHighLevel (RECRUITING-SPEC §9, Phase 1).

Reads a SECOND GHL location, the brokerage's own recruiting one, under the `ghl_recruiting`
provider. It shares nothing with `sync_ghl` but the vendor: that one snapshots a membership
programme's tags and renewals, this one maintains a candidate pipeline with history.

THE SHAPE OF THIS FILE IS THE POINT. `_ghl_snapshot` replaces a kind's rows every run, which is
right for "how many members are there today" and destroys every question Recruiting asks. So:

  * candidates are UPSERTED by (tenant_id, opportunity_id) and never deleted;
  * a stage change appends to `recruiting_stage_event` -- the only place "signed in September"
    can come from, because current state cannot say WHEN;
  * appointments are upserted by GHL's event id, so a status that moves from `confirmed` to
    `showed` updates one row rather than making a second.

Nothing here writes to GHL. The first write ships in Phase 4, through the outbox.

Two conventions that have each cost a production outage and are load-bearing here:

  * REAL datetime OBJECTS into DateTime columns, never strings. asyncpg refuses a string and
    SQLite accepts it, so a test suite on SQLite cannot catch it -- 26 of 26 FUB production syncs
    failed this way before anybody could see it (FUB-SPEC A1).
  * EVERY string is truncated against its column's real length. An over-long value passes SQLite
    and raises `value too long for type character varying(n)` on Postgres.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations import ghl
from ..models import (
    Integration, RecruitingActivity, RecruitingAppointment, RecruitingCandidate,
    RecruitingSeat, RecruitingStageEvent,
)
from ..security import dec

# Column widths, from models.py. Named here so a truncation is obviously deliberate rather than
# a magic number, and so the two move together.
_LEN = {"name": 200, "brokerage": 200, "city": 120, "source": 120, "status": 16,
        "stage_group": 48, "summary": 280, "opportunity_id": 64, "contact_id": 64,
        "pipeline_id": 64, "stage_id": 64, "source_url": 512}


def _trunc(value, n: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:n] if text else None


def _dtm(value) -> dt.datetime | None:
    """A GHL timestamp as an AWARE datetime, or None.

    GHL sends ISO-8601, sometimes with `Z`, sometimes with an offset, occasionally epoch
    milliseconds on the calendar endpoints. Anything unparseable becomes None rather than a
    guess: a wrong date here becomes a wrong "days in stage" on somebody's screen.
    """
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, (int, float)):                      # epoch ms
        try:
            return dt.datetime.fromtimestamp(float(value) / 1000.0, dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.isdigit() and len(text) >= 12:                   # epoch ms as a string
        return _dtm(int(text))
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def stage_group_for(stage_id: str | None, groups) -> tuple[str | None, str | None]:
    """(label, owner_role) for a stage, from `config.recruiting_stage_groups`.

    MATCHED ON STAGE ID, never on the stage's name. D2 allows name substrings as a first-run
    SUGGESTION in the settings screen and nowhere else: a brokerage renaming "Offer out" to
    "ICA sent" must not silently empty half the tab.
    """
    for row in groups or []:
        try:
            label, owner_role, stage_ids = row[0], row[1], row[2]
        except (TypeError, IndexError):
            continue
        if stage_id and stage_id in (stage_ids or []):
            return _trunc(label, _LEN["stage_group"]), owner_role
    return None, None


def _custom_value(opp: dict, field_id: str | None):
    """One opportunity custom-field value by FIELD ID."""
    if not field_id:
        return None
    for row in (opp.get("customFields") or []):
        if row.get("id") == field_id:
            return row.get("fieldValue", row.get("value"))
    return None


def _money(value):
    if value in (None, ""):
        return None
    try:
        return round(float(str(value).replace("$", "").replace(",", "")), 2)
    except (TypeError, ValueError):
        return None


async def _seats(s: AsyncSession, tenant_id) -> list[RecruitingSeat]:
    rows = await s.execute(select(RecruitingSeat).where(
        RecruitingSeat.tenant_id == tenant_id, RecruitingSeat.active.is_(True)))
    return list(rows.scalars().all())


async def sync_ghl_recruiting(s: AsyncSession, tenant_id: uuid.UUID, integ: Integration) -> int:
    """Pull the recruiting pipeline. Returns the number of rows touched.

    Degrades rather than raises when it is not configured yet: a connection with no
    `pipeline_id` is a workspace that has connected the location and not finished Settings, which
    is a normal state during rollout and not a failed sync.
    """
    cfg = integ.config or {}
    location_id = cfg.get("location_id")
    pipeline_id = cfg.get("pipeline_id")
    if not location_id:
        raise ValueError("Recruiting needs a location_id on the connection.")
    if not pipeline_id:
        # Not an error. Settings has not been finished, and there is nothing to read yet.
        return 0

    token = dec(integ.access_token_enc) if integ.access_token_enc else None
    if not token:
        raise ValueError("Recruiting needs a Private Integration Token.")

    groups = cfg.get("recruiting_stage_groups") or []
    gci_field = cfg.get("gci_field_id")
    brokerage_field = cfg.get("brokerage_field_id")
    now = dt.datetime.now(dt.timezone.utc)
    touched = 0

    seats = await _seats(s, tenant_id)
    by_ghl_user = {seat.ghl_user_id: seat for seat in seats if seat.ghl_user_id}
    by_calendar = {seat.calendar_id: seat for seat in seats if seat.calendar_id}

    # ── candidates ──────────────────────────────────────────────────────────────────────────
    opps = await ghl.get_opportunities(token, location_id)
    opps = [o for o in opps if (o.get("pipelineId") or o.get("pipeline_id")) == pipeline_id]

    existing = {}
    if opps:
        rows = await s.execute(select(RecruitingCandidate).where(
            RecruitingCandidate.tenant_id == tenant_id,
            RecruitingCandidate.opportunity_id.in_(
                [_trunc(o.get("id"), _LEN["opportunity_id"]) for o in opps if o.get("id")])))
        existing = {c.opportunity_id: c for c in rows.scalars().all()}

    for opp in opps:
        opp_id = _trunc(opp.get("id"), _LEN["opportunity_id"])
        if not opp_id:
            continue
        stage_id = _trunc(opp.get("pipelineStageId") or opp.get("stageId"), _LEN["stage_id"])
        group, owner_role = stage_group_for(stage_id, groups)
        contact = opp.get("contact") or {}
        assigned = opp.get("assignedTo") or opp.get("assignedUserId")
        owner_seat = by_ghl_user.get(assigned)

        row = existing.get(opp_id)
        if row is None:
            row = RecruitingCandidate(
                tenant_id=tenant_id, business_id=integ.business_id, opportunity_id=opp_id)
            s.add(row)
            # entered_stage_at is only knowable from now on: GHL does not report when a stage
            # was entered, so the first sighting is the best honest answer, and every later
            # change gets a real one from the diff below.
            row.entered_stage_at = _dtm(opp.get("updatedAt")) or now
            existing[opp_id] = row
        elif row.stage_id != stage_id:
            # THE DIFF. One event per observed change, and the stage clock restarts.
            occurred = _dtm(opp.get("updatedAt")) or now
            s.add(RecruitingStageEvent(
                tenant_id=tenant_id, candidate_id=row.id, from_stage_id=row.stage_id,
                to_stage_id=stage_id, to_group=group, occurred_at=occurred, source="sync",
                # Whoever owns it at the moment of the change. Attribution has to survive a
                # later reassignment, so it is copied here rather than joined later.
                actor_seat_id=(owner_seat.id if owner_seat else row.owner_seat_id)))
            row.entered_stage_at = occurred
            touched += 1

        row.contact_id = _trunc(contact.get("id") or opp.get("contactId"), _LEN["contact_id"])
        row.pipeline_id = _trunc(pipeline_id, _LEN["pipeline_id"])
        row.stage_id = stage_id
        row.stage_group = group
        row.status = _trunc(opp.get("status"), _LEN["status"])
        row.owner_seat_id = owner_seat.id if owner_seat else row.owner_seat_id
        row.name = _trunc(opp.get("name") or ghl.contact_name(contact) if contact else opp.get("name"),
                          _LEN["name"])
        row.brokerage = _trunc(_custom_value(opp, brokerage_field), _LEN["brokerage"])
        row.city = _trunc(contact.get("city"), _LEN["city"])
        row.source = _trunc(contact.get("attributionSource") or contact.get("source")
                            or opp.get("source"), _LEN["source"])
        row.gci_ttm = _money(_custom_value(opp, gci_field))
        row.created_at_src = _dtm(opp.get("createdAt")) or row.created_at_src
        row.updated_at_src = _dtm(opp.get("updatedAt"))
        row.dnd = _dnd(contact)
        row.source_url = _trunc(ghl.contact_url(location_id, row.contact_id) if row.contact_id
                                else None, _LEN["source_url"])
        row.synced_at = now
        touched += 1

    await s.flush()          # candidate ids, for the appointment join below

    # ── appointments, per configured calendar ───────────────────────────────────────────────
    by_contact = {c.contact_id: c for c in existing.values() if c.contact_id}
    if by_calendar:
        start_ms = int((now - dt.timedelta(days=14)).timestamp() * 1000)
        end_ms = int((now + dt.timedelta(days=30)).timestamp() * 1000)
        for calendar_id, seat in by_calendar.items():
            body = await ghl.ghl_json(
                "GET", "/calendars/events", token=token, location_id=location_id,
                params={"locationId": location_id, "calendarId": calendar_id,
                        "startTime": start_ms, "endTime": end_ms})
            events = body.get("events") or []
            if not events:
                continue
            ids = [_trunc(e.get("id"), 64) for e in events if e.get("id")]
            rows = await s.execute(select(RecruitingAppointment).where(
                RecruitingAppointment.tenant_id == tenant_id,
                RecruitingAppointment.event_id.in_(ids)))
            known = {a.event_id: a for a in rows.scalars().all()}
            for ev in events:
                event_id = _trunc(ev.get("id"), 64)
                if not event_id:
                    continue
                appt = known.get(event_id)
                if appt is None:
                    appt = RecruitingAppointment(
                        tenant_id=tenant_id, event_id=event_id, source="ghl")
                    s.add(appt)
                    known[event_id] = appt
                contact_id = _trunc(ev.get("contactId"), _LEN["contact_id"])
                appt.calendar_id = _trunc(calendar_id, 64)
                appt.seat_id = seat.id
                appt.contact_id = contact_id
                cand = by_contact.get(contact_id)
                if cand is not None:
                    appt.candidate_id = cand.id
                appt.start_at = _dtm(ev.get("startTime"))
                appt.end_at = _dtm(ev.get("endTime"))
                # GHL's own vocabulary, stored verbatim. D8 decides what "Held" means on top of
                # it; normalising here would destroy the evidence that decision rests on.
                appt.status = _trunc(ev.get("appointmentStatus"), 24)
                appt.created_at_src = _dtm(ev.get("dateAdded") or ev.get("createdAt"))
                booked_by = by_ghl_user.get(ev.get("createdBy") or ev.get("assignedUserId"))
                if booked_by is not None and appt.booked_by_seat_id is None:
                    appt.booked_by_seat_id = booked_by.id
                touched += 1

    # ── seat <-> GHL user suggestions ───────────────────────────────────────────────────────
    # SUGGESTIONS, not assignments. Matching by email is right often enough to save the typing
    # and wrong often enough that it must not happen behind somebody's back -- FUB-SPEC A7 is
    # what an unfixable automatic match costs. The settings screen offers these; a person picks.
    users = await ghl.get_users(token, location_id)
    cfg["ghl_users"] = [{"id": u.get("id"), "name": _trunc(u.get("name"), 160),
                         "email": (u.get("email") or "").strip().lower()}
                        for u in users if u.get("id")]
    cfg["synced_at"] = now.isoformat()
    integ.config = dict(cfg)                 # reassign: SQLAlchemy does not see in-place mutation

    await s.commit()
    return touched


def _dnd(contact: dict) -> dict:
    """Per-channel do-not-contact, read before every send (§5.5) and never overridable.

    GHL reports a blanket `dnd` flag AND per-channel `dndSettings`. Both are honoured, and the
    blanket flag wins: a contact who opted out entirely has not consented to email because the
    per-channel record happens to be missing.
    """
    blanket = bool(contact.get("dnd"))
    settings = contact.get("dndSettings") or {}
    out = {}
    for channel, key in (("sms", "SMS"), ("email", "Email"), ("call", "Call")):
        status = ((settings.get(key) or {}).get("status") or "").lower()
        out[channel] = blanket or status in ("active", "permanent")
    return out


async def note_activity(s: AsyncSession, tenant_id, *, candidate_id, kind: str,
                        occurred_at: dt.datetime, seat_id=None, summary: str | None = None,
                        source: str = "axcion", **ids) -> RecruitingActivity:
    """Append one activity row, truncated and deduped by whichever GHL id it carries.

    Phase 1 only ever calls this for stage moves the sync observed. Phase 4's outbox and Phase
    6's poll both land here too, which is why the dedupe lives at this seam: a text we sent comes
    back through the poll with the same `ghl_message_id` and must not be counted twice.
    """
    row = RecruitingActivity(
        tenant_id=tenant_id, candidate_id=candidate_id, seat_id=seat_id, kind=_trunc(kind, 24),
        occurred_at=occurred_at, summary=_trunc(summary, _LEN["summary"]), source=source,
        ghl_message_id=_trunc(ids.get("ghl_message_id"), 64),
        ghl_note_id=_trunc(ids.get("ghl_note_id"), 64),
        ghl_task_id=_trunc(ids.get("ghl_task_id"), 64),
        ghl_event_id=_trunc(ids.get("ghl_event_id"), 64),
        duration_s=ids.get("duration_s"))
    s.add(row)
    return row
