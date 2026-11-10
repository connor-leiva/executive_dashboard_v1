"""The weekly lead-source digest: its per-group config and its deliveries.

Phase 6 of docs/specs/ulrg-reporting-dashboard-port.md.

  lead_source_digest           one per lead-source group: schedule, recipients, the coaching bar,
                               ClickUp list and assignee, model. Off until an admin turns it on.
  lead_source_digest_delivery  one per send, unique on (digest, run_key): `week:<date>` for the
                               scheduled send, `test:<uuid>` for a test.

SEED, for Utah Life's realtor_com group only (a `ulrg` business): one config, INACTIVE, with a
single recipient -- the workspace user named Connor Leiva, otherwise connor.leiva@gmail.com -- no
ClickUp list, the default model. Nothing is sent on deploy: an inactive digest is never queued.

Revision ID: 0106_lead_source_digest
Revises: 0105_lead_source_facts
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID, JSONType

revision: str = "0106_lead_source_digest"
down_revision: Union[str, None] = "0105_lead_source_facts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DIG, _DEL = "lead_source_digest", "lead_source_digest_delivery"


def _tables(bind) -> set:
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table: str) -> set:
    insp = sa.inspect(bind)
    return {i["name"] for i in insp.get_indexes(table)} if table in insp.get_table_names() else set()


def upgrade() -> None:
    bind = op.get_bind()
    have = _tables(bind)
    if _DIG not in have:
        op.create_table(
            _DIG,
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
            sa.Column("lead_source_group_id", GUID(), sa.ForeignKey("lead_source_group.id", ondelete="CASCADE"),
                      nullable=False, unique=True),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("send_weekday", sa.SmallInteger(), nullable=False),
            sa.Column("send_hour", sa.SmallInteger(), nullable=False),
            sa.Column("send_minute", sa.SmallInteger(), nullable=False),
            sa.Column("timezone", sa.String(64), nullable=True),
            sa.Column("recipients", JSONType, nullable=False),
            sa.Column("unsubscribed", JSONType, nullable=False),
            sa.Column("max_agents", sa.SmallInteger(), nullable=False),
            sa.Column("min_leads", sa.SmallInteger(), nullable=False),
            sa.Column("include_reinforce", sa.Boolean(), nullable=False),
            sa.Column("benchmarks", JSONType, nullable=True),
            sa.Column("clickup_list_id", sa.String(64), nullable=True),
            sa.Column("clickup_assignee", sa.String(64), nullable=True),
            sa.Column("agent_map", JSONType, nullable=True),
            sa.Column("model", sa.String(80), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    if _DEL not in have:
        op.create_table(
            _DEL,
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
            sa.Column("digest_id", GUID(), sa.ForeignKey(f"{_DIG}.id", ondelete="CASCADE"), nullable=False),
            sa.Column("run_key", sa.String(64), nullable=False),
            sa.Column("week_ending", sa.Date(), nullable=False),
            sa.Column("is_test", sa.Boolean(), nullable=False),
            sa.Column("status", sa.String(12), nullable=False),
            sa.Column("attempts", sa.SmallInteger(), nullable=False),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("recipients", JSONType, nullable=True),
            sa.Column("subject", sa.String(300), nullable=True),
            sa.Column("model_used", sa.String(80), nullable=True),
            sa.Column("agents", JSONType, nullable=True),
            sa.Column("clickup_tasks", JSONType, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("digest_id", "run_key", name="uq_lead_source_digest_run"),
        )
    for table, cols in ((_DIG, ("tenant_id",)), (_DEL, ("tenant_id", "digest_id"))):
        ix = _indexes(bind, table)
        for col in cols:
            if f"ix_{table}_{col}" not in ix:
                op.create_index(f"ix_{table}_{col}", table, [col])
    _seed(bind)


def _seed(bind) -> None:
    md = sa.MetaData()
    business = sa.Table("business", md, sa.Column("id", GUID()), sa.Column("tenant_id", GUID()),
                        sa.Column("key", sa.String(32)))
    group = sa.Table("lead_source_group", md, sa.Column("id", GUID()), sa.Column("tenant_id", GUID()),
                     sa.Column("business_id", GUID()), sa.Column("key", sa.String(64)))
    user = sa.Table("user", md, sa.Column("tenant_id", GUID()), sa.Column("name", sa.String(200)),
                    sa.Column("email", sa.String(255)))
    dig = sa.Table(_DIG, md, sa.Column("id", GUID()), sa.Column("tenant_id", GUID()),
                   sa.Column("lead_source_group_id", GUID()), sa.Column("is_active", sa.Boolean()),
                   sa.Column("send_weekday", sa.SmallInteger()), sa.Column("send_hour", sa.SmallInteger()),
                   sa.Column("send_minute", sa.SmallInteger()), sa.Column("recipients", JSONType),
                   sa.Column("unsubscribed", JSONType), sa.Column("max_agents", sa.SmallInteger()),
                   sa.Column("min_leads", sa.SmallInteger()), sa.Column("include_reinforce", sa.Boolean()),
                   sa.Column("model", sa.String(80)))
    if "user" not in _tables(bind) or "lead_source_group" not in _tables(bind):
        return
    rows = bind.execute(sa.select(group.c.id, group.c.tenant_id)
                        .join(business, business.c.id == group.c.business_id)
                        .where(business.c.key == "ulrg", group.c.key == "realtor_com")).all()
    for gid, tid in rows:
        if bind.execute(sa.select(dig.c.id).where(dig.c.lead_source_group_id == gid)).first():
            continue
        email = bind.execute(sa.select(user.c.email).where(user.c.tenant_id == tid,
                                                           user.c.name == "Connor Leiva")).scalar()
        bind.execute(dig.insert().values(
            id=uuid.uuid4(), tenant_id=tid, lead_source_group_id=gid, is_active=False, send_weekday=0,
            send_hour=6, send_minute=0, recipients=[(email or "connor.leiva@gmail.com").lower()],
            unsubscribed=[], max_agents=6, min_leads=1, include_reinforce=True, model="claude-sonnet-4-6"))


def downgrade() -> None:
    have = _tables(op.get_bind())
    for table in (_DEL, _DIG):
        if table in have:
            op.drop_table(table)
