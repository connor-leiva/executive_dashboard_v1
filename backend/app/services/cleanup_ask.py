"""Books Cleanup · Ask about this -- a question about one thing on the tab (a transaction, a line of the statement,
what two companies owe each other), answered by Claude from the tab's own figures for it.

The context is the engine's, for the one thing asked about: the same rows the page shows, never the database, and
never another workspace's. Nothing the answer says changes the books; when the question is really "change this",
the answer names the tab's own action for it. The key stays on the server (settings.ANTHROPIC_API_KEY); without
one the tab doesn't offer the button (`enabled`)."""
from __future__ import annotations

import json
import logging

from ..config import settings
from . import books_cleanup as eng

log = logging.getLogger(__name__)
_client_obj = None
MAX_ROWS = 80          # the largest rows of a line or a pair; the rest are counted, not sent

SYSTEM = """You explain a group of companies' cleaned-up books to their owners, who are not accountants. The data \
below is the Books Cleanup tab's own figures for the one thing they are looking at (a transaction, a line of a \
company's P&L, or what two of the companies owe each other), with the books' context.

Answer from that data only. Quote figures exactly as given, in dollars. When the data doesn't say, say so plainly \
rather than guess. Keep it short: two to five sentences, or a short list when that is clearer. Use plain words: no \
debits and credits unless they ask; "intercompany" is what one company owes another; a "correction" is the entry \
that turns QuickBooks' figure into the corrected one.

You can't change anything. When they want something changed, say which of the tab's own actions does it: on a \
transaction's card, Move it to another company, Split it between companies, Personal (not business), Not a real \
charge, Another line, or Another month; the splits of shared staff, software and ads, the partner rates, the \
owner's salary and the owner's charges are on the Decisions page. A question about taxes or the law is one for \
their CPA or attorney: say so."""


def enabled() -> bool:
    return bool(settings.ANTHROPIC_API_KEY)


def _client():
    global _client_obj
    if _client_obj is None:
        from anthropic import AsyncAnthropic
        _client_obj = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client_obj


async def _claude_call(system: str, messages: list[dict]) -> str:
    """The one network call (the tests replace it)."""
    resp = await _client().messages.create(model=settings.ASSISTANT_MODEL, max_tokens=settings.ASSISTANT_MAX_TOKENS,
                                           system=system, thinking={"type": "disabled"}, messages=messages)
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()


_KEEP = ("kind", "key", "month", "date", "payee", "who", "what", "label", "why", "amount", "tx_amount", "brand",
         "brands", "paid_by", "paid_from", "company", "other", "src", "pct", "pool_total", "status", "changed", "note",
         "booked_as", "money_in", "gone", "change", "component", "number")


def _slim(r: dict) -> dict:
    """One row as the model needs it: its words and figures, without the page's plumbing."""
    out = {k: r[k] for k in _KEEP if r.get(k) not in (None, "", [], {})}
    if r.get("tx"):
        out["transaction"] = _slim(r["tx"])
    if r.get("entry"):
        out["old_journal_entry"] = _slim(r["entry"])
    return out


def context_for(bk: eng.Books, about: dict) -> dict:
    """The data for one question: the books' context, and the thing asked about as the tab shows it."""
    d = bk.d
    ctx = {"the_books": {
        "companies": bk.companies, "short_names": d.get("company_short") or {}, "hub": bk.hub,
        "brands_and_their_company": {b: bk.bc[b] for b in bk.brands},
        "period": {k: d["period"][k] for k in ("start", "end")},
        "net_income_corrected": {c: bk.net_income(c) for c in bk.companies},
        "kinds_of_correction": dict(bk.labels)}}
    kind = (about or {}).get("kind")
    if kind == "tx":
        key = str(about.get("key") or "")
        t = next((x for x in bk.transactions() if x["key"] == key), None)
        if t is None:
            raise eng.InputError("That transaction isn't in the file.")
        ctx["transaction"] = {k: v for k, v in t.items() if v not in (None, "", [], {})}
        ctx["transaction"]["in_the_corrected_books"] = [
            {k: l[k] for k in ("company", "brand", "cls", "pool", "item", "line", "month", "amount") if l.get(k) is not None}
            for l in bk.ledger if l["key"] == key]
    elif kind == "line":
        view, line, month = str(about.get("view") or ""), str(about.get("line") or ""), about.get("month")
        try:
            month = int(month) if month not in (None, "") else None
        except (TypeError, ValueError):
            month = 0
        if month is not None and month not in bk.MONTHS:
            raise eng.InputError("That month isn't in the books.")
        st = bk.statement(view, month)
        row = next((r for s in st["sections"] for r in s["lines"] if r["line"] == line), None)
        if row is None:
            raise eng.InputError("That line isn't on the statement.")
        det = bk.line_detail(view, line, month)
        rows = sorted(det["rows"], key=lambda r: -abs(r.get("amount") or 0))
        ctx["line"] = {"company_or_view": view, "line": line, "month": month or "the year so far", "figures": row,
                       "how_quickbooks_becomes_corrected": det.get("why"),
                       "rows_largest_first": [_slim(r) for r in rows[:MAX_ROWS]], "rows_in_all": len(rows)}
    elif kind == "pair":
        dt = bk.interco_detail(str(about.get("pair") or ""))
        rows = sorted(dt["rows"], key=lambda r: -abs(r["amount"]))
        ctx["pair"] = {k: dt[k] for k in ("pair", "reads", "booked", "corrections", "corrected", "parts")}
        ctx["pair"].update({"items_largest_first": [_slim(r) for r in rows[:MAX_ROWS]], "items_in_all": len(rows)})
    else:
        raise eng.InputError("Ask about a transaction, a line of the statement or a pair of companies.")
    return ctx


async def ask(bk: eng.Books, about: dict, question: str, history: list[dict] | None = None) -> dict:
    q = (question or "").strip()
    if not q:
        raise eng.InputError("Ask a question.")
    system = SYSTEM + "\n\nThe data (JSON):\n" + json.dumps(context_for(bk, about), separators=(",", ":"), default=str)
    messages = []
    for turn in (history or [])[-6:]:
        role, content = turn.get("role"), str(turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content[:3000]})
    if messages and messages[0]["role"] != "user":
        messages = messages[1:]
    if messages and messages[-1]["role"] == "user":          # an earlier question that got no answer
        messages.pop()
    messages.append({"role": "user", "content": q[:1000]})
    text = await _claude_call(system, messages)
    log.info("cleanup ask: about=%s question=%d chars answer=%d chars", (about or {}).get("kind"), len(q), len(text))
    return {"answer": text or "I couldn't put an answer together. Try asking it another way.",
            "model": settings.ASSISTANT_MODEL}
