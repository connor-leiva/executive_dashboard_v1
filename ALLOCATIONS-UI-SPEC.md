# Allocations UI: production audit and rebuild guide

Written 2026-11-04 from four production screenshots (Rules, Exceptions, Drivers, Reconciliation)
and a read of `main` at `74e7653`: `BooksAllocations.jsx`, every file in
`frontend/src/books/allocations/`, `useBooks.js`, and the payload builders in
`services/alloc_engine.py`, `services/alloc_rules.py` and `routers/books.py`. The Partner tab
was not in the screenshots and is not covered.

> **The ask:** the Allocations page is live and hard to use. Audit what production shows, then
> say exactly what to change so the page answers its questions at a glance: what needs me this
> month, where does each shared cost go, and what has not resolved and why.

House format for this document and for every UI string it specifies: no em dashes and no en
dashes. Hyphens, commas and parentheses only.

**The headline.** Four of the problems are wrong numbers or wrong words on screen, not taste,
and they come first (Phase 1). The Rules ledger shows "Retained" figures under the "To partners"
header. A Direct rule that keeps its cost on its own entity is reported unresolved forever.
Reconciliation prints the method name where the reason should be. Exceptions counts $8,031.59
of already-allocated payroll as unclaimed. The rest is structure: the page has no visible month,
the create button sits under a 46-row table, the driver form is 60 controls long, and most rows
on every screen are zeros. And it does not look finished: boxes inside boxes, five control
heights, no type hierarchy, and gold on so many rows that the ones needing a person do not stand
out (U19 to U26). §9 makes `Allocation Rules v2.dc.html` the visual target: production should
look nearly identical to it, and every colour it uses is already a `LEDGER` token.

This supersedes `ALLOCATION-RULES-SPEC.md` §7.3, §7.4 layout and §7.6 where they conflict.
Everything in that spec's §3 standing rules still holds.

---

## 0. How to use this document

For Claude Code, in order:

1. Read §2 (findings). Each cites a screenshot and a file. Every finding is resolved by a named
   phase.
2. Read §3 (principles). They decide every case this document does not spell out.
3. Build phases in order (§10). Each ends in a **Done when** checkable from outside the code. Do
   not start a phase until the previous gate is met and recorded in §13.
4. §11 lists decisions made on Connor's behalf. Build to them. §12 lists what only Connor can
   answer.
5. Open `Allocation Rules v2.dc.html` beside the running app. It is the visual target (§9). The
   original `Allocation Rules v1.dc.html` is the same look with the earlier structure.
6. After each frontend phase, screenshot the changed views at 1280px, 820px and 375px against a
   tenant with real November data and attach them to §13. The repo's own comments record three
   layout bugs that passed the build and were found only by looking (`BooksAllocations.jsx`
   style block). This document's U1 is a fourth.
7. Read §0.1 before anything else in this list. Where an amendment conflicts with the body, the
   amendment wins, as in the parent spec.

---

## 0.1 Amendments, 2026-11-04

Every finding U1 to U26 was checked against `main` before building. **All 26 are real**, and the
four visible in the screenshots were confirmed by eye as well. Nothing below retracts a finding.
What follows is what verification changed, and it is binding.

**1. The default month is the last complete month, but never earlier than this module's first
authoritative month.** §5.1 and D2 say "the last complete month. On November 4 that is October".
That breaks the cutover: `ALLOCATION-RULES-SPEC.md` makes Books Cleanup authoritative through
31 October and this module authoritative from 1 November, and all 46 live rules are effective
from November with `reach` forward, so October renders as zeros, which is worse than the `mtd`
it replaces. It also reopens amendment 31, the picker that opened on a month nobody chose, now
that Drivers takes the page month (§5.1). The rule above satisfies both intents: November today,
November in December, October only once October is a month this module owns. **Every "Done when"
in §10 that names October (§10 Phases 3, 4, 5, 6) is restated against the month the rule yields.**

**2. §7.8 of the parent spec binds this document.** Five of its seven rows are still live in the
file §9 makes the visual target: `v2.dc.html` names Spring (`driverIntent`) and Julie
(`partnerIntent`, also rendered in the Partner view), carries six invented rule `rationale`
strings, the exception inference "the amount is round, which usually means it was a manually
keyed transfer", and a `"not in mockup"` placeholder. §9 says "nearly identical" and never cites
§7.8; §9.6 counts only colour, type, weight, spacing, radius and border as defects. **§9.6 gains
a seventh check: no string from §7.8's table ships.** This is the same class of defect as the
marketing patch that deleted the dashboard's hero plates: a handover described as additive that
carries deletions nobody read.

**3. §7.3 is an amendment to the parent §5.6, not a bug fix.** D7's rule sentence is payee-only,
as the audit says, but §5.6 states "Multi-line transactions always appear here (D7)" without
qualification, and the build record repeats it. The current code implements the spec as written.
The double count is still real and still wrong (D6), so §7.3 is built - but **§5.6 and the Phase 7
build-record paragraph in `ALLOCATION-RULES-SPEC.md` are amended in the same commit**, or the next
reader restores today's behaviour as a fix.

**4. §7.1 is one line, not a two-caller refactor.** `rules_payload` has no pending logic to
update; it reads `reconciliation`'s output (`"pending": money.get("pending", [])`), by its own
docstring: "THE POOL FIGURES COME FROM reconciliation, not from a second computation." So the
helper has one call site. This is also why fixing `reconciliation` fixes the Rules view: for a
pending rule `went` is 0, so `retained` is the whole pool, which is the figure U1 shows under the
wrong header. **Trap:** `_keeps_all` written as `all(t["business_id"] == pool for t in
(v.targets or []))` returns True for an empty list, and `targets` is NULL on every `driven`,
`traced`, `retained` and `ended` version. It must require a non-empty `targets`.

**5. `derive_month` does not exist.** The writer is `derive_applied` (`alloc_engine.py:308`), so
§7.2's instruction and the U2/U3 evidence lines name a function a builder cannot open. The
extraction is otherwise clean: the per-rule body (`:335` to `:407`) contains no `await`, every
helper it calls is sync, and the one async read it needs (`_observed_inbound`) is already hoisted
above the loop and moves into `ctx`. **But it cuts up the only function that writes
contributions,** which is what the module's to-the-cent invariants defend. **Phase 1 gains a gate:
`derive_applied` writes byte-identical rows before and after the extraction, proved on Appendix B's
fixtures, plus `test_alloc_engine.py`'s three invariant tests green.**

**6. §5.1's fiscal-start premise is false.** `CoaSettings` has no fiscal field; the engine reads
`fy_month` from QuickBooks during sync (`coa_balances.py:325`). The browser cannot compute the
month list. **The month options come from a payload** - return `allocation_months(today, fy_month)`
or `fy_month` itself. This also fixes amendment 3 of this list, since the server then owns the
default and a test can time-travel it, which a browser-side "last complete month" cannot.

**7. §7.5 is gated on §7.3 and is not free.** `partly_covered_count` cannot exist before §7.3
creates the concept; `unclaimed_total` reusing today's `total` would report the very 8,031.59 D6
excludes; `payee_count` needs the grouping §7.3 adds. And "no new computation" is true of the kind
and false of the cost: the summary is a second full run of `exceptions` and `reconciliation`, the
two most expensive functions here, on every page load - roughly 500 extra round trips at 46 rules
across the fiscal series. **Build it after §7.3, and serve the view and the summary from one
computation per request.** The 2026-10-10 sync incident is why this is written down.

**8. §7.6 needs more than it says.** `subjects_by_driver` exists but returns bare names, filters
on `is_current`, and **takes no month** - so on a historical month it returns today's subject
roster against that month's commit, which the new month control makes reachable. It must resolve
through `effective_version(history, month)` the way `reconciliation` does. `mode` exists nowhere
and is derived from the last commit's `value_kind` set. `rules[].pool_amount` comes from
`pools_for_month`, not from the driver tables.

**9. §9.1 needs one new token.** 33 of the mockup's 34 hexes map to `LEDGER` exactly, verified
programmatically: zero mismatches, zero missing tokens, and no mockup hex the table omits. The
exception is the primary-button hover `#17402F`, used three times. B1 forbids the inline hex and
the suggested `alpha(L.deep, .92)` is off-hue and translucent where the mockup is opaque. **Add
`deepHover: "#17402F"` to `LEDGER`.** The three `rgba(14,43,34,…)` values are `L.deep` and
`alpha()` is correct for them.

