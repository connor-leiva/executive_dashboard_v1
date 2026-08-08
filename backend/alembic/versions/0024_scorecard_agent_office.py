"""Per-team scorecard attribution — agent.sisu_group_ids + scorecard_group.sisu_group_id

The Sisu client feed carries no sub-team, but GET /v1/agent/edit-agent/{id} does. A daily roster
sync stores each agent's Sisu group memberships on agent.sisu_group_ids; scorecard_group.sisu_group_id
records the office group a team maps to (tenant data, set by the seed). Together they let the per-team
resolvers attribute closed/UC deals to Davis/SLC/Utah County. Idempotent guards; local/SQLite builds
from create_all, so this matters for Postgres (prod).

Revision ID: 0024_scorecard_agent_office
Revises: 0023_ulrg_scorecard
Create Date: 2026-08-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.dbtypes import JSONType

revision: str = "0024_scorecard_agent_office"
down_revision: Union[str, None] = "0023_ulrg_scorecard"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "agent", "sisu_group_ids"):
        op.add_column("agent", sa.Column("sisu_group_ids", JSONType, nullable=True))
    if not _has_column(bind, "scorecard_group", "sisu_group_id"):
        op.add_column("scorecard_group", sa.Column("sisu_group_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "scorecard_group", "sisu_group_id"):
        op.drop_column("scorecard_group", "sisu_group_id")
    if _has_column(bind, "agent", "sisu_group_ids"):
        op.drop_column("agent", "sisu_group_ids")
