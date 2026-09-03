from __future__ import annotations

import uuid
from datetime import datetime, date
from decimal import Decimal

from sqlalchemy import (
    String, Text, ForeignKey, Numeric, Integer, SmallInteger, BigInteger,
    Boolean, DateTime, Date, Float, CheckConstraint, UniqueConstraint, Index, func, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .dbtypes import GUID, JSONType


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Tenant(Base):
    __tablename__ = "tenant"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    # active | suspended. A column rather than config because login and every live session
    # read it on the hot path, and because "which tenants are suspended" is a question the
    # operator surface asks directly.
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    # team | business | portfolio — see app/plans.py, which owns every limit attached to them.
    # A column rather than config because gates read it on the hot path and the operator console
    # asks "who is on what" directly. Existing workspaces are backfilled to `portfolio` so nobody
    # loses a tab they were already using; plans.plan_of() also defaults generously for the same
    # reason, since guessing low costs a paying customer a feature and guessing high only costs
    # revenue nobody was collecting.
    plan: Mapped[str] = mapped_column(String(16), default="team", server_default="team")
    # Portfolio-scoped, non-secret config (e.g. Books intercompany elimination account
    # list `books_elim_accounts`). Portfolio-level because eliminations span businesses.
    config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlatformUser(Base):
    """An operator of the PLATFORM, not a member of any tenant.

    Deliberately NOT a `User` with a flag, and deliberately NOT a member of a distinguished
    "platform tenant". Both of those were considered and rejected:

      A flag on User would recreate exactly what phases 1-3 removed — a session bound to one
      realm that can read another. Every route thereafter has to remember the exception, and
      one mis-set boolean is a total compromise.

      A platform TENANT row reuses more machinery but is counted by tenancy._fallback_tenant
      (a second tenant closes the single-tenant fallback, which would change host resolution
      for the live customer the moment the platform tenant is created) and is iterated by all
      five worker jobs.

    A separate table with a separate token shape cannot cross either way, structurally: a
    platform token carries `pu` and no `tid`, so deps.current_user's realm check rejects it;
    a tenant token carries no `pu`, so require_platform_admin rejects it. Both fail closed
    rather than relying on anyone remembering a rule.
    """
    __tablename__ = "platform_user"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Domain(Base):
    __tablename__ = "domain"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    hostname: Mapped[str] = mapped_column(String(255), unique=True)   # e.g. app.acumyn.io
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


class User(Base):
    __tablename__ = "user"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)   # null while invited
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32), default="owner")   # owner | admin | member
    # ── multi-user platform (accounts, roles, tab grants) ──
    status: Mapped[str] = mapped_column(String(16), default="active")       # active | invited | disabled
    tab_access: Mapped[list | None] = mapped_column(JSONType, nullable=True)  # member grants; NULL for owner/admin
    token_version: Mapped[int] = mapped_column(Integer, default=0)          # bump to revoke all outstanding tokens
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    action_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)     # sha256 of invite/reset token
    action_token_purpose: Mapped[str | None] = mapped_column(String(16), nullable=True)  # invite | reset
    action_token_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ── TOTP second factor (step-up for the Binder section) ──
    # The secret is Fernet-encrypted at rest, never stored or logged in plaintext. Enrollment is
    # only live once totp_confirmed_at is set (a started-but-unconfirmed secret can't unlock).
    totp_secret_enc: Mapped[str | None] = mapped_column(String(255), nullable=True)
    totp_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    totp_recovery: Mapped[list | None] = mapped_column(JSONType, nullable=True)   # sha256 of unused codes
    totp_last_used: Mapped[str | None] = mapped_column(String(12), nullable=True)  # replay guard: last code
    totp_failed: Mapped[int] = mapped_column(Integer, default=0)
    totp_locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_user_tenant_email"),)


class IntranetUserState(Base):
    __tablename__ = "intranet_user_state"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String(32))
    state_key: Mapped[str] = mapped_column(String(64), default="global", server_default="global")
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "scope", "state_key",
                         name="uq_intranet_user_state"),
        Index("ix_intranet_state_tenant_scope_key", "tenant_id", "scope", "state_key"),
    )


class AuditLog(Base):
    """Every user-management + integration mutation, for the sell-to-teams trail."""
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(16), default="user", server_default="user")
    actor_member_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("intranet_member.id"), nullable=True)
    actor_label: Mapped[str] = mapped_column(String(255), default="Unknown", server_default="Unknown")
    action: Mapped[str] = mapped_column(String(48))       # user.invited | user.role_changed | integration.connected …
    category: Mapped[str] = mapped_column(String(24), default="System", server_default="System")
    summary: Mapped[str] = mapped_column(String(500), default="", server_default="")
    target_type: Mapped[str | None] = mapped_column(String(24), nullable=True)   # user | integration | business
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONType, nullable=True)          # {"from": "member", "to": "admin"}
    event_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONType, default=dict, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IntranetWorkspace(Base):
    __tablename__ = "intranet_workspace"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    portal_name: Mapped[str] = mapped_column(Text)
    tagline: Mapped[str | None] = mapped_column(Text, nullable=True)
    subdomain: Mapped[str] = mapped_column(Text)
    custom_domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_domain_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    palette: Mapped[dict] = mapped_column(JSONType, default=dict, server_default=text("'{}'"))
    logo_light_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_dark_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_mark_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(Text, default="America/Denver", server_default="America/Denver")
    week_starts_on: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    default_calendar_view: Mapped[str] = mapped_column(Text, default="week", server_default="week")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_intranet_workspace_tenant"),)


class IntranetRole(Base):
    __tablename__ = "intranet_role"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    sort: Mapped[int] = mapped_column(SmallInteger)
    is_leadership: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_intranet_role_tenant_key"),)


class IntranetCapability(Base):
    __tablename__ = "intranet_capability"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    sort: Mapped[int] = mapped_column(SmallInteger)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_intranet_capability_tenant_key"),)


class IntranetPermission(Base):
    __tablename__ = "intranet_permission"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    capability_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("intranet_capability.id", ondelete="CASCADE"), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("intranet_role.id", ondelete="CASCADE"), nullable=False)
    level: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        CheckConstraint("level IN ('Full','View','Limited','None')", name="ck_intranet_permission_level"),
        UniqueConstraint("tenant_id", "capability_id", "role_id",
                         name="uq_intranet_permission_tenant_capability_role"),
    )


class IntranetMember(Base):
    __tablename__ = "intranet_member"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    full_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("intranet_role.id"), nullable=False)
    market: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_source: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        CheckConstraint("auth_source IN ('SSO','Guest','Manual')", name="ck_intranet_member_auth_source"),
        CheckConstraint("status IN ('Active','Invited','Removed')", name="ck_intranet_member_status"),
        UniqueConstraint("tenant_id", "email", name="uq_intranet_member_tenant_email"),
    )


class IntranetCourse(Base):
    __tablename__ = "intranet_course"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(Text)
    track_progress: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    required_for_onboarding: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    issues_certificate: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    sequential: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    sort: Mapped[int] = mapped_column(SmallInteger)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        CheckConstraint("state IN ('Draft','Live','Needs Review')", name="ck_intranet_course_state"),
    )


class IntranetCourseRole(Base):
    __tablename__ = "intranet_course_role"
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    course_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("intranet_course.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("intranet_role.id", ondelete="CASCADE"), primary_key=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IntranetLesson(Base):
    __tablename__ = "intranet_lesson"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    course_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("intranet_course.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    sort: Mapped[int] = mapped_column(SmallInteger)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        CheckConstraint("source_type IN ('LOOM','SKOOL','HERE','PDF','EXP','PLACE')",
                        name="ck_intranet_lesson_source_type"),
    )


class IntranetSopCategory(Base):
    __tablename__ = "intranet_sop_category"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text)
    sort: Mapped[int] = mapped_column(SmallInteger)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_intranet_sop_category_tenant_name"),)


class IntranetSop(Base):
    __tablename__ = "intranet_sop"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text)
    category_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("intranet_sop_category.id"), nullable=False)
    owner_member_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("intranet_member.id"), nullable=True)
    state: Mapped[str] = mapped_column(Text)
    review_due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("intranet_sop_version.id", use_alter=True, name="fk_sop_current_version"), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        CheckConstraint("state IN ('Draft','Live','Needs Review','Archived')", name="ck_intranet_sop_state"),
    )


class IntranetSopVersion(Base):
    __tablename__ = "intranet_sop_version"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    sop_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("intranet_sop.id", ondelete="CASCADE"), nullable=False)
    version_label: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text)
    byte_size: Mapped[int] = mapped_column(BigInteger)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("intranet_member.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("sop_id", "version_label", name="uq_intranet_sop_version_label"),)


class IntranetSopAcknowledgement(Base):
    __tablename__ = "intranet_sop_acknowledgement"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    sop_version_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("intranet_sop_version.id"), nullable=False)
    member_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("intranet_member.id"), nullable=False)
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        UniqueConstraint("sop_version_id", "member_id", name="uq_intranet_sop_ack_version_member"),
    )


class IntranetWtdList(Base):
    __tablename__ = "intranet_wtd_list"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(SmallInteger)
    name: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text, default="follow_up_boss", server_default="follow_up_boss")
    external_list_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    script_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    daily_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", "position", name="uq_intranet_wtd_tenant_position"),)


class IntranetLaunchpadTile(Base):
    __tablename__ = "intranet_launchpad_tile"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text)
    logo_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    tile_group: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    auth_type: Mapped[str] = mapped_column(Text)
    sort: Mapped[int] = mapped_column(SmallInteger)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        CheckConstraint("auth_type IN ('SSO','Deeplink','Invite','Link')",
                        name="ck_intranet_launchpad_auth_type"),
    )


