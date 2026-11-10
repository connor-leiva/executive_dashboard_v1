# Port the ULRG reporting dashboard into Axcion

Status: specification only. Do not treat this file as an implementation.

Source of the legacy behavior: `connor-leiva/roi-conversion-dashboard` at `main`, commit `c75e5072d7d4a1c94a476a5c663df23f892f99eb`. Paths below are cited as `roi-conversion-dashboard/fub_live_dashboard/...`. The hosted site `ulrgdashreporting.com` was not reachable; every rule in this document comes from that tree, not from the live site.

Axcion paths are relative to this repo (`executive_dashboard_v1`).

The legacy app already has its own build spec, `roi-conversion-dashboard/fub_live_dashboard/UL_Reporting_Settings_Sources_Spec.md`. That document describes work that is already in the legacy code (lead-source groups, client-side date filtering). This file is the plan for rebuilding that product inside Axcion. Where the two disagree, the legacy Python is the source of truth.

## How to build this

Rebuild the **production** dashboard: the live Follow Up Boss + Sisu reports. Do not rebuild the request-time fan-out (five Follow Up Boss calls per person on every page load). Axcion already syncs CRM data into Postgres on a worker and serves reports from those rows. Extend that pattern.

Make every Utah Life-specific value a per-tenant, per-business setting. Seed Utah Life (`business.key = "ulrg"`) with the source strings, program start, fee defaults, and the Sympli cost share. A second tenant must be able to define its own source strings, QuickBooks spend mapping, cost share, and program start without a code change.

Lead-source spend is not a typed monthly fee. It is pulled from that tenant's QuickBooks company and then reduced by a configured cost share. See section 4.7.

## Decisions

These replace the earlier open questions. Do not reopen them in implementation.

1. **Spend comes from QuickBooks.** Utah Life's lead spend, including Realtor.com, is in QuickBooks Online. Map accounts and vendors to a lead-source group per tenant. Do not seed a dollar amount, and do not use class. Utah Life's Realtor.com rule is expense account `365` (primary) or vendor `962`. The charges are Purchases, not bills.
2. **Sympli pays half of Realtor.com.** Each source group can name another business and a share percent. The report shows ROI on gross spend and on spend after that share. Seed Realtor.com at 50% borne by the business with `key = "sympli"`. Sympli's QuickBooks is not connected, and Utah Life's books have no Sympli vendor, customer, or account for this spend. The 50% is only the setting.
3. **Write Follow Up Boss ids back to Sisu.** When an email fallback is what finds the contact, send `save_mapping_on_fallback=true`, the same as `sisu.get_contact_by_external_id` does today in the legacy app.
4. **Cohort months use the workspace calendar.** `IntranetWorkspace.timezone` (default `America/Denver`, read by `sync._workspace_tz`). The cohort key is the `YYYY-MM` of `lead.created_at_src`, which `fub.map_person` already stores in that zone. Legacy used UTC. Parity will disagree for leads near a month boundary. That difference is expected.
5. **Digest settings are per tenant in the UI.** Recipients, ClickUp list, and the model are editable. The only seeded recipient is Connor Leiva. The digest stays off until an admin enables it.
6. **Realtor.com is billed monthly.** Spend belongs to the month of `book_txn.txn_date`. Do not amortize.

Do not copy credentials out of the legacy tree. `roi-conversion-dashboard/fub_live_dashboard/backend/app/sisu.py` falls back to a real username and API token when env vars are unset. Leave those values there. Axcion already stores integration secrets encrypted on `integration`.

The three CSV tabs (Source Funnel, Agent Accountability, Agent Coaching) are a local-dev snapshot over `people.csv` / `calls.csv` / `appointments.csv` / `deals.csv` / `email_events.csv`. Production hides them when no export directory is mounted (`LeadConversionDashboard.jsx`, `csvAvailable`). Do not port the CSV pipeline. Section 4.1 records their formulas in case a later phase wants the same numbers computed from stored events.

## 1. What the legacy app is

One FastAPI process serves a React/Vite UI and talks to Follow Up Boss and Sisu at request time. Postgres holds tenants, users, sessions, lead-source groups, a cohort-result cache, a job queue, and the weekly digest. It does **not** store people, calls, or transactions. Those live only inside the cached JSON payload until the cache expires.

| Piece | Path |
| --- | --- |
| UI shell and tabs | `frontend/src/components/LeadConversionDashboard.jsx` |
| Routes | `frontend/src/App.jsx` (`/login`, `/dashboard/*`) |
| API | `backend/app/main.py` |
| CSV snapshot metrics | `backend/app/metrics.py` |
| Cohort rules | `backend/app/cohorts.py` |
| Live Follow Up Boss fetch | `backend/app/cohorts_live.py` |
| Sisu join and company dollar | `backend/app/cohorts_sisu.py`, `backend/app/sisu.py` |
| Team and agent pipeline | `backend/app/pipeline.py` |
| What's Working | `backend/app/pipeline_behavior.py` |
| Source groups | `backend/app/lead_sources.py` |
| Coaching benchmarks | `backend/app/benchmarks.py` |
| Auth | `backend/app/auth.py`, `backend/app/routes_auth.py` |
| Admin and settings API | `backend/app/routes_admin.py` |
| Cache | `backend/app/cohorts_cache.py` |
| Scheduler | `backend/app/scheduler.py` (digests and the job queue only) |
| Schema | `backend/app/models.py`, `backend/alembic/versions/0001`–`0005` |

Production data path for the four live tabs:

1. Resolve the tenant's `lead_source_groups` row (`key`, default `realtor_com`).
2. Pull every Follow Up Boss person whose `source` equals one of that group's variants and whose `created` is on or after the program start (hard-coded 1 Feb 2026).
3. For each person, pull calls, texts, email events, appointments, and deals.
4. Join Sisu by Follow Up Boss person id, then by email, and classify transactions.
5. Cache the universe in `cohort_cache`. Date presets slice that universe; they do not refetch.

## 2. Pages and views

`App.jsx` has two routes. Everything except `/login` renders `LeadConversionDashboard` behind `RequireAuth`.

The tab strip in `LeadConversionDashboard.jsx` (around the `view-toggle` block):

| View key | Label | Shown in production | Data |
| --- | --- | --- | --- |
| `funnel` | Source Funnel | Only if a CSV export directory exists | `GET /api/dashboard/all` via `metrics.py` |
| `agents` | Agent Accountability | Same | Same payload, `agents[]` |
| `coaching` | Agent Coaching | Same | Same payload, `coaching_agents[]` |
| `cohorts` | Realtor.com Cohorts | Yes. Forced to live + Sisu when CSV is absent | `GET /api/dashboard/cohorts` |
| `team_pipeline` | Team Pipeline | Yes | `GET /api/dashboard/team-pipeline` |
| `agent_pipeline` | Agent Pipeline | Yes | `GET /api/dashboard/agent-pipeline/{name}` |
| `whats_working` | What's Working | Yes. Reuses the team-pipeline universe | `pipeline_behavior.build_whats_working_payload` |
| `settings` | Settings | Yes | Lead sources, digest, sign-out |

The source picker (`SourcePicker.jsx`) is on Cohorts, Team Pipeline, Agent Pipeline, and What's Working. The choice is stored in `localStorage` key `ulrg.dashboardSourceKey`, default `realtor_com`. Settings and the CSV tabs ignore it.

There is no separate router path per tab. All of them are local state inside one page.

### 2.1 Source Funnel (CSV only)

`FUNNEL_STEPS` in `LeadConversionDashboard.jsx`: Total Leads, Attempted Contact, Live Connections, Appointments Set, Met With, Showings, Offers, Closed. One column per focus source (section 6.1). Presets: This Week, Last Week, This Month, Last Month, YTD, Last Year, LTM, plus a custom range. Week starts Monday (`WEEK_STARTS_ON`).

### 2.2 Agent Accountability (CSV only)

Table sorted by dials, then live connections: agent, leads, contacted, contact rate, dials, live connections, dial-to-live, emails sent, appointment-or-beyond, closed. A score ring and a "send more leads" hint are computed in the browser (`agentScore`, `sendMoreLeads` in the same file). Those two are presentation, not stored metrics.

### 2.3 Agent Coaching (CSV only)

Per assigned agent, over the same date-filtered people: total leads, contact rate, never called, median hours to first outbound call, percent of called leads inside 5 minutes / 1 hour / over 24 hours, dormant 30 days (no `last_activity`, or last activity older than 30 days), no-answer rate, live rate, called by self vs called by an ISA, appointment-or-beyond, closes, emails sent. ISA detection: `users.csv` role contains `"isa"`, or the caller name contains `"isa"`, or `created_by` is an ISA user id (`metrics.py`, `_is_isa_actor`).

### 2.4 Cohorts (production)

`CohortView` inside `LeadConversionDashboard.jsx`.

Header KPIs when Sisu is on: company dollar to date, program cost, net profit, ROI multiple, GCI, volume, match rate. Formula block on the page states company dollar as GCI + transaction fee − EXP risk (team) − EXP risk (agent) − agent payment.

Controls: start/end cohort month (`type="month"`), Apply, All cohorts, Live/CSV toggle (production forces live), Sisu toggle (production forces on), Refresh now (`refresh=true`).

Three tables, each with Export CSV:

1. **Cohort Performance Summary.** One row per `YYYY-MM`. Columns: Cohort, Maturity, Leads, Contacted, Contact %, Avg 1st Contact, <5 Min %, <24 Hr %, Appts Set, Set %, Held, Held %, UC, Closed, Dead, Active, then (Sisu) Sisu Match, Sisu Closed, Sisu UC, Company Dollar, Company Take, GCI, Volume, then Conv-to-Date, Age. Conversion is hidden under 3 months, muted from 3 to under 6 months, and marked industry-comparable at 12 months (`maturityBadge`, `conversionDisplay`).
2. **Speed-to-Lead Distribution.** Counts and percents in the buckets in section 4.2.
3. **Uncontacted Lead Snapshot.** People with no outbound touch, and of those, how many are at least 24 / 48 / 72 hours old. The 72-hour count is the sweep-eligible flag.

Drill-downs, also exported: closed-transaction audit (name, address, agent, GCI, trans fee, both EXP risk lines, agent payment, company dollar, volume, whether EXP risk came from the API or the default), under-contract list, per-lead list (matched / unmatched), Sisu linkage diagnostic (first 50 unmatched people).

Date filtering of an already-fetched payload is client-side: `filterCohortPayload` and `recomputeSisuMeta`. Cohort age and maturity stay anchored to the cohort month, not to the filter.

### 2.5 Team Pipeline (production)

