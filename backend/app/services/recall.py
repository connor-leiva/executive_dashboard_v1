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

import base64
import datetime as dt
import hashlib
import hmac
import re

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import CallTranscript, SalesCall

# Statuses we store. Recall emits more; these are the ones that mean something operationally.
ST_SCHEDULED, ST_WAITING, ST_RECORDING = "scheduled", "waiting", "recording"
ST_DONE, ST_FAILED, ST_ENDED = "done", "failed", "ended"

# Recall's status_change events -> our vocabulary. Anything unmapped is stored verbatim so a
# new event name shows up in the data instead of vanishing.
_STATUS_MAP = {
    "joining_call": ST_SCHEDULED, "in_waiting_room": ST_WAITING,
    "in_call_not_recording": ST_WAITING, "recording_permission_denied": ST_FAILED,
    # call_ended says the bot LEFT the call - it is not evidence that anything was recorded.
    # A bot that sat in the waiting room and was never admitted emits it too, so mapping it to
    # "done" reported that call as recorded and silenced the no-recording warning for exactly
    # the failure the warning exists to catch.
    "in_call_recording": ST_RECORDING, "call_ended": ST_ENDED, "done": ST_DONE,
    "analysis_done": ST_DONE, "fatal": ST_FAILED, "media_expired": ST_FAILED,
}

# A bot that sits in an empty personal room bills by the hour, so these bound the waste.
#
# Two things learned from a REAL bot's payload (2026-08-19), both of which had silently made
# this block a no-op: automatic_leave is a TOP-LEVEL field on the bot, not part of
# recording_config, and everyone_left_timeout takes an object rather than a number. Sent the
# wrong way, Recall accepts the request and quietly applies its own defaults - the values
# looked configured here while 1200/1200/2 were actually in force.
AUTOMATIC_LEAVE = {
    "waiting_room_timeout": 900,                  # rep never admits it -> give up (default 1200)
    "noone_joined_timeout": 900,                  # nobody shows -> don't record an empty room
    "everyone_left_timeout": {"timeout": 60},     # call ends -> stop promptly (default 2s)
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


# ── webhook authenticity ──────────────────────────────────────────────────────────────────
# Recall signs webhooks with the workspace "Verification Secret" (whsec_...) using the
# Standard Webhooks scheme: HMAC-SHA256 over "{id}.{timestamp}.{body}", base64, sent as a
# space-separated list of "v1,<sig>". It does NOT send a bearer token or a custom header, so
# comparing a shared secret would reject every real delivery.
_SIG_TOLERANCE_S = 300          # reject replays; Standard Webhooks recommends 5 minutes


def _hdr(headers, name: str) -> str:
    """Both spellings are in the wild: svix-id (original) and webhook-id (the standard)."""
    return headers.get(f"svix-{name}") or headers.get(f"webhook-{name}") or ""


def verify_signature(secret: str, headers, body: bytes, now: dt.datetime | None = None) -> bool:
    """True if this really came from Recall, unmodified and recent."""
    msg_id, ts, sigs = _hdr(headers, "id"), _hdr(headers, "timestamp"), _hdr(headers, "signature")
    if not (secret and msg_id and ts and sigs):
        return False
    try:
        sent = int(ts)
    except ValueError:
        return False
    now = now or dt.datetime.now(dt.timezone.utc)
    if abs(int(now.timestamp()) - sent) > _SIG_TOLERANCE_S:
        return False
    try:
        key = base64.b64decode(secret.split("_", 1)[1] if secret.startswith("whsec_") else secret)
    except Exception:                                   # noqa: BLE001 — malformed secret
        return False
    signed = f"{msg_id}.{ts}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    # A webhook may carry several signatures during a secret rotation; any valid one passes.
    for part in sigs.split():
        _, _, val = part.partition(",")
        if val and hmac.compare_digest(val, expected):
            return True
    return False


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
        },
        "automatic_leave": AUTOMATIC_LEAVE,       # top level - see the note on AUTOMATIC_LEAVE
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


# Zoom puts the id after /j/, Google Meet uses the whole final path segment. Anything else
# falls back to the URL itself, lowercased.
_ZOOM_ID = re.compile(r"zoom\.us/(?:j|s|w)/(\d+)")
_MEET_ID = re.compile(r"meet\.google\.com/([a-z0-9-]+)", re.I)