class IntranetLaunchpadTileRole(Base):
    __tablename__ = "intranet_launchpad_tile_role"
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    tile_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("intranet_launchpad_tile.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("intranet_role.id", ondelete="CASCADE"), primary_key=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IntranetCalendarCategory(Base):
    __tablename__ = "intranet_calendar_category"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text)
    color: Mapped[str] = mapped_column(Text)
    calendar_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort: Mapped[int] = mapped_column(SmallInteger)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IntranetCalendarCategoryRole(Base):
    __tablename__ = "intranet_calendar_category_role"
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    category_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("intranet_calendar_category.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("intranet_role.id", ondelete="CASCADE"), primary_key=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IntranetIntegration(Base):
    __tablename__ = "intranet_integration"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_key: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    role_label: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSONType, default=dict, server_default=text("'{}'"))
    credential_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        CheckConstraint("status IN ('Connected','Action Needed','Not Connected')",
                        name="ck_intranet_integration_status"),
        UniqueConstraint("tenant_id", "provider_key", name="uq_intranet_integration_tenant_provider"),
    )


class IntranetAiSource(Base):
    __tablename__ = "intranet_ai_source"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_kind: Mapped[str] = mapped_column(Text)
    min_role_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("intranet_role.id"), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    indexed_item_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sort: Mapped[int] = mapped_column(SmallInteger)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IntranetAiSetting(Base):
    __tablename__ = "intranet_ai_setting"
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), primary_key=True)
    always_cite: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    refuse_without_source: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    offer_escalation: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    learn_from_corrections: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    escalation_channel: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IntranetMarketingSetting(Base):
    """How a marketing request leaves the building, per tenant.

    A singleton keyed on tenant, the same shape as IntranetAiSetting, rather than more columns on
    IntranetWorkspace: the workspace row is the tenant's IDENTITY, and a delivery destination is
    not that. It also keeps Phase 10's request table free to reference this without dragging the
    workspace into it.

    THE DESTINATION IS NOT A CREDENTIAL. `destination` holds a channel name, an address or an
    https URL -- something a person types and can read back. Anything token-shaped belongs in
    IntranetIntegration, which already encrypts and masks; storing a Slack bot token here would
    put a secret in a row this API returns to the browser in full.

    `connected` is deliberately absent as a column. Whether the destination actually works is not
    a fact the config can assert about itself -- it is the result of the last delivery attempt, and
    inventing a boolean here would let the console claim a connection nobody has tested.
    """

    __tablename__ = "intranet_marketing_setting"
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # none | slack | email | webhook. "none" is a real answer and the default: a workspace that
    # has not chosen where requests go should say so, not pretend to have somewhere to send them.
    destination_type: Mapped[str] = mapped_column(Text, default="none", server_default="none")
    destination: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Which role picks the request up. Null means nobody is assigned yet, which the intranet says
    # out loud rather than routing into a void.
    default_role_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("intranet_role.id", ondelete="SET NULL"), nullable=True)
    # Field keys the submitter must fill. A list, because the set is the tenant's decision.
    required_fields: Mapped[list] = mapped_column(JSONType, default=list, server_default=text("'[]'"))
    notify: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set by a real delivery attempt, never by saving the form. Null means never tested.
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    draft_dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IntranetContentGap(Base):
    __tablename__ = "intranet_content_gap"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text)
    ask_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(Text)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_member_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("intranet_member.id"), nullable=True)
    first_asked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_asked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        CheckConstraint("status IN ('Open','Assigned','Resolved','No Action')",
                        name="ck_intranet_content_gap_status"),
    )


class IntranetSetupTask(Base):
    __tablename__ = "intranet_setup_task"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text)
    destination: Mapped[str] = mapped_column(Text)
    sort: Mapped[int] = mapped_column(SmallInteger)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("intranet_member.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_intranet_setup_task_tenant_key"),)


class IntranetPublishBatch(Base):
    __tablename__ = "intranet_publish_batch"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_by: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("intranet_member.id"), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot: Mapped[dict] = mapped_column(JSONType, default=dict, server_default=text("'{}'"))
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("intranet_member.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IntranetPendingChange(Base):
    __tablename__ = "intranet_pending_change"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    change_kind: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_member_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("intranet_member.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    publish_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("intranet_publish_batch.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        CheckConstraint("change_kind IN ('created','updated','deleted')",
                        name="ck_intranet_pending_change_kind"),
        Index("ix_intranet_pending_change_unpublished", "tenant_id",
              sqlite_where=text("publish_batch_id IS NULL"),
              postgresql_where=text("publish_batch_id IS NULL")),
    )


class Business(Base):
    """The profit centers. Brand config is data so each tab can later adopt its own brand."""
    __tablename__ = "business"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(32))           # ulrg | springb | sympli
    name: Mapped[str] = mapped_column(String(120))
    tag: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), default="healthy")  # healthy|watch|opportunity
    # Financial-entity model + page routing (QBO entity routing). `kind` dispatches the
    # compute_financials shape so a non-real-estate entity never renders a Sisu-shaped P&L.
    # `display_tab` is the nav-tab key this entity's FINANCIAL area renders on — the
    # user-configurable routing target; NULL means "use this entity's own key". A business
    # is left out of portfolio/consolidation totals when include_in_portfolio is False
    # (e.g. the operational-only springb holder after the QBO account split).
    kind: Mapped[str] = mapped_column(String(24), default="real_estate")  # real_estate|commission_jv|membership|holding
    display_tab: Mapped[str | None] = mapped_column(String(32), nullable=True)
    include_in_portfolio: Mapped[bool] = mapped_column(Boolean, default=True)
    accent: Mapped[str] = mapped_column(String(9), default="#61835E")   # bright (borders/dots)
    ink: Mapped[str] = mapped_column(String(9), default="#4F6A4D")      # readable text accent
    is_jv: Mapped[bool] = mapped_column(Boolean, default=False)
    jv_share: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("1.0"))  # 0.5 = 50%
    # Flag "watch" when the period margin falls below this (data-driven status).
    watch_margin_below: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # Spring's avg JV revenue per funded loan (drives the flywheel gap, Part 4).
    per_loan_share: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # Attach-rate goal for the flywheel (percent; default 60).
    capture_target: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("60"))
    # Three-lens financials: monthly expense run-rate + commission fallback.
    expense_run_rate_mode: Mapped[str] = mapped_column(String(16), default="trailing_3mo")  # trailing_3mo|last_month|manual
    expense_run_rate_manual: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    default_agent_split: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)  # e.g. 0.60 fallback
    # Sympli JV economics (reverse-engineered from the QBO P&L, tunable in Settings):
    # the loan-officer split (cost of sale) and the operating-cost ratio, both as a
    # fraction of commission revenue. Drive the calculated Live/Projection lenses.
    lo_comp_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)   # 0.55 = 55%
    opex_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)      # 0.29 = 29%
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # Non-secret per-business config: sparkline trend, manual ops tiles, manual
    # funnel, and scorecard contributions (used until a live source connects).
    config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # Binder tie only (SPEC-binder-module Part 1/6): the legal entity this operating
    # business is, used for the tax-lifecycle tie (federal/state_tax obligations read
    # this Business's Books close). Nullable; nothing else about Business changes.
    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("legal_entity.id"), nullable=True)
    # Books / chart-of-accounts standard (SPEC-chart-of-accounts). `archetype` decides which
    # leaf accounts this entity activates from the standard chart, which validations run, and
    # which dashboard blocks render — a property entity has no cost of sale, so it should not
    # show a gross-margin tile at all. `gross_profit_label` keeps the vocabulary native to the
    # business while the structure stays identical: a brokerage says Company Dollar.
    archetype: Mapped[str] = mapped_column(String(16), default="transactional")
    # transactional | program | event | property | holding | dormant
    gross_profit_label: Mapped[str] = mapped_column(String(40), default="Gross Profit")
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_business_tenant_key"),)


class Integration(Base):
    """One row per connected source. For QBO, one row per entity (per realmId)."""
    __tablename__ = "integration"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(24))     # fub | sisu | qbo | arive
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="disconnected")  # connected|disconnected|error
    realm_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # QBO company id
    access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # non-secret per-provider config
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Agent(Base):
    __tablename__ = "agent"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(24))          # sisu | fub
    external_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Sisu group memberships (office/pod/tier group_ids), refreshed by the roster sync — the live
    # source for per-team scorecard attribution (Sisu's client feed carries no sub-team). NULL = not
    # yet fetched (≠ "no groups").
    sisu_group_ids: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    __table_args__ = (UniqueConstraint("tenant_id", "source", "external_id", name="uq_agent_src_ext"),)


