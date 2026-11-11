"""Zoom session attendance.

Revision ID: 0108_zoom_attendance
Revises: 0107_ad_digest, 0106_lead_source_digest
"""
import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID, JSONType

revision = "0108_zoom_attendance"
down_revision = ("0107_ad_digest", "0106_lead_source_digest")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attendance_sync",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rules", JSONType, nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_stats", JSONType, nullable=True),
        sa.Column("backfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", name="uq_attendance_sync_tenant"),
    )
    op.create_table(
        "attendance_session",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("zoom_id", sa.String(64), nullable=False),
        sa.Column("zoom_uuid", sa.String(128), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("topic", sa.String(400), nullable=False),
        sa.Column("host_email", sa.String(255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="report"),
        sa.UniqueConstraint("tenant_id", "kind", "zoom_id", name="uq_attendance_session"),
    )
    op.create_index("ix_attendance_session_tenant", "attendance_session", ["tenant_id"])
    op.create_table(
        "attendance_person",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", GUID(), sa.ForeignKey("attendance_session.id", ondelete="CASCADE"), nullable=False),
        sa.Column("person_key", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("contact_external_id", sa.String(64), nullable=True),
        sa.Column("contact_name", sa.String(200), nullable=True),
        sa.Column("match_method", sa.String(16), nullable=False, server_default="none"),
        sa.UniqueConstraint("session_id", "person_key", name="uq_attendance_person"),
    )
    op.create_index("ix_attendance_person_tenant", "attendance_person", ["tenant_id"])
    op.create_index("ix_attendance_person_session", "attendance_person", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_attendance_person_session", table_name="attendance_person")
    op.drop_index("ix_attendance_person_tenant", table_name="attendance_person")
    op.drop_table("attendance_person")
    op.drop_index("ix_attendance_session_tenant", table_name="attendance_session")
    op.drop_table("attendance_session")
    op.drop_table("attendance_sync")
