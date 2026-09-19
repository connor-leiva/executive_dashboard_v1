"""Who's Who becomes a page the workspace sets up: each person's profile, and the page's own settings.

`intranet_member` gains what the mockup's directory and profile draw -- the subtitle line, a tag,
what to bring them, a quote, the bring list, an office, a pronoun for the headings (Bring Her /
Reach Him / They Own), where Message goes, what they own, the photo's focus point, and where they
sit on the page (auto / leadership / agents / hidden) and in what order. `intranet_directory_setting`
holds the page itself: the featured person, the intro, three stats, and the preview count.

A data step carries the old free-text `owns` into `owns_items` as one item, so nothing a workspace
wrote is lost. Additive and idempotent; the new checks are added on Postgres only (SQLite cannot add
a constraint to an existing table; its test schema comes from the model, which declares them).

Revision ID: 0076_whos_who
Revises: 0075_wtd_playbook
"""
import json

from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision = "0076_whos_who"
down_revision = "0075_wtd_playbook"
branch_labels = None
depends_on = None

MEMBER_COLUMNS = (
    ("headline", sa.Text(), {}),
    ("tag", sa.Text(), {}),
    ("help_line", sa.Text(), {}),
    ("quote", sa.Text(), {}),
    ("bring", JSONType, {}),
    ("office", sa.Text(), {}),
    ("pronoun", sa.Text(), {"nullable": False, "server_default": "they"}),
    ("message_url", sa.Text(), {}),
    ("owns_items", JSONType, {}),
    ("photo_focus", sa.Text(), {}),
    ("directory_placement", sa.Text(), {"nullable": False, "server_default": "auto"}),
    ("directory_order", sa.SmallInteger(), {}),
)
CHECKS = (
    ("ck_intranet_member_pronoun", "pronoun IN ('she','he','they')"),
    ("ck_intranet_member_placement", "directory_placement IN ('auto','leadership','agents','hidden')"),
)


def owns_sql(is_pg: bool) -> str:
    """The data step's UPDATE, as a string a test can compile against Postgres without a database.
    See 0046: `:items::jsonb` does not bind (text() will not read a parameter with a colon behind
    it) and crash-looped production; CAST keeps the parameter delimited."""
    value = "CAST(:items AS jsonb)" if is_pg else ":items"
    return f"UPDATE intranet_member SET owns_items = {value} WHERE id = :id"


def _inspect():
    return sa.inspect(op.get_bind())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in _inspect().get_columns(table)}


def upgrade() -> None:
    have = _columns("intranet_member")
    added_owns_items = "owns_items" not in have
    for name, type_, extra in MEMBER_COLUMNS:
        if name not in have:
            op.add_column("intranet_member",
                          sa.Column(name, type_, nullable=extra.get("nullable", True),
                                    server_default=extra.get("server_default")))

    # The old free text becomes the first owned item, once.
    if added_owns_items and "owns" in have:
        bind = op.get_bind()
        update = sa.text(owns_sql(bind.dialect.name == "postgresql"))
        rows = bind.execute(sa.text(
            "SELECT id, owns FROM intranet_member WHERE owns IS NOT NULL AND owns <> ''")).fetchall()
        for row_id, owns in rows:
            if owns.strip():
                bind.execute(update, {"items": json.dumps([{"label": owns.strip()[:120], "url": None}]),
                                      "id": row_id})

    if op.get_bind().dialect.name == "postgresql":
        checks = {c["name"] for c in _inspect().get_check_constraints("intranet_member")}
        for name, clause in CHECKS:
            if name not in checks:
                op.create_check_constraint(name, "intranet_member", clause)

    if "intranet_directory_setting" not in _inspect().get_table_names():
        op.create_table(
            "intranet_directory_setting",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("featured_member_id", GUID(),
                      sa.ForeignKey("intranet_member.id", ondelete="SET NULL"), nullable=True),
            sa.Column("featured_label", sa.Text(), nullable=True),
            sa.Column("intro", sa.Text(), nullable=True),
            sa.Column("stats", JSONType, nullable=False, server_default=sa.text("'[]'")),
            sa.Column("preview_count", sa.SmallInteger(), nullable=False, server_default="9"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", name="uq_intranet_directory_setting_tenant"),
        )
        op.create_index("ix_intranet_directory_setting_tenant_id", "intranet_directory_setting",
                        ["tenant_id"])


def downgrade() -> None:
    if "intranet_directory_setting" in _inspect().get_table_names():
        op.drop_index("ix_intranet_directory_setting_tenant_id",
                      table_name="intranet_directory_setting")
        op.drop_table("intranet_directory_setting")
    if op.get_bind().dialect.name == "postgresql":
        checks = {c["name"] for c in _inspect().get_check_constraints("intranet_member")}
        for name, _ in CHECKS:
            if name in checks:
                op.drop_constraint(name, "intranet_member", type_="check")
    have = _columns("intranet_member")
    for name, _, _ in MEMBER_COLUMNS:
        if name in have:
            op.drop_column("intranet_member", name)