class Transaction(Base):
    """Real estate deals from Sisu. Drives units, volume, GCI, pipeline, funnel."""
    __tablename__ = "transaction"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id"))
    source: Mapped[str] = mapped_column(String(24), default="sisu")
    external_id: Mapped[str] = mapped_column(String(64))
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)   # buy | sell
    status: Mapped[str] = mapped_column(String(16))     # active | pending | closed | dead
    gci: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    agent_commission: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)  # cost of sale
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    buyer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    buyer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Attachment-flywheel join keys (Sisu vendor pick + extra borrower contacts).
    mortgage_vid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title_vid: Mapped[int | None] = mapped_column(Integer, nullable=True)   # Sisu title_company_vid → Meraki attach
    buyer_email2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    buyer_phone: Mapped[str | None] = mapped_column(String(16), nullable=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent.id"), nullable=True)
    contract_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_close_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # pending → projection
    # Sisu funnel/leading-indicator dates + raw stage code.
    appt_set_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    appt_met_date: Mapped[date | None] = mapped_column(Date, nullable=True)   # Sisu appt_dt — appointment held/met
    signed_date: Mapped[date | None] = mapped_column(Date, nullable=True)     # Sisu signed_dt — buyer/listing agreement signed
    lead_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    listing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sisu_status_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        UniqueConstraint("tenant_id", "source", "external_id", name="uq_txn_src_ext"),
        Index("ix_txn_business_status", "tenant_id", "business_id", "status"),
    )


class Lead(Base):
    """FUB people/leads for the top of the funnel."""
    __tablename__ = "lead"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id"))
    source: Mapped[str] = mapped_column(String(24), default="fub")
    external_id: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str | None] = mapped_column(String(80), nullable=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent.id"), nullable=True)
    created_at_src: Mapped[date | None] = mapped_column(Date, nullable=True)
    __table_args__ = (UniqueConstraint("tenant_id", "source", "external_id", name="uq_lead_src_ext"),)


class PLSnapshot(Base):
    """One QuickBooks ProfitAndLoss summary per business per period."""
    __tablename__ = "pl_snapshot"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    revenue: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    cogs: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    gross_profit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    opex: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    noi: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    net_income: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    source: Mapped[str] = mapped_column(String(16), default="qbo")
    realm_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    books_closed: Mapped[bool] = mapped_column(Boolean, default=False)  # clears the Booked "close in progress" flag
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("tenant_id", "business_id", "period_start", "period_end", name="uq_pl_period"),
    )


class CashSnapshot(Base):
    """Cash on hand from the QBO BalanceSheet (bank accounts)."""
    __tablename__ = "cash_snapshot"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business.id"), nullable=True)
    as_of: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)


class SyncRun(Base):
    __tablename__ = "sync_run"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(24))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|ok|error
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # {"records": 412, "seconds": 3.1}


class MetricRecord(Base):
    """Flexible records behind non-real-estate metrics (Go High Level: members,
    subscriptions, event registrations). Also feeds the audit drawer."""
    __tablename__ = "metric_record"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id"))
    source: Mapped[str] = mapped_column(String(24))          # ghl
    kind: Mapped[str] = mapped_column(String(32))            # member | subscription | registration
    external_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)   # active | cancelled | registered
    segment: Mapped[str | None] = mapped_column(String(32), nullable=True)  # becollective | forum
    occurred_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    __table_args__ = (UniqueConstraint("tenant_id", "source", "kind", "external_id", name="uq_metricrec"),)


# ── Phase 3 stub (Arive / flywheel) — create the table, do not populate yet ──
class LoanRecord(Base):
    __tablename__ = "loan_record"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(24), default="arive")
    external_id: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str | None] = mapped_column(String(40), nullable=True)  # preapproval|application|locked|funded
    borrower_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    borrower_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    loan_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    referring_agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent.id"), nullable=True)
    matched_transaction_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("transaction.id"), nullable=True)
    funded_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    __table_args__ = (UniqueConstraint("tenant_id", "source", "external_id", name="uq_loan_src_ext"),)


# ══ Acumyn Books module (SPEC-books-module Part 1) ══════════════════════════
# Additive: Books runs the bookkeeping the Command Center reads. The scan pipeline
# and review draft write ONLY to these tables; QuickBooks write-back is a separate,
# feature-flagged, human-approved action. Nothing here replaces PLSnapshot (the
# dashboard's summary source of truth) — PLLine is the detail underneath it.

class BookTxn(Base):
    """One ledger transaction pulled from QBO — the unit the scan pipeline and
    approval queue operate on."""
    __tablename__ = "book_txn"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id"))
    realm_id: Mapped[str] = mapped_column(String(64))                 # QBO company id (matches Integration.realm_id)
    qbo_type: Mapped[str] = mapped_column(String(24))                 # Purchase|Deposit|JournalEntry|Transfer|Bill|BillPayment
    qbo_id: Mapped[str] = mapped_column(String(32))
    sync_token: Mapped[str | None] = mapped_column(String(16), nullable=True)   # sparse-update write-back needs it
    txn_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    payee: Mapped[str | None] = mapped_column(String(200), nullable=True)
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_label: Mapped[str | None] = mapped_column(String(200), nullable=True)   # current category
    account_qbo_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bank_account_label: Mapped[str | None] = mapped_column(String(200), nullable=True)  # feed it rode in on
    came_categorized: Mapped[bool] = mapped_column(Boolean, default=False)  # arrived on a real (non-suspense) account
    scan_state: Mapped[str] = mapped_column(String(16), default="pending")
        # pending -> (cleared | needs_approval | escalated) -> approved -> posted
    suggestion: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
        # {"category", "account_qbo_id", "confidence", "reason"}
    flags: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
        # {"anomaly", "intercompany", "first_vendor", "over_band", "possible_1099", "multi_line"}
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
        # {"action": "approve"|"recategorize"|"escalate"|"ic_characterized", "category", ...}
    posted_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # write-back only
    __table_args__ = (UniqueConstraint("tenant_id", "realm_id", "qbo_type", "qbo_id", name="uq_booktxn_src"),)


class PLLine(Base):
    """Account-level P&L detail behind PLSnapshot group totals — feeds the P&L page.
    The (business, period) sum of section lines ties to the snapshot (Part 7 invariant)."""
    __tablename__ = "pl_line"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    section: Mapped[str] = mapped_column(String(16))                  # income | cogs | expense | other
    parent: Mapped[str | None] = mapped_column(String(200), nullable=True)   # report group header path (e.g. Marketing)
    label: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    position: Mapped[int] = mapped_column(Integer, default=0)         # preserve report row order
    __table_args__ = (
        UniqueConstraint("tenant_id", "business_id", "period_start", "period_end",
                         "section", "parent", "label", name="uq_pl_line"),
    )


class ICRule(Base):
    """CFO-set intercompany policy. Transfers matching a rule flow through; everything
    else stops and escalates (never guessed)."""
    __tablename__ = "ic_rule"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(200))                  # "Office rent to holding LLC"
    from_business_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("business.id"), nullable=True)  # null = any
    to_business_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("business.id"), nullable=True)    # null = any
    characterization: Mapped[str] = mapped_column(String(24))        # loan|distribution|contribution|shared_expense|rent|payroll_alloc
    monthly_cap: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)  # null = no cap; over cap escalates
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ICLink(Base):
    """One intercompany movement — two BookTxn sides tied together, or one side awaiting
    its match / a human characterization. Open (non-tied) links block the close."""
    __tablename__ = "ic_link"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    from_business_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("business.id"))
    to_business_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("business.id"))
    from_txn_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("book_txn.id"), nullable=True)
    to_txn_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("book_txn.id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    occurred_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="unmatched")
        # unmatched -> matched -> (auto_tied | escalated) -> characterized -> tied
    characterization: Mapped[str | None] = mapped_column(String(24), nullable=True)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("ic_rule.id"), nullable=True)  # set when a rule auto-tied it
    decided_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ClosePeriod(Base):
    """Month-end close checklist state, one row per business per month."""
    __tablename__ = "close_period"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id"))
    period: Mapped[date] = mapped_column(Date)                       # month start
    steps: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
        # {"bank_rec", "card_rec", "intercompany", "accruals", "statements"} bools
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | closed
    closed_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("tenant_id", "business_id", "period", name="uq_close_period"),)


class BooksReview(Base):
    """Claude's drafted monthly narrative. Draft until the CFO signs off; Claude never
    posts to the official record."""
    __tablename__ = "books_review"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    period: Mapped[date] = mapped_column(Date)                       # month start
    body: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | signed
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    signed_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("tenant_id", "period", name="uq_books_review"),)


# ── Acumyn Binder module (SPEC-binder-module Part 1) ─────────────────────────
# A document-driven obligation engine. LegalEntity is tenant data (user-created,
# never seeded); JurisdictionRule is product reference data (seeded). Every
# Obligation is human-confirmed before it is tracked — enforced in the service
# layer, never auto-committed by extraction.

class LegalEntity(Base):
    """Every LLC / corp in the portfolio. The Binder's unit; broader than Business
    (most are holding entities with no P&L). Created/edited by the user in the UI,
    one entity at a time — never seeded, hardcoded, or shipped in a migration."""
    __tablename__ = "legal_entity"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    legal_name: Mapped[str] = mapped_column(String(200))           # "Utah Life Real Estate Group, LLC"
    nickname: Mapped[str | None] = mapped_column(String(120), nullable=True)  # "The Team"
    description: Mapped[str | None] = mapped_column(Text, nullable=True)      # the "what they pay for" note
    entity_type: Mapped[str | None] = mapped_column(String(24), nullable=True)   # llc | s_corp | c_corp | partnership | trust
    jurisdiction: Mapped[str | None] = mapped_column(String(2), nullable=True)   # state code: "UT" | "AZ"
    formation_date: Mapped[date | None] = mapped_column(Date, nullable=True)     # anchor for annual-report derivation
    ein: Mapped[str | None] = mapped_column(String(32), nullable=True)           # stored as-is for v1 (Part 14 security note)
    entity_group: Mapped[str] = mapped_column(String(16), default="operating")   # operating | holding (drives the matrix split)
    ownership: Mapped[str | None] = mapped_column(String(16), nullable=True)     # "100%", "70%", "50%"
    business_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("business.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", "legal_name", name="uq_legal_entity"),)


