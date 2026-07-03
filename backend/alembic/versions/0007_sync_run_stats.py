"""add stats JSON to sync_run (record counts / seconds for the last-run line)

Revision ID: 0007_sync_run_stats
Revises: 0006_three_lens_financials
Create Date: 2026-07-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_sync_run_stats"
down_revision: Union[str, None] = "0006_three_lens_financials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "sync_run", "stats"):
        op.add_column("sync_run", sa.Column("stats", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("sync_run", "stats")
