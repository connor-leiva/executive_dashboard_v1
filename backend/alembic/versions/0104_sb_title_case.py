"""Title-case the Spring B scorecard labels already stored on the board.

Names, group names, and department names follow the catalog. Values, goals, and
hand-entered weeks are not touched. Running it again writes the same names.

Revision ID: 0104_sb_title_case
Revises: 0103_sb_round2
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy.orm import Session

revision: str = "0104_sb_title_case"
down_revision: Union[str, None] = "0103_sb_round2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.services.springb_enterprise import apply_enterprise
    sess = Session(bind=op.get_bind())
    apply_enterprise(sess)
    sess.commit()


def downgrade() -> None:
    pass
