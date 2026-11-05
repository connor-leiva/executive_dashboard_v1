"""SkySlope's status strings, turned into the handful of states the metrics count.

SkySlope publishes no list of sale statuses, and the strings are free text, so this map is the spec's
table (docs/skyslope-integration-spec.md, "Status normalization") with Connor's two decisions applied
(SKYSLOPE-SPEC.md §8): a `Pre-contract` sale is NOT under contract, and an `Incomplete` listing is NOT
active. Each gets its own state, so either decision can be reversed in a workspace's config without a
re-pull.

NOTHING IS GUESSED. A string the map does not name becomes `unmapped`, which no metric counts and the
panel shows by name, so a new SkySlope status is a visible gap and not a silent miscount.

A WORKSPACE CAN OVERRIDE ANY OF IT through `tenant.config["skyslope_status_map"]`:
    {"listing": {"Incomplete": "active"}, "sale": {"Pre-contract": "under_contract"}}
Keys are matched without regard to case or spacing, values must be one of the states below, and an
override with any other value is ignored rather than trusted.
"""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import SkyslopeListing, SkyslopeSale, Tenant

CONFIG_KEY = "skyslope_status_map"

LISTING_STATES = ("active", "incomplete", "expired", "canceled", "converted", "unmapped")
SALE_STATES = ("pre_contract", "under_contract", "past_due", "closed", "canceled", "unmapped")

# Lowercased, spaces and slashes squeezed: see _key().
_LISTING = {
    "active": "active",
    "incomplete": "incomplete",          # decision 5: an unfinished file is not on the market
    "expired": "expired",
    "canceled/pend": "canceled",
    "canceled/app": "canceled",
    "canceledpending": "canceled",
    "canceled pending": "canceled",
    "canceled": "canceled",
    "cancelled": "canceled",
    "archived": "canceled",              # a listing archived without a sale ended without one
    "transaction": "converted",          # became a sale: the sale is what counts
}
_SALE = {
    "pending": "under_contract",
    "under contract": "under_contract",
    "pre-contract": "pre_contract",      # decision 4: shown separately, never counted as under contract
    "precontract": "pre_contract",
    "pre contract": "pre_contract",
    "expired": "past_due",               # the closing date passed and it did not close
    "closed": "closed",
    "archived": "closed",
    "sold": "closed",
    "canceled": "canceled",
    "cancelled": "canceled",
    "canceled/pend": "canceled",
    "canceled/app": "canceled",
    "canceledpending": "canceled",
    "canceled pending": "canceled",
    "canceled approved": "canceled",
}


def _key(raw) -> str:
    text = re.sub(r"\s+", " ", str(raw or "").strip().lower())
    return re.sub(r"\s*/\s*", "/", text)


def status_map(tenant_config: dict | None) -> dict[str, dict[str, str]]:
    """The defaults with this workspace's overrides on top. Overrides naming an unknown state are
    dropped: a typo in config must not invent a state the metrics then silently ignore."""
    over = (tenant_config or {}).get(CONFIG_KEY) or {}
    out = {"listing": dict(_LISTING), "sale": dict(_SALE)}
    for kind, states in (("listing", LISTING_STATES), ("sale", SALE_STATES)):
        for raw, state in ((over.get(kind) or {}) if isinstance(over.get(kind), dict) else {}).items():
            if state in states and _key(raw):
                out[kind][_key(raw)] = state
    return out


def normalize(kind: str, raw, mapping: dict[str, dict[str, str]] | None = None) -> str:
    """`listing` or `sale`, and SkySlope's string. Always a state, `unmapped` when nothing matched."""
    table = (mapping or status_map(None))[kind]
    return table.get(_key(raw), "unmapped")


def is_past_due(state: str | None, expected_close: dt.datetime | None, today: dt.date) -> bool:
    """SkySlope's own `Expired`, or a file still under contract whose closing date is behind us. The
    second is derived on read, because no status change marks the day a closing date passes."""
    if state == "past_due":
        return True
    return state == "under_contract" and expected_close is not None and expected_close.date() < today


async def apply(s: AsyncSession, tenant_id) -> dict[str, int]:
    """Bring every stored file's `status` in line with the current map. Runs after each sync and after
    a config change, so changing a decision never needs a re-pull. Returns how many rows moved, and
    the raw strings nothing matched, which the panel names."""
    cfg = (await s.execute(select(Tenant.config).where(Tenant.id == tenant_id))).scalar_one_or_none()
    mapping = status_map(cfg)
    moved, unmapped = 0, set()
    for kind, model in (("listing", SkyslopeListing), ("sale", SkyslopeSale)):
        rows = (await s.execute(select(model.status_raw, model.status).where(
            model.tenant_id == tenant_id).distinct())).all()
        by_raw: dict = {}
        for raw, current in rows:
            want = normalize(kind, raw, mapping)
            if want == "unmapped":
                unmapped.add(f"{kind}: {raw or '(blank)'}")
            if current != want:
                by_raw[raw] = want
        for raw, want in by_raw.items():
            cond = model.status_raw.is_(None) if raw is None else model.status_raw == raw
            result = await s.execute(update(model).where(model.tenant_id == tenant_id, cond).values(status=want))
            moved += result.rowcount or 0
    return {"moved": moved, "unmapped": sorted(unmapped)}
