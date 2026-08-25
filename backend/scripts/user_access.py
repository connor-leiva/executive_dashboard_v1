"""Diagnose and restore a user's ability to sign in, from the command line.

There is deliberately no way to READ a password: they are bcrypt hashes, one-way by design.
Anything that could show you a stored password would be a bug, not a feature.

What locks someone out is usually not the password anyway, and the login response does not
distinguish the causes (on purpose — a login that says WHICH thing was wrong is a login that
enumerates accounts for an attacker). So `--show` prints the actual state: account status,
lockout, failed attempts, tenant status, and whether the tenant resolves from a host at all.

  # why can't this person sign in?
  python -m scripts.user_access --tenant springb --email spring@springb.com --show

  # clear a lockout from repeated attempts
  python -m scripts.user_access --tenant springb --email spring@springb.com --unlock

  # issue a reset link — the user chooses their own password, which stays theirs
  python -m scripts.user_access --tenant springb --email spring@springb.com --reset-link

  # last resort: set a password directly (audited; see the note on --set-password)
  NEW_PASSWORD=... python -m scripts.user_access --tenant springb \\
      --email spring@springb.com --set-password

If what you actually want is to SEE WHAT THEY SEE, none of these is the right tool — signing
in as somebody leaves their audit trail looking like they did it. That is what masquerade is
for: a time-boxed session that records who was really acting.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Domain, Tenant, User
from app.security import hash_pw, new_action_token
from app.services.audit import audit

RESET_HOURS = 24
MIN_LEN = 12


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _aware(d):
    return None if d is None else (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc))


async def _load(s, tenant_slug: str, email: str):
    t = (await s.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one_or_none()
    if t is None:
        raise SystemExit(f"[error] no tenant '{tenant_slug}'")
    u = (await s.execute(select(User).where(
        User.tenant_id == t.id, User.email == email.lower()))).scalar_one_or_none()
    if u is None:
        have = (await s.execute(select(User.email).where(User.tenant_id == t.id))).scalars().all()
        raise SystemExit(f"[error] no user '{email}' in '{tenant_slug}'. "
                         f"Users: {', '.join(sorted(have)) or 'none'}")
    return t, u


def _db_target() -> str:
    """Which database this is actually talking to, with the password stripped.

    The commonest way to waste an hour on this tool is to run it without pointing at
    production: it quietly reads the local SQLite file, reports a perfectly healthy account,
    and the real one is never touched. So say which database it is, before anything else.
    """
    from app.config import settings

    url = settings.DATABASE_URL
    if url.startswith("sqlite"):
        return f"LOCAL SQLite ({url.rsplit('/', 1)[-1]})  <-- NOT production"
    try:                                     # postgres://user:pw@host:port/db -> user@host:port/db
        creds, hostpart = url.split("://", 1)[1].split("@", 1)
        return f"Postgres {creds.split(':', 1)[0]}@{hostpart}"
    except (IndexError, ValueError):
        return "Postgres (could not parse the URL)"


async def _show(s, t: Tenant, u: User) -> None:
    locked = _aware(u.locked_until)
    still_locked = bool(locked and locked > _now())
    hosts = (await s.execute(select(Domain.hostname).where(
        Domain.tenant_id == t.id).order_by(Domain.is_primary.desc()))).scalars().all()
    tenant_count = (await s.execute(select(func.count()).select_from(Tenant))).scalar_one()

    print(f"\n  database        {_db_target()}")
    print(f"  user            {u.email}  ({u.name})")
    print(f"  role/status     {u.role} / {u.status}"
          + ("   <- cannot sign in: not active" if u.status != "active" else ""))
    print(f"  password set    {'yes' if u.password_hash else 'NO — invite never accepted'}")
    print(f"  failed logins   {u.failed_logins or 0}")
    print(f"  locked          {'YES until ' + locked.isoformat() if still_locked else 'no'}")
    print(f"  last login      {u.last_login_at.isoformat() if u.last_login_at else 'never'}")
    print(f"  pending link    {u.action_token_purpose or 'none'}"
          + (f" (expires {u.action_token_expires.isoformat()})" if u.action_token_expires else ""))
    print(f"\n  tenant          {t.slug} / {t.name}  [{t.status}]"
          + ("   <- cannot sign in: workspace suspended" if t.status != "active" else ""))
    print(f"  hosts           {', '.join(hosts) or 'NONE'}")
    if not hosts:
        print("                  ^ with no domain row, this tenant is only reachable through")
        print("                    the single-tenant fallback, which closes once a second")
        print(f"                    tenant exists. Tenants right now: {tenant_count}.")
    elif tenant_count > 1:
        print(f"                  ({tenant_count} tenants exist, so the fallback is closed —")
        print("                   sign in at one of the hosts above, not a Railway URL.)")
    print()


async def _run(args) -> None:
    async with SessionLocal() as s:
        t, u = await _load(s, args.tenant, args.email)

        if args.unlock:
            u.failed_logins, u.locked_until = 0, None
            audit(s, t.id, None, "user.unlocked", "user", u.id, {"via": "cli"})
            await s.commit()
            print(f"[ok] cleared the lockout for {u.email}")

        if args.reset_link:
            raw, th = new_action_token()
            u.action_token_hash, u.action_token_purpose = th, "reset"
            u.action_token_expires = _now() + dt.timedelta(hours=RESET_HOURS)
            audit(s, t.id, None, "user.reset_link", "user", u.id, {"via": "cli"})
            await s.commit()
            host = (await s.execute(select(Domain.hostname).where(
                Domain.tenant_id == t.id).order_by(Domain.is_primary.desc()))).scalars().first()
            scheme = "http" if (host or "").startswith(("localhost", "127.")) else "https"
            base = f"{scheme}://{host}" if host else "<your app URL>"
            print(f"\n[ok] reset link for {u.email} — valid {RESET_HOURS}h, single use:\n")
            print(f"   {base}/reset-password?token={raw}\n")

        if args.set_password:
            pw = os.environ.get("NEW_PASSWORD", "")
            if len(pw) < MIN_LEN:
                raise SystemExit(f"[error] set NEW_PASSWORD in the environment, at least "
                                 f"{MIN_LEN} characters (never pass a secret as an argument)")
            u.password_hash = hash_pw(pw)
            u.status = "active" if u.status == "invited" else u.status
            u.failed_logins, u.locked_until = 0, None
            # Every existing session dies: if this was used to recover an account, any session
            # the previous holder had must not survive it.
            u.token_version = (u.token_version or 0) + 1
            u.action_token_hash = u.action_token_purpose = u.action_token_expires = None
            audit(s, t.id, None, "user.password_set_by_operator", "user", u.id, {"via": "cli"})
            await s.commit()
            print(f"[ok] password set for {u.email}; all existing sessions revoked.")
            print("     This is recorded in the tenant's audit trail as an operator action.")

        if args.show or not (args.unlock or args.reset_link or args.set_password):
            await _show(s, t, u)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tenant", required=True, help="tenant slug, e.g. springb")
    ap.add_argument("--email", required=True)
    ap.add_argument("--show", action="store_true", help="print why sign-in may be failing")
    ap.add_argument("--unlock", action="store_true", help="clear a failed-attempt lockout")
    ap.add_argument("--reset-link", action="store_true",
                    help="issue a single-use reset link (the user picks their own password)")
    ap.add_argument("--set-password", action="store_true",
                    help="set a password from NEW_PASSWORD; revokes sessions; audited")
    asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    main()
