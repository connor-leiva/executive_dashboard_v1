"""Morning ads digest, one row per account per day.

Revision ID: 0107_ad_digest
Revises: 0106_ad_writes
"""
import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID, JSONType

revision = "0107_ad_digest"
down_revision = "0106_ad_writes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ad_digest",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ad_account_id", GUID(), sa.ForeignKey("ad_account.id", ondelete="CASCADE"), nullable=False),
        sa.Column("digest_on", sa.Date(), nullable=False),
        sa.Column("body", JSONType, nullable=False),
        sa.Column("emailed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("ad_account_id", "digest_on", name="uq_ad_digest_day"),
    )
    op.create_index("ix_ad_digest_tenant", "ad_digest", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_ad_digest_tenant", table_name="ad_digest")
    op.drop_table("ad_digest")
