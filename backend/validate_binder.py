"""Acumyn Binder - Part 13 Step 4 go/no-go (scaffolding, built in Step 3).

Runs the Binder pipeline against a FOLDER of real documents in a LOCAL seeded DB and prints,
per document, what was ingested and (once Step 4 lands) what the extractor proposes. This is
the gate before any extraction endpoint ships - specifically where entity-name-collision
handling gets proven on real data, exactly as validate_books.py gated the QBO report shape.

Step 3 wires the ingestion half: point it at a folder and it stores + hashes + dedups every
file and records a SyncRun. The extraction half (entity match, proposed obligations, gaps) is
marked TODO(step 4) below; binder_extract.py fills it in and this script becomes the real
go/no-go by printing per-document proposals.

Nothing here touches production: it seeds and writes a LOCAL SQLite DB and stores blobs under
BINDER_STORAGE_BUCKET (a temp dir if unset). No network, no live credentials.

Usage:
  python validate_binder.py <folder> [--tenant springb] [--limit N]
"""
import argparse
import asyncio
import os
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Tenant, User, LegalEntity, BinderDocument, JurisdictionRule, ProposedObligation
from app.seed import seed
from app.services import binder_ingest, binder_extract

_EXT_OK = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".txt"}


def _collect(folder: str, limit: int) -> list[dict]:
    files = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() not in _EXT_OK:
            continue
        with open(path, "rb") as f:
            files.append({"filename": name, "data": f.read()})
        if limit and len(files) >= limit:
            break
    return files


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="folder of documents to ingest")
    ap.add_argument("--tenant", default="springb")
    ap.add_argument("--limit", type=int, default=0, help="cap the number of files (0 = all)")
    args = ap.parse_args()

    if not os.path.isdir(args.folder):
        sys.exit(f"Not a folder: {args.folder}")
    files = _collect(args.folder, args.limit)
    if not files:
        sys.exit(f"No ingestible files ({', '.join(sorted(_EXT_OK))}) in {args.folder}")

    await seed()
    async with SessionLocal() as s:
        tenant = (await s.execute(select(Tenant).where(Tenant.slug == args.tenant))).scalar_one_or_none()
        if tenant is None:
            sys.exit(f"No tenant '{args.tenant}' (run the seed).")
        owner = (await s.execute(select(User).where(
            User.tenant_id == tenant.id, User.role == "owner"))).scalars().first()

        # Context the extraction step matches against.
        entities = (await s.execute(select(LegalEntity).where(
            LegalEntity.tenant_id == tenant.id, LegalEntity.active.is_(True)))).scalars().all()
        rules = (await s.execute(select(JurisdictionRule).where(
            JurisdictionRule.tenant_id.is_(None)))).scalars().all()
        print(f"\nTenant '{args.tenant}': {len(entities)} entities configured, "
              f"{len(rules)} system jurisdiction rules seeded.")
        if not entities:
            print("NOTE: entities are user-created; with none configured the entity-match step "
                  "has nothing to match against. Configure entities first for a real test.")
        if not binder_extract._enabled():
            print("NOTE: ANTHROPIC_API_KEY is not set, so extraction is SKIPPED (ingestion only). "
                  "Set it to exercise the classify/match/derive pass.")

        print(f"\nIngesting {len(files)} file(s) from {args.folder} ...")
        res = await binder_ingest.ingest_batch(s, tenant.id, owner, files, uploaded_via="folder")
        print(f"  created={res['created']}  deduped={res['deduped']}  failed={res['failed']}  "
              f"sync_run={res['sync_run_id']}")

        # ── Extraction: the go/no-go core (entity-name collision handling on real data) ──
        ext = await binder_extract.run_binder_extraction(s, tenant.id)
        if ext.get("skipped"):
            print("\nIngestion path OK. Extraction skipped (no key).")
            sys.exit(0)
        print(f"\nExtracted {ext['documents']} document(s) -> {ext['proposals']} proposal(s):\n")
        ambiguous_ct = 0
        for r in ext["results"]:
            amb = " AMBIGUOUS(pick required)" if r["ambiguous"] else ""
            ambiguous_ct += int(r["ambiguous"])
            print(f"  {r['category']:<16} entity~'{r['entity_guess'] or '?'}' "
                  f"conf={r['entity_confidence']:.2f}{amb}  proposals={r['proposals']} gaps={r['gaps']}")
        # Per-proposal detail (kind / method / date / basis) — what the review queue will show.
        props = (await s.execute(select(ProposedObligation).where(
            ProposedObligation.tenant_id == tenant.id).order_by(ProposedObligation.created_at))).scalars().all()
        print("\nProposed obligations (nothing is tracked until a human confirms):")
        for p in props:
            due = (p.proposed or {}).get("due_date") or "human-set"
            print(f"  [{p.flavor:<7}] {p.kind:<18} method={p.method:<5} due={due:<12} "
                  f"conf={p.confidence:.2f}")
            print(f"            basis: {(p.basis or '')[:100]}")
        print(f"\nGO/NO-GO: {ext['documents']} docs, {ext['proposals']} proposals, "
              f"{ambiguous_ct} ambiguous matches to resolve. Review the entity matches above "
              f"before wiring the confirm loop.")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
