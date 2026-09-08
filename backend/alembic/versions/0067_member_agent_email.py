"""The address a member is known by in Sisu and Follow Up Boss.

Everything per-person in the portal -- My Numbers, and Sunburst's "what it already knows" panel --
needs to answer "which agent is this member". Both `agent` and `intranet_member` carry an email, so
the answer is normally derivable and nothing needs storing.

This is the exception, and it is common enough to be worth a column: somebody joined under an old
address, or the brokerage's CRM has their personal one while the portal has their work one. Without
an override those members see zeroes on their own numbers and have no way to say why -- and an
admin has no way to fix it short of editing the CRM.

NULL means "use their portal email", which is what it should mean and what it is for almost
everybody. NOT publishable: this is an identity fact, not staged content, and a member whose email
changed should not be waiting on somebody to press Publish before their own figures come back.

Revision ID: 0067_member_agent_email
Revises: 0066_lesson_taught_by
"""
from alembic import op
import sqlalchemy as sa

revision = "0067_member_agent_email"
down_revision = "0066_lesson_taught_by"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("intranet_member")}


def upgrade() -> None:
    if "agent_email" not in _columns():
        op.add_column("intranet_member", sa.Column("agent_email", sa.Text(), nullable=True))


def downgrade() -> None:
    if "agent_email" in _columns():
        op.drop_column("intranet_member", "agent_email")
