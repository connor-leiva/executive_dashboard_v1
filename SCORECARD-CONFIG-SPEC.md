# Scorecard Configurability — Teardown & Build Spec

**Goal:** make the shared L10 Scorecard (the `springb` board rendered in Forum / beCollective / Edge,
and the `ulrg` board) **fully configurable per tenant from the Scorecard Settings UI** — offices,
office owners, measurables (add as well as remove), labels, measurement periods, goals per period,
and — the hard one — **what data auto-syncs into each metric** (pick a source + filters per metric,
e.g. "Forum → Members Added ← GHL filtered on x,y,z"; "Activated Agent → UCs ← Sisu filtered on x,y,z").

This document audits exactly how it's wired today and lays out what has to change. The short version:
**the structure layer is already data-driven and ~70% there; the data-routing layer is entirely
hardcoded and is a real build.** Everything below is grounded in the current code.

---

## 1. Current state at a glance

| What you want to configure | Where it lives today | In the Settings UI? | Gap |
|---|---|---|---|
| **Offices** (groups) | `scorecard_group` rows, created only by the **seed** | Edit owner only | **No add / remove / rename / reorder office** |
| **Office owner** | `scorecard_group.owner_name` + `owner_photo_ref` | ✅ `OfficeRow` (name + headshot) | Free-text name, not a linked user; otherwise fine |
| **Measurables** | `scorecard_metric` rows, created only by the **seed** | Rename ✅, Remove (soft) ✅ | **No "Add measurable"** |
| **Measurable labels** | `scorecard_metric.name` | ✅ `PATCH /ulrg/metric/{id}` | — |
| **Measurement periods** | `tenant.config.fiscal_quarters` (JSON) | ✅ `PeriodsEditor` | Shared **tenant-wide** — every board shares one period set |
| **Goals per period** | `scorecard_goal` (weekly + cumulative) | ✅ `GoalsEditor` | — |
| **Auto-sync / data routing per metric** | `scorecard_metric.resolver_key` → a **hardcoded Python function** | ❌ nothing | **The whole thing.** Source, dataset, filters, date field, aggregation, and office attribution are all baked into code. |
| **Which boards exist / who sees them** | `SCORECARD_SCOPES` dict, **hardcoded** (`routers/ulrg.py:49`) | ❌ | Two scopes hardcoded (`ulrg`, `springb`); not per-tenant data |

So six of the eight dimensions are already backed by per-tenant, per-business tables with endpoints —
the work there is **CRUD gaps**, not architecture. The seventh (routing) and eighth (boards) are
architectural.

---

## 2. The three layers, and which are data-driven

**Layer A — Structure (data-driven, good bones).**
`scorecard_group` → `scorecard_metric` → `scorecard_goal` / `scorecard_value`, all keyed by
`tenant_id` + `business_id`. `build_scorecard(tenant, business, weeks)` reads them generically and
computes the grid. Settings endpoints already mutate owners, names, active, periods, goals. Adding
"create office" / "create measurable" is **more of the same** — new rows, new endpoints, new UI.

**Layer B — Routing / auto-sync (hardcoded, the real lift).**
A metric opts into auto-sync by setting `resolver_key` (a string). That key maps to a Python function
in `RESOLVERS` (`services/scorecard_resolvers.py`). **Everything that makes the number is inside the
function**:
- the **source feed** (Sisu / GHL-recruiting / Arive) — implicit in which table it queries;
- the **dataset** (`Transaction`, `RecruitingCandidate`, …);
- the **filters** (`status == "closed"`, `sale_price > 0`, `status == "showed"`, …) — see
  `_window_filters` (`scorecard_resolvers.py:96`);
- the **date field** that anchors the weekly window (`close_date` vs `contract_date` vs
  `appt_met_date` vs `created_at_src` …);
- the **aggregation** (count, distinct-count, rate = numerator/denominator);
- the **office attribution** (`_office_agent_ids(sisu_group_id)` — Sisu-specific).

There are ~11 of these (`ulrg_homes_closed`, `ulrg_team_under_contract`, `ulrg_team_appts_met`,
`ulrg_team_signed`, `ulrg_team_sympli_attach`, `ulrg_team_meraki_attach`, `ghl_recruiting_new/held/
booked/signed_qtd`). Each is bespoke. **There is no way to express a new one except to write Python
and deploy.** That is the barrier to "configure what data syncs for each metric."

