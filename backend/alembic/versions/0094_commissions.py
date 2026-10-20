"""Commission tracking for the beCollective Sales Desk (2026-10-20).

Commission is owed on cash that ARRIVED, so the unit is a payment, not a member: a $14,400
plan pays its rep $1,200 at a time and the member reappears every month they keep paying.

Two tables and one column:
  commission_statement  a month, closed. A row means the period is settled.
  commission_payment    one cash event, attributed. `statement_id` locks it to a closed month.
  sales_rep.commission_pct   the rate, snapshotted onto each payment when its month closes.

Statement is created first: commission_payment carries an FK to it.

Revision ID: 0094_commissions
Revises: 0093_books_cleanup
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID

revision: str = "0094_commissions"
down_revision: Union[str, None] = "0093_books_cleanup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATEMENT = "commission_statement"
_PAYMENT = "commission_payment"


def _tables(bind):
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    have = _tables(bind)

    if _STATEMENT not in have:
        op.create_table(
            _STATEMENT,
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("launch_id", GUID(), sa.ForeignKey("launch.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("period", sa.String(7), nullable=False),
            sa.Column("total_cash", sa.Numeric(14, 2), server_default="0"),
            sa.Column("total_commission", sa.Numeric(14, 2), server_default="0"),
            sa.Column("payment_count", sa.Integer(), server_default="0"),
            sa.Column("vendor_id", GUID(), sa.ForeignKey("vendor.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("payable_id", GUID(), sa.ForeignKey("payable.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("closed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("closed_by", GUID(), sa.ForeignKey("user.id", ondelete="SET NULL"),
                      nullable=True),
            sa.UniqueConstraint("tenant_id", "launch_id", "period",
                                name="uq_commission_statement_period"),
        )
        op.create_index("ix_commission_statement_tenant_id", _STATEMENT, ["tenant_id"])
        op.create_index("ix_commission_statement_launch_id", _STATEMENT, ["launch_id"])

    if _PAYMENT not in have:
        op.create_table(
            _PAYMENT,
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("launch_id", GUID(), sa.ForeignKey("launch.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("external_id", sa.String(64), nullable=False),
            sa.Column("provider", sa.String(24), server_default="ghl"),
            sa.Column("contact_id", sa.String(64), nullable=True),
            sa.Column("opportunity_id", sa.String(64), nullable=True),
            sa.Column("member_name", sa.String(160), nullable=True),
            sa.Column("rep_email", sa.String(160), nullable=True),
            sa.Column("payment_type", sa.String(24), nullable=True),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("statement_id", GUID(),
                      sa.ForeignKey("commission_statement.id", ondelete="SET NULL"), nullable=True),
            sa.Column("rate_pct", sa.Numeric(6, 3), nullable=True),
            sa.Column("commission_amount", sa.Numeric(12, 2), nullable=True),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            # THE idempotency control: a re-sync must never create a second row for one charge.
            sa.UniqueConstraint("tenant_id", "provider", "external_id",
                                name="uq_commission_payment_ext"),
        )
        op.create_index("ix_commission_payment_tenant_id", _PAYMENT, ["tenant_id"])
        op.create_index("ix_commission_payment_launch_id", _PAYMENT, ["launch_id"])
        op.create_index("ix_commission_payment_contact_id", _PAYMENT, ["contact_id"])
        op.create_index("ix_commission_payment_opportunity_id", _PAYMENT, ["opportunity_id"])
        op.create_index("ix_commission_payment_rep_email", _PAYMENT, ["rep_email"])
        op.create_index("ix_commission_payment_statement_id", _PAYMENT, ["statement_id"])
        op.create_index("ix_commission_payment_paid_at", _PAYMENT, ["paid_at"])
        # The monthly report scans one launch over one month; without this it table-scans every
        # payment the cohort has ever taken.
        op.create_index("ix_commission_payment_period", _PAYMENT,
                        ["tenant_id", "launch_id", "paid_at"])

    if "sales_rep" in have:
        cols = {c["name"] for c in sa.inspect(bind).get_columns("sales_rep")}
        if "commission_pct" not in cols:
            op.add_column("sales_rep", sa.Column("commission_pct", sa.Numeric(6, 3), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    have = _tables(bind)
    if "sales_rep" in have:
        cols = {c["name"] for c in sa.inspect(bind).get_columns("sales_rep")}
        if "commission_pct" in cols:
            op.drop_column("sales_rep", "commission_pct")
    if _PAYMENT in have:
        op.drop_table(_PAYMENT)
    if _STATEMENT in have:
        op.drop_table(_STATEMENT)
