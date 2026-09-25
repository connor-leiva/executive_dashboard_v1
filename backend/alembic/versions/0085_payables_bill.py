"""Payables Phase 2 — bills, approvals, the policy matrix, and the per-invoice timeline.

SPEC-payables §3. Four tables.

NUMBERING: the spec says 0081. It was written when 0079 was head; Recruiting took 0080-0083 and
Phase 1 took 0084. Phase 3 should be 0086, not the 0082 the spec names.

The highest-value line in this file is uq_payable_invoice — one invoice number per vendor per
tenant is the duplicate-payment control. It is enforced in the service as well, so the screen
can name the payable it collided with instead of surfacing a constraint violation.

`payable.payment_run_id` carries no foreign key yet. payment_run does not exist until Phase 3,
and a forward reference would fail this migration on the way up.

`approval_policy.max_amount` is nullable on purpose: the top band must be open-ended, or an
invoice larger than every ceiling matches no band and reaches approved with nobody required.

Revision ID: 0085_payables_bill
Revises: 0084_payables_vendor
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision = "0085_payables_bill"
down_revision = "0084_payables_vendor"
branch_labels = None
depends_on = None


def _tables() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tabs = _tables()

    # Guarded: 0001_init runs create_all() of the CURRENT models, so a database built from
    # scratch already carries these and an unguarded create_table would fail on it.
    if "payable" not in tabs:
        op.create_table(
            "payable",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("vendor_id", GUID(), sa.ForeignKey("vendor.id", ondelete="RESTRICT"),
                      nullable=False, index=True),
            sa.Column("business_id", GUID(),
                      sa.ForeignKey("business.id", ondelete="SET NULL"), nullable=True),
            sa.Column("legal_entity_id", GUID(),
                      sa.ForeignKey("legal_entity.id", ondelete="SET NULL"), nullable=True),
            sa.Column("invoice_number", sa.String(60), nullable=False),
            sa.Column("invoice_date", sa.Date(), nullable=True),
            sa.Column("due_date", sa.Date(), nullable=True),
            sa.Column("service_period_start", sa.Date(), nullable=True),
            sa.Column("service_period_end", sa.Date(), nullable=True),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("document_id", GUID(),
                      sa.ForeignKey("binder_document.id", ondelete="SET NULL"), nullable=True),
            sa.Column("extraction", JSONType, nullable=True),
            sa.Column("standard_account_id", GUID(),
                      sa.ForeignKey("standard_account.id", ondelete="SET NULL"), nullable=True),
            sa.Column("class_key", sa.String(60), nullable=True),
            sa.Column("location_key", sa.String(60), nullable=True),
            sa.Column("status", sa.String(24), nullable=False, server_default="received"),
            sa.Column("is_exception", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("exception_reason", sa.Text(), nullable=True),
            sa.Column("submitted_by", GUID(), sa.ForeignKey("user.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("payment_run_id", GUID(), nullable=True, index=True),
            sa.Column("qbo_bill_id", sa.String(32), nullable=True),
            sa.Column("qbo_sync_token", sa.String(16), nullable=True),
            sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", "vendor_id", "invoice_number",
                                name="uq_payable_invoice"),
        )
        op.create_index("ix_payable_status", "payable", ["tenant_id", "status"])
        op.create_index("ix_payable_due", "payable", ["tenant_id", "due_date"])

    if "payable_approval" not in tabs:
        op.create_table(
            "payable_approval",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("payable_id", GUID(), sa.ForeignKey("payable.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("approver_user_id", GUID(),
                      sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("decision", sa.String(16), nullable=True),
            sa.Column("threshold_band", sa.String(60), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "approval_policy" not in tabs:
        op.create_table(
            "approval_policy",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("label", sa.String(60), nullable=False),
            sa.Column("business_id", GUID(),
                      sa.ForeignKey("business.id", ondelete="CASCADE"), nullable=True),
            sa.Column("min_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("max_amount", sa.Numeric(14, 2), nullable=True),
            sa.Column("required_role", sa.String(24), nullable=True),
            sa.Column("required_user_ids", JSONType, nullable=True),
            sa.Column("requires_second_approver", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if "payable_event" not in tabs:
        op.create_table(
            "payable_event",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("payable_id", GUID(), sa.ForeignKey("payable.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("event", sa.String(24), nullable=False),
            sa.Column("actor_user_id", GUID(),
                      sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
            sa.Column("payload", JSONType, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )


def downgrade() -> None:
    tabs = _tables()
    # Children first: every one of these points at payable.
    for name in ("payable_event", "approval_policy", "payable_approval"):
        if name in tabs:
            op.drop_table(name)
    if "payable" in tabs:
        op.drop_index("ix_payable_due", table_name="payable")
        op.drop_index("ix_payable_status", table_name="payable")
        op.drop_table("payable")
