"""Acumyn Books — the deterministic scan pipeline (SPEC-books-module Part 3.1-3.2).

Runs in the worker after each txn sync, over scan_state='pending' rows oldest first.
Writes ONLY to Acumyn tables (book_txn, ic_link) — never to QuickBooks. Two passes here:

  Pass 1  history rules  — a vendor categorized the same way >= 3 times clears silently
                           (the everyday ~90%); a known vendor at an unusual amount is
                           flagged over_band -> needs_approval instead.
  Pass 2  intercompany   — Transfers / cross-entity movement are paired into ICLinks and
                           auto-tied by CFO policy rules, or escalated. Never goes to Claude.

Pass 3 (Claude, the ambiguous remainder) is Step 4 and leaves rows 'pending' here.
"""
import datetime as dt
import json
from collections import Counter
from decimal import Decimal

from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import BookTxn, PLLine, ICLink, ICRule, Business

_HISTORY_STATES = ("cleared", "approved", "posted")
IC_CHARACTERIZATIONS = {"loan", "distribution", "contribution", "shared_expense", "rent", "payroll_alloc"}
# The money-out side of an intercompany pair (vs. a Deposit, which is money in).
_OUTFLOW_TYPES = ("Transfer", "Purchase", "Bill", "BillPayment", "JournalEntry")


def _norm(p) -> str:
    return " ".join((p or "").lower().split())


# ── Pass 1: deterministic history rules ──────────────────────────────────────
async def _load_history_index(s, tenant_id, today, go_live=None):
    """Build a (business_id, normalized payee) -> [ground-truth rows] index ONCE per scan,
    over the trailing 12 months. Ground truth = cleared/approved/posted, or historical
    (came_categorized, before the pipeline go-live). Snapshotted at pass start, so a row
    cleared earlier this same pass doesn't bootstrap a later clear (conservative, correct)."""
    since = today - dt.timedelta(days=365)
    rows = (await s.execute(select(BookTxn).where(
        BookTxn.tenant_id == tenant_id,
        BookTxn.txn_date >= since, BookTxn.txn_date <= today))).scalars().all()
    idx: dict = {}
    for r in rows:
        if not r.payee:
            continue
        historical = r.came_categorized and (go_live is None or (r.txn_date < go_live))
        if r.scan_state in _HISTORY_STATES or historical:
            idx.setdefault((r.business_id, _norm(r.payee)), []).append(r)
    return idx


def _pass1(txn, history):
    """(kind, suggestion) where kind in {'clear','over_band'}, or None to leave pending.
    A known vendor with a consistent category clears; the same vendor at an amount well
    outside its band is a human check, not a silent clear."""
    if len(history) < 3:
        return None
    labels = [h.account_label for h in history if h.account_label]
    if not labels:
        return None
    modal, modal_n = Counter(labels).most_common(1)[0]
    if modal_n / len(history) < 0.80:
        return None
    if (txn.account_label or "") != modal:      # arrived on a different account -> Claude/human
        return None
    n = len(history)
    trailing_max = max((abs(float(h.amount)) for h in history), default=0.0)
    if trailing_max and abs(float(txn.amount)) <= 1.5 * trailing_max:
        return ("clear", {"category": modal, "account_qbo_id": txn.account_qbo_id,
                          "confidence": 0.99,
                          "reason": f"Matches {n} prior charges categorized here."})
    return ("over_band", {"category": modal, "account_qbo_id": txn.account_qbo_id,
                          "confidence": 0.6,
                          "reason": f"Known vendor, amount above the usual range ({n} priors)."})


# ── Pass 2: intercompany detection ───────────────────────────────────────────
def _is_ic_candidate(txn, alias_map) -> bool:
    if txn.qbo_type == "Transfer":
        return True
    hay = f"{txn.payee or ''} {txn.memo or ''}".lower()
    for bid, aliases in alias_map.items():
        if bid == txn.business_id:
            continue
        if any(a and a.lower() in hay for a in aliases):
            return True
    return False


