"""Lead.email — the attribution join key.

One nullable column and its index. Nothing else changes.

WHY THIS REVERSES A DELIBERATE DECISION. The Lead model stored a name and nothing else, on the
reasoning that a follow-up row opens the person in Follow Up Boss where the call is actually
made, so the app never needed contact details and should not hold a copy of 143,000 people's.
That reasoning was right while the only question was "who should I call today".

The question changed. "Which Realtor.com leads financed with Sympli" spans three systems -- FUB
holds the lead source, Sisu the transaction, Arive the loan -- and the only identifier all three
carry for the same person is an email address. Name matching was tried first and is not merely
weaker, it is wrong here: `transaction.buyer_name` in this app turns out to hold the OTHER party
(the seller on a buy-side deal), so matching leads to transactions by name was pairing sellers
with buyers and scoring about 10%. The same join on email scores 32% against Arive and is correct
where it hits.

SCOPE, deliberately narrow: the PRIMARY address only, lowercased, nothing else. Not the secondary
addresses FUB holds on the same person, not phones, not postal addresses. One field, because one
field is what the join needs.

Nullable with no backfill in the migration. The column fills on the next FUB sync, which rewrites
every person it walks; a migration that tried to populate it would need the FUB API and a
credential, which is not a migration's job.

NUMBERING: written as 0090 off 0089 and that was WRONG -- 0090_forum_event already existed and
the chain had run on to 0096 while this was being built. Two heads, which is the shape that
crash-loops the deploy. Re-pointed at 0096_alloc_rule_versions and renumbered 0097. The lesson is
that `alembic heads` has to be read when the migration is WRITTEN, not only before it ships: the
tip moves under you when other work is landing on main.

Revision ID: 0097_lead_email
Revises: 0096_alloc_rule_versions
"""
from alembic import op
import sqlalchemy as sa

revision = "0097_lead_email"
down_revision = "0096_alloc_rule_versions"
branch_labels = None
depends_on = None


def _cols(table: str) -> set:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "lead" not in tables:
        return
    if "email" not in _cols("lead"):
        op.add_column("lead", sa.Column("email", sa.String(255), nullable=True))
    # Named exactly as the model names it (`index=True` -> ix_<table>_<column>). A database built
    # by create_all already has it under that name; one patched here must match, or a later
    # migration that drops it works on one path and fails on the other.
    if "ix_lead_email" not in _indexes("lead"):
        op.create_index("ix_lead_email", "lead", ["email"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "lead" not in tables:
        return
    if "ix_lead_email" in _indexes("lead"):
        op.drop_index("ix_lead_email", table_name="lead")
    if "email" in _cols("lead"):
        # batch mode so the downgrade works on SQLite too, where DROP COLUMN needs a rebuild.
        with op.batch_alter_table("lead") as b:
            b.drop_column("email")
