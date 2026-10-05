r"""Phase 7's confirmation — does the Event tab agree with GHL?

Reads the event out of whatever DATABASE_URL points at, then counts the same populations
straight from GHL in the same breath and compares.

It first compared against figures measured by hand on 2026-10-05 (24 guests / 34 members /
58 in the room) and promptly "failed", because Spring kept working the funnel: a member
registered, and the one contact carrying both tags stopped carrying one. The constants were
right when written and wrong an hour later. Checking a moving number against a frozen one tells
you the clock moved, not whether the code is correct — so the comparison is DB vs GHL.

It counts through the sync's OWN matchers, deliberately: this cannot catch a bug inside
`match_guest_tag` and is not meant to. It catches a sync that stored the wrong people, stored
them twice, or stopped running — which is what actually goes wrong.

READ-ONLY on both sides. Writes nothing, anywhere.

    $env:DATABASE_URL="<Railway Postgres URL>"
    .venv\Scripts\python.exe scripts\confirm_q4.py
    .venv\Scripts\python.exe scripts\confirm_q4.py --wait 20   # poll until the worker has run
"""
import argparse
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from sqlalchemy import select                                      # noqa: E402
from app.db import SessionLocal, engine                            # noqa: E402
from app.models import ForumEvent, ForumEventGuest                 # noqa: E402
from app.services import forum_event as FE                         # noqa: E402


async def ghl_contacts():
    """Every contact in the Forum location, or None when this machine has no probe creds."""
    probe = os.path.join(HERE, ".probe.env")
    if not os.path.exists(probe):
        return None
    for line in open(probe, encoding="utf-8"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)
    tok, loc = os.environ.get("GHL_TOKEN"), os.environ.get("GHL_LOCATION_ID")
    if not (tok and loc):
        return None
    from app.integrations.ghl import get_contacts
    return await get_contacts(tok, loc)


async def look(slug):
    async with SessionLocal() as s:
        ev = (await s.execute(select(ForumEvent).where(
            ForumEvent.slug == slug))).scalars().first()
        if ev is None:
            return None, None
        n = len((await s.execute(select(ForumEventGuest).where(
            ForumEventGuest.event_id == ev.id))).scalars().all())
        if n == 0:
            return ev, None
        return ev, await FE.compute_event(s, ev.tenant_id, ev)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="q4-2026")
    ap.add_argument("--wait", type=int, default=0, help="minutes to poll for the worker")
    a = ap.parse_args()

    ev = d = None
    for attempt in range(max(1, a.wait * 4)):          # 15s per attempt
        ev, d = await look(a.slug)
        if ev is None:
            print(f"[not found] no {a.slug!r} event in this database")
            await engine.dispose()
            return 1
        if d is not None:
            break
        if attempt == 0:
            print(f"event {ev.slug} exists, no guests yet - waiting for the worker...")
        await asyncio.sleep(15)

    if d is None:
        print("still no guest rows. The worker has not synced this event yet.")
        await engine.dispose()
        return 1

    r = d["registration"]
    print("WHAT THE EVENT TAB SHOWS\n")
    print(f"  {r['guests']} of {r['goal']} VIP guests   ({r['state']}, {r['days_to_event']} days out)")
    print(f"  {r['members_registered']} members registered  |  {r['room']} in the room")
    print(f"  {r['paid']} paid / {r['comped']} comped")
    print(f"  {r['guests_without_opp']} RSVPs with no sale open, "
          f"{r['guests_stage_conflict']} parked in the funnel")
    print("  funnel: " + ", ".join(f"{f['key']}={f['count']}" for f in d["funnel"]))
    print(f"  ticket revenue {d['revenue']['ticket_booked']}  "
          f"member ARR {d['revenue']['member_arr']}   (unpriced by design)")
    print("  warnings: " + ("; ".join(d["warnings"]) or "none"))

    contacts = await ghl_contacts()
    if contacts is None:
        print("\n(no .probe.env here - cannot census GHL, so the figures above stand unchecked)")
        await engine.dispose()
        return 0

    guests = [c for c in contacts if FE.match_guest_tag(c.get("tags") or [], ev.guest_tags)]
    members = [c for c in contacts if FE.match_member_tag(c.get("tags") or [], ev.member_tags)]
    both = [c for c in guests if FE.match_member_tag(c.get("tags") or [], ev.member_tags)]
    want = {"guests": len(guests),
            "members_registered": len(members) - len(both),
            "room": len(guests) + len(members) - len(both)}

    print("\nAGAINST A LIVE CENSUS OF GHL, TAKEN JUST NOW")
    ok = True
    for k, w in want.items():
        got = r[k]
        good = got == w
        ok = ok and good
        print(f"  [{'MATCH' if good else 'DIFFERS'}] {k}: {got} (GHL says {w})")
    if both:
        print(f"  ({len(both)} contact(s) carry both tags and are counted once, as guests)")
    print("\n" + ("CONFIRMED - the tab agrees with GHL" if ok else
                  "DIFFERS - reconcile person by person before trusting the tab"))
    await engine.dispose()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
