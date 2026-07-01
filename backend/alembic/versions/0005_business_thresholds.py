"""add watch_margin_below + per_loan_share to business

Revision ID: 0005_business_thresholds
Revises: 0004_metric_record
Create Date: 2026-07-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_business_thresholds"
down_revision: Union[str, None] = "0004_metric_record"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "business", "watch_margin_below"):
        op.add_column("business", sa.Column("watch_margin_below", sa.Numeric(5, 2), nullable=True))
    if not _has_column(bind, "business", "per_loan_share"):
        op.add_column("business", sa.Column("per_loan_share", sa.Numeric(14, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("business", "per_loan_share")
    op.drop_column("business", "watch_margin_below")
