"""Scorecard self-service config — scorecard_group.owner_photo_ref (office headshot)

A team's owner headshot lives in object storage (shared binder_storage); the group row keeps the
storage ref. Owner name (scorecard_group.owner_name) already exists and becomes editable in the UI.
Idempotent guard; local/SQLite builds from create_all, so this matters for Postgres (prod).

Revision ID: 0025_scorecard_owner_photo
Revises: 0024_scorecard_agent_office
Create Date: 2026-08-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0025_scorecard_owner_photo"
down_revision: Union[str, None] = "0024_scorecard_agent_office"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "scorecard_group", "owner_photo_ref"):
        op.add_column("scorecard_group", sa.Column("owner_photo_ref", sa.String(length=300), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "scorecard_group", "owner_photo_ref"):
        op.drop_column("scorecard_group", "owner_photo_ref")
