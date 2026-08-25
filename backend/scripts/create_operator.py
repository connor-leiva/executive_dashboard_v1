"""Create a PLATFORM operator — the account that administers tenants.

Chicken-and-egg: the operator API is gated by an operator session, so the first one cannot be
created through it. This is that bootstrap, and it stays a CLI on purpose — creating an
operator is the single most privileged action on the platform, and requiring shell access to
the deployment is a stronger control than any password.

Usage:
  cd backend && ./.venv/Scripts/python.exe -m scripts.create_operator \\
      --email you@example.com --name "Your Name"

The password is read from PLATFORM_OPERATOR_PASSWORD, or generated and printed once. It is
never taken as an argument: an argument lands in shell history and in the process list, where
other users on the box can read it.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal, engine
from app.models import Base, PlatformUser
from app.security import hash_pw

MIN_LEN = 12


async def _run(email: str, name: str, password: str, reset: bool) -> str:
    if settings.is_sqlite:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        existing = (await s.execute(select(PlatformUser).where(
            PlatformUser.email == email))).scalar_one_or_none()
        if existing and not reset:
            raise SystemExit(f"[error] operator '{email}' already exists (use --reset-password)")
        if existing:
            existing.password_hash = hash_pw(password)
            existing.is_active = True
            existing.failed_logins, existing.locked_until = 0, None
            # Bump so any session issued against the old password stops working.
            existing.token_version = (existing.token_version or 0) + 1
            await s.commit()
            return "reset"
        s.add(PlatformUser(email=email, name=name, password_hash=hash_pw(password)))
        await s.commit()
        return "created"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", default=None)
    ap.add_argument("--reset-password", action="store_true",
                    help="set a new password for an existing operator (revokes its sessions)")
    a = ap.parse_args()

    email = a.email.strip().lower()
    if "@" not in email:
        raise SystemExit("[error] a valid email is required")

    password = os.environ.get("PLATFORM_OPERATOR_PASSWORD", "")
    generated = False
    if not password:
        password, generated = secrets.token_urlsafe(18), True
    if len(password) < MIN_LEN:
        raise SystemExit(f"[error] password must be at least {MIN_LEN} characters")

    what = asyncio.run(_run(email, a.name or email.split("@")[0], password, a.reset_password))
    print(f"\n[ok] platform operator {what}: {email}")
    if generated:
        print(f"     password (shown once): {password}")
    print("\n     This account administers TENANTS. It is not a login to any customer's\n"
          "     dashboard, and it cannot read a customer's data.\n", file=sys.stderr)


if __name__ == "__main__":
    main()
