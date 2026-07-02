"""three-lens financials columns

Revision ID: 0006_three_lens_financials
Revises: 0005_business_thresholds
Create Date: 2026-07-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_three_lens_financials"
down_revision: Union[str, None] = "0005_business_thresholds"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(bind, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _add(bind, table: str, column: sa.Column) -> None:
    if column.name not in _cols(bind, table):
        op.add_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()
    _add(bind, "transaction", sa.Column("agent_commission", sa.Numeric(14, 2), nullable=True))
    _add(bind, "transaction", sa.Column("expected_close_date", sa.Date(), nullable=True))
    _add(bind, "business", sa.Column("expense_run_rate_mode", sa.String(16),
                                     nullable=False, server_default="trailing_3mo"))
    _add(bind, "business", sa.Column("expense_run_rate_manual", sa.Numeric(14, 2), nullable=True))
    _add(bind, "business", sa.Column("default_agent_split", sa.Numeric(5, 4), nullable=True))
    _add(bind, "pl_snapshot", sa.Column("books_closed", sa.Boolean(),
                                        nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("pl_snapshot", "books_closed")
    op.drop_column("business", "default_agent_split")
    op.drop_column("business", "expense_run_rate_manual")
    op.drop_column("business", "expense_run_rate_mode")
    op.drop_column("transaction", "expected_close_date")
    op.drop_column("transaction", "agent_commission")
