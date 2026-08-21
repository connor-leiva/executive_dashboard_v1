"""Books COA Phase 4: shared-cost allocations — the policy rules and the contributions
derived from them (SPEC-coa-mapping-provenance 2.5).

Additive, and empty on arrival. Contributions are derived per period by the allocation sync;
rules are declared by a person, because an allocation is a policy decision and never the
bookkeeper's. Nothing on the dashboard reads either table until Phase 5.

Revision ID: 0043_allocations
Revises: 0042_apb_balance_end
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID

revision: str = "0043_allocations"
down_revision: Union[str, None] = "0042_apb_balance_end"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables(bind) -> set:
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table: str) -> set:
    return {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tabs = _tables(bind)

    if "allocation_rule" not in tabs:
        op.create_table(
            "allocation_rule",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            # NULL target = every entity in the tenant.
            sa.Column("target_business_id", GUID(),
                      sa.ForeignKey("business.id", ondelete="CASCADE"), nullable=True),
            sa.Column("source_business_id", GUID(),
                      sa.ForeignKey("business.id", ondelete="CASCADE"), nullable=False),
            sa.Column("pattern", sa.String(300), nullable=False),
            sa.Column("pool_name", sa.String(80), nullable=False),
            sa.Column("basis", sa.String(200), nullable=True),
            sa.Column("driver_source", sa.String(120), nullable=True),
            sa.Column("approved_by", GUID(), sa.ForeignKey("user.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("je_ref", sa.String(20), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "allocation_contribution" not in tabs:
        op.create_table(
            "allocation_contribution",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("period_start", sa.Date(), nullable=False),
            sa.Column("period_end", sa.Date(), nullable=False),
            sa.Column("source_business_id", GUID(),
                      sa.ForeignKey("business.id", ondelete="CASCADE"), nullable=False),
            sa.Column("target_business_id", GUID(),
                      sa.ForeignKey("business.id", ondelete="CASCADE"), nullable=False),
            sa.Column("standard_account_id", GUID(),
                      sa.ForeignKey("standard_account.id", ondelete="CASCADE"), nullable=False),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("booking", sa.String(10), nullable=False, server_default="observed"),
            sa.Column("pool_name", sa.String(80), nullable=False),
            sa.Column("basis", sa.String(200), nullable=True),
            sa.Column("driver_source", sa.String(120), nullable=True),
            sa.Column("approved_by", GUID(), sa.ForeignKey("user.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("je_ref", sa.String(20), nullable=True),
            sa.Column("rule_id", GUID(),
                      sa.ForeignKey("allocation_rule.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            # An entity cannot allocate to itself: that is a rename, not an allocation, and it
            # would net to zero while looking like movement.
            sa.CheckConstraint("source_business_id <> target_business_id",
                               name="ck_alloc_not_self"),
        )

    # Ensured outside the create_table guard — see the same note in 0039 and 0041.
    if "ix_alloc_period" not in _indexes(bind, "allocation_contribution"):
        op.create_index("ix_alloc_period", "allocation_contribution",
                        ["tenant_id", "period_start", "period_end"])


def downgrade() -> None:
    bind = op.get_bind()
    tabs = _tables(bind)
    if "allocation_contribution" in tabs:        # child first
        op.drop_table("allocation_contribution")
    if "allocation_rule" in tabs:
        op.drop_table("allocation_rule")
