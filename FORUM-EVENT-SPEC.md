# The Forum Event tab: audit, spec and build guide

Written 2026-10-05 from a nine-lens audit of this repository (models, sync, compute, API,
frontend, configuration, tests, the Forum tab, and the spec set itself) and a live read-only
probe of the Forum's GoHighLevel location `IT2T9rc5U89rz8YqPT1E` — pipelines, stage names, stage
counts, tag names, custom-field names and payment amounts. No member PII is copied into this
document. Nothing was written to GHL.

> **The ask:** a tab on the Forum that mirrors the beCollective "Launch" mechanics — track
> progress registering guests for the upcoming in-person event, track total VIP guests, and
> anything else worth monitoring. Completely multi-tenant and configurable, like everything else
> in the tool. Audit the beCollective architecture first, then write the phases.

This document is the plan and, as phases ship, the build record (§12).

**The headline finding.** The Forum already has event machinery. It is half-built, unreachable
from the UI, and structurally limited to **one event at a time** — syncing a new event deletes
the previous event's registrations (F1). The Event tab is therefore not a greenfield addition;
it is a replacement for something already there, and §8 Phase 1 is about retiring it safely.

---

## 1. What the Forum sells, and what GHL already holds

### 1.1 The funnel is already modelled

`01.1 - Forum Main Sales Funnel` (`gxSpXsniAtjBOnaix3CV`), 308 opportunities, is the funnel
Connor described, stage for stage. Live counts at the time of writing:

| Stage | Count | Maps to group |
|---|---:|---|
| Leads: (Qualifi) | 123 | `leads` |
| Leads: Attended Mastermind in the Past | 31 | `leads` |
| VIP Page: Opt-In | 23 | `optin` |
| **VIP Guest: Confirmed** | **18** | **`registered`** |
| Hot Nurture: Current VIP Event | 26 | `attending_nurture` |
| Sent Contract: Single - Monthly | 0 | `deciding` |
| Sent Contract: Single - PIF | 0 | `deciding` |
| Sent Contract: Dual - Monthly | 0 | `deciding` |
| Sent Contract: Dual - PIF | 1 | `deciding` |
| Payment Received: Fulfillment Started | 0 | `committed` |
| **Won: Onboarded** | **11** | **`converted`** |
| Hot / Warm / Cold Nurture: Upcoming VIP Event | 26+12+18 | `nurture` |
| Lost: DQ / Abandon | 45 | `lost` |

This is the same shape `classify_stage` already handles (`app/services/launch.py:58-70`), so the
grouping engine transfers rather than being rewritten.

**The contract stages carry two axes, not one.** `Single`/`Dual` crossed with `Monthly`/`PIF`.
beCollective's `payment_plan_map` is one-dimensional (`{"pif": [...], "plan": [...]}`,
`launch.py:38-39`). See §9 D4.

### 1.2 Events are identified by TAG, not by a field

All 18 contacts in `VIP Guest: Confirmed` carry the contact tag **`the forum q4 2026 guest rsvp`**.
Prior events used `q3 august 2026 guest vip comped`, `inner circle oct 2025`, `ic october 2025
guest`, `spring break 2025 vip`. There is no event field on the opportunity and no per-event
pipeline — one rolling funnel is reused every quarter, and the tag is what separates Q3 from Q4.

This matters because the `Launch` model **already has** `shift_reg_tag`, `shift_reg_tags`,
`shift_event_date`, `shift_goal` and `shift_pace_curve` (`models.py:1897-1902`), and
`compute_shift` (`launch.py:219-250`) already paces tag-matched registrations against a goal with
days-to-event, expected, gap and a `behind|onpace|ahead|done` state, plus comped detection
(`is_comp = any("comp" in t for t in matched)`, `sync.py:878`) and channel attribution. **The VIP
registration tracker is largely built. It is wearing a Shift-shaped name.**

### 1.3 VIP ticket revenue is not recoverable from payments

The Forum location has 395 transactions and 23 subscriptions. `$2,500` is ambiguous: of 50
succeeded `$2,500` charges, the sampled ones are *monthly membership dues* (recurring
`Subscription for …`), not guest tickets. And `monetaryValue` is `0` on 298 of the 308 funnel
opportunities.

> **Therefore: VIP guests are counted from the stage and the tag, and priced from tenant
> configuration.** This is exactly how beCollective counts seats and prices them from `price_map`
> (`launch.py:456-479`). Do not attempt to derive guest revenue from the payments API. §10 keeps
> the door open.

This is pinned by an existing test: `classify_stream("The Forum VIP Guest Ticket") ==
"event_tickets"` (`tests/test_billing.py:25`) — ticket money is deliberately **excluded** from
membership revenue. The Event tab must not double-count it into the Forum's MRR.

### 1.4 One live funnel, and one campaign that fed it

| Pipeline | Opps | Status |
|---|---:|---|
| `01.1 - Forum Main Sales Funnel` | 308 | **the live quarterly VIP funnel** — the only one this tab reads by default |
| `Q3 Direct Mailer - Guest Ticket` | 15 | a campaign-specific guest-ticket funnel that fed Q3 (created 2026-06-19 → 2026-07-28) |
| `Inner Circle Pipeline` | 128 | **retired** (Connor, 2026-10-05). Last opportunity created 2026-08-04; its "Dallas $2500 / Guest Ticket Comped" stages are history, not a second product |
| `(Renewals) Current Forum Members` | 47 | the member roster, staged by renewal month — not an event |

