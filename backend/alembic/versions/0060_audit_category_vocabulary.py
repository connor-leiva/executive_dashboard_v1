"""Widen ck_audit_log_category to the vocabulary the code actually writes.

LIVE PRODUCTION BUG. 0055 created this constraint allowing six values --
Publish, Config, Access, Read, Content, System -- while the tenant console writes fifteen. Only
two of them were legal, so thirteen of the console's screens answered 500 the moment somebody
pressed Save: Training, SOPs, Tool Launchpad, People, Team Calendar, AI, Roles, Marketing,
Integrations, Setup, Sign-in, Win the Day and Brand. The upload itself succeeded and the audit
row rolled the transaction back with it.

It was invisible for two reasons worth writing down. The constraint is created under
`if _dialect() == "postgresql"`, and the test suite runs on SQLite -- so no test could ever have
hit it. And the failure is a 500 from an INSERT the endpoint never mentions, so the console
reported "Internal Server Error" against a file upload that had in fact worked.

The set is built from services.audit.AUDIT_CATEGORIES, which is now the single definition, and a
test asserts every category the code writes appears there.

Revision ID: 0060_audit_category_vocabulary
Revises: 0059_books_suggestion_basis
"""
from alembic import op
import sqlalchemy as sa

from app.services.audit import AUDIT_CATEGORIES

revision = "0060_audit_category_vocabulary"
down_revision = "0059_books_suggestion_basis"
branch_labels = None
depends_on = None

# The six 0055 allowed, kept so a downgrade restores exactly what was there.
_ORIGINAL = ("Publish", "Config", "Access", "Read", "Content", "System")


def _dialect() -> str:
    return op.get_bind().dialect.name


def _has_check(table: str, name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return any(c.get("name") == name for c in insp.get_check_constraints(table))


def _sql_in(values) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in sorted(values))


def upgrade() -> None:
    # Postgres only, matching 0055: SQLite never had this constraint, so there is nothing to
    # widen there and an ALTER would be a no-op at best.
    if _dialect() != "postgresql":
        return
    if _has_check("audit_log", "ck_audit_log_category"):
        op.drop_constraint("ck_audit_log_category", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_category", "audit_log",
        f"category IN ({_sql_in(AUDIT_CATEGORIES)})")


def downgrade() -> None:
    if _dialect() != "postgresql":
        return
    if _has_check("audit_log", "ck_audit_log_category"):
        op.drop_constraint("ck_audit_log_category", "audit_log", type_="check")
    # Rows written while the wider set was in force would violate this, so the narrow constraint
    # is restored NOT VALID: it governs new rows without rejecting history the product created.
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_log_category "
        f"CHECK (category IN ({_sql_in(_ORIGINAL)})) NOT VALID")
