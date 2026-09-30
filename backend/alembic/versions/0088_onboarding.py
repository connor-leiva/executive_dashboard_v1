"""Onboarding — a dated plan for one person's first thirty days, and their progress through it.

Seven tables, no changes to anything existing. ONBOARDING-SPEC.md §3.

The module arrived as a hand-built HTML page for one new hire: the twenty-three days were written
into the bundle and every check-off lived in that browser's localStorage. Both halves become rows
here. The plan half is what makes a second hire an insert instead of a deploy; the progress half
is what lets the people running the thirty-day review read the same numbers the person is ticking
off, instead of the page's "Copy report" button pasting them into a message.

PROGRESS LIVES ON THE PLAN'S OWN ROWS -- `onboarding_block.done`, `.outcome_state`,
`onboarding_day.debrief`, `onboarding_target.actual` -- rather than in a join table keyed by user.
A plan is issued TO somebody: two hires starting the same programme get two plans. A shared plan
with per-user progress would be a different product, and the join table is the migration that
introduces it if it is ever wanted.

`done` and `outcome_state` are separate columns because they answer separate questions. A block
can be worked through and still miss its outcome; "five appointments set" either happened or it
did not, and a schema that recorded only attendance would hide the thing a thirty-day review
exists to look at. `outcome_state` is nullable and NULL means unanswered -- a day that has not
happened yet must not read as a day that went wrong.

NUMBERING: 0087_payables_docs is head, so this is 0088. `alembic heads` was one line before and
after (a fork here crash-loops the deploy, which is how that was learned).

Revision ID: 0088_onboarding
Revises: 0087_payables_docs
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision = "0088_onboarding"
down_revision = "0087_payables_docs"
branch_labels = None
depends_on = None


def _tables() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


# table -> (composite index name, columns). The per-column indexes come from `index=True` on the
# columns themselves, which names them ix_<table>_<column> exactly as the models do.
_COMPOSITE = {
    "onboarding_plan": ("ix_onboarding_plan_tenant_status", ["tenant_id", "status"]),
    "onboarding_week": ("ix_onboarding_week_plan", ["plan_id", "sort_order"]),
    "onboarding_day": ("ix_onboarding_day_plan", ["plan_id", "sort_order"]),
    "onboarding_block": ("ix_onboarding_block_day", ["day_id", "sort_order"]),
    "onboarding_target": ("ix_onboarding_target_plan", ["plan_id", "due_on"]),
    "onboarding_script": ("ix_onboarding_script_plan", ["plan_id", "motion"]),
    "onboarding_conversation": ("ix_onboarding_conversation_plan", ["plan_id", "created_at"]),
}


def upgrade() -> None:
    tabs = _tables()

    if "onboarding_plan" not in tabs:
        op.create_table(
            "onboarding_plan",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            # SET NULL, not CASCADE: deleting a user must not delete the record of what they
            # were asked to do, which is the half that still matters after they leave.
            sa.Column("user_id", GUID(), sa.ForeignKey("user.id", ondelete="SET NULL"),
                      nullable=True, index=True),
            sa.Column("subject_name", sa.String(160), nullable=False),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("starts_on", sa.Date(), nullable=False),
            sa.Column("ends_on", sa.Date(), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            # JSONType BARE, never instantiated -- `JSONType()` raises TypeError mid-upgrade,
            # which would leave the schema half-applied.
            sa.Column("motions", JSONType, nullable=True),
            sa.Column("rules", JSONType, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "onboarding_week" not in tabs:
        op.create_table(
            "onboarding_week",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("plan_id", GUID(), sa.ForeignKey("onboarding_plan.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("label", sa.String(48), nullable=False),
            sa.Column("date_range", sa.String(64), nullable=False),
            sa.Column("subtitle", sa.String(64), nullable=True),
            sa.Column("intro", JSONType, nullable=True),
            sa.Column("outro", JSONType, nullable=True),
            sa.Column("show_rules", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        )

    if "onboarding_day" not in tabs:
        op.create_table(
            "onboarding_day",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("plan_id", GUID(), sa.ForeignKey("onboarding_plan.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("week_id", GUID(), sa.ForeignKey("onboarding_week.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("day_date", sa.Date(), nullable=False),
            # Set only for a span -- a weekend shown as one card.
            sa.Column("end_date", sa.Date(), nullable=True),
            sa.Column("dow", sa.String(16), nullable=False),
            sa.Column("location", sa.String(120), nullable=True),
            sa.Column("tag", sa.String(48), nullable=True),
            sa.Column("offsite", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("notes", JSONType, nullable=True),
            sa.Column("debrief", sa.Text(), nullable=True),
            sa.Column("debrief_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        )

    if "onboarding_block" not in tabs:
        op.create_table(
            "onboarding_block",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("day_id", GUID(), sa.ForeignKey("onboarding_day.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            # "9:00-11:00" fits; the longest in the seeded plan is 11 characters.
            sa.Column("time_label", sa.String(32), nullable=False),
            sa.Column("task", sa.Text(), nullable=False),
            sa.Column("outcome", sa.Text(), nullable=False),
            sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
            # hit | miss | NULL, and NULL is "not answered", not "missed".
            sa.Column("outcome_state", sa.String(8), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        )

    if "onboarding_target" not in tabs:
        op.create_table(
            "onboarding_target",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("plan_id", GUID(), sa.ForeignKey("onboarding_plan.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("label", sa.String(200), nullable=False),
            # NULL target means the row is a yes/no and `done` answers it.
            sa.Column("target", sa.Integer(), nullable=True),
            sa.Column("actual", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("due_on", sa.Date(), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        )

    if "onboarding_script" not in tabs:
        op.create_table(
            "onboarding_script",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("plan_id", GUID(), sa.ForeignKey("onboarding_plan.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            # Not a foreign key: motions are labels on a plan, and renaming one must not cascade
            # a delete over the lines somebody saved under it.
            sa.Column("motion", sa.String(64), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "onboarding_conversation" not in tabs:
        op.create_table(
            "onboarding_conversation",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("plan_id", GUID(), sa.ForeignKey("onboarding_plan.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("name", sa.String(160), nullable=True),
            sa.Column("team", sa.String(160), nullable=True),
            sa.Column("context", sa.Text(), nullable=True),
            sa.Column("next_step", sa.Text(), nullable=True),
            # hot | warm | no | NULL, and NULL is unrated.
            sa.Column("heat", sa.String(8), nullable=True),
            sa.Column("appointment_set", sa.Boolean(), nullable=False,
                      server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    # Composite indexes, after the tables exist. Guarded by name for the same reason 0086 is: a
    # database built by 0001's create_all already has these under the model's names, and a later
    # migration that drops one has to find the same name on both paths.
    now = _tables()
    for table, (name, cols) in _COMPOSITE.items():
        if table in now and name not in _indexes(table):
            op.create_index(name, table, cols)


def downgrade() -> None:
    tabs = _tables()
    # Children before parents: the FKs are ON DELETE CASCADE in the database, but dropping a
    # parent table out from under a child is a different operation and fails on Postgres.
    for table in ("onboarding_conversation", "onboarding_script", "onboarding_target",
                  "onboarding_block", "onboarding_day", "onboarding_week", "onboarding_plan"):
        if table not in tabs:
            continue
        name = _COMPOSITE[table][0]
        if name in _indexes(table):
            op.drop_index(name, table_name=table)
        op.drop_table(table)