So `pipeline_match` is a **list**, not a string: the Direct Mailer precedent shows a campaign can
feed the same event alongside the main funnel. It is not a list because of Inner Circle, and
nothing in this build reads Inner Circle.

---

## 2. Audit: what exists today, and what it means for this build

Each finding has a citation. Findings marked **BLOCKER** must be resolved by a phase before the
tab can work at all.

| # | Finding | Evidence | Effect on this build |
|---|---|---|---|
| F1 | **BLOCKER.** Only one event can exist at a time, and syncing a new one destroys the previous one's registrations. The snapshot is `DELETE FROM metric_record WHERE tenant_id AND business_id AND source AND kind`, then insert. | `sync.py:423-444` (`_metric_snapshot`) | Event guests cannot live in `metric_record`. §4 gives them a real table with upserts. |
| F2 | **BLOCKER.** `active_launch_for` is scoped to `(tenant_id, business_id, is_active)` and nothing else. `Launch.program` is written and echoed but never filtered on — a repo-wide grep finds no `Launch.program ==`. | `launch.py:155-180`; `models.py:1868` | An Event stored as a `Launch` on the same business would collide with the beCollective cohort. §4 D1 uses a separate table. |
| F3 | **BLOCKER.** Four other callers bind silently to whatever `active_launch_for` returns: the public rep-desk share link, the whole Sales Desk, the opp snapshot and the Shift registrant sync. | `share.py:90`; `sales_desk.py:335`; `sync.py:725`; `sync.py:844` | Any change to launch selection is a four-surface blast radius. Another reason not to touch it. |
| F4 | **BLOCKER.** `_launch_tab` hardcodes the tab: `return "becollective" if (tabs and "becollective" in tabs) else b.key`. Every launch route on `springb` is gated behind the **beCollective** tab, not Forum. | `launches.py:37-40` | Event routes need their own resolver gated on `forum`. Copying `_launch_tab` inherits the bug. |
| F5 | `BrandScorecardTabs` is shared by the Forum **and** The Edge, and its tab list is a hardcoded two-item array. | `BrandScorecardTabs.jsx:8-16`; `CommandCenter.jsx:1264-1267` (Forum), `:1273` (Edge) | Adding an Event tab there leaks it into The Edge. §8 Phase 3 clones `BecollectiveView.jsx` into a Forum shell instead. |
| F6 | The Forum's existing `data.event` is a dead end: the "Event Readiness" `PulseTile` is the only tile with no `onClick`. | `ForumView.jsx` pulse tiles | Nothing to preserve in the UI. Replace, don't extend. |
| F7 | The Forum's event deck card is unreachable by construction — gated on `has("event") && data.event && !data.renewals`, and the Forum always has renewals. | `ForumView.jsx:459` | Dead code. Remove in Phase 1 with its test. |
| F8 | The pace machinery was built and never connected. `reg_count` rows are written once per sync day with `external_id = f"{event_tag}:{today}"`, `amount`, and `meta.days_out`. | `sync.py:682-706` | The history store already exists in spirit. §4 formalises it as `forum_event_weekly`. |
| F9 | That `reg_count` upsert is **not business-scoped** — the existence check filters `tenant_id, source, kind, external_id` with no `business_id`. | `sync.py:694-696` | A second business on the same tenant corrupts the first's counts. Fixed by the new table in §4. |
| F10 | `MetricRecord.external_id` is `String(64)` and `kind` is `String(32)`. `f"{event_tag}:{date}"` with a realistic tag is close to the limit. | `models.py:1590-1591` | Over-long strings pass SQLite and the whole suite, then truncation-error on Postgres. See the standing rule in §3. |
| F11 | Two different "Registered" numbers exist on two surfaces and disagree — the Forum KPI counts member-only registrations (`member_regs = len(all_regs) - guests`), another surface counts all. | `forum.py` registration split | §7 defines each metric once, server-side. The Event tab must not add a third. |
| F12 | `_recruiting.vipGuests` is two definitions behind one number: `vip = next((s["n"] for s in stages if "vip" in s["label"].lower()), guests or 0)` — a stage count **or** a tag count, whichever resolves. | `forum.py:417` | Do not reuse this number. §7 replaces it. |
| F13 | The "Recruiting Pipeline" pulse tile drills to the wrong thing — clicking the pipeline count opens the event-registration list. | `ForumView.jsx:894` | Pre-existing bug. Fix in Phase 1 while the file is open. |
| F14 | **Authorization hole.** `tab_for_metric` falls through to `"portfolio"` for any unknown key. An `event_*` lineage key with no branch is readable by anyone granted Portfolio. | `tabs.py:157-160, 238-239, 251` | §8 Phase 2 adds an explicit `event_` branch **and** a test that asserts the fallthrough is not reached. |
| F15 | The stage-group vocabulary is duplicated in **five** places that must change together: `GROUPS`, `DEFAULT_STAGE_MAP` keys, `_GROUP_ORDER`, the frontend `STAGE_GROUPS` rows, and the drawer's merge. | `launch.py:18-19, 22-39, 56-57`; `LaunchSection.jsx` | The Event tab defines its own vocabulary in **one** module and derives the rest. §4.3. |
| F16 | `goal_basis` is `String(8)` and `pace_model` is `String(10)`. A value like `"vip_seats"` (9) or `"guest_count"` (11) passes SQLite and the entire test suite, then truncation-errors on Postgres. | `models.py:1874, 1885` | Every new enum column in §4 declares a width with headroom and is listed in §3. |
| F17 | Thirteen launch config columns are PUT-able with no UI field, and two (`payment_plan_map`, `won_grace_days`) are accepted by the schema but not emitted by `config_out` — a future editor reading config would silently blank them. | `launch.py:412-433`; `schemas.py:604, 617` | §5 requires every Event config field to be in the Out schema, the Upsert schema **and** the drawer, with a test asserting the three agree. |
| F18 | A `PUT` replaces JSON columns whole; the merge is done in the browser. | `launches.py:69` (`setattr`); `LaunchSection.jsx:493-494` | Same pattern is fine, but §5 states it explicitly so a future API client does not blank a map. |
| F19 | The only UI that creates a launch posts Spring's cohort verbatim — `AUGUST_TEMPLATE` hardcodes `pipeline_match`, `cohort_value`, ticket prices and `shift_goal`. | `LaunchSection.jsx:31-42` | The Event create form must be empty-by-default with per-field help, not a Spring template. §6. |
| F20 | Nothing enforces one active launch per business — no unique index on `(business_id, is_active)`. | `0018_becollective_launch.py:74` | Deliberate for events: **many** events are active-in-history. §4 indexes `(tenant_id, business_id, status, starts_on)` instead. |
| F21 | `_renewals` and `_event` read `dt.date.today()` inside the function body even though `build_forum` threads `today` everywhere else. | `forum.py:476` and two sites in `_event` | Every Event compute entry point takes `today=`/`now=`. Non-negotiable; the suite time-travels. |
| F22 | The assistant packs context for `forum`, `becollective` and `binder` only; there is no launch payload at all. | `assistant.py:74-80`; `TAB_LEGEND:28-36` | The Ask panel will not see the Event tab unless Phase 6 adds it. |
| F23 | `create_rep_share` does not enforce the plan's share-link cap, while the scorecard route does and 402s. | `launches.py:337-356` vs `ulrg.py:216-224` | If Event adds a share link, it checks `plans.over_limit`. §5. |
| F24 | The nav-tab list is asserted as an exact ordered list, and the Forum deck-card keys are asserted as an exact set. | `tests/test_platform.py:68-69`; `tests/test_forum.py:158` | Both tests must be updated in the same commit as the change that breaks them, not after. |

