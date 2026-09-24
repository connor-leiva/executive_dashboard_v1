"""ULRG Recruiting — Phase 0 discovery probe (RECRUITING-SPEC.md §9, Phase 0).

Answers D1-D6 and D8 from the live recruiting location, and settles the four **VERIFY** items the
spec refuses to let code depend on:

  V1  Do Private Integration Token calls return the rate-limit headers the docs describe for
      OAuth apps? (§5.3 — the token bucket is built on those headers.)
  V2  `GET /calendars/{id}/free-slots` — the real parameter names and response shape. (§5.4)
  V3  `GET /conversations/search` params, and the message `type` values actually in use, since
      Dials and Conversations are counted off them. (§5.6)
  V4  Does the location's calendar send its own confirmations? Inferred from the calendar's
      notification settings; confirm with a location admin either way. (§5.4, booking copy)

READ-ONLY BY CONSTRUCTION. There is exactly one request helper and it hardcodes GET. This probe
runs against a brokerage's live recruiting pipeline before any write path exists, so "it only
reads" is a property of the code rather than a promise in a docstring.

Credentials come from backend/.probe.env (git-ignored) or the environment. Claude never opens that
file; add the two keys yourself:

    GHL_RECRUITING_TOKEN=pit-...
    GHL_RECRUITING_LOCATION=...

The token needs the Phase 1 READ scopes only (§5.1): contacts.readonly, opportunities.readonly,
conversations.readonly, conversations/message.readonly, calendars.readonly,
calendars/events.readonly, users.readonly, locations.readonly, locations/customFields.readonly.
A 403 on any call below prints the scope that is missing rather than a stack trace -- that is the
list you hand back to the location admin.

PII is masked. No raw contact record, message body or email address is written to disk.

Run:  cd backend && ./.venv/Scripts/python.exe probe_ghl_recruiting.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from collections import Counter

import httpx

try:                                    # keep the Windows console from dying on unicode
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

BASE = "https://services.leadconnectorhq.com"
VERSION = "2021-07-28"                  # pinned repo-wide; a v3 move is a separate change (§5.4)
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ghl_recruiting_probe.json")

# Which scope a 403 on each call is telling you about. Printed instead of the raw error, because
# "403" on its own sends somebody back to the docs for twenty minutes.
SCOPE_HINTS = {
    "/locations/": "locations.readonly",
    "/locations/{id}/customFields": "locations/customFields.readonly",
    "/users/": "users.readonly",
    "/opportunities/pipelines": "opportunities.readonly",
    "/opportunities/search": "opportunities.readonly",
    "/calendars/": "calendars.readonly",
    "/calendars/events": "calendars/events.readonly",
    "free-slots": "calendars/events.readonly",
    "/conversations/search": "conversations.readonly",
    "/conversations/": "conversations/message.readonly",
    "/contacts/": "contacts.readonly",
}

RATE_HEADERS = ("X-RateLimit-Remaining", "X-RateLimit-Daily-Remaining",
                "X-RateLimit-Max", "X-RateLimit-Interval-Milliseconds")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def mask(value):
    """Mask emails and phone numbers anywhere in a value, recursively."""
    if isinstance(value, str):
        v = _EMAIL.sub(lambda m: m.group(0)[0] + "***@" + m.group(0).split("@")[-1], value)
        return _PHONE.sub(lambda m: "***" + re.sub(r"\D", "", m.group(0))[-4:], v)
    if isinstance(value, dict):
        return {k: mask(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v) for v in value]
    return value


def load_conf() -> tuple[str, str]:
    conf = {}
    path = os.path.join(HERE, ".probe.env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                conf[k.strip()] = v.strip().strip('"').strip("'")
    token = conf.get("GHL_RECRUITING_TOKEN") or os.environ.get("GHL_RECRUITING_TOKEN") or ""
    loc = conf.get("GHL_RECRUITING_LOCATION") or os.environ.get("GHL_RECRUITING_LOCATION") or ""
    if not token or not loc:
        sys.exit(
            "Missing credentials. Add to backend/.probe.env (git-ignored):\n"
            "    GHL_RECRUITING_TOKEN=pit-...\n"
            "    GHL_RECRUITING_LOCATION=...\n\n"
            "The token is a Private Integration Token on the RECRUITING location, created by a\n"
            "location admin with the read scopes listed in this file's docstring. It is not the\n"
            "Spring B / Forum token -- that is a different location entirely."
        )
    return token, loc


class Probe:
    """Every call goes through `get`. There is no other verb, on purpose."""

    def __init__(self, token: str, location: str):
        self.location = location
        self.client = httpx.Client(
            base_url=BASE, timeout=30.0,
            headers={"Authorization": f"Bearer {token}", "Version": VERSION,
                     "Accept": "application/json"},
        )
        self.rate_seen: dict[str, str] = {}
        self.errors: list[dict] = []

    def get(self, path: str, **params):
        try:
            r = self.client.get(path, params={k: v for k, v in params.items() if v is not None})
        except Exception as exc:  # noqa: BLE001
            self.errors.append({"path": path, "error": f"{type(exc).__name__}: {exc}"})
            return None
        for h in RATE_HEADERS:                      # V1
            if h in r.headers:
                self.rate_seen[h] = r.headers[h]
        if r.status_code == 403:
            hint = next((s for frag, s in SCOPE_HINTS.items() if frag in path), "unknown")
            self.errors.append({"path": path, "status": 403, "missing_scope": hint})
            print(f"  403 on {path} -- the token is missing scope: {hint}")
            return None
        if r.status_code >= 400:
            body = r.text[:300]
            self.errors.append({"path": path, "status": r.status_code, "body": mask(body)})
            print(f"  {r.status_code} on {path}: {mask(body)}")
            return None
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            self.errors.append({"path": path, "error": "response was not JSON"})
            return None


def ms(days_from_now: int) -> int:
    return int((dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days_from_now)).timestamp() * 1000)


def main() -> None:
    token, location = load_conf()
    p = Probe(token, location)
    out: dict = {"probed_at": dt.datetime.now(dt.timezone.utc).isoformat(), "location_id": location}

    # ── D1a: the location and, critically, its timezone (quiet hours depend on it, §5.5) ────
    print("\n== location ==")
    loc = p.get(f"/locations/{location}") or {}
    loc = loc.get("location", loc)
    out["location"] = {"id": loc.get("id"), "name": loc.get("name"),
                       "timezone": loc.get("timezone"), "country": loc.get("country")}
    print(f"  {loc.get('name')}  timezone={loc.get('timezone')}")
    if loc.get("timezone") and loc.get("timezone") != "America/Denver":
        print("  NOTE: quiet hours use the LOCATION timezone (§5.5). This is not America/Denver,")
        print("        so confirm which one the 08:00-21:00 window should follow.")

    # ── D1b: pipelines and stages, with open counts per stage ───────────────────────────────
    print("\n== pipelines ==")
    pipes = (p.get("/opportunities/pipelines", locationId=location) or {}).get("pipelines", []) or []
    out["pipelines"] = []
    for pipe in pipes:
        stages = [{"id": st.get("id"), "name": st.get("name"), "position": st.get("position")}
                  for st in (pipe.get("stages") or [])]
        out["pipelines"].append({"id": pipe.get("id"), "name": pipe.get("name"), "stages": stages})
        print(f"  {pipe.get('name')}  id={pipe.get('id')}  ({len(stages)} stages)")
        for st in stages:
            print(f"      {st['name']:<34} {st['id']}")

    # Open counts per stage, for the pipeline that looks like recruiting. Counted from a search
    # rather than assumed -- D1 is "which pipeline", and the answer is whichever one has the
    # candidates in it.
    print("\n== open opportunities per pipeline (first 100 each) ==")
    out["stage_counts"] = {}
    for pipe in out["pipelines"]:
        res = p.get("/opportunities/search", location_id=location, pipeline_id=pipe["id"],
                    status="open", limit=100)
        opps = (res or {}).get("opportunities", []) or []
        by_stage = Counter(o.get("pipelineStageId") for o in opps)
        names = {st["id"]: st["name"] for st in pipe["stages"]}
        out["stage_counts"][pipe["id"]] = {names.get(k, k): v for k, v in by_stage.items()}
        total = (res or {}).get("meta", {}).get("total", len(opps))
        print(f"  {pipe['name']}: {total} open")
        for sid, n in by_stage.most_common():
            print(f"      {names.get(sid, sid):<34} {n}")
        # One full opportunity shape, masked: this is where gci/brokerage custom values live.
        if opps and "sample_opportunity" not in out:
            out["sample_opportunity"] = mask(opps[0])

    # ── D3: users, for seat <-> GHL user matching by email (§1.5) ───────────────────────────
    print("\n== users ==")
    users = (p.get(f"/users/", locationId=location) or {}).get("users", []) or []
    out["users"] = [{"id": u.get("id"), "name": u.get("name"), "email": mask(u.get("email") or ""),
                     "roles": (u.get("roles") or {}).get("role")} for u in users]
    for u in out["users"]:
        print(f"  {(u['name'] or '?'):<28} {u['id']}  {u['email']}  {u['roles']}")

    # ── D4 + V2 + V4: calendars, their team members, and one free-slots response ────────────
    print("\n== calendars ==")
    cals = (p.get("/calendars/", locationId=location) or {}).get("calendars", []) or []
    out["calendars"] = []
    for c in cals:
        team = [{"userId": t.get("userId"), "priority": t.get("priority")}
                for t in (c.get("teamMembers") or [])]
        entry = {"id": c.get("id"), "name": c.get("name"), "slug": c.get("slug"),
                 "widgetType": c.get("widgetType"), "slotDuration": c.get("slotDuration"),
                 "teamMembers": team,
                 # V4: whether the calendar sends its own confirmations. The drawer copy "the
                 # invite goes out by text and email" is only honest if this is on.
                 "notifications": mask(c.get("notifications") or c.get("notificationOptions") or {})}
        out["calendars"].append(entry)
        print(f"  {(c.get('name') or '?'):<34} {c.get('id')}  members={len(team)} "
              f"slot={c.get('slotDuration')}")

    if out["calendars"]:
        cal_id = out["calendars"][0]["id"]
        print(f"\n== V2: free-slots shape (calendar {cal_id}) ==")
        slots = p.get(f"/calendars/{cal_id}/free-slots", startDate=ms(0), endDate=ms(7),
                      timezone=out["location"].get("timezone") or "America/Denver")
        out["free_slots_sample"] = mask(slots) if slots else None
        if slots:
            keys = [k for k in slots.keys()][:8]
            print(f"  top-level keys: {keys}")
            for k in keys:
                v = slots[k]
                if isinstance(v, dict) and "slots" in v:
                    print(f"    {k}: {len(v['slots'])} slots, first={v['slots'][:1]}")

    # ── D2: custom fields, hunting for trailing GCI and brokerage ───────────────────────────
    print("\n== custom fields ==")
    fields = (p.get(f"/locations/{location}/customFields") or {}).get("customFields", []) or []
    out["custom_fields"] = [{"id": f.get("id"), "name": f.get("name"), "fieldKey": f.get("fieldKey"),
                             "dataType": f.get("dataType"), "model": f.get("model")}
                            for f in fields]
    wanted = re.compile(r"gci|production|volume|ttm|trailing|brokerage|broker|cap|units|office",
                        re.I)
    hits = [f for f in out["custom_fields"] if wanted.search(f.get("name") or "")
            or wanted.search(f.get("fieldKey") or "")]
    print(f"  {len(out['custom_fields'])} fields; {len(hits)} look relevant to D2:")
    for f in hits:
        print(f"      {(f['name'] or '?'):<34} {f['dataType']:<12} {f['model']:<12} {f['id']}")

    # ── V3: conversations and the message type values actually in use ───────────────────────
    print("\n== V3: conversations ==")
    convos = p.get("/conversations/search", locationId=location, limit=20,
                   sort="desc", sortBy="last_message_date")
    items = (convos or {}).get("conversations", []) or []
    out["conversations_sample_count"] = len(items)
    out["conversations_search_keys"] = sorted((convos or {}).keys())
    print(f"  {len(items)} conversations; response keys {out['conversations_search_keys']}")
    types: Counter = Counter()
    directions: Counter = Counter()
    for conv in items[:10]:
        msgs = p.get(f"/conversations/{conv.get('id')}/messages", limit=20)
        body = ((msgs or {}).get("messages") or {})
        rows = body.get("messages", body) if isinstance(body, dict) else body
        for m in (rows or [])[:20]:
            if not isinstance(m, dict):
                continue
            types[m.get("messageType") or m.get("type")] += 1
            directions[m.get("direction")] += 1
            if "sample_message" not in out:
                # Keys only plus a masked shell: message bodies are not written to disk (§5.5).
                out["sample_message"] = {"keys": sorted(m.keys()),
                                         "messageType": m.get("messageType"),
                                         "type": m.get("type"),
                                         "direction": m.get("direction"),
                                         "status": m.get("status"),
                                         "has_duration": "duration" in m or "callDuration" in m}
    out["message_types"] = dict(types)
    out["message_directions"] = dict(directions)
    print(f"  message types: {dict(types)}")
    print(f"  directions:    {dict(directions)}")
    if out.get("sample_message"):
        print(f"  message keys:  {out['sample_message']['keys']}")

    # ── D8: one appointment's full shape, and the status values in use ──────────────────────
    print("\n== appointments (-14d .. +30d) ==")
    statuses: Counter = Counter()
    out["appointments_sample"] = None
    for c in out["calendars"]:
        ev = p.get("/calendars/events", locationId=location, calendarId=c["id"],
                   startTime=ms(-14), endTime=ms(30))
        events = (ev or {}).get("events", []) or []
        for e in events:
            statuses[e.get("appointmentStatus")] += 1
        if events and out["appointments_sample"] is None:
            out["appointments_sample"] = mask(events[0])
        print(f"  {(c['name'] or '?'):<34} {len(events)} events")
    out["appointment_statuses"] = dict(statuses)
    print(f"  statuses in use: {dict(statuses)}")

    # ── V1: did a PIT ever return the rate-limit headers? ───────────────────────────────────
    print("\n== V1: rate-limit headers on PIT responses ==")
    out["rate_limit_headers"] = p.rate_seen
    if p.rate_seen:
        for k, v in p.rate_seen.items():
            print(f"  {k}: {v}")
        print("  -> the §5.3 token bucket can read these. Good.")
    else:
        print("  NONE RETURNED. The §5.3 token bucket cannot be driven by headers on a PIT;")
        print("  it needs a fixed local budget instead (100/10s, 200k/day) and §5.3 must say so.")

    out["errors"] = p.errors
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print(f"\nwrote {OUT}")

    # ── What a human still has to decide ────────────────────────────────────────────────────
    print("\n== still needs a person (§2) ==")
    print("  D1  which pipeline above is recruiting -> record the id")
    print("  D2  stage -> group map, using the stage IDS printed above, not name matching")
    print("  D3  which users are the three Team Leaders and which is the SDR")
    print("  D4  one recruiting calendar per Team Leader, or one round-robin calendar")
    print("  D5  per-seat SMS sender numbers, or the location default")
    print("  D6  is this location A2P 10DLC registered? (no SMS write ships until it is)")
    print("  D8  'Held' = appointmentStatus showed, or reaching the Met group -- confirm")
    print("  V4  do the calendar automations above actually send confirmations? ask the admin")
    if p.errors:
        print(f"\n  {len(p.errors)} call(s) failed -- see 'errors' in the JSON. Any 403 above is a")
        print("  scope to add to the private integration before Phase 1.")


if __name__ == "__main__":
    main()
