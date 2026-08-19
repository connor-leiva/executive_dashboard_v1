"""What does Recall's bot LIST endpoint actually return?

adopt_existing_bots() matches a call to a bot using the bot's join_at, taken from the LIST
response. The retrieve endpoint definitely has join_at - but the list may return a reduced
shape, and if join_at is missing the matcher falls back to created_at. For the backfill bots
that is the evening they were created, nowhere near the calls they belong to, so nothing
would ever match and no recording would ever appear.

This prints the fields that matter, per bot, so that question is settled rather than guessed.
Values are structural (ids, timestamps, hostnames) - no media URLs, no transcripts, no names.

    .venv\\Scripts\\python.exe scripts\\list_bots.py
"""
import datetime as dt
import os
import sys

import httpx


def iso(v):
    return v if isinstance(v, str) else None


def main():
    key = os.getenv("RECALL_API_KEY")
    if not key:
        sys.exit("set RECALL_API_KEY first")
    base = f"https://{os.getenv('RECALL_REGION', 'us-west-2')}.recall.ai"

    r = httpx.get(f"{base}/api/v1/bot/", headers={"Authorization": f"Token {key}"},
                  params={"page_size": 200}, timeout=30)
    if r.status_code >= 300:
        sys.exit(f"FAIL {r.status_code}: {r.text[:300]}")
    payload = r.json()
    bots = payload.get("results") if isinstance(payload, dict) else payload
    bots = bots or []

    print(f"envelope    : {'dict with ' + str(sorted(payload.keys())) if isinstance(payload, dict) else 'bare list'}")
    print(f"bots listed : {len(bots)}")
    if isinstance(payload, dict) and payload.get("next"):
        print("             !! more pages exist - the matcher only reads the first")
    if not bots:
        return

    keys = sorted(bots[0].keys())
    print(f"per-bot keys: {keys}\n")
    print("  the two fields adoption depends on:")
    print(f"    join_at present on first bot   -> {'YES' if 'join_at' in keys else 'NO  <-- matcher will fall back to created_at'}")
    print(f"    meeting_url present            -> {'YES' if 'meeting_url' in keys else 'NO'}")
    print(f"    recordings present (status)    -> {'YES' if 'recordings' in keys else 'NO  <-- adopted calls stay at scheduled'}\n")

    print(f"  {'bot id':<38}{'join_at':<22}{'created_at':<22}status")
    for b in bots[:25]:
        recs = b.get("recordings") or []
        st = ((recs[0].get("status") or {}).get("code") if recs else None)
        if not st:
            ch = b.get("status_changes") or []
            st = (ch[-1] or {}).get("code") if ch else "?"
        print(f"  {str(b.get('id'))[:36]:<38}{str(iso(b.get('join_at')))[:20]:<22}"
              f"{str(iso(b.get('created_at')))[:20]:<22}{st}")

    # If join_at is absent the gap between created_at and the real call is what breaks it.
    if "join_at" not in keys:
        print("\n  join_at is MISSING from the list response. adopt_existing_bots falls back to")
        print("  created_at, which for the backfill bots is when the script ran - not when the")
        print("  call was. That gap is far wider than the 20-minute tolerance, so nothing matches.")


if __name__ == "__main__":
    main()
