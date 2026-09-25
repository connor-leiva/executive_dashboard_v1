"""Payables Phase 1 — the vendor master, and the audit category that goes with it.

SPEC-payables §2. Two tables and one constraint rebuild.

NUMBERING: the spec says 0080. It was written when 0079 was the head; 0080-0083 are the
Recruiting migrations, so this is 0084. Taking the spec's number literally would have forked
the head, and a forked head crash-loops Railway on boot.

`vendor_bank_account` is append-only by design, which the schema cannot enforce on its own —
the service supersedes rather than updates. `superseded_by` is the chain that makes the history
readable, and the reason an UPDATE would be wrong is that it erases where money used to go.

The audit CHECK is rebuilt here because `services/audit.py` gained "Payments". That constraint
is created under `if _dialect() == "postgresql"` and the suite runs SQLite, so no test can see
it — 0060 exists because exactly that gap put thirteen console screens into production 500s.
Structure copied from 0060 deliberately, including the NOT VALID downgrade.

Revision ID: 0084_payables_vendor
Revises: 0083_recruiting_accountability
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID
from app.services.audit import AUDIT_CATEGORIES

revision = "0084_payables_vendor"
down_revision = "0083_recruiting_accountability"
branch_labels = None
depends_on = None

# What the constraint allowed before this migration: everything the code knows, minus the one
# category this migration adds. Computed rather than hardcoded (0060 hardcodes because it was
# restoring a *narrower* set it did not own) — and a downgrade that is fractionally too wide
# rejects nothing the product wrote, which is the direction that cannot cause an outage.
_PRIOR = tuple(sorted(AUDIT_CATEGORIES - {"Payments"}))


def _dialect() -> str:
    return op.get_bind().dialect.name


def _tables() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _has_check(table: str, name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return any(c.get("name") == name for c in insp.get_check_constraints(table))


def _sql_in(values) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in sorted(values))


def upgrade() -> None:
    tabs = _tables()

    # Guarded: 0001_init runs create_all() of the CURRENT models, so a database built from
    # scratch already carries these tables and an unguarded create_table would fail on it.
    if "vendor" not in tabs:
        op.create_table(
            "vendor",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("legal_name", sa.String(200), nullable=False),
            sa.Column("name_norm", sa.String(200), nullable=False),
            sa.Column("display_name", sa.String(200), nullable=False),
            sa.Column("dba", sa.String(200), nullable=True),
            sa.Column("vendor_type", sa.String(12), nullable=False, server_default="business"),
            # Last four only. The full TIN is never stored in Acumyn — see §2.4 and the model.
            sa.Column("tin_last4", sa.String(4), nullable=True),
            sa.Column("w9_document_id", GUID(),
                      sa.ForeignKey("binder_document.id", ondelete="SET NULL"), nullable=True),
            sa.Column("w9_received_at", sa.Date(), nullable=True),
            sa.Column("is_1099", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("default_standard_account_id", GUID(),
                      sa.ForeignKey("standard_account.id", ondelete="SET NULL"), nullable=True),
            sa.Column("default_business_id", GUID(),
                      sa.ForeignKey("business.id", ondelete="SET NULL"), nullable=True),
            sa.Column("default_legal_entity_id", GUID(),
                      sa.ForeignKey("legal_entity.id", ondelete="SET NULL"), nullable=True),
            sa.Column("terms_days", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", "name_norm", name="uq_vendor_name_norm"),
        )
        op.create_index("ix_vendor_status", "vendor", ["tenant_id", "status"])

    if "vendor_bank_account" not in tabs:
        op.create_table(
            "vendor_bank_account",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("vendor_id", GUID(), sa.ForeignKey("vendor.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("routing_last4", sa.String(4), nullable=False),
            sa.Column("account_last4", sa.String(4), nullable=False),
            sa.Column("verified_by", GUID(), sa.ForeignKey("user.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("verification_method", sa.String(16), nullable=False,
                      server_default="callback"),
            sa.Column("verification_note", sa.Text(), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("superseded_by", GUID(),
                      sa.ForeignKey("vendor_bank_account.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    # ── the audit vocabulary (Postgres only, exactly as 0060) ──
    if _dialect() != "postgresql":
        return
    if _has_check("audit_log", "ck_audit_log_category"):
        op.drop_constraint("ck_audit_log_category", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_category", "audit_log",
        f"category IN ({_sql_in(AUDIT_CATEGORIES)})")


def downgrade() -> None:
    tabs = _tables()
    if "vendor_bank_account" in tabs:
        op.drop_table("vendor_bank_account")
    if "vendor" in tabs:
        op.drop_index("ix_vendor_status", table_name="vendor")
        op.drop_table("vendor")

    if _dialect() != "postgresql":
        return
    if _has_check("audit_log", "ck_audit_log_category"):
        op.drop_constraint("ck_audit_log_category", "audit_log", type_="check")
    # NOT VALID, as 0060: any Payments row written while the wider set was in force would
    # violate this, and rejecting history the product legitimately created is worse than a
    # constraint that governs only new rows.
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_log_category "
        f"CHECK (category IN ({_sql_in(_PRIOR)})) NOT VALID")
