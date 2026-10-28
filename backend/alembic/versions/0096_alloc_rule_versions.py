"""Applied allocations: rule versions, drivers, and QuickBooks line detail.

Five tables and six columns. Nothing reads them yet (ALLOCATION-RULES-SPEC Phase 1).

  allocation_driver          a monthly input a rule can be driven by (staff time, headcount)
  allocation_driver_commit   one month of one driver, committed by a person; append-only
  allocation_driver_value    one number inside one commit; immutable
  allocation_rule_version    one version of one rule's terms; append-only
  book_txn_line              one QuickBooks line, with its own account and its own amount

Created in that order because each FKs the one above it, and `allocation_contribution`'s two new
columns FK `allocation_rule_version` and `allocation_driver_commit`, so both exist first.

ADDITIVE ONLY. No column is dropped, renamed or retyped, and every existing `observed`
allocation row keeps its meaning exactly: `allocation_rule.mode` defaults to `observed` and
`allocation_contribution.grain` to `window`, which is what those rows already are.

`book_txn_line` is the one table here that is not about rules. `BookTxn` stores a header amount
against the FIRST category line's account, so a four-line purchase charges line one for the
whole transaction — the gap the Books Cleanup tab reports as "transactions short by". It costs
no extra QuickBooks calls: `books_sync` already receives `obj["Line"]`.

NUMBERING: 0095_agent_roster_dates is head, so this is 0096 (0093 is books_cleanup, and 0094
and 0095 chain after it). `alembic heads` was one line before this; a fork crash-loops Railway
on boot. The revision id is 24 characters, inside the 30-character house margin — a longer id
stamp-fails on Postgres only, after its body has run (the 0046 crash-loop).

Guarded throughout: `0001_init` runs `create_all()` of the current models, so a fresh database
already has every table and column below, and a half-applied deploy re-runs this file.

Revision ID: 0096_alloc_rule_versions
Revises: 0095_agent_roster_dates
Create Date: 2026-10-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID, JSONType

revision: str = "0096_alloc_rule_versions"
down_revision: Union[str, None] = "0095_agent_roster_dates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DRIVER = "allocation_driver"
_COMMIT = "allocation_driver_commit"
_VALUE = "allocation_driver_value"
_VERSION = "allocation_rule_version"
_LINE = "book_txn_line"
_RULE = "allocation_rule"
_CONTRIB = "allocation_contribution"
_COA = "coa_settings"


def _tables(bind) -> set:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> set:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def _indexes(bind, table: str) -> set:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return set()
    return {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    have = _tables(bind)

    if _DRIVER not in have:
        op.create_table(
            _DRIVER,
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("key", sa.String(40), nullable=False),
            sa.Column("label", sa.String(80), nullable=False),
            sa.Column("kind", sa.String(8), nullable=False),          # split | count
            sa.Column("unit", sa.String(12), nullable=True),          # percent | people | members
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", "key", name="uq_alloc_driver_key"),
        )

    if _COMMIT not in have:
        op.create_table(
            _COMMIT,
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("driver_id", GUID(), sa.ForeignKey(f"{_DRIVER}.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("period_month", sa.Date(), nullable=False),
            sa.Column("committed_by", GUID(), sa.ForeignKey("user.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            # Self-referential: the commit that replaced this one.
            sa.Column("superseded_by", GUID(), sa.ForeignKey(f"{_COMMIT}.id", ondelete="SET NULL"),
                      nullable=True),
        )

    if _VALUE not in have:
        op.create_table(
            _VALUE,
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("commit_id", GUID(), sa.ForeignKey(f"{_COMMIT}.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("subject", sa.String(120), nullable=True),
            sa.Column("business_id", GUID(), sa.ForeignKey("business.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("value_kind", sa.String(10), nullable=False),   # share|amount|count|remainder
            sa.Column("value", sa.Numeric(14, 4), nullable=True),
        )

    if _VERSION not in have:
        op.create_table(
            _VERSION,
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("rule_id", GUID(), sa.ForeignKey(f"{_RULE}.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("tier", sa.Integer(), nullable=True),
            sa.Column("method", sa.String(10), nullable=False),
            # JSONType BARE, never instantiated — `JSONType()` raises TypeError mid-upgrade.
            sa.Column("targets", JSONType, nullable=True),
            sa.Column("driver_id", GUID(), sa.ForeignKey(f"{_DRIVER}.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("driver_subject", sa.String(120), nullable=True),
            sa.Column("source_spec", JSONType, nullable=True),
            sa.Column("effective_from", sa.Date(), nullable=False),
            sa.Column("reach", sa.String(10), nullable=False),        # forward|from_date|retro
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("approved_by", GUID(), sa.ForeignKey("user.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("superseded_by", GUID(), sa.ForeignKey(f"{_VERSION}.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
            sa.UniqueConstraint("rule_id", "version", name="uq_alloc_rule_version"),
        )

    if _LINE not in have:
        op.create_table(
            _LINE,
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("book_txn_id", GUID(), sa.ForeignKey("book_txn.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("line_num", sa.Integer(), nullable=False),
            sa.Column("qbo_line_id", sa.String(32), nullable=True),
            sa.Column("detail_type", sa.String(40), nullable=True),
            sa.Column("account_label", sa.String(200), nullable=True),
            sa.Column("account_qbo_id", sa.String(32), nullable=True),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("posting_type", sa.String(6), nullable=True),   # debit | credit
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("payee", sa.String(200), nullable=True),
            sa.Column("class_label", sa.String(200), nullable=True),
            sa.UniqueConstraint("book_txn_id", "line_num", name="uq_book_txn_line"),
        )

    # ── named indexes, outside the create_table guards ──
    # A fresh database built by create_all skips every guard above, so an index added to a
    # model later would never be created here. Ensured by name instead.
    if "ix_alloc_drv_commit" not in _indexes(bind, _COMMIT):
        op.create_index("ix_alloc_drv_commit", _COMMIT,
                        ["tenant_id", "driver_id", "period_month", "is_current"])
    if "ix_alloc_ver_rule" not in _indexes(bind, _VERSION):
        op.create_index("ix_alloc_ver_rule", _VERSION, ["tenant_id", "rule_id", "is_current"])
    if "ix_book_txn_line_account" not in _indexes(bind, _LINE):
        op.create_index("ix_book_txn_line_account", _LINE, ["tenant_id", "account_qbo_id"])

    # ── added columns ──
    cols = _columns(bind, _RULE)
    if _RULE in have and "mode" not in cols:
        op.add_column(_RULE, sa.Column("mode", sa.String(10), nullable=False,
                                       server_default="observed"))
    if _RULE in have and "scope_kind" not in cols:
        op.add_column(_RULE, sa.Column("scope_kind", sa.String(12), nullable=False,
                                       server_default="account"))

    cols = _columns(bind, _CONTRIB)
    if _CONTRIB in have and "grain" not in cols:
        op.add_column(_CONTRIB, sa.Column("grain", sa.String(8), nullable=False,
                                          server_default="window"))
    # No database-level FK on these two, matching the models and `book_txn.payable_id`.
    # `allocation_contribution` already exists, and alembic cannot add a referencing column to
    # an existing table on SQLite ("No support for ALTER of constraints in SQLite dialect"), so
    # a constraint here would be one production has that no test has ever exercised.
    if _CONTRIB in have and "rule_version_id" not in cols:
        op.add_column(_CONTRIB, sa.Column("rule_version_id", GUID(), nullable=True))
    if _CONTRIB in have and "driver_commit_id" not in cols:
        op.add_column(_CONTRIB, sa.Column("driver_commit_id", GUID(), nullable=True))
    if _CONTRIB in have and "ix_alloc_month" not in _indexes(bind, _CONTRIB):
        op.create_index("ix_alloc_month", _CONTRIB,
                        ["tenant_id", "grain", "booking", "period_start"])

    if _COA in have and "alloc_pool_business_ids" not in _columns(bind, _COA):
        op.add_column(_COA, sa.Column("alloc_pool_business_ids", JSONType, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    have = _tables(bind)

    # Columns first, then children before parents.
    if _COA in have and "alloc_pool_business_ids" in _columns(bind, _COA):
        op.drop_column(_COA, "alloc_pool_business_ids")

    if _CONTRIB in have:
        if "ix_alloc_month" in _indexes(bind, _CONTRIB):
            op.drop_index("ix_alloc_month", table_name=_CONTRIB)
        cols = _columns(bind, _CONTRIB)
        for c in ("driver_commit_id", "rule_version_id", "grain"):
            if c in cols:
                op.drop_column(_CONTRIB, c)

    if _RULE in have:
        cols = _columns(bind, _RULE)
        for c in ("scope_kind", "mode"):
            if c in cols:
                op.drop_column(_RULE, c)

    # Children before parents.
    for table in (_LINE, _VERSION, _VALUE, _COMMIT, _DRIVER):
        if table in have:
            op.drop_table(table)