**10. Appendix A is rewritten before it is used as a gate.** B11 and B12 do not exist (the table
is B1 to B10 plus B13). Several expectations are qualified in prose rather than being a literal
zero, so a builder reading "Expect 0" fails a correct build: B3, B4, B5, B6 and B9 all have
legitimate non-zero hits. And `$F` covers `RuleDrawer.jsx` and `PartnerView.jsx`, which **no phase
in §10 touches** - against that scope there are three `REASON` maps not two and four `Money`
functions not three, so Phase 1's gate fails on files nobody is editing. **Either the phases widen
or `$F` narrows; this build narrows `$F` and leaves Partner alone,** since Partner is not in the
screenshots, is excluded by this document's own first page, and shipped only in the parent's
Phase 10. Each check gets a pattern that is verified to fail when the defect is present, not only
to pass when it is absent.

**11. The "Done when" gates must not be written on customer data.** "searching jacob leaves the
two Blake Jacobsen rules", "Payroll Pay 8,031.59", "Ostermiller's 70%, $2,500, rest" are live
payee names and figures, and parent §3 forbids exactly these as literals. They are also only
runnable against one tenant's production. **Restated against Appendix B's fixtures,** which
already carry a suitable subject.

**12. Three fixes the rebuild would otherwise lose.** (a) Principle 4 says the frontend holds no
vocabulary, but §8.2 deletes only `REASON`, `METHOD_LINE` and `KIND_WORD`, leaving
`ExceptionsView`'s `GAP` map, and §6.3 introduces a new three-way unit control whose words come
from nowhere: **add `meta.gaps` and `meta.value_kinds`.** (b) Parent amendment 24's `no_entity`
gap is still unbuilt, and §7.3's group key `(business_id, payee)` is keyed on the field that is
NULL for the commission bills amendment 24 exists for: **land it with §7.3 or record that it is
still open.** (c) Parent amendment 29's three-state exceptions queue, found live, is not mentioned
in §6.2's rebuild: **the configured-with-no-pool state and the unconfigured setup panel both
survive.**

**13. Reuse before rebuild.** Parent standing rule 17 names `frontend/src/cleanup/kit.jsx`, which
this document never mentions and which already exports `Pill`, `Note`, `NUM` and the two
formatters §8.3 tells the builder to grep for (`monthName`, `dayLabel`). **`controls.jsx` wraps
`kit.jsx` rather than re-deriving it;** B10 still passes, as `kit.jsx` is outside `$F`. Separately,
U13 and §6.1 move the tier weight off the row, which contradicts the parent §7.2 tier table; the
audit also mis-cites that table as parent §2. **The weight moves to the name span as U13 says, and
the parent §7.2 table is amended to match** rather than left to contradict it.

**14. Two things the mockup does not answer, found by opening it.** (a) §6.3 says each subject's
second line names what it feeds and the month's dollars, "the reason to get it right"; the mockup
puts the mode description there and shows no dollars. Principle 8 gives appearance to the mockup
and data to this document, so **the row carries both**: mode line and a dollars-at-stake line.
(b) §9.2's exception card recipe has a "what is known" slot filled in the mockup by designer
inference, which §7.8 forbids. **That slot renders the real payee, account and memo**, as §6.2
already specifies for the expanded charge row.

**15. §12 item 2 is struck.** It asks whether payroll taxes should stay a driver subject or be
derived from the people rows. Connor answered it on this date and the answer is specified in
`ALLOCATION-RULES-SPEC.md` §10.1, which goes further than the question: the derivation is
dollar-weighted, not an average of the percentages. §6.3's matrix is compatible with §10.1 landing
later and nothing needs undoing, **on one condition: the matrix rows and the commit bar's count
come from the payload's `subjects`, never from a list held in the frontend.** Then the taxes row
simply disappears when §10.1 ships, which is §10.1's stated outcome.

**One concern raised in review and withdrawn.** That §9.2's gold "Total" pill uses the flag palette
for a row that does not add to 100, where parent §7.2 reserves gold for a judgment and bans poppy,
leaving no colour for a refusal. Checked against the running code: production already renders this
state in gold and carries the rule as a comment beside it. No change.

**Testing reality.** `frontend/package.json` has no test script and no runner; there are no
frontend test files. All eight Phase 1 correctness fixes are gated by greps and screenshots, which
makes Phase 7 mandatory rather than a polish pass. Screenshots are taken against `npm run build`
plus `preview`, never the dev server.

---

## 1. What production shows

| Screen | What is on it |
|---|---|
| Rules | Four tier cards (T1 Direct 1,666.67 / 7 rules, T2 Traced 0.00 / 0, T3 Driven 6,334.92 / 16, T4 Retained 0.00 / 23). An entity select. "46 of 46 shown". A ledger sorted by name: Active Campaign, Addevent, Asana, B. Bengtzen, Blake Jacobsen (commission), Blake Jacobsen (salary), Bocah Productions, Breakthroughs LLC, Brianna Wood, Calendly. Almost every figure is 0.00. No create button in view. |
| Exceptions | "Carrying shared cost: ULRG + Team, Spring B, The Forum, beCollective", Change. "Unclaimed 32,126.60 across 21 transactions in mtd". One card per transaction: Payroll Pay 8,031.59 (No memo, More than one account), Breakthroughs LLC 5,500.00, Profytz LLC 5,000.00, Google Gsuite 3,617.82, Postmatic 2,650.00, each with a raw bank memo and "Create a rule from this". |
| Drivers | One card, "Staff time commitment", "Answered per person, per entity · percent", month select Nov 2026. A gold "Not set for 2026-11-01" panel, then "Answer 2026-11-01" with, per subject (Payroll taxes and fees, Sarah Edelman, Sarah Ostermiller (salary), and more below the fold), five entity rows each with a "share of the pool" select and a "0 to 1" input. |
| Reconciliation | Pool, In the books, Resolved, Unresolved, Variance. Three gold rows: Sarah Ostermiller (salary) 5,500.00 unresolved, sub-row "2026-11-01 · driven"; Blake Jacobsen (salary) 1,666.67, "2026-11-01 · direct"; Payroll taxes and fees 834.92, "2026-11-01 · driven". Then every other rule at 0.00 in all four columns. |

The date is November 4. The page reads the dashboard period, `mtd`, so every figure covers
four days.

---

## 2. Findings

**Wrong on screen** (data or words that mislead). Phase 1.

