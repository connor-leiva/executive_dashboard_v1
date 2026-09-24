"""ULRG Recruiting — the queue the rules build (RECRUITING-SPEC §4.7, Phase 3).

One table. The rule engine is pure functions over the Phase 1 tables; this is where its output
lands so a person can press Done on it.

THE UNIQUE CONSTRAINT IS THE FEATURE. (tenant, rule, candidate, window) means a rule that
evaluates every five minutes produces one row per occasion rather than 288 a day. Without it the
tick is not idempotent, and "idempotent" is the only reason it is safe to run a rule engine on a
schedule at all.

Still nothing written to GoHighLevel: Done clears the item locally, and completing the matching
GHL task is Phase 4's outbox.

Revision ID: 0081_recruiting_queue
Revises: 0080_recruiting_foundation
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID

revision = "0081_recruiting_queue"
down_revision = "0080_recruiting_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recruiting_queue_item",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_id", GUID(), sa.ForeignKey("recruiting_candidate.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_key", sa.String(32), nullable=False),
        sa.Column("window_key", sa.String(64), nullable=False),
        sa.Column("owner_seat_id", GUID(), sa.ForeignKey("recruiting_seat.id", ondelete="SET NULL"), nullable=True),
        sa.Column("state", sa.String(16), nullable=False, server_default="open"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("why", sa.String(400), nullable=True),
        sa.Column("primary_action", sa.String(16), nullable=True),
        sa.Column("cleared_by", sa.String(8), nullable=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        # No ForeignKey: recruiting_action is Phase 4. The column ships now because the shape is
        # settled; the constraint ships with the table it points at.
        sa.Column("action_id", GUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "rule_key", "candidate_id", "window_key",
                            name="uq_recruiting_queue_window"),
    )
    op.create_index("ix_recruiting_queue_item_tenant_id", "recruiting_queue_item", ["tenant_id"])
    op.create_index("ix_recruiting_queue_item_candidate_id", "recruiting_queue_item", ["candidate_id"])
    op.create_index("ix_recruiting_queue_item_rule_key", "recruiting_queue_item", ["rule_key"])
    op.create_index("ix_recruiting_queue_item_owner_seat_id", "recruiting_queue_item", ["owner_seat_id"])
    op.create_index("ix_recruiting_queue_item_state", "recruiting_queue_item", ["state"])
    op.create_index("ix_recruiting_queue_item_due_at", "recruiting_queue_item", ["due_at"])
    op.create_index("ix_recruiting_queue_open", "recruiting_queue_item",
                    ["tenant_id", "state", "due_at"])
    op.create_index("ix_recruiting_queue_seat", "recruiting_queue_item",
                    ["tenant_id", "owner_seat_id", "state"])


def downgrade() -> None:
    op.drop_table("recruiting_queue_item")