`TeamPipelineView.jsx`. Presets: All time, Rolling 30D, Rolling 90D, Last 4 months, YTD (`PIPELINE_PRESETS`). The window filters on the lead's `created` date, inclusive (`main.py`, `_filter_universe_by_created`).

Blocks:

- Eight-stage team funnel strip. Each stage shows the cumulative count, conversion from the previous stage, gap versus the industry median, and a stuck count.
- Coaching summary: team leads, closes, stuck, and the four tier bands with the agents in each band.
- Agent table grouped by tier (critical, needs coaching, on track, strong). Columns: agent, tier, mini funnel, stuck, pickup rate, predicted close rate, biggest bottleneck. Row click opens Agent Pipeline. Export CSV.

### 2.6 Agent Pipeline (production)

`AgentPipelineView.jsx`, opened from the team table or the agent selector.

- Highlight cards (pickup versus team, met-with, and the other cards built in `pipeline.py`, `_build_highlights`).
- Vertical cumulative funnel. Clicking a stage filters the stuck list.
- Stuck leads, filterable by stage, Export CSV. Columns include name, stage, age, last touch, and a Follow Up Boss deep link.
- Speed-to-first-contact histogram (different buckets from the cohort table; section 4.4).
- Trend card.

### 2.7 What's Working (production)

`WhatsWorkingView.jsx`. Same universe and same date preset as Team Pipeline. Three framings over one payload (`pipeline_behavior.build_whats_working_payload`):

- Agent versus team median.
- Two agents against each other.
- Leads that closed in Sisu versus leads treated as dead.

Metrics: median minutes to first touch, median attempts before first live conversation, percent of leads with 5+ outbound attempts, percent with 2+ channels in the first 24 hours, median live-call duration, median days from first live touch to appointment, appointments-kept percent, median price, percent of leads that arrived during daytime. Each median carries a sample size. A logging-hygiene block flags agents whose call notes or appointment outcomes are too thin to trust.

Lead-journey strips show touches on a 0–72 hour axis from `created` (`build_journey`).

### 2.8 Settings (production)

`SettingsView.jsx`: `LeadSourcesAdmin.jsx` (admin), `DigestSettings.jsx` (admin), and sign-out for every signed-in user (`POST /api/auth/logout`).

Lead-source admin (`routes_admin.py`): list including inactive, create, update, soft-delete. Fields: immutable `key` (`^[a-z][a-z0-9_]*$`), display name, a typed `monthly_cost_dollars` (0 to 1,000,000), variants (max 50), match mode `exact|contains|regex`, active flag, display order. Changing variants or match mode deletes that group's cohort cache. Changing the typed cost does not. Axcion does not keep that typed cost. Section 4.7 replaces it with QuickBooks.

Digest admin: per source group, active flag, send day/hour/minute, timezone (default `America/Denver`), recipients, max agents (1–12, default 6), minimum lead threshold, include-reinforce flag, test send, delivery history, test ClickUp task.

## 3. Auth and access

Legacy (`routes_auth.py`, `auth.py`, migration `0002_seed`):

- No public signup. An email must be on `allowed_emails` for a tenant.
- Password hash is argon2id. Session cookie is `ulrg_session`, signed with itsdangerous. The `sessions` row can be revoked.
- First user of a tenant becomes `admin`. Later users are `viewer`.
- Seed tenant name `Utah Life Real Estate Group`, allowlisted email `connor.leiva@gmail.com`.
- Postgres RLS on tenant tables reads `app.current_tenant`. The scheduler and admin writes run `SET LOCAL row_security = off`.
- Admin gates source-group and digest writes. `GET /api/dashboard/lead-sources` is any signed-in user and returns active groups only.
- Daily call caps exist as columns (`fub_calls_per_day_cap`, `sisu_calls_per_day_cap`) and `usage_events` logs each external call. Confirm in `middleware.py` before relying on the cap; the column is present either way.

Axcion already does this differently, and the port must follow Axcion:

- Tenant comes from the request host (`backend/app/tenancy.py`, `deps.current_user`). Every query filters `tenant_id`. There is no RLS.
- Roles are `owner`, `admin`, `member` (`backend/app/models.py`, `User.role`). Members are further limited by `tab_access` and `backend/app/services/tabs.py`.
- A tenant has many businesses. Follow Up Boss and Sisu integrations, leads, and transactions are scoped to `business_id` (`Integration`, `Lead`, `Transaction`).
- Secrets live in `integration.access_token_enc` / `refresh_token_enc`, not in source.

Settings writes: `owner` and `admin` only (`deps.require_role`). Reads: anyone who can see the brokerage tab. Do not add a second auth system.

## 4. Metric definitions

Percentages use `_safe_pct`: `round(numerator / denominator * 100, 1)`, or `0.0` when the denominator is 0. Money is `round(..., 2)`.

### 4.1 CSV snapshot formulas (`metrics.py`)

People are included when their `created` (else `updated`) date is inside the range. With `focus_sources_only=true` (the API default), a person counts only if `_focus_source_bucket` returns one of the names in section 6.1. Calls, appointments, deals, and emails are date-filtered on their own timestamps **and** dropped unless the person was already kept. A call with no `person_id` can still count if `person_source` maps into a focus bucket.

| Metric | Definition |
| --- | --- |
| Total leads | People in the source bucket |
| Attempted contact | `contacted` is `1/true/yes/y/t` |
| Dials | Outbound calls. `direction == outbound`, else `is_incoming` false |
| Live connections | Call `outcome` contains one of: answer, answered, connected, live, spoke, conversation, completed. Counted even on inbound |
| Spoke with | Distinct people with at least one live call |
| Appointments set | Distinct people with an appointment whose `start_time` / `start` / `created` is in range |
| Met / showing / offers / under contract / closed | Substrings of the **person** stage. Met: `met`, `consult`. Showing: `showing`, `tour`, `view`. Offers: `offer`, `negotiat`. Under contract: `under contract`, `pending`, `escrow`, `contract`, and also anything closed. Closed: `closed`, `sold` |
| Closed (floor) | Also the distinct people with a deal whose status or stage contains `closed`, `won`, or `sold`, dated on `actual_close_date` / `close_date` / `created`. The funnel uses `max(stage closed count, distinct closed-deal people)` |
| Agent contact rate | Contacted / leads |
| Dial to live | Live connections / dials |
| Appointment or beyond | Distinct people with an appointment, or whose stage contains appointment, met, show, offer, contract, pending, closed, sold |
| Median hours to first call | Median of `(first outbound call − person created)` in hours, among people who have both |
| % under 5 min / 1 hr / over 24 hr | Those lags, divided by the count of people who were called at least once, not by all leads |
| Dormant 30d | `last_activity` missing, or older than 30 days from `datetime.utcnow()` |
| Emails sent | `event_type` in `sent`, `delivered` |

The validation files `validation/dashboard_validation_20260309_170730.md` and the duplicate `170840` compare this CSV path to an older API extract for source `SOI` and agent `Spring Bengtzen`. Closed did not match (API 101, CSV 0). Do not use that report as the parity oracle for the live dashboard.

### 4.2 Cohort assignment and the three tables (`cohorts.py`, `aggregate_cohorts`)

A person is in the report when:

- Follow Up Boss `source` matches the active lead-source group (section 6.2), and
- `created` parses, and
- the cohort key is greater than or equal to the program-start month.

Legacy cohort key is `created` formatted `%Y-%m` after timestamps are converted to UTC and then stripped of tzinfo (`_parse_datetime`). It never changes. **Axcion does not copy that.** The cohort key is the year and month of `lead.created_at_src`, the workspace calendar date already written by `fub.map_person` using `sync._workspace_tz` (`IntranetWorkspace.timezone`, default `America/Denver`). A lead created at 11pm Mountain on 31 January is January in Axcion and February in the legacy report. Junk, wrong-number, and unqualified leads stay in the denominator. There is no exclusion for Follow Up Boss stage `Trash`. Close credit stays with the original lead, not with whichever agent closed it.

**Contact.** `first_outbound_at` is the earliest of: outbound call, outbound text, sent or delivered email, and appointment `start`, each ignored when the timestamp is missing or earlier than `created` (`cohorts_live._fetch_person_payload`). "Contacted" means that timestamp exists. It is not the Follow Up Boss `contacted` boolean.

Minutes to first touch = `max((first_outbound − created) / 60, 0)`.

Speed buckets, left-closed except the first:

| Key | Window |
| --- | --- |
| `u5min` | `< 5` minutes |
| `u30min` | `5 <= m < 30` |
| `u2hr` | `30 <= m < 120` |
| `u24hr` | `2 hours <= m < 24 hours` |
| `u72hr` | `24 hours <= m < 72 hours` |
| `o72hr` | `>= 72` hours |
| `never` | no first touch |

Cohort row, denominator `n` = leads in the month:

| Field | Formula |
| --- | --- |
| `contacted_pct` | contacted / n |
| `avg_time_to_contact_minutes` | mean of the minutes above, among people who have a first touch. `null` if none |
| `pct_contacted_5min` | count with minutes `< 5` / **n** (not / contacted) |
| `pct_contacted_24hr` | count with minutes `<= 24h` / **n** |
| `appts_set_count` | appointment row exists, or stage contains `appointment set` or `appointment`, or the held rule below matches |
| `appts_set_pct` | set / n |
| `appts_held_count` | an appointment `outcome` contains attended, completed, held, showed, or kept; **or** stage contains met with, showing, tour, listing agreement, active listing, submitting offers, offer; **or** under contract; **or** closed |
| `appts_held_pct` | held / **set** |
| `under_contract_count` | person stage contains under contract / pending / escrow, or a deal stage does, and the person is not closed |
| `closed_count` | person stage contains closed / sold, or a deal status contains closed / won |
| `dead_count` | not closed and not under contract, and stage contains dead, trash, archive, junk, do not contact, lost, unqualified |
| `active_count` | everyone else |
| `conversion_to_date_pct` | closed / n. The UI hides it when `show_conversion` is false |

Closed wins over under contract, which wins over dead.

Age in months (`_cohort_age_months`):

```
months = (now.year - cohort.year) * 12 + (now.month - cohort.month)
months += (now.day - 1) / 30
```

`is_mtd` is the cohort key equal to the current month. `show_conversion` is age ≥ 3 and not MTD. `muted_conversion` is age < 6. `industry_comparable` is age ≥ 12.

`maturity_pct` is 100 when `int(age) >= 12`, else this curve (missing keys, including 0, are 0):

| Month | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12+ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| % of eventual closes assumed realized | 5 | 8 | 12 | 18 | 28 | 45 | 55 | 63 | 75 | 82 | 87 | 100 |

The curve is a label. Nothing multiplies conversion by it. Projected final conversion is not implemented.

