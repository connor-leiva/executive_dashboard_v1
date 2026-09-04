"""The marketing request record itself

Phase 10. 0056 gave the console somewhere to say where requests should go; this is the request.

WHY THE RECORD LANDS BEFORE DELIVERY DOES. Section 23 still lists the production destination as
an open decision, and the obvious reading is to wait for it. That has the order backwards: the
handoff's own acceptance criteria say a disconnected destination must keep requests saved and
must not drop user input. A request typed up and lost because nothing was listening is worse
than no form at all, and the person who loses it is the agent who did the work of writing it.

So the record is the product. `delivered_at` stays null until something actually delivers, which
is the truthful state today and needs no backfill when delivery ships.

Revision ID: 0057_marketing_requests
Revises: 0056_marketing_requests_config
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID

revision: str = "0057_marketing_requests"
down_revision: Union[str, None] = "0056_marketing_requests_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "intranet_marketing_request"
INDEX = "ix_intranet_marketing_request_tenant_created"


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        # SET NULL, not CASCADE: removing somebody from the roster must not delete the requests
        # they filed. The queue would silently lose work that is still outstanding.
        sa.Column("requester_member_id", GUID(),
                  sa.ForeignKey("intranet_member.id", ondelete="SET NULL"), nullable=True),
        # Denormalised so a request still names who asked once that member is gone.
        sa.Column("requester_label", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("request_type", sa.Text(), nullable=True),
        sa.Column("listing", sa.Text(), nullable=True),
        sa.Column("client", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("priority", sa.Text(), nullable=False, server_default="Normal"),
        sa.Column("status", sa.Text(), nullable=False, server_default="New"),
        sa.Column("assignee_member_id", GUID(),
                  sa.ForeignKey("intranet_member.id", ondelete="SET NULL"), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('New','In Progress','Blocked','Done','Cancelled')",
                           name="ck_intranet_marketing_request_status"),
        sa.CheckConstraint("priority IN ('Low','Normal','High')",
                           name="ck_intranet_marketing_request_priority"),
    )
    # Both readers -- the console queue and an agent's own list -- want newest first within a
    # tenant, so the composite index serves both and neither needs a sort.
    op.create_index(INDEX, TABLE, ["tenant_id", "created_at"])
    print(f"[0057] created {TABLE}", flush=True)


def downgrade() -> None:
    if _has_table(TABLE):
        op.drop_table(TABLE)
