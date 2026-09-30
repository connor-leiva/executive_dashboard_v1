"""Move Payables' documents out of the Binder's categories.

No schema change. `binder_document.category` is a plain String with no constraint, and the two
new values -- `payable` and `vendor_tax` -- are already valid. What this migration does is fix
the ROWS that were filed under Binder categories before those two existed, because the code that
now hides Payables' documents from the Binder hides them BY CATEGORY. Leave the rows alone and
the behaviour is only half true: new invoices are separated and the ones already filed keep
showing up in the Binder's document list, its review queue and its extraction pass.

Two sets of rows, both identified by what points AT them rather than by guessing from filenames:

  * a vendor's W-9 -- `vendor.w9_document_id` -- was filed as `tax`. A W-9 is a tax document, but
    it is the supplier's, and `tax` is where the workspace's own filings live. On the live
    workspace that is one row sitting among eleven.
  * an invoice uploaded through /payables/upload -- `payable.document_id` -- was filed as `other`,
    because the upload route predates the category.

Data-only, so it is naturally idempotent: re-running it matches nothing. On a fresh database
0001_init's create_all has already made both tables and they are empty, so it updates nothing.

Revision ID: 0087_payables_docs
Revises: 0086_payment_run
"""
from alembic import op
import sqlalchemy as sa

revision = "0087_payables_docs"
down_revision = "0086_payment_run"
branch_labels = None
depends_on = None


def _tables() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _relabel(pairs) -> None:
    """pairs: (source table, its column pointing at a document, the category to set)."""
    have = _tables()
    if "binder_document" not in have:
        return
    for table, column, category in pairs:
        if table not in have:
            continue
        cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
        if column not in cols:
            continue
        op.execute(sa.text(
            f"UPDATE binder_document SET category = :cat WHERE category <> :cat AND id IN "
            f"(SELECT {column} FROM {table} WHERE {column} IS NOT NULL)").bindparams(cat=category))


def upgrade() -> None:
    _relabel([("vendor", "w9_document_id", "vendor_tax"),
              ("payable", "document_id", "payable")])


def downgrade() -> None:
    # Back to where each came from. `other` is the honest reverse for an invoice: the upload route
    # had no category to give it, and inventing one on the way down would be a different lie.
    _relabel([("vendor", "w9_document_id", "tax"),
              ("payable", "document_id", "other")])
