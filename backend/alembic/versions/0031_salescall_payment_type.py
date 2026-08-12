"""Sales Desk: SalesCall.payment_type (PIF/Financed/Monthly/Custom) for the payment mix.

Revision ID: 0031_salescall_payment_type
Revises: 0030_sales_desk
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031_salescall_payment_type"
down_revision: Union[str, None] = "0030_sales_desk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(bind, table: str) -> set:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "sales_call" in sa.inspect(bind).get_table_names() and "payment_type" not in _cols(bind, "sales_call"):
        op.add_column("sales_call", sa.Column("payment_type", sa.String(24), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "sales_call" in sa.inspect(bind).get_table_names() and "payment_type" in _cols(bind, "sales_call"):
        op.drop_column("sales_call", "payment_type")
