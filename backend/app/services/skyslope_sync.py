"""SkySlope sync: from one access key's Bulk Export into the workspace's tables.

WHAT RUNS, per key per cycle (`plan`):
  - backfill, once: everything the key can see.
  - incremental: files modified since the watermark, less ten minutes of overlap.
  - daily reconcile: every open file again, because a date-driven flip such as Active to Expired may
    not touch `modifiedOn`.
  - weekly full pull: everything again, and the only pass that can notice a file has gone.
  - users, every cycle.

WHAT THE WATERMARK MEANS. `replicaLastCommitTimeUtc` read BEFORE the export, so it is a moment the
export is known to include. It moves only after a pull arrived whole. A pull that dropped or came up
short leaves it where it was and the next run asks again, which is safe because every row is upserted
by GUID. The first item of an export says how many files follow, and that count is checked.

WHAT IS STORED IS AN ALLOW-LIST, and that is the design rather than a detail. SkySlope sends buyers,
sellers, every contact on a file, earnest money, custom fields and each agent's home address, and the
spec's "never store" list names them. Stripping a list of bad keys fails open: a field SkySlope adds
next year would be written without anybody deciding to. Building each row from the keys below fails
closed, and tests/test_skyslope_sync.py plants a marker in every one of those fields and searches the
stored rows for it.

WHO A FILE BELONGS TO comes from its own agent fields, never from the key that returned it. Co-agent
sharing returns one file to several keys, and that key may not be the agent on it.

SkySlope's status strings have no published list and are stored as sent, in `status_raw`. After every
sync, services/skyslope_normalize.py sets `status` from them, and services/skyslope_project.py
rebuilds the business's `transaction` rows. Both run on what is stored, so a status-map change or
another key's rows are settled by the next sync, without a re-pull.

A SAMPLE CONNECTION (services/skyslope_sample.py) is read through this same code with a stand-in for
the HTTP client; `_api_for` is the only line that differs.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations import skyslope
from ..models import Integration, SkyslopeFileAgent, SkyslopeListing, SkyslopeSale, SkyslopeUser
from ..security import dec
from .upsert import upsert

STATE_KEY = "skyslope_state"
PROVIDER = "skyslope"

# Early enough to mean "everything". With no date filter at all the export returns only recent files.
EVERYTHING_SINCE = "2000-01-01T00:00:00Z"
TYPES = "summary,listing,sale"
# The statuses a file can still move out of, so the ones a date can change without touching modifiedOn.
OPEN_STATUSES = "active,pending,incomplete,expired,canceledPending"
OVERLAP = dt.timedelta(minutes=10)
DAILY = dt.timedelta(hours=24)
WEEKLY = dt.timedelta(days=7)
BATCH = 200
# A stream that drops is rerun at once, because a manual source may not be asked again for days.
PULL_ATTEMPTS = 2

_UTC = dt.timezone.utc
_LOCKS: dict = {}

# ── the allow-lists ───────────────────────────────────────────────────────────────────────────
# Top-level fields kept in `raw`, as sent. Anything not named here is never written.
_SALE_RAW = ("saleGuid", "listingGuid", "agentGuid", "coAgentGuids", "mlsNumber", "status", "statusId",
             "officeGuid", "dealType", "contractAcceptedDate", "contractAcceptanceDate",
             "escrowClosingDate", "actualClosingDate", "deadDate", "createdOn", "modifiedOn",
             "salePrice", "listingPrice")
_LISTING_RAW = ("listingGuid", "agentGuid", "coAgentGuids", "coListingAgentGuid", "mlsNumber", "status",
                "officeGuid", "listingDate", "expirationDate", "deadDate", "createdOn", "modifiedOn",
                "listingPrice")
_PROPERTY = ("city", "state", "zip")        # never the street: SKYSLOPE-SPEC.md §8, decision 8
_STAGE = ("name",)
# Commission figures only. CommissionDTO's own `commissionBreakdown` is free text and is left out, and
# so is a breakdown line's `details`. A line's `name` stays: it is how the GCI field gets identified.
_COMMISSION = ("listingCommissionAmount", "listingCommissionPercent", "officeGrossCommission",
               "referralAmount", "referralAmountPercent", "saleCommissionPercent",
               "salesCommissionAmount", "transCoordinatorFee", "adminBrokerageComp")
_SPLIT = ("agentGuid", "amount", "percentage")
_BREAKDOWN = ("name", "amount")


def _pick(source, keys) -> dict:
    return {k: source[k] for k in keys if k in source} if isinstance(source, dict) else {}


def _pick_all(items, keys) -> list[dict]:
    return [_pick(i, keys) for i in items if isinstance(i, dict)] if isinstance(items, list) else []


# ── values ────────────────────────────────────────────────────────────────────────────────────
def _guid(value) -> str | None:
    """Lowercase, so a join cannot miss on case. All zeros is .NET's empty GUID, which means none."""
    text = str(value).strip().lower() if value else ""
    return text[:64] if text and set(text) - {"0", "-"} else None