| # | Finding | Evidence | Fix |
|---|---|---|---|
| U1 | **"To partners" and "Retained" figures sit in the wrong columns.** Each "To partners" value renders flush left in its track, so it sits against "In the books" ("0.00 0.00"), and each "Retained" value sits under the "To partners" header. Blake Jacobsen (salary) reads as 1,666.67 sent to partners when it is 1,666.67 retained and pending. | Rules screenshot. `RulesView.jsx` wraps both figures in `<span className="al-hide-narrow">`. The right-align rule in `BooksAllocations.jsx` covers `.al-partner-row > .al-hide-narrow, .al-recon-row > .al-hide-narrow` and omits `.al-rules-row`. The comment above it describes this exact bug on the other two ledgers. | §8.1: one `Money` component that is a right-aligned block, so a wrapper can never break it. |
| U2 | **A Direct rule whose entity is the pool entity never resolves.** `derive_month` skips the pool entity's own share ("RETAINED IS NEVER A ROW"), so a Direct rule to itself writes no row. `reconciliation` counts a month resolved only if a row was written or `method == "retained"`. So the rule is pending every month, gold on Rules, listed on Reconciliation, and counted in the "Reconciliation · 3" badge. | Rules and Reconciliation screenshots, Blake Jacobsen (salary), "2026-11-01 · direct". `alloc_engine.py` `derive_month` docstring and `reconciliation` (`retained_only = v.method == "retained"`). **Confirm** the rule's one target is its pool entity; if it is not, the cause is something else and Phase 1 must find it. | §7.1 |
| U3 | **Reconciliation shows the method where the reason belongs.** "2026-11-01 · driven" tells nobody what to do. `reconciliation` builds pending as `{month, amount, method, tier}` with no `reason`, and `ReconView` falls back to `p.method`. The reasons exist only inside `derive_month`'s pending list and are discarded. | Reconciliation screenshot. `alloc_engine.py` `reconciliation`, `ReconView.jsx` `REASON[p.reason] \|\| p.method`. | §7.2 |
| U4 | **Exceptions counts allocated cost as unclaimed.** A multi-line transaction skips the account-coverage check as well as the vendor check (`if not multi:` wraps both). D7 only forbade claiming by payee. Account rules read trial balances, which include every line, so the 8,031.59 "Payroll Pay" on `Payroll Expenses:S. Ostermiller` is already in the "Sarah Ostermiller (salary)" pool, and is also listed and counted in the 32,126.60. | Exceptions screenshot, first card. `alloc_engine.py` `exceptions`. | §7.3 |
| U5 | **Every version's approver reads "Unknown".** `version_out` returns no `approved_by`; `RulesView` prints `ver.approved_by \|\| "Unknown"`. The approver is the one field the version table exists to name. | `alloc_rules.py` `version_out`, `RulesView.jsx` `Expanded`. | §7.4 |
| U6 | **Reconciliation's Variance column is Unresolved with the sign flipped.** `variance = resolved - pool`, `unresolved_amount = pool - resolved`. Every pending pool shows twice, once in gold and once in parens. | Reconciliation screenshot. `alloc_engine.py` `reconciliation`. | Remove the column (§6.4). Keep the field in the API. |
| U7 | **Tier cards add up money in the browser and do not say what the number is.** `SummaryCards` sums `pool_amount` client side, against the file header's "every figure is the server's". The card shows "1,666.67" with no label and no period. | Rules screenshot. `RulesView.jsx` `SummaryCards`. | §7.4 `meta.tier_totals`; §6.1 |
| U8 | **Two copies of the reason vocabulary that disagree.** `REASON` is defined in both `RulesView.jsx` and `ReconView.jsx` with different wording for the same code. | Both files. | §7.4 `meta.reasons`; §8.2 |

**Look and feel.** Phases 2 to 7, through §9.

| # | Finding | Evidence | Fix |
|---|---|---|---|
| U19 | **Boxes inside boxes.** Drivers is three borders deep (driver card, gold Not set panel, Answer panel). Exceptions is 21 separate cards with gaps between them. Rules puts four bordered cards above a bordered table. Nothing reads as the main surface. | Drivers, Exceptions, Rules screenshots. | §9 |
| U20 | **No type hierarchy.** Names, method lines, figures and headers all sit between 11.5 and 13px; the largest figure on Rules is 15px. Column headers and eyebrows are 10px in `L.faint`, too light to read on paper. | All four screenshots. `EYEBROW` in every view file. | §9 |
| U21 | **Controls at five heights.** Sub-nav buttons, the entity select, Change, Create a rule, the driver selects and Commit are all different heights and paddings, set by literals in each file. Native select chevrons differ by browser. | All four screenshots. Inline `padding` on every `<button>` and `<select>`. | §9 |
| U22 | **Gold everywhere.** Tier 3 rows, pending rows, gap chips, the Not set panel and Reconciliation rows all use the flag palette. On Rules 16 of 46 rows are washed gold, so the pending ones do not stand out. | Rules, Drivers, Reconciliation screenshots. | §9 |
| U23 | **Tall, sparse rows.** Two-line rows around 52px where most cells read 0.00, and the method sentence takes a third of the width. | Rules and Reconciliation screenshots. | §9 |
| U24 | **No interaction states.** Rows and buttons have a pointer cursor and nothing else: no hover, no visible keyboard focus, no pressed state, no chevron saying a row expands. | No `:hover` or `:focus-visible` rules in the page style block. | §9 |
| U25 | **Loading and saving are bare text.** "Reading this month." stands in for content; a commit or a saved rule changes a button label and nothing confirms it. | `DriversView.jsx`, `ExceptionsView.jsx`. | §9 |
| U26 | **Loose composition.** Tier card figures float top right away from their labels. Exception buttons sit bottom right, far from the payee they act on. The sub-nav, toolbar and table do not share edges. | Rules and Exceptions screenshots. | §9 |

**Structure.** Phases 2 to 6.

| # | Finding | Evidence | Fix |
|---|---|---|---|
| U9 | **No visible month, and the default is four days.** The page takes the dashboard `period` (`Books.jsx` default `mtd`). Rules and Reconciliation never say which period they cover; Exceptions says "in mtd"; Reconciliation's footer says "Covering 1 month of mtd". On November 4 nearly every rule shows 0.00, so the tier cards read as if 39 of 46 rules do nothing. Drivers has its own month select per card, unconnected to the rest of the page. | All four screenshots. `BooksAllocations.jsx`, `DriversView.jsx` `DriverCard`. | §5.1 |
| U10 | **The create button is under the table.** "New allocation rule" renders after the `StatePanel`, below all 46 rows, with `marginTop: 16`. The screenshot has no way to create a rule in view. This is the question Connor asked on 2026-09-09 about the mockup, now in production. | Rules screenshot. `BooksAllocations.jsx`. | §5.2 |
| U11 | **Rules is a 46-row alphabetical list where most rows are zero.** The Method column repeats one of six sentences. The second line ("T4 vendor Active Campaign") repeats the pool name behind an enum. There is no search. Tier filtering exists only by clicking a card, which nothing suggests. | Rules screenshot. `RulesView.jsx`. | §6.1 |
| U12 | **Tier 3 rows look selected.** `tierRowStyle` draws `inset 0 0 0 3px` on all four sides. Addevent reads as a focused row. The spec called for a left edge. | Rules screenshot, Addevent. `TierMarker.jsx` `tierRowStyle`. | §6.1 |
| U13 | **Row weight is applied to the whole row.** Tier 1 rows bold their method sentence and their zeros. | Rules screenshot, Blake Jacobsen rows. `tierRowStyle` sets `fontWeight` on the row. | §6.1 |
| U14 | **The driver form is one list of dropdown and input pairs.** Each subject repeats all five entities, each with a "share of the pool" select and a "0 to 1" input, so six subjects is 60 controls with the Commit button below them all. The card says "percent" and the inputs want fractions. Two panels explain the same rule at length. Nothing says what each subject's numbers move in dollars. "Payroll taxes and fees" is a pool, listed under "Answered per person". | Drivers screenshot. `DriversView.jsx` `CommitForm`, `NotSet`. | §6.3 |
| U15 | **Exceptions is one tall card per transaction.** A rule is per payee, but the queue is per charge, so a repeating vendor appears once per charge. Raw bank memos run full width in body text. Every card has the same action. Nothing suggests a rule even where Payables already knows the vendor's entity. | Exceptions screenshot. `ExceptionsView.jsx`. | §6.2 |
| U16 | **Reconciliation is 43 rows of zeros under three that matter,** and a pending row offers no way to fix it. | Reconciliation screenshot. `ReconView.jsx`. | §6.4 |
| U17 | **Raw formats in copy.** ISO dates (`2026-11-01`, "From 2026-01-01", "Committed 2026-11-03"), enum codes (`driven`, `direct`, `account`, `vendor`, `mtd`, `T4 vendor`), and the period token. | All four screenshots. | §8.3 |
| U18 | **Explanations where labels belong.** "It is not carried forward from last month, because a number nobody looked at would read as one somebody decided" appears on every uncommitted month. The reasoning belongs in the code comment, where it already is. | Drivers screenshot. | §8.4 |

---

## 3. Principles

These decide anything this document does not spell out.

1. **The month is the unit.** Every view shows one month unless the reader chooses year to
   date, and says which in words.
2. **What needs a person comes first.** Pending before resolved, unclaimed before claimed,
   amount before name. Zero-activity rows fold into one line at the end.
3. **One number, one place.** A figure appears once per view. No column that is another column
   negated.
4. **Words, not codes.** Every enum is rendered through the server's vocabulary (`meta.tiers`,
   `meta.methods`, `meta.reasons`, `meta.scopes`). The frontend holds none.
5. **Every pending item says why, and links to the fix.**
6. **The server computes, the browser formats.** Unchanged from the module's standing rule.
   Percent display strings come from the server (`pct`); typed percents go to the server as
   strings and are converted there.
