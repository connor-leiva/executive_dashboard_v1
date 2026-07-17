"""Acumyn Binder — date math, status, recurrence (SPEC-binder-module Part 6).

Status is COMPUTED, never stored, from due_date + lead_days + today. Recurrence roll-forward
runs on `complete`. Kept pure so the confirm loop, the matrix, and the reminder worker all
read the same rules.

The one cross-module tie (federal_tax / state_tax -> "in_progress" while the linked Business's
Books close is pending) is passed in as `books_pending`; the Books ClosePeriod read that
computes it is wired with the matrix in a later step. Until then callers pass books_pending=False.
"""
from __future__ import annotations

import calendar
import datetime as dt

TAX_KINDS = {"federal_tax", "state_tax"}


def compute_status(*, applicable: bool, kind: str, due_date: dt.date | None,
                   lead_days: int, today: dt.date, books_pending: bool = False) -> str:
    """current | due_soon | overdue | in_progress | not_applicable (Part 6)."""
    if not applicable:
        return "not_applicable"
    if kind in TAX_KINDS and books_pending:
        return "in_progress"
    if due_date is None:
        return "current"                 # e.g. a human-set one-time obligation with no date yet
    if due_date < today:
        return "overdue"
    if due_date <= today + dt.timedelta(days=lead_days):
        return "due_soon"
    return "current"


def _add_months(d: dt.date, months: int) -> dt.date:
    """Shift a date by whole months, clamping the day into the target month."""
    m = d.month - 1 + months
    year = d.year + m // 12
    month = m % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def _add_years(d: dt.date, years: int) -> dt.date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:                   # Feb 29 -> Feb 28
        return d.replace(year=d.year + years, day=28)


def roll_forward(due_date: dt.date | None, cadence: str) -> dt.date | None:
    """Advance a completed obligation to its next cycle. one_time / none do not roll (the
    obligation is complete permanently); a missing due_date can't roll."""
    if due_date is None:
        return None
    if cadence == "annual":
        return _add_years(due_date, 1)
    if cadence == "biennial":
        return _add_years(due_date, 2)
    if cadence == "quarterly":
        return _add_months(due_date, 3)
    return due_date                      # one_time | none: stays put, completion is terminal
