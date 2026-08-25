"""Platform operators + tenant suspension

Two additive changes behind the operator surface.

`platform_user` is a SEPARATE identity table, not a flag on `user` and not a member of a
distinguished tenant. See the model docstring for why both of those were rejected; the short
version is that a platform token carries `pu` and no `tid` while a tenant token carries `tid`
and no `pu`, so neither can satisfy the other's guard and both fail closed.

`tenant.status` makes suspension real: `auth.login` refuses a suspended tenant and
`deps.current_user` refuses its live sessions, so suspending is not merely a label on a list.
Defaults to 'active', so every existing tenant is unaffected.

Revision ID: 0045_platform_operators
Revises: 0044_recall_tenant_isolation
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID

revision: str = "0045_platform_operators"
down_revision: Union[str, None] = "0044_recall_tenant_isolation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "platform_user" not in _tables():
        op.create_table(
            "platform_user",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("email", sa.String(255), nullable=False, unique=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("password_hash", sa.String(255), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "status" not in _cols("tenant"):
        op.add_column("tenant", sa.Column("status", sa.String(16), nullable=False,
                                          server_default="active"))


def downgrade() -> None:
    if "status" in _cols("tenant"):
        op.drop_column("tenant", "status")
    if "platform_user" in _tables():
        op.drop_table("platform_user")
