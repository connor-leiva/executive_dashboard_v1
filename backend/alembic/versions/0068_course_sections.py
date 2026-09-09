"""Courses get sections, lessons get a kind and a body, members get an enrolment date.

Three things at once because they are one feature: a five-day onboarding needs somewhere to hang
"Day 2", something to say a lesson is an article rather than a video, and a date to count days
from.

EXISTING COURSES DO NOT MOVE. `section_id` stays NULL on every lesson and `grouping_scheme`
defaults to 'none' on every course, so the portal renders exactly what it renders today. Sections
are opt-in per course from the console afterwards. That is the whole migration story and it is why
there is no backfill here except `kind`.

`kind` IS backfilled, from evidence rather than from the host enum: a `.pdf` source or
source_type='PDF' is a document, everything else is a video. Nothing becomes 'reading', because a
reading lesson is defined by having a body and there are none yet.

WHY `kind` IS NOT `source_type`. That column is LOOM|SKOOL|HERE|PDF|EXP|PLACE and answers "where
does this live". Kind answers "what sort of thing is this". They are orthogonal -- a reading lesson
has no host at all -- and collapsing them would mean a PDF-hosted video and a downloadable document
became indistinguishable.

ENROLMENT IS ITS OWN TABLE, not a column on the progress blob. Completion already lives per user in
`intranet_user_state` and stays there; what was missing is only the date a member started a course,
which release_rule='day_n' counts from. It is server-owned rather than client-written because the
blob is client-writable, and an agent who could backdate their own enrolment could unlock every
section at once.

Revision ID: 0068_course_sections
Revises: 0067_member_agent_email
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID

revision = "0068_course_sections"
down_revision = "0067_member_agent_email"
branch_labels = None
depends_on = None

SECTION = "intranet_course_section"
ENROLMENT = "intranet_course_enrolment"

KINDS = "'video','reading','document'"
SCHEMES = "'day','module','week','phase','part','custom','none'"

LESSON_COLUMNS = {
    "section_id": sa.Column("section_id", GUID(),
                            sa.ForeignKey(f"{SECTION}.id", ondelete="SET NULL"), nullable=True),
    "kind": sa.Column("kind", sa.Text(), nullable=False, server_default="video"),
    "body_html": sa.Column("body_html", sa.Text(), nullable=True),
    "word_count": sa.Column("word_count", sa.Integer(), nullable=True),
    "read_minutes": sa.Column("read_minutes", sa.Integer(), nullable=True),
    "page_count": sa.Column("page_count", sa.Integer(), nullable=True),
}
COURSE_COLUMNS = {
    "grouping_scheme": sa.Column("grouping_scheme", sa.Text(), nullable=False,
                                 server_default="none"),
    "lock_sections": sa.Column("lock_sections", sa.Boolean(), nullable=False,
                               server_default=sa.text("false")),
}

# Constraints that arrive with their column rather than after it, keyed by (table, column).
# Postgres gets these as a separate ALTER, which is alembic's normal rendering; see _add_column.
CONSTRAINED = {
    ("intranet_lesson", "kind"):
        ("ck_intranet_lesson_kind", f"kind IN ({KINDS})"),
    ("intranet_course", "grouping_scheme"):
        ("ck_intranet_course_grouping_scheme", f"grouping_scheme IN ({SCHEMES})"),
}


def _sqlite_ddl(bind) -> dict[tuple[str, str], str]:
    """The three constrained columns as the single statement SQLite accepts.

    alembic renders a column-level FK or CHECK as ADD COLUMN followed by ALTER TABLE ADD
    CONSTRAINT. Postgres takes that; SQLite has no ALTER for constraints at all and raises
    NotImplementedError. SQLite does accept the clause INSIDE the ADD COLUMN -- for a foreign key
    provided the default is NULL, which ours is -- so the constraint is spelled out here rather
    than skipped. Skipping would leave every test database without the SET NULL, and that rule is
    the one worth testing.
    """
    guid = GUID().compile(dialect=bind.dialect)
    return {
        ("intranet_lesson", "section_id"):
            f"ALTER TABLE intranet_lesson ADD COLUMN section_id {guid} "
            f"REFERENCES {SECTION} (id) ON DELETE SET NULL",
        ("intranet_lesson", "kind"):
            f"ALTER TABLE intranet_lesson ADD COLUMN kind TEXT NOT NULL DEFAULT 'video' "
            f"CHECK (kind IN ({KINDS}))",
        ("intranet_course", "grouping_scheme"):
            f"ALTER TABLE intranet_course ADD COLUMN grouping_scheme TEXT NOT NULL "
            f"DEFAULT 'none' CHECK (grouping_scheme IN ({SCHEMES}))",
    }


def _add_column(table: str, name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        ddl = _sqlite_ddl(bind).get((table, name))
        if ddl:
            op.execute(ddl)
            return
    op.add_column(table, column)
    named = CONSTRAINED.get((table, name))
    if named and bind.dialect.name != "sqlite":
        op.create_check_constraint(named[0], table, named[1])


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()

    if SECTION not in tables:
        op.create_table(
            SECTION,
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(),
                      sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
            sa.Column("course_id", GUID(),
                      sa.ForeignKey("intranet_course.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("release_rule", sa.Text(), nullable=False, server_default="immediate"),
            sa.Column("release_day", sa.Integer(), nullable=True),
            sa.Column("release_date", sa.Date(), nullable=True),
            sa.Column("due_rule", sa.Text(), nullable=False, server_default="none"),
            sa.Column("due_day", sa.Integer(), nullable=True),
            sa.Column("sort", sa.Integer(), nullable=False, server_default=sa.text("0")),
            # Publishable, like every other content row: a section drafted in the console must not
            # appear in the portal before somebody presses Publish.
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("draft_dirty", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.CheckConstraint(
                "release_rule IN ('immediate','day_n','after_previous','fixed_date')",
                name="ck_intranet_course_section_release_rule"),
            sa.CheckConstraint(
                "due_rule IN ('none','end_of_day_n','end_of_week_n','before_next_section')",
                name="ck_intranet_course_section_due_rule"),
        )
        op.create_index("ix_intranet_course_section_course", SECTION, ["course_id", "sort"])
        op.create_index("ix_intranet_course_section_tenant", SECTION, ["tenant_id"])

    if ENROLMENT not in tables:
        op.create_table(
            ENROLMENT,
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(),
                      sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", GUID(),
                      sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
            sa.Column("course_id", GUID(),
                      sa.ForeignKey("intranet_course.id", ondelete="CASCADE"), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            # One row per person per course. Without this a second open could start the clock
            # again and Day 1 would follow Day 4.
            sa.UniqueConstraint("user_id", "course_id", name="uq_course_enrolment_user_course"),
        )
        op.create_index("ix_intranet_course_enrolment_tenant", ENROLMENT, ["tenant_id"])

    have = _columns("intranet_lesson")
    for name, column in LESSON_COLUMNS.items():
        if name not in have:
            _add_column("intranet_lesson", name, column)
    if "section_id" not in have:
        op.create_index("ix_intranet_lesson_section", "intranet_lesson", ["section_id", "sort"])

    have_course = _columns("intranet_course")
    for name, column in COURSE_COLUMNS.items():
        if name not in have_course:
            _add_column("intranet_course", name, column)

    # Backfill from the source, not the enum: what a lesson IS, judged by what it points at.
    op.execute("""
        UPDATE intranet_lesson
           SET kind = 'document'
         WHERE source_type = 'PDF'
            OR lower(coalesce(source_ref, '')) LIKE '%.pdf'
    """)


def downgrade() -> None:
    have = _columns("intranet_lesson")
    if "section_id" in have:
        op.drop_index("ix_intranet_lesson_section", table_name="intranet_lesson")
    for name in LESSON_COLUMNS:
        if name in have:
            op.drop_column("intranet_lesson", name)
    have_course = _columns("intranet_course")
    for name in COURSE_COLUMNS:
        if name in have_course:
            op.drop_column("intranet_course", name)
    tables = _tables()
    if ENROLMENT in tables:
        op.drop_table(ENROLMENT)
    if SECTION in tables:
        op.drop_table(SECTION)
