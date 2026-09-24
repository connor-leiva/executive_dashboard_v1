"""ULRG Recruiting — the write outbox (RECRUITING-SPEC §4.6, Phase 4).

One table, and it is the most consequential one in this build: every write against a customer's
GoHighLevel goes through it, and its row is committed BEFORE the call is made. If the process
dies mid-send there is still a record that something was attempted -- which is the difference
between "we do not know what happened" and "nothing happened". A text to a recruit cannot be
un-sent, so the system has to be able to tell those apart.

The unique key (tenant, idempotency_key) is what stands between an impatient double-click and two
texts to the same person. GHL has no idempotency of its own for a message.

Creating the table does NOT turn anything on. All four gates in §5.2 default closed and
`dry_run` defaults true, so the first thing this table does in production is record exactly what
WOULD have been sent.

Revision ID: 0082_recruiting_outbox
Revises: 0081_recruiting_queue
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision = "0082_recruiting_outbox"
down_revision = "0081_recruiting_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recruiting_action",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("seat_id", GUID(), sa.ForeignKey("recruiting_seat.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_user_id", GUID(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("candidate_id", GUID(), sa.ForeignKey("recruiting_candidate.id", ondelete="SET NULL"), nullable=True),
        sa.Column("queue_item_id", GUID(), sa.ForeignKey("recruiting_queue_item.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("request", JSONType, nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ghl_ref", sa.String(64), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(400), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_recruiting_action_idem"),
    )
    op.create_index("ix_recruiting_action_tenant_id", "recruiting_action", ["tenant_id"])
    op.create_index("ix_recruiting_action_idempotency_key", "recruiting_action", ["idempotency_key"])
    op.create_index("ix_recruiting_action_seat_id", "recruiting_action", ["seat_id"])
    op.create_index("ix_recruiting_action_candidate_id", "recruiting_action", ["candidate_id"])
    op.create_index("ix_recruiting_action_kind", "recruiting_action", ["kind"])
    op.create_index("ix_recruiting_action_status", "recruiting_action", ["status"])
    op.create_index("ix_recruiting_action_next_attempt_at", "recruiting_action", ["next_attempt_at"])
    op.create_index("ix_recruiting_action_created_at", "recruiting_action", ["created_at"])
    # The drain query: queued rows whose backoff has elapsed. Composite because the tick runs
    # every minute across every workspace and must not table-scan to find nothing.
    op.create_index("ix_recruiting_action_drain", "recruiting_action",
                    ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_table("recruiting_action")