7. **Labels over explanations.** One line of copy where a sentence would do. Rationale for the
   design lives in comments and specs.
8. **The mockup is the look.** Where this document and `Allocation Rules v2.dc.html` disagree on
   appearance, the mockup wins. Where they disagree on behaviour or data, this document wins.

---

## 4. Layout of the page

```
Books / Allocation rules
[Rules] [Exceptions · 9] [Drivers · 1] [Reconciliation · 2] [Partner]      Month [October 2026 v]

  Needs you in October:  9 unclaimed charges  4,512.30   ·   2 rules not resolved  6,334.92
                         ·   Staff time commitment not committed

  ( view )
```

- The month control and the "Needs you" strip are page level, above every view, and outside
  `StatePanel` (the same reason the Partner picker sits outside it).
- Badge counts on the sub-nav come from the summary endpoint (§7.5), not from counting rows.
- The strip's three items are links: to Exceptions, to Reconciliation, to Drivers with that
  driver selected. An item with nothing in it is omitted; with all three empty the strip reads
  "Nothing needs you in October."

---

## 5. Page shell

### 5.1 The month control

- Lives in `BooksAllocations.jsx` state, independent of the dashboard period selector (§11 D1).
- Options: every month from the fiscal year start through the current month, newest first, as
  "November 2026", plus "Year to date". Fiscal start from `CoaSettings` as the engine already
  reads it.
- **Default: the last complete month.** On November 4 that is October.
- Every hook takes the month: `useAllocRules({month})` and siblings pass `period_start` and
  `period_end` (the first and last day of the month) to the existing routes. `_period()` in
  `routers/books.py` already prefers explicit dates, so no route change. Year to date passes
  `period=ytd`.
- Drivers reads the same month. The per-card month select in `DriverCard` is removed.
- Remember the choice for the session in `sessionStorage` under `alloc.month`. Never write the
  dashboard's period.

### 5.2 Toolbar

Directly under the sub-nav, per view. On Rules: search, tier chips, entity select, and **New
rule** as the primary button at the right end. On Exceptions: the pool strip and its Change
button. Remove the button that renders after `StatePanel`.

### 5.3 Mobile

The month control and sub-nav wrap to two lines below 560px. The strip stacks its items. No
horizontal scroll at 375px on any view.

---

## 6. Views

### 6.1 Rules

**Toolbar.** Search (filters pool name, pattern and entity names, client side), tier chips
`All · T1 Direct 7 · T2 Traced 0 · T3 Driven 16 · T4 Retained 23` with counts from
`meta.tier_totals`, entity select, New rule.

**Summary line**, replacing the four cards:

```
October 2026     In the books 48,210.55     To partners 31,904.12     Stayed 9,971.51     Not resolved 6,334.92
```

From `meta.totals` (§7.4). "Not resolved" in `L.flagInk` and omitted when zero. Tier counts live
on the chips; the cards go.

**Ledger columns.** `marker | Rule | Goes to | In the books | To partners | Stayed`.

| Column | Content |
|---|---|
| marker | `TierMarker` and `TierCode` stacked, unchanged |
| Rule | Line 1: `pool_name`, weight by tier (§2 table in the parent spec), the ONLY weighted text in the row. Line 2, muted: `{meta.scopes[scope_kind]} · {pattern}` only when the pattern differs from the pool name after normalization, else just the scope word; then ` · on {pool entity name}`. When pending, line 3 in `L.flagInk`: "{Month} not resolved: {meta.reasons[reason]}". |
| Goes to | `targets_label` from the server (§7.4), e.g. "The Forum 100%", "The Forum 50%, beCollective 30%, ULRG + Team 20%", "Stays on ULRG + Team", "By Staff time commitment", "By Meta campaigns". Replaces Method. |
| In the books, To partners, Stayed | `Money` (§8.1). Zero renders in `L.muted` at weight 400. Stayed is `retained` renamed in the UI only. |

**Order.** Pending rules first by unresolved amount, then by In the books descending, then by
name. Rules with zero In the books for the month fold into a last row, "31 rules with nothing
booked in October", that expands in place.

**Tier treatment** (fixes U12, U13): as the mockup. Tier 3 rows get `L.flagBg` and
`boxShadow: inset 3px 0 0 0 ${L.flagEdge}` (left edge only, never a ring). Pending shows as the
gold method line and "pending" in To partners. `tierRowStyle` returns background and shadow only;
weight moves to the name span.

**Expanded row.** Keep the three blocks, with:

- Why this rule exists: unchanged.
- How it resolves: each target as entity name, bar, server `pct` ("50%") and the month's dollars
  from `to_partners_by_target` (§7.4). Pending shows the reason sentence and the fix link (§6.4).
- Version history: `v2 · From April 2026 · Split 50/30/20 · Connor Leiva · Apr 2, 2026 · Current`.
  Method through `meta.methods`, approver from `approved_by.name` (U5), dates through §8.3.
- Supersede with a new version: unchanged.

**Empty states.** No rules: unchanged copy plus a New rule button. Filters match nothing: "No
rules match" and Clear filters.

### 6.2 Exceptions

**Group by payee.** The server returns groups (§7.3): one card per normalized payee on a pool
entity, with charge count, total, accounts, latest date, and the transactions.

```
Breakthroughs LLC                                          3 charges   16,500.00
Coaching/Consulting · on ULRG + Team · latest Nov 2
Suggested: Tier 1 to The Forum (Payables default)          [Create rule]  [Keep on ULRG + Team]
  > show charges
```

- Order by total descending.
- Accounts: the last segment of each FQN, deduplicated ("Coaching/Consulting"), full path on the
  expanded charge row.
- Expanded charges: date, last account segment, amount, and memo on one line in `F.num` at 11px,
  ellipsis, full memo on tap. QBO link per charge with `QboLink` from `books/ui.jsx`.
- Gap chips sit on the group: No memo, No payee, More than one account, "3 this month" (replaces
  "Repeats this period").
- **Create rule** opens the drawer with vendor scope, payee, pool entity and, when present, the
  suggestion's tier and target preselected.
- **Keep on {pool entity}** opens the drawer at Tier 4 with everything filled except the reason.
  The reason is still required (`validate_version` refuses an empty one, correctly).
- No payee groups ("No payee on these charges") offer only Create rule, scoped by account.

**Partly covered** (fixes U4). Below the queue, collapsed by default:

```
Worth a look · 1 charge, not counted above
Payroll Pay  8,031.59   Covered by Sarah Ostermiller (salary) on its main account. It posts to more
                        than one account, so the other lines could not be checked.   [Open in QuickBooks]
```

**Header.** "Unclaimed in October 4,512.30 · 9 charges from 5 payees". The pool strip stays as
is, compact, with Change.

### 6.3 Drivers

**Layout.** When more than one driver exists, a list at the top: label, status for the month
("Committed Nov 3" or "Not committed"), and the pool it moves ("Moves 6,334.92 in October").
Selecting one shows its matrix. One driver: the matrix directly.

**The matrix** (split drivers). Rows are subjects, columns are entities.

```
                                 ULRG + Team   Spring B   Sympli   The Forum   beCollective   Total
Sarah Ostermiller (salary)          [   ]       [   ]     [   ]     [ 70 ]       [ $2,500 ]   rest to ULRG
  feeds Sarah Ostermiller (salary) · 5,500.00                                          Fixed and rest v
Sarah Edelman                       [   ]       [   ]     [   ]     [ 50 ]       [ 50 ]        100%
  feeds Sarah Edelman · 3,200.00                                                       Percent v
Payroll taxes and fees              [   ]       [   ]     [   ]     [   ]        [   ]         0%
  feeds Payroll taxes and fees · 834.92                                                Percent v
```

- Column headers are entity names from the payload. No dots in this table (three of five share
  one accent, amendment 23, so a dot would add nothing).
- Each row has a mode, chosen per subject, defaulting to the mode of its last commit:
  - **Percent**: every cell is a percent, 0 to 100. Total shows "100%" in `L.deep` or the actual
    sum in `L.flagInk`. The browser adds typed percents for this display only (the one sum the
    parent spec allows).
  - **Fixed and rest**: each cell has a three-way unit control (%, $, rest). At most one cell per
    row is rest. Total shows "rest to {entity}".
- The second line under each subject names what it feeds and the month's pool, from the server
  (§7.6). That is the dollars at stake, and the reason to get it right.
