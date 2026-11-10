"""Lead-source groups, their QuickBooks spend rules and cost shares; vendor id and credit flag on book_txn.

Phase 1 of docs/specs/ulrg-reporting-dashboard-port.md.

  lead_source_group        a named set of Follow Up Boss source strings, per tenant AND business
  lead_source_spend_rule   which QuickBooks lines are that group's spend (account / vendor / class)
  lead_source_cost_share   another business bearing a percent of that spend, by agreement
  book_txn.vendor_qbo_id   the payee's QuickBooks id, which books_sync used to drop
  book_txn.purchase_credit a Purchase QuickBooks marks `Credit` -- a refund, not spend

THE SEED, for a business keyed `ulrg` only, and only rows that are missing:
  realtor_com  Realtor.com, active, contains-match on the six variants Utah Life's Follow Up Boss
               uses, program start 2026-02-01, EXP risk defaults $69 / $49. Three spend rules,
               OR'd: account 365, vendor 962 (Realtor.com), vendor 1568 (Opcity, Inc). A 50% cost
               share borne by the business keyed `sympli` when one exists in the same tenant.
  realty_com   Realty.com, INACTIVE, no variants, no spend rule (spec §12.3).
No dollar amount, no class, no rule on Sympli's books, and nothing for any other tenant.

The seed is written with Core statements against this file's own table shapes rather than through
app.models, so a column added to these tables later cannot break a fresh run of this migration.

ADDITIVE AND GUARDED: `0001_init` builds a fresh database from the current models, which already
carry every table and column here, so each step checks before it creates.

Revision ID: 0104_lead_source_groups
Revises: 0103_sb_round2
"""
import datetime as dt
import uuid
from decimal import Decimal
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID, JSONType

revision: str = "0104_lead_source_groups"
down_revision: Union[str, None] = "0103_sb_round2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_GROUP, _RULE, _SHARE = "lead_source_group", "lead_source_spend_rule", "lead_source_cost_share"

REALTOR_VARIANTS = ["Realtor.com", "Realtor.com MVIP", "Realtor MVIP", "Market VIP via Opcity BLA",
                    "market vip via opcity bla", "realtor.com mvip"]
REALTOR_RULES = [("365", None), (None, "962"), (None, "1568")]      # (account, vendor)


def _tables(bind) -> set:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> set:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _indexes(bind, table: str) -> set:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return set()
    return {i["name"] for i in insp.get_indexes(table)}


