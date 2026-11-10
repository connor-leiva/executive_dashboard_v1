"""Planned weekly ad budget per brand group.

Revision ID: 0105_ad_weekly_budget
Revises: 0104_sb_title_case
"""
import sqlalchemy as sa
from alembic import op

from app.dbtypes import JSONType

revision = "0105_ad_weekly_budget"
down_revision = "0104_sb_title_case"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ad_account", sa.Column("weekly_budgets", JSONType, nullable=True))


def downgrade() -> None:
    op.drop_column("ad_account", "weekly_budgets")
