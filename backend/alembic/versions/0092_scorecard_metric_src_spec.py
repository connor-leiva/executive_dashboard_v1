"""scorecard_metric.source_spec — the declarative auto-sync spec (Phase 2 routing engine). A JSON
spec {source, dataset, date_field, filters[], aggregate, attribution} the generic engine interprets,
replacing the hardcoded resolver. Nullable; when set it WINS over resolver_key, else the legacy
resolver still runs, so the live board is untouched until a metric is deliberately flipped. Idempotent;
local/SQLite builds from create_all.

Revision ID: 0092_scorecard_metric_src_spec
Revises: 0091_scorecard_group_active
Create Date: 2026-10-06

(Revision id kept to 30 chars: alembic_version.version_num is VARCHAR(32), and a longer id
stamp-fails on Postgres only — the 0046 crash-loop. The column it adds is still `source_spec`.)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.dbtypes import JSONType

revision: str = "0092_scorecard_metric_src_spec"
down_revision: Union[str, None] = "0091_scorecard_group_active"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "scorecard_metric", "source_spec"):
        op.add_column("scorecard_metric", sa.Column("source_spec", JSONType, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "scorecard_metric", "source_spec"):
        op.drop_column("scorecard_metric", "source_spec")
