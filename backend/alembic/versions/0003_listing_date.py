"""add listing_date to transaction

Revision ID: 0003_listing_date
Revises: 0002_sisu_txn_fields
Create Date: 2026-07-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_listing_date"
down_revision: Union[str, None] = "0002_sisu_txn_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transaction", sa.Column("listing_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("transaction", "listing_date")
