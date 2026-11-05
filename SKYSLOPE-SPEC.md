# SkySlope integration: audit and build record

**Status, 2026-11-04: Phases 1 to 3 are built against a fake shaped from the OpenAPI file. Nothing has
met the real API. Phases 4 to 6 are not started.** Connor reports that SkySlope confirmed the use and
accepted every default in §8. What that confirmation covers in writing is still open: see §10.

The handed-over spec is [docs/skyslope-integration-spec.md](docs/skyslope-integration-spec.md),
kept verbatim. It still governs everything this file does not change. Everything here was read
from the repo, from production (read-only), from SkySlope's live OpenAPI file (version
`2.2.1-preview.0.27`, 166 paths) or from the published API License Terms (last updated January
2025). The licence reading is an engineer's, not a lawyer's, and the Order Form can change any
of it.

## 10. What Phases 1 to 3 built, and what they changed

**The gate, in three parts.** Without the application credentials (`SKYSLOPE_CLIENT_ID`,
`SKYSLOPE_CLIENT_SECRET`) the source is hidden and connecting is refused, so deploying this code changes
nothing for any customer. With them, an admin can connect and sync. `SKYSLOPE_SCHEDULED_SYNC` stays false
until SkySlope approves polling in writing. Until then the only things that reach SkySlope are that
admin's Connect, their Sync on a key, and their Sync all. The tick, the operator console and the CLI do
not. `services/sync_policy.py` is the one answer, and `run_all`'s default is the safe one.

**Where it differs from the plan above and the spec:**

| Planned | Built | Why |
| --- | --- | --- |
| `type=listing,sale` | `type=summary,listing,sale` | `summary` is one of that parameter's values. Asking only for the file kinds risks losing the counts the short-read check needs. |
| Per-office calls to learn a user's offices | One `GET /api/users` | `UserDTO2` already carries `officeGuids`. It also carries a home address and two phones, which the allow-list drops. |
| Optional per-connection Client ID (§1) | Environment only | Route A was accepted. The credential blob is JSON and the rate limit is already keyed per Client ID, so route B is a small change if it is ever needed, not the retrofit §1 feared. |
| Strip the "never store" fields | Build each row from an allow-list | Stripping fails open: a field SkySlope adds next year would be written without anybody deciding to. A test plants a marker in every banned field and searches every stored row. |
| Normalized `status` in Phase 3 | Column exists, NULL until Phase 4 | Scope. `status_raw` and `raw` are stored, so Phase 4 needs no re-pull. |
| `skyslope_file_agent` in Phase 4 | Written from Phase 3 | The sync already holds those fields, and writing them now means Phase 4 needs no re-pull. |
| Mark removed when "no key" returns a file | Cutoff is the OLDEST full pull among connected keys | One key can never say a file is gone. A key that has never finished a full pull holds it back entirely, and a full pull that returned nothing flags nothing. |

**Decided while building, none of them in the spec:**

- `/sync/all` includes SkySlope, because that button says it refreshes every source and an admin pressed
  it. Everything else that reaches `run_all` leaves it out, and a test fails if anything else sets
  `requested=True`.
- An operator cannot start a SkySlope pull while it is manual. §3(n) is about a user of the licensee being
  present, and an operator is not one.
- A key that may not list users (403) still syncs its files, and the run records `users: "denied"`.
  Connor's agent-level key may be exactly this case.
- A stream that drops is rerun once at once, because a manual source may not be asked again for days.
- `SkySlopeError` text is built from the status, `code` and path only. It reaches the Settings page, and a
  response body could echo a credential or a client's name.
- A cached session is keyed on the secret as well as the key. Keyed on the key alone, a stored row with
  the wrong secret rode on another row's session, even another workspace's. A test found this.

**How it was checked.** The full backend suite passes run alone on this code: 2943 tests, 130 of them
SkySlope's. Each of 43 mutations to the controls was made in turn and a test caught every one; the three
that first survived each got a new test. A production build in the browser, against a local fake and a
scratch database: a wrong secret is refused, the right key connects and backfills 12 files, a marker planted
in every banned field is in no stored row, a second Sync and a second key both change nothing, an edit with
blank fields keeps the key and state and pulls nothing, and a disconnect clears the secret and hides the row.
The migration goes up, matches the models, re-runs cleanly, comes down and goes up again.

**What cannot be proven yet.** There are no credentials, so the following have only met the fake:
Phase 2's CLI run, Phase 3's live done-when, and the list at the top of `app/integrations/skyslope.py`.
`scripts/skyslope_probe.py` asks those questions and prints shapes and counts only. The one most likely to
need a change is whether the summary's counts respect the filters. If they do not, every incremental
fails with "came up short": visible on the first run, and fixed by checking counts on full pulls only.
The migration has been run on SQLite only, because there is no local Postgres and prod is not for this.