---

## 3. Per workspace and per agent

Everything below is per workspace. Nothing in this build may reference a business key, a tag, a
pipeline name or a price as a literal.

| What | Configured in | Per agent |
|---|---|---|
| Which business owns events | `roles.membership(s, tenant_id)` — the lowest-`sort_order` business whose `kind == "membership"` (`roles.py:67-89`). Never `Business.key == "springb"`. | n/a |
| Which pipelines feed an event | `forum_event.pipeline_match` (list of case-insensitive substrings) | n/a |
| Which stages mean what | `forum_event.stage_map` — `{group: [substring, …]}`, same shape as `DEFAULT_STAGE_MAP` | n/a |
| Which tag identifies this event's guests | `forum_event.reg_tags` (list) | n/a |
| Which tag marks a comped guest | `forum_event.comp_tag_match` (default `"comp"`, matching `sync.py:878`) | n/a |
| VIP ticket price, and what a seat is worth | `forum_event.vip_price`, `forum_event.price_map` | n/a |
| Registration goal and pace curve | `forum_event.guest_goal`, `pace_curve`, `pace_tolerance` | n/a |
| Event dates | `forum_event.starts_on`, `ends_on`, `window_start`, `window_end` | n/a |
| Timezone for day boundaries | `forum_event.default_tz` (default `"America/Denver"`) | n/a |
| Who can see the tab | the `forum` tab grant, via `assert_tab` | yes — per user |
| Who can edit the event | `require_role("owner","admin")` | yes — per user |

**Standing rules this build must follow.** Each has bitten before.

| Rule | Where it's enforced | Why |
|---|---|---|
| Every new `String(n)` column is checked against its longest realistic value | §4 column tables state the limit and the longest value | F16; SQLite ignores varchar length, Postgres truncation-errors in prod |
| One alembic head before shipping | `alembic heads` must print exactly one | a fork crash-loops Railway |
| Every compute entry point takes an injected clock | §7 signatures | F21; the suite time-travels to prove pace |
| Brand tokens only — `T` and `alpha()` from `theme.js`, no local hex, no font imports | `tests/test_brand_*` | the reskin is centrally owned |
| No global `box-sizing: border-box` reset | `index.html:28-29` and its test | adding one moves a great deal of layout |
| A JSON column is copied before mutation | ORM dict alias | mutating the loaded dict makes SQLAlchemy skip the UPDATE silently |
| Tests use the injected clock and scope every `delete()` by tenant | §8 Phase 0 | the existing launch fixtures delete across tenants (`tests/test_launch.py:40`) |

---

## 4. Data model (migration `0090_forum_event`)

Current head is `0089_onboarding_reader`; the new revision chains from it. Copy the structure of
`0088_onboarding.py` — explicit `nullable=False`, `JSONType` passed **bare** (it is already an
instance, `dbtypes.py:44-46`), composite indexes created after the tables and guarded by name,
children dropped before parents.

### 4.1 Why not reuse `launch`

