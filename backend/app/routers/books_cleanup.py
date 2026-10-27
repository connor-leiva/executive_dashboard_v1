"""Books Cleanup -- API. The tab that replaced the audit workbook (services/books_cleanup.py).

Every route requires the `cleanup` tab, which a workspace has only while it has a loaded file
(services/tabs.py); owners and admins pass the grant implicitly.

READS are the statements: the corrected P&L by company and month with the dollars behind every
line, the correcting entries, the balance sheets, what the companies owe each other, the brands'
year, and the open items.

WRITES are of two kinds, and the split is deliberate:

  * THE DECISIONS -- how a shared cost splits across the brands, the partners' rates and the
    owner's salary, whether each of the owner's charges was hers, investment or loan. Each one
    moves money between companies in the entries the bookkeeper will post, so they are an owner's
    or admin's to make. A member with the tab reads them.
  * THE WORK -- answering an open item, ticking a checklist step, writing the QuickBooks entry
    number against a posted entry. That is the bookkeeper's half, so the tab grant is enough.

Every write is audited with the path it changed and the value before and after: the tab replaces
a workbook whose yellow cells had no history at all, and "who moved this person's split" is a question
somebody will ask.

THE FORUM'S MEMBERS ONE BY ONE -- the list under its projection from the members' records: names, what
each paid and is set to pay, renewal dates, flags, links into the CRM -- are the membership tab's
roster, so they go where the roster goes: owners, admins, and whoever has that tab. Anyone else with
this tab gets the same projection and counts, and the page says where the names are (_sees_roster).

THE FILE ARRIVES BY UPLOAD (owners and admins). It holds the company's real books, so it is never
in the repository; scripts/load_books_cleanup.py loads one from the command line too.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import json
import logging
import math
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import assert_tab, current_user
from ..models import CleanupDataset, CleanupState, User
from ..services import books_cleanup as eng
from ..services import cleanup_ask, cleanup_live
from ..services.audit import audit
from ..services.tabs import tenant_tab_descriptors

TAB = "cleanup"
# The membership tab: the Forum's roster lives there (GET /forum checks the same grant), so its members one by one
# in a projection from their records go only to someone who could read them there.
ROSTER_TAB = "forum"
MAX_BYTES = 25 * 1024 * 1024
# moving, splitting or taking out a single transaction moves money between companies too ("tx")
DECISIONS = {"ads", "rates", "salary", "holdco", "bc_basis", "staff", "items", "charges", "tx",
             # the forward view's assumptions and the budget: the owners' plan, not the bookkeeper's
             "forward", "budget"}

router = APIRouter(prefix="/cleanup", tags=["cleanup"])
log = logging.getLogger(__name__)

# The computed books, per (file, decisions version). Building them is a few tens of milliseconds;
# reading a multi-megabyte file out of the database every request is most of that, so the cache
# also holds the file. Small: one workspace has this tab.
_CACHE: dict[tuple, tuple] = {}


class Change(BaseModel):
    path: list[str | int] = Field(min_length=1, max_length=6)
    value: Any = None


class ChangeSet(BaseModel):
    changes: list[Change] = Field(min_length=1, max_length=250)


class AskBody(BaseModel):
    about: dict                    # {"kind": "tx", "key"} | {"kind": "line", "view", "line", "month"} | {"kind": "pair", "pair"}
    question: str = Field(min_length=1, max_length=1000)
    history: list[dict] = Field(default_factory=list, max_length=12)


async def _current(s: AsyncSession, tenant_id) -> tuple[CleanupDataset | None, CleanupState | None]:
    ds = (await s.execute(select(CleanupDataset.id, CleanupDataset.label, CleanupDataset.uploaded_at,
                                 CleanupDataset.uploaded_by)
                          .where(CleanupDataset.tenant_id == tenant_id)
                          .order_by(CleanupDataset.uploaded_at.desc(), CleanupDataset.id.desc())
                          .limit(1))).first()
    st = await s.get(CleanupState, tenant_id)
    return ds, st


async def _books(s: AsyncSession, user: User) -> tuple[eng.Books, Any, CleanupState | None]:
    await assert_tab(user, s, TAB)
    ds, st = await _current(s, user.tenant_id)
    if ds is None:
        raise HTTPException(404, "No Books Cleanup file has been loaded for this workspace.")
    key = (ds.id, st.version if st else 0)
    hit = _CACHE.get(key)
    if hit is None:
        payload = (await s.execute(select(CleanupDataset.payload).where(CleanupDataset.id == ds.id))).scalar_one()
        bk = eng.Books(payload, eng.merged_inputs(payload, st.inputs if st else None))
        if len(_CACHE) >= 6:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = hit = (bk, ds)
    return hit[0], hit[1], st


async def _live(s: AsyncSession, user: User, bk: eng.Books) -> tuple[list[dict], str | None]:
    """The companies the app projects from its own records rather than the file (the Forum, from its members), as
    they stand today. Not cached with the books: the records move with every sync. A failure here is said on
    Forecast rather than taking the file's own projection down with it."""
    try:
        return await cleanup_live.blocks(s, user.tenant_id, bk.d), None
    except Exception:
        log.exception("cleanup live projection failed")
        return [], "The projection from the members' records didn't build. Try again in a moment."