class BinderDocument(Base):
    """A stored document. Evidence behind obligations, or filed on its own."""
    __tablename__ = "binder_document"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("legal_entity.id"), nullable=True)  # null until matched + confirmed
    filename: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(24), default="other")   # formation|insurance|tax|lease|registered_agent|estate|other
    storage_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)   # key/path in storage (Part 2)
    content_hash: Mapped[str] = mapped_column(String(64))                # sha256, dedup
    uploaded_via: Mapped[str] = mapped_column(String(16), default="upload")  # upload | email | folder
    extracted: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # cached raw extraction (incl. entity_attributes)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", "content_hash", name="uq_binder_doc_hash"),)


class Obligation(Base):
    """A tracked obligation. Feeds the matrix. Created ONLY by human confirm — every
    row carries a confirmed_by user (invariant 1)."""
    __tablename__ = "obligation"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("legal_entity.id"))
    kind: Mapped[str] = mapped_column(String(24))            # annual_report|registered_agent|insurance|boi|federal_tax|state_tax|estimated_payments|lease
    jurisdiction: Mapped[str | None] = mapped_column(String(2), nullable=True)
    applicable: Mapped[bool] = mapped_column(Boolean, default=True)      # false -> renders "n/a"
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    cadence: Mapped[str] = mapped_column(String(16), default="annual")   # annual|quarterly|biennial|one_time|none
    lead_days: Mapped[int] = mapped_column(Integer, default=45)          # flag window
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("binder_document.id"), nullable=True)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("jurisdiction_rule.id"), nullable=True)
    last_completed: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)  # NOT NULL in practice (invariant 1)
    last_reminded_stage: Mapped[str | None] = mapped_column(String(12), nullable=True)  # none|lead|urgent|overdue
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)                  # cached AI "why" explanation
    ai_summary_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("tenant_id", "entity_id", "kind", name="uq_obligation"),)


class ProposedObligation(Base):
    """Claude's derivation from a document. NEVER a tracked obligation until a human
    confirms it — the only thing the extraction pipeline writes (with BinderDocument.extracted)."""
    __tablename__ = "proposed_obligation"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("binder_document.id"))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("legal_entity.id"), nullable=True)  # best guess (null if none)
    entity_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    entity_candidates: Mapped[list | None] = mapped_column(JSONType, nullable=True)  # [{entity_id,name,score}] when ambiguous
    kind: Mapped[str] = mapped_column(String(24))
    method: Mapped[str] = mapped_column(String(8))          # read | rule
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    proposed: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # {due_date, lead_days, cadence, fields:[[k,v]], jurisdiction?}
    basis: Mapped[str | None] = mapped_column(Text, nullable=True)          # Claude's plain-English derivation
    flavor: Mapped[str] = mapped_column(String(12), default="normal")       # normal | gap | renewal
    renewal_of_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("obligation.id"), nullable=True)
    state: Mapped[str] = mapped_column(String(12), default="pending")       # pending | confirmed | dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JurisdictionRule(Base):
    """The rules engine (reference data): turns an anchor into a schedule. tenant_id NULL
    means a shared system rule (seeded, Part 4); a tenant may add overrides. The one
    table not strictly tenant-scoped, because jurisdiction rules are not tenant-specific."""
    __tablename__ = "jurisdiction_rule"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), nullable=True, index=True)  # null = system default
    jurisdiction: Mapped[str | None] = mapped_column(String(2), nullable=True)   # state code; null = federal/any
    entity_type: Mapped[str | None] = mapped_column(String(24), nullable=True)   # applies to which types; null = any
    kind: Mapped[str] = mapped_column(String(24))              # obligation kind this rule produces
    cadence: Mapped[str] = mapped_column(String(16))           # annual|quarterly|biennial|one_time|none
    derivation: Mapped[str] = mapped_column(String(24))        # anniversary_month_end|fixed_date|entity_type_calendar|not_applicable|manual
    params: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # {month, day, offset_days, quarters:[...]}
    lead_days_default: Mapped[int] = mapped_column(Integer, default=45)
    last_verified: Mapped[date] = mapped_column(Date, server_default=func.current_date())  # when a human last checked this is current
    source_note: Mapped[str | None] = mapped_column(Text, nullable=True)          # citation / where the rule comes from
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Launch(Base):
    """A single cohort launch (beCollective) tracked against a revenue goal. Config is
    tenant-editable; the current metrics are computed on read from synced opportunity
    data + this config. "ARR" here = annualized revenue ADDED by this cohort (Spring's
    loose usage), not a recurring subscription — renewals live on the other sections."""
    __tablename__ = "launch"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    program: Mapped[str] = mapped_column(String(80), default="beCollective")
    event_start: Mapped[date | None] = mapped_column(Date, nullable=True)      # live event start
    event_end: Mapped[date | None] = mapped_column(Date, nullable=True)        # live event end
    window_start: Mapped[date] = mapped_column(Date)                           # cart opens
    window_end: Mapped[date] = mapped_column(Date)                            # cart closes
    goal_arr: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    ticket_pif: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    ticket_plan: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    plan_installments: Mapped[int] = mapped_column(Integer, default=12)
    mix_pif: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0.50"))   # assumption only
    pipeline_match: Mapped[str] = mapped_column(String(160))                   # GHL pipeline name or id
    cohort_value: Mapped[str | None] = mapped_column(String(80), nullable=True)  # Cohort opp-field value
    pace_model: Mapped[str] = mapped_column(String(10), default="linear")      # linear | curve
    pace_tolerance: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("0.100"))  # fraction of goal
    won_grace_days: Mapped[int] = mapped_column(Integer, default=7)            # won-date guard slack past window
    # Goal basis: "arr" → seat_target = ceil(goal_arr/blended); "seats" → seat_target = seat_goal.
    goal_basis: Mapped[str] = mapped_column(String(8), default="arr")          # arr | seats
    seat_goal: Mapped[int | None] = mapped_column(Integer, nullable=True)      # target members when goal_basis=seats
    # ── The Shift (lead-up webinar) — the top-of-funnel layer that feeds memberships.
    #    Registrants pace against an empirical cumulative curve (pace_model="curve"), and a
    #    reg→member ratio (seat_goal / shift_goal) projects the downstream membership goal. ──
    shift_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    shift_event_date: Mapped[date | None] = mapped_column(Date, nullable=True)          # the "0 days to event" anchor
    shift_goal: Mapped[int | None] = mapped_column(Integer, nullable=True)              # registrant goal
    shift_reg_tag: Mapped[str | None] = mapped_column(String(80), nullable=True)        # legacy single GHL tag
    shift_reg_tags: Mapped[list | None] = mapped_column(JSONType, nullable=True)        # GHL tags that mark a registrant
    shift_campaign_match: Mapped[str | None] = mapped_column(String(80), nullable=True)  # utm_campaign substring for Shift-scoped attribution
    shift_actual: Mapped[int | None] = mapped_column(Integer, nullable=True)            # manual seed / fallback count
    shift_pace_curve: Mapped[dict | None] = mapped_column(JSONType, nullable=True)      # {days_to_event: cum_fraction}
    shift_pace_tolerance: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("0.080"))
    stage_map: Mapped[dict] = mapped_column(JSONType)                          # group -> [raw stage substrings]
    payment_plan_map: Mapped[dict] = mapped_column(JSONType)                   # {"pif":[...], "plan":[...]}
    # ── Sales Desk / pricing v2 (SPEC-becollective-salesdesk §4–5). price_map supersedes the
    #    two-price ticket_pif/ticket_plan model above (kept until the compute rewrite lands, so
    #    the live Launch tab keeps working); default_tz localizes the prose Call Time; and
    #    history_since marks the first sync with the append-only SalesCall event log live. ──
    price_map: Mapped[dict | None] = mapped_column(JSONType, nullable=True)    # {type:{acv,upfront,monthly,months,provisional}}
    default_tz: Mapped[str] = mapped_column(String(48), default="America/Denver")
    history_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # first sync with the event log live
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LaunchWeekly(Base):
    """Rolling weekly momentum store — the one launch metric that needs history (opt-ins,
    calls, closes, cumulative enrolled). The sync upserts the current ISO-week row."""
    __tablename__ = "launch_weekly"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    launch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("launch.id", ondelete="CASCADE"), index=True)
    week_start: Mapped[date] = mapped_column(Date)             # Monday of the ISO week
    optins: Mapped[int] = mapped_column(Integer, default=0)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    calls_source: Mapped[str] = mapped_column(String(16), default="proxy")   # appointments | proxy
    closes: Mapped[int] = mapped_column(Integer, default=0)
    enrolled_cum: Mapped[int] = mapped_column(Integer, default=0)  # cumulative enrolled seats at week end
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("launch_id", "week_start", name="uq_launch_week"),)


