r"""What does the dashboard actually hold for one call?

Recall can prove whether a BOT existed. This proves what the scheduler was looking at when
it decided whether to book one - the row, its parsed time, its link, and every change we
have observed to it. That is the other half of "why did this call get no notetaker".

Run against prod by pointing DATABASE_URL at it (Railway -> Postgres -> Connect):

    $env:DATABASE_URL="postgresql://..."
    .venv\Scripts\python.exe scripts\call_row.py --name "Elisha"

Prints one contact's rows only. Names are supplied by the caller and echoed back; no
recording URLs are printed, since those are signed media links.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select                                         # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings                                      # noqa: E402
from app.models import SalesCall, SalesCallChange                    # noqa: E402


def _fmt(v):
    return "—" if v in (None, "") else str(v)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="substring of the contact name")
    a = ap.parse_args()

    eng = create_async_engine(settings.async_database_url, pool_pre_ping=True)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    async with Session() as s:
        rows = (await s.execute(
            select(SalesCall)
            .where(SalesCall.contact_name.ilike(f"%{a.name}%"))
            .order_by(SalesCall.first_seen_at))).scalars().all()
        print(f"rows for {a.name!r}: {len(rows)}\n")
        for r in rows:
            flag = "CURRENT" if r.is_current else "superseded"
            print(f"  [{flag}] {r.contact_name}   row {str(r.id)[:8]}")
            print(f"     opportunity   {r.opportunity_id}")
            print(f"     call_time_raw {_fmt(r.call_time_raw)}")
            print(f"     call_time_utc {_fmt(r.call_time_utc)}   <- what the scheduler compares")
            print(f"     meeting_url   {'SET' if r.meeting_url else 'NULL  <-- cannot book without this'}")
            print(f"     recall_bot_id {_fmt(r.recall_bot_id)}")
            print(f"     rec_status    {_fmt(r.recording_status)}")
            print(f"     outcome       {_fmt(r.outcome)}")
            print(f"     first_seen_at {_fmt(r.first_seen_at)}   <- when WE learned of it")
            ch = (await s.execute(
                select(SalesCallChange)
                .where(SalesCallChange.sales_call_id == r.id)
                .order_by(SalesCallChange.observed_at))).scalars().all()
            if ch:
                print("     observed changes:")
                for c in ch:
                    print(f"       {str(c.observed_at)[:19]}  {c.field:<12} "
                          f"{_fmt(c.old_value)[:34]} -> {_fmt(c.new_value)[:34]}")
            print()
    await eng.dispose()

    print("Read it like this:")
    print("  first_seen_at AFTER the call started -> we learned too late to book (sync gap).")
    print("  meeting_url NULL                     -> nothing to send a bot to.")
    print("  recall_bot_id set but absent from Recall -> the row points at a bot that is not")
    print("     ours, so the scheduler thought it was covered and skipped it.")
    print("  a call_time change today             -> the booking moved after we synced it.")


if __name__ == "__main__":
    asyncio.run(main())
