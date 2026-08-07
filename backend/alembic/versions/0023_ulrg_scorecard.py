"""ULRG L10 Scorecard + Team Rooms — the six tables (SPEC-ulrg-scorecard Part 2)

scorecard_group / scorecard_metric / scorecard_value, team_commitment (+ progress), and
share_link. GUID PKs + JSONType per repo convention. Idempotent guards; local/SQLite builds from
create_all, so this migration matters for Postgres (prod). Groups/metrics are tenant data seeded
per tenant — a new tenant gets none.

Revision ID: 0023_ulrg_scorecard
Revises: 0022_ai_media
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision: str = "0023_ulrg_scorecard"
down_revision: Union[str, None] = "0022_ai_media"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def _tenant():
    return sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True)


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "scorecard_group"):
        op.create_table(
            "scorecard_group",
            sa.Column("id", GUID(), primary_key=True),
            _tenant(),
            sa.Column("business_id", GUID(), sa.ForeignKey("business.id"), index=True),
            sa.Column("key", sa.String(40), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("owner_user_id", GUID(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("owner_name", sa.String(120), nullable=True),
            sa.Column("is_team_room", sa.Boolean(), server_default=sa.true()),
            sa.Column("read", sa.Text(), nullable=True),
            sa.Column("sort_order", sa.Integer(), server_default="0"),
            sa.UniqueConstraint("tenant_id", "business_id", "key", name="uq_scorecard_group"),
        )

    if not _has_table(bind, "scorecard_metric"):
        op.create_table(
            "scorecard_metric",
            sa.Column("id", GUID(), primary_key=True),
            _tenant(),
            sa.Column("group_id", GUID(), sa.ForeignKey("scorecard_group.id", ondelete="CASCADE"), index=True),
            sa.Column("name", sa.String(160), nullable=False),
            sa.Column("note", sa.String(160), nullable=True),
            sa.Column("goal", sa.Numeric(12, 4), nullable=False),
            sa.Column("direction", sa.String(4), server_default="gte"),
            sa.Column("type", sa.String(10), nullable=False),
            sa.Column("stage", sa.Integer(), nullable=True),
            sa.Column("lever", sa.String(12), nullable=True),
            sa.Column("source", sa.String(12), server_default="manual"),
            sa.Column("resolver_key", sa.String(60), nullable=True),
            sa.Column("owner_user_id", GUID(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("owner_initials", sa.String(4), nullable=True),
            sa.Column("sort_order", sa.Integer(), server_default="0"),
            sa.Column("active", sa.Boolean(), server_default=sa.true()),
        )

    if not _has_table(bind, "scorecard_value"):
        op.create_table(
            "scorecard_value",
            sa.Column("id", GUID(), primary_key=True),
            _tenant(),
            sa.Column("metric_id", GUID(), sa.ForeignKey("scorecard_metric.id", ondelete="CASCADE"), index=True),
            sa.Column("week_start", sa.Date(), nullable=False),
            sa.Column("value", sa.Numeric(12, 4), nullable=True),
            sa.Column("source", sa.String(10), server_default="manual"),
            sa.Column("entered_by", GUID(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("entered_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("metric_id", "week_start", name="uq_scorecard_value"),
        )

    if not _has_table(bind, "team_commitment"):
        op.create_table(
            "team_commitment",
            sa.Column("id", GUID(), primary_key=True),
            _tenant(),
            sa.Column("group_id", GUID(), sa.ForeignKey("scorecard_group.id", ondelete="CASCADE"), index=True),
            sa.Column("period_month", sa.Date(), nullable=False),
            sa.Column("kind", sa.String(10), nullable=False),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("target", sa.Numeric(12, 4), nullable=True),
            sa.Column("unit", sa.String(24), nullable=True),
            sa.Column("due_on", sa.Date(), nullable=True),
            sa.Column("source", sa.String(12), server_default="manual"),
            sa.Column("resolver_key", sa.String(60), nullable=True),
            sa.Column("config", JSONType, nullable=True),
            sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("committed_ref_url", sa.String(400), nullable=True),
            sa.Column("sort_order", sa.Integer(), server_default="0"),
        )

    if not _has_table(bind, "team_commitment_progress"):
        op.create_table(
            "team_commitment_progress",
            sa.Column("id", GUID(), primary_key=True),
            _tenant(),
            sa.Column("commitment_id", GUID(), sa.ForeignKey("team_commitment.id", ondelete="CASCADE"), index=True),
            sa.Column("as_of", sa.Date(), nullable=False),
            sa.Column("value", sa.Numeric(12, 4), nullable=False),
            sa.Column("entered_by", GUID(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("entered_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("commitment_id", "as_of", name="uq_commitment_progress"),
        )

    if not _has_table(bind, "share_link"):
        op.create_table(
            "share_link",
            sa.Column("id", GUID(), primary_key=True),
            _tenant(),
            sa.Column("scope", sa.String(24), nullable=False),
            sa.Column("scope_ref", sa.String(40), nullable=True),
            sa.Column("token", sa.String(64), nullable=False, unique=True),
            sa.Column("created_by", GUID(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    for t in ("share_link", "team_commitment_progress", "team_commitment",
              "scorecard_value", "scorecard_metric", "scorecard_group"):
        if _has_table(bind, t):
            op.drop_table(t)
