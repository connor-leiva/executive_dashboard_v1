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
from .books_scan import (IC_CHARACTERIZATIONS, BASIS_HISTORY, BASIS_OVER_BAND, BASIS_SPLIT,
                         BASIS_CLAUDE, BASIS_NONE)
from .metrics import _pl_period, _period_range, canonical_period, period_label

# What the reviewer reads instead of the tag. The prose in `reason` still carries the detail;
# this is the groupable half.
BASIS_LABELS = {
    BASIS_HISTORY: "Matched from history",
    BASIS_OVER_BAND: "Known vendor, unusual amount",
    BASIS_SPLIT: "Split — never auto-categorized",
    BASIS_CLAUDE: "Claude read it",
    BASIS_NONE: "Not yet reached",
}


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
RAIL_STATES = ("pending", "cleared", "needs_approval", "escalated", "approved", "posted")
# What the review screen can filter to. "all" is the funnel's Captured; "auto" is the
# came_categorized lens, which cross-cuts the rail rather than sitting inside it.
QUEUE_FILTERS = RAIL_STATES + ("all", "auto")

# The second axis: what DECIDED the category, as opposed to where the transaction sits.
# Independent of scan_state — something can be cleared by history or cleared by Claude.
BASIS_FILTERS = (BASIS_HISTORY, BASIS_CLAUDE, BASIS_OVER_BAND, BASIS_SPLIT, BASIS_NONE, "any")

# How much to trust a call. This is policy, not presentation: the filter, the row badge and
# the drawer's explanation all read it here, so "weak" cannot come to mean three things.
# A history match earns strength from repetition; Claude earns it from confidence.
STRENGTH = {"history_strong": 25, "history_thin": 5,
            "claude_strong": 0.80, "claude_thin": 0.50}


def basis_of(suggestion: dict | None) -> str:
    """The basis a row filters and renders under, in ONE place. If the facet count derived this
    differently from the row, a chip would promise rows the list then refuses to show."""
    sug = suggestion or {}
    # A suggestion with no tag predates migration 0059; only Pass 3 wrote those, which is the
    # same conclusion the backfill reached from the prose.
    return sug.get("basis") or (BASIS_NONE if not sug else BASIS_CLAUDE)


def strength_rule(suggestion: dict | None) -> str:
    """The threshold sentence, generated FROM the thresholds. A hand-written copy in the UI
    would still read '25 priors' the day somebody tuned it to 40."""
    basis = (suggestion or {}).get("basis")
    if not basis or basis in (BASIS_SPLIT, BASIS_NONE):
        return ""
    if basis == BASIS_CLAUDE:
        return ("Strong at %d%% confidence or more, thin from %d%%."
                % (STRENGTH["claude_strong"] * 100, STRENGTH["claude_thin"] * 100))
    if basis == BASIS_OVER_BAND:
        return "Capped at thin: a known vendor behaving unusually is never a strong call."
    return ("Strong at %d prior charges or more, thin from %d."
            % (STRENGTH["history_strong"], STRENGTH["history_thin"]))


def strength_of(suggestion: dict | None) -> str:
    """strong | thin | weak | na — how much evidence stands behind this category."""
    sug = suggestion or {}
    basis = sug.get("basis")
    if not basis or basis in (BASIS_SPLIT, BASIS_NONE):
        return "na"                       # nothing was decided, so there is nothing to trust
    if basis == BASIS_HISTORY:
        p = sug.get("priors") or 0
        return ("strong" if p >= STRENGTH["history_strong"]
                else "thin" if p >= STRENGTH["history_thin"] else "weak")
    if basis == BASIS_CLAUDE:
        c = sug.get("confidence") or 0
        return ("strong" if c >= STRENGTH["claude_strong"]
                else "thin" if c >= STRENGTH["claude_thin"] else "weak")
    # over_band is capped at thin on purpose: a known vendor behaving unusually is never a
    # strong call, however many priors it has. The priors are why we noticed, not reassurance.
    p = sug.get("priors") or 0
    return "thin" if p >= STRENGTH["history_thin"] else "weak"


