"""Chapters on a stored transcript (2026-08-19).

Topic segmentation for the call-review player: a rail of jump points under the video and tick
marks on the scrubber, so a 20-minute call is skimmable rather than scrubbable.

Two columns, not one. `chapters` NULL means "not generated yet" and `[]` means "generated,
and this call had no discernible structure" — without `chapters_at` those are the same value
to the generator, which would then retry an empty call on every tick forever.

Lives on call_transcript rather than its own table so it purges with the words it describes.

Revision ID: 0037_call_chapters
Revises: 0036_call_transcript
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import JSONType

revision: str = "0037_call_chapters"
down_revision: Union[str, None] = "0036_call_transcript"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "call_transcript"


def _cols(bind):
    insp = sa.inspect(bind)
    if _TABLE not in insp.get_table_names():
        return None
    return {c["name"] for c in insp.get_columns(_TABLE)}


def upgrade() -> None:
    cols = _cols(op.get_bind())
    if cols is None:
        return                                     # fresh DB: create_all already made it
    if "chapters" not in cols:
        op.add_column(_TABLE, sa.Column("chapters", JSONType, nullable=True))
    if "chapters_at" not in cols:
        op.add_column(_TABLE, sa.Column("chapters_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    cols = _cols(op.get_bind())
    if cols is None:
        return
    if "chapters_at" in cols:
        op.drop_column(_TABLE, "chapters_at")
    if "chapters" in cols:
        op.drop_column(_TABLE, "chapters")
