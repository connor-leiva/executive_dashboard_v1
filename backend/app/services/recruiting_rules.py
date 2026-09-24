"""ULRG Recruiting — the rule engine (RECRUITING-SPEC §6).

Plain rules, configured in Settings, run on a tick. No AI: the user asked for rules, and the
`why` line beside each item is rendered from structured facts rather than generated. That is not
a limitation to be lifted later -- a person is about to text a recruit because this line told
them to, and it has to be checkable.

SHAPED LIKE follow_ups.py: DEFAULTS, BOUNDS, `clean_settings(raw, strict=)`, and pure functions
underneath with no HTTP and no session. `evaluate()` takes facts and returns items; `build_queue()`
is the thin part that loads the facts, calls it, and writes the difference. Everything worth
testing is in the pure half.

TWO PROPERTIES CARRY THE WHOLE DESIGN:

  * IDEMPOTENT. Every item carries a `window_key` naming the occasion it belongs to, and the
    table's unique constraint is (tenant, rule, candidate, window). A rule that fires every five
    minutes produces one row per occasion, not 288 a day.
  * SELF-CLEARING. An item is a claim about local tables. When the claim stops holding it becomes
    `auto_cleared`, whoever made it stop -- which is how a text sent from inside GHL clears
    somebody's Axcion list without them touching Axcion.
"""
from __future__ import annotations

import datetime as dt
import uuid
import zoneinfo
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import (
    Integration, RecruitingActivity, RecruitingAppointment, RecruitingCandidate,
    RecruitingQueueItem, RecruitingSeat,
)

# The groups the rules reason about by name. A workspace maps its own stage ids onto these in
# Settings; a group it does not map simply never triggers the rules that name it.
G_APPT_SET = "Appointment set"
G_MET = "Met"
G_OFFER = "Offer out"
G_SIGNED = "Signed"
G_NURTURE = "Nurture"

OUTBOUND = ("sms_out", "email_out", "call_out")
INBOUND = ("sms_in", "email_in", "call_in")

DEFAULTS = {
    "new_lead_untouched": {"on": True, "minutes": 60},
    "appt_24h": {"on": True, "from_hours": 18, "to_hours": 30},
    "met_no_next_step": {"on": True, "hours": 48},
    "offer_out_stale": {"on": True, "days": 5},
    "no_touch_7d": {"on": True, "days": 7},
    "stage_14d": {"on": True, "days": 14},
    "appt_set_no_event": {"on": True, "hours": 24},
}
BOUNDS = {
    "new_lead_untouched": {"minutes": (5, 1440)},
    "appt_24h": {"from_hours": (2, 72), "to_hours": (4, 96)},
    "met_no_next_step": {"hours": (4, 336)},
    "offer_out_stale": {"days": (1, 60)},
    "no_touch_7d": {"days": (2, 90)},
    "stage_14d": {"days": (3, 180)},
    "appt_set_no_event": {"hours": (2, 336)},
}
RULE_LABELS = {
    "new_lead_untouched": "New lead, no contact",
    "appt_24h": "Appointment tomorrow",
    "met_no_next_step": "Met, no next step",
    "offer_out_stale": "Offer out, gone quiet",
    "no_touch_7d": "No contact in a week",
    "stage_14d": "Stuck in stage",
    "appt_set_no_event": "Booked, no appointment",
}
# The plain-text drafts the drawer offers. Merge fields only, never generated (§5.5).
TEMPLATE_KEYS = ("text", "email_subject", "email_body")
DEFAULT_TEMPLATES = {
    "text": "Hi {first} — {owner_first} here. Wanted to check in on where things stand. "
            "Any questions I can answer?",
    "email_subject": "Following up, {first}",
    "email_body": "Hi {first},\n\nJust following up. Happy to answer anything still open.\n\n"
                  "{owner_first}",
}
MERGE_FIELDS = ("first", "owner_first", "calendar_owner")


# ── settings ─────────────────────────────────────────────────────────────────────────────────

