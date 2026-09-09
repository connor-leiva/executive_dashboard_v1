"""Sections: their labels, their dates, and how long a lesson takes.

ONE PLACE, DELIBERATELY. The console and the portal are two separate React apps, and every value
here is one they must agree on -- a section called "Day 3" in the builder and "Module 3" in the
portal is a bug the author cannot fix. So the label, the duration string, the release date and the
due date are all computed here and shipped on the payload, and neither frontend does the
arithmetic.

THE LABEL IS NOT STORED. It is `grouping_scheme` (on the course) plus `sort` (on the section), so
switching a course from Day to Module renames every section at once and moves nothing. The scheme
lives on the course precisely so that switch is one write.

DAY 1 IS THE DAY THE MEMBER ENROLLED, not the day after. `release_day = 1` therefore means "open
immediately"; the offset is `release_day - 1`. Off-by-one here shows up as a section that opens a
day late for every member in the workspace, which is why it is written down once here rather than
inferred at each call site.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

# Schemes that are just a word and a number.
LABEL_WORDS = {"day": "Day", "module": "Module", "week": "Week", "phase": "Phase"}

# 'custom' and 'none' are absent on purpose: for those the section's own `name` is the label, and
# generating one would put two titles on the same card.
SCHEMES = ("day", "module", "week", "phase", "part", "custom", "none")

RELEASE_RULES = ("immediate", "day_n", "after_previous", "fixed_date")
DUE_RULES = ("none", "end_of_day_n", "end_of_week_n", "before_next_section")

_ROMAN = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
          (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))


def roman(n: int) -> str:
    out = []
    for value, numeral in _ROMAN:
        while n >= value:
            out.append(numeral)
            n -= value
    return "".join(out)


def label_for(scheme: str | None, index: int) -> str:
    """The generated label for the section at 0-based position `index`.

    Empty string for 'custom' and 'none' -- the caller renders `name` alone rather than an empty
    chip, and "" is how it knows to.
    """
    scheme = (scheme or "none").lower()
    if scheme in LABEL_WORDS:
        return f"{LABEL_WORDS[scheme]} {index + 1}"
    if scheme == "part":
        return f"Part {roman(index + 1)}"
    return ""


# ── how long a thing takes ────────────────────────────────────────────────────────────────

def duration_of(kind: str | None, *, duration_minutes: int | None = None,
                read_minutes: int | None = None, page_count: int | None = None) -> dict | None:
    """The one duration string, in its two lengths.

    A video is minutes, a document is pages, an article is minutes of reading -- three different
    units wearing the same slot on the card, which is exactly the sort of thing two frontends
    format differently if you let them.
    """
    kind = (kind or "video").lower()
    if kind == "reading":
        value, short, long = read_minutes, "{}m read", "{} min read"
    elif kind == "document":
        value, short, long = page_count, "{} pp", "{} pages"
    else:
        value, short, long = duration_minutes, "{}m", "{} min"
    if not value:
        return None
    return {"value": int(value), "short": short.format(value), "long": long.format(value)}


def lesson_minutes(kind: str | None, duration_minutes: int | None,
                   read_minutes: int | None) -> int:
    """Minutes this lesson costs a member, for a course total.

    COALESCE rather than a sum: a lesson has one length, and adding a video's minutes to a
    reading estimate would double-count the one lesson that has both because an author switched
    its kind. Pages are deliberately not minutes -- there is no honest conversion.
    """
    if (kind or "video").lower() == "reading":
        return int(read_minutes or duration_minutes or 0)
    return int(duration_minutes or read_minutes or 0)


def total_minutes(lessons) -> int:
    """Course run time across mixed kinds. Reading-heavy courses reported 0 before this."""
    return sum(lesson_minutes(getattr(le, "kind", None), getattr(le, "duration_minutes", None),
                              getattr(le, "read_minutes", None)) for le in lessons)


# ── ordering ──────────────────────────────────────────────────────────────────────────────

def order_lessons(lessons, sections):
    """Ungrouped lessons first, then each section's lessons in the author's order.

    NOT `ORDER BY section_id, sort`. `section_id` is a UUID, so ordering by it orders the sections
    by random hex -- stable, arbitrary, and not the order anybody dragged them into. The section's
    own `sort` is the position, so that is what ranks them, and lessons with no section rank -1 so
    they land above the first card exactly as the console draws them.
    """
    rank = {str(s.id): i for i, s in enumerate(sections)}
    return sorted(lessons, key=lambda le: (
        rank.get(str(getattr(le, "section_id", None) or ""), -1),
        int(getattr(le, "sort", 0) or 0),
        (getattr(le, "title", "") or "").lower()))


# ── when a section opens and when it is due ───────────────────────────────────────────────

def workspace_today(timezone_name: str | None) -> dt.date:
    """Today in the workspace's own timezone.

    A due date is a promise made in the office's day, not UTC's. At 6pm Mountain the UTC date has
    already rolled over, and a member would be told a section was overdue during the afternoon
    they were given to finish it.
    """
    if timezone_name:
        try:
            return dt.datetime.now(ZoneInfo(timezone_name)).date()
        except Exception:
            pass
    return dt.datetime.now(dt.timezone.utc).date()


def opens_on(section, started_on: dt.date | None) -> dt.date | None:
    """The date this section's release rule resolves to, or None if it is not a date at all.

    `after_previous` returns None because it is a rule about progress rather than the calendar --
    the caller answers it from completion, not from here.
    """
    rule = (getattr(section, "release_rule", None) or "immediate").lower()
    if rule == "fixed_date":
        return getattr(section, "release_date", None)
    if rule == "day_n":
        day = getattr(section, "release_day", None)
        if started_on is None or not day:
            return None
        return started_on + dt.timedelta(days=int(day) - 1)
    return None


def due_on(section, started_on: dt.date | None) -> dt.date | None:
    """The date this section is due, from the member's own enrolment.

    `end_of_week_n` lands on the LAST day of that week -- week 1 is days 1-7, so `started_on + 6`.
    Using `+ 7` would give a member eight days for a one-week block, every week.
    """
    rule = (getattr(section, "due_rule", None) or "none").lower()
    day = getattr(section, "due_day", None)
    if started_on is None or not day:
        return None
    if rule == "end_of_day_n":
        return started_on + dt.timedelta(days=int(day) - 1)
    if rule == "end_of_week_n":
        return started_on + dt.timedelta(days=int(day) * 7 - 1)
    return None


def day_number(started_on: dt.date | None, today: dt.date) -> int | None:
    """"Day 2 of 5" -- which day of the course the member is on. 1-based, never below 1."""
    if started_on is None:
        return None
    return max(1, (today - started_on).days + 1)


def _note(released: bool, opens: dt.date | None, due: dt.date | None, today: dt.date,
          complete: bool) -> str:
    """The one line of text under the section's counts."""
    if not released:
        if opens is None:
            return "Opens when the previous section is done"
        if opens == today + dt.timedelta(days=1):
            return "Opens tomorrow"
        return f"Opens {opens.strftime('%a')}" if (opens - today).days < 7 \
            else f"Opens {opens.strftime('%b %-d') if _dash_ok() else opens.strftime('%b %d')}"
    if complete or due is None:
        return ""
    if due < today:
        return "Overdue"
    if due == today:
        return "Due today"
    if due == today + dt.timedelta(days=1):
        return "Due tomorrow"
    return f"Due {due.strftime('%a')}" if (due - today).days < 7 \
        else f"Due {due.strftime('%b %-d') if _dash_ok() else due.strftime('%b %d')}"