Considered and rejected. `Launch` has the right *shape* — window, event date, goal basis, seat
goal, pipeline match, stage map, price map, registration tags, pace curve — and reusing it would
save a table. It fails on three counts, each a BLOCKER in §2:

- `active_launch_for` has no program discriminator (F2) and four callers bind to it (F3), so an
  Event row on the membership business would be served as the beCollective cohort to the Sales
  Desk, the opp sync, the Shift sync and the public rep-desk link.
- `_launch_tab` would gate Forum events behind the beCollective tab (F4).
- Events recur quarterly and must be **comparable across quarters**; `Launch` is built around one
  active row (F20), and the `metric_record` snapshot behind it is delete-then-insert (F1).

Making `Launch` program-aware is a larger, riskier change to a shipped revenue surface than
adding a table. If Connor prefers the merge, §9 D1 is where to say so.

### 4.2 `forum_event` — one row per event, the whole config

| Column | Type | Notes |
|---|---|---|
| `id` | `GUID()` PK | |
| `tenant_id` | `GUID()` FK `tenant.id` CASCADE, indexed, `nullable=False` | |
| `business_id` | `GUID()` FK `business.id` CASCADE, indexed, `nullable=False` | the membership business |
| `name` | `String(120)` | "The Forum Q4 2026" |
| `slug` | `String(64)` | URL key; unique per `(tenant_id, business_id)` |
| `status` | `String(16)` | `draft` \| `selling` \| `running` \| `closed`. Longest value 7; width 16 for headroom (F16) |
| `starts_on` / `ends_on` | `Date` | the event itself |
| `window_start` / `window_end` | `Date` | the selling window; drives pace and "which event is current" |
| `venue` | `String(120)`, nullable | display only |
| `default_tz` | `String(40)` | default `"America/Denver"` |
| `pipeline_match` | `JSONType` | list of substrings, e.g. `["forum main sales funnel", "direct mailer"]` |
| `stage_map` | `JSONType` | `{group: [substring, …]}` — see §4.3 |
| `reg_tags` | `JSONType` | list of contact tags, e.g. `["the forum q4 2026 guest rsvp"]` |
| `comp_tag_match` | `String(32)` | default `"comp"` |
| `guest_goal` | `Integer` | the VIP-guest target |
| `vip_price` | `Numeric(12,2)` | $2,500 |
| `price_map` | `JSONType`, nullable | membership conversion pricing, keyed by §4.4 |
| `member_goal` | `Integer`, nullable | optional conversion target |
| `pace_curve` | `JSONType`, nullable | `{days_to_event: cumulative_fraction}`, same shape as `DEFAULT_SHIFT_CURVE` |
| `pace_tolerance` | `Numeric(5,4)` | default `0.08` |
| `is_active` | `Boolean` | default `True` |
| `created_at` / `updated_at` | `DateTime(timezone=True)` | server defaults |

Indexes: `ix_forum_event_tenant_id`, `ix_forum_event_business_id`, and composite
`ix_forum_event_scope` on `(tenant_id, business_id, status, starts_on)`. Unique constraint
`uq_forum_event_slug` on `(tenant_id, business_id, slug)`.

**No unique index on `(business_id, is_active)`** — many events coexist by design (F20).

### 4.3 The group vocabulary, defined once

In a new `app/services/forum_event.py`, not spread across five files (F15):

```python
GROUPS = ("leads", "optin", "registered", "attending", "deciding",
          "committed", "converted", "nurture", "lost", "uncategorized")

DEFAULT_STAGE_MAP = {
    "leads":      ["leads:"],
    "optin":      ["vip page: opt-in", "opt-in", "opt in"],
    "registered": ["vip guest: confirmed", "ticket purchased", "purchased"],
    "attending":  ["hot nurture: current vip event", "attended"],
    "deciding":   ["sent contract"],
    "committed":  ["payment received"],
    "converted":  ["won: onboarded", "onboarded", "completed investment"],
    "nurture":    ["nurture: upcoming", "invite to next", "previously attended"],
    "lost":       ["lost", "dq", "abandon", "no deposit"],
}

# A won opp must resolve as `converted` even though its stage text may also match `nurture`.
GROUP_ORDER = ("converted", "committed", "deciding", "attending", "registered",
               "optin", "nurture", "lost", "leads")
```

The frontend reads the group list from the payload. It is never re-declared in JSX.

### 4.4 Payment types — the Single/Dual axis (F; §1.1)

`price_map` is keyed by the four contract stages as they exist in GHL, so no inference is needed:

```jsonc
{
  "Single - PIF":     {"acv": 12000, "upfront": 12000},
  "Single - Monthly": {"acv": 14400, "upfront": 1200, "monthly": 1200, "months": 12},
  "Dual - PIF":       {"acv": 20000, "upfront": 20000},
  "Dual - Monthly":   {"acv": 24000, "upfront": 2000, "monthly": 2000, "months": 12}
}
```

`party_size` is derivable from the key prefix for reporting ("how many seats did Dual sell?") and
is **not** a column.

**These ship unset.** `price_map` is nullable and `vip_price` has no default — the numbers above
are illustrative only, and no migration seeds them. Pricing is configuration, entered in the
drawer when somebody cares. The tab must be fully useful before anyone does; see §9 D8.

### 4.5 `forum_event_guest` — one row per guest, upserted (fixes F1, F9)

This is the table that makes multiple events possible. Rows are **upserted by key, never
snapshot-deleted**, so Q3's guests survive Q4's sync.