**Open with SkySlope, whatever "confirmed" meant:** whether the Order Form names Axcion for many customers
(blocker 1); written OK for polling, and whether it covers Sync now (blocker 4); permission to name them on
the Settings card (blocker 6). The card shows the name with a monogram and no logo, and only once the
application credentials are set.

**Phase 5 has to extend the purge** if the sync starts writing `transaction` and `agent` (decision 2): those
rows are SkySlope-sourced too. `services/skyslope_purge.py` says so, and derives only the `skyslope_*` tables.

## 1. What "for the entire product" changes

The spec is written for one workspace. Connor's brief is that any workspace can connect it.

| The spec says | Product-wide |
| --- | --- |
| `services/activated_agent.py`, "AA panels" | A generic service and surface, shown on any `real_estate` business with `skyslope` connected. No workspace is named in code. |
| App Client ID and Secret in env vars | Env var as the default, with an optional per-connection override inside the encrypted blob. Which one is used depends on the licence route (§2). Cheap to keep open now, expensive to retrofit. |
| Status map in tenant config, Connor decides `Pre-contract` and `Incomplete` | His answers become the product default. A tenant can override them. |
| Roster starts with the "ID - Activated Agents" office | Office GUIDs are per-workspace config with no default. Empty means everyone the key can see. |
| 100 requests a minute per Client ID, "shared by every key" | Also shared by every *workspace*, so one customer's weekly full pull slows the rest. Pacing must read `x-ratelimit-*` rather than count locally: manual syncs run in the API process, and scheduled ones can run in a separate worker. |
| Create the Activated Agent tenant | Already exists. See §2. |

## 2. Blockers

The spec lists three. The audit adds three, and the first of them comes before the Order Form.

| # | Blocker | Why | Who |
| --- | --- | --- | --- |
| 1 | **New: the licence route for a multi-customer product** | See below. | Connor, with SkySlope, before signing |
| 2 | Signed Order Form naming Axcion | From the spec. Every call, including the test account. | Connor, with SkySlope |
| 3 | A key that sees the whole team | From the spec. | Connor, with eXp |
| 4 | Written OK for polling | From the spec, and wider than it says. §3(n) is about the licensee or user not being logged into the API Materials or the SkySlope Services. Being logged into *Axcion* is not obviously that, so the OK should cover "Sync now" as well as the scheduler. | Connor, with SkySlope |
| 5 | **New: Sisu is not connected on Activated Agent** | Production has `activatedagent` (business `main`, kind `real_estate`, Portfolio plan, one owner, created 2026-09-22) with **no integrations and 0 agent rows**. Sisu is connected only on `springb` and `utah-life`. Matching has nothing to match against until AA has its own Sisu connection. | Connor |
| 6 | **New: permission to use the name** | The terms give no general right to SkySlope's name or logo. The spec's neutral chips cover the panels, but the Settings connect card has to say what you are connecting. Ask for written permission to name it there. Until then, monogram only, never a logo (the same rule Go High Level is under). The marketing site needs the same answer. | Connor, with SkySlope |

**Why blocker 1 matters.** Four clauses in the standard terms read against a multi-tenant product:

- §1: the licence covers only the application named on the Order Form, with no right to sublicense,
  and limits use to the licensee and its employees, agents and contractors. A customer's staff are
  none of those.
- §3(g): bars service-bureau, rental or sharing arrangements for the benefit of a third party or
  customer.
- §3(h): bars bulk downloads or extractions of Customer Data outside what the licence authorizes.
  Bulk Export is the core of this design.
- §5: bars collecting, storing or disclosing that data except as the licence permits.

Activated Agent alone might sit inside a Brokerage Customer licence. Offering this to every Axcion
workspace very likely does not, unless the Order Form says so. The spec's open question,
"Brokerage Customer or Integration Partner?", therefore has a practical answer for this brief. Ask
for one of these:

- **A. Integration Partner:** one Order Form lets Axcion serve many customers. One app Client ID,
  and all workspaces share its rate limit.
- **B. Customer-by-customer:** each brokerage holds its own Order Form and credentials, and Axcion
  is the licensed application on each. Per-connection Client IDs, separate limits, more onboarding
  friction. §5 does allow disclosure that the data's owner permits, which helps this route.

Counsel should read the Order Form before signing.

## 3. Repo audit: what this work mirrors

