"""Follow Up Boss follow-ups: what a lead needs for Needs You Today, the open tasks, and a member's
chosen CRM identity.

`lead` held id, stage, agent and a date -- enough for a funnel count and nothing else, so no rule
about who needs a follow-up could be written against it. It gains a name (no contact details: a
follow-up row opens the person in FUB), the lead source, FUB's `contacted` flag and the source's
timestamps. `crm_task` holds the open tasks each sync reads, as a snapshot. And
`intranet_member.agent_links` is the CRM user an admin picked for somebody whose email does not
match, which the portal had promised agents an admin could set with no screen to set it on.

Additive and idempotent, like the migrations around it: every step checks before it acts.

Revision ID: 0073_fub_follow_ups
Revises: 0072_strip_config_secrets
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision = "0073_fub_follow_ups"
down_revision = "0072_strip_config_secrets"
branch_labels = None
depends_on = None

LEAD_COLUMNS = (
    ("name", sa.String(200)),
    ("origin", sa.String(120)),
    ("contacted", sa.Boolean()),
    ("src_created_at", sa.DateTime(timezone=True)),
    ("src_updated_at", sa.DateTime(timezone=True)),
    ("last_activity_at", sa.DateTime(timezone=True)),
    ("synced_at", sa.DateTime(timezone=True)),
)


def _inspect():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    have = {c["name"] for c in _inspect().get_columns("lead")}
    for name, type_ in LEAD_COLUMNS:
        if name not in have:
            op.add_column("lead", sa.Column(name, type_, nullable=True))
    if "ix_lead_follow_up" not in {i["name"] for i in _inspect().get_indexes("lead")}:
        op.create_index("ix_lead_follow_up", "lead", ["tenant_id", "agent_id", "contacted"])

    if "crm_task" not in _inspect().get_table_names():
        op.create_table(
            "crm_task",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("source", sa.String(24), nullable=False, server_default="fub"),
            sa.Column("external_id", sa.String(64), nullable=False),
            sa.Column("person_external_id", sa.String(64), nullable=True),
            sa.Column("agent_id", GUID(), sa.ForeignKey("agent.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("name", sa.String(300), nullable=True),
            sa.Column("task_type", sa.String(40), nullable=True),
            sa.Column("due_on", sa.Date(), nullable=True),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("tenant_id", "source", "external_id", name="uq_crm_task_src_ext"),
        )
        op.create_index("ix_crm_task_tenant_id", "crm_task", ["tenant_id"])
        op.create_index("ix_crm_task_agent_due", "crm_task", ["tenant_id", "agent_id", "due_on"])

    if "agent_links" not in {c["name"] for c in _inspect().get_columns("intranet_member")}:
        op.add_column("intranet_member", sa.Column("agent_links", JSONType, nullable=True))


def downgrade() -> None:
    if "agent_links" in {c["name"] for c in _inspect().get_columns("intranet_member")}:
        op.drop_column("intranet_member", "agent_links")
    if "crm_task" in _inspect().get_table_names():
        op.drop_index("ix_crm_task_agent_due", table_name="crm_task")
        op.drop_index("ix_crm_task_tenant_id", table_name="crm_task")
        op.drop_table("crm_task")
    if "ix_lead_follow_up" in {i["name"] for i in _inspect().get_indexes("lead")}:
        op.drop_index("ix_lead_follow_up", table_name="lead")
    have = {c["name"] for c in _inspect().get_columns("lead")}
    for name, _type in reversed(LEAD_COLUMNS):
        if name in have:
            op.drop_column("lead", name)
