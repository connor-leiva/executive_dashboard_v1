"""What people actually asked the assistant.

intranet_content_gap already records questions the assistant COULD NOT answer, which is the
console's to-do list. This is the other half: every question, answered or not, because the useful
signal is not only "we have no document for this" but "forty people asked about commission splits
this month and the answer we gave them came from an SOP nobody has revised since March".

Both are kept, and neither is derivable from the other -- a gap is deduplicated and counted, a
question is a single event with its own answer and citations at the moment it was asked.

IT NAMES THE PERSON WHO ASKED, and that is worth saying out loud rather than discovering later: a
workspace admin can read what an individual member asked the assistant. That is what the feature is
for -- you cannot fix a content gap you cannot attribute or follow up -- but it makes this table
staff data, so the portal tells members it is recorded, the member FK is SET NULL rather than
CASCADE so a departure does not silently rewrite history into anonymity, and the denormalised
label is what survives.

Revision ID: 0064_ai_questions
Revises: 0063_marketing_delivery
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision = "0064_ai_questions"
down_revision = "0063_marketing_delivery"
branch_labels = None
depends_on = None

TABLE = "intranet_ai_question"


def _has_table() -> bool:
    return TABLE in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table():
        return
    op.create_table(
        TABLE,
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(),
                  sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", GUID(),
                  sa.ForeignKey("intranet_member.id", ondelete="SET NULL"), nullable=True),
        sa.Column("asker_label", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        # Whether the assistant found something in this workspace's own content to answer from.
        # False is the interesting value: it is what opens a content gap.
        sa.Column("answered", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("citations", JSONType, nullable=False, server_default=sa.text("'[]'")),
        # What went wrong, when nothing did answer: no API key, over the plan, a model error.
        sa.Column("failure", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # The console reads one workspace's questions newest first, and that is the only read.
    op.create_index("ix_intranet_ai_question_tenant_created", TABLE, ["tenant_id", "created_at"])


def downgrade() -> None:
    if not _has_table():
        return
    op.drop_index("ix_intranet_ai_question_tenant_created", table_name=TABLE)
    op.drop_table(TABLE)
