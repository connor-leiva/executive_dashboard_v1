"""Teach the beCollective Stripe sync about the paying members its roster never knew.

WHY THIS EXISTS. `sync_becollective_stripe` gates every Stripe charge on a roster of emails from
`_bc_roster_emails` (services/sync.py:1511-1519), and a charge whose payer is not on it is
silently dropped. On 2026-11-04 a temporary diagnostic measured what that costs: 342 of 594
beCollective charges refused, of which 162 charges across 25 payers were real membership money --
$232,700 collected. The dashboard was showing 72.8% of beCollective's cash.

Three things make the roster structurally smaller than "people who pay beCollective":

  1. `_bc_roster_emails` selects emails for `kind IN ('bc_member', 'bc_membership')`, but
     bc_membership rows NEVER carry one -- the opportunities loop builds its row without an
     `email` key at all (sync.py:1191-1193), so all 60 won-onboarded opportunities land with
     email NULL and are filtered out one line later. The people who provably paid contribute
     zero emails to the gate. Half that `kind IN (...)` clause is dead.
  2. What remains is the contacts pass, and a contact is a member only if a hand-typed
     "Member Type" custom field is filled in (sync.py:216-225). Those fields were added to the
     beCollective location on 2026-07-27 -- five months after cohort one started paying.
  3. The roster is a snapshot of who is a member NOW, applied to ALL history (sync.py:1548).
     Marking a member inactive also erases money they genuinely paid while active.

This script does not fix any of that. It applies the one override the sync already honours --
`extra_member_emails` (sync.py:1541) -- so the cash becomes visible while the real fix is
reviewed. In production that key was unset, so this is its first use.

DERIVED, NOT RETYPED. The emails come from the diagnostic rows in the database, not from a list
anybody transcribed: `kind='dropped_offroster'`, `source='stripe_bc'`, `would_be_offering=true`,
`status='succeeded'`. A payer is added only when money actually ARRIVED under that address. A
transcription slip therefore cannot put a stranger on the roster -- there is nothing to mistype.

That makes this script depend on the diagnostic (commit 49344e2) still being deployed. Once the
diagnostic is removed there is nothing to derive from, and re-running reports zero candidates
rather than quietly undoing anything. Run it before the diagnostic comes out.

beCollective ONLY. `extra_member_emails` is read in exactly one place, the beCollective charge
sync. The Forum's legacy feed has its own gate and its own escape hatch (a GHL transaction
`label`, sync.py:1403) and is untouched by this.

Safe to re-run: the write is a set union over the existing list, so a second run is a no-op and
reports as one. It never removes an address somebody added by hand.

Dry-run by default -- prints each payer, their collected cash, and the before/after list.
--apply writes.

  railway ssh --service executive_dashboard_v1 -- python -m scripts.add_bc_roster_overrides
  railway ssh --service executive_dashboard_v1 -- python -m scripts.add_bc_roster_overrides --apply

ORDER: the write only changes what the NEXT sync keeps. Charges already dropped are re-read from
Stripe every run (sync.py:1548) and the snapshot is rebuilt wholesale, so the money appears at
the next tick (~30 min) without a backfill.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Integration, MetricRecord, Tenant

DIAG_KIND = "dropped_offroster"
SOURCE = "stripe_bc"


def _is_candidate(r: MetricRecord, min_amount: Decimal) -> bool:
    """A dropped charge that proves a paying member the roster is missing.

    Filtered in Python rather than SQL on purpose: `meta` is a JSONType that is JSONB on
    Postgres and JSON on SQLite, and the dropped set is a few hundred rows. Portable beats
    clever here, and it lets this run against a throwaway SQLite database unchanged.
    """
    meta = r.meta or {}
    return bool(
        r.email
        and (r.status or "").lower() == "succeeded"
        and meta.get("would_be_offering") is True
        and (r.amount or Decimal(0)) >= min_amount
    )


async def main() -> int:
    ap = argparse.ArgumentParser(
        description="Add the beCollective payers the roster filter drops to extra_member_emails.")
    ap.add_argument("--tenant", default="springb", help="tenant slug (default: springb)")
    ap.add_argument("--apply", action="store_true", help="write it (otherwise dry-run)")
    ap.add_argument("--min-amount", type=Decimal, default=Decimal("500"),
                    help="ignore charges below this (default: 500, the live offering floor). "
                         "Guards against the $1 card-verification class that the offering "
                         "filter accepts on a program-name match alone.")
    args = ap.parse_args()

    print(f"db: {settings.DATABASE_URL.split('@')[-1]}")   # host only, never the password

    async with SessionLocal() as s:
        tenant_id = (await s.execute(
            select(Tenant.id).where(Tenant.slug == args.tenant))).scalar_one_or_none()
        if tenant_id is None:
            print(f"No tenant {args.tenant!r}.")
            return 2

        integs = (await s.execute(select(Integration).where(
            Integration.tenant_id == tenant_id,
            Integration.provider == SOURCE))).scalars().all()
        if not integs:
            print(f"No {SOURCE} integration on {args.tenant!r}. Nothing to configure.")
            return 2
        if len(integs) > 1:
            print(f"{len(integs)} {SOURCE} integrations on {args.tenant!r} — refusing to guess "
                  "which one gates the sync. Resolve by hand.")
            return 2
        integ = integs[0]

        rows = (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == tenant_id,
            MetricRecord.source == SOURCE,
            MetricRecord.kind == DIAG_KIND))).scalars().all()
        if not rows:
            print(f"No {DIAG_KIND!r} rows for {SOURCE}. Either the diagnostic is no longer "
                  "deployed, or no sync has run since it was. Nothing to derive from.")
            return 1

        cash: dict[str, Decimal] = defaultdict(Decimal)
        charges: dict[str, int] = defaultdict(int)
        names: dict[str, str] = {}
        for r in rows:
            if not _is_candidate(r, args.min_amount):
                continue
            em = r.email.strip().lower()
            cash[em] += (r.amount or Decimal(0))
            charges[em] += 1
            names.setdefault(em, (r.name or "").strip())

        current = [str(e).strip().lower()
                   for e in ((integ.config or {}).get("extra_member_emails") or [])]
        already = set(current)
        new = sorted(set(cash) - already)

        print(f"\n{len(rows)} dropped charges on record; {len(cash)} payers qualify "
              f"(succeeded, would-be-member, >= ${args.min_amount}).")
        print(f"extra_member_emails currently holds {len(current)}.\n")

        if not new:
            print("Every qualifying payer is already on the override list. Nothing to do.")
            return 0

        width = max(len(e) for e in new)
        total = Decimal(0)
        for em in sorted(new, key=lambda e: -cash[e]):
            print(f"  + {em.ljust(width)}  {names.get(em, '')[:28]:<28} "
                  f"{charges[em]:>3} chg  ${cash[em]:>10,.2f}")
            total += cash[em]
        print(f"\n  {len(new)} payers, ${total:,.2f} of collected cash currently unreported.")

        skipped = sorted(already & set(cash))
        if skipped:
            print(f"\n  ({len(skipped)} qualifying payers already listed, left alone.)")

        if not args.apply:
            print("\nDRY RUN — nothing written. Re-run with --apply.")
            return 0

        # A COPY: mutating the loaded dict makes SQLAlchemy skip the UPDATE in silence.
        cfg = dict(integ.config or {})
        cfg["extra_member_emails"] = sorted(already | set(cash))
        integ.config = cfg
        await s.commit()

        after = (await s.execute(select(Integration.config).where(
            Integration.id == integ.id))).scalar_one() or {}
        stored = sorted(str(e).strip().lower()
                        for e in (after.get("extra_member_emails") or []))
        if stored != sorted(already | set(cash)):
            print("\nWROTE, BUT THE READ-BACK DOES NOT MATCH. Investigate before trusting it.")
            print(f"  wanted {len(already | set(cash))}, stored {len(stored)}")
            return 1
        print(f"\nWrote {len(stored)} addresses to extra_member_emails "
              f"(was {len(current)}). Read back and verified.")
        print("The money appears at the next beCollective sync (~30 min). No backfill needed.")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
