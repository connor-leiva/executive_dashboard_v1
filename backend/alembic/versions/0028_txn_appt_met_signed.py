"""transaction.appt_met_date + signed_date — Sisu appt_dt (appointment held) and signed_dt (agreement
signed), so the scorecard can auto-source Appointments Met and Clients Signed (same pattern as
close_date/contract_date). Nullable; idempotent. Local/SQLite builds from create_all.

Revision ID: 0028_txn_appt_met_signed
Revises: 0027_scorecard_cumulative_goal
Create Date: 2026-08-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0028_txn_appt_met_signed"
down_revision: Union[str, None] = "0027_scorecard_cumulative_goal"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "transaction", "appt_met_date"):
        op.add_column("transaction", sa.Column("appt_met_date", sa.Date(), nullable=True))
    if not _has_column(bind, "transaction", "signed_date"):
        op.add_column("transaction", sa.Column("signed_date", sa.Date(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "transaction", "signed_date"):
        op.drop_column("transaction", "signed_date")
    if _has_column(bind, "transaction", "appt_met_date"):
        op.drop_column("transaction", "appt_met_date")
