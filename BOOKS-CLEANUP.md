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
| Statement | The corrected P&L by company (and The Edge, a brand inside the hub, on a cash basis), year or month: QuickBooks, the cleanup's change, corrected. Every line opens to *how the number is made*, in one sentence, and every dollar in it, month by month (below). Losses read "−$89,844 loss", never "($89,844)". |
| Transactions | Every transaction in the year, month by month, searchable, by company ("anything to do with beCollective": what it paid, and what the corrected books put on it), with *Needs you* (a decision, or a possible second copy) and *Changed by the cleanup* filters. |
| Decisions | The workbook's yellow cells: partner rates and the owner's salary, investment or loan, the revenue basis, the ad split, staff person by person and month by month, software and other shared costs item by item, the owner's charges one by one. |
| Corrections | The journal entries to post: one per company per month, plus January 1, in debits and credits to the cent. The QuickBooks entry number is written against each once posted; the download is a QuickBooks journal-entry import file. |
| Balance sheet | Each company on the last day, as booked and corrected, with the accounts to create or retype. |
| Intercompany | What the companies owe each other, each brand's year, and each partner's earned-against-paid. |
| To do / Findings | The checklist and open items (answered here), and the audit's findings and answered questions. |

## A transaction, the way the owners read it

The Oct 19 review (Spring and Connor, on a mockup) settled how a transaction reads, everywhere the tab
lists them (`frontend/src/cleanup/CleanupTx.jsx`, used by a Statement line's drawer and by
Transactions):

* **five columns up front**: Date · Payee and the account it was paid from · *For* the company and
  the P&L line it's filed to · What it was · Amount;
* **month by month, oldest first**, each month with its count and total;
* **a quiet ⋯** on each row (or a click anywhere on it) opens a card with what the columns don't say:
  what the cleanup changed, how QuickBooks had it, the review's note, and a possible second copy;
* in a line's drawer, the shares of shared costs, timing, dues and partner shares are **one row per
  month per kind** ("Shared staff", "Held for later months"), each card saying what it's made of;
* sized by the list's own width (CSS container queries), so a drawer on a phone reads as cards.

The words are the file's. `books_cleanup_out/engine/tx_words.py` (outside the repo, with the data)
gives each transaction a tidy payee (one spelling per vendor), the account it was paid from, the
review's description without the audit workbook's row numbers, what changed, and a possible second
copy: the same payee and amount within ten days, money out, $25 or more, and not a charge that
recurs (unless two companies paid it within three days); the review's own explanation ("two movers,
$489.25 each") marks a pair checked. An older file without the words still reads, on its QuickBooks
payee.

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

That is how v12 went into production on 2026-10-19: `railway ssh` forwards piped input (the
base64 file, ~2.2 MB, arrives whole), so run it once without `--apply` and compare the net incomes
and checks it prints with the workbook before running it with `--apply`. The temporary copy is
deleted either way.

A new file keeps every decision already made on the tab; anything the new file adds arrives at its
own value. Earlier files are kept, not replaced.

## Not done

* Posting to QuickBooks. By decision, the app never writes to QuickBooks: the entries go in by
  hand or by QuickBooks' own journal-entry import, and their numbers are recorded here.
* A history view of earlier uploads (they are kept in `cleanup_dataset`; nothing shows them yet).
* The rest of the Oct 19 mockup, in this order: **Move** (a transaction to another company, split,
  personal, not real, another line or month, recomputed into the books and the entries, listed on
  Decisions with undo); **the forward view** (Forecast, Budget, Cash flow, and the Statement's future
  months); **the finishing** (Intercompany itemized, entry descriptions that name each change, Ask
  about this). The file already carries what Move needs (`accounts.ic_revenue`, `prepaid`,
  `accrued_liab`, each transaction's bank lines).
