# Allocation Rules: audit, spec and build guide

Written 2026-10-09 from a fresh read of `main` (models, services, routers, migrations, tests and
the Books frontend), one month after the first audit (`docs/allocation-rules-audit.md`,
2026-09-09). Nothing was written to QuickBooks, Meta or the database.

> **The ask:** an allocation rules module for Books. Every shared cost carries a declared rule
> that says who it belongs to, whether that is a fact or a judgment (four tiers), why, and from
> when. Rules are append-only and versioned so any month can be reconstructed. Monthly drivers
> feed the judgment rules. Anything no rule claims waits in an exception queue. Partners can see
> why a cost sits on their entity. Visual reference: `Allocation Rules v1.dc.html` (six screens
> plus the create and edit drawer) and `docs/allocation-rules-spec.md` (its design spec).

This document is the plan and, as phases ship, the build record (§12).

House format for this document and for every UI string it specifies: no em dashes and no en
dashes. Hyphens, commas and parentheses only.

**The headline finding.** The engine half-exists and the hard half does not. Allocation rules,
contributions, a rebuild route and a close check shipped in Phase 4 of the COA work, and the
Statement already composes both `observed` and `applied` contributions. But every rule today is
one funder to one carrier, matched on an account prefix, evaluated per dashboard window rather
than per month, and edited in place. A 50/30/20 split, a vendor rule, an April rule change and a
March restatement are all impossible on the current model. Phases 1 to 4 fix the foundation
before any screen is built.

**The good news about Section 5 of the brief.** The tie-out invariant runs on the mapped rollup
*before* any contribution is composed (`coa_balances.py:456` and `:467`, then contributions are
read at `:471`). Applied
allocations cannot break it, the portfolio zero-sum holds by construction, and the Statement
already returns both `as_booked` and `as_allocated` with both net-income figures. The "show
both, defer the decision" choice Connor made at the design gate is what the code already does.
See §9 D10.

---

## 0. How to use this document

For Claude Code, in order:

1. Read §2 (findings) and §3 (standing rules) before writing anything. Every BLOCKER in §2 is
   resolved by a named phase.
2. Build phases in order. Each ends in a **Done when** that is checkable from outside the code.
   "Tests pass" is never one. Do not start a phase until the previous one's gate is met and
   recorded in §12.
3. Re-check the alembic head at the start of Phase 1. It was `0092_scorecard_metric_src_spec` on
   2026-10-09. If it has moved, renumber and say so in §12, as `0084_payables_vendor` did.
4. §9 lists decisions made on Connor's behalf. Build to them. §11 lists what only Connor can
   decide. Build around those as first-class pending states; never default a value for one.
5. The mockup is a reference for layout, hierarchy, copy and states. It is not code to port. Its
   figures and rule rationales are illustrative (see §7.8) and must not reach production.

**This supersedes** `docs/allocation-rules-spec.md` §5 (build order) and §6 checks 6 to 9, which
name migration `0068`. The head has moved 25 revisions since.

---

---

## 0.1 Amendments, 2026-10-27

The body below was written on 2026-10-09. Eleven commits landed after it, including the whole
Books Cleanup tab and the Sales Desk commissions module, and the alembic head moved from
`0092_scorecard_metric_src_spec` to `0095_agent_roster_dates`. A re-verification of all sixteen
findings against `main` on 2026-10-27 confirmed every one of them as still real, corrected five,
retired two, and found nineteen things the body cannot know. Connor approved all twenty-seven
amendments on 2026-10-27.

**Where an amendment conflicts with the body, the amendment wins.** The body is kept as written
so the original reasoning stays legible.

### The document

1. This document now lives in the repo at the root, beside the other `*-SPEC.md` files. Phase 0's
   gate could not close while it existed only as a handoff.
2. Stop citing `models.py` and `sync.py` by line. Both churn: four commits each in eighteen days.
   `AllocationRule` 2489 to 2569, `AllocationContribution` 2525 to 2605, `BookTxn` 1626 to 1632,
   `Vendor` 3195 to 3275, `SalesCall.is_current` 1967 to 1973, and `_QBO_PERIODS` moved far. Cite
   the class or the function.

### Migration and Phase 1

3. The migration is `0096_alloc_rule_versions`, revising `0095_agent_roster_dates`. `0093` is
   taken by `0093_books_cleanup`, and `0094_commissions` and `0095_agent_roster_dates` chain
   after it.
4. **Acceptance check A2 was a false green.** It globbed `backend/alembic/versions/0093*`, which
   now matches someone else's clean migration: it passed with no allocation code written at all,
   and would have kept passing if the allocation migration used JSONB. It now globs `0096*` and
   asserts that exactly one file matches.
5. **Phase 1's gate could not pass as written.** It stamped `0092` and upgraded, but `seed()`
   runs `create_all` and `0095_agent_roster_dates` is unguarded, so the upgrade fails on a column
   that already exists. The gate now stamps `0095_agent_roster_dates`.
6. Phase 1's gate adopts the stronger one already in the repo:
   `tests/test_migration_0093_cleanup.py` asserts the migration's columns EQUAL the model's,
   pins `down_revision`, and proves idempotence by upgrading twice and downgrading twice in an
   isolated database. Model `test_migration_0096_alloc.py` on it.
7. F12 overstated the limit. Revision ids must fit `VARCHAR(32)`; 30 is the house margin, held by
   all 95 revisions (eight sit exactly at 30, none exceeds it). The post-mortem is
   `0046_grandfather_defaults`, which shipped at 34 characters and crash-looped AFTER its body
   had run. CI already enforces it: `tests/test_migration_0046.py` sweeps every file asserting
   `len <= 32`.

### Two findings are retired

8. **F13 and D11 are obsolete. Audit with `category="Books"`.** D11 chose `Config` specifically to
   avoid a Postgres-only CHECK rebuild. `0093_books_cleanup` already performed that rebuild from
   the whole `AUDIT_CATEGORIES` set, and a `Books` category now exists. Acceptance check A14 was
   enforcing the wrong category and is corrected.
9. **The journal-entry export already exists.** `/cleanup/corrections.csv` emits a QuickBooks
   journal-entry import file: one entry per month, each line a debit or a credit, QBO's own column
   names. Add `GET /books/allocations/entries.csv?period=` reusing `_journal()` rather than
   inventing it, and `api.js:getBlob` on the frontend, which records why a plain `<a href>` will
   not do (a navigation carries no Authorization header).

### Connor's four decisions, 2026-10-27

10. **Cutover.** Books Cleanup is authoritative for 2026 through 31 October. This module is
    authoritative from 1 November 2026. Both engines exist; the date, not the tab somebody opened,
    decides which answers.
11. **Rounding lands on the largest line,** not the pool entity. This supersedes D5. QuickBooks'
    journal-entry import expects it and `books_cleanup._parts` already does it.
12. **Shares are fractions 0 to 1,** not percent strings summing to 100, matching the house
    convention set by `books_cleanup.pct`. Changes section 4.2 `targets`, section 5.4, and the
    drawer's running total in section 7.4. The drawer still accepts "50" in the box.
13. **QuickBooks line detail is stored.** A `book_txn_line` child table lands in the same
    migration, which makes Phase 1 larger than the body describes. This costs no extra QuickBooks
    calls: `books_sync` already receives `obj["Line"]` and discards everything after the first
    category line, keeping the header amount. That discard is what overstates a multi-line
    transaction against its first category, and storing the lines is what makes the Statement's
    evidence tie to the trial balance.

### The engine the body does not know about

14. **Section 1 gains "the other allocation engine."** `services/books_cleanup.py` is a second,
    complete, shipped engine: per-month pools, per-person-per-month-per-brand staff splits,
    per-item splits, an ads split, a payment-processing split, and a `follows_wages` weighted
    driver. It shares nothing with `coa_alloc.py`. Amendment 10 settles which is authoritative.
15. `rate()` is not "THE one place a percentage becomes money" product-wide. `books_cleanup._parts`
    already is one, in float, rounding onto the largest line. `rate()` is the one place in THIS
    module.
16. **`split_not_100` is a refusal, not a pending reason.** It moves out of the pending set in
    section 5.4 and into `validate_version`. A stored split that cannot resolve is a bug, not a
    state, and `books_cleanup.pct` already refuses at save with a plain sentence.
17. Reuse `frontend/src/cleanup/kit.jsx`, which already exports `Seg`, `Sheet`, `NumberField`
    with `SaveLine`, `Note` and `Pill`, rather than assembling equivalents. Section 7.1's pointer
    at `BooksPayables.jsx` `SegNav` is the older precedent.
