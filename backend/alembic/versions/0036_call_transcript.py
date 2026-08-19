"""Stored transcripts for recorded Alignment Calls (2026-08-19).

Connor's decisions: STORE rather than fetch on demand (search and trend analysis are the
point), keep for ONE YEAR (purge_after, enforced by a worker job), visible to anyone with
Sales Desk access.

One row per call, not per segment: ~250 calls/month is ~3k rows a year instead of a million,
and every useful question is a search over `text` then a jump into `segments`.

Revision ID: 0036_call_transcript
Revises: 0035_sales_call_recording
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID, JSONType

revision: str = "0036_call_transcript"
down_revision: Union[str, None] = "0035_sales_call_recording"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "call_transcript"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _TABLE in insp.get_table_names():
        return                                     # fresh DB: create_all already made it
    op.create_table(
        _TABLE,
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sales_call_id", GUID(), sa.ForeignKey("sales_call.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("recall_bot_id", sa.String(64), nullable=True),
        sa.Column("segments", JSONType, nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("speakers", JSONType, nullable=True),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_call_transcript_tenant_id", _TABLE, ["tenant_id"])
    op.create_index("ix_call_transcript_sales_call_id", _TABLE, ["sales_call_id"])
    op.create_index("ix_call_transcript_recall_bot_id", _TABLE, ["recall_bot_id"])
    # The purge job scans by date, so it must not table-scan a year of transcripts.
    op.create_index("ix_call_transcript_purge_after", _TABLE, ["purge_after"])


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE in sa.inspect(bind).get_table_names():
        op.drop_table(_TABLE)
