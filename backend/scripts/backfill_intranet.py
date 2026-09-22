"""Give an EXISTING workspace the portal its plan already includes.

WHY THIS EXISTS. The team portal is gated on two different things, and only one of them is the
plan. `plans.allows(tenant, "intranet")` says a workspace is ENTITLED to a portal; the app
switcher additionally requires an `intranet_workspace` row, which is the portal actually
existing. Those rows are created by `services.intranet_bootstrap.bootstrap_intranet`, and the
only caller is `provision_tenant` -- wired in on 2026-09-03 (`c93eea4`).

So every workspace provisioned before that date has a plan that promises a portal and no rows to
serve one, permanently, with nothing in the product to say so. `springb` (2026-07-01) is the one
that matters; `acmerealty`, `testrealty` and `testrealty2` are in the same state.

WHAT IT CREATES: structure only -- Owner/Manager/Member roles, the capability matrix, the
console_access grant, an empty setup checklist, the inherited provider rows, and a member row for
the owner so somebody can actually open the console. It creates NO content. That distinction is
the whole point: `scripts/seed_intranet.py` is the other option and it is one real customer's
staff list, courses and tool stack, so pointing it at a second workspace would fill that
workspace with another company's people.

Safe to re-run: `bootstrap_intranet` returns early when the workspace row already exists, so this
cannot overwrite an admin's roles or permissions. Re-running reports a no-op and changes nothing.

Usage:
  cd backend && ./.venv/Scripts/python.exe -m scripts.backfill_intranet --tenant springb
  ... --dry-run                 # say what would happen, write nothing
  ... --owner-email a@b.com     # when the workspace has no owner, or more than one

In production it has to run inside the container, because DATABASE_URL is Railway-internal:
  railway ssh --service executive_dashboard_v1 -- python -m scripts.backfill_intranet --tenant springb
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import func, select

from app import plans
from app.config import settings
from app.db import SessionLocal, engine
from app.models import (
    Base,
    IntranetCapability,
    IntranetMember,
    IntranetPermission,
    IntranetRole,
    IntranetSetupTask,
    IntranetWorkspace,
    Tenant,
    User,
)
from app.services.intranet_bootstrap import bootstrap_intranet

COUNTED = [("roles", IntranetRole), ("capabilities", IntranetCapability),
           ("permissions", IntranetPermission), ("setup tasks", IntranetSetupTask),
           ("members", IntranetMember)]


class BackfillError(Exception):
    """Something the operator has to decide, not something to guess at."""


async def _counts(s, tenant_id) -> dict[str, int]:
    out = {}
    for label, model in COUNTED:
        out[label] = (await s.execute(select(func.count()).select_from(model).where(
            model.tenant_id == tenant_id))).scalar_one()
    return out


async def _owner_email(s, tenant: Tenant, override: str | None) -> str | None:
    """Who becomes the first member. An explicit address always wins.

    With no owner the bootstrap still runs -- a workspace with roles and no members is recoverable
    from the console, a workspace with neither is not -- but it is said out loud, because the
    result is a portal nobody can administer yet.
    """
    if override:
        return override.strip().lower()
    owners = (await s.execute(select(User).where(
        User.tenant_id == tenant.id, User.role == "owner").order_by(User.created_at))).scalars().all()
    if len(owners) > 1:
        raise BackfillError(
            f"{len(owners)} owners on '{tenant.slug}' ({', '.join(o.email for o in owners)}). "
            "Say which one with --owner-email.")
    return owners[0].email if owners else None


async def _run(slug: str, override: str | None, dry_run: bool, force: bool) -> dict:
    if settings.is_sqlite:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        tenant = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalars().first()
        if tenant is None:
            raise BackfillError(f"no workspace with slug '{slug}'")

        existing = (await s.execute(select(IntranetWorkspace).where(
            IntranetWorkspace.tenant_id == tenant.id))).scalars().first()
        if existing is not None:
            return {"slug": slug, "action": "no-op", "reason": "it already has a portal",
                    "counts": await _counts(s, tenant.id)}

        # The plan is checked BEFORE writing rather than after: creating a portal a plan does not
        # include builds something the switcher will never offer, which looks like a broken
        # backfill rather than the wrong plan.
        if not plans.allows(tenant, "intranet") and not force:
            raise BackfillError(
                f"'{slug}' is on the {plans.plan_of(tenant)} plan, which does not include the "
                "portal -- the rows would be created and the app switcher still would not offer "
                "it. Change the plan first, or pass --force if you mean to.")

        owner = await _owner_email(s, tenant, override)
        if dry_run:
            return {"slug": slug, "action": "would create", "owner": owner,
                    "plan": plans.plan_of(tenant), "counts": await _counts(s, tenant.id)}

        await bootstrap_intranet(s, tenant.id, workspace_name=tenant.name, subdomain=tenant.slug,
                                 owner_email=owner)
        await s.commit()
        return {"slug": slug, "action": "created", "owner": owner,
                "plan": plans.plan_of(tenant), "counts": await _counts(s, tenant.id)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True, help="workspace slug, e.g. springb")
    ap.add_argument("--owner-email", default=None,
                    help="who becomes the first member (default: the workspace's owner)")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--force", action="store_true",
                    help="create the rows even though the plan does not include the portal")
    a = ap.parse_args()

    try:
        r = asyncio.run(_run(a.tenant.strip().lower(), a.owner_email, a.dry_run, a.force))
    except BackfillError as e:
        raise SystemExit(f"[error] {e}")

    # ASCII on purpose: this gets run from a Windows console often enough, and cp1252 turns an
    # em dash into a replacement character right next to the word "ok".
    print(f"\n[ok] {r['slug']}: {r['action']}"
          + (f" - {r['reason']}" if r.get("reason") else ""))
    if r.get("owner"):
        print(f"     first member: {r['owner']}")
    elif r["action"] != "no-op":
        print("     NO OWNER FOUND — the portal will exist with nobody able to administer it.\n"
              "     Add a member with console access, or re-run with --owner-email.")
    print("     " + ", ".join(f"{v} {k}" for k, v in r["counts"].items()))
    if r["action"] == "created":
        print("\n     Structure only: no roster, no courses, no tiles. The workspace fills those\n"
              "     in from its own console.")


if __name__ == "__main__":
    main()
