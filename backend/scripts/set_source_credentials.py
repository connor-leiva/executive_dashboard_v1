"""Set a tenant's credentials for a token-based source, from the command line.

Exists because credentials moved from process-wide env vars onto per-tenant integration rows
(phase 1) — the right model, but it means a source with no row is simply not connected, and
until the Settings UI carries a form for it there is no other way in. This is also the fastest
way to restore a source against production without waiting on a deploy.

  cd backend && ./.venv/Scripts/python.exe -m scripts.set_source_credentials \\
      --tenant springb --provider sisu --business ulrg \\
      --username team@example.com          # token read from SOURCE_TOKEN

  ./.venv/Scripts/python.exe -m scripts.set_source_credentials \\
      --tenant springb --provider fub --business ulrg   # key read from SOURCE_TOKEN

The secret comes from SOURCE_TOKEN, never an argument: an argument lands in shell history and
in the process list, where anyone else on the box can read it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Business, Integration, Tenant
from app.security import enc

# Providers whose credentials are a single opaque token vs. a username+token pair.
SINGLE_TOKEN = {"fub", "arive", "ghl", "ghl_bc", "ghl_legacy", "stripe_legacy", "stripe_bc"}
USER_AND_TOKEN = {"sisu"}


async def _run(tenant_slug: str, provider: str, business_key: str,
               username: str, token: str) -> str:
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(
            Tenant.slug == tenant_slug))).scalar_one_or_none()
        if t is None:
            raise SystemExit(f"[error] no tenant '{tenant_slug}'")
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == t.id, Business.key == business_key))).scalar_one_or_none()
        if biz is None:
            have = (await s.execute(select(Business.key).where(
                Business.tenant_id == t.id))).scalars().all()
            raise SystemExit(f"[error] tenant '{tenant_slug}' has no business "
                             f"'{business_key}' (has: {', '.join(sorted(have)) or 'none'})")

        integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == t.id, Integration.provider == provider,
            Integration.business_id == biz.id))).scalar_one_or_none()
        created = integ is None
        if integ is None:
            integ = Integration(tenant_id=t.id, provider=provider, business_id=biz.id)
            s.add(integ)

        if provider in USER_AND_TOKEN:
            integ.access_token_enc = enc(json.dumps({"username": username, "token": token}))
        else:
            integ.access_token_enc = enc(token)
        # Clear the prior failure so the next tick retries instead of staying stuck on an
        # error raised before these credentials existed.
        integ.status, integ.last_error = "connected", None
        await s.commit()
        return "created" if created else "updated"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True, help="tenant slug, e.g. springb")
    ap.add_argument("--provider", required=True,
                    choices=sorted(SINGLE_TOKEN | USER_AND_TOKEN))
    ap.add_argument("--business", required=True, help="business key the source feeds, e.g. ulrg")
    ap.add_argument("--username", default="", help="required for sisu (its Basic-auth user)")
    a = ap.parse_args()

    token = os.environ.get("SOURCE_TOKEN", "").strip()
    if not token:
        raise SystemExit("[error] set SOURCE_TOKEN in the environment (never pass a secret as "
                         "an argument — it lands in shell history and the process list)")
    if a.provider in USER_AND_TOKEN and not a.username.strip():
        raise SystemExit(f"[error] --username is required for {a.provider}")

    what = asyncio.run(_run(a.tenant, a.provider, a.business,
                            a.username.strip(), token))
    print(f"[ok] {a.provider} credentials {what} for tenant '{a.tenant}' "
          f"(business '{a.business}'). The next sync tick will use them; "
          f"'Sync now' in Settings triggers one immediately.")


if __name__ == "__main__":
    main()
