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
import re
from collections import Counter
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations import ghl
from ..models import MetricRecord, SalesCall, SalesCallChange, SalesRep

# ── config, not inlined (§12): the outcome vocabulary lives here once ──────────────────────
# The four canonical outcomes. Everything downstream compares against THESE names, never a
# bare literal, so the vocabulary is defined in exactly one place.
OUT_SHOWED, OUT_NO_SHOW, OUT_CANCELLED, OUT_RESCHEDULED = "Showed", "No Show", "Cancelled", "Rescheduled"
CANON_OUTCOMES = (OUT_SHOWED, OUT_NO_SHOW, OUT_CANCELLED, OUT_RESCHEDULED)
DEFAULT_OUTCOME_MAP = {
    "showed": OUT_SHOWED, "show": OUT_SHOWED, "attended": OUT_SHOWED,
    "no show": OUT_NO_SHOW, "noshow": OUT_NO_SHOW, "no-show": OUT_NO_SHOW,
    "cancelled": OUT_CANCELLED, "canceled": OUT_CANCELLED, "cancel": OUT_CANCELLED,
    "rescheduled": OUT_RESCHEDULED, "reschedule": OUT_RESCHEDULED, "resched": OUT_RESCHEDULED,
    # Outcome-only values: the sales portal offers these but NO pipeline stage matches them,
    # so stage_map can't supply them (and putting them there would mis-bucket the funnel if a
    # stage were ever named this). Reaching a decision means the call happened.
    "decided no": OUT_SHOWED, "decided yes": OUT_SHOWED, "decided": OUT_SHOWED,
}
# Reps record the DISPOSITION they reached in Call Outcome, using the same vocabulary as the
# pipeline stages. That vocabulary is already configured once, in the launch's stage_map (and
# editable in Stage Grouping) — so the outcome field DERIVES from it rather than keeping a
# second hardcoded copy that would drift the moment someone renames a stage.
# Reaching any of these means the call was HELD; which disposition it was is already carried
# by the stage-driven Likely Yes / Likely No / Link Sent columns.
HELD_DISPOSITION_KEYS = ("deciding", "likely_yes", "likely_no", "link_sent", "nurture")


def held_phrases_for(launch) -> tuple:
    """This tenant's disposition vocabulary from stage_map — the phrases that, written into
    Call Outcome, mean the call was held. Matched as substrings, exactly like the stage
    classifier, so the configured phrase "likely yes" recognizes "Deciding - Likely Yes"."""
    smap = (getattr(launch, "stage_map", None) or {})
    return tuple(_outcome_key(p) for k in HELD_DISPOSITION_KEYS
                 for p in (smap.get(k) or []) if _outcome_key(p))


# semantic key -> the opportunity fieldKey suffix / name substrings used to resolve its id
_SC_FIELDS = {
    "sales_rep":    ("sales rep",),
    "booking_id":   ("booking id",),
    "call_time":    ("call time",),
    "call_outcome": ("call outcome",),
    "payment_type": ("payment type",),
    "cohort":       ("cohort",),
    # Where the call actually happens. Connor named it "Appointment Link" in GHL and fills it
    # from the booking webhook's Location Text; the aliases cover the obvious renames.
    "meeting_url":  ("appointment link", "meeting link", "meeting url"),
}
# "Friday, August 14, 2026 at 8:30 AM" — the prose GHL writes for Call Time. Some bookings append
# the attendee's zone ("… 8:30 AM EDT"); when present it's the REAL zone and we honor it.
_CALL_TIME_FMT = "%A, %B %d, %Y at %I:%M %p"
_TZ_ABBR = {                                          # US zone abbreviations → fixed UTC offset (hrs)
    "EDT": -4, "EST": -5, "CDT": -5, "CST": -6, "MDT": -6, "MST": -7, "PDT": -7, "PST": -8,
    "AKDT": -8, "AKST": -9, "HDT": -9, "HST": -10, "UTC": 0, "GMT": 0,
}
_TZ_SUFFIX = re.compile(r"\s+([A-Z]{2,4})$")


