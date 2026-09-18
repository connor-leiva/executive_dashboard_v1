"""An account can be added to the portal roster without being invited yet.

`user.invite_held`: true for an account created by the console's Add, until an admin presses Send
invite. While it holds, nothing reaches the person -- no invite, no Google sign-in, no password reset
email, no workspace finder listing -- so a team can set people up before telling them. Every existing
row is false, which is how every account behaved before this column.

Revision ID: 0074_invite_held
Revises: 0073_fub_follow_ups
"""
from alembic import op
import sqlalchemy as sa

revision = "0074_invite_held"
down_revision = "0073_fub_follow_ups"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("user")}


def upgrade() -> None:
    if "invite_held" not in _columns():
        op.add_column("user", sa.Column("invite_held", sa.Boolean(), nullable=False,
                                        server_default=sa.text("false")))


def downgrade() -> None:
    if "invite_held" in _columns():
        op.drop_column("user", "invite_held")
