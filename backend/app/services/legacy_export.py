"""Delta CSV for GHL import — turn the synced legacy-Stripe Forum charges into a
ready-to-upload GHL Transactions CSV, in the EXACT format that imported cleanly:
17 template columns, no BOM, and a LEADING SPACE on the Date + Time values only
(that space is what makes GHL treat them as `general`, not text, so it stops
re-parsing/mangling the zero-padded DD/MM/YYYY). Times are h:mm AM/PM, no seconds.

GHL has no transaction-write API, so this only GENERATES the file; the human uploads
it. A per-integration date watermark (config['delta_through']) makes every download
net-new — charges strictly after the watermark — so re-imports never duplicate.
Pure + testable: no I/O, no DB.
"""
from __future__ import annotations

import csv
import datetime as dt
import io

# The GHL import template header — leading spaces preserved exactly as GHL ships it.
TEMPLATE = ["Customer First Name", "Customer Last Name", "Customer email", "Customer Phone",
            "Currency", " Late Fees", " Tip Amount", " Amount Paid", " Transaction Date",
            " Transaction Time", " Payment Method", " Payment Provider", " Address Line 1",
            " City", " State", " Country", " Postal Code"]


def _split_name(name: str | None) -> tuple[str, str]:
    parts = (name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _fmt_amount(amt) -> str:
    try:
        v = float(amt or 0)
    except (TypeError, ValueError):
        v = 0.0
    return f"{v:.2f}".rstrip("0").rstrip(".") or "0"


def fmt_date(d: dt.date) -> str:
    """' DD/MM/YYYY' — leading space + zero-padded, the format GHL accepts."""
    return d.strftime(" %d/%m/%Y")


def fmt_time(charged_at: str | None) -> str:
    """' hh:mm AM/PM' — leading space, 2-digit hour, NO seconds. Defaults to noon
    when the charge carries no timestamp (cosmetic on a transaction record)."""
    if charged_at:
        try:
            t = dt.datetime.fromisoformat(charged_at)
            return t.strftime(" %I:%M %p")
        except (ValueError, TypeError):
            pass
    return " 12:00 PM"


def payment_row(rec) -> list[str]:
    """One synced legacy-Stripe payment record → the 17 GHL template columns."""
    meta = getattr(rec, "meta", None) or {}
    contact = meta.get("contact") or {}
    first, last = _split_name(getattr(rec, "name", None))
    return [
        first, last, (getattr(rec, "email", None) or ""), (contact.get("phone") or ""),
        (meta.get("currency") or "usd").upper(), "0", "0", _fmt_amount(getattr(rec, "amount", 0)),
        fmt_date(rec.occurred_on), fmt_time(meta.get("charged_at")), "Card", "Stripe",
        (contact.get("address_line1") or ""), (contact.get("city") or ""),
        (contact.get("state") or ""), (contact.get("country") or ""), (contact.get("postal_code") or ""),
    ]


def pending(records, through: dt.date | None):
    """The net-new, uploadable subset: succeeded charges dated strictly after the
    watermark (GHL uploads are always 'succeeded'; refunds/failures don't upload).
    Sorted oldest-first. Returns the list of records."""
    out = [r for r in records
           if (getattr(r, "status", "") == "succeeded") and getattr(r, "occurred_on", None)
           and (through is None or r.occurred_on > through)]
    out.sort(key=lambda r: (r.occurred_on, getattr(r, "name", "") or ""))
    return out


def build_csv(records, through: dt.date | None) -> tuple[str, int, dt.date | None]:
    """Render the delta CSV text. Returns (csv_text, row_count, new_watermark) where
    new_watermark is the max date written (advance the watermark to it after the user
    confirms the upload). csv_text is empty-header-only when nothing is pending."""
    rows = pending(records, through)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(TEMPLATE)
    for r in rows:
        w.writerow(payment_row(r))
    new_wm = max((r.occurred_on for r in rows), default=None)
    return buf.getvalue(), len(rows), new_wm