async def _find_ic_match(s, tenant_id, txn):
    """A counterpart in a DIFFERENT realm: equal amount, txn_date within 3 days, not
    already linked. Prefer the opposite flow direction (a Deposit for an outflow)."""
    lo, hi = txn.txn_date - dt.timedelta(days=3), txn.txn_date + dt.timedelta(days=3)
    cands = (await s.execute(select(BookTxn).where(
        BookTxn.tenant_id == tenant_id, BookTxn.realm_id != txn.realm_id,
        BookTxn.amount == txn.amount, BookTxn.txn_date >= lo, BookTxn.txn_date <= hi,
        BookTxn.id != txn.id).order_by(BookTxn.txn_date.asc()))).scalars().all()
    want_deposit = txn.qbo_type in _OUTFLOW_TYPES
    best = None
    for c in cands:
        linked = (await s.execute(select(func.count(ICLink.id)).where(
            ICLink.tenant_id == tenant_id,
            or_(ICLink.from_txn_id == c.id, ICLink.to_txn_id == c.id)))).scalar_one()
        if linked:
            continue
        if (c.qbo_type == "Deposit") == want_deposit:   # opposite direction -> ideal
            return c
        best = best or c
    return best


async def _eval_ic_rule(s, tenant_id, from_bid, to_bid, amount, occurred_on):
    """First active rule whose direction matches (null side = any) and (rule, within_cap).
    Cap sums this calendar month's auto_tied amounts under that rule. (None, False) if no
    rule applies."""
    rules = (await s.execute(select(ICRule).where(
        ICRule.tenant_id == tenant_id, ICRule.active.is_(True)))).scalars().all()
    for rule in rules:
        if rule.from_business_id not in (None, from_bid):
            continue
        if rule.to_business_id not in (None, to_bid):
            continue
        if rule.monthly_cap is None:
            return (rule, True)
        month_start = occurred_on.replace(day=1)
        nxt = (month_start + dt.timedelta(days=32)).replace(day=1)
        prior = (await s.execute(select(func.coalesce(func.sum(ICLink.amount), 0)).where(
            ICLink.tenant_id == tenant_id, ICLink.rule_id == rule.id,
            ICLink.status == "auto_tied",
            ICLink.occurred_on >= month_start, ICLink.occurred_on < nxt))).scalar_one()
        return (rule, float(prior) + float(amount) <= float(rule.monthly_cap))
    return (None, False)


def _flag(txn, key):
    txn.flags = {**(txn.flags or {}), key: True}


async def _handle_ic(s, tenant_id, txn, today) -> str:
    """Pair the candidate across realms, create an ICLink, and auto-tie or escalate per
    policy. Returns the resulting scan_state for the tally."""
    match = await _find_ic_match(s, tenant_id, txn)
    if match is None:                                   # one-sided -> escalate, blocks close
        s.add(ICLink(tenant_id=tenant_id, from_business_id=txn.business_id,
                     to_business_id=txn.business_id, from_txn_id=txn.id,
                     amount=txn.amount, occurred_on=txn.txn_date, status="unmatched"))
        _flag(txn, "intercompany")
        txn.scan_state = "escalated"
        return "escalated"

    # Direction: the outflow side is "from", the Deposit side is "to".
    if txn.qbo_type == "Deposit":
        frm, to = match, txn
    else:
        frm, to = txn, match
    rule, ok = await _eval_ic_rule(s, tenant_id, frm.business_id, to.business_id,
                                   txn.amount, txn.txn_date)
    link = ICLink(tenant_id=tenant_id, from_business_id=frm.business_id,
                  to_business_id=to.business_id, from_txn_id=frm.id, to_txn_id=to.id,
                  amount=txn.amount, occurred_on=min(frm.txn_date, to.txn_date))
    for t in (frm, to):
        _flag(t, "intercompany")
    if rule and ok:
        link.status, link.characterization, link.rule_id = "auto_tied", rule.characterization, rule.id
        frm.scan_state = to.scan_state = "cleared"
        result = "cleared"
    else:                                               # no rule, or over cap
        link.status = "escalated"
        frm.scan_state = to.scan_state = "escalated"
        result = "escalated"
    s.add(link)
    return result


