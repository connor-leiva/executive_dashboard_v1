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
| Ask about this (the one network call: `_claude_call`) | `backend/app/services/cleanup_ask.py` |
| The Forum's months ahead, from its members' records (not the file) | `backend/app/services/cleanup_live.py` |
| Tables | `cleanup_dataset`, `cleanup_state` — `backend/app/models.py`, migration `0093_books_cleanup` |
| Audit category "Books" | `services/audit.py`; 0093 also rebuilds Postgres' `ck_audit_log_category` to allow it (without that, every save is a 500 in production and green everywhere else — `test_audit_categories.py` now checks the pairing) |
| The tab exists only while a file is loaded | `backend/app/services/tabs.py` (`_has_cleanup`) |
| Command-line loader | `backend/scripts/load_books_cleanup.py` |
| Frontend | `frontend/src/cleanup/` (shell `Cleanup.jsx`, one file per page) |
| Tests | `backend/tests/test_books_cleanup.py`, `test_books_cleanup_live.py`, `test_books_cleanup_forward_fixes.py`, `test_migration_0093_cleanup.py` |

## The pages

| Page | What it is |
| --- | --- |
| Statement | The corrected P&L by company (and The Edge, a brand inside the hub, on a cash basis), year or month: QuickBooks, the cleanup's change, corrected. Every line opens to *how the number is made*, in one sentence, and every dollar in it, month by month (below). Losses read "−$89,844 loss", never "($89,844)". |
| Transactions | Every transaction in the year, month by month, searchable, by company ("anything to do with beCollective": what it paid, and what the corrected books put on it), with *Needs you* (a decision, or a possible second copy) and *Changed by the cleanup* filters. |
| Decisions | The workbook's yellow cells: partner rates and the owner's salary, investment or loan, the revenue basis, the ad split, staff person by person and month by month, software and other shared costs item by item, the owner's charges one by one. |
| Corrections | The journal entries to post: one per company per month, plus January 1, in debits and credits to the cent, each line saying what it's for (below). The QuickBooks entry number is written against each once posted; the download is a QuickBooks journal-entry import file whose Description column carries the same words. |
| Balance sheet | Each company on the last day, as booked and corrected, with the accounts to create or retype. |
| Intercompany | What the companies owe each other, each brand's year, and each partner's earned-against-paid. Each pair opens to why the cleanup changes it, by reason, and every item behind that (below). |
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

## Changing one transaction

What the owners change most (Oct 19) isn't a transaction's category: it's which company it shows on. So
a transaction's card offers **Move it to another company** (or bill it back to Utah Life), **Split it
between companies**, **Personal, not business** (the owner's draw, or somebody else's to pay back), and
**Not a real charge** (paid twice and owed back; or, if it never cleared the bank, entered twice or never
happened), with **another line** and **another month** under "More…". Each says in one sentence, before
it's saved, what it will do, including who will owe whom.

A change is saved as `inputs.tx[key]` (owners and admins: it moves money between companies) and the
engine lays it over the reviewed ledger (`Books._effective`). It is the review's own placement rule
(`engine/corrections.py`) run again: the place the review gave each of the transaction's lines comes back
out, the new one goes in, each through intercompany when another company paid it. Every placement leaves
the paying company where QuickBooks has it and every other company even, so a change can't unbalance an
entry, and the pools' splits, the partners' shares and the brand views all read the changed ledger.
Its postings are their own part of each entry ("Your changes to single transactions").

