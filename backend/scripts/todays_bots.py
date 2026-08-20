r"""Which of today's calls have a notetaker, and why the rest don't.

why_no_bot.py lists what Recall holds. call_row.py shows one row. Neither answers the question
somebody actually asks in the morning — "six calls are on the board, why do I see two bots?" —
because that needs both sides at once: the booked calls from GHL and the scheduled bots from
Recall, lined up against each other.

Every call is reported with a verdict, and an unbooked call gets the REASON rather than a
blank. The reasons are different problems with different owners: a missing Appointment Link is
GHL's, a vanity redirect is a config line, and "not due yet" is not a problem at all.

Joinability is decided by the app's OWN resolve_meeting_url, so this reports what the
scheduler would do rather than a second opinion that could disagree with it.

    $env:RECALL_API_KEY="..."
    .venv\Scripts\python.exe scripts\todays_bots.py                 # today, launch-local
    .venv\Scripts\python.exe scripts\todays_bots.py --date 2026-08-21

GHL credentials come from .probe.env. Prints names and meeting-room ids; no media URLs.
"""
import argparse
import asyncio
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx                                                        # noqa: E402

from app.config import settings                                     # noqa: E402
from app.integrations.ghl import (get_opportunities, get_opportunity,  # noqa: E402
                                  opp_custom_values)
from app.services.recall import meeting_key, resolve_meeting_url    # noqa: E402
from app.services.sales_desk import parse_call_time, _tz            # noqa: E402

PIPELINE = "ukAWqE1qrXKdzhLoLb8Z"          # be Collective Experience #1 Sales
CF_TIME = "KwLMcn4wIH2BsEEA1sjy"
CF_LINK = "ZUbOB5DHwKt0kkVkz6SR"
CF_REP = "uKA9N4O3f6dvBclSopEB"
CF_OUT = "9hyXDUwQmTYULCeUVM7x"


def _load_probe_env():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".probe.env")
    if not os.path.exists(path):
        sys.exit(".probe.env not found — GHL credentials are required")
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)


def _room(url) -> str:
    """The room, or a short tag for a link that is not one. Kept narrow so a long vanity URL
    cannot shove the verdict column off the end of the line - the verdict is the point."""
    if not url:
        return "-"
    m = re.search(r"/j/(\d+)", str(url)) or re.search(r"meet\.google\.com/([a-z-]+)", str(url))
    if m:
        return m.group(1)[:13]
    host = re.sub(r"^https?://(www\.)?", "", str(url)).split("/")[0]
    return host[:13]


def _bot_room(b) -> str:
    mu = b.get("meeting_url")
    if isinstance(mu, dict):
        return str(mu.get("meeting_id") or "")
    return _room(mu)


def _ampm(d) -> str:
    """%-I is glibc-only and %#I is Windows-only; this runs on both."""
    h = d.hour % 12 or 12
    return f"{h}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


def _parse_iso(v):
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


async def _bots():
    key = os.getenv("RECALL_API_KEY")
    if not key:
        sys.exit("set RECALL_API_KEY first")
    base = f"https://{os.getenv('RECALL_REGION', settings.RECALL_REGION)}.recall.ai"
    out, url, params, pages = [], f"{base}/api/v1/bot/", {"page_size": 200}, 0
    async with httpx.AsyncClient(timeout=45) as c:
        while url and pages < 10:
            r = await c.get(url, headers={"Authorization": f"Token {key}"}, params=params)
            if r.status_code >= 300:
                sys.exit(f"Recall FAIL {r.status_code}: {r.text[:200]}")
            p = r.json()
            out += (p.get("results") if isinstance(p, dict) else p) or []
            url, params, pages = (p.get("next") if isinstance(p, dict) else None), None, pages + 1
    return out


async def _calls(day: dt.date, tz):
    _load_probe_env()
    tok, loc = os.environ["GHL_BC_TOKEN"], os.environ["GHL_BC_LOCATION_ID"]
    ops = await get_opportunities(tok, loc)
    # A call booked days ago can still be today's, so the cheap updatedAt filter has to be
    # generous. Anything touched in the last three weeks is a candidate.
    cutoff = (day - dt.timedelta(days=21)).isoformat()
    live = [o for o in ops if o.get("pipelineId") == PIPELINE and (o.get("updatedAt") or "") >= cutoff]
    sem = asyncio.Semaphore(6)

    async def one(o):
        async with sem:                            # values only exist on the DETAIL endpoint
            cv = opp_custom_values(await get_opportunity(tok, loc, o["id"]) or {})
        raw = cv.get(CF_TIME)
        if not raw:
            return None
        when, _ = parse_call_time(raw, tz)
        if not when:
            return None
        return {"name": o.get("name"), "raw": raw, "when": when, "rep": cv.get(CF_REP),
                "outcome": cv.get(CF_OUT), "link": cv.get(CF_LINK)}

    rows = [r for r in await asyncio.gather(*[one(o) for o in live]) if r]
    local = [r for r in rows if r["when"].astimezone(tz).date() == day]
    return sorted(local, key=lambda r: r["when"])