# ── Orchestration ────────────────────────────────────────────────────────────
async def _scan_one(s, tenant_id, txn, alias_map, history_idx, today) -> str:
    if (txn.flags or {}).get("multi_line"):             # split txn -> always a human call
        if not txn.suggestion:
            txn.suggestion = {"category": txn.account_label, "confidence": 0.0,
                              "reason": "Split across multiple accounts; needs review."}
        txn.scan_state = "needs_approval"
        return "needs_approval"
    if _is_ic_candidate(txn, alias_map):
        return await _handle_ic(s, tenant_id, txn, today)
    history = [h for h in history_idx.get((txn.business_id, _norm(txn.payee)), []) if h.id != txn.id]
    p1 = _pass1(txn, history)
    if p1:
        kind, suggestion = p1
        txn.suggestion = suggestion
        if kind == "clear":
            txn.scan_state = "cleared"
            return "cleared"
        _flag(txn, "over_band")
        txn.scan_state = "needs_approval"
        return "needs_approval"
    return "pending"                                    # leave for Pass 3 (Claude, Step 4)


async def run_scan(s: AsyncSession, tenant_id, today=None) -> dict:
    """Run the deterministic passes over every pending txn for the tenant, oldest first.
    Returns the per-state tally; logs a books_scan line the invariant checker reads."""
    today = today or dt.date.today()
    businesses = (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id))).scalars().all()
    alias_map = {b.id: {b.name, b.key, *((b.config or {}).get("ic_aliases", []))}
                 for b in businesses}
    history_idx = await _load_history_index(s, tenant_id, today)
    pending = (await s.execute(select(BookTxn).where(
        BookTxn.tenant_id == tenant_id, BookTxn.scan_state == "pending")
        .order_by(BookTxn.txn_date.asc(), BookTxn.id.asc()))).scalars().all()

    tally = {"cleared": 0, "needs_approval": 0, "escalated": 0, "pending": 0}
    for txn in pending:
        if txn.scan_state != "pending":                 # resolved as another txn's IC match
            continue
        tally[await _scan_one(s, tenant_id, txn, alias_map, history_idx, today)] += 1
    await s.commit()
    print(f"books_scan deterministic seen={len(pending)} cleared={tally['cleared']} "
          f"needs_approval={tally['needs_approval']} escalated={tally['escalated']} "
          f"pending={tally['pending']}", flush=True)

    if _claude_enabled():                               # Pass 3: the ambiguous remainder
        c = await run_claude_pass(s, tenant_id, today, history_idx=history_idx)
        tally["cleared"] += c["cleared"]
        tally["needs_approval"] += c["needs_approval"]
        tally["pending"] -= (c["cleared"] + c["needs_approval"])
    return tally


# ── Pass 3: Claude categorization of the ambiguous remainder (SPEC 3.3) ────────
# Behind config (BOOKS_SCAN_CLAUDE_ENABLED + a key). Claude suggests a category from the
# business chart of accounts and flags anomalies; it NEVER posts to QuickBooks and never
# has the last word — every output routes to `cleared` (only when it confirms the existing
# category with high confidence) or `needs_approval` (a human decides). This module imports
# nothing from the QBO write path (Part 7 invariant #6).
_client_obj = None


def _claude_enabled() -> bool:
    return bool(settings.ANTHROPIC_API_KEY) and settings.BOOKS_SCAN_CLAUDE_ENABLED


def _client():
    global _client_obj
    if _client_obj is None:
        from anthropic import AsyncAnthropic
        _client_obj = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client_obj


def _text_of(resp) -> str:
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()


async def _coa_for(s, tenant_id, business_id) -> list[dict]:
    """The categorization universe for a business — its P&L accounts (name + section as
    type), from the synced PLLine detail. Claude may only suggest from this list."""
    rows = (await s.execute(select(PLLine.label, PLLine.section).where(
        PLLine.tenant_id == tenant_id, PLLine.business_id == business_id).distinct())).all()
    return [{"account": label, "type": section} for label, section in rows]


def _history_digest(history_idx, batch) -> list[dict]:
    """payee -> (modal category, count, amount band) for the payees in this batch."""
    out, seen = [], set()
    for t in batch:
        key = (t.business_id, _norm(t.payee))
        if not t.payee or key in seen:
            continue
        seen.add(key)
        rows = history_idx.get(key, [])
        labels = [r.account_label for r in rows if r.account_label]
        if not labels:
            continue
        amts = [abs(float(r.amount)) for r in rows]
        out.append({"payee": t.payee, "modal_category": Counter(labels).most_common(1)[0][0],
                    "count": len(rows), "amount_band": [round(min(amts), 2), round(max(amts), 2)]})
    return out


