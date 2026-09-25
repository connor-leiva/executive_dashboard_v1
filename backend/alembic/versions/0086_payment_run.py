"""Payables Phase 3 — payment runs, and the link that lets an approved bill skip the queue.

SPEC-payables §4.1. One table and one column.

NUMBERING: the spec says 0082. Recruiting took 0080-0083 and Payables 0084-0085, so this is
0086.

`payment_run.business_id` is NOT NULL. The spec left it nullable to permit either shape;
Connor settled it — one run per entity, because the five companies have separate QBO realms and
separate bank accounts, and a combined run produces an export somebody splits by hand at the
bank.

`payable.exception_holds` records WHICH holds an override cleared and on which run, so an
override cannot become a standing exemption against holds nobody has seen yet. It lands here
rather than in its own migration because 0086 has not shipped.

`book_txn.payable_id` is the payoff of the whole module: a transaction carrying one was coded
and approved by a human BEFORE the money moved, so it skips the scan queue instead of being
reviewed a second time. It is deliberately absent from books_sync._TXN_SOURCE_KEYS — a re-sync
that nulled it would silently push already-approved payments back into review, and nothing in
the symptom would point at the sync.

No foreign key on `payable.payment_run_id`. That column shipped in 0085, before payment_run
existed. Adding the constraint now means a batch table rebuild on SQLite for a table that by
then holds live bills, and the service is the only writer of the column — the risk outweighs
what the constraint buys here.

Revision ID: 0086_payment_run
Revises: 0085_payables_bill
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision = "0086_payment_run"
down_revision = "0085_payables_bill"
branch_labels = None
depends_on = None


def _tables() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _cols(table: str) -> set:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    tabs = _tables()

    if "payment_run" not in tabs:
        op.create_table(
            "payment_run",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("business_id", GUID(), sa.ForeignKey("business.id", ondelete="CASCADE"),
                      nullable=False, index=True),
            sa.Column("run_date", sa.Date(), nullable=False),
            sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
            sa.Column("released_by", GUID(), sa.ForeignKey("user.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("total_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("export_ref", sa.String(200), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_payment_run_date", "payment_run",
                        ["tenant_id", "business_id", "run_date"])

    if "payable" in tabs and "exception_holds" not in _cols("payable"):
        op.add_column("payable", sa.Column("exception_holds", JSONType, nullable=True))
        # JSONType is used BARE, never instantiated (see app/dbtypes.py and every prior
        # migration). `JSONType()` raises TypeError mid-upgrade — after payment_run has
        # already been created, so the next attempt starts from a half-applied schema.

    if "book_txn" in tabs and "payable_id" not in _cols("book_txn"):
        op.add_column("book_txn", sa.Column("payable_id", GUID(), nullable=True))
    # Named exactly as the model names it (`index=True` -> ix_<table>_<column>). A database
    # built by 0001's create_all already has this index under that name; one patched by this
    # migration must end up with the SAME name, or a later migration that drops it works on
    # one path and fails on the other.
    if "book_txn" in tabs and "ix_book_txn_payable_id" not in _indexes("book_txn"):
        op.create_index("ix_book_txn_payable_id", "book_txn", ["payable_id"])


def downgrade() -> None:
    tabs = _tables()
    if "payable" in tabs and "exception_holds" in _cols("payable"):
        with op.batch_alter_table("payable") as b:
            b.drop_column("exception_holds")
    if "book_txn" in tabs:
        if "ix_book_txn_payable_id" in _indexes("book_txn"):
            op.drop_index("ix_book_txn_payable_id", table_name="book_txn")
        if "payable_id" in _cols("book_txn"):
            # batch mode so the downgrade works on SQLite too, where DROP COLUMN needs a rebuild.
            with op.batch_alter_table("book_txn") as b:
                b.drop_column("payable_id")
    if "payment_run" in tabs:
        if "ix_payment_run_date" in _indexes("payment_run"):
            op.drop_index("ix_payment_run_date", table_name="payment_run")
        op.drop_table("payment_run")
