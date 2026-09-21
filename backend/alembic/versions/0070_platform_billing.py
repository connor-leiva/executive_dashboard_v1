"""Axcion charging workspaces: the Stripe mirror and the account's configuration.

OPERATOR-CONSOLE-SPEC §4.3. platform_subscription and platform_invoice mirror Axcion's own Stripe
account; they are corrected from Stripe and never edited by hand, and money is cents. Two tables
the spec does not list: platform_stripe_event, so a retried webhook changes nothing, and
platform_billing_config, the account's keys, entered in the operator console and stored encrypted
instead of the spec's STRIPE_PLATFORM_* environment variables. platform_subscription also carries
stripe_event_at, so an event arriving out of order cannot overwrite newer state.

Revision ID: 0070_platform_billing
Revises: 0069_platform_audit
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID

revision = "0070_platform_billing"
down_revision = "0069_platform_audit"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "platform_billing_config" not in tables:
        op.create_table(
            "platform_billing_config",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("secret_key_enc", sa.Text(), nullable=True),
            sa.Column("webhook_secret_enc", sa.Text(), nullable=True),
            sa.Column("account_id", sa.String(64), nullable=True),
            sa.Column("account_name", sa.String(255), nullable=True),
            sa.Column("livemode", sa.Boolean(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_by", sa.String(255), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "platform_subscription" not in tables:
        op.create_table(
            "platform_subscription",
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("stripe_customer_id", sa.String(64), nullable=False, unique=True),
            sa.Column("stripe_subscription_id", sa.String(64), nullable=True, unique=True),
            sa.Column("stripe_price_id", sa.String(64), nullable=True),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("amount_cents", sa.Integer(), nullable=True),
            sa.Column("currency", sa.String(3), nullable=False, server_default="usd"),
            sa.Column("interval", sa.String(16), nullable=True),
            sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
            sa.Column("trial_end", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancel_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("default_payment_method", sa.String(128), nullable=True),
            sa.Column("payment_method_exp", sa.String(16), nullable=True),
            sa.Column("collected_cents", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("billing_contact_email", sa.String(255), nullable=True),
            sa.Column("po_reference", sa.String(128), nullable=True),
            sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("stripe_event_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "platform_invoice" not in tables:
        op.create_table(
            "platform_invoice",
            sa.Column("stripe_invoice_id", sa.String(64), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
            sa.Column("number", sa.String(64), nullable=True),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("amount_due_cents", sa.Integer(), nullable=False),
            sa.Column("amount_paid_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("hosted_invoice_url", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_platform_invoice_tenant_id", "platform_invoice", ["tenant_id"])
        op.create_index("ix_platform_invoice_created", "platform_invoice", ["tenant_id", "created_at"])
    if "platform_stripe_event" not in tables:
        op.create_table(
            "platform_stripe_event",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("type", sa.String(64), nullable=False),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    tables = _tables()
    for name in ("platform_stripe_event", "platform_invoice", "platform_subscription", "platform_billing_config"):
        if name in tables:
            op.drop_table(name)
