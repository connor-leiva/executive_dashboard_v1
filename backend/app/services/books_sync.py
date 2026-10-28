"""Axcion Books — transaction-level QBO sync (SPEC-books-module Part 2).

Reuses the live QBO OAuth client and refresh-token rotation already powering
`sync_qbo_pl` (see sync.py). Additive: nothing here touches the PLSnapshot path the
dashboard reads. Two syncs per connected QBO integration (one per entity/realm):

  sync_qbo_txns     — pull ledger transactions into book_txn (the scan/queue unit)
  sync_qbo_pl_lines — pull the P&L detail tree into pl_line (behind the summary)

Upserts target Postgres (prod); the worker does not run against SQLite. The pure
QBO-object -> row mapping (`_txn_to_row` and its helpers) is what the tests exercise;
the txn JSON shapes and the P&L detail tree both get one live confirmation pass via
validate_books.py before this is trusted (SPEC Part 10 Step 2).
"""
import datetime as dt
from decimal import Decimal

from sqlalchemy import select, insert, delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations import qbo
from ..models import Integration, BookTxn, BookTxnLine, PLLine, SyncRun
from .sync import _valid_access_token, _QBO_PERIODS

# The ledger entities Books categorizes. Order matters only for readable logs.
_QBO_TXN_ENTITIES = ("Purchase", "Deposit", "JournalEntry", "Transfer", "Bill", "BillPayment")

# Suspense / holding accounts mean "nobody categorized this yet" -> came_categorized False.
_SUSPENSE = {"uncategorized expense", "uncategorized income", "uncategorized asset",
             "ask my accountant", "uncategorized"}

# QBO line detail blocks that carry the income/expense AccountRef (the category).
_CATEGORY_DETAIL_KEYS = ("AccountBasedExpenseLineDetail", "DepositLineDetail",
                         "JournalEntryLineDetail", "ItemBasedExpenseLineDetail")

# Only these source-side columns update on a re-sync. scan_state / suggestion / decision /
# reviewed_* / flags / posted_back_at are owned by the scan pipeline + human review and
# MUST survive a re-sync of an already-reviewed row (SPEC 2.3).
#
# `payable_id` BELONGS TO THAT SET AND IS DELIBERATELY ABSENT FROM THIS LIST. It is the link
# saying a human coded and approved this money BEFORE it moved, and it is what lets the txn
# skip the review queue entirely (books_scan). Adding it here would let the next QuickBooks
# sync quietly null it out, and the only symptom would be already-approved payments
# reappearing for review — with nothing pointing at the sync that erased the link.
_TXN_SOURCE_KEYS = ["sync_token", "txn_date", "amount", "payee", "memo",
                    "account_label", "account_qbo_id", "bank_account_label", "came_categorized"]


def txn_upsert(rows: list[dict]):
    """The re-sync statement. Factored out so a test can assert what a re-sync overwrites
    against the statement that actually runs, rather than against a copy of it in the test —
    the `payable_id` rule above is only worth as much as the thing checking it."""
    stmt = pg_insert(BookTxn).values(rows)
    return stmt.on_conflict_do_update(
        index_elements=["tenant_id", "realm_id", "qbo_type", "qbo_id"],
        set_={k: stmt.excluded[k] for k in _TXN_SOURCE_KEYS},
    )


def _trim(v, n):
    return v[:n] if isinstance(v, str) and len(v) > n else v


