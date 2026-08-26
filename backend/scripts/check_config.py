"""Check a deployment's configuration WITHOUT starting the app.

The point is to answer "would the startup guard let this boot?" before shipping a release that
enforces it. Running it inside the target environment is the whole idea:

    railway ssh --service executive_dashboard_v1 "python -m scripts.check_config"

Exit codes: 0 = would start, 1 = would refuse. Never prints a secret's value — only whether it
is absent, still the committed default, or set to something of its own.
"""
from __future__ import annotations

import sys

from app.config import settings
from app.startup_checks import is_deployed, validate_config


def _state(name: str) -> str:
    """Describe a secret without disclosing it."""
    from app.startup_checks import _committed_default

    value = (getattr(settings, name, "") or "").strip()
    if not value:
        return "NOT SET"
    if value == _committed_default(name):
        return "the repository's default  <-- not a secret"
    return f"set, {len(value)} chars"


def main() -> int:
    fatal, warn = validate_config()

    url = settings.DATABASE_URL
    db = "SQLite (local)" if url.startswith("sqlite") else "Postgres"
    try:
        db = f"Postgres {url.split('://', 1)[1].split('@', 1)[1]}" if db == "Postgres" else db
    except (IndexError, ValueError):
        pass

    print(f"\n  database        {db}")
    print(f"  ENV             {settings.ENV}")
    print(f"  enforced here   {'YES - this is a reachable deployment' if is_deployed() else 'no - treated as local development'}")
    print(f"  APP_SECRET      {_state('APP_SECRET')}")
    print(f"  FERNET_KEY      {_state('FERNET_KEY')}\n")

    for w in warn:
        print(f"  [warning] {w}")
    if warn:
        print()

    if not fatal:
        print("  [ok] configuration is valid; the app will start.\n")
        return 0

    for f in fatal:
        print(f"  [FATAL] {f}")
    if is_deployed():
        print("\n  This deployment would REFUSE TO START. Fix before deploying.\n")
        return 1
    print("\n  Local development, so these are tolerated - but they would be fatal deployed.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
