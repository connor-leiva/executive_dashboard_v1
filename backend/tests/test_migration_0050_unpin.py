"""Migration 0050 — un-pin one customer's brand from workspaces it was never theirs to be on.

0049 grandfathered the compiled-in palette onto every existing workspace, on the reasoning that
anything present at that moment was rendering those values so pinning them preserved what users
saw. True, and the wrong conclusion: what the OTHER workspaces saw was the defect being fixed.
Grandfathering is right when the current behaviour is correct for that row, and here it was
correct for exactly one row.

The repair has its own failure mode, which is why most of these tests are about restraint: a
migration that clears borrowed values must not also clear chosen ones.
"""
import json

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

MIGRATION = "alembic/versions/0050_unpin_borrowed_brand.py"


def _module():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / MIGRATION
    spec = importlib.util.spec_from_file_location("mig0050", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _table(conn):
    conn.execute(sa.text(
        "CREATE TABLE tenant (id TEXT PRIMARY KEY, slug TEXT, config TEXT, created_at TEXT)"))


def _insert(conn, tid, slug, created, brand):
    conn.execute(sa.text("INSERT INTO tenant VALUES (:i,:s,:c,:t)"),
                 {"i": tid, "s": slug, "c": json.dumps({"brand": brand}), "t": created})


@pytest.mark.parametrize("is_pg", [True, False])
def test_the_update_binds_every_parameter_it_names(is_pg):
    mod = _module()
    dialect = postgresql.dialect(paramstyle="pyformat") if is_pg else sa.dialects.sqlite.dialect()
    compiled = sa.text(mod.update_sql(is_pg)).compile(dialect=dialect)
    assert sorted(compiled.params) == ["cfg", "id"], f"rendered as {compiled!s}"


async def test_the_oldest_workspace_keeps_its_brand_and_gains_its_hero_plates():
    """It is the one the brand actually belongs to — it predates multi-tenancy. Identified by age
    rather than by slug so the migration does not write a customer's name into schema history."""
    mod = _module()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _table(conn)
        _insert(conn, "t1", "springb", "2026-07-01",
                {"palette": dict(mod.LEGACY_PALETTE), "type": dict(mod.LEGACY_TYPE),
                 **mod.LEGACY_LOGIN})
        _insert(conn, "t2", "acme", "2026-08-26",
                {"palette": dict(mod.LEGACY_PALETTE), "type": dict(mod.LEGACY_TYPE),
                 **mod.LEGACY_LOGIN})
        import alembic.op
        from unittest.mock import patch
        with patch.object(alembic.op, "get_bind", return_value=conn):
            mod.upgrade()
        got = {r[0]: json.loads(r[1])["brand"] for r in
               conn.execute(sa.text("SELECT id, config FROM tenant")).fetchall()}

    assert got["t1"]["palette"] == mod.LEGACY_PALETTE, "took the brand off the workspace it belongs to"
    assert got["t1"]["hero_image"] == mod.LEGACY_LOGIN["hero_image"]
    assert got["t1"]["hero_plates"] == mod.HERO_PLATES, "its own gradients were not restored"


async def test_every_other_workspace_is_returned_to_the_platform_identity():
    """This is what the owner actually saw: a workspace at its own address rendering Acumyn for a
    moment, then flipping to another customer's colours and photograph once /public/brand answered."""
    mod = _module()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _table(conn)
        _insert(conn, "t1", "springb", "2026-07-01", {"palette": dict(mod.LEGACY_PALETTE)})
        for tid, slug, when in [("t2", "acme", "2026-08-26"), ("t3", "testrealty", "2026-08-30")]:
            _insert(conn, tid, slug, when,
                    {"palette": dict(mod.LEGACY_PALETTE), "type": dict(mod.LEGACY_TYPE),
                     **mod.LEGACY_LOGIN})
        import alembic.op
        from unittest.mock import patch
        with patch.object(alembic.op, "get_bind", return_value=conn):
            mod.upgrade()
        got = {r[0]: json.loads(r[1])["brand"] for r in
               conn.execute(sa.text("SELECT id, config FROM tenant")).fetchall()}

    for tid in ("t2", "t3"):
        assert got[tid]["palette"] == {}, f"{tid} still carries a borrowed palette"
        assert got[tid]["type"] == {}
        assert got[tid]["hero_image"] is None, f"{tid} still shows another customer's photograph"
        assert got[tid]["photo"] is None


async def test_a_workspace_that_chose_its_own_colours_is_left_alone():
    """THE RESTRAINT THAT MATTERS. Values are compared against 0049's literals before removal, so
    a deliberate choice survives. A repair that also discards somebody's decision is a second bug
    wearing the first one's clothes."""
    mod = _module()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _table(conn)
        _insert(conn, "t1", "springb", "2026-07-01", {"palette": dict(mod.LEGACY_PALETTE)})
        _insert(conn, "t2", "chose", "2026-08-26",
                {"palette": {"ink": "#101820", "poppy": "#B5651D"},
                 "type": {"display": "Georgia"},
                 "hero_image": "/uploads/their-own.jpg", "photo": None})
        import alembic.op
        from unittest.mock import patch
        with patch.object(alembic.op, "get_bind", return_value=conn):
            mod.upgrade()
        got = {r[0]: json.loads(r[1])["brand"] for r in
               conn.execute(sa.text("SELECT id, config FROM tenant")).fetchall()}

    assert got["t2"]["palette"] == {"ink": "#101820", "poppy": "#B5651D"}
    assert got["t2"]["type"] == {"display": "Georgia"}
    assert got["t2"]["hero_image"] == "/uploads/their-own.jpg"


async def test_running_it_twice_changes_nothing_the_second_time():
    mod = _module()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _table(conn)
        _insert(conn, "t1", "springb", "2026-07-01", {"palette": dict(mod.LEGACY_PALETTE)})
        _insert(conn, "t2", "acme", "2026-08-26", {"palette": dict(mod.LEGACY_PALETTE)})
        import alembic.op
        from unittest.mock import patch
        with patch.object(alembic.op, "get_bind", return_value=conn):
            mod.upgrade()
            first = conn.execute(sa.text("SELECT config FROM tenant ORDER BY id")).fetchall()
            mod.upgrade()
            second = conn.execute(sa.text("SELECT config FROM tenant ORDER BY id")).fetchall()
    assert [json.loads(r[0]) for r in first] == [json.loads(r[0]) for r in second]


def test_the_platform_default_is_not_derived_from_a_customer_asset():
    """The hero the platform ships must be Acumyn's own artwork.

    An earlier pass desaturated one customer's ribbed gradient and made it the default for every
    workspace. It was measurably the same artwork in eight tints, so collapsing them was tidy —
    and desaturating somebody's brand asset does not make it yours. The eight files are hers,
    addressed as her configuration; the platform ships bokeh plates of its own.
    """
    from pathlib import Path

    brand = Path(__file__).resolve().parents[2] / "frontend" / "public" / "brand"
    assert not (brand / "RibbedGradient.jpg").exists(), (
        "the desaturated derivative of a customer's asset is back as a platform default")
    for plate in ("bokeh-light.jpg", "bokeh-ink.jpg"):
        assert (brand / "acumyn" / plate).exists(), f"Acumyn's own {plate} is missing"
    # And her originals must still be present, since her config now points at them.
    for tint in ("Evergreen", "Meadow", "Petal"):
        assert (brand / f"RibbedGradient_{tint}.jpg").exists(), f"{tint} was not restored"
