"""Migration 0079 — carry the two stored spellings of the old name across to Axcion.

Both of these values fail QUIETLY when left behind: a stale typeface falls through to the
default pairing, and a stale support address makes the console mint a second account beside
the first. Neither raises at migration time, so the only way to know the migration did its
job is to assert it.

A third was planned and removed. The Win-the-Day playbook's `format` lives in the export
envelope, never in the stored row, so a migration for it would have matched zero rows for
ever. The real exposure there is a file exported before the rename, and it is covered in
test_wtd_playbook.py where the importer is.

The restraint tests matter as much as the rename tests. A workspace that chose "classic" must
still be on "classic" afterwards, and an ordinary user whose address happens to contain the
term must not be re-addressed as though it were a support account.
"""
import json

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

MIGRATION = "alembic/versions/0079_rebrand_stored_names.py"


def _module():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / MIGRATION
    spec = importlib.util.spec_from_file_location("mig0079", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _schema(conn):
    conn.execute(sa.text("CREATE TABLE tenant (id TEXT PRIMARY KEY, slug TEXT, config TEXT)"))
    conn.execute(sa.text(
        'CREATE TABLE "user" (id TEXT PRIMARY KEY, tenant_id TEXT, email TEXT, name TEXT)'))


def _tenant(conn, tid, slug, brand):
    conn.execute(sa.text("INSERT INTO tenant VALUES (:i,:s,:c)"),
                 {"i": tid, "s": slug, "c": json.dumps({"brand": brand} if brand else {})})


def _user(conn, uid, tid, email, name):
    conn.execute(sa.text('INSERT INTO "user" VALUES (:i,:t,:e,:n)'),
                 {"i": uid, "t": tid, "e": email, "n": name})


def _typeface(conn, tid):
    raw = conn.execute(sa.text("SELECT config FROM tenant WHERE id = :i"), {"i": tid}).scalar_one()
    return (json.loads(raw).get("brand") or {}).get("typeface")


@pytest.mark.parametrize("is_pg", [True, False])
@pytest.mark.parametrize("table,column", [("tenant", "config")])
def test_every_json_update_binds_the_parameters_it_names(is_pg, table, column):
    """The jsonb CAST is the part that silently does not bind: `:val::jsonb` renders as a cast
    of a literal and loses the parameter. 0046 learned this the hard way; this keeps it learned."""
    mod = _module()
    dialect = postgresql.dialect(paramstyle="pyformat") if is_pg else sa.dialects.sqlite.dialect()
    compiled = sa.text(mod._set_json(table, column, is_pg)).compile(dialect=dialect)
    assert sorted(compiled.params) == ["id", "val"], f"rendered as {compiled!s}"


def test_the_stored_typeface_moves_and_a_chosen_one_is_left_alone():
    mod = _module()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _schema(conn)
        _tenant(conn, "t1", "testrealty", {"typeface": "acumyn"})
        _tenant(conn, "t2", "springb", {"typeface": "classic"})
        _tenant(conn, "t3", "utah-life", {})                    # brand, but no typeface
        _tenant(conn, "t4", "acmerealty", None)                 # no brand at all
        assert mod._swap_typeface(conn, False, mod.OLD_TYPEFACE, mod.NEW_TYPEFACE) == 1
        assert _typeface(conn, "t1") == "axcion"
        assert _typeface(conn, "t2") == "classic", "a chosen typeface is not the rename's business"
        assert _typeface(conn, "t3") is None, "an absent typeface stays absent, not defaulted"
        assert _typeface(conn, "t4") is None


def test_a_tenant_with_no_typeface_is_not_given_one():
    """It resolves through the new default at read time. Writing one in would turn an absence
    -- which means "whatever the platform default is" -- into a choice this workspace never made."""
    mod = _module()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _schema(conn)
        _tenant(conn, "t1", "utah-life", {"seeds": {"brand": "#123456"}})
        mod._swap_typeface(conn, False, mod.OLD_TYPEFACE, mod.NEW_TYPEFACE)
        cfg = json.loads(conn.execute(sa.text("SELECT config FROM tenant")).scalar_one())
        assert "typeface" not in cfg["brand"]
        assert cfg["brand"]["seeds"] == {"brand": "#123456"}, "the rest of brand is untouched"



def test_the_support_account_is_re_addressed_and_renamed():
    mod = _module()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _schema(conn)
        _user(conn, "u1", "t1", "connor.leiva+acumyn-support@gmail.com",
              "Connor Leiva (Acumyn support)")
        assert mod._swap_support_accounts(
            conn, mod.OLD_TAG, mod.NEW_TAG, mod.OLD_SUFFIX, mod.NEW_SUFFIX) == 1
        email, name = conn.execute(sa.text('SELECT email, name FROM "user"')).one()
        assert email == "connor.leiva+axcion-support@gmail.com"
        assert name == "Connor Leiva (Axcion support)"


def test_an_ordinary_account_that_merely_mentions_the_old_name_is_untouched():
    """The LIKE pattern is the tagged address, not the bare word. Someone at the old company
    domain, or a person whose name contains it, is not a support account."""
    mod = _module()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _schema(conn)
        _user(conn, "u1", "t1", "someone@acumyn.io", "Someone at Acumyn")
        assert mod._swap_support_accounts(
            conn, mod.OLD_TAG, mod.NEW_TAG, mod.OLD_SUFFIX, mod.NEW_SUFFIX) == 0
        email, name = conn.execute(sa.text('SELECT email, name FROM "user"')).one()
        assert (email, name) == ("someone@acumyn.io", "Someone at Acumyn")


def test_a_collision_skips_that_row_instead_of_failing_the_whole_migration():
    """(tenant_id, email) is unique. An integrity error here would roll back the typeface work
    for a collision that is almost certainly an account that has already been re-addressed."""
    mod = _module()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _schema(conn)
        _user(conn, "u1", "t1", "c+acumyn-support@x.com", "C (Acumyn support)")
        _user(conn, "u2", "t1", "c+axcion-support@x.com", "C (Axcion support)")
        assert mod._swap_support_accounts(
            conn, mod.OLD_TAG, mod.NEW_TAG, mod.OLD_SUFFIX, mod.NEW_SUFFIX) == 0
        assert conn.execute(sa.text(
            'SELECT email FROM "user" WHERE id = :i'), {"i": "u1"}).scalar_one() \
            == "c+acumyn-support@x.com", "left alone rather than clobbered"


def test_the_same_address_in_a_different_workspace_is_not_a_collision():
    """Uniqueness is per workspace, so one operator's support account in two workspaces must
    both move. Checking the address globally would strand the second."""
    mod = _module()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _schema(conn)
        _user(conn, "u1", "t1", "c+acumyn-support@x.com", "C (Acumyn support)")
        _user(conn, "u2", "t2", "c+acumyn-support@x.com", "C (Acumyn support)")
        assert mod._swap_support_accounts(
            conn, mod.OLD_TAG, mod.NEW_TAG, mod.OLD_SUFFIX, mod.NEW_SUFFIX) == 2


def test_the_migration_round_trips():
    """Upgrade then downgrade lands exactly where it started. The spec rehearses this against a
    restored production copy, and a downgrade that has never run is not a rollback plan."""
    mod = _module()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _schema(conn)
        _tenant(conn, "t1", "testrealty", {"typeface": "acumyn"})
        _user(conn, "u1", "t1", "c+acumyn-support@x.com", "C (Acumyn support)")
        before = conn.execute(sa.text(
            'SELECT (SELECT config FROM tenant), '
            '(SELECT email || "|" || name FROM "user")')).one()

        mod._swap_typeface(conn, False, mod.OLD_TYPEFACE, mod.NEW_TYPEFACE)
        mod._swap_support_accounts(conn, mod.OLD_TAG, mod.NEW_TAG,
                                   mod.OLD_SUFFIX, mod.NEW_SUFFIX)
        assert _typeface(conn, "t1") == "axcion"

        mod._swap_typeface(conn, False, mod.NEW_TYPEFACE, mod.OLD_TYPEFACE)
        mod._swap_support_accounts(conn, mod.NEW_TAG, mod.OLD_TAG,
                                   mod.NEW_SUFFIX, mod.OLD_SUFFIX)
        after = conn.execute(sa.text(
            'SELECT (SELECT config FROM tenant), '
            '(SELECT email || "|" || name FROM "user")')).one()
        assert after == before