def _clean(v) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def title_name(name: str | None) -> str | None:
    """Standardize a lead name to Title Case for display: 'Dalila OROZCO' -> 'Dalila Orozco',
    'nina watson' -> 'Nina Watson'. Leaves intentional intercaps ('McKinnon', 'JaRelle') and
    short all-caps initials ('MJ', 'C.') alone, and capitalizes across hyphens/apostrophes
    ('bayer-carney' -> 'Bayer-Carney', "o'brien" -> "O'Brien"). Presentation only — the stored
    SalesCall.contact_name is untouched."""
    if not name or not name.strip():
        return name

    def token(tok: str) -> str:
        if any(c.islower() for c in tok) and any(c.isupper() for c in tok[1:]):
            return tok                                        # McKinnon, DeShawn, JaRelle
        if tok.replace(".", "").isupper() and len(tok.replace(".", "")) <= 2:
            return tok                                        # initials: MJ, JP, C.
        return re.sub(r"[A-Za-z]+", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), tok)

    return " ".join(token(t) for t in name.split(" "))


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


def _outcome_key(raw) -> str:
    """Normalize for lookup: case, and any separator reps type ('payment_link_sent',
    'Payment Link Sent', 'Deciding - Likely Yes') collapse to the same key."""
    return re.sub(r"[^a-z0-9]+", " ", str(raw).strip().lower()).strip()


