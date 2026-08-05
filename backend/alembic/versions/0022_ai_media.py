"""AI Employees — media library (Summer's b-roll / stock / event photo repository)

Creates ai_media_asset: the searchable catalog for the assets Summer pulls from when building
content. Blobs live in object storage (R2, shared with the Binder via binder_storage); this row
holds the metadata (kind, title, description/caption, tags, storage_ref, content_type, size,
dimensions). GUID PKs + JSONType per repo convention. Idempotent guards; local/SQLite builds
from create_all, so this migration matters for Postgres (prod).

Revision ID: 0022_ai_media
Revises: 0021_ai_employees
Create Date: 2026-08-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision: str = "0022_ai_media"
down_revision: Union[str, None] = "0021_ai_employees"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def _has_index(bind, table: str, name: str) -> bool:
    if not _has_table(bind, table):
        return False
    return name in {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "ai_media_asset"):
        op.create_table(
            "ai_media_asset",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True),
            sa.Column("employee_id", GUID(), sa.ForeignKey("ai_employee.id", ondelete="CASCADE"), index=True),
            sa.Column("kind", sa.String(20), server_default="stock"),
            sa.Column("title", sa.String(160), server_default=""),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("tags", JSONType, nullable=True),
            sa.Column("storage_ref", sa.String(400), nullable=False),
            sa.Column("filename", sa.String(200), nullable=False),
            sa.Column("content_type", sa.String(80), server_default="application/octet-stream"),
            sa.Column("size_bytes", sa.Integer(), server_default="0"),
            sa.Column("width", sa.Integer(), nullable=True),
            sa.Column("height", sa.Integer(), nullable=True),
            sa.Column("uploaded_by", GUID(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if not _has_index(bind, "ai_media_asset", "ix_ai_media_te"):
        op.create_index("ix_ai_media_te", "ai_media_asset", ["tenant_id", "employee_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind, "ai_media_asset", "ix_ai_media_te"):
        op.drop_index("ix_ai_media_te", table_name="ai_media_asset")
    if _has_table(bind, "ai_media_asset"):
        op.drop_table("ai_media_asset")
