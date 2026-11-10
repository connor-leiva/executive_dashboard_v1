"""Spring B enterprise scorecard: tier, cadence, unit, nullable goal, and the row seed.

Additive. Existing boards keep their rows. Goal 0 stays 0 (still track-only unless scores_zero).
The data step upserts the Spring B membership board and does not delete values.

Revision ID: 0101_springb_tiers
Revises: 0100_workspace_scorecard
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.dbtypes import JSONType

revision: str = "0101_springb_tiers"
down_revision: Union[str, None] = "0100_workspace_scorecard"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    group_cols = [
        ("season", sa.String(120)),
        ("reviewed_in", sa.String(160)),
        ("lens", sa.String(16)),
        ("dept_key", sa.String(40)),
        ("dept_name", sa.String(80)),
        ("lead_name", sa.String(80)),
    ]
    for name, typ in group_cols:
        if not _has_column(bind, "scorecard_group", name):
            op.add_column("scorecard_group", sa.Column(name, typ, nullable=True))

    metric_cols = [
        ("tier", sa.String(16), True, None),
        ("cadence", sa.String(12), False, sa.text("'weekly'")),
        ("unit", sa.String(8), False, sa.text("'count'")),
        ("target_by", sa.String(80), True, None),
        ("slug", sa.String(48), True, None),
        ("provenance", sa.String(8), True, None),
        ("provenance_from", sa.String(40), True, None),
        ("provenance_detail", sa.Text(), True, None),
        ("owner_name", sa.String(80), True, None),
        ("scores_zero", sa.Boolean(), False, sa.text("false")),
        ("cadence_label", sa.String(32), True, None),
        ("check_note", sa.Text(), True, None),
        ("why", sa.Text(), True, None),
        ("anchors", JSONType, True, None),
    ]
    for name, typ, nullable, default in metric_cols:
        if not _has_column(bind, "scorecard_metric", name):
            op.add_column("scorecard_metric", sa.Column(name, typ, nullable=nullable, server_default=default))

    # goal null = track only. Existing zeros stay zeros and still mean track-only.
    if _has_column(bind, "scorecard_metric", "goal"):
        with op.batch_alter_table("scorecard_metric") as b:
            b.alter_column("goal", existing_type=sa.Numeric(12, 4), nullable=True)
    if _has_column(bind, "scorecard_metric", "note"):
        with op.batch_alter_table("scorecard_metric") as b:
            b.alter_column("note", existing_type=sa.String(160), type_=sa.Text(), nullable=True)

    # Seed after the columns exist, in this same transaction. Import here so a fresh
    # interpreter doesn't query the table before the ALTER.
    from app.services.springb_enterprise import apply_enterprise
    # Commit explicitly. A Session bound to Alembic's connection rolls its work back on close,
    # which would drop the seed and the new columns while the revision still got stamped.
    sess = Session(bind=bind)
    apply_enterprise(sess)
    sess.commit()


def downgrade() -> None:
    bind = op.get_bind()
    # Put a 0 back anywhere a goal was cleared, then restore NOT NULL. History of values stays.
    op.execute("UPDATE scorecard_metric SET goal = 0 WHERE goal IS NULL")
    with op.batch_alter_table("scorecard_metric") as b:
        b.alter_column("goal", existing_type=sa.Numeric(12, 4), nullable=False)
    for name in ("anchors", "why", "check_note", "cadence_label", "scores_zero", "owner_name",
                 "provenance_detail", "provenance_from", "provenance", "slug", "target_by",
                 "unit", "cadence", "tier"):
        if _has_column(bind, "scorecard_metric", name):
            op.drop_column("scorecard_metric", name)
    for name in ("lead_name", "dept_name", "dept_key", "lens", "reviewed_in", "season"):
        if _has_column(bind, "scorecard_group", name):
            op.drop_column("scorecard_group", name)
