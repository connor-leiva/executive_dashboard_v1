"""Chapters for a recorded call — topic segmentation over a stored transcript.

Boundaries and titles are not mechanically derivable. Speaker turns and silences are in the
timings, but "biggest awareness from day one" only comes from reading the conversation, so
this is a model pass. One call per transcript, once, at ~3-4k input tokens for a 20-minute
conversation — a fraction of a cent on Haiku, and it never runs twice for the same call.

What comes back is a NAVIGATION aid, not a record. The transcript is the evidence; a chapter
title is a label somebody can click. Nothing downstream should treat a title as a claim about
what was said, which is why titles never enter the searchable `text` column.

Off unless ANTHROPIC_API_KEY is set — like every other Claude path in this codebase.
"""
from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import CallTranscript

# Below this there is nothing to chapter: a no-show leaves a one-line transcript, and paying a
# model to tell us a 40-second call had one topic is waste with a straight face.
MIN_SEGMENTS = 12
MIN_DURATION_S = 180

# Long calls are trimmed by dropping
# the middle rather than the tail: chapter boundaries cluster at the start and end of a
# conversation, and a truncated tail loses "commitment and next steps" — the one chapter
# anybody actually wants to jump to.
MAX_LINES = 400
_HEAD, _TAIL = 200, 200

_SYSTEM = (
    "You segment sales-call transcripts into chapters for a review player. "
    "Return 3-7 chapters covering the whole call in order, starting at 0. "
    "Titles are 3-7 words, specific to THIS conversation, plain sentence case, no numbering "
    "and no quotes. Prefer what was actually discussed over generic labels like "
    "'introduction' or 'closing'. Use only the transcript; never invent a topic that is not "
    "in it. If the call has no discernible structure, return an empty list. "
    "Each line is prefixed with its offset in SECONDS in brackets, then the same offset as "
    "mm:ss for reading. Report every start as a whole number of SECONDS, copied from the "
    "bracketed value - never as mm:ss and never as a decimal."
)

_TOOL = {
    "name": "chapters",
    "description": "The chapter breakdown for this call.",
    "input_schema": {
        "type": "object",
        "properties": {
            "chapters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "integer",
                                   "description": "Whole seconds from the top of the call, "
                                                  "copied from the [Ns] prefix. 493, not 8:13."},
                        "title": {"type": "string", "description": "3-7 words, specific to this call."},
                    },
                    "required": ["start", "title"],
                },
            },
        },
        "required": ["chapters"],
    },
}


