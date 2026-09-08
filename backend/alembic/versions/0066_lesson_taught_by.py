"""Who teaches a lesson.

The lesson editor collected a "Source label" -- a name for where the video lives -- and the course
builder design asks for something different: the person who teaches it, shown as a byline under the
lesson title. Those are not the same field wearing two hats. "Loom" answers where the file is;
"Sharida Hansen" answers who to ask about the content, which is the question somebody watching a
lesson actually has.

So this is a new column rather than a rename. `source_label` keeps its job (it is what the portal's
launch button says for a source that cannot be embedded), and nothing has to be migrated or
reinterpreted.

Revision ID: 0066_lesson_taught_by
Revises: 0065_lesson_player
"""
from alembic import op
import sqlalchemy as sa

revision = "0066_lesson_taught_by"
down_revision = "0065_lesson_player"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("intranet_lesson")}


def upgrade() -> None:
    if "taught_by" not in _columns():
        op.add_column("intranet_lesson", sa.Column("taught_by", sa.Text(), nullable=True))


def downgrade() -> None:
    if "taught_by" in _columns():
        op.drop_column("intranet_lesson", "taught_by")