- Blank rows are not submitted, as today.
- **Prefill:** a "Start from October" button fills every row from the previous commit. Nothing
  is prefilled without the click (keeps D8).
- **Commit bar,** sticky at the bottom of the view: "Commit November · 2 of 3 subjects answered"
  and the first blocker in words ("Sarah Edelman adds up to 90%"). Disabled with the blocker
  shown, never silently.
- **Committed state:** the same matrix read-only, values from the server's `pct` strings and
  amounts, "Committed Nov 3 by Connor Leiva", and **Change November**, which reopens the matrix
  seeded with the committed values and writes a new commit superseding it.
- **Count drivers:** one row, the entities as columns, each a count. Total shows the sum and
  each column's share as the server returns it after commit.
- **Mobile, below 660px:** one card per subject. Entity inputs in a two-column grid with the
  name above each input. Mode and total on the card header.

**Copy.** Card subtitle: "One row per person or pool, one column per entity." Not committed
state: a single line in the list and above the matrix, "Not committed for November. Rules that
read this driver allocate nothing until it is." Remove the `NotSet` panel and the "Answer"
eyebrow paragraph.

### 6.4 Reconciliation

**Columns.** `Rule | In the books | Resolved | Not resolved`. Variance is removed (U6).

**Order.** Pending rules first by amount; then resolved rules with activity; then a folded
"40 rules with nothing booked in October".

**Pending rows.** Under the rule, one line per pending month:

```
October · No driver numbers committed for Sarah Ostermiller (salary)           5,500.00   Commit October >
```

The action comes from the reason (`meta.reasons[code].action`, §7.4):

| Reason | Action | Goes to |
|---|---|---|
| `no_driver_values`, `driver_subject_missing` | Commit {Month} | Drivers, that driver |
| `fixed_exceeds_pool`, `allocation_exceeds_pool` | Change the numbers | Drivers, that driver |
| `double_count_risk` | Open rule | Rules, that row expanded |
| `source_not_connected` | Check the connection | Settings, integrations |
| `source_unmapped_spend` | Map campaigns | Rules, that row expanded |
| `unmapped_account` | Map account | Books, Mapping |
| `vendor_rule_collision` | Open rules | Rules, filtered to the payee |
| `multi_line` | Open in QuickBooks | QBO link |

**Totals row** at the foot. **Intercompany card** stays as is.

**Mobile.** Below 660px the three figures stack right-aligned under the rule name with inline
labels, the pattern the mockup's `.ar-figs` established. Never drop Resolved or Not resolved.

---

## 7. Backend changes

All additive. No migration.

### 7.1 Direct to self resolves (U2)

In `alloc_engine.reconciliation`, a rule-month is resolved when a row was written, or the method
is `retained`, **or every target is the pool entity**. Implement as one helper,
`_keeps_all(v, pool_business_id) -> bool`, used by `reconciliation` and by the pending logic in
`rules_payload`. Do not write a self row; `ck_alloc_not_self` is right.

### 7.2 One resolver, two callers (U3)

Extract the per-rule body of `derive_month` into
`resolve_rule_month(rule, v, pool_by_account, month, ctx) -> (rows, pending)`, pure apart from
reads already in `ctx`. `derive_month` writes the rows; `reconciliation` calls the same function
for rule-months with no written row and returns its `pending` entries, each with `reason`. The
file's own docstring on `rules_payload` states the reason: two computations of one figure is the
worst defect this module could have.

### 7.3 Exceptions: coverage and groups (U4, U15)

In `exceptions`:

- Apply the account-coverage check to multi-line transactions too. Only the vendor check is
  skipped for them (D7 as written).
- A multi-line transaction whose primary account is covered gets `status: "partly_covered"` and
  `covered_by: {rule_id, pool_name}`, and is excluded from `total`. Others are
  `status: "unclaimed"`. Do not reuse the existing `section` key; it already carries the
  statement section.
- Add `groups`: rows with `status == "unclaimed"` grouped by `(business_id, normalize_name(payee))`,
  each `{payee, business_id, count, total, accounts, latest_date, gaps, txn_ids, suggestion}`.
  No-payee rows group by account. Keep `rows` for compatibility.
- `suggestion` when the normalized payee matches a Payables `Vendor.name_norm` with a
  `default_business_id`: `{tier: 1, business_id, source: "payables"}`. Suggest only; never create.

### 7.4 Payload vocabulary and figures (U5, U7, U8)

`rules_payload` and the recon route return:

- `meta.methods`, `meta.scopes`, `meta.reasons` beside `meta.tiers`: code to label, and for
  reasons `{label, action, target}` per §6.4. Defined once in `alloc_rules.py` next to `TIERS`.
- `meta.totals` for the period: `pool_amount`, `to_partners`, `retained`, `unresolved`.
- `meta.tier_totals`: per tier `{count, pool_amount, pending_count}`.
- Per rule: `targets_label` (built server side from names and shares), each target's `pct`
  string ("50"), and `to_partners_by_target` for the period.
- `version_out` adds `approved_by: {id, name}` and `created_at` is already there.

### 7.5 Summary endpoint

`GET /books/allocations/summary?period_start=&period_end=` behind `books_user`:
`{unclaimed_total, unclaimed_count, payee_count, partly_covered_count, pending_rules,
pending_amount, drivers: [{id, label, committed: bool, committed_at, committed_by, pool_amount}]}`.
Feeds the strip and the badges. Built from `exceptions`, `reconciliation` and the driver tables;
no new computation.

### 7.6 Drivers: percents in, subjects with stakes out (U14)

- `POST /drivers/{id}/commits` accepts, for `share` rows, `pct` (a string, 0 to 100) instead of
  `value`. Convert with one function, `pct_to_share(text) -> Decimal`, in `alloc_rules.py` beside
  `_share`, quantized to `SHARE_PLACES`. Storage stays a fraction (amendment 12). The browser
  never divides.
- `GET /drivers/{id}/month` returns, per value, `pct` beside `value` for share rows, and
  `subjects: [{name, mode, rules: [{id, pool_name, pool_amount}]}]` for the month.
- `committed_by: {id, name}` on the commit.

---

## 8. Shared frontend pieces

New folder files, imported by every view. Each replaces copies that exist today.

### 8.1 `allocations/Money.jsx`

One component. A block-level, right-aligned span (`display: "block", textAlign: "right"`) so a
wrapper cannot misalign it. Ledger parens for negatives. Zero renders in `L.muted` at weight
400. Props:
`v`, `tone`, `size`. Replaces the three local `Money` functions.

### 8.2 `allocations/words.js`

Reads `meta.*` from the payload and exposes `methodLabel`, `scopeLabel`, `reasonOf`,
`tierLabel`. No vocabulary constants of its own (A10 still holds). Delete both `REASON` maps,
`METHOD_LINE` and `KIND_WORD`.

### 8.3 Dates

Grep `frontend/src` for an existing month or date formatter first and reuse it. If none:
`fmtMonth("2026-11-01")` is "November 2026", `fmtMonthShort` is "Nov 2026", `fmtDay("2026-11-03")`
is "Nov 3" (with the year only when not the current year). Formatting only; it parses the ISO
string and computes nothing.

### 8.4 Copy rules for this page

No em or en dashes. No sentence longer than one line where a label fits. No enum code in any
string. Every period named as a month in words.

---

## 9. Visual target: the mockup

Production should look nearly identical to `Allocation Rules v2.dc.html`. That file is the
original `Allocation Rules v1.dc.html` look, unchanged, with the structure this document adds
(month control, needs-you strip, search, folded zero rules, suggestions and Keep on, Worth a look,
editable driver cells, Reconciliation without the decision panel or Variance). Open it in a
browser and click through: every view and state below is in it.

### 9.1 Every mockup colour is already a token

The mockup was built on the Statement's palette, so matching it needs **no new tokens**. Map
each hex in the mockup to `LEDGER` exactly as below, and use the token in code (the folder is
grepped for raw hex).