# ── Sales Desk (SPEC-becollective-salesdesk §4) — the rep-level call-throughput view.
#    GHL holds only CURRENT state (Call Outcome is the latest value); the Desk needs HISTORY,
#    so it maintains an append-only event log built by diffing successive syncs. Every rate on
#    the tab is computed from this log, never from live GHL fields. ────────────────────────────
class SalesCall(Base):
    """One row per distinct booking attempt. Append-only in spirit: a rebook creates a NEW row
    rather than mutating the old one, so a no-show survives a later show (spec §3)."""
    __tablename__ = "sales_call"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    launch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("launch.id", ondelete="CASCADE"), index=True)
    opportunity_id: Mapped[str] = mapped_column(String(64), index=True)              # GHL opp id
    contact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    booking_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    rep_email: Mapped[str | None] = mapped_column(String(160), index=True, nullable=True)
    call_time_raw: Mapped[str | None] = mapped_column(String(120), nullable=True)
    call_time_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # parsed; null if unparseable
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)           # Showed | No Show | Cancelled | Rescheduled
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)      # when Acumyn first observed it
    payment_type: Mapped[str | None] = mapped_column(String(24), nullable=True)      # PIF | Financed | Monthly | Custom (§6.1) — feeds the payment mix
    # ── recording. meeting_url arrives from GHL's "Appointment Link" the moment the call is
    # booked; everything below is written by the bot scheduler and the Recall webhook. All
    # nullable — a call with no recording is normal, not an error.
    meeting_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    recall_bot_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    recording_status: Mapped[str | None] = mapped_column(String(32), nullable=True)  # scheduled | waiting | recording | done | failed
    recording_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    recording_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # How many times the transcript fetch has been tried. Without it, a row whose transcript
    # never arrives held a slot in every batch forever and starved everything behind it.
    transcript_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)                  # false once superseded by a rebook
    # A Recall bot belongs to exactly ONE call. The column was merely indexed, so nothing
    # stopped two rows sharing a bot id — and apply_bot_status resolves the webhook's bot with
    # .first(), which would then serve one client's recording on another's Watch link. Partial
    # so the many NULLs are unconstrained (see migration 0044).
    __table_args__ = (Index("uq_sales_call_recall_bot", "recall_bot_id", unique=True,
                            sqlite_where=text("recall_bot_id IS NOT NULL"),
                            postgresql_where=text("recall_bot_id IS NOT NULL")),
                      UniqueConstraint("tenant_id", "opportunity_id", "booking_id",
                                       name="uq_sales_call_booking"),)


class CallTranscript(Base):
    """The words of one recorded call, stored so the corpus can be queried.

    Deliberately one row per CALL, not per segment: ~250 calls a month means ~3k rows a year
    rather than a million, and every question worth asking ("which calls mentioned price")
    is a search over `text` followed by a jump into `segments` for the moment.

    Connor's calls (2026-08-19): store rather than fetch-on-demand, because search and
    trend analysis are the whole point; keep for ONE YEAR, hence purge_after; visible to
    anyone with Sales Desk access, so there is no per-rep scoping here - authorization is
    the tab, exactly like the rest of the Desk. Never exposed on the public share pages.
    """
    __tablename__ = "call_transcript"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    sales_call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_call.id", ondelete="CASCADE"), index=True, unique=True)
    recall_bot_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    # [{speaker, start, end, text}] — start/end are seconds from the top of the recording, so
    # a click in the UI can seek the video straight to that moment.
    segments: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)          # flattened, for search
    speakers: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # {name: seconds} -> talk ratio
    # [{start, title, len}] — topic segmentation, generated once from `segments` after the
    # transcript lands. Nullable on purpose and never backfilled on read: null means "not
    # generated yet", [] means "generated and this call had no discernible structure", and the
    # UI hides the rail for both. Lives on this row so it purges with the words it describes.
    chapters: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    chapters_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Chaptering attempts. The failure path leaves chapters_at NULL on purpose, so without a
    # counter a call the model keeps failing on was re-sent to Anthropic every 15 minutes,
    # forever — the only unbounded spend loop in the module, and it predates multi-tenancy.
    chapter_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # A verbatim record of a client conversation is not something to keep by accident. The
    # purge job deletes on this date; it is set at write time, never inferred later.
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)


class SalesCallChange(Base):
    """Audit of every field change the sync observed. Diagnostics + the Data Health strip;
    never the basis for headline counts (spec §4)."""
    __tablename__ = "sales_call_change"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    sales_call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales_call.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(32))                                   # outcome | rep_email | booking_id | call_time
    old_value: Mapped[str | None] = mapped_column(String(160), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(160), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SalesRep(Base):
    """Rep roster: email is the key, display name is presentation (spec §4)."""
    __tablename__ = "sales_rep"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(160))
    display_name: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_sales_rep_email"),)


# ── AI Employees (SPEC-ai-employees-tab §3) ──────────────────────────────────
# An AI employee runs skills → drafts artifacts → a human approves before anything
# ships. AISkill is PRODUCT data (seeded, versioned); everything else is tenant data.
# PKs are GUID (repo convention, not the spec's int); JSONType (not JSONB). Governance:
# no auto-commit — runs land awaiting_approval, artifacts land draft.

class AISkill(Base):
    """Product-seeded skill definition (prompt template + output contract). Enabled per
    employee with per-tenant overrides; the seed row is never mutated by an override."""
    __tablename__ = "ai_skill"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(40), unique=True)          # audit, trend_brief, ...
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)
    default_prompt: Mapped[str] = mapped_column(Text)                  # template, {placeholders}
    default_schedule: Mapped[str | None] = mapped_column(String(60), nullable=True)  # cron or null (manual)
    artifact_kinds: Mapped[list] = mapped_column(JSONType, default=list)    # ["audit"] etc.
    output_contract: Mapped[dict] = mapped_column(JSONType, default=dict)   # JSON schema the run must satisfy
    version: Mapped[int] = mapped_column(Integer, default=1)


class AIEmployee(Base):
    __tablename__ = "ai_employee"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    role_title: Mapped[str] = mapped_column(String(80))
    avatar_color: Mapped[str] = mapped_column(String(7), default="#227175")
    status: Mapped[str] = mapped_column(String(12), default="active")   # active | paused
    writeback_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict] = mapped_column(JSONType, default=dict)        # brand_doc_refs, timezone, quiet_hours, budget knobs
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AIEmployeeSkill(Base):
    __tablename__ = "ai_employee_skill"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("ai_employee.id", ondelete="CASCADE"), index=True)
    skill_key: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    prompt_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    schedule_override: Mapped[str | None] = mapped_column(String(60), nullable=True)   # cron; null = skill default
    config: Mapped[dict] = mapped_column(JSONType, default=dict)        # skill knobs (Section 5) incl. condition builder
    seed_version: Mapped[int] = mapped_column(Integer, default=1)       # seed version the override was written against
    __table_args__ = (UniqueConstraint("employee_id", "skill_key", name="uq_ai_emp_skill"),)


class AIRun(Base):
    __tablename__ = "ai_run"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("ai_employee.id", ondelete="CASCADE"), index=True)
    skill_key: Mapped[str] = mapped_column(String(40))
    trigger: Mapped[str] = mapped_column(String(12))                   # manual | scheduled | condition
    status: Mapped[str] = mapped_column(String(20), default="queued")
        # queued | running | awaiting_approval | approved | shipped | failed | dismissed | skipped_budget
    trigger_context: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # {source,label,title,facts} (the EVENT)
    context: Mapped[dict | None] = mapped_column(JSONType, nullable=True)          # pasted source material / pacing facts
    reads: Mapped[list] = mapped_column(JSONType, default=list)        # the diagnosis lines
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)   # one-line, for the run list
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (Index("ix_ai_run_te", "tenant_id", "employee_id"),
                      Index("ix_ai_run_ts", "tenant_id", "status"))


class AIArtifact(Base):
    __tablename__ = "ai_artifact"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("ai_run.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))                     # audit|trend|strategy|design|script|measure
    lane: Mapped[str] = mapped_column(String(20))                     # Intel|Strategy|Creative|Tracking
    title: Mapped[str] = mapped_column(String(160))
    dest_label: Mapped[str | None] = mapped_column(String(60), nullable=True)   # "Claude in Chrome", "GoHighLevel", ...
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)      # shape = mockup preview object
    state: Mapped[str] = mapped_column(String(12), default="draft")    # draft | approved | shipped | dismissed
    approved_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_ai_artifact_tr", "tenant_id", "run_id"),)


class AIRosterAccount(Base):
    __tablename__ = "ai_roster_account"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("ai_employee.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(20), default="instagram")
    handle: Mapped[str] = mapped_column(String(80))
    why: Mapped[str | None] = mapped_column(Text, nullable=True)       # human note on why they're watched
    status: Mapped[str] = mapped_column(String(12), default="watch")   # watch | active | archived
    added_by: Mapped[str] = mapped_column(String(12), default="human") # human | ai (watchlist extension)
    score_overlap: Mapped[int] = mapped_column(Integer, default=0)     # 0-5 audience overlap
    score_offer: Mapped[int] = mapped_column(Integer, default=0)       # 0-5 offer similarity
    score_perf: Mapped[int] = mapped_column(Integer, default=0)        # 0-5 recent performance
    in_launch: Mapped[bool] = mapped_column(Boolean, default=False)
    last_audited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (Index("ix_ai_roster_te", "tenant_id", "employee_id"),)   # priority is computed, not stored


class AIIntelEntry(Base):
    """The compounding pattern library — findings a run files, tagged, retained per settings."""
    __tablename__ = "ai_intel_entry"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("ai_employee.id", ondelete="CASCADE"), index=True)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("ai_run.id"), nullable=True)
    finding: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AIMediaAsset(Base):
    """Summer's media library — b-roll, stock, past-event photos she pulls from to build content.
    Blob lives in object storage (R2, shared with the Binder via binder_storage); the row is the
    searchable catalog. `description`/`tags` are what a text model references (Stage 2 auto-captions
    images via a vision pass); the cascade's carousel/reel steps select assets by them (Stage 3)."""
    __tablename__ = "ai_media_asset"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("ai_employee.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="stock")   # broll | stock | event | logo | other
    title: Mapped[str] = mapped_column(String(160), default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)   # caption (manual now, auto later)
    tags: Mapped[list] = mapped_column(JSONType, default=list)
    storage_ref: Mapped[str] = mapped_column(String(400))                 # opaque R2/fs key
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(80), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_ai_media_te", "tenant_id", "employee_id"),)


# ── ULRG L10 Scorecard + Team Rooms (SPEC-ulrg-scorecard Part 2) ──────────────
# Replaces the EOS L10 Google Sheet. Values stored ASCENDING (oldest first), reversed only at
# render. Three row types (flow/rate/snapshot); snapshot never carries a cumulative block. Nothing
# is hardcoded to Spring — groups/metrics are tenant data (seeded per tenant); a new tenant gets none.

class ScorecardGroup(Base):
    __tablename__ = "scorecard_group"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("business.id"), index=True)
    key: Mapped[str] = mapped_column(String(40))                  # davis | slc | utco | overall
    name: Mapped[str] = mapped_column(String(120))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String(120), nullable=True)   # display when no user row
    is_team_room: Mapped[bool] = mapped_column(Boolean, default=True)            # 'overall' is False
    read: Mapped[str | None] = mapped_column(Text, nullable=True)                # authored per group (Part 5.1)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # Sisu office group_id this team maps to (43958=Davis, 43957=Salt Lake, 45345=Utah County for
    # Spring) — tenant data set by the seed, drives per-team resolver attribution. NULL for 'overall'.
    sisu_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_photo_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)   # headshot in object storage
    __table_args__ = (UniqueConstraint("tenant_id", "business_id", "key", name="uq_scorecard_group"),)


