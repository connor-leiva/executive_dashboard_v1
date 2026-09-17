"""Support access: a time-boxed account for an Acumyn operator (OPERATOR-CONSOLE-SPEC §4.1, C8 option c).

user.expires_at, non-null only on a support account. deps.current_user refuses anything but reads
from such an account and refuses it entirely once the moment passes; a five-minute job then disables
it. The partial index keeps that job's scan to the handful of rows it can match.

Revision ID: 0071_support_access
Revises: 0070_platform_billing
"""
from alembic import op
import sqlalchemy as sa

revision = "0071_support_access"
down_revision = "0070_platform_billing"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("user")}


def upgrade() -> None:
    if "expires_at" not in _columns():
        op.add_column("user", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        op.create_index("ix_user_expires_at", "user", ["expires_at"],
                        postgresql_where=sa.text("expires_at IS NOT NULL"))


def downgrade() -> None:
    if "expires_at" in _columns():
        op.drop_index("ix_user_expires_at", table_name="user")
        op.drop_column("user", "expires_at")
