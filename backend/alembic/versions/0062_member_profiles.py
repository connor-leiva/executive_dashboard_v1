"""Profile fields on the roster, so Who's Who can say something.

The member-facing directory said "Directory is empty" while the roster sat in intranet_member,
admin-only, with nothing on it a colleague would want: no title, no phone, no bio, no sense of
what somebody owns. These are those columns.

All nullable and added in place rather than as a separate profile table: they belong to the
member, every one of them is optional, and a second table would mean a join and a row that may
not exist for the sake of five columns.

NOT publishable, deliberately. The roster is operational fact, not staged content -- a new hire's
phone number should not wait for somebody to press Publish before the team can call them.

Revision ID: 0062_member_profiles
Revises: 0061_intranet_pages
"""
from alembic import op
import sqlalchemy as sa

revision = "0062_member_profiles"
down_revision = "0061_intranet_pages"
branch_labels = None
depends_on = None

COLUMNS = ("title", "bio", "phone", "owns", "photo_key")


def _existing() -> set[str]:
    insp = sa.inspect(op.get_bind())
    return {c["name"] for c in insp.get_columns("intranet_member")}


def upgrade() -> None:
    have = _existing()
    for name in COLUMNS:
        if name not in have:
            op.add_column("intranet_member", sa.Column(name, sa.Text(), nullable=True))


def downgrade() -> None:
    have = _existing()
    for name in COLUMNS:
        if name in have:
            op.drop_column("intranet_member", name)