class ScorecardMetric(Base):
    __tablename__ = "scorecard_metric"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("scorecard_group.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    note: Mapped[str | None] = mapped_column(String(160), nullable=True)
    goal: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    direction: Mapped[str] = mapped_column(String(4), default="gte")             # gte | lte
    type: Mapped[str] = mapped_column(String(10))                                # flow | rate | snapshot
    stage: Mapped[int | None] = mapped_column(Integer, nullable=True)            # funnel position, null off-funnel
    lever: Mapped[str | None] = mapped_column(String(12), nullable=True)         # volume | behavior
    source: Mapped[str] = mapped_column(String(12), default="manual")            # sisu | fub | ghl | manual
    resolver_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    owner_initials: Mapped[str | None] = mapped_column(String(4), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ScorecardValue(Base):
    __tablename__ = "scorecard_value"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    metric_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("scorecard_metric.id", ondelete="CASCADE"), index=True)
    week_start: Mapped[date] = mapped_column(Date)               # Monday of the ISO week
    value: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)   # null ≠ zero (uncollected)
    source: Mapped[str] = mapped_column(String(10), default="manual")             # resolver | manual
    entered_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("metric_id", "week_start", name="uq_scorecard_value"),)


class ScorecardGoal(Base):
    """Per-period goal for a measurable (Phase C). Each measurement period keeps its own goals so
    past periods never shift when a new sprint's goals are set. Absent row → the metric's default
    goal (ScorecardMetric.goal). Unique per (metric, period)."""
    __tablename__ = "scorecard_goal"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    metric_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("scorecard_metric.id", ondelete="CASCADE"), index=True)
    period_key: Mapped[str] = mapped_column(String(40))
    goal: Mapped[Decimal] = mapped_column(Numeric(12, 4))                      # the WEEKLY goal
    # the whole-period total (e.g. 130 homes/quarter); the cumulative block tracks toward it. NULL →
    # cumulative falls back to weekly_goal × weeks. Only meaningful for flow metrics.
    cumulative_goal: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    __table_args__ = (UniqueConstraint("metric_id", "period_key", name="uq_scorecard_goal"),)


class TeamCommitment(Base):
    __tablename__ = "team_commitment"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("scorecard_group.id", ondelete="CASCADE"), index=True)
    period_month: Mapped[date] = mapped_column(Date)             # first of month
    kind: Mapped[str] = mapped_column(String(10))               # pace | rock | worklist
    title: Mapped[str] = mapped_column(String(200))
    target: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)   # null for worklists
    unit: Mapped[str | None] = mapped_column(String(24), nullable=True)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(12), default="manual")
    resolver_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    config: Mapped[dict] = mapped_column(JSONType, default=dict)   # resolver args, e.g. {"sessions":[...]}
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    committed_ref_url: Mapped[str | None] = mapped_column(String(400), nullable=True)   # the ClickUp L10 task
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class TeamCommitmentProgress(Base):
    """Hand-tracked rock progress points (Part 2.5)."""
    __tablename__ = "team_commitment_progress"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    commitment_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("team_commitment.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[date] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    entered_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("commitment_id", "as_of", name="uq_commitment_progress"),)


class ShareLink(Base):
    """Read-only share token so the Scorecard / a Team Room can be embedded in ClickUp (Part 2.6)."""
    __tablename__ = "share_link"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String(24))               # ulrg_scorecard | ulrg_team | sd_rep
    scope_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)  # team key | rep email (sd_rep)
    token: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StandardAccount(Base):
    """The normalized chart every entity's books are rendered through
    (SPEC-chart-of-accounts, SPEC-coa-mapping-provenance 2.1).

    Tenant-scoped and editable, because the chart is a living policy document rather than a
    constant. Consolidation merges on `bucket`, never on `name` — merging by name is the
    failure mode the earlier COA review flagged, and this column is what replaces it.
    """
    __tablename__ = "standard_account"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(10))                 # e.g. "8010"; unique per tenant
    name: Mapped[str] = mapped_column(String(120))
    # One series, one meaning: 1000 asset, 2000 liability, 3000 equity, 4000 revenue
    # (4900 contra), 5000 cost of sale, 6000-8999 opex, 9000 below the operating line.
    bucket: Mapped[str] = mapped_column(String(32))               # see coa.BUCKETS
    statement: Mapped[str] = mapped_column(String(2))             # pl | bs
    section: Mapped[str] = mapped_column(String(16))
    # revenue | cogs | opex | other_income | other_expense | asset | liability | equity
    normal_balance: Mapped[str] = mapped_column(String(6))        # debit | credit
    # One level of nesting only — depth is enforced in the seeder and the admin UI, not by a
    # DB check, because SQLite (used by the test suite) will not enforce a recursive one.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("standard_account.id", ondelete="SET NULL"), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # True for Due To / Due From and the 4700 intercompany revenue range. Drives BOTH the
    # consolidation elimination and the provenance-flag exemption, so the two share one
    # mechanism rather than drifting apart (SPEC 6.4).
    is_intercompany_account: Mapped[bool] = mapped_column(Boolean, default=False)
    # What links the chart to the Deferred Revenue schedule. An account marked `ratable` or
    # `event_date` carrying a balance on an entity with no matching schedule entry is a
    # close-blocking exception — that check belongs in the Balance Sheet Tie-Out.
    recognition: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # point_in_time | ratable | event_date | not_applicable
    archetypes: Mapped[list | None] = mapped_column(JSONType, nullable=True)  # which archetypes open it
    definition: Mapped[str | None] = mapped_column(Text, nullable=True)   # tooltip + policy memo
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_standard_account_code"),)


class CoaMapRule(Base):
    """A FullyQualifiedName-prefix rule that maps a whole QBO subtree to one standard account
    (SPEC-coa-mapping-provenance 2.3, extended after Phase 0 discovery).

    Phase 0 found that people-as-accounts are not scattered through the charts, they are
    concentrated in a handful of subtrees: ULRG's "61300 Contract Labor:Virtual Assistants:*",
    Spring B's "Contract Labor:*", beCollective's "Commissions:*". Without a rule, every new VA
    or new closer hired next month arrives as a fresh QBO account and trips the 5.3 unmapped
    guard — forever, on a statement that then refuses to render.

    The rule does NOT replace `coa_map`. On first sight of an account the sync consults the
    rules and writes a normal `coa_map` row, still keyed on `qbo_account_id`. Identity stays on
    the ID; the name is only ever the thing a human wrote a rule *about*.
    """
    __tablename__ = "coa_map_rule"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    # NULL means every entity in the tenant. "Contract Labor:" is a real subtree on Spring B
    # AND The Forum, so a portfolio-wide rule is the common case, not the exotic one.
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("business.id", ondelete="CASCADE"), nullable=True)
    match_type: Mapped[str] = mapped_column(String(8), default="prefix")   # prefix
    # Matched against FullyQualifiedName, case-insensitively. Longest matching prefix wins, so
    # "61300 Contract Labor:Virtual Assistants:" beats "61300 Contract Labor:" with no priority
    # column to get out of sync — two different prefixes of the same length cannot both match.
    pattern: Mapped[str] = mapped_column(String(300))
    standard_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_account.id", ondelete="CASCADE"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)      # why this rule exists
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CoaMap(Base):
    """One row per QBO account per entity: the per-entity map into the standard chart
    (SPEC-coa-mapping-provenance 2.3). The heart of the mapping layer.

    Keyed on `qbo_account_id` and never on name. Names change, duplicate across entities, and
    QBO allows two accounts with the same name at different levels — ULRG carries account
    number 69000 twice, as "69000 Other Expense" and "69000 Insurance", both embedded in the
    NAME rather than in AcctNum. A name-keyed map silently merges those two.

    `standard_account_id IS NULL` means unmapped. A new QBO account is never silently dropped;
    it lands here unmapped and surfaces for a decision (5.1), and from Phase 3 an unmapped
    account WITH activity blocks the render rather than quietly omitting itself.
    """
    __tablename__ = "coa_map"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id", ondelete="CASCADE"), index=True)
    qbo_account_id: Mapped[str] = mapped_column(String(50))
    qbo_account_name: Mapped[str] = mapped_column(String(200))      # leaf name; display only
    # FullyQualifiedName, parent-first and colon-joined. Display only as far as identity goes,
    # but it IS what coa_map_rule matches on — ULRG nests five deep, so a leaf name like
    # "S. Wodrich" carries no meaning without the path above it.
    qbo_account_fqn: Mapped[str | None] = mapped_column(String(400), nullable=True)
    qbo_account_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    qbo_active: Mapped[bool] = mapped_column(Boolean, default=True)  # QBO's own Active flag
    standard_account_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("standard_account.id", ondelete="SET NULL"), nullable=True)
    # How this row got its standard account. A rule pass re-maps `rule` rows when the rule
    # changes and never touches a `manual` one — a human decision outranks a pattern.
    mapped_via: Mapped[str | None] = mapped_column(String(8), nullable=True)   # manual | rule
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("coa_map_rule.id", ondelete="SET NULL"), nullable=True)
    mapped_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    mapped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_ignored: Mapped[bool] = mapped_column(Boolean, default=False)
    ignore_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)  # required to ignore
    __table_args__ = (
        UniqueConstraint("tenant_id", "business_id", "qbo_account_id", name="uq_coa_map_account"),
    )


