"""Schedule Recall.ai recording bots for booked-but-not-yet-held Alignment Calls.

Two modes:
  --from-csv  bookings.csv   one-off backfill of appointments already on the calendar
  --from-ghl                 ongoing: read the meeting-link field off open opportunities

Dry-run by default. Nothing is sent to Recall until you pass --go.
Bot ids are recorded in scripts/.recall_bots.json so re-running never double-books.

CSV columns (header row required): when,name,rep,meeting_url[,status]
  when   = "2026-08-21 09:00"  (local, in --tz)
  status = optional; anything cancelled/rescheduled/no-show is skipped
"""
import argparse, csv, datetime as dt, json, os, pathlib, re, sys
from zoneinfo import ZoneInfo

import httpx

HERE = pathlib.Path(__file__).resolve().parent
LEDGER = HERE / ".recall_bots.json"

# ── the bot's display name is your DISCLOSURE surface: attendees see it in the participant
# list, so make it say who it belongs to and what it does. Legal reviews this string.
BOT_NAME = os.getenv("RECALL_BOT_NAME", "Spring — Call Notetaker")

# Recall bills per recorded hour, so a bot that sits in an empty personal room costs real
# money. These three timeouts are the whole cost story. Seconds.
AUTOMATIC_LEAVE = {
    "waiting_room_timeout": 900,     # rep never admits it -> give up after 15 min
    "noone_joined_timeout": 900,     # nobody shows -> don't record an empty room
    "everyone_left_timeout": 120,    # call ends -> stop promptly
}

TEST_PATTERNS = (r"\(test\)", r"team\+test@", r"\btest\b")


def is_test_row(name: str, extra: str = "") -> bool:
    blob = f"{name} {extra}".lower()
    return any(re.search(p, blob) for p in TEST_PATTERNS)


def resolve_url(raw: str, overrides: dict) -> str | None:
    """Vanity redirects aren't joinable. Map them to the real room, and drop any fragment."""
    if not raw:
        return None
    u = raw.strip().split("#")[0]
    for frm, to in overrides.items():
        if frm in u:
            return to
    if not re.search(r"(zoom\.us|meet\.google\.com|teams\.microsoft\.com|teams\.live\.com)", u):
        return None                                   # not a joinable meeting link
    return u


def load_ledger() -> dict:
    return json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}


def save_ledger(d: dict) -> None:
    LEDGER.write_text(json.dumps(d, indent=2), encoding="utf-8")