def _verdict(c, bots, now, tz):
    """Why this call does or does not have a notetaker."""
    resolved = resolve_meeting_url(c["link"])
    want = meeting_key(resolved)
    lead = dt.timedelta(minutes=settings.RECALL_LEAD_MINUTES)
    for b in bots:
        j = _parse_iso(b.get("join_at")) or _parse_iso(b.get("created_at"))
        if not j or not want or _bot_room(b) != want:
            continue
        if abs((c["when"] - lead) - j) <= dt.timedelta(minutes=20):
            return "BOT", f"joins {_ampm(j.astimezone(tz))}", str(b.get("id"))[:8]
    if not c["link"]:
        return "NO LINK", "Appointment Link empty in GHL - nothing to send a bot to", ""
    if not resolved:
        return "UNRESOLVED", f"{_room(c['link'])} is a landing page, not a room - needs RECALL_URL_OVERRIDES", ""
    if c["when"] <= now:
        return "MISSED", "call already started and no bot was ever created", ""
    mins = (c["when"] - now).total_seconds() / 60
    horizon = settings.RECALL_LEAD_MINUTES + settings.RECALL_TICK_MINUTES + 10
    if mins > horizon:
        return "PENDING", f"due in {int(mins)} min; books inside {horizon} min of start", ""
    return "LATE", f"inside the {horizon}-min window and still unbooked", ""


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD in the launch timezone (default: today)")
    ap.add_argument("--tz", default="America/Denver")
    ap.add_argument("--overrides", default=None,
                    help="RECALL_URL_OVERRIDES as deployed, e.g. \"vanity.com/zoom=https://real\". "
                         "Without it a vanity link reads UNRESOLVED here while it resolves fine "
                         "in prod.")
    a = ap.parse_args()
    if a.overrides:
        settings.RECALL_URL_OVERRIDES = a.overrides
    tz = _tz(a.tz)
    now = dt.datetime.now(dt.timezone.utc)
    day = dt.date.fromisoformat(a.date) if a.date else now.astimezone(tz).date()

    calls, bots = await asyncio.gather(_calls(day, tz), _bots())
    same_day = [b for b in bots
                if (_parse_iso(b.get("join_at")) or _parse_iso(b.get("created_at")))
                and (_parse_iso(b.get("join_at")) or _parse_iso(b.get("created_at"))
                     ).astimezone(tz).date() == day]
    print(f"{day} ({a.tz})   calls booked: {len(calls)}   bots on the account for that day: {len(same_day)}")
    if not settings.RECALL_URL_OVERRIDES:
        print("NOTE: RECALL_URL_OVERRIDES is empty HERE, which is not the same as empty in prod.")
        print("      Pass --overrides with the deployed value; otherwise every vanity link")
        print("      reads UNRESOLVED in this report while booking perfectly well in Railway.")
    print()
    print(f"  {'time':<9}{'who':<26}{'rep':<13}{'room':<15}{'verdict':<12}why")
    counts = {}
    for c in calls:
        verdict, why, bot = _verdict(c, bots, now, tz)
        counts[verdict] = counts.get(verdict, 0) + 1
        rep = (c["rep"] or "—").split("@")[0][:11]
        who = re.sub(r"\s*[-–]\s*[+(\d].*$", "", c["name"] or "").strip()[:24]
        print(f"  {_ampm(c['when'].astimezone(tz)):<9}{who:<26}{rep:<13}"
              f"{_room(c['link']):<15}{verdict:<12}{why}")
    print()
    print("  " + "   ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    extra = len(same_day) - counts.get("BOT", 0)
    if extra > 0:
        print(f"  {extra} bot(s) that day match no booked call — backfill, a duplicate, or a")
        print("  call whose time moved after the bot was made. why_no_bot.py lists them.")


if __name__ == "__main__":
    asyncio.run(main())
