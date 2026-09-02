"""Add per-user intranet state

The intranet stores completion/progress state per tenant and per user. Win the Day uses the
user's local ISO date as the state key so it resets daily for that person without sharing state
across users or workspaces.

Revision ID: 0053_intranet_user_state
Revises: 0052_restore_lost_palette
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID, JSONType

revision: str = "0053_intranet_user_state"
down_revision: Union[str, None] = "0052_restore_lost_palette"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "intranet_user_state" in _tables():
        return
    op.create_table(
        "intranet_user_state",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("user_id", GUID(), sa.ForeignKey("user.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("state_key", sa.String(64), nullable=False, server_default="global"),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("value", JSONType, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "user_id", "scope", "state_key",
                            name="uq_intranet_user_state"),
    )
    op.create_index(
        "ix_intranet_state_tenant_scope_key",
        "intranet_user_state",
        ["tenant_id", "scope", "state_key"],
    )


def downgrade() -> None:
    if "intranet_user_state" in _tables():
        op.drop_index("ix_intranet_state_tenant_scope_key", table_name="intranet_user_state")
        op.drop_table("intranet_user_state")
