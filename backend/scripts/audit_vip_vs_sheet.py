r"""Reconcile the manual VIP tracking sheet against GHL, person by person.

The dashboard agrees with GHL exactly, so any discrepancy with the sheet is a discrepancy
between the sheet and GHL - not a counting bug. This says which people, and WHY each one is
missing, because the fixes are different:

  not in GHL at all        somebody was added to the sheet and never to the CRM
  in GHL, no event tag     in the CRM but never tagged for this event
  in GHL, a DIFFERENT tag  tagged for the wrong event, or cancelled and not removed
  business partner         a second body on one sheet row, who may have no contact of their own

Matches on email first, then on a normalised name, because sheets have typos and GHL has
duplicates. READ-ONLY against GHL and the sheet.

    .venv\Scripts\python.exe scripts\audit_vip_vs_sheet.py "C:\path\to\sheet.xlsx"
"""
import asyncio
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

for line in open(os.path.join(HERE, ".probe.env"), encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)

import openpyxl                                                    # noqa: E402
from app.integrations.ghl import get_contacts                      # noqa: E402

TOK, LOC = os.environ["GHL_TOKEN"], os.environ["GHL_LOCATION_ID"]
GUEST_TAG = "the forum q4 2026 guest rsvp"


def nname(s):
    return re.sub(r"[^a-z]", "", (s or "").lower())


def read_sheet(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    def cell(r, i):
        return (str(r[i]).strip() if i < len(r) and r[i] is not None else "")
    vip, partners, cancelled = [], [], []
    for r in wb["VIP GUESTS"].iter_rows(min_row=2, values_only=True):
        if cell(r, 0):
            vip.append({"name": cell(r, 0), "email": cell(r, 1).lower()})
        if cell(r, 5):
            partners.append({"name": cell(r, 5), "email": cell(r, 6).lower(),
                             "of": cell(r, 0)})
    for r in wb["Canceled"].iter_rows(min_row=3, values_only=True):
        n = cell(r, 1)
        if n and n.lower() not in ("name", "members:"):
            cancelled.append({"name": n, "email": cell(r, 2).lower()})
    return vip, partners, cancelled


async def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path or not os.path.exists(path):
        print("usage: audit_vip_vs_sheet.py <sheet.xlsx>")
        return 1
    vip, partners, cancelled = read_sheet(path)
    contacts = await get_contacts(TOK, LOC)

    by_email, by_name = {}, {}
    for c in contacts:
        e = (c.get("email") or "").strip().lower()
        if e:
            by_email.setdefault(e, c)
        nm = nname(f"{c.get('firstName') or ''}{c.get('lastName') or ''}") or nname(c.get("contactName") or "")
        if nm:
            by_name.setdefault(nm, c)

    tagged = {c.get("id") for c in contacts
              if any(GUEST_TAG in str(t).strip().lower() for t in (c.get("tags") or []))}
    cancelled_names = {nname(x["name"]) for x in cancelled}

    print(f"SHEET: {len(vip)} primary VIPs, {len(partners)} business partners, "
          f"{len(cancelled)} cancelled")
    print(f"GHL  : {len(tagged)} contacts carry {GUEST_TAG!r}\n")

    buckets = {"tagged": [], "no_tag": [], "other_tag": [], "absent": [], "on_cancel_sheet": []}
    for p in vip:
        c = by_email.get(p["email"]) if p["email"] else None
        if c is None:
            c = by_name.get(nname(p["name"]))
        if c is None:
            buckets["absent"].append((p, None))
            continue
        tags = [str(t).strip().lower() for t in (c.get("tags") or [])]
        if c.get("id") in tagged:
            buckets["tagged"].append((p, c))
        elif nname(p["name"]) in cancelled_names:
            buckets["on_cancel_sheet"].append((p, c))
        else:
            ev = [t for t in tags if "rsvp" in t or "vip" in t or "guest" in t]
            buckets["other_tag" if ev else "no_tag"].append((p, c, ev))

    print(f"[{len(buckets['tagged'])}] ON THE SHEET AND TAGGED - these are the ones that agree\n")

    def show(key, title, why):
        rows = buckets[key]
        if not rows:
            return
        print(f"[{len(rows)}] {title}")
        print(f"      {why}")
        for item in rows:
            p = item[0]
            extra = ""
            if key == "other_tag" and len(item) > 2:
                extra = "  tags: " + ", ".join(item[2][:3])
            print(f"      - {p['name']:<26} {p['email'] or '(no email on sheet)'}{extra}")
        print()

    show("absent", "ON THE SHEET, NOT IN GHL AT ALL",
         "added to the sheet and never to the CRM - the dashboard cannot see them")
    show("no_tag", "IN GHL, NO EVENT TAG",
         "the contact exists but was never tagged for this event")
    show("other_tag", "IN GHL, TAGGED FOR SOMETHING ELSE",
         "tagged, but not with this event's guest tag")
    show("on_cancel_sheet", "ON BOTH THE VIP AND CANCELLED SHEETS",
         "listed as attending and as cancelled - the sheet disagrees with itself")

    sheet_names = {nname(p["name"]) for p in vip} | {nname(p["name"]) for p in partners}
    extra = [c for c in contacts if c.get("id") in tagged
             and nname(f"{c.get('firstName') or ''}{c.get('lastName') or ''}") not in sheet_names]
    if extra:
        print(f"[{len(extra)}] TAGGED IN GHL, NOT ON THE SHEET")
        print("      the dashboard counts them; the sheet does not")
        for c in extra:
            print(f"      - {(c.get('firstName') or '') + ' ' + (c.get('lastName') or ''):<26} "
                  f"{c.get('email') or ''}")
        print()

    if partners:
        print(f"[{len(partners)}] BUSINESS PARTNERS (a second body on one sheet row)")
        print("      the sheet counts bodies in the room; the tag counts contacts")
        for p in partners:
            c = by_email.get(p["email"]) if p["email"] else by_name.get(nname(p["name"]))
            where = "tagged" if (c and c.get("id") in tagged) else (
                "in GHL, untagged" if c else "not in GHL")
            print(f"      - {p['name']:<26} (with {p['of']}) - {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