async def _rail(s, tenant_id, mstart, mend) -> dict:
    """Scan-pipeline counts for a window. `captured` partitions exactly into the scan states —
    the rail-conservation invariant.

    The partition covers approved and posted too. It previously stopped at the four upstream
    states, which meant the invariant silently went false the moment anybody approved anything
    inside the window: an approved txn is still captured but was counted in no bucket. Harmless
    while approvals were rare; guaranteed to fire once the Friday review approves in bulk.
    """
    def _c(*conds):
        return select(func.count(BookTxn.id)).where(
            BookTxn.tenant_id == tenant_id, BookTxn.txn_date >= mstart,
            BookTxn.txn_date <= mend, *conds)
    captured = (await s.execute(_c())).scalar_one()
    by_state = {st: (await s.execute(_c(BookTxn.scan_state == st))).scalar_one()
                for st in RAIL_STATES}
    auto = (await s.execute(_c(BookTxn.came_categorized.is_(True)))).scalar_one()
    return {"captured": captured, "auto_categorized": auto, **by_state}


async def books_invariants(s, tenant_id, today=None) -> dict:
    """Compute + log the Part 7 acceptance checks the frontend/tests assert on."""
    today = today or dt.date.today()
    mstart, mend = _month_bounds(today)
    rail = await _rail(s, tenant_id, mstart, mend)
    rail_ok = rail["captured"] == sum(rail[st] for st in RAIL_STATES)
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