def meeting_key(value) -> str | None:
    """One comparable identity for a meeting, from either side of the match.

    Recall returns meeting_url as an OBJECT - {meeting_id, platform} - while we store the
    full joinable URL from GHL. Comparing those directly can never be equal, so every call
    that had a link skipped every bot and 114 unlinked calls matched nothing. Reduce both to
    the meeting id instead.
    """
    if isinstance(value, dict):                      # a bot's meeting_url
        for k in ("meeting_id", "id"):
            v = value.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip().lower()
        value = value.get("meeting_url") or value.get("url")
    if not isinstance(value, str) or not value.strip():
        return None
    u = value.strip()
    m = _ZOOM_ID.search(u) or _MEET_ID.search(u)
    if m:
        return m.group(1).lower()
    return u.split("?")[0].rstrip("/").lower()


def _bot_url(b: dict) -> str | None:
    """The bot's meeting identity, comparable with meeting_key() of a stored URL."""
    return meeting_key(b.get("meeting_url"))


def _parse_iso(v) -> dt.datetime | None:
    try:
        d = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def bot_status(b: dict) -> str:
    """Our status for a bot, read from its payload.

    Adoption has to set this from the bot itself. The webhook only fires on NEW status
    changes, so a call that finished before we ever knew about the bot would otherwise sit
    at "scheduled" forever and never show its recording.
    """
    for rec in (b.get("recordings") or []):
        code = ((rec.get("status") or {}).get("code") or "")
        if not code:
            continue
        mapped = _STATUS_MAP.get(code, code[:32])
        # "done" is a claim that a recording exists, so only make it when media is actually
        # attached. A recording row can complete with nothing in it.
        # Presence, not truthiness: an empty dict is a real media entry, and the live payload
        # carries audio_mixed = None for absent media.
        if mapped == ST_DONE and (rec.get("media_shortcuts") or {}).get("video_mixed") is None:
            return ST_ENDED
        return mapped
    changes = b.get("status_changes") or []
    if changes:
        code = (changes[-1] or {}).get("code") or ""
        if code:
            return _STATUS_MAP.get(code, code[:32])
    return ST_SCHEDULED


