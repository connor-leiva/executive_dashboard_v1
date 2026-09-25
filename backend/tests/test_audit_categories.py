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
import ast
from pathlib import Path

import pytest

from app.services.audit import AUDIT_CATEGORIES

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "0060_audit_category_vocabulary.py"

# Every module that writes an audit row with an explicit category. Globbed rather than listed, so
# a new router is covered without anybody remembering this file.
SOURCES = sorted((BACKEND / "app").rglob("*.py"))


# The functions that actually write an audit row. Anything else taking a `category=` keyword is
# a different vocabulary and none of this test's business.
_AUDIT_WRITERS = {"audit", "_record_mutation"}


def _categories_written() -> dict[str, set[str]]:
    """Category literals passed to audit() / _record_mutation(), per file.

    Parsed, not pattern-matched. This read keyword `category="X"` anywhere in a file and assumed
    it meant the audit log -- true until payables called
    `binder_ingest.ingest_document(..., category="tax")`, which is the BINDER's document
    vocabulary (formation|insurance|tax|...) and has nothing to do with audit_log. The heuristic
    flagged it as a category that would 500 in production, which was simply wrong.

    Reading the call target instead of the surrounding text is strictly narrower AND strictly
    more complete: it also sees an audit() call whose category sits on a later line, which the
    regex found only by accident of both being in the same file.
    """
    found: dict[str, set[str]] = {}
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:                     # not importable anyway; the suite will say so
            continue
        cats: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name not in _AUDIT_WRITERS:
                continue
            for kw in node.keywords:
                if kw.arg == "category" and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    cats.add(kw.value.value)
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
