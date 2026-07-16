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
from app.models import Tenant, User, BinderDocument, JurisdictionRule
from app.seed import seed
from app.services import binder_ingest

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

        # Context the extraction step will match against.
        ent_ct = len((await s.execute(select(BinderDocument.id).where(
            BinderDocument.tenant_id == tenant.id))).all())
        rules = (await s.execute(select(JurisdictionRule).where(
            JurisdictionRule.tenant_id.is_(None)))).scalars().all()
        print(f"\nTenant '{args.tenant}': {ent_ct} documents already on file, "
              f"{len(rules)} system jurisdiction rules seeded.")
        print("NOTE: entities are user-created; if the tenant has none configured, the entity "
              "match step (Step 4) has nothing to match against. Configure entities first.\n")

        print(f"Ingesting {len(files)} file(s) from {args.folder} ...")
        res = await binder_ingest.ingest_batch(s, tenant.id, owner, files, uploaded_via="folder")
        print(f"  created={res['created']}  deduped={res['deduped']}  failed={res['failed']}  "
              f"sync_run={res['sync_run_id']}\n")
        for d in res["documents"]:
            if "error" in d:
                print(f"  [FAIL] {d.get('filename')}: {d['error']}")
            else:
                tag = " (dedup)" if d["deduped"] else ""
                print(f"  [{d['content_hash'][:10]}] {d['filename']}{tag}  "
                      f"category={d['category']}  pending_extraction={d['extraction_pending']}")

    # TODO(step 4 - binder_extract.py): for each ingested document, run extraction and print
    #   entity_name_guess + match candidates (the collision-handling proof), method (read/rule),
    #   proposed obligation (kind, due_date, cadence, confidence), and the gap/renewal flavor.
    #   That output is what turns this scaffold into the real go/no-go gate.
    print("\nIngestion path OK. Extraction proposals: TODO(step 4).")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
