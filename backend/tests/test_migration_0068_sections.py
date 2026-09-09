"""Migration 0068 — sections, lesson kinds, and an enrolment date.

The reason this file exists at all: the chain cannot be replayed from scratch on SQLite (0001 is a
`create_all` from live metadata, so every later `add_column` collides with a column that is already
there), which means `alembic upgrade head` on a fresh database does not exercise 0068's body. It is
exercised here instead, against a hand-built pre-0068 schema.

Most of these tests are about what must NOT happen. This migration runs against a workspace with
real courses in it, and the promise made to those courses is that they do not move: no sections, no
regrouping, the same flat lesson list the portal renders today.
"""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

MIGRATION = "alembic/versions/0068_course_sections.py"

LESSON_ADDED = {"section_id", "kind", "body_html", "word_count", "read_minutes", "page_count"}
COURSE_ADDED = {"grouping_scheme", "lock_sections"}
NEW_TABLES = ("intranet_course_section", "intranet_course_enrolment")


def _migration():
    path = Path(__file__).resolve().parents[1] / MIGRATION
    spec = importlib.util.spec_from_file_location("mig0068", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bound_op(conn):
    """Bind alembic's module-level `op` proxy to a live connection, the way a deploy does, so the
    body runs against the real Operations implementation rather than a stand-in."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    return Operations.context(MigrationContext.configure(conn))


def _rebuild_without(conn, name, dropped):
    """Recreate one table without the columns 0068 adds.

    Subtracted from the live metadata rather than pasted as DDL: a hand-written CREATE TABLE would
    drift away from the model the first time anyone touched it, and then this test would be
    checking a schema that no longer exists anywhere.
    """
    from app.models import Base

    src = Base.metadata.tables[name]
    meta = sa.MetaData()
    kept = sa.Table(name, meta, *[c._copy() for c in src.columns if c.name not in dropped])
    conn.execute(sa.text(f"DROP TABLE {name}"))
    conn.execute(sa.schema.CreateTable(kept))


def _pre_0068(conn):
    from app.models import Base

    Base.metadata.create_all(conn)
    for table in NEW_TABLES:
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
    _rebuild_without(conn, "intranet_lesson", LESSON_ADDED)
    _rebuild_without(conn, "intranet_course", COURSE_ADDED)


def _engine():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _pre_0068(conn)
    return engine


def _columns(conn, table):
    return {c["name"] for c in sa.inspect(conn).get_columns(table)}


def _tenant(conn, tid="t1"):
    """Only needed by the test that turns foreign keys on -- SQLite ignores them otherwise."""
    conn.execute(sa.text(
        "INSERT INTO tenant (id, slug, name) VALUES (:i, 'ulrg', 'Utah Life')"), {"i": tid})


def _course(conn, cid="c1", title="Onboarding"):
    conn.execute(sa.text(
        "INSERT INTO intranet_course (id, tenant_id, title, category, state, sort) "
        "VALUES (:i, 't1', :t, 'Onboarding', 'Live', 0)"), {"i": cid, "t": title})


def _lesson(conn, lid, source_type="LOOM", source_ref="https://loom.test/x", sort=0):
    conn.execute(sa.text(
        "INSERT INTO intranet_lesson (id, tenant_id, course_id, title, source_type, source_ref,"
        " sort) VALUES (:i, 't1', 'c1', :i, :st, :sr, :s)"),
        {"i": lid, "st": source_type, "sr": source_ref, "s": sort})


# ── the file itself ───────────────────────────────────────────────────────────────────────

def test_the_revision_id_fits_the_column_alembic_stamps_it_into():
    """alembic_version.version_num is VARCHAR(32). 0046 shipped at 34 and Postgres refused the
    stamp AFTER the body had run."""
    mod = _migration()
    assert len(mod.revision) <= 32, f"{mod.revision!r} is {len(mod.revision)} chars"
    assert mod.down_revision == "0067_member_agent_email", "chained from the wrong head"


# ── the body ──────────────────────────────────────────────────────────────────────────────

def test_it_creates_both_tables_and_every_column():
    mod = _migration()
    engine = _engine()
    with engine.begin() as conn:
        assert not (set(NEW_TABLES) & set(sa.inspect(conn).get_table_names())), "setup failed"
        with _bound_op(conn):
            mod.upgrade()
        after = set(sa.inspect(conn).get_table_names())
        assert set(NEW_TABLES) <= after
        assert LESSON_ADDED <= _columns(conn, "intranet_lesson")
        assert COURSE_ADDED <= _columns(conn, "intranet_course")


def test_running_it_twice_is_a_no_op():
    """A failed deploy gets retried, and every add here is guarded on the column already
    existing."""
    mod = _migration()
    engine = _engine()
    with engine.begin() as conn:
        with _bound_op(conn):
            mod.upgrade()
            mod.upgrade()          # must not raise


def test_the_downgrade_puts_the_schema_back():
    mod = _migration()
    engine = _engine()
    with engine.begin() as conn:
        before_lesson = _columns(conn, "intranet_lesson")
        with _bound_op(conn):
            mod.upgrade()
            mod.downgrade()
        assert not (set(NEW_TABLES) & set(sa.inspect(conn).get_table_names()))
        assert _columns(conn, "intranet_lesson") == before_lesson
        assert not (COURSE_ADDED & _columns(conn, "intranet_course"))


# ── what happens to the courses that are already there ────────────────────────────────────

def test_an_existing_course_does_not_move():
    """The promise of this migration. A workspace with courses in it must render exactly the same
    page the morning after: no sections, no grouping, the same flat list."""
    mod = _migration()
    engine = _engine()
    with engine.begin() as conn:
        _course(conn)
        _lesson(conn, "l1")
        _lesson(conn, "l2", sort=1)
        with _bound_op(conn):
            mod.upgrade()
        row = conn.execute(sa.text(
            "SELECT grouping_scheme, lock_sections FROM intranet_course")).one()
        assert row[0] == "none"
        assert not row[1]
        sections = conn.execute(sa.text(
            "SELECT count(*) FROM intranet_lesson WHERE section_id IS NOT NULL")).scalar()
        assert sections == 0, "no lesson may be put in a section by the migration"
        assert conn.execute(sa.text("SELECT count(*) FROM intranet_course_section")).scalar() == 0


@pytest.mark.parametrize("source_type,source_ref,expected", [
    ("PDF", "handbook.pdf", "document"),
    ("PDF", None, "document"),
    ("HERE", "https://cdn.test/Buyer-Packet.PDF", "document"),   # case, and a hosted file
    ("LOOM", "https://loom.test/abc", "video"),
    ("SKOOL", None, "video"),
    ("HERE", "https://cdn.test/walkthrough.mp4", "video"),
])
def test_kind_is_backfilled_from_the_evidence_not_the_host(source_type, source_ref, expected):
    """`source_type` says where a lesson lives, which is nearly but not quite the same question.
    A `.pdf` sitting on our own storage is a document even though its host is HERE."""
    mod = _migration()
    engine = _engine()
    with engine.begin() as conn:
        _course(conn)
        _lesson(conn, "l1", source_type=source_type, source_ref=source_ref)
        with _bound_op(conn):
            mod.upgrade()
        assert conn.execute(sa.text("SELECT kind FROM intranet_lesson")).scalar() == expected


def test_nothing_is_backfilled_to_reading():
    """A reading lesson is one that HAS a body, and no body exists yet. Guessing here would put
    lessons in the portal with a 'Reading' chip and a blank page behind it."""
    mod = _migration()
    engine = _engine()
    with engine.begin() as conn:
        _course(conn)
        for i, (st, ref) in enumerate([("PDF", "a.pdf"), ("LOOM", "b"), ("EXP", None)]):
            _lesson(conn, f"l{i}", source_type=st, source_ref=ref, sort=i)
        with _bound_op(conn):
            mod.upgrade()
        kinds = {r[0] for r in conn.execute(sa.text("SELECT kind FROM intranet_lesson"))}
        assert "reading" not in kinds


# ── the structural claims ─────────────────────────────────────────────────────────────────

def test_deleting_a_section_leaves_its_lessons_alone():
    """ON DELETE SET NULL, and the reason for it: a section is an arrangement of lessons, not a
    container that owns them. CASCADE here would mean an author tidying up their Day headings
    destroyed the course."""
    mod = _migration()
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys=ON"))
        _tenant(conn)
        _course(conn)
        _lesson(conn, "l1")
        with _bound_op(conn):
            mod.upgrade()
        conn.execute(sa.text(
            "INSERT INTO intranet_course_section (id, tenant_id, course_id, name, sort) "
            "VALUES ('s1', 't1', 'c1', 'Day 1', 0)"))
        conn.execute(sa.text("UPDATE intranet_lesson SET section_id = 's1'"))
        conn.execute(sa.text("DELETE FROM intranet_course_section WHERE id = 's1'"))
        row = conn.execute(sa.text("SELECT id, section_id FROM intranet_lesson")).one()
        assert row[0] == "l1", "the lesson went with the section"
        assert row[1] is None


def test_a_member_cannot_be_enrolled_in_the_same_course_twice():
    """The clock `day_n` counts from. A second row would restart it and put Day 1 after Day 4."""
    mod = _migration()
    engine = _engine()
    with engine.begin() as conn:
        with _bound_op(conn):
            mod.upgrade()
        insert = sa.text(
            "INSERT INTO intranet_course_enrolment (id, tenant_id, user_id, course_id) "
            "VALUES (:i, 't1', 'u1', 'c1')")
        conn.execute(insert, {"i": "e1"})
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(insert, {"i": "e2"})


# ── the wiring that is derived rather than declared ───────────────────────────────────────

def test_a_section_joins_the_publish_cycle_without_being_added_to_a_list():
    """`_publishable_models()` derives its list from the mapper registry by looking for
    tenant_id + published_at + draft_dirty. A section drafted in the console must not appear in
    the portal before somebody presses Publish, and it gets that by having those three columns."""
    from app.models import IntranetCourseSection
    from app.routers.console import _publishable_models

    assert IntranetCourseSection in _publishable_models()


def test_an_enrolment_is_not_publishable():
    """It is a fact about a member, not authored content. Sweeping it into publish would mean
    pressing Publish rewrote people's start dates."""
    from app.models import IntranetCourseEnrolment
    from app.routers.console import _publishable_models

    assert IntranetCourseEnrolment not in _publishable_models()
