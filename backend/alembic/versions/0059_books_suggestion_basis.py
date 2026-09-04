"""Books review: tag every existing suggestion with WHY it exists.

`suggestion.reason` is prose with the count interpolated — "Matches 195 prior charges",
"Matches 12 prior charges", "Matches 6 prior charges" are three unrelated strings to the
database. The live books carry ten such variants inside the top twelve reasons plus a tail of
roughly 715 more, which makes "show me everything matched from history" un-filterable.

This backfills a stable `basis` key (and the prior count, where the prose carries one) onto
existing rows. books_scan writes both going forward.

Classification is by pattern, and the fallback is reasoned rather than guessed: Pass 1 always
writes one of two known sentences, splits always write a third, so a suggestion whose reason
matches none of them can only have come from Pass 3 (Claude) — which is also the only writer
that can leave the reason empty.

No schema change: `suggestion` is already a JSON column. Values only.

Revision ID: 0059_books_suggestion_basis
Revises: 0058_marketing_attachments
"""
from typing import Sequence, Union
import re

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID, JSONType

revision: str = "0059_books_suggestion_basis"
down_revision: Union[str, None] = "0058_marketing_attachments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_HISTORY = re.compile(r"^Matches (\d+) prior charges categorized here\.?$")
_OVER_BAND = re.compile(r"^Known vendor, amount above the usual range \((\d+) priors?\)\.?$")
_SPLIT = "Split across multiple accounts; needs review."


def _classify(reason: str) -> tuple[str, int | None]:
    r = (reason or "").strip()
    m = _HISTORY.match(r)
    if m:
        return "history_match", int(m.group(1))
    m = _OVER_BAND.match(r)
    if m:
        return "over_band", int(m.group(1))
    if r == _SPLIT:
        return "split", None
    return "claude", None


def _tbl():
    return sa.table("book_txn", sa.column("id", GUID()), sa.column("suggestion", JSONType))


def upgrade() -> None:
    bind = op.get_bind()
    if "book_txn" not in set(sa.inspect(bind).get_table_names()):
        return
    t = _tbl()
    rows = bind.execute(sa.select(t.c.id, t.c.suggestion)
                        .where(t.c.suggestion.is_not(None))).fetchall()
    payload = []
    for rid, sug in rows:
        if not isinstance(sug, dict) or "basis" in sug:
            continue                                   # idempotent: never re-tag
        basis, priors = _classify(sug.get("reason"))
        new = {**sug, "basis": basis}
        if priors is not None:
            new["priors"] = priors                     # lets the reviewer sort by weakest evidence
        payload.append({"rid": rid, "sug": new})

    if payload:
        bind.execute(t.update().where(t.c.id == sa.bindparam("rid"))
                     .values(suggestion=sa.bindparam("sug")), payload)
    print(f"0059: tagged {len(payload)} of {len(rows)} suggestions", flush=True)


def downgrade() -> None:
    bind = op.get_bind()
    if "book_txn" not in set(sa.inspect(bind).get_table_names()):
        return
    t = _tbl()
    rows = bind.execute(sa.select(t.c.id, t.c.suggestion)
                        .where(t.c.suggestion.is_not(None))).fetchall()
    payload = []
    for rid, sug in rows:
        if not isinstance(sug, dict) or "basis" not in sug:
            continue
        new = {k: v for k, v in sug.items() if k not in ("basis", "priors")}
        payload.append({"rid": rid, "sug": new})
    if payload:
        bind.execute(t.update().where(t.c.id == sa.bindparam("rid"))
                     .values(suggestion=sa.bindparam("sug")), payload)
