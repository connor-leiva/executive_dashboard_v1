"""Business financial-entity model + page routing (QBO entity routing)

Additive columns on `business` so a QBO entity can be modeled as its own business and
its P&L routed to a user-chosen dashboard page, without the hardcoded springb→forum/
becollective synthesis:
  - kind                 which compute_financials shape this entity uses
  - display_tab          the nav-tab key this entity's financial area renders on
                         (NULL = use the entity's own key); the user-configurable target
  - include_in_portfolio whether this business's P&L feeds portfolio/consolidation totals

Backfill preserves current behavior exactly: ulrg→real_estate, sympli→commission_jv,
springb→membership + display_tab='forum' (matching the existing _BIZ_TAB mapping); every
existing business stays include_in_portfolio=True. Idempotent guards so it is safe to
re-run; local/SQLite builds its schema from create_all, so this matters for Postgres (prod).

Revision ID: 0014_business_routing
Revises: 0013_books_init
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014_business_routing"
down_revision: Union[str, None] = "0013_books_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "business", "kind"):
        op.add_column("business", sa.Column(
            "kind", sa.String(24), nullable=False, server_default="real_estate"))
    if not _has_column(bind, "business", "display_tab"):
        op.add_column("business", sa.Column("display_tab", sa.String(32), nullable=True))
    if not _has_column(bind, "business", "include_in_portfolio"):
        op.add_column("business", sa.Column(
            "include_in_portfolio", sa.Boolean(), nullable=False, server_default=sa.true()))

    # Backfill to reproduce today's behavior. ulrg keeps the real_estate default.
    biz = sa.table("business", sa.column("key", sa.String), sa.column("kind", sa.String),
                   sa.column("display_tab", sa.String))
    op.execute(biz.update().where(biz.c.key == "sympli").values(kind="commission_jv"))
    op.execute(biz.update().where(biz.c.key == "springb").values(kind="membership", display_tab="forum"))


def downgrade() -> None:
    bind = op.get_bind()
    for col in ("include_in_portfolio", "display_tab", "kind"):
        if _has_column(bind, "business", col):
            op.drop_column("business", col)
