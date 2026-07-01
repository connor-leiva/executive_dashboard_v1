"""add Sisu funnel fields to transaction

Revision ID: 0002_sisu_txn_fields
Revises: 0001_init
Create Date: 2026-07-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_sisu_txn_fields"
down_revision: Union[str, None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transaction", sa.Column("appt_set_date", sa.Date(), nullable=True))
    op.add_column("transaction", sa.Column("lead_date", sa.Date(), nullable=True))
    op.add_column("transaction", sa.Column("sisu_status_code", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("transaction", "sisu_status_code")
    op.drop_column("transaction", "lead_date")
    op.drop_column("transaction", "appt_set_date")
