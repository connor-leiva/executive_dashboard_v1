"""Books Cleanup -- an audited year's books, recomputed live from the decisions made on the tab.

WHAT THIS IS. A one-off, for a workspace whose year had to be taken apart transaction by
transaction before its QuickBooks could be trusted. The audit itself ran outside the app; its
result arrives as ONE uploaded file (schema `axcion.books_cleanup/1`):

  * every reviewed ledger line -- which brand it belongs to, which shared pool, which P&L line,
    and what QuickBooks booked it as;
  * QuickBooks' own P&L, by company, line and month, and its trial balances on the last day;
  * the review's part of the correcting journal entries, already worked out line by line;
  * the dues and event timing, and the words (open items, findings, questions).

What the DECISIONS move is computed here, from that file plus what people set on the tab: how each
shared cost splits across the brands, the partners' rates and the owner's salary ahead of one of
them, whether each of the owner's charges was hers or the business's, investment or loan. That is
the spreadsheet's arithmetic -- its corrected P&L, its correcting entries and its balance sheets --
and it was checked against the workbook it replaces, cell for cell, before this tab existed.

NOTHING IN THIS FILE NAMES A COMPANY, A PERSON OR AN ACCOUNT. They arrive in the file. The shape is
fixed -- one company pays the shared costs (the hub) and the others carry shares of them; one has a
membership dues schedule; partners earn a rate of a company's operating income -- because that is
the shape this audit had. A different workspace's cleanup would arrive as a different file.

SIGNS, once: P&L amounts are P&L-signed (revenue and costs both positive, as a statement reads).
Postings to accounts are debit-positive, as a journal entry reads. Month 0 is January 1 (the
opening entry); months 1..N are the year's.
"""
from __future__ import annotations

import json
from collections import defaultdict

SCHEMA = "axcion.books_cleanup/1"

# What each part of a correcting entry is, in words. The keys are what the file and this engine tag
# each posting with; the file may relabel any of them (`component_labels`).
COMPONENTS = {
    "moved": "Moved to the company it belongs to",
    "recat": "Recategorized within the company",
    "never": "Entries that never happened",
    "old": "Old journal entries undone",
    "outside": "Paid by another company",
    "split": "Shared costs split (your splits)",
    "dues": "Membership dues, earned over each term",
    "timing": "Counted in the month it belongs to",
    "partner": "Partner profit shares",
    "opening": "January 1 position (prior year)",
    "own": "The owner's own charges",
    "chargeline": "The owner's charges, on another line",
    "holdco": "Investment or loan",
    "yours": "Your changes to single transactions",
}
HOLDCO_CHOICES = ("Investment (equity)", "Loan")
# what the tab can do to one transaction (inputs["tx"][key]), and why one can come out of the books
TX_DO = ("move", "split", "personal", "remove", "line", "month")
REMOVE_WHY = ("paid_twice", "entered_twice", "never")
BC_BASES = ("rebuilt", "old_books", "received")


def _r(x: float) -> float:
    return round(x + 0.0, 2)


def _parts(amount: float, parts: dict) -> dict:
    """An amount split by shares, to the cent, the parts adding back to the whole (the rounding on the largest)."""
    out = {b: _r(amount * f) for b, f in parts.items()}
    big = max(out, key=lambda b: abs(out[b]))
    out[big] = _r(out[big] + amount - sum(out.values()))
    return out


class InputError(ValueError):
    """A decision that the file cannot hold (an unknown person, a split above 100%, ...)."""


# ── inputs: the file's starting values, overlaid with what people saved on the tab ───────────────

def merged_inputs(data: dict, saved: dict | None) -> dict:
    """The decisions in force: the file's values, with every saved value laid over the top.

    Merged key by key rather than replaced section by section, so a person who changed one month of
    one person's split has changed exactly that, and a new upload that adds a person or an item
    brings that one in at the file's value without touching anybody's edits.
    """
    base = data.get("inputs") or {}
    out = {
        "ads": dict(base.get("ads") or {}),
        "rates": dict(base.get("rates") or {}),
        "salary": base.get("salary") or 0.0,
        "holdco": base.get("holdco") or HOLDCO_CHOICES[0],
        "bc_basis": base.get("bc_basis") or "rebuilt",
        "staff": {p: {m: dict(b) for m, b in months.items()} for p, months in (base.get("staff") or {}).items()},
        "items": {k: {i: dict(b) for i, b in rows.items()} for k, rows in (base.get("items") or {}).items()},
        "charges": {ref: dict(v) for ref, v in (base.get("charges") or {}).items()},
        "answers": dict(base.get("answers") or {}),
        "posted": {c: dict(v) for c, v in (base.get("posted") or {}).items()},
        "checklist": {},
        "notes": dict(base.get("notes") or {}),
        # each transaction the tab moved, split, made personal, took out, or put on another line or month
        "tx": {k: dict(v) for k, v in (base.get("tx") or {}).items() if v},
        # the forward view's assumptions (Forecast) and the projected company's monthly plan (Budget)
        "forward": dict(base.get("forward") or {}),
        "budget": dict(base.get("budget") or {}),
    }
    for k, v in (saved or {}).items():
        if k in ("salary", "holdco", "bc_basis"):
            out[k] = v
        elif k in ("ads", "rates", "answers", "notes", "checklist"):
            out[k].update(v or {})
        elif k == "staff":
            for p, months in (v or {}).items():
                for m, b in months.items():
                    out["staff"].setdefault(p, {}).setdefault(m, {}).update(b)
        elif k == "items":
            for kind, rows in (v or {}).items():
                for i, b in rows.items():
                    out["items"].setdefault(kind, {}).setdefault(i, {}).update(b)
        elif k == "charges":
            for ref, c in (v or {}).items():
                out["charges"].setdefault(ref, {}).update(c)
        elif k == "posted":
            for c, refs in (v or {}).items():
                out["posted"].setdefault(c, {}).update(refs)
        elif k in ("forward", "budget"):
            out[k].update(v or {})
        elif k == "tx":
            for key, ch in (v or {}).items():   # a change replaces the one before it; null undoes it
                if ch:
                    out["tx"][key] = dict(ch)
                else:
                    out["tx"].pop(key, None)
    return out


