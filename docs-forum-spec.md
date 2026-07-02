# Command Center — Spec Addendum: The Forum focused view

**Audience:** Claude Code (and Connor). Continuation of `spring-command-center-SPEC.md` and `spring-command-center-SPEC-v2.md`. Everything here builds on what those established: the multitenant models, the `metric_record` table, the GHL client and `sync_ghl` snapshot pipeline, the `DataState` wrapper, the AuditDrawer, and the `/metrics/{key}/detail` lineage registry. Do not re-implement any of that — extend it.

**The reference mockup `the-forum-view-v2.jsx` is the single source of truth for all visual design, layout, interaction, motion, and the data shapes the API must return.** Ship it into the repo (e.g. `frontend/mockups/the-forum-view-v2.jsx`) and build the view to match it exactly. Its data objects (`KPIS`, `FUNNEL`, `RENEWALS_NEXT`/`RENEWALS_SUMMARY`, `EVENT`, `REVQ`, `DECK`, `DRILLS`) are deliberately shaped as the API contract so wiring is a near drop-in.

This addendum delivers four things, in priority order:

1. **Nav split** — Spring B stops being one nav item. "The Forum" and "beCollective" become separate spaces. The Forum gets the full focused view; beCollective ships as a designed placeholder (own spec later).
2. **The Forum view** — the existing six KPI tiles plus a four-card **deep-dive deck** (Recruiting pipeline · Renewals next 90 days · Next event · Revenue quality) with progressive disclosure: salient summary collapsed, full detail on select, row-level names in the AuditDrawer.
3. **GHL sync additions** — recruiting-funnel opportunities by stage, renewal status + 90-day horizon, subscription payment health, guest registrations, payment-type matching, and registration-pace snapshots.
4. **The Forum color system** — poppy/red is removed from this view. Watch states are amber, the action highlight is daffodil. Nothing in this business's reporting should read as alarm.

---

## Part 0 — Decisions that shape everything

**One business row, two views.** The `business` row with `key="springb"` does **not** split. It remains the financial entity (one QBO company) and the integration anchor (one GHL sub-account, one `integration` row for provider `ghl`). What splits is *presentation*: the dashboard payload replaces `areas.springb` with `areas.forum` and `areas.becollective`, and the sidebar gains the two nav items. The Forum area is built from GHL records where the member `segment` is `forum` or `inner_circle`; beCollective will later build from the `becollective` segment. The shared P&L renders on The Forum view with the caption *"Spring B entity · Forum revenue splits from beCollective by QBO class"* — the class-based split is future QuickBooks work, not this addendum.

**Point-in-time vs. period-scoped.** Only **New Members** respects the `?period=` param (opportunities reaching "Won: Onboarded" inside the period). Everything else on this view is current-state: active members, ARR, MRR, the recruiting pipeline, the renewal book, event registration, payment mix. Do not month-scope ARR or the funnel — on Jul 2 with `period=mtd`, the view must still show $1.2M and 14 in pipeline. This is why the deck earns its place next to the MTD zeros.

**Color semantics (this view only).** Structure = evergreen (label ticks, funnel bars, payment-mix bar). Positive progress = meadow (registration progress, committed chips). **Daffodil = the single "do this today" highlight** (action rows, watch dots, selected-card underline, Forum nav identity). Watch text = amber. Poppy does not appear anywhere on The Forum view, including the drawer — at-risk rows get a daffodil dot + amber value, not red text. Neutral facts (e.g. churned in the ARR bridge) render slate, not red. Other views keep their existing accents; only the Forum adopts this system.

