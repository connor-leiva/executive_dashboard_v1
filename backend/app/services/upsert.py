"""One batched upsert that runs the same statement in production and in the test suite.

WHY THIS EXISTS. The Follow Up Boss sync wrote its rows with `postgresql.insert(...)
.on_conflict_do_update`, which SQLite -- the test suite's database -- cannot compile, so no test ever
ran it. It then failed every production run for its whole life: it handed a Date column the string
'2024-10-09', which asyncpg refuses and which SQLite's Date type would have refused too, had a test
ever reached the insert. Twenty-six runs, twenty-six errors, no leads.

Postgres and SQLite spell ON CONFLICT the same way in SQLAlchemy, so the only difference is which
`insert` builds the statement. Choosing it from the session's own dialect means a sync written
against this helper is exercised by the suite exactly as production runs it.

BATCHED, because a CRM sync writes thousands of rows and one round trip per row was most of the
cost. The batch is sized by column count to stay well inside both drivers' bound-parameter limits
(asyncpg 32767; SQLite 32766 on any build this project runs).
"""
from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

MAX_PARAMS = 20_000


def insert_for(s: AsyncSession):
    """The dialect's own `insert`, which is the one that knows ON CONFLICT."""
    return pg_insert if s.get_bind().dialect.name == "postgresql" else sqlite_insert


async def upsert(s: AsyncSession, model, rows: list[dict], *, keys: list[str],
                 update: list[str]) -> int:
    """Insert `rows`, updating `update` on a conflict over `keys`. Returns the row count.

    Every row must carry the same keys: a multi-row VALUES has one column list.
    """
    if not rows:
        return 0
    # Postgres refuses an ON CONFLICT statement that touches the same row twice, so a source that
    # repeats a record inside one batch would fail the whole write. The last copy wins.
    rows = list({tuple(row[k] for k in keys): row for row in rows}.values())
    columns = len(rows[0])
    size = max(1, MAX_PARAMS // max(1, columns))
    ins = insert_for(s)
    for i in range(0, len(rows), size):
        stmt = ins(model).values(rows[i:i + size])
        stmt = stmt.on_conflict_do_update(
            index_elements=keys, set_={col: stmt.excluded[col] for col in update})
        await s.execute(stmt)
    return len(rows)