def _fit(model, column: str, value) -> str | None:
    """A free string cut to its column. SQLite ignores varchar lengths and Postgres raises on them,
    so a stage name nobody planned for would fail a production sync that every test had passed."""
    if value is None or value == "":
        return None
    length = getattr(model.__table__.c[column].type, "length", None)
    text = str(value).strip()
    return (text[:length] if length else text) or None


def _number(value, limit: float) -> Decimal | None:
    """A Decimal that fits its Numeric column, or None rather than a failed write."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = Decimal(str(value))
    except InvalidOperation:
        return None
    return out if out.is_finite() and abs(out) < limit else None


def _money(value) -> Decimal | None:
    return _number(value, 1e12)


def _int(value) -> int | None:
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def normalize_email(raw) -> str | None:
    """Lowercase, trimmed, without the suffix SkySlope adds to a deactivated person's address."""
    email = str(raw or "").strip().lower()
    for suffix in ("_deleted", "_deactivated"):
        if email.endswith(suffix):
            email = email[: -len(suffix)]
    return email or None


def _canonical(value):
    """The same content in the same order. No list here has a meaningful order, and a false change
    would break the promise that an immediate second run changes nothing."""
    if isinstance(value, dict):
        return {k: _canonical(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return sorted((_canonical(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    return value


def content_hash(raw: dict, commission: dict | None) -> str:
    text = json.dumps(_canonical({"raw": raw, "commission": commission}), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode()).hexdigest()


# ── mapping ───────────────────────────────────────────────────────────────────────────────────
def _commission(item: dict) -> dict | None:
    out = {"commission": _pick(item.get("commission"), _COMMISSION),
           "splits": _pick_all(item.get("commissionSplits"), _SPLIT),
           "breakdowns": _pick_all(item.get("commissionBreakdowns"), _BREAKDOWN)}
    return out if any(out.values()) else None


def _common(model, item: dict, keys: tuple) -> dict:
    """The columns a listing and a sale share, built only from allow-listed fields."""
    raw = _pick(item, keys)
    prop = _pick(item.get("property"), _PROPERTY)
    stage = _pick(item.get("stage"), _STAGE)
    if prop:
        raw["property"] = prop
    if stage:
        raw["stage"] = stage
    commission = _commission(item)
    return {
        "agent_guid": _guid(item.get("agentGuid")),
        "status_raw": _fit(model, "status_raw", item.get("status")),
        "stage_name": _fit(model, "stage_name", stage.get("name")),
        "office_guid": _guid(item.get("officeGuid")),
        "list_price": _money(item.get("listingPrice")),
        "city": _fit(model, "city", prop.get("city")),
        "state": _fit(model, "state", prop.get("state")),
        "zip": _fit(model, "zip", prop.get("zip")),
        "mls_number": _fit(model, "mls_number", item.get("mlsNumber")),
        "canceled_at": skyslope.parse_ts(item.get("deadDate")),
        "source_created_at": skyslope.parse_ts(item.get("createdOn")),
        "source_modified_at": skyslope.parse_ts(item.get("modifiedOn")),
        "commission_raw": commission,
        "raw": raw,
        "content_hash": content_hash(raw, commission),
    }


def _agents(kind: str, guid: str, item: dict) -> list[dict]:
    """One row per (person, role) on this file. A person repeated within a role is merged, because
    that is the table's key and a commission split can name somebody twice."""
    found: dict[tuple, Decimal | None] = {}

    def add(user, role, pct=None):
        who = _guid(user)
        if not who:
            return
        pair = (who, role)
        if pct is not None and found.get(pair) is not None:
            pct = found[pair] + pct
        found[pair] = pct if pct is not None else found.get(pair)

    add(item.get("agentGuid"), "primary")
    for co in item.get("coAgentGuids") or []:
        add(co, "co_agent")
    if kind == "listing":
        add(item.get("coListingAgentGuid"), "co_listing")
    for split in item.get("commissionSplits") or []:
        if isinstance(split, dict):
            add(split.get("agentGuid"), "split", _number(split.get("percentage"), 1000))
    return [{"file_kind": kind, "file_guid": guid, "user_guid": who, "role": role,
             "split_pct": pct if pct is not None and abs(pct) < 1000 else None}
            for (who, role), pct in found.items()]


def map_listing(item: dict) -> tuple[dict, list[dict]] | None:
    guid = _guid(item.get("listingGuid"))
    if not guid:
        return None
    columns = {"listing_guid": guid, **_common(SkyslopeListing, item, _LISTING_RAW),
               "listed_at": skyslope.parse_ts(item.get("listingDate")),
               "expires_at": skyslope.parse_ts(item.get("expirationDate"))}
    return columns, _agents("listing", guid, item)


def map_sale(item: dict) -> tuple[dict, list[dict]] | None:
    guid = _guid(item.get("saleGuid"))
    if not guid:
        return None
    columns = {"sale_guid": guid, **_common(SkyslopeSale, item, _SALE_RAW),
               "listing_guid": _guid(item.get("listingGuid")),
               "status_id": _int(item.get("statusId")),
               "deal_side": _fit(SkyslopeSale, "deal_side", item.get("dealType")),
               "sale_price": _money(item.get("salePrice")),
               "contract_accepted_at": skyslope.parse_ts(
                   item.get("contractAcceptedDate") or item.get("contractAcceptanceDate")),
               "expected_close_at": skyslope.parse_ts(item.get("escrowClosingDate")),
               "closed_at": skyslope.parse_ts(item.get("actualClosingDate"))}
    return columns, _agents("sale", guid, item)


def map_user(item: dict) -> dict | None:
    """Name, email, type and offices. /api/users also carries a home address and phones."""
    guid = _guid(item.get("userGuid"))
    if not guid:
        return None
    raw_email = str(item.get("email") or "").strip()
    name = " ".join(p for p in (str(item.get("firstName") or "").strip(),
                                str(item.get("lastName") or "").strip()) if p)
    offices = item.get("officeGuids")
    return {"user_guid": guid, "name": _fit(SkyslopeUser, "name", name),
            "email_raw": _fit(SkyslopeUser, "email_raw", raw_email),
            "email_norm": _fit(SkyslopeUser, "email_norm", normalize_email(raw_email)),
            "user_type": _fit(SkyslopeUser, "user_type", item.get("userType")),
            "office_guids": sorted(g for g in (_guid(o) for o in offices) if g) if isinstance(offices, list) else None,
            "deactivated": raw_email.lower().endswith(("_deleted", "_deactivated"))}


# ── state ─────────────────────────────────────────────────────────────────────────────────────
def state_of(integ: Integration) -> dict:
    """A COPY. Writing into the dict SQLAlchemy loaded makes the UPDATE a silent no-op, because the
    new value compares equal to the "old" one it just mutated."""
    return dict((integ.config or {}).get(STATE_KEY) or {})


def _save(integ: Integration, state: dict) -> None:
    integ.config = {**(integ.config or {}), STATE_KEY: dict(state)}


def _iso(ts: dt.datetime) -> str:
    return ts.astimezone(_UTC).isoformat()


def _due(last: str | None, every: dt.timedelta, now: dt.datetime) -> bool:
    at = skyslope.parse_ts(last)
    return at is None or now - at >= every


def plan(state: dict, now: dt.datetime) -> list[str]:
    """Which pulls this cycle owes. A full pull covers everything the other two would have."""
    if not state.get("backfilled_at") or not state.get("watermark"):
        return ["backfill"]
    if _due(state.get("last_full_at"), WEEKLY, now):
        return ["full"]
    return ["incremental", "daily"] if _due(state.get("last_daily_at"), DAILY, now) else ["incremental"]


def filters(kind: str, state: dict) -> dict:
    if kind in ("backfill", "full"):
        return {"createdAfter": EVERYTHING_SINCE, "type": TYPES}
    if kind == "daily":
        return {"createdAfter": EVERYTHING_SINCE, "status": OPEN_STATUSES, "type": TYPES}
    since = skyslope.parse_ts(state["watermark"])
    return {"modifiedAfter": skyslope.fmt_ts(since - OVERLAP), "type": TYPES}


def creds_for(integ: Integration) -> skyslope.Creds:
    blob = json.loads(dec(integ.access_token_enc)) if integ.access_token_enc else {}
    if not (blob.get("access_key") and blob.get("access_secret")):
        raise skyslope.SkySlopeError(None, message="This connection has no access key")
    if not skyslope.configured():
        raise skyslope.SkySlopeError(None, message="This deployment holds no SkySlope application credentials")
    return skyslope.make_creds(blob["access_key"], blob["access_secret"])


# ── writing ───────────────────────────────────────────────────────────────────────────────────
async def _write_files(s: AsyncSession, tenant_id, business_id, model, guid_column: str,
                       mapped: list[tuple[dict, list[dict]]], stamp: dt.datetime) -> int:
    """One batch. Returns how many rows actually changed.

    Upserted rather than inserted, so a second key returning the same file at the same moment is a
    no-op rather than an IntegrityError. Every row in the batch has its last-seen moved, and only a
    row whose content moved is rewritten, so `changed` means what a person would mean by it.
    """
    latest = {cols[guid_column]: (cols, agents) for cols, agents in mapped}  # the source can repeat
    guid_col = getattr(model, guid_column)
    known = {row[0]: row for row in (await s.execute(
        select(guid_col, model.content_hash, model.removed_from_source_at).where(
            model.tenant_id == tenant_id, guid_col.in_(list(latest))))).all()}

    writes, write_agents, unchanged, returned = [], [], [], []
    for guid, (cols, agents) in latest.items():
        row = known.get(guid)
        if row is not None and row[1] == cols["content_hash"]:
            (returned if row[2] is not None else unchanged).append(guid)
            continue
        writes.append({"id": uuid.uuid4(), "tenant_id": tenant_id, "business_id": business_id,
                       **cols, "first_seen_at": stamp, "last_seen_at": stamp,
                       "removed_from_source_at": None})
        write_agents.extend(agents)

    if writes:
        # `first_seen_at` and `business_id` are the row's history, so a rewrite leaves them alone.
        await upsert(s, model, writes, keys=["tenant_id", guid_column],
                     update=[*(c for c in writes[0] if c not in (
                         "id", "tenant_id", "business_id", guid_column, "first_seen_at"))])
        rewritten = [w[guid_column] for w in writes]
        await s.execute(delete(SkyslopeFileAgent).where(
            SkyslopeFileAgent.tenant_id == tenant_id, SkyslopeFileAgent.file_guid.in_(rewritten)))
        await upsert(s, SkyslopeFileAgent,
                     [{"id": uuid.uuid4(), "tenant_id": tenant_id, **a} for a in write_agents],
                     keys=["tenant_id", "file_guid", "user_guid", "role"], update=["file_kind", "split_pct"])
    if unchanged:
        await s.execute(update(model).where(model.tenant_id == tenant_id, guid_col.in_(unchanged))
                        .values(last_seen_at=stamp))
    if returned:
        await s.execute(update(model).where(model.tenant_id == tenant_id, guid_col.in_(returned))
                        .values(last_seen_at=stamp, removed_from_source_at=None))
    await s.commit()
    return len(writes) + len(returned)


async def _pull(s: AsyncSession, tenant_id, integ: Integration, api, kind: str, state: dict,
                stamp: dt.datetime) -> dict:
    """One export, streamed into the tables in batches, so a large one holds little in memory and a
    dropped one keeps what it already wrote. Raises if it did not arrive whole."""
    summary = None
    listings, sales = [], []
    got = {"listing": 0, "sale": 0}
    changed = skipped = 0

    async def flush():
        nonlocal changed
        if listings:
            changed += await _write_files(s, tenant_id, integ.business_id, SkyslopeListing, "listing_guid", listings, stamp)
            listings.clear()
        if sales:
            changed += await _write_files(s, tenant_id, integ.business_id, SkyslopeSale, "sale_guid", sales, stamp)
            sales.clear()

    async for item in api.files(**filters(kind, state)):
        object_type = str((item or {}).get("objectType") or "").lower()
        if object_type == "summary":
            summary = item
            continue
        mapper = {"listing": map_listing, "sale": map_sale}.get(object_type)
        mapped = mapper(item) if mapper else None
        if mapped is None:
            skipped += 1
            continue
        got[object_type] += 1
        (listings if object_type == "listing" else sales).append(mapped)
        if len(listings) >= BATCH or len(sales) >= BATCH:
            await flush()
    await flush()

    if summary is None:
        # Promised as "always first". Without it a short read cannot be told from a complete one, so
        # the watermark does not get to move on a guess.
        raise skyslope.SkySlopeStreamError("The export arrived with no summary, so it could not be checked")
    expected = {"listing": _int(summary.get("numberOfListings")), "sale": _int(summary.get("numberOfSales"))}
    for label, want in expected.items():
        if want is not None and got[label] + skipped < want:
            raise skyslope.SkySlopeStreamError(
                f"The export came up short: {got[label]} {label}s of the {want} it announced")
    return {"kind": kind, "listings": got["listing"], "sales": got["sale"], "changed": changed, "skipped": skipped}


async def _sync_users(s: AsyncSession, tenant_id, api, stamp: dt.datetime) -> dict:
    """Refresh the people this key can see. An agent-level key may not be allowed to list them, which
    is a fact about its scope, so it is recorded and must not cost the run the files it did read."""
    try:
        rows = await api.users()
    except skyslope.SkySlopeError as e:
        if e.status == 403:
            return {"users": "denied"}
        raise
    mapped = {m["user_guid"]: m for m in (map_user(u) for u in rows if isinstance(u, dict)) if m}
    if not mapped:
        return {"users": 0, "users_changed": 0}
    known = {r.user_guid: r for r in (await s.execute(select(
        SkyslopeUser.user_guid, SkyslopeUser.name, SkyslopeUser.email_raw, SkyslopeUser.email_norm,
        SkyslopeUser.user_type, SkyslopeUser.office_guids, SkyslopeUser.deactivated).where(
        SkyslopeUser.tenant_id == tenant_id))).all()}
    fields = ("name", "email_raw", "email_norm", "user_type", "office_guids", "deactivated")
    writes, unchanged = [], []
    for guid, m in mapped.items():
        row = known.get(guid)
        if row is not None and all(getattr(row, f) == m[f] for f in fields):
            unchanged.append(guid)
        else:
            writes.append({"id": uuid.uuid4(), "tenant_id": tenant_id, **m,
                           "first_seen_at": stamp, "last_seen_at": stamp})
    await upsert(s, SkyslopeUser, writes, keys=["tenant_id", "user_guid"], update=[*fields, "last_seen_at"])
    for i in range(0, len(unchanged), 500):
        await s.execute(update(SkyslopeUser).where(
            SkyslopeUser.tenant_id == tenant_id, SkyslopeUser.user_guid.in_(unchanged[i:i + 500]))
            .values(last_seen_at=stamp))
    await s.commit()
    return {"users": len(mapped), "users_changed": len(writes)}


async def _mark_removed(s: AsyncSession, tenant_id, stamp: dt.datetime) -> int:
    """Flag files that no connected key returned on its latest full pull.

    A file dropping out of one key's results may only mean a share ended, so one key can never say a
    file is gone. The cutoff is the OLDEST full pull among the connected keys: a file last seen before
    that was missed by every one of them. A key that has never finished a full pull holds this back
    entirely, because it might be the only one that can see the file.
    """
    keys = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.provider == PROVIDER,
        Integration.status.in_(("connected", "error"))))).scalars().all()
    starts = [skyslope.parse_ts(state_of(k).get("last_full_at")) for k in keys]
    if not starts or any(at is None for at in starts):
        return 0
    cutoff = min(starts)
    removed = 0
    for model in (SkyslopeListing, SkyslopeSale):
        result = await s.execute(update(model).where(
            model.tenant_id == tenant_id, model.last_seen_at < cutoff,
            model.removed_from_source_at.is_(None)).values(removed_from_source_at=stamp))
        removed += result.rowcount or 0
    return removed


async def _run_pull(s, tenant_id, integ, api, kind, state, reading, now) -> dict:
    """One pull and what it earns. Only a whole export moves anything in `state`."""
    for attempt in range(PULL_ATTEMPTS):
        try:
            result = await _pull(s, tenant_id, integ, api, kind, state, now)
            break
        except skyslope.SkySlopeStreamError:
            if attempt == PULL_ATTEMPTS - 1:
                raise
    if kind in ("backfill", "full"):
        # `last_full_at` is the moment this cycle stamped on every row it saw, which is what lets
        # _mark_removed compare the two. The pull finishing later does not move it.
        state.update(watermark=reading, last_full_at=_iso(now), last_daily_at=_iso(now))
        state.setdefault("backfilled_at", _iso(now))
    elif kind == "incremental":
        state["watermark"] = reading
    else:
        state["last_daily_at"] = _iso(now)
    _save(integ, state)
    await s.commit()
    return result


def _api_for(integ: Integration):
    """The real client, or for a sample connection the stand-in that never touches the network. Only
    the source of the items differs: everything after this line is the same code for both."""
    from .skyslope_sample import SampleApi, anchor_of, is_sample
    if is_sample(integ):
        return SampleApi(anchor_of(integ))
    return skyslope.client(creds_for(integ))


async def sync_skyslope(s: AsyncSession, tenant_id, integ: Integration) -> tuple[int, dict]:
    """One cycle for one access key. Returns (files read, the numbers for the run's `stats`).

    `rows_changed` is the check on this: run it twice in a row and the second reports zero.
    """
    from . import skyslope_normalize, skyslope_project
    api_context = _api_for(integ)
    # Two clicks on Sync queue rather than stack, so a second backfill cannot start beside the first.
    async with _LOCKS.setdefault(integ.id, asyncio.Lock()):
        try:
            async with api_context as api:
                now = dt.datetime.now(_UTC)
                reading = await api.replica_timestamp()
                watermark = (reading or {}).get("replicaLastCommitTimeUtc") if isinstance(reading, dict) else None
                if not skyslope.parse_ts(watermark):
                    # Without this there is no safe point to resume from, so nothing is pulled.
                    raise skyslope.SkySlopeError(200, path="/api/files/replica-timestamp",
                                                 message="SkySlope's replica timestamp was missing")
                state = state_of(integ)
                pulls = [await _run_pull(s, tenant_id, integ, api, kind, state, watermark, now)
                         for kind in plan(state, now)]
                removed = 0
                # A full pull that returned nothing proves nothing: a key that lost its scope would
                # otherwise flag the whole workspace as gone overnight.
                if any(p["kind"] in ("backfill", "full") and (p["listings"] or p["sales"]) for p in pulls):
                    removed = await _mark_removed(s, tenant_id, now)
                    await s.commit()
                users = await _sync_users(s, tenant_id, api, now)
            # Phase 4 and the projection run on what is stored, not on this pull, so they also settle
            # rows another key wrote and a status-map change made since.
            statuses = await skyslope_normalize.apply(s, tenant_id)
            projected = await skyslope_project.project(s, tenant_id)
            await s.commit()
        except SQLAlchemyError:
            # The session holds a failed statement, and the caller still has to record the failure.
            await s.rollback()
            await s.refresh(integ)
            raise
    seen = sum(p["listings"] + p["sales"] for p in pulls)
    return seen, {"passes": pulls, "rows_seen": seen, "rows_changed": sum(p["changed"] for p in pulls),
                  "removed": removed, "watermark": watermark, "lag_seconds": reading.get("lagSeconds"),
                  "integration_id": str(integ.id), "statuses_moved": statuses["moved"],
                  "unmapped_statuses": statuses["unmapped"], "projected": projected, **users}
