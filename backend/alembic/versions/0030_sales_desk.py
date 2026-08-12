"""Sales Desk: sales_call / sales_call_change / sales_rep tables, and Launch price_map /
default_tz / history_since (SPEC-becollective-salesdesk §4). Additive — the legacy
ticket_pif/ticket_plan/mix_pif columns stay until the compute rewrite so the live Launch
tab keeps working; price_map is backfilled from them for existing launches.

Revision ID: 0030_sales_desk
Revises: 0029_txn_title_vid
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID, JSONType

revision: str = "0030_sales_desk"
down_revision: Union[str, None] = "0029_txn_title_vid"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables(bind) -> set:
    return set(sa.inspect(bind).get_table_names())


def _cols(bind, table: str) -> set:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _price_map(pif: float, plan: float) -> dict:
    """§5 default shape, seeded off the legacy two-price model (PIF + Financed=plan). Monthly
    and Custom are the provisional tiers; real counts price them once enrollments arrive."""
    return {
        "PIF":      {"acv": pif,   "upfront": pif,  "monthly": 0,    "months": 0,  "provisional": False},
        "Financed": {"acv": plan,  "upfront": 5000, "monthly": 750,  "months": 12, "provisional": False},
        "Monthly":  {"acv": 14400, "upfront": 1200, "monthly": 1200, "months": 12, "provisional": True},
        "Custom":   {"acv": None,  "upfront": None, "monthly": None, "months": 0,  "provisional": True},
    }


def upgrade() -> None:
    bind = op.get_bind()
    tabs = _tables(bind)

    if "sales_call" not in tabs:
        op.create_table(
            "sales_call",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True),
            sa.Column("launch_id", GUID(), sa.ForeignKey("launch.id", ondelete="CASCADE"), index=True),
            sa.Column("opportunity_id", sa.String(64), nullable=False, index=True),
            sa.Column("contact_id", sa.String(64), nullable=True),
            sa.Column("contact_name", sa.String(160), nullable=True),
            sa.Column("booking_id", sa.String(64), nullable=True, index=True),
            sa.Column("rep_email", sa.String(160), nullable=True, index=True),
            sa.Column("call_time_raw", sa.String(120), nullable=True),
            sa.Column("call_time_utc", sa.DateTime(timezone=True), nullable=True),
            sa.Column("outcome", sa.String(32), nullable=True),
            sa.Column("outcome_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.UniqueConstraint("tenant_id", "opportunity_id", "booking_id", name="uq_sales_call_booking"),
        )

    if "sales_call_change" not in tabs:
        op.create_table(
            "sales_call_change",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True),
            sa.Column("sales_call_id", GUID(), sa.ForeignKey("sales_call.id", ondelete="CASCADE"), index=True),
            sa.Column("field", sa.String(32), nullable=False),
            sa.Column("old_value", sa.String(160), nullable=True),
            sa.Column("new_value", sa.String(160), nullable=True),
            sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "sales_rep" not in tabs:
        op.create_table(
            "sales_rep",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True),
            sa.Column("email", sa.String(160), nullable=False),
            sa.Column("display_name", sa.String(80), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.UniqueConstraint("tenant_id", "email", name="uq_sales_rep_email"),
        )

    if "launch" in tabs:
        have = _cols(bind, "launch")
        if "price_map" not in have:
            op.add_column("launch", sa.Column("price_map", JSONType, nullable=True))
        if "default_tz" not in have:
            op.add_column("launch", sa.Column("default_tz", sa.String(48), nullable=False,
                                              server_default="America/Denver"))
        if "history_since" not in have:
            op.add_column("launch", sa.Column("history_since", sa.DateTime(timezone=True), nullable=True))

        # Backfill price_map from the legacy two-price columns for launches that lack one.
        launch = sa.table("launch",
                          sa.column("id", GUID()),
                          sa.column("ticket_pif", sa.Numeric(12, 2)),
                          sa.column("ticket_plan", sa.Numeric(12, 2)),
                          sa.column("price_map", JSONType))
        for rid, pif, plan, pm in bind.execute(sa.select(
                launch.c.id, launch.c.ticket_pif, launch.c.ticket_plan, launch.c.price_map)).fetchall():
            if pm:
                continue
            bind.execute(launch.update().where(launch.c.id == rid).values(
                price_map=_price_map(float(pif or 12000), float(plan or 14000))))


def downgrade() -> None:
    bind = op.get_bind()
    tabs = _tables(bind)
    if "launch" in tabs:
        have = _cols(bind, "launch")
        for col in ("history_since", "default_tz", "price_map"):
            if col in have:
                op.drop_column("launch", col)
    for t in ("sales_call_change", "sales_call", "sales_rep"):
        if t in tabs:
            op.drop_table(t)
