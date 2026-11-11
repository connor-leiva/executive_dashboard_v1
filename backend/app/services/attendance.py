"""Zoom sessions and the people who sat in them.

A person who rejoins is one row. The session type comes from the topic, the
same way an ad campaign's group comes from its name, so renaming a rule
reclassifies history without another pull from Zoom.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations import zoom
from ..models import (AttendancePerson, AttendanceSession, AttendanceSync, MetricRecord, Tenant)
from .ads_rules import match_name
from .scorecard import assign_sprints

DEFAULT_RULES = [
    {"match": "contains", "value": "edge", "label": "Edge Webinar"},
    {"match": "contains", "value": "the shift", "label": "beCollective / The Shift"},
    {"match": "contains", "value": "becollective", "label": "beCollective / The Shift"},
    {"match": "contains", "value": "be collective", "label": "beCollective / The Shift"},
    {"match": "contains", "value": "scottsdale", "label": "Forum"},
    {"match": "contains", "value": "dallas", "label": "Forum"},
    {"match": "contains", "value": "forum", "label": "Forum"},
]
UNMAPPED = "Unmapped"
_CONTACT_KINDS = ("member", "bc_member", "edge_member", "bc_shift_reg", "recruiting", "edge_registration")


def classify_topic(topic: str | None, rules: list | None = None) -> str:
    for rule in rules if rules is not None else DEFAULT_RULES:
        if match_name(topic or "", rule.get("match") or "contains", rule.get("value") or ""):
            return rule.get("label") or UNMAPPED
    return UNMAPPED


def validate_rules(rules) -> list[str]:
    if rules is None:
        return []
    if not isinstance(rules, list):
        return ["Mapping rules must be a list."]
    problems = []
    for i, rule in enumerate(rules, 1):
        if not isinstance(rule, dict):
            problems.append(f"Rule {i} is not an object.")
            continue
        if rule.get("match") not in ("prefix", "contains"):
            problems.append(f"Rule {i}: match must be prefix or contains.")
        if not str(rule.get("value") or "").strip():
            problems.append(f"Rule {i}: needs something to match on.")
        if not str(rule.get("label") or "").strip():
            problems.append(f"Rule {i}: needs a session type.")
    return problems


def dedupe_participants(rows: list[dict]) -> list[dict]:
    """Rejoins collapse to one person. Email wins over name."""
    folded: dict[str, dict] = {}
    for row in rows:
        email = str(row.get("user_email") or row.get("email") or "").strip().lower()
        name = " ".join(str(row.get("name") or "").split())
        key = email or (f"name:{name.lower()}" if name else "")
        if not key:
            continue
        slot = folded.setdefault(key, {
            "person_key": key[:255], "email": email or None, "name": name or None,
            "duration_seconds": 0, "joined_at": None, "left_at": None,
        })
        try:
            slot["duration_seconds"] += int(float(row.get("duration") or 0))
        except (TypeError, ValueError):
            pass
        joined = _parse_dt(row.get("join_time"))
        left = _parse_dt(row.get("leave_time"))
        if joined and (slot["joined_at"] is None or joined < slot["joined_at"]):
            slot["joined_at"] = joined
        if left and (slot["left_at"] is None or left > slot["left_at"]):
            slot["left_at"] = left
        if name and not slot["name"]:
            slot["name"] = name
    return list(folded.values())


def _parse_dt(value) -> dt.datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def backfill_bounds(today: dt.date, fiscal_quarters: list | None) -> tuple[dt.date, dt.date]:
    """Current sprint, plus the 13 weeks before it starts. Today is the end."""
    sprints = assign_sprints(fiscal_quarters or [], today)
    current = next((s for s in sprints if s.get("current")), None)
    start = dt.date.fromisoformat(current["start"]) if current else today - dt.timedelta(days=7)
    return start - dt.timedelta(weeks=13), today


async def _sync_row(s: AsyncSession, tenant_id) -> AttendanceSync:
    row = (await s.execute(select(AttendanceSync).where(
        AttendanceSync.tenant_id == tenant_id))).scalar_one_or_none()
    if row is None:
        row = AttendanceSync(tenant_id=tenant_id)
        s.add(row)
        await s.flush()
    return row


async def _contacts(s: AsyncSession, tenant_id) -> tuple[dict, dict]:
    rows = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.source == "ghl",
        MetricRecord.kind.in_(_CONTACT_KINDS)))).scalars()
    by_email: dict[str, MetricRecord] = {}
    names: dict[str, list] = {}
    for row in rows:
        email = (row.email or "").strip().lower()
        if email and email not in by_email:
            by_email[email] = row
        name = " ".join((row.name or "").lower().split())
        if name:
            names.setdefault(name, []).append(row)
    unique = {name: hits[0] for name, hits in names.items() if len(hits) == 1}
    return by_email, unique


def _match(person: dict, by_email: dict, by_name: dict) -> tuple[str, str | None, str | None]:
    email = person.get("email") or ""
    if email and email in by_email:
        row = by_email[email]
        return "email", row.external_id, row.name
    name = " ".join((person.get("name") or "").lower().split())
    if name and name in by_name:
        row = by_name[name]
        return "name", row.external_id, row.name
    return "none", None, None


async def sync_range(s: AsyncSession, tenant_id, start: dt.date, end: dt.date) -> dict:
    """Pull every user's past meetings and webinars in the window and replace their people."""
    state = await _sync_row(s, tenant_id)
    if not zoom.configured():
        state.last_error = "Zoom credentials are not set."
        return {"ok": False, "error": state.last_error}
    by_email, by_name = await _contacts(s, tenant_id)
    rules = state.rules
    counts = {"sessions": 0, "participants": 0, "matched": 0, "by_type": {}}
    topics: dict[str, str] = {}
    try:
        hosts = await zoom.users()
        print(f"[zoom_attendance] hosts={len(hosts)} window={start.isoformat()}..{end.isoformat()}",
              flush=True)
        for host in hosts:
            host_id = host.get("id")
            host_email = host.get("email")
            if not host_id:
                continue
            for kind, loader in (("meeting", zoom.meetings), ("webinar", zoom.webinars)):
                try:
                    items = await loader(host_id, start, end)
                except zoom.ZoomError as err:
                    if err.status in (400, 404):
                        continue
                    raise
                for item in items:
                    zoom_id = str(item.get("id") or "")
                    if not zoom_id:
                        continue
                    started = _parse_dt(item.get("start_time"))
                    if started and not (start <= started.date() <= end):
                        continue
                    try:
                        people, source = await zoom.participants(zoom_id, kind)
                    except zoom.ZoomError as err:
                        state.last_error = f"{item.get('topic')}: {err}"[:500]
                        continue
                    folded = dedupe_participants(people)
                    session = (await s.execute(select(AttendanceSession).where(
                        AttendanceSession.tenant_id == tenant_id,
                        AttendanceSession.kind == kind,
                        AttendanceSession.zoom_id == zoom_id))).scalar_one_or_none()
                    if session is None:
                        session = AttendanceSession(
                            tenant_id=tenant_id, zoom_id=zoom_id, kind=kind, topic=item.get("topic") or "Untitled")
                        s.add(session)
                        await s.flush()
                    session.zoom_uuid = item.get("uuid")
                    session.topic = (item.get("topic") or session.topic or "Untitled")[:400]
                    session.host_email = host_email
                    session.started_at = started
                    session.duration_minutes = item.get("duration")
                    session.source = source
                    await s.execute(delete(AttendancePerson).where(AttendancePerson.session_id == session.id))
                    for person in folded:
                        method, contact_id, contact_name = _match(person, by_email, by_name)
                        s.add(AttendancePerson(
                            tenant_id=tenant_id, session_id=session.id,
                            person_key=person["person_key"], email=person["email"], name=person["name"],
                            duration_seconds=person["duration_seconds"],
                            joined_at=person["joined_at"], left_at=person["left_at"],
                            contact_external_id=contact_id, contact_name=contact_name,
                            match_method=method))
                        if method != "none":
                            counts["matched"] += 1
                    label = classify_topic(session.topic, rules)
                    bucket = counts["by_type"].setdefault(label, {"sessions": 0, "participants": 0})
                    bucket["sessions"] += 1
                    bucket["participants"] += len(folded)
                    counts["sessions"] += 1
                    counts["participants"] += len(folded)
                    topics[session.topic] = label
        state.last_synced_at = dt.datetime.now(dt.timezone.utc)
        state.last_error = None
        state.last_stats = {
            **counts,
            "from": start.isoformat(), "to": end.isoformat(),
            "unmapped_topics": sorted(t for t, label in topics.items() if label == UNMAPPED),
        }
        print(f"[zoom_attendance] sessions={counts['sessions']} participants={counts['participants']} "
              f"by_type={counts['by_type']} unmapped={state.last_stats['unmapped_topics']}", flush=True)
        return {"ok": True, **state.last_stats}
    except Exception as err:  # noqa: BLE001 - the page and the log need the failure
        state.last_error = f"{type(err).__name__}: {err}"[:500]
        print(f"[zoom_attendance] FAILED {state.last_error}", flush=True)
        return {"ok": False, "error": state.last_error}