class AccountPeriodBalance(Base):
    """The pulled trial balance: one row per QBO account, per entity, per period
    (SPEC-coa-mapping-provenance 2.4). The ground truth the mapped statement must tie to.

    `amount` is stored **debit-positive, always** — assets and expenses positive, liabilities,
    equity and revenue negative. QBO hands back mixed conventions depending on the report, so
    the convention is applied once here at ingest by `coa_balances.normalize_sign` and never
    re-decided downstream. Render flips the sign for display on credit-normal accounts.

    Keyed on `qbo_account_id`, like everything else in this module. The account NAME is not
    stored at all: `coa_map` already holds it, and a second copy is a second thing to go stale.
    """
    __tablename__ = "account_period_balance"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id", ondelete="CASCADE"), index=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    qbo_account_id: Mapped[str] = mapped_column(String(50))
    # PERIOD ACTIVITY: the movement within [period_start, period_end]. A trial balance is an
    # as-of report — P&L accounts on it carry fiscal-year-to-date, not the range you asked for
    # — so this is the DIFFERENCE of two as-of pulls, which is the only way to get true period
    # activity out of it. Both columns are signed and debit-positive, and both sum to zero
    # across accounts (a difference of two zero-sums is a zero-sum).
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    # AS-OF the period end: the cumulative balance. What the balance sheet needs, and what a
    # trial balance natively reports.
    balance_end: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    source: Mapped[str] = mapped_column(String(20), default="qbo_tb")   # qbo_tb
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("tenant_id", "business_id", "period_start", "period_end",
                         "qbo_account_id", name="uq_apb_account_period"),
        Index("ix_apb_period", "tenant_id", "business_id", "period_start", "period_end"),
    )


class CoaSettings(Base):
    """Per-tenant knobs for the mapping and provenance layers (SPEC 2.6). One row per tenant.

    These are settings rather than constants because each is a judgement that can reasonably
    differ: how much intercompany makes a line worth flagging, how many cents of rounding to
    forgive, and whether an unmapped account is allowed to render at all. A magic number buried
    in a service is a judgement nobody can find, let alone change.
    """
    __tablename__ = "coa_settings"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), unique=True, index=True)
    # Below this share of a line, intercompany is not worth flagging (Phase 5).
    ic_flag_threshold_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("5.00"))
    # Cents. Rounding across a few hundred accounts is real; a dollar of drift is not.
    tie_out_tolerance: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.01"))
    # Default TRUE, and it should stay true. A statement that quietly omits accounts is worse
    # than an error message, because it looks right.
    block_render_on_unmapped: Mapped[bool] = mapped_column(Boolean, default=True)


class AllocationRule(Base):
    """Policy: which QBO subtree on which entity is a shared cost, and who funded it
    (SPEC-coa-mapping-provenance 2.5, extended for the QBO-sourced ingest).

    The spec's `allocation_contribution` carries pool, basis, driver and approver on every row.
    Those are policy, not observation, and they do not vary line by line — so they live here,
    declared once by a person, and the contribution rows inherit them. `approved_by` is
    required for the reason the spec gives: an allocation is a policy decision and never the
    bookkeeper's.

    Matched the same way `coa_map_rule` matches, on a FullyQualifiedName prefix, because the
    shared costs already sit in a named subtree — "Shared Service Expenses:" on The Forum and
    beCollective. Inferring the funder from an account name ("Due To SB Coaching") would work
    today and is exactly the name-keyed reasoning this module refuses everywhere else.
    """
    __tablename__ = "allocation_rule"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    # The entity whose books CARRY the cost. NULL means every entity in the tenant.
    target_business_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("business.id", ondelete="CASCADE"), nullable=True)
    # The entity that FUNDED it. Required: an allocation with no counterparty cannot net to
    # zero across the portfolio, which is the invariant in 6.6.
    source_business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("business.id", ondelete="CASCADE"))
    pattern: Mapped[str] = mapped_column(String(300))          # FQN prefix, longest wins
    pool_name: Mapped[str] = mapped_column(String(80))         # e.g. "Shared Services"
    basis: Mapped[str | None] = mapped_column(String(200), nullable=True)      # "Headcount, 40/60"
    driver_source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    je_ref: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AllocationContribution(Base):
    """One shared cost, for one period, on one standard account, between two entities
    (SPEC-coa-mapping-provenance 2.5).

    **Observed, not applied.** The spec assumes contributions are added on top of the books.
    On this portfolio they are already IN them: The Forum's July shared-service charge of
    32,450.07 equals, to the cent, the increase in its `Due To SB Coaching`. The expense sits
    on the target and the receivable sits on the source, and the source never expenses it —
    correctly, because the cost belongs to the target. Adding these amounts to a P&L would
    double-count every one of them.

    So `booking` records which world a row came from, and the composition reads it rather than
    assuming. `observed` rows adjust "as booked" DOWNWARD (the entity's own activity, before
    shared costs); `applied` rows, if a workbook ever feeds them, add to "as allocated" the way
    the spec describes. One column keeps both honest instead of one inverted formula.
    """
    __tablename__ = "allocation_contribution"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    source_business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id", ondelete="CASCADE"))
    target_business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id", ondelete="CASCADE"))
    standard_account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("standard_account.id", ondelete="CASCADE"))
    # Always positive. Direction is carried by source and target, never by the sign, so a
    # reader never has to work out which way a negative number points.
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    booking: Mapped[str] = mapped_column(String(10), default="observed")   # observed | applied
    pool_name: Mapped[str] = mapped_column(String(80))
    basis: Mapped[str | None] = mapped_column(String(200), nullable=True)
    driver_source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    je_ref: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("allocation_rule.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        # An entity cannot allocate to itself: that is a rename, not an allocation, and it
        # would net to zero while looking like movement.
        CheckConstraint("source_business_id <> target_business_id", name="ck_alloc_not_self"),
        Index("ix_alloc_period", "tenant_id", "period_start", "period_end"),
    )


# ── Meta Ads module (SPEC-ads-module.md Part 6) ────────────────────────────────────────
#
# The point of this module is the JOIN, not the click metrics. Meta owns impression, click and
# its own lead count; Acumyn already owns the registration, the booked call, the outcome, the
# signature and the cash. Nobody owned the seam between them, and campaigns rank differently by
# clicks than by customers - that difference is the whole product.
#
# Platform-neutral on purpose: `platform` and `external_id` mean a Google Ads account lands in
# these tables without a migration.


class AdAccount(Base):
    """One connected advertising account.

    Sits UNDER an Integration, which holds the Fernet-encrypted token, the same way a QuickBooks
    realm does - one System User token routinely carries several ad accounts.

    `business_id` is the entity that BOOKS this spend, and its archetype selects the funnel
    definition. It is NOT a filter: one Meta account routinely runs campaigns for several
    programmes at once, which is exactly what campaign grouping exists to untangle.
    """
    __tablename__ = "ad_account"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    integration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("business.id", ondelete="SET NULL"), nullable=True)
    platform: Mapped[str] = mapped_column(String(16), default="meta")   # meta | google | tiktok
    external_id: Mapped[str] = mapped_column(String(64))                # act_587749862890426
    name: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    # The ACCOUNT's reporting timezone. Meta's day boundaries follow it - not UTC, and not the
    # tenant's. Every date boundary in this module uses it. scorecard_tick learned the same
    # lesson: a naive UTC date flips the week a day early.
    timezone_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")   # active | paused | archived
    # Tenant overrides. NULL means "use the code default" in every case, so a tenant that
    # configures nothing still gets correct behaviour.
    group_rules: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    lead_actions: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    thresholds: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    funnel_override: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # The URL-parameter template every ad is expected to carry. The readiness check compares
    # ad.url_tags against it. Ad-level revenue is impossible without utm_content={{ad.id}}.
    utm_template: Mapped[str | None] = mapped_column(String(400), nullable=True)
    backfill_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("tenant_id", "platform", "external_id", name="uq_ad_account_ext"),
    )


