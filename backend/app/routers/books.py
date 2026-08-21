"""Acumyn Books API (SPEC-books-module Part 4). All routes under /api/v1/books, gated by
the `books` tab; CFO-only actions (characterize, rules) additionally require owner/admin.
Payload shapes mirror acumyn-books-v2.jsx."""
import datetime as dt
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import require_tab, require_role
from ..models import User
from ..services import books, coa_balances, coa_map
from ..services.books_scan import create_ic_rule, update_ic_rule

router = APIRouter(prefix="/books", tags=["books"])

books_user = require_tab("books")           # members with the books grant + owners/admins


class RecatIn(BaseModel):
    category: str
    account_qbo_id: str | None = None


class EscalateIn(BaseModel):
    note: str | None = None


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
async def get_queue(user: User = Depends(books_user), s: AsyncSession = Depends(get_session)):
    return await books.build_books_queue(s, user.tenant_id)


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

def _period(period_start: dt.date | None, period_end: dt.date | None) -> tuple[dt.date, dt.date]:
    """Default to the newest period the balance sync pulls, so the common call needs no dates."""
    if period_start and period_end:
        return period_start, period_end
    return coa_balances.balance_periods()[-1]


@router.get("/statement")
async def get_statement(business_id: uuid.UUID, period_start: dt.date | None = None,
                        period_end: dt.date | None = None, statement: str = "pl",
                        user: User = Depends(books_user),
                        s: AsyncSession = Depends(get_session)):
    try:
        return await coa_balances.build_mapped_statement(
            s, user.tenant_id, business_id, _period(period_start, period_end), statement)
    except coa_balances.UnmappedAccountsError as e:
        raise HTTPException(409, detail={
            "error": "unmapped_accounts", "message": str(e),
            "business_id": str(e.business_id),
            "accounts": [{**a, "amount": float(a["amount"])} for a in e.accounts],
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
