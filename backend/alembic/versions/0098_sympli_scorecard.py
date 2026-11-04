"""The lending scorecard: transaction.lead_type, scorecard_group.lo_email, scorecard_gate

Revision ID: 0098_sympli_scorecard
Revises: 0097_lead_email
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID

revision: str = "0098_sympli_scorecard"
down_revision: Union[str, None] = "0097_lead_email"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transaction", sa.Column("lead_type", sa.String(80), nullable=True))
    op.add_column("scorecard_group", sa.Column("lo_email", sa.String(255), nullable=True))
    op.create_table(
        "scorecard_gate",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("business_id", GUID(), sa.ForeignKey("business.id"), nullable=False, index=True),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("label", sa.String(240), nullable=False),
        sa.Column("owner_name", sa.String(120), nullable=True),
        sa.Column("done_on", sa.Date(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("tenant_id", "business_id", "due_date", "label", name="uq_scorecard_gate"),
    )


def downgrade() -> None:
    op.drop_table("scorecard_gate")
    op.drop_column("scorecard_group", "lo_email")
    op.drop_column("transaction", "lead_type")
