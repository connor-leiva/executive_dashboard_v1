"""Books Cleanup -- the audited year's books as a live tab. Two tables, and one constraint rebuild.

`cleanup_dataset` holds each uploaded audit file (schema axcion.books_cleanup/1): a few MB of JSON,
kept per upload rather than replaced, so the tab's history of what it showed stays answerable.
`cleanup_state` holds the decisions people make on the tab -- only what they changed, laid over the
file's own values -- one row per workspace.

The tab exists for a workspace only while it has a dataset (services/tabs.py), so this migration
adds no tab anywhere: a workspace gets Books Cleanup when somebody loads its file.

The audit CHECK is rebuilt because `services/audit.py` gained "Books", which every change on the
tab is audited under. That constraint is Postgres-only and the suite runs SQLite, so no test can
reach it: without this rebuild every save on the tab answered 500 in production while every test
passed. Structure copied from 0084 (and 0060), including the NOT VALID downgrade.

NUMBERING: 0092_scorecard_metric_src_spec is head, so this is 0093. `alembic heads` was one line
before (a fork here crash-loops the deploy).

Revision ID: 0093_books_cleanup
Revises: 0092_scorecard_metric_src_spec
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType
from app.services.audit import AUDIT_CATEGORIES

revision = "0093_books_cleanup"
down_revision = "0092_scorecard_metric_src_spec"
branch_labels = None
depends_on = None

# What the constraint allowed before this migration: everything the code knows, minus the one
# category this migration adds (computed, as 0084 does: a downgrade a fraction too wide rejects
# nothing the product wrote, which is the direction that cannot cause an outage).
_PRIOR = tuple(sorted(AUDIT_CATEGORIES - {"Books"}))


def _dialect() -> str:
    return op.get_bind().dialect.name


def _tables() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def _has_check(table: str, name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return any(c.get("name") == name for c in insp.get_check_constraints(table))


def _sql_in(values) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in sorted(values))


def upgrade() -> None:
    tabs = _tables()
    if "cleanup_dataset" not in tabs:
        op.create_table(
            "cleanup_dataset",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("label", sa.String(200), nullable=False),
            sa.Column("schema_id", sa.String(64), nullable=False),
            # JSONType BARE, never instantiated -- `JSONType()` raises TypeError mid-upgrade.
            sa.Column("payload", JSONType, nullable=False),
            sa.Column("uploaded_by", GUID(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if "ix_cleanup_dataset_tenant_uploaded" not in _indexes("cleanup_dataset"):
        op.create_index("ix_cleanup_dataset_tenant_uploaded", "cleanup_dataset", ["tenant_id", "uploaded_at"])
    if "cleanup_state" not in tabs:
        op.create_table(
            "cleanup_state",
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("inputs", JSONType, nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_by", GUID(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    # ── the audit vocabulary (Postgres only, exactly as 0084) ──
    if _dialect() != "postgresql":
        return
    if _has_check("audit_log", "ck_audit_log_category"):
        op.drop_constraint("ck_audit_log_category", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_category", "audit_log",
        f"category IN ({_sql_in(AUDIT_CATEGORIES)})")


def downgrade() -> None:
    tabs = _tables()
    if "cleanup_state" in tabs:
        op.drop_table("cleanup_state")
    if "cleanup_dataset" in tabs:
        if "ix_cleanup_dataset_tenant_uploaded" in _indexes("cleanup_dataset"):
            op.drop_index("ix_cleanup_dataset_tenant_uploaded", table_name="cleanup_dataset")
        op.drop_table("cleanup_dataset")

    if _dialect() != "postgresql":
        return
    if _has_check("audit_log", "ck_audit_log_category"):
        op.drop_constraint("ck_audit_log_category", "audit_log", type_="check")
    # NOT VALID, as 0060 and 0084: Books rows written while the wider set was in force would violate
    # this, and rejecting history the product legitimately created is worse than a constraint that
    # governs only new rows.
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_log_category "
        f"CHECK (category IN ({_sql_in(_PRIOR)})) NOT VALID")
