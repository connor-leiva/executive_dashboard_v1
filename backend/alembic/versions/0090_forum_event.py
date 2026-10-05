"""Forum events — the quarterly VIP event tab. FORUM-EVENT-SPEC.md §4.

Three tables. `forum_event` is one row per event and holds the whole of its configuration in
named, typed columns (the Launch pattern) rather than in Integration.config, so the editor has
something to validate against and a second event cannot overwrite the first's settings.

`forum_event_guest` is the one that makes quarterly events possible at all. The Forum's existing
event support keeps registrations in `metric_record`, which `_metric_snapshot` clears by
(tenant, business, source, kind) before every insert — so syncing a second event DESTROYS the
first one's registrations. These rows are upserted by (event_id, contact_id) and never swept.

Two populations attend and they carry different tags: VIP guests at $2,500 (the sales funnel)
and existing members who have registered (no sale attached). `kind` separates them. The Forum
derives one by subtracting the other today, which is the bug this replaces.

NO unique index on (business_id, is_active): many events coexist by design, which is the whole
difference from `launch`.

NUMBERING: 0089_onboarding_reader is head, so this is 0090. `alembic heads` was one line before
and after.

Revision ID: 0090_forum_event
Revises: 0089_onboarding_reader
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision = "0090_forum_event"
down_revision = "0089_onboarding_reader"
branch_labels = None
depends_on = None

_TABLES = ("forum_event", "forum_event_guest", "forum_event_weekly")
# Created after the tables and guarded by name, so a partially-applied revision can be re-run.
_INDEXES = (
    ("ix_forum_event_tenant_id", "forum_event", ["tenant_id"]),
    ("ix_forum_event_business_id", "forum_event", ["business_id"]),
    ("ix_forum_event_scope", "forum_event", ["tenant_id", "business_id", "status", "starts_on"]),
    ("ix_forum_event_guest_tenant_id", "forum_event_guest", ["tenant_id"]),
    ("ix_forum_event_guest_event_id", "forum_event_guest", ["event_id"]),
    ("ix_forum_event_guest_contact_id", "forum_event_guest", ["contact_id"]),
    ("ix_forum_event_guest_opportunity_id", "forum_event_guest", ["opportunity_id"]),
    ("ix_forum_event_guest_rep_email", "forum_event_guest", ["rep_email"]),
    ("ix_forum_event_guest_kind", "forum_event_guest", ["event_id", "kind", "group"]),
    ("ix_forum_event_weekly_tenant_id", "forum_event_weekly", ["tenant_id"]),
    ("ix_forum_event_weekly_event_id", "forum_event_weekly", ["event_id"]),
)


def _have(bind):
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    have = _have(bind)

    if "forum_event" not in have:
        op.create_table(
            "forum_event",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("business_id", GUID(), sa.ForeignKey("business.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("slug", sa.String(64), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
            sa.Column("starts_on", sa.Date(), nullable=True),
            sa.Column("ends_on", sa.Date(), nullable=True),
            sa.Column("window_start", sa.Date(), nullable=True),
            sa.Column("window_end", sa.Date(), nullable=True),
            sa.Column("venue", sa.String(120), nullable=True),
            sa.Column("default_tz", sa.String(40), nullable=False, server_default="America/Denver"),
            # JSONType is ALREADY an instance (dbtypes.py) — pass it bare, never JSONType().
            sa.Column("pipeline_match", JSONType, nullable=True),
            sa.Column("stage_map", JSONType, nullable=True),
            sa.Column("guest_tags", JSONType, nullable=True),
            sa.Column("member_tags", JSONType, nullable=True),
            sa.Column("declined_tags", JSONType, nullable=True),
            sa.Column("comp_tag_match", sa.String(32), nullable=False, server_default="comp"),
            sa.Column("guest_goal", sa.Integer(), nullable=True),
            sa.Column("member_goal", sa.Integer(), nullable=True),
            # Nullable with no default and no seeded value: the tab is fully useful unpriced,
            # and a revenue figure with no price renders as a dash, never $0.
            sa.Column("vip_price", sa.Numeric(12, 2), nullable=True),
            sa.Column("price_map", JSONType, nullable=True),
            sa.Column("pace_curve", JSONType, nullable=True),
            sa.Column("pace_tolerance", sa.Numeric(5, 4), nullable=False, server_default="0.08"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", "business_id", "slug", name="uq_forum_event_slug"),
        )

    if "forum_event_guest" not in have:
        op.create_table(
            "forum_event_guest",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("event_id", GUID(), sa.ForeignKey("forum_event.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("contact_id", sa.String(64), nullable=False),
            sa.Column("kind", sa.String(8), nullable=False, server_default="guest"),
            sa.Column("opportunity_id", sa.String(64), nullable=True),
            sa.Column("name", sa.String(160), nullable=True),
            sa.Column("stage", sa.String(120), nullable=True),
            sa.Column("group", sa.String(16), nullable=False, server_default="uncategorized"),
            sa.Column("is_comped", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("channel", sa.String(32), nullable=True),
            sa.Column("invited_by", sa.String(160), nullable=True),
            sa.Column("rep_email", sa.String(160), nullable=True),
            sa.Column("payment_type", sa.String(24), nullable=True),
            sa.Column("registered_on", sa.Date(), nullable=True),
            sa.Column("converted_on", sa.Date(), nullable=True),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("event_id", "contact_id", name="uq_forum_event_guest"),
        )

    if "forum_event_weekly" not in have:
        op.create_table(
            "forum_event_weekly",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("event_id", GUID(), sa.ForeignKey("forum_event.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("week_start", sa.Date(), nullable=False),
            sa.Column("guests", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("members_registered", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("converted", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("guests_cum", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("event_id", "week_start", name="uq_forum_event_week"),
        )

    insp = sa.inspect(bind)
    for name, table, cols in _INDEXES:
        if table in _have(bind) and name not in {i["name"] for i in insp.get_indexes(table)}:
            op.create_index(name, table, cols)


def downgrade() -> None:
    have = _have(op.get_bind())
    for table in reversed(_TABLES):          # children before parents
        if table in have:
            op.drop_table(table)
