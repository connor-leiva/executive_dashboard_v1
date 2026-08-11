"""transaction.title_vid — Sisu title_company_vid, so the scorecard can auto-source the Meraki
(title) attach rate the same way mortgage_vid drives the Sympli attach. Nullable; idempotent.
Local/SQLite builds from create_all.

Revision ID: 0029_txn_title_vid
Revises: 0028_txn_appt_met_signed
Create Date: 2026-08-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0029_txn_title_vid"
down_revision: Union[str, None] = "0028_txn_appt_met_signed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "transaction", "title_vid"):
        op.add_column("transaction", sa.Column("title_vid", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "transaction", "title_vid"):
        op.drop_column("transaction", "title_vid")
