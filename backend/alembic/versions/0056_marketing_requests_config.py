"""Marketing request destination config, per tenant

Phase 7 of the Utah Life handoff: every tenant-visible intranet behaviour must be configurable
in the admin console. Marketing Requests was the one that was not -- it existed only as a bare
{url, label} pair inside the intranet's JSON config blob, which is a link, not a workflow, and
nothing in the console could edit it.

This is the CONFIGURATION only. The request record itself, its attachments and its status
transitions are Phase 10; the table there will reference this one for where a request goes.

WHY A SINGLETON TABLE RATHER THAN COLUMNS ON intranet_workspace. The workspace row is the
tenant's identity -- name, domain, palette, marks. A delivery destination is not identity, and
intranet_ai_setting already establishes the shape for a per-tenant settings singleton. It also
leaves Phase 10 free to foreign-key this without pulling the workspace in.

Revision ID: 0056_marketing_requests_config
Revises: 0055_ul_admin_schema
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID, JSONType

revision: str = "0056_marketing_requests_config"
down_revision: Union[str, None] = "0055_ul_admin_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "intranet_marketing_setting"


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        # none | slack | email | webhook. "none" is the default and a real answer: a workspace
        # that has not chosen a destination should say so rather than imply one exists.
        sa.Column("destination_type", sa.Text(), nullable=False, server_default="none"),
        # A channel, an address or an https URL -- something a person types and reads back.
        # NOT a credential: anything token-shaped belongs in intranet_integration, which encrypts
        # and masks. This row is returned to the browser in full.
        sa.Column("destination", sa.Text(), nullable=True),
        sa.Column("default_role_id", GUID(),
                  sa.ForeignKey("intranet_role.id", ondelete="SET NULL"), nullable=True),
        sa.Column("required_fields", JSONType, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("notify", sa.Text(), nullable=True),
        # Written by a real delivery attempt, never by saving the form. Null means never tested,
        # which is why there is no `connected` boolean here -- config cannot assert that about
        # itself, and a column called `connected` would let the console claim a connection that
        # nobody has exercised.
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_detail", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("draft_dirty", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    print(f"[0056] created {TABLE}", flush=True)


def downgrade() -> None:
    if _has_table(TABLE):
        op.drop_table(TABLE)
