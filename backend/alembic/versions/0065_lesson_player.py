"""What a lesson needs to be a lesson rather than a checkbox.

The portal's course screen was a checklist: title, duration, a link that opened the source in a new
tab. The design it was meant to be is a lesson PLAYER -- the video in the page, the lesson's own
copy under it, its handouts beside it, and the rest of the course down the right with progress. Two
of those have never had anywhere to live:

  `description`  -- the paragraph that says what the lesson is for and when to watch it. The
                    console's lesson editor collects a title, a source and a duration, so the
                    portal had nothing to render even if it had wanted to.

  attachments    -- the one-pagers, packets and templates that hang off a lesson. Some are files
                    somebody uploads; some are links into a drive the team already keeps. Both are
                    attachments to the person reading, so this is ONE table with a `kind`, not an
                    uploads table plus a links table -- otherwise the portal has to merge two
                    lists and keep their sort orders agreeing.

Both are publishable (tenant_id + published_at + draft_dirty), which is not decoration: those three
columns ARE the contract `_publishable_models()` matches on, so adding them is what makes a handout
added to a draft lesson stay out of the live portal until somebody presses Publish. Without them it
would go live the moment it was saved.

Revision ID: 0065_lesson_player
Revises: 0064_ai_questions
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID

revision = "0065_lesson_player"
down_revision = "0064_ai_questions"
branch_labels = None
depends_on = None

TABLE = "intranet_lesson_attachment"


def _lesson_columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("intranet_lesson")}


def _has_table() -> bool:
    return TABLE in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if "description" not in _lesson_columns():
        op.add_column("intranet_lesson", sa.Column("description", sa.Text(), nullable=True))

    if not _has_table():
        op.create_table(
            TABLE,
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(),
                      sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
            # CASCADE: a handout has no meaning without its lesson, and bytes nobody can reach
            # are worse than deleted ones.
            sa.Column("lesson_id", GUID(),
                      sa.ForeignKey("intranet_lesson.id", ondelete="CASCADE"), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            # file | link
            sa.Column("kind", sa.Text(), nullable=False, server_default="file"),
            # Set for kind=file. Bytes live in binder_storage, same as SOP versions.
            sa.Column("storage_key", sa.Text(), nullable=True),
            sa.Column("filename", sa.Text(), nullable=True),
            # What the SERVER sniffed, never what the client declared -- a browser will happily
            # label an HTML file as a PDF, and that label is what a download echoes back.
            sa.Column("content_type", sa.Text(), nullable=True),
            sa.Column("byte_size", sa.Integer(), nullable=True),
            # Set for kind=link.
            sa.Column("url", sa.Text(), nullable=True),
            # The small grey note beside the name: "2 pages", "Utah Life Drive".
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("sort", sa.SmallInteger(), nullable=False, server_default="0"),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("draft_dirty", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.CheckConstraint("kind IN ('file','link')", name="ck_lesson_attachment_kind"),
        )
        op.create_index("ix_intranet_lesson_attachment_lesson", TABLE, ["lesson_id", "sort"])
        op.create_index("ix_intranet_lesson_attachment_tenant", TABLE, ["tenant_id"])


def downgrade() -> None:
    if _has_table():
        op.drop_index("ix_intranet_lesson_attachment_tenant", table_name=TABLE)
        op.drop_index("ix_intranet_lesson_attachment_lesson", table_name=TABLE)
        op.drop_table(TABLE)
    if "description" in _lesson_columns():
        op.drop_column("intranet_lesson", "description")
