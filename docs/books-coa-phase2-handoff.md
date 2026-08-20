# Books COA — Phase 2 handoff

Paste this whole file into a fresh Claude Code session to pick the build up at Phase 2.
Written 2026-08-20, at the end of Phase 1.

---

Continue the Books module on the Acumyn dashboard (`C:\Users\17192\Desktop\executive_dashboard`).
FastAPI + React, deploys to Railway from `main`. This is Phase 2 of the COA mapping build.

## Specs — read both before writing anything

- `C:\Users\17192\Downloads\SPEC-coa-mapping-provenance (1).md` — **current**. The
  un-suffixed file of the same name in that folder is STALE; ignore it.
- `C:\Users\17192\Downloads\SPEC-chart-of-accounts.md` — authoritative chart. Supersedes
  section 3 of the above.
- `C:\Users\17192\Downloads\pnl-provenance-mockup.html` — Phase 5 frontend reference.

## Already built

- **Phase 0 discovery**: `backend/coa_discovery.py` (commit `1c03f40`). Output in
  `backend/coa_discovery_out/` — gitignored, holds real account names and balances.
- **Phase 1**: commits `cfd7ba7` + `18cc6ab`. `StandardAccount` model, the 136-account chart
  in `backend/app/services/coa.py`, migration `0038_coa_standard_chart`, 22 tests in
  `backend/tests/test_coa_chart.py`. `Business` gained `archetype` + `gross_profit_label`.
- **NOT PUSHED.** Both commits sit on local `main`. Pushing runs `alembic upgrade head` in
  production.

## Phase 0 findings that shape Phase 2

- 693 accounts across 5 entities, only **254 with activity**. ULRG 271/124 (depth 5),
  Spring B 186/69, Sympli 155/13 (near-dormant, QBO default chart), beCollective 46/25,
  The Forum 35/23. All five trial balances sum to zero.

- **People-as-accounts are concentrated in subtrees, not scattered:**
  - ULRG — `61300 Contract Labor:Virtual Assistants:*`,
    `61000 Compensation:61100 Salaries:61120 Administration:*`,
    `51000 Commission Paid Out:51400 Buyer COS:*`
  - Spring B — `Contract Labor:*`, `Payroll Expenses:*`
  - beCollective — `Commissions:*`, `Payroll:*`
  - The Forum — `Contract Labor:*`

  **=> Build a `coa_map_rule` table**: `FullyQualifiedName` prefix → standard account. The
  sync consults it on first sight of an account and auto-writes the `coa_map` row. Without
  it, every new VA hire trips the 5.3 unmapped guard forever. `coa_map` itself stays exactly
  as specified — one row per `qbo_account_id`, keyed on ID.

- ULRG embeds account numbers in the **name**, not `AcctNum` (`"69000 Other Expense"`), and
  has 69000 twice: `69000 Other Expense` and `69000 Insurance`. Never key on name.

- **Intercompany already ties to the cent:** Forum `Due To SB Coaching` 447,968.76 ↔ Spring B
  `Due from The Forum`; beCollective 119,384.11 ↔ `Due from The Be Collective`; ULRG
  13,082.08 ↔ `Due From Utah Life RE Group`. **One break:** `Forum Transfer Account` is
  676,690.55 on Forum vs 679,140.55 on Spring B — off by **$2,450.00**.

- **Allocations already exist in QBO** as `Shared Service Expenses:` sub-accounts on The Forum
  (Payroll 176,778.11 / Advertising 169,456.22 / Dues 23,197.32) and beCollective
  (45,000.00 / 14,336.16 / 9,279.13). Bears directly on OQ6.

- Surface but do not fix: Spring B's `Due from The Forum`, `Due from The Be Collective` and
  `Due From Utah Life Real Estate Group` are typed Other Current Liability but are
  receivables. Section 9 territory.

## Decided — do not re-ask

- OQ1 / OQ2 / OQ3 resolved by `SPEC-chart-of-accounts`. Revenue standardized by **recognition
  timing**. Coaching = 6550.
- PLACE flow-through goes **below the line**: 9030 + 9040 other income, 9920 other expense.
- Spring B archetype = `program` (single value; the activation matrix reaches event accounts
  from program).
- Activation matrix read as: **Yes + Sometimes activate, Rarely + No do not.**
- 4600 Partnership = `point_in_time`. Balance sheet carried forward from mapping spec 3.2.

## Still open

- **OQ4** Charitable Donations placement / tax treatment — Acuity. 6570 seeded provisionally.
- **OQ4b** Gross vs net presentation for ULRG and Spring B — Acuity.
- **OQ5** provenance colour token — Connor. **Blocks Phase 5.** Spec recommends a dedicated
  `--provenance-wash` / `--provenance-edge` pair rather than a daffodil exception.
- **OQ6** allocation ingest, CSV vs in-app — see the QBO finding above.
- Untracked folder `expense-normalization-2026-08/` (vendor master, entity expense summary,
  shared cost candidates, findings memo). Unread, likely Phase 4 material.

## Build order

| Phase | Scope | Gate |
|---|---|---|
| 2 | `coa_map` + `coa_map_rule`, `coa_sync` job, mapping admin UI | |
| 3 | `account_period_balance`, `normalize_sign` with tests, unmapped guard, tie-out invariant | **Tie-out passes on all 5 entities before Phase 4** |
| 4 | `allocation_contribution`, ingest, zero-sum check | |
| 5 | Provenance computation, API, P&L render | OQ5 answered |
| 6 | Balance sheet on the same layer | |

## Conventions — non-negotiable

- Models go in `backend/app/models.py` (one file, not a package). Enum-ish values are
  `String` columns with a comment listing the values — **never** Postgres enums.
- `GUID()` instantiated, `JSONType` used bare, both from `app/dbtypes.py`.
- Migrations use idempotent guards. Copy the pattern in `0030_sales_desk.py` and `0038`.
- `0001_init` does `create_all()` of **current** models, so migrations do NOT replay from
  zero. Verify a new one via: `seed()` → `alembic stamp <prev>` → `downgrade -1` →
  `upgrade head`.
- **Always confirm `alembic heads` prints exactly ONE head before committing.** A fork
  crash-loops Railway into a prod outage. This has happened.
- Frontend: `theme.js` `T` tokens + `alpha()`, no hex literals, no local palettes. Verify UI
  on the prod build (`npm run build` + `preview`), not the dev server.
- Commit reviewed batches straight to `main`. **Do not push without asking** — push = prod
  migration.
- QBO is **read-only** in both phases. No writes to QuickBooks, ever.

## Environment

- Railway CLI: link the **acumyn.io** project, NOT "PLACE Command Center" — that's an old
  deployment with a stale database and a different `FERNET_KEY`. It cost hours last session.
- `backend/.probe.env` holds GHL tokens. Git-ignored. Never open or print it.
- Run a script against prod with:
  `railway run C:\Users\17192\Desktop\executive_dashboard\backend\.venv\Scripts\python.exe <script>`
- If auto mode's safety classifier starts blocking all Bash calls mid-session, switching auto
  mode off clears it.

---

Start by reading both specs, then build Phase 2. Show a build-order status chart with each
batch, and verify before committing.

**If the charts may have changed since 2026-08-20**, re-run `backend/coa_discovery.py` before
designing against the Phase 0 numbers above rather than trusting them.
