"""An SOP becomes a procedure you can read, not only a document you can download.

`intranet_sop` gains the written procedure (`body`, and `published_body` -- what members read, so
a rewrite is not in force until somebody publishes it), the card's one-line `summary`, `applies_to`,
the Launchpad tiles it needs (`tool_ids`), `last_reviewed_on`, and `required` for procedures whose
new versions have to be announced.

`intranet_sop_version`'s file columns become NULLABLE. A version is a revision, and a revision of
a written procedure has no file; acknowledgements hang off the version, so a body-only revision
needs a row of its own to ask the team again. On SQLite the schema comes from the models, which
already declare them nullable, and SQLite cannot alter nullability without rebuilding the table --
so, as in 0012 and 0016, that half runs on Postgres only.

Additive and idempotent otherwise.

Revision ID: 0077_sop_library
Revises: 0076_whos_who
"""
from alembic import op
import sqlalchemy as sa

from app.dbtypes import JSONType

revision = "0077_sop_library"
down_revision = "0076_whos_who"
branch_labels = None
depends_on = None

SOP_COLUMNS = (
    ("summary", sa.Text(), {}),
    ("body", JSONType, {}),
    ("published_body", JSONType, {}),
    ("applies_to", sa.Text(), {}),
    ("tool_ids", JSONType, {}),
    ("last_reviewed_on", sa.Date(), {}),
    ("required", sa.Boolean(), {"nullable": False, "server_default": sa.text("false")}),
)
FILE_COLUMNS = (
    ("filename", sa.Text()),
    ("storage_key", sa.Text()),
    ("content_type", sa.Text()),
    ("byte_size", sa.BigInteger()),
)


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    have = _columns("intranet_sop")
    for name, type_, extra in SOP_COLUMNS:
        if name not in have:
            op.add_column("intranet_sop",
                          sa.Column(name, type_, nullable=extra.get("nullable", True),
                                    server_default=extra.get("server_default")))

    if op.get_bind().dialect.name == "postgresql":
        for name, type_ in FILE_COLUMNS:
            op.alter_column("intranet_sop_version", name, existing_type=type_, nullable=True)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # Only reversible while every version still has its file, which is true unless a
        # body-only revision has been written since.
        for name, type_ in FILE_COLUMNS:
            op.alter_column("intranet_sop_version", name, existing_type=type_, nullable=False)
    have = _columns("intranet_sop")
    for name, _, _ in SOP_COLUMNS:
        if name in have:
            op.drop_column("intranet_sop", name)
