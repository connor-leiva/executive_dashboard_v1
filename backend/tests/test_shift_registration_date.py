"""A registration carries the day it happened.

WHY THIS EXISTS. Every bc_shift_reg row was written with occurred_on NULL - measured at 0 of
1416 in production by ads_probe. That is not a cosmetic gap:

  * An identity with no registration date has no COHORT day.
  * The cohort day is exactly what AdAttribution.first_seen_on is.
  * So cohort-basis reporting, the maturity curve, and every projection in the ads module had
    no source at all, and would have been built on top of a column that is always null.

Phase 0 exists to find this class of thing before the schema does. It found this one.

THE DATE CHOSEN IS dateAdded, and it is a decision rather than a default. It is when the
IDENTITY first appeared, not when they registered. For somebody already on the list who
registers later it files them in the cohort they actually arrived in - correct for crediting an
ad, and it reads oddly against a launch window. Connor chose it; this test pins it so a later
reader can see the choice was made rather than assumed.
"""
import datetime as dt

import pytest

from app.services import sync


def test_the_parser_accepts_both_shapes_ghl_sends():
    """GHL sends ISO on some payloads and epoch-ms on others; the sync reads createdAt first
    and falls back to dateAdded, matching the opportunity path that already worked."""
    assert sync._parse_ghl_dt("2026-08-14T17:03:00Z") == dt.date(2026, 8, 14)
    assert sync._parse_ghl_dt(1786726980000) == dt.date(2026, 8, 14)   # epoch-ms, same instant
    assert sync._parse_ghl_dt(None) is None
    assert sync._parse_ghl_dt("") is None
    assert sync._parse_ghl_dt("not a date") is None


def test_a_registration_is_dated_from_the_contact():
    """The behaviour itself, exercised the way the sync builds the row: createdAt wins, and
    dateAdded is the fallback rather than an alternative."""
    for contact, expected in [
        ({"createdAt": "2026-08-14T17:03:00Z"}, dt.date(2026, 8, 14)),
        ({"dateAdded": "2026-07-02T09:00:00Z"}, dt.date(2026, 7, 2)),
        ({"createdAt": "2026-08-14T17:03:00Z",
          "dateAdded": "2026-01-01T00:00:00Z"}, dt.date(2026, 8, 14)),   # createdAt wins
        ({}, None),                                                       # neither: stays undated
    ]:
        got = sync._parse_ghl_dt(contact.get("createdAt") or contact.get("dateAdded"))
        assert got == expected, contact


def test_a_contact_with_no_usable_date_stays_undated_rather_than_guessing():
    """Undated is a real state and must survive. A registration dated 'today' because the
    source had nothing would put that identity in whatever cohort the sync happened to run in,
    which moves attributed revenue every time the worker ticks. Counting it and refusing to
    time it is the honest answer, and it is what `dated=False` exists for downstream."""
    assert sync._parse_ghl_dt(None) is None


def test_the_date_is_not_applied_to_the_shared_base_dict():
    """Static guard. `base` is shared by bc_member, bc_registration and bc_shift_reg. dateAdded
    is the registration's date and nobody else's - putting it on `base` would silently redate
    the member roster and the event registrations, which are different questions entirely.

    Asserted by reading the source because the alternative is a live GHL sync."""
    from pathlib import Path

    src = Path(sync.__file__).read_text(encoding="utf-8")
    body = src[src.index("async def sync_becollective_ghl"):]
    reg_block = body[body.index('"kind": "bc_shift_reg"'):]
    assert '"occurred_on": reg_day' in reg_block[:400], (
        "the registration row no longer carries its cohort day")
    base_decl = body[body.index("base = dict("):]
    assert "occurred_on" not in base_decl[:base_decl.index(")")], (
        "occurred_on moved onto the shared base dict - it would redate bc_member and "
        "bc_registration, which do not mean the same thing by a date")