| Column | Type | Notes |
|---|---|---|
| `id` | `GUID()` PK | |
| `tenant_id` | `GUID()` FK CASCADE, indexed, `nullable=False` | |
| `event_id` | `GUID()` FK `forum_event.id` CASCADE, indexed, `nullable=False` | |
| `contact_id` | `String(64)`, indexed | GHL contact id |
| `opportunity_id` | `String(64)`, nullable, indexed | may be absent — a tagged contact with no opp is still a guest |
| `name` | `String(160)`, nullable | |
| `stage` | `String(120)`, nullable | raw GHL stage text, kept verbatim |
| `group` | `String(16)` | resolved via `stage_map`; longest value `uncategorized` = 13 |
| `is_comped` | `Boolean` | default `False` |
| `channel` | `String(32)`, nullable | reuse `classify_shift_source` (`launch.py:81-97`) |
| `invited_by` | `String(160)`, nullable | from the contact field `Guest Invited By`, or the opp field `Referred By` |
| `rep_email` | `String(160)`, nullable, indexed | from the opp field `Sales Rep` |
| `payment_type` | `String(24)`, nullable | one of the §4.4 keys |
| `registered_on` | `Date`, nullable | **see §7 note on dating** |
| `converted_on` | `Date`, nullable | set when the opp reaches `converted` |
| `first_seen_at` | `DateTime(timezone=True)` | server default |
| `last_seen_at` | `DateTime(timezone=True)` | bumped every sync |

Unique constraint `uq_forum_event_guest` on `(event_id, contact_id)`.
Composite index `ix_forum_event_guest_group` on `(event_id, group)`.

### 4.6 `forum_event_weekly` — the history store (formalises F8)

Mirrors `launch_weekly` (`models.py:1917-1931`), upserted once per ISO week per event:
`id`, `tenant_id`, `event_id`, `week_start (Date)`, `optins`, `registered`, `converted`,
`registered_cum`, `captured_at`. Unique on `(event_id, week_start)`.

---

## 5. API

All routes live in a new `app/routers/forum_events.py`, mounted at `/api/v1` beside
`launches.py`. They are keyed on a path business, like the launch routes.

```jsonc
// GET /businesses/{key}/events/current   → 404 when none, which the hook treats as "tab absent"
{
  "event": {                       // echo of the editable config (one `config_out` function)
    "id": "…", "name": "The Forum Q4 2026", "slug": "q4-2026",
    "status": "selling",
    "starts_on": "2026-11-12", "ends_on": "2026-11-14",
    "window_start": "2026-08-01", "window_end": "2026-11-12",
    "venue": "Scottsdale, AZ",
    "guest_goal": 60, "vip_price": 2500,
    "reg_tags": ["the forum q4 2026 guest rsvp"],
    "pipeline_match": ["forum main sales funnel"],
    "stage_map": { /* … */ }, "price_map": { /* … */ },
    "pace_curve": { /* … */ }, "pace_tolerance": 0.08,
    "default_tz": "America/Denver"
  },
  "registration": {                // server-computed, §7
    "goal": 60, "guests": 18, "paid": 16, "comped": 2,
    "pct_to_goal": 0.3,
    "days_to_event": 38,
    "expected": 27, "expected_pct": 0.45, "gap": -9, "state": "behind",
    "curve": [{"d": 38, "pct": 0.45, "count": 27}, /* … */],
    "sources": {"total": 18, "paid": 4, "organic": 12, "comped": 2,
                "channels": [{"key": "meta", "label": "Meta", "count": 4, "pct": 22}]}
  },
  "funnel": [                      // ordered, one row per group, each drillable
    {"key": "optin", "label": "Opt-in", "owner": "marketing", "count": 23, "tag": null},
    {"key": "registered", "label": "VIP guests", "owner": "setters", "count": 18,
     "tag": "16 paid · 2 comped"},
    {"key": "deciding", "label": "Contract sent", "owner": "closers", "count": 1,
     "tag": "$24K on the table"},
    {"key": "converted", "label": "Members", "owner": "onboarding", "count": 11, "tag": null}
  ],
  // Shown here for an event somebody has PRICED. With vip_price and price_map unset - the
  // shipping default - "revenue" is {"ticket_booked": null, "member_arr": null, ...} and the
  // "deciding" funnel tag above is null. Nothing else in this payload changes. See §9 D8.
  "revenue": {                     // ticket money and membership money, kept APART (see §1.3)
    "ticket_booked": 40000,        // paid guests x vip_price — never added to Forum MRR
    "member_arr": 143000,
    "member_goal": 20, "members": 11,
    "conversion": {"guests": 18, "converted": 11, "rate": 0.611}
  },
  "momentum": {                    // this week vs last, from forum_event_weekly
    "registered": {"now": 5, "was": 2}, "converted": {"now": 1, "was": 3}
  },
  "warnings": ["3 opps unmapped stage"],
  "as_of": "2026-10-05T17:00:00+00:00"
}
```

**Endpoints.**

- `GET /businesses/{key}/events` — list, newest first. Read: `assert_tab(user, s, "forum")`.
- `GET /businesses/{key}/events/current` — the event whose window contains today, else the
  nearest upcoming. **404 when none** — never an empty shell (`launches.py:89` precedent).
