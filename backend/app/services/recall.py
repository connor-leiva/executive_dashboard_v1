"""Recall.ai recording bots for Alignment Calls.

A bot joins by MEETING URL alone — no access to anyone's Zoom or Google account — which is
why it works across a team of 1099 reps on six different personal accounts.

Two entry points:
  schedule_due_bots()   the worker calls this every few minutes; it books a bot for each
                        call that is about to start and doesn't have one yet.
  apply_bot_status()    the webhook calls this when Recall reports a bot's status changed.

Everything about WHEN and WHETHER to record lives here; the caller just supplies a session.
"""
from __future__ import annotations

import datetime as dt
import re

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import SalesCall

# Statuses we store. Recall emits more; these are the ones that mean something operationally.
ST_SCHEDULED, ST_WAITING, ST_RECORDING = "scheduled", "waiting", "recording"
ST_DONE, ST_FAILED = "done", "failed"

# Recall's status_change events -> our vocabulary. Anything unmapped is stored verbatim so a
# new event name shows up in the data instead of vanishing.
_STATUS_MAP = {
    "joining_call": ST_SCHEDULED, "in_waiting_room": ST_WAITING,
    "in_call_not_recording": ST_WAITING, "recording_permission_denied": ST_FAILED,
    "in_call_recording": ST_RECORDING, "call_ended": ST_DONE, "done": ST_DONE,
    "analysis_done": ST_DONE, "fatal": ST_FAILED, "media_expired": ST_FAILED,
}

# A bot that sits in an empty personal room bills by the hour. These three timeouts are the
# whole cost story. Seconds.
AUTOMATIC_LEAVE = {
    "waiting_room_timeout": 900,      # rep never admits it -> give up
    "noone_joined_timeout": 900,      # nobody shows -> don't record an empty room
    "everyone_left_timeout": 120,     # call ends -> stop promptly
}

_JOINABLE = re.compile(r"(zoom\.us|meet\.google\.com|teams\.microsoft\.com|teams\.live\.com)")
_TEST_ROW = re.compile(r"\(test\)|\btest\b", re.I)


def _overrides() -> dict:
    """Vanity redirects -> the real room, as "from=to,from=to" in RECALL_URL_OVERRIDES.
    A rep whose calendar says allisonhare.com/zoom sends a landing page, not a meeting."""
    out = {}
    for pair in (settings.RECALL_URL_OVERRIDES or "").split(","):
        frm, _, to = pair.partition("=")
        if frm.strip() and to.strip():
            out[frm.strip()] = to.strip()
    return out


def resolve_meeting_url(raw: str | None) -> str | None:
    """The joinable URL for a booking, or None if there isn't one."""
    if not raw:
        return None
    u = raw.strip().split("#")[0]
    for frm, to in _overrides().items():
        if frm in u:
            return to
    return u if _JOINABLE.search(u) else None


def _base() -> str:
    return f"https://{settings.RECALL_REGION or 'us-west-2'}.recall.ai"


async def create_bot(client: httpx.AsyncClient, meeting_url: str, join_at: dt.datetime) -> tuple[str | None, str]:
    """Book one bot. Returns (bot_id, error) — never raises, so one bad row can't stall the tick."""
    payload = {
        "meeting_url": meeting_url,
        "bot_name": settings.RECALL_BOT_NAME or "Call Notetaker",
        "join_at": join_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "recording_config": {
            "transcript": {"provider": {"meeting_captions": {}}},
            "automatic_leave": AUTOMATIC_LEAVE,
        },
    }
    try:
        r = await client.post(f"{_base()}/api/v1/bot/",
                              headers={"Authorization": f"Token {settings.RECALL_API_KEY}"},
                              json=payload)
    except Exception as e:                                  # noqa: BLE001 — network blip
        return None, f"{type(e).__name__}: {e}"
    if r.status_code >= 300:
        return None, f"{r.status_code} {r.text[:200]}"
    return (r.json() or {}).get("id"), ""


async def schedule_due_bots(s: AsyncSession, now: dt.datetime | None = None) -> dict:
    """Book a bot for every call starting soon that doesn't have one.

    The window is deliberately wide on the near side (Recall wants >=10 min of lead time to
    guarantee an on-time join) and short on the far side, so a call rescheduled tomorrow is
    picked up on a later tick with its NEW time rather than being booked now and stranded.
    """
    if not settings.RECALL_API_KEY:
        return {}
    now = now or dt.datetime.now(dt.timezone.utc)
    lead = dt.timedelta(minutes=settings.RECALL_LEAD_MINUTES)
    horizon = now + lead + dt.timedelta(minutes=settings.RECALL_TICK_MINUTES)

    rows = (await s.execute(
        select(SalesCall).where(
            SalesCall.is_current.is_(True),
            SalesCall.recall_bot_id.is_(None),
            SalesCall.meeting_url.is_not(None),
            SalesCall.call_time_utc.is_not(None),
            SalesCall.call_time_utc > now,                  # never chase a call already underway
            SalesCall.call_time_utc <= horizon,
        ))).scalars().all()

    stat: dict = {}
    if not rows:
        return stat
    async with httpx.AsyncClient(timeout=30) as client:
        for sc in rows:
            if _TEST_ROW.search(sc.contact_name or ""):
                stat["recall_test_skipped"] = stat.get("recall_test_skipped", 0) + 1
                continue
            url = resolve_meeting_url(sc.meeting_url)
            if not url:
                stat["recall_no_joinable_url"] = stat.get("recall_no_joinable_url", 0) + 1
                continue
            call_at = sc.call_time_utc
            call_at = call_at if call_at.tzinfo else call_at.replace(tzinfo=dt.timezone.utc)
            # Recall needs join_at >=10 min out; if the call is closer than that, ask for the
            # soonest it will honour rather than silently booking a bot that arrives late.
            join_at = max(call_at - lead, now + dt.timedelta(minutes=11))
            bot, err = await create_bot(client, url, join_at)
            if bot:
                sc.recall_bot_id, sc.recording_status = bot, ST_SCHEDULED
                stat["recall_bots_created"] = stat.get("recall_bots_created", 0) + 1
            else:
                stat["recall_create_failed"] = stat.get("recall_create_failed", 0) + 1
                print(f"[recall] bot failed for {sc.contact_name!r}: {err}", flush=True)
    await s.commit()
    return stat


async def apply_bot_status(s: AsyncSession, bot_id: str, status: str,
                           recording_url: str | None = None) -> bool:
    """Record what Recall told us about one bot. Returns False if we don't know the bot."""
    sc = (await s.execute(
        select(SalesCall).where(SalesCall.recall_bot_id == bot_id))).scalars().first()
    if sc is None:
        return False
    sc.recording_status = _STATUS_MAP.get(status, status[:32])
    if recording_url:
        sc.recording_url = recording_url[:1024]
    if sc.recording_status in (ST_RECORDING, ST_DONE) and sc.recording_at is None:
        sc.recording_at = dt.datetime.now(dt.timezone.utc)
    await s.commit()
    return True
