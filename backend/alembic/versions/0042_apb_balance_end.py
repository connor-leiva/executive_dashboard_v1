"""Books COA: separate period ACTIVITY from the as-of BALANCE on account_period_balance.

QuickBooks' TrialBalance ignores `start_date`. It is an as-of report by definition: balance
sheet accounts carry their cumulative balance and P&L accounts carry fiscal-year-to-date.
Confirmed directly against the live Forum realm — a July-only pull and a January-to-July pull
return the identical figure.

So `amount` was storing year-to-date under a column named for period activity. The tie-out
could not catch it: mapped and booked were the same wrong number, which is precisely the
property the tie-out is documented not to prove.

`amount` now holds the DIFFERENCE of two as-of pulls, which is true period activity, and
`balance_end` holds the as-of balance the balance sheet needs. Existing rows are wiped rather
than migrated: every one of them holds a year-to-date figure in the activity column, and there
is nothing in the row to convert it with. The next sync refills them correctly, and until it
does an empty table reads as "no balances pulled" rather than as a wrong number.

Revision ID: 0042_apb_balance_end
Revises: 0041_coa_balances
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0042_apb_balance_end"
down_revision: Union[str, None] = "0041_coa_balances"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "account_period_balance"


def _cols(bind) -> set:
    if TABLE not in sa.inspect(bind).get_table_names():
        return set()
    return {c["name"] for c in sa.inspect(bind).get_columns(TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    have = _cols(bind)
    if not have:
        return
    if "balance_end" not in have:
        op.add_column(TABLE, sa.Column("balance_end", sa.Numeric(14, 2), nullable=False,
                                       server_default="0"))
    # Every existing row's `amount` is year-to-date, and no arithmetic here can recover the
    # period figure. Deleting is the honest move: the sync refills within the hour, and a
    # missing balance is reported as "no balances pulled" rather than rendering a wrong one.
    op.execute(sa.text(f"DELETE FROM {TABLE}"))


def downgrade() -> None:
    bind = op.get_bind()
    if "balance_end" in _cols(bind):
        with op.batch_alter_table(TABLE) as b:      # SQLite needs a rebuild to drop a column
            b.drop_column("balance_end")
