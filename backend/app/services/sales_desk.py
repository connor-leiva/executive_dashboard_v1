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
from ..models import SalesCall, SalesCallChange, SalesRep

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
                outcome=outcome, outcome_at=(now if outcome else None), is_current=True)
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
        })

    warn = await apply_sales_diff(s, tenant_id, launch, records, tz)
    if launch.history_since is None:            # mark the first sync with the log live (§3)
        launch.history_since = _utcnow()
        await s.commit()
    return warn
