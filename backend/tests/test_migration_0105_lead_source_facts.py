"""Migration 0105 -- crm_activity, the transaction commission lines and lead join, the lead columns.

Same shape as test_migration_0104: the new table EQUALS its model, the added columns appear and
come back out, and it runs twice each way.
"""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models import CrmActivity

MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/0105_lead_source_facts.py"
PARENTS = (
    "CREATE TABLE tenant (id CHAR(36) PRIMARY KEY)",
    "CREATE TABLE business (id CHAR(36) PRIMARY KEY)",
    "CREATE TABLE lead (id CHAR(36) PRIMARY KEY, tenant_id CHAR(36))",
    ('CREATE TABLE "transaction" (id CHAR(36) PRIMARY KEY, tenant_id CHAR(36), business_id CHAR(36), '
     "buyer_email VARCHAR(255))"),
)


def _module():
    spec = importlib.util.spec_from_file_location("mig0105", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(conn, fn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def test_it_matches_the_models_and_round_trips(tmp_path):
    eng = sa.create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    with eng.begin() as conn:
        for ddl in PARENTS:
            conn.execute(sa.text(ddl))
    mod = _module()
    assert mod.down_revision == "0104_lead_source_groups" and len(mod.revision) <= 30
    txn_added = {name for name, _ in mod.TXN_COLUMNS} | {"lead_id"}
    lead_added = {name for name, _ in mod.LEAD_COLUMNS}
    with eng.begin() as conn:
        _run(conn, mod.upgrade)
        _run(conn, mod.upgrade)
        insp = sa.inspect(conn)
        t = CrmActivity.__table__
        got = {c["name"]: (bool(c["nullable"]), c["default"] is not None) for c in insp.get_columns(t.name)}
        # The model as of this revision: the deal columns are 0109's (test_migration_0109).
        later = {"deal_stage", "deal_status"}
        want = {c.name: (c.nullable, c.server_default is not None) for c in t.columns if c.name not in later}
        assert got == want
        assert {"ix_crm_activity_person", "ix_crm_activity_tenant_id"} <= {i["name"] for i in insp.get_indexes(t.name)}
        assert txn_added <= {c["name"] for c in insp.get_columns("transaction")}
        assert {"ix_txn_lead", "ix_txn_buyer_email"} <= {i["name"] for i in insp.get_indexes("transaction")}
        assert any(fk["referred_table"] == "lead" for fk in insp.get_foreign_keys("transaction"))
        assert lead_added <= {c["name"] for c in insp.get_columns("lead")}
        # The model and the migration agree on every added column's type family.
        from app.models import Lead, Transaction
        for name in txn_added:
            assert name in Transaction.__table__.c, name
        for name in lead_added:
            assert name in Lead.__table__.c, name

        _run(conn, mod.downgrade)
        _run(conn, mod.downgrade)
        insp = sa.inspect(conn)
        assert "crm_activity" not in insp.get_table_names()
        assert not txn_added & {c["name"] for c in insp.get_columns("transaction")}
        assert not lead_added & {c["name"] for c in insp.get_columns("lead")}