Flags: `flag_slow_contact` when the average is not null and `> 30` minutes. `flag_low_24hr_contact` when the 24-hour percent is `< 85` and n > 0.

Industry conversion bands shipped for the UI, not used in the math: average 1–3%, top quartile 5–8%, elite ISA 10–14% (`BENCHMARKS`).

Uncontacted snapshot: `never` bucket, then of those people, age since `created` ≥ 24h, ≥ 48h, ≥ 72h. A lead contacted late is not in this table.

### 4.3 Sisu classification, company dollar, ROI (`cohorts_sisu.py`)

For each kept person, look up a Sisu contact (section 5.2) and pull that contact's transactions.

`_classify_transaction`, in this order, using datetimes rather than the team's status strings:

1. `closed_dt` parses and is `<= datetime.utcnow()` → `closed`.
2. Else `lost_reason_id` is set, or `archive_ts` parses → `lost`.
3. Else `uc_dt` parses → `under_contract`.
4. Else `active`.

A person can contribute more than one transaction. Counts are transaction counts, not person counts. A person's best state, used by the pipeline, is the first hit in `closed`, `under_contract`, `lost`, `active`, else `no_sisu_tx`.

Money, **closed transactions only** for the dollar totals:

| Input | Source |
| --- | --- |
| GCI | `gross_commission_amt`, else `commission_amt` |
| Volume | `closed_volume_amt`, else `trans_amt` |
| Transaction fee | `edit-client.trans_fee_amt`, else the summary `trans_fee_amt` |
| Adjusted GCI | `commission_info.adjusted_gci`, else GCI |
| Team income | `commission_info.team_income`, else summary `team_income_amt`. Kept as a cross-check. **Not** company dollar |
| Agent payment | Sum of `commission_info.summaries.final[*].value` where `external_type == 2` |
| EXP risk | From `commission_info.adjustments`, every line whose category contains `"exp risk management"`. The larger amount is team, the smaller is agent. One line is treated as team and agent 0. No lines → (0, 0), then the defaults below |
| Referral fees out | Sum of `commission_info.rebates.*.team`. Displayed. **Not** subtracted again, because the comment in `_compute_company_dollar` says the referral already sits inside the agent payment |

Defaults when the extracted side is `0.0`: team `$69`, agent `$49` (`DEFAULT_EXP_RISK_TEAM`, `DEFAULT_EXP_RISK_AGENT`). The audit column `exp_risk_source` is `API` when the team-side extract was `> 0`, else `DEFAULT`.

```
company_dollar = round(gci + trans_fee - exp_risk_team - exp_risk_agent - agent_payment, 2)
```

Per cohort month, with `leads` = FUB leads received and `closes` = Sisu closed transaction count:

| Field | Formula |
| --- | --- |
| Match rate | matched people / leads |
| Sisu closed % | Sisu closed transactions / leads |
| Company take % | company dollar / GCI × 100, 1 decimal |
| Team take % | team income / GCI × 100. The older definition. Keep it out of the headline |
| GCI per lead | GCI / leads |
| Company dollar per lead | company dollar / leads |
| Company dollar per close | company dollar / closes |

The legacy app then applied one flat `monthly_cost_dollars` to every cohort row, skipped ROI when that cost was 0, and set program cost to `monthly cost × the number of cohort rows with leads_received > 0`. Axcion does not do that. Spend, net profit, and both ROI multiples are defined in section 4.7. A date filter still recomputes the header from the visible rows, the way `recomputeSisuMeta` does, so the header cannot keep an unfiltered total.

Program rollup of company dollar, GCI, and volume is still the sum of the closed transactions on the visible rows.

`team_income` is documented in the legacy code as "team split + trans fee before adjustments." Axcion's current company-dollar approximation **is** this number. It is the wrong numerator for ROI. See section 7.

### 4.4 Pipeline stages (`pipeline.py`)

`classify_stage` returns the deepest stage. Counts are cumulative: a lead at stage N increments stages 1..N (`_compute_stage_counts`). Conversion into stage N is `count(N) / count(N-1)`.

Order and labels:

| Key | Label | How a lead lands here |
| --- | --- | --- |
| `leads_received` | New Leads Received | Default |
| `contacted` | Contacted (2-way) | Follow Up Boss `contacted` flag **or** `first_outbound_at`. This is wider than the cohort "contacted" definition, which ignores the boolean |
| `qualified` | Qualified / Buyer Connection | Stage contains qualified, buyer connection, or hot |
| `appt_set` | Appointment Set | Any appointment, or stage contains appointment / appt |
| `met_with` | Met With Client | Stage contains met / consult, or an appointment outcome is exactly met, kept, completed, or converted |
| `showing` | Showings Conducted | Stage contains showing / tour / view, or an appointment outcome contains showing |
| `offer_written` | Offer Written | Sisu state `under_contract`, or stage or a deal stage contains offer / negotiat / under contract / pending / escrow / contract |
| `closed` | Closed Transactions | Sisu state `closed` only. A Follow Up Boss "Closed" stage with no Sisu close does **not** count here |

Sisu `closed` and `under_contract` are decided before the Follow Up Boss stage. Lost Sisu leads are not a stage; they stay wherever the Follow Up Boss stage puts them, and What's Working can still call them dead.

**Stuck.** Not closed, and `STUCK_THRESHOLDS_DAYS` since `created` (not since a real stage change; the file says Follow Up Boss stage history is not available), **and** no `last_activity_at` or that timestamp is at least 7 days old (`STUCK_INACTIVITY_DAYS`). Thresholds in days: leads 1, contacted 5, qualified 7, appointment 14, met 21, showing 14, offer 7. Severity for the cell color (`age_severity`): ≤3 hot, ≤14 cool, ≤30 warm, else cold. The labels are not in age order; copy them as written.

**Pickup rate** = contacted-stage count / leads. **Raw close rate** = closed-stage count / leads.

**Predicted close rate** (`_predicted_cvr`): 0 when there are no leads. If `closed_count >= 1`, it is `closed / leads`. Otherwise it is the product of four rates, and a 0 rate is replaced by a fallback before multiplying: met-with or 0.55, showing or 0.65, offer or 0.40, closed or 0.50. Because a genuine 0 becomes the fallback, an agent with no met-with data is forecast as `0.55 × 0.65 × 0.40 × 0.50`.

**Versus benchmark**, in percentage points, is the stage conversion minus the median below. These medians are not the same as `benchmarks.py`.

| Stage conversion into | q1 | median | q3 |
| --- | --- | --- | --- |
| contacted | 50 | 65 | 80 |
| qualified | 40 | 55 | 70 |
| appt_set | 30 | 45 | 60 |
| met_with | 40 | 55 | 70 |
| showing | 50 | 65 | 80 |
| offer_written | 30 | 40 | 55 |
| closed | 30 | 50 | 70 |

Bottleneck is the stage with the most negative gap. Severity: `< -20` crit, `< -10` warn, `< -3` mild, else none. The label is `"{stage label} ({gap:+.0f}pp)"`.

**Tier** (`classify_agent_tier`), first match wins:

1. Critical if pickup `< 30`, or uncontacted count `> 5`, or predicted close rate `< 3`.
2. Strong if three or more stage conversions are at or above that stage's q3.
3. Needs coaching if the worst gap is `< -20` pp.
4. Else on track.

Stuck severity on the agent row: `>= 10` crit, `>= 4` warn, else ok.

Pipeline speed histogram, in minutes, separate from the cohort buckets: `≤5m` is `[0, 5)`, `1h` is `[5, 60)`, `24h` is `[60, 1440)`, `3d` is `[1440, 4320)`, `3d+` is the rest. Median and percent under 5 minutes are reported beside it.

Follow Up Boss deep link default: `https://liveutah1.followupboss.com/2/people/view/{id}` (`FUB_DEEP_LINK_BASE`). Axcion already learns the account domain in `fub_sync._identity` (`account_domain`). Use that, not the Utah Life host.

### 4.5 What's Working metrics (`pipeline_behavior.py`)

Team medians are computed on the pooled leads, not by averaging per-agent medians.

| Key | Definition |
| --- | --- |
| `speed_to_first_min` | Median of the pipeline speed minutes |
| `attempts_to_live` | Median, over people who have at least one `kind=live` touch, of how many touches sit strictly before that first live touch. People who never connect are excluded, not counted as zero |
| `pct_5plus_attempts` | People whose touch list has length ≥ 5 / people. The touch list includes appointments, not only calls |
| `multichannel_24h_pct` | People with at least two distinct `channel` values on touches at or before `created + 24h` / people |
| `median_call_dur_sec` | Median of `duration_sec` on `channel=call` touches with `duration_sec > 0`. A note-only live call (duration 0) does **not** enter this median. The UI caveat says so |
| `live_to_appt_days` | Median days from the first `kind=live` touch to the first appointment start |
| `appts_kept_pct` | Appointments whose outcome is exactly `met`, `kept`, or `completed`, divided by appointments that have any outcome. Null when none have an outcome |
| `median_price` | Median of Follow Up Boss `price` where it is an int or float. The row is available only when `_price_coverage_pct >= 60` (`price_available`) |
| `arrival_daytime_pct` | People whose parsed `created` hour satisfies `8 <= hour < 20` / people. The clock is the UTC-naive timestamp `_parse_datetime` stored, not America/Denver |
| `n_closed` | `sisu_state == closed` |

A call is `live` when `duration > 0` **or** the note is non-empty. Manual Follow Up Boss logs often have duration 0. Texts and sent emails are always `try`. An appointment is `live` when its outcome is exactly met, kept, or completed. The comment in `cohorts_live.py` (Bryn Johnstone, person id 140348, 28 May 2026) is why the note rule exists. Keep it.

Dead, for the closed-versus-dead framing: `sisu_state == lost`, or not closed / under contract / active and `is_stuck`. Read `_matches_filter` in `pipeline.py` and the dead comment in `build_whats_working_payload` and keep them identical.

`benchmarks.py` is **not** used by the cohort or pipeline screens. It is the digest's coaching bar: the product of the six floor rates is about 8% close, and the product of the six targets is about 10%.

| Transition | Floor | Target |
| --- | --- | --- |
| lead → contacted | 0.85 | 0.90 |
| contacted → appointment | 0.40 | 0.42 |
| appointment → met | 0.80 | 0.83 |
| met → showing | 0.65 | 0.68 |
| showing → offer | 0.55 | 0.58 |
| offer → closed | 0.80 | 0.82 |

Also: first-touch target 5 minutes, floor 15; percent with 5+ attempts target 85, floor 70; multi-channel in 24 hours target 80, floor 60. Top performer is 10% close and at least 2 closes. These constants are module-level and the file says to lift them onto the digest config when a second tenant needs different numbers. Do that in phase 6 rather than hard-coding Connor's 8–10% goal.