| Need | Mirror | Note |
| --- | --- | --- |
| Read-only client | `integrations/meta_ads.py`, with its guard `test_ads_client.py::test_the_client_never_writes` | **Not `ghl.py`.** The spec calls it read-only, but its own comments say the Recruiting write path lives there. |
| Rate limiting | The shared bucket in `integrations/ghl.py` | Paces from response headers. Worth copying even though the client itself isn't the model. |
| Credentials | `Integration.access_token_enc` holding encrypted JSON, as Sisu and Arive do (`routers/integrations.py`) | Replaces `skyslope_keys`. |
| Many keys per workspace | `/integrations/qbo/entities` | The generic connect route assumes one row per tenant, provider and business. |
| Sync state | `Integration.config`, protected the way `fub_state` is by `_merge_config` | Replaces `skyslope_sync_state`. Copy the JSON before writing, or SQLAlchemy skips the UPDATE silently. |
| Run log | `SyncRun` and `_sync_integration`, with `stats` for watermark, lag and rows changed | Replaces `skyslope_sync_runs`. Gets the orphan sweep and the operator console for free. |
| Scheduler | `worker.py` (APScheduler), its own job like `fub_followups_tick` | Not `tick`. See §5. |
| Tenant creation | `services/provisioning.py`, `scripts/create_tenant.py` | Nothing to create for AA. |
| Sisu agents | `Agent` (`source="sisu"`, `email`) | |
| Identity | `services/member_identity.py`: email on read, an override only where a person chose one | Settled design: "matched on email, not stored as a foreign key". So `agent_identity_links` holds **overrides only**, not one row per match. `IntranetMember.agent_links` is the override precedent, but it needs a portal member, which AA may not have. |
| Status map | `cfg["recruiting_stage_groups"]` | |
| Metrics service | `services/forum.py` | |
| Validation gate | `backend/app/validate_ulrg_resolvers.py` | Under `app/`, and ULRG-specific: mirror it, don't reuse it. |
| Migrations | Alembic. Head is `0098_sympli_scorecard`, on prod too | Next is `0099`. Table names are singular here: `skyslope_listing`, not `skyslope_listings`. |
| Dependencies | `backend/pyproject.toml`, Dockerfile on Python 3.12 | `ijson` is not installed. |

**Tables after the audit:** `skyslope_user`, `skyslope_listing`, `skyslope_sale`,
`skyslope_file_agent`, plus a small override table. That is five rather than the spec's eight,
because three of them already exist in another form. Purge has to cover the state held in
`Integration.config` and the `SyncRun` rows too.

## 4. Registering a provider

Derived from the files that name both `meta_ads` and `arive`, not from memory:

- `backend/app/routers/integrations.py`: allow-list and connect branch
- `backend/app/services/integrations_view.py`: `ORDER`, `META`, `FAMILY`, `CONNECTABLE_KIND` (`roles.REAL_ESTATE`)
- `backend/app/services/sync.py`: `_sync_integration`
- `backend/app/services/fleet_health.py`: label and `stale_after`
- `frontend/src/Settings.jsx`: `MONO`, description, form, offline sample

Plus one that derivation misses, because it was already wrong:

- `backend/app/plans.py`: `allows_source` is checked before anything else on connect. **`meta_ads`
  is on no plan today, so Settings refuses it with a 402 on every tier.** That is a live bug outside
  this work, not fixed here. This work adds a guard: every provider on the connect allow-list must be allowed by at least one
  plan.

`tests/test_provider_ui_is_complete.py` already ties the API and UI halves together.

## 5. Where the polling gate leaks

The spec puts a `SKYSLOPE_SCHEDULED_SYNC` flag on "the scheduler". That alone would not work:

- **`worker.tick` calls `run_all`, which syncs every integration with status `connected` or
  `error`.** Adding `skyslope` to `_sync_integration` polls it every 30 minutes whatever the flag
  says, on the first tick after a key is connected. The gate has to sit on the path `run_all` takes,
  with a test that a scheduled run skips it and a manual one does not.
- **`/sync/all` and the operator console's sync (`platform.py`) also call `run_all`.** The operator
  path should stay off until SkySlope's written OK says otherwise.
- **With the gate closed, the product raises false alarms.** `integrations_view.py` would say
  "Auto-sync has missed its last N runs", and `fleet_health.stale_after` would move the workspace to
  `watch`. Both need to know this source is manual.

## 6. The spec checked against its sources

**Confirmed:** OpenAPI version, `/auth/login` absent from `paths`, the placeholder security scheme,
`/api/files` with its filters and status values, `/api/users?includeDeactivated`, §3(n), the benchmark
bar in §3(p), and the purge duty. Every mapped field exists on `BulkExportSale` and `BulkExportListing`,
apart from the one below.

