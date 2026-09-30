"""Onboarding — a plan can name who reads it. ONBOARDING-SPEC.md §6.1.

One table. The scoping rule shipped in 0088 was "your own plan, unless you are an owner or
admin", which is the right default -- a tab grant must not hand out a colleague's day-by-day
account of a month that went badly -- and it is wrong for the one person the plan is built
around. The first seeded plan has its subject in a huddle with Justin at 8:30 on day one, being
trained by him at nine, debriefing with him at 4:45, and reviewing the whole month with him on
day thirty. Justin is a member, so he could see none of it. He was granted the tab and got an
empty page, which is how this was found.

READ ONLY. `may_write` does not consult this table: a coach reads the month, and ticking
somebody else's blocks off for them would make the record less true rather than more. Owners and
admins keep their write access, which is how a correction gets made.

CASCADE on both foreign keys. Unlike `onboarding_plan.user_id`, which is SET NULL because the
record of what somebody was asked to do outlives their account, a reader row means nothing once
either end of it is gone.

NUMBERING: 0088_onboarding is head, so this is 0089. `alembic heads` was one line before and
after.

Revision ID: 0089_onboarding_reader
Revises: 0088_onboarding
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID

revision = "0089_onboarding_reader"
down_revision = "0088_onboarding"
branch_labels = None
depends_on = None


def _tables() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _constraints(table: str) -> set:
    insp = sa.inspect(op.get_bind())
    return {c["name"] for c in insp.get_unique_constraints(table)}


def upgrade() -> None:
    if "onboarding_reader" not in _tables():
        op.create_table(
            "onboarding_reader",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("plan_id", GUID(), sa.ForeignKey("onboarding_plan.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("user_id", GUID(), sa.ForeignKey("user.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            # One row per person per plan: adding a coach twice is a no-op rather than a
            # duplicate that makes them appear twice in every list that names them.
            sa.UniqueConstraint("plan_id", "user_id", name="uq_onboarding_reader"),
        )


def downgrade() -> None:
    if "onboarding_reader" in _tables():
        op.drop_table("onboarding_reader")
