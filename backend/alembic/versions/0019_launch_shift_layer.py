"""beCollective Launch — The Shift top-of-funnel layer + seat-primary goal basis

Additive columns on `launch`: a lead-up-webinar layer (The Shift) with its own registrant
goal, GHL tag, empirical pace curve, and a seat-primary goal basis so the target can be
"100 members" rather than an ARR figure. Idempotent (column guards) — safe to re-run.

Revision ID: 0019_launch_shift_layer
Revises: 0018_becollective_launch
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.dbtypes import JSONType

revision: str = "0019_launch_shift_layer"
down_revision: Union[str, None] = "0018_becollective_launch"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = [
    ("goal_basis", sa.Column("goal_basis", sa.String(8), nullable=False, server_default="arr")),
    ("seat_goal", sa.Column("seat_goal", sa.Integer(), nullable=True)),
    ("shift_name", sa.Column("shift_name", sa.String(80), nullable=True)),
    ("shift_event_date", sa.Column("shift_event_date", sa.Date(), nullable=True)),
    ("shift_goal", sa.Column("shift_goal", sa.Integer(), nullable=True)),
    ("shift_reg_tag", sa.Column("shift_reg_tag", sa.String(80), nullable=True)),
    ("shift_actual", sa.Column("shift_actual", sa.Integer(), nullable=True)),
    ("shift_pace_curve", sa.Column("shift_pace_curve", JSONType, nullable=True)),
    ("shift_pace_tolerance", sa.Column("shift_pace_tolerance", sa.Numeric(4, 3),
                                       nullable=False, server_default="0.08")),
]


def _cols(bind, table: str) -> set:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "launch" not in sa.inspect(bind).get_table_names():
        return
    have = _cols(bind, "launch")
    for name, col in _COLUMNS:
        if name not in have:
            op.add_column("launch", col)


def downgrade() -> None:
    bind = op.get_bind()
    if "launch" not in sa.inspect(bind).get_table_names():
        return
    have = _cols(bind, "launch")
    for name, _ in reversed(_COLUMNS):
        if name in have:
            op.drop_column("launch", name)
