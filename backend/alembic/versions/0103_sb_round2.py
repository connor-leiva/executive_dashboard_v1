"""Point the round-2 Spring B rows at their new resolvers.

Additive. Values stay. Goals already set by the team stay (this is a refresh, not a first adopt).

Revision ID: 0103_sb_round2
Revises: 0102_sb_backfill_mark
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy.orm import Session

revision: str = "0103_sb_round2"
down_revision: Union[str, None] = "0102_sb_backfill_mark"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.services.springb_enterprise import apply_enterprise
    sess = Session(bind=op.get_bind())
    apply_enterprise(sess)
    sess.commit()


def downgrade() -> None:
    pass