async def run_for_tenant(s: AsyncSession, tenant_id, *, backfill: bool = False) -> dict:
    tenant = await s.get(Tenant, tenant_id)
    state = await _sync_row(s, tenant_id)
    today = dt.date.today()
    # A window that only reaches back a few days is not the sprint backfill. The first
    # production pull saved a 3-day range and would otherwise never look further.
    frm = (state.last_stats or {}).get("from")
    needs_history = True
    if frm:
        try:
            needs_history = dt.date.fromisoformat(frm) > today - dt.timedelta(days=60)
        except ValueError:
            needs_history = True
    if backfill or state.backfilled_at is None or needs_history:
        start, end = backfill_bounds(today, (tenant.config or {}).get("fiscal_quarters") if tenant else None)
        result = await sync_range(s, tenant_id, start, end)
        # An empty first pull is not a finished backfill. The next boot tries the
        # long window again instead of only the last three days.
        if result.get("ok") and result.get("sessions", 0) > 0:
            state.backfilled_at = dt.datetime.now(dt.timezone.utc)
        return result
    return await sync_range(s, tenant_id, today - dt.timedelta(days=3), today)


def _typed(sessions, people_by_session, rules, label: str, start: dt.date, end: dt.date):
    out = []
    for session in sessions:
        when = session.started_at.date() if session.started_at else None
        if when is None or not (start <= when <= end):
            continue
        if classify_topic(session.topic, rules) != label:
            continue
        out.append((session, people_by_session.get(session.id, [])))
    return out


