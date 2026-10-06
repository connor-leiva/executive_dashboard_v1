r"""Set the Q4 pace curve from Spring's own Q3 registration history.

WHERE THE NUMBERS COME FROM. The Forum sync has been writing a `reg_count` metric_record once
per sync day since before Q3 — external_id "<event_tag>:<date>", amount = the registration
count, meta.days_out = days to the event. The audit found this: built, written, and never read
by anything. Production holds 81 days of it for "the forum q3 2026".

Normalised against the final count (43), the pre-event portion is:

    days out   40    32    27    18    11     5     1     0
    fraction  0.40  0.58  0.70  0.79  0.84  0.93  0.95  1.00

That is measured, not assumed, and it is a Forum event rather than a webinar — unlike
DEFAULT_SHIFT_CURVE, which is the Shift's.

TWO HONEST LIMITS, both worth knowing before trusting the pace figure:

  1. `reg_count.amount` is MEMBER registrations, not VIP guests (sync.py writes member_regs).
     Q4's goal is 60 guests. Using this curve for guests assumes the two populations pace
     alike, which is an assumption and not a measurement. It is still far better evidence than
     a shape somebody invented, and Q4's own guest history is being captured weekly now
     (forum_event_weekly), so Q1 can use a guest-specific curve measured from Q4.

  2. The history starts at 40 days out — nothing earlier was ever recorded. The 104-day point
     below is the selling-window opening, anchored at 0.00 by definition rather than by
     measurement, so the early window interpolates instead of clamping flat at 0.40. Q4 is 38
     days out, comfortably inside the measured range.

The dip at 29 days (0.58 -> 0.53) is real — people untag — and is smoothed out here, because a
pace curve that goes backwards would make "behind" and "ahead" flip for reasons the room cannot
act on.

    $env:DATABASE_URL="<Railway Postgres URL>"
    .venv\Scripts\python.exe scripts\set_q4_pace_curve.py           # dry run
    .venv\Scripts\python.exe scripts\set_q4_pace_curve.py --go
"""
import argparse
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from sqlalchemy import select                                      # noqa: E402
from app.db import SessionLocal, engine                            # noqa: E402
from app.models import ForumEvent                                  # noqa: E402
from app.services.forum_event import compute_event                 # noqa: E402
from app.services.launch import curve_expected                     # noqa: E402

# Keys are strings because curve_expected does int(k) on them and JSON has no integer keys.
CURVE = {
    "104": 0.00,   # selling opens — definitional anchor, not measured
    "40": 0.40,
    "32": 0.58,
    "27": 0.70,
    "18": 0.79,
    "11": 0.84,
    "5": 0.93,
    "1": 0.95,
    "0": 1.00,
}


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
        before = await compute_event(s, ev.tenant_id, ev)

    r = before["registration"]
    goal, dte, guests = r["goal"], r["days_to_event"], r["guests"]
    print(f"{ev.name}: {guests} of {goal} guests, {dte} days out")
    print(f"   now      state {r['state']}, expected {r['expected']}, gap {r['gap']}")

    pct = curve_expected({k: v for k, v in CURVE.items()}, dte)
    exp = round(pct * goal)
    tol = float(ev.pace_tolerance or 0.08) * goal
    gap = guests - exp
    state = "behind" if gap < -tol else "ahead" if gap > tol else "onpace"
    print(f"   with it  state {state}, expected {exp} ({pct:.0%} of goal), gap {gap:+d}")
    print(f"            tolerance +/-{tol:.1f}")

    print("\n   curve (days out -> fraction of goal):")
    for k in sorted(CURVE, key=lambda x: -int(x)):
        print(f"      {k:>4}  {CURVE[k]:.2f}   {round(CURVE[k] * goal):>3} guests")

    if not a.go:
        print("\nDRY RUN - nothing written. Re-run with --go.")
        await engine.dispose()
        return 0

    async with SessionLocal() as s:
        ev = (await s.execute(select(ForumEvent).where(
            ForumEvent.slug == a.slug))).scalars().first()
        ev.pace_curve = CURVE
        await s.commit()
        after = await compute_event(s, ev.tenant_id, ev)
    r2 = after["registration"]
    print(f"\nSET. The tab now reads: {r2['guests']} of {r2['goal']}, "
          f"{r2['state']}, expected {r2['expected']}, gap {r2['gap']}")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
