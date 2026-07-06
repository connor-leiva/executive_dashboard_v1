"""add mortgage_vid + buyer_email2 + buyer_phone to transaction (attach flywheel)

Revision ID: 0008_txn_mortgage_vid
Revises: 0007_sync_run_stats
Create Date: 2026-07-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_txn_mortgage_vid"
down_revision: Union[str, None] = "0007_sync_run_stats"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "transaction", "mortgage_vid"):
        op.add_column("transaction", sa.Column("mortgage_vid", sa.Integer(), nullable=True))
    if not _has_column(bind, "transaction", "buyer_email2"):
        op.add_column("transaction", sa.Column("buyer_email2", sa.String(length=255), nullable=True))
    if not _has_column(bind, "transaction", "buyer_phone"):
        op.add_column("transaction", sa.Column("buyer_phone", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("transaction", "buyer_phone")
    op.drop_column("transaction", "buyer_email2")
    op.drop_column("transaction", "mortgage_vid")