def clean_rules(raw: dict | None, *, strict: bool = False) -> dict:
    """The rule settings, bounded. Refuses under `strict` (the UI), clamps otherwise (the database).

    A value typed into a form comes back with the field named, because silently clamping teaches
    somebody the field does nothing. A value already stored is clamped, because it has to run.
    """
    raw = raw if isinstance(raw, dict) else {}
    out = {k: dict(v) for k, v in DEFAULTS.items()}
    for key, spec in out.items():
        incoming = raw.get(key)
        if not isinstance(incoming, dict):
            continue
        if "on" in incoming:
            spec["on"] = bool(incoming["on"])
        for field_name, (lo, hi) in BOUNDS.get(key, {}).items():
            if field_name not in incoming:
                continue
            try:
                value = int(incoming[field_name])
            except (TypeError, ValueError):
                if strict:
                    raise ValueError(f"{key}.{field_name} must be a whole number")
                continue
            if not (lo <= value <= hi):
                if strict:
                    raise ValueError(f"{key}.{field_name} must be between {lo} and {hi}")
                value = max(lo, min(value, hi))
            spec[field_name] = value
    # A window that runs backwards would silently match nothing, which reads as "the rule is off".
    win = out["appt_24h"]
    if win["from_hours"] >= win["to_hours"]:
        if strict:
            raise ValueError("appt_24h.from_hours must be less than to_hours")
        out["appt_24h"] = dict(DEFAULTS["appt_24h"])
    return out


def clean_templates(raw: dict | None, *, strict: bool = False) -> dict:
    """The drafts, with their merge fields checked.

    An unknown merge field is REFUSED rather than left to render as a literal `{brokerage}` in a
    text message to a recruit. That is the one failure here that reaches a stranger.
    """
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT_TEMPLATES)
    for key in TEMPLATE_KEYS:
        if key not in raw:
            continue
        value = ("" if raw[key] is None else str(raw[key])).strip()
        if not value:
            out[key] = ""
            continue
        for token in _tokens(value):
            if token not in MERGE_FIELDS:
                if strict:
                    raise ValueError(f"templates.{key}: unknown merge field {{{token}}}. "
                                     f"Available: {', '.join(MERGE_FIELDS)}")
                value = value.replace("{" + token + "}", "")
        out[key] = value[:2000]
    return out


def _tokens(text: str) -> list[str]:
    out, depth, current = [], 0, ""
    for ch in text:
        if ch == "{":
            depth, current = 1, ""
        elif ch == "}" and depth:
            out.append(current.strip())
            depth = 0
        elif depth:
            current += ch
    return out


def render_template(text: str, **values) -> str:
    out = text or ""
    for key in MERGE_FIELDS:
        out = out.replace("{" + key + "}", str(values.get(key) or "").strip())
    return " ".join(out.split(" ")).strip()


# ── facts ────────────────────────────────────────────────────────────────────────────────────

