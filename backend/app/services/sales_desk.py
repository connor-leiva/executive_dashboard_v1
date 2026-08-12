"""beCollective Sales Desk — the sync side (SPEC-becollective-salesdesk §6).

GHL holds only CURRENT state (Call Outcome is a single latest value; a rebook overwrites
Sales Rep / Booking ID). The Desk needs HISTORY, so it maintains an append-only SalesCall
event log built by DIFFING successive syncs: a rebook creates a NEW row and leaves the
prior row's outcome intact (so a no-show survives a later show). Every rate on the tab is
computed from this log, never from live GHL fields.

Read-only against GHL (GETs only). The six opportunity fields are read via the per-opp
DETAIL endpoint — the /opportunities/search list omits their values.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations import ghl
from ..models import MetricRecord, SalesCall, SalesCallChange, SalesRep

# ── config, not inlined (§12): the outcome vocabulary lives here once ──────────────────────
CANON_OUTCOMES = ("Showed", "No Show", "Cancelled", "Rescheduled")
DEFAULT_OUTCOME_MAP = {
    "showed": "Showed", "show": "Showed", "attended": "Showed",
    "no show": "No Show", "noshow": "No Show", "no-show": "No Show",
    "cancelled": "Cancelled", "canceled": "Cancelled", "cancel": "Cancelled",
    "rescheduled": "Rescheduled", "reschedule": "Rescheduled", "resched": "Rescheduled",
}
# semantic key -> the opportunity fieldKey suffix / name substrings used to resolve its id
_SC_FIELDS = {
    "sales_rep":    ("sales rep",),
    "booking_id":   ("booking id",),
    "call_time":    ("call time",),
    "call_outcome": ("call outcome",),
    "payment_type": ("payment type",),
    "cohort":       ("cohort",),
}
# "Friday, August 14, 2026 at 8:30 AM" — the prose GHL writes for Call Time (no offset).
_CALL_TIME_FMT = "%A, %B %d, %Y at %I:%M %p"


def _clean(v) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aw(d: dt.datetime | None) -> dt.datetime | None:
    """Make a stored datetime aware-UTC. SQLite returns naive (Postgres returns aware); treat
    naive as UTC so comparisons against an aware `now` never raise."""
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _tz(name: str) -> dt.tzinfo:
    try:
        return ZoneInfo(name or "America/Denver")
    except Exception:  # noqa: BLE001 — unknown tz → the spec default
        return ZoneInfo("America/Denver")


def norm_outcome(raw, omap: dict | None = None) -> str | None:
    """Map a raw Call Outcome value to a canonical outcome, or None. Config-driven (§12)."""
    if not raw:
        return None
    omap = omap or DEFAULT_OUTCOME_MAP
    key = str(raw).strip().lower()
    if key in omap:
        return omap[key]
    canon = str(raw).strip()
    return canon if canon in CANON_OUTCOMES else None


# The four payment types (§5). Keys match Launch.price_map; a value we can't map (or the
# "no 5.x workflow" Custom case) is surfaced in Data Health, never silently priced.
PAYMENT_TYPES = ("PIF", "Financed", "Monthly", "Custom")
_PAYMENT_MAP = {"pif": "PIF", "paid in full": "PIF", "financed": "Financed", "finance": "Financed",
                "monthly": "Monthly", "custom": "Custom"}


def norm_payment(raw) -> str | None:
    if not raw:
        return None
    key = str(raw).strip().lower()
    if key in _PAYMENT_MAP:
        return _PAYMENT_MAP[key]
    canon = str(raw).strip().title()
    return canon if canon in PAYMENT_TYPES else None


def parse_call_time(raw: str | None, tz: dt.tzinfo) -> tuple[dt.datetime | None, bool]:
    """Prose Call Time -> UTC. (utc_datetime|None, ok). ok=False only on a genuine parse
    failure (a warning); an empty input is ok=True (nothing to parse). Never guesses (§6.3)."""
    if not raw:
        return None, True
    try:
        naive = dt.datetime.strptime(str(raw).strip(), _CALL_TIME_FMT)
    except (ValueError, TypeError):
        return None, False
    return naive.replace(tzinfo=tz).astimezone(dt.timezone.utc), True


def salescall_field_ids(defs: list[dict], overrides: dict | None = None) -> dict:
    """Resolve the six opp fields to ids by fieldKey/name (not hardcoded ids — §12).
    `overrides` (semantic_key -> field_id) win, for a tenant whose fields are named oddly."""
    out: dict = dict(overrides or {})
    for f in defs:
        short = (f.get("fieldKey") or "").lower().split(".")[-1]
        nm = (f.get("name") or "").lower()
        for sem, names in _SC_FIELDS.items():
            if sem in out:
                continue
            if short == sem or nm in names or any(n in nm for n in names):
                out[sem] = f.get("id")
    return out


def _log(s: AsyncSession, tenant_id, sc: SalesCall, field: str, old, new) -> None:
    s.add(SalesCallChange(
        tenant_id=tenant_id, sales_call_id=sc.id, field=field,
        old_value=(str(old)[:160] if old is not None else None),
        new_value=(str(new)[:160] if new is not None else None)))


async def apply_sales_diff(s: AsyncSession, tenant_id, launch, records: list[dict],
                           tz: dt.tzinfo, now: dt.datetime | None = None) -> dict:
    """The append-only diff (pure DB, no network — the testable core). `records` are decoded
    per-opp dicts: {opportunity_id, contact_id, contact_name, booking_id, rep_email,
    call_time_raw, outcome_raw}. Returns Data-Health warning counts."""
    now = now or _utcnow()
    warn: Counter = Counter()

    existing: dict = {}
    by_opp: dict = {}
    for sc in (await s.execute(select(SalesCall).where(SalesCall.launch_id == launch.id))).scalars():
        existing[(sc.opportunity_id, sc.booking_id)] = sc
        by_opp.setdefault(sc.opportunity_id, []).append(sc)

    for r in records:
        oid = str(r["opportunity_id"])
        booking = _clean(r.get("booking_id"))
        rep = _clean(r.get("rep_email"))
        raw_ct = _clean(r.get("call_time_raw"))
        outcome = norm_outcome(r.get("outcome_raw"))
        payment = norm_payment(r.get("payment_type_raw"))
        ct_utc, ok = parse_call_time(raw_ct, tz)
        if raw_ct and not ok:
            warn["call_time_unparsed"] += 1

        if not booking:
            if not (outcome or rep):
                continue                       # early opt-in, nothing to log yet
            warn["booking_id_missing"] += 1    # counted, but still logged with booking_id=None (§6.2)

        key = (oid, booking)
        sc = existing.get(key)
        if sc is None:
            # New booking. A rebook supersedes any prior CURRENT row for this opp but LEAVES ITS
            # OUTCOME INTACT — that is what preserves the no-show through a rebook (§3, §6.2).
            for prior in by_opp.get(oid, []):
                if prior.is_current and prior.booking_id != booking:
                    prior.is_current = False
            sc = SalesCall(
                tenant_id=tenant_id, launch_id=launch.id, opportunity_id=oid,
                contact_id=_clean(r.get("contact_id")), contact_name=(_clean(r.get("contact_name")) or "")[:160],
                booking_id=booking, rep_email=rep, call_time_raw=raw_ct, call_time_utc=ct_utc,
                outcome=outcome, outcome_at=(now if outcome else None), payment_type=payment, is_current=True)
            s.add(sc)
            await s.flush()                    # assign sc.id so subsequent change-logs can reference it
            existing[key] = sc
            by_opp.setdefault(oid, []).append(sc)
            continue

        # Existing row — diff each field and log observed changes.
        if outcome and outcome != sc.outcome:
            if sc.outcome:                     # value→value: a rare correction; surface it
                warn["outcome_reversed"] += 1
            _log(s, tenant_id, sc, "outcome", sc.outcome, outcome)
            sc.outcome, sc.outcome_at = outcome, now
        if rep and rep != sc.rep_email:
            _log(s, tenant_id, sc, "rep_email", sc.rep_email, rep)
            sc.rep_email = rep
        if raw_ct and raw_ct != sc.call_time_raw:
            _log(s, tenant_id, sc, "call_time", sc.call_time_raw, raw_ct)
            sc.call_time_raw, sc.call_time_utc = raw_ct, ct_utc
        if payment and payment != sc.payment_type:
            _log(s, tenant_id, sc, "payment_type", sc.payment_type, payment)
            sc.payment_type = payment

    await s.commit()
    return dict(warn)


async def seed_reps_from_users(s: AsyncSession, tenant_id, users: list[dict]) -> int:
    """Upsert the rep roster from the GHL user directory (email → display name). Idempotent."""
    existing = {(r.email or "").lower(): r for r in
                (await s.execute(select(SalesRep).where(SalesRep.tenant_id == tenant_id))).scalars()}
    n = 0
    for u in users:
        email = (u.get("email") or "").strip()
        if not email:
            continue
        name = ((u.get("name") or "").strip() or email)[:80]
        cur = existing.get(email.lower())
        if cur is None:
            s.add(SalesRep(tenant_id=tenant_id, email=email, display_name=name, is_active=True))
            n += 1
        elif name != email and cur.display_name != name:
            cur.display_name = name            # refresh from the directory
    return n


async def sync_sales_calls(s: AsyncSession, tenant_id, business_id, token: str, location_id: str,
                           opps: list[dict], pipeline_name: dict, today: dt.date | None = None) -> dict:
    """Fetch side: resolve the active launch + field ids, seed reps from the directory, pull
    each launch-pipeline opp's DETAIL (the six fields), then apply_sales_diff. Returns warnings."""
    from .launch import active_launch_for

    launch = await active_launch_for(s, tenant_id, business_id, today or dt.date.today())
    if not launch:
        return {}
    tz = _tz(launch.default_tz)
    match = (launch.pipeline_match or "").lower().strip()

    fid = salescall_field_ids(await ghl.get_custom_fields(token, location_id, model="opportunity"))
    try:
        await seed_reps_from_users(s, tenant_id, await ghl.get_users(token, location_id))
        await s.commit()
    except Exception as e:  # noqa: BLE001 — roster seed is best-effort
        print(f"[ghl_bc] rep roster seed skipped: {e}", flush=True)

    launch_opps = [o for o in opps if not match or match in (pipeline_name.get(o.get("pipelineId")) or "").lower()]
    records = []
    for o in launch_opps:
        detail = await ghl.get_opportunity(token, location_id, str(o.get("id")))
        cf = ghl.opp_custom_values(detail or {})
        records.append({
            "opportunity_id": str(o.get("id")),
            "contact_id": str(o.get("contactId") or ""),
            "contact_name": ((detail.get("contact") or {}).get("name")) or ghl.opp_name(o),
            "booking_id": cf.get(fid.get("booking_id")),
            "rep_email": cf.get(fid.get("sales_rep")),
            "call_time_raw": cf.get(fid.get("call_time")),
            "outcome_raw": cf.get(fid.get("call_outcome")),
            "payment_type_raw": cf.get(fid.get("payment_type")),
        })

    warn = await apply_sales_diff(s, tenant_id, launch, records, tz)
    if launch.history_since is None:            # mark the first sync with the log live (§3)
        launch.history_since = _utcnow()
        await s.commit()
    return warn


