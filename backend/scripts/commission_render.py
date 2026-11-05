"""Render a REVIEWED gather into the statement PDF.

    python scripts/commission_render.py commission_out/commission-2026-09-aimee.json

Reads the JSON `commission_gather.py` wrote, AFTER a person has been through it. It invents
nothing: every figure on the page is a figure in that file, and the two things that make a
statement different from a database dump are fields in the file rather than judgments made
here.

    "excluded": true, "reason": "..."   the row is not this rep's; it leaves the document
                                        entirely and the reason becomes a note
    "already_billed": true              the cash landed this period but was paid on an earlier
                                        statement; listed so the period reconciles, no
                                        commission

Last month those two fields were worth the difference between $3,325 and $1,430 on one
statement. They are why gather does not render.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph,
                                Spacer, Table, TableStyle)

INK = colors.HexColor("#1F2328")
MUTED = colors.HexColor("#6B7280")
LINE = colors.HexColor("#E5E7EB")
HEADBG = colors.HexColor("#F6F7F8")
FLAG = colors.HexColor("#FDF6EC")

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=19,
                    leading=24, alignment=TA_LEFT, textColor=INK, spaceAfter=2)
META = ParagraphStyle("META", parent=ss["Normal"], fontName="Helvetica", fontSize=9,
                      textColor=MUTED, spaceAfter=14)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=12.5,
                    leading=15, textColor=INK, spaceBefore=10, spaceAfter=5)
BODY = ParagraphStyle("BODY", parent=ss["Normal"], fontName="Helvetica", fontSize=10,
                      leading=14.5, textColor=INK)
CELL = ParagraphStyle("CELL", parent=ss["Normal"], fontName="Helvetica", fontSize=8.4,
                      leading=11, textColor=INK)
CELLH = ParagraphStyle("CELLH", parent=CELL, fontName="Helvetica-Bold", textColor=INK)
# Caveats are set at body size on the flag background, NOT shrunk into a footnote. Every one
# of them is a reason somebody might be paid the wrong amount.
CHECK = ParagraphStyle("CHECK", parent=ss["Normal"], fontName="Helvetica", fontSize=9.4,
                       leading=13, textColor=INK)

_TITLE = [""]
_PAGES = [0]


def usd(v) -> str:
    return f"${float(v):,.2f}"


def period_label(period: str) -> str:
    y, m = period.split("-")
    return f"{MONTHS[int(m) - 1]} {y}"


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.6)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.9 * inch, 0.55 * inch, _TITLE[0])
    total = f" of {_PAGES[0]}" if _PAGES[0] else ""
    canvas.drawRightString(letter[0] - 0.9 * inch, 0.55 * inch, f"Page {doc.page}{total}")
    canvas.setStrokeColor(LINE)
    canvas.line(0.9 * inch, 0.72 * inch, letter[0] - 0.9 * inch, 0.72 * inch)
    canvas.restoreState()


def _grid(data, widths, flag_rows=(), bold_rows=()):
    t = Table(data, colWidths=widths, hAlign="LEFT")
    style = [("GRID", (0, 0), (-1, -1), 0.5, LINE),
             ("BACKGROUND", (0, 0), (-1, 0), HEADBG),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("TOPPADDING", (0, 0), (-1, -1), 4.5),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
             ("LEFTPADDING", (0, 0), (-1, -1), 6),
             ("RIGHTPADDING", (0, 0), (-1, -1), 6)]
    for r in flag_rows:
        style.append(("BACKGROUND", (0, r), (-1, r), FLAG))
    for r in bold_rows:
        style.append(("BACKGROUND", (0, r), (-1, r), HEADBG))
    t.setStyle(TableStyle(style))
    return t


def kv_table(rows, flag_rows=(), bold_rows=()):
    data = [[Paragraph(a, CELLH if i == 0 else CELL), Paragraph(b, CELLH if i == 0 else CELL)]
            for i, (a, b) in enumerate(rows)]
    return _grid(data, [3.3 * inch, 3.4 * inch], flag_rows, bold_rows)


def cohort2_table(rows, subtotal_cash, subtotal_due, billed_label="—  billed earlier"):
    """Stage, and the contract columns only when the data actually carries them.

    A Contract value column of dashes on a pay document invites somebody to fill it in from
    memory. It appears when the figure is real and is absent when it is not."""
    money_cols = any(r.get("contract") for r in rows)
    head = ["Client", "Stage", "Payment plan"]
    if money_cols:
        head += ["Contract value"]
    head += ["Cash collected"]
    if money_cols:
        head += ["Remaining balance"]
    head += ["Commission (10%)"]
    data = [[Paragraph(h, CELLH) for h in head]]
    for r in rows:
        due = (usd(r["commission"]) if r.get("commission") is not None else billed_label)
        row = [Paragraph(r["member"] or "(no name)", CELL),
               Paragraph(r.get("stage") or "—", CELL),
               Paragraph(r.get("plan") or "—", CELL)]
        if money_cols:
            row.append(Paragraph(usd(r["contract"]) if r.get("contract") else "—", CELL))
        row.append(Paragraph(usd(r["cash"]), CELL))
        if money_cols:
            row.append(Paragraph(usd(r["remaining"]) if r.get("remaining") is not None
                                 else "—", CELL))
        row.append(Paragraph(due, CELL))
        data.append(row)
    sub = [Paragraph("Subtotal", CELLH)] + [Paragraph("", CELL)] * (2 + (1 if money_cols else 0))
    sub.append(Paragraph(usd(subtotal_cash), CELLH))
    if money_cols:
        sub.append(Paragraph("", CELL))
    sub.append(Paragraph(usd(subtotal_due) if subtotal_due is not None else "—", CELLH))
    data.append(sub)
    widths = ([1.7, 1.5, 0.95, 1.0, 1.0, 1.05, 1.0] if money_cols
              else [2.2, 1.8, 1.0, 1.1, 1.1])
    return _grid(data, [w * inch for w in widths], bold_rows=(len(data) - 1,))


def cohort1_table(rows, cash, due):
    head = ["Client", "Payment plan", "Instalment", "Commission (10%)"]
    data = [[Paragraph(h, CELLH) for h in head]]
    for r in rows:
        data.append([Paragraph(r["member"], CELL), Paragraph(r.get("plan") or "—", CELL),
                     Paragraph(usd(r["cash"]), CELL), Paragraph(usd(r["commission"]), CELL)])
    data.append([Paragraph("Subtotal", CELLH), Paragraph("", CELL),
                 Paragraph(usd(cash), CELLH), Paragraph(usd(due), CELLH)])
    return _grid(data, [2.5 * inch, 1.4 * inch, 1.4 * inch, 1.4 * inch],
                 bold_rows=(len(data) - 1,))


def grand_total(doc, label):
    rows = [[Paragraph("Programme", CELLH), Paragraph("Cash billable", CELLH),
             Paragraph("Commission due", CELLH)]]
    if doc["cohort1"]["rows"]:
        rows.append([Paragraph(f"First cohort — {label} instalments", CELL),
                     Paragraph(usd(doc["cohort1"]["cash"]), CELL),
                     Paragraph(usd(doc["cohort1"]["commission"]), CELL)])
    rows.append([Paragraph("August 2026 cohort — billed this period", CELL),
                 Paragraph(usd(doc["_c2_billable"]), CELL),
                 Paragraph(usd(doc["_c2_due"]), CELL)])
    rows.append([Paragraph("Total due", CELLH), Paragraph(usd(doc["_billable"]), CELLH),
                 Paragraph(usd(doc["_due"]), CELLH)])
    return _grid(rows, [3.3 * inch, 1.7 * inch, 1.7 * inch], bold_rows=(len(rows) - 1,))


def caveats(doc: dict) -> list[str]:
    """Every caveat in the payload, in the order a reader needs them.

    Deliberately reads all three lists the gather writes. They were added at different times
    and nothing kept them in step with this file -- the renderer knew about one of the three,
    so a caveat landing in either of the others was computed, logged and silently dropped.
    Asking the payload what it has, rather than naming the keys this file happens to remember,
    is what stops the next list going the same way.
    """
    out, seen = [], set()
    buckets = [doc.get("exceptions"),
               (doc.get("cohort1") or {}).get("notes"),
               ((doc.get("cohort1") or {}).get("meta") or {}).get("notes"),
               (doc.get("cohort2") or {}).get("notes")]
    for bucket in buckets:
        for n in (bucket or []):
            n = str(n).strip()
            if n and n not in seen:
                seen.add(n)
                out.append(n)
    return out


def check_table(items):
    data = [[Paragraph(f"{i}.", CHECK), Paragraph(n, CHECK)]
            for i, n in enumerate(items, 1)]
    tbl = Table(data, colWidths=[0.28 * inch, None], hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), FLAG),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return tbl


def recompute(doc: dict) -> dict:
    """Totals from the rows that survive review. Never trusts the gather's own sums: a person
    has edited the file since, and the whole point of the edit is that it changes them."""
    c2 = [r for r in doc["cohort2"]["rows"] if not r.get("excluded")]
    billable = [r for r in c2 if not r.get("already_billed")]
    prior = [r for r in c2 if r.get("already_billed")]
    rate = doc.get("rate_pct", 0.10)

    doc["_c2_billable_rows"] = billable
    doc["_c2_prior_rows"] = prior
    doc["_c2_billable"] = round(sum(r["cash"] for r in billable), 2)
    doc["_c2_prior"] = round(sum(r["cash"] for r in prior), 2)
    for r in billable:
        if r.get("commission") is None:
            r["commission"] = round(r["cash"] * rate, 2)
    for r in prior:
        r["commission"] = None
    doc["_c2_due"] = round(sum(r["commission"] for r in billable), 2)

    c1 = doc["cohort1"]
    doc["_billable"] = round(c1["cash"] + doc["_c2_billable"], 2)
    doc["_due"] = round(c1["commission"] + doc["_c2_due"], 2)
    doc["_cash_total"] = round(c1["cash"] + doc["_c2_billable"] + doc["_c2_prior"], 2)
    doc["_clients"] = len(c1["rows"]) + len(c2)
    doc["_billed_clients"] = len(c1["rows"]) + len(billable)
    doc["_excluded"] = [r for r in doc["cohort2"]["rows"] if r.get("excluded")]

    # The arithmetic has to close, or the document is not worth printing.
    assert round(doc["_cash_total"] - doc["_c2_prior"], 2) == doc["_billable"], "cash does not tie"
    return doc


def build(doc: dict, out: pathlib.Path, prepared: str, prior_label: str):
    label = period_label(doc["period"])
    payee, email = doc["payee"]["name"], doc["payee"]["email"]
    _TITLE[0] = f"Commission Statement — {payee} — {label}"

    def story():
        s = [
            Paragraph(f"Commission Statement — {payee} —<br/>{label}", H1),
            Paragraph(f"{prepared}  ·  @Connor", META),
            Paragraph("Summary", H2),
            Paragraph(f"Total commission due to {payee} for {label}: <b>{usd(doc['_due'])}</b>"
                      + (", across both beCollective cohorts." if doc["cohort1"]["rows"]
                         else ", for the August 2026 beCollective cohort."), BODY),
            Spacer(1, 6),
            grand_total(doc, label),
            Paragraph("Statement detail", H2),
        ]
        detail = [("Item", "Detail"),
                  ("Payee", f"{payee} ({email})"),
                  ("Business", "beCollective"),
                  ("Commission period", label),
                  ("Commission basis", f"{int(doc.get('rate_pct', .1) * 100)}% of cash collected"),
                  ("Clients", f"{doc['_clients']} ({doc['_billed_clients']} billed this period)"),
                  ("Total cash collected", usd(doc["_cash_total"]))]
        flag = ()
        if doc["_c2_prior"]:
            detail.append((f"Less: billed on the {prior_label} statement",
                           f"({usd(doc['_c2_prior'])})"))
            flag = (len(detail) - 1,)
        detail += [("Cash collected billable this period", usd(doc["_billable"])),
                   ("Commission due", usd(doc["_due"]))]
        s.append(kv_table(detail, flag_rows=flag, bold_rows=(len(detail) - 1,)))

        if doc["cohort1"]["rows"]:
            s.append(KeepTogether([
                Paragraph(f"First cohort — {label} instalments", H2),
                cohort1_table(doc["cohort1"]["rows"], doc["cohort1"]["cash"],
                              doc["cohort1"]["commission"])]))
        s.append(KeepTogether([
            Paragraph("August 2026 cohort — billed this period", H2),
            cohort2_table(doc["_c2_billable_rows"], doc["_c2_billable"], doc["_c2_due"])]))
        if doc["_c2_prior_rows"]:
            s.append(KeepTogether([
                Paragraph(f"August 2026 cohort — collected in {label.split()[0]}, "
                          f"billed in {prior_label.split()[0]}", H2),
                cohort2_table(doc["_c2_prior_rows"], doc["_c2_prior"], None,
                              billed_label=f"—  billed {prior_label.split()[0][:3]}")]))

        checks = caveats(doc)
        if checks:
            s.append(KeepTogether([
                Paragraph("Before this is paid", H2),
                Paragraph("Each line below needs a person to confirm it. They are computed "
                          "from the data behind this statement, not added by hand.", BODY),
                Spacer(1, 5),
                check_table(checks)]))

        notes = list(doc.get("notes_for_the_statement") or [])
        if doc.get("partial"):
            notes.insert(0, "This statement settles the August 2026 cohort only. The first "
                            "cohort is tracked separately and is not included here; its "
                            "commission for this period is owed under a separate statement.")
        for r in doc["_excluded"]:
            notes.append(f"{r['member']} appeared in the period's data and has been removed. "
                         f"{r.get('reason', 'Not attributable to this rep.')}")
        body = [Paragraph("Notes", H2)]
        for i, n in enumerate(notes, 1):
            body += [Paragraph(f"{i}. {n}", BODY), Spacer(1, 4)]
        s.append(KeepTogether(body))
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
    _PAGES[0] = render()        # first pass counts, second pass prints "of N"
    render()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("payload")
    ap.add_argument("--out", default=None)
    ap.add_argument("--prepared", default=None, help='e.g. "Nov 4, 2026  ·  Revised"')
    ap.add_argument("--prior-label", default="August 2026",
                    help="which earlier statement the carried rows were billed on")
    a = ap.parse_args()

    src = pathlib.Path(a.payload)
    if not src.exists():
        sys.exit(f"No such payload: {src}")
    doc = recompute(json.loads(src.read_text(encoding="utf-8")))

    label = period_label(doc["period"])
    out = pathlib.Path(a.out) if a.out else src.parent / (
        f"Commission Statement - {doc['payee']['name']} - {label}.pdf")
    # No %-d here: it is a glibc extension that raises on Windows, which is where this is
    # run. The portable form strips the leading zero afterwards.
    try:
        prepared = a.prepared or dt.date.today().strftime("%b %d, %Y").replace(" 0", " ")
    except Exception:                                        # noqa: BLE001
        prepared = a.prepared or str(dt.date.today())

    build(doc, out, prepared, a.prior_label)
    print(f"wrote {out}")
    print(f"  clients {doc['_clients']} ({doc['_billed_clients']} billed)")
    print(f"  cash    {usd(doc['_cash_total'])}   less prior {usd(doc['_c2_prior'])}")
    print(f"  billable{usd(doc['_billable']):>14}")
    print(f"  DUE     {usd(doc['_due']):>14}")
    if doc["_excluded"]:
        print(f"  excluded: {[r['member'] for r in doc['_excluded']]}")


if __name__ == "__main__":
    main()
