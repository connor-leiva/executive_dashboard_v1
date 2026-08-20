"""Books COA Phase 2: the per-entity map (coa_map) and the subtree rules that keep it from
needing a human every time somebody is hired (coa_map_rule).

Additive and idempotent. Nothing reads either table on the dashboard yet — Phase 3 builds the
mapped statement — so this migration cannot change any number currently rendered.

Empty on arrival by design. coa_map fills from the coa_sync job on the next worker pass, and
coa_map_rule fills from decisions a human makes on the mapping screen. Seeding rules here
would be putting accounting policy in a migration, where nobody would ever look for it.

Revision ID: 0039_coa_map
Revises: 0038_coa_standard_chart
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID

revision: str = "0039_coa_map"
down_revision: Union[str, None] = "0038_coa_standard_chart"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables(bind) -> set:
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table: str) -> set:
    return {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tabs = _tables(bind)

    # coa_map_rule first: coa_map carries an FK to it.
    if "coa_map_rule" not in tabs:
        op.create_table(
            "coa_map_rule",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            # NULL business_id = every entity in the tenant.
            sa.Column("business_id", GUID(),
                      sa.ForeignKey("business.id", ondelete="CASCADE"), nullable=True),
            sa.Column("match_type", sa.String(8), nullable=False, server_default="prefix"),
            sa.Column("pattern", sa.String(300), nullable=False),
            sa.Column("standard_account_id", GUID(),
                      sa.ForeignKey("standard_account.id", ondelete="CASCADE"), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_by", GUID(), sa.ForeignKey("user.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        # No unique constraint on (tenant, business, pattern): business_id is nullable and
        # Postgres treats NULLs as distinct, so the constraint would silently permit exactly
        # the duplicate it exists to stop. The service checks instead (coa_map.create_rule).

    if "coa_map" not in tabs:
        op.create_table(
            "coa_map",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("business_id", GUID(), sa.ForeignKey("business.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("qbo_account_id", sa.String(50), nullable=False),
            sa.Column("qbo_account_name", sa.String(200), nullable=False),
            sa.Column("qbo_account_fqn", sa.String(400), nullable=True),
            sa.Column("qbo_account_type", sa.String(60), nullable=True),
            sa.Column("qbo_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            # NULL = unmapped. The whole guard in SPEC 5.3 keys on this being nullable.
            sa.Column("standard_account_id", GUID(),
                      sa.ForeignKey("standard_account.id", ondelete="SET NULL"), nullable=True),
            sa.Column("mapped_via", sa.String(8), nullable=True),      # manual | rule
            sa.Column("rule_id", GUID(), sa.ForeignKey("coa_map_rule.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("mapped_by", GUID(), sa.ForeignKey("user.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("mapped_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("is_ignored", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("ignore_reason", sa.String(200), nullable=True),
            sa.UniqueConstraint("tenant_id", "business_id", "qbo_account_id",
                                name="uq_coa_map_account"),
        )

    # Indexes are ensured OUTSIDE the create_table guards. 0001_init does a create_all() of the
    # CURRENT models, so on a brand-new database both tables arrive already built — from
    # models.py, which carries the single-column indexes but not these composite ones. Guarding
    # them on table creation would mean the two lookups this module makes constantly (the
    # unmapped list, and rule resolution per entity) quietly go unindexed on exactly the
    # databases created after this file was written.
    if "ix_coa_map_rule_scope" not in _indexes(bind, "coa_map_rule"):
        op.create_index("ix_coa_map_rule_scope", "coa_map_rule", ["tenant_id", "business_id"])
    if "ix_coa_map_unmapped" not in _indexes(bind, "coa_map"):
        op.create_index("ix_coa_map_unmapped", "coa_map",
                        ["tenant_id", "business_id", "standard_account_id"])


def downgrade() -> None:
    bind = op.get_bind()
    tabs = _tables(bind)
    if "coa_map" in tabs:                        # child first — it references coa_map_rule
        op.drop_table("coa_map")
    if "coa_map_rule" in tabs:
        op.drop_table("coa_map_rule")
