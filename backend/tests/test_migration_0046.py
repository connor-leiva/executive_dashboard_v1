"""Migration 0046, and the blind spot that let it reach production broken.

It shipped with `SET config = :cfg::jsonb` and crash-looped the API on deploy: SQLAlchemy's
text() will not treat `:cfg` as a bind parameter when a colon follows it immediately, so the
string went to Postgres verbatim. Valid Postgres, valid SQLAlchemy, broken together.

Nothing caught it, and the reason is structural rather than careless:

  * Migrations do not replay from scratch here — 0001 does Base.metadata.create_all — so 0046's
    upgrade() had never executed in any test.
  * The suite runs on SQLite, which took the other branch, so the Postgres string had never been
    rendered even once.

Both are cheap to fix. Compiling a statement needs no database, and the migration body is an
ordinary function that can be called against a table built by hand.
"""
import json

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


MIGRATION = "alembic/versions/0046_grandfather_implicit_defaults.py"


def _module():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / MIGRATION
    spec = importlib.util.spec_from_file_location("mig0046", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("is_pg", [True, False])
def test_the_update_binds_every_parameter_it_names(is_pg):
    """THE REGRESSION. A parameter that does not bind is not a slow query or a wrong answer —
    the statement never reaches the server as SQL at all, and the deploy crash-loops.

    Compiled against the real dialect, so it is the same rendering the server would receive."""
    mod = _module()
    dialect = postgresql.dialect(paramstyle="pyformat") if is_pg else sa.dialects.sqlite.dialect()
    compiled = sa.text(mod.update_sql(is_pg)).compile(dialect=dialect)
    assert sorted(compiled.params) == ["cfg", "id"], (
        f"rendered as {compiled!s} - a named parameter was swallowed")
    assert ":cfg" not in str(compiled), "the parameter is still literal in the rendered SQL"


def test_postgres_gets_a_jsonb_cast_and_sqlite_does_not():
    """The column is JSONB on Postgres and text on SQLite, so the cast is required on one and
    meaningless on the other. Pinned because losing it fails at runtime, not at import."""
    mod = _module()
    assert "jsonb" in mod.update_sql(True).lower()
    assert "jsonb" not in mod.update_sql(False).lower()


async def test_it_grandfathers_only_what_is_missing():
    """The migration's actual body, against a real table. Its whole purpose is to preserve
    today's numbers, so what matters is that it fills the gaps and touches nothing else."""
    mod = _module()
    engine = sa.create_engine("sqlite://")                 # in-memory, one connection
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE integration (id TEXT PRIMARY KEY, provider TEXT, config TEXT)"))
        rows = [
            ("a1", "arive", None),                                   # no config at all
            ("s1", "sisu", json.dumps({})),                          # empty config
            ("g1", "ghl", json.dumps({"forum_tags": []})),           # empty list counts as absent
            ("s2", "sisu", json.dumps({"referral_domains": ["acme.com"]})),   # a real choice
            ("q1", "qbo", json.dumps({})),                           # untouched provider
        ]
        for rid, prov, cfg in rows:
            conn.execute(sa.text("INSERT INTO integration VALUES (:i, :p, :c)"),
                         {"i": rid, "p": prov, "c": cfg})

        import alembic.op
        from unittest.mock import patch
        with patch.object(alembic.op, "get_bind", return_value=conn):
            mod.upgrade()

        got = {r[0]: json.loads(r[1]) if r[1] else {} for r in
               conn.execute(sa.text("SELECT id, config FROM integration")).fetchall()}

    assert got["a1"]["states"] == ["UT"]                             # filled from nothing
    assert got["s1"]["referral_domains"] == ["liveutah.com"]         # filled from empty
    assert got["g1"]["forum_tags"], "an empty list must count as absent - production had exactly this"
    assert got["s2"]["referral_domains"] == ["acme.com"], "overwrote a value somebody chose"
    assert got["q1"] == {}, "touched a provider it has no business touching"


async def test_running_it_twice_changes_nothing_the_second_time():
    """Idempotent by construction, and worth pinning: a failed deploy gets retried, and the
    retry must not compound whatever the first attempt did."""
    mod = _module()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE integration (id TEXT PRIMARY KEY, provider TEXT, config TEXT)"))
        conn.execute(sa.text("INSERT INTO integration VALUES ('a1', 'arive', NULL)"))

        import alembic.op
        from unittest.mock import patch
        with patch.object(alembic.op, "get_bind", return_value=conn):
            mod.upgrade()
            first = conn.execute(sa.text("SELECT config FROM integration")).scalar_one()
            mod.upgrade()
            second = conn.execute(sa.text("SELECT config FROM integration")).scalar_one()

    assert json.loads(first) == json.loads(second)
