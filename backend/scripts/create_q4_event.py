r"""Phase 7 — create (or update) the Forum's Q4 2026 event, then show what it finds.

Everything Connor answered on 2026-10-05, in one command instead of a form:

    guest tag        the forum q4 2026 guest rsvp
    member tag       the forum q4 2026 rsvp
    not-attending    q4 not registered - member
    pipeline         forum main sales funnel
    goal             60
    dates            13-15 November 2026
    pricing          deliberately EMPTY - the room count never needs it

IDEMPOTENT. Creating an event that already exists updates it in place rather than making a
second one, so running this twice is safe. It writes exactly one row (plus the guest rows the
sync upserts) and deletes nothing.

    $env:DATABASE_URL="<Railway Postgres URL>"
    .venv\Scripts\python.exe scripts\create_q4_event.py            # show what WOULD happen
    .venv\Scripts\python.exe scripts\create_q4_event.py --go       # write it
    .venv\Scripts\python.exe scripts\create_q4_event.py --go --sync  # ...and sync it now

Without --go this is a dry run. With --sync it also runs the GHL pull immediately instead of
waiting up to 30 minutes for the worker, which is the difference between confirming the numbers
now and confirming them after lunch.
"""
import argparse
import asyncio
import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from sqlalchemy import select                                      # noqa: E402
from app.db import SessionLocal, engine                            # noqa: E402
from app.models import ForumEvent, Integration                     # noqa: E402
from app.services import roles                                     # noqa: E402
from app.services import forum_event as FE                         # noqa: E402

SLUG = "q4-2026"
FIELDS = dict(
    name="The Forum Q4 2026",
    status="selling",
    starts_on=dt.date(2026, 11, 13),
    ends_on=dt.date(2026, 11, 15),
    window_start=dt.date(2026, 8, 1),
    window_end=dt.date(2026, 11, 13),
    guest_goal=60,
    guest_tags=["the forum q4 2026 guest rsvp"],
    member_tags=["the forum q4 2026 rsvp"],
    declined_tags=["q4 not registered - member"],
    pipeline_match=["forum main sales funnel"],
    stage_map=FE.DEFAULT_STAGE_MAP,
    # Left unset on purpose. Every money figure renders as a dash until somebody prices it,
    # and not one count depends on it.
    vip_price=None,
    price_map=None,
)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="write it (otherwise a dry run)")
    ap.add_argument("--sync", action="store_true", help="pull from GHL immediately after")
    ap.add_argument("--tenant", default=None, help="tenant slug; default = the only one")
    a = ap.parse_args()

    async with SessionLocal() as s:
        from app.models import Tenant
        tenants = (await s.execute(select(Tenant))).scalars().all()
        if a.tenant:
            tenants = [t for t in tenants if t.slug == a.tenant]
        if len(tenants) != 1:
            print(f"found {len(tenants)} tenants: {[t.slug for t in tenants]}")
            print("pass --tenant <slug> to pick one")
            return 1
        tenant = tenants[0]
        biz = await roles.membership(s, tenant.id)
        if biz is None:
            print(f"[refused] {tenant.slug} has no membership business")
            return 1
        print(f"tenant   {tenant.slug}\nbusiness {biz.key} ({biz.name})")

        existing = (await s.execute(select(ForumEvent).where(
            ForumEvent.tenant_id == tenant.id,
            ForumEvent.business_id == biz.id,
            ForumEvent.slug == SLUG))).scalar_one_or_none()
        verb = "UPDATE" if existing else "CREATE"
        print(f"\n{verb} forum_event {SLUG!r}")
        for k, v in FIELDS.items():
            if k == "stage_map":
                v = f"<{len(v)} groups>"
            print(f"   {k:16} {v}")

        if not a.go:
            print("\nDRY RUN - nothing written. Re-run with --go.")
            return 0

        ev = existing or ForumEvent(tenant_id=tenant.id, business_id=biz.id, slug=SLUG)
        for k, v in FIELDS.items():
            setattr(ev, k, v)
        if existing is None:
            s.add(ev)
        await s.commit()
        eid = ev.id
        print(f"\n{verb}D. id {eid}")

        if not a.sync:
            print("The worker will pick it up within 30 minutes. "
                  "Re-run with --sync to pull now.")
            return 0

        integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == tenant.id,
            Integration.business_id == biz.id,
            Integration.provider == "ghl"))).scalar_one_or_none()
        if integ is None:
            print("[skip] no GHL integration on this business - cannot sync")
            return 1

    async with SessionLocal() as s:
        n = await FE.sync_events_for_integration(s, tenant.id, integ)
        print(f"\nsynced {n} people")

    async with SessionLocal() as s:
        ev = (await s.execute(select(ForumEvent).where(ForumEvent.id == eid))).scalar_one()
        d = await FE.compute_event(s, tenant.id, ev)
    r = d["registration"]
    print("\nWHAT THE TAB WILL SHOW")
    print(f"   {r['guests']} of {r['goal']} VIP guests   ({r['state']}, "
          f"{r['days_to_event']} days out)")
    print(f"   {r['members_registered']} members registered, {r['room']} in the room")
    print(f"   {r['guests_without_opp']} RSVPs with no sale open, "
          f"{r['guests_stage_conflict']} parked in the funnel")
    print("   funnel: " + ", ".join(f"{f['key']}={f['count']}" for f in d["funnel"]))
    print("   warnings: " + ("; ".join(d["warnings"]) or "none"))
    print("\nEXPECTED (measured 2026-10-05): 24 guests, 34 members, 58 in the room.")
    ok = (r["guests"], r["members_registered"], r["room"]) == (24, 34, 58)
    print("MATCHES" if ok else "DOES NOT MATCH - worth reconciling person by person")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
