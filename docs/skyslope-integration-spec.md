# SkySlope → Axcion Integration Spec (Activated Agent)

Nov 4, 2026 · @Connor

## Summary

Claude Code will build a read-only SkySlope connector into a new Activated Agent workspace in Axcion. Spring then sees each agent's listings, contracts and closings beside their Sisu activity. Phases 0–2 can start now; live data waits on a signed SkySlope Order Form and a key that sees the whole team.

**What gets built**

- A read-only client, `integrations/skyslope.py`, that signs in with HMAC and streams SkySlope's Bulk Export.
- Postgres tables for SkySlope users, listings, sales, agent links and sync runs, all purgeable on demand.
- A sync engine: one backfill per key, incremental pulls on `modifiedAfter`, and daily and weekly reconciliation.
- Status normalization, agent attribution by `agentGuid`, and email matching to the Sisu agents Axcion already knows.
- A metrics service and AA panels that show provenance on every number.

**Done when**

- The AA workspace shows each agent's active listings, under-contract units and closed units from live SkySlope data, and passes Axcion's Shell Test.
- Each SkySlope metric matches a hand count for one parallel week before it replaces a manual scorecard value.
- Anything blocked, such as GCI or partial key coverage, shows a chip instead of a number.

## Context and goals

Activated Agent restarts ads only once Spring can see what agents do after they get leads. SkySlope is where eXp agents record deals and get paid, so it is the source of truth for production.

- **Workspace:** a new Axcion tenant for Activated Agent, separate from Utah Life (`ulrg`) and the other tenants.
- **Sources:** SkySlope for listings and sales (new connector); Sisu for agent activity (already integrated in Axcion).
- **Per agent, the workspace answers:** How many active listings? How many deals are under contract and closed, at what volume? Who has gone quiet? What GCI did they generate, once GCI is defined?
- **Out of scope:** writing to SkySlope, documents (a paid feature that returns 402), buyer and seller contacts, the Forms and Offers APIs, and GraphQL.

## Prerequisites and blockers

Three items block production data: a signed SkySlope Order Form, a key that sees every AA agent's files, and written approval for scheduled polling. Phases 0–2 can proceed without them, though the SkySlope test account also arrives only after signing.

| Needed | Who | Blocks | Why |
| --- | --- | --- | --- |
| Signed Order Form naming Axcion.io as the licensed application | Connor, with SkySlope sales | Every API call, including the test account | SkySlope's CSM issues the Client ID and Secret only after signing. The license covers only the app named on the form. |
| A key that sees all AA agents' files | Connor, with eXp | Complete production data | Connor's login is agent-level and sees 2 listings, shared to him as co-agent in the "ID - Activated Agents" office. Ask eXp for an office-level Broker, Admin or Auditor user; the fallback is a key from every agent. |
| Written OK for scheduled polling | Connor, with SkySlope | Turning on the scheduler | The API terms (§3(n)) bar processes that run when no user is logged in. |
| Customer type, tier and price | Connor, with SkySlope sales | Signing | Unclear whether AA is a Brokerage Customer or an Integration Partner, and whether each agent key counts as an account (5+ is Premium). |
| GCI definition | Connor and partners | The GCI metric only | No SkySlope field is named GCI. Test candidate fields on 3 known deals, and confirm eXp agents fill them at all. |
| The email to match Sisu on | Connor | Auto-matching only | SkySlope profiles hold an eXp SSO email and a "Non-Broker Email"; which one the API returns is undocumented. |
| The access key created on Connor's login on 2026-11-04 | Connor | Nothing; security hygiene | The audit found one existing key. If Connor didn't create it, delete it under My Account → Integrations. |

## SkySlope API essentials

Use the live OpenAPI file as the source of truth for paths and fields, but write auth by hand: the file's security scheme is a placeholder.

**Environments and docs**

