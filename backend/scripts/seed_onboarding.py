"""Seed an onboarding plan into a workspace from a spec file. ONBOARDING-SPEC.md §5.

    python -m scripts.seed_onboarding --tenant springb --spec scripts/data/onboarding_matt_griner.json
    python -m scripts.seed_onboarding --tenant springb --spec ... --email matt@example.com
    python -m scripts.seed_onboarding --tenant springb --spec ... --replace

`--email` links the plan to an existing user, which is what makes it THEIRS: they see it in
their own Onboarding tab and may write to it. Without it the plan is readable by owners and
admins only, and can be linked later.

REFUSES A DUPLICATE by default. Running a seed twice is how a person ends up with two copies of
their month and half their ticks on each; `--replace` deletes the earlier plan (and everything
under it, by cascade) and says how many rows went.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

from sqlalchemy import select

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.db import SessionLocal                                        # noqa: E402
from app.models import OnboardingPlan, Tenant, User                    # noqa: E402
from app.services.onboarding import seed_plan                          # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True, help="workspace slug")
    ap.add_argument("--spec", required=True, help="path to the plan JSON")
    ap.add_argument("--email", help="link the plan to this user")
    ap.add_argument("--replace", action="store_true",
                    help="delete an existing plan with the same subject and dates first")
    args = ap.parse_args()

    spec = json.loads(pathlib.Path(args.spec).read_text(encoding="utf-8"))

    async with SessionLocal() as s:
        tenant = (await s.execute(select(Tenant).where(
            Tenant.slug == args.tenant))).scalar_one_or_none()
        if tenant is None:
            print(f"No workspace {args.tenant!r}")
            return 2

        user_id = None
        if args.email:
            u = (await s.execute(select(User).where(
                User.tenant_id == tenant.id,
                User.email == args.email.lower()))).scalar_one_or_none()
            if u is None:
                print(f"No user {args.email!r} in {args.tenant} -- seed without --email and "
                      f"link the plan once they have an account")
                return 2
            user_id = u.id

        existing = (await s.execute(select(OnboardingPlan).where(
            OnboardingPlan.tenant_id == tenant.id,
            OnboardingPlan.subject_name == spec["subject_name"],
            OnboardingPlan.starts_on == __import__("datetime").date.fromisoformat(
                spec["starts_on"])))).scalars().all()
        if existing and not args.replace:
            print(f"{len(existing)} plan(s) already exist for {spec['subject_name']} starting "
                  f"{spec['starts_on']}. Re-run with --replace to overwrite, which DELETES the "
                  f"progress recorded against them.")
            return 1
        for p in existing:
            await s.delete(p)          # cascades to weeks, days, blocks, targets, scripts, logs
        if existing:
            print(f"replaced {len(existing)} existing plan(s)")

        plan = await seed_plan(s, tenant.id, spec, user_id=user_id)
        await s.commit()

    weeks = len(spec.get("weeks") or [])
    days = sum(len(w.get("days") or []) for w in spec.get("weeks") or [])
    blocks = sum(len(d.get("blocks") or []) for w in spec.get("weeks") or []
                 for d in w.get("days") or [])
    print(f"seeded {plan.id} -- {spec['subject_name']}, {weeks} weeks, {days} days, "
          f"{blocks} blocks, {len(spec.get('targets') or [])} targets"
          + (f", linked to {args.email}" if args.email else ", not linked to a user yet"))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
