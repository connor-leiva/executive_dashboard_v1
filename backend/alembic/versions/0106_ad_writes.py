"""Ad write audit, kill switch, and per-brand autopilot.

Revision ID: 0106_ad_writes
Revises: 0105_ad_weekly_budget
"""
import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID, JSONType

revision = "0106_ad_writes"
down_revision = "0105_ad_weekly_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ad_account", sa.Column(
        "writes_killed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("ad_account", sa.Column("write_scope", sa.String(24), nullable=True))
    op.add_column("ad_account", sa.Column("write_scope_detail", sa.Text(), nullable=True))
    op.add_column("ad_account", sa.Column("autopilot", JSONType, nullable=True))
    op.create_table(
        "ad_write",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ad_account_id", GUID(), sa.ForeignKey("ad_account.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", GUID(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("actor_label", sa.String(200), nullable=False, server_default="Someone"),
        sa.Column("source", sa.String(16), nullable=False, server_default="user"),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("object_level", sa.String(16), nullable=False),
        sa.Column("object_external_id", sa.String(64), nullable=False),
        sa.Column("object_name", sa.String(400), nullable=True),
        sa.Column("before", JSONType, nullable=True),
        sa.Column("after", JSONType, nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("validated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("validation_note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="preview"),
        sa.Column("meta_error", sa.Text(), nullable=True),
        sa.Column("undo_of_id", GUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ad_write_tenant", "ad_write", ["tenant_id"])
    op.create_index("ix_ad_write_account", "ad_write", ["ad_account_id"])


def downgrade() -> None:
    op.drop_index("ix_ad_write_account", table_name="ad_write")
    op.drop_index("ix_ad_write_tenant", table_name="ad_write")
    op.drop_table("ad_write")
    op.drop_column("ad_account", "autopilot")
    op.drop_column("ad_account", "write_scope_detail")
    op.drop_column("ad_account", "write_scope")
    op.drop_column("ad_account", "writes_killed")