18. Container queries are allowed inside the drawer; media queries stay for the page shell.
    `BOOKS-CLEANUP.md` states the reason: a drawer at phone width should read as cards. A12 now
    accepts either.

### Three findings restated

19. **F1 is not "no monthly grain."** Three of the five window keys ARE calendar months. The gap
    is that there is no COMPLETE, STABLE monthly series from the fiscal year start: the set is a
    rolling three-month tail plus QTD plus YTD, so April is unreachable from October and each
    month silently leaves the vocabulary as the clock moves. Both halves need fixing and one does
    not fix the other.
20. **F8 was the right fix for the wrong reason.** `pytest-xdist` is not installed and the suite
    runs in one process, so another module's tests cannot interleave. The real cost is
    cross-TENANT: an unscoped clear makes "workspace A must not see workspace B's rules"
    impossible to write. `_clear` already took `tenant_id` and never used it. Fixed 2026-10-27.
21. **F14's fix is not "pick one canonical key set."** Seed has three keys, production has five,
    and keys are minted at runtime from typed entity names, so no migration can converge them.
    Delete `ENTITY` and `ent()` from `books/ui.jsx` and feed entity names and colours from the
    payload, which already carries them.
22. **F15 is re-aimed.** Person data does exist: the Cleanup people-splitter does per-person,
    per-month, per-entity shares with a sum-to-100 gate and a wage-weighted derived driver. The
    gap is that it is a per-tenant JSON payload with no model behind it. Generalize the working
    prior art; do not greenfield.
23. **D15 does not resolve F11.** Three of five businesses share the accent `#227175` (Sympli,
    The Forum, beCollective), so sourcing the dot from `Business.accent` trades a two-way colour
    collision for a three-way one. Connor's call, 2026-10-27: not a concern. D15 stands, the
    collision is recorded, and tier remains carried three ways so colour is never the only signal.

### The commissions module

24. Section 5.6 gains a `no_entity` gap code. Commission bills create expense rows with a NULL
    `business_id`, so without it the commission bill is the one shared cost the queue cannot see.
25. D2 should say why append-only versions beat the snapshot pattern commissions uses. Freezing
    one rate onto a payment row is right for one rate and cannot express a split that changes
    retroactively and must stay reconstructable per month.
26. Every month-grain payload and route carries a `"YYYY-MM"` label alongside the
    `(period_start, period_end)` tuples, which stay as the internal key because
    `AccountPeriodBalance` and `AllocationContribution` are already keyed that way. Commissions
    and the Cleanup frontend both speak `"YYYY-MM"`.
27. Section 12's Phase 0 record is filled below, from a read-only run against production on
    2026-10-27.

---

## 1. What exists today

### 1.1 The allocation engine (Phase 4, shipped)

| Piece | Where |
|---|---|
| `AllocationRule` | `models.py:2489`. One `source_business_id` (funder), one nullable `target_business_id` (carrier, NULL = every entity), `pattern` String(300), `pool_name`, `basis`, `driver_source`, `approved_by`, `je_ref`, `is_active`. |
| `AllocationContribution` | `models.py:2525`. One row per period, standard account and entity pair. `booking` is `observed` or `applied`. `rule_id` FK. `ck_alloc_not_self` CHECK. |
| Migration | `0043_allocations.py` |
| Service | `services/coa_alloc.py`: `match_alloc_rule` (longest prefix), `sync_allocations` (delete-then-derive per period), `contributions_for`, `allocation_check`, `create_rule`, `update_rule`, `delete_rule`, `list_rules` |
| Routes | `routers/books.py:367-447`: GET/POST `/allocations/rules`, PATCH/DELETE `/allocations/rules/{id}`, POST `/allocations/rebuild`, GET `/allocation-check` |
| Worker | `services/sync.py:1761-1764` calls `sync_allocations` for every `balance_periods()` key |
| Tests | `tests/test_coa_alloc.py`, 13 tests |

**Observed, not applied.** The shared costs are already in QuickBooks as `Shared Service
Expenses:` sub-accounts on The Forum and beCollective, and the funder books a receivable rather
than the expense. So `sync_allocations` only ever writes `booking="observed"`, and a contribution
describes what part of a line someone else funded. Nothing writes `applied` yet.

### 1.2 The Statement already composes applied rows

`build_mapped_statement` (`coa_balances.py:377`) does, per standard account:

```
in_observed  = sum(inbound where booking == "observed")
in_applied   = sum(inbound where booking == "applied")
out_applied  = sum(outbound where booking == "applied")
as_allocated = direct + in_applied - out_applied
as_booked    = direct - in_observed
```

and returns `as_booked`, `as_allocated`, `net_income_booked`, `net_income_allocated`,
`ic_share_pct`, `flagged` and, on flagged lines, `contributions` via `_contribution`
(`coa_balances.py:613`). The tie-out (`:456`) and the orphan check (`:467`) both run on
`mapped_total` before contributions are read. **An applied contribution moves `as_allocated` on
both sides and nothing else.**

`BooksStatement.jsx:81` `Composition` already renders each contribution with direction in
words, counterparty, pool, basis, driver source, approver and JE ref.

### 1.3 Periods are dashboard windows, not months

`balance_periods()` (`coa_balances.py:119`) returns `books_sync._pl_line_periods()`: the four
windows in `sync.py:1353` `_QBO_PERIODS = ("mtd", "qtd", "ytd", "last_month")` plus the month
before last month. `AccountPeriodBalance` and `AllocationContribution` are keyed on those
`(period_start, period_end)` tuples, and `contributions_for` matches on equality.

Period activity is the difference of two as-of trial balances, cached per date
(`coa_balances._as_of`, `sync_trial_balances`). A YTD window is one row, not twelve.

### 1.4 Matching is account-level only