Note the model already has a `scorecard_metric.source` column (`sisu|fub|ghl|manual`) — but it is a
descriptive label only; nothing routes off it. The routing is 100% `resolver_key`.

**Layer C — Boards/scopes (hardcoded).**
`SCORECARD_SCOPES` maps `ulrg → real_estate business + [ulrg] tab` and `springb → membership business
+ [forum, becollective, edge] tabs`. Tenant-agnostic by *kind*, but the **set of boards and their tab
bindings is a code constant**, not tenant data. Fine for one customer; a blocker for "per tenant."

---

## 3. Dimension-by-dimension: what to change

### 3.1 Offices — add / remove / rename / reorder  *(Layer A, small)*
- **Have:** `PATCH /ulrg/group/{id}` (owner_name), photo upload, soft structure from seed.
- **Need:** `POST /ulrg/group` (create: key, name, sort_order, scope→business), `PATCH` to rename +
  reorder, `DELETE`/active flag to remove. `scorecard_group` has no `active` column — add one (mirror
  `scorecard_metric.active`) so an office can be hidden without destroying its metrics/history.
- **UI:** an "Offices" editor section with add / rename / drag-reorder / remove, next to the existing
  owner rows.
- **Watch:** `key` is `String(40)` and used as a stable handle; generate a slug, don't let it collide.

### 3.2 Office owner  *(done; one optional upgrade)*
Works. Optional: let an owner be a **linked user** (`owner_user_id` exists but the UI only sets
`owner_name`) so a headshot/name follows the person.

### 3.3 Measurables — **ADD** (the missing CRUD verb)  *(Layer A, small–medium)*
- **Have:** rename + soft-remove (`PATCH /ulrg/metric/{id}` with `name` / `active`).
- **Need:** `POST /ulrg/metric` (group_id, name, type `flow|rate|snapshot`, direction, note,
  sort_order). Straightforward — **until** you want the new row to auto-sync, which is §3.7.
- **UI:** "Add measurable" in `MeasurablesEditor`, picking the group + type.

### 3.4 Labels  *(done)*
`PATCH /ulrg/metric/{id} {name}`. No change.

### 3.5 Measurement periods — make them **per-board**  *(Layer A, small)*
- **Have:** `GET/PUT /ulrg/periods` → `tenant.config.fiscal_quarters`. One list, shared by **every**
  board in the tenant (ULRG and Spring B share it today).
- **Need:** scope periods to the board. Either key them under `tenant.config.scorecard_periods[scope]`
  or move to a `scorecard_period` table (tenant+business). The UI already passes `scope`; the endpoints
  ignore it for periods.

### 3.6 Goals per period  *(done)*
`scorecard_goal` + `GET/PUT /ulrg/goals?scope=` is already per-metric, per-period, per-board. No change
beyond following §3.5's per-board periods.

### 3.7 Auto-sync / data routing — **the core redesign**  *(Layer B, large)*
This is the bulk of the work. See §4.

### 3.8 Boards per tenant  *(Layer C, medium; needed for true "per tenant")*
Replace the `SCORECARD_SCOPES` constant with a `scorecard_board` table (tenant_id, slug, business_id /
kind, tab bindings, title). Settings then lets a tenant define its own boards. Not required for Spring
specifically, but it's what "fully configurable per tenant" means once there's a second customer.

---

## 4. The data-routing redesign (Layer B)

The aim: a metric's auto-sync is a **declarative spec** the admin builds in the UI, interpreted by
**one generic engine**, instead of a hand-written resolver. Four pieces.

### 4.1 The metric source spec (new)
Store a JSON spec on the metric (new column `scorecard_metric.source_spec JSON`, nullable). Shape:

```jsonc
{
  "source":   "ghl",              // sisu | ghl | ghl_recruiting | arive | manual
  "dataset":  "metric_record",    // which synced dataset within the source (see registry)
  "date_field": "occurred_on",    // the column that anchors the weekly window
  "filters": [                     // ANDed predicates over the dataset's whitelisted fields
    {"field": "kind",    "op": "eq", "value": "member"},
    {"field": "segment", "op": "eq", "value": "forum"},
    {"field": "status",  "op": "eq", "value": "active"}
  ],
  "aggregate": {"fn": "count"},    // count | count_distinct(field) | sum(field) | rate{num,den}
  "attribution": "none"            // none | office  (office → scope to the group's roster)
}
```