def _dash_ok() -> bool:
    """`%-d` is glibc; Windows strftime rejects it. Prod is Linux, dev is Windows."""
    try:
        dt.date(2026, 1, 5).strftime("%-d")
        return True
    except ValueError:
        return False


def resolve(sections, *, scheme: str | None, lock_sections: bool = False,
            started_on: dt.date | None = None, today: dt.date | None = None,
            done_counts: dict | None = None) -> list[dict]:
    """Every section as the member sees it: label, state, dates, and the line of text.

    `done_counts` maps section id -> (done, total). A section with no lessons is never "complete";
    an empty section the author has not filled in yet would otherwise show a green tick.

    LOCKING IS TWO RULES IN SEQUENCE, and their order is the point. The release rule decides
    whether the calendar has reached this section; `lock_sections` decides whether the member has.
    Both must pass, so a course can be paced by date, by progress, by both, or by neither.
    """
    today = today or dt.date.today()
    done_counts = done_counts or {}
    out: list[dict] = []
    previous_complete = True
    seen_current = False

    for index, section in enumerate(sections):
        sid = str(getattr(section, "id", index))
        done, total = done_counts.get(sid, (0, 0))
        complete = bool(total) and done >= total

        rule = (getattr(section, "release_rule", None) or "immediate").lower()
        opens = opens_on(section, started_on)
        if rule == "after_previous":
            released = previous_complete
        elif opens is not None:
            released = today >= opens
        else:
            # day_n with no enrolment on file. Open it rather than lock it: a member with no
            # enrolment row is one who has never opened the course, and a locked first day is a
            # course nobody can start.
            released = True
        if lock_sections and not previous_complete:
            released = False

        if complete:
            state = "complete"
        elif released:
            state = "current"
        else:
            state = "locked"

        is_current = state == "current" and not seen_current
        seen_current = seen_current or is_current

        due = due_on(section, started_on)
        out.append({
            "id": sid,
            "name": getattr(section, "name", "") or "",
            "summary": getattr(section, "summary", None),
            "label": label_for(scheme, index),
            "sort": int(getattr(section, "sort", index) or 0),
            "release_rule": rule,
            "release_day": getattr(section, "release_day", None),
            "release_date": (getattr(section, "release_date", None).isoformat()
                             if getattr(section, "release_date", None) else None),
            "due_rule": (getattr(section, "due_rule", None) or "none").lower(),
            "due_day": getattr(section, "due_day", None),
            "opens_on": opens.isoformat() if opens else None,
            "due_on": due.isoformat() if due else None,
            "released": released,
            "state": state,
            "is_current": is_current,
            "done_count": done,
            "lesson_count": total,
            "note": _note(released, opens, due, today, complete),
        })
        previous_complete = complete

    return out
