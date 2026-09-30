"""Give an existing workspace the AP Clerk, and the skills it needs to exist.

WHY THIS EXISTS. Two separate things have to be true before an accounts-payable clerk can run,
and neither of them happens on deploy.

The first is the platform skill catalog. `seed_platform_catalogs` upserts the product skills into
`ai_skill`, and its only callers are `seed()` (development) and `provision_tenant` (a BRAND NEW
workspace). Nothing runs it when a deploy adds skills to the catalog, so a workspace that already
existed keeps whatever six rows it was provisioned with — forever, with nothing in the product to
say the five new ones are missing. `springb` is in exactly that state: six social skills, no AP.

An employee created without them still works, because dispatch reads the catalog from CODE and
`seed_employee_skills` falls back to version 1. What breaks is quieter: the settings screen
cannot tell you a prompt override has gone stale against a newer seed, because there is no seed
row to compare against.

The second is the employee itself, and specifically `config.family = "ap"`. The skill catalog is
one global list read by every employee, and the family is the only thing that decides which half
of it an employee draws from. An AP Clerk created without it is a social-media manager wearing an
accounts-payable name badge: it would be seeded Summer's six skills and would file carousel
designs on a nightly cron.

WHAT IT DOES NOT DO. It does not open the door. `AI_EMPLOYEES_WRITEBACK_ENABLED` and the
employee's own `writeback_enabled` toggle both stay as they are, so the clerk can be created,
can run, and can propose — and cannot turn a proposal into a bill until a person opens both.
That is deliberate: setting up an employee and authorising it to write are different decisions
and should be taken at different moments.

Safe to re-run. The catalog upsert is idempotent by key, and an existing AP Clerk is reported
rather than duplicated or modified.

Usage:
  cd backend && ./.venv/Scripts/python.exe -m scripts.setup_ap_clerk --tenant springb --dry-run
  ... --name "AP Clerk"         # what it is called in the roster

In production it has to run inside the container, because DATABASE_URL is Railway-internal:
  railway ssh --service executive_dashboard_v1 -- python -m scripts.setup_ap_clerk --tenant springb
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import func, select

from app import plans
from app.db import SessionLocal
from app.models import AIEmployee, AIEmployeeSkill, AISkill, Tenant, User
from app.services.ai_employees import seed_employee_skills
from app.services.ai_skills import AP, skills_for
from app.services.audit import audit
from app.services.provisioning import seed_platform_catalogs


class SetupError(RuntimeError):
    pass


async def _run(slug: str, name: str, dry_run: bool) -> dict:
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if t is None:
            raise SetupError(f"no workspace with slug {slug!r}")

        # ── 1. the catalog ───────────────────────────────────────────────────
        want = {d["key"] for d in skills_for(AP)}
        have = {k for (k,) in (await s.execute(select(AISkill.key))).all()}
        missing = sorted(want - have)

        # ── 2. the employee ──────────────────────────────────────────────────
        existing = (await s.execute(select(AIEmployee).where(
            AIEmployee.tenant_id == t.id,
            AIEmployee.status != "archived"))).scalars().all()
        clerk = next((e for e in existing
                      if (e.config or {}).get("family") == AP), None)

        cap = plans.limits(t).get("max_ai_employees")
        # Archived employees do not count — they do not run and spend nothing.
        over = clerk is None and plans.over_limit(t, "max_ai_employees", len(existing))

        result = {
            "slug": slug, "plan": plans.limits(t)["name"], "cap": cap,
            "active_employees": [e.name for e in existing],
            "catalog_missing": missing,
            "clerk": clerk.name if clerk else None,
            "over_limit": over,
            "action": "no-op",
        }
        if over:
            raise SetupError(
                f"{plans.limits(t)['name']} allows {cap} AI employees and {len(existing)} are "
                f"active ({', '.join(e.name for e in existing)}). Archive one, or raise the plan.")

        if dry_run:
            result["action"] = "would create" if clerk is None else "already there"
            return result

        if missing:
            await seed_platform_catalogs(s)
            await s.commit()

        if clerk is None:
            owner = (await s.execute(select(User).where(
                User.tenant_id == t.id, User.role.in_(("owner", "admin")))
                .order_by(User.created_at).limit(1))).scalars().first()
            clerk = AIEmployee(
                tenant_id=t.id, name=name, role_title="Accounts Payable",
                avatar_color="#61835E", status="active",
                # Writeback stays OFF. Creating the clerk and authorising it to write are two
                # decisions, and this script only takes the first.
                writeback_enabled=False,
                config={"family": AP, "timezone": "America/Denver"})
            s.add(clerk)
            await s.flush()
            await seed_employee_skills(s, clerk)
            audit(s, t.id, getattr(owner, "id", None), "ai.employee_create",
                  "ai_employee", clerk.id,
                  {"name": name, "family": AP, "via": "scripts.setup_ap_clerk"},
                  # A script, not a person: audit_log carries a CHECK enumerating
                  # user | system | integration, and filing this as a user would put a machine's
                  # act in the trail under somebody's name.
                  category="AI", actor_label="Axcion (setup script)", actor_type="system")
            await s.commit()
            result["action"] = "created"
        else:
            result["action"] = "already there"

        rows = (await s.execute(select(AIEmployeeSkill.skill_key).where(
            AIEmployeeSkill.employee_id == clerk.id))).all()
        result["clerk"] = clerk.name
        result["skills"] = sorted(k for (k,) in rows)
        result["writeback"] = bool(clerk.writeback_enabled)
        return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Create the AP Clerk for an existing workspace")
    ap.add_argument("--tenant", required=True, help="workspace slug, e.g. springb")
    ap.add_argument("--name", default="AP Clerk", help="roster name (default: AP Clerk)")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    a = ap.parse_args()

    try:
        r = asyncio.run(_run(a.tenant.strip().lower(), a.name.strip(), a.dry_run))
    except SetupError as e:
        raise SystemExit(f"[error] {e}")

    # ASCII on purpose: this gets run from a Windows console often enough, and cp1252 turns an
    # em dash into a replacement character right next to the word "ok".
    print(f"\n[ok] {r['slug']}: {r['action']}")
    print(f"     plan {r['plan']}, cap {r['cap']}, active now: "
          + (", ".join(r["active_employees"]) or "none"))
    if r["catalog_missing"]:
        verb = "would add" if a.dry_run else "added"
        print(f"     catalog: {verb} {len(r['catalog_missing'])} missing skills "
              f"({', '.join(r['catalog_missing'])})")
    else:
        print("     catalog: already complete")
    if r.get("skills"):
        print(f"     {r['clerk']} has {len(r['skills'])} skills: {', '.join(r['skills'])}")
    if r["action"] == "created":
        print("\n     Writeback is OFF, so it can run and propose but cannot turn a proposal\n"
              "     into a bill. To open that door: set AI_EMPLOYEES_WRITEBACK_ENABLED=true and\n"
              "     switch on this employee's own writeback toggle. Both halves are required.")


if __name__ == "__main__":
    main()
