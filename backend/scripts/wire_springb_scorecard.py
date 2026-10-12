"""Wire the Spring B L10 scorecard's Forum lines to their data feeds.

The Spring B board (one board, shared by the Forum, beCollective and Edge tabs) shipped with every line
manual. Four Forum lines now have a feed:

  Members Added     → resolver forum_members_added     GHL member enrollment dates — the Forum tab's own
                                                       'joined' definition, so the two reconcile
  VIP Guests Added  → resolver forum_vip_guests_added  funnel stage-change events, tracked from deploy;
                                                       weeks before tracking began read UNAVAILABLE
  Appointments Set  → source_spec (editable in the UI)  "Forum VIP Guest Application" calendar,
                                                       booked that week
  Appointments Met  → source_spec (editable in the UI)  same calendar, GHL 'showed' only, by start
                                                       date (reads low until attendance is marked)

Every other line stays manual — no data source exists for it yet.

Dry-run by default: each line's current wiring → new, and what it would read for the last N weeks.
--apply writes. --backfill N then fills those weeks for the wired lines only (overwriting: they are
auto-sourced from now on). A line someone already routed to something ELSE is left alone unless --force.

  railway ssh --service executive_dashboard_v1 "python -m scripts.wire_springb_scorecard"
  railway ssh --service executive_dashboard_v1 "python -m scripts.wire_springb_scorecard --apply --backfill 13"

ORDER: --apply also sets WHICH GHL calendar feeds the appointment lines (forum_appointment_calendar_ids on
the Forum GHL integration). Its bookings arrive with the NEXT Forum sync (~30 min; that run backfills 120
days). Until then --backfill skips the appointment lines rather than writing zeros that would stick — run
--backfill again after that sync.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from app.config import settings
from app.db import SessionLocal
from app.models import Business, Integration, MetricRecord, ScorecardGroup, ScorecardMetric, Tenant
from app.services import scorecard_resolvers as R
from app.services.scorecard_routing import FORUM_APPOINTMENT_SPECS, run_spec, validate_spec

# The GHL calendar(s) whose bookings are Forum "appointments" — Connor's pick, 2026-10-12. NOT the
# configured `forum_calendar_id`, which is "The Forum Events" (the quarterly-event calendar: no
# prospect appointments). Written to the Forum GHL integration as `forum_appointment_calendar_ids`.
FORUM_APPOINTMENT_CALENDARS = ["6q9ga2aGN28ryW990aR9"]      # "Forum VIP Guest Application"

# (group key, measurable name as it is on the board, wiring)
WIRING = [
    ("forum", "Members Added", {"resolver_key": "forum_members_added"}),
    ("forum", "VIP Guests Added", {"resolver_key": "forum_vip_guests_added"}),
    ("forum", "Appointments Set", {"source_spec": FORUM_APPOINTMENT_SPECS["forum_appointments_set"]}),
    ("forum", "Appointments Met", {"source_spec": FORUM_APPOINTMENT_SPECS["forum_appointments_met"]}),
]


def _db_target() -> str:
    url = settings.DATABASE_URL
    if url.startswith("sqlite"):
        return f"LOCAL SQLite ({url.rsplit('/', 1)[-1]})  <-- NOT production"
    try:
        creds, hostpart = url.split("://", 1)[1].split("@", 1)
        return f"Postgres {creds.split(':', 1)[0]}@{hostpart}"
    except (IndexError, ValueError):
        return "Postgres (could not parse the URL)"


def _show(v) -> str:
    if v is R.UNAVAILABLE:
        return "—"
    if v is None:
        return "None"
    return str(int(v)) if float(v).is_integer() else str(v)


def _state(rk, spec, want: dict) -> str:
    """'done' | 'conflict' | 'todo' for a line's current wiring against what we want."""
    if "resolver_key" in want:
        if rk == want["resolver_key"] and spec is None:
            return "done"
        return "conflict" if (spec is not None or rk not in (None, want["resolver_key"])) else "todo"
    if spec == want["source_spec"]:
        return "done"
    return "conflict" if spec is not None else "todo"


async def _value(s, tid, bid, group, want, ws, we):
    if "resolver_key" in want:
        return await R.RESOLVERS[want["resolver_key"]](s, tid, bid, ws, we, group=group)
    return await run_spec(want["source_spec"], s, tid, bid, ws, we, group=group)


async def _spec_has_data(s, tid, bid, spec) -> bool:
    kinds = [f["value"] for f in spec.get("filters") or []
             if f.get("field") == "kind" and f.get("op") == "eq"]
    if not kinds:
        return True
    return await s.scalar(select(MetricRecord.id).where(
        MetricRecord.tenant_id == tid, MetricRecord.business_id == bid,
        MetricRecord.kind == kinds[0]).limit(1)) is not None


