"""multi-user platform — User account/role/grant columns + audit_log

Adds the self-serve user layer (status, tab_access, token_version, login
hardening, invite/reset action tokens) and the audit trail. Data step migrates
the legacy `viewer` role to `member` and backfills status/token_version.

Revision ID: 0011_multiuser_platform
Revises: 0010_sympli_econ_rates
Create Date: 2026-07-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision: str = "0011_multiuser_platform"
down_revision: Union[str, None] = "0010_sympli_econ_rates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


_COLS = [
    ("status", sa.Column("status", sa.String(16), nullable=False, server_default="active")),
    ("tab_access", sa.Column("tab_access", JSONType, nullable=True)),
    ("token_version", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0")),
    ("last_login_at", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True)),
    ("failed_logins", sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0")),
    ("locked_until", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True)),
    ("invited_by", sa.Column("invited_by", GUID(), nullable=True)),
    ("action_token_hash", sa.Column("action_token_hash", sa.String(64), nullable=True)),
    ("action_token_purpose", sa.Column("action_token_purpose", sa.String(16), nullable=True)),
    ("action_token_expires", sa.Column("action_token_expires", sa.DateTime(timezone=True), nullable=True)),
]


def upgrade() -> None:
    bind = op.get_bind()
    for name, col in _COLS:
        if not _has_column(bind, "user", name):
            op.add_column("user", col)
    # invited-by self-FK (skip on SQLite, which can't ALTER-ADD constraints)
    if bind.dialect.name == "postgresql":
        op.create_foreign_key("fk_user_invited_by", "user", "user", ["invited_by"], ["id"])

    if not _has_table(bind, "audit_log"):
        op.create_table(
            "audit_log",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True),
            sa.Column("actor_user_id", GUID(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("action", sa.String(48), nullable=False),
            sa.Column("target_type", sa.String(24), nullable=True),
            sa.Column("target_id", sa.String(64), nullable=True),
            sa.Column("detail", JSONType, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    # Data step: legacy viewer → member; backfill status/token_version.
    op.execute("UPDATE \"user\" SET role = 'member' WHERE role = 'viewer'")
    op.execute("UPDATE \"user\" SET status = 'active' WHERE status IS NULL")
    op.execute("UPDATE \"user\" SET token_version = 0 WHERE token_version IS NULL")


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "audit_log"):
        op.drop_table("audit_log")
    for name, _ in reversed(_COLS):
        if _has_column(bind, "user", name):
            op.drop_column("user", name)