async def adopt_existing_bots(client: httpx.AsyncClient, rows: list,
                              taken: set | None = None) -> int:
    """Link bots Recall already has to the calls they belong to.

    Bots booked outside the app - the one-off backfill in scripts/recall_bots.py, or a
    retried tick - are invisible to the database, so without this the scheduler would send a
    SECOND bot to the same call. Recall is the source of truth for what exists, not a local
    ledger, so ask it.

    Three reps run every call through ONE static personal room, so a URL match is ambiguous
    and the join time has to identify the booking. Matching is greedy on the smallest gap and
    each bot is claimed once, because otherwise two calls 30 minutes apart in the same room
    both "match" the same bot and one of them silently goes unrecorded.
    """
    try:
        r = await client.get(f"{_base()}/api/v1/bot/",
                             headers={"Authorization": f"Token {settings.RECALL_API_KEY}"},
                             params={"page_size": 200})
        r.raise_for_status()
    except Exception as e:                                  # noqa: BLE001 — never block scheduling
        print(f"[recall] could not list existing bots: {type(e).__name__}: {e}", flush=True)
        return 0
    payload = r.json()
    bots = payload.get("results") if isinstance(payload, dict) else payload
    if isinstance(payload, dict) and payload.get("next"):
        # Only the first page is read; say so rather than silently ignoring older bots.
        print("[recall] more bots exist beyond the first page - raise page_size", flush=True)

    # Tolerance sits below the 30-minute spacing those shared rooms actually run at, so a
    # neighbouring call can never be mistaken for this one.
    tol = dt.timedelta(minutes=20)
    # Every bot is booked to join BEFORE its call, so the expected join time is
    # call_at - lead. Ranking on abs(call_at - join_at) scored a correct pairing at `lead`
    # rather than 0, which put a neighbouring call in a shared room at the same distance -
    # at lead 15 with 30-minute spacing the two tie exactly and insertion order decides.
    lead = dt.timedelta(minutes=settings.RECALL_LEAD_MINUTES)
    pairs = []
    for sc in rows:
        if sc.recall_bot_id:
            continue
        call_at = sc.call_time_utc
        if not call_at:
            continue
        call_at = call_at if call_at.tzinfo else call_at.replace(tzinfo=dt.timezone.utc)
        # A call booked before GHL carried the Appointment Link has no stored URL. Fall back
        # to matching on time alone: the greedy one-bot-one-call pass below still keeps two
        # neighbouring calls from claiming the same bot.
        want = meeting_key(resolve_meeting_url(sc.meeting_url))
        for b in (bots or []):
            if not b.get("id"):
                continue
            if want and _bot_url(b) != want:
                continue
            j = _parse_iso(b.get("join_at")) or _parse_iso(b.get("created_at"))
            if not j:
                continue
            # A bot joins at or before its call. One scheduled AFTER this call started
            # belongs to a later booking, so never let it win on raw distance.
            if j > call_at + dt.timedelta(minutes=2):
                continue
            gap = abs((call_at - lead) - j)
            if gap <= tol:
                # Prefer a URL-confirmed pairing when both are candidates for the same bot.
                rank = gap + (dt.timedelta(0) if want else dt.timedelta(seconds=1))
                pairs.append((rank, str(b["id"]), sc))

    by_id = {str(b.get("id")): b for b in (bots or []) if b.get("id")}
    print(f"[recall] adoption: {len(rows)} unlinked call(s) vs {len(bots or [])} bot(s) on the "
          f"account, {len(pairs)} candidate pairing(s)", flush=True)
    # Seed with bots ALREADY linked to other calls. used_bots alone only dedupes within one
    # pass, so on a later tick a neighbouring call could re-claim a bot that already belongs
    # to someone else - two rows sharing a bot id, one client's Watch link playing another's
    # conversation, and the robbed call never booking a bot of its own.
    adopted, used_bots, used_calls = 0, set(taken or ()), set()
    for _, bot_id, sc in sorted(pairs, key=lambda x: x[0]):
        if bot_id in used_bots or id(sc) in used_calls:
            continue
        sc.recall_bot_id = bot_id
        sc.recording_status = bot_status(by_id.get(bot_id) or {})
        if sc.recording_status in (ST_RECORDING, ST_DONE) and sc.recording_at is None:
            sc.recording_at = dt.datetime.now(dt.timezone.utc)
        used_bots.add(bot_id)
        used_calls.add(id(sc))
        adopted += 1
        print(f"[recall] adopted existing bot {bot_id} for {sc.contact_name!r}", flush=True)
    return adopted


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
    # Recall only guarantees an on-time join when join_at is >=10 min out, so a call has to be
    # picked up while it is still (lead + 10) minutes away. With a horizon of just lead+tick, a
    # call entering the window would be floored to now+11 - i.e. the bot arrives AFTER the call
    # started, on every single booking. The extra 10 minutes is what makes the lead real.
    horizon = now + lead + dt.timedelta(minutes=settings.RECALL_TICK_MINUTES + 10)

    # Two different questions, two different windows.
    #
    # Adoption looks BACKWARD as well: bots created outside the app (the one-off backfill)
    # belong to calls that have already happened, and if we only ever look forward those
    # recordings are orphaned permanently - the webhook reports a bot id nothing recognises
    # and the call shows no recording, forever.
    lookback = now - dt.timedelta(days=settings.RECALL_ADOPT_LOOKBACK_DAYS)
    # Adoption also reaches FORWARD past the scheduling horizon. The backfill booked bots for
    # calls up to nine days out; without this they stay unlinked until each call is minutes
    # away, so the Desk shows no recording status for any of them in the meantime.
    adopt_until = now + dt.timedelta(days=settings.RECALL_ADOPT_LOOKAHEAD_DAYS)
    # NOTE: adoption deliberately does NOT require meeting_url. Every booking made before the
    # Appointment Link field existed in GHL has none, and those are exactly the calls the
    # backfill bots belong to - requiring it made them permanently unmatchable.
    candidates = (await s.execute(
        select(SalesCall).where(
            SalesCall.is_current.is_(True),
            SalesCall.recall_bot_id.is_(None),
            SalesCall.call_time_utc.is_not(None),
            SalesCall.call_time_utc >= lookback,
            SalesCall.call_time_utc <= adopt_until,
        ))).scalars().all()
    # Scheduling is the narrow case: forward only, inside the horizon, and it DOES need a URL
    # - you cannot send a bot without somewhere to send it. Adoption's window is far wider,
    # so this must re-apply the horizon rather than inherit it.
    def _at(c):
        t = c.call_time_utc
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    rows = [c for c in candidates if c.meeting_url and now < _at(c) <= horizon]

    # Report what was CONSIDERED, not just what happened. "0 bots created" reads identically
    # whether there was nothing to do, the job never ran, or every match silently failed.
    stat: dict = {"candidates": len(candidates), "schedulable": len(rows)}
    if not candidates:
        return stat
    async with httpx.AsyncClient(timeout=30) as client:
        # Before booking anything, find out what Recall already has - for past calls too.
        taken = set((await s.execute(
            select(SalesCall.recall_bot_id).where(
                SalesCall.tenant_id == candidates[0].tenant_id,
                SalesCall.recall_bot_id.is_not(None)))).scalars())
        adopted = await adopt_existing_bots(client, candidates, taken)
        if adopted:
            stat["recall_bots_adopted"] = adopted
            await s.commit()
        for sc in rows:
            if sc.recall_bot_id:                    # just adopted - already covered
                continue
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


