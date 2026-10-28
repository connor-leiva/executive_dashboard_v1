"""Migration 0096 — applied allocations: rule versions, drivers, QuickBooks line detail.

The suite builds its schema with create_all and production builds it with Alembic, so nothing
else here ever runs this file. That is exactly how a migration drifts from its model unnoticed:
a column added to the model and not here is a column production does not have, and the first
request to read it fails there and nowhere else.

So, modelled on test_migration_0093_cleanup.py and a little stricter: the five new tables'
columns must EQUAL the models' columns, the six added columns must appear and come back out,
the chain is pinned, and the whole thing runs twice each way in an isolated database.

It also holds the two checks a grep in a spec cannot hold. Acceptance check A2 was a false
green for three weeks: it globbed `0093*`, which had come to match somebody else's clean
migration, so it passed with no allocation code written at all. Here it is a test.
"""
import importlib.util
import re
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models import (AllocationDriver, AllocationDriverCommit, AllocationDriverValue,
                        AllocationRuleVersion, BookTxnLine)

VERSIONS = Path(__file__).resolve().parents[1] / "alembic/versions"
MIGRATION = VERSIONS / "0096_alloc_rule_versions.py"

NEW_TABLES = (AllocationDriver, AllocationDriverCommit, AllocationDriverValue,
              AllocationRuleVersion, BookTxnLine)

# The columns this migration ADDS to tables that already exist, per owning table.
ADDED = {
    "allocation_rule": ("mode", "scope_kind"),
    "allocation_contribution": ("grain", "rule_version_id", "driver_commit_id"),
    "coa_settings": ("alloc_pool_business_ids",),
}

# Enough of each parent for the foreign keys and the composite index to resolve. Deliberately
# minimal: this test is about what 0096 creates, not about the rest of the schema.
PARENTS = (
    'CREATE TABLE tenant (id CHAR(36) PRIMARY KEY)',
    'CREATE TABLE "user" (id CHAR(36) PRIMARY KEY)',
    'CREATE TABLE business (id CHAR(36) PRIMARY KEY)',
    'CREATE TABLE book_txn (id CHAR(36) PRIMARY KEY)',
    'CREATE TABLE allocation_rule (id CHAR(36) PRIMARY KEY)',
    'CREATE TABLE allocation_contribution (id CHAR(36) PRIMARY KEY, tenant_id CHAR(36), '
    'booking VARCHAR(10), period_start DATE)',
    'CREATE TABLE coa_settings (id CHAR(36) PRIMARY KEY)',
)