async def main() -> int:
    ap = argparse.ArgumentParser(description="Wire the Spring B scorecard's Forum lines to their feeds.")
    ap.add_argument("--tenant", default="springb", help="tenant slug (default: springb)")
    ap.add_argument("--business", default="springb", help="the board's business key (default: springb)")
    ap.add_argument("--weeks", type=int, default=6, help="weeks to preview (default: 6)")
    ap.add_argument("--apply", action="store_true", help="write the wiring (otherwise dry-run)")
    ap.add_argument("--backfill", type=int, default=0, metavar="N",
                    help="after --apply, fill the last N weeks for the wired lines")
    ap.add_argument("--force", action="store_true", help="re-wire a line already routed to something else")
    args = ap.parse_args()

    for _, _, want in WIRING:
        if "source_spec" in want:
            errs = validate_spec(want["source_spec"])
            if errs:
                print(f"BUG: a spec in WIRING is invalid: {errs}")
                return 2
        elif want["resolver_key"] not in R.RESOLVERS:
            print(f"BUG: resolver {want['resolver_key']!r} is not registered")
            return 2

    print(f"DB target: {_db_target()}")
    print(f"{'APPLY' if args.apply else 'DRY-RUN'}   tenant={args.tenant!r}  business={args.business!r}\n")
    today = dt.datetime.now(ZoneInfo(settings.BILLING_TIMEZONE)).date()
    weeks = R._recent_weeks(today, args.weeks)

    async with SessionLocal() as s:
        row = (await s.execute(select(Business.id, Business.tenant_id).join(
            Tenant, Business.tenant_id == Tenant.id).where(
            Tenant.slug == args.tenant, Business.key == args.business))).first()
        if not row:
            print(f"No business {args.business!r} for tenant {args.tenant!r}.")
            return 2
        bid, tid = row

        # Which calendar(s) feed the appointment lines — set on the Forum GHL integration.
        integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == tid, Integration.business_id == bid,
            Integration.provider == "ghl"))).scalar_one_or_none()
        cal_change = None
        if integ is None:
            print("  WARNING   no Forum GHL integration on this business — the appointment lines will stay empty")
        else:
            have_cals = list((integ.config or {}).get("forum_appointment_calendar_ids") or [])
            cal_change = None if have_cals == FORUM_APPOINTMENT_CALENDARS else list(FORUM_APPOINTMENT_CALENDARS)
            print(f"  {'TODO' if cal_change else 'DONE':9} {'forum GHL appointment calendars':34} "
                  f"{have_cals or None} -> {FORUM_APPOINTMENT_CALENDARS}")

        plan = []        # (metric_id, label, group_ctx, want)
        for gkey, name, want in WIRING:
            hit = (await s.execute(
                select(ScorecardMetric.id, ScorecardMetric.resolver_key, ScorecardMetric.source_spec,
                       ScorecardGroup.key, ScorecardGroup.sisu_group_id)
                .join(ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id)
                .where(ScorecardMetric.tenant_id == tid, ScorecardGroup.business_id == bid,
                       ScorecardGroup.key == gkey, ScorecardMetric.name == name,
                       ScorecardMetric.active.is_(True)))).first()
            label = f"{gkey} / {name}"
            if not hit:
                print(f"  MISSING   {label:34} not on the board (renamed or removed?) — skipped")
                continue
            mid, rk, spec, g_key, sgid = hit
            group = {"key": g_key, "sisu_group_id": sgid}
            state = _state(rk, spec, want)
            target = (f"resolver {want['resolver_key']}" if "resolver_key" in want else "source_spec")
            vals = [_show(await _value(s, tid, bid, group, want, ws, we)) for ws, we in weeks]
            print(f"  {state.upper():9} {label:34} -> {target:32} last{len(weeks)}w (newest first): "
                  f"{' '.join(vals)}")
            if state == "conflict" and not args.force:
                print(f"            already routed elsewhere (resolver_key={rk!r}, spec={'set' if spec else None})"
                      f" — left alone; --force to override")
                continue
            if state != "done":
                plan.append((mid, label, group, want))
            elif args.backfill:
                plan.append((mid, label, group, want))     # already wired: still backfill on request

        if not args.apply:
            print(f"\nDRY-RUN: {len(plan)} line(s) would be wired"
                  f"{' and the appointment calendar set' if cal_change else ''}. Re-run with --apply.")
            return 0

        if cal_change:
            cfg = dict(integ.config or {})     # a COPY: mutating the loaded dict makes SQLAlchemy skip the UPDATE
            cfg["forum_appointment_calendar_ids"] = cal_change
            await s.execute(update(Integration).where(Integration.id == integ.id).values(config=cfg))
            print("SET the Forum appointment calendar — its bookings arrive with the NEXT Forum sync "
                  "(~30 min); then re-run --backfill to fill the appointment history.")

        for mid, label, group, want in plan:
            if "resolver_key" in want:
                vals = {"resolver_key": want["resolver_key"], "source_spec": None}
            else:
                vals = {"source_spec": copy.deepcopy(want["source_spec"])}
            await s.execute(update(ScorecardMetric).where(ScorecardMetric.id == mid).values(**vals))
        await s.commit()
        print(f"\nWIRED {len(plan)} line(s).")

    if args.apply and args.backfill:
        bweeks = R._recent_weeks(today, args.backfill)
        for mid, label, group, want in plan:
            if "source_spec" in want:
                async with SessionLocal() as s:
                    if not await _spec_has_data(s, tid, bid, want["source_spec"]):
                        print(f"  backfill SKIPPED {label}: its data hasn't synced yet "
                              f"(wait for the next Forum sync, then re-run --backfill)")
                        continue
            written = 0
            for ws, we in bweeks:
                async with SessionLocal() as s:
                    try:
                        v = await _value(s, tid, bid, group, want, ws, we)
                        await R._write(s, tid, mid, ws, v, f"wire:{label}", fill_only=False)
                        await s.commit()
                        written += 0 if v is R.UNAVAILABLE else 1
                    except Exception as e:  # noqa: BLE001 — one bad week must not stop the rest
                        await s.rollback()
                        print(f"  backfill {label} {ws}: {type(e).__name__}: {e}")
            print(f"  backfilled {label}: {written} of {len(bweeks)} week(s) had data "
                  f"(the rest read UNAVAILABLE and were left blank)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
