"""Recording columns on `sales_call` — Recall.ai bots for Alignment Calls (2026-08-18).

Additive and nullable. `meeting_url` is filled by the GHL sync from the "Appointment Link"
opportunity field (written by the workflow at booking time); the rest is written by the bot
scheduler and the Recall webhook. A call with no recording is normal, so nothing here is
required and no existing row needs backfilling.

Revision ID: 0035_sales_call_recording
Revises: 0034_user_totp
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035_sales_call_recording"
down_revision: Union[str, None] = "0034_user_totp"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = (
    ("meeting_url", sa.Column("meeting_url", sa.String(512), nullable=True)),
    ("recall_bot_id", sa.Column("recall_bot_id", sa.String(64), nullable=True)),
    ("recording_status", sa.Column("recording_status", sa.String(32), nullable=True)),
    ("recording_url", sa.Column("recording_url", sa.String(1024), nullable=True)),
    ("recording_at", sa.Column("recording_at", sa.DateTime(timezone=True), nullable=True)),
)
_IX = "ix_sales_call_recall_bot_id"          # the webhook looks a call up by bot id


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "sales_call" not in insp.get_table_names():
        return                                # fresh DB: create_all already made these
    have = {c["name"] for c in insp.get_columns("sales_call")}
    for name, col in _COLS:
        if name not in have:
            op.add_column("sales_call", col)
    if _IX not in {i["name"] for i in insp.get_indexes("sales_call")}:
        op.create_index(_IX, "sales_call", ["recall_bot_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "sales_call" not in insp.get_table_names():
        return
    if _IX in {i["name"] for i in insp.get_indexes("sales_call")}:
        op.drop_index(_IX, table_name="sales_call")
    have = {c["name"] for c in insp.get_columns("sales_call")}
    for name, _ in reversed(_COLS):
        if name in have:
            op.drop_column("sales_call", name)
