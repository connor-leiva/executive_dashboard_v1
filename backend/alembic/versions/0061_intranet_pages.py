"""Pages a workspace writes for itself.

Replaces four bespoke screens -- JV Partners, Listing Marketing, Sunburst Coaching, On The Phone
-- with one authored page type. Each of those is one customer's content wearing a route of its
own, and building them that way means building four more for the next customer.

Create-table only. Nothing is backfilled: a workspace has no authored pages until somebody writes
one, and inventing four empty pages for every existing tenant would be worse than none.

Revision ID: 0061_intranet_pages
Revises: 0060_audit_category_vocabulary
"""
from alembic import op
import sqlalchemy as sa

from app.models import GUID, JSONType

revision = "0061_intranet_pages"
down_revision = "0060_audit_category_vocabulary"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("intranet_page"):
        op.create_table(
            "intranet_page",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(),
                      sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("key", sa.Text(), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("subtitle", sa.Text(), nullable=True),
            sa.Column("nav_group", sa.Text(), nullable=False, server_default="Workspace"),
            sa.Column("sort", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("draft_dirty", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", "key", name="uq_intranet_page_tenant_key"),
        )

    if not _has_table("intranet_page_section"):
        op.create_table(
            "intranet_page_section",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(),
                      sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("page_id", GUID(),
                      sa.ForeignKey("intranet_page.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("heading", sa.Text(), nullable=True),
            sa.Column("body", sa.Text(), nullable=True),
            sa.Column("links", JSONType, nullable=False, server_default=sa.text("'[]'")),
            sa.Column("image_key", sa.Text(), nullable=True),
            sa.Column("sort", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("draft_dirty", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if not _has_table("intranet_page_role"):
        op.create_table(
            "intranet_page_role",
            sa.Column("tenant_id", GUID(),
                      sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("page_id", GUID(),
                      sa.ForeignKey("intranet_page.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("role_id", GUID(),
                      sa.ForeignKey("intranet_role.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("draft_dirty", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    for name in ("intranet_page_role", "intranet_page_section", "intranet_page"):
        if _has_table(name):
            op.drop_table(name)