Pinned by the tests (each kind on the invented file, where every figure can be worked by hand) and by a
harness run on the real file: every kind of change on every kind of transaction (own-paid, paid by
another company, shared, money in, a deposit with its fees), one at a time and all at once, 93 changed
books, every check passing. A moved transaction stays in sight where it was (struck through, "→ The
Forum"); Decisions lists every change in words with who, when, why, and Undo; the audit log says it in
the same words ("Minky Couture, Mar 24, $5,598.47: moved to The Forum").

## The months ahead (Forecast, Budget, Cash flow)

For the company the file projects (`forward`: beCollective, whose memberships are contracts): each
membership earned day by day over its 12 months, split into what members already paid and the
installments still to come; the installments, month by month; the launches by date window; the bank
balance to start from; and the Oct 19 meeting's assumptions as starting values. The engine
(`Books.forward`) projects from the CORRECTED books, so a Move changes the forecast too:

* **Forecast**: what's contracted, collected, earned and still to earn; the partner's share accrued on
  each calendar year's profit exactly as the corrected books accrue it (tied out with the mockup: with
  the defaults Julie earns $121,010 by Aug 2027 against $385,000 paid; with the meeting's retreats and
  10% commissions, $79,621); each launch's cost against what it contracted.
* **Budget**: every company's cost lines (so far, the last three months' pace, share of revenue); the
  projected company's monthly plan, editable, starting from that pace (and the meeting's no ads).
* **Cash flow**: the projected company's balance month by month from QuickBooks' Sep 30 balance
  (until the bank's own feed is connected): installments in, plan and assumptions out.
* **The Statement's "+ Future months"**: the projected company's year so far, still to come, and
  through the horizon.

The assumptions and the plan are saved like decisions (`inputs.forward`, `inputs.budget`; owners and
admins). They change projections only, never the books or the entries.

Each projection also shows **deferred revenue** (Forecast): what's been paid ahead of being earned, month by month
from the contracts' own position (collected less earned), beside what the corrected books carry in the company's
deferred and accrued-revenue accounts on their last day. Budget shows each projected company's lines **a member a
month** (the plan over its members).

## The Forum's months ahead, from its members' records

Added 2026-10-26, for Spring's action item: the same Forecast, Budget, Cash flow and future Statement for the Forum,
whose memberships renew every year. Its block isn't in the file: `services/cleanup_live.py` builds one, in the file's
`forward` shape, from what the app already holds, as it stands at the request:

* **which company**: the one the file keeps the dues schedule for (`dues.company`), when the workspace has a
  membership business with members on record and the file doesn't project that company itself. Nothing names it;
* **the members**: the membership business's active GHL members (`member`): each one's contract value
  (`total_cost`), renewal and enrollment dates, and how they pay (in full, monthly, quarterly, installments).
  The Edge's members are their own records and stay out; so do its charges, event tickets and sponsorships;
* **what they paid**: GHL Payments and the legacy Stripe account, deduplicated the way the Forum tab does it, matched
  on the CRM contact and then on the email; **what's scheduled**: their active subscriptions (`project_charges`).

A member's **term** is the twelve months before their renewal date, earned day by day (the books' rule for dues). On
the books' last day, what they had paid toward it against what had been earned is the paid-ahead (deferred) or
earned-ahead position; after it, the rest of the term is earned month by month (the paid part first), and what's
still owed comes in as it has since the books' end and then as their subscription schedules it, never more than is
owed. A member who **joined** since the books' end, or whose renewal is **already done** (the CRM's date moved on a
year, or they've paid since the renewal date), is a contract in hand, counted in full. Every other renewal in the
year ahead is **due**: counted at the renewal rate and paid the way that member pays.

Forecast's assumptions for it: the **renewal rate** (starting from the last twelve months' retention: of everyone who
was a member, the share still here), **new members a month** (none, to start) at **a year's value** (today's average),
joining each month after the books and paying the way today's members do; and the partner's pay, if the company has a
partner (starting from the last three months' pace; the share accrues after the owner's salary where the agreement
puts it first, as the books do). Cash flow starts from the corrected books' bank accounts on their last day. The
projection runs twelve months past the month the books end in.

The changes are saved under the company's name (`["forward", company, field]`, `["budget", company, line]`), so the
file's own projected company keeps the flat shape it was first saved in, and validated against the block built at
the save. **What the records can't place is said, not guessed**: a member with no renewal date or contract value is
left out and named, one with nothing paid toward the term and nothing scheduled is flagged (Forecast lists every
member, worst first, with what they paid by the books' end and since, what's scheduled, and their renewal), and the
deferred revenue the records imply is shown beside the books' own, with the difference: that is what to reconcile.
Add-on seats with no contract value of their own ride on a primary's and aren't flagged.

