"""SkySlope: a read-only copy of what a workspace's access keys can see

Five new tables, no changes to anything existing. SKYSLOPE-SPEC.md §3, and
docs/skyslope-integration-spec.md for the field mapping.

The handed-over spec drew eight. Three of them already exist in another form, so they are not here:
keys are `integration` rows, one per access key, with the secret in `access_token_enc`; sync state
is `integration.config`; the run log is `sync_run`.

There is deliberately no column for a street address, for anybody's phone, or for any buyer or
seller. SkySlope's API sends all of them and the sync drops them before a row is written. A schema
with nowhere to put them is the cheapest version of that promise.

`skyslope_agent_link` holds only a person's override. Matching on email is derived on read, as in
services/member_identity.py, so an ordinary match has no row.

NUMBERING: 0098_sympli_scorecard is head, so this is 0099. `alembic heads` was one line before and
after (a fork here crash-loops the deploy, which is how that was learned).

Revision ID: 0099_skyslope
Revises: 0098_sympli_scorecard
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision = "0099_skyslope"
down_revision = "0098_sympli_scorecard"
branch_labels = None
depends_on = None


def _tables() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _tenant() -> sa.Column:
    return sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                     nullable=False, index=True)


def _stamp(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _file_columns() -> list:
    """Everything a listing and a sale share (models._SkyslopeFile)."""
    return [
        sa.Column("id", GUID(), primary_key=True),
        _tenant(),
        sa.Column("business_id", GUID(), sa.ForeignKey("business.id", ondelete="SET NULL"), nullable=True),
        sa.Column("agent_guid", sa.String(64), nullable=True),
        sa.Column("status_raw", sa.String(80), nullable=True),
        sa.Column("status", sa.String(24), nullable=True),
        sa.Column("stage_name", sa.String(120), nullable=True),
        sa.Column("office_guid", sa.String(64), nullable=True),
        sa.Column("list_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("state", sa.String(32), nullable=True),
        sa.Column("zip", sa.String(16), nullable=True),
        sa.Column("mls_number", sa.String(64), nullable=True),
        _stamp("canceled_at"),
        _stamp("source_created_at"),
        _stamp("source_modified_at"),
        sa.Column("commission_raw", JSONType, nullable=True),
        sa.Column("raw", JSONType, nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        _stamp("first_seen_at", nullable=False),
        _stamp("last_seen_at", nullable=False),
        _stamp("removed_from_source_at"),
    ]


def upgrade() -> None:
    have = _tables()

    if "skyslope_user" not in have:
        op.create_table(
            "skyslope_user",
            sa.Column("id", GUID(), primary_key=True),
            _tenant(),
            sa.Column("user_guid", sa.String(64), nullable=False),
            sa.Column("name", sa.String(200), nullable=True),
            sa.Column("email_raw", sa.String(320), nullable=True),
            sa.Column("email_norm", sa.String(320), nullable=True),
            sa.Column("user_type", sa.String(40), nullable=True),
            sa.Column("office_guids", JSONType, nullable=True),
            sa.Column("deactivated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            _stamp("first_seen_at", nullable=False),
            _stamp("last_seen_at", nullable=False),
            sa.UniqueConstraint("tenant_id", "user_guid", name="uq_skyslope_user_guid"),
        )
        op.create_index("ix_skyslope_user_email", "skyslope_user", ["tenant_id", "email_norm"])

    if "skyslope_listing" not in have:
        op.create_table(
            "skyslope_listing",
            *_file_columns(),
            sa.Column("listing_guid", sa.String(64), nullable=False),
            _stamp("listed_at"),
            _stamp("expires_at"),
            sa.UniqueConstraint("tenant_id", "listing_guid", name="uq_skyslope_listing_guid"),
        )
        op.create_index("ix_skyslope_listing_agent", "skyslope_listing", ["tenant_id", "agent_guid"])
        op.create_index("ix_skyslope_listing_seen", "skyslope_listing", ["tenant_id", "last_seen_at"])

    if "skyslope_sale" not in have:
        op.create_table(
            "skyslope_sale",
            *_file_columns(),
            sa.Column("sale_guid", sa.String(64), nullable=False),
            sa.Column("listing_guid", sa.String(64), nullable=True),
            sa.Column("status_id", sa.Integer(), nullable=True),
            sa.Column("deal_side", sa.String(40), nullable=True),
            sa.Column("sale_price", sa.Numeric(14, 2), nullable=True),
            _stamp("contract_accepted_at"),
            _stamp("expected_close_at"),
            _stamp("closed_at"),
            sa.UniqueConstraint("tenant_id", "sale_guid", name="uq_skyslope_sale_guid"),
        )
        op.create_index("ix_skyslope_sale_agent", "skyslope_sale", ["tenant_id", "agent_guid"])
        op.create_index("ix_skyslope_sale_seen", "skyslope_sale", ["tenant_id", "last_seen_at"])

    if "skyslope_file_agent" not in have:
        op.create_table(
            "skyslope_file_agent",
            sa.Column("id", GUID(), primary_key=True),
            _tenant(),
            sa.Column("file_kind", sa.String(8), nullable=False),
            sa.Column("file_guid", sa.String(64), nullable=False),
            sa.Column("user_guid", sa.String(64), nullable=False),
            sa.Column("role", sa.String(16), nullable=False),
            sa.Column("split_pct", sa.Numeric(7, 4), nullable=True),
            sa.UniqueConstraint("tenant_id", "file_guid", "user_guid", "role", name="uq_skyslope_file_agent"),
        )
        op.create_index("ix_skyslope_file_agent_user", "skyslope_file_agent", ["tenant_id", "user_guid"])

    if "skyslope_agent_link" not in have:
        op.create_table(
            "skyslope_agent_link",
            sa.Column("id", GUID(), primary_key=True),
            _tenant(),
            sa.Column("user_guid", sa.String(64), nullable=False),
            sa.Column("agent_id", GUID(), sa.ForeignKey("agent.id", ondelete="SET NULL"), nullable=True),
            sa.Column("confirmed_by", GUID(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("tenant_id", "user_guid", name="uq_skyslope_agent_link"),
        )


def downgrade() -> None:
    have = _tables()
    for table in ("skyslope_agent_link", "skyslope_file_agent", "skyslope_sale",
                  "skyslope_listing", "skyslope_user"):
        if table in have:
            op.drop_table(table)