**Corrections:**

| Spec | Found | Consequence |
| --- | --- | --- |
| `GET /api/healthcheck`, used in Phase 2's done-when | Not in the OpenAPI file; no path contains "health" | Drop it. `replica-timestamp` is the first call of every run and proves the same thing, and §3(p) makes uptime checks awkward anyway. |
| Accept `contractAcceptanceDate` | Not on `BulkExportSale` | Keep the tolerance, since detail DTOs may differ. Check on the first live call. |
| `agent` listed as an untyped object | Typed as an array | Attribute from `agentGuid`, which the spec already does. |
| "If a stream drops, rerun" | The first stream item is a summary with `numberOfSales` and `numberOfListings`, and items sit under `value` | **Better than the spec:** compare counts to rows received, and never advance the watermark on a short read. Parse `value.item`. Confirm on the first live call that the counts respect the filters. |
| No date filter returns the last 7 days | Not in the OpenAPI file | Harmless. Every call passes a filter. |
| "The CI guard on hardcoded business data" | No `.github/`, so no CI. The guards run in the backend test suite. | Fixtures stay in tests. Nothing runs them automatically. |
| "Playwright renders them" | Not in `frontend/package.json` | Phase 6 uses a production build and the browser pane, then the spec's delete-seed-and-insert check. |
| "Shell Test" | Not a term in this repo | Phase 6's own text defines it well enough. |

## 7. Where the data shows up (decide by Phase 5)

Phases 1 to 4 are the same either way, so this does not hold up the start.

Every `real_estate` business already gets production tiles from `Transaction` (Units Closed, Volume,
GCI, Avg Sale Price, Pending Pipeline, Active Listings, Agents Producing), and AA's `main` is one. `Transaction` already has `source`, `side`, `status`,
`sale_price`, `gci`, `agent_id`, `contract_date`, `close_date`, `expected_close_date` and
`listing_date`. Seven services read it, and `metrics.py` does not filter on `source`.

- **A. Panels only, as the spec has it, made generic.** Cheapest. But a SkySlope-only workspace shows
  `Closed: 0` in the existing tiles right beside a panel saying 14.
- **B. Recommended: also feed `Transaction`, one writer per business.** A business names its production
  source. The SkySlope sync projects its normalized rows into `Transaction`, Sisu stops writing
  transactions on that business, and tiles, drill-downs, scorecard resolvers and portal numbers all work
  without changing a reader. A small generic panel covers what `Transaction` can't say: coverage, past
  due, not producing. This is what makes it a product integration rather than one customer's page.
  Three things to get right: `gci` stays null until defined and has to show a chip rather than `$0`;
  SkySlope users need `Agent` rows (`source="skyslope"`) for `agent_id`, and `metrics.py`'s agent total
  has no source filter; and no live workspace changes, because the default stays Sisu.

## 8. Decisions

| # | Decision | Recommended default | Needed by |
| --- | --- | --- | --- |
| 1 | Licence route (§2) | A, Integration Partner | Before signing |
| 2 | Where the data shows up (§7) | B | Phase 5 |
| 3 | Plan tier for `skyslope` | Same as Sisu and FUB: every plan. Revisit if the licence carries a per-customer cost. | Phase 3 |
| 4 | `Pre-contract` counts as under contract | No. Show it separately. | Phase 4 |
| 5 | `Incomplete` listings count as active | No. An unfinished file isn't on the market. | Phase 4 |
| 6 | Unit credit | Full unit to the primary agent, co-agents shown separately. Matches `Transaction`, which has one `agent_id`. Confirm against eXp data. | Phase 5 |
| 7 | "Not producing" window | 60 days, per-workspace config | Phase 5 |
| 8 | Street addresses | City, state and ZIP only. Less to purge, and the metrics don't need them. | Phase 1 |
| 9 | Which commission field is GCI | Open. Needs 3 known deals and live data. Shows a chip until then. | Phase 5 |

## 9. Build order, as amended

Phases 1 to 3 can be built now against fixtures shaped from the OpenAPI schemas. Live proof waits on
credentials.

- **Phase 1:** no tenant to create. Migration `0099`, the tables in §3, and the purge command.
- **Phase 2:** the client, with no healthcheck, a count check against the summary item, and the
  no-write guard.
- **Phase 3:** sync, plus provider registration (§4) and the gate on the `run_all` path (§5).
- **Phases 4 to 6:** as the spec has them, generic per §1, surface per decision 2.

Production cutover waits on blockers 1 to 4. Blocker 5 holds up matching only, and blocker 6 holds up
the name on the card.