async def _load(s, tenant_id):
    state = (await s.execute(select(AttendanceSync).where(
        AttendanceSync.tenant_id == tenant_id))).scalar_one_or_none()
    sessions = list((await s.execute(select(AttendanceSession).where(
        AttendanceSession.tenant_id == tenant_id))).scalars())
    people = list((await s.execute(select(AttendancePerson).where(
        AttendancePerson.tenant_id == tenant_id))).scalars())
    by_session: dict = {}
    for person in people:
        by_session.setdefault(person.session_id, []).append(person)
    return state, sessions, by_session


async def edge_attendees(s, tenant_id, start: dt.date, end: dt.date) -> tuple[list[dict], bool]:
    """People at an Edge webinar in the window. The bool is whether Zoom has ever synced."""
    state, sessions, by_session = await _load(s, tenant_id)
    synced = bool(state and state.last_synced_at)
    if not synced:
        return [], False
    rules = state.rules if state else None
    seen = {}
    for _session, people in _typed(sessions, by_session, rules, "Edge Webinar", start, end):
        for person in people:
            key = person.person_key
            if key in seen:
                continue
            seen[key] = {
                "name": person.contact_name or person.name,
                "email": person.email,
                "reached": person.joined_at.date().isoformat() if person.joined_at else None,
                "detail": "Edge Webinar",
            }
    return list(seen.values()), True


async def forum_red_members(s, tenant_id, business_id) -> list[dict]:
    """Active Forum members who missed the three newest Forum sessions in a row."""
    state, sessions, by_session = await _load(s, tenant_id)
    if not state or not state.last_synced_at:
        return []
    forum = []
    for session in sessions:
        if classify_topic(session.topic, state.rules) != "Forum" or not session.started_at:
            continue
        emails = {(p.email or "").lower() for p in by_session.get(session.id, []) if p.email}
        forum.append((session.started_at, emails, session.topic))
    forum.sort(key=lambda row: row[0], reverse=True)
    recent = forum[:12]
    if len(recent) < 3:
        return []
    members = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "member",
        MetricRecord.status == "active"))).scalars()
    red = []
    for member in members:
        email = (member.email or "").strip().lower()
        if not email:
            continue
        missed = 0
        last = None
        for started, emails, topic in recent:
            if email in emails:
                break
            missed += 1
            last = topic
        if missed >= 3:
            red.append({"id": member.external_id, "client": member.name, "agent": None,
                        "date": None, "address": None, "side": None, "sale_price": None,
                        "detail": f"Missed {missed} in a row" + (f", last {last}" if last else "")})
    return red


async def edge_live_rate(s, tenant_id, business_id, week_end: dt.date):
    """Paid Edge members at an Edge webinar in the 30 days ending week_end, as a percent."""
    from .scorecard_resolvers import UNAVAILABLE
    state, sessions, by_session = await _load(s, tenant_id)
    if not state or not state.last_synced_at:
        return UNAVAILABLE
    start = week_end - dt.timedelta(days=29)
    attended = set()
    for _session, people in _typed(sessions, by_session, state.rules, "Edge Webinar", start, week_end):
        for person in people:
            if person.email:
                attended.add(person.email.lower())
    subs = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "stripe_legacy", MetricRecord.kind == "edge_subscription"))).scalars().all()
    paid = [r for r in subs if (r.status or "") in ("active", "trialing", "past_due")
            and not (r.amount is not None and float(r.amount) <= 0)
            and "comp" not in str((r.meta or {}).get("plan_name") or "").lower()
            and r.email]
    if not paid:
        paid = [r for r in (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
            MetricRecord.source == "ghl", MetricRecord.kind == "edge_member",
            MetricRecord.status == "active"))).scalars() if r.email]
    if not paid:
        return None
    hit = sum(1 for row in paid if row.email.lower() in attended)
    return round(hit / len(paid) * 100, 1)
