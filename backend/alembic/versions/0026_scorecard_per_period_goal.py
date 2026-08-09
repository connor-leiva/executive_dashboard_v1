"""Per-period goals — scorecard_goal (metric × period → goal, keeps history)

Each measurement period keeps its own goals, so past periods never shift when a new sprint's goals
are set. Absent row falls back to scorecard_metric.goal. Idempotent guard; local/SQLite builds from
create_all, so this matters for Postgres (prod).

Revision ID: 0026_scorecard_per_period_goal
Revises: 0025_scorecard_owner_photo
Create Date: 2026-08-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID

revision: str = "0026_scorecard_per_period_goal"
down_revision: Union[str, None] = "0025_scorecard_owner_photo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "scorecard_goal"):
        op.create_table(
            "scorecard_goal",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True),
            sa.Column("metric_id", GUID(), sa.ForeignKey("scorecard_metric.id", ondelete="CASCADE"), index=True),
            sa.Column("period_key", sa.String(length=40), nullable=False),
            sa.Column("goal", sa.Numeric(12, 4), nullable=False),
            sa.UniqueConstraint("metric_id", "period_key", name="uq_scorecard_goal"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "scorecard_goal"):
        op.drop_table("scorecard_goal")
