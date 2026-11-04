r"""Add the sponsor and add-on guest tags to the Q4 event, then re-sync it.

WHY. The event counted one tag, `the forum q4 2026 guest rsvp`, and Spring's manual sheet
listed 30 VIP guests against the dashboard's 23. Every one of the 30 was in GHL; nine of them
carry the SPONSOR tag `forum vip guest sisu nov 2026` instead. They are real guests on somebody
else's seats, and they were not being counted at all.

The add-on tag is a separate, subtler miss. Guest tags match by SUBSTRING, which catches
`...guest rsvp paid` because the extra word is on the end, and does not catch
`...guest add on rsvp`, because the inserted words break the substring. Suffix variants work,
infix variants do not - so that person needs their tag listed explicitly.

The sync runs with the probe credentials rather than the integration's stored token: that token
is encrypted with production's Fernet key, which is not on this machine and should not be.
READ-ONLY against GHL; the only write is this event's guest_tags and its own guest rows.

    $env:DATABASE_URL="<Railway Postgres URL>"
    .venv\Scripts\python.exe scripts\set_q4_guest_tags.py          # dry run
    .venv\Scripts\python.exe scripts\set_q4_guest_tags.py --go     # apply + re-sync
"""
import argparse
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

for line in open(os.path.join(HERE, ".probe.env"), encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)

from sqlalchemy import select                                      # noqa: E402
from app.db import SessionLocal, engine                            # noqa: E402
from app.models import ForumEvent                                  # noqa: E402
from app.services import forum_event as FE                         # noqa: E402

TOKEN, LOCATION = os.environ["GHL_TOKEN"], os.environ["GHL_LOCATION_ID"]

TAGS = [
    "the forum q4 2026 guest rsvp",          # also catches "... guest rsvp paid" (suffix)
    "forum vip guest sisu nov 2026",         # Sisu's sponsored seats
    "forum vip guest realty.com nov 2026",   # Realty.com's sponsored seat
    "the forum q4 2026 guest add on rsvp",   # infix variant the substring match cannot reach
]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--slug", default="q4-2026")
    a = ap.parse_args()

    async with SessionLocal() as s:
        ev = (await s.execute(select(ForumEvent).where(
            ForumEvent.slug == a.slug))).scalars().first()
        if ev is None:
            print(f"[not found] no {a.slug!r} event")
            await engine.dispose()
            return 1
        before = await FE.compute_event(s, ev.tenant_id, ev)
        tid, eid = ev.tenant_id, ev.id
        current = list(ev.guest_tags or [])

    r = before["registration"]
    print(f"{ev.name}: {r['guests']} of {r['goal']} guests, {r['days_to_event']} days out\n")
    print("guest_tags now:")
    for t in current:
        print(f"   - {t}")
    print("\nguest_tags after:")
    for t in TAGS:
        print(f"   {'  ' if t in current else '+ '}{t}")

    if not a.go:
        print("\nDRY RUN - nothing written. Re-run with --go.")
        await engine.dispose()
        return 0

    async with SessionLocal() as s:
        ev = (await s.execute(select(ForumEvent).where(ForumEvent.id == eid))).scalar_one()
        ev.guest_tags = TAGS
        await s.commit()
    print("\ntags written. syncing...")

    async with SessionLocal() as s:
        ev = (await s.execute(select(ForumEvent).where(ForumEvent.id == eid))).scalar_one()
        stat = await FE.sync_forum_event(s, tid, ev, TOKEN, LOCATION)
    print(f"sync: {stat}")

    async with SessionLocal() as s:
        ev = (await s.execute(select(ForumEvent).where(ForumEvent.id == eid))).scalar_one()
        after = await FE.compute_event(s, tid, ev)
    r2 = after["registration"]
    print(f"\nBEFORE  {r['guests']} guests, {r['members_registered']} members, "
          f"{r['room']} in the room, {r['paid']} paid / {r['comped']} comped")
    print(f"AFTER   {r2['guests']} guests, {r2['members_registered']} members, "
          f"{r2['room']} in the room, {r2['paid']} paid / {r2['comped']} comped")
    print(f"        state {r2['state']}, expected {r2['expected']}, gap {r2['gap']}")
    print(f"        {r2['guests_without_opp']} RSVPs with no sale open")
    print("        warnings: " + ("; ".join(after["warnings"]) or "none"))
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
