"""scorecard_backfill_mark — one row so the Spring B 13-week fill runs once.

Additive. No existing table is altered.

Revision ID: 0102_sb_backfill_mark
Revises: 0101_springb_tiers
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision: str = "0102_sb_backfill_mark"
down_revision: Union[str, None] = "0101_springb_tiers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "scorecard_backfill_mark"):
        return
    op.create_table(
        "scorecard_backfill_mark",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("stats", JSONType, nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "key", name="uq_scorecard_backfill_mark"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "scorecard_backfill_mark"):
        op.drop_table("scorecard_backfill_mark")