| Mockup | Token | Mockup | Token |
|---|---|---|---|
| `#F7F2EA` | `L.card` | `#0E2B22` | `L.deep` |
| `#D8CDBC` | `L.cardEdge` | `#F3EFE6` | `L.onDeep` |
| `#FFFFFF` | `L.paper` | `#40564B` | `L.body` |
| `#FDFBF7` | `L.controls` | `#5C7367` | `L.bodyDim` |
| `#FBF7F1` | `L.colHead` | `#7C8F85` | `L.muted` |
| `#FBF8F2` | `L.totalBand` | `#8B9C92` | `L.faint` |
| `#F0E9DD` | `L.track` | `#9AA9A0` | `L.code` |
| `#F7F1E7` | `L.ruleLine` | `#A4B1A8` | `L.ghost` |
| `#F4EDE2` | `L.ruleGroup` | `#7D9A8D` | `L.onDeepEyebrow` |
| `#E2D9CA` | `L.ruleInner` | `#A7C0B4` | `L.onDeepPill` |
| `#E8E0D3` | `L.ruleHeader` | `#2F5044` | `L.onDeepPillEdge` |
| `#DCD2C2` | `L.ruleSection` | `#9DBDAE` | `L.onDeepPct` |
| `#E0D7C9` | `L.pillEdge` | `#5F9C82` | `L.accent` |
| `#FEFAEE` | `L.flagBg` | `#8FAE9F` | `L.accentMuted` |
| `#E0C476` | `L.flagEdge` | `#3F7A63` | `L.accentInk` |
| `#D4B45F` | `L.flagRing` | `#EEF4F0` | `L.accentBg` |
| `#93854F` | `L.flagInk` | | |

Two values are not hexes in `LEDGER`: the primary button hover `#17402F` (use `L.deep` at
opacity 0.92) and the shadows `rgba(14,43,34,…)` (use `alpha(L.deep, …)` from `theme.js`).

**Type.** The mockup's Archivo is `F.num` (`var(--font-data)`): every figure, eyebrow, code
and tag. Its Inter is `F.body` (`var(--font-text)`): names, prose, buttons. Never the family
names.

### 9.2 Recipes, by piece

Sizes are px. "Eyebrow" means `F.num` 10, letter-spacing 0.14em, uppercase, `L.faint`.

**Page frame.** The Allocations page sits in one frame below the Books header: `L.card` ground,
1px `L.cardEdge`, radius 16, `box-shadow: 0 16px 38px -26px alpha(L.deep, .5)`, max width 1180.

**Dark bar** (replaces nothing; new). `L.deep`, padding 11 26. Entity chips: height 28, padding
0 13, radius 999, `F.body` 13; off `L.onDeepPillEdge` border, transparent, `L.onDeepPill` text,
500; on `L.paper` fill and border, `L.deep` text, 600. At the right: eyebrow "Month" in
`L.onDeepEyebrow` and the month select, height 28, radius 999, 1px `L.onDeepPillEdge`, `L.deep`
fill, `L.onDeep` 13/600, chevron `L.onDeepPill`. The mockup's "Books / Allocation Rules" eyebrow
is dropped: the Books header already says it.

**Tab nav** (replaces the pill `SegNav`). `L.controls`, bottom 1px `L.ruleHeader`, padding 0 26.
Tab: `F.body` 13, padding 11 14 12, margin-bottom -1. Inactive `L.faint` 500; active `L.deep` 600
with a 2.5px `L.accent` bottom border. Labels "Rules", "Exceptions · 4", "Drivers · 1",
"Reconciliation · 3", "Partner".

**Needs-you strip.** `L.totalBand`, bottom 1px `L.ruleHeader`, padding 9 26, gap 8 18. Eyebrow in
`L.flagInk`. Each item: 7px ring (2px `L.flagRing`), label `F.body` 12.5 `L.body`, amount
`F.num` 600 `L.deep`; hover text `L.deep`.

**View body.** Padding 22 26 26, gap 16.

**Buttons.** Primary: `L.deep` fill and border, `L.onDeep` text, 600; 34 tall radius 9 13px in
a toolbar, 31 tall radius 8 12.5px in a card. Secondary: 31 tall, radius 8, 1px `L.pillEdge`,
`L.paper`, `L.bodyDim` 500; hover border and text `L.accentInk`. Quiet: no fill, `L.accentInk`
12/600, hover `L.deep`. Filter chip (tiers, drawer picks): 30 tall, radius 8, 12.5; off
`L.pillEdge` / `L.paper` / `L.bodyDim` 500; on `L.deep` / `L.deep` / `L.onDeep` 600.

**Inputs.** Search: 34 tall, 200 wide, radius 9, 1px `L.pillEdge`, `L.paper`, 13. Driver cell:
30 tall, radius 7, 1px `L.pillEdge` (`L.flagEdge` when the row is not set), `F.num` 13
right-aligned.

**Tier cards** (Rules). Grid `repeat(auto-fit, minmax(168px, 1fr))`, gap 11. Card: 1px
`L.ruleInner`, radius 12, `L.paper`, padding 12 14 13, `box-shadow: inset 3px 0 0` in the tier
colour (T1 `L.deep`, T2 `L.body`, T3 `L.flagRing`, T4 `L.ruleInner`). Lines: eyebrow "T1 Direct"
with count in `L.ghost` 11.5; amount `F.num` 21, tier weight, -0.02em, `L.deep`; caption "In the
books, Sep 2026" `F.num` 10 uppercase 0.12em `L.ghost`; gloss 11.5 `L.muted`. Amounts from
`meta.tier_totals` (§7.4).

**Ledger card.** 1px `L.ruleInner`, radius 13, `L.paper`, overflow hidden.
- Header: padding 9 18, `L.colHead`, bottom 1px `L.ruleHeader`, eyebrows; money headers right.
- Rules grid: `1fr 132px 118px 88px`, gap 14 (940: `1fr 112px 96px`; 660: `1fr 104px`).
- Row: padding 9 18, bottom 1px `L.ruleLine`. Marker 7px: T1 `L.deep` square; T2 2px `L.body`
  ring; T3 2px `L.flagRing` ring; T4 empty. Tier code `F.num` 10.5/600, 0.04em, `L.faint`, 22
  wide. Name `F.body` 13.5, tier weight, `L.deep` (T4 `L.muted`), then " · Vendor" in `L.ghost`
  400. Method line 11.5 `L.faint`, `L.flagInk` when pending. Goes to 12 `L.bodyDim`. In the books
  `F.num` 13.5 tier weight. To partners `F.num` 12: `L.accentInk`, "pending" `L.flagInk`, zero
  `L.ghost`. Tier 3: `L.flagBg` plus `inset 3px 0 0 L.flagEdge`.
- Expanded: `L.totalBand`, bottom 1px `L.ruleInner`, padding 15 18 17, gap 15, left inset 3px
  (T3 `L.flagEdge`, else `L.ruleSection`). Blocks: eyebrow, then 12.5 `L.body` at 1.55 line
  height, max 74ch. Split rows: 8px square in the entity tone, label 12.5, pct `F.num` 11.5
  `L.faint`, 5px bar on `L.track` radius 999, dollars `F.num` 12.5 right, min 104. Pending panel:
  1px `L.flagEdge`, radius 10, `L.flagBg`, padding 11 13, ring, 12.5 `L.flagInk`. Version rows:
  padding 8 11, radius 8, current on `L.accentBg`; "v2" `F.num` 11/700; window `F.num` 11.5
  `L.muted`; split 12 `L.body`; stamp 11.5 `L.faint`; tag `F.num` 10/700 0.06em uppercase radius
  5 (Current `L.paper`/`L.accentInk`, Superseded `L.track`/`L.faint`). Action row: top 1px
  `L.ruleInner`, padding-top 13.
- Fold row: padding 11 18, `L.controls`, 12.5 `L.muted`, chevron turns 90 degrees.
- Total row: padding 13 18, `L.totalBand`, top 1.5px `L.deep`; label 14/700; figure `F.num`
  15/700 -0.02em; To partners `F.num` 13/600 `L.accentInk`.
- Under the card, the tier legend sentence, 12 `L.ghost`.

