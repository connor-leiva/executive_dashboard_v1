"""scorecard_group.business_id nullable — a WORKSPACE board (the Portfolio scorecard) belongs to no business

Every existing board's queries filter groups by `business_id == <its business>`, so a group with no
business can never appear on them; only the workspace scope asks for `business_id IS NULL`.

Revision ID: 0100_workspace_scorecard
Revises: 0099_skyslope
"""
from typing import Sequence, Union

from alembic import op

from app.dbtypes import GUID

revision: str = "0100_workspace_scorecard"
down_revision: Union[str, None] = "0099_skyslope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("scorecard_group", "business_id", existing_type=GUID(), nullable=True)


def downgrade() -> None:
    # Workspace groups have nowhere to go back to; drop them (cascades to their rows) first.
    op.execute("DELETE FROM scorecard_group WHERE business_id IS NULL")
    op.alter_column("scorecard_group", "business_id", existing_type=GUID(), nullable=False)