async def fresh_recording_url(bot_id: str) -> str | None:
    """A playable link for a bot's recording, fetched fresh.

    Recall hands back SIGNED S3 URLs that expire after 5 hours, so a stored one is a link
    that works this afternoon and 404s tomorrow. Their guidance is to fetch a new one every
    time someone wants to watch, which is what this does. The signature is self-contained, so
    the resulting link plays for anyone who has it - no Recall account needed, which matters
    when the whole team shares one login.
    """
    if not (settings.RECALL_API_KEY and bot_id):
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{_base()}/api/v1/bot/{bot_id}/",
                            headers={"Authorization": f"Token {settings.RECALL_API_KEY}"})
            r.raise_for_status()
            body = r.json()
    except Exception as e:                              # noqa: BLE001 — surface as "no link"
        print(f"[recall] could not refresh media url for {bot_id}: {type(e).__name__}: {e}", flush=True)
        return None

    # Current shape: recordings[].media_shortcuts.video_mixed.data.download_url.
    # Older bots expose a flat video_url. Try both rather than pin one layout.
    for rec in (body.get("recordings") or []):
        data = (((rec.get("media_shortcuts") or {}).get("video_mixed") or {}).get("data") or {})
        for k in ("download_url", "url"):
            if isinstance(data.get(k), str):
                return data[k]
    for k in ("video_url", "video_mixed_url"):
        if isinstance(body.get(k), str):
            return body[k]
    return None


# ── transcripts ───────────────────────────────────────────────────────────────────────────
# Shape confirmed against a real call (2026-08-19), not guessed: the payload is a LIST of
# utterances, each carrying participant {name, is_host, ...} and a words[] array whose
# entries hold text plus start/end timestamps. Despite the field name a "word" is a caption
# chunk - 35 characters over 4.4 seconds in the sample - because the provider is
# meeting_captions. `relative` is seconds from the top of the recording, which is exactly
# what the player needs to seek.
_MERGE_GAP_S = 3.0        # join a speaker's consecutive chunks into readable paragraphs...
_MERGE_MAX_S = 45.0       # ...but never so long that seeking loses precision


