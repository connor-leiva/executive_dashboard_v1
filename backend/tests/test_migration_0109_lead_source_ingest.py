"""Migration 0109 -- the lead columns and the deal columns the scoped FUB ingestion stores.

It chains onto the single head (0108_zoom_attendance), the added columns and the origin index match
the models, and it runs twice each way. The single-head test (test_operator_console) guards the
graph as a whole.
"""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from app.models import CrmActivity, Lead

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic/versions/0109_lead_source_ingest.py"
PARENTS = (
    "CREATE TABLE tenant (id CHAR(36) PRIMARY KEY)",
    "CREATE TABLE business (id CHAR(36) PRIMARY KEY)",
    "CREATE TABLE lead (id CHAR(36) PRIMARY KEY, tenant_id CHAR(36), business_id CHAR(36), "
    "origin VARCHAR(120))",
    "CREATE TABLE crm_activity (id CHAR(36) PRIMARY KEY, tenant_id CHAR(36), kind VARCHAR(16))",
)


def _module():
    spec = importlib.util.spec_from_file_location("mig0109", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(conn, fn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def test_it_chains_onto_the_single_head():
    mod = _module()
    assert mod.down_revision == "0108_zoom_attendance" and len(mod.revision) <= 30
    heads = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_heads()
    assert heads == [mod.revision]


def test_it_matches_the_models_and_round_trips(tmp_path):
    eng = sa.create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    with eng.begin() as conn:
        for ddl in PARENTS:
            conn.execute(sa.text(ddl))
    mod = _module()
    lead_added = {name for name, _ in mod.LEAD_COLUMNS}
    act_added = {name for name, _ in mod.ACTIVITY_COLUMNS}
    assert lead_added == {"src_url", "assigned_name"} and act_added == {"deal_stage", "deal_status"}
    with eng.begin() as conn:
        _run(conn, mod.upgrade)
        _run(conn, mod.upgrade)
        insp = sa.inspect(conn)
        lead_cols = {c["name"]: c for c in insp.get_columns("lead")}
        act_cols = {c["name"]: c for c in insp.get_columns("crm_activity")}
        for name in lead_added:
            assert name in lead_cols and lead_cols[name]["nullable"] and Lead.__table__.c[name].nullable
            assert lead_cols[name]["type"].length == Lead.__table__.c[name].type.length
        for name in act_added:
            assert name in act_cols and CrmActivity.__table__.c[name].nullable
            assert act_cols[name]["type"].length == CrmActivity.__table__.c[name].type.length
        ix = {i["name"]: i["column_names"] for i in insp.get_indexes("lead")}
        model_ix = {i.name: [c.name for c in i.columns] for i in Lead.__table__.indexes}
        assert ix["ix_lead_origin"] == model_ix["ix_lead_origin"] == ["tenant_id", "business_id", "origin"]

        _run(conn, mod.downgrade)
        _run(conn, mod.downgrade)
        insp = sa.inspect(conn)
        assert not lead_added & {c["name"] for c in insp.get_columns("lead")}
        assert not act_added & {c["name"] for c in insp.get_columns("crm_activity")}
        assert "ix_lead_origin" not in {i["name"] for i in insp.get_indexes("lead")}