- `GET /businesses/{key}/events/{event_id}` — one event, same payload.
- `GET /businesses/{key}/events/{event_id}/drill/{metric}` — the drill drawer, returning the two
  existing shapes (`type: "records"` / `type: "calc"`) so `DrillRecords` is reused unchanged.
- `POST /businesses/{key}/events` — create. `require_role("owner","admin")` + `audit(...)`.
- `PUT /businesses/{key}/events/{event_id}` — partial patch. Same guard, same audit.

**Rules carried over from the audit.**

- Resolve the business from the path key scoped to the caller's tenant (`_biz`,
  `launches.py:43-48`), then re-resolve the event from that business on **every** sub-route —
  checking the tab on one business and then reading a child row by id alone was a real bug
  (`launches.py:204-206`).
- Tab first, role second, as `link_recordings` does (`launches.py:182`).
- `_DATE_FIELDS` / `_NUM_FIELDS` style coercion in one declarative place; a new date field that
  is not added to the set is stored as a raw string (`launches.py:30-32, 60-69`).
- Upsert schema all-optional; the required set is validated in POST only.
- JSON columns are replaced whole by PUT; the drawer merges (F18). State it in the schema
  docstring.
- Every config field appears in the Out schema, the Upsert schema **and** the drawer, with a test
  asserting the three agree (F17).
- If a share link is added later, it checks `plans.over_limit` and 402s (F23).

---

## 6. The Event tab (UI)

**Mount.** Do not touch `BrandScorecardTabs` — it is shared with The Edge (F5). Create
`frontend/src/ForumShell.jsx`, cloned from `BecollectiveView.jsx:26-83`: build a tab list for the
shared `SubTabs.jsx`, pick a pane by string key, pass `role` and `onSaved` down. Tabs:
`Overview | Event | Scorecard`. The Event tab is **conditionally present** — absent when
`/events/current` 404s, exactly as the Launch tab is absent without a launch.

Then change `CommandCenter.jsx:1264-1267` to mount `ForumShell` for the Forum and leave
`:1273` (The Edge) on `BrandScorecardTabs`. While there, pass the props the Forum mount currently
drops: `role`, `drillBusiness` (F; audit).

**The section.** `frontend/src/EventSection.jsx`, one file, at `frontend/src/` root. Public shape
copied verbatim:

```js
export default function EventSection({ data, usingSample, role, businessKey = "springb", onSaved })
```

`const isEditor = !role || role === "owner" || role === "admin";` — the `!role` clause means
sample/offline renders as editable.

**The hook.** `frontend/src/useEvent.js`, a hand-rolled `useEffect` copied line-for-line from
`useLaunch.js:10-46`. There is no React Query in this app. The contract is load-bearing: no
`VITE_API_BASE` renders the bundled sample; **404/403 means absent, not broken**; 401 is
swallowed because a global listener handles session expiry; and `loading: !data && !error &&
exists`.

**The sample.** `frontend/src/sampleEvent.js`, a default-exported plain object whose header
comment names the endpoint it mirrors and the scenario it depicts. Make its numbers agree with
this spec — the Launch brief and `sampleLaunch.js` disagree today, and that cost a review cycle.

**Layout**, top to bottom:

1. **Registration to goal** — the hero. Guests against `guest_goal`, days to event, the pace bar
   with expected vs actual, and the `behind | on pace | ahead | done` state. This is the "are we
   going to fill the room" number.
2. **VIP guests** — total, split paid vs comped, and the acquisition-channel breakdown.
3. **The funnel** — one row per group, each count a drill target.
4. **Conversion** — guests → members, the rate, and membership ARR. Ticket money is shown
   **beside** it, never summed into it (§1.3).
5. **Momentum** — this week vs last, from `forum_event_weekly`.
6. **Settings drawer** — the gear button, cloned from `LaunchSection.jsx:449-633`: a local
   editable copy, one `putJSON` of the whole patch, `onSaved()`, `onClose()`. Save is gated on
   `canPersist = isEditor && !usingSample` and says **why** when off — it reads "Read-only" with
   a title. Do not hide it.

**Drill.** Declare `const DrillCtx = createContext(null)` and a `<Num metric="…">` wrapper
(`LaunchSection.jsx:15-26`) — every figure is a drill target via context, never prop-threading.
The `.num` affordance is an inset box-shadow underline on hover and a petal focus ring, not a
colour change. Reuse `DrillRecords`, and pass it `businessKey` and `tz` — `LaunchDrawer` drops
both today (`LaunchSection.jsx:1317`), which is why recording links render with the wrong
business.

**CSS.** Scope `.drx-*` under the section's own root class. `SalesDeskSection.jsx:370-397`
re-declares those class names **unscoped**, which is a latent collision; do not add a third copy.

---

## 7. Metric definitions (server-side, one function each, unit-tested)

Each takes an injected clock (F21). No metric is computed in the browser.