def _may_decide(user: User) -> bool:
    return user.role in ("owner", "admin")


async def _sees_roster(s: AsyncSession, user: User) -> bool:
    """Whether this person sees the Forum's members one by one in its projection. Owners and admins do; anyone else
    only with the membership tab, the same grant that opens the roster itself."""
    if _may_decide(user):
        return True
    try:
        await assert_tab(user, s, ROSTER_TAB)
    except HTTPException:
        return False
    return True


def _counts_only(f: dict, tab: str | None) -> dict:
    """A projection from the members' records without its members: every figure and count stays, the list (names,
    amounts, renewal dates, flags, CRM links) and who was left out go, and the block says so. The counts are kept
    by name rather than the list taken out by name, so whatever a member's row grows to hold stays behind too. The
    file's own projection has no members' list and is passed as it is."""
    det = f.get("detail")
    if f.get("source") == "file" or not det:
        return f
    return {**f, "detail": {"counts": det.get("counts") or {}, "left_out_count": len(det.get("left_out") or []),
                            "restricted": True, "roster_tab": tab}}


def _bad(e: Exception):
    raise HTTPException(400, str(e))


def _big(request: Request, payload) -> Response:
    """A large read, gzipped when the browser takes it (every browser does). The year's transactions are
    about two megabytes of JSON and a busy line's detail a few hundred kilobytes; compressed, a tenth."""
    body = json.dumps(payload, separators=(",", ":"), default=str).encode()
    if len(body) > 32_000 and "gzip" in (request.headers.get("accept-encoding") or "").lower():
        return Response(gzip.compress(body, 6), media_type="application/json",
                        headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(body, media_type="application/json", headers={"Vary": "Accept-Encoding"})


# ── reads ────────────────────────────────────────────────────────────────────────────────────────

@router.get("")
async def overview(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    bk, ds, st = await _books(s, user)
    d = bk.d
    inside = d.get("inside_brand")
    summary = bk.summary()
    answers = bk.inp.get("answers") or {}
    checklist = bk.inp.get("checklist") or {}
    return {
        "label": ds.label, "uploaded_at": ds.uploaded_at.isoformat() if ds.uploaded_at else None,
        "exported_at": d.get("exported_at"), "period": d["period"], "companies": bk.companies,
        "company_short": d.get("company_short") or {}, "brands": bk.brands, "inside_brand": inside,
        "hub": d["hub"], "brand_company": d["brand_company"],
        "views": bk.companies + ([inside] if inside else []) + ["all"],
        # what a transaction's Move, Personal and Line say in words
        "billback_name": (d.get("billback") or {}).get("name"), "salary_owner": d.get("salary_owner"),
        "lines": {"revenue": [l for l in bk.REVENUE if not l.startswith(("Uncategorized", "Not yet"))],
                  "costs": [l for l in bk.COSTS if not l.startswith(("Uncategorized", "Not yet"))]},
        "can_move_month": all((d["accounts"].get(k) or {}) for k in ("prepaid", "accrued_liab")),
        "forward_company": (d.get("forward") or {}).get("company"),
        # every company with Forecast and Cash flow: the file's own, then any the app projects from its records
        "forward_companies": [f["company"] for f in bk.projected()]
                             + await cleanup_live.companies(s, user.tenant_id, d),
        "headline": {
            "net_income": {c: bk.net_income(c) for c in bk.companies},
            "inside": ({inside: bk.statement(inside)["totals"]["net_income"]["corrected"]} if inside else {}),
            "all": round(sum(bk.net_income(c) for c in bk.companies), 2),
            "qbo_net_income": {c: d["qbo"][c]["net_income"] for c in bk.companies},
            "interco": bk.intercompany()["pairs"],
            "partners": summary["partners"], "partners_through": summary["months_through"],
        },
        "checks": bk.checks(),
        "open_items": sum(1 for x in d.get("open_items", []) if not (answers.get(x["ref"]) or "").strip()),
        "checklist": {"done": sum(1 for v in checklist.values() if v.get("done")), "total": len(d.get("checklist", []))},
        "version": st.version if st else 0,
        "can_decide": _may_decide(user),
        "can_ask": cleanup_ask.enabled(),
    }


@router.get("/statement")
async def statement(view: str, month: int | None = Query(default=None, ge=1, le=12),
                    user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    bk, _, _ = await _books(s, user)
    _known_month(bk, month)
    try:
        return bk.statement(view, month)
    except eng.InputError as e:
        _bad(e)


def _known_month(bk: eng.Books, month: int | None) -> None:
    # The file's period, not the calendar's: a month it doesn't cover has no salary fraction to read.
    if month is not None and month not in bk.MONTHS:
        raise HTTPException(400, f"This file has no month {month}.")


@router.get("/statement/line")
async def statement_line(request: Request, view: str, line: str,
                         month: int | None = Query(default=None, ge=1, le=12),
                         user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    bk, _, _ = await _books(s, user)
    _known_month(bk, month)
    if line not in bk.REVENUE + bk.COSTS + bk.BELOW:
        raise HTTPException(400, f"Unknown line {line!r}.")
    try:
        return _big(request, bk.line_detail(view, line, month))
    except eng.InputError as e:
        _bad(e)


@router.get("/forward")
async def forward(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """The months after the books end (Forecast, Cash flow, the Statement's future months) for every projected
    company -- the file's own first, then any the app projects from its own records -- and every company's budget
    lines. `forward` is the file's own (null for a file that projects nothing). The Forum's members one by one are
    left out for someone without the membership tab (_sees_roster); the figures are the same."""
    bk, _, _ = await _books(s, user)
    live, err = await _live(s, user, bk)
    forwards = bk.forwards(live)
    if not await _sees_roster(s, user):
        tab = next((t["label"] for t in await tenant_tab_descriptors(s, user.tenant_id) if t["key"] == ROSTER_TAB), None)
        forwards = [_counts_only(f, tab) for f in forwards]
    return {"forwards": forwards, "forward": bk.forward(),
            "budgets": {c: bk.budget(c) for c in bk.companies}, "can_decide": _may_decide(user), "live_error": err}


@router.get("/transactions")
async def transactions(request: Request, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Every transaction in the file, with its words and where the corrected books put it. One read for the
    whole year: the page searches and filters it in place."""
    bk, _, _ = await _books(s, user)
    return _big(request, {"rows": bk.transactions(), "brands": bk.brands, "brand_company": bk.bc,
                          "hub": bk.hub, "vendor_names": bk.d.get("vendor_names") or []})


@router.get("/corrections")
async def corrections(company: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    bk, _, _ = await _books(s, user)
    try:
        c = bk.corrections(company)
    except eng.InputError as e:
        _bad(e)
    # each month's entry as the import file names and dates it, so the page and the file agree
    for mon in c["months"]:
        jno, date = _journal(bk, company, mon["k"])
        mon["journal_no"], mon["date"] = jno, date.isoformat()
    return c


def _journal(bk: eng.Books, company: str, k: int) -> tuple[str, dt.date]:
    """One month's entry number and date: January 1 for the opening entry, else the month's last day
    (the period's own last day for a month it ends part-way through)."""
    year = int(bk.d["period"]["start"][:4])
    end = dt.date.fromisoformat(bk.d["period"]["end"])
    if k == 0:
        date = dt.date(year, 1, 1)
    elif k == bk.MONTHS[-1]:
        date = end
    else:
        date = dt.date(year + (k // 12), k % 12 + 1, 1) - dt.timedelta(days=1)
    short = (bk.d.get("company_short") or {}).get(company, company)
    code = "".join(w[0] for w in short.split() if w[:1].isalnum()).upper() or "X"
    return f"AX-{code}-{date:%Y%m}" + ("-01" if k == 0 else ""), date


@router.get("/corrections.csv")
async def corrections_csv(company: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """The entries as a journal-entry import file: one entry per month, each line a debit or a credit.

    Column names follow QuickBooks Online's journal-entry import (Journal No., Journal Date, Account
    Name, Debits, Credits, Description). Check the mapping screen on import; accounts marked New on
    the tab have to exist in the company first.
    """
    bk, _, _ = await _books(s, user)
    try:
        c = bk.corrections(company)
    except eng.InputError as e:
        _bad(e)
    short = (bk.d.get("company_short") or {}).get(company, company)
    code = "".join(w[0] for w in short.split() if w[:1].isalnum()).upper() or "X"
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Journal No.", "Journal Date", "Account Name", "Debits", "Credits", "Description"])
    for mon in c["months"]:
        k = mon["k"]
        lines = [(r["account"], r["amounts"][k]) for r in c["accounts"] if abs(r["amounts"][k]) >= 0.005]
        if not lines:
            continue
        jno, date = _journal(bk, company, k)
        said = {r["account"]: (r.get("said") or [""] * len(c["months"]))[k] for r in c["accounts"]}
        for acct, v in lines:
            # each line says what it's for (the parts, whose pay, which transactions changed on the tab)
            words = f"Books cleanup, {mon['label']}: {said[acct]}" if said.get(acct) else f"Books cleanup correction, {mon['label']}"
            w.writerow([jno, date.strftime("%m/%d/%Y"), acct, f"{v:.2f}" if v > 0 else "",
                        f"{-v:.2f}" if v < 0 else "", words])
    name = f"cleanup-entries-{code.lower()}.csv"
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/balance")
async def balance(company: str, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    bk, _, _ = await _books(s, user)
    try:
        return bk.balance_sheet(company)
    except eng.InputError as e:
        _bad(e)


@router.get("/intercompany")
async def intercompany(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    bk, _, _ = await _books(s, user)
    return {**bk.intercompany(), "summary": bk.summary(), "billback_name": bk.d["billback"]["name"],
            "recover_label": bk.d.get("recover_label") or "Recovered"}


@router.get("/intercompany/pair")
async def intercompany_pair(request: Request, pair: str, user: User = Depends(current_user),
                            s: AsyncSession = Depends(get_session)):
    """What two companies owe each other, item by item: every transaction, share and change behind the cleanup's
    change to it (a few hundred rows for the busiest pair, so gzipped like the other large reads)."""
    bk, _, _ = await _books(s, user)
    try:
        return _big(request, bk.interco_detail(pair))
    except eng.InputError as e:
        _bad(e)


@router.post("/ask")
async def ask(body: AskBody, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Ask about this: a question about one transaction, line or pair, answered from the tab's own figures for it.
    Reading the tab is enough (it changes nothing); without the server's key the tab doesn't offer it. Its context is
    the file and the saved changes only, never the members' records, so the Forum's members one by one can't reach
    it (_sees_roster has nothing to hold back here)."""
    bk, _, _ = await _books(s, user)
    if not cleanup_ask.enabled():
        raise HTTPException(503, "Asking isn't set up on this server.")
    try:
        return await cleanup_ask.ask(bk, body.about, body.question, body.history)
    except eng.InputError as e:
        _bad(e)
    except Exception:
        log.exception("cleanup ask failed")
        raise HTTPException(502, "The answer didn't come back. Try again in a moment.")


SAVED_SCHEMA = "axcion.books_cleanup_saved/1"


@router.get("/saved.json")
async def saved_changes(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Every change saved on the tab, as one file: the decisions, the changes to single transactions, the forecast
    and plan, the answers, ticks and posted entry numbers -- exactly what cleanup_state holds, with the file they
    apply to. That file plus these changes rebuild every figure the tab shows
    (`Books(file, merged_inputs(file, inputs))`) -- all but a projection the app builds from its own records (the
    Forum's), which also needs those records as they stand -- so an analysis outside the app can start from the
    owners' own decisions. The decisions move money between companies, so the download is an owner's or admin's,
    and audited. It holds no member of the Forum's: only what was saved, and the Forum's assumptions are numbers."""
    await assert_tab(user, s, TAB)
    if not _may_decide(user):
        raise HTTPException(403, "Only an owner or admin downloads the saved changes.")
    ds, st = await _current(s, user.tenant_id)
    if ds is None:
        raise HTTPException(404, "No Books Cleanup file has been loaded for this workspace.")
    now = dt.datetime.now(dt.timezone.utc)
    version = st.version if st else 0
    payload = {"schema": SAVED_SCHEMA, "exported_at": now.isoformat(timespec="seconds"),
               "file": {"id": str(ds.id), "label": ds.label,
                        "uploaded_at": ds.uploaded_at.isoformat() if ds.uploaded_at else None},
               "version": version,
               "updated_at": st.updated_at.isoformat() if st and st.updated_at else None,
               "inputs": (st.inputs if st else None) or {}}
    audit(s, user.tenant_id, user.id, "cleanup.export", "cleanup", "saved.json",
          {"version": version, "file": str(ds.id)}, category="Books",
          summary=f"Books Cleanup: downloaded the saved changes (version {version})", actor_label=user.name or user.email)
    await s.commit()
    name = f"books-cleanup-saved-changes-v{version}-{now:%Y-%m-%d}.json"
    return Response(json.dumps(payload, indent=1, default=str), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/decisions")
async def decisions(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Every decision with the money it moves, so a split is never set without seeing what it splits."""
    bk, _, _ = await _books(s, user)
    d, inp = bk.d, bk.inp
    months = [x["m"] for x in d["period"]["months"]]
    pool = lambda name, m: round(bk.pool_m[(name, m)], 2)
    staff = []
    for p in d["people"]:
        cost = [round(bk.staff_cost.get((p["name"], m), 0.0), 2) for m in months]
        split = {str(m): dict(zip(bk.brands, bk.split(p["name"], m))) for m in months}
        staff.append({**p, "cost": cost, "total": round(sum(cost), 2), "split": split,
                      "brand_totals": {b: round(sum(cost[i] * split[str(m)][b] for i, m in enumerate(months)), 2)
                                       for b in bk.brands}})
    items = {}
    for kind, spec in d["items"].items():
        rows = []
        for r in spec["rows"]:
            cost = [round(bk.item_m.get((spec["pool"], r["name"], m), 0.0), 2) for m in months]
            sp = bk.item_split(spec["pool"], r["name"])
            rows.append({**r, "cost": cost, "total": round(sum(cost), 2), "split": sp,
                         "ok": abs(sum(sp.values()) - 1) < 1e-4})
        items[kind] = rows
    charges = []
    for c in d["charges"]:
        treat, line = bk.charge(c)
        charges.append({**c, "treat": treat, "line": line})
    return {
        "brands": bk.brands, "months": d["period"]["months"], "can_decide": _may_decide(user),
        "ads": {"split": {b: inp["ads"].get(b, 0.0) for b in bk.brands + ["billback"]},
                "billback_name": d["billback"]["name"], "pool": [pool("Advertising", m) for m in months]},
        "fees": {"split": d["fees"], "pool": [pool("Payment processing", m) for m in months]},
        "rates": inp["rates"], "salary": inp.get("salary"), "salary_owner": d.get("salary_owner"),
        "partners": d["partners"], "holdco": inp.get("holdco"), "holdco_choices": list(eng.HOLDCO_CHOICES),
        "bc_basis": inp.get("bc_basis"), "bc_bases": list(eng.BC_BASES),
        # the file names the company and words the choices; a file without them gets the keys
        "basis": d.get("basis") or {"company": None, "title": "Revenue timing",
                                    "choices": [{"key": k, "label": k, "note": ""} for k in eng.BC_BASES]},
        "holdco_rows": _holdco_rows(d), "holdco_accounts": d["accounts"]["holdco"],
        "words": d.get("decision_words") or {},
        "staff": staff, "items": items, "charges": charges, "charge_lines": eng.charge_lines(d),
        "notes": inp.get("notes") or {},
        # each transaction changed on the tab, oldest change first, with what it was
        "tx_changes": [{**{k: w[k] for k in ("key", "payee", "what", "paid_from", "tx_date", "tx_amount", "money_in")},
                        "change": ch, "said": eng.describe_change(d, key, ch)}
                       for key, ch in sorted((inp.get("tx") or {}).items(), key=lambda kv: kv[1].get("at") or "")
                       for w in [bk.tx_words(key)]],
    }


def _holdco_rows(d: dict) -> list[dict]:
    """The holding company's money, by company and the account it was booked to (credits are money in)."""
    out: dict[tuple, dict] = {}
    for x in d["holdco"]:
        r = out.setdefault((x["company"], x["account"]), {"company": x["company"], "account": x["account"],
                                                         "amount": 0.0, "count": 0})
        r["amount"] += x["amount"]
        r["count"] += 1
    return [{**r, "amount": round(r["amount"], 2)} for r in out.values()]


@router.get("/items")
async def items(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    bk, _, _ = await _books(s, user)
    d, inp = bk.d, bk.inp
    answers = inp.get("answers") or {}
    state = inp.get("checklist") or {}
    return {
        "open": [{**x, "answer": answers.get(x["ref"], "")} for x in d.get("open_items", [])],
        "answered": [{**x, "answer": answers.get(x["ref"], "")} for x in d.get("answered", [])],
        "checklist": [{**c, **(state.get(c["id"]) or {"done": False, "note": ""})} for c in d.get("checklist", [])],
        "checklist_groups": d.get("checklist_groups") or [],
        "findings": d.get("findings", []), "questions": d.get("questions", []),
        "questions_title": d.get("questions_title") or "Questions, answered",
        "can_decide": _may_decide(user),
    }


# ── writes ───────────────────────────────────────────────────────────────────────────────────────

@router.put("/inputs")
async def put_inputs(body: ChangeSet, user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Apply a set of changes at once (a person's whole month, say, so it never sits at 150%).

    All or nothing: one change the file cannot hold refuses the set, so a half-applied month is
    never what the next reader sees.
    """
    bk, ds, _ = await _books(s, user)
    heads = {str(c.path[0]) for c in body.changes}
    if heads & DECISIONS and not _may_decide(user):
        raise HTTPException(403, "Only an owner or admin changes the decisions.")
    data = bk.d
    if any(str(c.path[0]) in ("forward", "budget") and len(c.path) == 3 for c in body.changes):
        # a projected company the app builds from its own records: its block says what it can hold. Without the
        # block there is nothing to check the change against, which is the build's fault, not the change's: said
        # as that, not as "Nothing to change"
        live, err = await _live(s, user, bk)
        if err:
            raise HTTPException(503, "The projection from the members' records didn't build, so this can't be "
                                     "saved right now. Try again in a moment.")
        data = {**bk.d, "forward_live": live}
    try:
        clean = [([str(p) for p in c.path], eng.validate_change(data, list(c.path), c.value)) for c in body.changes]
    except eng.InputError as e:
        raise HTTPException(400, str(e))
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    clean = [(path, {**value, "by": user.name or user.email, "at": stamp} if path[0] == "tx" and value else value)
             for path, value in clean]
    # Locked and re-read, not the row _books() saw: two people saving at once would otherwise both
    # start from the same version and the second save would silently drop the first one's changes.
    st = (await s.execute(select(CleanupState).where(CleanupState.tenant_id == user.tenant_id)
                          .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if st is None:
        st = CleanupState(tenant_id=user.tenant_id, inputs={}, version=0)
        s.add(st)
    saved = json.loads(json.dumps(st.inputs or {}))       # a copy: mutating the loaded dict skips the UPDATE
    now = eng.merged_inputs(bk.d, saved)
    before = []
    for path, value in clean:
        node = now
        for p in path[:-1]:
            node = node.get(p) if isinstance(node, dict) else None
        before.append(node.get(path[-1]) if isinstance(node, dict) else None)
        eng.set_path(saved, path, value)
    st.inputs = saved
    st.version = (st.version or 0) + 1
    st.updated_by = user.id
    for (path, value), was in zip(clean, before):
        where = "/".join(path)
        # target_id is String(64) and summary String(500), and real paths run past 64 (a subscription's
        # full name) -- Postgres refuses an over-long value where SQLite stores it. The whole path and
        # value are in `detail`.
        said = (eng.describe_change(bk.d, path[1], value) if path[0] == "tx" and len(path) == 2
                else f"{where} set to {value!r}")
        audit(s, user.tenant_id, user.id, "cleanup.change", "cleanup", where[:64],
              {"path": path, "from": was, "to": value}, category="Books",
              summary=f"Books Cleanup: {said}"[:500], actor_label=user.name or user.email)
    await s.commit()
    bk2, _, _ = await _books(s, user)
    return {"version": st.version, "checks": bk2.checks(),
            "net_income": {c: bk2.net_income(c) for c in bk2.companies}}


@router.post("/dataset")
async def upload_dataset(file: UploadFile = File(...), user: User = Depends(current_user),
                         s: AsyncSession = Depends(get_session)):
    """Load a new audit file. The decisions already made stay; anything new arrives at the file's value."""
    if not _may_decide(user):
        raise HTTPException(403, "Only an owner or admin loads the cleanup file.")
    raw = await file.read(MAX_BYTES + 1)                  # never more than one byte past the limit
    if not raw:
        raise HTTPException(400, "Empty file")
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, f"The file is over {MAX_BYTES // (1024 * 1024)} MB.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(400, "That isn't a JSON file.")
    ds = await load_dataset(s, user.tenant_id, payload, user_id=user.id)
    audit(s, user.tenant_id, user.id, "cleanup.dataset", "cleanup_dataset", ds.id,
          {"label": ds.label, "bytes": len(raw)}, category="Books",
          summary=f"Books Cleanup file loaded: {ds.label}"[:500], actor_label=user.name or user.email)
    await s.commit()
    return {"id": str(ds.id), "label": ds.label}


def _unstorable(x, path="the file") -> str | None:
    """Where the file holds something JSONB can't: a number that isn't finite, or a NUL character."""
    stack = [(x, path)]
    while stack:
        v, p = stack.pop()
        if isinstance(v, float) and not math.isfinite(v):
            return f"{p} is {v}"
        if isinstance(v, str) and "\x00" in v:
            return f"{p} holds a NUL character"
        if isinstance(v, dict):
            for k, w in v.items():
                if "\x00" in str(k):
                    return f"{p} has a key with a NUL character"
                stack.append((w, f"{p}.{k}"))
        elif isinstance(v, list):
            stack.extend((w, f"{p}[{i}]") for i, w in enumerate(v))
    return None


async def load_dataset(s: AsyncSession, tenant_id, payload: dict, user_id=None) -> CleanupDataset:
    """Check a file builds, then store it as the workspace's current one. Shared with the CLI loader."""
    if not isinstance(payload, dict) or payload.get("schema") != eng.SCHEMA:
        raise HTTPException(400, "That isn't a Books Cleanup file.")
    if (where := _unstorable(payload)) is not None:
        # Postgres keeps the file as JSONB, which refuses a NaN, an infinity or a NUL character that
        # SQLite (and so every test) would store without a word: refuse it here, as a 400 that says
        # where, rather than as a 500 from the insert.
        raise HTTPException(400, f"The file can't be stored: {where}.")
    try:
        bk = eng.Books(payload, eng.merged_inputs(payload, None))
        bk.checks()
    except (KeyError, TypeError, ValueError, StopIteration) as e:
        raise HTTPException(400, f"The file doesn't build: {type(e).__name__}: {e}")
    ds = CleanupDataset(tenant_id=tenant_id, label=str(payload.get("label") or "Books Cleanup")[:200],
                        schema_id=eng.SCHEMA, payload=payload, uploaded_by=user_id)
    s.add(ds)
    # The decisions row exists from the first file on, so a save always has a row to lock: two first
    # saves racing to INSERT it would otherwise end with one of them refused on the primary key.
    if await s.get(CleanupState, tenant_id) is None:
        s.add(CleanupState(tenant_id=tenant_id, inputs={}, version=0))
    await s.flush()
    return ds
