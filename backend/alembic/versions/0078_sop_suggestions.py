"""Something out of date? Tell the owner.

One table: a member's note about one procedure, queued for the console under that procedure and
emailed to its owner (SOP-LIBRARY-SPEC.md, D5). Additive and idempotent; the status check is
added on Postgres only, as the others are -- SQLite builds its schema from the model, which
declares it.

Revision ID: 0078_sop_suggestions
Revises: 0077_sop_library
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID

revision = "0078_sop_suggestions"
down_revision = "0077_sop_library"
branch_labels = None
depends_on = None

TABLE = "intranet_sop_suggestion"


def upgrade() -> None:
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("sop_id", GUID(), sa.ForeignKey("intranet_sop.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("member_id", GUID(), sa.ForeignKey("intranet_member.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="New"),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(f"ix_{TABLE}_tenant_id", TABLE, ["tenant_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.create_check_constraint("ck_intranet_sop_suggestion_status", TABLE,
                                   "status IN ('New','Read','Done')")


def downgrade() -> None:
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        op.drop_index(f"ix_{TABLE}_tenant_id", table_name=TABLE)
        op.drop_table(TABLE)