def _aware(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


@dataclass
class Facts:
    """Everything the rules read, loaded once. Deliberately plain data: the whole engine below is
    testable without a database, which is what makes each rule's positive AND negative case cheap
    enough that both get written."""
    candidates: list = field(default_factory=list)
    appointments: list = field(default_factory=list)
    activity_by_candidate: dict = field(default_factory=dict)
    seats_by_id: dict = field(default_factory=dict)
    sdr_seat_id: uuid.UUID | None = None
    group_owner: dict = field(default_factory=dict)      # group label -> "sdr" | "team_leader"
    # When this workspace began observing the recruiting location at all -- the first sync.
    # Two rules reason from the ABSENCE of activity, and absence only means something after this
    # moment: the poll reads conversations forward from where it started and never backfills, so
    # for a candidate last contacted before we connected there is no row and never will be.
    # Without this, "nobody has reached out yet" is a claim about our own blindness.
    watch_start: dt.datetime | None = None

    def acts(self, candidate_id) -> list:
        return self.activity_by_candidate.get(str(candidate_id), [])

    def last(self, candidate_id, kinds) -> dt.datetime | None:
        times = [_aware(a.occurred_at) for a in self.acts(candidate_id) if a.kind in kinds]
        return max(times) if times else None


@dataclass
class Item:
    rule_key: str
    candidate_id: uuid.UUID
    window_key: str
    owner_seat_id: uuid.UUID | None
    due_at: dt.datetime | None
    why: str
    primary_action: str


def _day(value: dt.datetime | None, tz) -> str:
    return _aware(value).astimezone(tz).date().isoformat() if value else "none"


def _stage_owner(facts: Facts, cand) -> uuid.UUID | None:
    """Whose item this is. The candidate's own owner when it has one; otherwise whoever the stage
    group belongs to -- which for the SDR's stages is the SDR, and is why an unassigned new lead
    does not fall on the floor."""
    if cand.owner_seat_id:
        return cand.owner_seat_id
    if facts.group_owner.get(cand.stage_group) == "sdr":
        return facts.sdr_seat_id
    return None


# ── the rules ────────────────────────────────────────────────────────────────────────────────
#
# Each is a pure function of (facts, candidate, now, tz, config) returning an Item or None. Kept
# separate rather than folded into one pass so that each one's negative case is a test somebody
# can read, and so turning one off is a config flag rather than an edit.

def rule_new_lead_untouched(facts, cand, now, tz, cfg):
    if cand.stage_group in (G_SIGNED, G_NURTURE) or (cand.status or "open") != "open":
        return None
    created = _aware(cand.created_at_src) or _aware(cand.first_seen_at)
    if created is None or (now - created) < dt.timedelta(minutes=cfg["minutes"]):
        return None
    # It arrived before we were watching, so it is not a new lead to us and its silence is not
    # evidence. This is the difference between 202 items and 19 on a freshly connected location.
    if facts.watch_start is not None and created < facts.watch_start:
        return None
    if facts.last(cand.id, OUTBOUND) is not None:
        return None
    return Item("new_lead_untouched", cand.id, _day(created, tz),
                facts.sdr_seat_id or _stage_owner(facts, cand), created + dt.timedelta(minutes=cfg["minutes"]),
                f"Came in {_ago(now - created)} and nobody has reached out yet.", "text")


def rule_appt_24h(facts, cand, now, tz, cfg):
    for appt in facts.appointments:
        if str(appt.candidate_id or "") != str(cand.id):
            continue
        start = _aware(appt.start_at)
        if start is None:
            continue
        ahead = start - now
        if not (dt.timedelta(hours=cfg["from_hours"]) <= ahead <= dt.timedelta(hours=cfg["to_hours"])):
            continue
        if (appt.status or "").lower() in ("confirmed", "showed", "cancelled"):
            continue
        # An inbound message since the booking IS the confirmation, whatever the status says.
        booked = _aware(appt.created_at_src)
        replied = facts.last(cand.id, INBOUND)
        if booked and replied and replied > booked:
            continue
        return Item("appt_24h", cand.id, str(appt.event_id), appt.seat_id,
                    start - dt.timedelta(hours=cfg["from_hours"]),
                    f"Meeting {_when(start, tz)} and they have not confirmed.", "text")
    return None


def rule_met_no_next_step(facts, cand, now, tz, cfg):
    if cand.stage_group != G_MET or (cand.status or "open") != "open":
        return None
    entered = _aware(cand.entered_stage_at)
    if entered is None or (now - entered) < dt.timedelta(hours=cfg["hours"]):
        return None
    if cand.ghl_task_id:
        return None
    if _has_future_appointment(facts, cand, now):
        return None
    return Item("met_no_next_step", cand.id, _day(entered, tz), _stage_owner(facts, cand), entered
                + dt.timedelta(hours=cfg["hours"]),
                f"Met {_ago(now - entered)} ago with no next step set and nothing on the calendar.",
                "email")


def rule_offer_out_stale(facts, cand, now, tz, cfg):
    if cand.stage_group != G_OFFER or (cand.status or "open") != "open":
        return None
    entered = _aware(cand.entered_stage_at)
    if entered is None or (now - entered) < dt.timedelta(days=cfg["days"]):
        return None
    replied = facts.last(cand.id, INBOUND)
    if replied is not None and (now - replied) < dt.timedelta(days=cfg["days"]):
        return None
    tail = (f"No reply since {replied.astimezone(tz).strftime('%A')}." if replied
            else "They have not replied at all.")
    return Item("offer_out_stale", cand.id, _day(entered, tz), _stage_owner(facts, cand),
                entered + dt.timedelta(days=cfg["days"]),
                f"Offer has been out {_ago(now - entered)}. {tail}", "call")


def rule_no_touch_7d(facts, cand, now, tz, cfg):
    if cand.stage_group in (G_SIGNED, G_NURTURE) or (cand.status or "open") != "open":
        return None
    acts = [_aware(a.occurred_at) for a in facts.acts(cand.id)]
    floored = False
    if acts:
        last = max(acts)                    # real evidence, whatever it says
    else:
        # No activity row at all, which does NOT mean nothing happened -- it means we have not
        # looked, because the poll only reads forward from the day we connected. The silence we
        # can honestly claim starts there.
        last = _aware(cand.created_at_src) or _aware(cand.first_seen_at)
        if facts.watch_start is not None and (last is None or last < facts.watch_start):
            last, floored = facts.watch_start, True
    if last is None or (now - last) < dt.timedelta(days=cfg["days"]):
        return None
    # Say which one it is. "Nothing has happened in 9 months" reads as a fact about the candidate;
    # when it is really a fact about how long we have been watching, the list has to admit that or
    # nobody can trust the rest of it.
    why = (f"Nothing since we connected {_ago(now - last)} ago." if floored
           else f"Nothing has happened here in {_ago(now - last)}.")
    # The window is the silence itself, so one item per quiet spell rather than one a day.
    return Item("no_touch_7d", cand.id, _day(last, tz), _stage_owner(facts, cand),
                last + dt.timedelta(days=cfg["days"]), why, "text")


def rule_stage_14d(facts, cand, now, tz, cfg):
    if cand.stage_group in (G_SIGNED, G_NURTURE) or (cand.status or "open") != "open":
        return None
    entered = _aware(cand.entered_stage_at)
    if entered is None or (now - entered) < dt.timedelta(days=cfg["days"]):
        return None
    return Item("stage_14d", cand.id, _day(entered, tz), _stage_owner(facts, cand),
                entered + dt.timedelta(days=cfg["days"]),
                f"Has been in {cand.stage_group or 'this stage'} for {_ago(now - entered)}.", "call")


def rule_appt_set_no_event(facts, cand, now, tz, cfg):
    if cand.stage_group != G_APPT_SET or (cand.status or "open") != "open":
        return None
    entered = _aware(cand.entered_stage_at)
    if entered is None or (now - entered) < dt.timedelta(hours=cfg["hours"]):
        return None
    if _has_future_appointment(facts, cand, now):
        return None
    return Item("appt_set_no_event", cand.id, _day(entered, tz),
                facts.sdr_seat_id or _stage_owner(facts, cand),
                entered + dt.timedelta(hours=cfg["hours"]),
                "Marked as booked, but there is no appointment on anybody's calendar.", "book")


# `signed` deliberately produces NO item. §6 lists it in the rule table because it is a rule the
# engine evaluates, but its output is a stage_event and a Scorecard resolver, not a task: nobody
# needs a to-do that says somebody already signed.

RULES = [
    ("new_lead_untouched", rule_new_lead_untouched),
    ("appt_24h", rule_appt_24h),
    ("met_no_next_step", rule_met_no_next_step),
    ("offer_out_stale", rule_offer_out_stale),
    ("no_touch_7d", rule_no_touch_7d),
    ("stage_14d", rule_stage_14d),
    ("appt_set_no_event", rule_appt_set_no_event),
]


def _has_future_appointment(facts, cand, now) -> bool:
    return any(str(a.candidate_id or "") == str(cand.id) and _aware(a.start_at)
               and _aware(a.start_at) > now
               and (a.status or "").lower() not in ("cancelled", "noshow", "no-show")
               for a in facts.appointments)


def _when(when: dt.datetime, tz) -> str:
    """"Thursday at 9:00 am", built without strftime's `%-d` / `%-I`.

    Those are a POSIX extension: they render on Railway and raise ValueError on Windows, where
    this suite runs. A format string that works in production and crashes in the tests is the
    worst arrangement of the two.
    """
    local = when.astimezone(tz)
    hour = local.hour % 12 or 12
    meridiem = "am" if local.hour < 12 else "pm"
    return f"{local.strftime('%A')} at {hour}:{local.minute:02d} {meridiem}"


def _ago(delta: dt.timedelta) -> str:
    """How long, in the words somebody would use. Never "0 days"."""
    minutes = int(delta.total_seconds() // 60)
    if minutes < 90:
        return f"{max(1, minutes)} min"
    hours = minutes // 60
    if hours < 36:
        return f"{hours} hours"
    days = hours // 24
    if days < 14:
        return f"{days} days"
    return f"{days // 7} weeks"


def evaluate(facts: Facts, now: dt.datetime, tz, rules_cfg: dict) -> list[Item]:
    """Every item the rules claim right now. Pure."""
    out: list[Item] = []
    for key, fn in RULES:
        cfg = rules_cfg.get(key) or DEFAULTS[key]
        if not cfg.get("on", True):
            continue
        for cand in facts.candidates:
            try:
                item = fn(facts, cand, now, tz, cfg)
            except Exception:  # noqa: BLE001 - one bad row must not empty somebody's whole list
                item = None
            if item is not None:
                out.append(item)
    return out


# ── due labels, business-local ───────────────────────────────────────────────────────────────

def due_label(due_at: dt.datetime | None, now: dt.datetime, tz) -> dict:
    """"2 days late" / "Today" / "Tomorrow", decided in the brokerage's own day.

    Computed here rather than in the browser because a viewer in another timezone would otherwise
    see a different word for the same fact -- and because the whole tab's rule is that the server
    decides and the client draws.
    """
    if due_at is None:
        return {"label": None, "tone": "mute", "at": None}
    due_at = _aware(due_at)
    today = now.astimezone(tz).date()
    due_day = due_at.astimezone(tz).date()
    payload = {"at": due_at.isoformat()}
    # LATE IS A MOMENT, NOT A DAY. Comparing dates made something due at 4pm read "Today" at 7pm
    # -- which is the one case where the chip most needs to say otherwise, and the reason §6 words
    # it "20 hrs late" rather than naming a day.
    if due_at < now:
        hours = int((now - due_at).total_seconds() // 3600)
        days = (today - due_day).days
        label = (f"{max(1, int((now - due_at).total_seconds() // 60))} min late" if hours < 1
                 else f"{hours} hrs late" if hours < 48
                 else f"{days} days late")
        return {**payload, "label": label, "tone": "late"}
    if due_day == today:
        return {**payload, "label": "Today", "tone": "today"}
    if due_day == today + dt.timedelta(days=1):
        return {**payload, "label": "Tomorrow", "tone": "later"}
    local = due_at.astimezone(tz)
    return {**payload, "label": f"{local.strftime('%a')} {local.day} {local.strftime('%b')}",
            "tone": "later"}


def next_business_morning(now: dt.datetime, tz) -> dt.datetime:
    """Snooze target: the next business day at 06:00 local, which is when the morning build runs.
    Friday's snooze lands on Monday, not on Saturday when nobody is recruiting."""
    local = now.astimezone(tz)
    day = local.date() + dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return dt.datetime.combine(day, dt.time(6, 0), tzinfo=tz)


def business_tz():
    try:
        return zoneinfo.ZoneInfo(settings.BILLING_TIMEZONE or "America/Denver")
    except Exception:  # noqa: BLE001
        return zoneinfo.ZoneInfo("America/Denver")


# ── the thin part: load, evaluate, write the difference ──────────────────────────────────────

async def load_facts(s: AsyncSession, tenant_id, integ: Integration | None) -> Facts:
    cands = list((await s.execute(select(RecruitingCandidate).where(
        RecruitingCandidate.tenant_id == tenant_id))).scalars().all())
    appts = list((await s.execute(select(RecruitingAppointment).where(
        RecruitingAppointment.tenant_id == tenant_id))).scalars().all())
    acts = list((await s.execute(select(RecruitingActivity).where(
        RecruitingActivity.tenant_id == tenant_id))).scalars().all())
    seats = list((await s.execute(select(RecruitingSeat).where(
        RecruitingSeat.tenant_id == tenant_id, RecruitingSeat.active.is_(True)))).scalars().all())

    by_cand: dict = defaultdict(list)
    for a in acts:
        if a.candidate_id:
            by_cand[str(a.candidate_id)].append(a)
    groups = (integ.config or {}).get("recruiting_stage_groups") if integ else []
    owner_of = {}
    for row in groups or []:
        try:
            owner_of[row[0]] = row[1]
        except (TypeError, IndexError):
            continue
    sdr = next((x for x in seats if x.role == "sdr"), None)
    # The earliest candidate we have ever seen IS the moment we started watching: `first_seen_at`
    # is server-defaulted on insert, so the whole first sync shares one timestamp and every later
    # arrival is after it. Derived rather than stored, so it needs no migration and cannot drift.
    seen = [_aware(c.first_seen_at) for c in cands if c.first_seen_at]
    return Facts(candidates=cands, appointments=appts, activity_by_candidate=dict(by_cand),
                 seats_by_id={str(x.id): x for x in seats},
                 sdr_seat_id=sdr.id if sdr else None, group_owner=owner_of,
                 watch_start=min(seen) if seen else None)


async def build_queue(s: AsyncSession, tenant_id) -> dict:
    """Reconcile the queue with what the rules currently claim. Returns a small summary.

    RECONCILE, not rebuild. Items the rules still claim are left exactly as they are -- including
    the ones somebody has already pressed Done on, which must not come back. Items the rules have
    stopped claiming become `auto_cleared`, because the work happened; that is the same code path
    whether it happened in Axcion or in GHL, which is the point.
    """
    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id,
        Integration.provider == "ghl_recruiting"))).scalars().first()
    if integ is None or not (integ.config or {}).get("pipeline_id"):
        return {"claimed": 0, "opened": 0, "auto_cleared": 0, "skipped": "not configured"}

    tz = business_tz()
    now = dt.datetime.now(dt.timezone.utc)
    facts = await load_facts(s, tenant_id, integ)
    claimed = evaluate(facts, now, tz, clean_rules((integ.config or {}).get("rules")))

    existing = list((await s.execute(select(RecruitingQueueItem).where(
        RecruitingQueueItem.tenant_id == tenant_id))).scalars().all())
    by_key = {(i.rule_key, str(i.candidate_id), i.window_key): i for i in existing}
    claimed_keys = set()
    opened = 0

    for item in claimed:
        key = (item.rule_key, str(item.candidate_id), item.window_key)
        claimed_keys.add(key)
        row = by_key.get(key)
        if row is None:
            s.add(RecruitingQueueItem(
                tenant_id=tenant_id, candidate_id=item.candidate_id, rule_key=item.rule_key,
                window_key=item.window_key[:64], owner_seat_id=item.owner_seat_id,
                state="open", due_at=item.due_at, why=(item.why or "")[:400],
                primary_action=item.primary_action))
            opened += 1
            continue
        if row.state == "snoozed" and row.snoozed_until and _aware(row.snoozed_until) <= now:
            row.state = "open"                      # the snooze ran out; it is today's problem again
        if row.state == "open":
            # The facts move under a live item: a stale offer gets staler, and the line says so.
            row.why = (item.why or "")[:400]
            row.owner_seat_id = item.owner_seat_id
            row.due_at = item.due_at

    cleared = 0
    for key, row in by_key.items():
        if key in claimed_keys or row.state not in ("open", "snoozed"):
            continue
        row.state = "auto_cleared"
        row.cleared_by = "rule"
        row.cleared_at = now
        cleared += 1

    await s.commit()
    return {"claimed": len(claimed), "opened": opened, "auto_cleared": cleared}
