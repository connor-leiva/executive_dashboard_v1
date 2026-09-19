"""Migration 0076 -- Who's Who's columns, and the old free-text `owns` carried into `owns_items`.

Checked the way 0046 taught: its data step is compiled against the Postgres dialect here, because
the suite runs on SQLite and a bind that only breaks on Postgres otherwise reaches production.
"""
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

MIGRATION = "alembic/versions/0076_whos_who.py"


def _module():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / MIGRATION
    spec = importlib.util.spec_from_file_location("mig0076", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("is_pg", [True, False])
def test_the_owns_update_binds_every_parameter_it_names(is_pg):
    mod = _module()
    dialect = postgresql.dialect(paramstyle="pyformat") if is_pg else sa.dialects.sqlite.dialect()
    compiled = sa.text(mod.owns_sql(is_pg)).compile(dialect=dialect)
    assert sorted(compiled.params) == ["id", "items"], f"rendered as {compiled!s}"
    assert ":items" not in str(compiled)
    if is_pg:
        assert "CAST(" in str(compiled) and "AS jsonb)" in str(compiled)


def test_the_revision_fits_the_column_alembic_stamps_it_into():
    mod = _module()
    assert len(mod.revision) <= 32 and mod.down_revision == "0075_wtd_playbook"
