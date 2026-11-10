"""Migration 0106 -- the digest config and deliveries, and Utah Life's inactive seed."""
import importlib.util
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models import LeadSourceDigest, LeadSourceDigestDelivery

MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/0106_lead_source_digest.py"
PARENTS = (
    "CREATE TABLE tenant (id CHAR(36) PRIMARY KEY)",
    "CREATE TABLE business (id CHAR(36) PRIMARY KEY, tenant_id CHAR(36), key VARCHAR(32))",
    "CREATE TABLE lead_source_group (id CHAR(36) PRIMARY KEY, tenant_id CHAR(36), business_id CHAR(36), key VARCHAR(64))",
    'CREATE TABLE "user" (id CHAR(36) PRIMARY KEY, tenant_id CHAR(36), name VARCHAR(200), email VARCHAR(255))',
)


def _module():
    spec = importlib.util.spec_from_file_location("mig0106", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(conn, fn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def test_tables_match_the_models_and_the_seed_is_off_with_one_recipient(tmp_path):
    eng = sa.create_engine(f"sqlite:///{(tmp_path / 'm.db').as_posix()}")
    tid, other, ulrg, ob = (str(uuid.uuid4()) for _ in range(4))
    with eng.begin() as conn:
        for ddl in PARENTS:
            conn.execute(sa.text(ddl))
        conn.execute(sa.text("INSERT INTO business VALUES (:b, :t, 'ulrg'), (:ob, :o, 'brokerage')"),
                     {"b": ulrg, "t": tid, "ob": ob, "o": other})
        conn.execute(sa.text("INSERT INTO lead_source_group VALUES (:g, :t, :b, 'realtor_com'), (:g2, :o, :ob, 'realtor_com')"),
                     {"g": str(uuid.uuid4()), "t": tid, "b": ulrg, "g2": str(uuid.uuid4()), "o": other, "ob": ob})
        conn.execute(sa.text('INSERT INTO "user" VALUES (:u, :t, \'Connor Leiva\', \'Connor@Example.com\')'),
                     {"u": str(uuid.uuid4()), "t": tid})
    mod = _module()
    assert mod.down_revision == "0105_lead_source_facts" and len(mod.revision) <= 30
    with eng.begin() as conn:
        _run(conn, mod.upgrade)
        _run(conn, mod.upgrade)
        insp = sa.inspect(conn)
        for model in (LeadSourceDigest, LeadSourceDigestDelivery):
            t = model.__table__
            got = {c["name"]: (bool(c["nullable"]), c["default"] is not None) for c in insp.get_columns(t.name)}
            want = {c.name: (c.nullable, c.server_default is not None) for c in t.columns}
            assert got == want, t.name
        rows = conn.execute(sa.text("SELECT tenant_id, is_active, recipients, clickup_list_id, model FROM lead_source_digest")).all()
        assert len(rows) == 1 and rows[0].tenant_id == tid and not rows[0].is_active
        assert rows[0].recipients == '["connor@example.com"]' and rows[0].clickup_list_id is None
        assert rows[0].model == "claude-sonnet-4-6"
        _run(conn, mod.downgrade)
        assert "lead_source_digest" not in sa.inspect(conn).get_table_names()