`match_alloc_rule` matches a normalized FQN prefix against `CoaMap.qbo_account_fqn`.
Transactions exist separately in `BookTxn` (`models.py:1626`): `business_id`, `txn_date`,
`amount`, free-text `payee`, `memo`, `account_qbo_id` (one category), `qbo_type`, and
`flags.multi_line` when a transaction has more than one category line (`books_sync.py:159`).
`BookTxn` backfills from `_backfill_start` (`books_sync.py:176`), which defaults to Jan 1 of the
current year and is still an open item (its own docstring: SPEC Part 11 #4).

`Vendor` (`models.py:3195`, Payables) carries `name_norm` from `binder_extract.normalize_name()`
and a `default_business_id`. It is the payee master for bills, not for cleared transactions.

### 1.5 What a Tier 2 source can read

| Source | Exists | Notes |
|---|---|---|
| Meta spend by campaign | **Yes** | `AdInsightDaily` (`models.py`, level `campaign`, `spend`, `occurred_on` in the ad account's timezone) joined to `AdCampaign.name`. `ads_rules.classify_campaign` is a pure prefix/contains matcher with `_norm` separator handling. |
| Google Workspace seats | No | `google_auth.py` is sign-in only. |
| Amex cardholder detail | No | Nothing reads card statements. |
| Per-person W-2 wages | No | Payroll arrives as lumped entries. Only contractors appear by name, as `BookTxn.payee`. |

### 1.6 Frontend

| Piece | Where | Notes |
|---|---|---|
| Sub-nav | `books/Books.jsx:18` `SubNav`, `PAGE_TITLE` | Seven pages, internal state, `isCFO` from role |
| Data hooks | `books/useBooks.js` `useEndpoint(path, sample, deps)` | Pairs payload with path; sample fallback when `VITE_API_BASE` unset |
| Samples | `books/sampleBooks.js` | Offline payloads |
| Ledger palette | `theme.js` `LEDGER`, `LEDGER_FONT` | Composed-row gold: `flagBg`, `flagBgHover`, `flagEdge`, `flagRing`, `flagInk` |
| Responsive pattern | `BooksStatement.jsx:719-735` | One `<style>` block, class names plus media queries, never JS viewport measurement |
| Data states | `books/ui.jsx` `StatePanel` | loading, error with retry, empty |
| Segmented sub-view | `BooksPayables.jsx:67` `SegNav` | Precedent for a page with internal views |
| Entity labels | `books/ui.jsx` `ENTITY` | Hardcoded per key. See F11. |

### 1.7 What changed since 2026-09-09

- Head moved from `0067_member_agent_email` to `0092_scorecard_metric_src_spec`.
- Payables shipped (`0084` to `0087`): `Vendor`, and `vendor_bank_account` which is
  append-only by design with a `superseded_by` chain. A second in-repo precedent for §4.
- Acumyn became Axcion (`0079_rebrand_stored_names`, `AXCION-REBRAND-SPEC.md`). Comments and
  audit labels that say Acumyn are historical and stay.
- `0092` documents that revision ids must stay at or under 30 characters
  (`alembic_version.version_num` is VARCHAR(32), and a longer id stamp-fails on Postgres only).
- Model line numbers moved. Allocation models are now at `models.py:2489` and `:2525`.

---

## 2. Findings

Each has a citation. **BLOCKER** means the module cannot work until a phase resolves it.

| # | Finding | Evidence | Resolved by |
|---|---|---|---|
| F1 | **BLOCKER.** No monthly grain. A rule that changed in April cannot be applied correctly to a YTD window, and a driver entered per month has nothing to attach to. | `_QBO_PERIODS`, `balance_periods()`, `contributions_for` equality match | Phase 2 |
| F2 | **BLOCKER.** Rules are edited and deleted in place. `update_rule` is a `setattr` loop; `delete_rule` is a hard delete; both routes are live. A March restatement is impossible and an old split cannot be reconstructed. | `coa_alloc.update_rule`, `coa_alloc.delete_rule`, `books.py:407`, `:419` | Phase 3 |
| F3 | **BLOCKER.** A rule has exactly one target. A three-way split cannot be expressed, and `create_rule` refuses source equal to target, so "this cost is Forum's and Forum already books it" cannot be declared either. | `AllocationRule.target_business_id`; `coa_alloc.create_rule` ("An entity cannot allocate a cost to itself") | Phases 1, 3 |
| F4 | **BLOCKER.** Account-prefix matching only. Vendor rules (Qualifi, Mighty Networks, Stan Store) and per-person staff rules need transaction-level matching on payee. | `match_alloc_rule`; `BookTxn.payee` | Phase 4 |
| F5 | The contribution sign is documented two ways. The model says "Always positive"; the service stores signed amounts on purpose so a refund of a shared cost keeps its effect. The service is right. | `AllocationContribution.amount` comment vs the `sync_allocations` docstring | Phase 1 (fix the model comment only) |
| F6 | Applied rows cannot break the tie-out. Not a blocker; it is the reason Section 5 needs no engine change. | `coa_balances.py:456-471` | §9 D10 |
| F7 | Double-count risk. If an applied rule splits a pool on the funder while the same cost already sits on the carrier as an observed shared-service charge, the carrier is charged twice in `as_allocated`. | `as_allocated = direct + in_applied - out_applied` with `direct` already including observed | Phase 4 check |
| F8 | Test fixture deletes are unscoped. `_clear` runs `delete(AllocationContribution)` and `delete(AllocationRule)` with no WHERE. New allocation tests on the shared SQLite file will fail in order-dependent ways. | `tests/test_coa_alloc.py` `_clear` | Phase 0 |
| F9 | A multi-line `BookTxn` carries one `account_qbo_id`. Auto-claiming it by payee would misattribute every line after the first. | `books_sync.py:159`, `coa_balances.py:800` | §9 D7 |
| F10 | Retroactive reach is bounded by the `BookTxn` backfill start, which defaults to Jan 1 of the current year and is unconfirmed. | `books_sync.py:176-182` | §11 |
| F11 | Entity labels and dots are hardcoded per key in the frontend. `springb` reads "Spring B", and daffodil is the dot for both Spring B and The Forum. | `books/ui.jsx` `ENTITY` | §7.2: allocation views use `Business.name` from the payload |
| F12 | Revision ids over 30 characters stamp-fail on Postgres only. | `0092` docstring | §4 |
| F13 | `audit()` takes a `category` defaulting to "System". Adding a category requires a Postgres-only CHECK rebuild migration that SQLite tests cannot see. "Config" already exists. | `services/audit.py:20-33` | §9 D11 |
| F14 | Business keys differ between seed, tests and frontend: seed has `ulrg`, `springb`, `sympli`; the alloc test fixture adds `the_forum`; `ENTITY` uses `forum` and `becollective`. The `Business` docstring also mentions an "operational-only springb holder after the QBO account split". Which business row carries SB Coaching's realm in production is not knowable from the repo. | `seed.py:215-224`, `test_coa_alloc.py`, `models.py:1366` | Phase 0 inventory |
| F15 | No person model for staff drivers. Contractors appear only as `BookTxn.payee`; W-2 wages per person are not in QuickBooks detail. | §1.5 | §4.3 `subject`, §10 |
| F16 | The worker derives contributions for window keys only. Applied month rows need their own pass. | `sync.py:1761-1764` | Phase 4 |

---

## 3. Per workspace, and the standing rules

Everything is per tenant. **Nothing in this build may reference a business key, entity name,
vendor name, account name or percentage as a literal.** The figures in the brief are fixture
data only (Appendix B).

| What | Configured in |
|---|---|
| Which entities carry shared costs (feeds the exception queue) | `coa_settings.alloc_pool_business_ids` (§4.5). NULL means the queue renders its setup empty state. |
| Rule scope, tier, split, driver, source, rationale, start | `allocation_rule` plus `allocation_rule_version` (§4.1, §4.2) |
| Driver definitions and monthly values | `allocation_driver`, `_commit`, `_value` (§4.3) |
| Meta campaign to entity mapping | `allocation_rule_version.source_spec` (§5.4) |
| Entity display names | `Business.name`, edited in Settings (`routers/businesses.py`) |
| Who can read | the `books` tab grant (`books_user = require_tab("books")`, `books.py:20`) |
| Who can write rules, versions, driver commits, settings | `require_role("owner", "admin")` |

**Standing rules.** Each has bitten this repo before.

| Rule | Why |
|---|---|
| One alembic head before shipping. `alembic heads` prints exactly one line. | A fork crash-loops Railway on boot. |
| Revision id at most 30 characters. | `0092`: Postgres-only stamp failure. |
| Every migration step guarded (`_tables()`, `_has_column`, `_indexes`), indexes ensured outside `create_table` guards. | `0001_init` runs `create_all()` of current models, so fresh databases already have the tables. Pattern: `0043`, `0084`, `0092`. |
| `GUID()` and `JSONType` from `app/dbtypes.py` only. No `sa.Enum`, no `JSONB`, no Postgres-only types. | SQLite test suite. |
| Enum-like values are `String(n)` with a comment listing the values. Validated in the service, not by CHECK. | Repo convention; CHECKs are Postgres-only and invisible to tests. |
| Every new `String(n)` states its longest realistic value (§4 tables). | SQLite ignores length; Postgres errors in production. |
| A JSON column is copied before mutation. | Mutating the loaded dict makes SQLAlchemy skip the UPDATE silently. |
| Every compute entry point takes an injected `today: dt.date`. | Tests time-travel across month and fiscal boundaries. |
| Every test `delete()` is scoped by `tenant_id`. | F8. |
| QuickBooks is read-only. Applied contributions are never written back. | Module invariant, stated in `coa_alloc.py`. |
| No arithmetic in the browser beyond summing entered percentages for the input total. Every displayed figure is the server's. | `BooksStatement.jsx`: "NO ARITHMETIC LIVES HERE". |
| `frontend/src/books/` contains no raw hex and no font family names. Colors from `LEDGER` (and `T` where the Statement already uses it); type from `LEDGER_FONT`. | `theme.js` comment: the folder is grepped for raw hex. |
| Audit every mutation with `category="Books"` (amendment 8, supersedes F13). | F13. |

---

## 4. Data model (migration `0096_alloc_rule_versions`, see amendment 3)

Chains from `0092_scorecard_metric_src_spec`. Revision id is 24 characters. Additive only: no
column is dropped, renamed or retyped, and existing `observed` rows keep their meaning exactly.
Downgrade drops children before parents and removes added columns behind `_has_column`.

### 4.1 `allocation_rule`, two added columns

The existing table becomes the rule's **identity**: scope, funder and display name. Everything
that can change over time moves to versions.

| Column | Type | Notes |
|---|---|---|
| `mode` | String(10), NOT NULL, server_default `observed` | `observed` (existing semantics, untouched) or `applied` (this module). Longest: `observed`. |
| `scope_kind` | String(12), NOT NULL, server_default `account` | `account` (FQN prefix), `program` (FQN prefix, labelled as a program in the UI), `vendor` (normalized payee equality). Longest: `account`. |

For `applied` rules: `source_business_id` is the **pool entity** (where the cost is booked and
who funds it), `pattern` is the scope value, `pool_name` is the display name,
`target_business_id` stays NULL (targets live on the version), and `basis`, `driver_source`,
`approved_by`, `je_ref` are unused (the version carries them).

Uniqueness for applied rules is enforced in the service: one rule per
`(tenant_id, scope_kind, normalized pattern, source_business_id)`.

Also: correct the `amount` comment on `AllocationContribution` (F5) to say signed,
debit-positive, direction carried by source and target.

### 4.2 `allocation_rule_version`, new

One row per version. Append-only: a version is never updated except `is_current` and
`superseded_by` on the version it replaces, the same narrow pattern as `SalesCall.is_current`
(`models.py:1967`, `sales_desk.py:263-264`) and `vendor_bank_account.superseded_by` (`0084`).

| Column | Type | Notes |
|---|---|---|
| `id` | GUID PK | |
| `tenant_id` | GUID FK `tenant` CASCADE, indexed | |
| `rule_id` | GUID FK `allocation_rule` CASCADE, indexed | |
| `version` | Integer NOT NULL | 1-based. Unique `(rule_id, version)` as `uq_alloc_rule_version`. |
| `tier` | Integer NULL | 1 Direct, 2 Traced, 3 Driven, 4 Retained. NULL only when `method = ended`. |
| `method` | String(10) NOT NULL | `direct` (T1), `traced` (T2), `driven` (T3 with a driver), `split` (T3 reviewed split), `retained` (T4), `ended`. Longest: `retained`. |
| `targets` | JSONType NULL | `direct`: `[{"business_id": "…", "pct": "100"}]`. `split`: one entry per entity, pcts as strings, sum exactly 100. NULL otherwise. The pool entity may appear (its share is retained). |
| `driver_id` | GUID FK `allocation_driver` SET NULL, NULL | `driven` only. |
| `driver_subject` | String(120) NULL | `driven` with a split driver: which subject's values apply (a person). Longest realistic: a full name. |
| `source_spec` | JSONType NULL | `traced` only. Shape in §5.4. |
| `effective_from` | Date NOT NULL | Always the first of a month (normalized in the service). |
| `reach` | String(10) NOT NULL | `forward`, `from_date`, `retro`. What the author chose; recorded for audit and for the restatement warning. Longest: `from_date`. |
| `rationale` | Text NOT NULL | Non-empty after strip. Refused otherwise. |
| `approved_by` | GUID FK `user` SET NULL | The author. A person, never a process. |
| `is_current` | Boolean NOT NULL default true | Highest version for the rule. |
| `superseded_by` | GUID FK self SET NULL, NULL | |
| `created_at` | DateTime(tz) server_default now | Also the `as_of` key for reconstruction (§5.2). |

Index `ix_alloc_ver_rule` on `(tenant_id, rule_id, is_current)`.

### 4.3 Drivers, three new tables

`allocation_driver`: the definition.

| Column | Type | Notes |
|---|---|---|
| `id`, `tenant_id` | | |
| `key` | String(40) | Unique per tenant (`uq_alloc_driver_key`). e.g. `staff_time`. |
| `label` | String(80) | e.g. "Staff time commitment" |
| `kind` | String(8) | `split` (per subject, per entity: pct, amount or remainder) or `count` (per entity, a count; shares are count over total). |
| `unit` | String(12) NULL | `percent`, `people`, `members` |
| `is_active` | Boolean default true | |
| `created_at` | DateTime(tz) | |

`allocation_driver_commit`: one committed month. Append-only with `is_current` and
`superseded_by`, as §4.2.

| Column | Type | Notes |
|---|---|---|
| `id`, `tenant_id` | | |
| `driver_id` | GUID FK CASCADE | |
| `period_month` | Date | First of month |
| `committed_by` | GUID FK `user` SET NULL | |
| `committed_at` | DateTime(tz) server_default now | |
| `note` | Text NULL | |
| `is_current` | Boolean default true | Latest commit for `(driver_id, period_month)` |
| `superseded_by` | GUID FK self SET NULL | |

Index `ix_alloc_drv_commit` on `(tenant_id, driver_id, period_month, is_current)`.

`allocation_driver_value`: immutable, written only with its commit.

| Column | Type | Notes |
|---|---|---|
| `id`, `tenant_id` | | |
| `commit_id` | GUID FK `allocation_driver_commit` CASCADE, indexed | |
| `subject` | String(120) NULL | Split drivers: the person. NULL for count drivers. |
| `business_id` | GUID FK `business` CASCADE | |
| `value_kind` | String(10) | `pct`, `amount`, `count`, `remainder`. Longest: `remainder`. |
| `value` | Numeric(14,4) NULL | NULL only for `remainder` |

### 4.4 `allocation_contribution`, three added columns

| Column | Type | Notes |
|---|---|---|
| `grain` | String(8) NOT NULL, server_default `window` | `window` (existing observed rows) or `month` (applied rows). |
| `rule_version_id` | GUID FK `allocation_rule_version` SET NULL, NULL | Applied rows only |
| `driver_commit_id` | GUID FK `allocation_driver_commit` SET NULL, NULL | Driven rows only |

Index `ix_alloc_month` on `(tenant_id, grain, booking, period_start)`.

### 4.5 `coa_settings`, one added column

| Column | Type | Notes |
|---|---|---|
| `alloc_pool_business_ids` | JSONType NULL | List of business id strings whose unclaimed P&L expense forms the exception queue. NULL means not configured. |

---

## 5. The engine

Two new service modules. `coa_alloc.py` keeps the observed path and the checks.

- `app/services/alloc_rules.py`: tier vocabulary, validation, create, supersede, end, version
  resolution. Pure where possible.
- `app/services/alloc_engine.py`: months, pools, splits, the single `rate()`, derivation,
  exceptions, preview, reconciliation.

### 5.1 The tier vocabulary, defined once

```python
TIERS = {
    1: {"key": "direct",   "name": "Direct",   "methods": ("direct",),
        "gloss": "One entity consumes it"},
    2: {"key": "traced",   "name": "Traced",   "methods": ("traced",),
        "gloss": "A source report gives the exact split"},
    3: {"key": "driven",   "name": "Driven",   "methods": ("driven", "split"),
        "gloss": "A named driver, reviewed monthly"},
    4: {"key": "retained", "name": "Retained", "methods": ("retained",),
        "gloss": "Never reaches a partner entity"},
}
```

The frontend reads this from every payload's `meta.tiers`. It is never re-declared in JSX.

### 5.2 Months and version resolution

```python
def allocation_months(today: dt.date, fy_month: int) -> list[tuple[dt.date, dt.date]]
    # every calendar month from the fiscal year start through the month containing today

def effective_version(versions, month_start: dt.date, as_of: dt.datetime | None = None)
    # among versions with created_at <= as_of (all, when None) and effective_from <= month_start,
    # the one with the greatest `version`. None when nothing qualifies.
    # A result with method == "ended" means the rule claims nothing that month.
```

No version ever needs an end date. v1 from Jan and v2 from Apr resolve Feb to v1 and May to
v2. A retro v3 from Jan resolves every month to v3, while `as_of` before v3's `created_at`
still resolves Feb to v1. That is the March restatement, and its reconstruction.

### 5.3 Monthly balances

Add a second loop to `sync_trial_balances` that writes `AccountPeriodBalance` rows for every
key in `allocation_months()`, sharing the existing `_as_of` cache. Factor the existing loop body
into `_write_period(ps, pe, …)` so both loops use the same differencing and fiscal-year crossing
logic. **Do not change `balance_periods()`**: `routers/books.py` `_period()` defaults to
`balance_periods()[-1]`, and the worker's window pass depends on it.

Month keys overlap two existing window keys (last month and the month before). Delete-then-insert
per key is already idempotent, so the overlap is harmless.

Skip re-pulling a month whose rows exist and whose `_books_closed()` is true. Phase 0 measures
the added QuickBooks call count per realm per pass.

### 5.4 Pools and splits

For each applied rule, each month, with `v = effective_version(…)`:

**Pool amount.**

- `vendor` scope: sum of `BookTxn.amount` where `business_id == rule.source_business_id`,
  `txn_date` in the month, `normalize_name(payee) == normalize_name(pattern)`,
  `qbo_type in ("Purchase", "Bill")`, and not `flags.multi_line`. Grouped by standard account
  through `CoaMap(business_id, qbo_account_id = account_qbo_id)`.
- `account` or `program` scope: the month's `AccountPeriodBalance.amount` for every mapped
  account under the FQN prefix on the pool entity, grouped by standard account, **minus** every
  vendor-claimed amount on those same accounts that month (§9 D6). Longest prefix wins among
  account rules, as today.

**Split, by method.**

| Method | Shares |
|---|---|
| `direct` | 100 to the one target |
| `retained` | none (whole pool stays on the pool entity) |
| `split` | `targets` pcts |
| `driven`, count driver | each entity's count over the total, from the current commit for that month |
| `driven`, split driver | for `driver_subject`: fixed `amount` values first, then `pct` of the pool, then `remainder` |
| `traced`, `meta_campaigns` | each mapped entity's share of total Meta spend that month |
| `ended` | none, and the rule claims nothing |

`traced` `source_spec`:

```json
{"source": "meta_campaigns",
 "ad_account_ids": ["…"],
 "map": [{"match": "prefix", "value": "forum", "business_id": "…"},
         {"match": "contains", "value": "becollective", "business_id": "…"}]}
```

Monthly spend per campaign is `AdInsightDaily` at `level == "campaign"`, `occurred_on` in the
month (already in the ad account's timezone), joined to `AdCampaign.name`. Matching reuses
`ads_rules._norm` semantics: add a public `match_name(name, match, value)` to `ads_rules.py`
rather than copying `_norm`. Spend on unmapped campaigns counts in the total, so its share stays
on the pool entity and is reported as `source_unmapped_spend` with its amount. QuickBooks is the
amount; Meta is the ratio (§9 D13).

**The single rate.**

```python
CENT = Decimal("0.01")

def rate(amount: Decimal, pct: Decimal) -> Decimal:
    """THE one place a percentage becomes money. Nothing else multiplies by a share."""
    return (amount * pct / Decimal(100)).quantize(CENT, rounding=ROUND_HALF_EVEN)
```

Each target's amount is `rate(pool, pct)`. The pool entity's retained amount is
`pool - sum(targets)`, so rounding always lands on the pool entity and every month's split sums
to the pool to the cent (§9 D5). Count and traced shares are converted to a pct with four
decimals before `rate()`.

**Pending, never defaulted.** A rule-month that cannot resolve writes no contribution and
reports one reason, from one constant set in `alloc_engine.py`:

| Reason | When |
|---|---|
| `no_driver_values` | no current commit for the driver that month |
| `driver_subject_missing` | the commit has no values for `driver_subject` |
| `fixed_exceeds_pool` | fixed amounts sum above the subject's pool |
| `split_not_100` | pcts (plus remainder) cannot total exactly 100 |
| `source_not_connected` | no `AdInsightDaily` rows for the month |
| `source_unmapped_spend` | some spend matched no map entry (partial; the rest resolves) |
| `double_count_risk` | see F7: the target already has observed inbound from the pool entity on the same standard account that month |

### 5.5 Deriving and reading contributions

`derive_applied(s, tenant_id, today)`: for every month, delete `booking == "applied"`,
`grain == "month"` rows for that month only, then write one row per target with
`source_business_id = pool entity`, `target_business_id = target`, `standard_account_id`,
`amount`, `rule_id`, `rule_version_id`, `driver_commit_id`, `pool_name`, and `basis` set to a
readable summary of the version ("Tier 3 Driven, split 50/30/20"). Never touches observed or
window rows. Returns `{contributions, amount, pending: [...]}`. Called from the worker after
`sync_allocations` (`sync.py:1764`) and from `POST /allocations/rebuild`.

`contributions_for` changes its filter to:

```
(grain == "window" AND period_start == ps AND period_end == pe)
OR (grain == "month" AND booking == "applied" AND period_start >= ps AND period_end <= pe)
```

`_contribution` groups month rows by `(direction, counterparty, rule_version_id)` and sums them,
so a YTD Advertising line shows two inbound rows for beCollective (v1 Jan to Mar, v2 Apr on)
rather than ten. Each grouped row adds:

```json
"rule": {"id": "…", "pool_name": "…", "version": 2, "tier": 3, "method": "split",
         "effective_from": "2026-04-01", "rationale": "…",
         "months": ["2026-04-01", "…"]},
"driver": {"key": "…", "label": "…", "commit_id": "…", "values": [ … ]}
```

`driver` is present only on driven rows and only for a single-month window; a multi-month row
links to the Drivers view instead of inlining ten months of values.

### 5.6 Exceptions, computed live

Never stored, the same reason `useNextRun` gives for payment proposals: a stale queue would be
read as fact. For a month (or window), every `BookTxn` where:

- `business_id` is in `coa_settings.alloc_pool_business_ids`,
- the account maps to a P&L expense standard account,
- no effective vendor-scope version claims it, and
- no effective account-scope or program-scope version covers its account (any tier, including
  `retained`).

Multi-line transactions always appear here (D7), with a `multi_line` gap. Each row carries gap
codes derived from the row: `no_payee`, `no_memo`, `multi_line`, `recurring` (same normalized
payee three or more times in the window).

### 5.7 Preview, no write

`preview(draft, today)` runs §5.4 for a draft rule or a draft version across the months its
`reach` and `effective_from` cover, and returns per-target totals, retained, pending reasons,
the count and list of transactions it would claim (and therefore clear from exceptions), and
the months whose already-reported figures it would change. The drawer calls this on every
change, debounced.

### 5.8 Reconciliation

`reconciliation(s, tenant_id, period, today)` returns, per applied rule: `pool_amount` (what
QuickBooks holds), `resolved_amount` (contributions plus retained that resolved),
`unresolved_amount` (pending months), and the pending list. Plus `allocation_check(…)` unchanged
for the intercompany section. Variance is `resolved - pool` in cents.

---

## 6. API

All routes in `routers/books.py`, beside the existing allocation routes. Reads behind
`books_user`; writes behind `require_role("owner", "admin")`. Every write audits with
`category="Books"` (amendment 8) and a one-line `summary`.

| Method and path | Who | Does |
|---|---|---|
| GET `/books/allocations/rules?period=` | books | Rules with identity, current version, all versions, and per-rule `pool_amount`, `to_partners`, `retained`, `pending` for the window. Both modes. `meta.tiers`. |
| POST `/books/allocations/rules` | CFO | Create an applied rule and its v1 in one transaction. Body: `scope_kind`, `pattern`, `pool_name`, `pool_business_id`, plus the version fields. 400 with a plain sentence on any validation failure. |
| POST `/books/allocations/rules/{id}/versions` | CFO | Supersede: write v(n+1), flip the prior `is_current`. Same version body. |
| POST `/books/allocations/rules/{id}/end` | CFO | Write a version with `method = ended` from a month. |
| POST `/books/allocations/preview` | CFO | §5.7 for a draft. No write. |
| PATCH `/books/allocations/rules/{id}` | CFO | Unchanged for `mode == observed`. **409** for applied rules, naming the supersede route. |
| DELETE `/books/allocations/rules/{id}` | CFO | Unchanged for observed. **409** for applied rules. |
| GET `/books/allocations/exceptions?period=` | books | §5.6. 200 with `configured: false` when the pool list is NULL. |
| GET `/books/allocations/drivers` | books | Definitions, months, current commit per month, consuming rules. |
| POST `/books/allocations/drivers` | CFO | Define a driver. |
| GET `/books/allocations/drivers/{id}/months/{yyyy-mm}` | books | Current values, prior month's values as a prefill, and the effect preview of committing the prefill. |
| POST `/books/allocations/drivers/{id}/commits` | CFO | Commit a month's values. Validates per subject. Returns the before and after for every rule that reads it. |
| GET `/books/allocations/recon?period=` | books | §5.8 |
| PUT `/books/coa/settings/alloc-pools` | CFO | Set `alloc_pool_business_ids`. |
| POST `/books/allocations/rebuild` | CFO | Existing. Now also runs `derive_applied`. |

Every figure in every response is a JSON number of dollars and cents (`_money`), never a
formatted string, matching `build_mapped_statement`. Every entity reference carries
`{id, name}` from `Business`, never a key-derived label.

---

## 7. UI

### 7.1 Mount

- `Books.jsx`: add `["allocations", "Allocations"]` to `SubNav` after `"mapping"`, and
  `allocations: "Allocation rules"` to `PAGE_TITLE`. Count badge: the exception count, when the
  home payload gains it (optional, not a gate).
- New `frontend/src/books/BooksAllocations.jsx`: the shell, with a segmented sub-view (pattern:
  `BooksPayables.jsx:67` `SegNav`) for Rules, Exceptions, Drivers, Reconciliation.
- New folder `frontend/src/books/allocations/`: `RulesView.jsx`, `RuleDrawer.jsx`,
  `ExceptionsView.jsx`, `DriversView.jsx`, `ReconView.jsx`, `TierMarker.jsx`.
- Hooks in `useBooks.js` on `useEndpoint`: `useAllocRules(period)`, `useAllocExceptions(period)`,
  `useAllocDrivers()`, `useAllocDriverMonth(id, month)`, `useAllocRecon(period)`. Samples in
  `sampleBooks.js`. After a mutation, `refresh()` rather than patching local state (the hook's
  own comment explains why).

### 7.2 Visual system

- `LEDGER as L`, `LEDGER_FONT as F`, the Statement's `NUM` and `EYEBROW` shapes, `StatePanel`
  for loading, error and empty. No raw hex, no font names.
- **Tier is carried three ways, never by color alone** (the Statement's own bar):

| Tier | Marker | Code | Row |
|---|---|---|---|
| 1 Direct | filled square, `L.deep` | `T1` | paper, weight 700 |
| 2 Traced | ringed circle, `L.body` | `T2` | paper, weight 600 |
| 3 Driven | ringed circle, `L.flagRing` | `T3` | `L.flagBg`, 3px inset `L.flagEdge`, weight 500 |
| 4 Retained | none | `T4` | paper, `L.muted`, weight 400 |

- Entity names and dots: name from the payload's `Business.name`. Dot color from
  `Business.accent` (already a per-business column) rather than `ENTITY`, which avoids the
  shared-daffodil collision (F11) without touching `ui.jsx`.
- Gold means "a judgment or an unresolved item". Never used for an error. Poppy is not used in
  this module.

### 7.3 Screens

Layout, copy and states follow the mockup screen of the same name.

**Rules.** Tier summary cards (count, pool, gloss), tier filter, entity filter, ledger rows
(marker, code, scope, kind, method line, target, pool, to partners). `pending` in `L.flagInk`
where any month is pending. Row expands to: Why This Rule Exists, How It Resolves (targets, bar,
dollars, or the pending panel naming the reason), Version History (newest first, window, split,
approver, stamp, Current or Superseded), then Supersede With New Version and View Affected
Lines. No Edit and no Delete anywhere.

**Exceptions.** Total and count; one card per transaction with payee, account, amount, date,
memo, and gap chips; one action, Create Rule From This, which opens the drawer prefilled. Empty
state when the queue is empty says that is the goal. Setup state when pools are not configured,
with the entity picker (`PUT /coa/settings/alloc-pools`).

**Drivers.** Per driver: months, a per-subject table (staff) or per-entity table (counts) for
the selected month, status per row (Set, Effective dated, Not set), the "What Commits Change"
before and after from the server, and Commit. Not set uses the gold wash and is not an error.

**Reconciliation.** Per pool: pool, resolved, unresolved, variance in cents with ledger parens,
pending reasons inline. Below: the intercompany checks from `allocation_check`. No reading
chips: both columns always show (D10).

### 7.4 The rule drawer

One drawer, three entry points (New Rule, Supersede, Create From Exception). Five steps, step 3
changes with the tier:

1. What It Matches: `scope_kind` and value, entity it is booked on.
2. Is It A Fact Or A Judgment: four tier cards from `meta.tiers`.
3. Tier 1: one entity. Tier 2: source and campaign map. Tier 3: driver (or Reviewed split with
   steppers and a running total that must reach 100). Tier 4: nothing to enter, and says so.
4. Why: required.
5. From When: month picker plus `forward`, `from_date`, `retro`. Retro on a supersede or a
   Tier 3 raises the restatement warning listing the months that change (from preview).

The preview is pinned above the actions and comes from `POST /allocations/preview`. Save names
its blocker one at a time instead of only disabling. Save labels: Save Rule, Save As v{n},
Save And Clear Matches.

Desktop: 460px side panel. Below 560px: full-width sheet, opaque scrim.

### 7.5 Statement extension

In `BooksStatement.jsx` `Composition`, each contribution row with a `rule` gains a second line:
`{pool_name} v{version} · Tier {tier} {name} · from {Mon YYYY}`, and the months span when
grouped. A driven single-month row shows its driver values in a collapsed list. Rationale
renders below, as written. Nothing else in the Statement changes, and both 409 panels keep
working.

### 7.6 Responsive

One `<style>` block in `BooksAllocations.jsx`, classes plus media queries, as
`BooksStatement.jsx:719`. Two behaviors, chosen per column:

- Supplementary columns drop: on Rules, Target at 940px, To Partners at 660px.
- Comparison columns stack and never drop: on Reconciliation (pool, resolved, variance) and
  Drivers (per-entity values). Group them in one cell; below 660px the group becomes one
  right-aligned column, the header hides, each figure carries an inline label.

Row expansion is the only motion (130ms); `prefers-reduced-motion` removes it; the gold wash
never transitions. No horizontal scroll at 375px.

### 7.7 Copy

No em or en dashes in any string. Sentence case for body, Title Case for eyebrows and buttons as
in the mockup. Direction in words ("Allocated in", "Allocated out"), never by sign alone.

### 7.8 What must not ship from the mockup

| From the mockup | Why |
|---|---|
| Every rule rationale | Plausible reconstructions, not Spring's words. Rationales are entered by a person in the drawer. |
| Every figure | As-booked, untied brief figures. Fixture data only (Appendix B). |
| "These are Spring's judgments…", "This is what Spring sends Julie…" | Name specific people. |
| Footer disclaimer and "Mockup v1" | Scene setting. |
| "Open Decision, Shown Both Ways" panel and reading chips | Documented a spec question that §9 D10 answers. |
| Exception descriptions ("the amount is round, which usually means…") | Designer inference. Production shows memo and payee. |
| "Own revenue and native costs: not in mockup" | Placeholder for missing brief data. |

---

## 8. Phases

### Phase 0: Inventory and fixture hygiene (no product code)

- Write `scripts/alloc_inventory.py`, read-only, run against a restored production copy or a
  read-only replica. Print: every `Business` (`id`, `key`, `name`, `kind`,
  `include_in_portfolio`, `sort_order`, `accent`); every QBO `Integration` with its business and
  realm; `AllocationRule` rows; `BookTxn` min and max `txn_date` per realm and the share with
  `multi_line`; `AdAccount` rows with business and distinct campaign name count; current
  `CoaSettings`.
- Measure: as-of trial balance pulls per realm per pass today, and with `allocation_months()`
  added (count only; do not ship the loop).
- Verify the BookTxn sign against APB: for one closed month on one realm, sum `BookTxn.amount`
  on one expense account and compare with that month's `AccountPeriodBalance.amount`. Record the
  result and the sign rule.
- Scope `_clear` in `tests/test_coa_alloc.py` by `tenant_id` (F8).

**Done when** the inventory output, the call counts and the sign check are recorded in §12, and
`tests/test_coa_alloc.py` passes run alone, run in reverse, and run after the full suite.

### Phase 1: Data foundation (backend, no behavior change)

- Migration `0096_alloc_rule_versions` (§4, amendment 3), revising `0095_agent_roster_dates`.
  Models for every new table and column. Fix the F5 comment.
- **`book_txn_line`** (amendment 13): one row per QuickBooks line, carrying its own account and
  its own amount. `books_sync` already receives `obj["Line"]`; this stores it instead of keeping
  the first category and the header amount. No extra QuickBooks calls.
- No service reads the new columns yet.

**Done when** `alembic heads` prints one line; `tests/test_migration_0096_alloc.py`, modelled on
`tests/test_migration_0093_cleanup.py` (amendment 6), asserts the migration's columns equal the
models' columns, pins `down_revision` to `0095_agent_roster_dates`, and passes an upgrade-twice
and downgrade-twice round trip in an isolated database; on SQLite `seed()`, `alembic stamp
0095_agent_roster_dates`, `upgrade head`, `downgrade -1`, `upgrade head` all succeed (amendment
5); the same round trip succeeds on a Postgres copy; and `tests/test_coa_alloc.py` and
`tests/test_coa_balances.py` pass unmodified.

### Phase 2: Monthly grain

- `allocation_months()`, `_write_period()` refactor, the second loop in `sync_trial_balances`,
  closed-month skip.
- `contributions_for` containment filter (§5.5). No applied rows exist yet, so this is inert.

**Done when** for a fixture realm the month rows Jan through Sep sum, per account, to the YTD
window's `AccountPeriodBalance.amount` to the cent; and `build_mapped_statement` returns an
identical payload for every existing window before and after (assert equality in a test).

### Phase 3: Rule versions (append-only)

- `alloc_rules.py`: `TIERS`, `validate_version`, `create_applied_rule`, `supersede`, `end_rule`,
  `effective_version`.
- Routes: POST rules (applied), POST versions, POST end. PATCH and DELETE return 409 for
  applied rules.

**Done when** the Advertising scenario (Appendix B1) resolves Feb to v1 and May to v2; a retro
v3 from Jan resolves Feb to v3 while `as_of` before v3 still resolves Feb to v1; a version with
an empty rationale is refused with a sentence; and a test asserts that no SQL `UPDATE` reaches
`allocation_rule_version` except on `is_current` and `superseded_by`.

### Phase 4: The engine (direct, split, retained, vendor scope)

- `alloc_engine.py`: pools (§5.4), `rate()`, derivation (§5.5), the `double_count_risk` check,
  the worker call after `sync_allocations`, `/allocations/rebuild` extension.
- `_contribution` grouping and the `rule` block.

**Done when** for every month in Appendix B1 the targets plus retained equal the pool to the
cent (the YTD figure does not divide evenly by nine, which is the point); every fixture entity's
Statement still returns `tie_out.status == "tied"`; `allocation_check` still reports
`zero_sum.balanced`; a vendor rule inside an account rule's subtree is counted once (B4); a
multi-line transaction is never claimed by a vendor rule; and the existing observed tests pass
unmodified.

### Phase 5: Drivers

- Driver services, commits, values, validation, prefill from the prior month, commit effect
  preview. Driven method resolution.

**Done when** Appendix B2 resolves: flat amount to beCollective through August, zero from
September, remainder to the pool entity; Appendix B3's unset subject reports
`driver_subject_missing` for its rule only while every other rule resolves; and a month with no
commit reports `no_driver_values` rather than reusing the prior month.

### Phase 6: Traced (Meta)

- `ads_rules.match_name`. Traced resolution from `AdInsightDaily`. `source_unmapped_spend`.

**Done when** with three fixture campaigns (two mapped, one not) the mapped shares and the
unmapped share sum to 100 percent of the month's Meta spend; the unmapped amount appears in the
rule's pending list with its dollars; and a month with no insight rows reports
`source_not_connected`.

### Phase 7: Exceptions, preview, reconciliation (API)

- §5.6, §5.7, §5.8 and their routes. `PUT /coa/settings/alloc-pools`.

**Done when** creating a rule from an exception with `reach = retro` removes every matching
transaction from `GET /exceptions` in one call, and the number removed equals the count the
preview returned before the save; and `GET /recon` for Appendix B shows a pending Tier 3 rule
with `resolved_amount` 0 and its full pool as `unresolved_amount`.

### Phase 8: The Allocations page (frontend)

- §7.1 to §7.4, §7.6, §7.7. Samples in `sampleBooks.js`.

**Done when** all four views render against the API and offline; at 375px there is no
horizontal scroll on any view; a greyscale screenshot of the Rules view still distinguishes all
four tiers; and every check in Appendix A passes.

### Phase 9: Statement composition

- §7.5.

**Done when** an allocated YTD line shows one grouped row per rule version with version, tier,
effective month and rationale; a single-month driven row shows its driver values; and the
existing Statement tests, including both 409 panels, pass unmodified.

### Phase 10: Partner view

**Blocked** on §11 items 1 and 7. Build only when both are answered. The design is the mockup's
Partner View: one entity, its allocated lines with rule and reason, and the draw against earned
share on the chosen basis.

### Phase 11: Live, with Connor

Done in Connor's signed-in session, not a deploy: set the pool entities; rename the pool entity
in Settings if "SB Coaching" is the chosen display name; map Julie's draw account to `3100
Member Draws and Distributions` in Mapping; declare the first real rules with Spring's own
rationales; commit the first driver month. **Done when** the Rules view shows those rules with no
illustrative copy anywhere on the page.

---

## 9. Decisions made for Connor (change any of them)

**D1. Applied rules are new rows with `mode = applied`.** Observed rules and contributions are
untouched, so nothing already on the Statement can move.

**D2. Versions are rows; superseding is the only change.** The one permitted update is
`is_current` and `superseded_by` on the replaced version, the house precedent twice
(`SalesCall`, `vendor_bank_account`).

**D3. Ending a rule is a version** (`method = ended`), not a date on an old row. Fully
append-only, and the end has an author and a reason like every other change.

**D4. The month is the unit of derivation.** Windows are sums of months. This is the only way an
April change and a monthly driver can be right in a YTD figure.

**D5. Rounding lands on the largest line** (amendment 11 supersedes this; it originally read "on the pool entity"). Every split still sums to its pool to the cent.

**D6. Vendor claims are carved out before account rules.** A specific claim beats a general one,
the same logic as longest prefix.

**D7. Multi-line transactions are never auto-claimed by payee.** They go to exceptions with a
`multi_line` gap.

**D8. A driver month is never carried forward.** The UI prefills from last month; a person
commits. An uncommitted month is pending.

**D9. Exceptions are computed on read, never stored.**

**D10. No change to the tie-out invariant, and no reading chips.** The invariant protects the
mapping rollup and runs before composition; applied rows move `as_allocated` only; both readings
already ship side by side. What remains open is narrower: which reading the Partner View leads
with (§11 item 7).

**D11. RETIRED by amendment 8.** It read: audit category "Config", no new category, so no Postgres-only CHECK rebuild. That rebuild has since happened in `0093_books_cleanup` and a `Books` category exists.

**D12. Allocations is its own Books sub-nav page,** not a section of Mapping. Mapping answers
"where does this account go"; Allocations answers "whose cost is it".

**D13. Traced Meta splits use Meta's spend as the ratio and QuickBooks' amount as the pool.**
QuickBooks is the books. Their difference is reported, never reconciled away.

**D14. Vendor scope matches normalized payee equality,** using `binder_extract.normalize_name`,
the same normalization as `Vendor.name_norm`. Prefix matching on free-text payees would claim
the wrong vendor.

**D15. Entity color comes from `Business.accent`,** not the hardcoded `ENTITY` map. Note amendment 23: three of five businesses currently share one accent, so colour is never the only signal.

---

## 10. Not built, designed for

- **Per-person W-2 wages.** `subject` on driver values is ready; a wage source (Gusto or a
  payroll register import) would let staff-time drivers split payroll, not only contractors.
- **Google Workspace seats and Amex cardholder detail** as Tier 2 sources. `source_spec.source`
  is a string so a new source is a new branch, not a migration.
- **Rule suggestions from `Vendor.default_business_id`.** A Payables vendor with a default
  entity is a likely Tier 1 rule. Suggest; never create.
- **A partner access role** narrower than the `books` tab.
- **Bulk driver edits** across rules.
- **Balance sheet allocations.** Out of scope, as in the COA phasing.
- **Writing applied allocations back to QuickBooks.** Never. Read-only is the module invariant.

---

## 11. Only Connor can do

1. **beCollective profit basis**, accrual or cash. Blocks Phase 10.
2. **Pam Bakalian's split.** Renders as Not set until committed.
3. **Vizual Lab, Forward Event, Tina Andrea Media.** Exceptions until a rule claims them.
4. **The Meta figure.** The brief states 78 percent unattributed and also $30,280 of
   $260,267.22 traced (88.4 percent unattributed). Needs a source read before either is shown.
5. **Julie's draw granularity.** 3100 directly, or a sub-account beneath it.
6. **Rationales in Spring's words** for every rule declared in Phase 11.
7. **Which reading the Partner View leads with**, booked or allocated.
8. **The BookTxn backfill start** (F10, `books_sync._backfill_start`). It bounds how far a retro
   rule can reach.
9. **Which entities are pools** (`alloc_pool_business_ids`), and whether the pool entity's
   display name changes to "SB Coaching".

---

## 12. Build record

Fill per phase: commit, what changed, what the gate measured, anything corrected while
building (with the date), and the full suite count.

### Phase 0

Done 2026-10-27. Read-only throughout: nothing was written to QuickBooks, Meta or production.

**Inventory** (`backend/scripts/alloc_inventory.py`, run against production).

| What | Found |
|---|---|
| Businesses | 5: `ulrg` ULRG + Team (real_estate), `springb` Spring B (membership), `sympli` Sympli Mortgage (commission_jv), `the_forum` The Forum (membership), `becollective` beCollective (membership). All in portfolio. |
| QBO integrations | 5, one realm each, all connected. `springb` is realm 9130358004793436, so F14's open question (which row carries SB Coaching's realm) is answered. |
| Existing allocation rows | 1 rule ("Shared Services", pattern `Shared Service Expenses:`, source `springb`, target NULL meaning every entity) and 36 `observed` contributions totalling $1,219,094.37. Phase 1 must leave all of it untouched; B5 asserts it. |
| BookTxn reach | `ulrg` 2026-01-01, `springb` 2026-01-01, `sympli` 2026-01-20, `becollective` 2026-01-30, `the_forum` 2025-12-31. F10's bound is confirmed: a retroactive rule cannot reach before January 2026. |
| Ad accounts | 1, Meta, `act_587749862890426`, on `springb`, 18 distinct campaign names. Tier 2 Meta rules are viable and scoped to that one account. |
| CoaSettings | ic_flag_threshold_pct 5.00, tie_out_tolerance 0.01, block_render_on_unmapped true. No `alloc_pool_business_ids` yet, as expected. |
| Mapping coverage | 1,545 `coa_map` rows against 816 standard accounts. |

**Call count.** Arithmetic, not calls: period activity is the difference of two as-of trial
balances and the cache is keyed by date, so a pass costs the number of DISTINCT dates it needs.
Today 5 window periods need **6** distinct as-of dates. Adding the 10 month periods
(2026-01-01 to 2026-10-31) takes it to **12**: **+6 per realm per pass, +30 across five realms**,
a doubling. The closed-month skip in §5.3 is what keeps it from compounding. `sync_trial_balances`
already logs its real count as `as_of_pulls=`; compare the projection against that line after
Phase 2.

**BookTxn sign.** `ulrg`, 2026-10-01 to 2026-10-31, account 629:
`AccountPeriodBalance.amount` **1,474.89** and `sum(BookTxn.amount)` **1,474.89**, difference
**0.00**. BookTxn is debit-positive, the same orientation as the balance rows, so Phase 4 can mix
vendor pools (from BookTxn) with account pools (from AccountPeriodBalance) without negating
either side.

**Multi-line share, and what it means for D7.** Measured because D7 routes every multi-line
transaction to the exception queue, and nobody had counted them.

| Type | Multi-line | Of | Share |
|---|---|---|---|
| JournalEntry | 379 | 380 | 99.7% |
| Deposit | 1,214 | 1,505 | 80.7% |
| Purchase | 26 | 3,256 | 0.8% |
| Transfer | 0 | 3 | 0.0% |

Per entity the multi-line counts are: `ulrg` 685 Deposit / 115 JournalEntry; `springb` 162 / 98
plus 25 Purchase; `becollective` 99 / 67; `the_forum` 64 JournalEntry / 40 Deposit / 1 Purchase.

**Allocations pool expenses, and expenses are 99.2% single-line.** The alarming headline share
(62.9% of The Forum's transactions) is every type together; the multi-line mass is income
(deposits batching several payments, one real event with many lines) and journal entries (the
existing shared-service allocation mechanism, which this module replaces). So D7's queue is not
flooded by account-scope expense rules. It fills only where a pool deliberately includes
JE-booked shared-service charges, which is The Forum's case and the case being taken over.
Amendment 13 stores the lines regardless, because that is what makes the Statement's evidence
list tie to the trial balance.

**Fixture hygiene (F8).** `_clear` in `tests/test_coa_alloc.py` was scoped by `tenant_id` -- it
already took the argument and never used it. The stated reason was corrected: see amendment 20.

**Gates.** `tests/test_coa_alloc.py` 11 passed alone, 11 passed in explicit reverse node order,
and 11 passed again immediately after the full suite. Full suite **2,477 passed**, 27m47s.
`pytest-randomly` is not installed, so `-p no:randomly` is a no-op in this repo and reverse order
was produced by passing collected node ids in reverse.

### Phase 1
### Phase 2
### Phase 3
### Phase 4
### Phase 5
### Phase 6
### Phase 7
### Phase 8
### Phase 9
### Phase 10
### Phase 11

---

## Appendix A. Acceptance checks, grep-able

`$F` is `frontend/src/books/BooksAllocations.jsx frontend/src/books/allocations/`.

| # | Check | Expect |
|---|---|---|
| A1 | `alembic heads` | one line |
| A2 | `grep -nE "sa\.Enum\|postgresql\.ENUM\|JSONB\|PG_UUID" backend/alembic/versions/0096*` | 0, and `ls backend/alembic/versions/0096* \| wc -l` is 1 (amendment 4) |
| A3 | `grep -rnE "#[0-9A-Fa-f]{3,8}\b" $F` | 0 |
| A4 | `grep -rnE "Poppins\|Outfit\|Archivo\|Inter\b" $F` | 0 |
| A5 | `grep -rnP "[\x{2013}\x{2014}]" $F` | 0 |
| A6 | `grep -rni "poppy" $F` | 0 |
| A7 | `grep -rnE "\* *0?\.[0-9]\|/ *100\b" $F` | 0. The browser computes no shares. |
| A8 | `grep -nE "/ *Decimal\(100\)\|/ *100\b" backend/app/services/alloc_*.py` | matches only inside `rate()` and the count-to-pct conversion |
| A9 | `grep -nE "setattr\|\.delete\(" backend/app/services/alloc_rules.py` | 0 |
| A10 | `grep -c "TIERS" $F` | 0. Tiers come from `meta.tiers`. |
| A11 | `grep -rn "ENTITY\[" $F` | 0. Names from the payload. |
| A12 | `grep -rnE "@media\|@container" frontend/src/books/BooksAllocations.jsx frontend/src/books/allocations/` | non-zero (amendment 18) |
| A13 | `grep -rnE "springb\|the_forum\|becollective\|ulrg\|sympli" backend/app/services/alloc_*.py $F` | 0. No business keys. |
| A14 | `grep -n "category=\"Books\"" backend/app/services/alloc_*.py` | one per mutation (amendment 8) |

## Appendix B. Fixture scenarios

From the brief, as-booked and untied. **Tests and samples only.** Monthly amounts are invented
for fixtures and must sum to the brief's figures.

**B1. Advertising, general.** Pool $52,049.87 on the pool entity, Jan to Sep, nine monthly
amounts that sum to it (not equal: the figure does not divide by nine, which exercises D5).
v1 from Jan: Forum 70, beCollective 30, pool entity 0. v2 from Apr: 50, 30, 20.

**B2. Staff time, contractor paid by name.** Subject "S. Ostermiller" (vendor-scope rule on the
payee, driven by `staff_time`). Commits Jan to Aug: Forum pct 70, beCollective amount 2,500,
pool entity remainder. Sep: Forum pct 70, beCollective amount 0, pool entity remainder.

**B3. Unset subject.** Subject "Pamela Bakalian", $66,000 across the year, no values in any
commit. Pending `driver_subject_missing` every month; B2 still resolves.

**B4. Vendor inside an account subtree.** A vendor rule on "Qualifi" (Tier 1, Forum) and an
account rule on the subtree its transactions post to (Tier 3 split). Qualifi's $3,694 is
claimed once by the vendor rule and excluded from the account rule's pool.

**B5. Observed untouched.** The existing `FORUM` fixture in `test_coa_alloc.py`
(`Shared Service Expenses:`, $32,450.07) produces the same three observed rows and the same
`allocation_check` before and after every phase.

**B6. Double-count risk.** An applied split from the pool entity to The Forum on the payroll
standard account in a month where B5's observed payroll row exists for the same pair. Reports
`double_count_risk`.

**B7. Meta.** Three campaigns in one month: one matching a Forum map entry, one matching
beCollective, one matching nothing. Spend 6,000 / 3,000 / 1,000. Shares 60 / 30 / 10, the 10
retained and reported as `source_unmapped_spend` with its dollars.

## Appendix C. Mockup to code

| Mockup | Built in | Reads |
|---|---|---|
| Rules Table | `allocations/RulesView.jsx` | GET `/allocations/rules` |
| Create and edit drawer | `allocations/RuleDrawer.jsx` | POST `/allocations/preview`, POST rules, POST versions |
| Exception Queue | `allocations/ExceptionsView.jsx` | GET `/allocations/exceptions` |
| Monthly Drivers | `allocations/DriversView.jsx` | GET `/allocations/drivers`, GET month, POST commits |
| Reconciliation | `allocations/ReconView.jsx` | GET `/allocations/recon` |
| Allocation Detail | `BooksStatement.jsx` `Composition` | GET `/books/statement` (`contributions[].rule`, `.driver`) |
| Partner View | Phase 10 | blocked |
