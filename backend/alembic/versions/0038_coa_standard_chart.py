"""Books COA Phase 1: the standard_account table, the business archetype/gross_profit_label
config, and a seed of the authoritative chart for every existing tenant
(SPEC-chart-of-accounts, SPEC-coa-mapping-provenance section 2.1).

Additive and idempotent. Nothing reads standard_account yet — Phase 2 introduces coa_map and
the sync — so this migration cannot change any number currently on the dashboard.

The chart itself is imported from app.services.coa rather than copied in here. A second copy of
a chart of accounts is exactly the overlapping-source-of-truth problem this module exists to
solve, and the seeder is idempotent by design, so re-running it against a revised chart is the
intended path rather than a hazard.

Revision ID: 0038_coa_standard_chart
Revises: 0037_call_chapters
"""
from typing import Sequence, Union
import uuid

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID, JSONType

revision: str = "0038_coa_standard_chart"
down_revision: Union[str, None] = "0037_call_chapters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Archetype per entity. Spring B is "Program plus Event" in the chart document, and `program`
# is the correct single value for it: the activation matrix marks Event Revenue and Merchant
# as reachable from Program, so a program entity already opens 4200, 4400, 5200 and 5300.
_ARCHETYPE_BY_KEY = {
    "ulrg": ("transactional", "Company Dollar"),   # brokerages say Company Dollar, not Gross Profit
    "sympli": ("transactional", None),             # revenue is 4040 loan origination commission
    "springb": ("program", None),
    "forum": ("program", None),
    "becollective": ("program", None),
    "edge": ("program", None),
}


def _tables(bind) -> set:
    return set(sa.inspect(bind).get_table_names())


def _cols(bind, table: str) -> set:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tabs = _tables(bind)

    if "standard_account" not in tabs:
        op.create_table(
            "standard_account",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      index=True),
            sa.Column("code", sa.String(10), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("bucket", sa.String(32), nullable=False),
            sa.Column("statement", sa.String(2), nullable=False),
            sa.Column("section", sa.String(16), nullable=False),
            sa.Column("normal_balance", sa.String(6), nullable=False),
            sa.Column("parent_id", GUID(),
                      sa.ForeignKey("standard_account.id", ondelete="SET NULL"), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("is_intercompany_account", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
            sa.Column("recognition", sa.String(16), nullable=True),
            sa.Column("archetypes", JSONType, nullable=True),
            sa.Column("definition", sa.Text(), nullable=True),
            sa.UniqueConstraint("tenant_id", "code", name="uq_standard_account_code"),
        )

    if "business" in tabs:
        have = _cols(bind, "business")
        if "archetype" not in have:
            op.add_column("business", sa.Column("archetype", sa.String(16), nullable=False,
                                                server_default="transactional"))
        if "gross_profit_label" not in have:
            op.add_column("business", sa.Column("gross_profit_label", sa.String(40),
                                                nullable=False, server_default="Gross Profit"))

        # Set the archetypes we know. An entity whose key is not listed keeps the
        # `transactional` default and is set from Settings — a wrong archetype only means the
        # wrong leaf accounts are offered, never a wrong number.
        biz = sa.table("business", sa.column("key", sa.String),
                       sa.column("archetype", sa.String),
                       sa.column("gross_profit_label", sa.String))
        for key, (archetype, label) in _ARCHETYPE_BY_KEY.items():
            values = {"archetype": archetype}
            if label:
                values["gross_profit_label"] = label
            op.execute(biz.update().where(biz.c.key == key).values(**values))

    # ── seed the chart for every existing tenant ──
    from app.services.coa import chart_rows                     # local: keeps alembic import light

    sa_tbl = sa.table(
        "standard_account",
        sa.column("id", GUID()), sa.column("tenant_id", GUID()),
        sa.column("code", sa.String), sa.column("name", sa.String),
        sa.column("bucket", sa.String), sa.column("statement", sa.String),
        sa.column("section", sa.String), sa.column("normal_balance", sa.String),
        sa.column("sort_order", sa.Integer), sa.column("is_active", sa.Boolean),
        sa.column("is_intercompany_account", sa.Boolean),
        sa.column("recognition", sa.String), sa.column("archetypes", JSONType),
        sa.column("definition", sa.Text),
    )
    rows = chart_rows()
    for (tenant_id,) in bind.execute(sa.text("SELECT id FROM tenant")).fetchall():
        existing = {r[0] for r in bind.execute(
            sa.text("SELECT code FROM standard_account WHERE tenant_id = :t"),
            {"t": tenant_id}).fetchall()}
        payload = [{**r, "id": uuid.uuid4(), "tenant_id": tenant_id, "is_active": True}
                   for r in rows if r["code"] not in existing]
        if payload:
            op.bulk_insert(sa_tbl, payload)


def downgrade() -> None:
    bind = op.get_bind()
    if "standard_account" in _tables(bind):
        op.drop_table("standard_account")
    if "business" in _tables(bind):
        have = _cols(bind, "business")
        drop = [c for c in ("gross_profit_label", "archetype") if c in have]
        if drop:
            # batch mode so the downgrade/upgrade isolation check passes on SQLite too, where
            # DROP COLUMN needs a table rebuild. On Postgres this is a passthrough.
            with op.batch_alter_table("business") as b:
                for col in drop:
                    b.drop_column(col)