def create_bot(client, base, key, meeting_url, join_at_utc, label):
    payload = {
        "meeting_url": meeting_url,
        "bot_name": BOT_NAME,
        "join_at": join_at_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "recording_config": {
            "transcript": {"provider": {"meeting_captions": {}}},
            "automatic_leave": AUTOMATIC_LEAVE,
        },
    }
    r = client.post(f"{base}/api/v1/bot/", headers={"Authorization": f"Token {key}"}, json=payload)
    if r.status_code >= 300:
        # Print the API's own error verbatim - a wrong recording_config key shows up here,
        # which is faster than guessing at the schema.
        print(f"    !! {r.status_code} {r.text[:400]}")
        return None
    return r.json().get("id")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-csv")
    ap.add_argument("--check", action="store_true", help="verify the API key and region, then exit")
    ap.add_argument("--test-url", help="send ONE bot to this meeting now (coordinate first!)")
    ap.add_argument("--tz", default="America/Denver")
    ap.add_argument("--region", default=os.getenv("RECALL_REGION", "us-west-2"))
    ap.add_argument("--lead", type=int, default=15, help="minutes before start to join")
    ap.add_argument("--go", action="store_true", help="actually create bots")
    a = ap.parse_args()

    key = os.getenv("RECALL_API_KEY")
    if not key:
        sys.exit("set RECALL_API_KEY")
    base = f"https://{a.region}.recall.ai"
    tz, now = ZoneInfo(a.tz), dt.datetime.now(dt.timezone.utc)

    # Vanity / redirect links -> the real room. Add a line per rep who uses one.
    overrides = {"allisonhare.com/zoom": "https://us02web.zoom.us/j/9507511092"}

    # --check and --test-url keep the operator out of curl, whose quoting differs between
    # PowerShell and bash. Same command shape everywhere.
    if a.check:
        with httpx.Client(timeout=30) as c:
            try:
                r = c.get(f"{base}/api/v1/bot/", headers={"Authorization": f"Token {key}"})
            except Exception as e:
                sys.exit(f"FAIL  could not reach {base}  -  check --region "
                         f"(yours is '{a.region}').  {e}")
        if r.status_code == 200:
            n = len((r.json() or {}).get("results", []) if isinstance(r.json(), dict) else r.json() or [])
            print(f"PASS  key works, region '{a.region}' is right.  {n} bot(s) on the account so far.")
            return
        if r.status_code in (401, 403):
            sys.exit(f"FAIL  {r.status_code} - the key was rejected. Copy it again from the dashboard.")
        sys.exit(f"FAIL  {r.status_code} - probably the wrong region. {r.text[:200]}")

    if a.test_url:
        url = resolve_url(a.test_url, overrides)
        if not url:
            sys.exit(f"that doesn't look like a joinable meeting link: {a.test_url}")
        print(f"sending one bot to {url} now.")
        print("make sure whoever owns that room is expecting it - a personal room may have a")
        print("real call in it, and the bot would join uninvited.")
        if input("type YES to send: ").strip() != "YES":
            sys.exit("cancelled.")
        with httpx.Client(timeout=30) as c:
            bot = create_bot(c, base, key, url, dt.datetime.now(dt.timezone.utc), "test")
        print(f"bot {bot} sent - watch the meeting for it." if bot else "no bot created (see error above).")
        return

    if not a.from_csv:
        sys.exit("nothing to do: pass --check, --test-url, or --from-csv")

    rows = list(csv.DictReader(open(a.from_csv, encoding="utf-8-sig")))
    ledger, made, skipped = load_ledger(), 0, 0

    with httpx.Client(timeout=30) as client:
        for row in rows:
            name = (row.get("name") or "").strip()
            start = dt.datetime.strptime(row["when"].strip(), "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            url = resolve_url(row.get("meeting_url", ""), overrides)
            key_id = f"{name}|{start.isoformat()}"

            status = (row.get("status") or "").strip().lower()
            why = None
            if is_test_row(name, row.get("meeting_url", "")):     why = "test row"
            elif any(w in status for w in ("cancel", "resched", "no show", "noshow")):
                why = f"status={row.get('status','').strip()}"
            elif key_id in ledger:                                why = f"already booked ({ledger[key_id]})"
            elif start <= now:                                    why = "already started"
            elif not url:                                         why = f"no joinable link ({row.get('meeting_url','')[:40]})"
            if why:
                print(f"  skip  {start:%a %m/%d %H:%M}  {name[:26]:<26} - {why}")
                skipped += 1
                continue

            # Recall wants >=10 min of lead time for a guaranteed on-time join.
            join_at = max(start - dt.timedelta(minutes=a.lead), now + dt.timedelta(minutes=11))
            join_utc = join_at.astimezone(dt.timezone.utc)
            if not a.go:
                print(f"  PLAN  {start:%a %m/%d %H:%M}  {name[:26]:<26} join {join_utc:%m/%d %H:%MZ}  {url[:46]}")
                made += 1
                continue
            bot = create_bot(client, base, key, url, join_utc, name)
            if bot:
                ledger[key_id] = bot
                save_ledger(ledger)
                print(f"  BOT   {start:%a %m/%d %H:%M}  {name[:26]:<26} -> {bot}")
                made += 1

    verb = "would create" if not a.go else "created"
    print(f"\n{verb} {made} bot(s); skipped {skipped}.  ledger: {LEDGER}")
    if not a.go:
        print("re-run with --go to actually schedule them.")


if __name__ == "__main__":
    main()