class AdCampaign(Base):
    """Status and budget are not on insight rows, so they come from /campaigns and live here."""
    __tablename__ = "ad_campaign"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    ad_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ad_account.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(400))   # long by design; truncation is a display concern
    objective: Mapped[str | None] = mapped_column(String(48), nullable=True)
    status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    effective_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    daily_budget: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    lifetime_budget: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # From INSIGHT rows, not the dimension: a campaign can be ACTIVE and have delivered nothing
    # for a month. "Spending" is a delivery question, not a status question.
    first_seen_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_seen_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    __table_args__ = (
        UniqueConstraint("tenant_id", "ad_account_id", "external_id", name="uq_ad_campaign_ext"),
    )


class Ad(Base):
    """One ad, creative fields inline. No separate creative table: this module never needs a Meta
    AdCreative independent of the ad running it. Keyed on the Meta AD id."""
    __tablename__ = "ad"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    ad_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ad_account.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ad_campaign.id", ondelete="SET NULL"), nullable=True)
    external_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(400))
    adset_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    adset_name: Mapped[str | None] = mapped_column(String(400), nullable=True)
    status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    effective_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    creative_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    headline: Mapped[str | None] = mapped_column(String(300), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # SIGNED and SHORT LIVED. Refreshed every sync; never treated as a permalink.
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)   # stable - the cache key
    image_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)   # storage_ref once cached
    permalink: Mapped[str | None] = mapped_column(Text, nullable=True)
    url_tags: Mapped[str | None] = mapped_column(Text, nullable=True)   # the readiness check reads this
    creative_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_seen_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    __table_args__ = (
        UniqueConstraint("tenant_id", "ad_account_id", "external_id", name="uq_ad_ext"),
        Index("ix_ad_campaign", "tenant_id", "campaign_id"),
    )


class AdInsightDaily(Base):
    """One day of delivery for one object at one level.

    BOTH levels are stored. Campaign totals are not always the sum of their ads - spend on
    deleted ads survives at the campaign level, some account costs never land on an ad - so
    headlines read level='campaign' and the creative wall reads level='ad'. The gap is REPORTED,
    never quietly reconciled away.

    NO RATIOS ARE STORED. Meta returns ctr, cpm and cpc and this table deliberately keeps none of
    them, so there is no rate here for anyone to accidentally sum. A mean of daily CTRs is not the
    period CTR, and the surest way nobody averages one is for it not to exist. Every rate is
    derived at read time from summed numerators and denominators.
    """
    __tablename__ = "ad_insight_daily"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    ad_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ad_account.id", ondelete="CASCADE"), index=True)
    level: Mapped[str] = mapped_column(String(10))              # campaign | ad
    object_external_id: Mapped[str] = mapped_column(String(64))
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ad_campaign.id", ondelete="CASCADE"), nullable=True)
    ad_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ad.id", ondelete="CASCADE"), nullable=True)     # level='ad' only
    occurred_on: Mapped[date] = mapped_column(Date)                 # in the AD ACCOUNT's timezone
    spend: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    reach: Mapped[int | None] = mapped_column(Integer, nullable=True)      # 13-month retention
    clicks: Mapped[int] = mapped_column(Integer, default=0)                # ALL clicks
    inline_link_clicks: Mapped[int] = mapped_column(Integer, default=0)    # the honest CTR numerator
    frequency: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)  # 6-month retention
    # Kept WHOLE, so a new action_type never needs a re-sync and `leads` can be re-resolved from
    # stored rows when the configured lead types change.
    actions: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    action_values: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    leads: Mapped[int] = mapped_column(Integer, default=0)     # resolved from actions at write
    purchase_roas: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    attribution: Mapped[str | None] = mapped_column(String(40), nullable=True)  # setting this row used
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("tenant_id", "ad_account_id", "level", "object_external_id",
                         "occurred_on", name="uq_ad_insight_day"),
        Index("ix_ad_insight_range", "tenant_id", "ad_account_id", "occurred_on"),
        Index("ix_ad_insight_obj", "tenant_id", "level", "campaign_id"),
    )


class AdAttribution(Base):
    """First touch, FROZEN. One row per attributed identity. The spine of the whole module.

    WRITE ONCE. GHL records are written by _ghl_snapshot, which REPLACES the row set every sync,
    so a contact's UTM is CURRENT STATE rather than history. Re-deriving attribution each sync
    would move closed revenue between campaigns weeks after the fact, and nobody would be able to
    say why last month's report changed. Only last_seen_on is ever updated after insert.

    The Sales Desk hit precisely this and answered it the same way: its own append-only log, with
    every rate computed from that log rather than from live GHL fields.
    """
    __tablename__ = "ad_attribution"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    identity_kind: Mapped[str] = mapped_column(String(16))      # ghl_contact | sisu_client | email
    identity_key: Mapped[str] = mapped_column(String(160))      # GHL contact id, or a normalized email
    email_norm: Mapped[str | None] = mapped_column(String(255), nullable=True)
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("business.id", ondelete="SET NULL"), nullable=True)
    ad_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ad_account.id", ondelete="SET NULL"), nullable=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ad_campaign.id", ondelete="SET NULL"), nullable=True)
    # Set ONLY when match_method == 'ad'. The read service must never infer an ad from a campaign
    # match, and there is a test on exactly that.
    ad_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ad.id", ondelete="SET NULL"), nullable=True)
    utm_source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(120), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(300), nullable=True)
    utm_content: Mapped[str | None] = mapped_column(String(300), nullable=True)
    utm_term: Mapped[str | None] = mapped_column(String(300), nullable=True)
    landing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Meta | Google | TikTok | Email | Paid (other) | Organic / Existing | Comped
    channel: Mapped[str] = mapped_column(String(24))
    # The grain this row is ALLOWED to grant revenue at. Never present a channel-level match as
    # ad-level; the read service enforces this rather than trusting the caller.
    match_method: Mapped[str] = mapped_column(String(12))       # ad | campaign | channel | none
    confidence: Mapped[str] = mapped_column(String(8))          # exact | probable | channel
    first_seen_on: Mapped[date] = mapped_column(Date)           # THE COHORT DAY
    last_seen_on: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("tenant_id", "identity_kind", "identity_key", name="uq_ad_attr_identity"),
        Index("ix_ad_attr_cohort", "tenant_id", "first_seen_on"),
        Index("ix_ad_attr_campaign", "tenant_id", "campaign_id"),
    )


class AdConversion(Base):
    """One row per (identity, funnel stage) reached. Revenue lands on the closing stage.

    MATERIALIZED rather than derived, because time-to-close needs a stage DATE and the underlying
    rows do not all carry one. A stage whose source has no reliable date is written dated=False:
    COUNTED in the funnel, and excluded from every duration and from the curve fit. Counting it is
    honest; timing it is not.

    Written from rows that already exist - bc_shift_reg, SalesCall, the classify_stage groups,
    bc_onboarded. This table never invents a stage the source systems do not already assert, and
    it never re-derives stage semantics that migration 0032 already settled.
    """
    __tablename__ = "ad_conversion"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    attribution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ad_attribution.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id"), index=True)
    # registered | booked | applied | held | committed | closed
    stage_key: Mapped[str] = mapped_column(String(24))
    occurred_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    dated: Mapped[bool] = mapped_column(Boolean, default=True)   # False -> counted, never timed
    # THE FULL SIGNED CONTRACT, priced off the launch's own price sheet - not the GHL
    # opportunity's monetaryValue, which is a cash figure and was silently standing in for this.
    value_contracted: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # Cash MEASURED against this person: succeeded payment rows, joined by email.
    value_collected: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # Cash MODELLED as due at signing, from the same price sheet. Written on the committed rung
    # too, because "Cash received" is where that money actually is and the rung had no dollars.
    value_upfront: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # True when a rolling monthly membership was modelled at twelve months. A modelling choice,
    # not a signed number, so the UI has to be able to say so.
    value_annualized: Mapped[bool] = mapped_column(Boolean, default=False)
    # The four-type Payment Type when the Sales Desk logged one (PIF | Financed | Monthly |
    # Custom), else the legacy two-value snapshot field (pif | plan | custom).
    payment_type: Mapped[str | None] = mapped_column(String(12), nullable=True)
    # price_map | ticket | ghl_amount | unpriced. A modelled price and a signed one must never be
    # presented as the same fact, so the number carries how it was arrived at.
    value_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Provenance, so any number here opens to the row that produced it.
    source_kind: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("tenant_id", "attribution_id", "stage_key", name="uq_ad_conv_stage"),
        Index("ix_ad_conv_stage", "tenant_id", "stage_key", "occurred_on"),
    )


class AdCohortCurve(Base):
    """The MEASURED maturity curve: of a cohort's eventual closes, what share have landed by day N
    since first touch.

    Never assumed. Refitted from cohorts old enough to be complete, and until there are enough of
    those, no row exists, maturity reads unknown, and the projection is SUPPRESSED rather than
    modelled. A guessed curve is worse than an absent one because it looks like a number.

    DEFAULT_SHIFT_CURVE is the shape precedent, not the source of these values: a registration-
    pace curve and a close-lag curve are different clocks.
    """
    __tablename__ = "ad_cohort_curve"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id"), index=True)
    funnel_key: Mapped[str] = mapped_column(String(24))     # the archetype funnel this describes
    day: Mapped[int] = mapped_column(Integer)               # days since first touch
    share: Mapped[Decimal] = mapped_column(Numeric(6, 4))   # 0..1 cumulative share landed by `day`
    sample_cohorts: Mapped[int] = mapped_column(Integer)    # how many COMPLETE cohorts fitted this
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("tenant_id", "business_id", "funnel_key", "day", name="uq_ad_curve_day"),
    )
