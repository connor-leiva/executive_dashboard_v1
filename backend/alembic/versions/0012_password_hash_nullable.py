"""user.password_hash → nullable (invited users have no password yet)

The multi-user platform (0011) changed the model so an invited user row is created
with password_hash=NULL and only gets a hash when they accept. But 0011 only ADDED
columns — it never altered the pre-existing password_hash column, so in Postgres it
kept its original NOT NULL constraint and every invite failed with a
NotNullViolationError. (Local/SQLite works because the schema is built by
create_all from the current, nullable model.) This drops the constraint.

Revision ID: 0012_password_hash_nullable
Revises: 0011_multiuser_platform
Create Date: 2026-07-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012_password_hash_nullable"
down_revision: Union[str, None] = "0011_multiuser_platform"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # SQLite (local/dev/tests) builds its schema from create_all, where the model
    # already declares password_hash nullable; SQLite also can't ALTER a column's
    # nullability without a full table rebuild, so this only needs to run on Postgres.
    if bind.dialect.name == "sqlite":
        return
    op.alter_column("user", "password_hash", existing_type=sa.String(255), nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    # Reverting requires no invited (password-less) users to exist, or it will fail.
    op.alter_column("user", "password_hash", existing_type=sa.String(255), nullable=False)
