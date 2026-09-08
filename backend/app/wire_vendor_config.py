"""Curate the Sisu vendor mapping onto the LIVE Sisu integration config.

Sisu's vendor-directory endpoint (`get-team-vendors`) 404s for this account, so the daily sync can
NEVER auto-populate the flywheel's vendor mapping — it logs "vendor sync skipped" and leaves the
config untouched. The consequence: `sympli_mortgage_vids` stays unset on prod, so every Sympli
closing fails the vendor-match (signal A) and reads as "financed elsewhere" unless an Arive
funded-loan link happens to catch it (signal C). Confirmed on txn 6690504 (mortgage_vid 155743 =
Sympli, but config empty → tagged OTHER).

This applies the known-good curated mapping (the same values `seed.py` uses for the demo) with a
MERGE — it preserves `referral_domains` and anything else already on the config. Idempotent; because
the vendor pull 404s, the sync won't overwrite it. When/if Sisu's vendor directory starts returning
data, the sync will auto-maintain these instead. Run like the seeds:

    $env:DATABASE_URL="<prod public url>"; python -m app.wire_vendor_config [tenant_slug]   # default: springb
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from .db import SessionLocal
from .models import Tenant, Integration

# The curated Sisu vendor mapping for this tenant. These are Sisu's own vendor ids; they can't be
# auto-derived here because the vendor directory 404s. 155743 is confirmed Sympli (txn 6690504);
# 247597/249290 are Sympli's other mortgage-LO entities; 12/204025 are cash / seller-finance
# (excluded from the financeable attach-rate denominator).
VENDOR_CONFIG = {
    "sympli_mortgage_vids": [155743, 247597, 249290],
    "cash_vids": [12, 204025],
    "lender_names": {
        "155743": "Sympli Mortgage of Utah", "125147": "UMortgage-Adam",
        "158205": "Intercap Lending", "162203": "First Colony Mortgage",
        "38423": "City Creek-Jenna Kinard", "12": "Cash-No Lender", "204025": "Seller Finance",
    },
}


async def _main(slug: str = "springb") -> None:
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if not t:
            print(f"[wire_vendor_config] no tenant '{slug}'"); return
        integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == t.id, Integration.provider == "sisu"))).scalars().first()
        if not integ:
            print(f"[wire_vendor_config] no Sisu integration for '{slug}'"); return
        cfg = dict(integ.config or {})
        before = cfg.get("sympli_mortgage_vids")
        cfg.update(VENDOR_CONFIG)                 # merge — keeps referral_domains etc.
        integ.config = cfg
        await s.commit()
        print(f"[wire_vendor_config] '{slug}': sympli_mortgage_vids {before} -> "
              f"{cfg['sympli_mortgage_vids']}; cash_vids {cfg['cash_vids']}; "
              f"{len(cfg['lender_names'])} lender names. referral_domains kept: {cfg.get('referral_domains')}")


if __name__ == "__main__":
    _args = [a for a in sys.argv[1:] if not a.startswith("-")]
    asyncio.run(_main(_args[0] if _args else "springb"))
