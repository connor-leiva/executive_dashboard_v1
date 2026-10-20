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
}
HOLDCO_CHOICES = ("Investment (equity)", "Loan")
BC_BASES = ("rebuilt", "old_books", "received")


def _r(x: float) -> float:
    return round(x + 0.0, 2)


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
    raise InputError(f"Nothing to change at {'/'.join(path)}.")


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
        for l in self.d["ledger"]:
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

    # ---- the corrected P&L, by company, line and month (and by brand, for the brand views)
    def _corrected(self):
        corr = defaultdict(float)            # (company, line, m)
        by_brand = defaultdict(float)        # (brand, line, m): no timing, no partner share
        self.dues_dep = defaultdict(float)
        dc, dl = self.dues["company"], self.dues["line"]
        for l in self.d["ledger"]:
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
        return {"key": key, "payee": (t.get("who") or "") if payee is None else payee, "short": t.get("short") or "",
                "what": t.get("what") or "", "note": t.get("note") or "", "changed": t.get("changed") or "",
                "status": t.get("status") or "", "twins": t.get("twins") or [], "paid_from": t.get("paid_from") or "",
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
        for l in self.d["ledger"]:
            if (l["cls"] in ("revenue", "cost") and l["brand"] in brands and l["line"] == line
                    and l["month"] in ms and not (self.bc[l["brand"]] == dc and line == dl and kind != "brand")):
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
        lines = defaultdict(list)
        for l in self.d["ledger"]:
            lines[l["key"]].append(l)
        out = []
        for key, t in self.tx.items():
            ls = lines.get(key, [])
            place, shared = defaultdict(float), defaultdict(float)
            for l in ls:
                if l["cls"] not in ("revenue", "cost") or not l.get("line"):
                    continue
                if l["brand"] in self.bc:
                    place[(l["brand"], l["line"])] += l["amount"]
                else:
                    shared[l["line"]] += l["amount"]
            w = self.tx_words(key)
            date = w["tx_date"] or (ls[0]["date"] if ls else "")
            amount = w["tx_amount"]
            if amount is None:          # an older file: the P&L lines, money in positive
                amount = _r(sum(l["amount"] if l["cls"] == "revenue" else -l["amount"] for l in ls
                                if l["cls"] in ("revenue", "cost")))
            out.append({**w, "date": date, "month": int(date[5:7]) if date else None,
                        "company": t.get("company") or (ls[0]["company"] if ls else ""), "amount": amount,
                        "money_in": w["money_in"] if w["money_in"] is not None else amount > 0,
                        "placement": [{"brand": b, "line": ln, "amount": _r(v)}
                                      for (b, ln), v in place.items() if abs(v) >= 0.005]
                                     + [{"brand": None, "line": ln, "amount": _r(v)}
                                        for ln, v in shared.items() if abs(v) >= 0.005]})
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
                cls_, typ = (types.get(a) or new_types.get(a) or ["Liability", "Other Current Liability"])
            sec, note = cls_ if cls_ in sections else "Liability", ""
            rc = (self.acc.get("reclass") or {}).get(a)
            if "due from" in a.lower() and cls_ != "Asset" and (a not in tb or tb[a]["balance"] >= 0):
                sec, note = "Asset", f"Set up as {typ}, but it's owed to {company}: make it Other Current Asset."
            elif rc and cls_ == rc.get("when_class", cls_):
                sec, note = rc["section"], rc["note"]   # the file's own reading of an account set up wrong
            elif a not in tb and (self.acc["acct_new"].get(f"{company}|{a}") or a in new_types):
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
        L, b_ = self.d["ledger"], self.brands
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
