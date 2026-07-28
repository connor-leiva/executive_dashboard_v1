"""beCollective Launch section — launch + launch_weekly

Additive per SPEC-becollective-launch Section 6: one Launch config row per cohort launch
(tenant-editable) + a rolling weekly momentum store. No existing table is restructured.
Idempotent (guards) so it is safe to re-run; local/SQLite builds its schema from
create_all, so this migration matters for Postgres (prod).

Revision ID: 0018_becollective_launch
Revises: 0017_obligation_ai_summary
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision: str = "0018_becollective_launch"
down_revision: Union[str, None] = "0017_obligation_ai_summary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "launch"):
        op.create_table(
            "launch",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True),
            sa.Column("business_id", GUID(), sa.ForeignKey("business.id"), index=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("program", sa.String(80), nullable=False, server_default="beCollective"),
            sa.Column("event_start", sa.Date(), nullable=True),
            sa.Column("event_end", sa.Date(), nullable=True),
            sa.Column("window_start", sa.Date(), nullable=False),
            sa.Column("window_end", sa.Date(), nullable=False),
            sa.Column("goal_arr", sa.Numeric(14, 2), nullable=False),
            sa.Column("ticket_pif", sa.Numeric(12, 2), nullable=False),
            sa.Column("ticket_plan", sa.Numeric(12, 2), nullable=False),
            sa.Column("plan_installments", sa.Integer(), nullable=False, server_default="12"),
            sa.Column("mix_pif", sa.Numeric(5, 4), nullable=False, server_default="0.5"),
            sa.Column("pipeline_match", sa.String(160), nullable=False),
            sa.Column("cohort_value", sa.String(80), nullable=True),
            sa.Column("pace_model", sa.String(10), nullable=False, server_default="linear"),
            sa.Column("pace_tolerance", sa.Numeric(4, 3), nullable=False, server_default="0.1"),
            sa.Column("won_grace_days", sa.Integer(), nullable=False, server_default="7"),
            sa.Column("stage_map", JSONType, nullable=False),
            sa.Column("payment_plan_map", JSONType, nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if not _has_table(bind, "launch_weekly"):
        op.create_table(
            "launch_weekly",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True),
            sa.Column("launch_id", GUID(), sa.ForeignKey("launch.id", ondelete="CASCADE"), index=True),
            sa.Column("week_start", sa.Date(), nullable=False),
            sa.Column("optins", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("calls_source", sa.String(16), nullable=False, server_default="proxy"),
            sa.Column("closes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("enrolled_cum", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("launch_id", "week_start", name="uq_launch_week"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("launch_weekly", "launch"):
        if _has_table(bind, table):
            op.drop_table(table)
