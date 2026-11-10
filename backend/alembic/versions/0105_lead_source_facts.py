"""The facts the lead-source reports read: CRM activity, Sisu commission lines, the lead join.

Phase 2 of docs/specs/ulrg-reporting-dashboard-port.md.

  crm_activity      one Follow Up Boss call, text, email event or appointment per row: direction,
                    time, duration, whether it had a note, appointment outcome. No note text, no
                    recording, no phone number.
  transaction       the commission lines the report's company dollar is built from (trans fee,
                    adjusted GCI, team income, agent payment, both EXP risk fees and where they came
                    from, referral fee out, company dollar), the report's volume, the explicit lost
                    signal, when the lines were read, and `lead_id` -- the FUB lead the deal came from.
  lead              when its activity was last read, the Sisu contact it resolved to, when that was
                    looked up, and FUB's `price`.

Nothing is backfilled here: the next Follow Up Boss and Sisu syncs fill these for in-scope people.
ADDITIVE AND GUARDED, for the same reason as every migration since 0001 builds from the models.

Revision ID: 0105_lead_source_facts
Revises: 0104_lead_source_groups
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID

revision: str = "0105_lead_source_facts"
down_revision: Union[str, None] = "0104_lead_source_groups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACT = "crm_activity"

TXN_COLUMNS = (
    ("trans_fee", sa.Numeric(14, 2)), ("adjusted_gci", sa.Numeric(14, 2)),
    ("team_income", sa.Numeric(14, 2)), ("agent_payment", sa.Numeric(14, 2)),
    ("exp_risk_team", sa.Numeric(14, 2)), ("exp_risk_agent", sa.Numeric(14, 2)),
    ("exp_risk_source", sa.String(8)), ("referral_fee_out", sa.Numeric(14, 2)),
    ("company_dollar", sa.Numeric(14, 2)), ("closed_volume", sa.Numeric(14, 2)),
    ("lost_signal", sa.Boolean()), ("commission_read_at", sa.DateTime(timezone=True)),
)
LEAD_COLUMNS = (
    ("activity_synced_at", sa.DateTime(timezone=True)), ("sisu_contact_id", sa.String(32)),
    ("sisu_checked_at", sa.DateTime(timezone=True)), ("price", sa.Numeric(14, 2)),
)


def _tables(bind) -> set:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> set:
    insp = sa.inspect(bind)
    return {c["name"] for c in insp.get_columns(table)} if table in insp.get_table_names() else set()


def _indexes(bind, table: str) -> set:
    insp = sa.inspect(bind)
    return {i["name"] for i in insp.get_indexes(table)} if table in insp.get_table_names() else set()


def upgrade() -> None:
    bind = op.get_bind()
    if _ACT not in _tables(bind):
        op.create_table(
            _ACT,
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
            sa.Column("business_id", GUID(), sa.ForeignKey("business.id"), nullable=False),
            sa.Column("source", sa.String(24), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("external_id", sa.String(64), nullable=False),
            sa.Column("person_external_id", sa.String(64), nullable=False),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("direction", sa.String(10), nullable=False),
            sa.Column("is_live", sa.Boolean(), nullable=False),
            sa.Column("duration_sec", sa.Numeric(10, 1), nullable=True),
            sa.Column("has_note", sa.Boolean(), nullable=False),
            sa.Column("outcome", sa.String(60), nullable=True),
            sa.Column("channel", sa.String(8), nullable=False),
            sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("tenant_id", "source", "kind", "external_id", name="uq_crm_activity_src"),
        )
    have = _indexes(bind, _ACT)
    if "ix_crm_activity_tenant_id" not in have:
        op.create_index("ix_crm_activity_tenant_id", _ACT, ["tenant_id"])
    if "ix_crm_activity_person" not in have:
        op.create_index("ix_crm_activity_person", _ACT, ["tenant_id", "person_external_id"])

    tables = _tables(bind)
    if "transaction" in tables:
        cols = _columns(bind, "transaction")
        for name, typ in TXN_COLUMNS:
            if name not in cols:
                op.add_column("transaction", sa.Column(name, typ, nullable=True))
        if "lead_id" not in cols:
            # batch mode: SQLite can only add a foreign key by rebuilding the table, and Postgres
            # takes the same statement as a plain ALTER.
            with op.batch_alter_table("transaction") as b:
                b.add_column(sa.Column("lead_id", GUID(), nullable=True))
                b.create_foreign_key("fk_transaction_lead_id", "lead", ["lead_id"], ["id"],
                                     ondelete="SET NULL")
        have = _indexes(bind, "transaction")
        if "ix_txn_lead" not in have:
            op.create_index("ix_txn_lead", "transaction", ["tenant_id", "lead_id"])
        if "ix_txn_buyer_email" not in have:
            op.create_index("ix_txn_buyer_email", "transaction", ["tenant_id", "business_id", "buyer_email"])
    if "lead" in tables:
        cols = _columns(bind, "lead")
        for name, typ in LEAD_COLUMNS:
            if name not in cols:
                op.add_column("lead", sa.Column(name, typ, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if _ACT in tables:
        op.drop_table(_ACT)
    if "transaction" in tables:
        have = _indexes(bind, "transaction")
        for ix in ("ix_txn_lead", "ix_txn_buyer_email"):
            if ix in have:
                op.drop_index(ix, table_name="transaction")
        cols = _columns(bind, "transaction")
        with op.batch_alter_table("transaction") as b:
            if "lead_id" in cols:
                fks = {fk["name"] for fk in sa.inspect(bind).get_foreign_keys("transaction")}
                if "fk_transaction_lead_id" in fks:
                    b.drop_constraint("fk_transaction_lead_id", type_="foreignkey")
                b.drop_column("lead_id")
            for name, _typ in TXN_COLUMNS:
                if name in cols:
                    b.drop_column(name)
    if "lead" in tables:
        cols = _columns(bind, "lead")
        with op.batch_alter_table("lead") as b:
            for name, _typ in LEAD_COLUMNS:
                if name in cols:
                    b.drop_column(name)
