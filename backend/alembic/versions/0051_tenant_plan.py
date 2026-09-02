"""Give every workspace a plan, and grandfather the ones that predate plans

Plans gate the platform modules — Books, Binder, the referral flywheel, AI employees — plus how
many businesses and users a workspace may have and which sources it may connect. app/plans.py
owns every one of those numbers.

EXISTING WORKSPACES ARE BACKFILLED TO PORTFOLIO, the most generous tier, rather than to the
column's default. They were provisioned when there was no such thing as a plan and have been
using whatever they use; assigning them the cheapest tier would silently remove tabs from people
mid-session, which is not a pricing decision anybody made — it is a migration deciding to
downgrade a paying customer. Moving a workspace DOWN is a deliberate act with a conversation
attached, so it belongs in the operator console, not here.

New workspaces take the column default, `team`, and are moved up when somebody buys.

Revision ID: 0051_tenant_plan
Revises: 0050_unpin_borrowed_brand
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0051_tenant_plan"
down_revision: Union[str, None] = "0050_unpin_borrowed_brand"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenant", sa.Column("plan", sa.String(16), nullable=False,
                                      server_default="team"))
    # Every row that exists at this moment predates plans entirely.
    n = op.get_bind().execute(sa.text("UPDATE tenant SET plan = 'portfolio'")).rowcount
    print(f"[0051] {n} existing workspace(s) grandfathered to portfolio", flush=True)


def downgrade() -> None:
    op.drop_column("tenant", "plan")
