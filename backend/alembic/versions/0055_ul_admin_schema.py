"""Utah Life admin console schema

Phase 1 of the admin console spec: data model only. The repo already has audit_log, so this
extends that table instead of creating a second audit_event table.

Revision ID: 0055_ul_admin_schema
Revises: 0054_restore_palette_again
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID, JSONType

revision: str = "0055_ul_admin_schema"
down_revision: Union[str, None] = "0054_restore_palette_again"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspect():
    return sa.inspect(op.get_bind())


def _dialect() -> str:
    return op.get_bind().dialect.name


def _has_table(table: str) -> bool:
    return table in _inspect().get_table_names()


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    return column in {col["name"] for col in _inspect().get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    return name in {ix["name"] for ix in _inspect().get_indexes(table)}


def _has_check(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    return name in {ck["name"] for ck in _inspect().get_check_constraints(table)}


def _has_fk(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    return name in {fk["name"] for fk in _inspect().get_foreign_keys(table)}


def _create_table(table: str, *columns) -> None:
    if not _has_table(table):
        op.create_table(table, *columns)


def _drop_table(table: str) -> None:
    if _has_table(table):
        op.drop_table(table)


def _create_index(name: str, table: str, columns: list[str], **kwargs) -> None:
    if _has_table(table) and not _has_index(table, name):
        op.create_index(name, table, columns, **kwargs)


def _drop_index(name: str, table: str) -> None:
    if _has_table(table) and _has_index(table, name):
        op.drop_index(name, table_name=table)


def _id() -> sa.Column:
    return sa.Column("id", GUID(), primary_key=True)


def _tenant(index: bool = True) -> sa.Column:
    return sa.Column(
        "tenant_id",
        GUID(),
        sa.ForeignKey("tenant.id", ondelete="CASCADE"),
        nullable=False,
        index=index,
    )


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def _publishable() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("draft_dirty", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def _json_default() -> sa.Column:
    return sa.Column("config", JSONType, nullable=False, server_default=sa.text("'{}'"))


def _add_audit_columns() -> None:
    if not _has_column("audit_log", "actor_type"):
        op.add_column("audit_log", sa.Column(
            "actor_type", sa.String(16), nullable=False, server_default="user"))
    if not _has_column("audit_log", "actor_member_id"):
        op.add_column("audit_log", sa.Column("actor_member_id", GUID(), nullable=True))
    if not _has_column("audit_log", "actor_label"):
        op.add_column("audit_log", sa.Column(
            "actor_label", sa.String(255), nullable=False, server_default="Unknown"))
    if not _has_column("audit_log", "category"):
        op.add_column("audit_log", sa.Column(
            "category", sa.String(24), nullable=False, server_default="System"))
    if not _has_column("audit_log", "summary"):
        op.add_column("audit_log", sa.Column(
            "summary", sa.String(500), nullable=False, server_default=""))
        op.execute(sa.text("UPDATE audit_log SET summary = action WHERE summary = ''"))
    if not _has_column("audit_log", "metadata"):
        op.add_column("audit_log", sa.Column(
            "metadata", JSONType, nullable=False, server_default=sa.text("'{}'")))

    if _dialect() == "postgresql":
        if not _has_fk("audit_log", "fk_audit_log_actor_member"):
            op.create_foreign_key(
                "fk_audit_log_actor_member", "audit_log", "intranet_member",
                ["actor_member_id"], ["id"])
        if not _has_check("audit_log", "ck_audit_log_actor_type"):
            op.create_check_constraint(
                "ck_audit_log_actor_type", "audit_log",
                "actor_type IN ('user','system','integration')")
        if not _has_check("audit_log", "ck_audit_log_category"):
            op.create_check_constraint(
                "ck_audit_log_category", "audit_log",
                "category IN ('Publish','Config','Access','Read','Content','System')")
        try:
            op.alter_column(
                "audit_log", "action",
                existing_type=sa.String(48), type_=sa.String(96), existing_nullable=False)
        except Exception:
            pass

    _create_index("ix_audit_log_tenant_created", "audit_log", ["tenant_id", "created_at"])
    _create_index("ix_audit_log_tenant_category_created", "audit_log", ["tenant_id", "category", "created_at"])


def _drop_audit_columns() -> None:
    _drop_index("ix_audit_log_tenant_category_created", "audit_log")
    _drop_index("ix_audit_log_tenant_created", "audit_log")
    if _dialect() == "postgresql":
        if _has_check("audit_log", "ck_audit_log_category"):
            op.drop_constraint("ck_audit_log_category", "audit_log", type_="check")
        if _has_check("audit_log", "ck_audit_log_actor_type"):
            op.drop_constraint("ck_audit_log_actor_type", "audit_log", type_="check")
        if _has_fk("audit_log", "fk_audit_log_actor_member"):
            op.drop_constraint("fk_audit_log_actor_member", "audit_log", type_="foreignkey")
        try:
            op.alter_column(
                "audit_log", "action",
                existing_type=sa.String(96), type_=sa.String(48), existing_nullable=False)
        except Exception:
            pass

    for column in ("metadata", "summary", "category", "actor_label", "actor_member_id", "actor_type"):
        if _has_column("audit_log", column):
            op.drop_column("audit_log", column)


def upgrade() -> None:
    _create_table(
        "intranet_workspace",
        _id(),
        _tenant(),
        sa.Column("portal_name", sa.Text(), nullable=False),
        sa.Column("tagline", sa.Text(), nullable=True),
        sa.Column("subdomain", sa.Text(), nullable=False),
        sa.Column("custom_domain", sa.Text(), nullable=True),
        sa.Column("custom_domain_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("palette", JSONType, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("logo_light_key", sa.Text(), nullable=True),
        sa.Column("logo_dark_key", sa.Text(), nullable=True),
        sa.Column("logo_mark_key", sa.Text(), nullable=True),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="America/Denver"),
        sa.Column("week_starts_on", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("default_calendar_view", sa.Text(), nullable=False, server_default="week"),
        *_publishable(),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", name="uq_intranet_workspace_tenant"),
    )

    _create_table(
        "intranet_role",
        _id(),
        _tenant(),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("sort", sa.SmallInteger(), nullable=False),
        sa.Column("is_leadership", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_publishable(),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "key", name="uq_intranet_role_tenant_key"),
    )

    _create_table(
        "intranet_capability",
        _id(),
        _tenant(),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("sort", sa.SmallInteger(), nullable=False),
        *_publishable(),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "key", name="uq_intranet_capability_tenant_key"),
    )

    _create_table(
        "intranet_permission",
        _id(),
        _tenant(),
        sa.Column("capability_id", GUID(), sa.ForeignKey("intranet_capability.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", GUID(), sa.ForeignKey("intranet_role.id", ondelete="CASCADE"), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        *_publishable(),
        *_timestamps(),
        sa.CheckConstraint("level IN ('Full','View','Limited','None')", name="ck_intranet_permission_level"),
        sa.UniqueConstraint("tenant_id", "capability_id", "role_id",
                            name="uq_intranet_permission_tenant_capability_role"),
    )

    _create_table(
        "intranet_member",
        _id(),
        _tenant(),
        sa.Column("user_id", GUID(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role_id", GUID(), sa.ForeignKey("intranet_role.id"), nullable=False),
        sa.Column("market", sa.Text(), nullable=True),
        sa.Column("auth_source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("auth_source IN ('SSO','Guest','Manual')", name="ck_intranet_member_auth_source"),
        sa.CheckConstraint("status IN ('Active','Invited','Removed')", name="ck_intranet_member_status"),
        sa.UniqueConstraint("tenant_id", "email", name="uq_intranet_member_tenant_email"),
    )

    _add_audit_columns()

    _create_table(
        "intranet_course",
        _id(),
        _tenant(),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("track_progress", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("required_for_onboarding", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("issues_certificate", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sequential", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort", sa.SmallInteger(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        *_publishable(),
        *_timestamps(),
        sa.CheckConstraint("state IN ('Draft','Live','Needs Review')", name="ck_intranet_course_state"),
    )

    _create_table(
        "intranet_course_role",
        _tenant(),
        sa.Column("course_id", GUID(), sa.ForeignKey("intranet_course.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", GUID(), sa.ForeignKey("intranet_role.id", ondelete="CASCADE"), primary_key=True),
        *_publishable(),
        *_timestamps(),
    )

    _create_table(
        "intranet_lesson",
        _id(),
        _tenant(),
        sa.Column("course_id", GUID(), sa.ForeignKey("intranet_course.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("source_label", sa.Text(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort", sa.SmallInteger(), nullable=False),
        *_publishable(),
        *_timestamps(),
        sa.CheckConstraint("source_type IN ('LOOM','SKOOL','HERE','PDF','EXP','PLACE')",
                           name="ck_intranet_lesson_source_type"),
    )

    _create_table(
        "intranet_sop_category",
        _id(),
        _tenant(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("sort", sa.SmallInteger(), nullable=False),
        *_publishable(),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "name", name="uq_intranet_sop_category_tenant_name"),
    )

    _create_table(
        "intranet_sop",
        _id(),
        _tenant(),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category_id", GUID(), sa.ForeignKey("intranet_sop_category.id"), nullable=False),
        sa.Column("owner_member_id", GUID(), sa.ForeignKey("intranet_member.id"), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("review_due_on", sa.Date(), nullable=True),
        sa.Column("current_version_id", GUID(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        *_publishable(),
        *_timestamps(),
        sa.CheckConstraint("state IN ('Draft','Live','Needs Review','Archived')", name="ck_intranet_sop_state"),
    )

    _create_table(
        "intranet_sop_version",
        _id(),
        _tenant(),
        sa.Column("sop_id", GUID(), sa.ForeignKey("intranet_sop.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_label", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("uploaded_by", GUID(), sa.ForeignKey("intranet_member.id"), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        *_publishable(),
        *_timestamps(),
        sa.UniqueConstraint("sop_id", "version_label", name="uq_intranet_sop_version_label"),
    )
    if _dialect() != "sqlite" and not _has_fk("intranet_sop", "fk_sop_current_version"):
        op.create_foreign_key(
            "fk_sop_current_version", "intranet_sop", "intranet_sop_version",
            ["current_version_id"], ["id"])

    _create_table(
        "intranet_sop_acknowledgement",
        _id(),
        _tenant(),
        sa.Column("sop_version_id", GUID(), sa.ForeignKey("intranet_sop_version.id"), nullable=False),
        sa.Column("member_id", GUID(), sa.ForeignKey("intranet_member.id"), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        *_timestamps(),
        sa.UniqueConstraint("sop_version_id", "member_id", name="uq_intranet_sop_ack_version_member"),
    )

    _create_table(
        "intranet_wtd_list",
        _id(),
        _tenant(),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False, server_default="follow_up_boss"),
        sa.Column("external_list_id", sa.Text(), nullable=True),
        sa.Column("script_name", sa.Text(), nullable=True),
        sa.Column("daily_target", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_publishable(),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "position", name="uq_intranet_wtd_tenant_position"),
    )

    _create_table(
        "intranet_launchpad_tile",
        _id(),
        _tenant(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("logo_key", sa.Text(), nullable=True),
        sa.Column("tile_group", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("auth_type", sa.Text(), nullable=False),
        sa.Column("sort", sa.SmallInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_publishable(),
        *_timestamps(),
        sa.CheckConstraint("auth_type IN ('SSO','Deeplink','Invite','Link')",
                           name="ck_intranet_launchpad_auth_type"),
    )

    _create_table(
        "intranet_launchpad_tile_role",
        _tenant(),
        sa.Column("tile_id", GUID(), sa.ForeignKey("intranet_launchpad_tile.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", GUID(), sa.ForeignKey("intranet_role.id", ondelete="CASCADE"), primary_key=True),
        *_publishable(),
        *_timestamps(),
    )

    _create_table(
        "intranet_calendar_category",
        _id(),
        _tenant(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("color", sa.Text(), nullable=False),
        sa.Column("calendar_address", sa.Text(), nullable=True),
        sa.Column("sort", sa.SmallInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_publishable(),
        *_timestamps(),
    )

    _create_table(
        "intranet_calendar_category_role",
        _tenant(),
        sa.Column("category_id", GUID(), sa.ForeignKey("intranet_calendar_category.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", GUID(), sa.ForeignKey("intranet_role.id", ondelete="CASCADE"), primary_key=True),
        *_publishable(),
        *_timestamps(),
    )

    _create_table(
        "intranet_integration",
        _id(),
        _tenant(),
        sa.Column("provider_key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("role_label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        _json_default(),
        sa.Column("credential_ref", sa.Text(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("status IN ('Connected','Action Needed','Not Connected')",
                           name="ck_intranet_integration_status"),
        sa.UniqueConstraint("tenant_id", "provider_key", name="uq_intranet_integration_tenant_provider"),
    )

    _create_table(
        "intranet_ai_source",
        _id(),
        _tenant(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("min_role_id", GUID(), sa.ForeignKey("intranet_role.id"), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_crawled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("indexed_item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sort", sa.SmallInteger(), nullable=False),
        *_publishable(),
        *_timestamps(),
    )

    _create_table(
        "intranet_ai_setting",
        sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("always_cite", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("refuse_without_source", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("offer_escalation", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("learn_from_corrections", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("escalation_channel", sa.Text(), nullable=True),
        *_publishable(),
        *_timestamps(),
    )

    _create_table(
        "intranet_content_gap",
        _id(),
        _tenant(),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("ask_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("assigned_member_id", GUID(), sa.ForeignKey("intranet_member.id"), nullable=True),
        sa.Column("first_asked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_asked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("status IN ('Open','Assigned','Resolved','No Action')",
                           name="ck_intranet_content_gap_status"),
    )

    _create_table(
        "intranet_setup_task",
        _id(),
        _tenant(),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("destination", sa.Text(), nullable=False),
        sa.Column("sort", sa.SmallInteger(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by", GUID(), sa.ForeignKey("intranet_member.id"), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "key", name="uq_intranet_setup_task_tenant_key"),
    )

    _create_table(
        "intranet_publish_batch",
        _id(),
        _tenant(),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("published_by", GUID(), sa.ForeignKey("intranet_member.id"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("snapshot", JSONType, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_by", GUID(), sa.ForeignKey("intranet_member.id"), nullable=True),
        *_timestamps(),
    )

    _create_table(
        "intranet_pending_change",
        _id(),
        _tenant(),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", GUID(), nullable=True),
        sa.Column("change_kind", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("actor_member_id", GUID(), sa.ForeignKey("intranet_member.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("publish_batch_id", GUID(), sa.ForeignKey("intranet_publish_batch.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("change_kind IN ('created','updated','deleted')",
                           name="ck_intranet_pending_change_kind"),
    )
    _create_index(
        "ix_intranet_pending_change_unpublished",
        "intranet_pending_change",
        ["tenant_id"],
        sqlite_where=sa.text("publish_batch_id IS NULL"),
        postgresql_where=sa.text("publish_batch_id IS NULL"),
    )


def downgrade() -> None:
    _drop_index("ix_intranet_pending_change_unpublished", "intranet_pending_change")
    _drop_table("intranet_pending_change")
    _drop_table("intranet_publish_batch")
    _drop_table("intranet_setup_task")
    _drop_table("intranet_content_gap")
    _drop_table("intranet_ai_setting")
    _drop_table("intranet_ai_source")
    _drop_table("intranet_integration")
    _drop_table("intranet_calendar_category_role")
    _drop_table("intranet_calendar_category")
    _drop_table("intranet_launchpad_tile_role")
    _drop_table("intranet_launchpad_tile")
    _drop_table("intranet_wtd_list")
    _drop_table("intranet_sop_acknowledgement")
    if _dialect() != "sqlite" and _has_fk("intranet_sop", "fk_sop_current_version"):
        op.drop_constraint("fk_sop_current_version", "intranet_sop", type_="foreignkey")
    _drop_table("intranet_sop_version")
    _drop_table("intranet_sop")
    _drop_table("intranet_sop_category")
    _drop_table("intranet_lesson")
    _drop_table("intranet_course_role")
    _drop_table("intranet_course")
    _drop_audit_columns()
    _drop_table("intranet_member")
    _drop_table("intranet_permission")
    _drop_table("intranet_capability")
    _drop_table("intranet_role")
    _drop_table("intranet_workspace")
