"""Per-period cumulative goal — scorecard_goal.cumulative_goal (the period TOTAL, e.g. 130 homes/qtr)

A measurable now carries two editable figures per period: the weekly goal (already `goal`) and an
optional cumulative goal — the whole-period target that the cumulative block tracks toward. NULL →
the cumulative view falls back to weekly_goal × weeks (prior behaviour). Only meaningful for flow
metrics. Idempotent guard; local/SQLite builds from create_all, so this matters for Postgres (prod).

Revision ID: 0027_scorecard_cumulative_goal
Revises: 0026_scorecard_per_period_goal
Create Date: 2026-08-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0027_scorecard_cumulative_goal"
down_revision: Union[str, None] = "0026_scorecard_per_period_goal"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "scorecard_goal", "cumulative_goal"):
        op.add_column("scorecard_goal", sa.Column("cumulative_goal", sa.Numeric(12, 4), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "scorecard_goal", "cumulative_goal"):
        op.drop_column("scorecard_goal", "cumulative_goal")
