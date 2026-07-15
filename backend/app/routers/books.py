"""Acumyn Books API (SPEC-books-module Part 4). All routes under /api/v1/books, gated by
the `books` tab; CFO-only actions (characterize, rules) additionally require owner/admin.
Payload shapes mirror acumyn-books-v2.jsx."""
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import require_tab, require_role
from ..models import User
from ..services import books
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
