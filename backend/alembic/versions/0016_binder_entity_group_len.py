"""Widen legal_entity.entity_group varchar(8) -> varchar(16)

0015 created legal_entity.entity_group as varchar(8), but its own default value
"operating" is 9 characters. Postgres enforces the length and rejects the insert
(StringDataRightTruncationError), so creating an operating entity 500s; SQLite ignores
varchar lengths, which is why local/tests never caught it. Widen the column so every
allowed value ("operating" / "holding") fits, matching the corrected model.

Postgres-only: local/dev SQLite builds its schema from create_all against the (now
String(16)) model, so the alter is skipped there.

Revision ID: 0016_binder_entity_group_len
Revises: 0015_binder_init
Create Date: 2026-07-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0016_binder_entity_group_len"
down_revision: Union[str, None] = "0015_binder_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    if _has_column(bind, "legal_entity", "entity_group"):
        op.alter_column("legal_entity", "entity_group",
                        existing_type=sa.String(8), type_=sa.String(16),
                        existing_nullable=False, existing_server_default="operating")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    if _has_column(bind, "legal_entity", "entity_group"):
        op.alter_column("legal_entity", "entity_group",
                        existing_type=sa.String(16), type_=sa.String(8),
                        existing_nullable=False, existing_server_default="operating")
