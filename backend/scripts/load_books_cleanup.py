"""Load a Books Cleanup file into a workspace: the command-line twin of the tab's upload.

The file is the audit's result (schema axcion.books_cleanup/1) and holds the company's real books,
so it is never in the repository. It is checked exactly as an upload is -- it must build -- and
stored as the workspace's current file; the decisions already made on the tab are kept.

Dry run by default: it builds the books and prints what the tab will open on. --apply stores it.

  python -m scripts.load_books_cleanup --tenant springb --file ../books_cleanup_out/axcion_cleanup_v12.json
  python -m scripts.load_books_cleanup --tenant springb --file ... --apply

The FIRST file has to come this way (or as a direct POST to /cleanup/dataset): the tab, and its
upload button, only exist once a file is loaded. In production the file has to reach the container
first; pipe it rather than copying it anywhere (`railway ssh` forwards stdin -- this is how v12 was
loaded on 2026-10-19; see BOOKS-CLEANUP.md):

  base64 -w0 file.json | railway ssh --service executive_dashboard_v1 \\
      "base64 -d > /tmp/c.json && python -m scripts.load_books_cleanup --tenant springb --file /tmp/c.json --apply \\
       && rm /tmp/c.json"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib

from sqlalchemy import select

from app.db import SessionLocal
from app.models import CleanupState, Tenant
from app.routers.books_cleanup import load_dataset
from app.services import books_cleanup as eng


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tenant", required=True, help="the workspace's slug")
    ap.add_argument("--file", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    payload = json.loads(pathlib.Path(a.file).read_text(encoding="utf-8"))
    async with SessionLocal() as s:
        tenant = (await s.execute(select(Tenant).where(Tenant.slug == a.tenant))).scalar_one_or_none()
        if tenant is None:
            raise SystemExit(f"No workspace {a.tenant!r}.")
        st = await s.get(CleanupState, tenant.id)
        bk = eng.Books(payload, eng.merged_inputs(payload, st.inputs if st else None))
        print(f"{payload.get('label')}: {len(payload['ledger'])} ledger lines, period "
              f"{payload['period']['start']} to {payload['period']['end']}")
        for c in bk.companies:
            print(f"  net income, corrected: {c:<20} {bk.net_income(c):>14,.2f}")
        for ch in bk.checks():
            print(f"  {'OK ' if ch['ok'] else 'NO '} {ch['label']}")
        if st:
            print(f"  keeps the decisions already made on the tab (version {st.version})")
        if not a.apply:
            print("dry run: nothing stored (pass --apply)")
            return
        ds = await load_dataset(s, tenant.id, payload)
        await s.commit()
        print(f"stored {ds.label} as {tenant.slug}'s Books Cleanup file ({ds.id})")


if __name__ == "__main__":
    asyncio.run(main())
