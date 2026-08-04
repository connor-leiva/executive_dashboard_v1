"""AI Employees v1 — agentic runs → draft artifacts → human approval

Creates the 7 AI-Employees tables (SPEC-ai-employees-tab §3): ai_skill (product data),
ai_employee, ai_employee_skill, ai_run, ai_artifact, ai_roster_account, ai_intel_entry,
plus the composite indexes on (tenant_id, employee_id) for runs/artifacts/roster and
(tenant_id, status) on ai_run. GUID PKs + JSONType (repo convention, not the spec's int/
JSONB). Idempotent guards so it is safe to re-run; local/SQLite builds from create_all,
so this migration matters for Postgres (prod).

Revision ID: 0021_ai_employees
Revises: 0020_shift_sources
Create Date: 2026-08-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision: str = "0021_ai_employees"
down_revision: Union[str, None] = "0020_shift_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def _has_index(bind, table: str, name: str) -> bool:
    if not _has_table(bind, table):
        return False
    return name in {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def _tenant_col():
    return sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True)


def _created():
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now())


def _updated():
    return sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now())


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "ai_skill"):
        op.create_table(
            "ai_skill",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("key", sa.String(40), unique=True),
            sa.Column("name", sa.String(80)),
            sa.Column("description", sa.Text()),
            sa.Column("default_prompt", sa.Text()),
            sa.Column("default_schedule", sa.String(60), nullable=True),
            sa.Column("artifact_kinds", JSONType),
            sa.Column("output_contract", JSONType),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        )

    if not _has_table(bind, "ai_employee"):
        op.create_table(
            "ai_employee",
            sa.Column("id", GUID(), primary_key=True),
            _tenant_col(),
            sa.Column("name", sa.String(60)),
            sa.Column("role_title", sa.String(80)),
            sa.Column("avatar_color", sa.String(7), nullable=False, server_default="#227175"),
            sa.Column("status", sa.String(12), nullable=False, server_default="active"),
            sa.Column("writeback_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("config", JSONType),
            _created(), _updated(),
        )

    if not _has_table(bind, "ai_employee_skill"):
        op.create_table(
            "ai_employee_skill",
            sa.Column("id", GUID(), primary_key=True),
            _tenant_col(),
            sa.Column("employee_id", GUID(), sa.ForeignKey("ai_employee.id", ondelete="CASCADE"), index=True),
            sa.Column("skill_key", sa.String(40)),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("prompt_override", sa.Text(), nullable=True),
            sa.Column("schedule_override", sa.String(60), nullable=True),
            sa.Column("config", JSONType),
            sa.Column("seed_version", sa.Integer(), nullable=False, server_default="1"),
            sa.UniqueConstraint("employee_id", "skill_key", name="uq_ai_emp_skill"),
        )

    if not _has_table(bind, "ai_run"):
        op.create_table(
            "ai_run",
            sa.Column("id", GUID(), primary_key=True),
            _tenant_col(),
            sa.Column("employee_id", GUID(), sa.ForeignKey("ai_employee.id", ondelete="CASCADE"), index=True),
            sa.Column("skill_key", sa.String(40)),
            sa.Column("trigger", sa.String(12)),
            sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
            sa.Column("trigger_context", JSONType, nullable=True),
            sa.Column("context", JSONType, nullable=True),
            sa.Column("reads", JSONType),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
            _created(),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_ai_run_te", "ai_run", ["tenant_id", "employee_id"])
        op.create_index("ix_ai_run_ts", "ai_run", ["tenant_id", "status"])

    if not _has_table(bind, "ai_artifact"):
        op.create_table(
            "ai_artifact",
            sa.Column("id", GUID(), primary_key=True),
            _tenant_col(),
            sa.Column("run_id", GUID(), sa.ForeignKey("ai_run.id", ondelete="CASCADE"), index=True),
            sa.Column("kind", sa.String(20)),
            sa.Column("lane", sa.String(20)),
            sa.Column("title", sa.String(160)),
            sa.Column("dest_label", sa.String(60), nullable=True),
            sa.Column("payload", JSONType),
            sa.Column("state", sa.String(12), nullable=False, server_default="draft"),
            sa.Column("approved_by", GUID(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
            _created(),
        )
        op.create_index("ix_ai_artifact_tr", "ai_artifact", ["tenant_id", "run_id"])

    if not _has_table(bind, "ai_roster_account"):
        op.create_table(
            "ai_roster_account",
            sa.Column("id", GUID(), primary_key=True),
            _tenant_col(),
            sa.Column("employee_id", GUID(), sa.ForeignKey("ai_employee.id", ondelete="CASCADE"), index=True),
            sa.Column("platform", sa.String(20), nullable=False, server_default="instagram"),
            sa.Column("handle", sa.String(80)),
            sa.Column("why", sa.Text(), nullable=True),
            sa.Column("status", sa.String(12), nullable=False, server_default="watch"),
            sa.Column("added_by", sa.String(12), nullable=False, server_default="human"),
            sa.Column("score_overlap", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("score_offer", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("score_perf", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("in_launch", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("last_audited_at", sa.DateTime(timezone=True), nullable=True),
            _created(), _updated(),
        )
        op.create_index("ix_ai_roster_te", "ai_roster_account", ["tenant_id", "employee_id"])

    if not _has_table(bind, "ai_intel_entry"):
        op.create_table(
            "ai_intel_entry",
            sa.Column("id", GUID(), primary_key=True),
            _tenant_col(),
            sa.Column("employee_id", GUID(), sa.ForeignKey("ai_employee.id", ondelete="CASCADE"), index=True),
            sa.Column("source_run_id", GUID(), sa.ForeignKey("ai_run.id"), nullable=True),
            sa.Column("finding", sa.Text()),
            sa.Column("tags", JSONType),
            _created(),
        )


def downgrade() -> None:
    bind = op.get_bind()
    for name, table in (("ix_ai_roster_te", "ai_roster_account"),
                        ("ix_ai_artifact_tr", "ai_artifact"),
                        ("ix_ai_run_ts", "ai_run"), ("ix_ai_run_te", "ai_run")):
        if _has_index(bind, table, name):
            op.drop_index(name, table_name=table)
    for table in ("ai_intel_entry", "ai_roster_account", "ai_artifact", "ai_run",
                  "ai_employee_skill", "ai_employee", "ai_skill"):
        if _has_table(bind, table):
            op.drop_table(table)
