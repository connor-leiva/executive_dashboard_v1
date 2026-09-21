"""The operator's own audit trail, and the scheduler's heartbeat.

platform_audit (OPERATOR-CONSOLE-SPEC §4.2): every change an Axcion operator makes, across every
workspace, written alongside the workspace's own audit_log row. tenant_id is deliberately NOT a
foreign key and the slug is copied in, so deleting a workspace cannot delete the record that it was
deleted. operator_id is nullable with SET NULL: removing an operator keeps their history, and the
rows Stripe's webhooks will write have no operator at all.

job_heartbeat: one row per scheduled job, overwritten each run, so the console's System view can say
whether the worker is alive instead of inferring it from sync runs.

The spec's single `0069_operator_console` also adds user.expires_at. That column belongs to support
access and ships with it, so each phase's migration carries only what that phase uses.

Both tables are created only if absent, like every recent migration here: a local SQLite database is
built from the models, not from this chain.

Revision ID: 0069_platform_audit
Revises: 0068_course_sections
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision = "0069_platform_audit"
down_revision = "0068_course_sections"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "platform_audit" not in tables:
        op.create_table(
            "platform_audit",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("operator_id", GUID(), sa.ForeignKey("platform_user.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("operator_email", sa.String(255), nullable=True),
            sa.Column("action", sa.String(64), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=True),               # NOT a foreign key
            sa.Column("tenant_slug", sa.String(64), nullable=True),
            sa.Column("target_type", sa.String(32), nullable=True),
            sa.Column("target_id", sa.String(64), nullable=True),
            sa.Column("detail", JSONType, nullable=False, server_default=sa.text("'{}'")),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("ip", sa.String(45), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
        )
        op.create_index("ix_platform_audit_created", "platform_audit", ["created_at"])
        op.create_index("ix_platform_audit_tenant", "platform_audit", ["tenant_id", "created_at"])
    if "job_heartbeat" not in tables:
        op.create_table(
            "job_heartbeat",
            sa.Column("job", sa.String(48), primary_key=True),
            sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("last_seconds", sa.Float(), nullable=True),
        )


def downgrade() -> None:
    tables = _tables()
    if "job_heartbeat" in tables:
        op.drop_table("job_heartbeat")
    if "platform_audit" in tables:
        op.drop_index("ix_platform_audit_tenant", table_name="platform_audit")
        op.drop_index("ix_platform_audit_created", table_name="platform_audit")
        op.drop_table("platform_audit")
