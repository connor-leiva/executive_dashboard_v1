"""A delivery queue for marketing requests.

The request table already carried `delivered_at` and `delivery_detail`, deliberately left null
while there was nothing to deliver with. These are the two columns a RETRY needs, and without them
a retry has no honest shape: with no attempt count it either hammers a dead endpoint every minute
forever or gives up after one failure, and with no next-attempt time there is nothing for a tick
to select on.

Together with `delivered_at` these three say everything about where a request stands, without an
enum column that could disagree with them:

    delivered_at set                        -> delivered
    next_attempt_at set                     -> queued, or waiting out a backoff
    both null (attempts > 0)                -> tried and given up

Rows that predate this migration get next_attempt_at = NULL rather than now(). They were submitted
into a product that never promised to send them anywhere, and a backfill would deliver a stranger's
weeks-old request to a channel as though it had just come in.

Revision ID: 0063_marketing_delivery
Revises: 0062_member_profiles
"""
from alembic import op
import sqlalchemy as sa

revision = "0063_marketing_delivery"
down_revision = "0062_member_profiles"
branch_labels = None
depends_on = None

TABLE = "intranet_marketing_request"
COLUMNS = {
    "delivery_attempts": sa.Column("delivery_attempts", sa.Integer(), nullable=False,
                                   server_default="0"),
    "delivery_next_attempt_at": sa.Column("delivery_next_attempt_at",
                                          sa.DateTime(timezone=True), nullable=True),
}


def _existing() -> set[str]:
    insp = sa.inspect(op.get_bind())
    return {c["name"] for c in insp.get_columns(TABLE)}


def _indexes() -> set[str]:
    insp = sa.inspect(op.get_bind())
    return {i["name"] for i in insp.get_indexes(TABLE)}


def upgrade() -> None:
    have = _existing()
    for name, column in COLUMNS.items():
        if name not in have:
            op.add_column(TABLE, column)
    # The tick asks one question -- "what is due?" -- across every tenant at once, so the index
    # leads with the due time rather than the tenant.
    if "ix_intranet_marketing_request_due" not in _indexes():
        op.create_index("ix_intranet_marketing_request_due", TABLE,
                        ["delivery_next_attempt_at"])


def downgrade() -> None:
    if "ix_intranet_marketing_request_due" in _indexes():
        op.drop_index("ix_intranet_marketing_request_due", table_name=TABLE)
    have = _existing()
    for name in COLUMNS:
        if name in have:
            op.drop_column(TABLE, name)