async def build_books_queue(s, tenant_id, period: str = "mtd", state: str = "needs_approval",
                            basis: str = "any", auto_only: bool = False,
                            weak_only: bool = False,
                            include_signed_off: bool = False) -> dict:
    """The review list.

    Two independent axes. `state` is where a transaction sits in the pipeline; `basis` is what
    decided its category. They cross: something can be cleared BY history or cleared BY Claude,
    and "show me everything Claude decided" is a different question from "show me what cleared".
    Filtering only by stage — which is all this did — cannot ask the second one.

    Signed-off rows are hidden by default. A cleared transaction someone eyeballed and accepted
    should not reappear next Friday; it keeps its scan_state (so the rail still balances) and
    carries a reviewer instead. See acknowledge_txn.
    """
    start, end = _period_range(period)
    businesses = (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id).order_by(Business.sort_order))).scalars().all()
    bmap = {b.id: b.key for b in businesses}
    # Entity identity ships WITH the payload instead of living in a frontend constant. A
    # hardcoded map is per-customer code: the labels are one workspace's companies and the dot
    # colours are chosen by hand, so a second tenant gets wrong names and no colours at all.
    # Business.accent/ink already exist and are already editable — this just carries them.
    entities = [{"key": b.key, "name": b.name, "accent": b.accent, "ink": b.ink}
                for b in businesses]
    # Who signed a transaction off. The Approved stage is what an outside accountant reads, and
    # "approved" without a name attached is not a reviewable record — it is an assertion.
    umap = {u.id: (u.name or u.email) for u in (await s.execute(select(User).where(
        User.tenant_id == tenant_id))).scalars().all()}

    window = [BookTxn.tenant_id == tenant_id,
              BookTxn.txn_date >= start, BookTxn.txn_date <= end]

    # Facet the window from one lightweight read rather than a COUNT per chip. Sixteen round
    # trips would be the least of it: a plain COUNT cannot express "how many WOULD there be if
    # this facet were cleared", and without that a chip reads 30 above a list showing 4.
    facet_rows = (await s.execute(select(
        BookTxn.scan_state, BookTxn.came_categorized, BookTxn.reviewed_at,
        BookTxn.suggestion).where(*window))).all()

    def _passes(f, skip=None):
        """Every active filter except `skip` — so a facet never constrains its own count."""
        st, came, rev, sug = f
        if skip != "stage" and state and state != "all":
            if state == "auto":
                if not came:
                    return False
            elif st != state:
                return False
        if skip != "basis" and basis and basis != "any" and basis_of(sug) != basis:
            return False
        if skip != "auto_only" and auto_only and not came:
            return False
        if skip != "weak_only" and weak_only and strength_of(sug) != "weak":
            return False
        # Approved is the one stage where hiding signed-off work would hide the stage itself.
        if skip != "signed_off" and not include_signed_off and rev is not None and st != "approved":
            return False
        return True

    def _count(skip, pred):
        return sum(1 for f in facet_rows if _passes(f, skip) and pred(f))

    stages = {st: _count("stage", lambda f, st=st: f[0] == st) for st in RAIL_STATES}
    stages["all"] = _count("stage", lambda f: True)
    stages["auto"] = stages["auto_categorized"] = _count("stage", lambda f: bool(f[1]))
    # "signed off" is a lens, not a stage. It lived in `stages` and so applied the stage filter
    # while every real stage entry skips it — which made one key in the dict move between tabs
    # while the rest held still. It belongs in `lenses`, where it is faceted like its siblings.
    bases = {b: _count("basis", lambda f, b=b: basis_of(f[3]) == b) for b in BASIS_FILTERS
             if b != "any"}
    bases["any"] = _count("basis", lambda f: True)
    lenses = {"auto": _count("auto_only", lambda f: bool(f[1])),
              "weak": _count("weak_only", lambda f: strength_of(f[3]) == "weak"),
              "reviewed": _count("signed_off", lambda f: f[2] is not None)}

    conds = list(window)
    if state == "auto":
        # Not a rail bucket: "came in already categorized" cross-cuts the states, which is why
        # the home strip shows it as a stage of the funnel rather than a slot in the partition.
        conds.append(BookTxn.came_categorized.is_(True))
    elif state and state != "all":
        conds.append(BookTxn.scan_state == state)
    if auto_only:
        conds.append(BookTxn.came_categorized.is_(True))
    # Asking for the Approved stage IS asking to see signed-off work — approving stamps
    # reviewed_at, so hiding signed-off rows there leaves the chip reading 9 above an empty
    # list. Every other stage still hides what has been signed off, which is the point.
    if not include_signed_off and state != "approved":
        conds.append(BookTxn.reviewed_at.is_(None))
    rows = (await s.execute(select(BookTxn).where(*conds)
                            .order_by(BookTxn.txn_date.asc()))).scalars().all()
    # basis and strength live inside the suggestion JSON, whose operators differ between
    # Postgres and SQLite. Filtering them here keeps one behaviour on both, and the SQL above
    # has already cut the set to something small.
    if basis and basis != "any":
        rows = [t for t in rows if basis_of(t.suggestion) == basis]
    if weak_only:
        rows = [t for t in rows if strength_of(t.suggestion) == "weak"]

    # The approval tab keeps its historical meaning (everything outstanding, not just this
    # window) so the backlog stays visible rather than vanishing behind a date filter.
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
        b = basis_of(sug)
        return {"id": str(t.id), "entity": bmap.get(t.business_id, "-"), "date": _fmt_date(t.txn_date),
                "vendor": t.payee, "amount": -abs(float(t.amount)) if t.qbo_type != "Deposit" else float(t.amount),
                "qbo_type": t.qbo_type, "memo": t.memo, "current_category": t.account_label,
                "bank_account": t.bank_account_label, "suggest": sug.get("category"),
                "conf": (f"{round(conf * 100)}%" if isinstance(conf, (int, float)) else None),
                "reason": sug.get("reason"), "source": _source_label(t.bank_account_label),
                "flags": t.flags or {}, "qbo_url": qbo.app_txn_url(t.qbo_type, t.qbo_id),
                # Why this transaction is where it is — the Friday review reads these.
                "basis": b, "basis_label": BASIS_LABELS.get(b, b),
                "priors": sug.get("priors"),
                # How much evidence stands behind it. Computed here so the filter, the badge and
                # the drawer cannot drift into meaning three different things by "weak".
                "strength": strength_of(sug), "strength_rule": strength_rule(sug),
                "scan_state": t.scan_state,
                "came_categorized": bool(t.came_categorized),
                # A split's "category" is where it already sits, not a proposal. Saying so stops
                # the UI rendering it as a 0%-confidence suggestion, which reads as a bad guess.
                "is_proposal": b not in (BASIS_SPLIT, BASIS_NONE),
                "signed_off": t.reviewed_at is not None,
                "signed_off_at": t.reviewed_at.isoformat() if t.reviewed_at else None,
                "decision": (t.decision or {}).get("action"),
                # The audit trail an accountant needs: who, and what they called it. A
                # recategorize records the NEW category here while account_label still shows
                # what QuickBooks has, which is exactly the gap someone has to go close.
                "decided_by": umap.get(t.reviewed_by),
                "decided_category": (t.decision or {}).get("category")}

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
        # The line-by-line review: every transaction in the window, whatever happened to it.
        "period": {"key": canonical_period(period), "label": period_label(period),
                   "start": start.isoformat(), "end": end.isoformat()},
        "filter": {"state": state, "basis": basis, "auto_only": auto_only,
                   "weak_only": weak_only, "include_signed_off": include_signed_off},
        "stages": stages,
        "bases": bases,
        "basis_labels": BASIS_LABELS,
        "lenses": lenses,
        # Entity identity and the evidence thresholds both travel with the payload so the
        # client renders what the server decided instead of keeping a second copy of either.
        "entities": entities,
        "thresholds": STRENGTH,
        "rows": [_appr(t) for t in rows],
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


