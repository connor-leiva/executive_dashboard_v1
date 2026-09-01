"""ads_conversion: where a contract value came from, and the cash due at signing

Revision ID: 0048_ads_contract_value
Revises: 0047_ads_module

The ads tab took its contract value from the GHL opportunity's monetaryValue. For a member who
paid in full that happens to equal the contract; for a financed member it is the DOWN PAYMENT,
and for anyone who had not yet reached "Won: Onboarded" there was no row to read it from at all,
so the value was null. Cash collected, presented as contracted.

The fix prices each person off the launch's own price sheet, which means two new facts have to
travel with the number: WHERE it came from (a signed price sheet, the legacy two-price model, or
GHL's free-text amount), and what that price sheet says is due at signing - the figure the funnel
rung labelled "Cash received" needs and never had.
"""
import sqlalchemy as sa
from alembic import op

revision = "0048_ads_contract_value"
down_revision = "0047_ads_module"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable with no server default on purpose: an unpriced row must read as UNKNOWN, never as
    # zero. A zero contract drags the average down and reads as a free seat.
    op.add_column("ad_conversion", sa.Column("value_upfront", sa.Numeric(14, 2), nullable=True))
    op.add_column("ad_conversion", sa.Column("value_source", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("ad_conversion", "value_source")
    op.drop_column("ad_conversion", "value_upfront")