Connor's two examples land cleanly:
- **Forum → Members Added:** `source=ghl, dataset=metric_record, date_field=occurred_on,
  filters=[kind=member, segment=forum], aggregate=count`. **This data is already synced** (it's what the
  Forum tab reads) — so this one needs only the engine + UI, no new sync.
- **Activated Agent → UCs:** `source=sisu, dataset=transaction, date_field=contract_date,
  filters=[sale_price>0], aggregate=count, attribution=office`. This is literally the existing
  `ulrg_team_under_contract` resolver, expressed as data. Also already synced.

`resolver_key` stays during the transition as a legacy fallback: the engine runs `source_spec` if
present, else looks up `resolver_key` in the old registry. Migrate the 11 built-ins to specs, prove
parity (§4.5), then retire the registry.

### 4.2 The dataset registry (new — the foundational piece)
The UI can only offer choices the engine can safely execute, so each routable dataset must be
**described** once, in code: its model, which fields are filterable (+ type + allowed ops), which
columns are valid date anchors, and whether/how it supports office attribution. Grounded in what the
sync actually stores today:

| source | dataset (model) | date anchors | filterable fields | attribution |
|---|---|---|---|---|
| `sisu` | `transaction` | close/contract/appt_met/appt_set/signed/lead/listing_date | status, side, sale_price, mortgage_vid, title_vid, agent | **office** (agent→`sisu_group_ids`) |
| `ghl` | `metric_record` | occurred_on | kind, segment, status, amount, source, `meta.*` | business/segment (no sub-office yet) |
| `ghl_recruiting` | `recruiting_candidate` / `_appointment` / `_stage_event` | created_at_src, entered_stage_at, start_at, occurred_at | stage_group, status, pipeline_id, owner_seat | seat |
| `arive` | `metric_record` (kind=loan) | occurred_on | segment(funded), amount | — |

This table is the contract between the UI (what you can pick) and the engine (what it can run). It's
bounded and explicit on purpose — "fully customizable" means **"any combination the registry
exposes,"** and the registry grows as sync coverage grows (§4.6).

### 4.3 The generic engine (new; replaces the hardcoded resolvers)
One function: `(spec, tenant, business, group, week_start, week_end) → number | None | UNAVAILABLE`.
It looks up the dataset in the registry, builds the query (window on `date_field`, apply whitelisted
filters, apply attribution using the group's roster/office, aggregate), and returns the value. It must
**preserve the existing three-outcome contract** that cost weeks of history to get right
(`scorecard_resolvers.py:15-35`): a real `0`, a genuine `None`, and `UNAVAILABLE` when the feed is
disconnected / unsynced. The engine decides `UNAVAILABLE` from the source's liveness gate (the
registry supplies it, e.g. `_sisu_live`).

The runner (`run_resolvers`) changes only in how it gets a metric's callable: build a closure from
`source_spec` when present, else fall back to the registry. The per-(metric, week) isolation, the
look-back self-heal, and the `_write` overwrite rules all stay.

### 4.4 The routing UI (new)
In Settings, per measurable: **Source** dropdown → **Dataset** dropdown → **Date field** dropdown →
**Filter builder** (field / op / value rows, fields and ops driven by the registry) → **Aggregation**
→ **Attribution**. Plus a **live preview** ("this would be N for last week, from these K rows") that
calls the engine read-only before saving — the drill-down plumbing (`resolver_records`) already
returns the underlying rows, so preview is mostly reuse.

### 4.5 Migration & parity gate (don't skip)
Before deleting any hardcoded resolver, run the spec version and the Python version over the same
~13 weeks and assert they match (the repo's standing "validate before flip" rule, same as the
`validate_ulrg_resolvers.py` discipline). A derived test should also hold every registry field to the
model's real column (the SQLite-vs-Postgres and type-mismatch traps have bitten here before).

### 4.6 The honest constraint — routing is bounded by sync
You can only filter on data you've pulled. Today's syncs are **purpose-built**, not raw mirrors:
`metric_record` keeps name/email/amount/status/segment/occurred_on/`meta` — **not** arbitrary GHL tags
or custom fields. So "filter Members Added on [some GHL custom field]" may need the **sync** extended
to store that field (into `meta` or new columns) before the UI can offer it. The two metrics you named
are already covered; "everything, any filter" will surface sync gaps dataset by dataset. Plan for
registry + sync to grow together.

---

