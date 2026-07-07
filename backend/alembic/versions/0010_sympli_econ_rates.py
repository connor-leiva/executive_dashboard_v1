"""add lo_comp_rate + opex_rate to business (Sympli JV economics)

Reverse-engineered from the May QBO P&L: loan-officer comp ≈ 55% of commission
revenue (the configurable cost-of-sale line) and operating costs ≈ 29% — so the
Sympli calculated financials fall through to NOI and Spring's 50% JV share.

Revision ID: 0010_sympli_econ_rates
Revises: 0009_capture_target
Create Date: 2026-07-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010_sympli_econ_rates"
down_revision: Union[str, None] = "0009_capture_target"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "business", "lo_comp_rate"):
        op.add_column("business", sa.Column("lo_comp_rate", sa.Numeric(5, 4), nullable=True))
    if not _has_column(bind, "business", "opex_rate"):
        op.add_column("business", sa.Column("opex_rate", sa.Numeric(5, 4), nullable=True))
    # Seed the Sympli row with the rates derived from the May books so the
    # calculated financials work the moment Arive is connected in prod.
    op.execute("UPDATE business SET lo_comp_rate = 0.55, opex_rate = 0.29 "
               "WHERE key = 'sympli' AND lo_comp_rate IS NULL")


def downgrade() -> None:
    op.drop_column("business", "opex_rate")
    op.drop_column("business", "lo_comp_rate")
