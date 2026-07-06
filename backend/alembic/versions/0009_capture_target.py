"""add capture_target to business (flywheel attach-rate goal)

Revision ID: 0009_capture_target
Revises: 0008_txn_mortgage_vid
Create Date: 2026-07-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009_capture_target"
down_revision: Union[str, None] = "0008_txn_mortgage_vid"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "business", "capture_target"):
        op.add_column("business", sa.Column("capture_target", sa.Numeric(5, 2),
                                            nullable=False, server_default="60"))


def downgrade() -> None:
    op.drop_column("business", "capture_target")
