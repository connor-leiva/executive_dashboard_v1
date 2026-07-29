"""beCollective Launch — Shift registrant source breakdown (tags set + campaign match)

Additive columns on `launch`: a multi-tag registrant set (shift_reg_tags) and the
utm_campaign substring for Shift-scoped attribution (shift_campaign_match). The per-channel
breakdown itself rides on the bc_shift_reg records' meta, so no new table. Idempotent.

Revision ID: 0020_shift_sources
Revises: 0019_launch_shift_layer
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.dbtypes import JSONType

revision: str = "0020_shift_sources"
down_revision: Union[str, None] = "0019_launch_shift_layer"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = [
    ("shift_reg_tags", sa.Column("shift_reg_tags", JSONType, nullable=True)),
    ("shift_campaign_match", sa.Column("shift_campaign_match", sa.String(80), nullable=True)),
]


def _cols(bind) -> set:
    return {c["name"] for c in sa.inspect(bind).get_columns("launch")}


def upgrade() -> None:
    bind = op.get_bind()
    if "launch" not in sa.inspect(bind).get_table_names():
        return
    have = _cols(bind)
    for name, col in _COLUMNS:
        if name not in have:
            op.add_column("launch", col)


def downgrade() -> None:
    bind = op.get_bind()
    if "launch" not in sa.inspect(bind).get_table_names():
        return
    have = _cols(bind)
    for name, _ in reversed(_COLUMNS):
        if name in have:
            op.drop_column("launch", name)
