"""agent.start_date + agent.ended_date — the roster dates behind the L10 recruiting rows

Revision ID: 0095_agent_roster_dates
Revises: 0094_commissions
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0095_agent_roster_dates"
down_revision: Union[str, None] = "0094_commissions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent", sa.Column("start_date", sa.Date(), nullable=True))
    op.add_column("agent", sa.Column("ended_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent", "ended_date")
    op.drop_column("agent", "start_date")
