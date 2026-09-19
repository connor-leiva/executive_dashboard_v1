"""Win the Day becomes a playbook the workspace writes: the document, its script library, each call
list's place in it, and each person's on-ramp start and targets.

The portal's Win the Day was a compiled-in checklist (`WTD_BLOCKS`) with nothing behind it. Now:
- `intranet_wtd_playbook`, one validated document per workspace (services/wtd_playbook);
- `intranet_wtd_script`, the scripts lists point at and the Scripts tab shows;
- `intranet_wtd_list` gains its group, block, cadence, kind, text and scripts;
- `intranet_member` gains `started_on` (the on-ramp's day 1; NULL = not on it) and `wtd_goals`.

Additive and idempotent, like the migrations around it. The `kind` check is added on Postgres
only: SQLite cannot add a constraint to an existing table, and its schema in tests comes from the
model, which declares it.

Revision ID: 0075_wtd_playbook
Revises: 0074_invite_held
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision = "0075_wtd_playbook"
down_revision = "0074_invite_held"
branch_labels = None
depends_on = None

LIST_COLUMNS = (
    ("group_key", sa.Text(), {}),
    ("block_key", sa.Text(), {}),
    ("cadence", sa.Text(), {}),
    ("kind", sa.Text(), {"nullable": False, "server_default": "clear"}),
    ("description", sa.Text(), {}),
    ("script_ids", JSONType, {}),
)
MEMBER_COLUMNS = (
    ("started_on", sa.Date()),
    ("wtd_goals", JSONType),
)


def _inspect():
    return sa.inspect(op.get_bind())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in _inspect().get_columns(table)}


def upgrade() -> None:
    tables = _inspect().get_table_names()
    if "intranet_wtd_playbook" not in tables:
        op.create_table(
            "intranet_wtd_playbook",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("content", JSONType, nullable=False, server_default=sa.text("'{}'")),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("draft_dirty", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", name="uq_intranet_wtd_playbook_tenant"),
        )
        op.create_index("ix_intranet_wtd_playbook_tenant_id", "intranet_wtd_playbook",
                        ["tenant_id"])

    if "intranet_wtd_script" not in tables:
        op.create_table(
            "intranet_wtd_script",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("chip", sa.Text(), nullable=True),
            sa.Column("url", sa.Text(), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("group_key", sa.Text(), nullable=True),
            sa.Column("position", sa.SmallInteger(), nullable=False, server_default="0"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("draft_dirty", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_intranet_wtd_script_tenant_id", "intranet_wtd_script", ["tenant_id"])

    have = _columns("intranet_wtd_list")
    for name, type_, extra in LIST_COLUMNS:
        if name not in have:
            op.add_column("intranet_wtd_list",
                          sa.Column(name, type_, nullable=extra.get("nullable", True),
                                    server_default=extra.get("server_default")))
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        checks = {c["name"] for c in _inspect().get_check_constraints("intranet_wtd_list")}
        if "ck_intranet_wtd_list_kind" not in checks:
            op.create_check_constraint("ck_intranet_wtd_list_kind", "intranet_wtd_list",
                                       "kind IN ('clear','top_down','scan')")

    have = _columns("intranet_member")
    for name, type_ in MEMBER_COLUMNS:
        if name not in have:
            op.add_column("intranet_member", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    have = _columns("intranet_member")
    for name, _ in MEMBER_COLUMNS:
        if name in have:
            op.drop_column("intranet_member", name)
    if op.get_bind().dialect.name == "postgresql":
        checks = {c["name"] for c in _inspect().get_check_constraints("intranet_wtd_list")}
        if "ck_intranet_wtd_list_kind" in checks:
            op.drop_constraint("ck_intranet_wtd_list_kind", "intranet_wtd_list", type_="check")
    have = _columns("intranet_wtd_list")
    for name, _, _ in LIST_COLUMNS:
        if name in have:
            op.drop_column("intranet_wtd_list", name)
    tables = _inspect().get_table_names()
    if "intranet_wtd_script" in tables:
        op.drop_index("ix_intranet_wtd_script_tenant_id", table_name="intranet_wtd_script")
        op.drop_table("intranet_wtd_script")
    if "intranet_wtd_playbook" in tables:
        op.drop_index("ix_intranet_wtd_playbook_tenant_id", table_name="intranet_wtd_playbook")
        op.drop_table("intranet_wtd_playbook")
