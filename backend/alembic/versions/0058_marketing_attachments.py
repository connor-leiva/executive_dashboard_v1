"""Files that arrive with a marketing request

Phase 10. The request landed in 0057 without attachments, and `attachments` was pulled from the
console's requirable-field vocabulary because the form could not collect a file -- an admin could
otherwise switch requests on and make them impossible to file.

SUBMISSION IS ATOMIC. The files arrive in the same multipart request as the rest of the form and
are written in one transaction, rather than uploaded afterwards against a created request. For a
listing flyer the photograph often IS the request, and a two-step flow whose second step fails
leaves a request that reads as complete with the point of it missing -- silently, and on the
agent who did the work.

Revision ID: 0058_marketing_attachments
Revises: 0057_marketing_requests
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID

revision: str = "0058_marketing_attachments"
down_revision: Union[str, None] = "0057_marketing_requests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "intranet_marketing_attachment"


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        # CASCADE, unlike the request's member FKs: an attachment has no meaning without its
        # request, and bytes nobody can reach are worse than bytes that were deleted.
        sa.Column("request_id", GUID(),
                  sa.ForeignKey("intranet_marketing_request.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        # What the SERVER sniffed, never what the client declared. A browser will label an HTML
        # file as an image, and that label is what a download would echo back to the next viewer.
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", GUID(),
                  sa.ForeignKey("intranet_member.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    print(f"[0058] created {TABLE}", flush=True)


def downgrade() -> None:
    if _has_table(TABLE):
        op.drop_table(TABLE)