Pinned by `test_books_cleanup_live.py` on the invented file plus six invented members, each control broken on
purpose with a test failing every time. The file's own projection was compared before and after on the invented file
with five sets of assumptions: identical.

## What each posting is made of (Intercompany itemized, the entries in words)

Every posting the engine makes records what it is made of (`Books.src`): the review's reason for it, whose pay or
which subscription a shared cost's share is, the ads, the fees, the timing row, the partner, the owner's charge,
and, for a change made on the tab, the transaction it came from (`_effective` remembers the key behind each of
its postings). The arithmetic is untouched: the same additions in the same order, so every figure is identical
(checked on the real file: the engine before and after, as delivered, with every decision changed, and with seven
single-transaction changes of every kind, about 1.5 million characters of figures each, identical).

* **Intercompany, item by item.** Each pair carries its change by reason (`parts`), and opens
  (`GET /cleanup/intercompany/pair`, gzipped) to everything behind it, month by month in the tab's own transaction
  list, each amount in the pair's reading: each transaction the review put through the pair (its card can move it
  from there), each old journal entry the review undid (number, memo and why), each shared cost's share grouped
  by month (made of whose pay, which subscriptions), each change made on the tab in its own words. It adds up to
  the pair's change, and says so.
  The review's postings are per transaction only in a file that lists them: `interco.items` (one row per
  transaction, month and reason, on the pairs' accounts) and `interco.entries` (the old journal entries' words),
  written by the export from the review's own builder (`corrections.build` records `key_rows`; the export rebuilds
  the review and stops if it isn't the one `analysis.json` holds). An older file shows the review's moves by reason.
* **The entries in words.** Each line of each month's entry says what it's for (`corrections()[...]["said"]`),
  largest part first: "Shared costs split (your splits): Justin Nelson's pay; Recategorized within the company:
  Justin's fee: a cost, not money he owes (Oct 14)"; "Your changes to single transactions: Minky Couture, Mar 24,
  $5,598.47: moved to The Forum". Corrections shows it; the import file's Description carries it.

## Ask about this

A transaction's card, a Statement line's drawer and an Intercompany pair each offer **Ask about this**: a question
answered by Claude from the tab's own figures for that one thing (`POST /cleanup/ask`, `services/cleanup_ask.py`):
the transaction with its words and where the corrected books put it, the line with how it's made and its largest
rows, the pair with its reasons and its largest items, and the books' context. Never the database, never another
workspace. It answers in plain words, quotes the figures as given, says when the data doesn't say, and can't change
anything: when the question is really "change this", it names the tab's own action. A read: anyone with the tab
may ask. The key is the server's (`ANTHROPIC_API_KEY`, the model `ASSISTANT_MODEL`); without one the overview's
`can_ask` is false and the button isn't there. A short conversation per box; closing it forgets it.

## Who may do what

* **Reading** needs the `cleanup` tab — owners and admins have it; a member needs the grant.
* **The decisions** (splits, rates, salary, investment or loan, revenue basis, the owner's charges,
  and any change to a single transaction)
  move money between companies: **owners and admins only**, enforced by the server.
* **The work** (an answer, a tick, a posted entry number) needs only the tab.
* **Loading a file**: owners and admins.
* **Download saved changes** (`GET /cleanup/saved.json`, owners and admins): every saved change as one file
  (`axcion.books_cleanup_saved/1`: the loaded file's id and label, the version, the sparse `inputs`). With
  the loaded file, `Books(data, merged_inputs(data, inputs))` rebuilds every figure on the tab, so an
  analysis outside the app starts from what the owners actually saved -- except the Forum's projection, which is
  also made of the members' records as they stood (`services/cleanup_live.py`). Audited as `cleanup.export`.
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
