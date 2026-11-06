"""One document covering several reps, for the agency that invoices Spring for them.

The per-rep statements are what each contractor gets. This is what Empire Builders needs: a
single page saying how much is owed in total and how it divides between their people, with
every per-rep figure traceable to the statement that rep was sent.

    python scripts/commission_remittance.py --payload commission_out/commission-2026-10-jovanka.json \
        --payload commission_out/commission-2026-08-brianna.json ...

    # or, the usual case: every statement for a period, minus the reps who are not theirs
    python scripts/commission_remittance.py --period 2026-10 --exclude aimee --also 2026-08-brianna

DELIBERATELY NOT CALLED AN INVOICE. Empire invoices Spring, not the other way round, so a
document from Spring headed "Invoice" would be the wrong instrument and could be paid twice --
once against this and once against theirs. It is a remittance summary: the basis for their
invoice and the record of what was agreed.

The reference carries an -EB suffix for the same reason. close_month() owns `BC-COMM-<period>`
for the Payables bill and Payables rejects a repeated invoice number per vendor, so reusing
that number on a document sent outside would either collide with the real bill or quietly
become it.

Figures are never recomputed from the database here. Every number is read from the statement
payloads, run through the renderer's own recompute() so that a reviewed exclusion is honoured
exactly as it is on the rep's page, and then asserted to tie: the total must equal the sum of
the per-rep figures, and each per-rep figure must equal the sum of its own printed lines. A
summary that disagrees with the statements behind it is worse than no summary, because it is
the one a payment is made from.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph,
                                Spacer)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from commission_render import (BODY, CELL, CELLH, CHECK, H1, H2, META, _PAGES,  # noqa: E402
                               _TITLE, _footer, _grid, caveats, check_table, esc,
                               period_label, rate_label, recompute, usd)

AGENCY = "Empire Builders"
PROGRAMME = "beCollective"


def load(paths: list[pathlib.Path]) -> list[dict]:
    """Each payload, with the renderer's own recompute applied.

    recompute(), not the payload's stored totals: it is what honours `excluded` and what
    refuses a commission figure that contradicts its own row. Reading the stored sums instead
    would let this page state a number the rep's own statement does not.
    """
    out = []
    for p in paths:
        if not p.exists():
            sys.exit(f"No such payload: {p}")
        doc = recompute(json.loads(p.read_text(encoding="utf-8")))
        doc["_source"] = p.name
        out.append(doc)
    return out


def lines_for(doc: dict) -> list[dict]:
    """Every client line on one statement, flattened, with its cohort and month."""
    out = []
    for r in doc["cohort1"]["rows"]:
        out.append({"member": r["member"], "cohort": "First cohort",
                    "paid": r.get("paid") or doc["period"], "cash": r["cash"],
                    "commission": r["commission"]})
    for r in doc["_c2_billable_rows"]:
        out.append({"member": r["member"], "cohort": "August 2026 cohort",
                    "paid": r.get("paid") or doc["period"], "cash": r["cash"],
                    "commission": r["commission"]})
    return out


def collate(docs: list[dict]) -> dict:
    """Group the payloads by rep, keeping each statement separable.

    A rep can appear more than once -- Brianna Wood has three statements, because three months
    of her cash had never been invoiced. They stay as separate rows under her name rather than
    being merged into one figure, so the page shows WHICH months are being settled. Merging
    them is how an arrears month silently becomes part of a routine one.
    """
    reps: dict[str, dict] = {}
    for d in docs:
        key = (d["payee"]["email"] or d["payee"]["name"]).lower()
        rep = reps.setdefault(key, {"name": d["payee"]["name"], "email": d["payee"]["email"],
                                    "rate": d.get("rate_pct", 0.10), "statements": []})
        if abs(rep["rate"] - d.get("rate_pct", 0.10)) > 1e-9:
            sys.exit(f"{rep['name']} appears with two different rates "
                     f"({rate_label(rep['rate'])} and {rate_label(d.get('rate_pct'))}). "
                     f"Resolve it before producing a document someone is paid from.")
        lines = lines_for(d)
        rep["statements"].append({
            "period": d["period"], "label": period_label(d["period"]),
            "cash": d["_billable"], "due": d["_due"], "lines": lines,
            # include_scope=False: scope notes name other reps and their row counts, which
            # has no place on a document going to the agency. See exceptions() in the gather.
            "caveats": caveats(d, include_scope=False), "source": d["_source"],
        })

    # Every figure on this page must be the one on the rep's own statement.
    for rep in reps.values():
        for st in rep["statements"]:
            line_cash = round(sum(x["cash"] for x in st["lines"]), 2)
            line_due = round(sum(x["commission"] for x in st["lines"]), 2)
            if line_cash != st["cash"] or line_due != st["due"]:
                sys.exit(f"{rep['name']} {st['label']}: the statement totals "
                         f"({usd(st['cash'])} / {usd(st['due'])}) do not equal the sum of its "
                         f"own lines ({usd(line_cash)} / {usd(line_due)}). Refusing to "
                         f"summarise a statement that does not tie. Source: {st['source']}")
        rep["cash"] = round(sum(s["cash"] for s in rep["statements"]), 2)
        rep["due"] = round(sum(s["due"] for s in rep["statements"]), 2)

    ordered = sorted(reps.values(), key=lambda r: -r["due"])
    total_cash = round(sum(r["cash"] for r in ordered), 2)
    total_due = round(sum(r["due"] for r in ordered), 2)
    periods = sorted({s["period"] for r in ordered for s in r["statements"]})
    return {"reps": ordered, "total_cash": total_cash, "total_due": total_due,
            "periods": periods}


def summary_table(reps, total_cash, total_due):
    head = ["Contractor", "Periods settled", "Clients", "Cash collected", "Commission due"]
    data = [[Paragraph(h, CELLH) for h in head]]
    for r in reps:
        months = ", ".join(s["label"].split()[0] for s in
                           sorted(r["statements"], key=lambda s: s["period"]))
        clients = sum(len(s["lines"]) for s in r["statements"])
        data.append([Paragraph(f"{esc(r['name'])}<br/>{esc(r['email'])}", CELL),
                     Paragraph(esc(months), CELL),
                     Paragraph(str(clients), CELL),
                     Paragraph(usd(r["cash"]), CELL),
                     Paragraph(usd(r["due"]), CELL)])
    data.append([Paragraph("Total", CELLH), Paragraph("", CELL), Paragraph("", CELL),
                 Paragraph(usd(total_cash), CELLH), Paragraph(usd(total_due), CELLH)])
    return _grid(data, [2.2 * inch, 1.5 * inch, 0.7 * inch, 1.2 * inch, 1.2 * inch],
                 bold_rows=(len(data) - 1,))


def rep_detail(rep):
    head = ["Client", "Programme", "Paid on", "Cash collected",
            f"Commission ({rate_label(rep['rate'])})"]
    data = [[Paragraph(h, CELLH) for h in head]]
    for st in sorted(rep["statements"], key=lambda s: s["period"]):
        for x in st["lines"]:
            data.append([Paragraph(esc(x["member"]), CELL), Paragraph(esc(x["cohort"]), CELL),
                         Paragraph(esc(x["paid"]), CELL), Paragraph(usd(x["cash"]), CELL),
                         Paragraph(usd(x["commission"]), CELL)])
    data.append([Paragraph(f"{esc(rep['name'])} total", CELLH), Paragraph("", CELL),
                 Paragraph("", CELL), Paragraph(usd(rep["cash"]), CELLH),
                 Paragraph(usd(rep["due"]), CELLH)])
    return _grid(data, [1.9 * inch, 1.5 * inch, 0.95 * inch, 1.15 * inch, 1.3 * inch],
                 bold_rows=(len(data) - 1,))


def build(c: dict, out: pathlib.Path, reference: str, prepared: str,
          show_caveats: bool = False):
    label = (period_label(c["periods"][-1]) if c["periods"] else "")
    _TITLE[0] = f"Commission Remittance — {PROGRAMME} — {label} — {reference}"

    span = ""
    if len(c["periods"]) > 1:
        span = (f" It also settles earlier months that had never been invoiced: "
                f"{', '.join(period_label(p) for p in c['periods'][:-1])}.")

    def story():
        s = [
            Paragraph(f"Commission Remittance — {PROGRAMME} —<br/>{label}", H1),
            Paragraph(f"{prepared}  ·  Reference {reference}  ·  For {AGENCY}", META),
            Paragraph("Summary", H2),
            Paragraph(
                f"Commission owed on {PROGRAMME} cash collected, for the {len(c['reps'])} "
                f"{AGENCY} contractors below: <b>{usd(c['total_due'])}</b>.{span}", BODY),
            Spacer(1, 6),
            summary_table(c["reps"], c["total_cash"], c["total_due"]),
            Paragraph("What this document is", H2),
            Paragraph(
                f"This is a remittance summary, not an invoice: {AGENCY} invoices Spring, and "
                f"a document headed Invoice from this side could be paid twice. It is the "
                f"basis for that invoice and the record of what was agreed. Each contractor "
                f"has also been sent their own statement showing the same figures client by "
                f"client; the per-contractor totals here are read from those statements, not "
                f"recalculated, and this document refuses to print if any of them disagrees "
                f"with the lines beneath it.", BODY),
            Paragraph(
                f"Commission is earned on cash that ARRIVED in the period, never on contracts "
                f"signed. A member on a payment plan therefore appears in every month they "
                f"keep paying.", BODY),
        ]
        for rep in c["reps"]:
            months = ", ".join(s["label"] for s in
                               sorted(rep["statements"], key=lambda x: x["period"]))
            s.append(KeepTogether([
                Paragraph(f"{rep['name']} — {usd(rep['due'])}", H2),
                Paragraph(f"{months}. {esc(rep['email'])}", BODY),
                Spacer(1, 4),
                rep_detail(rep)]))

        # Not printed. See commission_render.build() for why; the same applies with more
        # force here, because this page leaves the building.
        if show_caveats:
            checks = []
            for rep in c["reps"]:
                seen = set()
                for st in rep["statements"]:
                    for n in st["caveats"]:
                        if n in seen:
                            continue      # the same caveat on each of a rep's three months
                        seen.add(n)
                        checks.append(f"{rep['name']}: {n}")
            if checks:
                s.append(KeepTogether([
                    Paragraph("Before this is paid", H2),
                    Paragraph("Carried from the individual statements, unedited. Each line "
                              "needs a person to confirm it.", BODY),
                    Spacer(1, 5),
                    check_table(checks)]))

        s.append(KeepTogether([
            Paragraph("Approval", H2),
            Paragraph("Approved by: Connor Leiva, CFO", BODY),
            Spacer(1, 2),
            Paragraph("Date: ______________________", BODY)]))
        return s

    def render():
        d = BaseDocTemplate(str(out), pagesize=letter,
                            leftMargin=0.9 * inch, rightMargin=0.9 * inch,
                            topMargin=0.85 * inch, bottomMargin=0.95 * inch,
                            title=_TITLE[0], author="Axcion")
        d.addPageTemplates([PageTemplate(
            id="main",
            frames=[Frame(d.leftMargin, d.bottomMargin, d.width, d.height, id="f")],
            onPage=_footer)])
        d.build(story())
        return d.page

    _PAGES[0] = 0
    _PAGES[0] = render()
    render()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", action="append", default=[], type=pathlib.Path,
                    help="a statement payload; repeatable")
    ap.add_argument("--period", default=None,
                    help="instead of --payload: take every commission-<period>-*.json")
    ap.add_argument("--dir", default="commission_out", type=pathlib.Path)
    ap.add_argument("--exclude", action="append", default=[],
                    help="rep slug to leave out when using --period, e.g. aimee")
    ap.add_argument("--also", action="append", default=[],
                    help="extra <period>-<slug> to include, e.g. 2026-08-brianna")
    ap.add_argument("--reference", default=None,
                    help="document reference; defaults to BC-COMM-<period>-EB")
    ap.add_argument("--caveats", action="store_true",
                    help="print the 'Before this is paid' checks. Off by default: this page "
                         "goes to the agency.")
    ap.add_argument("--out", default=None, type=pathlib.Path)
    ap.add_argument("--prepared", default=None)
    a = ap.parse_args()

    paths = list(a.payload)
    if a.period:
        for p in sorted(a.dir.glob(f"commission-{a.period}-*.json")):
            slug = p.stem.split("-")[-1]
            if slug in a.exclude or "SUPERSEDED" in p.name or p.name.endswith(".reviewed.json"):
                continue
            paths.append(p)
    for extra in a.also:
        p = a.dir / f"commission-{extra}.json"
        if p not in paths:
            paths.append(p)
    if not paths:
        sys.exit("Nothing to summarise. Pass --payload or --period.")

    docs = load(paths)
    c = collate(docs)

    period = c["periods"][-1] if c["periods"] else "unknown"
    reference = a.reference or f"BC-COMM-{period}-EB"
    out = a.out or (a.dir / f"Commission Remittance - {AGENCY} - "
                            f"{period_label(period)} - {reference}.pdf")
    try:
        prepared = a.prepared or dt.date.today().strftime("%b %d, %Y").replace(" 0", " ")
    except Exception:                                        # noqa: BLE001
        prepared = a.prepared or str(dt.date.today())

    build(c, out, reference, prepared, show_caveats=a.caveats)

    print(f"wrote {out}")
    print(f"  reference {reference}   periods {', '.join(c['periods'])}")
    for rep in c["reps"]:
        months = "+".join(s["period"] for s in sorted(rep["statements"],
                                                      key=lambda x: x["period"]))
        print(f"  {rep['name']:<18} {months:<26} {usd(rep['cash']):>11} -> {usd(rep['due']):>9}")
    print(f"  {'TOTAL':<18} {'':<26} {usd(c['total_cash']):>11} -> {usd(c['total_due']):>9}")
    print(f"  from: {', '.join(d['_source'] for d in docs)}")


if __name__ == "__main__":
    main()
