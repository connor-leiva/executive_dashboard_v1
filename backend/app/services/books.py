"""Acumyn Books — API service layer (SPEC-books-module Part 4).

Read builders for the four pages (Home, P&L, Queue, Intercompany) and the human-decision
mutations (approve / recategorize / escalate / characterize / tie). Payload keys mirror
acumyn-books-v2.jsx so the frontend wiring is mechanical. Every mutation is audited;
the scan pipeline proposes, people approve here.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (BookTxn, PLLine, PLSnapshot, ICLink, ICRule, Integration,
                      Business, Tenant, User)
from ..integrations import qbo
from .audit import audit
from . import roles
from .books_scan import IC_CHARACTERIZATIONS
from .metrics import _pl_period


def _fmt_date(d) -> str | None:
    return f"{d.strftime('%b')} {d.day}" if d else None      # cross-platform (no %-d)

HOLDINGS_COUNT = 12                                     # ULRG holding entities (SPEC 2.6)
_OPEN_IC = ("unmatched", "matched", "escalated", "characterized")   # not yet tied
_REVIEW_STATES = ("needs_approval", "escalated")


def _month_bounds(today: dt.date) -> tuple[dt.date, dt.date]:
    start = today.replace(day=1)
    nxt = (start + dt.timedelta(days=32)).replace(day=1)
    return start, nxt - dt.timedelta(days=1)


def _prior_month(ps: dt.date) -> tuple[dt.date, dt.date]:
    prev_end = ps - dt.timedelta(days=1)
    return prev_end.replace(day=1), prev_end


async def _biz_map(s, tenant_id) -> dict[str, Business]:
    return {b.key: b for b in (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id))).scalars().all()}


async def _books_entities(s, tenant_id) -> list[dict]:
    """The entities that participate in Books — businesses with a QuickBooks integration
    and Books enabled — so the P&L entity selector reflects what Integrations connected /
    routed (not a hardcoded list). Ordered by the business sort order."""
    rows = (await s.execute(
        select(Business).join(Integration, Integration.business_id == Business.id)
        .where(Business.tenant_id == tenant_id, Integration.provider == "qbo")
        .order_by(Business.sort_order))).scalars().all()
    out, seen = [], set()
    for b in rows:
        if b.id in seen or not (b.config or {}).get("books_enabled", True):
            continue
        seen.add(b.id)
        out.append({"key": b.key, "name": b.name})
    return out


# ── Rail + invariant (SPEC 4.1 / Part 7 #1) ──────────────────────────────────
async def _rail(s, tenant_id, mstart, mend) -> dict:
    """Scan-pipeline counts for the calendar month. `captured` partitions exactly into
    the four scan states — the rail-conservation invariant."""
    def _c(*conds):
        return select(func.count(BookTxn.id)).where(
            BookTxn.tenant_id == tenant_id, BookTxn.txn_date >= mstart,
            BookTxn.txn_date <= mend, *conds)
    captured = (await s.execute(_c())).scalar_one()
    by_state = {st: (await s.execute(_c(BookTxn.scan_state == st))).scalar_one()
                for st in ("cleared", "needs_approval", "escalated", "pending")}
    auto = (await s.execute(_c(BookTxn.came_categorized.is_(True)))).scalar_one()
    return {"captured": captured, "auto_categorized": auto, "cleared": by_state["cleared"],
            "needs_approval": by_state["needs_approval"], "escalated": by_state["escalated"],
            "pending": by_state["pending"]}


async def books_invariants(s, tenant_id, today=None) -> dict:
    """Compute + log the Part 7 acceptance checks the frontend/tests assert on."""
    today = today or dt.date.today()
    mstart, mend = _month_bounds(today)
    rail = await _rail(s, tenant_id, mstart, mend)
    rail_ok = rail["captured"] == (rail["cleared"] + rail["needs_approval"]
                                   + rail["escalated"] + rail["pending"])
    # No unreviewed approvals: every approved/posted txn has reviewer + decision (#5)
    bad = (await s.execute(select(func.count(BookTxn.id)).where(
        BookTxn.tenant_id == tenant_id, BookTxn.scan_state.in_(("approved", "posted")),
        (BookTxn.reviewed_by.is_(None)) | (BookTxn.decision.is_(None))))).scalar_one()
    inv = {"rail_conservation": rail_ok, "no_unreviewed_approvals": bad == 0}
    print(f"books_invariants ok={all(inv.values())} rail_conservation={rail_ok} "
          f"no_unreviewed_approvals={bad == 0} captured={rail['captured']} "
          f"pending={rail['pending']}", flush=True)
    return {**inv, "rail": rail}


# ── Home (SPEC 4.1) ──────────────────────────────────────────────────────────
def _snap_note(cur_lines, prev_lines) -> str | None:
    """The single opex parent group with the largest |MoM delta|, phrased for the tile."""
    def by_group(lines):
        g: dict[str, float] = {}
        for ln in lines:
            if ln.section != "expense":
                continue
            top = (ln.parent.split(":")[0].strip() if ln.parent else ln.label)
            g[top] = g.get(top, 0.0) + float(ln.amount)
        return g
    cur, prev = by_group(cur_lines), by_group(prev_lines)
    best, best_delta, best_pct = None, 0.0, 0.0
    for group, cv in cur.items():
        pv = prev.get(group, 0.0)
        if pv <= 0:
            continue
        delta = cv - pv
        if abs(delta) > abs(best_delta):
            best, best_delta, best_pct = group, delta, (delta / pv) * 100
    if not best or abs(best_pct) < 1:
        return None
    return f"{best} {'up' if best_delta > 0 else 'down'} {abs(round(best_pct))}% MoM"


async def build_books_home(s, tenant_id, period="mtd", today=None) -> dict:
    today = today or dt.date.today()
    mstart, mend = _month_bounds(today)
    rail = await _rail(s, tenant_id, mstart, mend)

    synced = (await s.execute(select(func.max(Integration.last_synced_at)).where(
        Integration.tenant_id == tenant_id, Integration.provider == "qbo"))).scalar_one()
    active = (await s.execute(select(func.count(Integration.id)).where(
        Integration.tenant_id == tenant_id, Integration.provider == "qbo",
        Integration.status == "connected"))).scalar_one()

    ps, pe = _pl_period(period)
    pv_s, pv_e = _prior_month(ps)
    tot = (await s.execute(select(
        func.coalesce(func.sum(PLSnapshot.revenue), 0), func.coalesce(func.sum(PLSnapshot.noi), 0))
        .where(PLSnapshot.tenant_id == tenant_id, PLSnapshot.period_start == ps,
               PLSnapshot.period_end == pe))).one()
    revenue, noi = float(tot[0]), float(tot[1])
    cur_lines = (await s.execute(select(PLLine).where(
        PLLine.tenant_id == tenant_id, PLLine.period_start == ps, PLLine.period_end == pe))).scalars().all()
    prev_lines = (await s.execute(select(PLLine).where(
        PLLine.tenant_id == tenant_id, PLLine.period_start == pv_s, PLLine.period_end == pv_e))).scalars().all()

    # Queue tile — oldest item awaiting review
    q = (await s.execute(select(BookTxn).where(
        BookTxn.tenant_id == tenant_id, BookTxn.scan_state.in_(_REVIEW_STATES))
        .order_by(BookTxn.txn_date.asc()).limit(1))).scalars().first()
    q_count = (await s.execute(select(func.count(BookTxn.id)).where(
        BookTxn.tenant_id == tenant_id, BookTxn.scan_state == "needs_approval"))).scalar_one()

    # IC tile — open (untied) links. A newly-connected entity (books_onboarding) still
    # shows its escalations, but they don't count as blocking the close until the CFO has
    # activated the real intercompany rules for it (avoids a fresh realm freezing close).
    open_links = (await s.execute(select(ICLink).where(
        ICLink.tenant_id == tenant_id, ICLink.status.in_(_OPEN_IC))
        .order_by(ICLink.amount.desc()))).scalars().all()
    onboarding_ids = {b.id for b in (await _biz_map(s, tenant_id)).values()
                      if (b.config or {}).get("books_onboarding")}
    blocking = any(l.status in ("unmatched", "escalated")
                   and l.from_business_id not in onboarding_ids
                   and l.to_business_id not in onboarding_ids
                   for l in open_links)

    rv = await _latest_review(s, tenant_id, ps)
    close = await _close_tiles(s, tenant_id, ps)

    return {
        "period": period,
        "synced_at": synced.isoformat() if synced else None,
        "entities": {"active": active or len(await _biz_map(s, tenant_id)), "holdings": HOLDINGS_COUNT},
        "rail": {k: rail[k] for k in ("captured", "auto_categorized", "cleared",
                                      "needs_approval", "escalated")},
        "tiles": {
            "pl": {"noi": noi, "revenue": revenue,
                   "margin": round((noi / revenue) * 100) if revenue else 0,
                   "note": _snap_note(cur_lines, prev_lines)},
            "queue": {"count": q_count, "oldest_days": ((today - q.txn_date).days if q and q.txn_date else 0),
                      "oldest_label": (f"{q.payee or 'Unknown'}, ${abs(float(q.amount)):,.0f}" if q else None)},
            "ic": {"open": len(open_links), "blocking": blocking,
                   "note": (open_links[0].note or _ic_label(open_links[0])) if open_links else None},
            "close": close,
        },
        "review": rv,
    }


async def _latest_review(s, tenant_id, period_start):
    from ..models import BooksReview
    r = (await s.execute(select(BooksReview).where(
        BooksReview.tenant_id == tenant_id, BooksReview.period == period_start))).scalar_one_or_none()
    return {"status": r.status, "body": r.body, "period": r.period.isoformat()} if r else None


async def _close_tiles(s, tenant_id, period_start):
    from ..models import ClosePeriod
    rows = (await s.execute(select(ClosePeriod, Business.key).join(
        Business, ClosePeriod.business_id == Business.id).where(
        ClosePeriod.tenant_id == tenant_id, ClosePeriod.period == period_start))).all()
    return [{"business": key, "steps": cp.steps or {}, "status": cp.status,
             "note": (f"Closed {cp.closed_at.date().isoformat()}" if cp.closed_at else None)}
            for cp, key in rows]


def _ic_label(link: ICLink) -> str:
    return f"${abs(float(link.amount)):,.0f} intercompany ({link.status})"


# ── P&L (SPEC 4.2) ───────────────────────────────────────────────────────────
def _top_group(ln: PLLine) -> str:
    return ln.parent.split(":")[0].strip() if ln.parent else ln.label


async def build_books_pl(s, tenant_id, business="all", period="mtd") -> dict:
    ps, pe = _pl_period(period)
    pv_s, pv_e = _prior_month(ps)
    bmap = await _biz_map(s, tenant_id)
    if business == "all":
        bids = [b.id for b in bmap.values()]
    else:
        b = bmap.get(business)
        bids = [b.id] if b else []

    async def _lines(start, end):
        if not bids:
            return []
        return (await s.execute(select(PLLine).where(
            PLLine.tenant_id == tenant_id, PLLine.business_id.in_(bids),
            PLLine.period_start == start, PLLine.period_end == end)
            .order_by(PLLine.position))).scalars().all()

    cur, prev = await _lines(ps, pe), await _lines(pv_s, pv_e)
    prev_by_key = {}
    for ln in prev:
        prev_by_key[(ln.section, _top_group(ln), ln.label)] = \
            prev_by_key.get((ln.section, _top_group(ln), ln.label), 0.0) + float(ln.amount)

    def _flat(section):
        merged: dict = {}
        for ln in cur:
            if ln.section != section:
                continue
            k = ln.label
            merged.setdefault(k, {"label": ln.label, "v": 0.0,
                                  "pv": prev_by_key.get((section, _top_group(ln), ln.label), 0.0)})
            merged[k]["v"] += float(ln.amount)
        return [{"label": r["label"], "v": round(r["v"], 2), "pv": round(r["pv"], 2)}
                for r in merged.values()]

    def _opex():
        groups: dict = {}
        for ln in cur:
            if ln.section != "expense":
                continue
            cat = _top_group(ln)
            g = groups.setdefault(cat, {"cat": cat, "v": 0.0, "pv": 0.0, "lines": []})
            g["v"] += float(ln.amount)
            g["pv"] += prev_by_key.get(("expense", cat, ln.label), 0.0)
            g["lines"].append({"label": ln.label, "v": round(float(ln.amount), 2),
                               "pv": round(prev_by_key.get(("expense", cat, ln.label), 0.0), 2)})
        return [{**g, "v": round(g["v"], 2), "pv": round(g["pv"], 2)} for g in groups.values()]

    async def _totals(start, end):
        r = (await s.execute(select(
            func.coalesce(func.sum(PLSnapshot.revenue), 0), func.coalesce(func.sum(PLSnapshot.gross_profit), 0),
            func.coalesce(func.sum(PLSnapshot.opex), 0), func.coalesce(func.sum(PLSnapshot.noi), 0))
            .where(PLSnapshot.tenant_id == tenant_id,
                   PLSnapshot.business_id.in_(bids) if bids else False,
                   PLSnapshot.period_start == start, PLSnapshot.period_end == end))).one()
        return {"revenue": float(r[0]), "gross_profit": float(r[1]), "opex": float(r[2]), "noi": float(r[3])}

    totals = await _totals(ps, pe)
    totals["prior"] = await _totals(pv_s, pv_e)
    out = {"business": business, "period_label": _period_label(ps, pe),
           "totals": totals, "revenue": _flat("income"), "cos": _flat("cogs"), "opex": _opex(),
           "entities": await _books_entities(s, tenant_id)}

    # Surface the JV share for whichever business is being viewed, if it has one. Keyed on the
    # property rather than on one customer's business name: a JV is a JV whatever it is called,
    # and a tenant with two of them was previously shown neither.
    _b = bmap.get(business)
    if _b is not None and _b.jv_share is not None and _b.kind == roles.COMMISSION_JV:
        out["jv_share"] = float(_b.jv_share)
    if business == "all":
        tenant = (await s.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
        elim = (tenant.config or {}).get("books_elim_accounts") or []
        amt = sum(float(ln.amount) for ln in cur
                  if any(e.lower() in (ln.label or "").lower() for e in elim))
        out["eliminations"] = {"applied": bool(elim), "amount": round(amt, 2)}
    return out


def _period_label(ps: dt.date, pe: dt.date) -> str:
    if ps.month == pe.month:
        return ps.strftime("%B %Y")
    if ps.month == 1 and pe.month == 12:
        return str(ps.year)
    return f"{ps.strftime('%b')}-{pe.strftime('%b %Y')}"


# ── Queue (SPEC 4.3) ─────────────────────────────────────────────────────────
def _source_label(bank: str | None) -> str:
    if not bank:
        return "Bank feed"
    low = bank.lower()
    if "amex" in low or "american express" in low or "delta" in low:
        return "AMEX"
    return bank


async def build_books_queue(s, tenant_id) -> dict:
    bmap = {b.id: b.key for b in (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id))).scalars().all()}
    approvals = (await s.execute(select(BookTxn).where(
        BookTxn.tenant_id == tenant_id, BookTxn.scan_state == "needs_approval")
        .order_by(BookTxn.txn_date.asc()))).scalars().all()
    # IC escalations (link-level, the CFO seat)
    esc_links = (await s.execute(select(ICLink).where(
        ICLink.tenant_id == tenant_id, ICLink.status.in_(("escalated", "unmatched")))
        .order_by(ICLink.occurred_on.asc()))).scalars().all()

    approved_7d = (await s.execute(select(func.count(BookTxn.id)).where(
        BookTxn.tenant_id == tenant_id, BookTxn.scan_state.in_(("approved", "posted")),
        BookTxn.reviewed_at >= dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)))).scalar_one()

    # The transactions behind the escalations — the detail a person needs to characterize.
    txn_ids = {tid for l in esc_links for tid in (l.from_txn_id, l.to_txn_id) if tid}
    txmap = {}
    if txn_ids:
        txmap = {r.id: r for r in (await s.execute(select(BookTxn).where(
            BookTxn.id.in_(txn_ids)))).scalars().all()}

    _IC_OPTIONS = ["Loan (due-to / due-from)", "Distribution", "Capital contribution",
                   "Shared expense", "Rent", "Payroll allocation"]

    def _detail(t):
        return {"id": str(t.id), "entity": bmap.get(t.business_id), "qbo_type": t.qbo_type,
                "date": _fmt_date(t.txn_date), "amount": float(t.amount), "payee": t.payee,
                "memo": t.memo, "account": t.account_label, "bank_account": t.bank_account_label,
                "source": _source_label(t.bank_account_label),
                "qbo_url": qbo.app_txn_url(t.qbo_type, t.qbo_id)}

    def _appr(t):
        sug = t.suggestion or {}
        conf = sug.get("confidence")
        return {"id": str(t.id), "entity": bmap.get(t.business_id, "-"), "date": _fmt_date(t.txn_date),
                "vendor": t.payee, "amount": -abs(float(t.amount)) if t.qbo_type != "Deposit" else float(t.amount),
                "qbo_type": t.qbo_type, "memo": t.memo, "current_category": t.account_label,
                "bank_account": t.bank_account_label, "suggest": sug.get("category"),
                "conf": (f"{round(conf * 100)}%" if isinstance(conf, (int, float)) else None),
                "reason": sug.get("reason"), "source": _source_label(t.bank_account_label),
                "flags": t.flags or {}, "qbo_url": qbo.app_txn_url(t.qbo_type, t.qbo_id)}

    def _esc(l):
        sides = [_detail(txmap[tid]) for tid in (l.from_txn_id, l.to_txn_id) if tid in txmap]
        one_sided = (l.status == "unmatched") or (l.to_txn_id is None) or (l.from_business_id == l.to_business_id)
        primary = sides[0] if sides else None
        if l.note:
            label = l.note
        elif not one_sided:
            label = f"{bmap.get(l.from_business_id, '?')} -> {bmap.get(l.to_business_id, '?')} transfer"
        elif primary:
            label = primary["payee"] or primary["bank_account"] or f"{primary['qbo_type']} · unmatched"
        else:
            label = "Intercompany item"
        return {"id": str(l.id), "kind": "ic", "date": _fmt_date(l.occurred_on),
                "amount": float(l.amount), "label": label, "txns": sides,
                "reason": ("No covering policy rule, or over the monthly cap." if l.status == "escalated"
                           else "One-sided — no matching counterpart found in another entity."),
                "options": _IC_OPTIONS,
                "tax_note": "Characterization affects basis and taxes; the CFO decides."}

    return {
        "stats": {"awaiting": len(approvals), "escalated": len(esc_links), "approved_7d": approved_7d},
        "approvals": [_appr(t) for t in approvals],
        "escalations": [_esc(l) for l in esc_links],
    }


# ── Intercompany page (SPEC 4.4 GET /books/ic) ───────────────────────────────
async def build_books_ic(s, tenant_id) -> dict:
    bmap = {b.id: b.key for b in (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id))).scalars().all()}
    links = (await s.execute(select(ICLink).where(
        ICLink.tenant_id == tenant_id).order_by(ICLink.occurred_on.desc()))).scalars().all()
    rules = (await s.execute(select(ICRule).where(
        ICRule.tenant_id == tenant_id).order_by(ICRule.label))).scalars().all()

    txn_ids = {tid for l in links for tid in (l.from_txn_id, l.to_txn_id) if tid}
    txmap = {}
    if txn_ids:
        txmap = {r.id: r for r in (await s.execute(select(BookTxn).where(
            BookTxn.id.in_(txn_ids)))).scalars().all()}

    def _side(t):
        return {"entity": bmap.get(t.business_id), "qbo_type": t.qbo_type, "date": _fmt_date(t.txn_date),
                "amount": float(t.amount), "payee": t.payee, "memo": t.memo, "account": t.account_label,
                "bank_account": t.bank_account_label, "qbo_url": qbo.app_txn_url(t.qbo_type, t.qbo_id)}

    def _pair(l):
        return {"id": str(l.id), "from": bmap.get(l.from_business_id, "?"),
                "to": bmap.get(l.to_business_id, "?"), "amount": float(l.amount),
                "date": l.occurred_on.isoformat(), "status": l.status,
                "characterization": l.characterization,
                "txns": [_side(txmap[tid]) for tid in (l.from_txn_id, l.to_txn_id) if tid in txmap]}

    def _rule(r):
        return {"id": str(r.id), "label": r.label, "characterization": r.characterization,
                "from": bmap.get(r.from_business_id) if r.from_business_id else None,
                "to": bmap.get(r.to_business_id) if r.to_business_id else None,
                "monthly_cap": float(r.monthly_cap) if r.monthly_cap is not None else None,
                "active": r.active}

    open_amt = sum(float(l.amount) for l in links if l.status in ("unmatched", "escalated"))
    return {
        "pairs": [_pair(l) for l in links],
        "rules": [_rule(r) for r in rules],
        "blocking": {"open": sum(1 for l in links if l.status in ("unmatched", "escalated")),
                     "amount": round(open_amt, 2)},
    }


# ── Mutations (SPEC 4.4) ─────────────────────────────────────────────────────
async def _txn(s, tenant_id, txn_id) -> BookTxn | None:
    return (await s.execute(select(BookTxn).where(
        BookTxn.tenant_id == tenant_id, BookTxn.id == txn_id))).scalar_one_or_none()


async def approve_txn(s, tenant_id, user: User, txn_id) -> BookTxn | None:
    t = await _txn(s, tenant_id, txn_id)
    if t is None:
        return None
    cat = (t.suggestion or {}).get("category") or t.account_label
    t.scan_state = "approved"
    t.decision = {"action": "approve", "category": cat,
                  "account_qbo_id": (t.suggestion or {}).get("account_qbo_id")}
    t.reviewed_by, t.reviewed_at = user.id, dt.datetime.now(dt.timezone.utc)
    audit(s, tenant_id, user.id, "books.txn_approved", "book_txn", t.id, {"category": cat})
    await s.commit()
    return t


async def recategorize_txn(s, tenant_id, user: User, txn_id, category, account_qbo_id=None) -> BookTxn | None:
    t = await _txn(s, tenant_id, txn_id)
    if t is None:
        return None
    t.scan_state = "approved"
    t.decision = {"action": "recategorize", "category": category, "account_qbo_id": account_qbo_id}
    t.reviewed_by, t.reviewed_at = user.id, dt.datetime.now(dt.timezone.utc)
    audit(s, tenant_id, user.id, "books.txn_recategorized", "book_txn", t.id,
          {"from": t.account_label, "to": category})
    await s.commit()
    return t


async def escalate_txn(s, tenant_id, user: User, txn_id, note=None) -> BookTxn | None:
    t = await _txn(s, tenant_id, txn_id)
    if t is None:
        return None
    t.scan_state = "escalated"
    t.decision = {"action": "escalate", "note": note}
    t.reviewed_by, t.reviewed_at = user.id, dt.datetime.now(dt.timezone.utc)
    audit(s, tenant_id, user.id, "books.txn_escalated", "book_txn", t.id, {"note": note})
    await s.commit()
    return t


async def _link(s, tenant_id, link_id) -> ICLink | None:
    return (await s.execute(select(ICLink).where(
        ICLink.tenant_id == tenant_id, ICLink.id == link_id))).scalar_one_or_none()


async def characterize_ic(s, tenant_id, user: User, link_id, characterization, note=None) -> ICLink | None:
    if characterization not in IC_CHARACTERIZATIONS:
        raise ValueError(f"invalid characterization: {characterization}")
    l = await _link(s, tenant_id, link_id)
    if l is None:
        return None
    l.characterization, l.status = characterization, "characterized"
    l.decided_by, l.decided_at, l.note = user.id, dt.datetime.now(dt.timezone.utc), note
    now = dt.datetime.now(dt.timezone.utc)
    for tid in (l.from_txn_id, l.to_txn_id):            # both sides -> approved (human decided)
        if tid:
            t = await _txn(s, tenant_id, tid)
            if t:
                t.scan_state = "approved"
                t.decision = {"action": "ic_characterized", "characterization": characterization}
                t.reviewed_by, t.reviewed_at = user.id, now
    audit(s, tenant_id, user.id, "books.ic_characterized", "ic_link", l.id,
          {"characterization": characterization})
    await s.commit()
    return l


async def tie_ic(s, tenant_id, user: User, link_id) -> ICLink | None:
    l = await _link(s, tenant_id, link_id)
    if l is None:
        return None
    l.status = "tied"
    audit(s, tenant_id, user.id, "books.ic_tied", "ic_link", l.id, None)
    await s.commit()
    return l
