r"""Phase 3's acceptance gate, run against the LIVE Forum location.

Proves two things that a fixture cannot:

  1. the sync finds the numbers the audit found by hand - 24 VIP guests and 35 registered
     members for Spring's Q4 tags;
  2. syncing a SECOND event leaves the first one's rows intact. That is the regression test for
     the bug this whole table exists to avoid: `_metric_snapshot` clears by
     (tenant, business, source, kind) before inserting, so the Forum can only ever hold one
     event's registrations, and quarterly events are unbuildable on it.

READ-ONLY against GHL (contacts, pipelines, opportunities, custom-field names). Writes only to a
throwaway SQLite file it creates and deletes. Credentials come from backend/.probe.env.

    .venv\Scripts\python.exe scripts\forum_event_live.py
"""
import asyncio
import datetime as dt
import os
import sys
import uuid

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

SCRATCH = os.path.join(HERE, "forum_event_live.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///./{os.path.basename(SCRATCH)}"

for line in open(os.path.join(HERE, ".probe.env"), encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)

from sqlalchemy import select                                      # noqa: E402
from app.db import SessionLocal, engine                            # noqa: E402
from app.models import Base, Tenant, Business, ForumEvent, ForumEventGuest, ForumEventWeekly  # noqa: E402
from app.services import forum_event as FE                         # noqa: E402

TOKEN = os.environ["GHL_TOKEN"]
LOCATION = os.environ["GHL_LOCATION_ID"]
TODAY = dt.date(2026, 10, 5)

Q4 = dict(name="The Forum Q4 2026", slug="q4-2026", status="selling",
          starts_on=dt.date(2026, 11, 13), ends_on=dt.date(2026, 11, 15),
          window_start=dt.date(2026, 8, 1), window_end=dt.date(2026, 11, 13),
          guest_goal=60,
          guest_tags=["the forum q4 2026 guest rsvp"],
          member_tags=["the forum q4 2026 rsvp"],
          declined_tags=["q4 not registered - member"],
          stage_map=FE.DEFAULT_STAGE_MAP,
          pipeline_match=["forum main sales funnel"])
# A real prior event, so the second sync is a real second event rather than a contrivance.
Q3 = dict(name="The Forum Q3 2026", slug="q3-2026", status="closed",
          starts_on=dt.date(2026, 8, 14), ends_on=dt.date(2026, 8, 16),
          window_start=dt.date(2026, 5, 1), window_end=dt.date(2026, 8, 14),
          guest_goal=40,
          guest_tags=["q3 august 2026 guest vip comped", "forum q3 2026 vip guest"],
          member_tags=["forum q3 member - not attending"],
          stage_map=FE.DEFAULT_STAGE_MAP,
          pipeline_match=["forum main sales funnel"])


async def main() -> int:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as s:
        t = Tenant(slug=f"probe-{uuid.uuid4().hex[:6]}", name="Probe")
        s.add(t)
        await s.flush()
        b = Business(tenant_id=t.id, key="forum", name="Forum", tag="Forum", kind="membership")
        s.add(b)
        await s.flush()
        q4 = ForumEvent(tenant_id=t.id, business_id=b.id, **Q4)
        q3 = ForumEvent(tenant_id=t.id, business_id=b.id, **Q3)
        s.add_all([q4, q3])
        await s.commit()
        tid, q4_id, q3_id = t.id, q4.id, q3.id

    print(f"location {LOCATION}   as-of {TODAY}\n")

    async with SessionLocal() as s:
        ev = (await s.execute(select(ForumEvent).where(ForumEvent.id == q4_id))).scalar_one()
        stat = await FE.sync_forum_event(s, tid, ev, TOKEN, LOCATION, today=TODAY)
    print(f"Q4 sync : {stat}")

    async with SessionLocal() as s:
        ev = (await s.execute(select(ForumEvent).where(ForumEvent.id == q4_id))).scalar_one()
        d = await FE.compute_event(s, tid, ev, today=TODAY)
    r = d["registration"]
    print(f"  guests {r['guests']}  members {r['members_registered']}  room {r['room']}")
    print(f"  no-opp {r['guests_without_opp']}  stage-conflict {r['guests_stage_conflict']}")
    print(f"  goal {r['goal']}  pct {r['pct_to_goal']}  days-out {r['days_to_event']}"
          f"  state {r['state']}")
    print("  funnel: " + ", ".join(f"{f['key']}={f['count']}" for f in d["funnel"]))
    print("  warnings: " + ("; ".join(d["warnings"]) or "none"))
    print(f"  revenue: ticket={d['revenue']['ticket_booked']} arr={d['revenue']['member_arr']}"
          "   (unpriced by design)")

    # THE REGRESSION. Sync a different event and prove Q4's rows survive it.
    async with SessionLocal() as s:
        ev3 = (await s.execute(select(ForumEvent).where(ForumEvent.id == q3_id))).scalar_one()
        stat3 = await FE.sync_forum_event(s, tid, ev3, TOKEN, LOCATION, today=TODAY)
    print(f"\nQ3 sync : {stat3}")

    async with SessionLocal() as s:
        after = len((await s.execute(select(ForumEventGuest).where(
            ForumEventGuest.event_id == q4_id))).scalars().all())
        q3n = len((await s.execute(select(ForumEventGuest).where(
            ForumEventGuest.event_id == q3_id))).scalars().all())
        weeks = len((await s.execute(select(ForumEventWeekly))).scalars().all())

    before = (stat.get("guests") or 0) + (stat.get("members") or 0) + (stat.get("declined") or 0)
    print(f"  Q4 rows after Q3 sync: {after} (was {before})   Q3 rows: {q3n}   weekly rows: {weeks}")

    ok = True
    def check(label, got, want):
        nonlocal ok
        good = got == want
        ok = ok and good
        print(f"  [{'PASS' if good else 'FAIL'}] {label}: {got} (expected {want})")

    print("\nGATE")
    check("VIP guests", r["guests"], 24)
    # 35 contacts carry the member tag, but ONE of them also carries the guest tag and
    # precedence makes them a guest (D10). 24 + 35 - 1 = 58 in the room.
    check("members registered", r["members_registered"], 34)
    check("room", r["room"], 58)
    check("Q4 rows survive a second event's sync", after, before)
    print(f"  [{'PASS' if q3n else 'FAIL'}] Q3 found its own rows: {q3n}")
    ok = ok and bool(q3n)

    await engine.dispose()
    if os.path.exists(SCRATCH):
        os.remove(SCRATCH)
    print("\n" + ("GATE PASSED" if ok else "GATE FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
