"""What the scoped Follow Up Boss lead ingestion stores beyond the lead row it already had.

The lead-source reports read leads whose FUB `source` matches an active lead-source group. Those
people are now fetched by source and creation date, the way the legacy dashboard read them
(services/fub_sync._lead_sources), instead of waiting for the whole-CRM backfill to reach them.

  lead           `src_url`, the person's Follow Up Boss link, and `assigned_name`, FUB's own
                 assigned-to name -- the agent the legacy report showed, kept for a person whose
                 FUB user Axcion does not hold. An index on (tenant, business, origin), because
                 every lead-source query filters on the source strings.
  crm_activity   `deal_stage` and `deal_status`, for the FUB deals now read beside calls, texts,
                 email events and appointments: the pipeline counts an offer from a deal's stage,
                 and the cohort table a close from its status.

Still one email per lead (the primary), no phones, no addresses: see the `Lead` model.
ADDITIVE AND GUARDED, for the same reason as every migration since 0001 builds from the models.

Revision ID: 0109_lead_source_ingest
Revises: 0108_zoom_attendance
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0109_lead_source_ingest"
down_revision: Union[str, None] = "0108_zoom_attendance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEAD_COLUMNS = (("src_url", sa.String(255)), ("assigned_name", sa.String(120)))
ACTIVITY_COLUMNS = (("deal_stage", sa.String(80)), ("deal_status", sa.String(40)))
ORIGIN_INDEX = "ix_lead_origin"


def _tables(bind) -> set:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> set:
    insp = sa.inspect(bind)
    return {c["name"] for c in insp.get_columns(table)} if table in insp.get_table_names() else set()


def _indexes(bind, table: str) -> set:
    insp = sa.inspect(bind)
    return {i["name"] for i in insp.get_indexes(table)} if table in insp.get_table_names() else set()


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "lead" in tables:
        cols = _columns(bind, "lead")
        for name, typ in LEAD_COLUMNS:
            if name not in cols:
                op.add_column("lead", sa.Column(name, typ, nullable=True))
        if ORIGIN_INDEX not in _indexes(bind, "lead"):
            op.create_index(ORIGIN_INDEX, "lead", ["tenant_id", "business_id", "origin"])
    if "crm_activity" in tables:
        cols = _columns(bind, "crm_activity")
        for name, typ in ACTIVITY_COLUMNS:
            if name not in cols:
                op.add_column("crm_activity", sa.Column(name, typ, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "lead" in tables:
        if ORIGIN_INDEX in _indexes(bind, "lead"):
            op.drop_index(ORIGIN_INDEX, table_name="lead")
        cols = _columns(bind, "lead")
        with op.batch_alter_table("lead") as b:
            for name, _typ in LEAD_COLUMNS:
                if name in cols:
                    b.drop_column(name)
    if "crm_activity" in tables:
        cols = _columns(bind, "crm_activity")
        with op.batch_alter_table("crm_activity") as b:
            for name, _typ in ACTIVITY_COLUMNS:
                if name in cols:
                    b.drop_column(name)
