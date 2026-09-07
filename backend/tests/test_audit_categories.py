"""Every audit category the code writes must be one the database will accept.

LIVE PRODUCTION BUG, found by a logo upload answering 500. Migration 0055 created
`ck_audit_log_category` allowing six values while the tenant console writes fifteen, so thirteen
of the console's screens raised a 500 the moment somebody pressed Save -- Training, SOPs, Tool
Launchpad, People, Team Calendar, AI, Roles, Marketing, Integrations, Setup, Sign-in, Win the Day
and Brand. The write the endpoint was actually doing had already succeeded; the audit row rolled
it back.

WHY A GREEN SUITE SAID NOTHING. The constraint is created under `if _dialect() == "postgresql"`
and this suite runs on SQLite, so no test could reach it. That is the part worth guarding, and it
cannot be guarded by adding another Postgres-only test -- so this compares the two lists directly
instead, which works on any database and needs no connection at all.

Adding a category to console.py without adding it to AUDIT_CATEGORIES now fails here, in the
suite that runs, rather than in production on the customer's Save button.
"""
import re
from pathlib import Path

import pytest

from app.services.audit import AUDIT_CATEGORIES

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "0060_audit_category_vocabulary.py"

# Every module that writes an audit row with an explicit category. Globbed rather than listed, so
# a new router is covered without anybody remembering this file.
SOURCES = sorted((BACKEND / "app").rglob("*.py"))


def _categories_written() -> dict[str, set[str]]:
    """Category literals passed to audit()/_record_mutation(), per file."""
    found: dict[str, set[str]] = {}
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        # KEYWORD ARGUMENTS ONLY -- `category="X"`, no spaces. PEP 8 puts no spaces around a
        # keyword argument's `=` and spaces around an assignment's, and this codebase follows
        # that, so it cleanly separates `audit(..., category="Training")` from
        # `doc.category = "other"` in binder_extract, which is a DOCUMENT category and has
        # nothing to do with the audit log. The looser pattern flagged it and was wrong.
        cats = set(re.findall(r'(?<![\w.])category="([^"]+)"', text))
        # The definition itself is not a call site.
        if cats and path.name not in ("audit.py",):
            found[str(path.relative_to(BACKEND))] = cats
    return found


def test_every_category_the_code_writes_is_one_the_database_allows():
    written = _categories_written()
    assert written, "no audit categories found — has the call shape changed?"
    offenders = {
        where: sorted(cats - AUDIT_CATEGORIES)
        for where, cats in written.items()
        if cats - AUDIT_CATEGORIES
    }
    assert not offenders, (
        "these categories would violate ck_audit_log_category on Postgres and 500 in "
        f"production: {offenders}. Add them to services.audit.AUDIT_CATEGORIES, which the "
        "migration builds the constraint from.")


def test_the_constraint_is_built_from_that_same_set():
    """The migration must derive the constraint rather than repeat the list, or the two drift and
    the check above starts passing while production still rejects the row."""
    assert MIGRATION.exists(), "migration 0060 is missing"
    text = MIGRATION.read_text(encoding="utf-8")
    assert "from app.services.audit import AUDIT_CATEGORIES" in text
    assert "_sql_in(AUDIT_CATEGORIES)" in text, (
        "the migration hardcodes its own list instead of using AUDIT_CATEGORIES")


@pytest.mark.parametrize("category", sorted(AUDIT_CATEGORIES))
def test_each_category_survives_a_round_trip(category):
    """Cheap, but it pins the shape: a category with a quote or a stray character would break the
    generated SQL, and the console's names are free text an admin never sees the inside of."""
    assert category == category.strip()
    assert "'" not in category and '"' not in category
    assert category, "an empty category would pass the IN () check and mean nothing"