| Metric | Definition |
|---|---|
| `guests` | count of `forum_event_guest` for the event whose `group` is in `("registered","attending","deciding","committed","converted")` — i.e. once confirmed, always a guest, even after converting |
| `paid` | `guests` where `is_comped is False` |
| `comped` | `guests` where `is_comped is True` |
| `pct_to_goal` | `guests / guest_goal`, 4dp, `0.0` when goal is 0 |
| `days_to_event` | `days_between(today, starts_on)`; `None` before the event is dated |
| `expected` | `round(curve_expected(pace_curve, days_to_event) * guest_goal)` — reuse `launch.py:126-148` verbatim, including `int(k)` on the string keys |
| `gap` | `guests - expected` |
| `state` | `pending` if no date, `done` if `days_to_event < 0`, else `behind` / `ahead` when `abs(gap) > pace_tolerance * guest_goal`, else `onpace` |
| `ticket_booked` | `paid * vip_price`, or `None` when `vip_price` is unset. **Never** added to membership revenue (§1.3) |
| `converted` | guests whose `group == "converted"` |
| `conversion_rate` | `converted / guests`, `None` (dash, never 0) on an empty denominator — §7 convention at `sales_desk.py:430-431` |
| `member_arr` | `None` when `price_map` is unset. Otherwise `sum(price_map[t].acv * count(t))` over `converted` guests by `payment_type`; guests whose type we cannot price still count as **seats** and are priced at the blended rate, with a warning naming how many |
| `funnel[group].count` | `count(forum_event_guest where group == g)` |
| `momentum.*` | this ISO week's `forum_event_weekly` row vs the prior week's |

**Every money metric is optional; no count metric depends on one.** `guests`, `paid`, `comped`,
`pct_to_goal`, `expected`, `gap`, `state`, `converted`, `conversion_rate` and the whole funnel are
computed with `vip_price` and `price_map` null. A revenue figure with no price renders as an em
dash with a "not priced yet" title — never `$0`, which reads as a result rather than an absence.
This is the same convention as `_rate` returning `None` on an empty denominator
(`sales_desk.py:430-431`).

**A note on dating a registration.** `registered_on` is set from the contact's `dateAdded`, which
is when the *identity* first appeared, not when they registered — GHL exposes no per-tag
timestamp. This is the same deliberate trade `bc_shift_reg` makes, and the comment at
`sync.py:880-894` explains the cost: somebody already on the list who registers later lands in the
cohort they arrived in. For a quarterly event with a selling window this reads oddly at the
edges. §9 D5.

---

## 8. Phases

Each phase ends in a **Done when** that is checkable from outside the code. "Tests pass" is never
one.

### Phase 0 — Fixture hygiene (no product code)

The existing launch fixtures `delete()` across tenants with no WHERE clause
(`tests/test_launch.py:40`, `:336`; `tests/test_sales_desk.py`), and the whole suite shares one
SQLite file. Adding event tests to that will produce order-dependent failures that look like
Event bugs.

- Scope every `delete()` in the launch and sales-desk fixtures by `tenant_id`.
- Add the `forum_event*` tables to the same discipline before writing a row.

**Done when** the launch and sales-desk suites pass when run in isolation *and* in reverse order
(`pytest -p no:randomly` vs `--reverse`), proving no cross-fixture dependency.

### Phase 1 — Retire the half-built event machinery (backend + Forum tab)

The Forum's existing event support is unreachable (F6, F7), double-defined (F11, F12), wrongly
scoped (F9) and wired to the wrong drill (F13). Leaving it in place means two "Registered"
numbers forever.

- Remove the dead `event` deck card and its exact-set assertion (`tests/test_forum.py:158`).
- Fix the Recruiting Pipeline tile's drill target.
- Keep `data.event` in the payload for one release, marked deprecated in its docstring, so a
  stale cached bundle does not crash.
- Fix `_renewals` / `_event` to use the injected clock (F21).

**Done when** the Forum tab renders with no event tile, `GET /forum` still returns 200 for a
workspace with no event configured, and `tests/test_forum.py` asserts the deck-card set without
`event`.

### Phase 2 — Data foundation (backend only, no UI)

- Migration `0090_forum_event`: the three tables in §4. One alembic head.
- `app/services/forum_event.py`: the group vocabulary (§4.3), `classify_stage` reuse,
  `compute_event(s, tenant_id, event, today=…)` returning the §5 payload.
- `tab_for_metric`: an explicit `event_` branch, **plus a test that asserts an unknown
  `event_*` key does not fall through to `portfolio`** (F14).
- Unit tests for every metric in §7 against a hand-built fixture, with the clock injected and the
  pace curve time-travelled across at least three days-to-event values.

**Done when** `compute_event` returns the §5 shape for a fixture event, and a test proves a guest
who converts still counts in `guests`.

### Phase 3 — The sync (backend only)

- `sync_forum_event(s, tenant_id, event)`: read opportunities in the configured pipelines and
  contacts carrying `reg_tags`, **upsert** `forum_event_guest` by `(event_id, contact_id)`,
  resolve `group` through `stage_map`, set `is_comped` from `comp_tag_match`, `channel` from
  `classify_shift_source`, `invited_by`, `rep_email`, `payment_type`.
- Upsert `forum_event_weekly` once per ISO week.
- Register it in the GHL branch of `_sync_integration` for the membership business, gated on at
  least one `forum_event` row existing — inert otherwise.

**Done when** a sync against the live Forum location populates 18 guests for the Q4 tag, and a
second sync of a *different* event leaves those 18 rows intact (the F1 regression, asserted).

### Phase 4 — The API

- `app/routers/forum_events.py` with the six endpoints in §5, mounted in `main.py`.
- `LaunchConfigOut`-style `config_out(event)` used by both list and detail.
- Schemas: all-optional upsert, required set validated in POST, every field present in all three
  places (F17), with the agreement test.
- Authz tests: read without the `forum` tab → 403; read another tenant's business key → 404;
  write as `member` → 403; every write writes an audit row.

