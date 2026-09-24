"""A write into an integration's config has to survive the commit.

Connor's report was "it's saying first sync needs to happen - that's been refreshed at least twice
now and no change". Every sync had in fact succeeded: 1,654 records, status ok, `last_synced_at`
set on the row. What never persisted was `config["synced_at"]`, which is the flag the tab reads --
so the product told him nothing had happened while the run history said it had.

The cause is a SQLAlchemy detail with no error attached to it. `Integration.config` is a plain JSON
column, not a MutableDict, so the unit of work decides whether to UPDATE by comparing the attribute's
current value against the one it loaded. The sync held a REFERENCE to the loaded dict, mutated it in
place, then reassigned an equal copy -- and "equal" is exactly the point: the original it compares
against had been mutated too, so the two matched and no UPDATE was emitted. The reassignment that
was supposed to make the change visible could not, and nothing anywhere raised.

The first test is the bug in miniature, against real SQLAlchemy rather than a mock, because the
behaviour being asserted belongs to the ORM. The second is a scan, because one sentence in a comment
is not a defence and this pattern reads as correct.
"""
import ast
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db import SessionLocal
from app.models import Integration


class _Base(DeclarativeBase):
    pass


class _Row(_Base):
    __tablename__ = "_cfg_probe"
    id: Mapped[int] = mapped_column(primary_key=True)
    config: Mapped[dict] = mapped_column(sa.JSON, default=dict)


async def test_mutating_the_loaded_config_in_place_does_not_persist():
    """The ORM behaviour itself, pinned. If a future SQLAlchemy starts flushing this, the alias
    pattern becomes harmless and this test says so loudly rather than quietly passing."""
    async with SessionLocal() as s:
        await s.run_sync(lambda sync_s: _Base.metadata.create_all(sync_s.get_bind()))
        await s.execute(sa.delete(_Row))
        s.add(_Row(id=1, config={"location_id": "loc"}))
        await s.commit()

    async with SessionLocal() as s:
        row = await s.get(_Row, 1)
        alias = row.config or {}            # a REFERENCE to what the ORM loaded
        alias["synced_at"] = "2026-09-24"
        row.config = dict(alias)            # equal to the object it is compared against
        await s.commit()
    async with SessionLocal() as s:
        row = await s.get(_Row, 1)
        assert "synced_at" not in (row.config or {}), (
            "SQLAlchemy now persists this; the alias pattern is no longer a silent data-loss bug "
            "and the scan below can be relaxed")

    async with SessionLocal() as s:
        row = await s.get(_Row, 1)
        copy = dict(row.config or {})       # a COPY: the comparison sees a real difference
        copy["synced_at"] = "2026-09-24"
        row.config = copy
        await s.commit()
    async with SessionLocal() as s:
        row = await s.get(_Row, 1)
        assert (row.config or {}).get("synced_at") == "2026-09-24"
        await s.execute(sa.delete(_Row))
        await s.commit()


def test_no_code_aliases_a_config_dict_and_then_writes_to_it():
    """The guard. `cfg = integ.config or {}` is indistinguishable from correct at a glance, and it
    throws the write away in total silence -- no exception, no log line, a successful sync. It must
    be caught by something that does not rely on anybody remembering."""
    offenders = []
    for path in sorted(Path(__file__).resolve().parents[1].joinpath("app").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                                   # not ours to police
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            aliased = {}
            for n in ast.walk(fn):
                if (isinstance(n, ast.Assign) and len(n.targets) == 1
                        and isinstance(n.targets[0], ast.Name)):
                    value = n.value
                    if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or):
                        value = value.values[0]              # `x.config or {}`
                    if isinstance(value, ast.Attribute) and value.attr == "config":
                        aliased[n.targets[0].id] = (n.lineno, ast.unparse(value))
            if not aliased:
                continue
            for n in ast.walk(fn):
                target = None
                if isinstance(n, ast.Assign):
                    for t in n.targets:
                        if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
                            target = t.value.id
                elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                      and n.func.attr in ("update", "pop", "setdefault", "clear")
                      and isinstance(n.func.value, ast.Name)):
                    target = n.func.value.id
                if target in aliased:
                    line, owner = aliased[target]
                    offenders.append(
                        f"{path.name}:{line} `{target} = {owner}` is a reference, not a copy, "
                        f"and is written at line {n.lineno} -- wrap it in dict()")
    assert not offenders, "config writes that will be silently discarded:\n  " + "\n  ".join(
        dict.fromkeys(offenders))


async def test_the_recruiting_sync_records_that_it_ran():
    """What the tab actually reads. `last_synced_at` on the row is not the same flag, and it was
    the one that kept working -- which is why the run history and the screen disagreed."""
    from app.services import recruiting_sync
    src = Path(recruiting_sync.__file__).read_text(encoding="utf-8")
    assert "cfg = dict(integ.config or {})" in src, (
        "the sync aliases integ.config again; config['synced_at'] will not persist")
    # And the flag it writes is the one the payload gates on.
    from app.services import recruiting
    assert '"synced_at"' in Path(recruiting.__file__).read_text(encoding="utf-8")
