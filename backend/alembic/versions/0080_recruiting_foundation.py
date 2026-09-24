"""ULRG Recruiting — the read-path foundation (RECRUITING-SPEC §4, Phase 1).

Five tables and one constraint widening. Nothing here writes to GoHighLevel; the outbox
(`recruiting_action`) and the queue (`recruiting_queue_item`) arrive in Phases 4 and 3.

WHY NEW TABLES RATHER THAN MetricRecord. `sync._ghl_snapshot` REPLACES a kind's row set on every
run, because GHL carries current state and no history. Recruiting is almost entirely history
questions -- how long in stage, who signed this month, speed to lead, show rate -- so it keeps an
append-only spine (`recruiting_stage_event`, `recruiting_activity`) and upserts its current-state
rows. Reusing `MetricRecord(kind="recruiting")` would also collide with the Forum's funnel, which
already writes that kind as a snapshot count.

THE AUDIT CATEGORY SHIPS IN THIS MIGRATION, NOT A LATER ONE. `ck_audit_log_category` is created
Postgres-only, so a missing value passes the whole SQLite test suite and 500s in production the
first time somebody presses the button -- which is exactly what 0060 was written to repair, across
thirteen console screens. Recruiting's first audited action is a text message to a real recruit,
so the constraint is widened here, three phases before anything writes one.

Revision ID: 0080_recruiting_foundation
Revises: 0079_rebrand_stored_names
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType
from app.services.audit import AUDIT_CATEGORIES

revision = "0080_recruiting_foundation"
down_revision = "0079_rebrand_stored_names"
branch_labels = None
depends_on = None

_WITHOUT_RECRUITING = sorted(set(AUDIT_CATEGORIES) - {"Recruiting"})


def _dialect() -> str:
    return op.get_bind().dialect.name


def _has_check(table: str, name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return any(c.get("name") == name for c in insp.get_check_constraints(table))


def _sql_in(values) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in sorted(values))


def upgrade() -> None:
    op.create_table(
        "recruiting_seat",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("business_id", GUID(), sa.ForeignKey("business.id"), nullable=False),
        sa.Column("user_id", GUID(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("member_id", GUID(), sa.ForeignKey("intranet_member.id", ondelete="SET NULL"), nullable=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("title", sa.String(160), nullable=True),
        sa.Column("ghl_user_id", sa.String(64), nullable=True),
        sa.Column("calendar_id", sa.String(64), nullable=True),
        sa.Column("from_number", sa.String(20), nullable=True),
        # sa.false()/sa.true(), NOT text("0")/text("1"). Postgres refuses an integer default on a
        # boolean column outright -- "column is of type boolean but default expression is of type
        # integer" -- while SQLite accepts it happily. The suite runs on SQLite, so this crashed
        # only in production, on the deploy. 0013, 0015 and 0021 all had it right already.
        sa.Column("writeback_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_recruiting_seat_tenant_id", "recruiting_seat", ["tenant_id"])
    op.create_index("ix_recruiting_seat_business_id", "recruiting_seat", ["business_id"])
    op.create_index("ix_recruiting_seat_user_id", "recruiting_seat", ["user_id"])
    op.create_index("ix_recruiting_seat_member_id", "recruiting_seat", ["member_id"])
    op.create_index("ix_recruiting_seat_ghl_user_id", "recruiting_seat", ["ghl_user_id"])
    op.create_index("ix_recruiting_seat_calendar_id", "recruiting_seat", ["calendar_id"])
    op.create_index("ix_recruiting_seat_tenant_role", "recruiting_seat", ["tenant_id", "role"])

    op.create_table(
        "recruiting_candidate",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("business_id", GUID(), sa.ForeignKey("business.id"), nullable=False),
        sa.Column("opportunity_id", sa.String(64), nullable=False),
        sa.Column("contact_id", sa.String(64), nullable=True),
        sa.Column("pipeline_id", sa.String(64), nullable=True),
        sa.Column("stage_id", sa.String(64), nullable=True),
        sa.Column("stage_group", sa.String(48), nullable=True),
        sa.Column("status", sa.String(16), nullable=True),
        sa.Column("owner_seat_id", GUID(), sa.ForeignKey("recruiting_seat.id", ondelete="SET NULL"), nullable=True),
        sa.Column("booker_seat_id", GUID(), sa.ForeignKey("recruiting_seat.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("brokerage", sa.String(200), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("source", sa.String(120), nullable=True),
        sa.Column("gci_ttm", sa.Numeric(14, 2), nullable=True),
        sa.Column("entered_stage_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at_src", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at_src", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dnd", JSONType, nullable=True),
        sa.Column("ghl_task_id", sa.String(64), nullable=True),
        sa.Column("source_url", sa.String(512), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        # The upsert key. A constraint rather than a convention: a sync bug that inserted a
        # duplicate would otherwise double a brokerage's pipeline quietly.
        sa.UniqueConstraint("tenant_id", "opportunity_id", name="uq_recruiting_candidate_opp"),
    )
    op.create_index("ix_recruiting_candidate_tenant_id", "recruiting_candidate", ["tenant_id"])
    op.create_index("ix_recruiting_candidate_business_id", "recruiting_candidate", ["business_id"])
    op.create_index("ix_recruiting_candidate_opportunity_id", "recruiting_candidate", ["opportunity_id"])
    op.create_index("ix_recruiting_candidate_contact_id", "recruiting_candidate", ["contact_id"])
    op.create_index("ix_recruiting_candidate_stage_id", "recruiting_candidate", ["stage_id"])
    op.create_index("ix_recruiting_candidate_stage_group", "recruiting_candidate", ["stage_group"])
    op.create_index("ix_recruiting_candidate_owner_seat_id", "recruiting_candidate", ["owner_seat_id"])
    op.create_index("ix_recruiting_candidate_group", "recruiting_candidate",
                    ["tenant_id", "stage_group", "status"])

    op.create_table(
        "recruiting_stage_event",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_id", GUID(), sa.ForeignKey("recruiting_candidate.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_stage_id", sa.String(64), nullable=True),
        sa.Column("to_stage_id", sa.String(64), nullable=True),
        sa.Column("to_group", sa.String(48), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("actor_seat_id", GUID(), sa.ForeignKey("recruiting_seat.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_recruiting_stage_event_tenant_id", "recruiting_stage_event", ["tenant_id"])
    op.create_index("ix_recruiting_stage_event_candidate_id", "recruiting_stage_event", ["candidate_id"])
    op.create_index("ix_recruiting_stage_event_to_group", "recruiting_stage_event", ["to_group"])
    op.create_index("ix_recruiting_stage_event_occurred_at", "recruiting_stage_event", ["occurred_at"])
    op.create_index("ix_recruiting_stage_event_actor_seat_id", "recruiting_stage_event", ["actor_seat_id"])
    op.create_index("ix_recruiting_stage_event_period", "recruiting_stage_event",
                    ["tenant_id", "to_group", "occurred_at"])

    op.create_table(
        "recruiting_appointment",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("calendar_id", sa.String(64), nullable=True),
        sa.Column("seat_id", GUID(), sa.ForeignKey("recruiting_seat.id", ondelete="SET NULL"), nullable=True),
        sa.Column("candidate_id", GUID(), sa.ForeignKey("recruiting_candidate.id", ondelete="SET NULL"), nullable=True),
        sa.Column("contact_id", sa.String(64), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(24), nullable=True),
        sa.Column("created_at_src", sa.DateTime(timezone=True), nullable=True),
        sa.Column("booked_by_seat_id", GUID(), sa.ForeignKey("recruiting_seat.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="ghl"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "event_id", name="uq_recruiting_appt_event"),
    )
    op.create_index("ix_recruiting_appointment_tenant_id", "recruiting_appointment", ["tenant_id"])
    op.create_index("ix_recruiting_appointment_event_id", "recruiting_appointment", ["event_id"])
    op.create_index("ix_recruiting_appointment_calendar_id", "recruiting_appointment", ["calendar_id"])
    op.create_index("ix_recruiting_appointment_seat_id", "recruiting_appointment", ["seat_id"])
    op.create_index("ix_recruiting_appointment_candidate_id", "recruiting_appointment", ["candidate_id"])
    op.create_index("ix_recruiting_appointment_contact_id", "recruiting_appointment", ["contact_id"])
    op.create_index("ix_recruiting_appointment_start_at", "recruiting_appointment", ["start_at"])
    op.create_index("ix_recruiting_appointment_status", "recruiting_appointment", ["status"])
    op.create_index("ix_recruiting_appointment_created_at_src", "recruiting_appointment", ["created_at_src"])
    op.create_index("ix_recruiting_appointment_booked_by_seat_id", "recruiting_appointment", ["booked_by_seat_id"])
    op.create_index("ix_recruiting_appt_window", "recruiting_appointment",
                    ["tenant_id", "start_at", "status"])

    op.create_table(
        "recruiting_activity",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_id", GUID(), sa.ForeignKey("recruiting_candidate.id", ondelete="CASCADE"), nullable=True),
        sa.Column("seat_id", GUID(), sa.ForeignKey("recruiting_seat.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ghl_message_id", sa.String(64), nullable=True),
        sa.Column("ghl_note_id", sa.String(64), nullable=True),
        sa.Column("ghl_task_id", sa.String(64), nullable=True),
        sa.Column("ghl_event_id", sa.String(64), nullable=True),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("summary", sa.String(280), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_recruiting_activity_tenant_id", "recruiting_activity", ["tenant_id"])
    op.create_index("ix_recruiting_activity_candidate_id", "recruiting_activity", ["candidate_id"])
    op.create_index("ix_recruiting_activity_seat_id", "recruiting_activity", ["seat_id"])
    op.create_index("ix_recruiting_activity_kind", "recruiting_activity", ["kind"])
    op.create_index("ix_recruiting_activity_occurred_at", "recruiting_activity", ["occurred_at"])
    op.create_index("ix_recruiting_activity_feed", "recruiting_activity",
                    ["tenant_id", "candidate_id", "occurred_at"])
    op.create_index("ix_recruiting_activity_seat_day", "recruiting_activity",
                    ["tenant_id", "seat_id", "kind", "occurred_at"])
    # PARTIAL uniques. Most of these ids are NULL -- a note has no message id -- and a plain
    # unique over a nullable column does not mean the same thing on SQLite and Postgres. These
    # make "at most one row per GHL id" true on both, which is what stops the Phase 6 poll
    # counting a text the outbox already recorded.
    op.create_index("uq_recruiting_activity_msg", "recruiting_activity",
                    ["tenant_id", "ghl_message_id"], unique=True,
                    sqlite_where=sa.text("ghl_message_id IS NOT NULL"),
                    postgresql_where=sa.text("ghl_message_id IS NOT NULL"))
    op.create_index("uq_recruiting_activity_event", "recruiting_activity",
                    ["tenant_id", "ghl_event_id"], unique=True,
                    sqlite_where=sa.text("ghl_event_id IS NOT NULL"),
                    postgresql_where=sa.text("ghl_event_id IS NOT NULL"))

    # ── the audit vocabulary, on the 0060 pattern ───────────────────────────────────────────
    if _dialect() != "postgresql":
        return
    if _has_check("audit_log", "ck_audit_log_category"):
        op.drop_constraint("ck_audit_log_category", "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_category", "audit_log",
        f"category IN ({_sql_in(AUDIT_CATEGORIES)})")


def downgrade() -> None:
    if _dialect() == "postgresql":
        if _has_check("audit_log", "ck_audit_log_category"):
            op.drop_constraint("ck_audit_log_category", "audit_log", type_="check")
        # NOT VALID, exactly as 0060 does: any Recruiting rows already written are history the
        # product created, and a downgrade should govern new rows without rejecting them.
        op.execute(
            "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_log_category "
            f"CHECK (category IN ({_sql_in(_WITHOUT_RECRUITING)})) NOT VALID")

    op.drop_table("recruiting_activity")
    op.drop_table("recruiting_appointment")
    op.drop_table("recruiting_stage_event")
    op.drop_table("recruiting_candidate")
    op.drop_table("recruiting_seat")
