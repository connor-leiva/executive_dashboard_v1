# Spring Command Center

An executive financial + operational command center for Spring's three profit
centers — **ULRG + Team** (real estate), **Spring B** (beCollective + The Forum),
and **Sympli Mortgage** (50% JV). Each business is seen through a **financial
layer** (QuickBooks, source of truth) and an **operational layer** (the system
that runs it: Sisu / Follow Up Boss / Arive).

Built on multitenant infrastructure so it generalizes beyond Spring (tenant #1)
and can be productized later.

```
frontend/   Vite + React dashboard (the canonical mockup, wired to the API)
backend/    FastAPI + SQLAlchemy 2.0 (async) API, sync worker, QBO OAuth
```

## Quick start (local, zero infra)

The backend runs on **SQLite** out of the box — no Postgres needed for local dev.

### 1. Backend

```bash
cd backend
python -m venv .venv
# Windows:  .venv\Scripts\activate     ·  macOS/Linux:  source .venv/bin/activate
pip install -e .
python -m app.seed                       # DEV FIXTURE: one workspace + representative data.
                                         # Refuses to run against a real database - it wipes.
python -m scripts.create_tenant --slug acme --name "Acme" --owner-email o@acme.com
python -m scripts.create_operator --email you@example.com   # administers TENANTS,
                                                            # not a customer login
uvicorn app.main:app --reload            # http://localhost:8000  (docs at /docs)
```

Seed prints the owner login: `spring@springb.com` / `springtime`.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev                              # http://localhost:5173
```

- With **no** `VITE_API_BASE` set, the frontend renders bundled **sample data**
  (the full mockup) so you can see the UI instantly.
- To run it against the local API, create `frontend/.env.local`:
  ```
  VITE_API_BASE=http://localhost:8000/api/v1
  ```
  then log in with the seeded owner account.

## Phasing (see SPEC for detail)

- **Phase 1 — Operational (Sisu + FUB).** ULRG operational metrics + scorecards
  computed from transactions/leads. Financial panels show an "awaiting
  QuickBooks" empty state until Phase 2. ✅ wired (seed simulates the Sisu/FUB
  data; drop in the real clients to go live).
- **Phase 2 — Financial (QuickBooks).** OAuth per entity (one realm each), pull
  the ProfitAndLoss summary into `pl_snapshot`. Lights up every P&L panel,
  combined profit, portfolio revenue, composition, and cash. ✅ OAuth +
  parser + sync implemented.
- **Phase 3 — Referral flywheel (Arive).** Loan pipeline + identity matching +
  capture rate + per-agent leaderboard. Schema + UI stubbed; `Flywheel.available`
  flips true when built.

## Going live with real data

1. **Sisu + Follow Up Boss** — port the existing clients (endpoints, auth,
   field mappings) from the Realtor.com reporting dashboard into
   `backend/app/integrations/{sisu,fub}.py` (scaffolds + data contracts are
   there), then set `SISU_*` / `FUB_*` env and connect the integrations.
2. **QuickBooks** — set `QBO_*` env, then per business hit
   `GET /api/v1/integrations/qbo/connect?business_id=…` (the dashboard's
   "Connect QuickBooks" button does this). Tokens are encrypted at rest
   (Fernet); refresh-token rotation is persisted on every call.
3. Run the **worker** for scheduled syncs: `python -m app.worker`.

## AI Employees

A configurable agent surface (rail tab **AI Employees** + a Settings group). An AI
employee = identity + product-seeded **skills** (prompt template + output contract) +
**triggers** (manual / cron / a `pace_check` condition bound to the launch curve) +
**governance**: every run drafts artifacts and a human approves before anything ships.
v1 archetype is a Social-Media-Manager with six skills (audit, trend brief, strategy,
carousel design, reel script, attribution tagging).

**Enable it** (off by default):

```bash
AI_EMPLOYEES_ENABLED=true            # gates the routers, the two worker jobs, and the rail item
ANTHROPIC_API_KEY=sk-...             # runs execute server-side; the key never reaches the browser
AI_EMPLOYEES_MODEL=                  # optional; falls back to ASSISTANT_MODEL
AI_EMPLOYEES_TOKEN_BUDGET=2000000    # per-tenant monthly (in+out); 0 = unlimited. Over budget → runs land skipped_budget
AI_EMPLOYEES_WRITEBACK_ENABLED=false # v1 ships approve+export; GHL writeback stays behind this gate + the per-employee toggle
```

**How it runs.** The worker registers two jobs (gated on the flag): `ai_dispatch`
(every minute — cron-due skills + the pace check → queue `AIRun`s) and `ai_execute`
(every 15s — oldest queued run → context pack → Anthropic call validated against the
skill's `output_contract`, one retry → **draft** artifacts, `awaiting_approval`). Nothing
auto-commits. Approving marks artifacts `approved`; they reach `shipped` only when **both**
writeback gates are open. Owner/admin manage and approve; a member with the `ai_employees`
tab grant gets read-only (403 on mutations).

**Demo data** (never auto-loaded): `python -m app.demo_fixture_ai [tenant_slug]` creates a
demo "Summer" employee with one historical shipped run for design/QA. It is the only place
tenant-specific names live; real tenants create their own employees in Settings › AI Employees.

## Deployment

Railway: three services (`api`, `worker`, `web`) + a Postgres plugin, one
project sharing `DATABASE_URL` + secrets. Set `DATABASE_URL` to the
`postgresql+asyncpg://…` URL, run `alembic upgrade head` as the `api`
pre-deploy/release command, and add the customer host to `web` and the
API host to `api`, then register each with `scripts/tenant_domains.py`. Full steps in
`spring-command-center-SPEC.md` §12.

## Tests

```bash
cd backend && pip install -e ".[dev]" && pytest
```