def _module():
    spec = importlib.util.spec_from_file_location("mig0096", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(conn, fn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _engine(tmp_path, name="m.db"):
    eng = sa.create_engine(f"sqlite:///{(tmp_path / name).as_posix()}")
    with eng.begin() as conn:
        for ddl in PARENTS:
            conn.execute(sa.text(ddl))
    return eng


def test_it_creates_what_the_models_describe_and_comes_back_out(tmp_path):
    eng = _engine(tmp_path)
    mod = _module()
    assert mod.down_revision == "0095_agent_roster_dates"
    assert mod.revision == "0096_alloc_rule_versions"

    with eng.begin() as conn:
        _run(conn, mod.upgrade)
        _run(conn, mod.upgrade)                        # a half-applied deploy re-runs it
        insp = sa.inspect(conn)
        names = set(insp.get_table_names())

        for model in NEW_TABLES:
            table = model.__table__
            assert table.name in names, table.name
            assert ({c["name"] for c in insp.get_columns(table.name)}
                    == {c.name for c in table.columns}), table.name
            # Names matching is not enough. A column this file leaves nullable that the model
            # declares NOT NULL, or a server default only one of them sets, is a difference
            # only production meets — and only on the row that omits the column.
            got = {c["name"]: (bool(c["nullable"]), c["default"] is not None)
                   for c in insp.get_columns(table.name)}
            want = {c.name: (c.nullable, c.server_default is not None) for c in table.columns}
            assert got == want, (table.name, {k: (got[k], want[k])
                                              for k in want if got[k] != want[k]})

        for table, added in ADDED.items():
            cols = {c["name"] for c in insp.get_columns(table)}
            assert set(added) <= cols, (table, sorted(set(added) - cols))

        # The named indexes, which a create_all database gets for free and this file has to
        # ensure by name (the reason they sit outside the create_table guards).
        for table, index in (("allocation_driver_commit", "ix_alloc_drv_commit"),
                             ("allocation_rule_version", "ix_alloc_ver_rule"),
                             ("book_txn_line", "ix_book_txn_line_account"),
                             ("allocation_contribution", "ix_alloc_month")):
            assert index in {i["name"] for i in insp.get_indexes(table)}, index

        # One version per rule, one line per position: the two constraints that stop a re-sync
        # or a double-submit producing a second row that silently wins.
        uq = {u["name"]: u["column_names"]
              for u in insp.get_unique_constraints("allocation_rule_version")}
        assert uq.get("uq_alloc_rule_version") == ["rule_id", "version"]
        uq = {u["name"]: u["column_names"] for u in insp.get_unique_constraints("book_txn_line")}
        assert uq.get("uq_book_txn_line") == ["book_txn_id", "line_num"]

        _run(conn, mod.downgrade)
        _run(conn, mod.downgrade)
        insp = sa.inspect(conn)
        gone = {m.__table__.name for m in NEW_TABLES}
        assert not gone & set(insp.get_table_names())
        for table, added in ADDED.items():
            cols = {c["name"] for c in insp.get_columns(table)}
            assert not set(added) & cols, (table, sorted(set(added) & cols))


def test_the_phase_one_round_trip(tmp_path):
    """Upgrade, down one, up again — the sequence the Phase 1 gate names, and the one a rollback
    actually performs. An upgrade that only works on an empty database passes the test above."""
    eng = _engine(tmp_path, "rt.db")
    mod = _module()
    with eng.begin() as conn:
        _run(conn, mod.upgrade)
        _run(conn, mod.downgrade)
        _run(conn, mod.upgrade)
        insp = sa.inspect(conn)
        assert {m.__table__.name for m in NEW_TABLES} <= set(insp.get_table_names())
        assert "grain" in {c["name"] for c in insp.get_columns("allocation_contribution")}


def test_no_postgres_only_types_and_exactly_one_file():
    """Acceptance check A2, as a test rather than a glob somebody has to remember to re-run.

    The suite runs SQLite and production runs Postgres, so a Postgres-only type here is
    invisible until the deploy. `JSONB` and `PG_UUID` are reachable by import and `sa.Enum`
    needs a Postgres-only CREATE TYPE, which is why GUID and JSONType exist in app/dbtypes.py.
    """
    matches = sorted(VERSIONS.glob("0096*"))
    assert len(matches) == 1, [p.name for p in matches]
    body = MIGRATION.read_text(encoding="utf-8")
    # Skip the docstring: it may legitimately say the word.
    code = body.split('"""', 2)[2] if body.count('"""') >= 2 else body
    for bad in ("sa.Enum", "postgresql.ENUM", "JSONB", "PG_UUID"):
        assert bad not in code, bad


def test_it_renders_native_postgres_ddl():
    """The round trip above runs on SQLite, and production is Postgres. No Postgres is reachable
    from a developer machine here, so the next best thing: render the DDL under the Postgres
    dialect and read it. That catches the class of failure the suite otherwise cannot see — a
    type that only exists on one dialect, or a boolean default SQLite tolerates and Postgres
    does not.

    It is NOT a substitute for executing against a Postgres copy, which stays in the gate.
    """
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    from app.models import AllocationContribution, AllocationRule, CoaSettings

    d = postgresql.dialect()
    ddl = "\n".join(str(CreateTable(m.__table__).compile(dialect=d)) for m in NEW_TABLES)
    comp = d.ddl_compiler(d, None)
    for model in (AllocationRule, AllocationContribution, CoaSettings):
        for name in ADDED[model.__table__.name]:
            ddl += "\n" + comp.get_column_specification(model.__table__.c[name])

    assert "UUID" in ddl and "CHAR(36)" not in ddl        # GUID resolved to the native type
    assert "JSONB" in ddl and " JSON " not in ddl         # JSONType likewise
    assert "CREATE TYPE" not in ddl                       # no enum: §3's standing rule
    assert "DEFAULT true" in ddl                          # not DEFAULT 1, which Postgres rejects
    assert "NUMERIC(14, 4)" in ddl and "NUMERIC(14, 2)" in ddl
    assert "DEFAULT 'observed'" in ddl and "DEFAULT 'window'" in ddl


def test_the_revision_id_fits_the_house_margin():
    """`alembic_version.version_num` is VARCHAR(32) and the house margin is 30. 0046 shipped at
    34 and crash-looped Railway AFTER its body had run, which is the worst place to find out."""
    mod = _module()
    assert len(mod.revision) <= 30, (mod.revision, len(mod.revision))
    assert re.fullmatch(r"0096_[a-z0-9_]+", mod.revision)