def _date(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _ref_name(d: dict, key: str):
    return ((d or {}).get(key) or {}).get("name") or None


def _category_lines(obj: dict) -> list[tuple[str, str | None]]:
    """Every (account_name, account_id) posted on this txn's lines, in order — the
    P&L category side (not the bank/payment side)."""
    out = []
    for ln in (obj.get("Line") or []):
        for k in _CATEGORY_DETAIL_KEYS:
            det = ln.get(k)
            if det and det.get("AccountRef"):
                ref = det["AccountRef"]
                out.append((ref.get("name") or "", ref.get("value") or None))
                break
    return out


def _line_entity_name(obj: dict):
    """Payee carried on a line rather than the header (Deposit / JournalEntry)."""
    for ln in (obj.get("Line") or []):
        for k in _CATEGORY_DETAIL_KEYS:
            ent = (ln.get(k) or {}).get("Entity") or {}
            nm = ent.get("name") or _ref_name(ent, "EntityRef")
            if nm:
                return nm
    return None


def _payee(obj: dict):
    for k in ("EntityRef", "VendorRef", "CustomerRef"):
        nm = (obj.get(k) or {}).get("name")
        if nm:
            return nm
    return _line_entity_name(obj)


def _amount(obj: dict, entity_type: str) -> float:
    if entity_type == "JournalEntry":
        debit = sum(float(ln.get("Amount") or 0) for ln in (obj.get("Line") or [])
                    if ((ln.get("JournalEntryLineDetail") or {}).get("PostingType") or "").lower() == "debit")
        if debit:
            return debit
        total = sum(float(ln.get("Amount") or 0) for ln in (obj.get("Line") or []))
        return round(total / 2, 2)                       # balanced JE with no posting types
    if entity_type == "Transfer":
        return float(obj.get("Amount") or 0)
    return float(obj.get("TotalAmt") or 0)


def _bank_account(obj: dict, entity_type: str):
    if entity_type == "Purchase":
        return _ref_name(obj, "AccountRef")              # the bank/card the money left
    if entity_type == "Deposit":
        return _ref_name(obj, "DepositToAccountRef")
    if entity_type == "Transfer":
        return _ref_name(obj, "FromAccountRef")
    if entity_type == "BillPayment":
        pay = obj.get("CheckPayment") or obj.get("CreditCardPayment") or {}
        return _ref_name(pay, "BankAccountRef") or _ref_name(pay, "CCAccountRef")
    return None                                          # Bill / others: no bank side


def _txn_to_row(tenant_id, integ: Integration, entity_type: str, obj: dict) -> dict:
    """Map one QBO entity to a BookTxn upsert row. Pure — this is the tested surface."""
    cats = _category_lines(obj)
    cat_name, cat_id = cats[0] if cats else ("", None)
    multi = len({(c[1] or c[0]) for c in cats}) > 1
    name_l = (cat_name or "").strip().lower()
    came = bool(cat_name) and name_l not in _SUSPENSE and "uncategor" not in name_l
    return {
        "tenant_id": tenant_id,
        "business_id": integ.business_id,
        "realm_id": integ.realm_id,
        "qbo_type": entity_type,
        "qbo_id": str(obj.get("Id") or ""),
        "sync_token": (str(obj["SyncToken"]) if obj.get("SyncToken") is not None else None),
        "txn_date": _date(obj.get("TxnDate")),
        "amount": Decimal(str(_amount(obj, entity_type))),
        "payee": _trim(_payee(obj), 200),
        "memo": _trim(obj.get("PrivateNote") or None, 2000),
        "account_label": _trim(cat_name or None, 200),
        "account_qbo_id": _trim(cat_id, 32),
        "bank_account_label": _trim(_bank_account(obj, entity_type), 200),
        "came_categorized": came,
        # multi_line is source-derived but lives in flags (scan-owned); set it at INSERT
        # only, so a re-sync never clobbers the scanner's anomaly/intercompany flags.
        "flags": {"multi_line": True} if multi else None,
        "scan_state": "pending",
    }


def _txn_lines(obj: dict) -> list[dict]:
    """Every line on one QBO transaction, in array order. Pure — the tested surface.

    `_txn_to_row` above keeps the header amount and the FIRST category line's account, which
    charges a four-line purchase's whole total to line one. These rows are what makes the
    difference visible. No extra QuickBooks call: the lines arrived with the transaction.

    Skipped: a line carrying no `Amount` (DescriptionOnly, SubTotal). Those are presentation,
    not money, and storing them would make a line count that means nothing.

    `line_num` is the ARRAY INDEX, not QBO's `LineNum`, which is absent on several entities and
    stops being contiguous once a line has been deleted. It is the key a re-sync replaces on.
    """
    out = []
    for i, ln in enumerate(obj.get("Line") or []):
        if ln.get("Amount") is None:
            continue
        detail_type = ln.get("DetailType")
        det = ln.get(detail_type or "") or {}
        if not det:                                   # older payloads omit DetailType
            for k in _CATEGORY_DETAIL_KEYS:
                if ln.get(k):
                    det = ln[k]
                    break
        ref = det.get("AccountRef") or {}
        ent = det.get("Entity") or {}
        # Mapped, never truncated. `posting_type` is String(6), so slicing an unexpected value
        # to six characters would store a plausible-looking wrong answer; SQLite would accept
        # it and Postgres would too. Anything that is not one of the two is no answer at all.
        posting = str(det.get("PostingType") or "").strip().lower()
        out.append({
            "line_num": i,
            "qbo_line_id": _trim(str(ln["Id"]), 32) if ln.get("Id") is not None else None,
            "detail_type": _trim(detail_type or None, 40),
            "account_label": _trim(ref.get("name") or None, 200),
            "account_qbo_id": _trim(ref.get("value") or None, 32),
            "amount": Decimal(str(ln.get("Amount") or 0)),
            "posting_type": posting if posting in ("debit", "credit") else None,
            "description": _trim(ln.get("Description") or None, 2000),
            "payee": _trim(ent.get("name") or _ref_name(ent, "EntityRef")
                           or _ref_name(det, "CustomerRef") or None, 200),
            "class_label": _trim(_ref_name(det, "ClassRef"), 200),
        })
    return out


def _txn_pairs(tenant_id, integ: Integration, by_entity: dict) -> list[tuple[dict, list[dict]]]:
    """Each upsert row beside the lines from the SAME object. Pure.

    One comprehension rather than two passes over `by_entity`: a row paired with a different
    object's lines reads as a correctly split transaction posted to the wrong accounts, and
    nothing downstream can tell. Dict iteration order is stable, but "stable" is not the same
    as "the same two passes", and this is not worth being clever about.
    """
    return [(_txn_to_row(tenant_id, integ, ent, obj), _txn_lines(obj))
            for ent, objs in by_entity.items() for obj in objs if obj.get("Id")]


async def _write_txn_lines(s: AsyncSession, tenant_id, integ: Integration, pairs: list) -> int:
    """Replace the stored lines for every transaction this pass touched.

    Replace rather than merge: a recategorized line comes back at the same position with a
    different account, and merging would leave both, which is the overstatement this table
    exists to remove.

    The transaction ids are resolved AFTER the upsert, by a SELECT. `on_conflict_do_update`
    keeps the existing row's id, so the id a caller guessed before the upsert may belong to
    nothing — and a line hanging off a transaction that is not there reads as a missing line,
    which looks exactly like the bug this fixes.
    """
    by_key = {}
    for row, lines in pairs:
        by_key[(row["qbo_type"], row["qbo_id"])] = lines   # last wins, as the upsert does
    if not by_key:
        return 0

    keys = list(by_key)
    found: dict[tuple[str, str], object] = {}
    for i in range(0, len(keys), 500):
        chunk = keys[i:i + 500]
        res = await s.execute(
            select(BookTxn.id, BookTxn.qbo_type, BookTxn.qbo_id).where(
                BookTxn.tenant_id == tenant_id, BookTxn.realm_id == integ.realm_id,
                BookTxn.qbo_id.in_([q for _t, q in chunk])))
        # Filtered on qbo_id alone and paired here: a row-value IN is not portable, and one
        # realm can hold a Purchase and a Deposit sharing an id.
        for txn_id, qbo_type, qbo_id in res:
            if (qbo_type, qbo_id) in by_key:
                found[(qbo_type, qbo_id)] = txn_id

    if not found:
        return 0
    txn_ids = list(found.values())
    for i in range(0, len(txn_ids), 500):
        await s.execute(delete(BookTxnLine).where(
            BookTxnLine.tenant_id == tenant_id,
            BookTxnLine.book_txn_id.in_(txn_ids[i:i + 500])))

    payload = [{"tenant_id": tenant_id, "book_txn_id": found[k], **ln}
               for k in keys if k in found for ln in by_key[k]]
    for i in range(0, len(payload), 500):
        await s.execute(insert(BookTxnLine), payload[i:i + 500])
    return len(payload)


def _cdc_split(data: dict, entities) -> dict:
    """Flatten a CDCResponse into {entity: [objects]}, dropping tombstones."""
    out = {e: [] for e in entities}
    for block in (data.get("CDCResponse") or []):
        for qr in (block.get("QueryResponse") or []):
            for e in entities:
                for obj in (qr.get(e) or []):
                    if isinstance(obj, dict) and (obj.get("status") != "Deleted"):
                        out[e].append(obj)
    return out


def _backfill_start(integ: Integration) -> dt.date:
    """OPEN ITEM (SPEC Part 11 #4): the fiscal-year backfill start awaits Connor's
    confirmation. Default to Jan 1 of the current year; make it overridable via the
    integration's non-secret config (`books_backfill_start`, ISO date) once set."""
    cfg = (integ.config or {}).get("books_backfill_start")
    d = _date(cfg)
    return d or dt.date(dt.date.today().year, 1, 1)


async def sync_qbo_txns(s: AsyncSession, tenant_id, integ: Integration, since=None) -> int:
    """Backfill (first run) or incremental-via-CDC pull of ledger transactions into
    book_txn. Upsert on uq_booktxn_src, refreshing only source columns so reviewed
    rows keep their decision. `since` is the incremental cursor; pass the value captured
    BEFORE sibling syncs (sync_qbo_pl) bumped integ.last_synced_at, else the CDC window
    collapses. Falls back to integ.last_synced_at. Returns the number of rows written."""
    token = await _valid_access_token(s, integ)
    now = dt.datetime.now(dt.timezone.utc)
    have = (await s.execute(select(func.count(BookTxn.id)).where(
        BookTxn.tenant_id == tenant_id, BookTxn.realm_id == integ.realm_id))).scalar_one()

    by_entity: dict[str, list] = {}
    if have == 0:
        start = _backfill_start(integ)
        for ent in _QBO_TXN_ENTITIES:
            by_entity[ent] = await qbo.query_all(
                integ.realm_id, token, ent, f"WHERE TxnDate >= '{start.isoformat()}'")
    else:
        cursor = since if since is not None else integ.last_synced_at
        since = (cursor or now) - dt.timedelta(hours=1)                # -1h slop buffer
        if since.tzinfo is None:
            since = since.replace(tzinfo=dt.timezone.utc)
        cdc_floor = now - dt.timedelta(days=30)                         # CDC window is 30d max
        if since < cdc_floor:                                          # gap too wide -> re-query
            gap_start = since.date()
            for ent in _QBO_TXN_ENTITIES:
                by_entity[ent] = await qbo.query_all(
                    integ.realm_id, token, ent, f"WHERE TxnDate >= '{gap_start.isoformat()}'")
        else:
            data = await qbo.cdc(integ.realm_id, token, ",".join(_QBO_TXN_ENTITIES), since.isoformat())
            by_entity = _cdc_split(data, _QBO_TXN_ENTITIES)

    pairs = _txn_pairs(tenant_id, integ, by_entity)
    rows = [r for r, _lines in pairs]

    for i in range(0, len(rows), 500):
        await s.execute(txn_upsert(rows[i:i + 500]))
    lines = await _write_txn_lines(s, tenant_id, integ, pairs)
    integ.last_synced_at = now
    await s.commit()
    print(f"[qbo_txns] realm={integ.realm_id} mode={'backfill' if have == 0 else 'cdc'} "
          f"rows={len(rows)} lines={lines}", flush=True)
    return len(rows)


def _pl_line_periods() -> list[tuple[dt.date, dt.date]]:
    """The (start, calendar-end) keys to refresh — the dashboard periods plus the month
    before last_month, so the P&L page always has a prior-month comparison."""
    from .metrics import _pl_period
    keys = {_pl_period(p) for p in _QBO_PERIODS}
    lm_start, _ = _pl_period("last_month")
    prev_end = lm_start - dt.timedelta(days=1)
    keys.add((prev_end.replace(day=1), prev_end))
    return sorted(keys)


async def sync_qbo_pl_lines(s: AsyncSession, tenant_id, integ: Integration) -> int:
    """For each dashboard period (plus one prior month), pull the P&L detail tree,
    parse it to account-level lines, and delete+insert that period's PLLine rows
    (labels shift as accounts change, so replace-in-place is simplest and safe)."""
    token = await _valid_access_token(s, integ)
    now = dt.datetime.now(dt.timezone.utc)
    written = 0
    for ps, pe in _pl_line_periods():
        fetch_end = min(pe, dt.date.today())         # actuals through today for the open period
        report = await qbo.profit_and_loss_detail(integ.realm_id, token, ps.isoformat(), fetch_end.isoformat())
        lines = qbo.parse_pl_lines(report)
        await s.execute(delete(PLLine).where(
            PLLine.tenant_id == tenant_id, PLLine.business_id == integ.business_id,
            PLLine.period_start == ps, PLLine.period_end == pe))
        if lines:
            await s.execute(insert(PLLine), [{
                "tenant_id": tenant_id, "business_id": integ.business_id,
                "period_start": ps, "period_end": pe, "section": ln["section"],
                "parent": ln["parent"], "label": _trim(ln["label"], 200),
                "amount": Decimal(str(ln["amount"])), "position": ln["position"],
            } for ln in lines])
        written += len(lines)
    integ.last_synced_at = now
    await s.commit()
    print(f"[qbo_pl_lines] realm={integ.realm_id} periods={len(_pl_line_periods())} "
          f"lines={written}", flush=True)
    return written


async def _books_run(s: AsyncSession, tenant_id, provider: str, thunk) -> None:
    """Run one Books sub-sync inside its own SyncRun, isolating its errors: a Books
    failure is recorded and surfaced but does NOT flip the qbo integration to error
    (the dashboard P&L snapshot from sync_qbo_pl stays healthy on its own SyncRun)."""
    run = SyncRun(tenant_id=tenant_id, provider=provider)
    s.add(run)
    await s.commit()
    started = dt.datetime.utcnow()
    try:
        records = await thunk()
        run.status = "ok"
        run.stats = {"records": records, "seconds": round((dt.datetime.utcnow() - started).total_seconds(), 1)}
    except Exception as e:  # noqa: BLE001 — record + surface, don't abort the pass
        run.status, run.detail = "error", str(e)
        run.stats = {"records": None, "seconds": round((dt.datetime.utcnow() - started).total_seconds(), 1)}
        print(f"[books_sync] {provider} failed: {e}", flush=True)
    run.finished_at = dt.datetime.utcnow()
    await s.commit()


async def run_books_syncs(s: AsyncSession, tenant_id, integ: Integration, since=None) -> None:
    """Worker entry point: run both Books syncs for a connected QBO integration, after
    sync_qbo_pl. `since` is integ.last_synced_at captured BEFORE sync_qbo_pl bumped it,
    so the txn CDC window covers the whole inter-pass gap (SPEC Part 2.5)."""
    await _books_run(s, tenant_id, "qbo_pl_lines", lambda: sync_qbo_pl_lines(s, tenant_id, integ))
    await _books_run(s, tenant_id, "qbo_txns", lambda: sync_qbo_txns(s, tenant_id, integ, since=since))
