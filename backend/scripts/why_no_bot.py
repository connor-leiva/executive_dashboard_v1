r"""Why did a given call get no notetaker?

Answers one question against Recall's own records: was a bot ever CREATED for the call,
and if so what happened to it. Everything printed is structural - bot ids, timestamps,
meeting ids, status codes. No media URLs, no transcripts, no attendee names.

    $env:RECALL_API_KEY="..."
    .venv\Scripts\python.exe scripts\why_no_bot.py --day 2026-08-19

Optional --room 8021825042 narrows to one shared personal room.
"""
import argparse
import datetime as dt
import os
import re
import sys

import httpx


def meeting_id(b):
    mu = b.get("meeting_url")
    if isinstance(mu, dict):
        return str(mu.get("meeting_id") or "")
    m = re.search(r"/j/(\d+)", str(mu or ""))
    return m.group(1) if m else str(mu or "")


def status_of(b):
    recs = b.get("recordings") or []
    if recs:
        code = (recs[0].get("status") or {}).get("code")
        if code:
            return code
    ch = b.get("status_changes") or []
    return (ch[-1] or {}).get("code", "?") if ch else "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True, help="UTC date, YYYY-MM-DD")
    ap.add_argument("--room", default=None)
    a = ap.parse_args()

    key = os.getenv("RECALL_API_KEY")
    if not key:
        sys.exit("set RECALL_API_KEY first")
    base = f"https://{os.getenv('RECALL_REGION', 'us-west-2')}.recall.ai"
    h = {"Authorization": f"Token {key}"}

    bots, url, params, pages = [], f"{base}/api/v1/bot/", {"page_size": 200}, 0
    while url and pages < 10:
        r = httpx.get(url, headers=h, params=params, timeout=45)
        if r.status_code >= 300:
            sys.exit(f"FAIL {r.status_code}: {r.text[:300]}")
        p = r.json()
        bots += (p.get("results") if isinstance(p, dict) else p) or []
        url, params, pages = (p.get("next") if isinstance(p, dict) else None), None, pages + 1
    print(f"bots on account : {len(bots)}  (pages read: {pages})")

    day = a.day
    todays = [b for b in bots if str(b.get("join_at") or b.get("created_at") or "").startswith(day)]
    if a.room:
        todays = [b for b in todays if meeting_id(b) == a.room]
    todays.sort(key=lambda b: str(b.get("join_at") or b.get("created_at")))
    print(f"bots for {day}   : {len(todays)}" + (f"  (room {a.room})" if a.room else ""))
    print()
    print(f"  {'join_at (UTC)':<22}{'created_at (UTC)':<22}{'room':<14}{'status':<26}bot id")
    for b in todays:
        print(f"  {str(b.get('join_at'))[:19]:<22}{str(b.get('created_at'))[:19]:<22}"
              f"{meeting_id(b)[:12]:<14}{status_of(b):<26}{b.get('id')}")
    print()
    print("A call with NO row here never had a bot created - the failure is on the dashboard")
    print("side (call not synced in time, no meeting link, or the tick did not run).")
    print("A row that IS here but never reached in_call_recording means the bot was created")
    print("and could not get in - waiting room, passcode, or the host never admitted it.")


if __name__ == "__main__":
    main()
