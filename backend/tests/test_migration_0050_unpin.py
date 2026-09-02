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


# ── 0052: restoring what the appearance panel overwrote ──────────────────────────────────
def _mod52():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0052_restore_lost_palette.py"
    spec = importlib.util.spec_from_file_location("mig0052", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_it_restores_a_palette_the_settings_panel_overwrote():
    """The panel opened on the PLATFORM's five colours rather than the workspace's own, so Save
    replaced thirty hand-built tokens with Cadet. A settings page that redecorates the product
    when you press Save is the one thing a settings page must never do."""
    mod = _mod52()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _table(conn)
        _insert(conn, "t1", "springb", "2026-07-01",
                {"palette": {}, "seeds": dict(mod.PLATFORM_SEEDS), "typeface": "classic",
                 "logo": "/api/v1/public/brand/logo?v=abc"})
        import alembic.op
        from unittest.mock import patch
        with patch.object(alembic.op, "get_bind", return_value=conn):
            mod.upgrade()
        brand = json.loads(conn.execute(sa.text("SELECT config FROM tenant")).scalar_one())["brand"]

    assert brand["palette"] == mod.LEGACY_PALETTE
    # The seeds now describe HER palette, so the panel opens on her colours rather than Acumyn's.
    assert brand["seeds"]["brand"] == "#FA8069"      # her coral, not Cadet
    assert brand["seeds"]["ink"] == "#002E2C"        # her evergreen
    assert brand["typeface"] == "classic", "an unrelated setting was disturbed"
    # The workspace has to be in the PATH. A CSS mask sends no custom headers, so a route that
    # identifies the workspace from one can never serve the thing it exists to serve.
    assert brand["logo"] == "/public/brand/springb/logo?v=abc"


async def test_it_refuses_to_overwrite_colours_somebody_actually_chose():
    """The restraint that matters. Restoring an old palette over a real decision would be this
    migration repeating the mistake it exists to undo."""
    mod = _mod52()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _table(conn)
        _insert(conn, "t1", "springb", "2026-07-01",
                {"palette": {}, "seeds": {"brand": "#123456", "surface": "#FFFFFF",
                                          "ink": "#000000", "positive": "#008000",
                                          "negative": "#800000"}})
        import alembic.op
        from unittest.mock import patch
        with patch.object(alembic.op, "get_bind", return_value=conn):
            mod.upgrade()
        brand = json.loads(conn.execute(sa.text("SELECT config FROM tenant")).scalar_one())["brand"]

    assert brand["palette"] == {}, "clobbered a workspace's own choice"
    assert brand["seeds"]["brand"] == "#123456"


async def test_it_leaves_a_workspace_that_still_has_its_palette_alone():
    mod = _mod52()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _table(conn)
        _insert(conn, "t1", "springb", "2026-07-01", {"palette": {"ink": "#111111"}})
        import alembic.op
        from unittest.mock import patch
        with patch.object(alembic.op, "get_bind", return_value=conn):
            mod.upgrade()
        brand = json.loads(conn.execute(sa.text("SELECT config FROM tenant")).scalar_one())["brand"]
    assert brand["palette"] == {"ink": "#111111"}


def test_a_mark_url_carries_the_workspace_because_an_image_request_cannot():
    """THE BUG THIS SHAPE EXISTS FOR.

    Every other route identifies the workspace from the X-Tenant-Host header the SPA sends. A
    mark is fetched by the BROWSER as an image — a CSS mask, an <img> — and those requests carry
    no custom headers, so the API saw only the platform host, which deliberately resolves to no
    workspace, and answered 404 to every one.

    It failed silently twice over: a CSS mask that cannot load renders nothing at all, and
    verifying the route with a curl that DID send the header made it look correct. The stored
    path is the only place the workspace can travel.
    """
    from pathlib import Path

    mod = _mod52()
    brand = {"logo": "/public/brand/logo?v=1", "logomark": "/public/brand/acme/logomark?v=2"}
    mod._normalise_marks(brand, "acme")
    assert brand["logo"] == "/public/brand/acme/logo?v=1", "the workspace was not added"
    assert brand["logomark"] == "/public/brand/acme/logomark?v=2", "an already-correct URL doubled"

    # ...and the route itself must take the workspace from the path, never from a header.
    src = (Path(__file__).resolve().parents[1] / "app" / "routers" / "auth.py").read_text(
        encoding="utf-8")
    head = src.index('@router.get("/public/brand/{slug}/{kind}")')
    nxt = src.index("@router.", head + 20)          # past this route's own decorator
    route = src[head:nxt]
    assert "current_tenant_id" not in route, (
        "the asset route reads the workspace from a header again — an image request has none")
    assert "Tenant.slug == slug" in route