### 4.6 Digest (production, but not a dashboard page)

`scheduler.py` enqueues due `digest_configs` every 5 minutes and drains `jobs` every 60 seconds (`SELECT … FOR UPDATE SKIP LOCKED`, 10 jobs, 3 attempts then `failed`). The handler is `digest_handler.handle_weekly_digest`. Selection and prose live in `digest_select.py`, `digest_characterize.py`, `digest_llm.py`, `digest_render.py`. Delivery is email (`mailer.py`) plus, after the email commits, one ClickUp task per agent block (`digest_clickup.py`, `clickup.py`). Unsubscribe is `GET /api/digest/unsubscribe`.

This is a large feature on top of the same universe. It is phase 6. Do not block the dashboard on it. Recipients, the ClickUp list, and the model are tenant settings in the Axcion UI (phase 6), not environment variables. The legacy defaults that the UI starts from: timezone `America/Denver` (use `IntranetWorkspace.timezone` instead), max agents 6, minimum lead threshold 1, model `claude-sonnet-4-6` (`digest_llm._DEFAULT_MODEL`), ClickUp list id null so ClickUp stays off (`0005_digest_clickup_columns`).

### 4.7 QuickBooks spend, cost share, and ROI

Axcion already has QuickBooks per business. `Integration.provider = "qbo"` stores the realm and the encrypted tokens. `integrations/qbo.py` does OAuth, the ProfitAndLoss report, and the chart of accounts. `books_sync.sync_qbo_txns` already pulls `Purchase`, `Bill`, `BillPayment`, `JournalEntry`, `Deposit`, and `Transfer` into `book_txn` and `book_txn_line`. A Purchase is how QuickBooks represents a bank or credit-card charge. `test_purchase_categorized` shows the shape: the header `AccountRef` is the card (stored as `bank_account_label`), and the line `AccountBasedExpenseLineDetail.AccountRef` is the expense account stored as `book_txn_line.account_qbo_id`. Vendor 962 has no accounts-payable bills, and the company has had no bills since 2023, so the Realtor.com charges are Purchases. No new entity type is required.

Two fields the current sync drops, which this report needs:

- The vendor id. `_payee` keeps `EntityRef.name` (and `VendorRef.name`) and throws away `value`. Add `vendor_qbo_id` on `book_txn`, from `EntityRef.value` on a Purchase and `VendorRef.value` on a Bill. The line payee is often empty on a Purchase, so vendor matching reads the header.
- The Purchase `Credit` flag. A true `Credit` is a refund, not more spend. Store it as `purchase_credit` on `book_txn`. Line `posting_type` is null on a Purchase, so the null case must not treat a refund as a charge.

Do not match the card account. Amex `1150040000` is the header account the money left. Matching it would count every card charge as lead spend.

Use `book_txn_line`, not the P&L summary and not `book_txn.amount`. The header amount is the first category line only. `txn_date` is the books date. Realtor.com is billed monthly, so the cohort month is that date's `YYYY-MM`. Do not amortize, and do not spread one charge across later months.

`books_sync._backfill_start` defaults to 1 January of the current year unless `integration.config.books_backfill_start` is set. Before trusting ROI, confirm the Utah Life realm has Purchase lines for account `365` back to the program start (`2026-02-01`). If the stored history starts later, set `books_backfill_start` to `2026-02-01` and re-sync. That is a cursor change, not a new importer.

**Utah Life's chart, from the live company.** Account numbers are null; the digits are in the name.

| Role | Id | Name |
| --- | --- | --- |
| Expense account, Realtor.com | `365` | `Realtor.com`. Fully qualified `62000 Lead Generation:62100 General Prospecting & Marketing:62130 Internet Lead Generation:Realtor.com`. Parent `363`, `62130 Internet Lead Generation` |
| Vendor, Realtor.com | `962` | `Realtor.com` |
| Expense account, Realty.com | `579` | `Realty.com` |
| Vendor, Realty.com | `1311` | `Realty.com LLC` |
| Expense account, Zillow | `366` | `Zillow` |
| Vendor, Zillow | `68` | `Zillow.com` |
| Vendor, Opcity | `1568` | `Opcity` |

Other internet-lead accounts exist and are not part of the Realtor.com rule: Ylopo `350`, Facebook `386`, Fello `757`, Follow-up Boss `1150040018`, OJO Home `754`, Other - Internet Lead Gen `391`, RedX `492`, Remine `509`, Shilo AI `1150040081`, Sierra `568`, Vulcan 7 `387`. Do not roll them into Realtor.com because they share the parent `62130`.

Classes and departments are not a match key. They could not be listed on the live company, and the only department use found was on bills from 2022. Leave `class_label` off the Utah Life rules. Other tenants may still set it.

**Mapping, per tenant.** New table `lead_source_spend_rule`, many rows per source group:

- `lead_source_group_id`, `tenant_id`
- `business_id` — which QuickBooks company. This is the realm on that business's `qbo` integration. Utah Life's rules point at the brokerage business. Do not point one at Sympli. Sympli's QuickBooks is not linked, and Utah Life's books have no Sympli vendor, customer, or account for this cost.
- Optional `account_qbo_id`. When set, the line's expense account must equal it. This is the line account, never the header card account.
- Optional `vendor_qbo_id`. When set, `book_txn.vendor_qbo_id` must equal it.
- Optional `class_label` substring. Unused for Utah Life.
- A rule with no account, vendor, or class is invalid. Do not allow a rule that means "all expenses."
- Within one rule, every field that is set must match (AND). A line is in the group if any rule matches (OR). A line that matches two rules of the same group counts once.
- A line that matches two groups counts in the first group by `display_order` only. Say so in the settings UI when the admin's rules overlap.

Seed, for the Utah Life brokerage only, two rules on `realtor_com`, OR'd:

1. `account_qbo_id = 365`. This is the primary rule. It counts a Purchase coded to Realtor.com even when the card payee is blank.
2. `vendor_qbo_id = 962`. This counts a charge to that vendor that was coded to a different account. A line that is both counts once.

Do not seed account `579` or vendor `1311` until the Realty.com group is activated. Do not seed Zillow. Do not add Opcity vendor `1568` to the Realtor.com rules. Market VIP / Opcity leads are inside the Realtor.com *lead* group (section 6.2). Whether the Opcity *charges* belong in the Realtor.com *spend* is still open. Until that is answered, those purchases stay out, so the lead count can include Opcity leads while the spend does not.

The settings UI lists the connected company's accounts and the vendors already on `book_txn`. The two seeded rules are checked. An admin can add another account from the `62130` list above without a deploy.

**Amount.** For a matched line, start from `book_txn_line.amount`. Add it when `posting_type` is null or `debit`, unless `book_txn.purchase_credit` is true, in which case subtract it. Subtract it when `posting_type` is `credit`. Ignore `BillPayment` lines so a bill and its payment are not both spend. Ignore `Transfer`.

**Cost share.** New table `lead_source_cost_share`:

- `lead_source_group_id`, `tenant_id`
- `bearer_business_id` — the other business that bears part of the cost. For Utah Life's Realtor.com group, seed the business with `key = "sympli"` when it exists.
- `share_pct` `Numeric(5,2)`. Seed `50.00`. The sum of shares on one group must be between 0 and 100.

This percent is the agreement. It is not a second QuickBooks pull. Gross spend is only the lines the rules match on Utah Life's company. Net spend is `gross × (1 − share_pct / 100)`. There is nothing in Utah Life's books to subtract for Sympli's half, and Sympli's company is not connected. Do not invent a Sympli rule.

**Per cohort month**, once at least one rule exists for the group:

| Field | Formula |
| --- | --- |
| `gross_spend` | Sum of the matched line amounts whose `txn_date` falls in that month. A month with rules and a completed sync and no lines is `0`, not null |
| `share_pct` | The group's configured share. `0` when there is no share row |
| `net_spend` | `gross_spend × (1 − share_pct / 100)`, rounded to cents |
| `cost_per_lead_gross` / `cost_per_lead_net` | Spend / leads, or null when there are no leads |
| `cost_per_close_gross` / `cost_per_close_net` | Spend / Sisu closes, or null when there are no closes |
| `net_profit_gross` | company dollar − gross spend |
| `net_profit_net` | company dollar − net spend |
| `roi_gross` | company dollar / gross spend, or null when gross spend is 0 |
| `roi_net` | company dollar / net spend, or null when net spend is 0 |

When the group has no spend rule, all of these are null and the UI says the QuickBooks mapping is not set. Do not show `$0` and an infinite ROI.

Program gross is the sum of `gross_spend` over the visible cohort months, including a month that had spend and no leads. Program net uses the same share percent. This is the opposite of the legacy rule, which dropped empty months and billed a flat fee. Do not compare these ROI figures to the legacy dashboard.