def _seconds(v) -> float | None:
    """Seconds from whatever the model returned. A "8:13" string is read as mm:ss rather than
    rejected, because that is the shape it reaches for when it is guessing."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and ":" in v:
        parts = v.strip().split(":")
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            return None
        return float(sum(n * 60 ** i for i, n in enumerate(reversed(nums))))
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clock(sec) -> str:
    s = max(0, int(sec or 0))
    return f"{s // 60}:{s % 60:02d}"


def transcript_lines(segments: list[dict]) -> str:
    """The transcript as timestamped lines, middle dropped when very long."""
    # Both units on every line, seconds first. Given only "8:13" the model answers 8.13 - a
    # decimal minute-second that reads as a valid number of seconds and quietly stacks every
    # chapter into the opening moments of the call. Observed on the first real generation.
    rows = [f"[{int(g.get('start') or 0)}s] {_clock(g.get('start'))} "
            f"{g.get('speaker') or 'Unknown'}: {(g.get('text') or '').strip()}"
            for g in segments if (g.get("text") or "").strip()]
    if len(rows) > MAX_LINES:
        rows = rows[:_HEAD] + [f"... [{len(rows) - _HEAD - _TAIL} lines omitted] ..."] + rows[-_TAIL:]
    return "\n".join(rows)


def _model() -> str:
    return settings.RECALL_CHAPTER_MODEL or settings.ASSISTANT_MODEL


def clean(raw, duration_s: float | None) -> list[dict]:
    """Sort, de-duplicate and bound what the model returned, then measure each chapter.

    The model is asked for start times; `len` is computed here from the NEXT boundary rather
    than taken from the model, because a length it invents can disagree with the start times
    it also invented and the scrubber would then disagree with the rail.
    """
    out = []
    for c in raw or []:
        start = _seconds(c.get("start"))
        if start is None:
            continue
        title = (c.get("title") or "").strip().strip('"')
        if not title or start < 0:
            continue
        if duration_s and start >= duration_s:
            continue                               # a boundary past the end is not a boundary
        out.append({"start": round(start, 1), "title": title[:120]})
    out.sort(key=lambda c: c["start"])
    # Collapse boundaries that land on top of each other; keep the first title.
    deduped = []
    for c in out:
        if deduped and c["start"] - deduped[-1]["start"] < 5:
            continue
        deduped.append(c)
    if not deduped:
        return []
    # A rail that does not start at 0 leaves the opening of the call unreachable.
    if deduped[0]["start"] > 0:
        deduped[0] = {**deduped[0], "start": 0.0}
    for i, c in enumerate(deduped):
        nxt = deduped[i + 1]["start"] if i + 1 < len(deduped) else (duration_s or c["start"])
        c["len"] = max(0, round(nxt - c["start"], 1))
    return deduped


async def generate(segments: list[dict], duration_s: float | None) -> list[dict] | None:
    """Chapters for one transcript. None on failure, [] when there is nothing to chapter."""
    if not settings.ANTHROPIC_API_KEY:
        return None
    if len(segments or []) < MIN_SEGMENTS or (duration_s or 0) < MIN_DURATION_S:
        return []                                  # settled, not failed - do not retry forever
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        resp = await client.messages.create(
            model=_model(),
            max_tokens=1024,
            system=_SYSTEM,
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "chapters"},
            messages=[{"role": "user", "content":
                       f"Call length: {_clock(duration_s)}.\n\nTranscript:\n{transcript_lines(segments)}"}],
        )
    except Exception as e:                         # noqa: BLE001 — never break the worker tick
        print(f"[chapters] generation failed: {type(e).__name__}: {e}", flush=True)
        return None
    def _covers(got):
        """A chaptered call whose LAST boundary sits in the opening minute is not a chapter
        list - it is the mm:ss-as-decimal failure wearing one. Settled rather than retried:
        the retry would spend money to be wrong again, and the log is the surfacing."""
        if not got or not duration_s or duration_s < MIN_DURATION_S:
            return True
        if max(c["start"] for c in got) >= duration_s * 0.25:
            return True
        print(f"[chapters] discarded {len(got)} chapters spanning "
              f"{max(c['start'] for c in got):.0f}s of a {duration_s:.0f}s call - "
              f"model likely returned mm:ss, not seconds", flush=True)
        return False

    for block in resp.content:
        if getattr(block, "type", None) == "tool_use":
            data = block.input
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except ValueError:
                    return None
            got = clean((data or {}).get("chapters"), duration_s)
            return got if _covers(got) else []
    return None


# One model call per transcript per tick. A transcript the generator keeps failing on is not
# going to start working, and every retry is billed — this was the only unbounded spend loop in
# the module, and it existed with a single tenant.
CHAPTER_MAX_ATTEMPTS = 5


async def generate_pending(s: AsyncSession, tenant_id, now: dt.datetime | None = None) -> dict:
    """Chapter ONE TENANT's stored transcripts that have not been through the generator yet.

    Keyed on chapters_at, not on chapters: a call that legitimately produced no chapters must
    not be re-sent to the model on every tick for the rest of its retention year.

    Scoped to a tenant so the batch is shared fairly rather than claimed by whoever has the
    biggest backlog, and bounded by an attempt counter because the failure path deliberately
    leaves chapters_at NULL — which, with no record of having tried, meant a failing transcript
    was re-sent to Anthropic every 15 minutes indefinitely.
    """
    if not settings.ANTHROPIC_API_KEY:
        return {}
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = (await s.execute(
        select(CallTranscript)
        .where(CallTranscript.tenant_id == tenant_id,
               CallTranscript.chapters_at.is_(None),
               CallTranscript.chapter_attempts < CHAPTER_MAX_ATTEMPTS)
        .order_by(CallTranscript.chapter_attempts, CallTranscript.fetched_at.desc())
        .limit(settings.RECALL_CHAPTER_BATCH))).scalars().all()
    stat: dict = {}
    for tr in rows:
        tr.chapter_attempts = (tr.chapter_attempts or 0) + 1   # count before the spend
        got = await generate(tr.segments or [], tr.duration_s)
        if got is None:
            stat["chapter_failed"] = stat.get("chapter_failed", 0) + 1
            if tr.chapter_attempts >= CHAPTER_MAX_ATTEMPTS:
                stat["chapter_gave_up"] = stat.get("chapter_gave_up", 0) + 1
            continue                               # leave chapters_at null so it retries later
        tr.chapters, tr.chapters_at = got, now
        stat["chaptered" if got else "chapter_empty"] = \
            stat.get("chaptered" if got else "chapter_empty", 0) + 1
    if rows:
        await s.commit()
    return stat