def _scan_system_prompt(biz, coa, digest) -> str:
    name = biz.name if biz else "the business"
    return (
        f"You are a bookkeeping assistant categorizing transactions for {name}. Suggest a "
        f"category for each transaction, choosing ONLY from this chart of accounts (never "
        f"invent an account):\n{json.dumps(coa, separators=(',', ':'))}\n\n"
        f"Vendor history (payee -> the category it is usually booked to, count, amount band):\n"
        f"{json.dumps(digest, separators=(',', ':'))}\n\n"
        f"For each transaction in the user's JSON array, return a JSON array (and NOTHING "
        f"else) of objects: {{\"id\", \"category\" (from the chart above), \"confidence\" "
        f"(0-1), \"reason\" (one short sentence), \"flags\": {{\"anomaly\", \"first_vendor\", "
        f"\"over_band\", \"possible_1099\"}}}}. Set a flag true only when it applies: "
        f"first_vendor (payee not in the history), over_band (amount far outside the vendor's "
        f"band), possible_1099 (a contractor paid by ACH/check), anomaly (anything else off). "
        f"Prefer the vendor's historical category when one exists; keep current_category when "
        f"it already looks right. Strict JSON only, exactly one object per input id."
    )


def _parse_scan_json(text: str) -> dict:
    """Defensively parse the model's JSON array into {id: item}. A malformed response
    yields {} (every txn then falls back to needs_approval)."""
    if not text:
        return {}
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json":
            t = t[4:]
    try:
        arr = json.loads(t[t.index("["):t.rindex("]") + 1])
    except (ValueError, json.JSONDecodeError):
        return {}
    return {str(it["id"]): it for it in arr
            if isinstance(it, dict) and it.get("id") is not None}


def _route_batch(batch, parsed) -> tuple[int, int]:
    """Apply the model's suggestions. Clear ONLY when confidence >= threshold AND the
    suggested category equals the txn's current account_label AND no anomaly flags — else
    needs_approval. Malformed/missing item -> needs_approval, suggestion=None."""
    thr = settings.BOOKS_CONF_THRESHOLD
    cleared = needs = 0
    for t in batch:
        item = parsed.get(str(t.id))
        if not isinstance(item, dict) or "category" not in item:
            t.scan_state, t.suggestion = "needs_approval", None
            needs += 1
            continue
        try:
            conf = float(item.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        cat = item.get("category")
        flags = item.get("flags") if isinstance(item.get("flags"), dict) else {}
        anomaly = any(bool(v) for v in flags.values())
        t.suggestion = {"category": cat, "account_qbo_id": None, "confidence": conf,
                        "reason": item.get("reason") or ""}
        set_flags = {k: True for k, v in flags.items() if v}
        if set_flags:
            t.flags = {**(t.flags or {}), **set_flags}
        if conf >= thr and cat == (t.account_label or "") and not anomaly:
            t.scan_state = "cleared"
            cleared += 1
        else:
            t.scan_state = "needs_approval"
            needs += 1
    return cleared, needs


async def _claude_call(client, model, system, user) -> str:
    """The single network seam (tests monkeypatch this). Returns the model's raw text."""
    resp = await client.messages.create(
        model=model, max_tokens=settings.BOOKS_SCAN_MAX_TOKENS, system=system,
        thinking={"type": "disabled"}, messages=[{"role": "user", "content": user}])
    return _text_of(resp)


async def run_claude_pass(s: AsyncSession, tenant_id, today=None, history_idx=None) -> dict:
    """Send the still-pending remainder to Claude in per-business batches and route the
    results. No-op (skipped) unless a key is set AND BOOKS_SCAN_CLAUDE_ENABLED."""
    if not _claude_enabled():
        return {"skipped": True, "cleared": 0, "needs_approval": 0}
    today = today or dt.date.today()
    if history_idx is None:
        history_idx = await _load_history_index(s, tenant_id, today)
    businesses = {b.id: b for b in (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id))).scalars().all()}
    pending = (await s.execute(select(BookTxn).where(
        BookTxn.tenant_id == tenant_id, BookTxn.scan_state == "pending")
        .order_by(BookTxn.txn_date.asc(), BookTxn.id.asc()))).scalars().all()
    if not pending:
        return {"skipped": False, "cleared": 0, "needs_approval": 0}

    by_biz: dict = {}
    for t in pending:
        by_biz.setdefault(t.business_id, []).append(t)

    client, model = _client(), (settings.BOOKS_SCAN_MODEL or settings.ASSISTANT_MODEL)
    batch_size = settings.BOOKS_SCAN_BATCH
    totals = {"skipped": False, "cleared": 0, "needs_approval": 0}
    for bid, txns in by_biz.items():
        biz = businesses.get(bid)
        coa = await _coa_for(s, tenant_id, bid)
        for i in range(0, len(txns), batch_size):
            batch = txns[i:i + batch_size]
            system = _scan_system_prompt(biz, coa, _history_digest(history_idx, batch))
            user = json.dumps([{
                "id": str(t.id), "date": t.txn_date.isoformat() if t.txn_date else None,
                "payee": t.payee, "memo": t.memo, "amount": float(t.amount),
                "bank_account": t.bank_account_label, "current_category": t.account_label,
            } for t in batch], separators=(",", ":"))
            try:
                text = await _claude_call(client, model, system, user)
                parsed = _parse_scan_json(text)
            except Exception as e:  # noqa: BLE001 — a failed batch -> all needs_approval
                print(f"[books_scan] claude batch failed: {e}", flush=True)
                parsed = {}
            c, n = _route_batch(batch, parsed)
            totals["cleared"] += c
            totals["needs_approval"] += n
            print(f"books_scan business={biz.key if biz else bid} batch={len(batch)} "
                  f"cleared={c} needs_approval={n} escalated=0", flush=True)
    await s.commit()
    return totals