- Production `https://api.skyslope.com`; staging `https://api-staging.skyslope.com`; integration `https://api-integ.skyslope.com`. Which one hosts the test account is unknown.
- OpenAPI: [swagger.json](https://api.skyslope.com/swagger/v1/swagger.json), version `2.2.1-preview.0.27`, readable in the [ReDoc viewer](https://api.skyslope.com/api/docs/redoc/index.html?url=/swagger/v1/swagger.json). The old docs URL now returns 404.
- Ignore the spec's `securitySchemes`; it points at a placeholder OpenID URL. Don't let codegen build auth from it.

**Authentication**

1. Make a timestamp in RFC3339 UTC, within 5 minutes of real time.
2. Sign `ClientID:ClientSecret:Timestamp` with HMAC-SHA256, keyed with the user's Access Secret, then Base64 the digest.
3. `POST /auth/login` with `Authorization: SS <AccessKey>:<signature>`, `Timestamp: <same string>`, and body `{"clientID": "...", "clientSecret": "..."}`.
4. Read `Session` and `Expiration`. Sessions last 2 hours and can't be refreshed, so log in again before expiry.
5. Send `Session: <token>` on every data call. Add `Timestamp` only if calls fail without it.

```python
import base64, hashlib, hmac
from datetime import datetime, timezone

def login_headers(client_id, client_secret, access_key, access_secret):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    msg = f"{client_id}:{client_secret}:{ts}".encode()
    sig = base64.b64encode(
        hmac.new(access_secret.encode(), msg, hashlib.sha256).digest()
    ).decode()
    return {"Authorization": f"SS {access_key}:{sig}", "Timestamp": ts}
```

Verify on the first live call: body key casing (`clientID` vs `ClientId`), prefix case (`SS` vs `ss`), and what an expired session returns. Treat any 401 as "log in again once, then retry."

**Endpoints this build uses**

| Call | Use | Notes |
| --- | --- | --- |
| `POST /auth/login` | One session per key | Not in the OpenAPI `paths` |
| `GET /api/files/replica-timestamp` | Sync watermark | Returns `replicaLastCommitTimeUtc`, `utcNow`, `lagSeconds` |
| `GET /api/files` (Bulk Export) | Every listing and sale the key can see | Streamed, no paging. With no date filter it returns only files created in the last 7 days. Filters: `createdAfter`, `modifiedAfter`, `type`, `status`, `stage` |
| `GET /api/users?includeDeactivated=true` | Roster and emails | No change filter; deactivated emails carry `_deleted` or `_deactivated` |
| `GET /api/offices` | Office names and GUIDs | Permission-filtered; list keys differ from detail keys |
| `GET /api/stages` | Stage names | Brokerage-configured |
| `GET /api/files/sales/{saleGuid}/tieredCommissions` | GCI candidate | Phase 5, only if GCI maps here |
| `GET /api/healthcheck` | Liveness check before a run | Auth requirement unknown |

Not used: document endpoints (paid, 402), contacts, form metadata, every write endpoint, and GraphQL (schema undocumented). Per-file detail GETs are for one-off shape checks only, never the sync loop.

**Limits and errors**

- 100 requests per minute per Client ID, shared by every key. Read `x-ratelimit-remaining` and wait for `x-ratelimit-reset`; the over-limit status code is undocumented.
- Errors return `{"errors": [...], "code": ...}`. Switch on `code`, never parse `errors[]`; validation failures are 422.
- The paged list endpoints return 422 past the last page, which means end of results.

Source: SkySlope API audit (2026-11-04), built from the OpenAPI file and SkySlope's help center.

## Data model and field mapping

Store listings and sales keyed by SkySlope GUIDs, keep agent links in their own table, and drop every contact object before anything is written. Match table and column names to the repo conventions found in Phase 0.

**Tables**

| Table | Key | Holds |
| --- | --- | --- |
| `skyslope_keys` | `id` | One row per Access Key per tenant: encrypted key and secret, owning `user_guid`, scope note, last login, status |
| `skyslope_users` | `tenant_id`, `user_guid` | Name, raw email, normalized email, `user_type`, `office_guids`, deactivated flag, last seen |
| `skyslope_listings` | `tenant_id`, `listing_guid` | Listing columns below, normalized status, trimmed raw JSON |
| `skyslope_sales` | `tenant_id`, `sale_guid` | Sale columns below, normalized status, trimmed raw JSON |
| `skyslope_file_agents` | `tenant_id`, `file_guid`, `user_guid`, `role` | Who is on each file: `primary`, `co_agent`, `co_listing` or `split` (with split %) |
| `skyslope_sync_state` | `tenant_id`, `key_id` | Watermark, last daily and weekly reconcile |
| `skyslope_sync_runs` | `id` | Kind, start and end, watermarks, replica lag, rows changed, errors |
| `agent_identity_links` | `tenant_id`, `skyslope_user_guid` | Link to the Sisu agent; see Agent matching |

The app-level Client ID and Secret live in environment variables, not a table. Every file row also keeps `first_seen_at`, `last_seen_at` and `removed_from_source_at`.

**Field mapping (Bulk Export → Axcion)**

| Axcion column | Sale field | Listing field | Handling |
| --- | --- | --- | --- |
| `guid` | `saleGuid` | `listingGuid` | Primary key |
| `agent_guid` | `agentGuid` | `agentGuid` | Primary agent; joins to `skyslope_users.user_guid` |
| file agents | `coAgentGuids[]`, `commissionSplits[].agentGuid` | `coAgentGuids[]`, `coListingAgentGuid` | Written to `skyslope_file_agents` |
| `status_raw`, `status_id` | `status`, `statusId` | `status` | Keep raw and normalize below; `statusId` alone doesn't set the display status |
| `stage_name` | `stage.name` | `stage.name` |  |
| `office_guid` | `officeGuid` | `officeGuid` |  |
| `deal_side` | `dealType` | — | Raw string; read values differ from documented write values |
| converted from | `listingGuid` | `status` = `Transaction` | Links a listing to its sale |
| `listed_at`, `expires_at` | — | `listingDate`, `expirationDate` |  |
| `contract_accepted_at` | `contractAcceptedDate` or `contractAcceptanceDate` | — | Accept both keys; `0001-01-01T00:00:00` becomes null |
| `expected_close_at` | `escrowClosingDate` | — |  |
| `closed_at` | `actualClosingDate` | — |  |
| `canceled_at` | `deadDate` | `deadDate` |  |
| `created_at`, `modified_at` | `createdOn`, `modifiedOn` | `createdOn`, `modifiedOn` | `modifiedOn` exists only in Bulk Export |
| prices | `salePrice`, `listingPrice` | `listingPrice` | Decimal |
| `commission_raw` | `commission`, `commissionSplits`, `commissionBreakdowns` | `commission` | JSON until GCI is defined |
| location | `property.city`, `.state`, `.zip` | same | Street address is an open decision |
| `mls_number` | `mlsNumber` | `mlsNumber` |  |

Never store: `sellers`, `buyers`, every `*Contact` object, `commercialLease`, `earnestMoneyDeposit`, `checklist`, `transactionCoordinators`, `commissionReferral.contact`, `customFields`, or the file-level `email` (likely a SkySlope inbox address, not the agent's).

**Status normalization**

| Normalized | Listing `status` | Sale `status` |
| --- | --- | --- |
| `active` | `Active`; `Incomplete` if Connor decides so | — |
| `under_contract` | — | `Under Contract`, `Pending`; `Pre-contract` if Connor decides so |
| `past_due` | — | `Expired` (closing date passed, not closed) |
| `closed` | — | `Closed`, `Archived`, and `Sold` if it appears |
| `canceled` | `Canceled/Pend`, `Canceled/App`, `Archived` (ended without a sale) | `Canceled` and any canceled-pending or approved label |
| `expired` | `Expired` | — |
| `converted` | `Transaction` (exclude; count the sale) | — |
| `unmapped` | Anything else, including `Unknown` | Anything else, including `Unknown` |

Status values are free strings, and SkySlope doesn't publish the full sale list. Keep this map in tenant config with an override, like Forum's `cfg["recruiting_stage_groups"]`, and log every unmapped value. Response timestamps carry no offset; store them as UTC and flag that assumption until SkySlope confirms.

## Sync design

Each key runs one backfill, then incremental pulls on `modifiedAfter`, with daily and weekly reconciliation to catch what `modifiedOn` misses.

&#91;embedded content: SkySlope sync pipeline · 2 triggers, 8 steps\]

Pulls from every key land in one store, and each file is credited from its own agent fields, never from the key that returned it.

1. **Backfill, once per key:** read `replica-timestamp` first, then `GET /api/files?createdAfter=2000-01-01T00:00:00Z&type=listing,sale`. Stream-parse it, since items arrive one at a time. Save the reading's `replicaLastCommitTimeUtc` as the watermark.
2. **Incremental, every 30 minutes (configurable):** read `replica-timestamp`, then pull with `modifiedAfter` set to the watermark minus 10 minutes. Upsert by GUID, then advance the watermark to the reading taken before the call.
3. **Daily reconcile:** re-pull open files (`status=active,pending,incomplete,expired,canceledPending` with an early `createdAfter`). Date-driven flips such as Active to Expired may not touch `modifiedOn`.
4. **Weekly full pull:** re-pull everything per key. Mark files no longer returned by any key with `removed_from_source_at`, and never hard-delete; the API has no deletions feed.
5. **Users, every cycle:** `GET /api/users?includeDeactivated=true`, then normalize emails.
6. **Many keys, one file:** co-agent sharing returns the same file to several keys. A file dropping out of one key's results may mean a share was removed, not that the file is gone.
7. **Budget:** Bulk Export costs one request per key per cycle, plus a login every 2 hours. Fifty keys cost about 50 requests a cycle, which the shared limiter can pace inside 100 a minute; per-file detail calls would not fit.
8. **Failures:** on a 401, log in again once and retry. When the limit runs out, wait until `x-ratelimit-reset`. If a stream drops, rerun the same call with the same watermark; upserts make that safe.
9. **Scheduler gate:** ship the scheduler behind `SKYSLOPE_SCHEDULED_SYNC=false` until SkySlope approves polling in writing. Until then, a logged-in admin can trigger "Sync now".

## Agent matching with Sisu

Link each SkySlope `userGuid` to a Sisu agent Axcion already knows, by normalized email, with a manual override. Never guess a match.

- **Normalize:** lowercase, trim, and strip a trailing `_deleted` or `_deactivated` from SkySlope emails.
- **Match:** against every email Axcion stores for Sisu agents; find where the Sisu integration keeps them in Phase 0.
- **Store:** one row per link in `agent_identity_links`, with `sisu_agent_id`, `method` (`email` or `manual`), `confirmed_by` and `confirmed_at`.
- **No match, or two matches:** write no link. List the agent in a "Needs match" admin view with a chip, and let Connor resolve it.
- **Roster:** AA agents are the users in the tenant's configured office GUIDs, starting with "ID - Activated Agents", plus anyone attributed on a synced file. Whether AA has offices in other states is unconfirmed.
- **Check first:** SkySlope profiles hold an eXp SSO email and a "Non-Broker Email". Compare the first live `/api/users` pull against Sisu before trusting auto-matching.

## Metrics

Phase 5 computes six SkySlope metrics per agent and for the team; GCI waits on a definition.

| Metric | Definition | SkySlope fields | Ready? |
| --- | --- | --- | --- |
| Active listings | Listings normalized `active`: count and list volume | `status`, `listingPrice` | Yes |
| Under contract | Sales normalized `under_contract`, with `past_due` flagged separately: count and volume | `status`, `salePrice`, `escrowClosingDate` | Needs the Pre-contract decision |
| New contracts | Sales whose contract was accepted in the period | `contractAcceptedDate` | Yes |
| Closed | Sales normalized `closed` with `actualClosingDate` in the period: units and volume | `actualClosingDate`, `salePrice` | Yes |
| Canceled | Files with `deadDate` in the period | `deadDate` | Yes |
| Not producing | Roster agents with no new listing or contract in N days | `listingDate`, `contractAcceptedDate` | Needs N |
| GCI | Not yet defined | Commission fields | Blocked on the GCI definition |

- **Credit:** whether a unit counts fully for the primary agent or splits by `commissionSplits` percentages is Connor's call. Co-agent files show separately either way.
- **Coverage:** every team total states how many roster agents a connected key actually covers ("Partial: N of M agents"), so a thin key never reads as a quiet team.
- **Validation:** a SkySlope metric replaces a manual scorecard value only after it matches a hand count for one parallel week, the gate `validate_ulrg_resolvers.py` already enforces for Utah Life.
- **Provenance:** each metric carries a chip: live, needs sync, needs key scope, or needs definition.

## Security and data handling

Everything from SkySlope is confidential under the API terms and must be purgeable on request. Keep the integration read-only and store only what the metrics need.

- **Secrets:** the Client ID and Secret are app-level Railway environment variables. Access Keys and Secrets are encrypted at rest per tenant, using the pattern Axcion already uses for integration credentials. None of them go in the repo, logs, prompts or the frontend; local work uses test-account values from an uncommitted `.env`.
- **Sessions:** hold tokens in memory with their expiry, and never log them.
- **Read-only guard:** the client's only POST is `/auth/login`. Add a test that fails if a write method appears, as with the read-only GHL client.
- **Minimize:** strip the fields under "Never store" before anything is persisted, and skip document endpoints entirely.
- **Purge:** build a command that deletes every SkySlope-sourced row for one tenant or all tenants and records when it ran. The terms require returning or destroying SkySlope data on termination or request, certified in writing by an officer.
- **Naming:** the terms bar using SkySlope's name or logo without written permission. Until SkySlope confirms an internal data-source label is fine, use neutral provenance chips such as "Transactions: live".
- **Monitoring:** a healthcheck before each run is fine, but don't build uptime or performance tracking of SkySlope's API; the terms bar benchmarking it.
- **Legal:** this section summarizes the [API License Terms of Use](https://skyslope.com/api-license-terms-of-use/) for engineering only. Counsel should read the Order Form and terms before signing.

## Build plan for Claude Code

Build in seven gated phases (0–6). Each PR shows real command output for its done-when check, and no UI ships before real rows exist.

1. **Phase 0: Audit and questions (no code).**
   - Read the repo: how tenants are created, the Sisu integration and where it stores agents, `integrations/ghl.py`, the integration rows in `sync.py`, credential storage, any scheduler, and the migration tool.
   - Run a structured question session with Connor on the decisions under Open questions, and record the answers in the PR.
   - Done when: a written repo audit names the files this work will mirror, and every decision has an answer.
2. **Phase 1: Tenant and schema.**
   - Create the Activated Agent tenant if it doesn't exist, following the existing tenant pattern.
   - Add migrations for the tables under Data model, plus the purge command.
   - Done when: migrations apply cleanly, a recognizable row inserted with psql reads back, and the purge command removes it.
3. **Phase 2: Read-only client (`integrations/skyslope.py`).**
   - HMAC login, a session cache that renews before the 2-hour expiry, a rate limiter shared per Client ID, a streaming Bulk Export parser (for example `ijson`), and calls for replica-timestamp, users, offices and stages.
   - Tests: the signature built from fixed inputs, the parser run on a fixture shaped from the OpenAPI schemas, and the no-write guard. Fixtures live in tests only, so the CI guard on hardcoded business data still covers app code.
   - Done when: tests pass; once test credentials exist, a CLI run of login, healthcheck and replica-timestamp shows real responses in the PR.
4. **Phase 3: Sync engine.**
   - Backfill, incremental, daily reconcile, weekly full export and users refresh, each logged in `skyslope_sync_runs`. The scheduler sits behind its flag; logged-in admins get "Sync now".
   - Done when: on the test account, a backfill fills the tables, an immediate second run changes 0 rows, and an edit made in SkySlope (by hand, never through Axcion) appears on the next incremental, all shown with psql output.
5. **Phase 4: Normalize and attribute.**
   - The status map in tenant config, date normalization, `skyslope_file_agents`, listing-to-sale links, Sisu email matching and the "Needs match" view.
   - Done when: every synced file has a normalized status or `unmapped`, and every roster agent is matched or listed as unmatched.
6. **Phase 5: Metrics service and API.**
   - `services/activated_agent.py`, following `services/forum.py`, plus an endpoint whose payload is the API contract, with provenance on every metric.
   - Done when: each metric matches a hand count for one parallel week.
7. **Phase 6: AA panels.**
   - Panels in the AA workspace with provenance chips and the coverage note.
   - Done when: Playwright renders them cleanly and the Shell Test passes. Stop the backend, delete seed data, insert a recognizable row and confirm the panel shows it; for the "Needs match" view, change a link in the UI and verify it in psql.

Production cutover waits on the three blockers: the signed Order Form, a team-wide key, and SkySlope's written OK for polling.

## Open questions

Claude Code asks Connor the decisions in Phase 0 and checks the technical unknowns on the first live calls. The rest are for SkySlope and eXp.

**Decisions for Connor (Phase 0)**

- [ ] Count `Pre-contract` sales as under contract?
- [ ] Count `Incomplete` listings as active?
- [ ] Credit a unit fully to the primary agent, or split it by `commissionSplits` percentages?
- [ ] How many days without a new listing or contract counts as "not producing"?
- [ ] Store street addresses for drill-downs, or only city, state and ZIP?
- [ ] Which commission field is GCI? Check 3 known deals.

**For SkySlope**

- [ ] Brokerage Customer or Integration Partner, which tier, and what price?
- [ ] Written OK for scheduled polling (§3(n)) and for storing listing and sale metadata (§3(h), §5), plus any retention limit.
- [ ] The full list of sale status strings and the `statusId` map, and how Pre-contract and Under Contract sales appear in Bulk Export.
- [ ] Does `modifiedOn` change on status flips (including date-driven ones), agent changes, commission edits and stage changes?
- [ ] Can files be deleted, and how would that show in the API?
- [ ] Which environment hosts the test account, and does it need separate credentials?
- [ ] What status code comes back over the rate limit, and is 100 per minute negotiable?

**For eXp**

- [ ] An office-level Broker, Admin or Auditor user for "ID - Activated Agents" and any other AA offices?
- [ ] Do eXp agents fill SkySlope's commission fields, or does eXp keep commissions elsewhere?
- [ ] Any restriction on agents generating API keys or using third-party integrations?

**Verify on the first live calls (Claude Code)**

- [ ] `/auth/login` body casing, `SS` prefix case, and whether data calls need `Timestamp`.
- [ ] Which contract-date key Bulk Export emits, and whether `0001-01-01` still marks "not set".
- [ ] Shapes of the untyped Bulk Export objects (`agent`, `coAgents`, `commission`, `stage`, `property`) against the detail DTOs.
- [ ] Whether response timestamps are UTC, and whether `modifiedAfter` is inclusive.
- [ ] Which email `/api/users` returns.