**Exceptions.** Header: eyebrow "Unclaimed in September", figure `F.num` 30/600 -0.02em, count
12.5 `L.muted`; explainer 12 `L.muted`, max 56ch. One card per payee: 1px `L.ruleInner`,
radius 13, `L.paper`. Body padding 14 16 15: payee 14.5/600; account `F.num` 11.5 `L.code`;
what is known 12.5 `L.muted`; suggestion 12 `L.accentInk` after a T1 square; gap chips 11/600,
padding 3 9, radius 5, `L.flagBg` / `L.flagEdge` / `L.flagInk`; amount `F.num` 20/600; date
`F.num` 11.5 `L.code`. Action band: top 1px `L.ruleGroup`, `L.controls`, padding 11 16, primary
"Create Rule From This", secondary "Keep On {entity}", then "Clears 4 items, including 4
historical" 12 `L.muted`. Saved band: `L.accentBg`, `L.accent` dot, quiet Undo. Worth a look:
one card on `L.totalBand` with an eyebrow header and chevron, rows inside on `L.paper`.

**Drivers.** Eyebrow with the driver name, one line 12.5 `L.muted`; Commit (primary) at the
right with the "Uncommitted changes" chip (11.5/600 `L.flagInk` on `L.flagBg`, 1px `L.flagEdge`,
radius 6). Table in the ledger card. Grid `1fr auto 156px`; the entity cells `76px` each, gap
10. Name 13.5/500 `L.deep`; note 11.5 `L.faint` ("Percent · …" or "Fixed and rest · …"). Total
pill `F.num` 12/700, padding 4 10, radius 999: `L.accentBg`/`L.accentInk` at 100% or with a
rest, else `L.flagBg`/`L.flagInk`; "Not set" when empty. A not set row: `L.flagBg` plus the
3px `L.flagEdge` inset. Below: the other drivers as cards, then What Commits Change on
`L.totalBand` (1px `L.ruleInner`, radius 13).

**Reconciliation.** Ledger card, header "Rule · In The Books · Resolved · Not Resolved". Figures
grid `126px 126px 118px`. Not resolved pill `F.num` 12.5/600, padding 3 9, radius 999 ("ties"
on `L.accentBg`). Pending rows first: `L.flagBg`, 3px `L.flagEdge` inset, name 600, reason line
11.5 `L.flagInk`, then the fix link as a quiet button with "›". Total row on `L.deep`: label
14/700 `L.onDeep`; figures `F.num` 15/700 `L.paper`, 15/600 `L.onDeepPct`, 13/700 `L.onDeep`.
Check cards: 1px edge, radius 12, padding 14 15 15, 7px dot, eyebrow, value `F.num` 19/600
(good: `L.ruleInner` / `L.accentBg` / `L.accentInk`; needs a look: `L.flagEdge` / `L.flagBg` /
`L.flagInk`).

**Partner.** As the mockup's Partner View: dark entity band, ledger with composed rows on
`L.flagBg` with the 3px inset and reason line, the draw panel on `L.flagBg`, the basis panel on
`L.totalBand`.

**Rule drawer.** As the mockup: 460 wide, `L.card`, left 1px `L.cardEdge`,
`box-shadow: -18px 0 44px -24px alpha(L.deep, .55)`, scrim `alpha(L.deep, .34)`. Sticky `L.deep`
header with eyebrow and title. Steps headed "1 · What It Matches" to "5 · From When". Tier cards
carry the marker, name and gloss; Tier 3 selected on `L.flagBg`. The preview and Save sit in a
sticky `L.controls` footer. Below 560: full width, opaque `L.card` scrim.

**Mobile.** The mockup's breakpoints and classes: at 940 the Goes to column drops; at 660 To
partners drops, the Reconciliation and Driver figure groups stack right-aligned with inline
labels (`.ar-figs`, `.ar-pcts`, `.ar-figlab`), and table headers hide.

### 9.3 Where the mockup overrides §6

- Rules keeps the four tier cards (with the caption). The §6.1 summary line is not built.
- The Rules ledger has four columns, Rule, Goes to, In the books, To partners. What stayed is
  shown in the expanded row, as the pool entity's share.
- Exceptions renders one card per payee in the card recipe above; the charges can expand inside
  the card.
- Drivers renders one column per entity in the payload (the mockup shows three because its
  people split across three). Same cell and total recipe.
- The sub-view nav is the underlined tab row, not the pill segmented control.
- The allocation detail lives on the Statement's composition panel, styled with the expanded-row
  blocks above.

### 9.4 Shared controls

Build the recipes once in `allocations/controls.jsx` (`Button`, `Select`, `Input`, `Chip`,
`Tabs`) and `allocations/Money.jsx`, so the look has one place to change. Nothing else in the
folder renders a `<button>`, `<select>` or `<input>` (check B10).

### 9.5 States the mockup implies