# ── compute (§7 math → §8 payload) ─────────────────────────────────────────────────────────
def blended_price(price_map: dict, counts: dict | None = None) -> tuple[float, bool]:
    """§5: blended = sum(acv*count) / count(priced), over PRICED types only (acv not null), from
    real enrollment `counts`. Before any priced enrollments, fall back to the mean acv of the
    non-provisional priced types (a planning blend). Returns (blended, derived_from_provisional).
    This is the ONE figure shared with the Launch tab (§7) — compute it here, once."""
    pm = price_map or {}
    priced = {t: (v or {}) for t, v in pm.items() if (v or {}).get("acv") is not None}
    counts = counts or {}
    n = sum(counts.get(t, 0) for t in priced)
    if n:
        acv_sum = sum(priced[t]["acv"] * counts.get(t, 0) for t in priced)
        prov = any(priced[t].get("provisional") and counts.get(t, 0) for t in priced)
        return acv_sum / n, prov
    base = [priced[t]["acv"] for t in priced if not priced[t].get("provisional")] or \
           [priced[t]["acv"] for t in priced]
    return (sum(base) / len(base) if base else 0.0), False


def _pay_note(p: dict) -> str:
    acv, up, mo = p.get("acv"), p.get("upfront"), p.get("monthly")
    if acv is None:
        return "negotiated — not priced"
    if mo and up and up != acv:
        return f"${up:,.0f} down, then ${mo:,.0f}/mo"
    if mo and not up:
        return f"${mo:,.0f}/mo"
    return f"${acv:,.0f} at signing"


