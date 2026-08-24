"""Test isolation.

Run the entire suite against a dedicated SQLite file so it never touches — or
pollutes — the local dev database (command_center.db). Previously tests seeded and
created users straight into the dev DB, so every `pytest` run left @x.com accounts
and invited users on the running app's Team page.

This must set DATABASE_URL *before* app.config builds its settings singleton, so it
lives at import time in conftest (pytest imports conftest before any test module,
and env vars take precedence over the .env file in pydantic-settings).
"""
import os
import pathlib

_TEST_DB = pathlib.Path(__file__).parent / "test_command_center.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB.as_posix()}"
# Never let the test suite reach the live Claude API (backend/.env carries a real key that
# pydantic-settings would otherwise load). Tests that exercise Claude paths monkeypatch a fake
# key + mock the network seam; everything else (e.g. post-upload background extraction) no-ops.
os.environ["ANTHROPIC_API_KEY"] = ""

# Start each suite run from a clean file. seed() recreates the schema per module,
# but a leftover file from a prior run could otherwise carry rows across runs.
for _p in (_TEST_DB,
           _TEST_DB.with_name(_TEST_DB.name + "-wal"),
           _TEST_DB.with_name(_TEST_DB.name + "-shm")):
    try:
        _p.unlink()
    except FileNotFoundError:
        pass


def binder_headers(token: str) -> dict:
    """Auth headers PLUS a live Binder step-up grant.

    Binder sits behind a second factor (see deps.require_tab_with_step_up). Enforcement of
    that gate is covered by test_totp_stepup.py; every other Binder test is about what lives
    BEHIND the gate, so it mints the grant directly instead of re-running enrollment.
    """
    from app.security import read_token, make_capability
    payload = read_token(token)
    grant = make_capability("stepup:binder", minutes=20,
                            sub=payload["sub"], ver=int(payload.get("ver", 0)))
    return {"Authorization": f"Bearer {token}", "X-Step-Up": grant}


import gc

import pytest

from app.db import engine


@pytest.fixture(autouse=True)
async def _no_connection_outlives_its_event_loop():
    """Dispose the engine's pool after every test.

    `asyncio_mode = "auto"` gives each test its OWN event loop, but `app.db.engine` is a
    module-level global created once at import. A connection opened on one test's loop and
    handed to the next is undefined behaviour with aiosqlite: it shows up as SQLAlchemy's
    "garbage collector is trying to clean up non-checked-in connection" warning, and once a
    run allocates enough for the GC to fire mid-query it becomes a hard interpreter access
    violation part-way through the suite — not a test failure, a crash with no attribution.

    Disposing per test costs nothing on SQLite (see the NullPool note in app/db.py) and makes
    the suite deterministic. The collect afterwards drains the finalizers while the loop that
    created them is still alive, which is the half that actually prevents the crash.
    """
    yield
    await engine.dispose()
    gc.collect()