- Focus: `:focus-visible`, 2px `L.accentInk` outline, offset 2 (the mockup's rule).
- Hover: rows `L.totalBand`; Tier 3 rows `L.flagBgHover`; buttons per §9.2.
- Loading: three skeleton rows inside the card (`L.track` bars), not a text line.
- Saved: the `L.accentBg` band with a dot, as on an exception card.
- Motion: row expand 130ms (opacity and 3px rise), drawer 150ms slide, nothing on figures.
  `prefers-reduced-motion` removes it all.

### 9.6 Visual check (Phase 7)

Screenshot each view at 1180px beside the same view in `Allocation Rules v2.dc.html`, and at 820
and 375. Differences in colour, type size, weight, spacing, radius or border are defects unless
§9.3 explains them. Attach both images to §13.

---

## 10. Phases

Every frontend phase builds its views to §9 and the mockup. Phase 7 checks it.

### Phase 1: Correct what is shown

U1 to U8. §7.1, §7.2, §7.4, §8.1, §8.2, and the U1 fix applied through `Money`.

**Done when:**
- On the Rules view, every figure sits under its own header at 1280px and 820px (screenshot).
- A Direct rule whose target is its pool entity shows nothing pending on Rules or Reconciliation,
  and the Reconciliation badge drops by one (test plus screenshot).
- Every pending row on Reconciliation shows a reason sentence; no row shows a method code.
- Exceptions' unclaimed total excludes partly covered rows; with the November data, Payroll Pay
  8,031.59 is no longer in it.
- Version history shows a person's name on every version.
- `grep -n "REASON\s*=\|METHOD_LINE\|KIND_WORD" frontend/src/books/allocations/` returns 0.
- `grep -n "row.pool +=" frontend/src/books/allocations/` returns 0.

### Phase 2: Page shell

§4, §5, §7.5, §8.3, and the shared visual pieces: the page frame, dark bar, tab nav and strip
(§9.2), `controls.jsx` (§9.4), focus, hover and skeleton loading (§9.5).

**Done when:** the month control defaults to October on November 4 and every view states the
month in words; changing it changes every view's figures; Drivers has no month control of its
own; New rule is visible without scrolling on Rules at 1280px and 375px; the strip's links land
on the right view with the right driver selected; the dark bar, tabs and strip match the mockup
side by side; tabbing shows a focus ring on every control.

### Phase 3: Rules

§6.1.

**Done when:** in October, pending rules are the first rows; zero rules are folded; searching
"jacob" leaves the two Blake Jacobsen rules; Tier 3 rows have a left edge only; a greyscale
screenshot still distinguishes all four tiers; no row has bold numbers.

### Phase 4: Exceptions

§6.2, §7.3.

**Done when:** a payee with three charges is one card showing "3 charges" and their total;
Create rule opens the drawer prefilled with vendor scope, payee, pool entity and any suggestion;
Keep on {entity} opens the drawer at Tier 4 with only the reason empty; saving either removes
the card; partly covered charges appear only under Worth a look.

### Phase 5: Drivers

§6.3, §7.6.

**Done when:** a three-subject month commits from one screen with the Commit bar always visible;
typing 70 in a Percent row stores 0.7000; a Percent row adding to 90 blocks the commit and the
bar says which row; Ostermiller's Fixed and rest row (70%, $2,500, rest) commits; each subject
shows what it feeds and the month's pool; at 375px each subject is a card with no horizontal
scroll.

### Phase 6: Reconciliation

§6.4.

**Done when:** there is no Variance column; pending rows come first with a reason and an action
link that lands on the fix; zero rules are folded; at 375px all three figures show on every row.

### Phase 7: Look at it

Screenshots of all four views at 1280, 820 and 375 with November data, attached to §13. Run
the §9.6 check on each. Fix what they show before calling the work done.

---

## 11. Decisions made for Connor (change any of them)

**D1. The page has its own month control,** independent of the dashboard period. Allocation work
is month-grained; the dashboard period is built for KPIs, and `mtd` on the 4th shows nothing.

**D2. Default to the last complete month.** Closing a month is the job. The strip still names
the current month's open drivers once it has any.

**D3. Variance leaves the UI.** It is Unresolved negated. The API field stays.

**D4. Zero rows fold.** Visible on demand, never deleted.

**D5. Percents are typed as percents everywhere and converted on the server.** Storage stays a
fraction (amendment 12).

**D6. Partly covered charges are listed but not counted as unclaimed.** Counting them double
reports cost that is already allocated.

**D7. "Keep on {entity}" still asks for a reason.** One click to the drawer, not one click to a
rule. The reason is the feature.

**D8. Exceptions group by payee.** A rule is per payee, so the queue is too.

**D9. The mockup is the visual source of truth.** Connor's call, 2026-11-04: production should
look nearly identical to the original mockup. Its palette is `LEDGER` already, so this costs no
new tokens.

**D10. Every control comes from `controls.jsx`.** The look only stays consistent if there is one
place to change it.

---

## 12. Only Connor can answer

1. **Blake Jacobsen (salary).** Confirm its one target is its own pool entity (ULRG + Team). If
   so, U2 is the cause. If not, Phase 1 must find another one.
2. **"Payroll taxes and fees" as a driver subject.** Production answers it like a person. The
   original intent was that payroll tax follows each person's wage split. Keep it as its own
   row, or derive it from the people rows (a backend change, not in this guide).
3. **Short entity names** for the driver matrix columns at 820px ("ULRG", "Sympli"). If
   `Business` should carry a short name, that is a Settings field; otherwise the columns truncate.

---

## 13. Build record

Fill per phase: commit, what changed, the gate as measured, screenshots, anything corrected
while building.

### Phase 1

**Done 2026-11-04.** U1 to U8, plus the coverage half of §7.3 (see "corrected while building").

Backend, `alloc_engine.py` and `alloc_rules.py`:
- `resolve_rule_month(rule, v, pool_by_account, month, ctx)`, the one resolver, with
  `ResolveCtx` for the per-month reads. `derive_applied` writes what it returns;
  `reconciliation` calls it for a rule-month that wrote no row, so the reason on screen is the
  reason that stopped the write (§7.2, U3). `_spend_context` extracted beside it, because the
  traced-spend cache was inline in `derive_all` and reconciliation needs the same denominators.
- `_keeps_all(v, pool_business_id)`, one call site in `reconciliation` (§7.1, U2).
- `vocabulary()`: `methods`, `scopes`, `reasons` with `{label, action, target}`, `gaps`,
  `value_kinds`. On the rules, recon, drivers and preview payloads (§7.4, U8).
- `meta.totals`, `meta.tier_totals`, per-rule `targets_label` and `to_partners_by_target`,
  `version_out(v, names)` with `approved_by` as an object, `approver_names()` (U5, U7).
- `exceptions()`: account coverage now applies to multi-line rows, which gain `status` and
  `covered_by` and leave `total` when covered (U4).

Frontend: `allocations/Money.jsx` and `allocations/words.js` added; four local `Money`
functions, three `REASON` maps, `METHOD_LINE` and `KIND_WORD` deleted; `.al-rules-row` added to
the right-align rule and `.al-money` added beside it; `sampleBooks.js` brought to the new
payload shape.

**Gate as measured:**
- Every figure under its own header at 1280px and 820px, screenshotted on `npm run build` plus
  `preview`, not the dev server. At 820px Resolved and Variance drop out and the rest stay
  aligned, which was the specific risk in `.al-money`.
- Direct-to-self resolves and leaves the badge: `test_a_direct_rule_to_its_own_entity_...`.
- Every pending row shows a reason sentence: "July 2026 . No driver numbers committed" on
  screen where "2026-07-01 . driven" was.
- A covered multi-line charge is listed and not counted: four tests, both sides of the branch.
- Version history names a person.
- `grep -rnE "REASON\s*=|METHOD_LINE|KIND_WORD" $F` -> 0. `grep -rn "row.pool +=" $F` -> 0.
  `grep -rn "function Money" $F` -> 1, in `Money.jsx`.
- `backend/tests/test_alloc_ui_phase1.py`, 17 tests. 166 pass across the allocation suites.
  Full backend suite 2761 pass, 2 fail, both in `test_edge.py`, which contains no reference to
  allocations, passes alone and passes beside the changed modules: an ordering artifact of the
  shared test database, raised separately.

**Corrected while building:**
1. **§7.2 names a function that does not exist.** The writer is `derive_applied`.
2. **§7.1 is one line, not two callers.** `rules_payload` has no pending logic; it reads
   `reconciliation`. Fixing one fixes the Rules view, which is why U1's wrong figure and U2's
   wrong state had the same root.
3. **`_keeps_all` needs a non-empty guard.** `all()` of an empty list is True and `targets` is
   NULL on every driven version, so the obvious one-liner reports an uncommitted driver month
   as RESOLVED. Mutation-tested: all five new controls fail a test when broken.
4. **The parity gate** §0.1 item 5 asked for: the pure resolver and the writer are compared
   field for field on a pool with an odd half-cent across three unequal shares.
5. **§7.3's coverage half moved into Phase 1,** because Phase 1's own "Done when" demands the
   figure while §10 lists §7.3 under Phase 4. The grouping half stays in Phase 4. `ALLOCATION-
   RULES-SPEC.md` §5.6 amended in the same commit, per §0.1 item 3.
6. **`targets_label` names the driver.** §6.1 asks for "By Staff time commitment"; returning
   the method sentence would print the same words on every driven row.
7. **`sampleBooks.js` is part of the payload contract.** Reading server totals broke the demo
   workspace: tier cards to 0.00 and raw `split` in the Method column. Any payload change
   needs that file.

### Phase 2
### Phase 3
### Phase 4
### Phase 5
### Phase 6
### Phase 7

---

## Appendix A. Checks

`$F` is `frontend/src/books/BooksAllocations.jsx frontend/src/books/allocations/`.

| # | Check | Expect |
|---|---|---|
| B1 | `grep -rnE "#[0-9A-Fa-f]{3,8}\b" $F` | 0 |
| B2 | `grep -rnP "[\x{2013}\x{2014}]" $F` | 0 |
| B3 | `grep -rnE "\b20[0-9]{2}-[0-9]{2}-[0-9]{2}\b" $F` | 0 outside tests and samples. No ISO literal in rendered copy. |
| B4 | `grep -rnE "\{(p\|r\|v\|ver)\.(method\|scope_kind\|reason)\}" $F` | 0. Codes go through `words.js`. |
| B5 | `grep -rnE "REASON\s*=\|METHOD_LINE\|KIND_WORD\|function Money" $F` | `function Money` once, in `Money.jsx`; the rest 0 |
| B6 | `grep -rnE "\+= *Number\(" $F` | 0. No money summed in the browser. |
| B7 | `grep -rn "inset 0 0 0 3px" $F` | 0 |
| B8 | `grep -rn "0 to 1" $F` | 0 |
| B9 | `grep -rn "period}" $F` | 0 in rendered strings. The period is a month in words. |
| B10 | `grep -rnE "<(button\|select\|input)\b" $F` | matches only in `allocations/controls.jsx` |
| B13 | `grep -rn "inset 0 0 0" $F` | 0. Tier 3 is a left edge. |

## Appendix B. Screen to code

| Screen | File | Reads |
|---|---|---|
| Shell, month, strip | `BooksAllocations.jsx` | `/allocations/summary` |
| Rules | `allocations/RulesView.jsx` | `/allocations/rules` (`applied`, `meta.*`) |
| Exceptions | `allocations/ExceptionsView.jsx` | `/allocations/exceptions` (`groups`, `rows` with `status`) |
| Drivers | `allocations/DriversView.jsx` | `/allocations/drivers`, `/drivers/{id}/month` (`subjects`, `pct`) |
| Reconciliation | `allocations/ReconView.jsx` | `/allocations/recon` (`pending[].reason`) |
| Shared | `allocations/Money.jsx`, `allocations/words.js`, `allocations/controls.jsx`, `TierMarker.jsx` | payload `meta` |
| Visual target | `Allocation Rules v2.dc.html` (this project; copy to `docs/` beside the spec) | |