def _create(bind) -> None:
    have = _tables(bind)
    if _GROUP not in have:
        op.create_table(
            _GROUP,
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
            sa.Column("business_id", GUID(), sa.ForeignKey("business.id", ondelete="CASCADE"), nullable=False),
            sa.Column("key", sa.String(64), nullable=False),
            sa.Column("display_name", sa.String(120), nullable=False),
            sa.Column("variants", JSONType, nullable=False),
            sa.Column("match_mode", sa.String(10), nullable=False),
            sa.Column("program_start", sa.Date(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("display_order", sa.Integer(), nullable=False),
            sa.Column("exp_risk_team_default", sa.Numeric(10, 2), nullable=False),
            sa.Column("exp_risk_agent_default", sa.Numeric(10, 2), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", "business_id", "key", name="uq_lead_source_group_key"),
        )
    if _RULE not in have:
        op.create_table(
            _RULE,
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
            sa.Column("lead_source_group_id", GUID(), sa.ForeignKey(f"{_GROUP}.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("business_id", GUID(), sa.ForeignKey("business.id", ondelete="CASCADE"), nullable=False),
            sa.Column("account_qbo_id", sa.String(32), nullable=True),
            sa.Column("vendor_qbo_id", sa.String(32), nullable=True),
            sa.Column("class_label", sa.String(200), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("account_qbo_id IS NOT NULL OR vendor_qbo_id IS NOT NULL "
                               "OR class_label IS NOT NULL", name="ck_lead_source_spend_rule_not_empty"),
        )
    if _SHARE not in have:
        op.create_table(
            _SHARE,
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
            sa.Column("lead_source_group_id", GUID(), sa.ForeignKey(f"{_GROUP}.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("bearer_business_id", GUID(), sa.ForeignKey("business.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("share_pct", sa.Numeric(5, 2), nullable=False),
            sa.UniqueConstraint("lead_source_group_id", "bearer_business_id", name="uq_lead_source_cost_share"),
            sa.CheckConstraint("share_pct >= 0 AND share_pct <= 100", name="ck_lead_source_cost_share_pct"),
        )
    # Named as the models name them (`index=True` -> ix_<table>_<column>), outside the create
    # guards, so a database built by create_all and one built here end up identical.
    for table, cols in ((_GROUP, ("tenant_id", "business_id")),
                        (_RULE, ("tenant_id", "lead_source_group_id")),
                        (_SHARE, ("tenant_id", "lead_source_group_id"))):
        have_ix = _indexes(bind, table)
        for col in cols:
            name = f"ix_{table}_{col}"
            if name not in have_ix:
                op.create_index(name, table, [col])

    cols = _columns(bind, "book_txn")
    if "book_txn" in _tables(bind):
        if "vendor_qbo_id" not in cols:
            op.add_column("book_txn", sa.Column("vendor_qbo_id", sa.String(32), nullable=True))
        if "purchase_credit" not in cols:
            op.add_column("book_txn", sa.Column("purchase_credit", sa.Boolean(), nullable=True))


def _seed(bind) -> None:
    md = sa.MetaData()
    business = sa.Table("business", md, sa.Column("id", GUID()), sa.Column("tenant_id", GUID()),
                        sa.Column("key", sa.String(32)))
    group = sa.Table(_GROUP, md, sa.Column("id", GUID()), sa.Column("tenant_id", GUID()),
                     sa.Column("business_id", GUID()), sa.Column("key", sa.String(64)),
                     sa.Column("display_name", sa.String(120)), sa.Column("variants", JSONType),
                     sa.Column("match_mode", sa.String(10)), sa.Column("program_start", sa.Date()),
                     sa.Column("is_active", sa.Boolean()), sa.Column("display_order", sa.Integer()),
                     sa.Column("exp_risk_team_default", sa.Numeric(10, 2)),
                     sa.Column("exp_risk_agent_default", sa.Numeric(10, 2)))
    rule = sa.Table(_RULE, md, sa.Column("id", GUID()), sa.Column("tenant_id", GUID()),
                    sa.Column("lead_source_group_id", GUID()), sa.Column("business_id", GUID()),
                    sa.Column("account_qbo_id", sa.String(32)), sa.Column("vendor_qbo_id", sa.String(32)))
    share = sa.Table(_SHARE, md, sa.Column("id", GUID()), sa.Column("tenant_id", GUID()),
                     sa.Column("lead_source_group_id", GUID()), sa.Column("bearer_business_id", GUID()),
                     sa.Column("share_pct", sa.Numeric(5, 2)))

    for ulrg_id, tid in bind.execute(sa.select(business.c.id, business.c.tenant_id)
                                     .where(business.c.key == "ulrg")).all():
        have = {k for (k,) in bind.execute(sa.select(group.c.key).where(
            group.c.tenant_id == tid, group.c.business_id == ulrg_id)).all()}
        if "realtor_com" not in have:
            gid = uuid.uuid4()
            bind.execute(group.insert().values(
                id=gid, tenant_id=tid, business_id=ulrg_id, key="realtor_com",
                display_name="Realtor.com", variants=REALTOR_VARIANTS, match_mode="contains",
                program_start=dt.date(2026, 2, 1), is_active=True, display_order=0,
                exp_risk_team_default=Decimal("69"), exp_risk_agent_default=Decimal("49")))
            for acct, vendor in REALTOR_RULES:
                bind.execute(rule.insert().values(id=uuid.uuid4(), tenant_id=tid,
                                                  lead_source_group_id=gid, business_id=ulrg_id,
                                                  account_qbo_id=acct, vendor_qbo_id=vendor))
            sympli = bind.execute(sa.select(business.c.id).where(
                business.c.tenant_id == tid, business.c.key == "sympli")).scalar()
            if sympli is not None:
                bind.execute(share.insert().values(id=uuid.uuid4(), tenant_id=tid,
                                                   lead_source_group_id=gid,
                                                   bearer_business_id=sympli,
                                                   share_pct=Decimal("50.00")))
        if "realty_com" not in have:
            bind.execute(group.insert().values(
                id=uuid.uuid4(), tenant_id=tid, business_id=ulrg_id, key="realty_com",
                display_name="Realty.com", variants=[], match_mode="contains", program_start=None,
                is_active=False, display_order=10, exp_risk_team_default=Decimal("69"),
                exp_risk_agent_default=Decimal("49")))


def upgrade() -> None:
    bind = op.get_bind()
    _create(bind)
    _seed(bind)


def downgrade() -> None:
    bind = op.get_bind()
    have = _tables(bind)
    for table in (_SHARE, _RULE, _GROUP):
        if table in have:
            op.drop_table(table)
    cols = _columns(bind, "book_txn")
    with op.batch_alter_table("book_txn") as b:
        for col in ("purchase_credit", "vendor_qbo_id"):
            if col in cols:
                b.drop_column(col)
