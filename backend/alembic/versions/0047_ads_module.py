"""Meta Ads module: the seven tables that join ad spend to closed revenue

SPEC-ads-module.md Part 6. Purely additive - no existing table is touched, so this cannot move
any number the dashboard renders today.

TWO CONSTRAINTS LEARNED THE HARD WAY, both from 0046, which crash-looped production twice in one
afternoon and for two different reasons:

  1. The revision id must fit VARCHAR(32). 0046 shipped at 34 characters and Postgres refused the
     STAMP after the body had already run - a migration that worked, failing anyway. SQLite does
     not enforce column length, so the whole suite passed. `0047_ads_module` is 15.

  2. Anything only exercised on Postgres is untested here. The suite runs SQLite and migrations do
     not replay from scratch (0001 does create_all), so a migration body can reach production
     having never executed anywhere. This one is plain DDL through op.create_table with no raw
     SQL and no dialect branch, which is the shape that cannot develop that class of fault. If a
     later revision needs raw SQL, compile it against the Postgres dialect in a test first.

Idempotent throughout, copying 0021_ai_employees: every create is guarded, so a partial apply
followed by a retry is safe.

SEEDS NOTHING. Ad accounts are user data; funnel definitions are code constants.

Revision ID: 0047_ads_module
Revises: 0046_grandfather_defaults
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID, JSONType

revision: str = "0047_ads_module"
down_revision: Union[str, None] = "0046_grandfather_defaults"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("ad_account", "ad_campaign", "ad", "ad_insight_daily",
          "ad_attribution", "ad_conversion", "ad_cohort_curve")


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def _has_index(bind, table: str, name: str) -> bool:
    if not _has_table(bind, table):
        return False
    return name in {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def _tenant():
    """Every table in this module is tenant-scoped. No exceptions, and CASCADE so removing a
    workspace removes its ad history with it rather than orphaning it."""
    return sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                     nullable=False, index=True)


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "ad_account"):
        op.create_table(
            "ad_account",
            sa.Column("id", GUID(), primary_key=True),
            _tenant(),
            sa.Column("integration_id", GUID(),
                      sa.ForeignKey("integration.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("business_id", GUID(),
                      sa.ForeignKey("business.id", ondelete="SET NULL"), nullable=True),
            sa.Column("platform", sa.String(16), server_default="meta"),
            sa.Column("external_id", sa.String(64), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("currency", sa.String(8), server_default="USD"),
            sa.Column("timezone_name", sa.String(64), nullable=True),
            sa.Column("status", sa.String(16), server_default="active"),
            sa.Column("group_rules", JSONType, nullable=True),
            sa.Column("lead_actions", JSONType, nullable=True),
            sa.Column("thresholds", JSONType, nullable=True),
            sa.Column("funnel_override", JSONType, nullable=True),
            sa.Column("utm_template", sa.String(400), nullable=True),
            sa.Column("backfill_start", sa.Date(), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", "platform", "external_id", name="uq_ad_account_ext"),
        )

    if not _has_table(bind, "ad_campaign"):
        op.create_table(
            "ad_campaign",
            sa.Column("id", GUID(), primary_key=True),
            _tenant(),
            sa.Column("ad_account_id", GUID(),
                      sa.ForeignKey("ad_account.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("external_id", sa.String(64), nullable=False),
            sa.Column("name", sa.String(400), nullable=False),
            sa.Column("objective", sa.String(48), nullable=True),
            sa.Column("status", sa.String(24), nullable=True),
            sa.Column("effective_status", sa.String(24), nullable=True),
            sa.Column("daily_budget", sa.Numeric(14, 2), nullable=True),
            sa.Column("lifetime_budget", sa.Numeric(14, 2), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("first_seen_on", sa.Date(), nullable=True),
            sa.Column("last_seen_on", sa.Date(), nullable=True),
            sa.UniqueConstraint("tenant_id", "ad_account_id", "external_id",
                                name="uq_ad_campaign_ext"),
        )

    if not _has_table(bind, "ad"):
        op.create_table(
            "ad",
            sa.Column("id", GUID(), primary_key=True),
            _tenant(),
            sa.Column("ad_account_id", GUID(),
                      sa.ForeignKey("ad_account.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("campaign_id", GUID(),
                      sa.ForeignKey("ad_campaign.id", ondelete="SET NULL"), nullable=True),
            sa.Column("external_id", sa.String(64), nullable=False),
            sa.Column("name", sa.String(400), nullable=False),
            sa.Column("adset_external_id", sa.String(64), nullable=True),
            sa.Column("adset_name", sa.String(400), nullable=True),
            sa.Column("status", sa.String(24), nullable=True),
            sa.Column("effective_status", sa.String(24), nullable=True),
            sa.Column("creative_external_id", sa.String(64), nullable=True),
            sa.Column("headline", sa.String(300), nullable=True),
            sa.Column("body", sa.Text(), nullable=True),
            sa.Column("thumbnail_url", sa.Text(), nullable=True),
            sa.Column("image_hash", sa.String(64), nullable=True),
            sa.Column("image_ref", sa.String(500), nullable=True),
            sa.Column("permalink", sa.Text(), nullable=True),
            sa.Column("url_tags", sa.Text(), nullable=True),
            sa.Column("creative_fetched_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("first_seen_on", sa.Date(), nullable=True),
            sa.Column("last_seen_on", sa.Date(), nullable=True),
            sa.UniqueConstraint("tenant_id", "ad_account_id", "external_id", name="uq_ad_ext"),
        )
    if not _has_index(bind, "ad", "ix_ad_campaign"):
        op.create_index("ix_ad_campaign", "ad", ["tenant_id", "campaign_id"])

    if not _has_table(bind, "ad_insight_daily"):
        op.create_table(
            "ad_insight_daily",
            sa.Column("id", GUID(), primary_key=True),
            _tenant(),
            sa.Column("ad_account_id", GUID(),
                      sa.ForeignKey("ad_account.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("level", sa.String(10), nullable=False),
            sa.Column("object_external_id", sa.String(64), nullable=False),
            sa.Column("campaign_id", GUID(),
                      sa.ForeignKey("ad_campaign.id", ondelete="CASCADE"), nullable=True),
            sa.Column("ad_id", GUID(), sa.ForeignKey("ad.id", ondelete="CASCADE"), nullable=True),
            sa.Column("occurred_on", sa.Date(), nullable=False),
            sa.Column("spend", sa.Numeric(14, 2), server_default="0"),
            sa.Column("impressions", sa.Integer(), server_default="0"),
            sa.Column("reach", sa.Integer(), nullable=True),
            sa.Column("clicks", sa.Integer(), server_default="0"),
            sa.Column("inline_link_clicks", sa.Integer(), server_default="0"),
            sa.Column("frequency", sa.Numeric(8, 4), nullable=True),
            sa.Column("actions", JSONType, nullable=True),
            sa.Column("action_values", JSONType, nullable=True),
            sa.Column("leads", sa.Integer(), server_default="0"),
            sa.Column("purchase_roas", sa.Numeric(10, 4), nullable=True),
            sa.Column("attribution", sa.String(40), nullable=True),
            sa.Column("pulled_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            # Restatement is an UPDATE against this constraint. Meta revises recent days as
            # attribution settles, so a sync that could only insert would freeze every prior day
            # at its first, understated value.
            sa.UniqueConstraint("tenant_id", "ad_account_id", "level", "object_external_id",
                                "occurred_on", name="uq_ad_insight_day"),
        )
    if not _has_index(bind, "ad_insight_daily", "ix_ad_insight_range"):
        op.create_index("ix_ad_insight_range", "ad_insight_daily",
                        ["tenant_id", "ad_account_id", "occurred_on"])
    if not _has_index(bind, "ad_insight_daily", "ix_ad_insight_obj"):
        op.create_index("ix_ad_insight_obj", "ad_insight_daily",
                        ["tenant_id", "level", "campaign_id"])

    if not _has_table(bind, "ad_attribution"):
        op.create_table(
            "ad_attribution",
            sa.Column("id", GUID(), primary_key=True),
            _tenant(),
            sa.Column("identity_kind", sa.String(16), nullable=False),
            sa.Column("identity_key", sa.String(160), nullable=False),
            sa.Column("email_norm", sa.String(255), nullable=True),
            sa.Column("business_id", GUID(),
                      sa.ForeignKey("business.id", ondelete="SET NULL"), nullable=True),
            sa.Column("ad_account_id", GUID(),
                      sa.ForeignKey("ad_account.id", ondelete="SET NULL"), nullable=True),
            sa.Column("campaign_id", GUID(),
                      sa.ForeignKey("ad_campaign.id", ondelete="SET NULL"), nullable=True),
            sa.Column("ad_id", GUID(), sa.ForeignKey("ad.id", ondelete="SET NULL"), nullable=True),
            sa.Column("utm_source", sa.String(120), nullable=True),
            sa.Column("utm_medium", sa.String(120), nullable=True),
            sa.Column("utm_campaign", sa.String(300), nullable=True),
            sa.Column("utm_content", sa.String(300), nullable=True),
            sa.Column("utm_term", sa.String(300), nullable=True),
            sa.Column("landing_url", sa.Text(), nullable=True),
            sa.Column("channel", sa.String(24), nullable=False),
            sa.Column("match_method", sa.String(12), nullable=False),
            sa.Column("confidence", sa.String(8), nullable=False),
            sa.Column("first_seen_on", sa.Date(), nullable=False),
            sa.Column("last_seen_on", sa.Date(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            # One row per identity per workspace. The uniqueness is what makes the write-once
            # rule enforceable rather than merely intended.
            sa.UniqueConstraint("tenant_id", "identity_kind", "identity_key",
                                name="uq_ad_attr_identity"),
        )
    if not _has_index(bind, "ad_attribution", "ix_ad_attr_cohort"):
        op.create_index("ix_ad_attr_cohort", "ad_attribution", ["tenant_id", "first_seen_on"])
    if not _has_index(bind, "ad_attribution", "ix_ad_attr_campaign"):
        op.create_index("ix_ad_attr_campaign", "ad_attribution", ["tenant_id", "campaign_id"])

    if not _has_table(bind, "ad_conversion"):
        op.create_table(
            "ad_conversion",
            sa.Column("id", GUID(), primary_key=True),
            _tenant(),
            sa.Column("attribution_id", GUID(),
                      sa.ForeignKey("ad_attribution.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("business_id", GUID(), sa.ForeignKey("business.id"), nullable=False, index=True),
            sa.Column("stage_key", sa.String(24), nullable=False),
            sa.Column("occurred_on", sa.Date(), nullable=True),
            sa.Column("dated", sa.Boolean(), server_default=sa.true()),
            sa.Column("value_contracted", sa.Numeric(14, 2), nullable=True),
            sa.Column("value_collected", sa.Numeric(14, 2), nullable=True),
            sa.Column("value_annualized", sa.Boolean(), server_default=sa.false()),
            sa.Column("payment_type", sa.String(12), nullable=True),
            sa.Column("source_kind", sa.String(32), nullable=False),
            sa.Column("source_ref", sa.String(64), nullable=False),
            sa.UniqueConstraint("tenant_id", "attribution_id", "stage_key",
                                name="uq_ad_conv_stage"),
        )
    if not _has_index(bind, "ad_conversion", "ix_ad_conv_stage"):
        op.create_index("ix_ad_conv_stage", "ad_conversion",
                        ["tenant_id", "stage_key", "occurred_on"])

    if not _has_table(bind, "ad_cohort_curve"):
        op.create_table(
            "ad_cohort_curve",
            sa.Column("id", GUID(), primary_key=True),
            _tenant(),
            sa.Column("business_id", GUID(), sa.ForeignKey("business.id"), nullable=False, index=True),
            sa.Column("funnel_key", sa.String(24), nullable=False),
            sa.Column("day", sa.Integer(), nullable=False),
            sa.Column("share", sa.Numeric(6, 4), nullable=False),
            sa.Column("sample_cohorts", sa.Integer(), nullable=False),
            sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", "business_id", "funnel_key", "day",
                                name="uq_ad_curve_day"),
        )


def downgrade() -> None:
    """Drops the module's tables, children first.

    Safe in a way most downgrades are not: these tables are additive and nothing outside the ads
    module reads them, so removing them cannot orphan another feature's data. It does discard
    every attribution row, which is history Meta will not give back - so this is a development
    convenience, not a production manoeuvre.
    """
    bind = op.get_bind()
    for table in reversed(TABLES):
        if _has_table(bind, table):
            op.drop_table(table)
