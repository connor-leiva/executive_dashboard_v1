"""Migration 0093 -- the Books Cleanup tables.

The suite builds its schema with create_all, and production builds it with Alembic, so nothing else
here ever runs this file. It is two CREATE TABLEs, which is exactly the kind of migration that drifts
from its model unnoticed: a column added to the model and not here is a column production doesn't
have, and the first request to read it fails there and nowhere else.

So: it creates what the models describe, it can run twice (a half-applied deploy re-runs it), and it
comes back out.
"""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models import CleanupDataset, CleanupState

MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/0093_books_cleanup.py"


def _module():
    spec = importlib.util.spec_from_file_location("mig0093", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(conn, fn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def test_it_creates_what_the_models_describe_and_comes_back_out(tmp_path):
    eng = sa.create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    mod = _module()
    assert mod.down_revision == "0092_scorecard_metric_src_spec"
    with eng.begin() as conn:
        conn.execute(sa.text("CREATE TABLE tenant (id CHAR(32) PRIMARY KEY)"))
        conn.execute(sa.text('CREATE TABLE "user" (id CHAR(32) PRIMARY KEY)'))
        _run(conn, mod.upgrade)
        _run(conn, mod.upgrade)                                      # a re-run finds it all there
        insp = sa.inspect(conn)
        for model in (CleanupDataset, CleanupState):
            table = model.__table__
            assert table.name in insp.get_table_names()
            assert {c["name"] for c in insp.get_columns(table.name)} == {c.name for c in table.columns}, table.name
        assert "ix_cleanup_dataset_tenant_uploaded" in {i["name"] for i in insp.get_indexes("cleanup_dataset")}
        pk = insp.get_pk_constraint("cleanup_state")["constrained_columns"]
        assert pk == ["tenant_id"]                                    # one decisions row per workspace
        _run(conn, mod.downgrade)
        _run(conn, mod.downgrade)
        assert not {"cleanup_dataset", "cleanup_state"} & set(sa.inspect(conn).get_table_names())