**Done when** `GET /businesses/{key}/events/current` 404s on a workspace with no event and
returns the §5 payload on one with an event, and the authz tests pass.

### Phase 5 — The tab (frontend)

- `ForumShell.jsx` (clone of `BecollectiveView.jsx`), `CommandCenter.jsx` mount swap for the
  Forum only, The Edge untouched.
- `EventSection.jsx`, `useEvent.js`, `sampleEvent.js`.
- The settings drawer, with every §4.2 field and per-field help. Empty-by-default create form,
  **not** a Spring template (F19).
- Update `tests/test_platform.py:68-69` (exact nav list) in the same commit if the nav changes.

**Done when** the Event sub-tab appears on the Forum for a workspace with an event and is absent
for one without, the hero reads the real Q4 numbers, every figure opens a drill drawer, and the
page is clean at 375px with no horizontal scroll.

### Phase 6 — Assistant and lineage

- Add the event payload to `assistant.py` context packing and a `TAB_LEGEND` entry (F22), so
  "how many VIP guests do we have for Q4" is answerable in the Ask panel.
- Add `event_*` keys to the lineage/drill registry with their definitions from §7.

**Done when** the Ask panel answers a question about guest count and pace, citing the tab.

### Phase 7 — Spring, live (with Connor)

Done in Connor's own signed-in session, not a deploy:

- Create the Q4 2026 event with the real tag, pipeline, goal and dates. Leave pricing empty.
- Confirm the guest count matches what Spring believes it is, guest by guest if it does not.
- Set the pace curve, or accept linear for the first event and capture the real curve after.

**Done when** Connor confirms the Q4 number against his own count, and the first weekly history
row is captured.

---

## 9. Decisions made for Connor (change any of them)

**D1. A new `forum_event` table rather than reusing `Launch`.** §4.1 gives the three blockers.
The cost is a second config surface that looks a lot like the launch one. The alternative —
making `Launch` program-aware — touches a shipped revenue surface with four silent callers.

**D2. Guests are counted from the tag and priced from config, not from payments.** §1.3. The
consequence is that a guest who pays outside GHL, or whose charge is a membership renewal,
cannot be distinguished by amount. Ticket revenue is therefore *booked*, not *collected*.

**D3. Once a guest, always a guest.** A VIP who converts to a member still counts in `guests`,
so the registration number does not fall when sales succeed. The funnel row for `registered`
shows the current stage population; the hero shows cumulative guests.

**D4. `price_map` is keyed by the GHL contract stage names** (`"Single - PIF"`, `"Dual -
Monthly"`), so the Single/Dual axis needs no inference and no new column (§4.4). If Spring ever
sells a trio, it is a new key, not a migration.

**D5. `registered_on` comes from the contact's `dateAdded`.** GHL exposes no per-tag timestamp
(§7). For an existing contact who registers for Q4, the date will be their original arrival. If
that distorts the pace curve badly in practice, the fix is a dated GHL workflow writing a custom
field at RSVP time — that is a GHL change, not a code change.

**D6. The Event tab is conditionally present**, like the Launch tab: absent until an event exists,
rather than an empty state. A 404 is the signal.

**D7. Ticket money stays out of membership revenue**, matching the existing and tested
`classify_stream` behaviour (§1.3).

**D8. The tab ships with no prices and is fully useful that way.** Connor's ask is *"are we
filling the room"*; that is a count against a goal and needs no money at all. So `vip_price` and
`price_map` are nullable with no seeded defaults, every revenue figure degrades to a dash, and
pricing is something somebody turns on later in the drawer. The cost is that the Event tab shows
no revenue until it is configured — which is the honest state, not a gap.

---

## 10. Not built, designed for

- **Cash collected per guest.** The schema has no `paid_amount` because §1.3 cannot source it
  honestly. If a dedicated VIP payment link or product name is introduced in GHL, matching on
  `entitySourceName` is a column and a sync line, not a redesign.
- **Multiple events running at once.** The tables support it (no unique on active); only
  `/events/current` picks one. A future "compare Q3 vs Q4" view reads two rows.
- **Attendance.** `group == "attending"` is modelled but there is no check-in source. A scanned
  check-in would set a `attended_on` column.
- **Per-rep event commissions.** `rep_email` is captured on every guest for exactly this, but no
  commission math is specified here.
- **The Inner Circle funnel.** Retired (§1.4). If it is ever revived it is a `forum_event` row
  with its own `pipeline_match` and tags — no code change, no new table.
- **Member guests vs net-new guests.** `invited_by` is captured; the "which member brought the
  most guests" leaderboard is a drill, not a new table.

---

## 11. Only Connor can do

1. **Confirm the Q4 tag is the only one.** This spec assumes `the forum q4 2026 guest rsvp`
   identifies every Q4 VIP guest. If marketing used a second tag, it goes in `reg_tags`.
2. **Set the guest goal** for Q4 and the event dates. These are the only two values the tab needs
   to be useful — everything else has a default or degrades to a dash.
3. **Confirm ticket revenue should stay out of Forum MRR** (D7) — it is currently tested that
   way, but it is a business decision, not a technical one.

Prices are deliberately **not** on this list. They are configuration with no defaults (D8), and
the tab works without them.

---

## 12. Build record

*Empty until Phase 1 ships. Each phase appends: what landed, the commit, what was verified
against production, and anything that failed or could not be reproduced.*