def _rate(num: int, den: int):
    return round(num / den * 100, 1) if den else None   # null (dash), never 0, on empty denom (§7)


async def compute_sales_desk(s: AsyncSession, tenant_id, launch, today: dt.date | None = None,
                             now: dt.datetime | None = None) -> dict:
    """The whole Sales Desk payload (§8), computed from the SalesCall event log — never live
    GHL fields. Deciding occupancy + wins come from the launch snapshot (bc_launch_opp)."""
    now = now or _utcnow()
    today = today or now.date()
    lid = str(launch.id)

    calls = list((await s.execute(select(SalesCall).where(SalesCall.launch_id == launch.id))).scalars())
    roster = {(r.email or "").lower(): r.display_name for r in
              (await s.execute(select(SalesRep).where(SalesRep.tenant_id == tenant_id))).scalars()}
    opp_recs = [r for r in (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.source == "ghl",
        MetricRecord.kind == "bc_launch_opp"))).scalars() if (r.meta or {}).get("launch_id") == lid]

    def dname(email):
        return roster.get((email or "").lower()) if email else None

    def unmapped(email):
        return bool(email and not roster.get((email or "").lower()))

    calls_by_opp: dict = {}
    for c in calls:
        calls_by_opp.setdefault(c.opportunity_id, []).append(c)

    def opp_rep(opp_id):
        """§6.4 — credit the rep on the most-recent-HELD call; fall back to the current field."""
        rows = calls_by_opp.get(opp_id, [])
        held = [r for r in rows if r.outcome == "Showed" and r.call_time_utc]
        if held:
            return max(held, key=lambda r: _aw(r.call_time_utc)).rep_email
        cur = [r for r in rows if r.is_current and r.rep_email] or [r for r in rows if r.rep_email]
        return cur[0].rep_email if cur else None

    reps: dict = {}

    def bucket(email):
        return reps.setdefault(email, dict(booked=0, held=0, noshow=0, cancelled=0, resched=0,
                                           upcoming=0, pending=0, inplay=0, won=0))

    for c in calls:                                    # every booking attempt counts, incl. superseded (§7)
        b = bucket(c.rep_email)
        b["booked"] += 1
        if c.outcome == "Showed":
            b["held"] += 1
        elif c.outcome == "No Show":
            b["noshow"] += 1
        elif c.outcome == "Cancelled":
            b["cancelled"] += 1
        elif c.outcome == "Rescheduled":
            b["resched"] += 1
        elif c.outcome is None and c.call_time_utc:
            b["upcoming" if _aw(c.call_time_utc) >= now else "pending"] += 1

    deciding = [r for r in opp_recs if (r.meta or {}).get("group") == "deciding"]
    won = [r for r in opp_recs if (r.meta or {}).get("group") == "enrolled"]
    for r in deciding:
        bucket(opp_rep(r.external_id))["inplay"] += 1
    for r in won:
        bucket(opp_rep(r.external_id))["won"] += 1

    # payment mix + blended, from the enrolled opps' current payment_type
    pay_counts: Counter = Counter()
    won_no_pay = 0
    for r in won:
        cur = [c for c in calls_by_opp.get(r.external_id, []) if c.is_current]
        pt = cur[0].payment_type if cur else None
        if pt in PAYMENT_TYPES:
            pay_counts[pt] += 1
        else:
            won_no_pay += 1
    price_map = launch.price_map or {}
    blended, blended_prov = blended_price(price_map, {t: pay_counts.get(t, 0) for t in PAYMENT_TYPES})
    deciding_count = len(deciding)
    priced_arr = sum((price_map.get(t) or {}).get("acv", 0) * pay_counts.get(t, 0)
                     for t in PAYMENT_TYPES if (price_map.get(t) or {}).get("acv") is not None)
    upfront_total = sum((price_map.get(t) or {}).get("upfront", 0) * pay_counts.get(t, 0)
                        for t in PAYMENT_TYPES if (price_map.get(t) or {}).get("upfront") is not None)

    tsum = {k: sum(b[k] for b in reps.values()) for k in
            ("booked", "held", "noshow", "cancelled", "resched", "upcoming", "pending", "won")}
    resolved = tsum["held"] + tsum["noshow"] + tsum["cancelled"]
    totals = dict(
        booked=tsum["booked"], held=tsum["held"], no_show=tsum["noshow"], cancelled=tsum["cancelled"],
        rescheduled=tsum["resched"], upcoming=tsum["upcoming"], pending=tsum["pending"],
        deciding=deciding_count, won=tsum["won"],
        show_rate=_rate(tsum["held"], resolved), close_rate=_rate(tsum["won"], tsum["held"]),
        blended=round(blended), blended_provisional=blended_prov,
        on_the_table=round(deciding_count * blended))

    rep_rows = []
    for email, b in reps.items():
        res = b["held"] + b["noshow"] + b["cancelled"]
        rep_rows.append(dict(
            rep_email=email, display_name=dname(email),
            booked=b["booked"], held=b["held"], noshow=b["noshow"], cancelled=b["cancelled"],
            resched=b["resched"], upcoming=b["upcoming"], inplay=b["inplay"], won=b["won"],
            show_rate=_rate(b["held"], res), close_rate=_rate(b["won"], b["held"]),
            unmapped=unmapped(email), unassigned=(email is None)))
    rep_rows.sort(key=lambda r: (r["unassigned"], -r["booked"]))   # Unassigned always shown, last

    # call board: today → +48h, current bookings, by call time; unscheduled last (§8)
    win_end = now + dt.timedelta(hours=48)
    day_start = dt.datetime.combine(today, dt.time.min, tzinfo=dt.timezone.utc)

    def call_row(c, uns=False):
        return dict(call_time_utc=(c.call_time_utc.isoformat() if c.call_time_utc else None),
                    contact_name=c.contact_name, rep_email=c.rep_email, display_name=dname(c.rep_email),
                    outcome=c.outcome, unscheduled=uns, unmapped=unmapped(c.rep_email))
    board = sorted((c for c in calls if c.is_current and c.call_time_utc
                    and day_start <= _aw(c.call_time_utc) <= win_end), key=lambda c: _aw(c.call_time_utc))
    unsched = [c for c in calls if c.is_current and c.call_time_utc is None and c.outcome is None]
    calls_out = [call_row(c) for c in board] + [call_row(c, True) for c in unsched]

    # no-show recovery: rebooked = a different booking exists for the same opportunity (§8)
    ns_out = []
    for c in (c for c in calls if c.outcome == "No Show"):
        ref = _aw(c.call_time_utc) or _aw(c.first_seen_at)
        ns_out.append(dict(
            contact_name=c.contact_name, rep_email=c.rep_email, display_name=dname(c.rep_email),
            days_since=max(0, (now - ref).days) if ref else 0,
            rebooked=any(o.booking_id != c.booking_id for o in calls_by_opp.get(c.opportunity_id, [])),
            opportunity_id=c.opportunity_id, unmapped=unmapped(c.rep_email)))
    ns_out.sort(key=lambda r: r["days_since"])

    payment_mix = [dict(type=t, count=pay_counts.get(t, 0), acv=(price_map.get(t) or {}).get("acv"),
                        upfront=(price_map.get(t) or {}).get("upfront"),
                        provisional=bool((price_map.get(t) or {}).get("provisional")),
                        note=_pay_note(price_map.get(t) or {})) for t in PAYMENT_TYPES]

    # Data Health (§8) — present issues only, in a fixed order
    warnings = []
    no_rep = sum(1 for c in calls if c.is_current and not c.rep_email)
    unmapped_emails = sorted({c.rep_email for c in calls if unmapped(c.rep_email)})
    pending_24 = sum(1 for c in calls if c.outcome is None and c.call_time_utc
                     and _aw(c.call_time_utc) < now - dt.timedelta(hours=24))
    unparsed = sum(1 for c in calls if c.call_time_raw and not c.call_time_utc)
    if no_rep:
        warnings.append(dict(n=no_rep, label="bookings with no rep",
                             hint="host unassigned in the portal — attribution blank"))
    if unmapped_emails:
        warnings.append(dict(n=len(unmapped_emails), label="rep not in the roster",
                             hint=(", ".join(unmapped_emails)[:110] + " — add a display name in settings")))
    if pending_24:
        warnings.append(dict(n=pending_24, label="outcomes pending > 24h",
                             hint="call time passed, no outcome logged yet"))
    if won_no_pay:
        warnings.append(dict(n=won_no_pay, label="won with no payment type",
                             hint="Custom Payment has no 5.x workflow — unpriced in ARR"))
    if unparsed:
        warnings.append(dict(n=unparsed, label="call times unparsed",
                             hint="prose format not recognized — shown as raw text"))

    return dict(
        history_since=(launch.history_since.isoformat() if launch.history_since else None),
        as_of=now.isoformat(), default_tz=launch.default_tz or "America/Denver",
        totals=totals, reps=rep_rows, calls=calls_out, no_shows=ns_out,
        payment_mix=payment_mix, upfront_total=round(upfront_total), priced_arr=round(priced_arr),
        warnings=warnings)