The header shows both. Label them "ROI on full QuickBooks spend" and "ROI after Sympli's share" (use the bearer business's name, not a hard-coded "Sympli", except in the Utah Life seed).

## 5. External APIs and fields

### 5.1 Follow Up Boss

Base `https://api.followupboss.com/v1`. Basic auth, API key as the username and an empty password. Pagination follows `_metadata.next`. Page size 100. Timeout 30s, 4 attempts, 429 backoff of `8 * (attempt + 1)` seconds (`cohorts_live.py`). The UI fan-out uses 6 workers.

| Call | Params | Fields read |
| --- | --- | --- |
| `GET /people` | `source={variant}`, `createdAfter={program_start as YYYY-MM-DD HH:MM:SS}`, `limit` | `id`, `source`, `created`, `stage`, `assignedTo`, `contacted`, `emails[]` (`isPrimary`, `value`), `primaryEmail`, `email`, `price`, `firstName`, `lastName`, `name`, `type` / `leadType` |
| `GET /people` discovery | same, `limit=1`, plus `q` probes for `market vip` and `opcity bla` | `_metadata.total` only. The `q` hits are reported as `related_non_realtor_hints` and are **not** included |
| `GET /calls` | `personId` | `direction` or `isIncoming`, `created`, `duration`, `note` |
| `GET /textMessages` | `personId` | same direction fields, `created` |
| `GET /emEvents` | `personId` | `type` or `event_type` in sent/delivered, `occurred` or `created` |
| `GET /appointments` | `personId` | `start` or `startTime`, `outcome` |
| `GET /deals` | `personId` | `status`, `stage`, `actualCloseDate` or `closeDate` |
| `GET /users` | CSV path only | `role`, `name`, `user_id` for ISA detection |

`source={variant}` is an exact Follow Up Boss filter. The group's matcher runs again on the returned `source` and drops rows that fail it. A `contains` group still only **fetches** the variant strings it probes. `KNOWN_REALTOR_VARIANTS` is always included in the probe list even when the group has its own variants (`_discover_variants`: candidates are the known list plus `extra_known`). That means a Realty.com group whose variants do not contain "Realtor" can still pull Realtor.com people if those strings are in `KNOWN_REALTOR_VARIANTS`, and the matcher then drops them only when the returned source fails the Realty.com matcher. Preserve the post-filter. Do not preserve the hard-coded known-variant probe list; probe the group's own variants only, or a tenant will see another brand's discovery traffic.

People with no `created`, or with a cohort month before the program start, are dropped. The same person returned under two variants is stored once, last write wins (`realtor_people[pid] = …`).

### 5.2 Sisu

Base defaults to `https://api.sisu.co/api` (production). Staging was abandoned because it was missing in-progress deals. Basic auth, username and token. Timeout 30s, 4 attempts, 429 backoff `6 * (attempt + 1)`. Ten workers. External service id is the env `SISU_EXTERNAL_SERVICE`, default **`fub`**. The docstring in `sisu.py` still says `follow_up_boss`; the constant is `fub`. Use `fub` for parity with Utah Life's existing mappings.

| Call | When |
| --- | --- |
| `GET /v1/contact/external` | `external_contact_id` = Follow Up Boss person id, `external_service_id=fub`, `allowed_statuses=N`, and when an email exists `fallback_email` plus `save_mapping_on_fallback=true` |
| `GET /v1/contact?email=` | Only when the primary contact has zero transactions, to pick up duplicate contacts from an old `{account}_{id}` external id. Transactions are merged and deduped by id |
| `GET /v1/contact/{id}/transactions` | Every matched contact |
| `GET /v1/client/commission-info/{id}` | Closed transactions |
| `GET /v1/client/edit-client/{id}` | Closed transactions, for `trans_fee_amt`, name, and address |

People with no email are not looked up. They show up as unmatched with that reason. `save_mapping_on_fallback=true` writes the Follow Up Boss person id onto the Sisu contact when the email fallback is what found it. Axcion must do the same write. Do it only on that fallback, only for a lead in an active source group, and only when the id lookup missed. An id that already matches does not need a write. Log each write on the sync run. Do not send the flag for people outside an active source group.

Axcion already calls different Sisu routes: `GET /v1/team/get-team-clients`, `get-team-agents`, `edit-agent`, `get-team-vendors`, and `commission-info` (`backend/app/integrations/sisu.py`). It does not call `/v1/contact/external`.

### 5.3 What is stored

Legacy stores none of the CRM rows. `cohort_cache` holds the finished JSON. Fresh for 1 hour (`COHORT_CACHE_FRESH_TTL`, default 3600). From 1 hour to 24 hours the cache is served and rebuilt in the background. After 24 hours the request waits. `refresh=true` bypasses it. Cache keys, after the settings work:

- `live|{group_key}|{0 or 1}` for cohorts, the second flag being Sisu on/off
- `team-pipeline|{group_key}`
- `agent-pipeline|{agent}|{group_key}`

The CSV payload has a separate 45-second in-process cache (`main.py`, `CACHE_TTL_SECONDS`).

There is no schedule that refreshes Follow Up Boss or Sisu. Data is fetched when a person opens a tab or hits Refresh. The scheduler only sends the digest.

## 6. Lead-source mapping

### 6.1 CSV focus buckets (`metrics._focus_source_bucket`)

First match wins:

| Bucket | Test on lowercased source |
| --- | --- |
| Zillow Preferred | contains `zillow preferred` |
| Zillow.com | contains `zillow.com` |
| Google PPC | contains `google ppc` |
| Realty.com | contains `realty.com` |
| Opcity | contains `opcity` |
| Brivity Connect | contains `brivity connect` |
| SOI | word `soi`, or `sphere of influence` |
| Expired | contains `expired` |
| Zillow | startswith `zillow` |
| (dropped) | anything else, when focus mode is on |

`Realty.com` and `Realtor.com` are different buckets. `realtor.com` does not contain `realty.com`.

### 6.2 Production groups

Table `lead_source_groups` (migration `0003`, service `lead_sources.py`). One row per `(tenant, key)`.

Match modes, case-insensitive, empty variants match nothing:

- `exact`: stripped source equals a variant.
- `contains`: a variant is a substring of the source. This is the default and matches the old behavior of "does this string contain the variant."
- `regex`: each variant is `re.compile(variant, IGNORECASE).search`.

Seeded for the tenant named `Utah Life Real Estate Group`:

- `realtor_com`, display `Realtor.com`, cost `0`, active, order 0, mode `contains`, variants: `Realtor.com`, `Realtor.com MVIP`, `Realtor MVIP`, `Market VIP via Opcity BLA`, `market vip via opcity bla`, `realtor.com mvip`.
- `realty_com`, display `Realty.com`, cost `0`, **inactive**, order 10, variants `[]`.

`contains` plus variant `Realtor.com` also matches `Realtor.com MVIP`. It does not match a bare `Realtor` or `Realty.com`. The older helper `_is_realtor_source` is a regex `realtor` plus extra sources, which is wider (any string containing "realtor"). Production passes `build_matcher(group)` and does not use that regex. The dev-without-database fallback still does, and it also appends `REALTOR_EXTRA_SOURCES`.

`KNOWN_REALTOR_VARIANTS` adds the bare string `Realtor` on top of the extras. The seed list does not include bare `Realtor`. For Utah Life parity, seed the migration's list, not the regex and not the bare `Realtor` string, unless a parity run shows leads whose source is exactly `Realtor`.

Program start is not on the legacy table. Both `build_cohort_payload_live` and `build_pipeline_universe` use `datetime(2026, 2, 1)`. Put `program_start` on the Axcion source-group row and seed Utah Life's Realtor.com group to `2026-02-01`. Compare that date to `lead.created_at_src`, not to the UTC timestamp. Leave Realty.com inactive until someone sets its variants and start.

The legacy `monthly_cost_dollars` column, seeded at 0, is not ported. Spend comes from QuickBooks (section 4.7). The env var `REALTOR_COM_MONTHLY_COST` stays behind in the legacy app.

Opcity and Market VIP are included in Realtor.com on purpose. The comment in `cohorts_live.py` says Move owns Opcity and that 4 of the first 5 confirmed closes arrived as `Market VIP via Opcity BLA`. Do not also create an Opcity group that double-counts those people, unless the tenant's variants are disjoint.

### 6.3 Axcion already classifies lead type differently

`backend/app/services/scorecard_sympli.py` buckets Sisu `lead_type` (the transaction field, not the Follow Up Boss person source) with one regex: `realtor|realty\.com|opcity|market vip` → `realtor`, `zillow` → `zillow`, else `sphere`. That collapses Realtor.com and Realty.com, and it reads a different column. Do not reuse `lead_bucket` for this report.

## 7. Attribution

Cohort membership follows the Follow Up Boss person: month of `created`, current `source`, original assigned agent is not required for the close to count.

The Sisu join is: Follow Up Boss id under service `fub`, else email, else (only if the first contact has no transactions) every other Sisu contact with that email. Matching is for transactions, not for a single "this deal is this lead" row that the rest of Axcion uses.

Axcion's brokerage funnel (`backend/app/services/metrics.py`, `_funnel`) does not join them. It counts Follow Up Boss leads created in the period, excluding stage `Trash`, and counts Sisu appointments, under-contract, and closed deals by **their own dates**. A deal that closes in May from a February lead is a May close on the Axcion card and a February cohort close on the legacy report. Both numbers are real. They answer different questions. The port adds the cohort report; it does not replace `_funnel`.

Axcion company dollar, today (`integrations/sisu.py`, `team_income` and `enrich_commissions`): `agent_commission = gci - team_income`, and `team_income` prefers `commission_info.team_income` (the `external_type == 1` recipient). The legacy code says that field is before the EXP-risk adjustments. The legacy company dollar subtracts both EXP-risk lines and the `external_type == 2` agent payment, and adds the transaction fee. Those two numbers will not match. Store both, and label only the legacy formula "Company dollar" on this report.

`enrich_commissions` only runs for pending deals and for closed deals with `close_date` on or after 1 January of this year minus 31 days. A cohort back to February 2026 will miss commission lines on older closes once that window moves. This report needs commission-info for every closed transaction that joins to a lead in scope, not only the financial year.

`map_client` reads `trans_fee_amt` and then `sync.py` does not persist it. The column list around the transaction upsert has no `trans_fee`.

Appointment stages on the Axcion card are the exact strings `Appointment`, `Appointment Set`, and `Met` (`_APPOINTMENT_STAGES`). The legacy pipeline accepts any appointment row and several substrings. Do not widen the existing card as a side effect of this port.

## 8. Hard-coded Utah Life values

Put each of these on tenant or business configuration. Seed only the Utah Life business.

| Value | Where it lives now | Seed for Utah Life |
| --- | --- | --- |
| Tenant name | migration `0002_seed` | Match the existing Axcion tenant; do not create a second one |
| Allowlisted admin email | `0002_seed`, `connor.leiva@gmail.com` | Do not re-seed users |
| Source group `realtor_com` and its variants | `0003_lead_source_groups.py` | As in section 6.2, active |
| Source group `realty_com` | same | Inactive, empty variants, cost 0 |
| Monthly cost | `monthly_cost_dollars`, seeded 0 | Do not seed a dollar amount. Map QuickBooks lines (section 4.7). Seed a 50% cost share borne by `sympli` on `realtor_com` |
| Program start | `datetime(2026, 2, 1)` in `cohorts_live.py` | `2026-02-01` on the Realtor.com group |
| EXP risk defaults | `$69` team, `$49` agent | Those defaults on the brokerage, overridable |
| Sisu username fallback | `utah-life-real-estate-group` plus a hard-coded token in `sisu.py` | Do not copy. Use the connected integration |
| External service id | `fub` | Per-integration, default `fub` |
| Follow Up Boss deep link | `https://liveutah1.followupboss.com/2/people/view` | The account domain already stored by `fub_sync` |
| Pipeline industry percentiles and tier cutoffs | `pipeline.py` module constants | Start as product defaults. Move them to settings only if a second tenant needs different bands |
| Digest 8% / 10% floors | `benchmarks.py` | Phase 6, on the digest config |
| Timezone | digest default `America/Denver`; cohort keys were UTC | Cohort keys and pipeline windows use `IntranetWorkspace.timezone` via `lead.created_at_src`. Digest send timezone defaults to that same workspace zone |
| Focus-source list | `metrics.FOCUS_SOURCES` | Not used by the live tabs |
| ClickUp list, recipients, model | digest columns and `DIGEST_LLM_MODEL` | Phase 6, editable per source group. Seed one recipient, Connor Leiva, and leave the list id empty |

`scripts/sisu_dump_jason_urban.py` and the other `backend/scripts/sisu_*.py` files are one-off audits against named people and transactions. They are not product behavior.

## 9. What Axcion already has

### 9.1 Tenancy and auth

`Tenant` (`tenant` table): slug, name, status, plan, `config` JSON. `Business`: `key` such as `ulrg`, `archetype`, `gross_profit_label` (the brokerage label is already "Company Dollar" in the financial sense of the P&L, which is a different number from section 4.3). `User` roles `owner | admin | member`, tab grants, JWT. `deps.current_user` binds the session to the host's tenant. `Integration` is one row per provider per business, with encrypted tokens and `config` for non-secret state (`fub_state` lives there).

### 9.2 Follow Up Boss

`backend/app/integrations/fub.py`, `backend/app/services/fub_sync.py`.

Stored on `lead`: external id, stage, agent, `created_at_src` (workspace calendar date), `src_created_at` (full UTC timestamp), name, `origin` (the Follow Up Boss `source` string, truncated to 120), primary email, `contacted`, `last_activity_at`. `PERSON_FIELDS` is exactly `id, name, firstName, lastName, stage, source, contacted, assignedUserId, created, updated, lastActivity, emails`.

Also stored: Follow Up Boss users as `agent` (`source=fub`), and open tasks as `crm_task` for Needs You Today.

Not stored: calls, texts, email events, appointments, deals, price, duration, notes, assigned-to display string (Axcion uses `assignedUserId`).

Sync shape: a quick pass every `FUB_FOLLOWUPS_INTERVAL_MINUTES` (newest people, `lastActivityAfter`, uncontacted re-reads, tasks) and a full pass on `SYNC_INTERVAL_MINUTES` that backfills the whole account a few pages at a time (`worker.py`). The first live account is about 130,000 people. Any new activity sync has to stay inside that budget. Do not add a per-person fan-out to the quick tick.

### 9.3 Sisu

`backend/app/integrations/sisu.py`, `sync.sync_sisu`.

`transaction` stores external id, side, status (`active | pending | closed | dead`), GCI, `agent_commission`, sale price, address, buyer name, buyer email and a second email, phone, agent, contract date (`uc_dt`), close date, expected close, appointment set, appointment met, signed, lead date, listing date, `sisu_status_code`, `lead_type`. Status uses the same date fields as the legacy classifier, plus `status_code == "LOSTT"` as dead, which the legacy cohort classifier does not.

Company dollar is `gci - agent_commission` when commission-info was read, and that subtraction is `team_income`. Enrichment is limited to pending deals and recent closes (section 7). No EXP-risk lines, no agent-payment line, no adjusted GCI, no referral-fee line, no `trans_fee` column.

There is no foreign key from `transaction` to `lead`. Attribution elsewhere is by email (`lead.email` was added for that reason; see the `Lead` docstring in `models.py`).

### 9.4 Reporting UI

`frontend/src/CommandCenter.jsx` shows, per business, a four-step lead-to-close funnel from `metrics._funnel`: Leads, Appointments, Under contract, Closed. Periods are the dashboard periods (this month, last month, and the rest of `PeriodNav`), not cohort months. Transactions drill through `TransactionsPanel.jsx`.

The L10 scorecard (`frontend/src/ulrg/`, `backend/app/services/scorecard.py`, `routers/ulrg.py`) is a weekly measurable grid. It is not this report. `scorecard_sympli.lead_bucket` is the only lead-source grouping, and it is on Sisu `lead_type` for the lending board.

Ads (`backend/app/services/ads_funnel.py`, the Ads tab) is a Meta spend funnel. It has cohort-versus-event basis and ROAS. It does not know Follow Up Boss sources or Sisu company dollar.

Tabs are declared in `backend/app/services/tabs.py`. A new report should be a view on the brokerage business, or a platform tab only if it must span businesses. Utah Life's Follow Up Boss account is one business. Scope the report to that business.

### 9.5 Gap table

| Legacy feature | Legacy source | Axcion | Gap |
| --- | --- | --- | --- |
| Signed-in multi-tenant shell | `auth.py`, RLS | Host tenant, JWT, roles, businesses | Exists. Use it. Do not port RLS or the allowlist |
| Encrypted Follow Up Boss and Sisu credentials | `tenants.fub_api_key_encrypted` | `integration` tokens | Exists |
| People sync: id, stage, source, agent, created, email, contacted, last activity | fetched live, not stored | `lead` | Exists. `origin` is the source string |
| Open tasks | not in this dashboard | `crm_task` | Exists, unused by this report |
| Calls, texts, email events with direction, time, duration, note | `cohorts_live._fetch_person_payload` | not synced | **Missing.** Blocks speed-to-lead, contacted, stuck, What's Working |
| Appointments with start and outcome | same | not synced. Sisu has `appt_set_date` and `appt_met_date` on the transaction only | **Missing** for leads that never became a Sisu file |
| Follow Up Boss deals | same | not synced | Partial. Sisu covers closed and under contract. Offer and showing still need the Follow Up Boss stage or a deal row |
| Follow Up Boss `price` | person payload | not in `PERSON_FIELDS` | **Missing.** What's Working median price only |
| Lead-source groups, variants, match mode | `lead_source_groups` | none. Sympli `lead_bucket` is a different column and merges the two brands | **Missing** |
| Program start per source | hard-coded 2026-02-01 | none | **Missing** |
| Cohort tables, maturity, speed, uncontacted | `cohorts.py` | none | **Missing** |
| Sisu contact lookup by Follow Up Boss id and email, including duplicate contacts, and the id write-back | `cohorts_sisu._lookup_one`, `save_mapping_on_fallback=true` | bulk `get-team-clients` plus email on the transaction. No write to Sisu | **Partial.** Add the lookup and the same write-back |
| Company dollar formula, EXP risk, agent payment, trans fee | `cohorts_sisu.py` | `team_income` stored as the complement of `agent_commission`, recent deals only | **Partial.** Wrong formula, incomplete history, fee lines not stored |
| ROI, cost per lead, cost per close | flat `monthly_cost_dollars` per cohort month that had leads | Purchases and their expense-account lines are already synced (`books_sync._QBO_TXN_ENTITIES`, `book_txn_line.account_qbo_id`). Vendor id and the Purchase `Credit` flag are dropped. Nothing maps an account to a lead source | **Partial.** Seed account `365` and vendor `962`. Store the vendor id and the credit flag. No new entity type |
| Eight-stage pipeline, tiers, stuck, predicted close rate | `pipeline.py` | four-step period funnel | **Missing** |
| What's Working and 72-hour journeys | `pipeline_behavior.py` | none | **Missing** |
| Referral Flywheel drawer filtered by lead source | not in the legacy app | `AuditDrawer.jsx` plus `lineage.py` flywheel keys. Rows have no lead source | **Missing.** Phase 7. The cards stay unfiltered |
| CSV source funnel and coaching | `metrics.py` | not present, and hidden in legacy production | Do not port the files. Optional later, from stored events |
| Exports | browser CSV of the tables on screen | transaction drill export elsewhere | **Missing** for these tables |
| Settings UI for sources and cost | `LeadSourcesAdmin.jsx` | integration settings, not source groups | **Missing** |
| Weekly digest, LLM writeup, ClickUp tasks | `digest_*.py`. Recipients, list id, and model were database and env settings and are not in the export | none of this pipeline | **Missing.** Phase 6. Recipients, ClickUp list, and model are UI settings. Seed Connor Leiva as the only recipient |
| Request-time cache of the whole universe | `cohort_cache` | not needed if facts are in Postgres | Do not port the cache table. Aggregates must be cheap enough to compute per request, with a normal query cache only if a profile says so |
| Trash excluded from leads | not excluded in cohorts | `_funnel` excludes stage `Trash` | Keep both behaviors in their own reports |
| `status_code == LOSTT` counts as dead | not in the cohort classifier | `classify_status` in `integrations/sisu.py` | Use the legacy order for this report so parity holds |
| Deep link host `liveutah1` | `pipeline.py` | `fub_state.account_domain` | Use the stored domain |

## 10. Implementation plan

Work in this order. Each phase is shippable and leaves the existing funnel, scorecard, and syncs behaving as they do now.

Conventions to follow: async SQLAlchemy, Alembic revision after the current head, `tenant_id` on every new table, `business_id` where the row belongs to one brokerage, upserts through `backend/app/services/upsert.py`, secrets only on `integration`, tests in `backend/tests/` with the fakes in `fub_fake.py` and the Sisu tests. Add a tab through `tabs.py` rather than a one-off route outside the shell. Frontend lives under `frontend/src/` and uses the existing API client and auth. Match the brokerage page's type and color; do not import the legacy `styles.css` or its Playfair / DM Sans theme.

### Phase 1. Source-group settings

**Data.** New table `lead_source_group`:

- `id`, `tenant_id`, `business_id`
- `key`, `display_name`
- `variants` JSON list, `match_mode` `exact|contains|regex` default `contains`
- `program_start` date, nullable
- `is_active`, `display_order`
- `exp_risk_team_default` Numeric default 69, `exp_risk_agent_default` Numeric default 49
- unique `(tenant_id, business_id, key)`

No `monthly_cost` column. Spend rules and cost shares are the tables in section 4.7.

Seed, in the migration or in `seed_ulrg_scorecard.py`'s style of an idempotent data migration, only when a business with `key = 'ulrg'` exists: the two groups in section 6.2, Realtor.com `program_start = 2026-02-01`, Realty.com inactive. On `realtor_com`, seed the two spend rules in section 4.7 (account `365`, vendor `962`). When a business with `key = 'sympli'` exists in the same tenant, seed one `lead_source_cost_share` on `realtor_com`: bearer Sympli, `share_pct = 50`. Do not seed a class, a dollar amount, or a Sympli QuickBooks rule. Do not seed other tenants.

**API.** Under the existing integrations or settings router, business-scoped:

- `GET /api/v1/businesses/{business_id}/lead-sources` active groups, any user who can see the business
- `POST`, `PATCH`, `DELETE` for owner and admin. Delete is soft: `is_active=false` and prefix `key` with `deleted_`, matching `lead_sources.soft_delete_group`
- Validate key, match mode, and variant count the way `routes_admin.py` does
- `GET` and `PUT` of spend rules and cost shares for that group. Reject a rule with no account, payee, and class. Reject shares that sum past 100

**UI.** A section on the brokerage settings surface, not a new product tab yet. List, edit variants, edit program start, activate Realty.com, pick QuickBooks lines, and set the cost share. The cost control is the mapper in section 4.7, not a dollar field.

**Acceptance.** A second business can create `zillow` with its own variants and Utah Life's rows do not change. Utah Life's `realtor_com` row matches the seed list, has spend rules for account `365` and vendor `962`, and, when Sympli exists, carries a 50% share. A Purchase line on account `365` counts even when `vendor_qbo_id` is null. A line that matches both rules counts once. A line on Amex `1150040000` whose expense account is not `365` and whose vendor is not `962` does not count. A `purchase_credit` line on account `365` reduces the month. Saving a spend rule does not require a redeploy. Tests cover exact, contains, and regex, including that `Realtor.com` does not match `Realty.com` and that `Market VIP via Opcity BLA` matches the Realtor.com lead group. A rule with no account, vendor, or class is rejected. Two shares of 60 do not save. Vendor `1568` is not in the seeded rules.

### Phase 2. Sync the missing facts

**Follow Up Boss activity.** New table `crm_activity`, one row per external event:

- `tenant_id`, `business_id`, `source='fub'`
- `external_id` unique with tenant and source. Follow Up Boss ids can collide across calls and texts, so include the kind in the key: `(tenant_id, source, kind, external_id)`
- `kind` `call | text | email | appointment`
- `person_external_id` (the lead's `external_id`)
- `occurred_at` timestamptz
- `direction` `outbound | inbound | unknown`
- `is_live` bool, `duration_sec` numeric null, `has_note` bool
- `outcome` text null (appointments)
- `channel` `call | text | email | appt`

Populate `is_live` with the rules in section 4.5. Do not store the note text or the call recording. `has_note` is enough, and it is what the classifier uses.

New table `crm_deal` only if phase 4 cannot classify offer and showing from `lead.stage` plus appointments. Prefer stage plus appointments first; add deals if a parity sample shows offer counts short.

Add `price` to the lead only if phase 5 is in scope. It is a single numeric, not a reason to store the rest of the person payload.

**How to fetch without a 130,000-person fan-out.** On the full sync, walk `/calls`, `/textMessages`, `/emEvents`, and `/appointments` with `lastActivityAfter` or the endpoint's updated cursor if Follow Up Boss supports it on that route. Confirm the parameter against a recorded response in `fub_fake.py` before trusting it. If an endpoint cannot be walked by time, backfill by the lead ids already in scope for active source groups (created on or after the earliest group `program_start`, source matching a variant), and checkpoint the cursor in `integration.config["fub_state"]` the way the people backfill does. Never fan out on the quick tick.

Keep `PERSON_FIELDS` narrow for the people walk. Activity walks request their own fields.

**Sisu lines.** On `transaction`, add nullable columns: `trans_fee`, `adjusted_gci`, `team_income`, `agent_payment`, `exp_risk_team`, `exp_risk_agent`, `exp_risk_source` (`api | default`), `referral_fee_out`, `company_dollar`. Fill them in `enrich_commissions` using `_compute_company_dollar` from section 4.3. When the API returns no EXP line, apply the business's defaults and set `exp_risk_source=default`.

Widen enrichment for transactions that join to an in-scope lead, not only year-to-date closes. Keep the existing `agent_commission = gci - team_income` write so the current financial cards do not move.

**Linkage.** Add `lead_id` nullable on `transaction`, set when:

1. The Sisu client payload already carries an external id under service `fub` equal to `lead.external_id`, if `get-team-clients` returns it. Inspect one real payload before assuming it does. If it does not, call `GET /v1/contact/external` only for leads in an active source group that are still unmatched, and store the resulting Sisu contact id on the lead (`sisu_contact_id`). When the lookup has to fall back to email, pass `save_mapping_on_fallback=true`, matching the legacy client. Do not pass it when the id lookup already hit.
2. Else lowercased primary email equals `transaction.buyer_email` or `buyer_email2`.
3. If that email hits several Sisu contacts, attach the transactions from all of them, deduped by `transaction.external_id`. This is the duplicate-contact case in `_lookup_one`.

Index `(tenant_id, lead_id)` and `(tenant_id, business_id, buyer_email)`.

**Acceptance.** After a sync against the fakes: a call with duration 0 and a note is `is_live`; a call with duration 0 and no note is not; an outbound text is `try`; a person created before `program_start` is not required for the activity backfill. `company_dollar` on a fixture with GCI 10000, trans fee 295, EXP 69 and 49, agent payment 7000 equals `10000 + 295 - 69 - 49 - 7000 = 3177`. A fixture with no EXP lines uses 69 and 49 and `exp_risk_source=default`. Existing dashboard tests (`test_dashboard.py`, `test_sisu.py`, `test_fub_sync.py`) still pass. `agent_commission` for a deal that already had `team_income` is unchanged. The email-fallback request includes `save_mapping_on_fallback=true`. The id-hit request does not.

### Phase 3. Cohort report

**Service.** `backend/app/services/lead_source_report.py`. Pure functions over rows already in Postgres. Inputs: tenant, business, source-group key, optional cohort start and end. No HTTP to Follow Up Boss or Sisu on the request.

Port `aggregate_cohorts` and the Sisu rollup in section 4.2 and 4.3 without changing those formulas. The cohort key is `lead.created_at_src` formatted `%Y-%m` (workspace calendar), not the UTC month of `src_created_at`. Spend and ROI follow section 4.7, not the legacy flat fee.

Contacted and speed come from `crm_activity`, not from `lead.contacted`. Appointment set and held come from appointment rows plus the stage substrings in section 4.2. Closed, under contract, GCI, volume, and company dollar come from linked `transaction` rows classified with the **legacy** order (do not treat `LOSTT` as lost unless `lost_reason_id` or `archive_ts` is set; persist those two signals if they are not already implied by `status`).

People with no email stay in the denominator and in the unmatched list.

Return gross and net spend, both ROI multiples, and both net-profit figures from section 4.7. A month with QuickBooks spend and no leads still counts in the program total. Return the per-transaction audit rows and the unmatched list (cap the unmatched payload at 50, and return the total).

**API.** `GET /api/v1/businesses/{business_id}/lead-sources/{key}/cohorts?start=YYYY-MM&end=YYYY-MM`. 404 when the group is missing or inactive. 403 when the user cannot see the business.

**UI.** A report page on the brokerage, linked from the existing lead-to-close panel so the period funnel and the cohort report are visibly different. Source picker, month range, the three tables, the header KPIs (company dollar, gross spend, net spend, both ROIs), the audit drawers, Export CSV of the visible rows. Maturity badges follow `show_conversion`, `muted_conversion`, and `industry_comparable`. Empty Realty.com (inactive, or active with no variants) shows an empty state, not an error. A group with no spend rule shows the company-dollar figures and a mapping-needed state instead of `$0` ROI.

**Acceptance.** Given a fixture of ten February leads and the transactions in the company-dollar example, the February row matches leads, contacted, speed bucket, set, held, Sisu closed, and company dollar to the cent. A lead with `created_at_src` of 31 January is in `2026-01` even if `src_created_at` is 1 February UTC. A lead with stage `Trash` is still in `leads_received`. A `Realty.com` source is absent from the Realtor.com report. A February `book_txn_line` of `$4,000` on the mapped account, with a 50% share, yields gross spend `4000`, net spend `2000`, and ROI on each. A March line is not in the February row. A credit `posting_type` reduces the spend. No spend rule yields null ROI, not zero. Tests live next to `test_dashboard.py` and do not call the network.

### Phase 4. Team and agent pipeline

**Service.** Port `classify_stage`, cumulative counts, stuck, tiers, bottleneck, predicted close rate, and the speed histogram from `pipeline.py`. Use Sisu `company` state from the linked transactions (`closed` beats `under_contract` beats `lost` beats `active`). Date presets filter on `lead.created_at_src`, with the same five windows as `_pipeline_preset_specs` interpreted in the workspace calendar. Precompute on the server if that is simpler than shipping every lead to the browser; the legacy client did it only because the cache key could not afford a refetch. Axcion can aggregate in SQL or Python per request. Results must match the preset boundaries, including last-4-months as "the first of the month, rolled back four months" in `main.py`.

Stuck still uses time since `created`, not a stage-entered timestamp, until Follow Up Boss stage history is synced. Say that in the UI the way the legacy code comments it, in one sentence, so a 40-day-old "met" lead is not described as having sat in met for 40 days if that is only a proxy.

Deep links use `https://{account_domain}.followupboss.com/2/people/view/{id}` when `fub_state.account_domain` is present.

**UI.** Team page: funnel strip, coaching summary, tier bands, agent table, export. Agent page: highlights, vertical funnel, stuck list with stage filter and export, speed histogram. Follow the existing table and panel patterns in `CommandCenter.jsx` and `TransactionsPanel.jsx`.

**Acceptance.** An agent with one close out of 20 leads has predicted close rate 5.0, not the fallback product. An agent with zero closes and zero met-with rate has predicted close rate `0.55*0.65*0.40*0.50` = 7.2 after the percent rounding in `_predicted_cvr` (check the rounding against the function and assert the actual result). Pickup under 30 lands in critical even when every later stage is strong. A closed Sisu transaction forces stage `closed` even if the Follow Up Boss stage is `New`. Export CSV has one row per agent in the team table.

### Phase 5. What's Working

Port `compute_metric_set`, `build_journey`, and `build_whats_working_payload`. Same three framings. Gate median price on coverage the way `_price_coverage_pct` does (the comment says the row renders at or above 60%). Sync `price` in this phase if it was skipped in phase 2.

**Acceptance.** Team median is the median of the pooled leads, asserted against a case where the average of the agent medians differs. A duration-0 call with a note counts as live and moves `attempts_to_live`. Journey events past 72 hours clamp to the edge and set the continues flag. Sample sizes render next to each median.

### Phase 6. Weekly digest

Only after phases 3 and 4 match Utah Life on counts. Port the behavior of `digest_select.py` and `benchmarks.py`. Store the floor and target rates, the schedule, the recipients, the ClickUp list id, the ClickUp default assignee, the optional agent map, and the model name on a per-source digest config. Email through the existing Axcion mail path (`docs/resend-axcion-runbook.md`), not a second SMTP stack. The model defaults to `claude-sonnet-4-6` and is editable; an empty API key still takes the deterministic fallback in `digest_llm.py`. ClickUp runs only when that config's list id is non-empty. The token is a tenant secret, not a field in the spec and not a value copied from the legacy env. Unsubscribe must be honored.

**UI.** On the same settings surface as the source group: active flag, day and time, timezone (default the workspace zone), recipient list, max agents, minimum leads, include-reinforce, the six floor and target rates, ClickUp list id, ClickUp assignee, and model. Test send and delivery history, matching `DigestSettings.jsx`.

**Seed.** For the Utah Life tenant's `realtor_com` group only, one config with `is_active = false` and a single recipient: the tenant user whose name is `Connor Leiva`, otherwise the address `connor.leiva@gmail.com`. No other recipient. ClickUp list id null. Do not send on deploy.

**Acceptance.** A config set to Monday 06:00 in the workspace zone enqueues one job per week, a second tick does not send twice (unique on config + week ending), and a failed send retries at most three times. No ClickUp call when the list id is empty. The seeded row has one recipient and is inactive. Changing the model on the config is what the next send uses, not a redeployed env var.

### Phase 7. Lead source on the Referral Flywheel drawer

This can ship once phases 1 and 2 are in. It does not wait on the cohort page. The headline flywheel cards stay as they are. Only the drawer filters.

The drawer is `frontend/src/AuditDrawer.jsx`. The flywheel cards in `frontend/src/CommandCenter.jsx` call `onDrill` with these keys, and `backend/app/services/lineage.py` builds the rows:

| Key | What each row is |
| --- | --- |
| `flywheel_buyers` | A financeable ULRG buy-side closing |
| `flywheel_captured` | One of those that Sympli financed |
| `flywheel_uncaptured` | One of those financed elsewhere |
| `flywheel_agent_referrals` | That agent's Sympli-financed closings |
| `flywheel_vendor_no_loan` | Picked Sympli in Sisu, no funded loan |
| `flywheel_sympli_referred` | A funded Sympli loan credited to Utah Life |
| `flywheel_sympli_linked` | One of those matched to a ULRG closing |
| `flywheel_referral_no_deal` | One of those with no ULRG closing |
| `flywheel_zero_referrals` | An agent, not a deal |

**Attribution.** One function, shared with the cohort report, `group_for_deal(transaction, lead, groups)`:

1. If the transaction's linked lead has `origin`, test it with the group's matcher (section 6.2).
2. Else test `transaction.lead_type` with the same matcher.
3. If two active groups match, the one with the lower `display_order` wins.
4. Otherwise the row is `unassigned`.

Deal rows (`flywheel_buyers`, `flywheel_captured`, `flywheel_uncaptured`, `flywheel_agent_referrals`, `flywheel_vendor_no_loan`) get `lead_source_key` and `lead_source_label` from that function. Loan rows get them from the matched ULRG transaction when there is one, and `unassigned` when there is not. `flywheel_referral_no_deal` will usually be unassigned, because there is no ULRG deal to read. Do not invent a source from Arive.

`flywheel_zero_referrals` stays a list of agents. Do not put a lead-source chip on an agent. Do not filter that drawer.

**Payload.** On the deal and loan flywheel responses, add `lead_sources`: the active groups for the brokerage plus `{key: "unassigned", label: "Unassigned"}`, each with a count of rows. Each row carries `lead_source_key`.

**UI.** In `AuditDrawer.jsx`, when `d.lead_sources` is present and has more than one key with a count, show a chip row in the same style as the existing ULRG / Other filter (`srcFilter`). Default is All. Choosing Realtor.com shows only rows with that key and updates the "N records" line. The card the user clicked does not change. The chip label is the group's `display_name`, so a tenant without Realtor.com does not see that word.

**Acceptance.** A closing whose linked lead `origin` is `Realtor.com MVIP` appears under the Realtor.com chip and disappears when the drawer is set to Realty.com. A closing with no lead and no matching `lead_type` appears only under All and Unassigned. The Sympli 50% share is not computed in this drawer. The capture-rate card is the same number before and after the filter. `flywheel_zero_referrals` has no chips. A test builds the rows from fixtures and does not call Follow Up Boss, Sisu, or QuickBooks.

### Files to touch

Phase 1: a new Alembic revision, `backend/app/models.py`, a small service module, a router included from `backend/app/main.py`, settings UI, tests. Also `backend/app/services/books_sync.py` for `vendor_qbo_id` and `purchase_credit` on `book_txn`. Do not add a QuickBooks entity type. `Purchase` is already in `_QBO_TXN_ENTITIES`.

Phase 2: `backend/app/integrations/fub.py`, `backend/app/integrations/sisu.py`, `backend/app/services/fub_sync.py`, `backend/app/services/sync.py`, `backend/app/models.py`, Alembic, `backend/tests/fub_fake.py`, `test_fub_sync.py`, `test_sisu.py`.

Phase 3–5: `backend/app/services/lead_source_report.py` (new), a router, `frontend/src/CommandCenter.jsx` or a sibling report component, `frontend/src/api.js` if that is how calls are made, tests. Register the view in `tabs.py` if it is a nav tab. Spend reads `book_txn_line` only. Do not add a QuickBooks call on the report request.

Phase 6: worker job in `backend/app/worker.py`, mail, models for config and deliveries, settings UI. Do not start this in the same change as phase 3.

Phase 7: `backend/app/services/lineage.py` and `frontend/src/AuditDrawer.jsx`. This is the one change allowed inside the flywheel drawer. Do not change the capture-rate math in `metrics.py`.

Do not modify `metrics._funnel`, the scorecard resolvers, or the ads funnel except to link out to the new report.

## 11. Parity checks for Utah Life

Run these after phases 2 and 3, and again after phase 4, against the connected Utah Life integrations. Do not run them against seed data and call it parity.

1. Freeze a moment. Record the legacy payload of `GET /api/dashboard/cohorts?live=true&sisu=true` for `source=realtor_com` with no date filter, and the Axcion cohort payload for the same group. Compare as-of timestamps; they will not be identical, so rerun both within the same hour.
2. Per cohort month, these must match exactly: `leads_received`, `contacted_count`, `appts_set_count`, `appts_held_count`, `dead_count`, `active_count`. Speed-bucket counts must match exactly.
3. `avg_time_to_contact_minutes` within 0.1 minutes. Percents within 0.1.
4. Sisu `sisu_closed_count`, `sisu_under_contract_count`, `sisu_matched_count` exact. Unmatched ids listed on both sides, ignoring order.
5. Per closed transaction that both sides list: GCI, volume, trans fee, agent payment, and company dollar within `$0.01`. `exp_risk_source` agrees. Where both used the default, both used 69 and 49.
6. Header company-dollar total within `$0.01` of the sum of the cohort rows. Do not compare ROI or program cost to the legacy dashboard. The legacy figure is a flat fee times nonempty months. Axcion's figure is QuickBooks. Separately, for one finished month, gross spend equals the sum of the mapped `book_txn_line` amounts, and net spend is half of that while the Sympli share is 50.
7. A person whose source is `Realty.com` appears in neither report. A person whose source is `Market VIP via Opcity BLA` appears in the Realtor.com report.
8. Cohort month is the workspace date. A lead created at 11pm America/Denver on 31 January is `2026-01` in Axcion and `2026-02` in the legacy report. Count those boundary leads, list them, and do not file them as failures. Leads created between 00:00 and 17:00 Mountain should match the legacy month.
9. Pipeline, same window `all_time`: per agent, stage counts, tier, stuck count, pickup rate, and predicted close rate match. Then repeat for `rolling_30` and `ytd`.
10. The existing Command Center funnel for the same calendar month is **not** required to match the cohort row. Record both, and explain a difference that is the Trash exclusion or the date basis (section 7) rather than "fixing" the card.

The March 2026 file `validation/dashboard_validation_20260309_170730.md` is a failed CSV-versus-extract check for SOI. It is not an input to this plan.

Store the comparison as a script under `backend/scripts/`, read-only, printing a row per metric with match or delta. Do not commit Utah Life's payload.

## 12. Open questions

1. **Opcity spend versus Opcity leads.** Market VIP / Opcity leads count in the Realtor.com lead group. Vendor `1568` (Opcity) is not in the seeded spend rules. Confirm whether those Purchases should be added to the Realtor.com spend, left out, or given their own source group. Until then they stay out.
2. **Does `get-team-clients` already include the Follow Up Boss external id?** The legacy app does not use that endpoint. If the bulk payload has `external_ids` for service `fub`, phase 2 can link without `/v1/contact/external`. That has to be read off a live response; it is not in the mapper.
3. **Bare source `Realtor`.** `KNOWN_REALTOR_VARIANTS` probes it. The seeded group does not list it, so a contains-match on `Realtor.com` will not keep it. If Utah Life still receives leads with that exact source, add it to the seed variants.
4. **Realty.com variants.** The group is intentionally empty and inactive. The QuickBooks account is `579` and the vendor is `1311` (`Realty.com LLC`). Nobody has recorded which Follow Up Boss strings are Realty.com rather than Realtor.com, other than the CSV bucket test `realty.com` in the source. Do not invent variants, and do not seed the spend rule until the group is activated.
5. **Assigned agent.** The live fetch reads `assignedTo` through `_norm`, which stringifies whatever Follow Up Boss returns. Axcion resolves `assignedUserId` to `agent.name`. Parity of the agent table depends on those strings matching. Check one person whose assigned-to object is not a plain string.
6. **Stage history.** Stuck age is time since `created`, because stage-change history is not synced. If Follow Up Boss exposes it, a later improvement can replace the proxy. Do not block the port on it.
7. **ISA split.** Only the CSV coaching tab separates ISA calls from agent calls, and only from `users.csv`. The live pipeline does not. Confirm whether the port needs that split. It is not on the production tabs.
8. **Two company-dollar definitions in one product.** The P&L and `agent_commission` path should keep using team income. This report should use section 4.3. The UI has to name them so a brokerage owner does not add them together.
9. **`LOSTT` without `lost_reason_id`.** Axcion's Sisu status marks those deals dead. The cohort classifier does not. If Utah Life uses that code, the dead and active counts will disagree until the parity rule is confirmed.

## 13. What not to do

- Do not port the CSV extractor, `FUB_EXPORT_DIR`, or the three local-only tabs as a file drop.
- Do not call Follow Up Boss or Sisu from a dashboard request.
- Do not copy `styles.css`, the Utah Life wordmark, or the hard-coded Follow Up Boss host into shared components.
- Do not put Realtor.com variants, the February 2026 start, the $69 / $49 defaults, or the 50% Sympli share in code that runs for every tenant. Seed them for `ulrg` only.
- Do not type a monthly lead cost, and do not sum every account under `62130 Internet Lead Generation` into Realtor.com. Spend is account `365` or vendor `962`, plus any rule an admin adds.
- Do not match the Amex card account `1150040000`. That is the account the charge left, not the expense.
- Do not match on class or department.
- Do not filter the flywheel capture-rate cards. The filter is the drawer only.
- Do not change `metrics._funnel` to exclude or include Trash in order to match a cohort row.
- Do not store call notes, email bodies, or phone numbers. `has_note` and the primary email are the fields this report uses.
- Do not print or commit the fallback token in `roi-conversion-dashboard/fub_live_dashboard/backend/app/sisu.py`.