def parse_transcript(doc) -> dict:
    """Recall's transcript payload -> segments the UI can render and seek to."""
    if not isinstance(doc, list):
        doc = (doc or {}).get("transcript") or (doc or {}).get("results") or []
    raw = []
    for item in doc:
        if not isinstance(item, dict):
            continue
        part = item.get("participant") or {}
        speaker = (part.get("name") or "").strip() or "Unknown"
        words = [w for w in (item.get("words") or []) if isinstance(w, dict)]
        if not words:
            continue

        def rel(w, key):
            v = (w.get(key) or {})
            return v.get("relative") if isinstance(v, dict) else None

        start = rel(words[0], "start_timestamp")
        end = rel(words[-1], "end_timestamp")
        text = " ".join((w.get("text") or "").strip() for w in words).strip()
        if not text or start is None:
            continue
        raw.append({"speaker": speaker, "is_host": bool(part.get("is_host")),
                    "start": float(start), "end": float(end if end is not None else start),
                    "text": text})

    raw.sort(key=lambda x: x["start"])
    segments: list = []
    for seg in raw:
        prev = segments[-1] if segments else None
        if (prev and prev["speaker"] == seg["speaker"]
                and seg["start"] - prev["end"] <= _MERGE_GAP_S
                and seg["end"] - prev["start"] <= _MERGE_MAX_S):
            prev["end"] = seg["end"]
            prev["text"] = f"{prev['text']} {seg['text']}".strip()
        else:
            segments.append(dict(seg))

    # Talk ratio comes free: seconds of speech per person, and is_host marks the rep.
    speakers: dict = {}
    for seg in segments:
        e = speakers.setdefault(seg["speaker"], {"seconds": 0.0, "is_host": seg["is_host"]})
        e["seconds"] = round(e["seconds"] + max(0.0, seg["end"] - seg["start"]), 1)
    for seg in segments:
        seg["start"], seg["end"] = round(seg["start"], 2), round(seg["end"], 2)

    return {
        "segments": segments,
        "text": "\n".join(f"{s['speaker']}: {s['text']}" for s in segments),
        "speakers": speakers,
        "duration_s": int(round(max((s["end"] for s in segments), default=0))),
    }


async def fetch_transcript(bot_id: str) -> dict | None:
    """Download and parse one bot's transcript, or None if there isn't one yet."""
    if not (settings.RECALL_API_KEY and bot_id):
        return None
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(f"{_base()}/api/v1/bot/{bot_id}/",
                            headers={"Authorization": f"Token {settings.RECALL_API_KEY}"})
            r.raise_for_status()
            body = r.json()
            for rec in (body.get("recordings") or []):
                tr = ((rec.get("media_shortcuts") or {}).get("transcript") or {})
                if ((tr.get("status") or {}).get("code")) != "done":
                    continue
                url = (tr.get("data") or {}).get("download_url")
                if not url:
                    continue
                t = await c.get(url)
                t.raise_for_status()
                return parse_transcript(t.json())
    except Exception as e:                              # noqa: BLE001 — surface as "not yet"
        print(f"[recall] transcript fetch failed for {bot_id}: {type(e).__name__}: {e}", flush=True)
    return None


async def store_transcripts(s: AsyncSession, now: dt.datetime | None = None) -> dict:
    """Fetch and store transcripts for finished recordings that do not have one yet.

    Retention is written at insert time, never inferred later: a verbatim record of a client
    conversation should not outlive its policy because nobody remembered to apply one.
    """
    if not settings.RECALL_API_KEY:
        return {}
    now = now or dt.datetime.now(dt.timezone.utc)
    have = set((await s.execute(select(CallTranscript.sales_call_id))).scalars())
    rows = (await s.execute(
        select(SalesCall).where(
            SalesCall.recall_bot_id.is_not(None),
            SalesCall.recording_status == ST_DONE,
        ))).scalars().all()
    todo = [c for c in rows if c.id not in have][:settings.RECALL_TRANSCRIPT_BATCH]
    stat: dict = {}
    for sc in todo:
        parsed = await fetch_transcript(sc.recall_bot_id)
        if not parsed or not parsed.get("segments"):
            stat["transcript_not_ready"] = stat.get("transcript_not_ready", 0) + 1
            continue
        s.add(CallTranscript(
            tenant_id=sc.tenant_id, sales_call_id=sc.id, recall_bot_id=sc.recall_bot_id,
            segments=parsed["segments"], text=parsed["text"], speakers=parsed["speakers"],
            duration_s=parsed["duration_s"],
            purge_after=now + dt.timedelta(days=settings.RECALL_TRANSCRIPT_RETAIN_DAYS)))
        stat["transcripts_stored"] = stat.get("transcripts_stored", 0) + 1
    if stat.get("transcripts_stored"):
        await s.commit()
    return stat


async def purge_expired_transcripts(s: AsyncSession, now: dt.datetime | None = None) -> int:
    """Delete transcripts past their retention date. Connor set one year."""
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = (await s.execute(
        select(CallTranscript).where(
            CallTranscript.purge_after.is_not(None),
            CallTranscript.purge_after <= now))).scalars().all()
    for t in rows:
        await s.delete(t)
    if rows:
        await s.commit()
        print(f"[recall] purged {len(rows)} transcript(s) past retention", flush=True)
    return len(rows)


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