## 5. Per-tenant / productization notes
- `SCORECARD_SCOPES` (boards) and the `/ulrg/*` route prefix are tenant-agnostic by *kind* but still
  a code constant — fine for Spring, must become a `scorecard_board` table for a second tenant (§3.8).
- Periods are tenant-wide, not per-board (§3.5).
- Office attribution is **Sisu-only** (`_office_agent_ids`). GHL/other sources have no sub-office
  concept yet; on the Spring B board the "offices" (Forum/beCollective/Edge/Activated) are really
  **different feeds/segments**, so per-group routing there means *per-group source binding*, not
  splitting one feed by office. The spec in §4.1 handles both (a metric names its own source; an
  `attribution:office` metric additionally scopes to the group roster).

---

## 6. Recommended phasing

1. **Phase 1 — CRUD gaps (small, high value, independent):** Add measurable; office add/remove/
   rename/reorder (+ `scorecard_group.active`); per-board periods. Ships the visible "configure
   everything structural" without touching routing.
2. **Phase 2 — Routing engine (large):** dataset registry + `source_spec` column + generic engine +
   migrate the 11 resolvers onto specs behind the parity gate. No UI yet; specs seeded/validated.
3. **Phase 3 — Routing UI:** source/dataset/date/filter/aggregation builder + live preview.
4. **Phase 4 — Sync coverage:** extend each dataset's stored fields as routing demand surfaces
   (starting with the GHL fields Forum/beCollective actually filter on).
5. **Phase 5 — Boards per tenant:** `scorecard_board` table replacing `SCORECARD_SCOPES`.

Phases 1 and 2 are independent and can run in parallel. The two metrics you called out
(Forum Members Added, Activated UCs) are deliverable at the **end of Phase 3** with **no new sync**,
because their raw data is already stored.

---

## 7. Risks / must-not-break
- **The three-outcome contract** (`0` vs `None` vs `UNAVAILABLE`) must survive the engine rewrite — it
  is the thing that erased weeks of history when it was once two outcomes.
- **Filter safety:** whitelist fields + ops per dataset; never interpolate user input into SQL.
- **Parity before flip:** compare spec vs legacy over real weeks before retiring any resolver.
- **Migrations:** single Alembic head; `scorecard_group.active` + `scorecard_metric.source_spec` are
  additive.
- **Prod-vs-local:** verify on the Postgres prod build, not just SQLite (varchar length, dialect-only
  SQL, and seed-string limits have all bitten here).

---

## 8a. Shipped (Phase 1 — structural CRUD)

Live + browser-proven on the prod build (migration `0091_scorecard_group_active`):
- **Add / rename / reorder / remove offices** — `POST /ulrg/group`, extended `PATCH /ulrg/group/{id}`
  (name, sort_order, active). New `scorecard_group.active` soft-removes an office (metrics + history
  kept, restorable); `build_scorecard` and the goals editor filter it out. A new office **inherits its
  board's `is_team_room` convention** (ULRG offices get a Move card, Spring B's don't) — a hardcoded
  `True` otherwise left a spurious Move card on a Spring B office.
- **Add measurable** — `POST /ulrg/metric` (group, name, type, direction, note); starts manual /
  track-only. Rename + remove already existed.
- **Settings UI** — the Offices section gained per-office name editing, ↑/↓ reorder, remove, and an
  "+ Add office" row; the Measurables section gained an "+ Add" row (office + name + type).
- Group payload now carries `sort_order` (enables the reorder swap).
- Proven end to end in the browser (Forum → Scorecard → Settings on the Spring B board): add office →
  appears on the grid + selectable for measurables; add measurable → appears; reorder → grid + settings
  both reorder; remove → office + its measurable gone, cleanly. ULRG board unaffected (scope-isolated).

Still to do: §3.5 per-board periods (periods remain tenant-wide); §3.7 routing (below); §3.8 boards.

## 8. Bottom line
- **Structure (offices, measurables add, labels, periods, goals):** already data-driven; these are
  bounded CRUD additions (Phase 1). Call it the smaller half.
- **Routing (auto-sync per metric):** today it is hand-written Python per metric. Making it
  UI-configurable is a genuine subsystem — a declarative spec, a dataset registry, a generic engine,
  and a filter-builder UI (Phases 2–4) — but it is **tractable and incremental**, the model already
  has the hooks (`source`, `resolver_key`, the group/business scoping), and your two headline metrics
  need no new data sync to light up.