**Degrade, never break.** Every deck card and drill has a defined fallback when its GHL inputs are missing (Part 4's table). A missing input hides or dims that element; it never errors the view.

---

## Part 1 — Frontend

### 1.1 Nav + routing

Replace the Spring B nav item:

```js
const NAV = [
  { k: "overview",     label: "Portfolio",         dot: parchment },
  { k: "ulrg",         label: "ULRG + Team",       dot: meadow },
  { k: "forum",        label: "The Forum",         dot: daffodil },
  { k: "becollective", label: "beCollective",      dot: petal },
  { k: "sympli",       label: "Sympli Mortgage",   dot: teal },
  { k: "flywheel",     label: "Referral Flywheel", dot: sprout, divide: true },
];
```

Routes: `/forum` and `/becollective`. Redirect any existing `/springb` route to `/forum`. The nav active border color in this shell is daffodil (per the mockup); if the shell's active border is currently global poppy, make it a token so each view can theme it later — for now daffodil is fine shell-wide.

**Overview page:** the single Spring B `AreaCard` becomes two cards — **The Forum** (revenue/NOI from the springb entity until classes split, sub-line "Members 70 · ARR $1.2M") and **beCollective** (pending state: "Operational view coming — GHL segment configured"). The portfolio composition bar keeps one "Spring B" revenue segment (it's one QBO entity); add a footnote "Forum + beCollective" on that segment's legend.

**Theme tokens.** Add to the shared theme (values from the mockup): `daffodil #FFF3AD`, `daffodilBg #FFF9D6`, `amber #9C6A1E`, `amberBg #F5EAD3`, `meadowInk #4F6A4D`, `meadowBg #E9EFE7`, `mist #DCE7E9`.

### 1.2 View anatomy (top to bottom)

Everything below matches `the-forum-view-v2.jsx` — treat this section as the map, the mockup as the territory.

1. **Header.** Accent bar (evergreen) · "The Forum" · "Mastermind · {N} members" · right-aligned watch indicator: a soft-pulsing amber dot + "{watch_count} items to watch". When `watch_count` is 0, render a meadow dot + "Healthy" instead (reuse the `Dot` component semantics, amber replacing poppy for watch).
2. **Row 1** — a fixed `grid-template-columns: minmax(0,5fr) minmax(0,7fr)` so the two cards always match height. Left: the Financial P&L panel exactly as it exists today (QuickBooks `DataState` empty state until connected, then the standard `PLTable`), plus the entity caption. Right: the six KPI tiles (`KPIS` below). Tiles with a `drill` key are buttons with the corner `↗` and hover lift; tiles without (New Members) are static.
3. **Deep-dive deck.** Section eyebrow "Deep dives" + hint "select a card to expand". Four equal cards in `repeat(4, minmax(0,1fr))` (2-up ≤1000px, 1-up ≤560px), each with fixed anatomy: uppercase label + chevron → hero stat + sub → micro-visual → salient line. `minHeight: 148`. The salient line carries the watch signal: amber text + daffodil dot when `tone: "watch"`, meadow-ink text when `tone: "good"`. **There is no separate flag-pill row** — v1's pills were folded into the deck.
4. **Expanded detail.** Selecting a card sets `sel`; the full detail renders in a full-width `Card` below the deck with a `PanelLabel`, the "Go High Level" source badge, and a "Collapse ▴" button. Selecting the active card again (or Collapse) closes it. Default state is **all collapsed** — flag to Spring in review: if she wants renewals pre-expanded on load, it's `useState("renewals")`.
5. **Micro-visuals** (fixed-height lane so cards stay equal): pipeline = 4 stacked mini-bars (evergreen, opacity ramp by count); renewals = committed/talking/risk split bar (meadow / teal 35% / amber 55%); event = registration progress (meadow on parchment); revq = PIF/monthly split (evergreen solid / evergreen 28% — the established "solid is real, lighter is collecting" convention).
6. **Detail contents** (each maps 1:1 to a mockup component):
   - `PipelineDetail` — horizontal stage bars with count and $ per stage, cohort footnote under a hairline.
   - `RenewalsDetail` — up to 6 rows (month · name · segment chip · value · status chip), footer row: retention stat left, "all {count} · {value} →" right (opens the `renewal_book` drawer).
   - `EventDetail` — location/name/dates + days-out, registration progress with "{r} of {m} members · {pct}%" and "+{g} guests (prospect seats)", the daffodil `ActionRow` "{u} members not yet registered → the call list" (opens `unregistered` drawer), and the amber behind-pace note when applicable.
   - `RevQDetail` — payment-mix split bar with PIF/monthly captions (monthly caption opens `monthly` drawer), daffodil `ActionRow` "{n} subscriptions past due · {v} → recover" (opens `pastdue` drawer), and the ARR bridge (Jan 1 → +New → −Churned → Today; churned in slate, total in meadow-ink). Hide the bridge row entirely if the API omits it.

### 1.3 Motion (CSS only — no libraries, no JS animation)

- Detail expansion: `.fd-body { animation: fdFade .28s ease }` (opacity 0→1, translateY 6px→0), keyed by `sel` so switching cards re-runs it.
- Selected card: daffodil underline `scaleX(0)→scaleX(1)` over `.22s ease`, `transform-origin: left`.
- Hover: tiles/deck cards lift `translateY(-1/-2px)` with soft evergreen shadow, `.15s ease`.
- Drawer: existing `.22s` slide-in.
- Watch dot: 2.4s soft pulse, wrapped in `@media (prefers-reduced-motion: no-preference)`.
- Global `prefers-reduced-motion: reduce` block kills all transitions/animations/hover transforms. Keyboard focus: 2px teal `focus-visible` outline on every interactive element.

### 1.4 Drawer datasets

Reuse the existing AuditDrawer; do not build a second drawer. The Forum introduces a uniform row shape the drawer must render:

```ts
{ name, seg?: "F"|"IC", l2?: string, r1: string, r2?: string, tone?: "watch", source_url }
```

`seg` renders the Forum (daffodil bg, evergreen text) / IC (mist bg, teal text) chip; `tone:"watch"` renders the daffodil-dot marker and amber `r1` — never red. Header shows title, count (count in daffodil), the "computed as" sentence, and the GHL source badge; every row ends in `↗` → `source_url`. Drill keys and their triggers:

| Drill key | Opened from |
|---|---|
| `active_members` (roster) | Active Members tile |
| `forum_arr` (contracts) | Forum ARR tile |
| `renewal_book` | Renewals Due tile · renewals detail footer |
| `registered` | Registered tile |
| `unregistered` | Event detail call-list ActionRow |
| `pastdue` | RevQ detail recover ActionRow |
| `monthly` | MRR tile · RevQ monthly caption |

### 1.5 States

Wrap the whole Forum view's data region in `DataState`. GHL disconnected → the operational panel and the deck render one shared empty state: "Connect Go High Level" → `/settings/integrations` (P&L keeps its own QuickBooks state). Loading → skeleton: six tile shimmers + four deck-card shimmers. A deck card whose inputs are missing (per Part 4's fallback column) renders dimmed with sub-text "needs {config key}" rather than hiding — visible gaps get configured; invisible ones don't.

### 1.6 beCollective placeholder

`/becollective` renders the brand header (petal accent) and a single card: "beCollective gets its own space — same shell, its own accent, its own GHL segment. Operational view coming." plus a disabled-styled preview list of its planned tiles (Members · MRR · Engagement · Funnel). No backend work beyond the `areas.becollective` stub in Part 3.

---

## Part 2 — Go High Level: sync additions

All additions extend `sync_ghl` and `integrations/ghl.py`. Keep the established snapshot pattern (`_ghl_snapshot` replaces the prior record set per kind so drops fall out) for everything except registration-pace counts, which append. No new tables — `metric_record.meta` (JSONB) carries the new fields.

### 2.1 Recruiting pipeline → kind `recruiting`

Today the sync only extracts sales-funnel opps that reached "Won: Onboarded". Add a snapshot of **all open opportunities in the sales funnel**, one record per opp:

- Find the sales pipeline via `get_pipelines` and a new config key `sales_pipeline_match` (case-insensitive substring, default `"sales"`), the same way `renewals_pipeline_match` works.
- Stage identity: resolve each opp's `pipelineStageId` against the pipeline's stage list to get the stage **name and position**. Store `meta: { stage, stage_position }`.
- Record: `kind="recruiting"`, `status="open"`, `amount = monetaryValue or None`, `source_url` = the GHL opportunity link.
- Exclude won/lost/abandoned opps (they're covered by `onboarded` and, for the bridge, 2.5).

Stage display order and labels come from the pipeline itself (position order, won/lost stages excluded) — do not hardcode stage names. If the team renames a stage in GHL, the funnel follows.

**Onboarded records get value.** Extend the existing `onboarded` mapping to also store `amount = monetaryValue` — the bridge (2.5) and cohort footer need it.

### 2.2 Renewal status + horizon → extend kind `membership`

The renewals-pipeline snapshot already stores `amount` and `meta.renewal_month`. Add:

- `meta.stage` — the opp's stage name (resolved as in 2.1).
- `meta.renewal_status` — one of `committed | talking | risk`, mapped from the stage name via config:

```json
"renewal_stage_status": {
  "committed": ["verbal yes", "agreement out", "renewed"],
  "talking":   ["call booked", "in conversation", "outreach"],
  "risk":      ["no response", "considering exit", "at risk"]
}
```

Match case-insensitive substring against the stage name; no match → `talking` (neutral default, never `risk` by default). The three buckets drive the status chips, the deck micro-split, and the at-risk watch flag.

### 2.3 Subscriptions: keep every status → kind `subscription`

Stop filtering to active-only at sync time. Snapshot all subscriptions with their real status (`active | past_due | cancelled | …` — normalize GHL's exact strings in the client) and `meta: { failed_at, contact_id }` when the payments API provides them. MRR still sums `active` only; `past_due` drives the recover flow. If the payments scope is missing, this kind degrades to a skip exactly as today — the RevQ card then hides its past-due row and payment mix (Part 4).

### 2.4 Payment type on memberships

After syncing both kinds, annotate each `membership` record with `meta.payment = "monthly" | "pif"`: monthly if an `active` or `past_due` subscription matches the membership's contact (match on GHL `contact_id` first, email fallback); otherwise PIF. This yields the payment mix (33 PIF / 14 monthly in the reference data) and the roster's payment column. Consistency invariant: `pif_count + monthly_count == membership_count`.

### 2.5 ARR bridge inputs → kind `membership_lost`

Snapshot **lost** opportunities in the renewals pipeline with `occurred_on = lastStatusChangeAt` and `amount`. The bridge then computes YTD from records already on hand:

```
new_ytd     = Σ amount of onboarded records with occurred_on in current year
churned_ytd = Σ amount of membership_lost records with occurred_on in current year
start_arr   = current_arr − new_ytd + churned_ytd
```

If either timestamp/amount set is empty or incomplete, omit `revq.bridge` from the payload — the frontend hides the row. Never render a bridge that doesn't sum.

### 2.6 Guests + event config

A `registration` whose contact is **not** in the member set gets `meta.guest = true` — these are the prospect seats. Extend the integration config:

```json
{
  "event_tag": "the forum q3 2026",
  "event_name": "Park City, UT",
  "event_title": "The Forum · Q3 Immersion",
  "event_dates": "Sep 15–17",
  "event_date": "2026-09-15",
  "prior_event_pace": 34
}
```

`event_date` (ISO) drives days-out. `prior_event_pace` is the manual fallback for the first cycle (registrations at the same days-out before the prior event); 2.7 replaces it with real data going forward.

### 2.7 Registration pace snapshots → kind `reg_count` (append-only)

Once per sync day, insert `kind="reg_count"`, `occurred_on=today`, `amount=<registered count>`, `meta: { event_tag, days_out }`. **Do not** route through `_ghl_snapshot` — this kind appends (guard with an upsert on `(tenant, source, kind, external_id)` where `external_id = f"{event_tag}:{today}"`). Pace comparison: registered today vs. the prior event's `reg_count` at the nearest `days_out`; fall back to `prior_event_pace` config; if neither exists, the event card shows no pace note. `behind_pace = registered < comparison`.

### 2.8 Consolidated config (seed + settings form)

```json
{
  "location_id": "…",
  "member_tags": ["inner circle active", "the forum active", "forumadmin",
                   "member: secondary", "inner circle active add on"],
  "forum_tags": ["the forum active", "member: secondary", "forumadmin"],
  "innercircle_tags": ["inner circle active", "inner circle active add on"],
  "renewals_pipeline_match": "renewals",
  "sales_pipeline_match": "sales",
  "onboarded_stage_match": "won: onboarded",
  "renewal_stage_status": { "committed": ["verbal yes", "agreement out", "renewed"],
                            "talking": ["call booked", "in conversation", "outreach"],
                            "risk": ["no response", "considering exit", "at risk"] },
  "default_contract_value": 28000,
  "event_tag": "the forum q3 2026",
  "event_name": "Park City, UT",
  "event_title": "The Forum · Q3 Immersion",
  "event_dates": "Sep 15–17",
  "event_date": "2026-09-15",
  "prior_event_pace": 34
}
```

Surface the new keys on `/settings/integrations` → the GHL config form. `default_contract_value` fills per-stage funnel dollars when opps lack `monetaryValue` (`stage_value = Σ monetaryValue, else count × default_contract_value`).

---

## Part 3 — API contract

### 3.1 `GET /api/v1/forum?period=mtd|qtd|ytd|last_month`

One endpoint returns the whole view, shaped on the mockup's data objects. Build it in `metrics.py` from `metric_record` + the integration config; share helpers with the dashboard builder so numbers can never diverge.

```jsonc
{
  "status": "watch",                    // "healthy" when watch.count == 0
  "watch": { "count": 3, "items": ["pastdue", "at_risk", "behind_pace"] },
  "members_total": 70,
  "pl": { /* existing springb P&L shape, or null until QBO connects */ },
  "kpis": [
    { "key": "active_members", "label": "Active Members", "value": "70",
      "sub": "Forum 26 · Inner Circle 44", "drill": "active_members" },
    { "key": "forum_arr", "label": "Forum ARR", "value": "$1.2M",
      "sub": "47 memberships", "drill": "forum_arr" },
    { "key": "new_members", "label": "New Members", "value": "0", "sub": "month to date" },
    { "key": "renewals_due", "label": "Renewals Due", "value": "0", "sub": "July", "drill": "renewal_book" },
    { "key": "registered", "label": "Registered", "value": "28", "sub": "Park City, UT", "drill": "registered" },
    { "key": "mrr", "label": "MRR", "value": "$31K", "sub": "monthly subscriptions", "drill": "monthly" }
  ],
  "deck": [
    { "k": "pipeline", "label": "Recruiting pipeline", "hero": "14",
      "hero_sub": "in the pipeline", "salient": "3 invited · $84K near-term", "tone": "good" },
    { "k": "renewals", "label": "Renewals · next 90 days", "hero": "$300K",
      "hero_sub": "12 renewals", "salient": "3 at risk · $66K", "tone": "watch" },
    { "k": "event", "label": "Next event · Park City", "hero": "75",
      "hero_sub": "days out", "salient": "42 unregistered · behind pace", "tone": "watch" },
    { "k": "revq", "label": "Revenue quality", "hero": "69%",
      "hero_sub": "paid in full", "salient": "2 past due · $4.4K", "tone": "watch" }
  ],
  "funnel": {
    "stages": [
      { "label": "Applied", "v": 14, "value": "$392K" },
      { "label": "Discovery call booked", "v": 9, "value": "$252K" },
      { "label": "Call held", "v": 6, "value": "$168K" },
      { "label": "Invited · agreement out", "v": 3, "value": "$84K" }
    ],
    "footer": "Q2 cohort: 31 applications → 7 onboarded · 23% application-to-member · avg 21 days to close"
  },
  "renewals": {
    "rows": [
      { "name": "Marcus Tran", "seg": "F", "month": "Aug", "value": "$30K", "status": "committed" }
      // … up to 6, ordered by renewal date then value desc
    ],
    "summary": { "count": 12, "value": "$300K",
                 "mix": { "committed": 6, "talking": 3, "risk": 3 },
                 "risk_value": "$66K",
                 "retention": "Trailing 12 mo · 86% logo · 91% dollar retention" }
  },
  "event": {
    "title": "The Forum · Q3 Immersion", "where": "Park City, UT", "when": "Sep 15–17",
    "days_out": 75, "registered": 28, "members": 70, "guests": 4,
    "unregistered": 42, "behind_pace": true,
    "pace_note": "34 were registered at this point before Scottsdale Q2"
  },
  "revq": {
    "pif": { "value": 828000, "count": 33 },
    "monthly": { "value": 372000, "count": 14, "sub": "$31K MRR annualized" },
    "past_due": { "count": 2, "value": "$4.4K" },
    "bridge": [                          // omit key entirely if inputs incomplete
      { "label": "Jan 1", "value": "$1.13M" },
      { "label": "New", "value": "+$158K" },
      { "label": "Churned", "value": "−$88K", "soft": true },
      { "label": "Today", "value": "$1.2M", "tot": true }
    ]
  }
}
```

Field-level fallbacks: `funnel: null` (no sales pipeline matched), `event: null` (no event config), `revq.past_due: null` and `revq.monthly: null` (no payments scope), `renewals.summary.retention: null` (insufficient history — hide the caption, not the card). `deck[*].salient` degrades with its section (e.g. no pace data → event salient becomes "42 unregistered", tone stays watch only while `unregistered > 0` is judged noteworthy: rule = watch when `registered/members < 0.5` inside 90 days).

### 3.2 Dashboard changes — `GET /api/v1/dashboard`

- `areas.springb` is **removed**. Add `areas.forum` (status + the six ops + the shared `pl`) and `areas.becollective` (`{ "status": "pending", "ops": [], "pl": null }`).
- Scorecard "Active Members" keeps value `70`, sub becomes `"The Forum"`.
- Update every test asserting `areas["springb"]` (see Part 6).

### 3.3 Drill-down details — extend the lineage registry

All rows adopt the drawer shape from 1.4 and must set `source_url`. New/changed keys in `services/lineage.py`:

| Key | Rows | Computed-as sentence |
|---|---|---|
| `active_members` *(enrich)* | member records, `l2 = "Joined {mo yyyy} · {PIF/Monthly}"`, `r1 = value`, `r2 = "renews {mo} · event {✓/✗}"` (join membership + registration by contact) | Contacts in Go High Level carrying an active membership tag, segmented Forum / Inner Circle. |
| `forum_arr` *(as-is + seg)* | memberships by value desc | unchanged |
| `renewal_book` *(new)* | memberships with renewal in the next 90 days; `l2 = "{Mon} · {Status}"`, `r2 = "stage: {stage}"`, `tone: "watch"` when risk | Open opportunities in the renewals pipeline with a renewal month in the next 90 days. |
| `renewals_due` *(keep)* | current-month subset — still backs the KPI tile's number | unchanged |
| `registered` *(as-is + seg)* | member registrations (`guest != true`), `l2 = "Registered {date}"` | Contacts tagged '{event_tag}'. |
| `unregistered` *(new)* | member set minus registered contacts; `l2 = "Last attended: {…}"` when derivable from prior event tags, else omitted; `r2 = "also renewal risk"` + `tone` when the contact is a risk-status renewal | Active members without the '{event_tag}' tag. This is the call list. |
| `pastdue` *(new)* | subscriptions with status `past_due`; `l2 = "{reason} · {date}"` when meta has it | GHL subscriptions with a failed most-recent charge. |
| `monthly` *(new)* | active + past_due subscriptions; `r1 = "$X/mo"`, `r2 = "current" / "past due"` | Active recurring subscriptions in GHL Payments. Sum = MRR. |

---

## Part 4 — Every number on the view (computation table)

| Element | From (kind) | Logic | Fallback when missing |
|---|---|---|---|
| Active Members 70 / segments | `member` | count; segment split | — (core; empty state if GHL off) |
| Forum ARR / memberships | `membership` | Σ amount / count | tile "—" |
| New Members | `onboarded` | count with `occurred_on` in period | tile "—" |
| Renewals Due (KPI) | `membership` | `renewal_month == current month` | tile "—" |
| Registered / guests | `registration` | count where `guest != true` / `== true` | event card hides guests line |
| MRR | `subscription` | Σ amount where status `active` | tile "—", revq hides mix |
| Funnel stages + $ | `recruiting` + pipeline stages | count per stage in position order; `$ = Σ monetaryValue else count × default_contract_value` | deck card dimmed "needs sales_pipeline_match" |
| Funnel footer (cohort) | `onboarded` + `recruiting` history | prior-quarter: applications created, onboarded won, close %, avg `won_at − created_at` days | omit footer |
| Renewals next-90 rows/summary | `membership` | renewal date in next 90 days; status from `meta.renewal_status`; mix + Σ; risk Σ | deck card dimmed |
| Retention caption | `membership` + `membership_lost` | trailing-12-mo: renewed/(renewed+lost) logo; Σ renewed value / Σ due value dollar | omit caption |
| Days out / event copy | config | `event_date − today` | deck card dimmed "needs event_date" |
| Unregistered | `member` − `registration` | set difference by contact | — |
| Behind pace | `reg_count` or `prior_event_pace` | registered < comparison at same days-out | omit pace note |
| PIF / monthly mix | `membership.meta.payment` | counts + Σ value; PIF value = ARR − monthly value | omit mix row |
| Past due | `subscription` | status `past_due`: count + Σ | omit recover row |
| ARR bridge | ARR + `onboarded` + `membership_lost` (YTD) | Part 2.5 formula; **must sum exactly** | omit bridge |
| Watch count | derived | `#{past_due>0, risk_count>0, behind_pace}` true | counts only computable flags |
| Area status | derived | `watch` if watch count > 0 else `healthy` | `healthy` |

**Consistency invariants — assert these in tests and in a debug log line at the end of the forum build:** `forum + inner_circle == members_total` · `pif.count + monthly.count == membership count` · `pif.value + monthly.value == ARR` · `bridge sums to today's ARR` · `renewals mix sums to summary.count` · `risk_value == Σ risk rows` · `registered + unregistered == members_total`.

---

## Part 5 — Data model & migration

No new tables, no new columns. New `metric_record.kind` values: `recruiting`, `membership_lost`, `reg_count`. New `meta` fields documented in Part 2 (`stage`, `stage_position`, `renewal_status`, `payment`, `guest`, `failed_at`, `contact_id`, `event_tag`, `days_out`). New integration-config keys per 2.8 (JSONB — no Alembic migration required). If the `metric_record` unique constraint would collide for `reg_count` daily rows, the composite `external_id = "{event_tag}:{date}"` avoids it.

---

## Part 6 — Tests (extend `backend/tests/test_dashboard.py` or a new `test_forum.py`)

Follow the existing style: seed `metric_record` rows deterministically (clear prior GHL records for the business first), call the endpoint, assert the payload. Minimum set:

1. **`test_forum_payload_shape`** — seed members (2 F + 1 IC), 3 memberships with renewal months/statuses/payments, 4 recruiting opps across 3 stages, registrations (2 members + 1 guest), subscriptions (2 active + 1 past_due), onboarded + lost with amounts. Assert: kpis values; funnel stage order/counts/$ (incl. `default_contract_value` fill); renewals summary mix + risk value; event registered/guests/unregistered; revq mix + past due; bridge sums; watch count; every invariant from Part 4.
2. **`test_forum_period_scoping`** — only `new_members` changes between `mtd` and `ytd`; ARR/funnel/renewals identical.
3. **`test_forum_fallbacks`** — wipe subscriptions → `revq.past_due` and `monthly` null, MRR "—", watch count drops; wipe event config → `event: null`, deck event card flagged; incomplete bridge inputs → no `bridge` key.
4. **`test_forum_drills`** — `renewal_book` (90-day filter, watch tone on risk), `unregistered` (set difference; renewal-risk cross-flag), `pastdue`, `monthly`; every row has `source_url`.
5. **`test_dashboard_area_split`** — `areas.forum` present with ops, `areas.becollective` pending, `areas.springb` absent; scorecard sub "The Forum". Update the existing springb assertions.
6. **`test_reg_count_appends`** — two syncs on different days produce two `reg_count` rows; same-day resync upserts, not duplicates.

Also update `backend/validate_forum.py` to print the new payload sections against live data (no PII) — it's the fastest way to confirm the stage/status mappings match the team's real GHL pipelines before the frontend lands.

---

## Part 7 — Build order

1. **Sync additions** (Part 2) + `validate_forum.py` against live GHL. Confirm real stage names → tune `renewal_stage_status` and `sales_pipeline_match` in config before anything else; the mappings are the only genuinely unknown part.
2. **`/api/v1/forum`** + metrics/lineage additions + tests. Ship the invariants log line.
3. **Nav split + Forum view shell** — routing, header, row 1 wired to the new endpoint, DataState states. The view is already useful here.
4. **Deck + expansion + motion** — the four summary cards, detail panels, CSS animation block, reduced-motion.
5. **Drawer datasets** — wire all seven drills; verify every `↗` opens the right GHL record.
6. **Dashboard/overview split + beCollective placeholder** — area cards, scorecard sub, redirect.
7. **Polish pass against the mockup** — side-by-side with `the-forum-view-v2.jsx`; the color-semantics rules in Part 0 are acceptance criteria (grep the Forum view for poppy: there should be zero uses).

Sequence note: 1–2 de-risk the data; 3–5 are the visible product; 6–7 are the seams. If live GHL lacks payments scope or the sales pipeline, ship anyway — the fallbacks in Parts 3–4 are designed so the view is complete-looking with whatever subset syncs.
