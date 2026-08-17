"""TOTP second factor on `user` — powers the Binder step-up unlock (2026-08-16).

Additive and nullable: existing sessions and users are untouched, and nobody is locked out
by the deploy. A section only demands the factor once its guard is switched on (Binder is).

Revision ID: 0034_user_totp
Revises: 0033_new_pipeline_dispositions
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import JSONType

revision: str = "0034_user_totp"
down_revision: Union[str, None] = "0033_new_pipeline_dispositions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = (
    ("totp_secret_enc", sa.Column("totp_secret_enc", sa.String(255), nullable=True)),
    ("totp_confirmed_at", sa.Column("totp_confirmed_at", sa.DateTime(timezone=True), nullable=True)),
    ("totp_recovery", sa.Column("totp_recovery", JSONType, nullable=True)),
    ("totp_last_used", sa.Column("totp_last_used", sa.String(12), nullable=True)),
    ("totp_failed", sa.Column("totp_failed", sa.Integer(), nullable=False, server_default="0")),
    ("totp_locked_until", sa.Column("totp_locked_until", sa.DateTime(timezone=True), nullable=True)),
)


def _existing(bind) -> set:
    return {c["name"] for c in sa.inspect(bind).get_columns("user")}


def upgrade() -> None:
    bind = op.get_bind()
    if "user" not in sa.inspect(bind).get_table_names():
        return
    have = _existing(bind)
    for name, col in _COLS:
        if name not in have:
            op.add_column("user", col)


def downgrade() -> None:
    bind = op.get_bind()
    if "user" not in sa.inspect(bind).get_table_names():
        return
    have = _existing(bind)
    for name, _ in reversed(_COLS):
        if name in have:
            op.drop_column("user", name)