def validate_change(data: dict, path: list, value):
    """Check one change against what the file can hold, and return the value to store.

    Every decision has a fixed address: ["staff", person, month, brand], ["items", "software", item,
    brand], ["charges", ref, "treat"], and so on. An address the file does not know is refused rather
    than stored, because a split saved against a person who is not in the books would be a number
    that changes nothing and is believed by whoever reads it next.
    """
    if not isinstance(path, list) or not path or not all(isinstance(p, (str, int)) for p in path):
        raise InputError("A change needs a path.")
    path = [str(p) for p in path]
    head = path[0]
    brands = set(data["brands"])
    months = {str(x["m"]) for x in data["period"]["months"]}

    def pct(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise InputError("A share is a number between 0 and 1.")
        if not 0 <= f <= 1:
            raise InputError("A share is a number between 0 and 1.")
        return f

    def text(v, n):
        if v is None:
            return ""
        if not isinstance(v, str) or len(v) > n:
            raise InputError(f"Text up to {n} characters.")
        return v.strip()

    if head == "ads" and len(path) == 2 and path[1] in brands | {"billback"}:
        return pct(value)
    if head == "rates" and len(path) == 2 and path[1] in {p["company"] for p in data["partners"]}:
        return pct(value)
    if head == "salary" and len(path) == 1:
        try:
            f = float(value)
        except (TypeError, ValueError):
            raise InputError("The salary is a yearly amount in dollars.")
        if not 0 <= f <= 10_000_000:
            raise InputError("The salary is a yearly amount in dollars.")
        return f
    if head == "holdco" and len(path) == 1 and value in HOLDCO_CHOICES:
        return value
    if head == "bc_basis" and len(path) == 1 and value in BC_BASES:
        return value
    if head == "staff" and len(path) == 4:
        people = {p["name"]: p for p in data["people"]}
        p = people.get(path[1])
        if p and not p.get("follows_wages") and path[2] in months and path[3] in brands:
            return pct(value)
    if head == "items" and len(path) == 4 and path[1] in (data.get("items") or {}):
        names = {r["name"] for r in data["items"][path[1]]["rows"]}
        if path[2] in names and path[3] in brands:
            return pct(value)
    if head == "charges" and len(path) == 3:
        refs = {c["ref"] for c in data["charges"]}
        if path[1] in refs and path[2] == "treat" and value in ("own", "business"):
            return value
        if path[1] in refs and path[2] == "line" and value in charge_lines(data):
            return value
    if head == "answers" and len(path) == 2:
        refs = {x["ref"] for x in data.get("open_items", []) + data.get("answered", [])}
        if path[1] in refs:
            return text(value, 4000)
    if head == "posted" and len(path) == 3 and path[1] in data["companies"] and path[2] in {"0"} | months:
        return text(value, 64)
    if head == "checklist" and len(path) == 2 and path[1] in {c["id"] for c in data.get("checklist", [])}:
        if not isinstance(value, dict):
            raise InputError("A checklist step is {done, note}.")
        return {"done": bool(value.get("done")), "note": text(value.get("note"), 2000)}
    if head == "tx" and len(path) == 2:
        return _tx_change(data, path[1], value, pct, text)
    if head in ("forward", "budget") and len(path) == 2 and data.get("forward"):
        return _forward_change(data, head, path[1], value, text)
    raise InputError(f"Nothing to change at {'/'.join(path)}.")


def _forward_change(data: dict, head: str, field: str, value, text):
    """One of the forward view's assumptions (Forecast) or one line of the projected company's monthly plan
    (Budget). They change projections, never the books."""
    f = data["forward"]
    months = {s["key"] for s in f["schedule"] if s["key"] <= f["horizon"]}

    def dollars(v, top=10_000_000):
        try:
            x = float(v)
        except (TypeError, ValueError):
            raise InputError("An amount in dollars.")
        if not 0 <= x <= top or x != x:
            raise InputError("An amount in dollars, from zero up.")
        return round(x, 2)

    def month(v):
        if v not in months:
            raise InputError("Pick a month in the forecast.")
        return v

    def when(v, label_default=""):
        if not isinstance(v, dict):
            raise InputError("A label, a month, an amount, and on or off.")
        return {"label": text(v.get("label") or label_default, 80), "key": month(v.get("key")),
                "amount": dollars(v.get("amount")), "on": bool(v.get("on"))}

    if head == "budget":
        if field not in data["lines"]["costs"] or field in (f.get("plan_from") or {}) or field.startswith(("Uncategorized", "Not yet")):
            raise InputError("That line isn't one the budget plans.")
        return dollars(value)
    if field == "commission_pct":
        return dollars(value, 100)
    if field == "partner_pay":
        return dollars(value)
    if field == "partner_end":
        return month(value)
    if field == "retreats":
        if not isinstance(value, list) or len(value) > 8:
            raise InputError("Up to eight retreats.")
        return [when(r, "A retreat") for r in value]
    if field == "repay":
        return when(value, (f["assumptions"].get("repay") or {}).get("label") or "A repayment")
    raise InputError(f"Nothing to change at {head}/{field}.")


def _tx_change(data: dict, key: str, value, pct, text):
    """One transaction's change, checked against the file: {do, why} and what the change needs. None undoes it.

    A transaction is changed whole -- every one of its income and cost lines -- because a charge belongs to one
    company (or is split between them) as a charge, not line by line."""
    if key not in (data.get("transactions") or {}):
        raise InputError("There's no such transaction in the file.")
    if value is None:
        return None                       # undone: back to where the review put it
    if not isinstance(value, dict):
        raise InputError("A change to a transaction says what to do: move, split, personal, remove, line or month.")
    lines = [l for l in data["ledger"] if l["key"] == key]
    pl = [l for l in lines if l["cls"] in ("revenue", "cost")]
    if not pl:
        raise InputError("That transaction isn't income or a cost, so it isn't on any company's results to change.")
    money_in = all(l["cls"] == "revenue" for l in pl)
    brands = set(data["brands"])
    do = value.get("do")
    out = {"do": do, "why": text(value.get("why"), 500)}
    if do == "move":
        to = value.get("to")
        if to not in brands and not (to == "billback" and data.get("billback")):
            raise InputError("Move it to one of the companies, or bill it back.")
        out["to"] = to
    elif do == "split":
        parts = value.get("parts")
        if not isinstance(parts, dict):
            raise InputError("A split is each company's share.")
        clean = {}
        for b, f in parts.items():
            if b not in brands:
                raise InputError(f"{b!r} isn't one of the companies.")
            f = pct(f)
            if f > 0:
                clean[b] = f
        if len(clean) < 2 or abs(sum(clean.values()) - 1) > 1e-4:
            raise InputError("A split is two or more companies, adding up to 100%.")
        out["parts"] = clean
    elif do == "personal":
        if money_in:
            raise InputError("Money coming in isn't anybody's personal spending.")
        out["who"] = text(value.get("who"), 80) or "owner"
    elif do == "remove":
        if len(pl) != len(lines):
            raise InputError("Part of this transaction isn't income or a cost: it can't come out whole here.")
        reason = value.get("reason")
        if reason not in REMOVE_WHY:
            raise InputError("It was paid twice, entered twice, or never happened.")
        if reason == "paid_twice" and money_in:
            raise InputError("Money that came in twice is a refund to make, not a payment owed back.")
        if reason != "paid_twice" and data["transactions"][key].get("cleared"):
            raise InputError("The bank shows it, so it happened. If it was paid twice, the money is owed back.")
        out["reason"] = reason
    elif do == "line":
        if any(l["pool"] for l in pl):
            raise InputError("A shared cost's line is its pool's: change the item's split on Decisions.")
        if not money_in and any(l["cls"] == "revenue" for l in pl):
            raise InputError("Money in and money out together: there's no one line for both.")
        side = data["lines"]["revenue"] if money_in else data["lines"]["costs"]
        line = value.get("line")
        if line not in side or str(line).startswith(("Uncategorized", "Not yet")):
            raise InputError("Pick a line on its own side: income for money in, a cost for money out.")
        out["line"] = line
    elif do == "month":
        m = value.get("month")
        if isinstance(m, bool) or not isinstance(m, int) or m not in {x["m"] for x in data["period"]["months"]}:
            raise InputError("Pick a month the books cover.")
        if not all((data["accounts"].get(k) or {}) for k in ("prepaid", "accrued_liab")):
            raise InputError("This file has no prepaid or accrued accounts to carry it to another month: load a newer one.")
        out["month"] = m
    else:
        raise InputError("Move, split, personal, remove, line or month.")
    return out


def describe_change(data: dict, key: str, ch: dict | None) -> str:
    """One transaction's change in words, for the audit log and the Decisions list: "Minky Couture, Mar 24,
    $5,598.47: moved to The Forum"."""
    t = (data.get("transactions") or {}).get(key) or {}
    name = t.get("payee") or t.get("who") or "A transaction"
    when = t.get("date") or ""
    if when:
        when = f", {['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][int(when[5:7]) - 1]} {int(when[8:10])}"
    amt = f", ${abs(float(t['amount'])):,.2f}" if t.get("amount") is not None else ""
    if not ch:
        return f"{name}{when}{amt}: back to where the review put it"
    do = ch.get("do")
    short = data.get("company_short") or {}
    co = lambda b: short.get(b, b)
    if do == "move":
        what = (f"billed back to {(data.get('billback') or {}).get('name', 'the neighbour')}" if ch.get("to") == "billback"
                else f"moved to {co(ch.get('to'))}")
    elif do == "split":
        what = "split " + ", ".join(f"{co(b)} {f * 100:.1f}%".replace(".0%", "%") for b, f in (ch.get("parts") or {}).items())
    elif do == "personal":
        who = ch.get("who") or "owner"
        what = (f"{data.get('salary_owner') or 'the owner'}'s personal (an owner's draw)" if who == "owner"
                else f"{who}'s personal (owed back)")
    elif do == "remove":
        what = {"paid_twice": "paid twice: the second payment is owed back", "entered_twice": "entered twice: taken out",
                "never": "never happened: taken out"}.get(ch.get("reason"), "taken out")
    elif do == "line":
        what = f"filed to {ch.get('line')}"
    elif do == "month":
        labels = {x["m"]: x["label"] for x in data["period"]["months"]}
        what = f"counted in {labels.get(ch.get('month'), ch.get('month'))}"
    else:
        what = "changed"
    return f"{name}{when}{amt}: {what}"


def set_path(saved: dict, path: list, value) -> None:
    """Write one validated change into the saved-inputs document (which only holds what changed)."""
    path = [str(p) for p in path]
    if len(path) == 1:
        saved[path[0]] = value
        return
    node = saved.setdefault(path[0], {})
    for p in path[1:-1]:
        node = node.setdefault(p, {})
    node[path[-1]] = value


def charge_lines(data: dict) -> list[str]:
    return [l for l in data["lines"]["costs"] if not l.startswith(("Uncategorized", "Not yet"))]


# ── the engine ───────────────────────────────────────────────────────────────────────────────────

class Books:
    """Everything the tab shows, computed once for one file and one set of decisions."""

    def __init__(self, data: dict, inputs: dict):
        if data.get("schema") != SCHEMA:
            raise InputError("This isn't a Books Cleanup file.")
        self.d = d = data
        self.inp = inputs
        self.companies: list[str] = d["companies"]
        self.brands: list[str] = d["brands"]
        self.bc: dict = d["brand_company"]
        self.hub: str = d["hub"]
        self.REVENUE: list[str] = d["lines"]["revenue"]
        self.COSTS: list[str] = d["lines"]["costs"]
        self.BELOW: list[str] = d["lines"]["below"]
        self.REV_SIDE = set(self.REVENUE) | {"Other income"}
        self.MONTHS = [x["m"] for x in d["period"]["months"]]
        self.acc = d["accounts"]
        self.labels = {**COMPONENTS, **(d.get("component_labels") or {})}
        self.dues = d["dues"]
        self.tx = d["transactions"]
        self.changes = inputs.get("tx") or {}
        self.ledger = self._effective()
        self._pools()
        self._corrected()
        self._postings()

    # ---- shared pools, and each decision's split of them
    def _pools(self):
        self.pool_m = defaultdict(float)
        self.staff_cost = defaultdict(float)
        self.item_m = defaultdict(float)
        self.acct_of_pool_line = defaultdict(float)
        self.item_kind = {}
        for kind, spec in (self.d.get("items") or {}).items():
            self.item_kind[spec["pool"]] = kind
        for l in self.ledger:
            if l["cls"] != "cost" or not l["pool"]:
                continue
            m = l["month"]
            self.pool_m[(l["pool"], m)] += l["amount"]
            if l["pool"] == "Staff and contractors":
                self.staff_cost[(l["item"], m)] += l["amount"]
            if l["pool"] in self.item_kind:
                self.item_m[(l["pool"], l["item"] or "(unnamed)", m)] += l["amount"]
            if l["pool"] in ("Advertising", "Payment processing"):
                self.acct_of_pool_line[(l["pool"], l["corr_account"], m)] += l["amount"]
        self.people = {p["name"]: p for p in self.d["people"]}
        self.w2 = [p["name"] for p in self.d["people"] if p.get("w2")]
        self.ads = {b: float(self.inp["ads"].get(b) or 0) for b in self.brands + ["billback"]}
        self.fees = {b: float(self.d["fees"].get(b) or 0) for b in self.brands}
        self.item_line = {}
        for kind, spec in (self.d.get("items") or {}).items():
            for row in spec["rows"]:
                self.item_line[(spec["pool"], row["name"])] = row["line"]
        self._split_cache = {}

    def split(self, person: str, m: int) -> tuple:
        """One person's share for each brand in one month (in brand order)."""
        key = (person, m)
        if key in self._split_cache:
            return self._split_cache[key]
        p = self.people.get(person) or {}
        if p.get("follows_wages"):
            den = sum(self.staff_cost.get((w, m), 0) for w in self.w2)
            out = (tuple(sum(self.staff_cost.get((w, m), 0) * self.split(w, m)[k] for w in self.w2) / den
                         for k in range(len(self.brands))) if den
                   else tuple(1.0 if b == self.d["hub_brand"] else 0.0 for b in self.brands))
        else:
            s = (self.inp["staff"].get(person) or {}).get(str(m)) or {}
            out = tuple(float(s.get(b) or 0) for b in self.brands)
        self._split_cache[key] = out
        return out

    def item_split(self, pool: str, item: str) -> dict:
        kind = self.item_kind[pool]
        s = (self.inp["items"].get(kind) or {}).get(item) or {}
        return {b: float(s.get(b) or 0) for b in self.brands}

    def charge(self, c: dict) -> tuple[str, str]:
        x = self.inp["charges"].get(c["ref"]) or {}
        return x.get("treat") or "own", x.get("line") or c["booked_line"]

    def timing_rows(self):
        basis = self.inp.get("bc_basis") or "rebuilt"
        for t in self.d["timing"]:
            if t["basis"] is None or t["basis"] == basis:
                yield t

    # ---- the tab's changes to single transactions
    def _effective(self) -> list:
        """The reviewed ledger with the tab's changes to single transactions laid over it -- a moved charge on
        its new company, a split one on each, a personal or removed one on nobody -- and (in `tx_post`) the
        postings that put each one there.

        The postings are the review's own rule (engine/corrections.py) run again: the placement the review gave
        a line comes back out, the new one goes in, each through intercompany when another company paid it. Every
        placement leaves the paying company where QuickBooks has it (one debit against the payment's credit) and
        every other company even, so a change can't unbalance an entry; and the partner shares, the pools' splits
        and the brand views all read the changed ledger, so they follow on their own."""
        self.tx_post = defaultdict(float)          # (company, account, month) -> debit-positive
        self.extra_types = {}                      # accounts a change creates: {account: [class, type]}
        if not self.changes:
            return self.d["ledger"]
        out = []
        for l in self.d["ledger"]:
            ch = self.changes.get(l["key"])
            out.extend(self._change(l, ch) if ch and l["cls"] in ("revenue", "cost") else [l])
        for key, ch in self.changes.items():       # one that never happened takes its bank lines back out too
            if ch.get("do") == "remove" and ch.get("reason") in ("entered_twice", "never"):
                t = self.tx.get(key) or {}
                comp = t.get("company") or next((l["company"] for l in self.d["ledger"] if l["key"] == key), None)
                for c in t.get("cash") or []:
                    self._post(comp, c["account"], c["month"], -c["amount"])
        return out

    def _change(self, l: dict, ch: dict) -> list:
        """One income or cost line under one change: the lines that replace it (none, if it leaves every
        company's results), with its postings added to `tx_post`."""
        c0, cls, a, m = l["company"], l["cls"], l["amount"], l["month"]
        old = self.bc.get(l["brand"]) or c0                    # a shared cost sits on the company that paid it
        acct = l.get("corr_account") or self.acc["main"][f"{old}|{l['line']}"]
        dr = a if cls == "cost" else -a
        bare = {**l, "pool": None, "item": None}               # out of its pool: on one company, not split
        do = ch.get("do")
        if do == "move" and ch.get("to") == "billback":        # billed back to the neighbour: owed, not a cost
            self._place(c0, cls, old, acct, m, -a)
            self._post(c0, self._owed_by(c0, self.d["billback"]["name"], self.d["billback"]["account"]), m, dr)
            return []
        if do == "move":
            new = self.bc[ch["to"]]
            if new != old:
                self._place(c0, cls, old, acct, m, -a)
                self._place(c0, cls, new, self.acc["main"][f"{new}|{l['line']}"], m, a)
            return [{**bare, "brand": ch["to"]}]
        if do == "split":
            self._place(c0, cls, old, acct, m, -a)
            out = []
            for b, part in _parts(a, ch["parts"]).items():
                comp = self.bc[b]
                self._place(c0, cls, comp, acct if comp == old else self.acc["main"][f"{comp}|{l['line']}"], m, part)
                out.append({**bare, "brand": b, "amount": part})
            return out
        if do == "personal":                                    # the owner's draw, or owed back by whoever it was
            self._place(c0, cls, old, acct, m, -a)
            who = ch.get("who") or "owner"
            if who == "owner":
                draw = (self.acc["owner"] if c0 == self.hub
                        else f"{self.d.get('salary_owner') or 'The owner'}'s draws")
                if draw != self.acc["owner"]:
                    self.extra_types.setdefault(draw, ["Equity", "Equity"])
                self._post(c0, draw, m, dr)
            else:
                self._post(c0, self._owed_by(c0, who), m, dr)
            return []
        if do == "remove":                                      # paid twice: the second payment is owed back
            self._place(c0, cls, old, acct, m, -a)
            if ch.get("reason") == "paid_twice":
                t = self.tx.get(l["key"]) or {}
                self._post(c0, self._owed_by(c0, t.get("payee") or t.get("who") or "the payee"), m, dr)
            return []
        if do == "line":
            self._place(c0, cls, old, acct, m, -a)
            self._place(c0, cls, old, self.acc["main"][f"{old}|{ch['line']}"], m, a)
            return [{**l, "line": ch["line"]}]
        if do == "month":                                       # paid one month, counted in another
            m2 = ch["month"]
            if m2 != m:
                side = self._side(old, cls, later=m2 > m)
                self._post(old, acct, m, -dr)
                self._post(old, side, m, dr)
                self._post(old, acct, m2, dr)
                self._post(old, side, m2, -dr)
            return [{**l, "month": m2}]
        return [l]

    def _place(self, c0: str, cls: str, comp: str, acct: str, m: int, amount: float) -> None:
        """A line's corrected placement on `acct` in `comp` (P&L-signed `amount`; negative takes it back out).
        The paying company's debit always lands; another company carries it and owes the payer for it."""
        dr = amount if cls == "cost" else -amount
        if comp == c0:
            self._post(c0, acct, m, dr)
        else:
            self._post(c0, self._ic(c0, comp, cls), m, dr)
            self._post(comp, acct, m, dr)
            self._post(comp, self._ic(comp, c0, cls), m, -dr)

    def _post(self, comp: str, acct: str, m: int, v: float) -> None:
        self.tx_post[(comp, acct, m)] += v

    def _ic(self, a: str, b: str, cls: str) -> str:
        """What `a` books against `b`: money in between two companies can sit on its own account."""
        if cls == "revenue":
            own = (self.acc.get("ic_revenue") or {}).get(f"{a}|{b}")
            if own:
                return own
        return self.acc["ic"][f"{a}|{b}"]

    def _owed_by(self, comp: str, who: str, existing: str | None = None) -> str:
        """The account for money somebody owes `comp` back: the file's own where it has one, else a new
        'Due from <who>' (an asset, which the balance sheet says to set up)."""
        if existing and comp == self.hub:
            return existing
        acct = f"Due from {who}"
        self.extra_types.setdefault(acct, ["Asset", "Other Current Asset"])
        return acct

    def _side(self, comp: str, cls: str, later: bool) -> str:
        """Where a line waits between the month it was paid and the month it counts in: a cost counted later is
        prepaid, earlier is accrued; money in counted later is deferred, earlier is accrued revenue."""
        key = ("prepaid" if later else "accrued_liab") if cls == "cost" else ("deferred" if later else "accrued_revenue")
        return self.acc[key][comp]

    # ---- the corrected P&L, by company, line and month (and by brand, for the brand views)
    def _corrected(self):
        corr = defaultdict(float)            # (company, line, m)
        by_brand = defaultdict(float)        # (brand, line, m): no timing, no partner share
        self.dues_dep = defaultdict(float)
        dc, dl = self.dues["company"], self.dues["line"]
        for l in self.ledger:
            if l["cls"] not in ("revenue", "cost") or l["brand"] not in self.bc:
                continue
            comp, line, m = self.bc[l["brand"]], l["line"], l["month"]
            if line.startswith("Uncategorized"):
                continue
            by_brand[(l["brand"], line, m)] += l["amount"]
            if comp == dc and line == dl:
                self.dues_dep[m] += l["amount"]
                continue
            corr[(comp, line, m)] += l["amount"]
        for m in self.MONTHS:
            corr[(dc, dl, m)] += float(self.dues["earned"].get(str(m), 0))
            for k, b in enumerate(self.brands):
                comp = self.bc[b]
                for pool, pct in (("Advertising", self.ads), ("Payment processing", self.fees)):
                    v = self.pool_m[(pool, m)] * pct[b]
                    line = self.d["pools"][pool]["line"]
                    corr[(comp, line, m)] += v
                    by_brand[(b, line, m)] += v
                v = sum(c * self.split(p, mm)[k] for (p, mm), c in self.staff_cost.items() if mm == m)
                corr[(comp, "Staff and contractors", m)] += v
                by_brand[(b, "Staff and contractors", m)] += v
        for (pool, item, m), v in self.item_m.items():
            s = self.item_split(pool, item)
            line = self.d["pools"][pool]["line"] or self.item_line[(pool, item)]
            for b in self.brands:
                corr[(self.bc[b], line, m)] += v * s[b]
                by_brand[(b, line, m)] += v * s[b]
        cb = self.d.get("charges_brand")
        for c in self.d["charges"]:
            treat, line = self.charge(c)
            if treat == "business":
                corr[(self.hub, line, c["month"])] += c["amount"]
                by_brand[(cb, line, c["month"])] += c["amount"]
        for t in self.timing_rows():
            corr[(t["company"], t["line"], t["month"])] += t["amount"]
        # partners: each month adds the rate times the growth in the year's operating income (after
        # the owner's salary, for a partner whose agreement puts it first), so a loss month takes
        # back only what was accrued before it and a year with no profit accrues nothing.
        self.share = {}
        frac = self.d["period"]["salary_fraction"]
        for p in self.d["partners"]:
            comp = p["company"]
            rate = float(self.inp["rates"].get(comp) or 0)
            sal = float(self.inp.get("salary") or 0) if p.get("salary") else 0.0
            cum_prev = base_prev = 0.0
            for m in self.MONTHS:
                noi = (sum(corr[(comp, x, m)] for x in self.REVENUE)
                       - sum(corr[(comp, x, m)] for x in self.COSTS))
                cum = cum_prev + noi
                base = max(0.0, cum - sal * float(frac[str(m)]))
                self.share[(comp, m)] = rate * (base - base_prev)
                corr[(comp, "Partner profit shares", m)] += self.share[(comp, m)]
                cum_prev, base_prev = cum, base
        self.corr, self.by_brand = corr, by_brand
        self.qbo = defaultdict(float)
        for comp, info in self.d["qbo"].items():
            for m, lines in (info.get("by_month") or {}).items():
                for line, v in lines.items():
                    self.qbo[(comp, line, int(m))] += v

    # ---- the correcting entries, by company, account, part and month (debit-positive)
    def _postings(self):
        post = defaultdict(float)            # (company, account, component, m)
        acc, hub = self.acc, self.hub
        main = acc["main"]
        ic = lambda a, b: acc["ic"][f"{a}|{b}"]
        for r in self.d["review"]:
            for m, v in enumerate(r["months"]):
                if v:
                    post[(r["company"], r["account"], r["component"], m)] += v
        outside = [b for b in self.brands if self.bc[b] != hub]       # the brands whose company isn't the hub
        for m in self.MONTHS:
            for pool, pct, billback in (("Advertising", self.ads, True), ("Payment processing", self.fees, False)):
                moved = sum(pct[b] for b in outside) + (self.ads["billback"] if billback else 0)
                for (p, a, mm), amt in self.acct_of_pool_line.items():
                    if p == pool and mm == m:
                        post[(hub, a, "split", m)] -= amt * moved
                pm = self.pool_m[(pool, m)]
                for b in outside:
                    comp = self.bc[b]
                    a = self.d["pools"][pool]["share_account"] or main[f"{comp}|{self.d['pools'][pool]['line']}"]
                    post[(comp, a, "split", m)] += pm * pct[b]
                    post[(hub, ic(hub, comp), "split", m)] += pm * pct[b]
                    post[(comp, ic(comp, hub), "split", m)] -= pm * pct[b]
                if billback:
                    post[(hub, self.d["billback"]["account"], "split", m)] += pm * self.ads["billback"]
            for (p, mm), v in self.staff_cost.items():
                if mm != m:
                    continue
                sp = self.split(p, m)
                acct = self.people[p]["account"]
                for k, b in enumerate(self.brands):
                    if b not in outside:
                        continue
                    comp = self.bc[b]
                    post[(hub, acct, "split", m)] -= v * sp[k]
                    post[(comp, self.d["pools"]["Staff and contractors"]["share_account"], "split", m)] += v * sp[k]
                    post[(hub, ic(hub, comp), "split", m)] += v * sp[k]
                    post[(comp, ic(comp, hub), "split", m)] -= v * sp[k]
            # membership dues: earned, not deposited
            dc, dl = self.dues["company"], self.dues["line"]
            acc_prev, acc_now = self.dues["accrued"][m - 1], self.dues["accrued"][m]
            adj = float(self.dues["earned"].get(str(m), 0)) - self.dues_dep[m]
            post[(dc, main[f"{dc}|{dl}"], "dues", m)] -= adj
            post[(dc, acc["accrued_revenue"][dc], "dues", m)] += acc_now - acc_prev
            post[(dc, acc["deferred"][dc], "dues", m)] += adj - (acc_now - acc_prev)
            for p in self.d["partners"]:
                comp, s = p["company"], self.share[(p["company"], m)]
                post[(comp, main[f"{comp}|Partner profit shares"], "partner", m)] += s
                post[(comp, p.get("payable_account") or p.get("advance_account"), "partner", m)] -= s
        for (pool, item, m), v in self.item_m.items():
            s = self.item_split(pool, item)
            acct = self.d["items"][self.item_kind[pool]]["rows"]
            row = next(r for r in acct if r["name"] == item)
            for b in outside:
                comp = self.bc[b]
                post[(hub, row["account"], "split", m)] -= v * s[b]
                a = (self.d["pools"][pool]["share_account"] if self.d["pools"][pool]["share_account"]
                     else main[f"{comp}|{self.item_line[(pool, item)]}"])
                post[(comp, a, "split", m)] += v * s[b]
                post[(hub, ic(hub, comp), "split", m)] += v * s[b]
                post[(comp, ic(comp, hub), "split", m)] -= v * s[b]
        mirror_to = next(c for c in self.companies if c != hub and c == self.dues["company"])
        for t in self.timing_rows():
            d = t["amount"] if t["line"] in self.COSTS else -t["amount"]
            post[(t["company"], t["account"], "timing", t["month"])] += d
            post[(t["company"], t["side"], "timing", t["month"])] -= d
            if t["mirror"]:
                post[(hub, ic(hub, mirror_to), "timing", t["month"])] += d
                post[(hub, acc["retained_earnings"], "timing", t["month"])] -= d
        for c in self.d["charges"]:
            treat, line = self.charge(c)
            if treat == "own":
                post[(hub, c["account"], "own", c["month"])] -= c["amount"]
                post[(hub, acc["owner"], "own", c["month"])] += c["amount"]
            elif line != c["booked_line"]:
                post[(hub, c["account"], "chargeline", c["month"])] -= c["amount"]
                post[(hub, main[f"{hub}|{line}"], "chargeline", c["month"])] += c["amount"]
        loan = self.inp.get("holdco") == HOLDCO_CHOICES[1]
        hc = acc["holdco"]
        for x in self.d["holdco"]:
            if x["account"] == hc["invest"]:
                post[(x["company"], x["account"], "holdco", x["month"])] -= x["amount"]
                post[(x["company"], hc["loan"] if loan else hc["dist"], "holdco", x["month"])] += x["amount"]
            elif loan:
                post[(x["company"], x["account"], "holdco", x["month"])] -= x["amount"]
                post[(x["company"], hc["loan"], "holdco", x["month"])] += x["amount"]
        # January 1: the dues the hub collected in the prior year for this year's service
        dc = self.dues["company"]
        d0, a0 = self.dues["deferred"][0], self.dues["accrued"][0]
        net0 = d0 - float(self.dues.get("in_transit_open") or 0) - a0
        post[(dc, acc["deferred"][dc], "opening", 0)] -= d0 - float(self.dues.get("in_transit_open") or 0)
        post[(dc, acc["accrued_revenue"][dc], "opening", 0)] += a0
        post[(dc, acc["transfer"], "opening", 0)] += net0
        post[(hub, acc["transfer"], "opening", 0)] -= net0
        post[(hub, acc["cash_to_accrual"], "opening", 0)] += net0
        for (c, a, m), v in self.tx_post.items():     # the tab's changes to single transactions
            if abs(v) >= 1e-9:
                post[(c, a, "yours", m)] += v
        self.post = post
        self.by_account = defaultdict(float)  # (company, account, m)
        for (c, a, _comp, m), v in post.items():
            self.by_account[(c, a, m)] += v

    # ── reads ────────────────────────────────────────────────────────────────────────────────────

    def line_of(self, company: str, account: str) -> str | None:
        return self.acc["acct_line"].get(f"{company}|{account}")

    def view_kind(self, view: str) -> str:
        if view == "all":
            return "all"
        if view in self.companies:
            return "company"
        if view in self.brands:
            return "brand"
        raise InputError(f"Unknown view {view!r}.")

    def _lines_all(self) -> list[str]:
        return [l for l in self.REVENUE + self.COSTS if not l.startswith("Uncategorized")] + self.BELOW

    def amounts(self, view: str, line: str, month: int | None) -> dict:
        """One line's figures for a view and a month (None = the year to date)."""
        ms = [month] if month else self.MONTHS
        kind = self.view_kind(view)
        if kind == "brand":
            v = 0.0 if line in self.BELOW else sum(self.by_brand[(view, line, m)] for m in ms)
            return {"qbo": None, "corrections": None, "corrected": _r(v)}
        comps = self.companies if kind == "all" else [view]
        corrected = sum(self.corr[(c, line, m)] for c in comps for m in ms)
        qms = ms if month else [0] + self.MONTHS        # QuickBooks books some entries on January 1
        qbo = sum(self.qbo[(c, line, m)] for c in comps for m in qms)
        if line.startswith("Uncategorized"):
            corrected = 0.0
        return {"qbo": _r(qbo), "corrections": _r(corrected - qbo), "corrected": _r(corrected)}

    def statement(self, view: str, month: int | None = None) -> dict:
        kind = self.view_kind(view)
        sections = []
        for key, label, lines in (("revenue", "Revenue", self.REVENUE), ("costs", "Costs", self.COSTS),
                                  ("below", "Below the line", self.BELOW)):
            rows = []
            for line in lines:
                if line.startswith("Uncategorized"):
                    if kind == "brand":
                        continue                  # a brand's year is its reviewed lines: nothing is uncategorized
                    a = self.amounts(view, line, month)
                    if not a["qbo"]:
                        continue                  # only shown while QuickBooks still has something there
                a = self.amounts(view, line, month)
                if kind == "brand" and line in self.BELOW:
                    continue
                rows.append({"line": line, **a,
                             "flagged": bool(a["corrections"] is not None and abs(a["corrections"]) >= 1)})
            sections.append({"key": key, "label": label, "lines": rows,
                             "total": self._sum(rows)})
        t = lambda k: next(s["total"] for s in sections if s["key"] == k)
        rev, costs = t("revenue"), t("costs")
        noi = {k: (None if rev[k] is None else _r(rev[k] - costs[k])) for k in rev}
        below = {r["line"]: r for r in next(s["lines"] for s in sections if s["key"] == "below")}
        other = below.get("Other income") or {"qbo": 0, "corrections": 0, "corrected": 0}
        ps = below.get("Partner profit shares") or {"qbo": 0, "corrections": 0, "corrected": 0}
        ni = {k: (None if noi[k] is None else _r(noi[k] + (other[k] or 0) - (ps[k] or 0))) for k in noi}
        out = {"view": view, "kind": kind, "month": month, "sections": sections,
               "totals": {"total_revenue": rev, "total_costs": costs, "net_operating_income": noi,
                          "net_income": ni}, "memo": self._memo(view, month)}
        if kind == "company" and month is None:
            q = self.d["qbo"][view]["net_income"]
            out["ties_to_quickbooks"] = {"report": _r(q), "lines": ni["qbo"], "delta": _r(ni["qbo"] - q)}
        return out

    def _sum(self, rows):
        keys = ("qbo", "corrections", "corrected")
        return {k: (None if any(r[k] is None for r in rows) else _r(sum(r[k] for r in rows))) for k in keys}

    def _memo(self, view, month):
        memo = []
        for p in self.d["partners"]:
            if view in (p["company"], "all") and p.get("salary"):
                frac = self.d["period"]["salary_fraction"]
                f = float(frac[str(month)]) - float(frac[str(month - 1)]) if month else float(frac[str(self.MONTHS[-1])])
                memo.append({"label": f"{self.d.get('salary_owner') or 'The owner'}'s salary, before "
                                      f"{p['person']}'s share (not booked)",
                             "amount": _r(float(self.inp.get("salary") or 0) * f)})
        return memo

    def tx_words(self, key: str) -> dict:
        """How one transaction reads on a row: the file's words for it (`transactions`), with whatever an older
        file lacks filled from what it has -- its QuickBooks payee for the tidy one, no flags."""
        t = self.tx.get(key) or {}
        payee = t.get("payee")
        change = self.changes.get(key)
        return {"key": key, "payee": (t.get("who") or "") if payee is None else payee, "short": t.get("short") or "",
                "what": t.get("what") or "", "note": t.get("note") or "", "changed": t.get("changed") or "",
                "status": "yours" if change else (t.get("status") or ""), "change": change,
                "cleared": t.get("cleared"), "twins": t.get("twins") or [], "paid_from": t.get("paid_from") or "",
                "card_holder": t.get("card_holder") or "",
                "qbo": t.get("qbo") or {"payee": t.get("who") or "", "category": ""},
                "tx_amount": t.get("amount"), "money_in": t.get("money_in"), "tx_date": t.get("date") or ""}

    def line_detail(self, view: str, line: str, month: int | None = None, limit: int = 5000) -> dict:
        """Every dollar behind one line: the transactions, and each share, schedule and timing item.

        A transaction is one row however many of its ledger lines land here (one split across two brands of
        the same company is still one charge), with the words a row shows. Shares carry what they're made of
        (whose pay, which pool, what percent), so a page can group them without reading the label."""
        kind = self.view_kind(view)
        ms = set([month] if month else self.MONTHS)
        brands = (self.brands if kind == "all" else [view] if kind == "brand"
                  else [b for b in self.brands if self.bc[b] == view])
        comps = set(self.bc[b] for b in brands)
        rows = []
        dc, dl = self.dues["company"], self.dues["line"]
        txs: dict[tuple, dict] = {}
        here = lambda l: (l["cls"] in ("revenue", "cost") and l["brand"] in brands and l["line"] == line
                          and l["month"] in ms and not (self.bc[l["brand"]] == dc and line == dl and kind != "brand"))
        for l in self.ledger:
            if here(l):
                r = txs.get((l["key"], l["month"]))
                if r is not None:
                    r["amount"] += l["amount"]
                    if l["brand"] not in r["brands"]:
                        r["brands"].append(l["brand"])
                    continue
                t = self.tx.get(l["key"]) or {}
                txs[(l["key"], l["month"])] = r = {"kind": "tx", "month": l["month"], "date": l["date"], "label": t.get("what") or "",
                                     "who": t.get("who") or "", "brand": l["brand"], "brands": [l["brand"]],
                                     "paid_by": l["company"], "booked_as": l["account"], "ref": t.get("ref") or "",
                                     "amount": l["amount"], "verdict": t.get("verdict") or "", "pooled": bool(l["pool"]),
                                     **self.tx_words(l["key"])}
                rows.append(r)
        for r in txs.values():
            r["amount"] = _r(r["amount"])
        if self.changes:
            # what a changed transaction had here before the change: on its row if some of it is still here (a
            # split), else as a row of its own that adds nothing to the line -- moved away, not vanished
            was = defaultdict(float)
            first = {}
            for l in self.d["ledger"]:
                if l["key"] in self.changes and here(l):
                    was[(l["key"], l["month"])] += l["amount"]
                    first.setdefault((l["key"], l["month"]), l)
            for k, v in was.items():
                if k in txs:
                    txs[k]["was"] = _r(v)
                    continue
                l = first[k]
                t = self.tx.get(l["key"]) or {}
                rows.append({"kind": "tx", "gone": True, "month": l["month"], "date": l["date"],
                             "label": t.get("what") or "", "who": t.get("who") or "", "brand": l["brand"],
                             "brands": [l["brand"]], "paid_by": l["company"], "booked_as": l["account"],
                             "ref": t.get("ref") or "", "amount": 0.0, "was": _r(v), "verdict": t.get("verdict") or "",
                             "pooled": False, **self.tx_words(l["key"])})
        if line == dl and dc in comps and kind != "brand":
            for m in sorted(ms):
                rows.append({"kind": "dues", "month": m, "label": "Dues earned this month, member by member",
                             "brand": next(b for b in brands if self.bc[b] == dc),
                             "amount": _r(float(self.dues["earned"].get(str(m), 0)))})
        if line in self.COSTS:
            for b in brands:
                k = self.brands.index(b)
                for pool, pct, what, src in (("Advertising", self.ads, "the shared ads", "ads"),
                                             ("Payment processing", self.fees, "the Stripe fees", "fees")):
                    if self.d["pools"][pool]["line"] != line:
                        continue
                    for m in sorted(ms):
                        v = self.pool_m[(pool, m)] * pct[b]
                        if abs(v) >= 0.005:
                            rows.append({"kind": "share", "src": src, "month": m, "label": f"{b}'s share of {what} "
                                         f"({pct[b] * 100:.1f}% of {self.pool_m[(pool, m)]:,.0f})",
                                         "brand": b, "paid_by": self.hub, "pct": pct[b],
                                         "pool_total": _r(self.pool_m[(pool, m)]), "amount": _r(v)})
                if line == "Staff and contractors":
                    for (p, m), c in sorted(self.staff_cost.items()):
                        if m in ms:
                            v = c * self.split(p, m)[k]
                            if abs(v) >= 0.005:
                                rows.append({"kind": "share", "src": "staff", "month": m, "label": f"{b}'s share of "
                                             f"{p}'s pay ({self.split(p, m)[k] * 100:.1f}%)", "who": p, "brand": b,
                                             "paid_by": self.hub, "booked_as": self.people[p]["account"],
                                             "pct": self.split(p, m)[k], "pool_total": _r(c),
                                             "follows_wages": bool(self.people[p].get("follows_wages")),
                                             "amount": _r(v)})
                for (pool, item, m), c in sorted(self.item_m.items()):
                    if m not in ms:
                        continue
                    il = self.d["pools"][pool]["line"] or self.item_line[(pool, item)]
                    if il != line:
                        continue
                    s = self.item_split(pool, item)[b]
                    if abs(c * s) >= 0.005:
                        rows.append({"kind": "share", "src": "item", "month": m,
                                     "label": f"{b}'s share of {item} ({s * 100:.1f}%)", "who": item, "brand": b,
                                     "paid_by": self.hub, "pct": s, "pool_total": _r(c),
                                     "item_kind": self.item_kind[pool], "amount": _r(c * s)})
                if b == self.d.get("charges_brand"):
                    for c in self.d["charges"]:
                        treat, cl = self.charge(c)
                        if treat == "business" and cl == line and c["month"] in ms:
                            rows.append({"kind": "charge", "month": c["month"], "date": c["date"],
                                         "label": c["what"], "who": c["who"], "brand": b, "paid_by": self.hub,
                                         "booked_as": c["account"], "ref": c["sid"], "amount": c["amount"],
                                         "charge_ref": c["ref"]})
        if kind != "brand" and (line in self.REVENUE or line in self.COSTS):
            for t in self.timing_rows():
                if t["company"] in comps and t["line"] == line and t["month"] in ms:
                    rows.append({"kind": "timing", "month": t["month"], "label": t["what"], "booked_as": t["account"],
                                 "amount": t["amount"]})
        if line == "Partner profit shares" and kind != "brand":
            for p in self.d["partners"]:
                if p["company"] in comps:
                    for m in sorted(ms):
                        v = self.share[(p["company"], m)]
                        if abs(v) >= 0.005:
                            rows.append({"kind": "partner", "month": m, "label": f"{p['person']}'s share, accrued on "
                                         "the year to date", "who": p["person"], "amount": _r(v)})
        total = _r(sum(r["amount"] for r in rows))
        rows.sort(key=lambda r: -abs(r["amount"]))
        out = {"view": view, "line": line, "month": month, "count": len(rows), "rows_total": total,
               "rows": rows[:limit], "truncated": max(0, len(rows) - limit), **self.amounts(view, line, month)}
        if kind != "brand":
            out["why"] = self._why(comps, line, month)
        if line == "Staff and contractors":
            out["people"] = self._people(brands, ms)
        return out

    def _people(self, brands, ms) -> list[dict]:
        """The staff line person by person: what each one cost these brands, month by month (their pay times
        the brands' share of them)."""
        ks = [self.brands.index(b) for b in brands]
        out = []
        for p in self.d["people"]:
            months = {}
            for m in sorted(ms):
                c = self.staff_cost.get((p["name"], m), 0.0)
                v = c * sum(self.split(p["name"], m)[k] for k in ks) if abs(c) >= 0.005 else 0.0
                if abs(v) >= 0.005:
                    months[str(m)] = _r(v)
            if months:
                out.append({"name": p["name"], "months": months, "total": _r(sum(months.values())),
                            "follows_wages": bool(p.get("follows_wages")), "note": p.get("note") or ""})
        return sorted(out, key=lambda r: -abs(r["total"]))

    def transactions(self) -> list[dict]:
        """Every transaction in the file, as the Transactions page lists it: its words, the company that paid
        it, and where the corrected books put it (each brand and P&L line it lands on, and how much). A shared
        cost has no one brand -- the decisions split it -- and money moving between accounts has no P&L line."""
        if getattr(self, "_txs", None) is not None:
            return self._txs
        lines, before = defaultdict(list), defaultdict(list)
        for l in self.ledger:
            lines[l["key"]].append(l)
        for l in self.d["ledger"]:
            if l["key"] in self.changes:
                before[l["key"]].append(l)

        def placement(ls):
            place, shared = defaultdict(float), defaultdict(float)
            for l in ls:
                if l["cls"] not in ("revenue", "cost") or not l.get("line"):
                    continue
                if l["brand"] in self.bc:
                    place[(l["brand"], l["line"])] += l["amount"]
                else:
                    shared[l["line"]] += l["amount"]
            return ([{"brand": b, "line": ln, "amount": _r(v)} for (b, ln), v in place.items() if abs(v) >= 0.005]
                    + [{"brand": None, "line": ln, "amount": _r(v)} for ln, v in shared.items() if abs(v) >= 0.005])

        out = []
        for key, t in self.tx.items():
            ls = lines.get(key, [])
            w = self.tx_words(key)
            if key in self.changes:          # where the review had it, before the tab's change
                w["was"] = placement(before[key])
            ls = ls or before.get(key, [])   # a removed one still has a date and a payer
            date = w["tx_date"] or (ls[0]["date"] if ls else "")
            amount = w["tx_amount"]
            if amount is None:          # an older file: the P&L lines, money in positive
                amount = _r(sum(l["amount"] if l["cls"] == "revenue" else -l["amount"] for l in ls
                                if l["cls"] in ("revenue", "cost")))
            out.append({**w, "date": date, "month": int(date[5:7]) if date else None,
                        "company": t.get("company") or (ls[0]["company"] if ls else ""), "amount": amount,
                        "money_in": w["money_in"] if w["money_in"] is not None else amount > 0,
                        "placement": placement(lines.get(key, []))})
        out.sort(key=lambda r: (r["date"] or "", r["key"]))
        self._txs = out
        return out

    def _why(self, comps, line, month):
        """How QuickBooks' figure becomes the corrected one: the correcting entries on this line's
        accounts, by reason. Revenue reads the other way round from a debit."""
        sign = -1 if line in self.REV_SIDE else 1
        parts = defaultdict(float)
        for (c, a, comp, m), v in self.post.items():
            if c in comps and (m == month if month else True) and self.line_of(c, a) == line:
                if month is None or m == month:
                    parts[comp] += sign * v
        return [{"component": k, "label": self.labels.get(k, k), "amount": _r(v)}
                for k, v in sorted(parts.items(), key=lambda kv: -abs(kv[1])) if abs(v) >= 0.5]

    def corrections(self, company: str) -> dict:
        """The entries to post for one company: one per month, plus January 1."""
        if company not in self.companies:
            raise InputError(f"Unknown company {company!r}.")
        n = len(self.MONTHS) + 1
        accts = defaultdict(lambda: [0.0] * n)
        comp_parts = defaultdict(lambda: defaultdict(lambda: [0.0] * n))   # what each line is for, by month
        for (c, a, comp, m), v in self.post.items():
            if c == company:
                accts[a][m] += v
                comp_parts[a][comp][m] += v
        rank = {ln: i for i, ln in enumerate(self.REVENUE + self.COSTS + self.BELOW)}
        rows = []
        for a, vals in accts.items():
            if not any(abs(v) >= 0.005 for v in vals):
                continue
            line = self.line_of(company, a)
            rows.append({"account": a, "line": line, "new": bool(self.acc["acct_new"].get(f"{company}|{a}")),
                         "amounts": [_r(v) for v in vals], "total": _r(sum(vals)),
                         "parts": {k: _r(sum(v)) for k, v in comp_parts[a].items() if abs(sum(v)) >= 0.005},
                         "parts_by_month": {k: [_r(x) for x in v] for k, v in comp_parts[a].items()
                                            if any(abs(x) >= 0.005 for x in v)}})
        rows.sort(key=lambda r: (r["line"] is None, rank.get(r["line"], 99) if r["line"] else self._bs_rank(r["account"]),
                                 r["account"]))
        # QuickBooks refuses an entry whose debits and credits differ by a cent, and rounding each line on
        # its own leaves exactly that. An entry that balances exactly before rounding puts its rounding
        # cents on its largest line. One that doesn't balance is left alone, so the check below still sees it.
        for k in range(n):
            live = [r for r in rows if abs(r["amounts"][k]) >= 0.005]
            exact = sum(accts[r["account"]][k] for r in rows)
            resid = _r(sum(r["amounts"][k] for r in live))
            if live and abs(exact) < 0.005 and resid:
                big = max(live, key=lambda r: abs(r["amounts"][k]))
                big["amounts"][k] = _r(big["amounts"][k] - resid)
                big["total"] = _r(sum(big["amounts"]))
        balance = [_r(sum(r["amounts"][k] for r in rows)) for k in range(n)]
        ni_change = [_r(-sum(r["amounts"][k] for r in rows if r["line"])) for k in range(n)]
        qbo_ni = [_r(sum((1 if ln in self.REV_SIDE else -1) * self.qbo[(company, ln, k)]
                         for ln in self.REVENUE + self.COSTS + self.BELOW)) for k in range(n)]
        corrected_ni = [0.0] + [_r(self._ni(company, m)) for m in self.MONTHS]
        months = [{"k": 0, "label": "Jan 1"}] + [{"k": x["m"], "label": x["label"]} for x in self.d["period"]["months"]]
        return {"company": company, "months": months, "accounts": rows, "balance": balance,
                "net_income_change": ni_change, "qbo_net_income": qbo_ni, "corrected_net_income": corrected_ni,
                "balanced": all(abs(b) < 0.005 for b in balance),
                "ties": all(abs(qbo_ni[k] + ni_change[k] - corrected_ni[k]) < 1 for k in range(n)),
                "posted": dict((self.inp.get("posted") or {}).get(company) or {}),
                "labels": {k: self.labels.get(k, k) for k in {p for r in rows for p in r["parts_by_month"]}}}

    def _ni(self, company, m):
        rev = sum(self.corr[(company, x, m)] for x in self.REVENUE)
        cost = sum(self.corr[(company, x, m)] for x in self.COSTS)
        return rev - cost + self.corr[(company, "Other income", m)] - self.corr[(company, "Partner profit shares", m)]

    def net_income(self, company: str) -> float:
        return _r(sum(self._ni(company, m) for m in self.MONTHS))

    def balance_sheet(self, company: str) -> dict:
        if company not in self.companies:
            raise InputError(f"Unknown company {company!r}.")
        tb = {x["account"]: x for x in self.d["bs"][company]["accounts"]}
        corr = defaultdict(float)
        for (c, a, m), v in self.by_account.items():
            if c == company and not self.line_of(c, a):
                corr[a] += v
        types = self.acc["types"].get(company) or {}
        new_types = self.acc["new_types"]
        accounts = sorted({a for a, x in tb.items() if abs(x["balance"]) >= 0.005}
                          | {a for a, v in corr.items() if abs(v) >= 0.005})
        sections = {"Asset": [], "Liability": [], "Equity": []}
        for a in accounts:
            if a in tb:
                cls_, typ = tb[a]["class"], tb[a]["type"]
            else:
                cls_, typ = (types.get(a) or new_types.get(a) or self.extra_types.get(a)
                             or ["Liability", "Other Current Liability"])
            sec, note = cls_ if cls_ in sections else "Liability", ""
            rc = (self.acc.get("reclass") or {}).get(a)
            if "due from" in a.lower() and cls_ != "Asset" and (a not in tb or tb[a]["balance"] >= 0):
                sec, note = "Asset", f"Set up as {typ}, but it's owed to {company}: make it Other Current Asset."
            elif rc and cls_ == rc.get("when_class", cls_):
                sec, note = rc["section"], rc["note"]   # the file's own reading of an account set up wrong
            elif a not in tb and (self.acc["acct_new"].get(f"{company}|{a}") or a in new_types or a in self.extra_types):
                note = f"New: set it up as {typ}."
            sign = 1 if sec == "Asset" else -1
            q = sign * (tb[a]["balance"] if a in tb else 0.0)
            sections[sec].append({"account": a, "type": typ, "qbo": q, "corrections": sign * corr[a],
                                  "corrected": q + sign * corr[a], "note": note})
        ni_q = float(self.d["bs"][company]["net_income"])
        ni_c = sum(self._ni(company, m) for m in self.MONTHS)
        sections["Equity"].append({"account": "Net income for the period", "type": "", "qbo": ni_q,
                                   "corrections": ni_c - ni_q, "corrected": ni_c, "note": ""})
        # Totals and the balance check from the UNROUNDED figures, then everything rounded for show:
        # rounding each row first leaves a cent of drift that reads as a balance sheet that doesn't balance.
        keys = ("qbo", "corrections", "corrected")
        tot = {s: {k: sum(r[k] for r in rows) for k in keys} for s, rows in sections.items()}
        le = {k: tot["Liability"][k] + tot["Equity"][k] for k in keys}
        balanced = {"qbo": abs(tot["Asset"]["qbo"] - le["qbo"]) < 0.01,
                    "corrected": abs(tot["Asset"]["corrected"] - le["corrected"]) < 0.01}
        for rows in sections.values():
            for r in rows:
                for k in keys:
                    r[k] = _r(r[k])
        return {"company": company, "sections": [{"key": s, "label": {"Asset": "Assets", "Liability": "Liabilities",
                                                                      "Equity": "Equity"}[s], "rows": rows,
                                                  "total": {k: _r(v) for k, v in tot[s].items()}}
                                                 for s, rows in sections.items()],
                "liabilities_plus_equity": {k: _r(v) for k, v in le.items()}, "balanced": balanced}

    def intercompany(self) -> dict:
        bal = {(b["company"], b["account"]): b["closing"] for b in self.d["interco"]["balances"]}
        ytd = lambda c, a: sum(v for (cc, aa, m), v in self.by_account.items() if cc == c and aa == a)
        pairs = []
        for p in self.d["interco"]["pairs"]:
            booked = sum(t["sign"] * (bal.get(tuple(t["balance"]), 0.0) if t["balance"] else 0.0) for t in p["terms"])
            corr = sum(t["sign"] * ytd(t["company"], t["post"]) for t in p["terms"])
            pairs.append({"pair": p["pair"], "reads": p["reads"], "booked": _r(booked), "corrections": _r(corr),
                          "corrected": _r(booked + corr)})
        return {"pairs": pairs}

    def summary(self) -> dict:
        """Each brand's year on a cash basis, and the partners' shares worked out (January to the last
        full month)."""
        L, b_ = self.ledger, self.brands
        rev, direct = defaultdict(float), defaultdict(float)
        rev_m, direct_m = defaultdict(float), defaultdict(float)
        for l in L:
            if l["cls"] == "revenue":
                rev[l["brand"]] += l["amount"]
                rev_m[(l["brand"], l["month"])] += l["amount"]
            elif l["cls"] == "cost" and l["brand"]:
                direct[l["brand"]] += l["amount"]
                direct_m[(l["brand"], l["month"])] += l["amount"]
        cb = self.d.get("charges_brand")
        for c in self.d["charges"]:
            if self.charge(c)[0] == "business":
                direct[cb] += c["amount"]
                direct_m[(cb, c["month"])] += c["amount"]
        staff_m = defaultdict(float)
        for (p, m), v in self.staff_cost.items():
            for k, b in enumerate(b_):
                staff_m[(b, m)] += v * self.split(p, m)[k]
        items_m = defaultdict(float)
        for (pool, item, m), v in self.item_m.items():
            s = self.item_split(pool, item)
            for b in b_:
                items_m[(b, pool, m)] += v * s[b]
        pool = lambda name: sum(v for (p, m), v in self.pool_m.items() if p == name)
        rows = []
        for b in b_:
            ads_ = pool("Advertising") * self.ads[b]
            staff = sum(v for (bb, m), v in staff_m.items() if bb == b)
            sw = sum(v for (bb, p, m), v in items_m.items() if bb == b and p == "Software and subscriptions")
            oh = sum(v for (bb, p, m), v in items_m.items() if bb == b and p == "General overhead")
            fee = pool("Payment processing") * self.fees[b]
            tot = direct[b] + ads_ + staff + sw + oh + fee
            rows.append({"brand": b, "revenue": _r(rev[b]), "direct": _r(direct[b]), "ads": _r(ads_),
                         "staff": _r(staff), "software": _r(sw), "other": _r(oh), "fees": _r(fee),
                         "total_costs": _r(tot), "operating_result": _r(rev[b] - tot)})
        n = int(self.d["period"].get("accrual_months") or self.MONTHS[-1])
        rev_lines = set(self.REVENUE)
        eff = lambda t: (1 if t["line"] in rev_lines else -1) * t["amount"]

        def month_costs(b, m):
            return (direct_m[(b, m)] + self.pool_m[("Advertising", m)] * self.ads[b] + staff_m[(b, m)]
                    + items_m[(b, "Software and subscriptions", m)] + items_m[(b, "General overhead", m)]
                    + self.pool_m[("Payment processing", m)] * self.fees[b])
        partners = []
        for p in self.d["partners"]:
            comp = p["company"]
            brand = next(b for b in b_ if self.bc[b] == comp)
            dues_dep = defaultdict(float)
            if comp == self.dues["company"]:
                for l in L:
                    if l["cls"] == "revenue" and l["brand"] == brand and l["line"] == self.dues["line"]:
                        dues_dep[l["month"]] += l["amount"]
            noi = 0.0
            for m in range(1, n + 1):
                earned = float(self.dues["earned"].get(str(m), 0)) if comp == self.dues["company"] else 0.0
                noi += rev_m[(brand, m)] - dues_dep[m] + earned - month_costs(brand, m)
            noi += sum(eff(t) for t in self.timing_rows() if t["company"] == comp and 1 <= t["month"] <= n)
            rate = float(self.inp["rates"].get(comp) or 0)
            sal = float(self.inp.get("salary") or 0) * n / 12 if p.get("salary") else 0.0
            earned = rate * max(0.0, noi - sal)
            paid = sum(l["amount"] for l in L if l["cls"] == "partner" and l["company"] == comp and l["month"] <= n)
            partners.append({"company": comp, "brand": brand, "person": p["person"], "rate": rate,
                             "noi": _r(noi), "salary": _r(sal), "earned": _r(earned), "paid": _r(paid),
                             "owed_back": _r(max(0.0, paid - earned)), "owed_to": _r(max(0.0, earned - paid)),
                             "to_date": _r(sum(self.share[(comp, m)] for m in self.MONTHS)), "note": p.get("note", "")})
        recover = sum(l["amount"] for l in L if l["cls"] == "recover")
        return {"brands": rows, "billback_ads": _r(pool("Advertising") * self.ads["billback"]),
                "partners": partners, "months_through": n, "recovered": _r(recover)}

    # ---- the forward view: Forecast, Budget, Cash flow, and the Statement's future months
    def _recent_months(self) -> list[int]:
        """The last three full months (the books' final month is usually part of one)."""
        n = int(self.d["period"].get("accrual_months") or self.MONTHS[-1])
        return [m for m in self.MONTHS if n - 2 <= m <= n]

    def budget(self, company: str) -> dict:
        """A company's cost lines: the year so far, a month at the last three months' pace, and each as a share
        of revenue -- what it was, beside what the plan says it needs to be."""
        if company not in self.companies:
            raise InputError(f"Unknown company {company!r}.")
        rec = self._recent_months()
        rev = sum(self.corr[(company, x, m)] for x in self.REVENUE for m in self.MONTHS)
        lines = []
        for x in self.COSTS:
            if x.startswith(("Uncategorized", "Not yet")):
                continue
            ytd = sum(self.corr[(company, x, m)] for m in self.MONTHS)
            recent = sum(self.corr[(company, x, m)] for m in rec) / len(rec) if rec else 0.0
            if abs(ytd) >= 0.5 or abs(recent) >= 0.5:
                lines.append({"line": x, "ytd": _r(ytd), "recent": _r(recent),
                              "pct": _r(ytd / rev * 100) if rev > 0 else None})
        noi = sum(self._ni(company, m) + self.corr[(company, "Partner profit shares", m)]
                  - self.corr[(company, "Other income", m)] for m in self.MONTHS)
        return {"company": company, "revenue": _r(rev), "noi": _r(noi), "lines": lines}

    def plan(self) -> dict:
        """The projected company's monthly cost, line by line: what the file says to start from (the meeting's
        no ads, say), else the last three months' pace; and whatever the tab saved over it. The lines the
        forecast plans itself (the retreats, the commissions on installments) aren't in it."""
        f = self.d.get("forward") or {}
        co = f.get("company")
        if not co:
            return {}
        rec = self._recent_months()
        out = {}
        for x in self.COSTS:
            if x in (f.get("plan_from") or {}) or x.startswith(("Uncategorized", "Not yet")):
                continue
            pace = sum(self.corr[(co, x, m)] for m in rec)
            if x in (f.get("plan_defaults") or {}):
                out[x] = float(f["plan_defaults"][x])
            elif x not in (f.get("selling") or []) and abs(pace) >= 1:
                out[x] = float(round(pace / len(rec)))
        for x, v in (self.inp.get("budget") or {}).items():
            if x in self.COSTS and x not in (f.get("plan_from") or {}):
                out[x] = float(v)
        return out

    def forward(self) -> dict | None:
        """The months after the books end, for the company the file projects (`forward`), through its horizon:
        its contracts' revenue as members earn it, the plan's monthly costs, the forecast's retreats and
        commissions, and its partner's share as the year's profit builds -- the same accrual as the corrected
        books, each calendar year on its own. Then its cash, from the bank balance the file starts from. None
        when the file projects nothing."""
        f = self.d.get("forward")
        if not f:
            return None
        co = f["company"]
        year = int(self.d["period"]["start"][:4])
        asm = json.loads(json.dumps(f.get("assumptions") or {}))
        asm.update(self.inp.get("forward") or {})
        plan = self.plan()
        running = sum(plan.values())
        sched = {s["key"]: s["from_paid"] + s["from_future"] for s in f["schedule"]}
        horizon = [s["key"] for s in f["schedule"] if s["key"] <= f["horizon"]]
        coll = f.get("collections") or {}
        frac0 = float(f.get("first_fraction") or 1.0)
        retreats = lambda k: sum(float(r.get("amount") or 0) for r in asm.get("retreats") or []
                                 if r.get("on") and r.get("key") == k)
        commission = lambda k: float(asm.get("commission_pct") or 0) / 100 * float(coll.get(k, 0))
        pay_of = lambda k: float(asm.get("partner_pay") or 0) if k <= (asm.get("partner_end") or "") else 0.0
        partner = next((p for p in self.d["partners"] if p["company"] == co), None)
        rate = float(self.inp["rates"].get(co) or 0) if partner else 0.0
        paid_m = defaultdict(float)
        for l in self.ledger:
            if l["cls"] == "partner" and l["company"] == co:
                paid_m[l["month"]] += l["amount"]
        rows, cum, base = [], defaultdict(float), defaultdict(float)
        tot = {"earned": 0.0, "paid": 0.0}

        def step(k, rev, cost, projected, pay):
            yr = k[:4]
            cum[yr] += rev - cost
            nb = max(0.0, cum[yr])
            share = rate * (nb - base[yr])
            base[yr] = nb
            tot["earned"] += share
            tot["paid"] += pay
            rows.append({"key": k, "rev": _r(rev), "cost": _r(cost), "profit": _r(rev - cost), "ytd": _r(cum[yr]),
                         "share": _r(share), "earned": _r(tot["earned"]), "paid_month": _r(pay), "paid": _r(tot["paid"]),
                         "ahead": _r(tot["paid"] - tot["earned"]), "projected": projected})

        first = horizon[0] if horizon else None
        for m in self.MONTHS:
            k = f"{year}-{m:02d}"
            rev = sum(self.corr[(co, x, m)] for x in self.REVENUE)
            cost = sum(self.corr[(co, x, m)] for x in self.COSTS)
            if k == first:      # the month the books end in: actual so far, then the rest of it projected
                step(k, rev + sched.get(k, 0), cost + running * frac0 + retreats(k) + commission(k), True,
                     paid_m[m] or pay_of(k))
            else:
                step(k, rev, cost, False, paid_m[m])
        for k in horizon[1:]:
            step(k, sched.get(k, 0), running + retreats(k) + commission(k), True, pay_of(k))
        future = [r for r in rows if r["projected"]]
        months_ahead = frac0 + max(0, len(horizon) - 1)
        by_line = defaultdict(float)
        by_line[f["revenue_line"]] += sum(sched.get(k, 0) for k in horizon)
        for x, v in plan.items():
            by_line[x] += v * months_ahead
        for x, what in (f.get("plan_from") or {}).items():
            by_line[x] += sum((retreats if what == "retreats" else commission)(k) for k in horizon)
        by_line["Partner profit shares"] += sum(r["share"] for r in future)
        # cash: from the file's balance, a whole month at a time (the balance is the month before the books end)
        cash, bal = [], float((f.get("cash") or {}).get("balance") or 0)
        repay = asm.get("repay") or {}
        for k in horizon:
            back = float(repay.get("amount") or 0) if repay.get("on") and repay.get("key") == k else 0.0
            row = {"key": k, "open": _r(bal), "in": _r(coll.get(k, 0)), "partner": _r(pay_of(k)), "running": _r(running),
                   "commissions": _r(commission(k)), "retreats": _r(retreats(k)), "repay": _r(back)}
            bal += row["in"] - row["partner"] - row["running"] - row["commissions"] - row["retreats"] - back
            row["close"] = _r(bal)
            cash.append(row)
        launches = []
        for l in f.get("launches") or []:
            costs = {x: _r(sum(self.corr[(co, x, m)] for m in l["months"])) for x in f.get("selling") or []}
            total = sum(costs.values())
            launches.append({**l, "costs": costs, "cost_total": _r(total),
                             "per_dollar": _r(l["contracted"] / total) if total > 0 else None})
        labels = {s["key"]: s["label"] for s in f["schedule"]}
        c = f["contracts"]
        # the company's statement through the horizon: the corrected year so far, plus the months ahead
        secs = []
        for s in self.statement(co)["sections"]:
            secs.append({"key": s["key"], "label": s["label"], "lines": [
                {"line": r["line"], "so_far": r["corrected"], "ahead": _r(by_line.get(r["line"], 0.0)),
                 "through": _r(r["corrected"] + by_line.get(r["line"], 0.0))} for r in s["lines"]]})

        def totals(field):
            sec = lambda k: [l for s in secs if s["key"] == k for l in s["lines"]]
            rev, cost = sum(l[field] for l in sec("revenue")), sum(l[field] for l in sec("costs"))
            below = {l["line"]: l[field] for l in sec("below")}
            return {"total_revenue": _r(rev), "total_costs": _r(cost), "net_operating_income": _r(rev - cost),
                    "net_income": _r(rev - cost + below.get("Other income", 0) - below.get("Partner profit shares", 0))}
        return {
            "company": co, "partner": partner["person"] if partner else None, "rate": rate,
            "contracts": {**c, "still_to_earn": _r(c["contracted"] - c["earned_so_far"])},
            "schedule": f["schedule"], "months": [{"key": k, "label": labels[k]} for k in horizon],
            "assumptions": asm, "meeting": f.get("meeting"), "plan": plan, "plan_from": f.get("plan_from") or {},
            "notes": f.get("notes") or {}, "running": _r(running), "months_ahead": _r(months_ahead),
            "rows": rows, "future_by_line": {x: _r(v) for x, v in by_line.items()},
            "statement": {"sections": secs, "totals": {k: totals(k) for k in ("so_far", "ahead", "through")},
                          "through": labels.get(horizon[-1]) if horizon else None},
            "summary": {"profit": _r(sum(r["profit"] for r in rows)), "earned": rows[-1]["earned"] if rows else 0.0,
                        "paid": rows[-1]["paid"] if rows else 0.0, "ahead": rows[-1]["ahead"] if rows else 0.0,
                        "through": horizon[-1] if horizon else None},
            "launches": launches,
            "cash": {**(f.get("cash") or {}), "rows": cash,
                     "runs_out": next((r["key"] for r in cash if r["close"] < 0), None)},
        }

    def checks(self) -> list[dict]:
        out = []
        n = len(self.MONTHS) + 1
        for comp in self.companies:
            c = self.corrections(comp)
            out.append({"key": f"balance|{comp}", "label": f"{comp}: every entry balances", "ok": c["balanced"]})
            out.append({"key": f"ties|{comp}", "label": f"{comp}: QuickBooks plus the entries is the corrected "
                                                        "P&L, month by month", "ok": c["ties"]})
            bs = self.balance_sheet(comp)
            out.append({"key": f"bs|{comp}", "label": f"{comp}: the balance sheet balances, as booked and "
                                                      "corrected", "ok": bs["balanced"]["qbo"] and bs["balanced"]["corrected"]})
        # every line's entries equal the line's correction (the per-line tie)
        bad = []
        for comp in self.companies:
            for line in self.REVENUE + self.COSTS + self.BELOW:
                sign = -1 if line in self.REV_SIDE else 1
                got = sum(v for (c, a, m), v in self.by_account.items() if c == comp and self.line_of(c, a) == line)
                want = self.amounts(comp, line, None)["corrections"] * sign
                if line.startswith("Uncategorized"):
                    want = -sum(self.qbo[(comp, line, m)] for m in range(n)) * sign
                if abs(got - want) >= 1:
                    bad.append(f"{comp} {line}")
        out.append({"key": "lines", "label": "Every P&L line's entries add up to its correction", "ok": not bad,
                    "detail": bad[:5]})
        off = [p for p in self.d["people"] if not p.get("follows_wages")
               for m in self.MONTHS if abs(self.staff_cost.get((p["name"], m), 0)) >= 0.005
               and abs(sum(self.split(p["name"], m)) - 1) > 1e-4]
        out.append({"key": "staff", "label": "Every person's split adds to 100% in each month they were paid",
                    "ok": not off, "detail": sorted({p["name"] for p in off})[:5]})
        items_off = [(kind, r["name"]) for kind, spec in self.d["items"].items() for r in spec["rows"]
                     if abs(sum(self.item_split(spec["pool"], r["name"]).values()) - 1) > 1e-4]
        out.append({"key": "items", "label": "Every subscription's and shared cost's split adds to 100%",
                    "ok": not items_off, "detail": [n_ for _, n_ in items_off][:5]})
        ads_total = sum(self.ads.values())
        out.append({"key": "ads", "label": "The ad split adds to 100%", "ok": abs(ads_total - 1) < 1e-4})
        return out

    def _bs_rank(self, a: str) -> int:
        """Where a balance-sheet account sits in an entry: what's owed between companies first, then
        the timing accounts, then the partners', then equity. The file can place an account the words
        below wouldn't (`accounts.bs_rank`)."""
        own = (self.acc.get("bs_rank") or {}).get(a)
        if own is not None:
            return int(own)
        if a == self.d["billback"]["account"]:
            return 0
        return _bs_rank(a)


def _bs_rank(a: str) -> int:
    a_ = a.lower()
    if "due to" in a_ or "due from" in a_ or "transfer account" in a_:
        return 0
    if any(w in a_ for w in ("deferred", "accrued revenue", "receivable")):
        return 1
    if any(w in a_ for w in ("prepaid", "accrued liabilities")):
        return 2
    if any(w in a_ for w in ("profit share", "advances to", "payable")):
        return 3
    if any(w in a_ for w in ("owner", "contribution", "capital", "retained", "cash to accrual", "investment",
                             "distribution", "loan")):
        return 4
    return 5
