"""Axcion Books API (SPEC-books-module Part 4). All routes under /api/v1/books, gated by
the `books` tab; CFO-only actions (characterize, rules) additionally require owner/admin.
Payload shapes mirror axcion-books-v2.jsx."""
import datetime as dt
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import require_tab, require_role
from ..models import User
from ..services import (alloc_drivers, alloc_engine, alloc_rules, books, coa_alloc,
                        coa_balances, coa_map)
from ..services.audit import audit
from ..services.books_scan import create_ic_rule, update_ic_rule

router = APIRouter(prefix="/books", tags=["books"])

books_user = require_tab("books")           # members with the books grant + owners/admins


class RecatIn(BaseModel):
    category: str
    account_qbo_id: str | None = None


class EscalateIn(BaseModel):
    note: str | None = None


class BulkIn(BaseModel):
    ids: list[uuid.UUID]
    action: str                      # approve | acknowledge


class CharacterizeIn(BaseModel):
    characterization: str
    note: str | None = None


class RuleIn(BaseModel):
    label: str
    characterization: str
    from_business_id: uuid.UUID | None = None
    to_business_id: uuid.UUID | None = None
    monthly_cap: float | None = None
    active: bool = True


class RulePatch(BaseModel):
    label: str | None = None
    characterization: str | None = None
    monthly_cap: float | None = None
    active: bool | None = None


# ── Reads ─────────────────────────────────────────────────────────────────────
@router.get("")
async def get_home(period: str = "mtd", user: User = Depends(books_user),
                   s: AsyncSession = Depends(get_session)):
    home = await books.build_books_home(s, user.tenant_id, period)
    inv = await books.books_invariants(s, user.tenant_id)     # computes + logs the log line
    return {**home, "invariants_ok": all(v for k, v in inv.items() if k != "rail")}


@router.get("/pl")
async def get_pl(business: str = "all", period: str = "mtd", user: User = Depends(books_user),
                 s: AsyncSession = Depends(get_session)):
    return await books.build_books_pl(s, user.tenant_id, business, period)


@router.get("/queue")
async def get_queue(period: str = "mtd", state: str = "needs_approval", basis: str = "any",
                    auto_only: bool = False, weak_only: bool = False,
                    include_signed_off: bool = False, business: str = "all",
                    user: User = Depends(books_user), s: AsyncSession = Depends(get_session)):
    if state not in books.QUEUE_FILTERS:
        raise HTTPException(400, f"unknown state: {state}")
    if basis not in books.BASIS_FILTERS:
        raise HTTPException(400, f"unknown basis: {basis}")
    # `business` is checked in the service, which has this workspace's businesses. A fixed list
    # here would be one customer's companies again.
    try:
        return await books.build_books_queue(s, user.tenant_id, period=period, state=state,
                                             basis=basis, auto_only=auto_only,
                                             weak_only=weak_only,
                                             include_signed_off=include_signed_off,
                                             business=business)
    except books.UnknownBusiness as e:
        raise HTTPException(400, str(e))


@router.get("/ic")
async def get_ic(user: User = Depends(books_user), s: AsyncSession = Depends(get_session)):
    return await books.build_books_ic(s, user.tenant_id)


# ── Txn mutations (books-tab) ─────────────────────────────────────────────────
def _ok(t):
    if t is None:
        raise HTTPException(404, "Not found")
    return {"ok": True, "id": str(t.id), "scan_state": t.scan_state}


@router.post("/txn/{txn_id}/approve")
async def approve(txn_id: uuid.UUID, user: User = Depends(books_user),
                  s: AsyncSession = Depends(get_session)):
    return _ok(await books.approve_txn(s, user.tenant_id, user, txn_id))


@router.post("/txn/{txn_id}/acknowledge")
async def acknowledge(txn_id: uuid.UUID, user: User = Depends(books_user),
                      s: AsyncSession = Depends(get_session)):
    """Seen and accepted, without claiming it went through approval."""
    return _ok(await books.acknowledge_txn(s, user.tenant_id, user, txn_id))


@router.post("/txn/bulk")
async def bulk(body: BulkIn, user: User = Depends(books_user),
               s: AsyncSession = Depends(get_session)):
    try:
        return await books.bulk_review(s, user.tenant_id, user, body.ids, body.action)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/txn/{txn_id}/recategorize")