# ── ICRule CRUD + placeholder seed (SPEC 3.4 / Part 11 #3) ────────────────────
async def create_ic_rule(s, tenant_id, label, characterization, from_business_id=None,
                         to_business_id=None, monthly_cap=None, active=True) -> ICRule:
    if characterization not in IC_CHARACTERIZATIONS:
        raise ValueError(f"invalid characterization: {characterization}")
    rule = ICRule(tenant_id=tenant_id, label=label, characterization=characterization,
                  from_business_id=from_business_id, to_business_id=to_business_id,
                  monthly_cap=monthly_cap, active=active)
    s.add(rule)
    await s.commit()
    return rule


async def update_ic_rule(s, tenant_id, rule_id, **fields) -> ICRule | None:
    rule = (await s.execute(select(ICRule).where(
        ICRule.tenant_id == tenant_id, ICRule.id == rule_id))).scalar_one_or_none()
    if rule is None:
        return None
    if "characterization" in fields and fields["characterization"] not in IC_CHARACTERIZATIONS:
        raise ValueError(f"invalid characterization: {fields['characterization']}")
    for k, v in fields.items():
        if v is not None and hasattr(rule, k):
            setattr(rule, k, v)
    await s.commit()
    return rule


async def seed_ic_rules(s, tenant_id, active=False) -> list[ICRule]:
    """The three known CFO rules. Amounts/splits are PLACEHOLDERS (SPEC Part 11 #3):
    seeded INACTIVE so they don't gate real txns until Connor confirms the figures.
    Idempotent by label."""
    defaults = [
        dict(label="Office rent to holding LLC", characterization="rent", monthly_cap=None),
        dict(label="ULRG-Sympli co-op marketing (cap $5,000/mo PLACEHOLDER)",
             characterization="shared_expense", monthly_cap=Decimal("5000")),
        dict(label="Payroll allocation (split PLACEHOLDER)",
             characterization="payroll_alloc", monthly_cap=None),
    ]
    existing = {r.label for r in (await s.execute(select(ICRule).where(
        ICRule.tenant_id == tenant_id))).scalars().all()}
    created = []
    for d in defaults:
        if d["label"] in existing:
            continue
        rule = ICRule(tenant_id=tenant_id, active=active, **d)
        s.add(rule)
        created.append(rule)
    await s.commit()
    return created
