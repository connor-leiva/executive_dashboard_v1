"""Migration 0104 -- lead-source groups, spend rules, cost shares, and the Utah Life seed.

Modelled on test_migration_0096_alloc.py: the three new tables must EQUAL the models (names,
nullability, server defaults), the two book_txn columns must appear and come back out, it runs
twice each way, and the seed writes Utah Life's rows for a `ulrg` business only -- once.
"""
import importlib.util
import re
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models import LeadSourceCostShare, LeadSourceGroup, LeadSourceSpendRule

VERSIONS = Path(__file__).resolve().parents[1] / "alembic/versions"
MIGRATION = VERSIONS / "0104_lead_source_groups.py"
NEW_TABLES = (LeadSourceGroup, LeadSourceSpendRule, LeadSourceCostShare)

PARENTS = (
    "CREATE TABLE tenant (id CHAR(36) PRIMARY KEY)",
    "CREATE TABLE business (id CHAR(36) PRIMARY KEY, tenant_id CHAR(36), key VARCHAR(32))",
    "CREATE TABLE book_txn (id CHAR(36) PRIMARY KEY)",
)


def _module():
    spec = importlib.util.spec_from_file_location("mig0104", MIGRATION)
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


def _add_business(conn, tid, key):
    bid = str(uuid.uuid4())
    conn.execute(sa.text("INSERT INTO business (id, tenant_id, key) VALUES (:i, :t, :k)"),
                 {"i": bid, "t": tid, "k": key})
    return bid


def test_it_creates_what_the_models_describe_and_comes_back_out(tmp_path):
    eng = _engine(tmp_path)
    mod = _module()
    assert mod.down_revision == "0103_sb_round2"
    with eng.begin() as conn:
        _run(conn, mod.upgrade)
        _run(conn, mod.upgrade)
        insp = sa.inspect(conn)
        for model in NEW_TABLES:
            t = model.__table__
            got = {c["name"]: (bool(c["nullable"]), c["default"] is not None)
                   for c in insp.get_columns(t.name)}
            want = {c.name: (c.nullable, c.server_default is not None) for c in t.columns}
            assert got == want, (t.name, {k: (got.get(k), want.get(k)) for k in set(got) | set(want)
                                          if got.get(k) != want.get(k)})
            want_ix = {f"ix_{t.name}_{c.name}" for c in t.columns if c.index}
            assert want_ix <= {i["name"] for i in insp.get_indexes(t.name)}, t.name
        assert {"vendor_qbo_id", "purchase_credit"} <= {c["name"] for c in insp.get_columns("book_txn")}
        uq = {u["name"]: u["column_names"] for u in insp.get_unique_constraints("lead_source_group")}
        assert uq["uq_lead_source_group_key"] == ["tenant_id", "business_id", "key"]

        _run(conn, mod.downgrade)
        _run(conn, mod.downgrade)
        insp = sa.inspect(conn)
        assert not {m.__table__.name for m in NEW_TABLES} & set(insp.get_table_names())
        assert not {"vendor_qbo_id", "purchase_credit"} & {c["name"] for c in insp.get_columns("book_txn")}


def test_the_seed_writes_utah_lifes_rows_once_and_nobody_elses(tmp_path):
    eng = _engine(tmp_path, "seed.db")
    mod = _module()
    with eng.begin() as conn:
        spring, other = str(uuid.uuid4()), str(uuid.uuid4())
        ulrg = _add_business(conn, spring, "ulrg")
        sympli = _add_business(conn, spring, "sympli")
        _add_business(conn, other, "brokerage")              # a second tenant: no ulrg business
        _run(conn, mod.upgrade)
        _run(conn, mod.upgrade)                              # the seed is idempotent

        groups = conn.execute(sa.text(
            "SELECT tenant_id, business_id, key, is_active, program_start, match_mode, "
            "exp_risk_team_default, exp_risk_agent_default FROM lead_source_group ORDER BY key")).all()
        assert [(g.tenant_id, g.business_id, g.key, bool(g.is_active)) for g in groups] == [
            (spring, ulrg, "realtor_com", True), (spring, ulrg, "realty_com", False)]
        assert str(groups[0].program_start) == "2026-02-01" and groups[0].match_mode == "contains"
        assert float(groups[0].exp_risk_team_default) == 69 and float(groups[0].exp_risk_agent_default) == 49
        rules = conn.execute(sa.text(
            "SELECT business_id, account_qbo_id, vendor_qbo_id FROM lead_source_spend_rule")).all()
        assert {(r.account_qbo_id, r.vendor_qbo_id) for r in rules} == {
            ("365", None), (None, "962"), (None, "1568")}
        assert len(rules) == 3 and all(r.business_id == ulrg for r in rules)
        shares = conn.execute(sa.text(
            "SELECT bearer_business_id, share_pct FROM lead_source_cost_share")).all()
        assert [(s.bearer_business_id, float(s.share_pct)) for s in shares] == [(sympli, 50.0)]


def test_the_revision_id_fits_the_house_margin_and_one_file():
    mod = _module()
    assert len(mod.revision) <= 30 and re.fullmatch(r"0104_[a-z0-9_]+", mod.revision)
    # One file declares this revision. Not "one 0104 file": the ads work landed its own
    # 0104_sb_title_case beside this one, which is two files and no conflict -- the merge
    # revision joins the branches, and the single-head test guards the graph.
    declared = re.compile(r'^revision(?::\s*str)?\s*=\s*"([^"]+)"', re.M)
    owners = [f.name for f in VERSIONS.glob("*.py")
              if mod.revision in declared.findall(f.read_text(encoding="utf-8"))]
    assert owners == [MIGRATION.name]
    code = MIGRATION.read_text(encoding="utf-8").split('"""', 2)[2]
    for bad in ("sa.Enum", "postgresql.ENUM", "JSONB", "PG_UUID"):
        assert bad not in code, bad
