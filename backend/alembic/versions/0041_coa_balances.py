"""Books COA Phase 3: the pulled trial balance (account_period_balance) and the per-tenant
settings the guard and the tie-out read (coa_settings).

Additive. Nothing on the dashboard reads either table yet — the mapped statement is built on
top of them in this same phase, and it is a new surface rather than a change to an existing
number.

`coa_settings` is seeded with one row per tenant at the spec's defaults, so the guard and the
tie-out never have to cope with a missing row. The service still defaults if one is absent,
because a tenant created later must not silently render unguarded.

Revision ID: 0041_coa_balances
Revises: 0040_coa_charitable_below_line
"""
from decimal import Decimal
from typing import Sequence, Union
import uuid

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID

revision: str = "0041_coa_balances"
down_revision: Union[str, None] = "0040_coa_charitable_below_line"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables(bind) -> set:
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table: str) -> set:
    return {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tabs = _tables(bind)

    if "account_period_balance" not in tabs:
        op.create_table(
            "account_period_balance",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("business_id", GUID(), sa.ForeignKey("business.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("period_start", sa.Date(), nullable=False),
            sa.Column("period_end", sa.Date(), nullable=False),
            sa.Column("qbo_account_id", sa.String(50), nullable=False),
            # Signed, debit-positive. The convention is applied once at ingest.
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("source", sa.String(20), nullable=False, server_default="qbo_tb"),
            sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", "business_id", "period_start", "period_end",
                                "qbo_account_id", name="uq_apb_account_period"),
        )

    if "coa_settings" not in tabs:
        op.create_table(
            "coa_settings",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, unique=True, index=True),
            sa.Column("ic_flag_threshold_pct", sa.Numeric(5, 2), nullable=False,
                      server_default="5.00"),
            sa.Column("tie_out_tolerance", sa.Numeric(6, 2), nullable=False,
                      server_default="0.01"),
            sa.Column("block_render_on_unmapped", sa.Boolean(), nullable=False,
                      server_default=sa.true()),
        )

    # Ensured outside the create_table guards: 0001_init create_all()s the CURRENT models, so a
    # brand-new database arrives with these tables already built from models.py — which carries
    # the single-column indexes but not this composite one. See the same note in 0039.
    if "ix_apb_period" not in _indexes(bind, "account_period_balance"):
        op.create_index("ix_apb_period", "account_period_balance",
                        ["tenant_id", "business_id", "period_start", "period_end"])

    # One settings row per tenant, at the spec's defaults. Every column is supplied
    # explicitly rather than leaning on the server defaults above: on a database built by
    # 0001_init's create_all() the table comes from models.py, whose defaults are Python-side,
    # so the columns carry no server default at all and a partial insert fails NOT NULL.
    tbl = sa.table(
        "coa_settings",
        sa.column("id", GUID()), sa.column("tenant_id", GUID()),
        sa.column("ic_flag_threshold_pct", sa.Numeric(5, 2)),
        sa.column("tie_out_tolerance", sa.Numeric(6, 2)),
        sa.column("block_render_on_unmapped", sa.Boolean()),
    )
    have = {r[0] for r in bind.execute(sa.text("SELECT tenant_id FROM coa_settings")).fetchall()}
    missing = [{"id": uuid.uuid4(), "tenant_id": t,
                "ic_flag_threshold_pct": Decimal("5.00"),
                "tie_out_tolerance": Decimal("0.01"),
                "block_render_on_unmapped": True}
               for (t,) in bind.execute(sa.text("SELECT id FROM tenant")).fetchall()
               if t not in have]
    if missing:
        op.bulk_insert(tbl, missing)


def downgrade() -> None:
    bind = op.get_bind()
    tabs = _tables(bind)
    if "account_period_balance" in tabs:
        op.drop_table("account_period_balance")
    if "coa_settings" in tabs:
        op.drop_table("coa_settings")
