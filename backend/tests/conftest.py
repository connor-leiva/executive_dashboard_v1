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

# Start each suite run from a clean file. seed() recreates the schema per module,
# but a leftover file from a prior run could otherwise carry rows across runs.
for _p in (_TEST_DB,
           _TEST_DB.with_name(_TEST_DB.name + "-wal"),
           _TEST_DB.with_name(_TEST_DB.name + "-shm")):
    try:
        _p.unlink()
    except FileNotFoundError:
        pass
