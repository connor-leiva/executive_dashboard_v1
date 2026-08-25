"""List and manage the hostnames a tenant answers on.

A tenant's `domain` rows decide two different things, and it is easy to notice only the first:

  1. WHICH TENANT A REQUEST IS. tenancy.resolve_tenant matches the browser's hostname against
     these rows. A tenant with no row for the host it is actually served from resolves only
     through the single-tenant fallback — which closes the moment a second tenant exists. So a
     missing row is not a cosmetic problem; it is a lockout waiting for the next customer.

  2. WHERE LINKS POINT. tenancy.tenant_app_url reads the PRIMARY row to build every URL handed
     to a human: password resets, invites, share links, the QuickBooks OAuth return. A wrong
     primary produces links to a domain you may not even own.

  python -m scripts.tenant_domains --tenant springb --list
  python -m scripts.tenant_domains --tenant springb --add app.acumyn.io --primary
  python -m scripts.tenant_domains --tenant springb --remove old.example.com
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.models import Domain, Tenant
from app.tenancy import PLATFORM_HOSTS


def _db_target() -> str:
    url = settings.DATABASE_URL
    if url.startswith("sqlite"):
        return f"LOCAL SQLite ({url.rsplit('/', 1)[-1]})  <-- NOT production"
    try:
        creds, hostpart = url.split("://", 1)[1].split("@", 1)
        return f"Postgres {creds.split(':', 1)[0]}@{hostpart}"
    except (IndexError, ValueError):
        return "Postgres (could not parse the URL)"


def _normalize_host(raw: str) -> str:
    """Clean up a host argument and reject one that could never work.

    Argument validation before the session, deliberately: needing a reachable database to
    be told an argument is invalid is how a typo turns into a connection-string hunt.
    """
    host = raw.strip().lower().split("//")[-1].split("/")[0]
    suffix = "." + settings.PLATFORM_DOMAIN.lower()
    # tenancy.resolve_tenant rejects these ahead of its domain lookup, so a row for one is
    # inert. Refuse at add time; otherwise it looks added and 404s at request time.
    if host.endswith(suffix) and host[: -len(suffix)] in PLATFORM_HOSTS:
        raise SystemExit(
            f"[error] '{host}' is a platform host: it never resolves to a tenant, so "
            f"this row would look added and the host would still never work.\n"
            f"        Reserved under {settings.PLATFORM_DOMAIN}: "
            f"{', '.join(sorted(PLATFORM_HOSTS))}.\n"
            f"        (admin. is the operator console; api. is this API.)")
    return host


async def _run(args) -> None:
    add_host = _normalize_host(args.add) if args.add else None
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(
            Tenant.slug == args.tenant))).scalar_one_or_none()
        if t is None:
            raise SystemExit(f"[error] no tenant '{args.tenant}'")

        if args.add:
            host = add_host
            clash = (await s.execute(select(Domain).where(
                Domain.hostname == host))).scalar_one_or_none()
            if clash and clash.tenant_id != t.id:
                raise SystemExit(f"[error] '{host}' already belongs to another tenant")
            if clash is None:
                s.add(Domain(tenant_id=t.id, hostname=host, is_primary=False))
                await s.flush()
            if args.primary:
                for d in (await s.execute(select(Domain).where(
                        Domain.tenant_id == t.id))).scalars():
                    d.is_primary = (d.hostname == host)
            await s.commit()
            print(f"[ok] '{host}' -> {t.slug}" + (" (primary)" if args.primary else ""))

        if args.remove:
            host = args.remove.strip().lower()
            row = (await s.execute(select(Domain).where(
                Domain.tenant_id == t.id, Domain.hostname == host))).scalar_one_or_none()
            if row is None:
                raise SystemExit(f"[error] '{host}' is not a domain of '{t.slug}'")
            remaining = (await s.execute(select(func.count()).select_from(Domain).where(
                Domain.tenant_id == t.id))).scalar_one()
            if remaining <= 1:
                raise SystemExit(
                    f"[error] '{host}' is the only domain for '{t.slug}'. Removing it would "
                    f"leave the tenant reachable only through the single-tenant fallback, "
                    f"which closes as soon as a second tenant exists. Add the real host first.")
            was_primary = row.is_primary
            await s.delete(row)
            await s.flush()
            if was_primary:                       # never leave a tenant with no primary
                nxt = (await s.execute(select(Domain).where(
                    Domain.tenant_id == t.id))).scalars().first()
                if nxt:
                    nxt.is_primary = True
                    print(f"[note] '{host}' was primary; '{nxt.hostname}' is now.")
            await s.commit()
            print(f"[ok] removed '{host}' from {t.slug}")

        rows = (await s.execute(select(Domain).where(Domain.tenant_id == t.id)
                                .order_by(Domain.is_primary.desc(), Domain.hostname))).scalars().all()
        tenant_count = (await s.execute(select(func.count()).select_from(Tenant))).scalar_one()
        print(f"\n  database   {_db_target()}")
        print(f"  tenant     {t.slug} / {t.name}\n")
        if not rows:
            print("  (no domains — reachable ONLY through the single-tenant fallback)\n")
        for d in rows:
            print(f"    {'*' if d.is_primary else ' '} {d.hostname}"
                  + ("   <- links are built from this one" if d.is_primary else ""))
        print()
        if tenant_count > 1:
            print(f"  {tenant_count} tenants exist, so the fallback is CLOSED: only the hosts")
            print("  above resolve to this tenant.\n")
        else:
            print("  1 tenant, so the fallback still catches unrecognized hosts. That stops")
            print("  the moment a second tenant is created.\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--list", action="store_true", help="show the tenant's domains (the default)")
    ap.add_argument("--add", metavar="HOST", help="add a hostname")
    ap.add_argument("--primary", action="store_true", help="with --add: make it the primary")
    ap.add_argument("--remove", metavar="HOST", help="remove a hostname")
    asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    main()