async def recategorize(txn_id: uuid.UUID, body: RecatIn, user: User = Depends(books_user),
                       s: AsyncSession = Depends(get_session)):
    return _ok(await books.recategorize_txn(s, user.tenant_id, user, txn_id,
                                            body.category, body.account_qbo_id))


@router.post("/txn/{txn_id}/escalate")
async def escalate(txn_id: uuid.UUID, body: EscalateIn, user: User = Depends(books_user),
                   s: AsyncSession = Depends(get_session)):
    return _ok(await books.escalate_txn(s, user.tenant_id, user, txn_id, body.note))


# ── Intercompany actions ──────────────────────────────────────────────────────
@router.post("/ic/{link_id}/characterize")           # CFO only
async def characterize(link_id: uuid.UUID, body: CharacterizeIn,
                       user: User = Depends(require_role("owner", "admin")),
                       s: AsyncSession = Depends(get_session)):
    try:
        link = await books.characterize_ic(s, user.tenant_id, user, link_id,
                                           body.characterization, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if link is None:
        raise HTTPException(404, "Not found")
    return {"ok": True, "id": str(link.id), "status": link.status}


@router.post("/ic/{link_id}/tie")
async def tie(link_id: uuid.UUID, user: User = Depends(books_user),
              s: AsyncSession = Depends(get_session)):
    link = await books.tie_ic(s, user.tenant_id, user, link_id)
    if link is None:
        raise HTTPException(404, "Not found")
    return {"ok": True, "id": str(link.id), "status": link.status}


@router.post("/ic/rules")                             # CFO only
async def create_rule(body: RuleIn, user: User = Depends(require_role("owner", "admin")),
                      s: AsyncSession = Depends(get_session)):
    try:
        rule = await create_ic_rule(
            s, user.tenant_id, body.label, body.characterization,
            from_business_id=body.from_business_id, to_business_id=body.to_business_id,
            monthly_cap=(Decimal(str(body.monthly_cap)) if body.monthly_cap is not None else None),
            active=body.active)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": str(rule.id)}


@router.patch("/ic/rules/{rule_id}")                  # CFO only
async def patch_rule(rule_id: uuid.UUID, body: RulePatch,
                     user: User = Depends(require_role("owner", "admin")),
                     s: AsyncSession = Depends(get_session)):
    fields = body.model_dump(exclude_none=True)
    if "monthly_cap" in fields:
        fields["monthly_cap"] = Decimal(str(fields["monthly_cap"]))
    try:
        rule = await update_ic_rule(s, user.tenant_id, rule_id, **fields)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if rule is None:
        raise HTTPException(404, "Not found")
    return {"ok": True, "id": str(rule.id)}


# ── Chart of accounts mapping (SPEC-coa-mapping-provenance 5.5) ───────────────
# Reads and per-account decisions are the bookkeeper's daily work, so they sit behind the
# books tab. Rules are structural — one pattern silently maps every account a future hire
# creates — so they take the same owner/admin gate as the intercompany rules above.

class MapIn(BaseModel):
    business_id: uuid.UUID
    qbo_account_ids: list[str]
    standard_account_id: uuid.UUID | None = None      # null unmaps


class IgnoreIn(BaseModel):
    business_id: uuid.UUID
    qbo_account_ids: list[str]
    reason: str


class CoaRuleIn(BaseModel):
    pattern: str
    standard_account_id: uuid.UUID
    business_id: uuid.UUID | None = None              # null = every entity in the tenant
    note: str | None = None


class CoaRulePatch(BaseModel):
    pattern: str | None = None
    standard_account_id: uuid.UUID | None = None
    note: str | None = None
    is_active: bool | None = None


@router.get("/coa")
async def coa_entities(user: User = Depends(books_user),
                       s: AsyncSession = Depends(get_session)):
    return {"entities": await coa_map.mapping_entities(s, user.tenant_id)}


@router.get("/coa/map")
async def coa_mapping(business_id: uuid.UUID, user: User = Depends(books_user),
                      s: AsyncSession = Depends(get_session)):
    try:
        return await coa_map.mapping_overview(s, user.tenant_id, business_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/coa/map")
async def coa_set_mapping(body: MapIn, user: User = Depends(books_user),
                          s: AsyncSession = Depends(get_session)):
    try:
        n = await coa_map.set_mapping(s, user.tenant_id, user, body.business_id,
                                      body.qbo_account_ids, body.standard_account_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "updated": n}


@router.post("/coa/ignore")
async def coa_ignore(body: IgnoreIn, user: User = Depends(books_user),
                     s: AsyncSession = Depends(get_session)):
    try:
        n = await coa_map.set_ignored(s, user.tenant_id, user, body.business_id,
                                      body.qbo_account_ids, body.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "updated": n}


@router.post("/coa/rules")                            # CFO only
async def coa_create_rule(body: CoaRuleIn,
                          user: User = Depends(require_role("owner", "admin")),
                          s: AsyncSession = Depends(get_session)):
    try:
        rule = await coa_map.create_rule(s, user.tenant_id, user, body.pattern,
                                         body.standard_account_id,
                                         business_id=body.business_id, note=body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": str(rule.id)}


@router.patch("/coa/rules/{rule_id}")                 # CFO only
async def coa_patch_rule(rule_id: uuid.UUID, body: CoaRulePatch,
                         user: User = Depends(require_role("owner", "admin")),
                         s: AsyncSession = Depends(get_session)):
    # exclude_unset, not exclude_none: `is_active: false` is a real edit and exclude_none
    # would drop it. Only the keys the client actually sent reach the service.
    fields = body.model_dump(exclude_unset=True)
    try:
        rule = await coa_map.update_rule(s, user.tenant_id, user, rule_id, **fields)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": str(rule.id)}


@router.delete("/coa/rules/{rule_id}")                # CFO only
async def coa_delete_rule(rule_id: uuid.UUID,
                          user: User = Depends(require_role("owner", "admin")),
                          s: AsyncSession = Depends(get_session)):
    try:
        await coa_map.delete_rule(s, user.tenant_id, user, rule_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ── The mapped statement + the tie-out (SPEC 5.3, 5.4) ────────────────────────
# Both reads. The statement REFUSES rather than rendering partially: 409 when accounts with
# activity are unmapped, 409 when the mapped total does not agree with QuickBooks. A 409 and
# not a 500 because neither is a fault — they are the module working, and the frontend has a
# specific, actionable panel to render for each.

def _period(period_start: dt.date | None, period_end: dt.date | None,
            period: str | None = None) -> tuple[dt.date, dt.date]:
    """Explicit dates win; otherwise resolve the dashboard's period string the same way every
    other view does, so the Books statement moves with the period selector at the top of the
    page rather than having a second, invisible notion of "now"."""
    if period_start and period_end:
        return period_start, period_end
    if period:
        from ..services.metrics import _pl_period
        return _pl_period(period)
    return coa_balances.balance_periods()[-1]


@router.get("/statement")
async def get_statement(business_id: uuid.UUID, period_start: dt.date | None = None,
                        period_end: dt.date | None = None, statement: str = "pl",
                        mode: str = "allocated", threshold_pct: float | None = None,
                        period: str | None = None,
                        user: User = Depends(books_user),
                        s: AsyncSession = Depends(get_session)):
    """`mode` picks which figure each line renders: `allocated` is what the books say with
    shared costs where they belong, `booked` is the entity's own activity. `threshold_pct` is a
    session override for how much intercompany makes a line worth flagging — it never writes
    back to coa_settings (SPEC 7.3)."""
    if mode not in ("allocated", "booked"):
        raise HTTPException(400, "mode must be 'allocated' or 'booked'")
    try:
        return await coa_balances.build_mapped_statement(
            s, user.tenant_id, business_id, _period(period_start, period_end, period),
            statement, mode=mode, threshold_pct=threshold_pct)
    except coa_balances.UnmappedAccountsError as e:
        raise HTTPException(409, detail={
            "error": "unmapped_accounts", "message": str(e),
            "business_id": str(e.business_id),
            # Every Decimal coerced, not just the one that happened to be noticed: FastAPI's
            # encoder does not reach inside an HTTPException detail, so a stray Decimal turns
            # a deliberate, actionable 409 into an opaque 500.
            "accounts": [{k: (float(v) if isinstance(v, Decimal) else v)
                          for k, v in a.items()} for a in e.accounts],
        })
    except coa_balances.TieOutError as e:
        raise HTTPException(409, detail={
            "error": "tie_out_failed", "message": str(e),
            "business_id": str(e.business_id), "delta": float(e.delta),
            "mapped": float(e.mapped), "booked": float(e.booked),
        })
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/coa/tie-out")
async def get_tie_out(period_start: dt.date | None = None, period_end: dt.date | None = None,
                      user: User = Depends(books_user),
                      s: AsyncSession = Depends(get_session)):
    """Every entity's invariants in one call — the Phase 3 gate, and a close-checklist step.
    Never raises; the point is to see which entity is broken and by how much."""
    return await coa_balances.tie_out_report(s, user.tenant_id,
                                             _period(period_start, period_end))


# ── Allocations (SPEC-coa-mapping-provenance 2.5, 6.6) ────────────────────────
# Reads sit behind the books tab. Rules are policy — one pattern decides how a shared cost is
# attributed across entities — so they take owner/admin, like every other structural rule here.

class AllocRuleIn(BaseModel):
    pattern: str
    pool_name: str
    source_business_id: uuid.UUID
    target_business_id: uuid.UUID | None = None       # null = every entity
    basis: str | None = None
    driver_source: str | None = None
    je_ref: str | None = None


class AllocRulePatch(BaseModel):
    pattern: str | None = None
    pool_name: str | None = None
    basis: str | None = None
    driver_source: str | None = None
    je_ref: str | None = None
    is_active: bool | None = None


# ── applied rules: versioned, never edited in place (ALLOCATION-RULES-SPEC Phase 3) ──

class AllocVersionIn(BaseModel):
    method: str                                        # direct|traced|driven|split|retained|ended
    effective_from: dt.date
    reach: str                                         # forward|from_date|retro
    rationale: str
    tier: int | None = None
    targets: list[dict] | None = None                  # [{business_id, share}], shares 0 to 1
    driver_id: uuid.UUID | None = None
    driver_subject: str | None = None
    source_spec: dict | None = None


class AppliedRuleIn(AllocVersionIn):
    scope_kind: str                                    # account|program|vendor
    pattern: str
    pool_name: str
    source_business_id: uuid.UUID                      # the POOL entity


class AllocEndIn(BaseModel):
    effective_from: dt.date
    rationale: str


# ── drivers: the monthly numbers a driven rule reads (ALLOCATION-RULES-SPEC 4.3) ──

class DriverIn(BaseModel):
    key: str
    label: str
    kind: str                                          # split | count
    unit: str | None = None                            # percent | people | members


class DriverCommitIn(BaseModel):
    period: dt.date
    rows: list[dict]                                   # {subject, business_id, value_kind, value}
    note: str | None = None


@router.get("/allocations/rules")
async def alloc_list_rules(period: str | None = None,
                           period_start: dt.date | None = None,
                           period_end: dt.date | None = None,
                           user: User = Depends(books_user),
                           s: AsyncSession = Depends(get_session)):
    """Both modes in one payload (SPEC 6).

    `rules` is the APPLIED list, because that is what the Allocations page renders. The observed
    list comes back under `observed`: it is the older shape and nothing but this route reads it
    today, so it is carried for completeness rather than for a caller. A first draft of this
    docstring claimed the Chart mapping view depended on `rules` keeping the observed shape;
    that was wrong, and a grep of `frontend/src` settled it in one line.

    Named for its neighbours (alloc_create_rule, alloc_patch_rule), and renamed once because
    `alloc_rules` shadowed the `services.alloc_rules` module at import: every applied-rule route
    then raised AttributeError on a function object, and only calling one showed it.
    """
    key = _period(period_start, period_end, period)
    applied = await alloc_engine.rules_payload(s, user.tenant_id, key, today=dt.date.today())
    money = coa_balances._money
    return {
        "meta": applied["meta"],
        "businesses": applied["businesses"],
        "months": applied["months"],
        "rules": [{**r,
                   "pool_amount": money(r["pool_amount"]),
                   "to_partners": money(r["to_partners"]),
                   "retained": money(r["retained"]),
                   "pending": [{**p, "amount": money(p["amount"])} for p in r["pending"]]}
                  for r in applied["rules"]],
        "observed": await coa_alloc.list_rules(s, user.tenant_id),
    }


@router.post("/allocations/rules")                    # CFO only
async def alloc_create_rule(body: AllocRuleIn,
                            user: User = Depends(require_role("owner", "admin")),
                            s: AsyncSession = Depends(get_session)):
    try:
        rule = await coa_alloc.create_rule(s, user.tenant_id, user, **body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": str(rule.id)}


async def _refuse_if_applied(s, tenant_id, rule_id) -> None:
    """An applied rule is never edited or deleted in place. 409 rather than 400: the request is
    well formed and the state of the thing is what makes it impossible, and the caller's next
    move is a different route rather than a corrected body."""
    rule = await s.get(coa_alloc.AllocationRule, rule_id)
    if rule is not None and rule.tenant_id == tenant_id and rule.mode == "applied":
        raise HTTPException(409, "An applied rule is versioned, so it is never edited or "
                                 "deleted in place. Add a version instead, or end it from a "
                                 "month, and what it used to say stays readable.")


@router.patch("/allocations/rules/{rule_id}")         # CFO only
async def alloc_patch_rule(rule_id: uuid.UUID, body: AllocRulePatch,
                           user: User = Depends(require_role("owner", "admin")),
                           s: AsyncSession = Depends(get_session)):
    await _refuse_if_applied(s, user.tenant_id, rule_id)
    try:
        rule = await coa_alloc.update_rule(s, user.tenant_id, user, rule_id,
                                           **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": str(rule.id)}


@router.delete("/allocations/rules/{rule_id}")        # CFO only
async def alloc_delete_rule(rule_id: uuid.UUID,
                            user: User = Depends(require_role("owner", "admin")),
                            s: AsyncSession = Depends(get_session)):
    await _refuse_if_applied(s, user.tenant_id, rule_id)
    try:
        await coa_alloc.delete_rule(s, user.tenant_id, user, rule_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/allocations/applied")                  # CFO only
async def alloc_create_applied(body: AppliedRuleIn,
                               user: User = Depends(require_role("owner", "admin")),
                               s: AsyncSession = Depends(get_session)):
    try:
        rule, v = await alloc_rules.create_applied_rule(s, user.tenant_id, user,
                                                        **body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": str(rule.id), "version": alloc_rules.version_out(v)}


@router.get("/allocations/applied/{rule_id}/versions")
async def alloc_versions(rule_id: uuid.UUID, user: User = Depends(books_user),
                         s: AsyncSession = Depends(get_session)):
    rows = await alloc_rules.versions_for(s, user.tenant_id, rule_id)
    return {"versions": [alloc_rules.version_out(v) for v in rows]}


@router.post("/allocations/applied/{rule_id}/versions")   # CFO only
async def alloc_add_version(rule_id: uuid.UUID, body: AllocVersionIn,
                            user: User = Depends(require_role("owner", "admin")),
                            s: AsyncSession = Depends(get_session)):
    try:
        v = await alloc_rules.supersede(s, user.tenant_id, user, rule_id, **body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "version": alloc_rules.version_out(v)}


@router.post("/allocations/applied/{rule_id}/end")    # CFO only
async def alloc_end_rule(rule_id: uuid.UUID, body: AllocEndIn,
                         user: User = Depends(require_role("owner", "admin")),
                         s: AsyncSession = Depends(get_session)):
    try:
        v = await alloc_rules.end_rule(s, user.tenant_id, user, rule_id, **body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "version": alloc_rules.version_out(v)}


@router.post("/allocations/rebuild")                  # CFO only
async def alloc_rebuild(period_start: dt.date | None = None, period_end: dt.date | None = None,
                        user: User = Depends(require_role("owner", "admin")),
                        s: AsyncSession = Depends(get_session)):
    """Re-derive a period's contributions from the current rules. The worker does this on every
    pass; this is for when a rule has just been declared and nobody wants to wait."""
    period = _period(period_start, period_end)
    observed = await coa_alloc.sync_allocations(s, user.tenant_id, period)
    # And the applied ones, for every calendar month the requested period CONTAINS. A rebuild
    # of a year-to-date window has to recompute the twelve months inside it, not one row keyed
    # on the window: month rows are what `contributions_for` matches by containment, and
    # deriving the window instead would leave the months stale while the window looked fresh.
    today = dt.date.today()
    months = [m for m in coa_balances.allocation_months(today, 1)
              if m[0] >= period[0] and m[1] <= period[1]]
    applied = await alloc_engine.derive_all(s, user.tenant_id, today=today, months=months)
    await s.commit()
    return {**observed, "applied": {"months": applied["months"],
                                    "contributions": applied["contributions"],
                                    "amount": float(applied["amount"]),
                                    "pending": applied["pending"]}}


@router.get("/allocation-check")
async def allocation_check(period_start: dt.date | None = None,
                           period_end: dt.date | None = None,
                           user: User = Depends(books_user),
                           s: AsyncSession = Depends(get_session)):
    """Portfolio zero-sum, plus the intercompany reconciliation that actually finds things.
    A blocking step in the close checklist (SPEC 6.6)."""
    return await coa_alloc.allocation_check(s, user.tenant_id,
                                            _period(period_start, period_end))


@router.get("/statement/line")
async def get_line_detail(business_id: uuid.UUID, standard_account_id: uuid.UUID,
                          period_start: dt.date | None = None, period_end: dt.date | None = None,
                          period: str | None = None, limit: int = 400,
                          user: User = Depends(books_user),
                          s: AsyncSession = Depends(get_session)):
    """The QBO accounts and the transactions behind one statement line, so a number can be
    audited rather than trusted. Lazily fetched on expand — hundreds of transactions per line
    have no business riding along on every statement request."""
    try:
        return await coa_balances.line_detail(
            s, user.tenant_id, business_id, standard_account_id,
            _period(period_start, period_end, period), limit=min(limit, 1000))
    except ValueError as e:
        raise HTTPException(404, str(e))


# ── drivers (ALLOCATION-RULES-SPEC 4.3, 5.4, Phase 5) ──

@router.get("/allocations/drivers")
async def alloc_list_drivers(user: User = Depends(books_user),
                             s: AsyncSession = Depends(get_session)):
    rows = await alloc_drivers.list_drivers(s, user.tenant_id)
    return {"drivers": [alloc_drivers.driver_out(d) for d in rows]}


@router.post("/allocations/drivers")                  # CFO only
async def alloc_create_driver(body: DriverIn,
                              user: User = Depends(require_role("owner", "admin")),
                              s: AsyncSession = Depends(get_session)):
    try:
        d = await alloc_drivers.create_driver(s, user.tenant_id, user, **body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "driver": alloc_drivers.driver_out(d)}


@router.get("/allocations/drivers/{driver_id}/month")
async def alloc_driver_month(driver_id: uuid.UUID, period: dt.date,
                             user: User = Depends(books_user),
                             s: AsyncSession = Depends(get_session)):
    """What this month holds, and what the form would start from.

    `commit` is null when the month has none, and `prefill` is offered beside it rather than
    merged into it: the engine reads one and not the other, so the payload must not blur them
    (a month with no commit allocates nothing and reports `no_driver_values`).
    """
    c = await alloc_drivers.current_commit(s, user.tenant_id, driver_id, period)
    out = {"period": alloc_drivers.month_start(period).isoformat(), "commit": None,
           "prefill": await alloc_drivers.prefill(s, user.tenant_id, driver_id, period)}
    if c is not None:
        out["commit"] = alloc_drivers.commit_out(
            c, await alloc_drivers.values_for(s, user.tenant_id, c.id))
    return out


@router.post("/allocations/drivers/{driver_id}/commits")   # CFO only
async def alloc_commit_driver(driver_id: uuid.UUID, body: DriverCommitIn,
                              user: User = Depends(require_role("owner", "admin")),
                              s: AsyncSession = Depends(get_session)):
    try:
        c = await alloc_drivers.commit_month(s, user.tenant_id, user, driver_id,
                                             period=body.period, rows=body.rows,
                                             note=body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "commit": alloc_drivers.commit_out(
        c, await alloc_drivers.values_for(s, user.tenant_id, c.id))}


# ── exceptions, preview, reconciliation, pool settings (SPEC 5.6 to 5.8, Phase 7) ──

class AllocPoolsIn(BaseModel):
    business_ids: list[uuid.UUID]


class AllocPreviewIn(BaseModel):
    scope_kind: str
    pattern: str
    source_business_id: uuid.UUID
    method: str
    effective_from: dt.date
    reach: str
    rationale: str
    tier: int | None = None
    targets: list[dict] | None = None
    driver_id: uuid.UUID | None = None
    driver_subject: str | None = None
    source_spec: dict | None = None


@router.get("/allocations/exceptions")
async def alloc_exceptions(period: str | None = None,
                           period_start: dt.date | None = None,
                           period_end: dt.date | None = None,
                           user: User = Depends(books_user),
                           s: AsyncSession = Depends(get_session)):
    """Shared cost on a pool entity that no effective rule claims. Computed live, never stored.

    Answers 200 with `configured: false` when nobody has set the pool entities, rather than an
    empty list: "nothing to do" and "nobody has said where to look" are different answers and
    an empty list reads as the first.
    """
    out = await alloc_engine.exceptions(
        s, user.tenant_id, _period(period_start, period_end, period), today=dt.date.today())
    return {
        "configured": out["configured"],
        "pool_business_ids": out["pool_business_ids"],
        "total": coa_balances._money(out["total"]),
        "rows": [{**r, "amount": coa_balances._money(r["amount"])} for r in out["rows"]],
    }


@router.post("/allocations/preview")                  # CFO only
async def alloc_preview(body: AllocPreviewIn,
                        user: User = Depends(require_role("owner", "admin")),
                        s: AsyncSession = Depends(get_session)):
    """What a draft would do. Writes nothing, and cannot: it never touches the write path."""
    try:
        out = await alloc_engine.preview(s, user.tenant_id, body.model_dump(),
                                         today=dt.date.today())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        **out,
        "pool_amount": coa_balances._money(out["pool_amount"]),
        "retained": coa_balances._money(out["retained"]),
        "targets": [{**t, "amount": coa_balances._money(t["amount"])}
                    for t in out["targets"]],
        "pending": [{**p, **({"amount": coa_balances._money(p["amount"])}
                             if "amount" in p else {}),
                     **({"meta_spend": coa_balances._money(p["meta_spend"])}
                        if "meta_spend" in p else {})}
                    for p in out["pending"]],
    }


@router.get("/allocations/recon")
async def alloc_recon(period: str | None = None,
                      period_start: dt.date | None = None,
                      period_end: dt.date | None = None,
                      user: User = Depends(books_user),
                      s: AsyncSession = Depends(get_session)):
    """Per rule: what the books hold, what resolved, and what did not (5.8). Plus the
    intercompany check, unchanged, because that one reads observed rows only."""
    key = _period(period_start, period_end, period)
    out = await alloc_engine.reconciliation(s, user.tenant_id, key, today=dt.date.today())
    money = coa_balances._money
    return {
        "months": out["months"],
        "rules": [{**r,
                   "pool_amount": money(r["pool_amount"]),
                   "resolved_amount": money(r["resolved_amount"]),
                   "unresolved_amount": money(r["unresolved_amount"]),
                   "variance": money(r["variance"]),
                   "pending": [{**p, "amount": money(p["amount"])} for p in r["pending"]]}
                  for r in out["rules"]],
        "check": await coa_alloc.allocation_check(s, user.tenant_id, key),
    }


@router.put("/coa/settings/alloc-pools")              # CFO only
async def alloc_set_pools(body: AllocPoolsIn,
                          user: User = Depends(require_role("owner", "admin")),
                          s: AsyncSession = Depends(get_session)):
    """Which entities carry shared cost. The one setting this module cannot infer: an entity
    with no rule pointing at it is indistinguishable from one nobody has got to yet."""
    from ..models import Business

    known = {b.id for b in (await s.execute(select(Business).where(
        Business.tenant_id == user.tenant_id))).scalars()}
    unknown = [str(b) for b in body.business_ids if b not in known]
    if unknown:
        raise HTTPException(400, "One of those entities is not in this workspace.")

    cfg = await coa_balances.get_settings(s, user.tenant_id)
    # A JSON column is COPIED before writing, never mutated in place: mutating the loaded list
    # makes SQLAlchemy skip the UPDATE in silence, which cost this repo four syncs that
    # reported success and wrote nothing.
    cfg.alloc_pool_business_ids = [str(b) for b in body.business_ids]
    audit(s, user.tenant_id, user.id, "coa.set_alloc_pools", target_type="coa_settings",
          target_id=cfg.id, category="Books",
          summary=f"Shared-cost entities set to {len(body.business_ids)}",
          detail={"business_ids": cfg.alloc_pool_business_ids})
    await s.commit()
    return {"ok": True, "pool_business_ids": cfg.alloc_pool_business_ids}