async def acknowledge_txn(s, tenant_id, user: User, txn_id) -> BookTxn | None:
    """"I looked at this on Friday and I'm fine with it."

    Deliberately does NOT change scan_state. An auto-cleared transaction was never proposed for
    approval, so calling it approved would overstate what happened — and moving it to a new
    bucket would break rail conservation, alarming the Books home screen for no reason. It keeps
    its state and gains a reviewer, which is enough to drop it out of next Friday's list.
    """
    t = await _txn(s, tenant_id, txn_id)
    if t is None:
        return None
    t.decision = {"action": "acknowledged", "category": t.account_label,
                  "was_state": t.scan_state}
    t.reviewed_by, t.reviewed_at = user.id, dt.datetime.now(dt.timezone.utc)
    audit(s, tenant_id, user.id, "books.txn_acknowledged", "book_txn", t.id,
          {"category": t.account_label, "scan_state": t.scan_state})
    await s.commit()
    return t


BULK_ACTIONS = ("approve", "acknowledge")
BULK_MAX = 500


async def bulk_review(s, tenant_id, user: User, txn_ids: list, action: str) -> dict:
    """Approve or acknowledge many at once — the Friday review's whole point.

    Every row still gets its own reviewer stamp and its own audit entry, because a bulk action
    is a hundred decisions made quickly, not one decision about a hundred things. Ids that do
    not resolve are reported rather than silently skipped: a bulk tool that quietly does less
    than you asked is worse than one that fails.
    """
    if action not in BULK_ACTIONS:
        raise ValueError(f"invalid bulk action: {action}")
    ids = list(dict.fromkeys(txn_ids))                  # de-dupe, keep order
    if len(ids) > BULK_MAX:
        raise ValueError(f"too many transactions in one action (max {BULK_MAX})")
    rows = (await s.execute(select(BookTxn).where(
        BookTxn.tenant_id == tenant_id, BookTxn.id.in_(ids)))).scalars().all()
    found = {r.id for r in rows}
    now = dt.datetime.now(dt.timezone.utc)
    for t in rows:
        if action == "approve":
            cat = (t.suggestion or {}).get("category") or t.account_label
            t.scan_state = "approved"
            t.decision = {"action": "approve", "category": cat, "bulk": True,
                          "account_qbo_id": (t.suggestion or {}).get("account_qbo_id")}
        else:
            t.decision = {"action": "acknowledged", "category": t.account_label,
                          "was_state": t.scan_state, "bulk": True}
        t.reviewed_by, t.reviewed_at = user.id, now
        audit(s, tenant_id, user.id, f"books.txn_{action}d", "book_txn", t.id,
              {"bulk": True, "category": t.decision.get("category")})
    await s.commit()
    return {"action": action, "requested": len(ids), "applied": len(rows),
            "missing": [str(i) for i in ids if i not in found]}


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
