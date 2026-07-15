"""Acumyn Books module — initial tables + Tenant.config

Additive per SPEC-books-module Part 1: the ledger-transaction / scan-pipeline /
intercompany / close / review tables the Books module operates on, plus a portfolio-
scoped Tenant.config (holds the intercompany elimination account list). No existing
table is restructured. Idempotent (guards) so it is safe to re-run; local/SQLite builds
its schema from create_all, so this migration matters for Postgres (prod).

Revision ID: 0013_books_init
Revises: 0012_password_hash_nullable
Create Date: 2026-07-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision: str = "0013_books_init"
down_revision: Union[str, None] = "0012_password_hash_nullable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    # Portfolio-scoped, non-secret tenant config (Books eliminations list lives here).
    if not _has_column(bind, "tenant", "config"):
        op.add_column("tenant", sa.Column("config", JSONType, nullable=True))

    if not _has_table(bind, "book_txn"):
        op.create_table(
            "book_txn",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True),
            sa.Column("business_id", GUID(), sa.ForeignKey("business.id"), nullable=False),
            sa.Column("realm_id", sa.String(64), nullable=False),
            sa.Column("qbo_type", sa.String(24), nullable=False),
            sa.Column("qbo_id", sa.String(32), nullable=False),
            sa.Column("sync_token", sa.String(16), nullable=True),
            sa.Column("txn_date", sa.Date(), nullable=False),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("payee", sa.String(200), nullable=True),
            sa.Column("memo", sa.Text(), nullable=True),
            sa.Column("account_label", sa.String(200), nullable=True),
            sa.Column("account_qbo_id", sa.String(32), nullable=True),
            sa.Column("bank_account_label", sa.String(200), nullable=True),
            sa.Column("came_categorized", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("scan_state", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("suggestion", JSONType, nullable=True),
            sa.Column("flags", JSONType, nullable=True),
            sa.Column("reviewed_by", GUID(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("decision", JSONType, nullable=True),
            sa.Column("posted_back_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("tenant_id", "realm_id", "qbo_type", "qbo_id", name="uq_booktxn_src"),
        )

    if not _has_table(bind, "pl_line"):
        op.create_table(
            "pl_line",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True),
            sa.Column("business_id", GUID(), sa.ForeignKey("business.id"), nullable=False),
            sa.Column("period_start", sa.Date(), nullable=False),
            sa.Column("period_end", sa.Date(), nullable=False),
            sa.Column("section", sa.String(16), nullable=False),
            sa.Column("parent", sa.String(200), nullable=True),
            sa.Column("label", sa.String(200), nullable=False),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.UniqueConstraint("tenant_id", "business_id", "period_start", "period_end",
                                "section", "parent", "label", name="uq_pl_line"),
        )

    if not _has_table(bind, "ic_rule"):
        op.create_table(
            "ic_rule",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True),
            sa.Column("label", sa.String(200), nullable=False),
            sa.Column("from_business_id", GUID(), sa.ForeignKey("business.id"), nullable=True),
            sa.Column("to_business_id", GUID(), sa.ForeignKey("business.id"), nullable=True),
            sa.Column("characterization", sa.String(24), nullable=False),
            sa.Column("monthly_cap", sa.Numeric(14, 2), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )

    if not _has_table(bind, "ic_link"):
        op.create_table(
            "ic_link",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True),
            sa.Column("from_business_id", GUID(), sa.ForeignKey("business.id"), nullable=False),
            sa.Column("to_business_id", GUID(), sa.ForeignKey("business.id"), nullable=False),
            sa.Column("from_txn_id", GUID(), sa.ForeignKey("book_txn.id"), nullable=True),
            sa.Column("to_txn_id", GUID(), sa.ForeignKey("book_txn.id"), nullable=True),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("occurred_on", sa.Date(), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="unmatched"),
            sa.Column("characterization", sa.String(24), nullable=True),
            sa.Column("rule_id", GUID(), sa.ForeignKey("ic_rule.id"), nullable=True),
            sa.Column("decided_by", GUID(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
        )

    if not _has_table(bind, "close_period"):
        op.create_table(
            "close_period",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True),
            sa.Column("business_id", GUID(), sa.ForeignKey("business.id"), nullable=False),
            sa.Column("period", sa.Date(), nullable=False),
            sa.Column("steps", JSONType, nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="open"),
            sa.Column("closed_by", GUID(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("tenant_id", "business_id", "period", name="uq_close_period"),
        )

    if not _has_table(bind, "books_review"):
        op.create_table(
            "books_review",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True),
            sa.Column("period", sa.Date(), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("model", sa.String(64), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
            sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("signed_by", GUID(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("tenant_id", "period", name="uq_books_review"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("books_review", "close_period", "ic_link", "ic_rule", "pl_line", "book_txn"):
        if _has_table(bind, table):
            op.drop_table(table)
    if _has_column(bind, "tenant", "config"):
        op.drop_column("tenant", "config")
