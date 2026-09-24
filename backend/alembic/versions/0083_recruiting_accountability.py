"""ULRG Recruiting — commitments and goals (RECRUITING-SPEC §4.8, §9 Phase 5).

Two tables, and neither stores an actual. Every number a commitment is measured against is
computed from the stage events, appointments and activities at read time, so a commitment and
its progress cannot drift apart. A stored actual that disagrees with the underlying rows is the
failure that makes a weekly number quietly meaningless, and it is not recoverable after the fact.

Goals are PER PERIOD, on the ScorecardGoal pattern: a past month's verdict must not change
because somebody set next month's target. That is the whole reason that table is keyed by period,
and the reason this is a table rather than a number on the seat.

Revision ID: 0083_recruiting_accountability
Revises: 0082_recruiting_outbox
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID

revision = "0083_recruiting_accountability"
down_revision = "0082_recruiting_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recruiting_commitment",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seat_id", GUID(), sa.ForeignKey("recruiting_seat.id", ondelete="CASCADE"), nullable=False),
        # A Monday in BUSINESS-LOCAL time. A week keyed off UTC begins on Sunday evening in
        # Denver, so a commitment would appear the night before the week it belongs to.
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("metric", sa.String(16), nullable=False),
        sa.Column("commit", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "seat_id", "week_start", "metric",
                            name="uq_recruiting_commitment"),
    )
    op.create_index("ix_recruiting_commitment_tenant_id", "recruiting_commitment", ["tenant_id"])
    op.create_index("ix_recruiting_commitment_seat_id", "recruiting_commitment", ["seat_id"])
    op.create_index("ix_recruiting_commitment_week_start", "recruiting_commitment", ["week_start"])
    op.create_index("ix_recruiting_commitment_week", "recruiting_commitment",
                    ["tenant_id", "week_start"])

    op.create_table(
        "recruiting_goal",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        # NULL means the TEAM goal. A team goal and the sum of its seats are allowed to differ.
        sa.Column("seat_id", GUID(), sa.ForeignKey("recruiting_seat.id", ondelete="CASCADE"), nullable=True),
        sa.Column("period_key", sa.String(16), nullable=False),
        sa.Column("metric", sa.String(16), nullable=False),
        sa.Column("goal", sa.Numeric(10, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "seat_id", "period_key", "metric",
                            name="uq_recruiting_goal"),
    )
    op.create_index("ix_recruiting_goal_tenant_id", "recruiting_goal", ["tenant_id"])
    op.create_index("ix_recruiting_goal_seat_id", "recruiting_goal", ["seat_id"])
    op.create_index("ix_recruiting_goal_period_key", "recruiting_goal", ["period_key"])
    op.create_index("ix_recruiting_goal_period", "recruiting_goal",
                    ["tenant_id", "period_key", "metric"])


def downgrade() -> None:
    op.drop_table("recruiting_goal")
    op.drop_table("recruiting_commitment")