def norm_outcome(raw, omap: dict | None = None, held_phrases: tuple = ()) -> str | None:
    """Map a raw Call Outcome value to a canonical outcome, or None. Config-driven (§12).

    `held_phrases` is the tenant's disposition vocabulary (see held_phrases_for) — reps write
    the disposition they reached, and reaching one means the call happened. Matched by
    substring, like stages, but only AFTER the explicit outcomes so a phrase can never
    shadow "No Show" / "Cancelled"."""
    if not raw:
        return None
    omap = omap or DEFAULT_OUTCOME_MAP
    key = _outcome_key(raw)
    normalized_map = {_outcome_key(k): v for k, v in omap.items()}
    if key in normalized_map:
        return normalized_map[key]
    for phrase, out in normalized_map.items():          # "No Show - left voicemail"
        if out in (OUT_NO_SHOW, OUT_CANCELLED) and phrase in key:
            return out
    for phrase in held_phrases:                          # the configured dispositions
        if phrase in key:
            return OUT_SHOWED
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
    failure (a warning); an empty input is ok=True (nothing to parse). A trailing zone
    abbreviation ("… 8:30 AM EDT") pins the real zone; otherwise the launch tz is assumed."""
    if not raw:
        return None, True
    s = str(raw).strip()
    tzinfo = tz
    m = _TZ_SUFFIX.search(s)                          # "AM"/"PM" fall through — not in _TZ_ABBR
    if m and m.group(1) in _TZ_ABBR:
        tzinfo = dt.timezone(dt.timedelta(hours=_TZ_ABBR[m.group(1)]))
        s = s[:m.start()].strip()
    try:
        naive = dt.datetime.strptime(s, _CALL_TIME_FMT)
    except (ValueError, TypeError):
        return None, False
    return naive.replace(tzinfo=tzinfo).astimezone(dt.timezone.utc), True


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
    # The tenant's own disposition vocabulary (Stage Grouping) — one source of truth for both
    # the pipeline stages and what reps type into Call Outcome.
    held_phrases = held_phrases_for(launch)

    existing: dict = {}
    by_opp: dict = {}
    for sc in (await s.execute(select(SalesCall).where(SalesCall.launch_id == launch.id))).scalars():
        existing[(sc.opportunity_id, sc.booking_id)] = sc
        by_opp.setdefault(sc.opportunity_id, []).append(sc)
        # Backfill: the diff below only re-parses a call time when its RAW STRING changes, so a
        # row stored before a parser improvement (e.g. zoned "… 8:30 AM EDT") would keep a NULL
        # call_time_utc forever. Re-parse stored raws that never resolved — including superseded
        # rows, which the per-record loop never revisits.
        if sc.call_time_raw and sc.call_time_utc is None:
            utc, ok = parse_call_time(sc.call_time_raw, tz)
            if ok and utc:
                sc.call_time_utc = utc

    async def _apply_one(r: dict) -> None:
        oid = str(r["opportunity_id"])
        booking = _clean(r.get("booking_id"))
        rep = _clean(r.get("rep_email"))
        raw_ct = _clean(r.get("call_time_raw"))
        outcome = norm_outcome(r.get("outcome_raw"), held_phrases=held_phrases)
        payment = norm_payment(r.get("payment_type_raw"))
        ct_utc, ok = parse_call_time(raw_ct, tz)
        if raw_ct and not ok:
            warn["call_time_unparsed"] += 1
        # A populated outcome we can't map used to be discarded silently, which is how 19 real
        # dispositions went missing. Count them so a new vocabulary shows up in the sync log.
        if _clean(r.get("outcome_raw")) and outcome is None:
            warn["outcome_unmapped"] += 1

        if not booking:
            if not (outcome or rep):
                return                         # early opt-in, nothing to log yet
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
            return

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

    # Each opp is isolated in a SAVEPOINT: one malformed record rolls back ONLY itself (counted
    # in warn) instead of aborting the whole run — a single bad opp used to discard every update.
    for r in records:
        try:
            async with s.begin_nested():
                await _apply_one(r)
        except Exception:                      # noqa: BLE001 — surface as a count, keep processing
            warn["record_error"] += 1

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
    records, fetch_failed = [], 0
    for o in launch_opps:
        detail = await ghl.get_opportunity(token, location_id, str(o.get("id")))
        if detail is None:                  # rate-limited / errored fetch (after retries). SKIP it —
            fetch_failed += 1               # a bare record would look like an empty opp and quietly
            continue                        # drop a real booking; leaving the prior row is correct.
        cf = ghl.opp_custom_values(detail)
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
    if fetch_failed:                        # visible in logs + Data Health — never a silent drop
        warn["opp_fetch_failed"] = fetch_failed
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


async def payment_counts_by_group(s: AsyncSession, tenant_id, launch) -> dict:
    """{group: Counter(four-type payment)} for committed + enrolled opps, from each opp's current
    SalesCall.payment_type. Shared by the Launch ARR (§9.3) and the Desk payment mix — one source
    of truth for how members pay. Empty for a group whose opps have no logged Payment Type yet."""
    lid = str(launch.id)
    groups = {r.external_id: (r.meta or {}).get("group") for r in
              (await s.execute(select(MetricRecord).where(
                  MetricRecord.tenant_id == tenant_id, MetricRecord.source == "ghl",
                  MetricRecord.kind == "bc_launch_opp"))).scalars()
              if (r.meta or {}).get("launch_id") == lid}
    pay = {c.opportunity_id: c.payment_type for c in
           (await s.execute(select(SalesCall).where(
               SalesCall.launch_id == launch.id, SalesCall.is_current.is_(True)))).scalars()
           if c.payment_type}
    out: dict = {}
    for opp, grp in groups.items():
        if grp in ("committed", "enrolled"):
            pt = pay.get(opp)
            if pt in PAYMENT_TYPES:
                out.setdefault(grp, Counter())[pt] += 1
    return out


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
    roster = {(r.email or "").lower(): (r.display_name, r.is_active) for r in
              (await s.execute(select(SalesRep).where(SalesRep.tenant_id == tenant_id))).scalars()}
    opp_recs = [r for r in (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.source == "ghl",
        MetricRecord.kind == "bc_launch_opp"))).scalars() if (r.meta or {}).get("launch_id") == lid]

    def dname(email):
        e = roster.get((email or "").lower()) if email else None
        return e[0] if e else None

    def unmapped(email):
        return bool(email and (email or "").lower() not in roster)

    def deactivated(email):
        e = roster.get((email or "").lower()) if email else None
        return bool(e and e[1] is False)

    calls_by_opp: dict = {}
    for c in calls:
        calls_by_opp.setdefault(c.opportunity_id, []).append(c)

    # The Call Outcome FIELD is unreliable — reps advance the card and often never fill it
    # (measured 2026-08-16: 22 opps sat in a post-call stage with the field blank). The STAGE
    # is the reliable signal: you cannot reach a post-call group without the call happening.
    # So a current call whose opp has advanced counts as HELD even with a blank field.
    HELD_IMPLYING_GROUPS = ("deciding", "committed", "enrolled")
    opp_group = {r.external_id: (r.meta or {}).get("group") for r in opp_recs}

    def effective_outcome(c):
        """The outcome to REPORT for a call: the logged value wins; otherwise the pipeline
        implies it. Only the CURRENT call can be upgraded, so a superseded no-show still
        survives a rebook (§3) — this never overwrites an explicit outcome."""
        if c.outcome:
            return c.outcome
        if c.is_current and opp_group.get(c.opportunity_id) in HELD_IMPLYING_GROUPS:
            return OUT_SHOWED
        return None

    def opp_rep(opp_id):
        """§6.4 — credit the rep on the most-recent-HELD call; fall back to the current field."""
        rows = calls_by_opp.get(opp_id, [])
        held = [r for r in rows if effective_outcome(r) == OUT_SHOWED and r.call_time_utc]
        if held:
            return max(held, key=lambda r: _aw(r.call_time_utc)).rep_email
        cur = [r for r in rows if r.is_current and r.rep_email] or [r for r in rows if r.rep_email]
        return cur[0].rep_email if cur else None

    reps: dict = {}

    def bucket(email):
        return reps.setdefault(email, dict(booked=0, held=0, noshow=0, cancelled=0,
                                           upcoming=0, pending=0, likely_yes=0, likely_no=0,
                                           link_sent=0, paid=0, won=0))

    for c in calls:                                    # every booking attempt counts, incl. superseded (§7)
        b = bucket(c.rep_email)
        b["booked"] += 1
        oc = effective_outcome(c)
        if oc == OUT_SHOWED:
            b["held"] += 1
        elif oc == OUT_NO_SHOW:
            b["noshow"] += 1
        elif oc == OUT_CANCELLED:
            b["cancelled"] += 1
        elif oc is None and c.call_time_utc:           # Rescheduled retired from display 2026-08-14;
            b["upcoming" if _aw(c.call_time_utc) >= now else "pending"] += 1   # legacy values still stored

    # Post-call dispositions (§8 rework): a deciding opp sits in Likely Yes / Likely No /
    # Link Sent by PIPELINE STAGE; a committed opp is Paid. Phrases live in stage_map (§12).
    smap = launch.stage_map or {}

    def disposition(stage):
        low = (stage or "").lower()
        for key in ("likely_yes", "likely_no", "link_sent"):
            if any(p in low for p in (smap.get(key) or [])):
                return key
        return None

    deciding = [r for r in opp_recs if (r.meta or {}).get("group") == "deciding"]
    committed = [r for r in opp_recs if (r.meta or {}).get("group") == "committed"]
    won = [r for r in opp_recs if (r.meta or {}).get("group") == "enrolled"]
    for r in deciding:
        d = disposition((r.meta or {}).get("stage"))
        if d:
            bucket(opp_rep(r.external_id))[d] += 1
    for r in committed:
        bucket(opp_rep(r.external_id))["paid"] += 1
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
            ("booked", "held", "noshow", "cancelled", "upcoming", "pending",
             "likely_yes", "likely_no", "link_sent", "paid", "won")}
    resolved = tsum["held"] + tsum["noshow"] + tsum["cancelled"]
    totals = dict(
        booked=tsum["booked"], held=tsum["held"], no_show=tsum["noshow"], cancelled=tsum["cancelled"],
        upcoming=tsum["upcoming"], pending=tsum["pending"],
        deciding=deciding_count, likely_yes=tsum["likely_yes"], likely_no=tsum["likely_no"],
        link_sent=tsum["link_sent"], paid=tsum["paid"], won=tsum["won"],
        show_rate=_rate(tsum["held"], resolved), close_rate=_rate(tsum["won"], tsum["held"]),
        blended=round(blended), blended_provisional=blended_prov,
        on_the_table=round(deciding_count * blended))

    # Deactivated reps (manage reps → active off) leave the leaderboard; their calls still count
    # in the totals above, and Data Health says so — nothing silently vanishes.
    hidden_calls = sum(b["booked"] for email, b in reps.items() if deactivated(email))
    rep_rows = []
    for email, b in reps.items():
        if deactivated(email):
            continue
        res = b["held"] + b["noshow"] + b["cancelled"]
        rep_rows.append(dict(
            rep_email=email, display_name=dname(email),
            booked=b["booked"], held=b["held"], noshow=b["noshow"], cancelled=b["cancelled"],
            upcoming=b["upcoming"], likely_yes=b["likely_yes"], likely_no=b["likely_no"],
            link_sent=b["link_sent"], paid=b["paid"], won=b["won"],
            show_rate=_rate(b["held"], res), close_rate=_rate(b["won"], b["held"]),
            unmapped=unmapped(email), unassigned=(email is None)))
    rep_rows.sort(key=lambda r: (r["unassigned"], -r["booked"]))   # Unassigned always shown, last

    # call board: today → +48h, current bookings, by call time; unscheduled last (§8)
    win_end = now + dt.timedelta(hours=48)
    day_start = dt.datetime.combine(today, dt.time.min, tzinfo=dt.timezone.utc)

    def call_row(c, uns=False):
        return dict(call_time_utc=(c.call_time_utc.isoformat() if c.call_time_utc else None),
                    contact_name=title_name(c.contact_name), rep_email=c.rep_email, display_name=dname(c.rep_email),
                    outcome=effective_outcome(c), unscheduled=uns, unmapped=unmapped(c.rep_email))
    board = sorted((c for c in calls if c.is_current and c.call_time_utc
                    and day_start <= _aw(c.call_time_utc) <= win_end), key=lambda c: _aw(c.call_time_utc))
    unsched = [c for c in calls if c.is_current and c.call_time_utc is None
               and effective_outcome(c) is None]
    calls_out = [call_row(c) for c in board] + [call_row(c, True) for c in unsched]

    # no-show recovery: rebooked = a different booking exists for the same opportunity (§8)
    ns_out = []
    for c in (c for c in calls if effective_outcome(c) == OUT_NO_SHOW):
        ref = _aw(c.call_time_utc) or _aw(c.first_seen_at)
        ns_out.append(dict(
            contact_name=title_name(c.contact_name), rep_email=c.rep_email, display_name=dname(c.rep_email),
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
    pending_24 = sum(1 for c in calls if effective_outcome(c) is None and c.call_time_utc
                     and _aw(c.call_time_utc) < now - dt.timedelta(hours=24))
    unparsed = sum(1 for c in calls if c.call_time_raw and not c.call_time_utc)
    if no_rep:
        warnings.append(dict(n=no_rep, label="bookings with no rep", key="dh.no_rep",
                             hint="host unassigned in the portal — attribution blank"))
    if unmapped_emails:
        warnings.append(dict(n=len(unmapped_emails), label="rep not in the roster", key="dh.unmapped",
                             hint=(", ".join(unmapped_emails)[:110] + " — add a display name in settings")))
    if pending_24:
        warnings.append(dict(n=pending_24, label="outcomes pending > 24h", key="dh.pending24",
                             hint="call time passed, no outcome logged yet"))
    if won_no_pay:
        warnings.append(dict(n=won_no_pay, label="won with no payment type", key="dh.won_no_pay",
                             hint="Custom Payment has no 5.x workflow — unpriced in ARR"))
    if unparsed:
        warnings.append(dict(n=unparsed, label="call times unparsed", key="dh.unparsed",
                             hint="prose format not recognized — shown as raw text"))
    if hidden_calls:
        warnings.append(dict(n=hidden_calls, label="calls from deactivated reps", key="dh.deactivated",
                             hint="hidden from the leaderboard — still counted in the totals"))

    return dict(
        history_since=(launch.history_since.isoformat() if launch.history_since else None),
        as_of=now.isoformat(), default_tz=launch.default_tz or "America/Denver",
        totals=totals, reps=rep_rows, calls=calls_out, no_shows=ns_out,
        payment_mix=payment_mix, upfront_total=round(upfront_total), priced_arr=round(priced_arr),
        warnings=warnings)


# ── drill (§8 — what's behind a number; same records/calc shapes the Launch drawer renders) ──
async def drill_sales_desk(s: AsyncSession, tenant_id, launch, metric: str, rep: str | None = None,
                           now: dt.datetime | None = None) -> dict:
    """Every figure on the Sales Desk resolves to its underlying calls/opps (records) or its
    formula (calc). `rep` scopes call-backed metrics to one leaderboard row ('__unassigned__'
    matches the null-rep bucket)."""
    from fastapi import HTTPException

    now = now or _utcnow()
    tzname = launch.default_tz or "America/Denver"
    calls = list((await s.execute(select(SalesCall).where(SalesCall.launch_id == launch.id))).scalars())
    roster = {(r.email or "").lower(): (r.display_name, r.is_active) for r in
              (await s.execute(select(SalesRep).where(SalesRep.tenant_id == tenant_id))).scalars()}

    def dname(email):
        e = roster.get((email or "").lower()) if email else None
        return e[0] if e else None

    def rep_label(email):
        return dname(email) or email or "Unassigned"

    if rep == "__unassigned__":
        calls = [c for c in calls if not c.rep_email]
    elif rep:
        calls = [c for c in calls if (c.rep_email or "").lower() == rep.lower()]
    scope = f" — {rep_label(None if rep == '__unassigned__' else rep)}" if rep else ""

    lid = str(launch.id)
    opp_recs = [r for r in (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.source == "ghl",
        MetricRecord.kind == "bc_launch_opp"))).scalars() if (r.meta or {}).get("launch_id") == lid]
    calls_by_opp: dict = {}
    for c in calls:
        calls_by_opp.setdefault(c.opportunity_id, []).append(c)

    # Same rule as compute_sales_desk: the stage implies the outcome when the field is blank,
    # so a drawer can never disagree with the number that opened it.
    HELD_IMPLYING_GROUPS = ("deciding", "committed", "enrolled")
    opp_group = {r.external_id: (r.meta or {}).get("group") for r in opp_recs}

    def effective_outcome(c):
        if c.outcome:
            return c.outcome
        if c.is_current and opp_group.get(c.opportunity_id) in HELD_IMPLYING_GROUPS:
            return OUT_SHOWED
        return None

    def opp_rep(opp_id):
        rows = calls_by_opp.get(opp_id, [])
        held = [r for r in rows if effective_outcome(r) == OUT_SHOWED and r.call_time_utc]
        if held:
            return max(held, key=lambda r: _aw(r.call_time_utc)).rep_email
        cur = [r for r in rows if r.is_current and r.rep_email] or [r for r in rows if r.rep_email]
        return cur[0].rep_email if cur else None

    def in_rep_scope(opp_id):
        if not rep:
            return True
        r = opp_rep(opp_id)
        return (r is None) if rep == "__unassigned__" else ((r or "").lower() == rep.lower())

    def records(title, subtitle, rows, columns):
        return {"metric": metric, "type": "records", "title": title, "subtitle": subtitle,
                "count": len(rows), "columns": columns, "rows": rows}

    def calc(title, value, steps, formula=None, note=None):
        return {"metric": metric, "type": "calc", "title": title, "value": str(value),
                "steps": steps, "formula": formula, "note": note}

    def call_row(c):
        t = _aw(c.call_time_utc)
        status = effective_outcome(c) or ("Upcoming" if t and t >= now else "Pending" if t else "Unscheduled")
        return {"contact": title_name(c.contact_name) or "-", "rep": rep_label(c.rep_email),
                "time": (t.isoformat()[:16].replace("T", " ") + " UTC") if t else (c.call_time_raw or "—"),
                "status": status, "current": c.is_current}

    CALL_COLS = ["contact", "rep", "time", "status", "current"]

    def call_records(title, rows_calls, subtitle=None):
        rows = [call_row(c) for c in sorted(rows_calls, key=lambda c: (_aw(c.call_time_utc) or now))]
        return records(title + scope, subtitle or f"{len(rows)} calls | launch to date", rows, CALL_COLS)

    def opp_rows(group, pay_filter=None, stage_pred=None):
        out = []
        for r in opp_recs:
            meta = r.meta or {}
            if meta.get("group") != group or not in_rep_scope(r.external_id):
                continue
            if stage_pred and not stage_pred(meta.get("stage")):
                continue
            cur = [c for c in calls_by_opp.get(r.external_id, []) if c.is_current]
            pay = cur[0].payment_type if cur else None
            if pay_filter and pay != pay_filter:
                continue
            out.append({"name": title_name(r.name) or "-", "rep": rep_label(opp_rep(r.external_id)),
                        "stage": meta.get("stage"), "payment": pay, "url": r.source_url})
        return out

    OPP_COLS = ["name", "rep", "stage", "payment", "url"]
    outcome_of = {"kpi.held": OUT_SHOWED, "kpi.no_show": OUT_NO_SHOW,
                  "kpi.cancelled": OUT_CANCELLED, "kpi.rescheduled": OUT_RESCHEDULED}
    held = [c for c in calls if effective_outcome(c) == OUT_SHOWED]
    noshow = [c for c in calls if effective_outcome(c) == OUT_NO_SHOW]
    cancelled = [c for c in calls if effective_outcome(c) == OUT_CANCELLED]
    price_map = launch.price_map or {}

    if metric == "kpi.booked":
        return call_records("Calls booked", calls,
                            "every booking attempt, incl. superseded rebooks | event log")
    if metric == "kpi.upcoming":
        return call_records("Upcoming calls",
                            [c for c in calls if c.is_current and effective_outcome(c) is None
                             and c.call_time_utc and _aw(c.call_time_utc) >= now])
    if metric == "kpi.pending":
        return call_records("Awaiting outcome",
                            [c for c in calls if effective_outcome(c) is None and c.call_time_utc
                             and _aw(c.call_time_utc) < now])
    if metric in outcome_of:
        oc = outcome_of[metric]
        return call_records(oc, [c for c in calls if effective_outcome(c) == oc])
    if metric == "kpi.show_rate":
        res = len(held) + len(noshow) + len(cancelled)
        v = f"{round(100 * len(held) / res)}%" if res else "—"
        return calc("Show rate" + scope, v,
                    [{"label": "Held (Showed)", "value": len(held)},
                     {"label": "No-show", "value": len(noshow)},
                     {"label": "Cancelled", "value": len(cancelled)},
                     {"label": "Resolved calls", "value": res}],
                    formula="held / (held + no-show + cancelled)",
                    note="Upcoming and pending calls are excluded from the denominator.")
    if metric == "kpi.close_rate":
        won_n = sum(1 for r in opp_recs if (r.meta or {}).get("group") == "enrolled"
                    and in_rep_scope(r.external_id))
        v = f"{round(100 * won_n / len(held))}%" if held else "—"
        return calc("Close rate" + scope, v,
                    [{"label": "Won (enrolled)", "value": won_n},
                     {"label": "Held calls", "value": len(held)}],
                    formula="won / held")
    if metric == "kpi.won":
        rows = opp_rows("enrolled")
        return records("Won" + scope, f"{len(rows)} enrolled | {launch.pipeline_match}", rows, OPP_COLS)
    if metric == "kpi.in_play":
        rows = opp_rows("deciding")
        return records("In play" + scope, f"{len(rows)} deciding | {launch.pipeline_match}", rows, OPP_COLS)
    if metric in ("kpi.likely_yes", "kpi.likely_no", "kpi.link_sent"):
        key = metric.split(".", 1)[1]
        smap = launch.stage_map or {}
        phrases = smap.get(key) or []
        rows = opp_rows("deciding", stage_pred=lambda st: any(p in (st or "").lower() for p in phrases))
        label = {"likely_yes": "Likely Yes", "likely_no": "Likely No", "link_sent": "Payment Link Sent"}[key]
        return records(label + scope, f"{len(rows)} deciding in this disposition", rows, OPP_COLS)
    if metric == "kpi.paid":
        rows = opp_rows("committed")
        return records("Paid — contract out" + scope,
                       f"{len(rows)} committed (cash received, unsigned)", rows, OPP_COLS)
    if metric == "kpi.on_the_table":
        deciding_n = sum(1 for r in opp_recs if (r.meta or {}).get("group") == "deciding"
                         and in_rep_scope(r.external_id))
        counts = await payment_counts_by_group(s, tenant_id, launch)
        blended, prov = blended_price(price_map, counts.get("enrolled") or Counter())
        return calc("On the table" + scope, f"${round(deciding_n * blended):,.0f}",
                    [{"label": "Deciding opps", "value": deciding_n},
                     {"label": "Blended seat value", "value": f"${round(blended):,.0f}"}],
                    formula="deciding × blended",
                    note="Blended is provisional until priced enrollments exist." if prov else None)
    if metric == "kpi.blended":
        counts = await payment_counts_by_group(s, tenant_id, launch)
        enr = counts.get("enrolled") or Counter()
        blended, prov = blended_price(price_map, enr)
        steps = [{"label": f"{t} × {enr.get(t, 0)}",
                  "value": f"${((price_map.get(t) or {}).get('acv') or 0):,.0f}"}
                 for t in PAYMENT_TYPES if (price_map.get(t) or {}).get("acv") is not None]
        return calc("Blended seat value", f"${round(blended):,.0f}", steps,
                    formula="sum(acv × count) / priced enrollments"
                            if sum(enr.values()) else "mean acv of non-provisional priced types",
                    note="Provisional — no priced enrollments yet." if prov else None)
    if metric.startswith("mix."):
        t = metric.split(".", 1)[1]
        if t not in PAYMENT_TYPES:
            raise HTTPException(404, "Unknown payment type")
        rows = opp_rows("enrolled", pay_filter=t)
        return records(f"{t} members", _pay_note(price_map.get(t) or {}), rows, OPP_COLS)
    if metric in ("money.upfront", "money.priced_arr"):
        counts = await payment_counts_by_group(s, tenant_id, launch)
        enr = counts.get("enrolled") or Counter()
        fld = "upfront" if metric == "money.upfront" else "acv"
        steps = [{"label": f"{t} × {enr.get(t, 0)}", "value": f"${((price_map.get(t) or {}).get(fld) or 0):,.0f}"}
                 for t in PAYMENT_TYPES if (price_map.get(t) or {}).get(fld) is not None]
        total = sum(((price_map.get(t) or {}).get(fld) or 0) * enr.get(t, 0) for t in PAYMENT_TYPES)
        return calc("Collected at signing" if fld == "upfront" else "Priced annual value",
                    f"${round(total):,.0f}", steps, formula=f"sum({fld} × enrolled count)",
                    note="Custom is unpriced and excluded.")
    if metric in ("recovery.chase", "recovery.all"):
        rows = []
        for c in noshow:
            rebooked = any(o.booking_id != c.booking_id for o in calls_by_opp.get(c.opportunity_id, []))
            if metric == "recovery.chase" and rebooked:
                continue
            ref = _aw(c.call_time_utc) or _aw(c.first_seen_at)
            rows.append({"contact": title_name(c.contact_name) or "-", "rep": rep_label(c.rep_email),
                         "days_since": max(0, (now - ref).days) if ref else 0, "rebooked": rebooked})
        title = "No-shows to chase" if metric == "recovery.chase" else "All no-shows"
        return records(title + scope, f"{len(rows)} | rebooked = a newer booking exists for the same opp",
                       rows, ["contact", "rep", "days_since", "rebooked"])
    if metric == "dh.no_rep":
        return call_records("Bookings with no rep",
                            [c for c in calls if c.is_current and not c.rep_email],
                            "the Sales Rep field is blank on these opps — fill it in GHL")
    if metric == "dh.unmapped":
        emails = sorted({c.rep_email for c in calls
                         if c.rep_email and not roster.get((c.rep_email or "").lower())})
        rows = [{"email": e, "calls": sum(1 for c in calls if (c.rep_email or "").lower() == e.lower())}
                for e in emails]
        return records("Reps not in the roster", "name them via manage reps on the leaderboard",
                       rows, ["email", "calls"])
    if metric == "dh.pending24":
        return call_records("Outcomes pending > 24h",
                            [c for c in calls if effective_outcome(c) is None and c.call_time_utc
                             and _aw(c.call_time_utc) < now - dt.timedelta(hours=24)],
                            "call time passed over a day ago with no outcome logged")
    if metric == "dh.won_no_pay":
        rows = [r for r in opp_rows("enrolled") if not r["payment"]]
        return records("Won with no payment type", "set Payment Type on the opp in GHL", rows, OPP_COLS)
    if metric == "dh.unparsed":
        rows = [{"contact": title_name(c.contact_name) or "-", "rep": rep_label(c.rep_email),
                 "raw_call_time": c.call_time_raw, "current": c.is_current}
                for c in calls if c.call_time_raw and not c.call_time_utc]
        return records("Call times unparsed", f"raw text stored as-is | launch tz {tzname}",
                       rows, ["contact", "rep", "raw_call_time", "current"])
    if metric == "dh.deactivated":
        deact = {e for e, v in roster.items() if v[1] is False}
        return call_records("Calls from deactivated reps",
                            [c for c in calls if (c.rep_email or "").lower() in deact],
                            "hidden from the leaderboard — still counted in the totals")
    raise HTTPException(404, "Unknown metric")


# ── rep share page (own numbers only — read-only, token-scoped; see routers/share.py) ─────
async def compute_rep_desk(s: AsyncSession, tenant_id, launch, rep_email: str,
                           today: dt.date | None = None, now: dt.datetime | None = None) -> dict:
    """One rep's slice of the Sales Desk for their personal share link: their leaderboard row,
    their call board, and their no-shows to chase. No other reps, no money totals — the page
    is distributable without granting dashboard access."""
    d = await compute_sales_desk(s, tenant_id, launch, today=today, now=now)
    low = (rep_email or "").lower()
    row = next((r for r in d["reps"] if (r["rep_email"] or "").lower() == low), None)
    if row is None:                     # roster row exists but no calls yet — an empty slate
        row = dict(rep_email=rep_email, display_name=None, booked=0, held=0, noshow=0,
                   cancelled=0, upcoming=0, likely_yes=0, likely_no=0, link_sent=0, paid=0,
                   won=0, show_rate=None, close_rate=None, unmapped=False, unassigned=False)
    return dict(
        launch_name=launch.name, as_of=d["as_of"], default_tz=d["default_tz"],
        display_name=row.get("display_name") or rep_email, rep=row,
        calls=[c for c in d["calls"] if (c.get("rep_email") or "").lower() == low],
        no_shows=[n for n in d["no_shows"] if (n.get("rep_email") or "").lower() == low])
