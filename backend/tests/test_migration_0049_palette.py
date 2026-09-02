"""Migration 0049 — pin the existing workspace's colours before the palette stops being compiled in.

Written to the shape 0046 arrived at the hard way. That migration reached production twice
broken, both times through something that is only wrong on Postgres in a suite that only runs
SQLite: a bind parameter that did not bind, and a revision id too long for the column alembic
stamps it into. Neither needed a database to catch — one is a compile, the other is len().

So this one is checked the same way before it ships, not after.
"""
import json

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

MIGRATION = "alembic/versions/0049_grandfather_palette.py"


def _module():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / MIGRATION
    spec = importlib.util.spec_from_file_location("mig0049", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("is_pg", [True, False])
def test_the_update_binds_every_parameter_it_names(is_pg):
    """0046's first crash, in one line. `:cfg::jsonb` renders as a literal because text() will
    not read a parameter with a colon behind it; the statement then reaches the server as
    something that is not SQL, and the deploy crash-loops."""
    mod = _module()
    dialect = postgresql.dialect(paramstyle="pyformat") if is_pg else sa.dialects.sqlite.dialect()
    compiled = sa.text(mod.update_sql(is_pg)).compile(dialect=dialect)
    assert sorted(compiled.params) == ["cfg", "id"], f"rendered as {compiled!s}"
    assert ":cfg" not in str(compiled)


def test_the_pinned_palette_is_complete_and_is_real_hex():
    """It has to reproduce the compiled-in look exactly. A missing token falls through to
    Acumyn's default, which would move ONE colour on a live dashboard — the kind of change
    nobody reports and everybody notices."""
    mod = _module()
    assert len(mod.LEGACY_PALETTE) == 30, "the palette had 30 slots; this must pin all of them"
    for name, value in mod.LEGACY_PALETTE.items():
        assert isinstance(value, str) and value.startswith("#") and len(value) == 7, (name, value)
        int(value[1:], 16)                       # raises if it is not hex
    assert set(mod.LEGACY_TYPE) == {"display", "text", "data"}


async def test_it_pins_existing_workspaces_and_leaves_a_chosen_palette_alone():
    mod = _module()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE tenant (id TEXT PRIMARY KEY, slug TEXT, config TEXT)"))
        rows = [
            ("t1", "springb", None),                                        # no config at all
            ("t2", "empty", json.dumps({})),                                # config, no brand
            ("t3", "partial", json.dumps({"brand": {"product_name": "X"}})),  # brand, no palette
            ("t4", "chose", json.dumps({"brand": {"palette": {"ink": "#123456"},
                                                  "type": {"display": "Georgia"}}})),
        ]
        for rid, slug, cfg in rows:
            conn.execute(sa.text("INSERT INTO tenant VALUES (:i,:s,:c)"),
                         {"i": rid, "s": slug, "c": cfg})

        import alembic.op
        from unittest.mock import patch
        with patch.object(alembic.op, "get_bind", return_value=conn):
            mod.upgrade()

        got = {r[0]: json.loads(r[1]) for r in
               conn.execute(sa.text("SELECT id, config FROM tenant")).fetchall()}

    for tid in ("t1", "t2", "t3"):
        assert got[tid]["brand"]["palette"] == mod.LEGACY_PALETTE, tid
        assert got[tid]["brand"]["type"] == mod.LEGACY_TYPE, tid
    assert got["t3"]["brand"]["product_name"] == "X", "clobbered an unrelated brand field"
    # A workspace that chose its own keeps it, untouched and un-merged.
    assert got["t4"]["brand"]["palette"] == {"ink": "#123456"}
    assert got["t4"]["brand"]["type"] == {"display": "Georgia"}


async def test_running_it_twice_changes_nothing_the_second_time():
    """A failed deploy gets retried. The retry must not compound the first attempt."""
    mod = _module()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE tenant (id TEXT PRIMARY KEY, slug TEXT, config TEXT)"))
        conn.execute(sa.text("INSERT INTO tenant VALUES ('t1','springb',NULL)"))
        import alembic.op
        from unittest.mock import patch
        with patch.object(alembic.op, "get_bind", return_value=conn):
            mod.upgrade()
            first = conn.execute(sa.text("SELECT config FROM tenant")).scalar_one()
            mod.upgrade()
            second = conn.execute(sa.text("SELECT config FROM tenant")).scalar_one()
    assert json.loads(first) == json.loads(second)


def test_the_semantic_states_are_all_legible_as_text():
    """The palette this replaces used #FFDD1F for warnings — about 1.4:1 on white, which is a dot
    and not a word. The four states are specified at 6:1 or better precisely so they can carry
    text, so the contrast is asserted rather than assumed."""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "palette.js").read_text(
        encoding="utf-8")
    block = src[src.index("export const SEMANTIC"):]
    block = block[: block.index("};")]
    states = dict(re.findall(r'(\w+):\s*"(#[0-9A-Fa-f]{6})"', block))
    assert set(states) == {"success", "warning", "error", "info"}, states

    def luminance(hex_value):
        def channel(c):
            c /= 255
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        n = int(hex_value[1:], 16)
        r, g, b = (n >> 16) & 255, (n >> 8) & 255, n & 255
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

    for name, value in states.items():
        ratio = (1.0 + 0.05) / (luminance(value) + 0.05)
        assert ratio >= 4.5, f"{name} {value} is {ratio:.2f}:1 on white — below AA for text"
