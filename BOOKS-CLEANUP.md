# Books Cleanup — build record

A one-off tab for a workspace that has taken a year of books apart, transaction by transaction,
and is putting them back together in QuickBooks. It replaced the audit workbook that did that job
in a spreadsheet: the same statements, laid out like Books → Statement, with the decisions
editable, the numbers recomputed live, and the bookkeeper's work (entries posted, open items
answered, checklist ticked) tracked and audited.

Built 2026-10-19 for Spring B (the `springb` workspace), from the v12 audit workbook.

## How it fits together

```
the audit (outside the repo)          the app
───────────────────────────           ──────────────────────────────────────────────────────
reviewed ledger + QuickBooks   ──►   one JSON file (schema axcion.books_cleanup/1)
P&L, trial balances, timing,          │  uploaded on the tab, or scripts/load_books_cleanup.py
the decisions' starting values,       ▼
and the words (open items,          cleanup_dataset  (each upload kept; the newest is in force)
findings, questions, checklist)     cleanup_state    (only what people changed, laid over the file)
                                      │
                                      ▼
                              services/books_cleanup.py  ──►  routers/books_cleanup.py  ──►  frontend/src/cleanup/
                              (the workbook's arithmetic)      (/api/v1/cleanup/*)            (the tab)
```

**The file holds real financials, so it is never in the repository.** It is produced by
`books_cleanup_out/engine/export_axcion.py` (git-ignored, with everything else in that folder)
and reaches the app by upload. The repository's tests use an invented three-company file.

**Nothing in the engine names a company, a person or an account.** They all arrive in the file,
and so do the words the tab shows (what each decision means, the checklist's headings, the
recommendation text). A different workspace's cleanup would be a different file.

## Where things live

| Concern | Path |
| --- | --- |
| The engine (corrected P&L, entries, balance sheets, intercompany, checks) | `backend/app/services/books_cleanup.py` |
| API, mounted at `/api/v1/cleanup` | `backend/app/routers/books_cleanup.py` |
| Tables | `cleanup_dataset`, `cleanup_state` — `backend/app/models.py`, migration `0093_books_cleanup` |
| Audit category "Books" | `services/audit.py`; 0093 also rebuilds Postgres' `ck_audit_log_category` to allow it (without that, every save is a 500 in production and green everywhere else — `test_audit_categories.py` now checks the pairing) |
| The tab exists only while a file is loaded | `backend/app/services/tabs.py` (`_has_cleanup`) |
| Command-line loader | `backend/scripts/load_books_cleanup.py` |
| Frontend | `frontend/src/cleanup/` (shell `Cleanup.jsx`, one file per page) |
| Tests | `backend/tests/test_books_cleanup.py`, `test_migration_0093_cleanup.py` |

## The pages

| Page | What it is |
| --- | --- |
| Statement | The corrected P&L by company (and The Edge, a brand inside the hub, on a cash basis), year or month: QuickBooks, the cleanup's change, corrected. Every line opens to *how the number is made* (QuickBooks → each kind of correction → corrected) and every dollar in it. |
| Decisions | The workbook's yellow cells: partner rates and the owner's salary, investment or loan, the revenue basis, the ad split, staff person by person and month by month, software and other shared costs item by item, the owner's charges one by one. |
| Corrections | The journal entries to post: one per company per month, plus January 1, in debits and credits to the cent. The QuickBooks entry number is written against each once posted; the download is a QuickBooks journal-entry import file. |
| Balance sheet | Each company on the last day, as booked and corrected, with the accounts to create or retype. |
| Intercompany | What the companies owe each other, each brand's year, and each partner's earned-against-paid. |
| To do / Findings | The checklist and open items (answered here), and the audit's findings and answered questions. |

## Who may do what

* **Reading** needs the `cleanup` tab — owners and admins have it; a member needs the grant.
* **The decisions** (splits, rates, salary, investment or loan, revenue basis, the owner's charges)
  move money between companies: **owners and admins only**, enforced by the server.
* **The work** (an answer, a tick, a posted entry number) needs only the tab.
* **Loading a file**: owners and admins.
* Every change is audited (`cleanup.change`, category Books) with its path, old value and new one.

## Correctness

The engine was tied out against the recalculated workbook cell for cell — P&L by company and by
month, The Edge, all three companies, every correction cell, the balance sheets, intercompany and
the partner shares — on v12 as delivered (2,449 comparisons), on a copy with every decision
changed (2,453), and on the old-books revenue basis: **zero differences**. The tie-out harness is
outside the repository with the data it compares.

Built into the engine, and pinned by the tests:

* every entry balances **to the cent** — QuickBooks refuses an import a cent out, so an entry that
  balances before rounding carries its rounding on its largest line, and one that doesn't is shown
  as not balancing rather than rounded into it;
* QuickBooks plus the entries is the corrected P&L, month by month, for every company;
* both balance sheets balance, as booked and corrected, whatever the decisions;
* a partner's share accrues on the year to date (after the owner's salary where the agreement puts
  it first), so a loss month takes back only what was accrued.

## Loading the file into production

The tab appears in a workspace's rail as soon as a file is loaded, and its **Load a new file**
button (owners and admins) loads every file after that. **The first one can't come through the
tab**, because the tab doesn't exist yet. It goes in from the command line, or as a direct `POST
/api/v1/cleanup/dataset` (multipart `file`) with an owner's session, which needs no tab:

```
cd backend
python -m scripts.load_books_cleanup --tenant springb --file ../books_cleanup_out/axcion_cleanup_v12.json   # dry run, local
base64 -w0 ../books_cleanup_out/axcion_cleanup_v12.json | railway ssh --service executive_dashboard_v1 \
  "base64 -d > /tmp/c.json && python -m scripts.load_books_cleanup --tenant springb --file /tmp/c.json --apply && rm /tmp/c.json"
```

The second command has not been run against Railway yet: `railway ssh` takes a command, but that it
forwards piped input is an assumption until someone sees it work. Check with a harmless pipe first
(`echo ok | railway ssh --service executive_dashboard_v1 cat`).

A new file keeps every decision already made on the tab; anything the new file adds arrives at its
own value. Earlier files are kept, not replaced.

## Not done

* Posting to QuickBooks. By decision, the app never writes to QuickBooks: the entries go in by
  hand or by QuickBooks' own journal-entry import, and their numbers are recorded here.
* A history view of earlier uploads (they are kept in `cleanup_dataset`; nothing shows them yet).
