"""Scorecard routing engine (SCORECARD-CONFIG-SPEC Phase 2).

Turns a metric's auto-sync from a hand-written Python resolver into a DECLARATIVE spec the UI can
build. A spec names a synced dataset, the date column that anchors the weekly window, a list of
whitelisted filters, an aggregation, and how to attribute rows to an office:

    {"source": "sisu", "dataset": "transaction", "date_field": "contract_date",
     "filters": [{"field": "sale_price", "op": "gt", "value": 0}],
     "aggregate": {"fn": "count"}, "attribution": "office"}

`run_spec(...)` interprets it against the synced tables and returns the SAME three outcomes the
hardcoded resolvers do — a number, a real `None` (looked, nothing this week), or `UNAVAILABLE`
(could not look: feed down / roster unsynced / misconfigured). The last distinction is the one that
once erased weeks of history, so the engine is careful to return `UNAVAILABLE`, never a wrong `0`,
whenever it can't actually look.

Safety: the UI can only pick from the DATASET REGISTRY below, and every field/op is whitelisted —
a spec can never reach a column or operator that isn't declared here, so none of this interpolates
user input into SQL. `RESOLVER_SPECS` holds the declarative twin of each hardcoded count resolver;
the parity test proves they produce identical numbers, which is what makes flipping a metric from
`resolver_key` to `source_spec` safe.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select

from ..models import MetricRecord, Transaction
from .scorecard_resolvers import UNAVAILABLE, _office_agent_ids, _sisu_live


@dataclass(frozen=True)
class Dataset:
    """A synced table the UI can route a metric at. `fields` is the whitelist the engine will filter
    or aggregate on (name -> (column, type)); `date_fields` the valid weekly anchors. `attribution`
    says whether rows can be scoped to an office. `liveness` is the null≠zero gate — when the feed
    is not connected/synced the engine returns UNAVAILABLE instead of a misleading 0."""
    source: str
    key: str
    model: type
    date_fields: dict          # name -> column
    fields: dict               # name -> (column, type)   type in {"num", "str", "date", "bool"}
    attribution: tuple         # ("none",) or ("none", "office")
    liveness: object           # async (s, tenant_id, business_id) -> bool, or None (always live)
    label: str = ""


async def _sisu_liveness(s, tenant_id, business_id) -> bool:
    return await _sisu_live(s, tenant_id, business_id)


# ── the registry — the contract between what the UI offers and what the engine can run ────────────
_TXN = Dataset(
    source="sisu", key="transaction", model=Transaction, label="Sisu — transactions",
    date_fields={k: getattr(Transaction, k) for k in (
        "close_date", "contract_date", "appt_set_date", "appt_met_date",
        "signed_date", "lead_date", "listing_date", "expected_close_date")},
    fields={
        "status": (Transaction.status, "str"), "side": (Transaction.side, "str"),
        "sale_price": (Transaction.sale_price, "num"), "gci": (Transaction.gci, "num"),
        "agent_commission": (Transaction.agent_commission, "num"),
        "mortgage_vid": (Transaction.mortgage_vid, "num"), "title_vid": (Transaction.title_vid, "num"),
        "sisu_status_code": (Transaction.sisu_status_code, "str"),
    },
    attribution=("none", "office"), liveness=_sisu_liveness)

_METRIC_RECORD = Dataset(
    source="ghl", key="metric_record", model=MetricRecord, label="GHL — members / revenue records",
    date_fields={"occurred_on": MetricRecord.occurred_on},
    fields={
        "kind": (MetricRecord.kind, "str"), "segment": (MetricRecord.segment, "str"),
        "status": (MetricRecord.status, "str"), "source": (MetricRecord.source, "str"),
        "amount": (MetricRecord.amount, "num"),
    },
    attribution=("none",), liveness=None)   # no sub-office attribution; business/segment via filters

DATASETS: dict[str, Dataset] = {f"{d.source}.{d.key}": d for d in (_TXN, _METRIC_RECORD)}

_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "is_null", "not_null"}
_AGG_FNS = {"count", "sum", "count_distinct"}

# UI metadata: a stable display order and how many values each op takes (so the editor knows whether to
# render no value box, one, or a list). Kept beside _OPS so the two can't drift.
_OP_ORDER = ["eq", "ne", "gt", "gte", "lt", "lte", "in", "is_null", "not_null"]
_OP_ARITY = {"eq": "one", "ne": "one", "gt": "one", "gte": "one", "lt": "one", "lte": "one",
             "in": "list", "is_null": "none", "not_null": "none"}


def catalog() -> dict:
    """The whitelist, JSON-serialised — exactly what the Settings routing UI may offer. Everything here
    is what `validate_spec`/`run_spec` accept, so the UI can never build a spec the engine would reject."""
    return {
        "datasets": [
            {"source": d.source, "dataset": d.key, "label": d.label,
             "date_fields": list(d.date_fields.keys()),
             "fields": [{"name": n, "type": t} for n, (_col, t) in d.fields.items()],
             "attribution": list(d.attribution)}
            for d in DATASETS.values()
        ],
        "ops": [{"op": op, "arity": _OP_ARITY[op]} for op in _OP_ORDER],
        "aggregates": ["count", "count_distinct", "sum"],
    }


def _coerce(value, typ):
    if typ == "num":
        return value if isinstance(value, (int, float, Decimal)) else Decimal(str(value))
    if typ == "date":
        return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))
    if typ == "bool":
        return bool(value)
    return str(value)


def _predicate(col, op, value, typ):
    """A single whitelisted filter as a SQL predicate, or None if the op/value is unusable."""
    try:
        if op == "is_null":
            return col.is_(None)
        if op == "not_null":
            return col.is_not(None)
        if op == "in":
            return col.in_([_coerce(v, typ) for v in (value or [])])
        v = _coerce(value, typ)
        return {"eq": col == v, "ne": col != v, "gt": col > v, "gte": col >= v,
                "lt": col < v, "lte": col <= v}.get(op)
    except Exception:
        return None


def validate_spec(spec) -> list[str]:
    """Human-readable problems with a spec (empty = valid). For the Phase 3 UI and a save-time guard."""
    errs: list[str] = []
    if not isinstance(spec, dict):
        return ["spec must be an object"]
    ds = DATASETS.get(f"{spec.get('source')}.{spec.get('dataset')}")
    if ds is None:
        return [f"unknown source/dataset: {spec.get('source')}/{spec.get('dataset')}"]
    if spec.get("date_field") not in ds.date_fields:
        errs.append(f"date_field must be one of {sorted(ds.date_fields)}")
    for f in (spec.get("filters") or []):
        if f.get("field") not in ds.fields:
            errs.append(f"unknown filter field: {f.get('field')}")
        elif f.get("op") not in _OPS:
            errs.append(f"unknown op: {f.get('op')}")
        elif _predicate(ds.fields[f["field"]][0], f.get("op"), f.get("value"), ds.fields[f["field"]][1]) is None:
            errs.append(f"bad filter value for {f.get('field')} {f.get('op')}")
    agg = spec.get("aggregate") or {"fn": "count"}
    if agg.get("fn") not in _AGG_FNS:
        errs.append(f"aggregate.fn must be one of {sorted(_AGG_FNS)}")
    if agg.get("fn") in ("sum", "count_distinct"):
        fld = ds.fields.get(agg.get("field"))
        if fld is None:
            errs.append(f"aggregate.field must be one of {sorted(ds.fields)}")
        elif agg["fn"] == "sum" and fld[1] != "num":
            errs.append(f"sum needs a numeric field, not {agg.get('field')}")
    att = spec.get("attribution", "none")
    if att not in ds.attribution:
        errs.append(f"attribution {att!r} not supported by this dataset")
    return errs


async def run_spec(spec, s, tenant_id, business_id, week_start: dt.date, week_end: dt.date, group=None):
    """Interpret a source_spec → number | None | UNAVAILABLE. Mirrors the hardcoded resolvers'
    contract exactly (that is what the parity test locks in)."""
    ds = DATASETS.get(f"{(spec or {}).get('source')}.{(spec or {}).get('dataset')}") if isinstance(spec, dict) else None
    if ds is None:
        return UNAVAILABLE                                  # unknown dataset → cannot look
    if ds.liveness is not None and not await ds.liveness(s, tenant_id, business_id):
        return UNAVAILABLE                                  # feed not connected/synced
    date_col = ds.date_fields.get(spec.get("date_field"))
    if date_col is None:
        return UNAVAILABLE
    model = ds.model
    clauses = [model.tenant_id == tenant_id, model.business_id == business_id,
               date_col >= week_start, date_col <= week_end]
    for f in (spec.get("filters") or []):
        fd = ds.fields.get(f.get("field"))
        if fd is None:
            return UNAVAILABLE                              # misconfigured → never a wrong number
        pred = _predicate(fd[0], f.get("op"), f.get("value"), fd[1])
        if pred is None:
            return UNAVAILABLE
        clauses.append(pred)

    if spec.get("attribution") == "office":
        if "office" not in ds.attribution:
            return UNAVAILABLE
        sgid = (group or {}).get("sisu_group_id")
        if sgid is None:                                    # a per-office metric with no office (e.g. Overall)
            return UNAVAILABLE
        ids = await _office_agent_ids(s, tenant_id, sgid)
        if ids is None:                                     # roster not synced → could not look
            return UNAVAILABLE
        clauses.append(model.agent_id.in_(ids))             # empty office → IN () → a real 0

    agg = spec.get("aggregate") or {"fn": "count"}
    fn = agg.get("fn", "count")
    if fn == "count":
        return float(await s.scalar(select(func.count()).select_from(model).where(*clauses)) or 0)
    if fn == "count_distinct":
        fd = ds.fields.get(agg.get("field"))
        if fd is None:
            return UNAVAILABLE
        return float(await s.scalar(select(func.count(func.distinct(fd[0]))).where(*clauses)) or 0)
    if fn == "sum":
        fd = ds.fields.get(agg.get("field"))
        if fd is None or fd[1] != "num":
            return UNAVAILABLE
        return float(await s.scalar(select(func.coalesce(func.sum(fd[0]), 0)).where(*clauses)) or 0)
    return UNAVAILABLE


# ── declarative twins of the hardcoded count resolvers (parity-proven) ────────────────────────────
# Flipping a metric from resolver_key to one of these is safe once the parity test passes for it.
RESOLVER_SPECS: dict[str, dict] = {
    "ulrg_homes_closed": {
        "source": "sisu", "dataset": "transaction", "date_field": "close_date",
        "filters": [{"field": "status", "op": "eq", "value": "closed"},
                    {"field": "sale_price", "op": "gt", "value": 0}],
        "aggregate": {"fn": "count"}, "attribution": "none"},
    "ulrg_team_homes_closed": {
        "source": "sisu", "dataset": "transaction", "date_field": "close_date",
        "filters": [{"field": "status", "op": "eq", "value": "closed"},
                    {"field": "sale_price", "op": "gt", "value": 0}],
        "aggregate": {"fn": "count"}, "attribution": "office"},
    "ulrg_team_under_contract": {
        "source": "sisu", "dataset": "transaction", "date_field": "contract_date",
        "filters": [{"field": "sale_price", "op": "gt", "value": 0}],
        "aggregate": {"fn": "count"}, "attribution": "office"},
    "ulrg_team_appts_met": {
        "source": "sisu", "dataset": "transaction", "date_field": "appt_met_date",
        "filters": [], "aggregate": {"fn": "count"}, "attribution": "office"},
    "ulrg_team_signed": {
        "source": "sisu", "dataset": "transaction", "date_field": "signed_date",
        "filters": [], "aggregate": {"fn": "count"}, "attribution": "office"},
}
