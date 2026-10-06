"""scorecard_group.active — soft-remove / hide an office without destroying its metrics + history,
mirroring scorecard_metric.active. Defaults true so every existing office stays visible. Nullable-safe,
idempotent; local/SQLite builds from create_all.

Revision ID: 0091_scorecard_group_active
Revises: 0090_forum_event
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0091_scorecard_group_active"
down_revision: Union[str, None] = "0090_forum_event"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "scorecard_group", "active"):
        op.add_column("scorecard_group", sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.text("true")))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "scorecard_group", "active"):
        op.drop_column("scorecard_group", "active")
